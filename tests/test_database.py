from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
    with pytest.raises(Exception):
        await db.add_feed("https://a.com/feed", "A2", 101, guild_id=1, added_by=43)


@pytest.mark.asyncio
async def test_feed_state_created_on_add(db: Database):
    feed_id = await db.add_feed("https://a.com/feed", "A", 100, 1, 42)
    state = await db.get_feed_state(feed_id)
    assert state is not None
    assert state["feed_id"] == feed_id
    assert state["poll_interval"] == 900
    assert state["consecutive_errors"] == 0


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

    # Verify state and items exist
    assert await db.get_feed_state(feed_id) is not None
    assert await db.is_item_posted(feed_id, "guid-1") is True

    await db.remove_feed(feed_id)

    assert await db.get_feed_state(feed_id) is None
    assert await db.is_item_posted(feed_id, "guid-1") is False


@pytest.mark.asyncio
async def test_update_feed_state_rejects_bad_column(db: Database):
    feed_id = await db.add_feed("https://a.com/feed", "A", 100, 1, 42)
    with pytest.raises(ValueError, match="unknown feed_state columns"):
        await db.update_feed_state(feed_id, bogus_column="oops")


@pytest.mark.asyncio
async def test_prune_old_items(db: Database):
    feed_id = await db.add_feed("https://a.com/feed", "A", 100, 1, 42)

    old_ts = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    recent_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()

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
