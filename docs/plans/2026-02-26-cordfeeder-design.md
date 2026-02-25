# CordFeeder Design

An RSS/Atom feed reader that posts to Discord channels, controlled entirely through Discord slash commands.

## Architecture

CordFeeder is a single-process Python application with two concurrent concerns running in one async event loop:

- **Discord bot** — connects via `discord.py`, handles slash commands, posts feed items as embeds
- **Feed poller** — async background loop that polls feeds on schedule and queues new items for posting

No web server, no external queue, no separate workers. The bot connects to Discord over a persistent WebSocket and stays running. All state persists in a local SQLite database, so restarts are seamless — it reconnects and resumes without re-posting.

### Dependencies

- `discord.py` — Discord bot framework
- `feedparser` — RSS/Atom parsing
- `aiosqlite` — async SQLite access
- `aiohttp` — HTTP client for fetching feeds (transitive dependency of `discord.py`)

### Project Structure

```
cordfeeder/
├── bot.py          # Discord bot setup, command registration, event handlers
├── poller.py       # Feed polling loop, adaptive scheduling, HTTP conditional GET
├── parser.py       # Feed parsing, item normalisation, deduplication
├── database.py     # SQLite schema, queries, migrations
├── formatter.py    # Discord embed formatting for feed items
├── config.py       # Environment-based configuration
└── main.py         # Entry point
```

### Configuration

All via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_BOT_TOKEN` | (required) | Bot authentication token |
| `FEED_MANAGER_ROLE` | `"Feed Manager"` | Discord role required to manage feeds |
| `DEFAULT_POLL_INTERVAL` | `900` | Default poll interval in seconds (15 min) |
| `DATABASE_PATH` | `cordfeeder.db` | Path to SQLite database file |

## Data Model

Three SQLite tables:

### `feeds`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Short numeric ID used in commands |
| `url` | TEXT | Feed URL |
| `name` | TEXT | Display name (auto-populated from feed title, editable) |
| `channel_id` | INTEGER | Discord channel to post to |
| `guild_id` | INTEGER | Discord server ID |
| `added_by` | INTEGER | Discord user ID who added the feed |
| `created_at` | TEXT | ISO 8601 timestamp |

### `feed_state`

| Column | Type | Description |
|--------|------|-------------|
| `feed_id` | INTEGER FK | References `feeds.id` |
| `etag` | TEXT | Last ETag header from server |
| `last_modified` | TEXT | Last Last-Modified header from server |
| `last_poll_at` | TEXT | When we last polled |
| `next_poll_at` | TEXT | When to poll next |
| `poll_interval` | INTEGER | Current adaptive interval in seconds |
| `consecutive_errors` | INTEGER | Error count for backoff calculation |
| `last_error` | TEXT | Most recent error message |

### `posted_items`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `feed_id` | INTEGER FK | References `feeds.id` |
| `item_guid` | TEXT | RSS item GUID or link (unique identifier) |
| `posted_at` | TEXT | When posted to Discord |
| `message_id` | INTEGER | Discord message ID |

Unique constraint on `(feed_id, item_guid)` to prevent double-posting. Items older than 90 days are periodically pruned.

## Feed Polling

### Poll Loop

1. On startup, the poller kicks off as an async background task.
2. Every 30 seconds, it checks `feed_state` for feeds where `next_poll_at <= now`.
3. For each due feed, fetch the URL with conditional GET headers:
   - `If-None-Match` (from stored ETag)
   - `If-Modified-Since` (from stored Last-Modified)
   - `Accept-Encoding: gzip`
   - `User-Agent: CordFeeder/1.0 (Discord RSS bot)`
4. Handle responses:
   - **304** — no changes, schedule next poll
   - **200** — parse feed, diff against `posted_items`, post new items
   - **301** — update stored URL permanently
   - **410** — delete the feed, notify the channel
   - **429/403** — back off to 4 hours minimum, or respect `Retry-After`
   - **5xx** — increment error count, exponential backoff
5. Update `feed_state` with new ETag/Last-Modified, calculate next poll time.

### Adaptive Polling Interval

After a successful fetch, estimate post frequency from recent item timestamps:

- Set poll interval to roughly half the average gap between posts
- Clamp between 5 minutes and 12 hours
- On first fetch, default to 15 minutes until enough data accumulates
- Respect feed hints (`<ttl>`, `Cache-Control`, `sy:updatePeriod`) as a floor

### Error Backoff

```
next_interval = min(poll_interval * 2^consecutive_errors, 86400) + random_jitter
```

Cap at 24 hours. Reset on successful fetch.

### Request Discipline

- Limit to 2 concurrent fetches to the same host
- Jitter: add random 0-25% to each poll interval
- Descriptive User-Agent with contact info

### Posting New Items

- Parse with `feedparser`, extract: title, link, summary, author, published date, image
- Check each item's GUID (or link as fallback) against `posted_items`
- Post new items as Discord embeds, oldest first (chronological order)
- Cap at 5 new items per poll per feed to avoid flooding; remainder picked up next cycle
- On first add, post only the 3 most recent items

## Discord Commands

All under a `/feed` command group.

### `/feed add <url> [channel]`

- Validates the URL by fetching and parsing it
- Creates the feed record, posts the 3 most recent items as preview
- Confirms: "Feed added: **Feed Title** (ID: 7)"
- `channel` defaults to current channel
- Requires Feed Manager role

### `/feed remove <id>`

- Removes feed and all associated state/posted items
- Confirms: "Removed feed **Ars Technica** (ID: 3)"
- Requires Feed Manager role

### `/feed list`

- Shows all feeds for the server: ID, name, channel, last polled, poll interval
- Available to everyone

### `/feed preview <url>`

- Fetches feed and shows latest item as an embed without subscribing
- Available to everyone

### `/feed config`

- Shows bot status: total feeds, feeds in error state, uptime
- Requires Feed Manager role

### Access Control

- `add`, `remove`, `config` require the role specified by `FEED_MANAGER_ROLE`
- `list` and `preview` available to everyone
- All command responses are ephemeral (only visible to invoker) except feed item posts

## Feed Item Formatting

Each item posts as a Discord embed:

- **Author** — feed name (small text) with feed icon if available
- **Title** — article title, hyperlinked to the article URL
- **Description** — first ~300 characters of summary, HTML stripped, truncated at word boundary
- **Image** — thumbnail if the item has an image or enclosure
- **Footer** — publish date (relative if recent, absolute otherwise) and feed ID
- **Colour** — consistent per feed, derived by hashing the feed URL

If an item has no summary, only the title and link are shown.
