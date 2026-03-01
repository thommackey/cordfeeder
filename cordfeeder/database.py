from __future__ import annotations

import logging
from datetime import UTC, datetime

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    url                 TEXT NOT NULL,
    name                TEXT NOT NULL,
    channel_id          INTEGER NOT NULL,
    guild_id            INTEGER NOT NULL,
    added_by            INTEGER NOT NULL,
    created_at          TEXT NOT NULL,
    etag                TEXT,
    last_modified       TEXT,
    last_poll_at        TEXT,
    next_poll_at        TEXT,
    poll_interval       INTEGER NOT NULL DEFAULT 900,
    consecutive_errors  INTEGER NOT NULL DEFAULT 0,
    last_error          TEXT,
    UNIQUE(url, guild_id)
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


_UPDATABLE_STATE_COLUMNS = frozenset(
    {
        "etag",
        "last_modified",
        "last_poll_at",
        "next_poll_at",
        "poll_interval",
        "consecutive_errors",
        "last_error",
    }
)


class Database:
    """Async SQLite persistence layer for CordFeeder."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        """Return the active connection, raising if not yet initialised."""
        if self._db is None:
            raise RuntimeError("Database not initialised; call initialise() first")
        return self._db

    async def initialise(self) -> None:
        """Open the SQLite connection and create tables."""
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        logger.info("database initialised", extra={"path": self._path})

    async def close(self) -> None:
        """Close the database connection."""
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
        """Insert a new feed row (state columns use schema defaults).

        Returns:
            The auto-generated feed ID.
        """
        now = datetime.now(UTC).isoformat()
        async with self._conn.execute(
            """INSERT INTO feeds (url, name, channel_id, guild_id, added_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (url, name, channel_id, guild_id, added_by, now),
        ) as cursor:
            feed_id = cursor.lastrowid
        assert feed_id is not None
        await self._conn.commit()
        logger.info(
            "feed added",
            extra={"feed_id": feed_id, "url": url, "guild_id": guild_id},
        )
        return feed_id

    async def remove_feed(self, feed_id: int) -> None:
        """Delete a feed and cascade to its posted items."""
        await self._conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
        await self._conn.commit()
        logger.info("feed removed", extra={"feed_id": feed_id})

    async def get_feed(self, feed_id: int) -> dict | None:
        """Fetch a single feed row by ID, or None if not found."""
        async with self._conn.execute(
            "SELECT * FROM feeds WHERE id = ?", (feed_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_feeds(self, guild_id: int) -> list[dict]:
        """List all feeds for a guild."""
        async with self._conn.execute(
            "SELECT * FROM feeds WHERE guild_id = ? ORDER BY name",
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_feed_by_url(self, url: str, guild_id: int) -> dict | None:
        """Look up a feed by its URL within a guild."""
        async with self._conn.execute(
            "SELECT * FROM feeds WHERE url = ? AND guild_id = ?", (url, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def update_feed_channel(self, feed_id: int, channel_id: int) -> None:
        """Move a feed to a different Discord channel."""
        await self._conn.execute(
            "UPDATE feeds SET channel_id = ? WHERE id = ?", (channel_id, feed_id)
        )
        await self._conn.commit()
        logger.info(
            "feed channel updated",
            extra={"feed_id": feed_id, "channel_id": channel_id},
        )

    async def update_feed_url(self, feed_id: int, new_url: str) -> None:
        """Update the URL of an existing feed."""
        await self._conn.execute(
            "UPDATE feeds SET url = ? WHERE id = ?", (new_url, feed_id)
        )
        await self._conn.commit()
        logger.info(
            "feed url updated",
            extra={"feed_id": feed_id, "new_url": new_url},
        )

    # ------------------------------------------------------------------
    # Feed state
    # ------------------------------------------------------------------

    async def get_feed_state(self, feed_id: int) -> dict | None:
        """Fetch the polling state columns for a feed, or None if not found."""
        async with self._conn.execute(
            """SELECT etag, last_modified, last_poll_at, next_poll_at,
                      poll_interval, consecutive_errors, last_error
               FROM feeds WHERE id = ?""",
            (feed_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def update_feed_state(self, feed_id: int, **kwargs: object) -> None:
        """Update one or more state columns on a feed row.

        Raises:
            ValueError: If any column name is not in the allowed set.
        """
        if not kwargs:
            return
        bad = kwargs.keys() - _UPDATABLE_STATE_COLUMNS
        if bad:
            raise ValueError(f"unknown feed_state columns: {sorted(bad)}")
        columns = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [feed_id]
        await self._conn.execute(
            f"UPDATE feeds SET {columns} WHERE id = ?",  # noqa: S608
            values,
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # Posted items
    # ------------------------------------------------------------------

    async def record_posted_item(
        self, feed_id: int, item_guid: str, message_id: int | None = None
    ) -> None:
        """Record an item as posted (idempotent via INSERT OR IGNORE)."""
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            """INSERT OR IGNORE INTO posted_items
               (feed_id, item_guid, posted_at, message_id)
               VALUES (?, ?, ?, ?)""",
            (feed_id, item_guid, now, message_id),
        )
        await self._conn.commit()

    async def is_item_posted(self, feed_id: int, item_guid: str) -> bool:
        """Check whether an item GUID has already been posted for a feed."""
        async with self._conn.execute(
            "SELECT 1 FROM posted_items WHERE feed_id = ? AND item_guid = ?",
            (feed_id, item_guid),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def get_posted_guids(self, feed_id: int, guids: list[str]) -> set[str]:
        """Return the subset of guids that have already been posted for a feed."""
        if not guids:
            return set()
        placeholders = ",".join("?" for _ in guids)
        # Placeholders are generated "?" literals, not user input
        sql = f"SELECT item_guid FROM posted_items WHERE feed_id = ? AND item_guid IN ({placeholders})"  # noqa: S608, E501
        async with self._conn.execute(sql, [feed_id, *guids]) as cursor:
            rows = await cursor.fetchall()
        return {row["item_guid"] for row in rows}

    async def prune_old_items(self, days: int = 90) -> int:
        """Delete posted_items older than *days*. Returns the count deleted."""
        now = datetime.now(UTC).isoformat()
        async with self._conn.execute(
            """DELETE FROM posted_items
               WHERE posted_at < datetime(?, '-' || ? || ' days')""",
            (now, days),
        ) as cursor:
            count = cursor.rowcount
        await self._conn.commit()
        logger.info("pruned old items", extra={"deleted": count, "days": days})
        return count

    # ------------------------------------------------------------------
    # Polling queries
    # ------------------------------------------------------------------

    async def get_due_feeds(self) -> list[dict]:
        """Return feeds whose next_poll_at is NULL or in the past."""
        now = datetime.now(UTC).isoformat()
        async with self._conn.execute(
            """SELECT * FROM feeds
               WHERE next_poll_at IS NULL OR next_poll_at <= ?
               ORDER BY next_poll_at""",
            (now,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]
