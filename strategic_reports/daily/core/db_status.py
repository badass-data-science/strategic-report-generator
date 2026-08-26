"""
Operational health report on the tracking database itself — distinct from
every other report in this package, which reports on the *news* (a topic's
strategic bullets, urgency scores, cross-topic overview). This one reports
on the *pipeline*: is it running on schedule, and did each run actually
persist what it should have.

Motivated directly by the empty-archive class of bug fixed in
article_archive_savepoint_fix (see project memory / CHANGELOG): two real
runs registered a nonzero runs.article_count but silently persisted zero
rows to articles/article_tags, because one bad row aborted the whole
transaction. insert_rows_isolating_failures (db.py) fixes the root cause
going forward, but gives no visibility into whether it's needed again, or
into the other half of that failure mode — a run simply not happening on
schedule. load_db_status() is a read-only query against tables every other
module in this package already writes; it adds no new persistence.

Kept as its own report (a `db status` CLI command, see cli.py) rather than
a section of the daily HTML report: this is for the operator, not the
reader, checked on demand or from a separate monitoring cron — not
regenerated as a side effect of every daily run.
"""

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg

from .db import get_connection

# Tables whose row count is checked per run, alongside runs.article_count.
# Each is a distinct persistence step in the pipeline (see article_archive.py,
# tag_tracking.py, urgency.py, bullet_diff.py, overview_archive.py) that can
# independently succeed or fail for a given run_id.
_TRACKED_TABLES = [
    "articles",
    "tag_counts",
    "urgency_scores",
    "bullets",
    "community_summaries",
    "cross_topic_overviews",
]


@dataclass
class RunHealth:
    run_id: str
    created_at: str
    article_count: int
    table_rows: dict[str, int]  # table name -> row count for this run_id
    flags: list[str] = field(default_factory=list)


@dataclass
class RunGap:
    after_run_id: str
    before_run_id: str
    hours: float


@dataclass
class DbStatusReport:
    total_runs: int
    first_run_at: str | None
    last_run_at: str | None
    stale: bool
    stale_after_hours: float
    gaps: list[RunGap]
    runs: list[RunHealth]  # most recent first


def _row_counts_by_run(
    conn: psycopg.Connection, table: str, run_ids: list[str]
) -> dict[str, int]:
    if not run_ids:
        return {}
    rows = conn.execute(
        f"SELECT run_id, count(*) FROM {table} WHERE run_id = ANY(%s) GROUP BY run_id",
        (run_ids,),
    ).fetchall()
    return dict(rows)


def _flag_run(article_count: int, table_rows: dict[str, int]) -> list[str]:
    """
    Flag a run whose runs.article_count says articles were considered but a
    downstream table that should have gotten rows from them is empty — the
    exact shape of the bug insert_rows_isolating_failures now guards
    against (one bad row silently discarding an entire table for the run).
    A run with article_count == 0 is flagged separately and less
    severely: that's a genuinely quiet news day, not necessarily a bug.
    """
    flags = []
    if article_count == 0:
        flags.append("zero_articles_considered")
        return flags
    for table in _TRACKED_TABLES:
        if table_rows.get(table, 0) == 0:
            flags.append(f"empty_{table}")
    return flags


def load_db_status(
    database_url: str,
    recent_runs: int = 20,
    stale_after_hours: float = 36.0,
) -> DbStatusReport:
    """
    Query the tracking database for run cadence and per-run persistence
    health.

    Gap and staleness detection walk every run's created_at (cheap — two
    columns, no joins) so a gap far outside the `recent_runs` detail window
    is never missed. Per-table row counts (the empty_<table> flags) are
    only computed for the most recent `recent_runs` runs, since each is a
    join against a potentially large table — bounding that work is the
    reason `recent_runs` exists.
    """
    with get_connection(database_url) as conn:
        all_runs = conn.execute(
            "SELECT run_id, created_at FROM runs ORDER BY created_at ASC"
        ).fetchall()

        gaps: list[RunGap] = []
        for (run_a, ts_a), (run_b, ts_b) in zip(all_runs, all_runs[1:]):
            delta_hours = (
                datetime.fromisoformat(ts_b) - datetime.fromisoformat(ts_a)
            ).total_seconds() / 3600
            if delta_hours > stale_after_hours:
                gaps.append(RunGap(after_run_id=run_a, before_run_id=run_b, hours=delta_hours))

        last_run_at = all_runs[-1][1] if all_runs else None
        stale = last_run_at is not None and (
            (datetime.now(UTC) - datetime.fromisoformat(last_run_at)).total_seconds() / 3600
            > stale_after_hours
        )

        recent = conn.execute(
            "SELECT run_id, created_at, article_count FROM runs "
            "ORDER BY created_at DESC LIMIT %s",
            (recent_runs,),
        ).fetchall()
        recent_run_ids = [r[0] for r in recent]

        rows_by_table = {
            table: _row_counts_by_run(conn, table, recent_run_ids) for table in _TRACKED_TABLES
        }

    runs = []
    for run_id, created_at, article_count in recent:
        table_rows = {table: rows_by_table[table].get(run_id, 0) for table in _TRACKED_TABLES}
        runs.append(
            RunHealth(
                run_id=run_id,
                created_at=created_at,
                article_count=article_count,
                table_rows=table_rows,
                flags=_flag_run(article_count, table_rows),
            )
        )

    return DbStatusReport(
        total_runs=len(all_runs),
        first_run_at=all_runs[0][1] if all_runs else None,
        last_run_at=last_run_at,
        stale=stale,
        stale_after_hours=stale_after_hours,
        gaps=gaps,
        runs=runs,
    )


def db_status_as_dict(report: DbStatusReport) -> dict[str, Any]:
    """
    Convert a DbStatusReport to a plain, JSON-serializable dict — every
    field is already a str/int/float/bool/list/dict/None, so this is a
    direct dataclasses.asdict() with no reshaping. Named/exported
    separately (rather than calling asdict() inline at each call site) so
    the `db status --json` CLI output and any other consumer serialize the
    report identically.
    """
    return asdict(report)
