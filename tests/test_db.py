"""
Tests for strategic_reports.daily.core.db: pooled connections, the
reachability fail-fast check, and run registration.
"""

import pytest
from strategic_reports.daily.core.db import ensure_database_reachable, get_connection, record_run


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
