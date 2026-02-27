"""Tests for the Discord bot's has_feed_manager_role function."""

from unittest.mock import MagicMock

from cordfeeder.bot import has_feed_manager_role


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
