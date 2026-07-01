"""
Urgency scoring history and alert detection.

Each pipeline run appends per-topic urgency scores to a persistent JSON history
file. After enough history has accumulated the alert logic switches from a simple
absolute threshold to a statistical baseline (z-score), which self-calibrates to
each topic's typical urgency level and avoids alert fatigue on domains that are
inherently high-scoring (e.g. Defense).

Alert order per run:
  1. Load history  (reads only, does not include the current run)
  2. Check alerts  (current scores vs. historical baseline)
  3. Append run    (writes current scores into history for future runs)

This ordering means the current run never inflates its own baseline.
"""

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

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
        return f"{self.topic}: score={self.score:.2f} exceeds absolute threshold {self.threshold:.2f}"


def load_history(history_path: Path) -> list[dict]:
    """Return the full run history, or an empty list if none exists yet."""
    if not history_path.exists():
        return []
    return json.loads(history_path.read_text(encoding="utf-8"))


def append_run(
    history_path: Path,
    results: list[TopicResult],
    run_id: str,
) -> None:
    """Append the current run's urgency scores to the history file."""
    history = load_history(history_path)
    scores = {
        r.config.title: r.strategy.urgency_score
        for r in results
        if r.strategy is not None
    }
    history.append({
        "date": str(date.today()),
        "run_id": run_id,
        "scores": scores,
    })
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


def check_alerts(
    results: list[TopicResult],
    history: list[dict],
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

        historical = [
            run["scores"][topic]
            for run in history
            if topic in run.get("scores", {})
        ]

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
