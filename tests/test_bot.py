"""Tests for the Discord bot's has_feed_manager_role function."""

from unittest.mock import MagicMock

import discord

from cordfeeder.bot import CordFeederBot, has_feed_manager_role
from cordfeeder.config import Config


def _make_role(name: str) -> MagicMock:
    role = MagicMock()
    role.name = name
    return role


def _make_interaction(role_names: list[str]) -> MagicMock:
    interaction = MagicMock()
    interaction.user.roles = [_make_role(n) for n in role_names]
    return interaction


def test_has_feed_manager_role():
    interaction = _make_interaction(["Feed Manager", "Member"])
    assert has_feed_manager_role(interaction, "Feed Manager") is True


def test_lacks_feed_manager_role():
    interaction = _make_interaction(["Member"])
    assert has_feed_manager_role(interaction, "Feed Manager") is False


def test_feed_manager_role_case_sensitive():
    interaction = _make_interaction(["feed manager"])
    assert has_feed_manager_role(interaction, "Feed Manager") is False


def test_bot_suppresses_all_mentions():
    """Bot must not allow feed content to trigger @everyone, @here, or user mentions."""
    config = Config(
        discord_token="test-token",
        feed_manager_role="Feed Manager",
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
