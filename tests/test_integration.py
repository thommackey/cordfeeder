"""Integration tests: exercises the full add-poll-post flow with real DB and parser."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from cordfeeder.config import Config
from cordfeeder.database import Database
from cordfeeder.parser import parse_feed

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


def _make_config(**overrides) -> Config:
    defaults = dict(
        discord_token="test-token",
        feed_manager_role="Feed Manager",
        default_poll_interval=900,
        database_path=":memory:",
        log_level="DEBUG",
    )
    defaults.update(overrides)
    return Config(**defaults)


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "integration.db"))
    await database.initialise()
    yield database
    await database.close()


FEED_URL = "https://example.com/feed.xml"
GUILD_ID = 1
CHANNEL_ID = 100
ADDED_BY = 42


# ------------------------------------------------------------------
# test_full_flow_add_poll_post
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_flow_add_poll_post(db: Database):
    """End-to-end: add feed, mark initial items, poll finds nothing new,
    simulate new item, detect it, record it, verify state."""

    # 1. Parse the sample RSS feed
    raw_xml = _read("sample_rss.xml")
    items = parse_feed(raw_xml)
    assert len(items) == 3

    # 2. Add a feed to the database
    feed_id = await db.add_feed(
        url=FEED_URL,
        name="Test Feed",
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        added_by=ADDED_BY,
    )

    # 3. Record initial items as posted (simulating what /feed add does)
    for item in items:
        await db.record_posted_item(feed_id, item.guid)

    # 4. Verify polling finds no new items (all already posted)
    unposted = [
        item for item in items if not await db.is_item_posted(feed_id, item.guid)
    ]
    assert unposted == [], "All initial items should already be marked as posted"

    # 5. Simulate a new item appearing
    new_guid = "https://example.com/4"
    assert not await db.is_item_posted(feed_id, new_guid)

    # 6. Record the new item as posted
    await db.record_posted_item(feed_id, new_guid, message_id=12345)
    assert await db.is_item_posted(feed_id, new_guid)

    # 7. Verify feed state has no errors
    state = await db.get_feed_state(feed_id)
    assert state is not None
    assert state["consecutive_errors"] == 0
    assert state["last_error"] is None


# ------------------------------------------------------------------
# test_adaptive_interval_updates_on_poll
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adaptive_interval_updates_on_poll(db: Database):
    """After a successful poll cycle, the feed state should reflect an
    updated poll_interval based on adaptive scheduling."""

    feed_id = await db.add_feed(
        url=FEED_URL,
        name="Adaptive Feed",
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        added_by=ADDED_BY,
    )

    # Verify default interval
    state = await db.get_feed_state(feed_id)
    assert state["poll_interval"] == 900

    # Simulate the poller updating state after a fetch with a computed interval
    new_interval = 3600
    await db.update_feed_state(
        feed_id,
        poll_interval=new_interval,
        consecutive_errors=0,
        last_poll_at="2026-02-27T10:00:00+00:00",
        next_poll_at="2026-02-27T11:00:00+00:00",
    )

    state = await db.get_feed_state(feed_id)
    assert state["poll_interval"] == new_interval
    assert state["last_poll_at"] == "2026-02-27T10:00:00+00:00"
    assert state["next_poll_at"] == "2026-02-27T11:00:00+00:00"
    assert state["consecutive_errors"] == 0


# ------------------------------------------------------------------
# test_error_backoff_increases
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_backoff_increases(db: Database):
    """Simulate consecutive errors and verify the counter increments
    and last_error is recorded, mimicking the poller's error path."""

    feed_id = await db.add_feed(
        url=FEED_URL,
        name="Flaky Feed",
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        added_by=ADDED_BY,
    )

    # Simulate three consecutive errors (as the poller would)
    for i in range(1, 4):
        await db.update_feed_state(
            feed_id,
            consecutive_errors=i,
            last_error=f"Connection timeout (attempt {i})",
        )
        state = await db.get_feed_state(feed_id)
        assert state["consecutive_errors"] == i
        assert f"attempt {i}" in state["last_error"]

    # Simulate recovery: errors reset to 0
    await db.update_feed_state(
        feed_id,
        consecutive_errors=0,
        last_error=None,
    )
    state = await db.get_feed_state(feed_id)
    assert state["consecutive_errors"] == 0
    assert state["last_error"] is None


# ------------------------------------------------------------------
# test_due_feeds_respects_next_poll_at
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_feeds_respects_next_poll_at(db: Database):
    """A feed with next_poll_at in the future should not appear in due feeds,
    while a feed with next_poll_at=NULL or in the past should."""

    feed_id = await db.add_feed(
        url=FEED_URL,
        name="Scheduled Feed",
        channel_id=CHANNEL_ID,
        guild_id=GUILD_ID,
        added_by=ADDED_BY,
    )

    # Newly added: next_poll_at is NULL, so it should be due
    due = await db.get_due_feeds()
    assert any(f["id"] == feed_id for f in due)

    # Schedule far in the future
    await db.update_feed_state(
        feed_id,
        next_poll_at="2099-12-31T23:59:59+00:00",
    )
    due = await db.get_due_feeds()
    assert not any(f["id"] == feed_id for f in due)

    # Schedule in the past
    await db.update_feed_state(
        feed_id,
        next_poll_at="2020-01-01T00:00:00+00:00",
    )
    due = await db.get_due_feeds()
    assert any(f["id"] == feed_id for f in due)
