"""Tests for prompt builder functions."""

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
    return [
        RawArticle(
            title=f"Article {i}",
            content=f"Body text for article {i}.",
            link=f"https://example.com/{i}",
            publish_date=datetime.datetime(2026, 6, 27, 10 - i, 0),
        )
        for i in range(3)
    ]


@pytest.fixture
def summaries():
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
    def test_summarizer_non_empty(self):
        assert len(SYSTEM_SUMMARIZER.strip()) > 30

    def test_strategist_non_empty(self):
        assert len(SYSTEM_STRATEGIST.strip()) > 30

    def test_summarizer_mentions_tags(self):
        assert "tag" in SYSTEM_SUMMARIZER.lower()

    def test_strategist_mentions_strategy(self):
        assert "strateg" in SYSTEM_STRATEGIST.lower()


class TestBuildSummarizePrompt:
    def test_contains_all_article_titles(self, articles):
        prompt = build_summarize_prompt(articles)
        for a in articles:
            assert a.title in prompt

    def test_contains_article_delimiters(self, articles):
        prompt = build_summarize_prompt(articles)
        assert "ARTICLE 1" in prompt
        assert "ARTICLE 2" in prompt
        assert "ARTICLE 3" in prompt

    def test_contains_article_urls(self, articles):
        prompt = build_summarize_prompt(articles)
        for a in articles:
            assert a.link in prompt

    def test_contains_publish_dates(self, articles):
        prompt = build_summarize_prompt(articles)
        assert "2026-06-27" in prompt

    def test_contains_article_count(self, articles):
        prompt = build_summarize_prompt(articles)
        assert "3" in prompt

    def test_single_article(self):
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
        prompt = build_summarize_prompt([])
        assert "0" in prompt


class TestBuildStrategyPrompt:
    def test_contains_topic_title(self, summaries):
        prompt = build_strategy_prompt("Artificial Intelligence", summaries)
        assert "Artificial Intelligence" in prompt

    def test_contains_article_titles(self, summaries):
        prompt = build_strategy_prompt("AI", summaries)
        assert "AI Surges" in prompt

    def test_contains_summary_bullets(self, summaries):
        prompt = build_strategy_prompt("AI", summaries)
        assert "Models improve." in prompt

    def test_contains_tags(self, summaries):
        prompt = build_strategy_prompt("AI", summaries)
        assert "artificial intelligence" in prompt

    def test_contains_article_count(self, summaries):
        prompt = build_strategy_prompt("AI", summaries)
        assert "1" in prompt
