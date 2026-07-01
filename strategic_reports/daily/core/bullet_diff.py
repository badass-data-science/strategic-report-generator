"""
Historical bullet diffing between consecutive pipeline runs.

Stores per-topic strategic bullets from each run and uses an LLM to
semantically classify changes: new insights, continued insights, and
insights that dropped out since yesterday.

Call order per run (same pattern as urgency.py):
  1. load_bullet_history   — reads only; gives yesterday's bullets
  2. diff_all_topics       — compares today against yesterday
  3. append_bullet_run     — writes today into history for future runs
"""

import asyncio
import json
from datetime import date
from pathlib import Path

import structlog

from .llm_client import LLMClient
from .models import BulletDiff, TopicResult
from .prompts import SYSTEM_DIFF, build_diff_prompt

log = structlog.get_logger(__name__)

_MAX_HISTORY_ENTRIES = 7


def load_bullet_history(history_path: Path) -> list[dict]:
    """Return run history, or empty list if the file does not exist yet."""
    if not history_path.exists():
        return []
    return json.loads(history_path.read_text(encoding="utf-8"))


def append_bullet_run(
    history_path: Path,
    results: list[TopicResult],
    run_id: str,
) -> None:
    """Append today's strategic bullets to the history file."""
    history = load_bullet_history(history_path)
    history.append({
        "date": str(date.today()),
        "run_id": run_id,
        "topics": {
            r.config.title: r.strategy.bullets
            for r in results
            if r.strategy is not None
        },
    })
    if len(history) > _MAX_HISTORY_ENTRIES:
        history = history[-_MAX_HISTORY_ENTRIES:]
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8"
    )


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
        if isinstance(item, Exception):
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
