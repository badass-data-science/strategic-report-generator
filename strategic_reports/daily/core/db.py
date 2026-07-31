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

-- The three tables below let a given run's tag_graph.json be reconstructed
-- from the database (see tag_tracking.rebuild_graph_data) and let a tag's
-- rate (count / that run's article_count) be tracked over time for
-- emerging-tag z-score alerts (see tag_tracking.check_emerging_tags).

CREATE TABLE IF NOT EXISTS tag_counts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL,
    tag TEXT NOT NULL,
    count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tag_counts_tag ON tag_counts(tag, created_at);
CREATE INDEX IF NOT EXISTS idx_tag_counts_run ON tag_counts(run_id);

CREATE TABLE IF NOT EXISTS tag_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL,
    tag TEXT NOT NULL,
    topic TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tag_topics_run_tag ON tag_topics(run_id, tag);

CREATE TABLE IF NOT EXISTS tag_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL,
    tag_a TEXT NOT NULL,
    tag_b TEXT NOT NULL,
    weight INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tag_edges_run ON tag_edges(run_id);

-- Audit trail of emerging-tag alerts that actually fired (not every tag's
-- rate/z-score every run — those are always recomputable from tag_counts +
-- runs.article_count via tag_tracking.load_tag_rate_history). Lets "what
-- was tag X's z-score on day N" be answered without redoing the historical
-- window calculation.
CREATE TABLE IF NOT EXISTS emerging_tag_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL,
    tag TEXT NOT NULL,
    count INTEGER NOT NULL,
    rate REAL NOT NULL,
    mean REAL NOT NULL,
    std REAL NOT NULL,
    z_score REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emerging_tag_alerts_tag ON emerging_tag_alerts(tag, created_at);
CREATE INDEX IF NOT EXISTS idx_emerging_tag_alerts_run ON emerging_tag_alerts(run_id);

-- Audit trail of the bridge tags actually surfaced to the cross-topic
-- synthesis prompt each run (see tag_graph.find_bridge_tags and
-- pipeline.synthesize_cross_topic). Self-contained — stores its own
-- topics rather than joining against tag_topics — because the two entry
-- points call the emerging-tag block (which persists this) at different
-- points relative to cross-topic synthesis in their pipeline order, so a
-- join can't be relied on to already have this run_id's tag_topics rows.
CREATE TABLE IF NOT EXISTS bridge_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL,
    tag TEXT NOT NULL,
    count INTEGER NOT NULL,
    rank INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bridge_tags_run ON bridge_tags(run_id);
CREATE INDEX IF NOT EXISTS idx_bridge_tags_tag ON bridge_tags(tag, created_at);

CREATE TABLE IF NOT EXISTS bridge_tag_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL,
    tag TEXT NOT NULL,
    topic TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bridge_tag_topics_run_tag ON bridge_tag_topics(run_id, tag);
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
