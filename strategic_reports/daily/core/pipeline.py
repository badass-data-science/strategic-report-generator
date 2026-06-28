"""
Async pipeline orchestrator.

This module wires together ingestion, LLM processing, and result collection.
It does not do I/O itself — it delegates to ingestion.py and llm_client.py.

TWO-PHASE DESIGN
----------------
The pipeline runs in two distinct phases with different concurrency strategies:

Phase 1 — RSS Ingestion (pure I/O, no rate limits)
  All topics' feed fetching fires simultaneously via asyncio.gather.
  No semaphore needed: RSS servers are not rate-limited like LLM APIs,
  and feed fetching is already parallelized within each topic.
  return_exceptions=True means a failure for one topic's ingestion is
  captured as an Exception object in the results list, not re-raised —
  the pipeline keeps going for all other topics.

Phase 2 — LLM Processing (rate-limited)
  Topics also run concurrently, but behind an asyncio.Semaphore.
  "async with sem:" blocks until a slot is available, so at most
  max_concurrent_llm_calls topics are hitting the LLM API at once.
  Without this, all 12 topics would hammer the API simultaneously,
  likely triggering rate limit errors.

ERROR ISOLATION
---------------
_process_topic wraps each topic's LLM work in try/except and returns a
TopicResult(error=...) instead of re-raising. One topic failing (bad feed,
API timeout, malformed LLM output) never cancels the others. The caller
gets a full list of results and can see which topics failed.

COMPARISON TO THE ORIGINAL
---------------------------
Original: sequential papermill calls with time.sleep(5) between each one.
  → 12 topics × (fetch + summarize + strategize) = serial execution
  → time.sleep(5) is a crude throttle that wastes wall-clock time

This refactor:
  → Phase 1 runs all fetches in parallel (I/O overlap)
  → Phase 2 runs up to N topics in parallel (semaphore-controlled)
  → No sleep() anywhere — the event loop yields naturally during await
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
    """
    Split lst into sublists of at most n items.

    Example: _chunk([1,2,3,4,5], 2) → [[1,2], [3,4], [5]]

    Used to batch articles for LLM summarization. LLMs have context window
    limits, so we can't send all 200 articles in one prompt. Batching into
    chunks of batch_size (default 50) keeps each prompt within safe limits.

    range(0, len(lst), n) generates [0, n, 2n, 3n, ...] — the start index
    of each chunk. lst[i : i + n] is a slice from i to i+n (exclusive),
    which naturally handles the last chunk being smaller than n.
    """
    return [lst[i : i + n] for i in range(0, len(lst), n)]


async def _summarize_articles(
    topic: TopicConfig,
    articles: list[RawArticle],
    client: LLMClient,
    batch_size: int,
) -> tuple[list[ArticleSummary], TokenUsage]:
    """
    Summarize and tag articles in sequential batches.

    Batches are sequential (not parallel) within a topic because each batch
    is an independent LLM call and we don't want to further multiply API
    pressure on top of the cross-topic concurrency from the semaphore.

    Returns all summaries flattened into a single list, plus accumulated
    token usage across all batches.
    """
    chunks = _chunk(articles, batch_size)
    log.info(
        "summarizing",
        topic=topic.title,
        articles=len(articles),
        batches=len(chunks),
    )

    all_summaries: list[ArticleSummary] = []
    total_usage = TokenUsage()  # starts at zero; we add to it each batch

    for i, chunk in enumerate(chunks, 1):
        # Each await here suspends this coroutine until the LLM responds,
        # letting other coroutines (other topics) run in the meantime.
        batch, usage = await client.complete_structured(
            prompt=build_summarize_prompt(chunk),
            response_model=ArticleSummaryBatch,
            system=SYSTEM_SUMMARIZER,
        )
        # batch.articles is a list[ArticleSummary]; extend flattens it in.
        all_summaries.extend(batch.articles)
        # TokenUsage.__add__ returns a new object; we rebind total_usage.
        total_usage = total_usage + usage
        log.debug("batch_done", topic=topic.title, batch=i, of=len(chunks))

    return all_summaries, total_usage


async def _synthesize_strategy(
    topic: TopicConfig,
    summaries: list[ArticleSummary],
    client: LLMClient,
) -> tuple[StrategicInsight, TokenUsage]:
    """
    Derive 3-5 strategic bullet points from the topic's article summaries.

    This is the second LLM call in the per-topic flow: it reads all the
    article summaries together and produces a holistic synthesis.
    """
    log.info("synthesizing_strategy", topic=topic.title, summaries=len(summaries))

    strategy, usage = await client.complete_structured(
        prompt=build_strategy_prompt(topic.title, summaries),
        response_model=StrategicInsight,
        system=SYSTEM_STRATEGIST,
    )
    return strategy, usage


async def _process_topic(
    topic: TopicConfig,
    articles: list[RawArticle] | Exception,  # Exception if Phase 1 ingestion failed
    client: LLMClient,
    batch_size: int,
    sem: asyncio.Semaphore,
) -> TopicResult:
    """
    Run LLM summarization + synthesis for one topic, fully isolated.

    Error isolation pattern:
      - If articles is an Exception (ingestion failed), return error result immediately.
      - If articles is empty (no recent news), return empty result immediately.
      - Otherwise, acquire the semaphore and run LLM calls.
      - If LLM calls raise, catch and return error result.
      - In none of these cases do we re-raise — the caller always gets a TopicResult.

    "async with sem:" is the asyncio.Semaphore context manager.
    It decrements the semaphore counter on entry (blocking if count is 0)
    and increments it on exit. So at most max_concurrent_llm_calls topics
    can be inside this block simultaneously.
    """
    # Phase 1 failure arrives here as an Exception in the articles slot
    # because run_pipeline used return_exceptions=True in asyncio.gather.
    if isinstance(articles, Exception):
        log.error("topic_fetch_failed", topic=topic.title, error=str(articles))
        return TopicResult(config=topic, error=str(articles))

    if not articles:
        log.warning("topic_no_articles", topic=topic.title)
        return TopicResult(config=topic)

    # Acquire the semaphore before making any LLM calls.
    # Other topics waiting here will proceed as slots become available.
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
                # Sum summarization + strategy token usage for this topic.
                token_usage=usage_s + usage_y,
            )

        except Exception as exc:
            # Catch-all for LLM errors (rate limits, timeouts, schema violations
            # that exhaust tenacity retries). Log it and return an error result
            # so the other topics' results are not lost.
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

    The returned list preserves the order of the input topics list, so the
    renderer can maintain the configured topic ordering in the HTML output.

    Args:
        topics:                   Ordered list of topics to process.
        client:                   Async LLMClient (shared across all topics).
        hours_cutoff:             Only consider articles published this recently.
        batch_size:               Max articles per LLM summarization call.
        max_concurrent_llm_calls: Semaphore width — tune this per provider.
                                  Lower for strict rate limits (Anthropic free tier),
                                  higher for hosted Ollama with no limits.
    """
    log.info("pipeline_start", topics=len(topics), hours_cutoff=hours_cutoff)

    # -------------------------------------------------------------------------
    # Phase 1: Fetch all RSS content concurrently.
    # -------------------------------------------------------------------------
    # return_exceptions=True is critical here: if any topic's ingestion raises
    # (e.g. missing JSON config file), the Exception is placed in the results
    # list at that topic's index rather than propagating and cancelling ALL topics.
    # _process_topic checks isinstance(articles, Exception) to handle this case.
    article_lists: list[list[RawArticle] | Exception] = list(
        await asyncio.gather(
            *[fetch_topic_articles(t, hours_cutoff) for t in topics],
            return_exceptions=True,
        )
    )

    # -------------------------------------------------------------------------
    # Phase 2: LLM processing, rate-limited by semaphore.
    # -------------------------------------------------------------------------
    # Create the semaphore once and share it across all _process_topic calls.
    # asyncio.Semaphore is not thread-safe but IS coroutine-safe.
    sem = asyncio.Semaphore(max_concurrent_llm_calls)

    # zip(topics, article_lists) pairs each TopicConfig with its fetched articles.
    # If Phase 1 failed for a topic, article_lists[i] is an Exception.
    results: list[TopicResult] = list(
        await asyncio.gather(
            *[
                _process_topic(topic, articles, client, batch_size, sem)
                for topic, articles in zip(topics, article_lists)
            ]
            # No return_exceptions=True here because _process_topic never raises —
            # it catches all exceptions internally and returns TopicResult(error=...).
        )
    )

    # Accumulate total usage for the summary log line.
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
