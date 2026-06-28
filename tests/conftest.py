"""Shared fixtures for the strategic-reports test suite."""

import datetime
import json
from pathlib import Path
from time import mktime
from unittest.mock import MagicMock

import pytest

from strategic_reports.daily.core.models import (
    ArticleSummary,
    RawArticle,
    StrategicInsight,
    TokenUsage,
    TopicConfig,
    TopicResult,
)


# ---------------------------------------------------------------------------
# Data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_raw_articles() -> list[RawArticle]:
    return [
        RawArticle(
            title=f"Article {i}",
            content=f"Content body for article {i}.",
            link=f"https://example.com/article-{i}",
            publish_date=datetime.datetime(2026, 6, 27, 12 - i, 0),
        )
        for i in range(3)
    ]


@pytest.fixture
def sample_article_summary() -> ArticleSummary:
    return ArticleSummary(
        title="LLMs Keep Improving",
        link="https://example.com/llms",
        publish_date="2026-06-27T09:00:00",
        summary=["Models are faster.", "Costs are dropping.", "New benchmarks set."],
        tags=["artificial intelligence", "large language models", "benchmarks",
              "technology", "research"],
    )


@pytest.fixture
def sample_strategy() -> StrategicInsight:
    return StrategicInsight(bullets=[
        "Invest in LLM tooling now while costs drop.",
        "Target applied-AI roles over pure research positions.",
        "Build a portfolio of small, deployed LLM projects.",
    ])


@pytest.fixture
def sample_topic_config(tmp_path: Path) -> TopicConfig:
    feeds_file = tmp_path / "feeds_ai.json"
    feeds_file.write_text(json.dumps({
        "feeds": [
            {"title": "AI News", "url": "https://example.com/feed1"},
            {"title": "AI Trends", "url": "https://example.com/feed2"},
        ]
    }))
    return TopicConfig(slug="feeds_ai", title="Artificial Intelligence", feeds_file=feeds_file)


@pytest.fixture
def sample_topic_result(
    sample_topic_config: TopicConfig,
    sample_article_summary: ArticleSummary,
    sample_strategy: StrategicInsight,
) -> TopicResult:
    return TopicResult(
        config=sample_topic_config,
        articles=[sample_article_summary],
        strategy=sample_strategy,
        token_usage=TokenUsage(prompt_tokens=800, completion_tokens=200, total_tokens=1000),
    )


# ---------------------------------------------------------------------------
# Feedparser mock helpers
# ---------------------------------------------------------------------------

def make_feed_entry(
    title: str,
    url: str,
    content: str,
    hours_ago: float = 1.0,
    content_type: str = "text/plain",
) -> MagicMock:
    """Return a MagicMock that mimics a feedparser entry."""
    dt = datetime.datetime.now() - datetime.timedelta(hours=hours_ago)
    entry = MagicMock()
    entry.title = title
    entry.link = url
    entry.summary = f"Summary of {title}"
    entry.published_parsed = dt.timetuple()
    entry.content = [{"value": content, "type": content_type}]
    return entry


def make_parsed_feed(entries: list) -> MagicMock:
    """Return a MagicMock that mimics a feedparser parsed feed."""
    feed = MagicMock()
    feed.entries = entries
    return feed
