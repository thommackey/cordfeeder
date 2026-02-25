from pathlib import Path

import pytest

from cordfeeder.parser import FeedItem, FeedMetadata, parse_feed, extract_feed_metadata

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


class TestExtractFeedMetadata:
    def test_extract_feed_metadata(self):
        meta = extract_feed_metadata(_read("sample_rss.xml"))
        assert meta.title == "Test Feed"
        assert meta.link == "https://example.com"
        assert meta.description == "A test RSS feed"
        assert meta.ttl == 60
