from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from cordfeeder.database import Database

_OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL,
    name        TEXT NOT NULL,
    channel_id  INTEGER NOT NULL,
    guild_id    INTEGER NOT NULL,
    added_by    INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE(url, guild_id)
);

CREATE TABLE IF NOT EXISTS feed_state (
    feed_id             INTEGER PRIMARY KEY REFERENCES feeds(id) ON DELETE CASCADE,
    etag                TEXT,
    last_modified       TEXT,
    last_poll_at        TEXT,
    next_poll_at        TEXT,
    poll_interval       INTEGER NOT NULL DEFAULT 900,
    consecutive_errors  INTEGER NOT NULL DEFAULT 0,
    last_error          TEXT
);

CREATE TABLE IF NOT EXISTS posted_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id     INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    item_guid   TEXT NOT NULL,
    posted_at   TEXT NOT NULL,
    message_id  INTEGER,
    UNIQUE(feed_id, item_guid)
);
"""


@pytest.mark.asyncio
async def test_add_feed(db: Database):
    feed_id = await db.add_feed(
        url="https://example.com/feed.xml",
        name="Example Feed",
        channel_id=100,
        guild_id=1,
        added_by=42,
    )
    assert isinstance(feed_id, int)

    feed = await db.get_feed(feed_id)
    assert feed is not None
    assert feed["url"] == "https://example.com/feed.xml"
    assert feed["name"] == "Example Feed"
    assert feed["channel_id"] == 100
    assert feed["guild_id"] == 1
    assert feed["added_by"] == 42
    assert feed["created_at"] is not None


@pytest.mark.asyncio
async def test_remove_feed(db: Database):
    feed_id = await db.add_feed(
        url="https://example.com/feed.xml",
        name="Example Feed",
        channel_id=100,
        guild_id=1,
        added_by=42,
    )
    await db.remove_feed(feed_id)
    assert await db.get_feed(feed_id) is None


@pytest.mark.asyncio
async def test_list_feeds_by_guild(db: Database):
    await db.add_feed("https://a.com/feed", "A", 100, guild_id=1, added_by=42)
    await db.add_feed("https://b.com/feed", "B", 101, guild_id=1, added_by=42)
    await db.add_feed("https://c.com/feed", "C", 200, guild_id=2, added_by=42)

    feeds = await db.list_feeds(guild_id=1)
    assert len(feeds) == 2
    urls = {f["url"] for f in feeds}
    assert urls == {"https://a.com/feed", "https://b.com/feed"}


@pytest.mark.asyncio
async def test_duplicate_feed_url_same_guild(db: Database):
    await db.add_feed("https://a.com/feed", "A", 100, guild_id=1, added_by=42)
    with pytest.raises(aiosqlite.IntegrityError):
        await db.add_feed("https://a.com/feed", "A2", 101, guild_id=1, added_by=43)


@pytest.mark.asyncio
async def test_feed_defaults_on_add(db: Database):
    feed_id = await db.add_feed("https://a.com/feed", "A", 100, 1, 42)
    state = await db.get_feed_state(feed_id)
    assert state is not None
    assert state["poll_interval"] == 900
    assert state["consecutive_errors"] == 0
    assert state["etag"] is None
    assert state["last_poll_at"] is None


@pytest.mark.asyncio
async def test_update_feed_state(db: Database):
    feed_id = await db.add_feed("https://a.com/feed", "A", 100, 1, 42)
    await db.update_feed_state(
        feed_id,
        etag='"abc123"',
        last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
    )
    state = await db.get_feed_state(feed_id)
    assert state["etag"] == '"abc123"'
    assert state["last_modified"] == "Wed, 01 Jan 2025 00:00:00 GMT"


@pytest.mark.asyncio
async def test_record_posted_item(db: Database):
    feed_id = await db.add_feed("https://a.com/feed", "A", 100, 1, 42)
    await db.record_posted_item(feed_id, "guid-1", message_id=9999)
    assert await db.is_item_posted(feed_id, "guid-1") is True
    assert await db.is_item_posted(feed_id, "guid-2") is False


@pytest.mark.asyncio
async def test_duplicate_posted_item_ignored(db: Database):
    feed_id = await db.add_feed("https://a.com/feed", "A", 100, 1, 42)
    await db.record_posted_item(feed_id, "guid-1", message_id=9999)
    # Should not raise — INSERT OR IGNORE
    await db.record_posted_item(feed_id, "guid-1", message_id=1111)
    assert await db.is_item_posted(feed_id, "guid-1") is True


@pytest.mark.asyncio
async def test_get_due_feeds(db: Database):
    """A newly added feed has next_poll_at=NULL so it should be immediately due."""
    feed_id = await db.add_feed("https://a.com/feed", "A", 100, 1, 42)
    due = await db.get_due_feeds()
    assert len(due) >= 1
    assert any(f["id"] == feed_id for f in due)


@pytest.mark.asyncio
async def test_remove_feed_cascades(db: Database):
    feed_id = await db.add_feed("https://a.com/feed", "A", 100, 1, 42)
    await db.record_posted_item(feed_id, "guid-1")

    assert await db.is_item_posted(feed_id, "guid-1") is True

    await db.remove_feed(feed_id)

    assert await db.get_feed(feed_id) is None
    assert await db.is_item_posted(feed_id, "guid-1") is False


@pytest.mark.asyncio
async def test_update_feed_state_rejects_bad_column(db: Database):
    feed_id = await db.add_feed("https://a.com/feed", "A", 100, 1, 42)
    with pytest.raises(ValueError, match="unknown feed_state columns"):
        await db.update_feed_state(feed_id, bogus_column="oops")


@pytest.mark.asyncio
async def test_prune_old_items(db: Database):
    feed_id = await db.add_feed("https://a.com/feed", "A", 100, 1, 42)

    old_ts = (datetime.now(UTC) - timedelta(days=120)).isoformat()
    recent_ts = (datetime.now(UTC) - timedelta(days=10)).isoformat()

    await db._db.execute(
        "INSERT INTO posted_items (feed_id, item_guid, posted_at) VALUES (?, ?, ?)",
        (feed_id, "old-item", old_ts),
    )
    await db._db.execute(
        "INSERT INTO posted_items (feed_id, item_guid, posted_at) VALUES (?, ?, ?)",
        (feed_id, "recent-item", recent_ts),
    )
    await db._db.commit()

    deleted = await db.prune_old_items(days=90)
    assert deleted == 1

    assert await db.is_item_posted(feed_id, "old-item") is False
    assert await db.is_item_posted(feed_id, "recent-item") is True


@pytest.mark.asyncio
async def test_update_feed_url(db: Database):
    feed_id = await db.add_feed("https://old.com/feed", "A", 100, 1, 42)
    await db.update_feed_url(feed_id, "https://new.com/feed")
    feed = await db.get_feed(feed_id)
    assert feed["url"] == "https://new.com/feed"


@pytest.mark.asyncio
async def test_migrate_v1_adds_columns_and_copies_data(tmp_path: Path):
    """initialise() migrates an old-schema database with separate feed_state."""
    db_path = str(tmp_path / "legacy.db")

    # Create old-schema database with data
    async with aiosqlite.connect(db_path) as raw:
        await raw.executescript(_OLD_SCHEMA)
        await raw.execute(
            """INSERT INTO feeds (url, name, channel_id, guild_id, added_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("https://example.com/feed", "Example", 100, 1, 42, "2026-01-01T00:00:00Z"),
        )
        await raw.execute(
            """INSERT INTO feed_state (feed_id, etag, poll_interval, consecutive_errors)
               VALUES (1, '"abc"', 1800, 2)""",
        )
        await raw.commit()

    # Run initialise(), which should trigger migration
    database = Database(db_path)
    await database.initialise()

    # State columns now exist on feeds and contain migrated data
    state = await database.get_feed_state(1)
    assert state is not None
    assert state["etag"] == '"abc"'
    assert state["poll_interval"] == 1800
    assert state["consecutive_errors"] == 2

    # get_due_feeds should work (relies on next_poll_at column)
    due = await database.get_due_feeds()
    assert len(due) == 1
    assert due[0]["url"] == "https://example.com/feed"

    # feed_state table should be gone
    async with database._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='feed_state'"
    ) as cur:
        assert await cur.fetchone() is None

    await database.close()


@pytest.mark.asyncio
async def test_migrate_v1_idempotent(db: Database):
    """Running migration on an already-migrated database is a no-op."""
    feed_id = await db.add_feed("https://a.com/feed", "A", 100, 1, 42)
    await db.update_feed_state(feed_id, etag='"xyz"')

    # Run migration again
    await db._migrate_v1()

    state = await db.get_feed_state(feed_id)
    assert state["etag"] == '"xyz"'
