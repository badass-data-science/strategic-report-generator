"""
Tests for strategic_reports.daily.core.overview_archive.

Covers:
  - record_overview persists one row per bullet, in order
  - multiple runs kept separate
"""

from strategic_reports.daily.core.db import get_connection, record_run
from strategic_reports.daily.core.overview_archive import record_overview


def _bullets_for_run(database_url: str, run_id: str) -> list[str]:
    with get_connection(database_url) as conn:
        rows = conn.execute(
            "SELECT bullet_text FROM cross_topic_overviews WHERE run_id = %s "
            "ORDER BY bullet_index",
            (run_id,),
        ).fetchall()
    return [r[0] for r in rows]


class TestRecordOverview:
    def test_round_trip_bullet_order_preserved(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=10)
        bullets = ["First cross-cutting theme.", "Second theme.", "Third theme."]
        record_overview(database_url, "run-0", bullets)
        assert _bullets_for_run(database_url, "run-0") == bullets

    def test_two_runs_kept_separate(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=10)
        record_overview(database_url, "run-0", ["Day 1 bullet."])
        record_run(database_url, "run-1", article_count=12)
        record_overview(database_url, "run-1", ["Day 2 bullet A.", "Day 2 bullet B."])

        assert _bullets_for_run(database_url, "run-0") == ["Day 1 bullet."]
        assert _bullets_for_run(database_url, "run-1") == ["Day 2 bullet A.", "Day 2 bullet B."]

    def test_empty_bullets_is_a_noop(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=10)
        record_overview(database_url, "run-0", [])
        assert _bullets_for_run(database_url, "run-0") == []
