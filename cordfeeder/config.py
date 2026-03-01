from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Immutable application configuration, loaded from environment variables."""

    discord_token: str
    feed_manager_role: str
    default_poll_interval: int
    database_path: str
    log_level: str
    min_poll_interval: int = 300  # 5 minutes
    max_poll_interval: int = 43200  # 12 hours
    max_items_per_poll: int = 5
    initial_items_count: int = 3
    user_agent: str = "CordFeeder/1.0 (Discord RSS bot)"

    @classmethod
    def from_env(cls) -> Config:
        """Build a Config from environment variables.

        Raises:
            ValueError: If required variables are missing or malformed.
        """
        token = os.environ.get("DISCORD_BOT_TOKEN", "")
        if not token:
            raise ValueError("DISCORD_BOT_TOKEN environment variable is required")
        return cls(
            discord_token=token,
            feed_manager_role=os.environ.get("FEED_MANAGER_ROLE", "Feed Manager"),
            default_poll_interval=_int_env("DEFAULT_POLL_INTERVAL", 900),
            database_path=os.environ.get("DATABASE_PATH", "data/cordfeeder.db"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )

    def log_summary(self) -> dict:
        """Return config values safe for logging (token redacted)."""
        return {
            "feed_manager_role": self.feed_manager_role,
            "default_poll_interval": self.default_poll_interval,
            "min_poll_interval": self.min_poll_interval,
            "max_poll_interval": self.max_poll_interval,
            "max_items_per_poll": self.max_items_per_poll,
            "database_path": self.database_path,
            "log_level": self.log_level,
        }


def _int_env(name: str, default: int) -> int:
    """Read an integer from an environment variable with a clear error."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got: {raw!r}") from None
