"""Embed formatter: converts FeedItems into Discord embeds."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import discord
from dateutil import parser as dateutil_parser

from cordfeeder.parser import FeedItem


def feed_colour(feed_url: str) -> discord.Colour:
    """Generate a consistent colour from a feed URL by hashing."""
    digest = hashlib.md5(feed_url.encode()).hexdigest()  # noqa: S324
    return discord.Colour(int(digest[:6], 16))


def _format_date(published: str | None) -> str | None:
    """Format a publish date for display.

    Returns relative format if recent ("2m ago", "3h ago"),
    absolute otherwise ("26 Feb 2026"). Returns None if unparseable.
    """
    if not published:
        return None

    try:
        dt = dateutil_parser.parse(published)
    except (ValueError, OverflowError):
        return None

    # Ensure timezone-aware for comparison
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    delta = now - dt

    total_seconds = delta.total_seconds()
    if total_seconds < 0:
        # Future date — show absolute
        return dt.strftime("%-d %b %Y")

    if total_seconds < 60:
        return "just now"
    if total_seconds < 3600:
        minutes = int(total_seconds // 60)
        return f"{minutes}m ago"
    if total_seconds < 86400:
        hours = int(total_seconds // 3600)
        return f"{hours}h ago"
    if total_seconds < 7 * 86400:
        days = int(total_seconds // 86400)
        return f"{days}d ago"

    return dt.strftime("%-d %b %Y")


def format_item_message(
    item: FeedItem,
    feed_name: str,
    feed_id: int,
) -> str:
    """Format a feed item as a plain Discord message.

    Produces a compact, non-intrusive format:
        **Feed Name** · [Article Title](<url>) · 2h ago
        > Summary text truncated at word boundary...

    URLs are wrapped in <> to suppress Discord's auto link preview.
    If the item has an image, it's shown inline instead of the summary.
    """
    # Header line: feed name · linked title · date
    # Wrap URL in <> to suppress Discord's automatic link preview
    parts = [f"**{feed_name}**"]
    parts.append(f"[{item.title}](<{item.link}>)")
    date_str = _format_date(item.published)
    if date_str:
        parts.append(date_str)
    header = " · ".join(parts)

    # If there's an image, show it inline (Discord renders image URLs as images)
    if item.image_url:
        return f"{header}\n{item.image_url}"

    # Otherwise show summary as a blockquote
    if item.summary:
        quoted = "\n".join(f"> {line}" for line in item.summary.splitlines())
        return f"{header}\n{quoted}"

    return header


def format_item_embed(
    item: FeedItem,
    feed_name: str,
    feed_url: str,
    feed_id: int,
    feed_icon_url: str | None = None,
) -> discord.Embed:
    """Format a feed item as a Discord embed (used for previews)."""
    description = item.summary if item.summary else None

    embed = discord.Embed(
        title=item.title,
        url=item.link,
        description=description,
        colour=feed_colour(feed_url),
    )

    author_kwargs: dict[str, str] = {"name": feed_name}
    if feed_icon_url:
        author_kwargs["icon_url"] = feed_icon_url
    embed.set_author(**author_kwargs)

    if item.image_url:
        embed.set_thumbnail(url=item.image_url)

    # Footer: formatted date + feed ID
    footer_parts: list[str] = []
    formatted_date = _format_date(item.published)
    if formatted_date:
        footer_parts.append(formatted_date)
    footer_parts.append(f"feed ID: {feed_id}")

    embed.set_footer(text=" \u00b7 ".join(footer_parts))

    return embed
