"""
Per-run tag tracking and emerging-tag z-score alerting — PostgreSQL-backed.

record_tags() persists a run's tag graph (per-tag counts, per-tag topics,
and tag-pair co-occurrence edges) into the tracking database, linked to
run_id. rebuild_graph_data() reconstructs tag_graph.json's {"nodes",
"links"} shape from the database for a given run_id — nothing there is
lost by storing it relationally instead of as a flat JSON file.

check_emerging_tags() flags tags whose rate this run (tag count / that
run's article_count — see db.record_run) is anomalously high relative to
that tag's own historical rate, mirroring urgency.py's z-score pattern.
Rate, not raw count, is what's compared: a count of 20 means something
different on a 400-article news day than a 50-article one.

record_emerging_tag_alerts() persists the alerts that fired (not every
tag's rate/z-score — those stay always recomputable from tag_counts +
runs.article_count), as an audit trail: "what was tag X's z-score on day
N" is then answerable directly.

record_community_summaries() persists the LLM-written paragraph per
Louvain community from pipeline.summarize_communities(), replacing
"labeled by top tag" with real substance — grounded in the articles
tag_graph.group_articles_by_community() groups by community.

Call order per run (same pattern as urgency.py/bullet_diff.py):
  0. db.record_run(database_url, run_id, article_count) — once per run
  1. load_tag_rate_history      (reads only, does not include the current run)
  2. check_emerging_tags         (current rates vs. historical baseline)
  3. record_tags                 (writes current run's tag graph for future runs)
  4. record_emerging_tag_alerts   (writes the alerts from step 2, if any)
  5. record_community_summaries    (writes pipeline.summarize_communities()'s output)

Unlike urgency scores (a bounded, LLM-scored 0-1 value where an absolute
cutoff like 0.8 is meaningful), tag rates have no obvious absolute
threshold to fall back on for thin-history tags — tags are an open,
growing vocabulary, and most will never accumulate much history. So
check_emerging_tags only ever uses the statistical check: a tag with fewer
than _MIN_HISTORY_RUNS prior appearances (including a brand-new tag) is
silently skipped rather than guessed at.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
import structlog

from .db import get_connection

log = structlog.get_logger(__name__)

# Minimum number of historical runs required before a tag can be flagged.
# Matches urgency.py's _MIN_HISTORY_RUNS for consistency.
_MIN_HISTORY_RUNS = 7


def _insert_rows_isolating_failures(
    conn: psycopg.Connection, sql: str, rows: list[tuple[Any, ...]], run_id: str, table: str
) -> int:
    """
    Insert `rows` via one batched executemany() inside a savepoint --
    a single round trip, the fast path, when nothing fails. If the batch
    fails, retry each row individually inside its own savepoint so one bad
    row can't silently discard the rest of the table for this run --
    same principle as article_archive.record_articles's per-article
    savepoints (see article_archive_savepoint_fix), but batched first
    since a table like tag_edges can have tens of thousands of rows per
    run, and looping row-by-row unconditionally would be a real
    performance regression for the common case where nothing fails.

    Returns the number of rows that failed to insert (0 = fully successful).
    """
    try:
        with conn.transaction():
            conn.cursor().executemany(sql, rows)
        return 0
    except Exception:
        failed = 0
        for row in rows:
            try:
                with conn.transaction():
                    conn.execute(sql, row)
            except Exception as exc:
                failed += 1
                log.warning(
                    "tag_tracking_insert_failed",
                    run_id=run_id,
                    table=table,
                    row=row,
                    error=repr(exc),
                )
        return failed


@dataclass
class EmergingTagAlert:
    tag: str
    count: int        # raw tag count this run
    rate: float        # count / this run's article_count
    mean: float          # historical mean rate for this tag
    std: float
    z_score: float

    def summary(self) -> str:
        return (
            f"{self.tag}: count={self.count} rate={self.rate:.4f} "
            f"(z={self.z_score:.1f}, mean={self.mean:.4f}±{self.std:.4f})"
        )


def record_tags(database_url: str, run_id: str, graph_data: dict[str, Any]) -> int:
    """
    Insert this run's tag graph into the tracking database, linked to
    run_id: per-tag counts, per-tag topics, and tag-pair co-occurrence
    edges. graph_data is tag_graph.build_graph_data()'s output — pass the
    same object you're about to write to tag_graph.json so the database and
    the JSON file always agree. Returns the number of rows across all
    three tables that failed to insert (0 = fully successful) — see
    _insert_rows_isolating_failures for why one bad row no longer loses
    every row in its table for this run.

    Assumes db.record_run(database_url, run_id, ...) has already been
    called this run, so the run_id foreign key exists.
    """
    now = datetime.now(UTC).isoformat()
    nodes = graph_data["nodes"]
    links = graph_data["links"]

    with get_connection(database_url) as conn:
        failed = 0
        failed += _insert_rows_isolating_failures(
            conn,
            "INSERT INTO tag_counts (run_id, created_at, tag, count) VALUES (%s, %s, %s, %s)",
            [(run_id, now, n["id"], n["count"]) for n in nodes],
            run_id,
            "tag_counts",
        )
        failed += _insert_rows_isolating_failures(
            conn,
            "INSERT INTO tag_topics (run_id, created_at, tag, topic) VALUES (%s, %s, %s, %s)",
            [(run_id, now, n["id"], topic) for n in nodes for topic in n["topics"]],
            run_id,
            "tag_topics",
        )
        failed += _insert_rows_isolating_failures(
            conn,
            "INSERT INTO tag_edges (run_id, created_at, tag_a, tag_b, weight) "
            "VALUES (%s, %s, %s, %s, %s)",
            [(run_id, now, link["source"], link["target"], link["weight"]) for link in links],
            run_id,
            "tag_edges",
        )
        conn.commit()
    return failed


def rebuild_graph_data(database_url: str, run_id: str) -> dict[str, Any]:
    """
    Reconstruct tag_graph.json's {"nodes": [...], "links": [...]} shape
    from the tracking database for a single run_id.
    """
    with get_connection(database_url) as conn:
        count_rows = conn.execute(
            "SELECT tag, count FROM tag_counts WHERE run_id = %s ORDER BY count DESC, tag ASC",
            (run_id,),
        ).fetchall()
        topic_rows = conn.execute(
            "SELECT tag, topic FROM tag_topics WHERE run_id = %s ORDER BY tag, topic",
            (run_id,),
        ).fetchall()
        edge_rows = conn.execute(
            "SELECT tag_a, tag_b, weight FROM tag_edges WHERE run_id = %s ORDER BY weight DESC",
            (run_id,),
        ).fetchall()

    topics_by_tag: dict[str, list[str]] = {}
    for tag, topic in topic_rows:
        topics_by_tag.setdefault(tag, []).append(topic)

    nodes = [
        {"id": tag, "count": count, "topics": topics_by_tag.get(tag, [])}
        for tag, count in count_rows
    ]
    links = [{"source": a, "target": b, "weight": w} for a, b, w in edge_rows]
    return {"nodes": nodes, "links": links}


def load_tag_rate_history(database_url: str) -> dict[str, list[float]]:
    """
    Return every tag's historical rate (tag count / that run's total
    article_count), oldest-first per tag. Does not include the current
    run — call this before record_tags().
    """
    with get_connection(database_url) as conn:
        rows = conn.execute(
            """
            SELECT tc.tag, tc.count, r.article_count
            FROM tag_counts tc
            JOIN runs r ON tc.run_id = r.run_id
            ORDER BY tc.tag, tc.created_at ASC
            """
        ).fetchall()

    history: dict[str, list[float]] = {}
    for tag, count, article_count in rows:
        rate = count / article_count if article_count else 0.0
        history.setdefault(tag, []).append(rate)
    return history


def check_emerging_tags(
    current_graph_data: dict[str, Any],
    current_article_count: int,
    history: dict[str, list[float]],
    z_score_threshold: float = 2.0,
) -> list[EmergingTagAlert]:
    """
    Flag tags whose rate this run is anomalously high relative to that
    tag's own historical rate (z-score against the tag's rolling mean/std).

    Tags with fewer than _MIN_HISTORY_RUNS prior runs (including brand-new
    tags) are skipped — see module docstring for why there's no absolute-
    threshold fallback here, unlike urgency.check_alerts.
    """
    if current_article_count == 0:
        return []

    alerts: list[EmergingTagAlert] = []
    for node in current_graph_data["nodes"]:
        tag = node["id"]
        count = node["count"]
        rate = count / current_article_count
        historical = history.get(tag, [])

        if len(historical) < _MIN_HISTORY_RUNS:
            continue

        mean = sum(historical) / len(historical)
        variance = sum((x - mean) ** 2 for x in historical) / len(historical)
        std = math.sqrt(variance)
        if std == 0:
            continue

        z = (rate - mean) / std
        if z >= z_score_threshold:
            alerts.append(EmergingTagAlert(
                tag=tag,
                count=count,
                rate=round(rate, 6),
                mean=round(mean, 6),
                std=round(std, 6),
                z_score=round(z, 2),
            ))

    return alerts


def record_emerging_tag_alerts(
    database_url: str, run_id: str, alerts: list[EmergingTagAlert]
) -> None:
    """
    Persist the emerging-tag alerts that fired this run, as an audit trail —
    "what was tag X's z-score on day N" is then answerable directly, without
    redoing the historical-window calculation. Only fired alerts are stored
    here, not every tag's rate/z-score every run; those remain always
    recomputable from tag_counts + runs.article_count via
    load_tag_rate_history(), so nothing is lost by not storing them all.

    Assumes db.record_run(database_url, run_id, ...) has already been
    called this run, so the run_id foreign key exists. A no-op if alerts is
    empty.
    """
    if not alerts:
        return

    now = datetime.now(UTC).isoformat()
    with get_connection(database_url) as conn:
        conn.cursor().executemany(
            "INSERT INTO emerging_tag_alerts "
            "(run_id, created_at, tag, count, rate, mean, std, z_score) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (run_id, now, a.tag, a.count, a.rate, a.mean, a.std, a.z_score)
                for a in alerts
            ],
        )
        conn.commit()


def record_bridge_tags(
    database_url: str, run_id: str, bridge_tags: list[dict[str, Any]]
) -> None:
    """
    Persist the bridge tags surfaced to the cross-topic synthesis prompt
    this run (tag_graph.find_bridge_tags()'s output: [{"tag", "topics",
    "count"}, ...]), as an audit trail — "which tags did we point the
    synthesis at on day N, and did the resulting Strategic Overview
    actually reflect them" is then answerable directly.

    Self-contained: stores each bridge tag's own topics rather than joining
    against tag_topics, since the two entry points call this at different
    points relative to record_tags() in their pipeline order.

    Assumes db.record_run(database_url, run_id, ...) has already been
    called this run. A no-op if bridge_tags is empty.
    """
    if not bridge_tags:
        return

    now = datetime.now(UTC).isoformat()
    with get_connection(database_url) as conn:
        conn.cursor().executemany(
            "INSERT INTO bridge_tags (run_id, created_at, tag, count, rank) "
            "VALUES (%s, %s, %s, %s, %s)",
            [
                (run_id, now, b["tag"], b["count"], rank)
                for rank, b in enumerate(bridge_tags, start=1)
            ],
        )
        conn.cursor().executemany(
            "INSERT INTO bridge_tag_topics (run_id, created_at, tag, topic) "
            "VALUES (%s, %s, %s, %s)",
            [
                (run_id, now, b["tag"], topic)
                for b in bridge_tags
                for topic in b["topics"]
            ],
        )
        conn.commit()


def record_community_summaries(
    database_url: str,
    run_id: str,
    community_summaries: dict[int, dict[str, Any]],
) -> int:
    """
    Persist this run's LLM-written community summaries
    (pipeline.summarize_communities()'s output: {community_id: {"label",
    "tags", "summary", "article_count"}}), linked to run_id. Returns the
    number of rows across both tables that failed to insert (0 = fully
    successful, including the community_summaries-is-empty no-op case) --
    see _insert_rows_isolating_failures.

    Self-contained: stores each community's own member tags rather than
    reconstructing them from tag_counts, since Louvain community
    membership (build_display_graph) is never itself persisted.

    Assumes db.record_run(database_url, run_id, ...) has already been
    called this run. A no-op if community_summaries is empty.
    """
    if not community_summaries:
        return 0

    now = datetime.now(UTC).isoformat()
    with get_connection(database_url) as conn:
        failed = 0
        failed += _insert_rows_isolating_failures(
            conn,
            "INSERT INTO community_summaries "
            "(run_id, created_at, community_id, label, summary, article_count) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (run_id, now, comm_id, info["label"], info["summary"], info["article_count"])
                for comm_id, info in community_summaries.items()
            ],
            run_id,
            "community_summaries",
        )
        failed += _insert_rows_isolating_failures(
            conn,
            "INSERT INTO community_summary_tags (run_id, created_at, community_id, tag) "
            "VALUES (%s, %s, %s, %s)",
            [
                (run_id, now, comm_id, tag)
                for comm_id, info in community_summaries.items()
                for tag in info["tags"]
            ],
            run_id,
            "community_summary_tags",
        )
        conn.commit()
    return failed
