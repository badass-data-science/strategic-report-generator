"""
Historical bullet diffing between consecutive pipeline runs — PostgreSQL-backed.

Stores per-topic strategic bullets from each run and uses an LLM to
semantically classify changes: new insights, continued insights, and
insights that dropped out since yesterday.

Call order per run (same pattern as urgency.py):
  0. db.record_run(database_url, run_id, article_count) — once per run,
     before any of the below (creates the run_id foreign key both tables use)
  1. load_bullet_history   — reads only; gives yesterday's bullets
  2. diff_all_topics       — compares today against yesterday
  3. append_bullet_run     — writes today into the db for future runs

load_bullet_history/append_bullet_run take database_url rather than a live
connection: each call opens its own pooled connection (see
db.get_connection). This keeps the functions safe to call from Prefect
tasks, where a shared connection can't be passed between tasks (it isn't
picklable).
"""

import asyncio
from datetime import UTC, datetime

import structlog

from .db import get_connection
from .llm_client import LLMClient
from .models import BulletDiff, TopicResult
from .prompts import SYSTEM_DIFF, build_diff_prompt

log = structlog.get_logger(__name__)


def load_bullet_history(database_url: str) -> dict[str, list[str]]:
    """
    Return the most recent prior run's bullets, keyed by topic — this is
    "yesterday" relative to a run about to be appended. Empty dict if no
    prior run exists yet (skip the diff on the very first run).
    """
    with get_connection(database_url) as conn:
        latest = conn.execute(
            "SELECT run_id FROM bullets ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if latest is None:
            return {}
        latest_run_id = latest[0]

        rows = conn.execute(
            "SELECT topic, bullet_text FROM bullets WHERE run_id = %s ORDER BY topic, bullet_index",
            (latest_run_id,),
        ).fetchall()

    yesterday: dict[str, list[str]] = {}
    for topic, bullet_text in rows:
        yesterday.setdefault(topic, []).append(bullet_text)
    return yesterday


def append_bullet_run(
    database_url: str,
    results: list[TopicResult],
    run_id: str,
) -> None:
    """
    Insert today's strategic bullets into the database.

    Assumes db.record_run(database_url, run_id, ...) has already been
    called this run, so the run_id foreign key exists.
    """
    now = datetime.now(UTC).isoformat()
    rows = [
        (run_id, now, r.config.title, i, bullet)
        for r in results
        if r.strategy is not None
        for i, bullet in enumerate(r.strategy.bullets)
    ]
    with get_connection(database_url) as conn:
        conn.cursor().executemany(
            "INSERT INTO bullets (run_id, created_at, topic, bullet_index, bullet_text) "
            "VALUES (%s, %s, %s, %s, %s)",
            rows,
        )
        conn.commit()


async def _diff_one_topic(
    topic: str,
    today_bullets: list[str],
    yesterday_bullets: list[str],
    client: LLMClient,
    sem: asyncio.Semaphore,
) -> tuple[str, BulletDiff | None]:
    async with sem:
        try:
            diff, _ = await client.complete_structured(
                prompt=build_diff_prompt(topic, today_bullets, yesterday_bullets),
                response_model=BulletDiff,
                system=SYSTEM_DIFF,
            )
            return topic, diff
        except Exception as exc:
            log.warning("bullet_diff_failed", topic=topic, error=str(exc))
            return topic, None


async def diff_all_topics(
    results: list[TopicResult],
    yesterday: dict[str, list[str]],
    client: LLMClient,
    max_concurrent: int = 3,
) -> dict[str, BulletDiff]:
    """
    Diff today's bullets against yesterday's for every topic that has both.

    Topics with no prior history entry are skipped (first run for that topic).
    Topics that errored or produced no strategy today are also skipped.
    Returns a dict keyed by topic title; missing keys mean no diff was computed.
    """
    sem = asyncio.Semaphore(max_concurrent)
    tasks = [
        _diff_one_topic(
            topic=result.config.title,
            today_bullets=result.strategy.bullets,
            yesterday_bullets=yesterday[result.config.title],
            client=client,
            sem=sem,
        )
        for result in results
        if result.strategy is not None and result.config.title in yesterday
    ]

    if not tasks:
        return {}

    pairs = await asyncio.gather(*tasks, return_exceptions=True)
    diffs: dict[str, BulletDiff] = {}
    for item in pairs:
        if isinstance(item, BaseException):
            log.warning("bullet_diff_gather_error", error=str(item))
            continue
        topic, diff = item
        if diff is not None:
            diffs[topic] = diff

    log.info(
        "bullet_diff_complete",
        topics=len(diffs),
        new=sum(len(d.new) for d in diffs.values()),
        dropped=sum(len(d.dropped) for d in diffs.values()),
    )
    return diffs
