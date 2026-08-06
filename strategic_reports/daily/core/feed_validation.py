"""
Health checks for the RSS feed configs in data/rss_feeds/*.json.

Feeds rot over time: hosts disappear, blogs migrate platforms, WAFs start
blocking naive HTTP clients, XML gets served with syntax errors. This module
finds that rot (validate_topic_feeds) and, optionally, fixes it
(remove_dead_feeds) by pruning dead entries out of a topic's feeds_*.json and
logging them in REMOVED.json — the same audit-log format already used for
manually-curated exclusions (e.g. "I do not want personal websites.").

Reuses the same asyncio.to_thread(feedparser.parse, ...) pattern as
ingestion.py's _fetch_one_feed, for the same reason: feedparser.parse is
blocking, and awaiting it via to_thread lets asyncio.gather check every feed
in a topic concurrently instead of one at a time.
"""

import asyncio
import json
import socket
from pathlib import Path

import feedparser
from pydantic import BaseModel

from .models import FeedConfig, TopicConfig

# feedparser uses urllib under the hood and honors socket.setdefaulttimeout()
# when no explicit timeout is passed. Without this, a feed that accepts the
# connection but never responds hangs forever — and worse than just stalling
# validate_topic_feeds's asyncio.gather, it wedges the worker thread
# permanently: asyncio.to_thread runs feedparser.parse on the default
# ThreadPoolExecutor, and cancelling the awaiting coroutine (e.g. via
# asyncio.wait_for) only stops waiting, it does not stop the thread. Enough
# hung feeds in one topic exhausts the pool and every later to_thread() call
# queues forever waiting for a free worker.
_FEED_TIMEOUT_S = 15


class FeedCheckResult(BaseModel):
    """Outcome of validating one feed: whether it's usable, and why not."""
    feed: FeedConfig
    ok: bool
    detail: str = ""  # empty when ok=True; a short failure reason otherwise


def _clean_exception(exc_str: str) -> str:
    """
    Strip feedparser/urllib wrapper noise from an exception string.

    "<urlopen error [Errno -2] Name or service not known>" is feedparser's
    wrapper around a network-layer failure — the interesting part is inside
    the brackets. "<unknown>:LINE:COL: message" is an XML parse error where
    "<unknown>" is just the parser's placeholder for "no filename" — the
    line/column/message is what a human wants to see.
    """
    if exc_str.startswith("<urlopen error ") and exc_str.endswith(">"):
        return exc_str[len("<urlopen error "):-1]
    if exc_str.startswith("<unknown>:"):
        return "malformed XML (" + exc_str[len("<unknown>:"):] + ")"
    return exc_str


async def _check_one_feed(feed: FeedConfig) -> FeedCheckResult:
    """
    Fetch and parse one feed, classifying it as ok or dead.

    A feed counts as dead if: fetching raised an exception (DNS failure,
    timeout, connection refused, ...), feedparser couldn't parse the
    response as XML and found zero entries anyway (a "bozo" feed that
    happens to have salvageable entries is left alone), or the response
    parsed cleanly but contained zero entries (e.g. an HTML error page
    served with HTTP 200).
    """
    socket.setdefaulttimeout(_FEED_TIMEOUT_S)
    try:
        parsed = await asyncio.to_thread(feedparser.parse, feed.url)
    except Exception as exc:
        return FeedCheckResult(feed=feed, ok=False, detail=str(exc))

    status = getattr(parsed, "status", None)
    entries = len(parsed.entries)

    if parsed.bozo and entries == 0:
        cleaned = _clean_exception(str(parsed.get("bozo_exception")))
        detail = cleaned if status is None else f"HTTP {status}; {cleaned}"
        return FeedCheckResult(feed=feed, ok=False, detail=detail)

    if entries == 0:
        detail = f"HTTP {status} but no parseable entries"
        return FeedCheckResult(feed=feed, ok=False, detail=detail)

    return FeedCheckResult(feed=feed, ok=True)


async def validate_topic_feeds(topic: TopicConfig) -> list[FeedCheckResult]:
    """Check every feed in one topic's feeds file concurrently."""
    raw_config = json.loads(topic.feeds_file.read_text())
    feeds = [FeedConfig(**f) for f in raw_config["feeds"]]
    return list(await asyncio.gather(*[_check_one_feed(f) for f in feeds]))


def remove_dead_feeds(
    topic: TopicConfig, results: list[FeedCheckResult], removed_path: Path
) -> int:
    """
    Prune failing feeds out of topic.feeds_file and log them in removed_path.

    removed_path is expected to already exist with a {"removed": {...}}
    shape (REMOVED.json ships with the package); a topic that has never had
    a feed removed before gets a new key in "removed", appended alongside
    whatever's already there rather than overwriting it — this is meant to
    be re-run periodically, accumulating history, not replace it each time.

    Returns the number of feeds removed (0 leaves both files untouched).
    """
    dead = [r for r in results if not r.ok]
    if not dead:
        return 0

    data = json.loads(topic.feeds_file.read_text())
    data["feeds"] = [r.feed.model_dump() for r in results if r.ok]
    topic.feeds_file.write_text(json.dumps(data, indent=4) + "\n")

    removed_data = json.loads(removed_path.read_text())
    bucket = removed_data["removed"].setdefault(topic.slug, [])
    bucket.extend({"title": r.feed.title, "url": r.feed.url, "exception": r.detail} for r in dead)
    removed_path.write_text(json.dumps(removed_data, indent=4) + "\n")

    return len(dead)
