"""Embed formatter: converts FeedItems into Discord embeds."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from dateutil import parser as dateutil_parser

from cordfeeder.parser import FeedItem

# Matches Discord mention patterns: @everyone, @here, <@id>, <@!id>, <@&id>
_MENTION_RE = re.compile(r"@(everyone|here)|<@[!&]?\d+>")

# Characters with special meaning in Discord markdown
_MD_SPECIAL = str.maketrans(
    {
        "*": "\\*",
        "_": "\\_",
        "~": "\\~",
        "`": "\\`",
        "|": "\\|",
        ">": "\\>",
        "[": "\\[",
        "]": "\\]",
    }
)


def sanitise_mentions(text: str) -> str:
    """Neutralise Discord mentions so feed content can't ping users."""
    return _MENTION_RE.sub(lambda m: m.group(0).replace("@", "@\u200b"), text)


def _sanitise_markdown(text: str) -> str:
    """Escape Discord markdown special characters in untrusted text."""
    return text.translate(_MD_SPECIAL)


def _sanitise_url(url: str) -> str:
    """Sanitise a URL for safe embedding in Discord markdown.

    Prevents breakout from [text](<url>) syntax by:
    - Truncating at the first whitespace (URLs can't contain unencoded spaces/newlines)
    - Encoding > which closes the <url> wrapper
    - Rejecting non-http(s) schemes

    Returns empty string if the URL is unsafe or empty.
    """
    if not url:
        return ""
    # Truncate at first whitespace/newline — anything after is injection payload
    url = url.strip().split()[0] if url.strip() else ""
    # Encode > to prevent breaking out of <url> angle-bracket wrappers
    url = url.replace(">", "%3E")
    # Only allow http/https — no javascript:, data:, file:, etc.
    if not url.lower().startswith(("http://", "https://")):
        return ""
    return url


def _strip_newlines(text: str) -> str:
    """Replace newlines with spaces to prevent content injection in headers."""
    return text.replace("\n", " ").replace("\r", "")


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
        dt = dt.replace(tzinfo=UTC)

    now = datetime.now(UTC)
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
    # Sanitise untrusted feed content to prevent mention injection,
    # markdown escape attacks, and content injection via newlines/URLs.
    safe_name = _strip_newlines(sanitise_mentions(feed_name))
    safe_title = _strip_newlines(sanitise_mentions(_sanitise_markdown(item.title)))
    safe_summary = sanitise_mentions(item.summary) if item.summary else ""
    safe_link = _sanitise_url(item.link)
    safe_image = _sanitise_url(item.image_url) if item.image_url else None

    # Header line: feed name · linked title · date
    # Wrap URL in <> to suppress Discord's automatic link preview
    parts = [f"**{safe_name}**"]
    if safe_link:
        parts.append(f"[{safe_title}](<{safe_link}>)")
    else:
        parts.append(safe_title)
    date_str = _format_date(item.published)
    if date_str:
        parts.append(date_str)
    header = " · ".join(parts)

    # Decide between image-primary (webcomics) and text-primary (newsletters).
    # If the summary has substantial text, treat images as decorative thumbnails
    # and show the text instead.  Only show images inline when the summary is
    # minimal — i.e. the image IS the content.
    text_primary = bool(safe_summary and len(item.summary) > 100)

    if safe_image and not text_primary:
        return f"{header}\n{safe_image}"

    if safe_summary:
        quoted = "\n".join(f"> {line}" for line in safe_summary.splitlines())
        return f"{header}\n{quoted}"

    return header
