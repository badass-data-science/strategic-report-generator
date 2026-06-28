"""
Async RSS ingestion layer.

Replaces content_fetch_inator.py + pickle-file hand-off from the original pipeline.

Key design decisions:

1. asyncio over multiprocessing
   The original code used multiprocessing.Pool to parallelize feed fetching.
   That's appropriate for CPU-bound work, but RSS fetching is I/O-bound
   (waiting on network). asyncio is cheaper: no process spawn overhead, no
   inter-process serialization, and tasks share memory safely.

2. asyncio.to_thread for feedparser
   feedparser.parse() is a synchronous (blocking) function. Calling it
   directly in an async function would block the event loop, preventing other
   coroutines from running during the network wait. asyncio.to_thread() runs
   the function in a thread pool, returning control to the event loop
   immediately. Think of it as "run this blocking call without blocking me."

3. Typed output
   Returns list[RawArticle] instead of a list of DataFrames. No pickle files,
   no .to_dict(orient='records'), no downstream schema guessing.

4. Named failure logging
   The original code used bare "except: continue" everywhere, silently
   swallowing failures. Here, feed-level failures are logged with the feed
   URL and error message so you can diagnose problems without re-running.
   Entry-level failures (bad date, missing content) still use continue
   because they're expected in noisy RSS feeds — but they don't hide
   feed-level network failures.
"""

import asyncio
import datetime
import json
from time import mktime

import feedparser
import html_to_markdown
import structlog

from .models import FeedConfig, RawArticle, TopicConfig

log = structlog.get_logger(__name__)


async def _fetch_one_feed(feed: FeedConfig, hours_cutoff: int) -> list[RawArticle]:
    """
    Fetch one RSS feed and return articles published within hours_cutoff.

    This is a private helper (leading underscore convention) called by
    fetch_topic_articles via asyncio.gather. It handles its own errors and
    returns an empty list on failure so one bad feed doesn't stop others.
    """
    try:
        # asyncio.to_thread runs feedparser.parse (synchronous, blocking)
        # in the default ThreadPoolExecutor without blocking the event loop.
        # Equivalent to: loop.run_in_executor(None, feedparser.parse, feed.url)
        parsed = await asyncio.to_thread(feedparser.parse, feed.url)
    except Exception as exc:
        # Log the failure with structured fields so you can grep for
        # "feed_fetch_failed" in log aggregation tools.
        log.warning("feed_fetch_failed", title=feed.title, url=feed.url, error=str(exc))
        return []

    # Compute the oldest acceptable publish date once, outside the loop.
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours_cutoff)
    articles: list[RawArticle] = []

    for entry in parsed.entries:
        # feedparser returns times as struct_time (from the C time library).
        # mktime() converts struct_time → Unix timestamp → datetime.
        # We wrap in try/except because some entries have malformed or missing
        # published dates; continue skips them rather than crashing.
        try:
            dt = datetime.datetime.fromtimestamp(mktime(entry.published_parsed))
        except Exception:
            continue

        # Skip articles older than the cutoff window.
        if dt < cutoff:
            continue

        try:
            # RSS entries can have multiple content blocks; we always take [0].
            raw = entry.content[0]["value"]
            # Many feeds serve full HTML in the content field. Convert it to
            # Markdown so the LLM gets clean, readable text without HTML tags.
            if entry.content[0]["type"].strip() == "text/html":
                raw = html_to_markdown.convert(raw).content
        except Exception:
            # Entry has no content field at all — skip it.
            continue

        articles.append(
            RawArticle(
                title=entry.title.strip(),
                content=raw.strip(),
                link=entry.link.strip(),
                publish_date=dt,
                # getattr with a default handles feeds that omit the summary field.
                summary_from_feed=getattr(entry, "summary", "").strip(),
            )
        )

    log.debug("feed_fetched", title=feed.title, count=len(articles))
    return articles


async def fetch_topic_articles(
    topic: TopicConfig,
    hours_cutoff: int = 24,
) -> list[RawArticle]:
    """
    Fetch all RSS feeds for a topic concurrently and return deduplicated
    articles sorted newest-first.

    This is the public entry point called by the pipeline orchestrator.

    Concurrency model:
      asyncio.gather(*coroutines) starts all coroutines simultaneously and
      waits for all of them to finish. For a topic with 50 feeds, all 50
      fetch operations run in parallel — total time ≈ slowest single feed,
      not sum of all feeds.

      return_exceptions=False means if _fetch_one_feed raises (it shouldn't,
      because it catches its own errors), the exception propagates from gather.
      The pipeline's error isolation then catches it at the topic level.
    """
    # Load the feeds list from disk. topic.feeds_file is a Path object;
    # .read_text() reads it as a string, json.loads() parses it.
    raw_config = json.loads(topic.feeds_file.read_text())

    # Unpack each dict in the "feeds" list into a FeedConfig Pydantic model.
    # FeedConfig(**f) is equivalent to FeedConfig(title=f["title"], url=f["url"]).
    feeds = [FeedConfig(**f) for f in raw_config["feeds"]]

    log.info("fetching_topic", topic=topic.title, feed_count=len(feeds))

    # Launch all feed fetches simultaneously.
    # The * unpacks the list of coroutines as positional arguments to gather.
    results = await asyncio.gather(
        *[_fetch_one_feed(f, hours_cutoff) for f in feeds],
        return_exceptions=False,
    )
    # results is a list of lists: [[article, article], [article], [], ...]
    # one inner list per feed, some empty (no recent articles or fetch failed)

    # Flatten + deduplicate by URL using a set for O(1) membership checks.
    # The order of insertion matters here: we process feeds in order, so
    # if the same URL appears in two feeds, the first one wins.
    seen: set[str] = set()
    articles: list[RawArticle] = []
    for batch in results:
        for article in batch:
            if article.link not in seen:
                seen.add(article.link)
                articles.append(article)

    # Sort newest-first so that if we later truncate for token budget,
    # we keep the most recent articles.
    articles.sort(key=lambda a: a.publish_date, reverse=True)

    log.info(
        "topic_fetched",
        topic=topic.title,
        total_articles=len(articles),
        hours_cutoff=hours_cutoff,
    )
    return articles
