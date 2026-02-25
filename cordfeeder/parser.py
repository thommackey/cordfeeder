"""Feed parser: RSS/Atom parsing via feedparser with HTML stripping and truncation."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

import feedparser


@dataclass(frozen=True, slots=True)
class FeedItem:
    title: str
    link: str
    guid: str
    summary: str
    author: str | None
    published: str | None
    image_url: str | None


@dataclass(frozen=True, slots=True)
class FeedMetadata:
    title: str
    link: str | None
    description: str | None
    ttl: int | None
    image_url: str | None


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    stripped = _TAG_RE.sub("", text)
    return html.unescape(stripped).strip()


def _truncate(text: str, max_len: int = 300) -> str:
    """Truncate at word boundary, appending '...' if shortened."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    # find last space to break at word boundary
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + "..."


def _extract_image(entry: dict) -> str | None:
    """Extract image URL from media_content, media_thumbnail, or enclosures."""
    # media_content
    for media in getattr(entry, "media_content", None) or []:
        url = media.get("url", "")
        if media.get("medium") == "image" or url.lower().split("?")[0].endswith(
            (".jpg", ".jpeg", ".png", ".gif", ".webp")
        ):
            return url

    # media_thumbnail
    for thumb in getattr(entry, "media_thumbnail", None) or []:
        url = thumb.get("url")
        if url:
            return url

    # enclosures
    for enc in getattr(entry, "enclosures", None) or []:
        enc_type = enc.get("type", "")
        if enc_type.startswith("image/"):
            return enc.get("url")

    return None


def parse_feed(raw: str) -> list[FeedItem]:
    """Parse RSS/Atom XML string into a list of FeedItems.

    Raises ValueError if the content is unparseable and yields no entries.
    """
    parsed = feedparser.parse(raw)

    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Unparseable feed: {parsed.bozo_exception}")

    items: list[FeedItem] = []
    for entry in parsed.entries:
        summary_raw = entry.get("summary", "") or entry.get("description", "") or ""
        summary = _truncate(_strip_html(summary_raw))

        items.append(
            FeedItem(
                title=entry.get("title", ""),
                link=entry.get("link", ""),
                guid=entry.get("id", "") or entry.get("link", ""),
                summary=summary,
                author=entry.get("author"),
                published=entry.get("published"),
                image_url=_extract_image(entry),
            )
        )

    return items


def extract_feed_metadata(raw: str) -> FeedMetadata:
    """Extract feed-level metadata from RSS/Atom XML string."""
    parsed = feedparser.parse(raw)
    feed = parsed.feed

    ttl_raw = feed.get("ttl")
    ttl = int(ttl_raw) if ttl_raw is not None else None

    # feed-level image
    image_url = None
    feed_image = feed.get("image")
    if feed_image:
        image_url = feed_image.get("href") or feed_image.get("url")

    return FeedMetadata(
        title=feed.get("title", ""),
        link=feed.get("link"),
        description=feed.get("subtitle") or feed.get("description"),
        ttl=ttl,
        image_url=image_url,
    )
