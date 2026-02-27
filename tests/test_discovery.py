"""Tests for feed auto-discovery HTML link extraction."""

from cordfeeder.discovery import _find_feed_links


def test_finds_rss_link_in_head():
    html = '''<html><head>
    <link rel="alternate" type="application/rss+xml" href="/feed.xml" title="My Feed">
    </head><body></body></html>'''
    links = _find_feed_links(html, "https://example.com")
    assert links == ["https://example.com/feed.xml"]


def test_finds_atom_link():
    html = '''<html><head>
    <link rel="alternate" type="application/atom+xml" href="https://example.com/atom.xml">
    </head><body></body></html>'''
    links = _find_feed_links(html, "https://example.com")
    assert links == ["https://example.com/atom.xml"]


def test_resolves_relative_urls():
    html = '<html><head><link rel="alternate" type="application/rss+xml" href="/blog/feed"></head></html>'
    links = _find_feed_links(html, "https://example.com")
    assert links == ["https://example.com/blog/feed"]


def test_ignores_non_feed_links():
    html = '''<html><head>
    <link rel="stylesheet" href="/style.css">
    <link rel="alternate" type="application/rss+xml" href="/feed.xml">
    </head></html>'''
    links = _find_feed_links(html, "https://example.com")
    assert links == ["https://example.com/feed.xml"]


def test_no_feed_links():
    html = '<html><head><title>No feeds here</title></head><body></body></html>'
    links = _find_feed_links(html, "https://example.com")
    assert links == []


def test_multiple_feeds_preserves_order():
    html = '''<html><head>
    <link rel="alternate" type="application/rss+xml" href="/main.xml" title="Main">
    <link rel="alternate" type="application/atom+xml" href="/comments.xml" title="Comments">
    </head></html>'''
    links = _find_feed_links(html, "https://example.com")
    assert links == ["https://example.com/main.xml", "https://example.com/comments.xml"]


def test_finds_feed_json():
    html = '''<html><head>
    <link rel="alternate" type="application/feed+json" href="/feed.json">
    </head></html>'''
    links = _find_feed_links(html, "https://example.com")
    assert links == ["https://example.com/feed.json"]


def test_handles_single_quoted_attributes():
    html = "<html><head><link rel='alternate' type='application/rss+xml' href='/feed.xml'></head></html>"
    links = _find_feed_links(html, "https://example.com")
    assert links == ["https://example.com/feed.xml"]
