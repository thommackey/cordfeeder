# CordFeeder Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an RSS-to-Discord bot that polls feeds and posts new items as embeds, controlled via slash commands.

**Architecture:** Single-process Python app — async Discord bot + background feed poller, SQLite for persistence. All interaction via Discord slash commands.

**Tech Stack:** Python 3.12+, discord.py, feedparser, aiosqlite, aiohttp, uv

---

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `cordfeeder/__init__.py`
- Create: `cordfeeder/config.py`
- Create: `tests/__init__.py`

**Step 1: Create pyproject.toml**

```toml
[project]
name = "cordfeeder"
version = "0.1.0"
description = "RSS/Atom feed reader that posts to Discord channels"
requires-python = ">=3.12"
dependencies = [
    "discord.py>=2.4,<3",
    "feedparser>=6.0,<7",
    "aiosqlite>=0.20,<1",
    "aiohttp>=3.9,<4",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
]

[project.scripts]
cordfeeder = "cordfeeder.main:main"
```

**Step 2: Create .env.example**

```
DISCORD_BOT_TOKEN=your-bot-token-here
FEED_MANAGER_ROLE=Feed Manager
DEFAULT_POLL_INTERVAL=900
DATABASE_PATH=cordfeeder.db
LOG_LEVEL=INFO
```

**Step 3: Create .gitignore**

```
__pycache__/
*.pyc
.env
*.db
*.db-journal
.venv/
dist/
*.egg-info/
.pytest_cache/
```

**Step 4: Create cordfeeder/config.py**

```python
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    discord_token: str
    feed_manager_role: str
    default_poll_interval: int
    database_path: str
    log_level: str
    min_poll_interval: int = 300      # 5 minutes
    max_poll_interval: int = 43200    # 12 hours
    max_items_per_poll: int = 5
    initial_items_count: int = 3
    user_agent: str = "CordFeeder/1.0 (Discord RSS bot)"

    @classmethod
    def from_env(cls) -> Config:
        token = os.environ.get("DISCORD_BOT_TOKEN", "")
        if not token:
            raise ValueError("DISCORD_BOT_TOKEN environment variable is required")
        return cls(
            discord_token=token,
            feed_manager_role=os.environ.get("FEED_MANAGER_ROLE", "Feed Manager"),
            default_poll_interval=int(os.environ.get("DEFAULT_POLL_INTERVAL", "900")),
            database_path=os.environ.get("DATABASE_PATH", "cordfeeder.db"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
```

**Step 5: Create empty __init__.py files**

`cordfeeder/__init__.py` and `tests/__init__.py` — both empty.

**Step 6: Install dependencies**

Run: `uv init --no-readme && uv add "discord.py>=2.4,<3" "feedparser>=6.0,<7" "aiosqlite>=0.20,<1" "aiohttp>=3.9,<4" && uv add --dev "pytest>=8.0" "pytest-asyncio>=0.24"`

Note: uv will create its own pyproject.toml. Merge the project metadata from step 1 into it, or let uv manage the file and just add our cordfeeder-specific fields.

**Step 7: Verify**

Run: `uv run python -c "from cordfeeder.config import Config; print('OK')"`
Expected: `OK`

**Step 8: Commit**

```bash
git add pyproject.toml .env.example .gitignore cordfeeder/ tests/ uv.lock
git commit -m "Scaffold project with config and dependencies"
```

---

### Task 2: Database Layer

**Files:**
- Create: `cordfeeder/database.py`
- Create: `tests/test_database.py`

**Step 1: Write failing tests for database operations**

```python
import pytest
import pytest_asyncio
from cordfeeder.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialise()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_add_feed(db):
    feed_id = await db.add_feed(
        url="https://example.com/rss",
        name="Example Feed",
        channel_id=123456,
        guild_id=789012,
        added_by=345678,
    )
    assert feed_id == 1
    feed = await db.get_feed(feed_id)
    assert feed["url"] == "https://example.com/rss"
    assert feed["name"] == "Example Feed"
    assert feed["channel_id"] == 123456


@pytest.mark.asyncio
async def test_remove_feed(db):
    feed_id = await db.add_feed(
        url="https://example.com/rss",
        name="Example",
        channel_id=123,
        guild_id=456,
        added_by=789,
    )
    await db.remove_feed(feed_id)
    feed = await db.get_feed(feed_id)
    assert feed is None


@pytest.mark.asyncio
async def test_list_feeds_by_guild(db):
    await db.add_feed("https://a.com/rss", "A", 1, 100, 1)
    await db.add_feed("https://b.com/rss", "B", 2, 100, 1)
    await db.add_feed("https://c.com/rss", "C", 3, 200, 1)  # different guild
    feeds = await db.list_feeds(guild_id=100)
    assert len(feeds) == 2


@pytest.mark.asyncio
async def test_duplicate_feed_url_same_guild(db):
    await db.add_feed("https://a.com/rss", "A", 1, 100, 1)
    with pytest.raises(Exception):
        await db.add_feed("https://a.com/rss", "A", 2, 100, 1)


@pytest.mark.asyncio
async def test_feed_state_created_on_add(db):
    feed_id = await db.add_feed("https://a.com/rss", "A", 1, 100, 1)
    state = await db.get_feed_state(feed_id)
    assert state is not None
    assert state["consecutive_errors"] == 0


@pytest.mark.asyncio
async def test_update_feed_state(db):
    feed_id = await db.add_feed("https://a.com/rss", "A", 1, 100, 1)
    await db.update_feed_state(feed_id, etag='"abc123"', last_modified="Thu, 01 Jan 2026 00:00:00 GMT")
    state = await db.get_feed_state(feed_id)
    assert state["etag"] == '"abc123"'


@pytest.mark.asyncio
async def test_record_posted_item(db):
    feed_id = await db.add_feed("https://a.com/rss", "A", 1, 100, 1)
    await db.record_posted_item(feed_id, "guid-1", message_id=111)
    assert await db.is_item_posted(feed_id, "guid-1") is True
    assert await db.is_item_posted(feed_id, "guid-2") is False


@pytest.mark.asyncio
async def test_duplicate_posted_item_ignored(db):
    feed_id = await db.add_feed("https://a.com/rss", "A", 1, 100, 1)
    await db.record_posted_item(feed_id, "guid-1", message_id=111)
    await db.record_posted_item(feed_id, "guid-1", message_id=222)  # should not raise
    # just verify it didn't crash — idempotent


@pytest.mark.asyncio
async def test_get_due_feeds(db):
    feed_id = await db.add_feed("https://a.com/rss", "A", 1, 100, 1)
    # newly added feed should be immediately due
    due = await db.get_due_feeds()
    assert len(due) == 1
    assert due[0]["feed_id"] == feed_id


@pytest.mark.asyncio
async def test_remove_feed_cascades(db):
    feed_id = await db.add_feed("https://a.com/rss", "A", 1, 100, 1)
    await db.record_posted_item(feed_id, "guid-1", message_id=111)
    await db.remove_feed(feed_id)
    assert await db.is_item_posted(feed_id, "guid-1") is False
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_database.py -v`
Expected: FAIL — `cannot import name 'Database'`

**Step 3: Implement database.py**

```python
from __future__ import annotations

import aiosqlite
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    name TEXT NOT NULL,
    channel_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    added_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(url, guild_id)
);

CREATE TABLE IF NOT EXISTS feed_state (
    feed_id INTEGER PRIMARY KEY REFERENCES feeds(id) ON DELETE CASCADE,
    etag TEXT,
    last_modified TEXT,
    last_poll_at TEXT,
    next_poll_at TEXT,
    poll_interval INTEGER NOT NULL DEFAULT 900,
    consecutive_errors INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS posted_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    item_guid TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    message_id INTEGER,
    UNIQUE(feed_id, item_guid)
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def initialise(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def add_feed(
        self,
        url: str,
        name: str,
        channel_id: int,
        guild_id: int,
        added_by: int,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "INSERT INTO feeds (url, name, channel_id, guild_id, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (url, name, channel_id, guild_id, added_by, now),
        )
        feed_id = cursor.lastrowid
        await self._db.execute(
            "INSERT INTO feed_state (feed_id, next_poll_at, poll_interval) VALUES (?, ?, 900)",
            (feed_id, now),
        )
        await self._db.commit()
        return feed_id

    async def remove_feed(self, feed_id: int) -> None:
        await self._db.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
        await self._db.commit()

    async def get_feed(self, feed_id: int) -> dict | None:
        cursor = await self._db.execute("SELECT * FROM feeds WHERE id = ?", (feed_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_feeds(self, guild_id: int) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT f.*, fs.last_poll_at, fs.poll_interval, fs.consecutive_errors, fs.last_error "
            "FROM feeds f JOIN feed_state fs ON f.id = fs.feed_id "
            "WHERE f.guild_id = ? ORDER BY f.id",
            (guild_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_feed_state(self, feed_id: int) -> dict | None:
        cursor = await self._db.execute("SELECT * FROM feed_state WHERE feed_id = ?", (feed_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def update_feed_state(self, feed_id: int, **kwargs) -> None:
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values())
        vals.append(feed_id)
        await self._db.execute(f"UPDATE feed_state SET {sets} WHERE feed_id = ?", vals)
        await self._db.commit()

    async def record_posted_item(self, feed_id: int, item_guid: str, message_id: int | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT OR IGNORE INTO posted_items (feed_id, item_guid, posted_at, message_id) VALUES (?, ?, ?, ?)",
            (feed_id, item_guid, now, message_id),
        )
        await self._db.commit()

    async def is_item_posted(self, feed_id: int, item_guid: str) -> bool:
        cursor = await self._db.execute(
            "SELECT 1 FROM posted_items WHERE feed_id = ? AND item_guid = ?",
            (feed_id, item_guid),
        )
        return await cursor.fetchone() is not None

    async def get_due_feeds(self) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "SELECT fs.*, f.url, f.name, f.channel_id, f.guild_id "
            "FROM feed_state fs JOIN feeds f ON fs.feed_id = f.id "
            "WHERE fs.next_poll_at <= ? "
            "ORDER BY fs.next_poll_at",
            (now,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def prune_old_items(self, days: int = 90) -> int:
        cutoff = datetime.now(timezone.utc).isoformat()
        # Calculate cutoff properly in the query
        cursor = await self._db.execute(
            "DELETE FROM posted_items WHERE posted_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        await self._db.commit()
        return cursor.rowcount
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_database.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add cordfeeder/database.py tests/test_database.py
git commit -m "Add database layer with schema and CRUD operations"
```

---

### Task 3: Feed Parser

**Files:**
- Create: `cordfeeder/parser.py`
- Create: `tests/test_parser.py`
- Create: `tests/fixtures/` (sample feed XML files)

**Step 1: Create test fixture files**

Create `tests/fixtures/sample_rss.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com</link>
    <description>A test RSS feed</description>
    <ttl>60</ttl>
    <item>
      <title>Third Post</title>
      <link>https://example.com/3</link>
      <guid>https://example.com/3</guid>
      <description>&lt;p&gt;This is the &lt;b&gt;third&lt;/b&gt; post with &lt;a href="https://example.com"&gt;HTML&lt;/a&gt; content.&lt;/p&gt;</description>
      <author>alice@example.com (Alice)</author>
      <pubDate>Wed, 25 Feb 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Second Post</title>
      <link>https://example.com/2</link>
      <guid>https://example.com/2</guid>
      <description>Second post content</description>
      <pubDate>Tue, 24 Feb 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>First Post</title>
      <link>https://example.com/1</link>
      <guid>https://example.com/1</guid>
      <description>First post content</description>
      <pubDate>Mon, 23 Feb 2026 12:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
```

Create `tests/fixtures/sample_atom.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test Atom Feed</title>
  <link href="https://example.com" rel="alternate"/>
  <id>urn:uuid:test-atom-feed</id>
  <entry>
    <title>Atom Entry</title>
    <link href="https://example.com/atom/1" rel="alternate"/>
    <id>urn:uuid:entry-1</id>
    <summary>An Atom entry summary</summary>
    <updated>2026-02-25T12:00:00Z</updated>
    <author><name>Bob</name></author>
  </entry>
</feed>
```

**Step 2: Write failing tests**

```python
from pathlib import Path

import pytest

from cordfeeder.parser import FeedItem, parse_feed, extract_feed_metadata

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_rss_feed():
    xml = (FIXTURES / "sample_rss.xml").read_text()
    items = parse_feed(xml)
    assert len(items) == 3
    assert items[0].title == "Third Post"
    assert items[0].link == "https://example.com/3"
    assert items[0].guid == "https://example.com/3"


def test_parse_atom_feed():
    xml = (FIXTURES / "sample_atom.xml").read_text()
    items = parse_feed(xml)
    assert len(items) == 1
    assert items[0].title == "Atom Entry"
    assert items[0].guid == "urn:uuid:entry-1"


def test_html_stripped_from_summary():
    xml = (FIXTURES / "sample_rss.xml").read_text()
    items = parse_feed(xml)
    # Third Post has HTML in description
    assert "<" not in items[0].summary
    assert "third" in items[0].summary.lower()


def test_summary_truncated():
    long_desc = "word " * 200  # 1000 chars
    xml = f"""<?xml version="1.0"?>
    <rss version="2.0"><channel><title>T</title>
    <item><title>Long</title><link>https://x.com/1</link>
    <guid>1</guid><description>{long_desc}</description></item>
    </channel></rss>"""
    items = parse_feed(xml)
    assert len(items[0].summary) <= 303  # 300 + "..."


def test_extract_feed_metadata():
    xml = (FIXTURES / "sample_rss.xml").read_text()
    meta = extract_feed_metadata(xml)
    assert meta.title == "Test Feed"
    assert meta.link == "https://example.com"
    assert meta.ttl == 60


def test_items_have_guid_fallback_to_link():
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>T</title>
    <item><title>No GUID</title><link>https://x.com/1</link>
    <description>desc</description></item>
    </channel></rss>"""
    items = parse_feed(xml)
    assert items[0].guid == "https://x.com/1"


def test_parse_invalid_feed():
    with pytest.raises(ValueError, match="parse"):
        parse_feed("this is not xml at all")
```

**Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_parser.py -v`
Expected: FAIL — `cannot import name 'FeedItem'`

**Step 4: Implement parser.py**

```python
from __future__ import annotations

import html
import re
from dataclasses import dataclass

import feedparser


@dataclass
class FeedItem:
    title: str
    link: str
    guid: str
    summary: str
    author: str | None
    published: str | None
    image_url: str | None


@dataclass
class FeedMetadata:
    title: str
    link: str | None
    description: str | None
    ttl: int | None
    image_url: str | None


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    clean = re.sub(r"<[^>]+>", "", text)
    return html.unescape(clean).strip()


def _truncate(text: str, max_len: int = 300) -> str:
    """Truncate at a word boundary."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len].rsplit(" ", 1)[0]
    return truncated + "..."


def _extract_image(entry: dict) -> str | None:
    """Try to find an image URL from an entry."""
    # Check media_content
    media = entry.get("media_content", [])
    for m in media:
        if m.get("medium") == "image" or (m.get("type", "").startswith("image/")):
            return m.get("url")
    # Check media_thumbnail
    thumbs = entry.get("media_thumbnail", [])
    if thumbs:
        return thumbs[0].get("url")
    # Check enclosures
    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith("image/"):
            return enc.get("href")
    return None


def parse_feed(raw: str) -> list[FeedItem]:
    """Parse raw RSS/Atom XML into a list of FeedItems."""
    parsed = feedparser.parse(raw)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Failed to parse feed: {parsed.bozo_exception}")

    items = []
    for entry in parsed.entries:
        summary_raw = entry.get("summary", "") or entry.get("description", "") or ""
        summary = _truncate(_strip_html(summary_raw))

        guid = entry.get("id") or entry.get("link", "")
        link = entry.get("link", "")
        author = entry.get("author")
        published = entry.get("published") or entry.get("updated")

        items.append(
            FeedItem(
                title=entry.get("title", "Untitled"),
                link=link,
                guid=guid,
                summary=summary,
                author=author,
                published=published,
                image_url=_extract_image(entry),
            )
        )
    return items


def extract_feed_metadata(raw: str) -> FeedMetadata:
    """Extract feed-level metadata."""
    parsed = feedparser.parse(raw)
    if parsed.bozo and not parsed.feed:
        raise ValueError(f"Failed to parse feed: {parsed.bozo_exception}")

    feed = parsed.feed
    ttl = None
    if hasattr(feed, "ttl"):
        try:
            ttl = int(feed.ttl)
        except (ValueError, TypeError):
            pass

    image_url = None
    if hasattr(feed, "image") and hasattr(feed.image, "href"):
        image_url = feed.image.href

    return FeedMetadata(
        title=feed.get("title", "Unknown Feed"),
        link=feed.get("link"),
        description=feed.get("subtitle") or feed.get("description"),
        ttl=ttl,
        image_url=image_url,
    )
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_parser.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add cordfeeder/parser.py tests/test_parser.py tests/fixtures/
git commit -m "Add feed parser with HTML stripping, truncation, and metadata extraction"
```

---

### Task 4: Embed Formatter

**Files:**
- Create: `cordfeeder/formatter.py`
- Create: `tests/test_formatter.py`

**Step 1: Write failing tests**

```python
import discord
import pytest

from cordfeeder.formatter import format_item_embed, feed_colour
from cordfeeder.parser import FeedItem


def test_format_basic_embed():
    item = FeedItem(
        title="Test Article",
        link="https://example.com/1",
        guid="1",
        summary="A summary of the article.",
        author="Alice",
        published="Wed, 25 Feb 2026 12:00:00 GMT",
        image_url=None,
    )
    embed = format_item_embed(item, feed_name="Test Feed", feed_url="https://example.com/rss", feed_id=3)
    assert isinstance(embed, discord.Embed)
    assert embed.title == "Test Article"
    assert embed.url == "https://example.com/1"
    assert "summary" in embed.description.lower()
    assert "3" in embed.footer.text  # feed ID in footer


def test_format_embed_with_image():
    item = FeedItem(
        title="Image Post",
        link="https://example.com/2",
        guid="2",
        summary="Has an image.",
        author=None,
        published=None,
        image_url="https://example.com/img.jpg",
    )
    embed = format_item_embed(item, feed_name="Feed", feed_url="https://example.com/rss", feed_id=1)
    assert embed.thumbnail.url == "https://example.com/img.jpg"


def test_format_embed_no_summary():
    item = FeedItem(
        title="Title Only",
        link="https://example.com/3",
        guid="3",
        summary="",
        author=None,
        published=None,
        image_url=None,
    )
    embed = format_item_embed(item, feed_name="Feed", feed_url="https://example.com/rss", feed_id=1)
    assert embed.description is None or embed.description == ""


def test_feed_colour_consistent():
    c1 = feed_colour("https://example.com/rss")
    c2 = feed_colour("https://example.com/rss")
    c3 = feed_colour("https://other.com/rss")
    assert c1 == c2
    assert c1 != c3  # very unlikely collision
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_formatter.py -v`
Expected: FAIL — `cannot import name 'format_item_embed'`

**Step 3: Implement formatter.py**

```python
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import discord
from dateutil import parser as dateutil_parser

from cordfeeder.parser import FeedItem


def feed_colour(feed_url: str) -> discord.Colour:
    """Generate a consistent colour from a feed URL."""
    h = hashlib.md5(feed_url.encode()).hexdigest()[:6]
    return discord.Colour(int(h, 16))


def _format_date(published: str | None) -> str | None:
    """Format a publish date for display."""
    if not published:
        return None
    try:
        dt = dateutil_parser.parse(published)
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = now - dt
        if delta.total_seconds() < 3600:
            mins = int(delta.total_seconds() / 60)
            return f"{mins}m ago" if mins > 0 else "just now"
        if delta.total_seconds() < 86400:
            hours = int(delta.total_seconds() / 3600)
            return f"{hours}h ago"
        return dt.strftime("%-d %b %Y")
    except (ValueError, TypeError):
        return None


def format_item_embed(
    item: FeedItem,
    feed_name: str,
    feed_url: str,
    feed_id: int,
    feed_icon_url: str | None = None,
) -> discord.Embed:
    """Format a feed item as a Discord embed."""
    embed = discord.Embed(
        title=item.title,
        url=item.link,
        description=item.summary or None,
        colour=feed_colour(feed_url),
    )
    embed.set_author(name=feed_name, icon_url=feed_icon_url)

    if item.image_url:
        embed.set_thumbnail(url=item.image_url)

    footer_parts = []
    date_str = _format_date(item.published)
    if date_str:
        footer_parts.append(date_str)
    footer_parts.append(f"feed ID: {feed_id}")
    embed.set_footer(text=" · ".join(footer_parts))

    return embed
```

Note: this introduces a `python-dateutil` dependency. Add it:

Run: `uv add "python-dateutil>=2.9,<3"`

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_formatter.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add cordfeeder/formatter.py tests/test_formatter.py
git commit -m "Add embed formatter with consistent feed colours and date formatting"
```

---

### Task 5: Feed Poller

**Files:**
- Create: `cordfeeder/poller.py`
- Create: `tests/test_poller.py`

**Step 1: Write failing tests**

These tests focus on the polling logic — scheduling, adaptive intervals, error backoff — without actually hitting the network or Discord. We mock the HTTP client and the Discord posting.

```python
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from cordfeeder.config import Config
from cordfeeder.database import Database
from cordfeeder.poller import Poller, calculate_adaptive_interval

FIXTURES = Path(__file__).parent / "fixtures"


def make_config(**overrides):
    defaults = dict(
        discord_token="fake",
        feed_manager_role="Feed Manager",
        default_poll_interval=900,
        database_path=":memory:",
        log_level="DEBUG",
    )
    defaults.update(overrides)
    return Config(**defaults)


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialise()
    yield database
    await database.close()


def test_adaptive_interval_frequent_posts():
    # Posts every 2 hours — should poll about every hour
    timestamps = [
        datetime(2026, 2, 26, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 2, 26, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 2, 26, 8, 0, tzinfo=timezone.utc),
        datetime(2026, 2, 26, 6, 0, tzinfo=timezone.utc),
    ]
    interval = calculate_adaptive_interval(timestamps, min_interval=300, max_interval=43200)
    assert 3000 <= interval <= 4200  # roughly 1 hour


def test_adaptive_interval_daily_posts():
    timestamps = [
        datetime(2026, 2, 26, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 2, 24, 12, 0, tzinfo=timezone.utc),
    ]
    interval = calculate_adaptive_interval(timestamps, min_interval=300, max_interval=43200)
    assert interval == 43200  # half a day, matches max


def test_adaptive_interval_clamped_to_min():
    # Posts every 2 minutes — should clamp to min
    timestamps = [
        datetime(2026, 2, 26, 12, 4, tzinfo=timezone.utc),
        datetime(2026, 2, 26, 12, 2, tzinfo=timezone.utc),
        datetime(2026, 2, 26, 12, 0, tzinfo=timezone.utc),
    ]
    interval = calculate_adaptive_interval(timestamps, min_interval=300, max_interval=43200)
    assert interval == 300


def test_adaptive_interval_single_item():
    # Not enough data — return None to signal "use default"
    timestamps = [datetime(2026, 2, 26, 12, 0, tzinfo=timezone.utc)]
    interval = calculate_adaptive_interval(timestamps, min_interval=300, max_interval=43200)
    assert interval is None


@pytest.mark.asyncio
async def test_poller_fetches_due_feeds(db):
    config = make_config()
    feed_id = await db.add_feed("https://example.com/rss", "Test", 1, 100, 1)

    mock_session = AsyncMock()
    sample_xml = (FIXTURES / "sample_rss.xml").read_text()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.headers = {}
    mock_response.text = AsyncMock(return_value=sample_xml)
    mock_session.get = AsyncMock(return_value=mock_response)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    poller = Poller(config=config, db=db, bot=MagicMock())
    poller._session = mock_session

    items = await poller.fetch_feed(feed_id, "https://example.com/rss")
    assert len(items) == 3


@pytest.mark.asyncio
async def test_poller_handles_304(db):
    config = make_config()
    feed_id = await db.add_feed("https://example.com/rss", "Test", 1, 100, 1)
    await db.update_feed_state(feed_id, etag='"abc"')

    mock_session = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status = 304
    mock_response.headers = {}
    mock_session.get = AsyncMock(return_value=mock_response)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    poller = Poller(config=config, db=db, bot=MagicMock())
    poller._session = mock_session

    items = await poller.fetch_feed(feed_id, "https://example.com/rss")
    assert items is None  # None signals "not modified"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_poller.py -v`
Expected: FAIL — `cannot import name 'Poller'`

**Step 3: Implement poller.py**

```python
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta

import aiohttp
from dateutil import parser as dateutil_parser

from cordfeeder.config import Config
from cordfeeder.database import Database
from cordfeeder.formatter import format_item_embed
from cordfeeder.parser import FeedItem, FeedMetadata, parse_feed, extract_feed_metadata

logger = logging.getLogger("cordfeeder.poller")


def calculate_adaptive_interval(
    timestamps: list[datetime],
    min_interval: int = 300,
    max_interval: int = 43200,
) -> int | None:
    """Calculate polling interval from item timestamps.

    Returns interval in seconds, or None if not enough data.
    """
    if len(timestamps) < 2:
        return None

    sorted_ts = sorted(timestamps, reverse=True)
    gaps = []
    for i in range(len(sorted_ts) - 1):
        gap = (sorted_ts[i] - sorted_ts[i + 1]).total_seconds()
        if gap > 0:
            gaps.append(gap)

    if not gaps:
        return None

    avg_gap = sum(gaps) / len(gaps)
    interval = int(avg_gap / 2)
    return max(min_interval, min(interval, max_interval))


class Poller:
    def __init__(self, config: Config, db: Database, bot) -> None:
        self._config = config
        self._db = db
        self._bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._running = False
        self._host_semaphores: dict[str, asyncio.Semaphore] = {}

    async def start(self) -> None:
        """Start the polling loop."""
        self._session = aiohttp.ClientSession(
            headers={"User-Agent": self._config.user_agent},
        )
        self._running = True
        logger.info("poller_started")
        asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop the polling loop and clean up."""
        self._running = False
        if self._session:
            await self._session.close()
        logger.info("poller_stopped")

    async def _poll_loop(self) -> None:
        """Main loop — check for due feeds every 30 seconds."""
        while self._running:
            try:
                due_feeds = await self._db.get_due_feeds()
                if due_feeds:
                    logger.debug("due_feeds_found", extra={"count": len(due_feeds)})
                    tasks = [self._poll_feed(f) for f in due_feeds]
                    await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:
                logger.exception("poll_loop_error")
            await asyncio.sleep(30)

    def _get_host_semaphore(self, url: str) -> asyncio.Semaphore:
        """Get or create a semaphore for rate-limiting per host."""
        from urllib.parse import urlparse
        host = urlparse(url).hostname or "unknown"
        if host not in self._host_semaphores:
            self._host_semaphores[host] = asyncio.Semaphore(2)
        return self._host_semaphores[host]

    async def fetch_feed(self, feed_id: int, url: str) -> list[FeedItem] | None:
        """Fetch a feed URL with conditional GET. Returns items or None if not modified."""
        state = await self._db.get_feed_state(feed_id)
        headers = {"Accept-Encoding": "gzip"}
        if state and state.get("etag"):
            headers["If-None-Match"] = state["etag"]
        if state and state.get("last_modified"):
            headers["If-Modified-Since"] = state["last_modified"]

        sem = self._get_host_semaphore(url)
        async with sem:
            async with self._session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 304:
                    return None

                if resp.status == 301:
                    new_url = str(resp.url)
                    logger.info("feed_redirect", extra={"feed_id": feed_id, "new_url": new_url})
                    # Update the stored URL
                    feed = await self._db.get_feed(feed_id)
                    if feed:
                        await self._db.update_feed_url(feed_id, new_url)

                if resp.status == 410:
                    logger.warning("feed_gone", extra={"feed_id": feed_id, "url": url})
                    raise FeedGoneError(feed_id, url)

                if resp.status == 429 or resp.status == 403:
                    retry_after = resp.headers.get("Retry-After")
                    raise FeedRateLimitError(feed_id, url, retry_after)

                if resp.status >= 500:
                    raise FeedServerError(feed_id, url, resp.status)

                resp.raise_for_status()

                raw = await resp.text()
                new_etag = resp.headers.get("ETag")
                new_last_modified = resp.headers.get("Last-Modified")

                await self._db.update_feed_state(
                    feed_id,
                    etag=new_etag,
                    last_modified=new_last_modified,
                    last_poll_at=datetime.now(timezone.utc).isoformat(),
                )

                return parse_feed(raw)

    async def _poll_feed(self, feed_info: dict) -> None:
        """Poll a single feed and post new items."""
        feed_id = feed_info["feed_id"]
        url = feed_info["url"]
        feed_name = feed_info["name"]
        channel_id = feed_info["channel_id"]

        try:
            items = await self.fetch_feed(feed_id, url)

            if items is None:
                logger.debug("feed_not_modified", extra={"feed_id": feed_id})
                await self._schedule_next_poll(feed_id, feed_info["poll_interval"])
                return

            # Filter to unposted items
            new_items = []
            for item in items:
                if not await self._db.is_item_posted(feed_id, item.guid):
                    new_items.append(item)

            # Cap items per poll
            new_items = new_items[-self._config.max_items_per_poll:]

            # Post oldest first
            new_items.reverse()
            for item in new_items:
                await self._post_item(feed_id, feed_name, url, channel_id, item)

            # Calculate adaptive interval
            timestamps = self._extract_timestamps(items)
            adaptive = calculate_adaptive_interval(
                timestamps,
                min_interval=self._config.min_poll_interval,
                max_interval=self._config.max_poll_interval,
            )
            interval = adaptive or self._config.default_poll_interval

            # Respect feed TTL as floor
            try:
                raw = None  # We'd need to cache the raw feed — skip TTL for now
                # TTL handling is done on initial add via metadata
            except Exception:
                pass

            await self._db.update_feed_state(feed_id, consecutive_errors=0, last_error=None)
            await self._schedule_next_poll(feed_id, interval)
            logger.info("feed_polled", extra={"feed_id": feed_id, "new_items": len(new_items), "next_interval": interval})

        except FeedGoneError:
            channel = self._bot.get_channel(channel_id)
            if channel:
                await channel.send(f"Feed **{feed_name}** (ID: {feed_id}) returned 410 Gone — removing it.")
            await self._db.remove_feed(feed_id)

        except FeedRateLimitError as e:
            backoff = max(int(e.retry_after or 0), 14400)  # 4 hours minimum
            await self._db.update_feed_state(
                feed_id,
                consecutive_errors=feed_info["consecutive_errors"] + 1,
                last_error=f"Rate limited (429/403)",
            )
            await self._schedule_next_poll(feed_id, backoff)
            logger.warning("feed_rate_limited", extra={"feed_id": feed_id, "backoff": backoff})

        except Exception as e:
            errors = feed_info["consecutive_errors"] + 1
            backoff = min(feed_info["poll_interval"] * (2 ** errors), 86400)
            jitter = random.uniform(0, backoff * 0.25)
            await self._db.update_feed_state(
                feed_id,
                consecutive_errors=errors,
                last_error=str(e),
            )
            await self._schedule_next_poll(feed_id, int(backoff + jitter))
            logger.error("feed_poll_error", extra={"feed_id": feed_id, "error": str(e), "consecutive": errors})

    async def _post_item(self, feed_id: int, feed_name: str, feed_url: str, channel_id: int, item: FeedItem) -> None:
        """Post a single feed item to Discord."""
        channel = self._bot.get_channel(channel_id)
        if not channel:
            logger.warning("channel_not_found", extra={"feed_id": feed_id, "channel_id": channel_id})
            return

        embed = format_item_embed(item, feed_name=feed_name, feed_url=feed_url, feed_id=feed_id)
        msg = await channel.send(embed=embed)
        await self._db.record_posted_item(feed_id, item.guid, message_id=msg.id)

    async def _schedule_next_poll(self, feed_id: int, interval: int) -> None:
        """Schedule the next poll with jitter."""
        jitter = random.uniform(0, interval * 0.25)
        next_poll = datetime.now(timezone.utc) + timedelta(seconds=interval + jitter)
        await self._db.update_feed_state(
            feed_id,
            poll_interval=interval,
            next_poll_at=next_poll.isoformat(),
        )

    @staticmethod
    def _extract_timestamps(items: list[FeedItem]) -> list[datetime]:
        """Extract parsed timestamps from feed items."""
        timestamps = []
        for item in items:
            if item.published:
                try:
                    dt = dateutil_parser.parse(item.published)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    timestamps.append(dt)
                except (ValueError, TypeError):
                    continue
        return timestamps


class FeedGoneError(Exception):
    def __init__(self, feed_id: int, url: str):
        self.feed_id = feed_id
        self.url = url


class FeedRateLimitError(Exception):
    def __init__(self, feed_id: int, url: str, retry_after: str | None):
        self.feed_id = feed_id
        self.url = url
        self.retry_after = retry_after


class FeedServerError(Exception):
    def __init__(self, feed_id: int, url: str, status: int):
        self.feed_id = feed_id
        self.url = url
        self.status = status
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_poller.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add cordfeeder/poller.py tests/test_poller.py
git commit -m "Add feed poller with adaptive intervals and error backoff"
```

---

### Task 6: Discord Bot & Slash Commands

**Files:**
- Create: `cordfeeder/bot.py`
- Create: `tests/test_bot.py`

**Step 1: Write failing tests for role checking and command logic**

The bot commands interact with Discord API, so tests focus on the permission checks and the business logic extracted into testable functions.

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock
from cordfeeder.bot import has_feed_manager_role


def _make_interaction(roles: list[str], guild_id: int = 100):
    """Create a mock Discord interaction with given role names."""
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    mock_roles = []
    for name in roles:
        role = MagicMock()
        role.name = name
        mock_roles.append(role)
    interaction.user = MagicMock()
    interaction.user.roles = mock_roles
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.channel = MagicMock()
    interaction.channel.id = 12345
    interaction.channel_id = 12345
    return interaction


def test_has_feed_manager_role():
    interaction = _make_interaction(["Feed Manager", "Member"])
    assert has_feed_manager_role(interaction, "Feed Manager") is True


def test_lacks_feed_manager_role():
    interaction = _make_interaction(["Member"])
    assert has_feed_manager_role(interaction, "Feed Manager") is False


def test_feed_manager_role_case_sensitive():
    interaction = _make_interaction(["feed manager"])
    assert has_feed_manager_role(interaction, "Feed Manager") is False
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bot.py -v`
Expected: FAIL — `cannot import name 'has_feed_manager_role'`

**Step 3: Implement bot.py**

```python
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cordfeeder.config import Config
from cordfeeder.database import Database
from cordfeeder.formatter import format_item_embed
from cordfeeder.parser import parse_feed, extract_feed_metadata
from cordfeeder.poller import Poller

logger = logging.getLogger("cordfeeder.bot")


def has_feed_manager_role(interaction: discord.Interaction, role_name: str) -> bool:
    """Check if the user has the required role."""
    return any(r.name == role_name for r in interaction.user.roles)


class FeedCog(commands.Cog):
    """Slash commands for managing RSS feeds."""

    feed_group = app_commands.Group(name="feed", description="Manage RSS feeds")

    def __init__(self, bot: CordFeederBot) -> None:
        self.bot = bot
        self.db = bot.db
        self.config = bot.config
        self.poller = bot.poller

    def _check_role(self, interaction: discord.Interaction) -> bool:
        return has_feed_manager_role(interaction, self.config.feed_manager_role)

    @feed_group.command(name="add", description="Add an RSS/Atom feed to this channel")
    @app_commands.describe(url="The RSS/Atom feed URL", channel="Channel to post to (defaults to current)")
    async def feed_add(
        self,
        interaction: discord.Interaction,
        url: str,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not self._check_role(interaction):
            await interaction.response.send_message(
                f"You need the **{self.config.feed_manager_role}** role to manage feeds.",
                ephemeral=True,
            )
            return

        target_channel = channel or interaction.channel
        await interaction.response.defer(ephemeral=True)

        try:
            # Validate the feed by fetching it
            async with self.bot.poller._session.get(
                url,
                headers={"User-Agent": self.config.user_agent, "Accept-Encoding": "gzip"},
                timeout=__import__("aiohttp").ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                raw = await resp.text()

            metadata = extract_feed_metadata(raw)
            items = parse_feed(raw)

            feed_id = await self.db.add_feed(
                url=url,
                name=metadata.title,
                channel_id=target_channel.id,
                guild_id=interaction.guild_id,
                added_by=interaction.user.id,
            )

            # Post the initial items (most recent N)
            initial = items[:self.config.initial_items_count]
            initial.reverse()  # oldest first
            for item in initial:
                embed = format_item_embed(item, feed_name=metadata.title, feed_url=url, feed_id=feed_id)
                msg = await target_channel.send(embed=embed)
                await self.db.record_posted_item(feed_id, item.guid, message_id=msg.id)

            await interaction.followup.send(
                f"Feed added: **{metadata.title}** (ID: {feed_id}) → {target_channel.mention}",
                ephemeral=True,
            )
            logger.info("feed_added", extra={"feed_id": feed_id, "url": url, "guild_id": interaction.guild_id})

        except ValueError as e:
            await interaction.followup.send(f"That URL doesn't appear to be a valid RSS/Atom feed: {e}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Failed to add feed: {e}", ephemeral=True)
            logger.error("feed_add_error", extra={"url": url, "error": str(e)})

    @feed_group.command(name="remove", description="Remove a feed by its ID")
    @app_commands.describe(feed_id="The feed ID (use /feed list to see IDs)")
    async def feed_remove(self, interaction: discord.Interaction, feed_id: int) -> None:
        if not self._check_role(interaction):
            await interaction.response.send_message(
                f"You need the **{self.config.feed_manager_role}** role to manage feeds.",
                ephemeral=True,
            )
            return

        feed = await self.db.get_feed(feed_id)
        if not feed or feed["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("Feed not found.", ephemeral=True)
            return

        feed_name = feed["name"]
        await self.db.remove_feed(feed_id)
        await interaction.response.send_message(
            f"Removed feed **{feed_name}** (ID: {feed_id})",
            ephemeral=True,
        )
        logger.info("feed_removed", extra={"feed_id": feed_id, "guild_id": interaction.guild_id})

    @feed_group.command(name="list", description="List all feeds on this server")
    async def feed_list(self, interaction: discord.Interaction) -> None:
        feeds = await self.db.list_feeds(guild_id=interaction.guild_id)
        if not feeds:
            await interaction.response.send_message(
                "No feeds configured. Use `/feed add` to get started.",
                ephemeral=True,
            )
            return

        lines = []
        for f in feeds:
            channel_mention = f"<#{f['channel_id']}>"
            status = ""
            if f.get("consecutive_errors", 0) > 0:
                status = f" ⚠️ {f['consecutive_errors']} errors"
            interval_mins = f.get("poll_interval", 900) // 60
            lines.append(f"**{f['id']}** · {f['name']} → {channel_mention} · every {interval_mins}m{status}")

        embed = discord.Embed(
            title="RSS Feeds",
            description="\n".join(lines),
            colour=discord.Colour.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @feed_group.command(name="preview", description="Preview the latest item from a feed URL")
    @app_commands.describe(url="The RSS/Atom feed URL to preview")
    async def feed_preview(self, interaction: discord.Interaction, url: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.bot.poller._session.get(
                url,
                headers={"User-Agent": self.config.user_agent, "Accept-Encoding": "gzip"},
                timeout=__import__("aiohttp").ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                raw = await resp.text()

            metadata = extract_feed_metadata(raw)
            items = parse_feed(raw)
            if not items:
                await interaction.followup.send("Feed parsed but contains no items.", ephemeral=True)
                return

            embed = format_item_embed(items[0], feed_name=metadata.title, feed_url=url, feed_id=0)
            embed.set_footer(text="Preview · not subscribed")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"Failed to preview feed: {e}", ephemeral=True)

    @feed_group.command(name="config", description="Show bot status and configuration")
    async def feed_config(self, interaction: discord.Interaction) -> None:
        if not self._check_role(interaction):
            await interaction.response.send_message(
                f"You need the **{self.config.feed_manager_role}** role.",
                ephemeral=True,
            )
            return

        feeds = await self.db.list_feeds(guild_id=interaction.guild_id)
        total = len(feeds)
        errored = sum(1 for f in feeds if f.get("consecutive_errors", 0) > 0)

        embed = discord.Embed(title="CordFeeder Status", colour=discord.Colour.blurple())
        embed.add_field(name="Feeds", value=str(total), inline=True)
        embed.add_field(name="Feeds with errors", value=str(errored), inline=True)
        embed.add_field(name="Default interval", value=f"{self.config.default_poll_interval // 60}m", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class CordFeederBot(commands.Bot):
    def __init__(self, config: Config, db: Database) -> None:
        intents = discord.Intents.default()
        intents.message_content = False
        super().__init__(command_prefix="!", intents=intents)
        self.config = config
        self.db = db
        self.poller = Poller(config=config, db=db, bot=self)

    async def setup_hook(self) -> None:
        await self.add_cog(FeedCog(self))
        await self.tree.sync()
        await self.poller.start()
        logger.info("bot_ready")

    async def close(self) -> None:
        await self.poller.stop()
        await self.db.close()
        await super().close()
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bot.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add cordfeeder/bot.py tests/test_bot.py
git commit -m "Add Discord bot with slash commands for feed management"
```

---

### Task 7: Main Entry Point & Logging

**Files:**
- Create: `cordfeeder/main.py`

**Step 1: Implement main.py**

```python
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter."""

    def __init__(self):
        super().__init__()
        self.hostname = socket.gethostname()

    def format(self, record: logging.LogRecord) -> str:
        log = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "host": self.hostname,
            "app": "cordfeeder",
        }
        if hasattr(record, "extra"):
            log.update(record.extra)
        if record.exc_info and record.exc_info[1]:
            exc = record.exc_info[1]
            log["err.type"] = type(exc).__name__
            log["err.msg"] = str(exc)
            import traceback
            log["err.stack"] = "".join(traceback.format_exception(*record.exc_info)).replace("\n", "\\n")
        return json.dumps(log)


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)
    # Quiet down noisy libraries
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


def main() -> None:
    from cordfeeder.config import Config
    from cordfeeder.database import Database
    from cordfeeder.bot import CordFeederBot

    config = Config.from_env()
    setup_logging(config.log_level)

    logger = logging.getLogger("cordfeeder.main")
    logger.info("starting", extra={"db_path": config.database_path})

    async def run():
        db = Database(config.database_path)
        await db.initialise()
        bot = CordFeederBot(config=config, db=db)
        async with bot:
            await bot.start(config.discord_token)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("shutdown_requested")


if __name__ == "__main__":
    main()
```

**Step 2: Verify the module loads**

Run: `DISCORD_BOT_TOKEN=fake uv run python -c "from cordfeeder.main import main; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add cordfeeder/main.py
git commit -m "Add main entry point with structured JSON logging"
```

---

### Task 8: Integration Test — End to End

**Files:**
- Create: `tests/test_integration.py`

**Step 1: Write integration test**

This tests the full flow from adding a feed to posting items, with mocked HTTP and Discord.

```python
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from cordfeeder.config import Config
from cordfeeder.database import Database
from cordfeeder.poller import Poller
from cordfeeder.parser import parse_feed, extract_feed_metadata
from cordfeeder.formatter import format_item_embed

FIXTURES = Path(__file__).parent / "fixtures"


def make_config():
    return Config(
        discord_token="fake",
        feed_manager_role="Feed Manager",
        default_poll_interval=900,
        database_path=":memory:",
        log_level="DEBUG",
    )


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialise()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_full_flow_add_poll_post(db):
    """Simulate: add feed → poll → post new items → poll again (no duplicates)."""
    config = make_config()
    sample_xml = (FIXTURES / "sample_rss.xml").read_text()

    # Add a feed
    metadata = extract_feed_metadata(sample_xml)
    feed_id = await db.add_feed(
        url="https://example.com/rss",
        name=metadata.title,
        channel_id=12345,
        guild_id=100,
        added_by=1,
    )

    # Record initial items as "already posted" (simulating the add command)
    items = parse_feed(sample_xml)
    for item in items[:3]:
        await db.record_posted_item(feed_id, item.guid, message_id=999)

    # Now simulate a poll — all items already posted, so nothing new
    new_items = []
    for item in items:
        if not await db.is_item_posted(feed_id, item.guid):
            new_items.append(item)
    assert len(new_items) == 0

    # Simulate a new item appearing
    new_guid = "https://example.com/4"
    assert await db.is_item_posted(feed_id, new_guid) is False

    # Post it
    await db.record_posted_item(feed_id, new_guid, message_id=1000)
    assert await db.is_item_posted(feed_id, new_guid) is True

    # Verify feed state
    state = await db.get_feed_state(feed_id)
    assert state["consecutive_errors"] == 0
```

**Step 2: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "Add integration test for add-poll-post flow"
```

---

### Task 9: Discord Bot Setup Guide

**Files:**
- Create: `docs/discord-bot-setup.md`

**Step 1: Write the setup guide**

Document how to create a Discord application, set up a bot user, generate an invite URL with correct permissions, and configure the `FEED_MANAGER_ROLE`.

Required bot permissions:
- Send Messages
- Embed Links
- Use Application Commands

Required OAuth2 scopes:
- `bot`
- `applications.commands`

Include step-by-step instructions with Discord Developer Portal URLs.

**Step 2: Commit**

```bash
git add docs/discord-bot-setup.md
git commit -m "Add Discord bot setup guide"
```

---

### Task 10: Final Polish & First Run

**Step 1: Add database `update_feed_url` method**

The poller references `db.update_feed_url()` for 301 redirects — make sure it exists in `database.py`:

```python
async def update_feed_url(self, feed_id: int, new_url: str) -> None:
    await self._db.execute("UPDATE feeds SET url = ? WHERE id = ?", (new_url, feed_id))
    await self._db.commit()
```

**Step 2: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

**Step 3: Manual smoke test**

1. Create a `.env` file with a real bot token
2. Run: `uv run python -m cordfeeder.main`
3. Verify bot comes online in Discord
4. Test `/feed preview` with a known feed URL
5. Test `/feed add` and verify items appear
6. Test `/feed list` and `/feed remove`

**Step 4: Final commit**

```bash
git add -A
git commit -m "Final polish — ready for first run"
```

Plan complete and saved to `docs/plans/2026-02-26-cordfeeder-implementation.md`. Two execution options:

**1. Subagent-Driven (this session)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** — Open new session in a worktree, batch execution with checkpoints

Which approach?