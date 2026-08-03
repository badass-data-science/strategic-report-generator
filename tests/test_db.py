"""
Tests for strategic_reports.daily.core.db: the output_dir/db_path safety
guard, connection/schema creation, and run registration.
"""

from pathlib import Path

import pytest
from strategic_reports.daily.core.db import connect, ensure_safe_db_path, record_run


class TestEnsureSafeDbPath:
    def test_raises_when_db_path_inside_output_dir(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        db_path = output_dir / "tracking.db"
        with pytest.raises(ValueError):
            ensure_safe_db_path(db_path, output_dir)

    def test_raises_when_db_path_equals_output_dir(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        with pytest.raises(ValueError):
            ensure_safe_db_path(output_dir, output_dir)

    def test_raises_when_db_path_deeply_nested(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        db_path = output_dir / "nested" / "deeper" / "tracking.db"
        with pytest.raises(ValueError):
            ensure_safe_db_path(db_path, output_dir)

    def test_allows_sibling_path(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        db_path = tmp_path / "db" / "tracking.db"
        ensure_safe_db_path(db_path, output_dir)  # must not raise

    def test_allows_unrelated_path(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        db_path = tmp_path / "elsewhere" / "tracking.db"
        ensure_safe_db_path(db_path, output_dir)  # must not raise


class TestConnect:
    def test_creates_file_and_parent_dirs(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nested" / "tracking.db"
        assert not db_path.exists()
        conn = connect(db_path)
        conn.close()
        assert db_path.exists()

    def test_creates_schema(self, tmp_path: Path) -> None:
        db_path = tmp_path / "tracking.db"
        conn = connect(db_path)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
        assert {"runs", "urgency_scores", "bullets"} <= tables

    def test_does_not_wipe_existing_data(self, tmp_path: Path) -> None:
        db_path = tmp_path / "tracking.db"
        conn = connect(db_path)
        conn.execute("INSERT INTO runs (run_id, created_at, article_count) VALUES ('r1', 'now', 5)")
        conn.commit()
        conn.close()

        conn2 = connect(db_path)
        row = conn2.execute("SELECT article_count FROM runs WHERE run_id = 'r1'").fetchone()
        conn2.close()
        assert row == (5,)


class TestRecordRun:
    def test_inserts_run_with_article_count(self, tmp_path: Path) -> None:
        db_path = tmp_path / "tracking.db"
        record_run(db_path, "run-1", article_count=42)

        conn = connect(db_path)
        row = conn.execute(
            "SELECT run_id, article_count FROM runs WHERE run_id = 'run-1'"
        ).fetchone()
        conn.close()
        assert row == ("run-1", 42)

    def test_records_created_at_timestamp(self, tmp_path: Path) -> None:
        db_path = tmp_path / "tracking.db"
        record_run(db_path, "run-1", article_count=1)

        conn = connect(db_path)
        row = conn.execute("SELECT created_at FROM runs WHERE run_id = 'run-1'").fetchone()
        conn.close()
        assert row is not None
        assert row[0]  # non-empty timestamp string

    def test_idempotent_on_duplicate_run_id(self, tmp_path: Path) -> None:
        """A second record_run() call for the same run_id is a no-op (INSERT OR IGNORE)."""
        db_path = tmp_path / "tracking.db"
        record_run(db_path, "run-1", article_count=10)
        record_run(db_path, "run-1", article_count=999)  # should not overwrite

        conn = connect(db_path)
        rows = conn.execute("SELECT article_count FROM runs WHERE run_id = 'run-1'").fetchall()
        conn.close()
        assert rows == [(10,)]
