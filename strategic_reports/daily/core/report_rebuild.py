"""
Rebuild a past run's report inputs from the tracking database, for the
`render-from-db` CLI command -- re-rendering index.html, the featured
reports, and the {topic}_summaries.html pages without re-running ingestion
or any LLM call.

Read-only, pure DB access (no LLM dependency), same split as
archive_query.py. What can't be rebuilt, because it's never persisted:

  - Bullet diffs ("New since yesterday" / "No longer in focus") -- produced
    by an LLM call in bullet_diff.diff_all_topics() and only rendered.
  - A topic's pipeline error message -- TopicResult.error isn't stored. A
    topic with archived articles but no stored bullets is flagged with a
    generic error note instead (that combination means strategy synthesis
    failed that run); a topic with neither renders as "no articles".
  - Systems Signals -- recomputed fresh from full history on every run
    (systems_signals.py), so recomputing it now wouldn't reflect the past
    run anyway. The caller skips systems_signals.json entirely.
  - Token usage -- not persisted, so the footer shows 0.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from ..config.topic_order import list_directories_and_titles
from .article_archive import load_articles
from .db import get_connection
from .models import (
    ArticleSummary,
    CrossTopicSynthesis,
    StrategicInsight,
    TopicConfig,
    TopicResult,
)

MISSING_BULLETS_ERROR = (
    "Strategic bullets for this topic weren't found in the tracking database "
    "for this run (synthesis likely failed at the time; the original error "
    "isn't persisted)."
)


@dataclass
class StoredRun:
    run_id: str
    created_at: datetime
    results: list[TopicResult]
    overview: CrossTopicSynthesis | None
    # Topics present in this run's rows but no longer in topic_order.py --
    # not rendered, reported so the caller can warn.
    unknown_topics: list[str] = field(default_factory=list)
    # Articles that failed ArticleSummary validation (e.g. rows written
    # under older, looser constraints) and were loaded unvalidated.
    unvalidated_article_count: int = 0


def resolve_run(database_url: str, run_id: str | None) -> tuple[str, datetime]:
    """
    Return (run_id, created_at) for run_id, or for the most recent run if
    run_id is None. Raises LookupError if there's no such run.
    """
    with get_connection(database_url) as conn:
        if run_id is None:
            row = conn.execute(
                "SELECT run_id, created_at FROM runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise LookupError("the tracking database has no runs")
        else:
            row = conn.execute(
                "SELECT run_id, created_at FROM runs WHERE run_id = %s", (run_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"no run with run_id {run_id!r}")
    return str(row[0]), datetime.fromisoformat(str(row[1]))


def load_stored_run(database_url: str, run_id: str | None = None) -> StoredRun:
    """
    Rebuild one run's TopicResults (bullets, urgency score, article
    summaries) and Strategic Overview from the tracking database, in
    topic_order.py order -- the inputs renderer.render_report() needs.
    run_id=None means the most recent run.
    """
    resolved_id, created_at = resolve_run(database_url, run_id)

    bullets: dict[str, list[str]] = defaultdict(list)
    with get_connection(database_url) as conn:
        for topic, text in conn.execute(
            "SELECT topic, bullet_text FROM bullets WHERE run_id = %s "
            "ORDER BY topic, bullet_index",
            (resolved_id,),
        ):
            bullets[topic].append(text)
        urgency: dict[str, float] = {
            topic: score
            for topic, score in conn.execute(
                "SELECT topic, score FROM urgency_scores WHERE run_id = %s", (resolved_id,)
            )
        }
        overview_bullets = [
            row[0]
            for row in conn.execute(
                "SELECT bullet_text FROM cross_topic_overviews WHERE run_id = %s "
                "ORDER BY bullet_index",
                (resolved_id,),
            )
        ]

    articles: dict[str, list[ArticleSummary]] = defaultdict(list)
    unvalidated = 0
    for row in load_articles(database_url, resolved_id):
        topic = row.pop("topic")
        try:
            articles[topic].append(ArticleSummary.model_validate(row))
        except ValidationError:
            articles[topic].append(ArticleSummary.model_construct(**row))
            unvalidated += 1

    known_titles = {item["title"] for item in list_directories_and_titles}
    results = []
    for item in list_directories_and_titles:
        title = item["title"]
        config = TopicConfig(
            slug=item["slug"], title=title, feeds_file=Path(f"{item['slug']}.json")
        )
        topic_bullets = bullets.get(title)
        topic_articles = articles.get(title, [])
        if topic_bullets:
            strategy = StrategicInsight.model_construct(
                bullets=topic_bullets, urgency_score=urgency.get(title, 0.0)
            )
            results.append(TopicResult(config=config, articles=topic_articles, strategy=strategy))
        elif topic_articles:
            results.append(
                TopicResult(config=config, articles=topic_articles, error=MISSING_BULLETS_ERROR)
            )
        else:
            results.append(TopicResult(config=config))

    unknown = sorted((set(bullets) | set(articles)) - known_titles)

    overview = (
        CrossTopicSynthesis.model_construct(bullets=overview_bullets)
        if overview_bullets
        else None
    )
    return StoredRun(
        run_id=resolved_id,
        created_at=created_at,
        results=results,
        overview=overview,
        unknown_topics=unknown,
        unvalidated_article_count=unvalidated,
    )
