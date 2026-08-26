"""
Tests for strategic_reports.daily.core.db_status: tracking-database run
cadence and per-run persistence health.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from strategic_reports.daily.core.db import get_connection
from strategic_reports.daily.core.db_status import db_status_as_dict, load_db_status


def _insert_run(database_url: str, run_id: str, created_at: datetime, article_count: int) -> None:
    with get_connection(database_url) as conn:
        conn.execute(
            "INSERT INTO runs (run_id, created_at, article_count) VALUES (%s, %s, %s)",
            (run_id, created_at.isoformat(), article_count),
        )
        conn.commit()


def _insert_rows(database_url: str, table: str, run_id: str, count: int) -> None:
    """Insert `count` minimal rows into one of the tracked tables for run_id."""
    now = datetime.now(UTC).isoformat()
    inserts: dict[str, tuple[str, tuple[Any, ...]]] = {
        "articles": (
            "INSERT INTO articles (run_id, created_at, topic, title, link, publish_date) "
            "VALUES (%s, %s, 'Topic', 'Title', 'https://example.com', %s)",
            (run_id, now, now),
        ),
        "tag_counts": (
            "INSERT INTO tag_counts (run_id, created_at, tag, count) VALUES (%s, %s, 'tag', 1)",
            (run_id, now),
        ),
        "urgency_scores": (
            "INSERT INTO urgency_scores (run_id, created_at, topic, score) "
            "VALUES (%s, %s, 'Topic', 0.5)",
            (run_id, now),
        ),
        "bullets": (
            "INSERT INTO bullets (run_id, created_at, topic, bullet_index, bullet_text) "
            "VALUES (%s, %s, 'Topic', 0, 'Bullet')",
            (run_id, now),
        ),
        "community_summaries": (
            "INSERT INTO community_summaries "
            "(run_id, created_at, community_id, label, summary, article_count) "
            "VALUES (%s, %s, 0, 'Label', 'Summary', 1)",
            (run_id, now),
        ),
        "cross_topic_overviews": (
            "INSERT INTO cross_topic_overviews (run_id, created_at, bullet_index, bullet_text) "
            "VALUES (%s, %s, 0, 'Bullet')",
            (run_id, now),
        ),
    }
    sql, params = inserts[table]
    with get_connection(database_url) as conn:
        for _ in range(count):
            conn.execute(sql, params)
        conn.commit()


class TestLoadDbStatusEmpty:
    def test_no_runs_reports_zero_and_no_anomalies(self, database_url: str) -> None:
        report = load_db_status(database_url)
        assert report.total_runs == 0
        assert report.first_run_at is None
        assert report.last_run_at is None
        assert report.stale is False
        assert report.gaps == []
        assert report.runs == []


class TestLoadDbStatusHealthyRun:
    def test_fully_persisted_run_has_no_flags(self, database_url: str) -> None:
        _insert_run(database_url, "run-1", datetime.now(UTC), article_count=2)
        for table in [
            "articles", "tag_counts", "urgency_scores", "bullets",
            "community_summaries", "cross_topic_overviews",
        ]:
            _insert_rows(database_url, table, "run-1", count=2)

        report = load_db_status(database_url)

        assert report.total_runs == 1
        run = report.runs[0]
        assert run.run_id == "run-1"
        assert run.flags == []
        assert run.table_rows["articles"] == 2


class TestLoadDbStatusEmptyArchiveBug:
    """
    The article_archive_savepoint_fix scenario: article_count says articles
    were considered, but the articles (and every other derived) table has
    zero rows for that run_id.
    """

    def test_flags_empty_derived_tables(self, database_url: str) -> None:
        _insert_run(database_url, "run-1", datetime.now(UTC), article_count=5)

        report = load_db_status(database_url)

        run = report.runs[0]
        assert "empty_articles" in run.flags
        assert "empty_tag_counts" in run.flags
        assert "empty_urgency_scores" in run.flags
        assert "zero_articles_considered" not in run.flags

    def test_partially_persisted_run_only_flags_the_empty_tables(
        self, database_url: str
    ) -> None:
        _insert_run(database_url, "run-1", datetime.now(UTC), article_count=5)
        _insert_rows(database_url, "tag_counts", "run-1", count=5)

        report = load_db_status(database_url)

        run = report.runs[0]
        assert "empty_tag_counts" not in run.flags
        assert "empty_articles" in run.flags


class TestLoadDbStatusZeroArticleRun:
    def test_zero_article_count_flags_separately_and_skips_table_flags(
        self, database_url: str
    ) -> None:
        _insert_run(database_url, "run-1", datetime.now(UTC), article_count=0)

        report = load_db_status(database_url)

        run = report.runs[0]
        assert run.flags == ["zero_articles_considered"]


class TestLoadDbStatusGapsAndStaleness:
    def test_detects_a_gap_over_the_threshold(self, database_url: str) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        _insert_run(database_url, "run-1", base, article_count=1)
        _insert_run(database_url, "run-2", base + timedelta(hours=48), article_count=1)

        report = load_db_status(database_url, stale_after_hours=36.0)

        assert len(report.gaps) == 1
        gap = report.gaps[0]
        assert gap.after_run_id == "run-1"
        assert gap.before_run_id == "run-2"
        assert gap.hours == 48.0

    def test_no_gap_reported_within_threshold(self, database_url: str) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        _insert_run(database_url, "run-1", base, article_count=1)
        _insert_run(database_url, "run-2", base + timedelta(hours=24), article_count=1)

        report = load_db_status(database_url, stale_after_hours=36.0)

        assert report.gaps == []

    def test_stale_when_last_run_older_than_threshold(self, database_url: str) -> None:
        _insert_run(
            database_url, "run-1", datetime.now(UTC) - timedelta(hours=48), article_count=1
        )

        report = load_db_status(database_url, stale_after_hours=36.0)

        assert report.stale is True

    def test_not_stale_when_last_run_recent(self, database_url: str) -> None:
        _insert_run(database_url, "run-1", datetime.now(UTC), article_count=1)

        report = load_db_status(database_url, stale_after_hours=36.0)

        assert report.stale is False


class TestLoadDbStatusRecentRunsLimit:
    def test_limits_detailed_runs_but_not_total_count(self, database_url: str) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(5):
            _insert_run(database_url, f"run-{i}", base + timedelta(days=i), article_count=1)

        report = load_db_status(database_url, recent_runs=2)

        assert report.total_runs == 5
        assert len(report.runs) == 2
        # Most recent first.
        assert [r.run_id for r in report.runs] == ["run-4", "run-3"]


class TestDbStatusAsDict:
    def test_round_trips_through_json(self, database_url: str) -> None:
        _insert_run(database_url, "run-1", datetime.now(UTC), article_count=5)
        report = load_db_status(database_url)

        payload = json.loads(json.dumps(db_status_as_dict(report)))

        assert payload["total_runs"] == 1
        assert payload["runs"][0]["run_id"] == "run-1"
        assert "empty_articles" in payload["runs"][0]["flags"]

    def test_empty_report_serializes_with_null_timestamps(self, database_url: str) -> None:
        report = load_db_status(database_url)

        payload = json.loads(json.dumps(db_status_as_dict(report)))

        assert payload == {
            "total_runs": 0,
            "first_run_at": None,
            "last_run_at": None,
            "stale": False,
            "stale_after_hours": 36.0,
            "gaps": [],
            "runs": [],
        }
