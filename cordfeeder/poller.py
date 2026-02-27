"""Feed poller: fetches feeds on adaptive intervals, posts new items to Discord."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import aiohttp
from dateutil import parser as dateutil_parser

from cordfeeder.config import Config
from cordfeeder.database import Database
from cordfeeder.formatter import format_item_message
from cordfeeder.parser import FeedItem, parse_feed

logger = logging.getLogger(__name__)


def calculate_adaptive_interval(
    timestamps: list[datetime],
    min_interval: int = 300,
    max_interval: int = 43200,
) -> int | None:
    """Calculate a poll interval based on observed publish frequency.

    Returns half the average gap between consecutive timestamps, clamped
    between min_interval and max_interval. Returns None if fewer than 2
    timestamps are provided.
    """
    if len(timestamps) < 2:
        return None

    sorted_ts = sorted(timestamps, reverse=True)
    gaps = [
        (sorted_ts[i] - sorted_ts[i + 1]).total_seconds()
        for i in range(len(sorted_ts) - 1)
    ]
    avg_gap = sum(gaps) / len(gaps)
    interval = int(avg_gap / 2)
    return max(min_interval, min(interval, max_interval))


# ------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------


class FeedGoneError(Exception):
    """Raised when a feed returns HTTP 410 Gone."""

    def __init__(self, feed_id: int, url: str) -> None:
        self.feed_id = feed_id
        self.url = url
        super().__init__(f"Feed {feed_id} ({url}) is gone (410)")


class FeedRateLimitError(Exception):
    """Raised when a feed returns HTTP 429 or 403."""

    def __init__(self, feed_id: int, url: str, retry_after: int | None) -> None:
        self.feed_id = feed_id
        self.url = url
        self.retry_after = int(retry_after) if retry_after is not None else None
        super().__init__(f"Feed {feed_id} ({url}) rate limited")


class FeedServerError(Exception):
    """Raised when a feed returns a 5xx status."""

    def __init__(self, feed_id: int, url: str, status: int) -> None:
        self.feed_id = feed_id
        self.url = url
        self.status = int(status)
        super().__init__(f"Feed {feed_id} ({url}) server error {status}")


class FeedHTTPError(Exception):
    """Raised when a feed returns an unexpected non-2xx status."""

    def __init__(self, feed_id: int, url: str, status: int) -> None:
        self.feed_id = feed_id
        self.url = url
        self.status = int(status)
        super().__init__(f"Feed {feed_id} ({url}) HTTP {status}")


# ------------------------------------------------------------------
# Poller
# ------------------------------------------------------------------


class Poller:
    """Background feed poller that fetches RSS/Atom feeds and posts new items."""

    def __init__(self, config: Config, db: Database, bot) -> None:
        self.config = config
        self.db = db
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._host_semaphores: dict[str, asyncio.Semaphore] = {}
        self._running = False
        self._poll_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Create the HTTP session and start the background poll loop."""
        self._session = aiohttp.ClientSession(
            headers={"User-Agent": self.config.user_agent},
        )
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("poller started")

    async def stop(self) -> None:
        """Stop the poll loop and close the HTTP session."""
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("poller stopped")

    async def _poll_loop(self) -> None:
        """Main loop: poll due feeds, then sleep.  Prunes old posted_items daily."""
        last_prune = datetime.min.replace(tzinfo=timezone.utc)
        while self._running:
            try:
                due_feeds = await self.db.get_due_feeds()
                if due_feeds:
                    logger.info("polling due feeds", extra={"count": len(due_feeds)})
                    tasks = [self._poll_feed(f) for f in due_feeds]
                    await asyncio.gather(*tasks, return_exceptions=True)

                # Prune stale posted_items once per day
                now = datetime.now(timezone.utc)
                if (now - last_prune).total_seconds() >= 86400:
                    await self.db.prune_old_items()
                    last_prune = now
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("unexpected error in poll loop")
            await asyncio.sleep(30)

    def _get_host_semaphore(self, url: str) -> asyncio.Semaphore:
        """Return a per-host semaphore (max 2 concurrent requests per host)."""
        host = urlparse(url).hostname or url
        if host not in self._host_semaphores:
            self._host_semaphores[host] = asyncio.Semaphore(2)
        return self._host_semaphores[host]

    async def fetch_feed(self, feed_id: int, url: str) -> list[FeedItem] | None:
        """Fetch a single feed URL. Returns parsed items, or None for 304."""
        state = await self.db.get_feed_state(feed_id)

        headers: dict[str, str] = {"Accept-Encoding": "gzip"}
        if state:
            if state.get("etag"):
                headers["If-None-Match"] = state["etag"]
            if state.get("last_modified"):
                headers["If-Modified-Since"] = state["last_modified"]

        sem = self._get_host_semaphore(url)
        timeout = aiohttp.ClientTimeout(total=30)

        async with sem:
            async with self._session.get(url, headers=headers, timeout=timeout) as resp:
                status = resp.status

                if status == 304:
                    logger.debug("feed not modified", extra={"feed_id": feed_id})
                    return None

                if status == 410:
                    raise FeedGoneError(feed_id=feed_id, url=url)

                if status in (429, 403):
                    retry_raw = resp.headers.get("Retry-After")
                    try:
                        retry_after = int(retry_raw) if retry_raw else None
                    except (ValueError, TypeError):
                        retry_after = None
                    raise FeedRateLimitError(
                        feed_id=feed_id, url=url, retry_after=retry_after
                    )

                if 500 <= status < 600:
                    raise FeedServerError(feed_id=feed_id, url=url, status=status)

                if status != 200:
                    raise FeedHTTPError(feed_id=feed_id, url=url, status=status)

                body = await resp.text()
                items = parse_feed(body)

                # Update conditional GET state
                new_etag = resp.headers.get("ETag")
                new_last_modified = resp.headers.get("Last-Modified")
                await self.db.update_feed_state(
                    feed_id,
                    etag=new_etag,
                    last_modified=new_last_modified,
                )

                logger.info(
                    "feed fetched",
                    extra={"feed_id": feed_id, "items": len(items), "status": status},
                )
                return items

    async def _poll_feed(self, feed_info: dict) -> None:
        """Full poll cycle for a single feed."""
        feed_id = feed_info["id"]
        feed_url = feed_info["url"]
        feed_name = feed_info["name"]
        channel_id = feed_info["channel_id"]

        try:
            items = await self.fetch_feed(feed_id, feed_url)

            if items is None:
                # Not modified — schedule next poll with current interval
                interval = feed_info.get("poll_interval", self.config.default_poll_interval)
                await self._schedule_next_poll(feed_id, interval)
                await self.db.update_feed_state(feed_id, consecutive_errors=0)
                return

            # Filter to unposted items
            new_items = []
            for item in items:
                if not await self.db.is_item_posted(feed_id, item.guid):
                    new_items.append(item)

            # Cap at max_items_per_poll (take last N for most recent, then reverse)
            if len(new_items) > self.config.max_items_per_poll:
                new_items = new_items[-self.config.max_items_per_poll :]
            new_items = list(reversed(new_items))

            # Post items oldest-first
            for item in new_items:
                await self._post_item(feed_id, feed_name, feed_url, channel_id, item)

            # Adaptive interval
            timestamps = self._extract_timestamps(items)
            adaptive = calculate_adaptive_interval(
                timestamps,
                min_interval=self.config.min_poll_interval,
                max_interval=self.config.max_poll_interval,
            )
            interval = adaptive or feed_info.get(
                "poll_interval", self.config.default_poll_interval
            )

            await self._schedule_next_poll(feed_id, interval)
            await self.db.update_feed_state(feed_id, consecutive_errors=0)

            logger.info(
                "poll complete",
                extra={
                    "feed_id": feed_id,
                    "new_items": len(new_items),
                    "next_interval": interval,
                },
            )

        except FeedGoneError:
            logger.warning("feed gone", extra={"feed_id": feed_id, "url": feed_url})
            try:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    await channel.send(
                        f"Feed **{feed_name}** (`{feed_url}`) returned HTTP 410 Gone. "
                        f"Removing it automatically."
                    )
            except Exception:
                logger.exception("failed to notify channel about gone feed")
            await self.db.remove_feed(feed_id)

        except FeedRateLimitError as exc:
            retry_after = exc.retry_after or 0
            backoff = max(retry_after, 14400)
            logger.warning(
                "feed rate limited",
                extra={"feed_id": feed_id, "backoff": backoff},
            )
            await self._schedule_next_poll(feed_id, backoff)

        except Exception as exc:
            errors = feed_info.get("consecutive_errors", 0) + 1
            base_interval = feed_info.get(
                "poll_interval", self.config.default_poll_interval
            )
            backoff = min(base_interval * (2 ** errors), 86400)
            jitter = random.uniform(0, backoff * 0.1)
            backoff = int(backoff + jitter)

            logger.exception(
                "feed poll error",
                extra={"feed_id": feed_id, "consecutive_errors": errors},
            )
            await self.db.update_feed_state(
                feed_id, consecutive_errors=errors, last_error=str(exc)
            )
            await self._schedule_next_poll(feed_id, backoff)

    async def _post_item(
        self,
        feed_id: int,
        feed_name: str,
        feed_url: str,
        channel_id: int,
        item: FeedItem,
    ) -> None:
        """Send a feed item to the appropriate Discord channel.

        Always records the item as posted — even if sending fails — to prevent
        infinite retry loops when the channel is inaccessible.
        """
        channel = self.bot.get_channel(channel_id)
        if not channel:
            logger.warning(
                "channel not found",
                extra={"feed_id": feed_id, "channel_id": channel_id},
            )
            await self.db.record_posted_item(feed_id, item.guid)
            return

        content = format_item_message(
            item=item,
            feed_name=feed_name,
            feed_id=feed_id,
        )
        message_id = None
        try:
            msg = await channel.send(content)
            message_id = msg.id if hasattr(msg, "id") else None
        except Exception:
            logger.warning(
                "failed to send item to channel",
                extra={"feed_id": feed_id, "guid": item.guid, "channel_id": channel_id},
            )

        await self.db.record_posted_item(feed_id, item.guid, message_id)
        logger.debug(
            "item posted",
            extra={"feed_id": feed_id, "guid": item.guid},
        )

    async def _schedule_next_poll(self, feed_id: int, interval: int) -> None:
        """Schedule the next poll with 0-25% jitter added."""
        jitter = random.uniform(0, interval * 0.25)
        actual = int(interval + jitter)
        now = datetime.now(timezone.utc)
        next_poll = now + timedelta(seconds=actual)
        await self.db.update_feed_state(
            feed_id,
            last_poll_at=now.isoformat(),
            next_poll_at=next_poll.isoformat(),
            poll_interval=interval,
        )

    @staticmethod
    def _extract_timestamps(items: list[FeedItem]) -> list[datetime]:
        """Parse published dates from items, skipping unparseable ones."""
        timestamps = []
        for item in items:
            if not item.published:
                continue
            try:
                dt = dateutil_parser.parse(item.published)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                timestamps.append(dt)
            except (ValueError, OverflowError):
                continue
        return timestamps
