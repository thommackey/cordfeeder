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
