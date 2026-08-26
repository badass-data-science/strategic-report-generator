"""
Urgency scoring history and alert detection — PostgreSQL-backed.

Each pipeline run inserts per-topic urgency scores into the tracking
database (see db.py). After enough history has accumulated the alert logic
switches from a simple absolute threshold to a statistical baseline
(z-score), which self-calibrates to each topic's typical urgency level and
avoids alert fatigue on domains that are inherently high-scoring (e.g.
Defense).

Call order per run (same pattern as bullet_diff.py):
  0. db.record_run(database_url, run_id, article_count) — once per run,
     before any of the below (creates the run_id foreign key both tables use)
  1. load_history   (reads only, does not include the current run)
  2. check_alerts    (current scores vs. historical baseline)
  3. append_run       (writes current scores into the db for future runs)

This ordering means the current run never inflates its own baseline.

load_history/append_run take database_url rather than a live connection:
each call opens its own pooled connection (see db.get_connection). This
keeps the functions safe to call from Prefect tasks, where a shared
connection can't be passed between tasks (it isn't picklable).
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from .db import get_connection, insert_rows_isolating_failures
from .models import TopicResult

# Minimum number of historical runs required before the statistical baseline
# is used. Below this threshold the absolute threshold is the only check.
_MIN_HISTORY_RUNS = 7


@dataclass
class UrgencyAlert:
    topic: str
    score: float
    reason: str          # "absolute" or "statistical"
    threshold: float     # the threshold that was crossed
    mean: float | None = None
    std: float | None = None
    z_score: float | None = None

    def summary(self) -> str:
        if self.reason == "statistical":
            return (
                f"{self.topic}: score={self.score:.2f} "
                f"(z={self.z_score:.1f}, mean={self.mean:.2f}±{self.std:.2f})"
            )
        return (
            f"{self.topic}: score={self.score:.2f} "
            f"exceeds absolute threshold {self.threshold:.2f}"
        )


def load_history(database_url: str) -> dict[str, list[float]]:
    """
    Return every topic's historical urgency scores, oldest-first per topic.

    Does not include the current run — call this before append_run().
    """
    with get_connection(database_url) as conn:
        rows = conn.execute(
            "SELECT topic, score FROM urgency_scores ORDER BY topic, created_at ASC"
        ).fetchall()
    history: dict[str, list[float]] = {}
    for topic, score in rows:
        history.setdefault(topic, []).append(score)
    return history


def append_run(
    database_url: str,
    results: list[TopicResult],
    run_id: str,
) -> int:
    """
    Insert the current run's urgency scores into the database. Returns the
    number of rows that failed to insert (0 = fully successful) — see
    db.insert_rows_isolating_failures for why one bad row no longer risks
    losing every topic's score for this run.

    Assumes db.record_run(database_url, run_id, ...) has already been called
    this run, so the run_id foreign key exists.
    """
    now = datetime.now(UTC).isoformat()
    rows = [
        (run_id, now, r.config.title, r.strategy.urgency_score)
        for r in results
        if r.strategy is not None
    ]
    with get_connection(database_url) as conn:
        failed = insert_rows_isolating_failures(
            conn,
            "INSERT INTO urgency_scores (run_id, created_at, topic, score) VALUES (%s, %s, %s, %s)",
            rows,
            run_id,
            "urgency_scores",
        )
        conn.commit()
    return failed


def check_alerts(
    results: list[TopicResult],
    history: dict[str, list[float]],
    absolute_threshold: float = 0.8,
    z_score_threshold: float = 2.0,
) -> list[UrgencyAlert]:
    """
    Return alerts for topics whose urgency score is anomalously high.

    For each topic:
      - If enough historical runs exist (>= _MIN_HISTORY_RUNS), use a
        z-score check against that topic's rolling mean and std.
      - Otherwise (or if std is zero, meaning no variation), fall back
        to the absolute threshold.
      - A topic cannot trigger both checks in the same run.
    """
    alerts: list[UrgencyAlert] = []

    for result in results:
        if result.strategy is None:
            continue

        topic = result.config.title
        score = result.strategy.urgency_score
        historical = history.get(topic, [])

        if len(historical) >= _MIN_HISTORY_RUNS:
            mean = sum(historical) / len(historical)
            variance = sum((x - mean) ** 2 for x in historical) / len(historical)
            std = math.sqrt(variance)

            if std > 0:
                z = (score - mean) / std
                if z >= z_score_threshold:
                    alerts.append(UrgencyAlert(
                        topic=topic,
                        score=score,
                        reason="statistical",
                        threshold=z_score_threshold,
                        mean=round(mean, 4),
                        std=round(std, 4),
                        z_score=round(z, 2),
                    ))
                continue  # statistical check ran (std > 0) — skip absolute for this topic
            # std == 0: all historical scores identical, fall through to absolute check

        # Absolute threshold: primary check when history is thin, fallback when std==0.
        if score >= absolute_threshold:
            alerts.append(UrgencyAlert(
                topic=topic,
                score=score,
                reason="absolute",
                threshold=absolute_threshold,
            ))

    return alerts
