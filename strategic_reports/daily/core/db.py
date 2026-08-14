"""
PostgreSQL tracking database — pooled connection helper and run registration.

Both entry points (cli.py, flows/daily_report.py) wipe and recreate
--output-dir on every run (see renderer.render_report), but the tracking
database (urgency scores, bullet history, and other cross-run trackers)
must survive across runs. Schema ownership lives in alembic/ (run `alembic
upgrade head`, or `strategic-reports db upgrade`) — not here. Unlike the
old SQLite version, connecting never implicitly creates or updates tables;
running DDL on every pooled-connection acquisition is expensive and
lock-prone on Postgres, whereas it was free on SQLite.

CONNECTION POOLING
-------------------
Every DB-touching function in this package takes database_url: str, not a
live connection — the same picklability requirement the old db_path: Path
signatures had, since a shared connection can't cross a Prefect task
boundary (it isn't picklable). A plain connection string has the same
picklability, so the calling convention is unchanged; only the type is
(Path -> str).

Each process keeps its own small pool per database_url (see _get_pool),
built lazily on first use and reused by every subsequent call in that
process — this avoids paying a full TCP+auth handshake on every call, which
was free with SQLite's on-disk connections but isn't with Postgres.
get_connection() hands back a single pooled connection as a context
manager; callers keep the same "with ... as conn: ...; conn.commit()"
shape as before.
"""

from contextlib import AbstractContextManager
from datetime import UTC, datetime

import psycopg
from psycopg_pool import ConnectionPool

_pools: dict[str, ConnectionPool] = {}


def _get_pool(database_url: str) -> ConnectionPool:
    """
    Return this process's connection pool for database_url, creating it on
    first use. min_size=1/max_size=5 is sized for a sequential daily batch
    job (one topic/task at a time), not a concurrent web server.
    """
    pool = _pools.get(database_url)
    if pool is None:
        pool = ConnectionPool(database_url, min_size=1, max_size=5, open=True)
        _pools[database_url] = pool
    return pool


def get_connection(database_url: str) -> AbstractContextManager[psycopg.Connection]:
    """
    Context manager yielding a pooled connection: `with get_connection(url) as conn:`.

    Every call site still ends with an explicit conn.commit(), matching the
    explicit-commit style every DB-touching function already used under
    SQLite — psycopg_pool's connection() context manager would auto-commit
    on clean exit anyway, so this is a harmless no-op in the success case,
    not a second real commit.
    """
    return _get_pool(database_url).connection()


def ensure_database_reachable(database_url: str, connect_timeout: int = 5) -> None:
    """
    Fail fast if database_url isn't reachable, before doing any real work.

    Replaces the old ensure_safe_db_path() + connect_db(db_path).close()
    pair, whose job was catching an unusable SQLite path (schema creation
    on first use, output_dir collision) before a run's expensive LLM calls.
    That specific risk (a wiped-and-recreated output_dir colliding with the
    tracking db) was SQLite-file-specific and has no Postgres-URL analog;
    an unreachable/misconfigured database is the equivalent failure mode
    here, and schema is no longer created implicitly — see module docstring.

    Deliberately bypasses the pool (a direct, short-lived psycopg.connect())
    rather than going through get_connection(): a bad database_url would
    otherwise make the pool retry with backoff for its own timeout (default
    30s, several retries) before surfacing a failure, which is far too slow
    for a startup check meant to fail fast.
    """
    with psycopg.connect(database_url, connect_timeout=connect_timeout) as conn:
        conn.execute("SELECT 1")


def record_run(database_url: str, run_id: str, article_count: int) -> None:
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
    with get_connection(database_url) as conn:
        conn.execute(
            "INSERT INTO runs (run_id, created_at, article_count) VALUES (%s, %s, %s) "
            "ON CONFLICT (run_id) DO NOTHING",
            (run_id, datetime.now(UTC).isoformat(), article_count),
        )
        conn.commit()
