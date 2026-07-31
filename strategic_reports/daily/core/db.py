"""
SQLite tracking database — schema and connection helper shared by the CLI
and the Prefect flow.

Both entry points wipe and recreate --output-dir on every run (see
renderer.render_report), but the tracking database (urgency scores, bullet
history, and future cross-run trackers) must survive across runs. Keeping
db_path management entirely separate from output_dir means a run can never
delete its own history by accident.

SCHEMA NOTES
------------
Every row carries both a run_id (join key back to a specific run) and its
own created_at timestamp (ISO 8601, UTC) — not just a date — so change
tracking (z-score baselines, diffing, future drift-detection queries) has a
precise, orderable time axis rather than relying on row-insertion order or
comparing opaque run_id strings.

Unlike the JSON files this replaces (bullet_history.json capped itself at
the last 7 entries to bound file size), nothing here is pruned. SQLite has
no equivalent file-size pressure, and full history is more useful, not less
— several ideas discussed for this project (emerging-tag detection,
community drift) want as much history as they can get.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    article_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS urgency_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL,
    topic TEXT NOT NULL,
    score REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_urgency_scores_topic ON urgency_scores(topic, created_at);

CREATE TABLE IF NOT EXISTS bullets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL,
    topic TEXT NOT NULL,
    bullet_index INTEGER NOT NULL,
    bullet_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bullets_topic ON bullets(topic, created_at);
"""


def ensure_safe_db_path(db_path: Path, output_dir: Path) -> None:
    """
    Raise ValueError if db_path sits inside output_dir.

    output_dir is deleted and recreated by render_report() on every run. A
    db_path nested inside it (or equal to it) would be destroyed the moment
    the next run starts, silently erasing all accumulated history.
    """
    db_resolved = db_path.resolve()
    output_resolved = output_dir.resolve()
    if db_resolved == output_resolved or output_resolved in db_resolved.parents:
        raise ValueError(
            f"--db-path ({db_path}) is inside --output-dir ({output_dir}), "
            f"which is deleted and recreated on every run. Choose a --db-path "
            f"outside of --output-dir."
        )


def connect(db_path: Path) -> sqlite3.Connection:
    """
    Open the tracking database, creating it (and any missing parent
    directories) on first use. Never truncates or deletes an existing
    database file — that's what makes cross-run history possible.

    Runs the schema on every connect via CREATE TABLE/INDEX IF NOT EXISTS,
    so a fresh database gets its tables and an existing one is left alone.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def record_run(db_path: Path, run_id: str, article_count: int) -> None:
    """
    Register a pipeline run in the runs table, with the total number of
    articles considered across all topics that run.

    article_count is the denominator any cross-run tag-weight comparison
    needs: a tag's raw co-occurrence count is meaningless on its own — a
    count of 20 means something different on a 400-article news day than a
    50-article one. Dividing by this run's article_count turns raw counts
    into a comparable rate.

    Call once per run, before any urgency/bullet-history inserts that
    reference run_id — those don't create the runs row themselves.
    """
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO runs (run_id, created_at, article_count) VALUES (?, ?, ?)",
            (run_id, datetime.now(timezone.utc).isoformat(), article_count),
        )
        conn.commit()
    finally:
        conn.close()
