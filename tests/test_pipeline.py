"""
Tests for the async pipeline orchestrator.

LLMClient.complete_structured is mocked via AsyncMock so no real API calls
are made. fetch_topic_articles is patched at the pipeline module level.
"""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strategic_reports.daily.core.llm_client import LLMClient
from strategic_reports.daily.core.models import (
    ArticleSummary,
    ArticleSummaryBatch,
    RawArticle,
    StrategicInsight,
    TokenUsage,
    TopicConfig,
    TopicResult,
)
from strategic_reports.daily.core.pipeline import run_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mock_client(
    summary_batch: ArticleSummaryBatch | None = None,
    strategy: StrategicInsight | None = None,
    summary_usage: TokenUsage | None = None,
    strategy_usage: TokenUsage | None = None,
    raises: Exception | None = None,
) -> MagicMock:
    """Return a MagicMock LLMClient whose complete_structured is an AsyncMock."""
    summary_batch = summary_batch or ArticleSummaryBatch(articles=[
        ArticleSummary(
            title="Mock Article",
            link="https://mock.example.com",
            publish_date="2026-06-27T09:00:00",
            summary=["Point A.", "Point B.", "Point C."],
            tags=["mock", "test", "article", "fake", "data"],
        )
    ])
    strategy = strategy or StrategicInsight(bullets=[
        "Mock insight one.", "Mock insight two.", "Mock insight three."
    ])
    summary_usage = summary_usage or TokenUsage(total_tokens=100)
    strategy_usage = strategy_usage or TokenUsage(total_tokens=50)

    async def _complete_structured(prompt, response_model, system=None):
        if raises:
            raise raises
        if response_model is ArticleSummaryBatch:
            return summary_batch, summary_usage
        if response_model is StrategicInsight:
            return strategy, strategy_usage
        raise ValueError(f"Unexpected response_model: {response_model}")

    client = MagicMock(spec=LLMClient)
    client.complete_structured = _complete_structured
    client.total_usage = TokenUsage()
    return client


def make_articles(n: int = 2) -> list[RawArticle]:
    return [
        RawArticle(
            title=f"Article {i}",
            content=f"Content {i}",
            link=f"https://example.com/{i}",
            publish_date=datetime.datetime(2026, 6, 27, 12 - i, 0),
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunPipeline:
    async def test_returns_one_result_per_topic(self, sample_topic_config):
        client = make_mock_client()
        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   return_value=make_articles()):
            results = await run_pipeline([sample_topic_config], client)

        assert len(results) == 1

    async def test_successful_result_has_articles_and_strategy(self, sample_topic_config):
        client = make_mock_client()
        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   return_value=make_articles()):
            results = await run_pipeline([sample_topic_config], client)

        r = results[0]
        assert r.error is None
        assert len(r.articles) == 1
        assert r.strategy is not None
        assert len(r.strategy.bullets) == 3

    async def test_empty_topic_returns_result_without_error(self, sample_topic_config):
        client = make_mock_client()
        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   return_value=[]):
            results = await run_pipeline([sample_topic_config], client)

        r = results[0]
        assert r.error is None
        assert r.articles == []
        assert r.strategy is None

    async def test_ingestion_exception_captured_as_error(self, sample_topic_config):
        client = make_mock_client()
        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   side_effect=Exception("RSS server down")):
            results = await run_pipeline([sample_topic_config], client)

        r = results[0]
        assert r.error is not None
        assert "RSS server down" in r.error

    async def test_llm_exception_captured_as_error(self, sample_topic_config):
        client = make_mock_client(raises=RuntimeError("API limit hit"))
        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   return_value=make_articles()):
            results = await run_pipeline([sample_topic_config], client)

        r = results[0]
        assert r.error is not None
        assert "API limit hit" in r.error

    async def test_one_failure_does_not_cancel_others(self, tmp_path):
        good = TopicConfig(slug="feeds_ai", title="AI", feeds_file=tmp_path / "ai.json")
        bad = TopicConfig(slug="feeds_bad", title="Bad", feeds_file=tmp_path / "bad.json")

        client = make_mock_client()

        async def fetch_side_effect(topic, hours_cutoff):
            if topic.slug == "feeds_bad":
                raise Exception("bad feed")
            return make_articles()

        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   side_effect=fetch_side_effect):
            results = await run_pipeline([good, bad], client)

        assert len(results) == 2
        good_r = next(r for r in results if r.config.slug == "feeds_ai")
        bad_r = next(r for r in results if r.config.slug == "feeds_bad")
        assert good_r.error is None
        assert good_r.strategy is not None
        assert bad_r.error is not None

    async def test_token_usage_accumulated_per_topic(self, sample_topic_config):
        s_usage = TokenUsage(total_tokens=200)
        y_usage = TokenUsage(total_tokens=75)
        client = make_mock_client(summary_usage=s_usage, strategy_usage=y_usage)

        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   return_value=make_articles()):
            results = await run_pipeline([sample_topic_config], client)

        assert results[0].token_usage.total_tokens == 275

    async def test_multiple_topics_all_processed(self, tmp_path):
        configs = [
            TopicConfig(
                slug=f"feeds_topic{i}",
                title=f"Topic {i}",
                feeds_file=tmp_path / f"feeds_topic{i}.json",
            )
            for i in range(4)
        ]
        client = make_mock_client()
        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   return_value=make_articles()):
            results = await run_pipeline(configs, client, max_concurrent_llm_calls=2)

        assert len(results) == 4
        assert all(r.error is None for r in results)
        assert all(r.strategy is not None for r in results)

    async def test_batch_size_respected(self, sample_topic_config):
        """With batch_size=1, each article gets its own LLM call."""
        call_count = 0

        async def counting_complete(prompt, response_model, system=None):
            nonlocal call_count
            call_count += 1
            if response_model is ArticleSummaryBatch:
                return ArticleSummaryBatch(articles=[
                    ArticleSummary(
                        title="X", link="https://x.com",
                        publish_date="2026-06-27",
                        summary=["A.", "B.", "C."],
                        tags=["a", "b", "c", "d", "e"],
                    )
                ]), TokenUsage()
            return StrategicInsight(bullets=["A.", "B.", "C."]), TokenUsage()

        client = MagicMock(spec=LLMClient)
        client.complete_structured = counting_complete

        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   return_value=make_articles(n=3)):
            await run_pipeline([sample_topic_config], client, batch_size=1)

        # 3 articles with batch_size=1 → 3 summarize calls + 1 strategy call
        assert call_count == 4
