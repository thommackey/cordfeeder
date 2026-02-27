"""Tests for the feed poller."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cordfeeder.config import Config
from cordfeeder.poller import (
    FeedGoneError,
    FeedRateLimitError,
    FeedServerError,
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
            datetime(2026, 2, 26, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 2, 26, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 2, 26, 8, 0, tzinfo=timezone.utc),
            datetime(2026, 2, 26, 6, 0, tzinfo=timezone.utc),
        ]
        interval = calculate_adaptive_interval(
            timestamps, min_interval=300, max_interval=43200
        )
        assert 3000 <= interval <= 4200  # roughly 1 hour

    def test_daily_posts(self):
        timestamps = [
            datetime(2026, 2, 26, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 2, 24, 12, 0, tzinfo=timezone.utc),
        ]
        interval = calculate_adaptive_interval(
            timestamps, min_interval=300, max_interval=43200
        )
        assert interval == 43200  # clamped to max

    def test_clamped_to_min(self):
        timestamps = [
            datetime(2026, 2, 26, 12, 4, tzinfo=timezone.utc),
            datetime(2026, 2, 26, 12, 2, tzinfo=timezone.utc),
            datetime(2026, 2, 26, 12, 0, tzinfo=timezone.utc),
        ]
        interval = calculate_adaptive_interval(
            timestamps, min_interval=300, max_interval=43200
        )
        assert interval == 300

    def test_single_item(self):
        timestamps = [datetime(2026, 2, 26, 12, 0, tzinfo=timezone.utc)]
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

    def test_feed_server_error(self):
        err = FeedServerError(
            feed_id=1, url="https://example.com/feed", status=503
        )
        assert err.status == 503


# ---------------------------------------------------------------
# Poller.fetch_feed
# ---------------------------------------------------------------


class TestFetchFeed:
    @pytest.fixture
    def poller(self):
        config = _make_config()
        db = AsyncMock()
        db.get_feed_state = AsyncMock(return_value={"etag": None, "last_modified": None})
        db.update_feed_state = AsyncMock()
        bot = MagicMock()
        p = Poller(config=config, db=db, bot=bot)
        return p

    def _mock_response(self, status, text_body="", headers=None):
        mock_response = AsyncMock()
        mock_response.status = status
        mock_response.headers = headers or {}
        mock_response.text = AsyncMock(return_value=text_body)
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
        poller._session = mock_session

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
        poller._session = mock_session

        result = await poller.fetch_feed(feed_id=1, url="https://example.com/feed.xml")
        assert result is None

    @pytest.mark.asyncio
    async def test_raises_gone_on_410(self, poller):
        mock_response = self._mock_response(410)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        poller._session = mock_session

        with pytest.raises(FeedGoneError):
            await poller.fetch_feed(feed_id=1, url="https://example.com/feed.xml")

    @pytest.mark.asyncio
    async def test_raises_rate_limit_on_429(self, poller):
        mock_response = self._mock_response(429, headers={"Retry-After": "120"})

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        poller._session = mock_session

        with pytest.raises(FeedRateLimitError) as exc_info:
            await poller.fetch_feed(feed_id=1, url="https://example.com/feed.xml")
        assert exc_info.value.retry_after == 120

    @pytest.mark.asyncio
    async def test_raises_server_error_on_5xx(self, poller):
        mock_response = self._mock_response(503)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        poller._session = mock_session

        with pytest.raises(FeedServerError) as exc_info:
            await poller.fetch_feed(feed_id=1, url="https://example.com/feed.xml")
        assert exc_info.value.status == 503
