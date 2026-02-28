from pathlib import Path

import pytest

from cordfeeder.parser import (
    FeedItem, FeedMetadata, parse_feed, extract_feed_metadata,
    _strip_boilerplate, _strip_html,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


class TestParseRSSFeed:
    def test_parse_rss_feed(self):
        items = parse_feed(_read("sample_rss.xml"))
        assert len(items) == 3
        assert items[0].title == "Third Post"
        assert items[0].link == "https://example.com/3"
        assert items[0].guid == "https://example.com/3"
        assert items[0].author == "alice@example.com (Alice)"
        assert items[0].published == "Wed, 25 Feb 2026 12:00:00 GMT"
        assert items[1].title == "Second Post"
        assert items[2].title == "First Post"

    def test_parse_atom_feed(self):
        items = parse_feed(_read("sample_atom.xml"))
        assert len(items) == 1
        assert items[0].title == "Atom Entry"
        assert items[0].link == "https://example.com/atom/1"
        assert items[0].guid == "urn:uuid:entry-1"
        assert items[0].summary == "An Atom entry summary"
        assert items[0].author == "Bob"

    def test_html_stripped_from_summary(self):
        items = parse_feed(_read("sample_rss.xml"))
        third = items[0]
        assert "<" not in third.summary
        assert ">" not in third.summary
        assert "third" in third.summary
        assert "HTML" in third.summary

    def test_summary_truncated(self):
        long_text = " ".join(["word"] * 200)
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <title>Long Feed</title>
            <item>
              <title>Long Post</title>
              <link>https://example.com/long</link>
              <description>{long_text}</description>
            </item>
          </channel>
        </rss>"""
        items = parse_feed(xml)
        assert len(items[0].summary) <= 303  # 300 + "..."
        assert items[0].summary.endswith("...")

    def test_items_have_guid_fallback_to_link(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <title>No GUID Feed</title>
            <item>
              <title>No GUID</title>
              <link>https://example.com/no-guid</link>
              <description>No guid here</description>
            </item>
          </channel>
        </rss>"""
        items = parse_feed(xml)
        assert items[0].guid == "https://example.com/no-guid"

    def test_parse_invalid_feed(self):
        with pytest.raises(ValueError):
            parse_feed("this is not xml at all")


    def test_extract_image_from_description_html(self):
        xml = """<?xml version="1.0"?>
        <rss version="2.0"><channel><title>T</title>
        <item><title>Comic</title><link>https://x.com/1</link>
        <guid>1</guid>
        <description>&lt;img src="https://x.com/comic.png" /&gt;&lt;br /&gt;Bonus panel</description>
        </item></channel></rss>"""
        items = parse_feed(xml)
        assert items[0].image_url == "https://x.com/comic.png"


class TestStripBoilerplate:
    def test_strips_common_prefix(self):
        summaries = [
            "Welcome to my newsletter. Here is the first story about cats.",
            "Welcome to my newsletter. Here is the second story about dogs.",
            "Welcome to my newsletter. Here is the third story about fish.",
        ]
        result = _strip_boilerplate(summaries)
        assert all(not s.startswith("Welcome") for s in result)
        assert "first story about cats" in result[0]
        assert "second story about dogs" in result[1]

    def test_strips_common_suffix(self):
        summaries = [
            "Story about cats. Subscribe to get more content delivered weekly.",
            "Story about dogs. Subscribe to get more content delivered weekly.",
            "Story about fish. Subscribe to get more content delivered weekly.",
        ]
        result = _strip_boilerplate(summaries)
        assert all(not s.endswith("weekly.") for s in result)
        assert "Story about cats" in result[0]

    def test_ignores_short_common_prefix(self):
        summaries = [
            "The cat sat on the mat.",
            "The dog lay on the rug.",
        ]
        result = _strip_boilerplate(summaries)
        assert result == summaries  # "The " is too short to be boilerplate

    def test_single_entry_unchanged(self):
        summaries = ["Welcome to my newsletter. Here is the story."]
        result = _strip_boilerplate(summaries)
        assert result == summaries

    def test_strips_both_prefix_and_suffix(self):
        summaries = [
            "Welcome to Import AI. Robots are taking over manufacturing. Subscribe now for free!",
            "Welcome to Import AI. New drone regulations proposed today. Subscribe now for free!",
        ]
        result = _strip_boilerplate(summaries)
        assert "Robots are taking over" in result[0]
        assert "drone regulations" in result[1]
        assert not result[0].startswith("Welcome")
        assert not result[0].endswith("free!")

    def test_integration_in_parse_feed(self):
        """Boilerplate stripped from items that share a common preamble."""
        boilerplate = "Welcome to our weekly newsletter about technology and science. Subscribe now! "
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <title>Boilerplate Feed</title>
            <item>
              <title>Post A</title>
              <link>https://example.com/a</link>
              <description>{boilerplate}Actual content about AI advances.</description>
            </item>
            <item>
              <title>Post B</title>
              <link>https://example.com/b</link>
              <description>{boilerplate}Actual content about quantum computing.</description>
            </item>
          </channel>
        </rss>"""
        items = parse_feed(xml)
        assert not items[0].summary.startswith("Welcome")
        assert "AI advances" in items[0].summary
        assert "quantum computing" in items[1].summary


class TestLinkProcessing:
    """HTML-to-text handling of <a> tags (Mastodon URL stripping)."""

    def test_strips_url_only_links(self):
        """Mastodon-style <a> where visible text is the URL itself."""
        html = (
            '<p>Check this out '
            '<a href="https://example.com/article">'
            '<span class="invisible">https://</span>'
            '<span class="ellipsis">example.com/article</span>'
            '</a></p>'
        )
        assert _strip_html(html) == "Check this out"

    def test_keeps_descriptive_link_text(self):
        """<a> with human-readable anchor text should keep the text."""
        html = '<p>Listen to <a href="https://example.com">@incomparable</a> discuss it.</p>'
        assert _strip_html(html) == "Listen to @incomparable discuss it."

    def test_strips_plain_url_link(self):
        """Simple <a> whose text is a bare URL."""
        html = '<p>See <a href="https://example.com">https://example.com</a> for details.</p>'
        assert _strip_html(html) == "See for details."

    def test_collapses_whitespace_after_url_removal(self):
        """Removing a URL-only link shouldn't leave double spaces."""
        html = '<p>Before <a href="https://x.com">https://x.com</a> after</p>'
        result = _strip_html(html)
        assert "  " not in result


class TestTitleSynthesis:
    """Title-less items (e.g. Mastodon) get title from summary."""

    def test_title_synthesised_from_summary(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel><title>Mastodon Feed</title>
            <item>
              <link>https://mastodon.social/@user/123</link>
              <guid>https://mastodon.social/@user/123</guid>
              <description>Does Apple still not have any official accounts on Mastodon or Bluesky?</description>
            </item>
          </channel>
        </rss>"""
        items = parse_feed(xml)
        assert items[0].title == "Does Apple still not have any official accounts on Mastodon or Bluesky?"

    def test_long_summary_title_truncated(self):
        long_text = "A " * 60  # 120 chars
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel><title>Feed</title>
            <item>
              <link>https://example.com/1</link>
              <guid>1</guid>
              <description>{long_text}</description>
            </item>
          </channel>
        </rss>"""
        items = parse_feed(xml)
        assert len(items[0].title) <= 83  # 80 + "..."
        assert items[0].title.endswith("...")

    def test_normal_title_preserved(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel><title>Blog</title>
            <item>
              <title>My Article Title</title>
              <link>https://example.com/1</link>
              <guid>1</guid>
              <description>The article content.</description>
            </item>
          </channel>
        </rss>"""
        items = parse_feed(xml)
        assert items[0].title == "My Article Title"


class TestExtractFeedMetadata:
    def test_extract_feed_metadata(self):
        meta = extract_feed_metadata(_read("sample_rss.xml"))
        assert meta.title == "Test Feed"
        assert meta.link == "https://example.com"
        assert meta.description == "A test RSS feed"
        assert meta.ttl == 60
