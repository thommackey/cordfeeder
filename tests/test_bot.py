"""Tests for the Discord bot setup."""

from unittest.mock import MagicMock

from cordfeeder.bot import CordFeederBot
from cordfeeder.config import Config


def test_bot_suppresses_all_mentions():
    """Bot must not allow feed content to trigger @everyone, @here, or user mentions."""
    config = Config(
        discord_token="test-token",
        default_poll_interval=900,
        database_path=":memory:",
        log_level="INFO",
    )
    db = MagicMock()
    bot = CordFeederBot(config=config, db=db)
    am = bot.allowed_mentions
    assert am is not None
    assert am.everyone is False
    assert am.users is False
    assert am.roles is False
