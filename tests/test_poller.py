"""Tests for the feed poller."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cordfeeder.config import Config
from cordfeeder.poller import (
    FeedGoneError,
    FeedHTTPError,
    FeedRateLimitError,
    Poller,
    calculate_adaptive_interval,
)

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


# ---------------------------------------------------------------
# calculate_adaptive_interval
# ---------------------------------------------------------------


class TestAdaptiveInterval:
    def test_frequent_posts(self):
        """Posts every 2 hours -- should poll roughly every hour."""
        timestamps = [
            datetime(2026, 2, 26, 12, 0, tzinfo=UTC),
            datetime(2026, 2, 26, 10, 0, tzinfo=UTC),
            datetime(2026, 2, 26, 8, 0, tzinfo=UTC),
            datetime(2026, 2, 26, 6, 0, tzinfo=UTC),
        ]
        interval = calculate_adaptive_interval(
            timestamps, min_interval=300, max_interval=43200
        )
        assert 3000 <= interval <= 4200  # roughly 1 hour

    def test_daily_posts(self):
        timestamps = [
            datetime(2026, 2, 26, 12, 0, tzinfo=UTC),
            datetime(2026, 2, 25, 12, 0, tzinfo=UTC),
            datetime(2026, 2, 24, 12, 0, tzinfo=UTC),
        ]
        interval = calculate_adaptive_interval(
            timestamps, min_interval=300, max_interval=43200
        )
        assert interval == 43200  # clamped to max

    def test_clamped_to_min(self):
        timestamps = [
            datetime(2026, 2, 26, 12, 4, tzinfo=UTC),
            datetime(2026, 2, 26, 12, 2, tzinfo=UTC),
            datetime(2026, 2, 26, 12, 0, tzinfo=UTC),
        ]
        interval = calculate_adaptive_interval(
            timestamps, min_interval=300, max_interval=43200
        )
        assert interval == 300

    def test_single_item(self):
        timestamps = [datetime(2026, 2, 26, 12, 0, tzinfo=UTC)]
        interval = calculate_adaptive_interval(
            timestamps, min_interval=300, max_interval=43200
        )
        assert interval is None


# ---------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------


class TestExceptions:
    def test_feed_gone_error(self):
        err = FeedGoneError(feed_id=1, url="https://example.com/feed")
        assert err.feed_id == 1
        assert err.url == "https://example.com/feed"

    def test_feed_rate_limit_error(self):
        err = FeedRateLimitError(
            feed_id=1, url="https://example.com/feed", retry_after=600
        )
        assert err.retry_after == 600

    def test_feed_http_error(self):
        err = FeedHTTPError(feed_id=1, url="https://example.com/feed", status=404)
        assert err.status == 404


# ---------------------------------------------------------------
# Poller.fetch_feed
# ---------------------------------------------------------------


class TestResponseSizeLimit:
    """Oversized HTTP responses must not be loaded into memory."""

    @pytest.fixture
    def poller(self):
        config = _make_config()
        db = AsyncMock()
        db.get_feed_state = AsyncMock(
            return_value={"etag": None, "last_modified": None}
        )
        db.update_feed_state = AsyncMock()
        bot = MagicMock()
        return Poller(config=config, db=db, bot=bot)

    @pytest.mark.asyncio
    async def test_rejects_oversized_response(self, poller):
        """A response larger than MAX_FEED_BYTES must raise, not OOM."""
        from cordfeeder.poller import MAX_FEED_BYTES

        # Mock a response whose content.read returns more bytes than the limit
        mock_content = AsyncMock()
        mock_content.read = AsyncMock(return_value=b"x" * (MAX_FEED_BYTES + 1))

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.content = mock_content
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        poller.session = mock_session

        with pytest.raises(ValueError, match="too large"):
            await poller.fetch_feed(feed_id=1, url="https://example.com/feed.xml")


class TestFetchFeed:
    @pytest.fixture
    def poller(self):
        config = _make_config()
        db = AsyncMock()
        db.get_feed_state = AsyncMock(
            return_value={"etag": None, "last_modified": None}
        )
        db.update_feed_state = AsyncMock()
        bot = MagicMock()
        p = Poller(config=config, db=db, bot=bot)
        return p

    def _mock_response(self, status, text_body="", headers=None):
        mock_response = AsyncMock()
        mock_response.status = status
        mock_response.headers = headers or {}
        # Mock both resp.text() and resp.content.read() for size-limited reading
        body_bytes = (
            text_body.encode("utf-8") if isinstance(text_body, str) else text_body
        )
        mock_response.text = AsyncMock(return_value=text_body)
        mock_content = AsyncMock()
        mock_content.read = AsyncMock(return_value=body_bytes)
        mock_response.content = mock_content
        mock_response.get_encoding = MagicMock(return_value="utf-8")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        return mock_response

    @pytest.mark.asyncio
    async def test_fetches_due_feeds(self, poller):
        """Mock 200 response with sample_rss.xml, verify items returned."""
        sample_xml = _read("sample_rss.xml")
        mock_response = self._mock_response(200, sample_xml, {"ETag": '"abc123"'})

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        poller.session = mock_session

        items = await poller.fetch_feed(feed_id=1, url="https://example.com/feed.xml")

        assert items is not None
        assert len(items) == 3
        assert items[0].title == "Third Post"
        mock_session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_304(self, poller):
        """Mock 304 response, verify None returned."""
        mock_response = self._mock_response(304)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        poller.session = mock_session

        result = await poller.fetch_feed(feed_id=1, url="https://example.com/feed.xml")
        assert result is None

    @pytest.mark.asyncio
    async def test_raises_gone_on_410(self, poller):
        mock_response = self._mock_response(410)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        poller.session = mock_session

        with pytest.raises(FeedGoneError):
            await poller.fetch_feed(feed_id=1, url="https://example.com/feed.xml")

    @pytest.mark.asyncio
    async def test_raises_rate_limit_on_429(self, poller):
        mock_response = self._mock_response(429, headers={"Retry-After": "120"})

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        poller.session = mock_session

        with pytest.raises(FeedRateLimitError) as exc_info:
            await poller.fetch_feed(feed_id=1, url="https://example.com/feed.xml")
        assert exc_info.value.retry_after == 120

    @pytest.mark.asyncio
    async def test_raises_rate_limit_with_date_retry_after(self, poller):
        """Retry-After can be an HTTP date string per RFC 7231 — must not crash."""
        mock_response = self._mock_response(
            429, headers={"Retry-After": "Thu, 27 Feb 2026 13:00:00 GMT"}
        )

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        poller.session = mock_session

        with pytest.raises(FeedRateLimitError) as exc_info:
            await poller.fetch_feed(feed_id=1, url="https://example.com/feed.xml")
        # Should not crash; retry_after should be None when non-numeric
        assert exc_info.value.retry_after is None

    @pytest.mark.asyncio
    async def test_raises_http_error_on_5xx(self, poller):
        mock_response = self._mock_response(503)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        poller.session = mock_session

        with pytest.raises(FeedHTTPError) as exc_info:
            await poller.fetch_feed(feed_id=1, url="https://example.com/feed.xml")
        assert exc_info.value.status == 503

    @pytest.mark.asyncio
    async def test_raises_http_error_on_404(self, poller):
        """A 404 should raise FeedHTTPError, not try to parse HTML error page."""
        mock_response = self._mock_response(404, text_body="<html>Not Found</html>")

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        poller.session = mock_session

        with pytest.raises(FeedHTTPError) as exc_info:
            await poller.fetch_feed(feed_id=1, url="https://example.com/feed.xml")
        assert exc_info.value.status == 404


# ---------------------------------------------------------------
# Warmup polling
# ---------------------------------------------------------------


class TestWarmupPolling:
    """Feed warmup period: new feeds use default interval before adaptive kicks in."""

    @pytest.fixture
    def poller(self):
        config = _make_config()
        db = AsyncMock()
        db.get_feed_state = AsyncMock(
            return_value={"etag": None, "last_modified": None}
        )
        db.update_feed_state = AsyncMock()
        db.is_item_posted = AsyncMock(return_value=True)  # no new items to post
        db.record_posted_item = AsyncMock()
        bot = MagicMock()
        p = Poller(config=config, db=db, bot=bot)
        return p

    def _mock_response(self, text_body):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {}
        body_bytes = text_body.encode("utf-8")
        mock_content = AsyncMock()
        mock_content.read = AsyncMock(return_value=body_bytes)
        mock_response.content = mock_content
        mock_response.get_encoding = MagicMock(return_value="utf-8")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        return mock_response

    def _feed_info(self, created_at: datetime) -> dict:
        return {
            "id": 1,
            "url": "https://example.com/feed.xml",
            "name": "Test Feed",
            "channel_id": 123,
            "poll_interval": 900,
            "consecutive_errors": 0,
            "created_at": created_at.isoformat(),
        }

    @pytest.mark.asyncio
    async def test_new_feed_uses_default_interval(self, poller):
        """A feed created recently should use the default poll interval."""
        now = datetime.now(UTC)
        created_at = now - timedelta(minutes=5)  # 5 min old, well within warmup
        feed_info = self._feed_info(created_at)

        sample_xml = _read("sample_rss.xml")
        mock_response = self._mock_response(sample_xml)
        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        poller.session = mock_session

        await poller._poll_feed(feed_info)

        # Check that update_feed_state was called with the default interval (900)
        schedule_calls = [
            c
            for c in poller.db.update_feed_state.call_args_list
            if "poll_interval" in (c.kwargs or {})
        ]
        assert len(schedule_calls) >= 1
        assert schedule_calls[-1].kwargs["poll_interval"] == 900

    @pytest.mark.asyncio
    async def test_old_feed_uses_adaptive_interval(self, poller):
        """A feed created long ago should use the adaptive interval."""
        created_at = datetime(2025, 1, 1, tzinfo=UTC)  # over a year old
        feed_info = self._feed_info(created_at)

        sample_xml = _read("sample_rss.xml")
        mock_response = self._mock_response(sample_xml)
        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        poller.session = mock_session

        await poller._poll_feed(feed_info)

        # Check that the interval is NOT the default — it should be adaptive
        schedule_calls = [
            c
            for c in poller.db.update_feed_state.call_args_list
            if "poll_interval" in (c.kwargs or {})
        ]
        assert len(schedule_calls) >= 1
        used_interval = schedule_calls[-1].kwargs["poll_interval"]
        assert used_interval != poller.config.default_poll_interval
