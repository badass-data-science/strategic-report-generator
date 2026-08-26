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

An all-pairs x all-lags x both-directions scan runs hundreds to
thousands of independent correlation tests in one call -- against the
live DB (14 runs, 17 topics, ~7k tags) an uncorrected r_threshold cutoff
reported ~40 "significant" topic pairs and ~9,700(!) tag pairs, almost
all of it noise from testing that many hypotheses at once. Both public
functions below therefore run a Benjamini-Hochberg false-discovery-rate
correction (`fdr_q`) across every test performed in a single scan, and
only report a pair that clears BOTH the effect-size floor (r_threshold)
and FDR-adjusted significance -- see `_significant_correlations`.
"""

import math
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

# Minimum number of runs a tag must be nonzero in before it's eligible for
# tag_rate_lagged_correlations at all -- separate from _MIN_RUNS_FOR_LAG.
# Two tags that are each only ever nonzero in the same one or two runs
# (e.g. co-referenced entities like "ugg"/"deckers outdoor") trivially hit
# r=1.0 with a real, non-spurious p-value -- FDR correction doesn't catch
# this since it isn't a false positive, it's a sparsity artifact: the
# "signal" is really just "these two rare tags appeared in the same run."
_MIN_ACTIVE_RUNS_FOR_TAG_CORRELATION = 5

# Above this containment ratio (co-occurrence weight / the rarer tag's
# total appearances, summed across all history), two tags are treated as
# co-referential -- effectively two labels for the same entity/event (e.g.
# "qualcomm"/"wireless technology") -- and excluded from
# tag_rate_lagged_correlations entirely. Their rate series moving together
# isn't evidence of a feedback loop, it's the same signal counted twice;
# unlike _MIN_ACTIVE_RUNS_FOR_TAG_CORRELATION this isn't a sparsity
# artifact and shows up at nonzero lags too (autocorrelated "hot" topics
# drag both tags along together), so a lag-based filter can't catch it.
_MAX_CONTAINMENT_RATIO_FOR_TAG_CORRELATION = 0.8

# Above this same-community ratio (fraction of runs, among runs where both
# tags were assigned to some Louvain community that run, where they landed
# in the SAME community), two tags are treated as the same broad topic
# cluster and excluded from tag_rate_lagged_correlations -- e.g. "electric
# utility"/"power generation" or "defense"/"military": not the same
# entity (the containment ratio wouldn't catch these), but close enough on
# the tag graph that a correlation between them isn't a surprising
# cross-domain finding. Reuses community_summary_tags, which
# tag_tracking.record_community_summaries already persists every run from
# tag_graph.build_display_graph's Louvain clustering -- no new computation,
# and no LLM call (that table's community_id/tag columns come from
# networkx, not the LLM-written label/summary columns alongside them).
_MAX_SAME_COMMUNITY_RATIO_FOR_TAG_CORRELATION = 0.8

# Below this many runs where both tags were assigned a community at all,
# there's not enough evidence to judge whether they're the same cluster --
# same "skip rather than guess" reasoning as _MIN_RUNS_FOR_LAG.
_MIN_SHARED_RUNS_FOR_COMMUNITY_FILTER = 3


@dataclass
class LaggedCorrelation:
    subject_a: str
    subject_b: str
    lag: int  # subject_b's value `lag` runs after subject_a's
    correlation: float  # Pearson r
    n: int  # aligned (x, y) pairs the correlation was computed over
    p_value: float  # two-tailed p-value for this r, n (uncorrected)
    q_value: float  # Benjamini-Hochberg FDR-adjusted p-value across the whole scan

    def summary(self) -> str:
        return (
            f"{self.subject_a} -> {self.subject_b} (lag={self.lag}): "
            f"r={self.correlation:+.2f} (n={self.n}, q={self.q_value:.3f})"
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


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _betacf(a: float, b: float, x: float, max_iter: int = 200, eps: float = 1e-12) -> float:
    """Continued-fraction evaluation of the incomplete beta function (Numerical Recipes)."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < eps:
        d = eps
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < eps:
            d = eps
        c = 1.0 + aa / c
        if abs(c) < eps:
            c = eps
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < eps:
            d = eps
        c = 1.0 + aa / c
        if abs(c) < eps:
            c = eps
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """I_x(a, b) -- used below to get a t-distribution p-value without a scipy dependency."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.log(x) * a + math.log(1.0 - x) * b - _log_beta(a, b))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _pearson_p_value(r: float, n: int) -> float:
    """
    Two-tailed p-value for a Pearson r over n samples -- the same test
    scipy.stats.pearsonr reports, via the standard t = r*sqrt((n-2)/(1-r^2))
    transform and the incomplete-beta form of the t-distribution's CDF,
    since this project has no hard scipy/numpy dependency to reach for.
    """
    r = max(-1.0, min(1.0, r))
    df = n - 2
    if df <= 0:
        return 1.0
    if abs(r) >= 1.0:
        return 0.0
    t_squared = r * r * df / (1.0 - r * r)
    return _regularized_incomplete_beta(df / (df + t_squared), df / 2.0, 0.5)


def _benjamini_hochberg_qvalues(p_values: Sequence[float]) -> list[float]:
    """
    BH FDR-adjusted p-values ("q-values"), same order as `p_values`: the
    smallest FDR threshold at which each hypothesis would be rejected,
    correcting for every test in this one batch simultaneously -- not a
    per-pair correction, which is why callers must gather every candidate
    from a single scan before calling this.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    q_sorted = [0.0] * m
    running_min = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running_min = min(running_min, p_values[i] * m / rank)
        q_sorted[rank - 1] = running_min
    q_values = [0.0] * m
    for rank, i in enumerate(order):
        q_values[i] = min(1.0, q_sorted[rank])
    return q_values


def _significant_correlations(
    candidates: list[tuple[str, str, int, float, int]],
    r_threshold: float,
    fdr_q: float,
) -> list[LaggedCorrelation]:
    """
    Apply BH FDR correction across every (subject_a, subject_b, lag, r, n)
    candidate from one scan, then keep only pairs clearing both the raw
    effect-size floor (r_threshold) and FDR-adjusted significance (fdr_q)
    -- see module docstring for why the effect-size filter alone isn't
    enough once a scan runs hundreds/thousands of simultaneous tests.
    """
    p_values = [_pearson_p_value(r, n) for _, _, _, r, n in candidates]
    q_values = _benjamini_hochberg_qvalues(p_values)
    results = [
        LaggedCorrelation(a, b, lag, round(r, 3), n, round(p, 4), round(q, 4))
        for (a, b, lag, r, n), p, q in zip(candidates, p_values, q_values, strict=True)
        if abs(r) >= r_threshold and q <= fdr_q
    ]
    return sorted(results, key=lambda c: -abs(c.correlation))


def topic_urgency_lagged_correlations(
    database_url: str, max_lag: int = 3, r_threshold: float = 0.6, fdr_q: float = 0.05
) -> list[LaggedCorrelation]:
    """
    All-pairs lagged correlation over topic urgency scores. Directional:
    (A, B, lag) and (B, A, lag) are both computed, since a feedback loop
    (A drives B) is not the same claim as its reverse (B drives A).
    """
    _, series = load_topic_urgency_series(database_url)
    candidates: list[tuple[str, str, int, float, int]] = []
    for topic_a, topic_b in combinations(series, 2):
        for lag in range(max_lag + 1):
            for a, b in ((topic_a, topic_b), (topic_b, topic_a)):
                out = lagged_pearson(series[a], series[b], lag)
                if out is None:
                    continue
                r, n = out
                candidates.append((a, b, lag, r, n))
    return _significant_correlations(candidates, r_threshold, fdr_q)


def _tags_with_enough_activity(
    series: dict[str, list[float]], min_active_runs: int
) -> set[str]:
    """
    Tags nonzero in at least `min_active_runs` runs -- see
    _MIN_ACTIVE_RUNS_FOR_TAG_CORRELATION for why this matters: below this
    floor, a "correlation" is often just two rare tags both being nonzero
    in the same handful of runs, not a real relationship.
    """
    return {
        tag
        for tag, rates in series.items()
        if sum(1 for rate in rates if rate > 0.0) >= min_active_runs
    }


def _containment_ratio(co_occurrence: int, count_a: int, count_b: int) -> float:
    """
    Fraction of the rarer tag's total appearances that also included the
    other tag. 1.0 means the two are effectively inseparable (always seen
    together); low values mean they occasionally co-occur but mostly vary
    independently.
    """
    denominator = min(count_a, count_b)
    if denominator == 0:
        return 0.0
    return co_occurrence / denominator


def _drop_near_synonymous_pairs(
    edge_rows: list[tuple[str, str]],
    co_occurrence_totals: dict[tuple[str, str], int],
    tag_totals: dict[str, int],
    max_containment_ratio: float,
) -> list[tuple[str, str]]:
    """Remove pairs whose containment ratio is at or above max_containment_ratio."""
    return [
        (tag_a, tag_b)
        for tag_a, tag_b in edge_rows
        if _containment_ratio(
            co_occurrence_totals.get((tag_a, tag_b), 0),
            tag_totals.get(tag_a, 0),
            tag_totals.get(tag_b, 0),
        )
        < max_containment_ratio
    ]


def _same_community_ratio(
    tag_a: str, tag_b: str, tag_community_by_run: dict[str, dict[str, int]]
) -> tuple[float, int]:
    """
    (ratio, n) where n is the number of runs in which both tags were
    assigned to some Louvain community, and ratio is the fraction of those
    runs in which they landed in the SAME community. (0.0, 0) if the two
    were never co-assigned a community at all.
    """
    shared = 0
    same = 0
    for communities in tag_community_by_run.values():
        comm_a = communities.get(tag_a)
        comm_b = communities.get(tag_b)
        if comm_a is None or comm_b is None:
            continue
        shared += 1
        if comm_a == comm_b:
            same += 1
    if shared == 0:
        return 0.0, 0
    return same / shared, shared


def _drop_topically_clustered_pairs(
    edge_rows: list[tuple[str, str]],
    tag_community_by_run: dict[str, dict[str, int]],
    max_same_community_ratio: float,
    min_shared_runs: int,
) -> list[tuple[str, str]]:
    """
    Remove pairs whose same-community ratio is at or above
    max_same_community_ratio -- unless there are fewer than min_shared_runs
    runs of evidence to judge that from, in which case the pair is kept
    (not enough evidence to call it a cluster, so don't guess).
    """
    kept = []
    for tag_a, tag_b in edge_rows:
        ratio, shared = _same_community_ratio(tag_a, tag_b, tag_community_by_run)
        if shared >= min_shared_runs and ratio >= max_same_community_ratio:
            continue
        kept.append((tag_a, tag_b))
    return kept


def tag_rate_lagged_correlations(
    database_url: str,
    max_lag: int = 3,
    r_threshold: float = 0.7,
    min_edge_weight: int = 2,
    fdr_q: float = 0.05,
    min_active_runs: int = _MIN_ACTIVE_RUNS_FOR_TAG_CORRELATION,
    max_containment_ratio: float = _MAX_CONTAINMENT_RATIO_FOR_TAG_CORRELATION,
    max_same_community_ratio: float = _MAX_SAME_COMMUNITY_RATIO_FOR_TAG_CORRELATION,
) -> list[LaggedCorrelation]:
    """
    Lagged correlation over tag rates, restricted to tag pairs that have
    co-occurred at least once with weight >= min_edge_weight in tag_edges
    -- see module docstring for why an all-pairs scan over ~7k tags isn't
    the right default here -- further restricted to tags active in at
    least min_active_runs runs, so a pair isn't reported purely because
    both tags are rare and happened to be nonzero together, and finally
    with both near-synonymous pairs (containment ratio >= max_containment_ratio)
    and same-topic-cluster pairs (same-community ratio >= max_same_community_ratio)
    dropped, since neither co-referential tags nor two tags from the same
    Louvain community moving together is evidence of a cross-domain
    feedback loop.
    """
    with get_connection(database_url) as conn:
        edge_rows = conn.execute(
            "SELECT DISTINCT tag_a, tag_b FROM tag_edges WHERE weight >= %s",
            (min_edge_weight,),
        ).fetchall()
        co_occurrence_totals = {
            (tag_a, tag_b): total
            for tag_a, tag_b, total in conn.execute(
                "SELECT tag_a, tag_b, SUM(weight) FROM tag_edges GROUP BY tag_a, tag_b"
            ).fetchall()
        }
        tag_totals = dict(
            conn.execute("SELECT tag, SUM(count) FROM tag_counts GROUP BY tag").fetchall()
        )
        tag_community_by_run: dict[str, dict[str, int]] = {}
        for run_id, tag, community_id in conn.execute(
            "SELECT run_id, tag, community_id FROM community_summary_tags"
        ).fetchall():
            tag_community_by_run.setdefault(run_id, {})[tag] = community_id

    edge_rows = _drop_near_synonymous_pairs(
        edge_rows, co_occurrence_totals, tag_totals, max_containment_ratio
    )
    edge_rows = _drop_topically_clustered_pairs(
        edge_rows,
        tag_community_by_run,
        max_same_community_ratio,
        _MIN_SHARED_RUNS_FOR_COMMUNITY_FILTER,
    )

    candidate_tags = {t for a, b in edge_rows for t in (a, b)}
    _, series = load_tag_rate_series(database_url, tags=candidate_tags)
    active_tags = _tags_with_enough_activity(series, min_active_runs)

    candidates: list[tuple[str, str, int, float, int]] = []
    for tag_a, tag_b in edge_rows:
        if tag_a not in active_tags or tag_b not in active_tags:
            continue
        for lag in range(max_lag + 1):
            for a, b in ((tag_a, tag_b), (tag_b, tag_a)):
                out = lagged_pearson(series[a], series[b], lag)
                if out is None:
                    continue
                r, n = out
                candidates.append((a, b, lag, r, n))
    return _significant_correlations(candidates, r_threshold, fdr_q)
