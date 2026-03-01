# CordFeeder

<!-- showboat-id: 8bad4866-e5d4-4b5e-be95-ce9281a1872b -->

CordFeeder is a Discord bot that monitors RSS and Atom feeds and posts new items to your Discord channels. Subscribe feeds with a single slash command, and new articles appear automatically — no manual checking, no missed posts.

It runs as a single process on any machine that can reach Discord and your feeds. State persists in a local SQLite database, so restarts are seamless.

**No web server. No external queue. No separate workers.** Just a bot that watches feeds.

## Features

- **Slash commands** — manage feeds from Discord with `/feed add`, `/feed remove`, `/feed list`, `/feed preview`, and `/feed config`
- **Auto-discovery** — paste any URL; CordFeeder probes for an RSS or Atom feed automatically
- **Adaptive polling** — adjusts check frequency to match how often each feed publishes (5 minutes–12 hours)
- **Conditional GET** — sends `If-None-Match` and `If-Modified-Since` headers so unchanged feeds cost one byte of bandwidth
- **Deduplication** — GUID-based dedup prevents items from being posted twice, even across restarts
- **Smart formatting** — text-rich items show summaries; image-primary feeds (webcomics) show inline images; boilerplate prefixes and suffixes are stripped automatically
- **Injection protection** — feed content is sanitised against Discord mention injection, markdown escapes, URL breakout, and newline smuggling
- **Graceful error handling** — 410 Gone removes the feed automatically; 429/403 back off for at least 4 hours; 5xx errors use exponential backoff up to 24 hours
- **Role-based access** — feed management commands require a configurable Discord role; list and preview are public
- **Structured logging** — every event emits a JSON log line for easy ingestion into any log aggregator

## Project layout

```bash
find cordfeeder -name '*.py' | sort | sed 's/^/  /'
```

```output
  cordfeeder/__init__.py
  cordfeeder/__main__.py
  cordfeeder/bot.py
  cordfeeder/config.py
  cordfeeder/database.py
  cordfeeder/discovery.py
  cordfeeder/formatter.py
  cordfeeder/main.py
  cordfeeder/parser.py
  cordfeeder/poller.py
```

Each module has a single responsibility:

| Module | Purpose |
|--------|---------|
| `bot.py` | Discord bot, slash command handlers |
| `poller.py` | Background poll loop, HTTP fetching, error backoff |
| `parser.py` | RSS/Atom parsing, HTML stripping, boilerplate removal |
| `formatter.py` | Discord message formatting, injection sanitisation |
| `discovery.py` | Feed auto-discovery from arbitrary URLs |
| `database.py` | SQLite schema and queries |
| `config.py` | Environment-variable configuration |
| `main.py` | Entry point, structured JSON logging |

## Installation

Requires Python 3.12+. [uv](https://github.com/astral-sh/uv) is recommended but plain pip works too.

```bash
git clone <this repo>
cd cordfeeder
uv sync
```

Then follow the Discord setup guide in [docs/discord-bot-setup.md](docs/discord-bot-setup.md) to create the bot and generate an invite URL.

## Configuration

Copy `.env.example` to `.env` and fill in your values:

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_BOT_TOKEN` | *(required)* | Bot authentication token from the Discord Developer Portal |
| `FEED_MANAGER_ROLE` | `Feed Manager` | Name of the Discord role allowed to add/remove/configure feeds |
| `DEFAULT_POLL_INTERVAL` | `900` | How often to check feeds in seconds (15 minutes). Min 300, max 43200 |
| `DATABASE_PATH` | `data/cordfeeder.db` | Path to the SQLite database file |
| `LOG_LEVEL` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARN`, `ERROR` |

CordFeeder validates configuration on startup and exits with a clear error if `DISCORD_BOT_TOKEN` is missing or if any integer variable is not parseable.

Config validation is enforced at startup — here is what happens when the token is missing:

```python

import os, sys
os.environ.pop('DISCORD_BOT_TOKEN', None)
try:
    from cordfeeder.config import Config
    Config.from_env()
except ValueError as e:
    print(f'ValueError: {e}')

```

```output
ValueError: DISCORD_BOT_TOKEN environment variable is required
```

## Running

```bash
uv run cordfeeder
```

On first start you will see structured JSON log lines:

```json
{"ts":"2026-02-27T10:00:00.000Z","level":"INFO","logger":"cordfeeder.main","msg":"starting cordfeeder","host":"myhost","app":"cordfeeder","feed_manager_role":"Feed Manager","default_poll_interval":900}
{"ts":"2026-02-27T10:00:01.234Z","level":"INFO","logger":"cordfeeder.bot","msg":"bot setup complete","host":"myhost","app":"cordfeeder"}
```

SIGTERM triggers graceful shutdown — the poller finishes any in-flight requests before exiting.

## Slash commands

All commands live under the `/feed` group.

| Command | Who can use | Description |
|---------|-------------|-------------|
| `/feed add <url> [channel]` | Feed Manager | Subscribe to a feed. Accepts any URL — the bot auto-discovers the feed. |
| `/feed add <id> [channel]` | Feed Manager | Move an existing feed (by numeric ID) to a different channel. |
| `/feed remove <id>` | Feed Manager | Unsubscribe from a feed. Cleans up all state. |
| `/feed list` | Everyone | Show all feeds for this server with their IDs, channels, and poll intervals. |
| `/feed preview <url or id>` | Everyone | Fetch a feed and show the latest item without subscribing. |
| `/feed config` | Feed Manager | Show bot status: total feeds, error count, default poll interval. |

All command responses are ephemeral (only visible to the person who ran the command). Feed item posts are public.

## Feed auto-discovery

You do not need to find the raw RSS/Atom URL. Paste the homepage of a blog or any webpage and CordFeeder will find the feed for you.

Discovery tries three strategies in order:

1. **Direct parse** — if the URL already returns valid RSS/Atom, use it as-is
2. **HTML autodiscovery** — scan `<link rel="alternate" type="application/rss+xml">` tags in the page HTML
3. **Well-known path probing** — try common paths like `/feed`, `/feed.xml`, `/rss.xml`, `/atom.xml`, `/index.xml`, `/rss`, `/blog/feed`, `/feed.json`

If none of those work, the command responds with an error.

## Message formatting

Each new feed item posts as a plain Discord message (no embed) in this format:

```
**Feed Name** · [Article Title](<https://example.com/post>) · 2h ago
> Summary text truncated at word boundary to 300 characters...
```

The URL is wrapped in `<>` to suppress Discord's automatic link preview, keeping channels tidy.

For image-primary feeds (e.g. webcomics where the image is the content), the image URL is shown inline instead of the summary. For text-rich items, images are treated as decorative and the summary text is shown instead.

The formatter also strips newsletter boilerplate: if ≥80% of items in a feed share a common prefix or suffix of 20+ characters, it is removed automatically.

Here is how the parser handles a minimal RSS feed item:

```python

import sys
sys.path.insert(0, '.')
from cordfeeder.parser import parse_feed
from cordfeeder.formatter import format_item_message

rss = '''<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Example Blog</title>
    <item>
      <title>My First Post</title>
      <link>https://example.com/first-post</link>
      <description>&lt;p&gt;This is the &lt;strong&gt;post summary&lt;/strong&gt; with some HTML.&lt;/p&gt;</description>
      <pubDate>Mon, 12 Jan 2026 09:00:00 +0000</pubDate>
      <guid>https://example.com/first-post</guid>
    </item>
  </channel>
</rss>'''

items = parse_feed(rss)
item = items[0]
print('Parsed item:')
print(f'  title:   {item.title}')
print(f'  guid:    {item.guid}')
print(f'  summary: {item.summary!r}  (HTML stripped)')
print()
msg = format_item_message(item=item, feed_name='Example Blog', feed_id=1)
print('Formatted Discord message:')
print(msg)

```

```output
Parsed item:
  title:   My First Post
  guid:    https://example.com/first-post
  summary: 'This is the post summary with some HTML.'  (HTML stripped)

Formatted Discord message:
**Example Blog** · [My First Post](<https://example.com/first-post>) · 12 Jan 2026
> This is the post summary with some HTML.
```

## Polling behaviour

The poll loop runs every 30 seconds and checks for feeds where `next_poll_at <= now`. Polling all due feeds runs concurrently (limited to 2 simultaneous requests per host).

### Adaptive intervals

After each successful fetch, CordFeeder calculates the average gap between item publish dates and sets the next poll interval to half that value, clamped between 5 minutes and 12 hours. This means a feed that publishes twice a day gets checked every ~6 hours; a feed that publishes every 10 minutes gets checked every ~5 minutes (the minimum).

Newly added feeds use the default poll interval (15 minutes) for a warmup period of 3 poll cycles (~45 minutes) before the adaptive algorithm takes over. This prevents a feed with infrequent historical posts from immediately jumping to a multi-hour interval, ensuring new posts are detected promptly after subscribing.

```python

import sys
sys.path.insert(0, '.')
from datetime import datetime, timezone, timedelta
from cordfeeder.poller import calculate_adaptive_interval

# Build timestamp sequences for different publishing cadences
def make_ts(n, gap_minutes):
    base = datetime(2026, 1, 20, tzinfo=timezone.utc)
    return [base - timedelta(minutes=i * gap_minutes) for i in range(n)]

cases = [
    ('Every 10 min   (high-frequency)',  make_ts(10, 10)),
    ('Hourly',                            make_ts(24, 60)),
    ('Once daily',                        make_ts(7, 1440)),
    ('Weekly',                            make_ts(5, 10080)),
]
for name, timestamps in cases:
    interval = calculate_adaptive_interval(timestamps)
    hours = interval / 3600
    print(f'{name:<40}  -> poll every {hours:.1f}h ({interval}s)')

```

```output
Every 10 min   (high-frequency)           -> poll every 0.1h (300s)
Hourly                                    -> poll every 0.5h (1800s)
Once daily                                -> poll every 12.0h (43200s)
Weekly                                    -> poll every 12.0h (43200s)
```

The interval is the *floor* — jitter of 0–25% is added on top so not every feed polls at exactly the same moment.

### Error backoff

When a feed fails to fetch, the next interval grows exponentially:

```
next_interval = min(base_interval × 2^consecutive_errors, 86400) + jitter
```

A feed that fails five times in a row with a 15-minute base interval will wait up to 8 hours before the next attempt. The error count resets on any successful fetch.

## Security

Feed content is untrusted. CordFeeder applies several layers of sanitisation before sending anything to Discord:

- **Mention injection** — `@everyone`, `@here`, and `<@user>` patterns are neutralised by inserting a zero-width space after the `@`
- **Markdown injection** — title text is escaped so feed authors can't inject bold, italic, spoilers, or other Discord markdown
- **URL sanitisation** — item links are validated to be `http://` or `https://`; any whitespace or `>` characters that could break the angle-bracket URL wrapper are stripped or encoded
- **Newline injection** — titles and feed names are stripped of newlines to prevent header smuggling
- **Size limit** — feed responses larger than 5 MB are rejected to prevent memory exhaustion
- **No privileged intents** — the bot does not request `message_content` or any other privileged gateway intent
- **AllowedMentions.none()** — even if sanitisation misses something, Discord.py's allowed\_mentions override prevents the bot from actually pinging anyone

```python
import sys
sys.path.insert(0, '.')
from cordfeeder.formatter import format_item_message
from cordfeeder.parser import FeedItem

attacks = [
    ('Mention injection',   '@everyone free giveaway!'),
    ('Markdown injection',  '**bold** and ||spoiler|| attempt'),
    ('URL breakout',        'https://evil.com/> @everyone pwned'),
]
for label, payload in attacks:
    item = FeedItem(
        title=payload, link='https://example.com/post', guid='x',
        summary='', author=None, published=None, image_url=None,
    )
    msg = format_item_message(item=item, feed_name='Legit Blog', feed_id=42)
    first_line = msg.splitlines()[0]
    print(label + ':')
    print('  Input:  ' + repr(payload))
    print('  Output: ' + repr(first_line))
    print()
```

```output
Mention injection:
  Input:  '@everyone free giveaway!'
  Output: '**Legit Blog** · [@\u200beveryone free giveaway!](<https://example.com/post>)'

Markdown injection:
  Input:  '**bold** and ||spoiler|| attempt'
  Output: '**Legit Blog** · [\\*\\*bold\\*\\* and \\|\\|spoiler\\|\\| attempt](<https://example.com/post>)'

URL breakout:
  Input:  'https://evil.com/> @everyone pwned'
  Output: '**Legit Blog** · [https://evil.com/\\> @\u200beveryone pwned](<https://example.com/post>)'

```

## Database

State is stored in two SQLite tables:

- **`feeds`** — one row per subscribed feed: identity (URL, name, channel, guild, who added it) and polling state (ETag, Last-Modified, next poll time, error count)
- **`posted_items`** — GUID of every item ever posted; rows older than 90 days are pruned daily

Cascaded deletes keep everything consistent: removing a feed cleans up all its posted-item records automatically. The database runs in WAL mode for better concurrent read performance.

## Development

```bash
uv sync               # install all dependencies including dev extras
uv run pytest         # run the full test suite
```

The test suite covers the parser, formatter, poller, database layer, bot commands, and end-to-end integration tests.

```bash
uv run pytest --tb=no -q 2>&1
```

```output
........................................................................ [ 83%]
..............                                                           [100%]
86 passed, 1 warning in 0.29s
```

## Structured logging

Every significant event emits a single-line JSON object. This makes logs easy to ship to Datadog, Loki, CloudWatch, or any other log aggregator.

```python
import sys, logging, json
sys.path.insert(0, '.')
from cordfeeder.main import JSONFormatter

formatter = JSONFormatter()
record = logging.LogRecord(
    name='cordfeeder.poller', level=logging.INFO,
    pathname='', lineno=0, msg='poll complete', args=(), exc_info=None,
)
record.__dict__.update({'feed_id': 7, 'new_items': 3, 'next_interval': 1800})
parsed = json.loads(formatter.format(record))
parsed['ts'] = '2026-02-27T10:00:02.345Z'   # stabilise timestamp for demo
parsed['host'] = 'myhost'
print(json.dumps(parsed, indent=2))
```

```output
{
  "ts": "2026-02-27T10:00:02.345Z",
  "level": "INFO",
  "logger": "cordfeeder.poller",
  "msg": "poll complete",
  "host": "myhost",
  "app": "cordfeeder",
  "feed_id": 7,
  "new_items": 3,
  "next_interval": 1800
}
```

Every log line includes `ts` (ISO 8601 UTC), `level`, `logger`, `msg`, `host`, `app`, plus any structured fields relevant to that event (feed IDs, item counts, error details, etc.). Exceptions are captured as `err.type`, `err.msg`, and `err.stack`.

## Deployment

CordFeeder runs directly under systemd on a DigitalOcean droplet. `infra/deploy.sh` handles ongoing deploys; `infra/setup.sh` provisions the droplet from scratch.

### DigitalOcean deployment

Prerequisites: [doctl](https://docs.digitalocean.com/reference/doctl/) CLI and an SSH key at `~/.ssh/id_rsa.pub`.

```bash
brew install doctl
doctl auth init    # paste your DO API token
```

**Provision a droplet** (one-time):

```bash
./infra/setup.sh
```

This creates a \$6/mo droplet (Ubuntu 24.04, 1 vCPU, 1 GB), installs uv, clones the repo, and enables the systemd unit. Override defaults with `DROPLET_NAME`, `DROPLET_REGION`, or `DROPLET_SIZE` env vars.

**Copy your secrets and deploy:**

```bash
scp .env root@<DROPLET_IP>:~/cordfeeder/.env
DROPLET_IP=<DROPLET_IP> ./infra/deploy.sh
```

`infra/deploy.sh` SSHes in, pulls the latest code, syncs dependencies via uv, and restarts the systemd service. On subsequent deploys, just run `infra/deploy.sh` — it looks up the IP via doctl if `DROPLET_IP` is not set.

**Tear down** when you are done:

```bash
./infra/teardown.sh
```

See [infra/README.md](infra/README.md) for full configuration reference.

### Monitoring

Tail live logs from the droplet:

```bash
ssh root@<DROPLET_IP> journalctl -u cordfeeder -f
```

Filter for errors:

```bash
ssh root@<DROPLET_IP> journalctl -u cordfeeder --no-pager | jq 'select(.level == "ERROR")'
```

Check service status:

```bash
ssh root@<DROPLET_IP> systemctl status cordfeeder
```

SIGTERM triggers graceful shutdown: the bot closes the Discord WebSocket, the poller drains in-flight requests, and the database connection is cleanly closed before the process exits.

## See also

- [Discord bot setup guide](docs/discord-bot-setup.md) — step-by-step instructions for creating the Discord application, bot token, and invite URL
