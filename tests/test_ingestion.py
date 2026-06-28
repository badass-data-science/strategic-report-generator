"""Tests for RSS ingestion — feedparser calls are mocked."""

import datetime
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from strategic_reports.daily.core.ingestion import _fetch_one_feed, fetch_topic_articles
from strategic_reports.daily.core.models import FeedConfig, TopicConfig
from tests.conftest import make_feed_entry, make_parsed_feed


FEED = FeedConfig(title="Test Feed", url="https://example.com/feed")


class TestFetchOneFeed:
    async def test_returns_articles_within_cutoff(self):
        entries = [
            make_feed_entry("Recent Article", "https://x.com/1", "Content 1", hours_ago=0.5),
        ]
        parsed = make_parsed_feed(entries)

        with patch("strategic_reports.daily.core.ingestion.feedparser.parse", return_value=parsed):
            articles = await _fetch_one_feed(FEED, hours_cutoff=24)

        assert len(articles) == 1
        assert articles[0].title == "Recent Article"
        assert articles[0].link == "https://x.com/1"

    async def test_excludes_articles_beyond_cutoff(self):
        entries = [
            make_feed_entry("Old Article", "https://x.com/old", "Old content", hours_ago=48),
        ]
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   return_value=make_parsed_feed(entries)):
            articles = await _fetch_one_feed(FEED, hours_cutoff=24)

        assert articles == []

    async def test_mixed_age_articles(self):
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
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   side_effect=Exception("network error")):
            articles = await _fetch_one_feed(FEED, hours_cutoff=24)

        assert articles == []

    async def test_skips_entry_with_no_published_parsed(self):
        entry = make_feed_entry("Article", "https://x.com/1", "body", hours_ago=1)
        entry.published_parsed = None
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   return_value=make_parsed_feed([entry])):
            articles = await _fetch_one_feed(FEED, hours_cutoff=24)

        assert articles == []

    async def test_skips_entry_with_no_content(self):
        entry = make_feed_entry("Article", "https://x.com/1", "body", hours_ago=1)
        entry.content = []
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   return_value=make_parsed_feed([entry])):
            articles = await _fetch_one_feed(FEED, hours_cutoff=24)

        assert articles == []

    async def test_html_content_converted(self):
        entry = make_feed_entry(
            "HTML Article", "https://x.com/html",
            "<p>Hello <strong>world</strong></p>",
            hours_ago=1,
            content_type="text/html",
        )
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   return_value=make_parsed_feed([entry])):
            articles = await _fetch_one_feed(FEED, hours_cutoff=24)

        assert len(articles) == 1
        assert "<p>" not in articles[0].content
        assert "Hello" in articles[0].content


class TestFetchTopicArticles:
    async def test_deduplicates_by_url(self, sample_topic_config):
        entry = make_feed_entry("Dup", "https://x.com/dup", "body", hours_ago=1)
        parsed = make_parsed_feed([entry])

        with patch("strategic_reports.daily.core.ingestion.feedparser.parse", return_value=parsed):
            articles = await fetch_topic_articles(sample_topic_config, hours_cutoff=24)

        urls = [a.link for a in articles]
        assert len(urls) == len(set(urls)), "Duplicate URLs found"
        assert len(articles) == 1

    async def test_sorted_newest_first(self, sample_topic_config):
        entries_feed1 = [
            make_feed_entry("Old", "https://x.com/old", "body", hours_ago=5),
        ]
        entries_feed2 = [
            make_feed_entry("New", "https://x.com/new", "body", hours_ago=1),
        ]
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

        assert articles[0].title == "New"
        assert articles[1].title == "Old"

    async def test_returns_empty_when_all_feeds_fail(self, sample_topic_config):
        with patch("strategic_reports.daily.core.ingestion.feedparser.parse",
                   side_effect=Exception("down")):
            articles = await fetch_topic_articles(sample_topic_config, hours_cutoff=24)

        assert articles == []

    async def test_loads_feeds_from_json(self, tmp_path):
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
