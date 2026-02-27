"""Feed auto-discovery: find an RSS/Atom feed URL from any webpage."""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import aiohttp
import feedparser

logger = logging.getLogger(__name__)

# Feed-type strings we look for in <link> tags
_FEED_TYPES = ("rss+xml", "atom+xml", "feed+json")

# Regex to find <link> tags with their attributes
_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE | re.DOTALL)

# Regex to extract individual attributes from a tag
_ATTR_RE = re.compile(r"""(\w[\w-]*)=["']([^"']*?)["']""", re.IGNORECASE)

# Well-known feed paths to probe
_WELL_KNOWN_PATHS = (
    "/feed",
    "/feed.xml",
    "/rss.xml",
    "/atom.xml",
    "/rss",
    "/index.xml",
    "/feed.json",
    "/blog/feed",
)

_PROBE_TIMEOUT = aiohttp.ClientTimeout(total=10)


class FeedNotFoundError(Exception):
    def __init__(self, url: str):
        self.url = url
        super().__init__(f"No RSS/Atom feed found at {url}")


def _find_feed_links(html: str, base_url: str) -> list[str]:
    """Extract feed URLs from HTML <link> tags.

    Looks for <link rel="alternate" type="application/rss+xml|atom+xml|feed+json">
    and resolves relative hrefs against base_url.
    """
    results: list[str] = []

    for tag_match in _LINK_TAG_RE.finditer(html):
        tag = tag_match.group(0)
        attrs: dict[str, str] = {}
        for attr_match in _ATTR_RE.finditer(tag):
            attrs[attr_match.group(1).lower()] = attr_match.group(2)

        rel = attrs.get("rel", "").lower()
        link_type = attrs.get("type", "").lower()
        href = attrs.get("href", "")

        if rel != "alternate" or not href:
            continue

        if any(ft in link_type for ft in _FEED_TYPES):
            results.append(urljoin(base_url, href))

    return results


def _looks_like_html(content_type: str, body: str) -> bool:
    """Check whether a response is HTML based on content-type or body prefix."""
    if "html" in content_type.lower():
        return True
    stripped = body.lstrip()[:100].lower()
    return stripped.startswith("<!doctype") or stripped.startswith("<html")


def _is_valid_feed(body: str) -> bool:
    """Check whether body parses as a valid feed (has entries or a title)."""
    parsed = feedparser.parse(body)
    if parsed.entries:
        return True
    if parsed.feed.get("title"):
        return True
    return False


def _content_type_looks_feedish(content_type: str) -> bool:
    """Check whether a content-type header suggests feed content."""
    ct = content_type.lower()
    return any(kw in ct for kw in ("xml", "rss", "atom", "json"))


async def discover_feed_url(
    url: str,
    session: aiohttp.ClientSession,
    timeout: aiohttp.ClientTimeout,
) -> str:
    """Discover the feed URL for a given URL.

    Tries direct parsing, HTML autodiscovery, and well-known path probing.
    Returns the feed URL if found, raises FeedNotFoundError otherwise.
    """
    logger.info("starting feed discovery", extra={"url": url})

    # Step 1: Direct parse
    logger.debug("step 1: direct parse", extra={"url": url})
    try:
        async with session.get(url, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            body = await resp.text()
    except Exception as exc:
        logger.warning(
            "failed to fetch url",
            extra={"url": url, "err.type": type(exc).__name__, "err.msg": str(exc)},
        )
        raise FeedNotFoundError(url) from exc

    if _is_valid_feed(body):
        logger.info("url is a valid feed directly", extra={"url": url})
        return url

    # Step 2: HTML autodiscovery
    if _looks_like_html(content_type, body):
        logger.debug("step 2: html autodiscovery", extra={"url": url})
        feed_links = _find_feed_links(body, url)
        for link in feed_links:
            logger.debug("found feed link in html", extra={"feed_url": link})
            try:
                async with session.get(link, timeout=_PROBE_TIMEOUT) as resp:
                    probe_body = await resp.text()
                if _is_valid_feed(probe_body):
                    logger.info(
                        "discovered feed via html link tag",
                        extra={"url": url, "feed_url": link},
                    )
                    return link
            except Exception:
                logger.debug("html-discovered link failed", extra={"feed_url": link})
                continue

    # Step 3: Well-known URL probing
    logger.debug("step 3: well-known path probing", extra={"url": url})
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    for path in _WELL_KNOWN_PATHS:
        probe_url = base + path
        try:
            async with session.head(probe_url, timeout=_PROBE_TIMEOUT) as resp:
                if resp.status != 200:
                    continue
                ct = resp.headers.get("Content-Type", "")
                if not _content_type_looks_feedish(ct):
                    continue

            # HEAD looked promising — GET and verify
            async with session.get(probe_url, timeout=_PROBE_TIMEOUT) as resp:
                probe_body = await resp.text()

            if _is_valid_feed(probe_body):
                logger.info(
                    "discovered feed via well-known path",
                    extra={"url": url, "feed_url": probe_url},
                )
                return probe_url
        except Exception:
            logger.debug("well-known probe failed", extra={"probe_url": probe_url})
            continue

    # Step 4: Give up
    logger.warning("no feed found", extra={"url": url})
    raise FeedNotFoundError(url)
