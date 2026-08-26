"""
Tests for strategic_reports.daily.core.db: pooled connections, the
reachability fail-fast check, and run registration.
"""

import pytest
from strategic_reports.daily.core.db import (
    ensure_database_reachable,
    get_connection,
    insert_rows_isolating_failures,
    record_run,
)

_INSERT_URGENCY_SCORE = (
    "INSERT INTO urgency_scores (run_id, created_at, topic, score) VALUES (%s, %s, %s, %s)"
)


class TestEnsureDatabaseReachable:
    def test_succeeds_against_reachable_database(self, database_url: str) -> None:
        ensure_database_reachable(database_url)  # must not raise

    def test_raises_against_unreachable_database(self) -> None:
        with pytest.raises(Exception):
            ensure_database_reachable(
                "postgresql://nope:nope@localhost:1/nope", connect_timeout=1
            )


class TestGetConnection:
    def test_yields_a_working_connection(self, database_url: str) -> None:
        with get_connection(database_url) as conn:
            row = conn.execute("SELECT 1").fetchone()
        assert row == (1,)

    def test_reuses_the_same_pool_across_calls(self, database_url: str) -> None:
        """Two calls with the same database_url don't error — the pool is cached, not rebuilt."""
        with get_connection(database_url) as conn:
            conn.execute("SELECT 1")
        with get_connection(database_url) as conn:
            conn.execute("SELECT 1")


class TestRecordRun:
    def test_inserts_run_with_article_count(self, database_url: str) -> None:
        record_run(database_url, "run-1", article_count=42)

        with get_connection(database_url) as conn:
            row = conn.execute(
                "SELECT run_id, article_count FROM runs WHERE run_id = %s", ("run-1",)
            ).fetchone()
        assert row == ("run-1", 42)

    def test_records_created_at_timestamp(self, database_url: str) -> None:
        record_run(database_url, "run-1", article_count=1)

        with get_connection(database_url) as conn:
            row = conn.execute(
                "SELECT created_at FROM runs WHERE run_id = %s", ("run-1",)
            ).fetchone()
        assert row is not None
        assert row[0]  # non-empty timestamp string

    def test_idempotent_on_duplicate_run_id(self, database_url: str) -> None:
        """A second record_run() call for the same run_id is a no-op (ON CONFLICT DO NOTHING)."""
        record_run(database_url, "run-1", article_count=10)
        record_run(database_url, "run-1", article_count=999)  # should not overwrite

        with get_connection(database_url) as conn:
            rows = conn.execute(
                "SELECT article_count FROM runs WHERE run_id = %s", ("run-1",)
            ).fetchall()
        assert rows == [(10,)]


class TestInsertRowsIsolatingFailures:
    """
    Shared savepoint-isolation helper used by record_articles, record_tags,
    record_community_summaries, urgency.append_run, and
    bullet_diff.append_bullet_run. Exercised here directly against
    urgency_scores (any tracking-db table with the right shape would do).
    """

    def test_returns_zero_and_inserts_all_rows_on_success(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=0)
        rows = [
            ("run-0", "2026-01-01T00:00:00", "AI", 0.1),
            ("run-0", "2026-01-01T00:00:00", "Defense", 0.2),
        ]
        with get_connection(database_url) as conn:
            failed = insert_rows_isolating_failures(
                conn, _INSERT_URGENCY_SCORE, rows, "run-0", "urgency_scores"
            )
            conn.commit()

        assert failed == 0
        with get_connection(database_url) as conn:
            count = conn.execute(
                "SELECT count(*) FROM urgency_scores WHERE run_id = %s", ("run-0",)
            ).fetchone()
        assert count == (2,)

    def test_one_bad_row_does_not_lose_the_others(self, database_url: str) -> None:
        # A NUL byte is valid in a Python str but Postgres text columns
        # reject it outright -- forces exactly one bad row without mocking.
        record_run(database_url, "run-0", article_count=0)
        rows = [
            ("run-0", "2026-01-01T00:00:00", "Good A", 0.1),
            ("run-0", "2026-01-01T00:00:00", "Bad \x00 Topic", 0.5),
            ("run-0", "2026-01-01T00:00:00", "Good B", 0.2),
        ]
        with get_connection(database_url) as conn:
            failed = insert_rows_isolating_failures(
                conn, _INSERT_URGENCY_SCORE, rows, "run-0", "urgency_scores"
            )
            conn.commit()

        assert failed == 1
        with get_connection(database_url) as conn:
            topics = {
                row[0]
                for row in conn.execute(
                    "SELECT topic FROM urgency_scores WHERE run_id = %s", ("run-0",)
                ).fetchall()
            }
        assert topics == {"Good A", "Good B"}

    def test_empty_rows_is_a_noop(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=0)
        with get_connection(database_url) as conn:
            failed = insert_rows_isolating_failures(
                conn, _INSERT_URGENCY_SCORE, [], "run-0", "urgency_scores"
            )
            conn.commit()
        assert failed == 0
