"""
Persists each run's cross-topic synthesis overview into the tracking
database, linked to run_id.

pipeline.synthesize_cross_topic() produces this narrative, but it's
otherwise rendered only into index.html and lost the moment output_dir is
wiped on the next run (see renderer.render_report). record_overview()
gives it the same durability as articles, bullets, and community
summaries — the source rdf_export.py reads to build the RDF knowledge
graph.
"""

from datetime import UTC, datetime
from pathlib import Path

from .db import connect


def record_overview(db_path: Path, run_id: str, bullets: list[str]) -> None:
    """Insert this run's cross-topic synthesis bullets into the database."""
    now = datetime.now(UTC).isoformat()
    conn = connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO cross_topic_overviews "
            "(run_id, created_at, bullet_index, bullet_text) VALUES (?, ?, ?, ?)",
            [(run_id, now, i, bullet) for i, bullet in enumerate(bullets)],
        )
        conn.commit()
    finally:
        conn.close()
