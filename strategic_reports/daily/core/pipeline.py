"""
Async pipeline orchestrator.

Two-phase design:
  Phase 1 — RSS ingestion:  all topics fetched concurrently (pure I/O).
  Phase 2 — LLM processing: topics processed concurrently up to
             `max_concurrent_llm_calls` via asyncio.Semaphore, so we don't
             saturate the API while still exploiting available parallelism.
"""

import asyncio

import structlog

from .ingestion import fetch_topic_articles
from .llm_client import LLMClient
from .models import (
    ArticleSummary,
    ArticleSummaryBatch,
    RawArticle,
    StrategicInsight,
    TokenUsage,
    TopicConfig,
    TopicResult,
)
from .prompts import (
    SYSTEM_STRATEGIST,
    SYSTEM_SUMMARIZER,
    build_strategy_prompt,
    build_summarize_prompt,
)

log = structlog.get_logger(__name__)


def _chunk(lst: list, n: int) -> list[list]:
    return [lst[i : i + n] for i in range(0, len(lst), n)]


async def _summarize_articles(
    topic: TopicConfig,
    articles: list[RawArticle],
    client: LLMClient,
    batch_size: int,
) -> tuple[list[ArticleSummary], TokenUsage]:
    """Summarize and tag articles in batches; return all summaries + total usage."""
    chunks = _chunk(articles, batch_size)
    log.info(
        "summarizing",
        topic=topic.title,
        articles=len(articles),
        batches=len(chunks),
    )

    all_summaries: list[ArticleSummary] = []
    total_usage = TokenUsage()

    for i, chunk in enumerate(chunks, 1):
        batch, usage = await client.complete_structured(
            prompt=build_summarize_prompt(chunk),
            response_model=ArticleSummaryBatch,
            system=SYSTEM_SUMMARIZER,
        )
        all_summaries.extend(batch.articles)
        total_usage = total_usage + usage
        log.debug("batch_done", topic=topic.title, batch=i, of=len(chunks))

    return all_summaries, total_usage


async def _synthesize_strategy(
    topic: TopicConfig,
    summaries: list[ArticleSummary],
    client: LLMClient,
) -> tuple[StrategicInsight, TokenUsage]:
    """Derive strategic bullet points from article summaries."""
    log.info("synthesizing_strategy", topic=topic.title, summaries=len(summaries))

    strategy, usage = await client.complete_structured(
        prompt=build_strategy_prompt(topic.title, summaries),
        response_model=StrategicInsight,
        system=SYSTEM_STRATEGIST,
    )
    return strategy, usage


async def _process_topic(
    topic: TopicConfig,
    articles: list[RawArticle] | Exception,
    client: LLMClient,
    batch_size: int,
    sem: asyncio.Semaphore,
) -> TopicResult:
    """Run LLM summarization + synthesis for one topic, isolated from other topics."""
    if isinstance(articles, Exception):
        log.error("topic_fetch_failed", topic=topic.title, error=str(articles))
        return TopicResult(config=topic, error=str(articles))

    if not articles:
        log.warning("topic_no_articles", topic=topic.title)
        return TopicResult(config=topic)

    async with sem:
        try:
            summaries, usage_s = await _summarize_articles(
                topic, articles, client, batch_size
            )
            strategy, usage_y = await _synthesize_strategy(topic, summaries, client)

            return TopicResult(
                config=topic,
                articles=summaries,
                strategy=strategy,
                token_usage=usage_s + usage_y,
            )

        except Exception as exc:
            log.error("topic_llm_failed", topic=topic.title, error=str(exc))
            return TopicResult(config=topic, error=str(exc))


async def run_pipeline(
    topics: list[TopicConfig],
    client: LLMClient,
    hours_cutoff: int = 24,
    batch_size: int = 50,
    max_concurrent_llm_calls: int = 3,
) -> list[TopicResult]:
    """
    Run the full pipeline for all topics and return one TopicResult per topic.

    Args:
        topics:                  Ordered list of topics to process.
        client:                  Async LLMClient instance.
        hours_cutoff:            Only consider articles published within this window.
        batch_size:              Max articles per LLM summarization call.
        max_concurrent_llm_calls: Semaphore width — how many topics may hit the
                                  LLM API simultaneously.
    """
    log.info("pipeline_start", topics=len(topics), hours_cutoff=hours_cutoff)

    # Phase 1: fetch all RSS feeds concurrently; errors are captured, not raised
    article_lists: list[list[RawArticle] | Exception] = list(
        await asyncio.gather(
            *[fetch_topic_articles(t, hours_cutoff) for t in topics],
            return_exceptions=True,
        )
    )

    # Phase 2: LLM processing, rate-limited by semaphore
    sem = asyncio.Semaphore(max_concurrent_llm_calls)
    results: list[TopicResult] = list(
        await asyncio.gather(
            *[
                _process_topic(topic, articles, client, batch_size, sem)
                for topic, articles in zip(topics, article_lists)
            ]
        )
    )

    total_usage = TokenUsage()
    for r in results:
        total_usage = total_usage + r.token_usage

    log.info(
        "pipeline_complete",
        total_topics=len(results),
        successful=sum(1 for r in results if r.error is None),
        failed=sum(1 for r in results if r.error is not None),
        total_tokens=total_usage.total_tokens,
    )

    return results
