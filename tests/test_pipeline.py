"""
Tests for the async pipeline orchestrator (pipeline.py).

MOCKING STRATEGY
-----------------
The pipeline has two external dependencies:
  1. fetch_topic_articles  — patched at the pipeline module level
  2. LLMClient.complete_structured — replaced with a typed async function

For (1), we use patch("strategic_reports.daily.core.pipeline.fetch_topic_articles").
This patches the name as imported in pipeline.py — the correct location.

For (2), we build a real async function (not AsyncMock) that dispatches on
response_model. This is the most important technique in this test file:

    async def _complete_structured(
        prompt: str, response_model: type[Any], system: str | None = None
    ) -> tuple[Any, TokenUsage]:
        if response_model is ArticleSummaryBatch:
            return summary_batch, summary_usage
        if response_model is StrategicInsight:
            return strategy, strategy_usage

We then assign it to a MagicMock(spec=LLMClient):
    client.complete_structured = _complete_structured

WHY MagicMock(spec=LLMClient) INSTEAD OF AsyncMock?
-----------------------------------------------------
AsyncMock would work for a simple always-return-the-same-thing scenario,
but our pipeline calls complete_structured with different response_model types
in the same test. We need dispatch logic, which requires a real async function.

MagicMock(spec=LLMClient) creates a mock that:
  - Only allows attributes that exist on LLMClient (protects against typos)
  - Does NOT auto-create return values for missing attributes
  - Still lets us set client.complete_structured = our_async_function

WHY NOT CALL THE REAL LLMClient?
----------------------------------
The real LLMClient would make HTTP calls to an LLM API. We never want unit
tests to make network calls — they'd be slow, non-deterministic, require API
credentials, and cost money.

ERROR ISOLATION TESTS
----------------------
These are the most important pipeline tests. We verify that:
  - A failed ingestion for topic B doesn't prevent topic A from succeeding
  - An LLM error for one topic doesn't cancel other topics

This mirrors the key guarantee in pipeline.py: return_exceptions=True in
Phase 1 gather + try/except in _process_topic.

BATCH SIZE TEST
---------------
test_batch_size_respected uses a counting closure to verify that 3 articles
with batch_size=1 produce 3 summarize calls (one per article) + 1 strategy call.
This tests the _chunk() function's effect on the number of LLM calls.
"""

import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from strategic_reports.daily.core.db import record_run
from strategic_reports.daily.core.llm_client import LLMClient
from strategic_reports.daily.core.models import (
    ArchiveAnswer,
    ArticleSummary,
    ArticleSummaryBatch,
    CommunitySummary,
    QueryTags,
    RawArticle,
    StrategicInsight,
    TokenUsage,
    TopicConfig,
    TopicResult,
)
from strategic_reports.daily.core.pipeline import (
    answer_archive_question,
    extract_query_tags,
    run_pipeline,
    summarize_communities,
)
from strategic_reports.daily.core.tag_tracking import record_community_summaries

# ---------------------------------------------------------------------------
# Helpers — not fixtures; used inline in test methods
# ---------------------------------------------------------------------------

def make_mock_client(
    summary_batch: ArticleSummaryBatch | None = None,
    strategy: StrategicInsight | None = None,
    summary_usage: TokenUsage | None = None,
    strategy_usage: TokenUsage | None = None,
    raises: Exception | None = None,
) -> MagicMock:
    """
    Build a mock LLMClient whose complete_structured dispatches on response_model.

    This helper encapsulates all the mock setup boilerplate so each test
    method can call make_mock_client() with only the parameters it cares about.
    Default values are provided so tests only need to override what's relevant.

    The raises parameter lets a test simulate an LLM failure:
        client = make_mock_client(raises=RuntimeError("API limit hit"))

    The inner function _complete_structured is an actual async def, not AsyncMock.
    We need real async dispatch logic (if response_model is X: return this,
    if response_model is Y: return that). AsyncMock can only return one value.
    """
    # Build sensible defaults so tests don't need to construct valid objects
    # for cases they don't care about.
    summary_batch = summary_batch or ArticleSummaryBatch(articles=[
        ArticleSummary(
            title="Mock Article",
            link="https://mock.example.com",
            publish_date="2026-06-27T09:00:00",
            summary=["Point A.", "Point B.", "Point C."],
            tags=["mock", "test", "article", "fake", "data"],
        )
    ])
    strategy = strategy or StrategicInsight(
        bullets=["Mock insight one.", "Mock insight two.", "Mock insight three."],
        urgency_score=0.3,
    )
    summary_usage = summary_usage or TokenUsage(total_tokens=100)
    strategy_usage = strategy_usage or TokenUsage(total_tokens=50)

    async def _complete_structured(
        prompt: str, response_model: type[Any], system: str | None = None
    ) -> tuple[Any, TokenUsage]:
        # If 'raises' was set, simulate an LLM error.
        if raises:
            raise raises
        # Dispatch on the Pydantic class that the pipeline is requesting.
        # 'is' checks object identity, not equality — correct for class comparison.
        if response_model is ArticleSummaryBatch:
            return summary_batch, summary_usage
        if response_model is StrategicInsight:
            return strategy, strategy_usage
        raise ValueError(f"Unexpected response_model: {response_model}")

    # spec=LLMClient makes the mock reject attribute access that doesn't exist
    # on the real LLMClient class. This catches typos like client.complet_structured.
    client = MagicMock(spec=LLMClient)
    # Assign the async function directly — this replaces the method on the mock.
    client.complete_structured = _complete_structured
    # total_usage is a property on the real client; we provide a default here.
    client.total_usage = TokenUsage()
    return client


def make_articles(n: int = 2) -> list[RawArticle]:
    """Build n RawArticles for use as mock ingestion output."""
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
    """Tests for run_pipeline() — the public pipeline entry point."""

    async def test_returns_one_result_per_topic(self, sample_topic_config: TopicConfig) -> None:
        """run_pipeline must return exactly one TopicResult per input topic."""
        client = make_mock_client()
        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   return_value=make_articles()):
            results = await run_pipeline([sample_topic_config], client)

        assert len(results) == 1

    async def test_successful_result_has_articles_and_strategy(
        self, sample_topic_config: TopicConfig
    ) -> None:
        """
        Happy path: articles are fetched and LLM calls succeed.
        The result should have non-empty articles, a strategy, and no error.
        """
        client = make_mock_client()
        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   return_value=make_articles()):
            results = await run_pipeline([sample_topic_config], client)

        r = results[0]
        assert r.error is None
        assert len(r.articles) == 1      # one article from mock ArticleSummaryBatch
        assert r.strategy is not None
        assert len(r.strategy.bullets) == 3

    async def test_empty_topic_returns_result_without_error(
        self, sample_topic_config: TopicConfig
    ) -> None:
        """
        Topic with no recent articles (empty ingestion) → TopicResult with no error
        and no strategy. This is the 'no news today' state.
        """
        client = make_mock_client()
        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   return_value=[]):   # empty list = no recent articles
            results = await run_pipeline([sample_topic_config], client)

        r = results[0]
        assert r.error is None
        assert r.articles == []
        assert r.strategy is None

    async def test_ingestion_exception_captured_as_error(
        self, sample_topic_config: TopicConfig
    ) -> None:
        """
        If fetch_topic_articles raises, run_pipeline should capture it as
        TopicResult(error=...) rather than letting it propagate to the caller.

        This tests the return_exceptions=True in Phase 1 asyncio.gather +
        the isinstance(articles, Exception) check in _process_topic.
        """
        client = make_mock_client()
        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   side_effect=Exception("RSS server down")):
            results = await run_pipeline([sample_topic_config], client)

        r = results[0]
        assert r.error is not None
        assert "RSS server down" in r.error

    async def test_llm_exception_captured_as_error(self, sample_topic_config: TopicConfig) -> None:
        """
        If the LLM client raises (rate limit, timeout, etc.), the exception
        should be captured as TopicResult(error=...) rather than propagating.

        This tests the try/except in _process_topic's async with sem: block.
        """
        client = make_mock_client(raises=RuntimeError("API limit hit"))
        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   return_value=make_articles()):
            results = await run_pipeline([sample_topic_config], client)

        r = results[0]
        assert r.error is not None
        assert "API limit hit" in r.error

    async def test_one_failure_does_not_cancel_others(self, tmp_path: Path) -> None:
        """
        THE MOST IMPORTANT PIPELINE TEST.

        When one topic's ingestion fails, the other topics must still complete.
        This verifies the error isolation design: return_exceptions=True in
        Phase 1 gather prevents one exception from cancelling sibling tasks.

        We use a side_effect function that raises for "bad" and succeeds for "good".
        """
        good = TopicConfig(slug="feeds_ai", title="AI", feeds_file=tmp_path / "ai.json")
        bad = TopicConfig(slug="feeds_bad", title="Bad", feeds_file=tmp_path / "bad.json")

        client = make_mock_client()

        # side_effect as an async function: coroutine that can both raise and return.
        async def fetch_side_effect(topic: TopicConfig, hours_cutoff: int) -> list[RawArticle]:
            if topic.slug == "feeds_bad":
                raise Exception("bad feed")
            return make_articles()

        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   side_effect=fetch_side_effect):
            results = await run_pipeline([good, bad], client)

        assert len(results) == 2

        # next() with a generator expression finds the first matching item.
        good_r = next(r for r in results if r.config.slug == "feeds_ai")
        bad_r = next(r for r in results if r.config.slug == "feeds_bad")

        # Good topic succeeded despite bad topic failing.
        assert good_r.error is None
        assert good_r.strategy is not None
        # Bad topic has error, but the result exists (it wasn't dropped).
        assert bad_r.error is not None

    async def test_token_usage_accumulated_per_topic(
        self, sample_topic_config: TopicConfig
    ) -> None:
        """
        Token usage from summarization and strategy calls must be summed into
        the TopicResult. This tests TokenUsage.__add__ in the pipeline context.
        """
        s_usage = TokenUsage(total_tokens=200)
        y_usage = TokenUsage(total_tokens=75)
        client = make_mock_client(summary_usage=s_usage, strategy_usage=y_usage)

        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   return_value=make_articles()):
            results = await run_pipeline([sample_topic_config], client)

        # 200 (summarize) + 75 (strategy) = 275
        assert results[0].token_usage.total_tokens == 275

    async def test_multiple_topics_all_processed(self, tmp_path: Path) -> None:
        """
        With 4 topics and max_concurrent_llm_calls=2, all 4 should complete.
        The semaphore limits concurrency but doesn't prevent completion.
        """
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
            # max_concurrent_llm_calls=2: at most 2 topics run LLM calls simultaneously
            results = await run_pipeline(configs, client, max_concurrent_llm_calls=2)

        assert len(results) == 4
        assert all(r.error is None for r in results)
        assert all(r.strategy is not None for r in results)

    async def test_batch_size_respected(self, sample_topic_config: TopicConfig) -> None:
        """
        With batch_size=1, each article gets its own LLM summarize call.
        3 articles → 3 summarize calls + 1 strategy call = 4 total calls.

        This tests the _chunk() function in pipeline.py: _chunk(3 articles, 1)
        produces 3 batches of 1 article each.

        We use a nonlocal counter in a closure to count calls.
        """
        call_count = 0

        # An async function that counts its own calls.
        async def counting_complete(
            prompt: str, response_model: type[Any], system: str | None = None
        ) -> tuple[Any, TokenUsage]:
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
            return StrategicInsight(bullets=["A.", "B.", "C."], urgency_score=0.3), TokenUsage()

        client = MagicMock(spec=LLMClient)
        client.complete_structured = counting_complete

        with patch("strategic_reports.daily.core.pipeline.fetch_topic_articles",
                   return_value=make_articles(n=3)):
            # batch_size=1 forces one LLM call per article for summarization
            await run_pipeline([sample_topic_config], client, batch_size=1)

        # 3 articles ÷ batch_size=1 = 3 summarize calls + 1 strategy call = 4
        assert call_count == 4


# ---------------------------------------------------------------------------
# Tests for summarize_communities()
# ---------------------------------------------------------------------------
# Uses hand-built display_data (the shape build_display_graph() produces)
# and TopicResults, rather than routing through the full graph pipeline, to
# keep these tests focused on summarize_communities' own orchestration:
# grouping, concurrency, and per-community failure isolation.

def _article(title: str, link: str, tags: list[str]) -> ArticleSummary:
    return ArticleSummary(
        title=title, link=link, publish_date="2026-07-31T09:00:00",
        summary=["Bullet one.", "Bullet two.", "Bullet three."], tags=tags,
    )


def _display_node(tag: str, count: int, community: int, community_label: str) -> dict[str, Any]:
    return {
        "id": tag, "count": count, "topics": [],
        "community": community, "community_label": community_label,
    }


def make_community_mock_client(fail_if_prompt_contains: str | None = None) -> MagicMock:
    """A mock client that answers CommunitySummary requests, optionally
    failing when the prompt contains a given substring (e.g. a label).
    """
    async def _complete_structured(
        prompt: str, response_model: type[Any], system: str | None = None
    ) -> tuple[Any, TokenUsage]:
        assert response_model is CommunitySummary
        if fail_if_prompt_contains and fail_if_prompt_contains in prompt:
            raise RuntimeError("simulated LLM failure")
        return (
            CommunitySummary(summary="A generated summary of this cluster of coverage."),
            TokenUsage(),
        )

    client = MagicMock(spec=LLMClient)
    client.complete_structured = _complete_structured
    return client


class TestSummarizeCommunities:
    _DISPLAY_DATA = {
        "nodes": [
            _display_node("policy", 10, community=0, community_label="policy"),
            _display_node("biotech", 12, community=1, community_label="biotech"),
        ],
        "links": [],
    }

    def _results(self, articles: list[ArticleSummary]) -> list[TopicResult]:
        config = TopicConfig(
            slug="feeds_ai", title="Artificial Intelligence", feeds_file=Path("/dev/null")
        )
        return [TopicResult(config=config, articles=articles)]

    async def test_returns_summary_per_community(self) -> None:
        results = self._results([
            _article(
                "A", "https://example.com/a", ["policy", "tech", "research", "markets", "law"]
            ),
            _article(
                "B", "https://example.com/b",
                ["biotech", "tech", "research", "markets", "science"]
            ),
        ])
        client = make_community_mock_client()
        summaries = await summarize_communities(results, self._DISPLAY_DATA, client)

        assert set(summaries.keys()) == {0, 1}
        assert summaries[0]["label"] == "policy"
        assert summaries[0]["article_count"] == 1
        assert summaries[0]["summary"] == "A generated summary of this cluster of coverage."

    async def test_community_with_no_matching_articles_absent(self) -> None:
        results = self._results([
            _article(
                "A", "https://example.com/a", ["policy", "tech", "research", "markets", "law"]
            ),
        ])
        client = make_community_mock_client()
        summaries = await summarize_communities(results, self._DISPLAY_DATA, client)
        assert set(summaries.keys()) == {0}

    async def test_no_communities_returns_empty(self) -> None:
        results = self._results([])
        client = make_community_mock_client()
        summaries = await summarize_communities(results, self._DISPLAY_DATA, client)
        assert summaries == {}

    async def test_one_failed_community_does_not_block_others(self) -> None:
        results = self._results([
            _article(
                "A", "https://example.com/a", ["policy", "tech", "research", "markets", "law"]
            ),
            _article(
                "B", "https://example.com/b",
                ["biotech", "tech", "research", "markets", "science"]
            ),
        ])
        # Fail specifically the "biotech" community; "policy" should still succeed.
        client = make_community_mock_client(fail_if_prompt_contains="biotech")
        summaries = await summarize_communities(results, self._DISPLAY_DATA, client)
        assert set(summaries.keys()) == {0}

    async def test_max_articles_caps_prompt_content(self) -> None:
        captured = {}

        async def _complete_structured(
        prompt: str, response_model: type[Any], system: str | None = None
    ) -> tuple[Any, TokenUsage]:
            captured["prompt"] = prompt
            return CommunitySummary(summary="Summary of a large cluster of coverage."), TokenUsage()

        client = MagicMock(spec=LLMClient)
        client.complete_structured = _complete_structured

        articles = [
            _article(
                f"Article {i}", f"https://example.com/{i}",
                ["policy", "tech", "research", "markets", "law"],
            )
            for i in range(5)
        ]
        results = self._results(articles)
        await summarize_communities(results, self._DISPLAY_DATA, client, max_articles=2)

        assert "Article 0" in captured["prompt"]
        assert "Article 1" in captured["prompt"]
        assert "Article 2" not in captured["prompt"]


# ---------------------------------------------------------------------------
# Tests for extract_query_tags() / answer_archive_question()
# ---------------------------------------------------------------------------

def make_archive_mock_client(
    tags: list[str] | None = None,
    answer_text: str = "A grounded answer citing the retrieved coverage.",
) -> MagicMock:
    """A mock client dispatching on QueryTags vs. ArchiveAnswer response_model."""
    tags = tags if tags is not None else ["policy"]

    async def _complete_structured(
        prompt: str, response_model: type[Any], system: str | None = None
    ) -> tuple[Any, TokenUsage]:
        if response_model is QueryTags:
            return QueryTags(tags=tags), TokenUsage()
        if response_model is ArchiveAnswer:
            return ArchiveAnswer(answer=answer_text), TokenUsage()
        raise ValueError(f"Unexpected response_model: {response_model}")

    client = MagicMock(spec=LLMClient)
    client.complete_structured = _complete_structured
    return client


class TestExtractQueryTags:
    async def test_returns_llm_extracted_tags(self) -> None:
        client = make_archive_mock_client(tags=["export controls", "biotech"])
        tags = await extract_query_tags("What's happening with export controls?", client)
        assert tags == ["export controls", "biotech"]


class TestAnswerArchiveQuestion:
    async def test_no_matching_communities_returns_fallback(self, db_path: Path) -> None:
        client = make_archive_mock_client(tags=["nonexistent-topic"])
        result = await answer_archive_question("anything?", db_path, client)
        assert result["communities"] == []
        assert "No archived coverage" in result["answer"]

    async def test_returns_grounded_answer_with_communities(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=10)
        record_community_summaries(db_path, "run-0", {
            0: {
                "label": "policy", "tags": ["export controls"],
                "summary": "Coverage of new export rules.", "article_count": 3,
            },
        })
        client = make_archive_mock_client(
            tags=["export controls"], answer_text="Export controls tightened recently."
        )
        result = await answer_archive_question(
            "What's happening with export controls?", db_path, client
        )
        assert result["answer"] == "Export controls tightened recently."
        assert len(result["communities"]) == 1
        assert result["communities"][0]["label"] == "policy"

    async def test_max_communities_passed_through(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=10)
        record_community_summaries(db_path, "run-0", {
            i: {
                "label": f"topic{i}", "tags": ["shared"],
                "summary": f"Coverage {i}.", "article_count": 1,
            }
            for i in range(5)
        })
        client = make_archive_mock_client(tags=["shared"])
        result = await answer_archive_question("question", db_path, client, max_communities=2)
        assert len(result["communities"]) == 2
