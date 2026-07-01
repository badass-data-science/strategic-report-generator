"""
Shared fixtures for the strategic-reports test suite.

WHAT IS conftest.py?
--------------------
pytest automatically discovers and loads conftest.py files. Any fixture defined
here is available to every test file in the same directory and all subdirectories,
without needing to import it. This is how pytest shares setup code across test files.

WHAT IS A PYTEST FIXTURE?
--------------------------
A fixture is a function decorated with @pytest.fixture that provides setup data
or objects to test functions. When a test function has a parameter with the same
name as a fixture, pytest automatically calls the fixture and injects its return
value:

    @pytest.fixture
    def sample_topic_config(tmp_path):
        ...
        return TopicConfig(...)

    def test_something(sample_topic_config):   # pytest injects the TopicConfig
        assert sample_topic_config.slug == "feeds_ai"

Fixtures can depend on other fixtures (e.g. sample_topic_result depends on
sample_topic_config, sample_article_summary, and sample_strategy). pytest
resolves the dependency graph automatically.

FIXTURE SCOPING
---------------
By default, fixtures are function-scoped: a new instance is created for every
test function that uses them. This keeps tests isolated — one test can mutate
the fixture's state without affecting another test.

tmp_path is a pytest built-in fixture that provides a fresh temporary directory
(as a pathlib.Path) for each test. It's cleaned up automatically after the test.
We use it for sample_topic_config so the feeds JSON file is written to an
isolated directory for each test, not a shared location.

MOCK HELPERS (make_feed_entry / make_parsed_feed)
--------------------------------------------------
These are not fixtures — they're plain functions importable from conftest.
Tests use them to build fake feedparser output without touching the network.

feedparser returns dict-like objects with many fields. MagicMock lets us
create a lightweight fake that only defines the fields our code actually reads:
  entry.title, entry.link, entry.published_parsed, entry.content, entry.summary

MagicMock auto-creates any attribute you access, but we set the specific ones
explicitly to control what the code under test sees.

IMPORTANT: published_parsed must be struct_time
feedparser uses struct_time (from C's time.h), not datetime.datetime.
datetime.timetuple() converts a datetime to struct_time.
If you pass a datetime directly, mktime(datetime) raises TypeError.
"""

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
# Data fixtures — provide pre-built model instances
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_raw_articles() -> list[RawArticle]:
    """
    Three RawArticle instances with different publish times.
    Useful for testing sorting (newest-first) and cutoff filtering.
    """
    return [
        RawArticle(
            title=f"Article {i}",
            content=f"Content body for article {i}.",
            link=f"https://example.com/article-{i}",
            # 12:00, 11:00, 10:00 — article 0 is newest
            publish_date=datetime.datetime(2026, 6, 27, 12 - i, 0),
        )
        for i in range(3)
    ]


@pytest.fixture
def sample_article_summary() -> ArticleSummary:
    """
    A valid ArticleSummary with exactly 3 summary bullets and 5 tags.
    Satisfies the Pydantic constraints in models.py (min_length=3, max_length=3
    for summary; min_length=5, max_length=20 for tags).
    """
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
    """
    A valid StrategicInsight with 3 bullets (minimum allowed by Pydantic).
    """
    return StrategicInsight(
        bullets=[
            "Invest in LLM tooling now while costs drop.",
            "Target applied-AI roles over pure research positions.",
            "Build a portfolio of small, deployed LLM projects.",
        ],
        urgency_score=0.3,
    )


@pytest.fixture
def sample_topic_config(tmp_path: Path) -> TopicConfig:
    """
    A TopicConfig that points to a real temporary feeds JSON file.

    tmp_path is a pytest built-in fixture: a unique temporary directory (Path)
    created fresh for each test that uses this fixture. It's automatically
    removed after each test.

    We write a real JSON file rather than mocking Path.read_text() because:
      1. It's simpler — no patching needed
      2. It tests the actual JSON parsing code path
      3. tmp_path isolation prevents tests from sharing state
    """
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
    """
    A successful TopicResult (all three stages completed).

    This fixture depends on three other fixtures — pytest injects all of them
    before creating this one. The dependency graph is resolved automatically.
    """
    return TopicResult(
        config=sample_topic_config,
        articles=[sample_article_summary],
        strategy=sample_strategy,
        token_usage=TokenUsage(prompt_tokens=800, completion_tokens=200, total_tokens=1000),
    )


# ---------------------------------------------------------------------------
# Feedparser mock helpers — not fixtures; imported by test files
# ---------------------------------------------------------------------------

def make_feed_entry(
    title: str,
    url: str,
    content: str,
    hours_ago: float = 1.0,
    content_type: str = "text/plain",
) -> MagicMock:
    """
    Return a MagicMock that mimics a feedparser entry dict-like object.

    feedparser entries have many fields. We only set the ones that
    ingestion.py actually reads, which keeps the mock minimal.

    CRITICAL: published_parsed is struct_time, not datetime
    -------------------------------------------------------
    feedparser returns times as struct_time (Python's wrapper around C's
    time.h struct tm). Our ingestion code calls:
        datetime.fromtimestamp(mktime(entry.published_parsed))

    If published_parsed is a datetime instead of struct_time, mktime() raises:
        TypeError: argument must be 9-item sequence, not datetime.datetime

    datetime.timetuple() returns a struct_time from a datetime — this is the
    correct bridge. We compute the datetime first (now - timedelta(hours=hours_ago))
    then call .timetuple() on it.

    Args:
        hours_ago: How many hours in the past the article was published.
                   hours_ago=0.5 → 30 minutes ago (within a 24h cutoff)
                   hours_ago=48  → 2 days ago (outside a 24h cutoff)
        content_type: "text/html" triggers HTML→Markdown conversion in ingestion.
                      "text/plain" skips conversion.
    """
    dt = datetime.datetime.now() - datetime.timedelta(hours=hours_ago)
    entry = MagicMock()
    entry.title = title
    entry.link = url
    entry.summary = f"Summary of {title}"
    # .timetuple() converts datetime → struct_time, matching feedparser's real output.
    entry.published_parsed = dt.timetuple()
    # content is a list of dicts; ingestion reads content[0]["value"] and content[0]["type"].
    entry.content = [{"value": content, "type": content_type}]
    return entry


def make_parsed_feed(entries: list) -> MagicMock:
    """
    Return a MagicMock that mimics a feedparser parsed feed result.

    feedparser.parse() returns an object with a .entries attribute
    (list of entry objects). This mock provides that minimal interface.
    """
    feed = MagicMock()
    feed.entries = entries
    return feed
