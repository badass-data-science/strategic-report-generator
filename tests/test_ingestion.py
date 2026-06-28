"""
Tests for RSS ingestion (ingestion.py) — feedparser is mocked via patch().

WHY MOCK FEEDPARSER?
--------------------
ingestion.py calls feedparser.parse(url), which makes a real network request.
We never want unit tests to hit the network because:
  1. Tests would fail with no internet connection
  2. External feeds change content — tests become non-deterministic
  3. Tests are slow (network I/O is milliseconds, not microseconds)

Instead, we use unittest.mock.patch to replace feedparser.parse with a
function that returns our make_parsed_feed() mock objects.

PATCH TARGET: WHERE TO PATCH
------------------------------
This is the most common mocking mistake in Python. You must patch the name
as it's used in the module under test, not where it's defined.

ingestion.py does:  import feedparser
                    parsed = await asyncio.to_thread(feedparser.parse, url)

So the name "feedparser.parse" is looked up in the ingestion module's namespace.
The correct patch target is:
    "strategic_reports.daily.core.ingestion.feedparser.parse"

NOT:
    "feedparser.parse"   ← patches the feedparser module directly, but
                            ingestion.py already holds a reference to its
                            own copy of feedparser — the patch doesn't reach it

asyncio.to_thread calls feedparser.parse in a thread pool, so we patch it
synchronously (return_value=) rather than as a coroutine — to_thread handles
the async wrapping.

TESTING ASYNC FUNCTIONS WITH PYTEST-ASYNCIO
--------------------------------------------
_fetch_one_feed and fetch_topic_articles are both async functions (async def).
In pytest.ini we have asyncio_mode = auto, which means:
  - All test methods that are "async def" automatically run inside an event loop
  - No @pytest.mark.asyncio decorator needed
  - "await somefunction()" works directly inside test methods

Without asyncio_mode = auto, you'd need:
    @pytest.mark.asyncio
    async def test_something(): ...

PRIVATE FUNCTION TESTING
--------------------------
We test _fetch_one_feed directly even though it's "private" (leading underscore).
The convention means "not part of the public API," but it's perfectly valid to
test private helpers when their logic is complex enough to warrant it. The
underscore is a signal to callers, not a Python access restriction.
"""

import datetime
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from strategic_reports.daily.core.ingestion import _fetch_one_feed, fetch_topic_articles
from strategic_reports.daily.core.models import FeedConfig, TopicConfig
from tests.conftest import make_feed_entry, make_parsed_feed


# Module-level constant for the FeedConfig used in single-feed tests.
# Defined here (not in a fixture) because it's simple and doesn't vary per test.
FEED = FeedConfig(title="Test Feed", url="https://example.com/feed")


class TestFetchOneFeed:
    """Tests for _fetch_one_feed() — the per-feed coroutine."""

    async def test_returns_articles_within_cutoff(self):
        """
        An article published 30 minutes ago should be returned within a 24h cutoff.
        make_feed_entry(hours_ago=0.5) simulates a recent article.
        """
        entries = [
            make_feed_entry("Recent Article", "https://x.com/1", "Content 1", hours_ago=0.5),
        ]
        parsed = make_parsed_feed(entries)

        # patch() replaces feedparser.parse in ingestion.py's namespace.
        # return_value=parsed means every call to feedparser.parse returns our mock.
        # The 'with' block restores the original function after the test.
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse", return_value=parsed):
            articles = await _fetch_one_feed(FEED, hours_cutoff=24)

        assert len(articles) == 1
        assert articles[0].title == "Recent Article"
        assert articles[0].link == "https://x.com/1"

    async def test_excludes_articles_beyond_cutoff(self):
        """
        An article published 48 hours ago should be excluded by a 24h cutoff.
        This verifies the cutoff filtering logic in _fetch_one_feed.
        """
        entries = [
            make_feed_entry("Old Article", "https://x.com/old", "Old content", hours_ago=48),
        ]
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   return_value=make_parsed_feed(entries)):
            articles = await _fetch_one_feed(FEED, hours_cutoff=24)

        assert articles == []

    async def test_mixed_age_articles(self):
        """
        Feed with one recent and one old article — only the recent one is returned.
        This is the realistic case: feeds have days of history.
        """
        entries = [
            make_feed_entry("Recent", "https://x.com/r", "body", hours_ago=1),
            make_feed_entry("Old", "https://x.com/o", "body", hours_ago=48),
        ]
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   return_value=make_parsed_feed(entries)):
            articles = await _fetch_one_feed(FEED, hours_cutoff=24)

        assert len(articles) == 1
        assert articles[0].title == "Recent"

    async def test_returns_empty_on_feedparser_exception(self):
        """
        If feedparser.parse raises (network error, timeout, etc.), _fetch_one_feed
        should return [] instead of propagating the exception.

        side_effect=Exception(...) makes the mock raise instead of returning.
        This tests the try/except around asyncio.to_thread in ingestion.py.
        """
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   side_effect=Exception("network error")):
            articles = await _fetch_one_feed(FEED, hours_cutoff=24)

        assert articles == []

    async def test_skips_entry_with_no_published_parsed(self):
        """
        An entry whose published_parsed is None should be silently skipped.
        mktime(None) raises TypeError; the inner try/except catches it and continues.
        """
        entry = make_feed_entry("Article", "https://x.com/1", "body", hours_ago=1)
        # Override the published_parsed that make_feed_entry set.
        entry.published_parsed = None
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   return_value=make_parsed_feed([entry])):
            articles = await _fetch_one_feed(FEED, hours_cutoff=24)

        assert articles == []

    async def test_skips_entry_with_no_content(self):
        """
        An entry with an empty content list should be silently skipped.
        entry.content[0] raises IndexError; the inner try/except catches it.
        This is common: some feeds include only a title and link, no content body.
        """
        entry = make_feed_entry("Article", "https://x.com/1", "body", hours_ago=1)
        entry.content = []  # Empty list → content[0] raises IndexError
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   return_value=make_parsed_feed([entry])):
            articles = await _fetch_one_feed(FEED, hours_cutoff=24)

        assert articles == []

    async def test_html_content_converted(self):
        """
        Articles with content_type="text/html" should have HTML stripped and
        converted to Markdown by html_to_markdown.convert().

        We verify that <p> tags are gone but the text content remains.
        """
        entry = make_feed_entry(
            "HTML Article", "https://x.com/html",
            "<p>Hello <strong>world</strong></p>",
            hours_ago=1,
            content_type="text/html",  # triggers HTML→Markdown conversion
        )
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   return_value=make_parsed_feed([entry])):
            articles = await _fetch_one_feed(FEED, hours_cutoff=24)

        assert len(articles) == 1
        assert "<p>" not in articles[0].content   # HTML tags must be gone
        assert "Hello" in articles[0].content      # Text content must remain


class TestFetchTopicArticles:
    """Tests for fetch_topic_articles() — the public multi-feed entry point."""

    async def test_deduplicates_by_url(self, sample_topic_config):
        """
        If the same URL appears in multiple feeds (syndication is common),
        fetch_topic_articles should return it only once.

        sample_topic_config has 2 feeds. The mock returns the same entry for
        both. After deduplication, only 1 article should appear.
        """
        entry = make_feed_entry("Dup", "https://x.com/dup", "body", hours_ago=1)
        parsed = make_parsed_feed([entry])

        with patch("strategic_reports.daily.core.ingestion.feedparser.parse", return_value=parsed):
            articles = await fetch_topic_articles(sample_topic_config, hours_cutoff=24)

        urls = [a.link for a in articles]
        # Assert no duplicates using set comparison.
        assert len(urls) == len(set(urls)), "Duplicate URLs found"
        assert len(articles) == 1

    async def test_sorted_newest_first(self, sample_topic_config):
        """
        Articles from multiple feeds should be sorted newest-first in the output.

        We use a stateful side_effect function to return different mock feeds
        on the first vs. second call to feedparser.parse.
        """
        entries_feed1 = [
            make_feed_entry("Old", "https://x.com/old", "body", hours_ago=5),
        ]
        entries_feed2 = [
            make_feed_entry("New", "https://x.com/new", "body", hours_ago=1),
        ]

        # side_effect as a function is called instead of using return_value.
        # nonlocal lets the nested function read and write call_count from
        # the enclosing scope (Python 3 closure pattern).
        call_count = 0
        def side_effect(url):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_parsed_feed(entries_feed1)
            return make_parsed_feed(entries_feed2)

        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   side_effect=side_effect):
            articles = await fetch_topic_articles(sample_topic_config, hours_cutoff=24)

        # Newest article (1 hour ago) should be first.
        assert articles[0].title == "New"
        assert articles[1].title == "Old"

    async def test_returns_empty_when_all_feeds_fail(self, sample_topic_config):
        """
        If every feed raises an exception, fetch_topic_articles should return []
        rather than raising. The per-feed error handling swallows feed failures.
        """
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   side_effect=Exception("down")):
            articles = await fetch_topic_articles(sample_topic_config, hours_cutoff=24)

        assert articles == []

    async def test_loads_feeds_from_json(self, tmp_path):
        """
        fetch_topic_articles reads feed URLs from the feeds JSON file on disk.
        This test verifies the full path from JSON file → FeedConfig → fetch.

        We write a minimal JSON file and verify the article from that feed's
        URL appears in the output — confirming the JSON loading works end-to-end.
        """
        feeds_file = tmp_path / "feeds_test.json"
        feeds_file.write_text(json.dumps({
            "feeds": [{"title": "F1", "url": "https://f1.com/feed"}]
        }))
        config = TopicConfig(slug="feeds_test", title="Test", feeds_file=feeds_file)

        entry = make_feed_entry("Article", "https://f1.com/1", "body", hours_ago=1)
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   return_value=make_parsed_feed([entry])):
            articles = await fetch_topic_articles(config, hours_cutoff=24)

        assert len(articles) == 1
