"""
Systems-thinking prototype: lagged cross-correlation over time series the
tracking database already accumulates every run, to surface candidate
feedback loops -- does topic/tag A's value predict B's value N runs later?
-- without adding any new persistence.

Read-only, reuses three tables tag_tracking.py and urgency.py already
populate every run:
  - urgency_scores  (topic x run -> score)   small, fixed topic vocabulary,
    so topic_urgency_lagged_correlations scans all pairs directly.
  - tag_counts      (tag x run -> count)     ~7k distinct tags, so
    tag_rate_lagged_correlations restricts the candidate-pair search space
    to tags already known to co-occur (tag_edges), the same "start from a
    graph-native signal" pattern already used for bridge tags
    (tag_graph.find_bridge_tags) rather than an all-pairs scan that would
    be both slow and mostly noise.
  - tag_edges       (tag_a x tag_b x run -> weight)  supplies that
    candidate-pair restriction.

This is a spike (branch: systems-thinking-prototype), not a shipped
feature -- no CLI/Prefect wiring, no new migration. Everything here is
gated by _MIN_RUNS_FOR_LAG, the same "skip rather than guess" pattern as
tag_tracking's _MIN_HISTORY_RUNS: with only a handful of runs since the
PostgreSQL migration (2026-08-13), real queries against the live database
will correctly return an empty list right now -- that's the gate working
as intended, not a bug. The math itself is validated in
tests/test_systems_signals.py against synthetic series, independent of
how much real history exists.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

from .db import get_connection

# Minimum aligned (x, y) pairs remaining after a lag shift before a
# correlation is reported at all -- below this, r is noise dressed up as a
# number. Deliberately higher than tag_tracking's _MIN_HISTORY_RUNS=7: that
# gate guards a single tag's mean/std, this one guards a correlation
# between two already-shortened series.
_MIN_RUNS_FOR_LAG = 8


@dataclass
class LaggedCorrelation:
    subject_a: str
    subject_b: str
    lag: int  # subject_b's value `lag` runs after subject_a's
    correlation: float  # Pearson r
    n: int  # aligned (x, y) pairs the correlation was computed over

    def summary(self) -> str:
        return (
            f"{self.subject_a} -> {self.subject_b} (lag={self.lag}): "
            f"r={self.correlation:+.2f} (n={self.n})"
        )


def load_run_order(database_url: str) -> list[str]:
    """Run IDs oldest-first by created_at -- the shared time axis every series below aligns to."""
    with get_connection(database_url) as conn:
        rows = conn.execute("SELECT run_id FROM runs ORDER BY created_at ASC").fetchall()
    return [r[0] for r in rows]


def load_topic_urgency_series(
    database_url: str,
) -> tuple[list[str], dict[str, list[float | None]]]:
    """
    Return (run_order, {topic: [score aligned to run_order]}), None where a
    run has no urgency_scores row for that topic (e.g. the topic errored
    out that run) -- left as a gap, not coerced to 0.0, since 0.0 is itself
    a meaningful low-urgency score, not a "missing" sentinel.
    """
    run_order = load_run_order(database_url)
    run_index = {run_id: i for i, run_id in enumerate(run_order)}

    with get_connection(database_url) as conn:
        rows = conn.execute("SELECT run_id, topic, score FROM urgency_scores").fetchall()

    series: dict[str, list[float | None]] = {}
    for run_id, topic, score in rows:
        idx = run_index.get(run_id)
        if idx is None:
            continue
        series.setdefault(topic, [None] * len(run_order))[idx] = score

    return run_order, series


def load_tag_rate_series(
    database_url: str, tags: set[str] | None = None
) -> tuple[list[str], dict[str, list[float]]]:
    """
    Return (run_order, {tag: [rate aligned to run_order]}) -- tag count /
    that run's article_count, same normalization as
    tag_tracking.load_tag_rate_history. A run where the tag didn't appear
    is a real 0.0, not a gap, unlike urgency scores above.

    Pass `tags` to restrict to a known candidate set (see
    tag_rate_lagged_correlations) instead of pulling all ~7k distinct tags.
    """
    run_order = load_run_order(database_url)
    run_index = {run_id: i for i, run_id in enumerate(run_order)}

    with get_connection(database_url) as conn:
        article_counts: dict[str, int] = dict(
            conn.execute("SELECT run_id, article_count FROM runs").fetchall()
        )
        if tags is None:
            rows = conn.execute("SELECT run_id, tag, count FROM tag_counts").fetchall()
        else:
            rows = conn.execute(
                "SELECT run_id, tag, count FROM tag_counts WHERE tag = ANY(%s)",
                (list(tags),),
            ).fetchall()

    series: dict[str, list[float]] = {}
    for run_id, tag, count in rows:
        idx = run_index.get(run_id)
        article_count = article_counts.get(run_id)
        if idx is None or not article_count:
            continue
        series.setdefault(tag, [0.0] * len(run_order))[idx] = count / article_count

    return run_order, series


def lagged_pearson(
    x: Sequence[float | None], y: Sequence[float | None], lag: int
) -> tuple[float, int] | None:
    """
    Pearson correlation between x[t] and y[t + lag] across every t where
    both are defined. None if fewer than _MIN_RUNS_FOR_LAG aligned pairs
    remain, or either side has zero variance (r is undefined, not 0).
    """
    if lag < 0:
        raise ValueError("lag must be >= 0")

    pairs = [
        (x[t], y[t + lag])
        for t in range(len(x) - lag)
        if x[t] is not None and y[t + lag] is not None
    ]
    n = len(pairs)
    if n < _MIN_RUNS_FOR_LAG:
        return None

    xs = [p[0] for p in pairs if p[0] is not None]
    ys = [p[1] for p in pairs if p[1] is not None]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(xs, ys, strict=True)) / n
    var_x = sum((a - mean_x) ** 2 for a in xs) / n
    var_y = sum((b - mean_y) ** 2 for b in ys) / n
    if var_x == 0 or var_y == 0:
        return None

    r = cov / (var_x**0.5 * var_y**0.5)
    return r, n


def topic_urgency_lagged_correlations(
    database_url: str, max_lag: int = 3, r_threshold: float = 0.6
) -> list[LaggedCorrelation]:
    """
    All-pairs lagged correlation over topic urgency scores. Directional:
    (A, B, lag) and (B, A, lag) are both computed, since a feedback loop
    (A drives B) is not the same claim as its reverse (B drives A).
    """
    _, series = load_topic_urgency_series(database_url)
    results = []
    for topic_a, topic_b in combinations(series, 2):
        for lag in range(max_lag + 1):
            for a, b in ((topic_a, topic_b), (topic_b, topic_a)):
                out = lagged_pearson(series[a], series[b], lag)
                if out is None:
                    continue
                r, n = out
                if abs(r) >= r_threshold:
                    results.append(LaggedCorrelation(a, b, lag, round(r, 3), n))
    return sorted(results, key=lambda c: -abs(c.correlation))


def tag_rate_lagged_correlations(
    database_url: str, max_lag: int = 3, r_threshold: float = 0.7, min_edge_weight: int = 2
) -> list[LaggedCorrelation]:
    """
    Lagged correlation over tag rates, restricted to tag pairs that have
    co-occurred at least once with weight >= min_edge_weight in tag_edges
    -- see module docstring for why an all-pairs scan over ~7k tags isn't
    the right default here.
    """
    with get_connection(database_url) as conn:
        edge_rows = conn.execute(
            "SELECT DISTINCT tag_a, tag_b FROM tag_edges WHERE weight >= %s",
            (min_edge_weight,),
        ).fetchall()

    candidate_tags = {t for a, b in edge_rows for t in (a, b)}
    _, series = load_tag_rate_series(database_url, tags=candidate_tags)

    results = []
    for tag_a, tag_b in edge_rows:
        if tag_a not in series or tag_b not in series:
            continue
        for lag in range(max_lag + 1):
            for a, b in ((tag_a, tag_b), (tag_b, tag_a)):
                out = lagged_pearson(series[a], series[b], lag)
                if out is None:
                    continue
                r, n = out
                if abs(r) >= r_threshold:
                    results.append(LaggedCorrelation(a, b, lag, round(r, 3), n))
    return sorted(results, key=lambda c: -abs(c.correlation))
