"""
Tests for prompt builder functions (prompts.py).

WHY TEST PROMPTS?
-----------------
Prompt builders are pure functions: given typed input, they return a string.
Testing them without calling the LLM verifies that:
  1. Article titles, URLs, and content actually make it into the prompt
  2. The delimiter structure (--- ARTICLE N ---) is present
  3. The article count is included (helps the model know how many to process)
  4. Tags and bullets from summaries appear in the strategy prompt

This is valuable because a broken prompt is silent — if build_summarize_prompt()
omitted article titles, the LLM would still return something, but the summaries
would be generic rather than article-specific. A unit test catches this breakage
before you spend tokens on a real LLM call.

PATTERN: LOCAL FIXTURES IN TEST FILES
--------------------------------------
These fixtures (articles, summaries) are defined at the top of this file, not in
conftest.py. Use conftest.py for fixtures shared across multiple test files; use
local fixtures for data that's specific to one test file's concerns.

Local fixtures are function-scoped by default: a fresh set of articles and
summaries is created for each test method that uses them.

CLASS-BASED TEST ORGANIZATION
-------------------------------
TestSystemMessages  — tests the content of system message constants
TestBuildSummarizePrompt — tests the user-turn builder for summarization
TestBuildStrategyPrompt  — tests the user-turn builder for strategy synthesis
"""

import datetime

import pytest

from strategic_reports.daily.core.models import ArticleSummary, RawArticle
from strategic_reports.daily.core.prompts import (
    SYSTEM_STRATEGIST,
    SYSTEM_SUMMARIZER,
    build_strategy_prompt,
    build_summarize_prompt,
)


@pytest.fixture
def articles():
    """Three RawArticles with different publish times for prompt content testing."""
    return [
        RawArticle(
            title=f"Article {i}",
            content=f"Body text for article {i}.",
            link=f"https://example.com/{i}",
            # Publish times: 10:00, 09:00, 08:00 — articles 0 is newest
            publish_date=datetime.datetime(2026, 6, 27, 10 - i, 0),
        )
        for i in range(3)
    ]


@pytest.fixture
def summaries():
    """One ArticleSummary for testing the strategy prompt builder."""
    return [
        ArticleSummary(
            title="AI Surges",
            link="https://example.com/ai",
            publish_date="2026-06-27T10:00:00",
            summary=["Models improve.", "Costs drop.", "Adoption rises."],
            tags=["artificial intelligence", "machine learning", "technology",
                  "innovation", "research"],
        )
    ]


class TestSystemMessages:
    """
    Smoke tests for the system message constants.

    We don't test the exact wording (that would make tests fragile to prompt
    tuning), but we do verify they're non-empty and contain key concept words.
    The primary risk these guard against is accidentally shipping an empty or
    accidentally truncated system message string.
    """

    def test_summarizer_non_empty(self):
        """System message must be a meaningful string, not empty or whitespace."""
        assert len(SYSTEM_SUMMARIZER.strip()) > 30

    def test_strategist_non_empty(self):
        assert len(SYSTEM_STRATEGIST.strip()) > 30

    def test_summarizer_mentions_tags(self):
        """The summarizer system prompt should reference tagging."""
        assert "tag" in SYSTEM_SUMMARIZER.lower()

    def test_strategist_mentions_strategy(self):
        """The strategist system prompt should reference strategy/strategic."""
        assert "strateg" in SYSTEM_STRATEGIST.lower()


class TestBuildSummarizePrompt:
    """
    Tests for build_summarize_prompt(articles) → str.

    Key properties to verify:
      - Each article's title and URL appear in the prompt (model needs them)
      - Delimiter structure is present (helps model find article boundaries)
      - Article count is mentioned (guides the model to process all of them)
      - Publish dates appear (provides temporal context)
    """

    def test_contains_all_article_titles(self, articles):
        """
        Every article title must appear in the prompt.
        If a title is missing, the LLM can't echo it back in the ArticleSummary.
        """
        prompt = build_summarize_prompt(articles)
        for a in articles:
            assert a.title in prompt

    def test_contains_article_delimiters(self, articles):
        """
        The --- ARTICLE N --- delimiters must be present so the model knows
        where one article ends and the next begins in a multi-article batch.
        """
        prompt = build_summarize_prompt(articles)
        assert "ARTICLE 1" in prompt
        assert "ARTICLE 2" in prompt
        assert "ARTICLE 3" in prompt

    def test_contains_article_urls(self, articles):
        """
        URLs must appear so the model can echo them back as the 'link' field
        in ArticleSummary (we don't want the model to invent URLs).
        """
        prompt = build_summarize_prompt(articles)
        for a in articles:
            assert a.link in prompt

    def test_contains_publish_dates(self, articles):
        """
        Dates must appear in the prompt. isoformat() should produce "2026-06-27..."
        We test for the date portion (not the full timestamp) since that's stable.
        """
        prompt = build_summarize_prompt(articles)
        assert "2026-06-27" in prompt

    def test_contains_article_count(self, articles):
        """
        The article count ("3") must appear so the model knows how many to process.
        Without it, models sometimes stop after summarizing 2-3 articles in a large batch.
        """
        prompt = build_summarize_prompt(articles)
        assert "3" in prompt

    def test_single_article(self):
        """Edge case: single-article batch works correctly."""
        article = RawArticle(
            title="Solo Article",
            content="Just one.",
            link="https://x.com/1",
            publish_date=datetime.datetime(2026, 6, 27),
        )
        prompt = build_summarize_prompt([article])
        assert "ARTICLE 1" in prompt
        assert "Solo Article" in prompt

    def test_empty_list(self):
        """Edge case: empty list produces a prompt with "0" in it (not a crash)."""
        prompt = build_summarize_prompt([])
        assert "0" in prompt


class TestBuildStrategyPrompt:
    """
    Tests for build_strategy_prompt(topic_title, summaries) → str.

    The strategy prompt packages article summaries (not raw articles) for
    the synthesis LLM call. Key data that must be present:
      - Topic title (guides the model's strategic framing)
      - Article titles (source material)
      - Summary bullets (the actual content to synthesize)
      - Tags (helps the model identify themes across articles)
      - Article count (guides completeness)
    """

    def test_contains_topic_title(self, summaries):
        """
        The topic title must appear so the model frames insights in the right domain.
        "AI" insights differ from "Biotech" insights even for the same articles.
        """
        prompt = build_strategy_prompt("Artificial Intelligence", summaries)
        assert "Artificial Intelligence" in prompt

    def test_contains_article_titles(self, summaries):
        """Article titles from the summaries must appear as section headers."""
        prompt = build_strategy_prompt("AI", summaries)
        assert "AI Surges" in prompt

    def test_contains_summary_bullets(self, summaries):
        """
        The bullet points from each ArticleSummary must appear in the strategy prompt.
        These ARE the content the model uses to synthesize insights.
        """
        prompt = build_strategy_prompt("AI", summaries)
        assert "Models improve." in prompt

    def test_contains_tags(self, summaries):
        """
        Tags must appear so the model can identify thematic clusters.
        A strategy about "machine learning" + "entrepreneurship" differs from
        one about "machine learning" + "regulation".
        """
        prompt = build_strategy_prompt("AI", summaries)
        assert "artificial intelligence" in prompt

    def test_contains_article_count(self, summaries):
        """The count of summaries must appear in the prompt."""
        prompt = build_strategy_prompt("AI", summaries)
        assert "1" in prompt
