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
    """Fetch one RSS feed and return articles within the cutoff window."""
    try:
        parsed = await asyncio.to_thread(feedparser.parse, feed.url)
    except Exception as exc:
        log.warning("feed_fetch_failed", title=feed.title, url=feed.url, error=str(exc))
        return []

    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours_cutoff)
    articles: list[RawArticle] = []

    for entry in parsed.entries:
        try:
            dt = datetime.datetime.fromtimestamp(mktime(entry.published_parsed))
        except Exception:
            continue

        if dt < cutoff:
            continue

        try:
            raw = entry.content[0]["value"]
            if entry.content[0]["type"].strip() == "text/html":
                raw = html_to_markdown.convert(raw).content
        except Exception:
            continue

        articles.append(
            RawArticle(
                title=entry.title.strip(),
                content=raw.strip(),
                link=entry.link.strip(),
                publish_date=dt,
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
    Fetch all RSS feeds for a topic concurrently and return deduplicated articles,
    sorted newest-first.
    """
    raw_config = json.loads(topic.feeds_file.read_text())
    feeds = [FeedConfig(**f) for f in raw_config["feeds"]]

    log.info("fetching_topic", topic=topic.title, feed_count=len(feeds))

    results = await asyncio.gather(
        *[_fetch_one_feed(f, hours_cutoff) for f in feeds],
        return_exceptions=False,
    )

    # Flatten, deduplicate by URL, sort newest-first
    seen: set[str] = set()
    articles: list[RawArticle] = []
    for batch in results:
        for article in batch:
            if article.link not in seen:
                seen.add(article.link)
                articles.append(article)

    articles.sort(key=lambda a: a.publish_date, reverse=True)

    log.info(
        "topic_fetched",
        topic=topic.title,
        total_articles=len(articles),
        hours_cutoff=hours_cutoff,
    )
    return articles
