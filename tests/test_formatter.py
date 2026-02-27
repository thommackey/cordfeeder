"""Tests for feed item formatting."""

import discord
import pytest

from cordfeeder.formatter import feed_colour, format_item_embed, format_item_message
from cordfeeder.parser import FeedItem


# ---------------------------------------------------------------
# Plain message format (primary format for feed items)
# ---------------------------------------------------------------


def test_message_basic():
    item = FeedItem(
        title="Test Article",
        link="https://example.com/1",
        guid="1",
        summary="A summary of the article.",
        author="Alice",
        published="Wed, 25 Feb 2026 12:00:00 GMT",
        image_url=None,
    )
    msg = format_item_message(item, feed_name="Test Feed", feed_id=3)
    assert "**Test Feed**" in msg
    assert "[Test Article](<https://example.com/1>)" in msg
    assert "> A summary of the article." in msg


def test_message_no_summary():
    item = FeedItem(
        title="Title Only",
        link="https://example.com/3",
        guid="3",
        summary="",
        author=None,
        published=None,
        image_url=None,
    )
    msg = format_item_message(item, feed_name="Feed", feed_id=1)
    assert "[Title Only]" in msg
    assert "\n>" not in msg  # no blockquote when no summary


def test_message_with_image_shows_inline():
    item = FeedItem(
        title="Comic Strip",
        link="https://example.com/comic/1",
        guid="1",
        summary="Click here to see more.",
        author=None,
        published=None,
        image_url="https://example.com/comics/strip.png",
    )
    msg = format_item_message(item, feed_name="SMBC", feed_id=2)
    # Image URL should be on its own line (Discord renders it inline)
    assert "https://example.com/comics/strip.png" in msg
    # Summary should NOT appear when there's an image
    assert "Click here" not in msg


def test_message_with_date():
    item = FeedItem(
        title="Dated",
        link="https://example.com/1",
        guid="1",
        summary="",
        author=None,
        published="Mon, 23 Feb 2026 12:00:00 GMT",
        image_url=None,
    )
    msg = format_item_message(item, feed_name="Feed", feed_id=1)
    # Should have a date component (either relative or absolute)
    parts = msg.split(" · ")
    assert len(parts) >= 2  # at minimum: feed name · title


# ---------------------------------------------------------------
# Embed format (used for previews)
# ---------------------------------------------------------------


def test_format_basic_embed():
    item = FeedItem(
        title="Test Article",
        link="https://example.com/1",
        guid="1",
        summary="A summary of the article.",
        author="Alice",
        published="Wed, 25 Feb 2026 12:00:00 GMT",
        image_url=None,
    )
    embed = format_item_embed(
        item,
        feed_name="Test Feed",
        feed_url="https://example.com/rss",
        feed_id=3,
    )
    assert isinstance(embed, discord.Embed)
    assert embed.title == "Test Article"
    assert embed.url == "https://example.com/1"
    assert "summary" in embed.description.lower()
    assert "3" in embed.footer.text


def test_format_embed_with_image():
    item = FeedItem(
        title="Image Post",
        link="https://example.com/2",
        guid="2",
        summary="Has an image.",
        author=None,
        published=None,
        image_url="https://example.com/img.jpg",
    )
    embed = format_item_embed(
        item,
        feed_name="Feed",
        feed_url="https://example.com/rss",
        feed_id=1,
    )
    assert embed.thumbnail.url == "https://example.com/img.jpg"


def test_feed_colour_consistent():
    c1 = feed_colour("https://example.com/rss")
    c2 = feed_colour("https://example.com/rss")
    c3 = feed_colour("https://other.com/rss")
    assert c1 == c2
    assert c1 != c3
