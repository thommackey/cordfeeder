from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
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


_FEED_STATE_COLUMNS = frozenset({
    "etag", "last_modified", "last_poll_at", "next_poll_at",
    "poll_interval", "consecutive_errors", "last_error",
})


class Database:
    """Async SQLite persistence layer for CordFeeder."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def initialise(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        logger.info("database initialised", extra={"path": self._path})

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Feeds
    # ------------------------------------------------------------------

    async def add_feed(
        self,
        url: str,
        name: str,
        channel_id: int,
        guild_id: int,
        added_by: int,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with self._db.execute(
            """INSERT INTO feeds (url, name, channel_id, guild_id, added_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (url, name, channel_id, guild_id, added_by, now),
        ) as cursor:
            feed_id = cursor.lastrowid

        # Create initial feed_state row (next_poll_at=NULL → immediately due)
        await self._db.execute(
            "INSERT INTO feed_state (feed_id) VALUES (?)", (feed_id,)
        )
        await self._db.commit()
        logger.info(
            "feed added",
            extra={"feed_id": feed_id, "url": url, "guild_id": guild_id},
        )
        return feed_id

    async def remove_feed(self, feed_id: int) -> None:
        await self._db.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
        await self._db.commit()
        logger.info("feed removed", extra={"feed_id": feed_id})

    async def get_feed(self, feed_id: int) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM feeds WHERE id = ?", (feed_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_feeds(self, guild_id: int) -> list[dict]:
        async with self._db.execute(
            """SELECT f.*, fs.last_poll_at, fs.next_poll_at,
                      fs.poll_interval, fs.consecutive_errors
               FROM feeds f
               LEFT JOIN feed_state fs ON fs.feed_id = f.id
               WHERE f.guild_id = ?
               ORDER BY f.name""",
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def update_feed_url(self, feed_id: int, new_url: str) -> None:
        await self._db.execute(
            "UPDATE feeds SET url = ? WHERE id = ?", (new_url, feed_id)
        )
        await self._db.commit()
        logger.info(
            "feed url updated",
            extra={"feed_id": feed_id, "new_url": new_url},
        )

    # ------------------------------------------------------------------
    # Feed state
    # ------------------------------------------------------------------

    async def get_feed_state(self, feed_id: int) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM feed_state WHERE feed_id = ?", (feed_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def update_feed_state(self, feed_id: int, **kwargs) -> None:
        if not kwargs:
            return
        bad = kwargs.keys() - _FEED_STATE_COLUMNS
        if bad:
            raise ValueError(f"unknown feed_state columns: {sorted(bad)}")
        columns = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [feed_id]
        await self._db.execute(
            f"UPDATE feed_state SET {columns} WHERE feed_id = ?", values  # noqa: S608
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Posted items
    # ------------------------------------------------------------------

    async def record_posted_item(
        self, feed_id: int, item_guid: str, message_id: int | None = None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT OR IGNORE INTO posted_items (feed_id, item_guid, posted_at, message_id)
               VALUES (?, ?, ?, ?)""",
            (feed_id, item_guid, now, message_id),
        )
        await self._db.commit()

    async def is_item_posted(self, feed_id: int, item_guid: str) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM posted_items WHERE feed_id = ? AND item_guid = ?",
            (feed_id, item_guid),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def prune_old_items(self, days: int = 90) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with self._db.execute(
            """DELETE FROM posted_items
               WHERE posted_at < datetime(?, '-' || ? || ' days')""",
            (now, days),
        ) as cursor:
            count = cursor.rowcount
        await self._db.commit()
        logger.info("pruned old items", extra={"deleted": count, "days": days})
        return count

    # ------------------------------------------------------------------
    # Polling queries
    # ------------------------------------------------------------------

    async def get_due_feeds(self) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        async with self._db.execute(
            """SELECT f.*, fs.etag, fs.last_modified, fs.last_poll_at,
                      fs.next_poll_at, fs.poll_interval, fs.consecutive_errors
               FROM feeds f
               JOIN feed_state fs ON fs.feed_id = f.id
               WHERE fs.next_poll_at IS NULL OR fs.next_poll_at <= ?
               ORDER BY fs.next_poll_at""",
            (now,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]
