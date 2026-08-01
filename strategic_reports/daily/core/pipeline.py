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
from pathlib import Path

import structlog

from .archive_query import find_relevant_communities
from .ingestion import fetch_topic_articles
from .llm_client import LLMClient
from .tag_graph import build_graph_data, find_bridge_tags, group_articles_by_community
from .models import (
    ArchiveAnswer,
    ArticleSummary,
    ArticleSummaryBatch,
    CommunitySummary,
    CrossTopicSynthesis,
    QueryTags,
    RawArticle,
    StrategicInsight,
    TokenUsage,
    TopicConfig,
    TopicResult,
)
from .prompts import (
    SYSTEM_ARCHIVE_ANSWER,
    SYSTEM_COMMUNITY_SUMMARY,
    SYSTEM_CROSS_TOPIC,
    SYSTEM_QUERY_TAGS,
    SYSTEM_STRATEGIST,
    SYSTEM_SUMMARIZER,
    build_archive_answer_prompt,
    build_community_summary_prompt,
    build_cross_topic_prompt,
    build_query_tags_prompt,
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
    """
    # Phase 1 failure arrives here as an Exception in the articles slot
    # because run_pipeline used return_exceptions=True in asyncio.gather.
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
                # Sum summarization + strategy token usage for this topic.
                token_usage=usage_s + usage_y,
            )

        except Exception as exc:
            # Catch-all for LLM errors (rate limits, timeouts, schema violations
            # that exhaust tenacity retries). Log it and return an error result
            # so the other topics' results are not lost.
            log.error("topic_llm_failed", topic=topic.title, error=str(exc))
            return TopicResult(config=topic, error=str(exc))


async def synthesize_cross_topic(
    results: list[TopicResult],
    client: LLMClient,
) -> CrossTopicSynthesis:
    """
    Make a single LLM call to synthesize cross-cutting themes across all topics.

    Only topics with a successful StrategicInsight are included in the prompt.
    Also computes bridge tags (tags whose articles span multiple topics — a
    graph-native, non-LLM signal) and includes them in the prompt as
    candidate leads for the model to weigh, grounding the synthesis in
    structure rather than pure inference.
    Raises if the LLM call fails — the caller (Prefect task) handles that.
    """
    successful = [r for r in results if r.strategy is not None]
    bridge_tags = find_bridge_tags(build_graph_data(results))
    log.info("synthesizing_cross_topic", topics=len(successful), bridge_tags=len(bridge_tags))
    synthesis, _ = await client.complete_structured(
        prompt=build_cross_topic_prompt(results, bridge_tags),
        response_model=CrossTopicSynthesis,
        system=SYSTEM_CROSS_TOPIC,
    )
    return synthesis


async def _summarize_one_community(
    community_id: int,
    label: str,
    tags: list[str],
    articles: list[ArticleSummary],
    client: LLMClient,
    sem: asyncio.Semaphore,
    max_articles: int,
) -> tuple[int, dict | None]:
    async with sem:
        try:
            summary, _ = await client.complete_structured(
                prompt=build_community_summary_prompt(label, tags, articles[:max_articles]),
                response_model=CommunitySummary,
                system=SYSTEM_COMMUNITY_SUMMARY,
            )
            return community_id, {
                "label": label,
                "tags": tags,
                "summary": summary.summary,
                "article_count": len(articles),
            }
        except Exception as exc:
            log.warning("community_summary_failed", community_id=community_id, error=str(exc))
            return community_id, None


async def summarize_communities(
    results: list[TopicResult],
    display_data: dict,
    client: LLMClient,
    max_concurrent: int = 3,
    max_articles: int = 12,
) -> dict[int, dict]:
    """
    Generate an LLM-written paragraph summary for each Louvain community in
    display_data (see tag_graph.build_display_graph), grounded in the
    articles whose tags belong to that community
    (tag_graph.group_articles_by_community).

    max_articles caps how many of a community's articles are included per
    prompt — communities can have far more matching articles than are
    useful (or fit comfortably) in one summarization call.

    Communities with no matching articles are skipped. A per-community
    failure logs a warning and is omitted from the result — one bad call
    never blocks the others. Returns {community_id: {"label", "tags",
    "summary", "article_count"}}.
    """
    grouped = group_articles_by_community(results, display_data)
    sem = asyncio.Semaphore(max_concurrent)
    tasks = [
        _summarize_one_community(
            comm_id, info["label"], info["tags"], info["articles"], client, sem, max_articles
        )
        for comm_id, info in grouped.items()
    ]
    if not tasks:
        return {}

    pairs = await asyncio.gather(*tasks, return_exceptions=True)
    summaries: dict[int, dict] = {}
    for item in pairs:
        if isinstance(item, Exception):
            log.warning("community_summary_gather_error", error=str(item))
            continue
        comm_id, data = item
        if data is not None:
            summaries[comm_id] = data

    log.info("summarize_communities_complete", communities=len(summaries))
    return summaries


async def extract_query_tags(question: str, client: LLMClient) -> list[str]:
    """
    LLM call extracting a short list of candidate tags/phrases from a
    free-text question, for graph-guided retrieval against the archived
    tag-community summaries (see archive_query.find_relevant_communities).
    """
    result, _ = await client.complete_structured(
        prompt=build_query_tags_prompt(question),
        response_model=QueryTags,
        system=SYSTEM_QUERY_TAGS,
    )
    return result.tags


async def answer_archive_question(
    question: str,
    db_path: Path,
    client: LLMClient,
    max_communities: int = 8,
) -> dict:
    """
    Answer a free-text question about the accumulated archive via
    graph-guided retrieval: extract candidate tags from the question, find
    matching community summaries across every run
    (archive_query.find_relevant_communities), then synthesize an answer
    grounded in those — never in outside knowledge or raw archive text
    the model wasn't shown.

    Returns {"answer": str, "communities": list[dict]} — communities is the
    retrieved context actually used, so the caller can show what the answer
    is (and isn't) grounded in.
    """
    candidate_tags = await extract_query_tags(question, client)
    log.info("archive_query_tags_extracted", tags=candidate_tags)

    communities = find_relevant_communities(db_path, candidate_tags, limit=max_communities)
    if not communities:
        return {
            "answer": "No archived coverage matches this question yet.",
            "communities": [],
        }

    answer, _ = await client.complete_structured(
        prompt=build_archive_answer_prompt(question, communities),
        response_model=ArchiveAnswer,
        system=SYSTEM_ARCHIVE_ANSWER,
    )
    return {"answer": answer.answer, "communities": communities}


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
