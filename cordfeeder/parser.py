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


# Minimum length for a common prefix/suffix to be considered boilerplate.
# Short common strings (e.g. "The ") are just coincidence, not boilerplate.
_BOILERPLATE_MIN_LEN = 20


def _strip_boilerplate(summaries: list[str]) -> list[str]:
    """Remove common prefix/suffix boilerplate shared across most summaries.

    If a supermajority (>=80%) of summaries share a long (>=20 char) prefix
    or suffix, it's almost certainly newsletter boilerplate.  Strip it at a
    word boundary.  Entries that don't match the detected boilerplate are
    left unchanged.
    """
    if len(summaries) < 2:
        return summaries

    # --- common prefix ---
    prefix = _majority_common_prefix(summaries)
    if len(prefix) >= _BOILERPLATE_MIN_LEN:
        cut = len(prefix)
        summaries = [
            s[cut:].lstrip() if s.startswith(prefix) else s
            for s in summaries
        ]

    # --- common suffix ---
    suffix = _majority_common_suffix(summaries)
    if len(suffix) >= _BOILERPLATE_MIN_LEN:
        summaries = [
            s[: -len(suffix)].rstrip() if s.endswith(suffix) else s
            for s in summaries
        ]

    return summaries


# Fraction of entries that must share the prefix/suffix to count.
_SUPERMAJORITY = 0.8


def _majority_common_prefix(strings: list[str]) -> str:
    """Find the longest prefix shared by >=80% of strings, at a word boundary."""
    if not strings:
        return ""
    threshold = max(2, int(len(strings) * _SUPERMAJORITY))

    # Start with the full text of the shortest string as candidate,
    # then shrink character-by-character until enough strings match.
    candidate = min(strings, key=len)
    while candidate:
        matches = sum(1 for s in strings if s.startswith(candidate))
        if matches >= threshold:
            break
        candidate = candidate[:-1]

    if not candidate:
        return ""
    # Snap back to last space to avoid partial words
    last_space = candidate.rfind(" ")
    return candidate[: last_space + 1] if last_space >= 0 else ""


def _majority_common_suffix(strings: list[str]) -> str:
    """Find the longest suffix shared by >=80% of strings, at a word boundary."""
    if not strings:
        return ""
    threshold = max(2, int(len(strings) * _SUPERMAJORITY))

    candidate = min(strings, key=len)
    while candidate:
        matches = sum(1 for s in strings if s.endswith(candidate))
        if matches >= threshold:
            break
        candidate = candidate[1:]

    if not candidate:
        return ""
    # Snap forward to first space to avoid partial words
    first_space = candidate.find(" ")
    return candidate[first_space + 1 :] if first_space >= 0 else ""


_IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


def _extract_image(entry: dict) -> str | None:
    """Extract image URL from media tags, enclosures, or description HTML."""
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

    # Fallback: <img> tags in description/summary HTML
    for field in ("summary", "description", "content"):
        raw = ""
        val = entry.get(field)
        if isinstance(val, list):
            raw = val[0].get("value", "") if val else ""
        elif isinstance(val, str):
            raw = val
        if raw:
            match = _IMG_RE.search(raw)
            if match:
                return match.group(1)

    return None


def parse_feed(raw: str) -> list[FeedItem]:
    """Parse RSS/Atom XML string into a list of FeedItems.

    Raises ValueError if the content is unparseable and yields no entries.
    """
    parsed = feedparser.parse(raw)

    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Unparseable feed: {parsed.bozo_exception}")

    # First pass: extract all fields, strip HTML but don't truncate yet.
    entries_data: list[tuple[dict, str]] = []
    for entry in parsed.entries:
        summary_raw = entry.get("summary", "") or entry.get("description", "") or ""
        stripped = _strip_html(summary_raw)
        entries_data.append((entry, stripped))

    # Strip boilerplate shared across all entries in this batch.
    stripped_summaries = [s for _, s in entries_data]
    cleaned_summaries = _strip_boilerplate(stripped_summaries)

    # Second pass: truncate and build FeedItems.
    items: list[FeedItem] = []
    for (entry, _), summary in zip(entries_data, cleaned_summaries):
        items.append(
            FeedItem(
                title=entry.get("title", ""),
                link=entry.get("link", ""),
                guid=entry.get("id", "") or entry.get("link", ""),
                summary=_truncate(summary),
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
