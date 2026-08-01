"""
Persists each run's article summaries into the tracking database, linked
to run_id — the source material every derived signal in this pipeline
(tag graph, strategic bullets, urgency scores) is computed from, but which
otherwise exists only in memory during a run.

record_articles() inserts one row per ArticleSummary (title, link,
publish_date), plus its summary bullets and tags as child rows. Call it
once per run, after db.record_run(db_path, run_id, ...).

load_articles() reconstructs a run's article summaries from the database —
a round-trip counterpart. The `ask` archive-query command (see
archive_query.py) currently reads community_summaries, not this table
directly — load_articles() is available if a future retrieval mode wants
to ground answers in raw article text rather than community summaries.
"""

from datetime import datetime, timezone
from pathlib import Path

from .db import connect
from .models import TopicResult


def record_articles(db_path: Path, run_id: str, results: list[TopicResult]) -> None:
    """
    Insert this run's article summaries (title, link, publish_date, summary
    bullets, tags) into the database, linked to run_id.

    Iterates result.articles directly (empty for error/empty topics, so no
    separate strategy-success check is needed — same pattern as
    tag_graph.build_graph_data). Assumes db.record_run(db_path, run_id, ...)
    has already been called this run, so the run_id foreign key exists.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(db_path)
    try:
        for result in results:
            topic_title = result.config.title
            for article in result.articles:
                cursor = conn.execute(
                    "INSERT INTO articles (run_id, created_at, topic, title, link, publish_date) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, now, topic_title, article.title, article.link, article.publish_date),
                )
                article_id = cursor.lastrowid
                conn.executemany(
                    "INSERT INTO article_summary_bullets "
                    "(article_id, run_id, created_at, bullet_index, bullet_text) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [
                        (article_id, run_id, now, i, bullet)
                        for i, bullet in enumerate(article.summary)
                    ],
                )
                conn.executemany(
                    "INSERT INTO article_tags (article_id, run_id, created_at, tag) VALUES (?, ?, ?, ?)",
                    [(article_id, run_id, now, tag) for tag in article.tags],
                )
        conn.commit()
    finally:
        conn.close()


def load_articles(db_path: Path, run_id: str) -> list[dict]:
    """
    Reconstruct this run's article summaries from the database: a list of
    dicts shaped like {"topic", "title", "link", "publish_date", "summary",
    "tags"} — the same fields ArticleSummary carries, per article.
    """
    conn = connect(db_path)
    try:
        article_rows = conn.execute(
            "SELECT id, topic, title, link, publish_date FROM articles "
            "WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()

        bullet_rows = conn.execute(
            "SELECT article_id, bullet_text FROM article_summary_bullets "
            "WHERE run_id = ? ORDER BY article_id, bullet_index",
            (run_id,),
        ).fetchall()
        tag_rows = conn.execute(
            "SELECT article_id, tag FROM article_tags WHERE run_id = ? ORDER BY article_id, id",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()

    bullets_by_article: dict[int, list[str]] = {}
    for article_id, bullet_text in bullet_rows:
        bullets_by_article.setdefault(article_id, []).append(bullet_text)

    tags_by_article: dict[int, list[str]] = {}
    for article_id, tag in tag_rows:
        tags_by_article.setdefault(article_id, []).append(tag)

    return [
        {
            "topic": topic,
            "title": title,
            "link": link,
            "publish_date": publish_date,
            "summary": bullets_by_article.get(article_id, []),
            "tags": tags_by_article.get(article_id, []),
        }
        for article_id, topic, title, link, publish_date in article_rows
    ]
