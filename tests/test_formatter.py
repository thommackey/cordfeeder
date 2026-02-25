"""Tests for embed formatting."""

import discord
import pytest

from cordfeeder.formatter import feed_colour, format_item_embed
from cordfeeder.parser import FeedItem


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


def test_format_embed_no_summary():
    item = FeedItem(
        title="Title Only",
        link="https://example.com/3",
        guid="3",
        summary="",
        author=None,
        published=None,
        image_url=None,
    )
    embed = format_item_embed(
        item,
        feed_name="Feed",
        feed_url="https://example.com/rss",
        feed_id=1,
    )
    assert embed.description is None or embed.description == ""


def test_feed_colour_consistent():
    c1 = feed_colour("https://example.com/rss")
    c2 = feed_colour("https://example.com/rss")
    c3 = feed_colour("https://other.com/rss")
    assert c1 == c2
    assert c1 != c3
