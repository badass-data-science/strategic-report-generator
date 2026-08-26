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

Originally a spike (branch: systems-thinking-prototype) -- no new
migration was ever needed (read-only), and it's now wired into both
report-generation entry points (cli.py's `run()` and
flows/daily_report.py's Prefect flow), rendered as the "Systems Signals"
section in index.html.j2. Everything here is
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

tag_rate_lagged_correlations additionally reuses three tables beyond the
three above, all already populated every run:
  - `tag_topics`   (run_id, tag, topic)  weighted blend of every topic a
    tag has appeared under (`_topic_weights`), used to control out each
    tag's own topic-level article volume before correlating (see
    `lagged_partial_pearson`) -- catches two tags riding the same busy
    topic's (or cross-topic trend's) news volume together, not one
    driving the other.
  - `articles`     (run_id, topic, link, ...) supplies both the
    topic-volume series above and, joined with `article_tags`, the
    co-occurring-article links `_drop_single_source_pairs` checks for a
    dominant link domain -- catches two tags that mostly co-occur because
    one recurring source (e.g. a daily market digest) republishes both
    together, not because the tags themselves move together.
  - `article_tags` (article_id, run_id, tag) supplies that self-join.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from urllib.parse import urlparse

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

# Above this source-dominance ratio (the single most common link domain
# among every article where the two tags co-occur, divided by the total
# number of co-occurring articles), two tags are treated as co-occurring
# mainly because one recurring source republishes both together -- e.g.
# "dax"/"nikkei" mostly co-occurred on the same daily Armstrong Economics
# "Market Talk" digest, not because DAX and Nikkei genuinely move
# together (2026-08-26 systems-thinking-prototype session). Neither the
# containment ratio (co-occurrence vs. each tag's OWN total appearances,
# which can be well under 0.8 even when nearly every co-occurrence traces
# to one source) nor the community filter (Louvain clustering, unrelated
# to source diversity) catches this.
_MAX_SOURCE_DOMINANCE_RATIO_FOR_TAG_CORRELATION = 0.8

# Below this many co-occurring articles, there's not enough evidence to
# judge source dominance -- same "skip rather than guess" reasoning as
# _MIN_SHARED_RUNS_FOR_COMMUNITY_FILTER (a pair with only 1-2 co-occurring
# articles trivially looks "100% one source" regardless).
_MIN_CO_OCCURRING_ARTICLES_FOR_SOURCE_FILTER = 3


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


def _runs_missing_from(run_article_counts: dict[str, int], runs_present: set[str]) -> set[str]:
    """
    Run IDs whose article_count is nonzero but which are absent from
    `runs_present` (e.g. distinct run_ids actually found in tag_counts, or
    in articles) -- i.e. a run whose recording into that table failed
    entirely (see article_archive_savepoint_fix -- confirmed to have
    happened for `articles`; tag_tracking.record_tags and
    record_community_summaries share the same fragile all-or-nothing
    transaction, so nothing rules it out there for a future run), not a
    run that genuinely had nothing to record.
    """
    return {
        run_id
        for run_id, article_count in run_article_counts.items()
        if article_count > 0 and run_id not in runs_present
    }


def load_topic_urgency_series(
    database_url: str,
) -> tuple[list[str], dict[str, list[float | None]]]:
    """
    Return (run_order, {topic: [score aligned to run_order]}), None where a
    run has no urgency_scores row for that topic (e.g. the topic errored
    out that run) -- left as a gap, not coerced to 0.0, since 0.0 is itself
    a meaningful low-urgency score, not a "missing" sentinel.

    A run whose article archive failed entirely (nonzero runs.article_count
    but zero rows in `articles`) also gets None for every topic that run --
    see _runs_missing_from. Confirmed to have actually happened
    pre-article_archive_savepoint_fix: two 2026-08-24 runs had real urgency
    scores computed but an empty article archive, and their spuriously low,
    correlated scores were what drove the Energy<->Forex r=0.92 false
    positive investigated the same session as that fix -- see
    energy_forex_correlation_artifact memory.
    """
    run_order = load_run_order(database_url)
    run_index = {run_id: i for i, run_id in enumerate(run_order)}

    with get_connection(database_url) as conn:
        rows = conn.execute("SELECT run_id, topic, score FROM urgency_scores").fetchall()
        article_counts: dict[str, int] = dict(
            conn.execute("SELECT run_id, article_count FROM runs").fetchall()
        )
        runs_with_article_data = {
            row[0] for row in conn.execute("SELECT DISTINCT run_id FROM articles").fetchall()
        }

    runs_with_missing_article_data = _runs_missing_from(article_counts, runs_with_article_data)

    series: dict[str, list[float | None]] = {}
    for run_id, topic, score in rows:
        idx = run_index.get(run_id)
        if idx is None:
            continue
        series.setdefault(topic, [None] * len(run_order))[idx] = score

    for run_id in runs_with_missing_article_data:
        idx = run_index.get(run_id)
        if idx is None:
            continue
        for scores in series.values():
            scores[idx] = None

    return run_order, series


def load_tag_rate_series(
    database_url: str, tags: set[str] | None = None
) -> tuple[list[str], dict[str, list[float | None]]]:
    """
    Return (run_order, {tag: [rate aligned to run_order]}) -- tag count /
    that run's article_count, same normalization as
    tag_tracking.load_tag_rate_history. A run where the tag didn't appear
    is a real 0.0, not a gap.

    A run whose tag_counts recording failed entirely gets None for every
    tag that run instead of a false 0.0 -- see _runs_missing_from. This
    matters more here than for the topic-volume control
    (tag_rate_lagged_correlations): these are the *primary* x/y series
    every tag-rate correlation is computed from, so a false 0.0 here would
    directly corrupt results, not just bias a nuisance control variable.

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
        runs_with_tag_data = {
            row[0] for row in conn.execute("SELECT DISTINCT run_id FROM tag_counts").fetchall()
        }

    runs_with_missing_tag_data = _runs_missing_from(article_counts, runs_with_tag_data)

    series: dict[str, list[float | None]] = {}
    for run_id, tag, count in rows:
        idx = run_index.get(run_id)
        article_count = article_counts.get(run_id)
        if idx is None or not article_count:
            continue
        series.setdefault(tag, [0.0] * len(run_order))[idx] = count / article_count

    for run_id in runs_with_missing_tag_data:
        idx = run_index.get(run_id)
        if idx is None:
            continue
        for rates in series.values():
            rates[idx] = None

    return run_order, series


def _pearson(xs: list[float], ys: list[float]) -> tuple[float, int] | None:
    """Plain Pearson r over two already-aligned, gap-free equal-length lists."""
    n = len(xs)
    if n < _MIN_RUNS_FOR_LAG:
        return None

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(xs, ys, strict=True)) / n
    var_x = sum((a - mean_x) ** 2 for a in xs) / n
    var_y = sum((b - mean_y) ** 2 for b in ys) / n
    if var_x == 0 or var_y == 0:
        return None

    r = cov / (var_x**0.5 * var_y**0.5)
    return r, n


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

    # Built as an explicit loop with local variables (xv/yv), not a
    # comprehension re-indexing x[t]/y[t+lag] inside the output expression
    # -- mypy can narrow "xv is not None" to xv: float for a local variable
    # across the whole if-block, but not across two separate re-evaluations
    # of the same subscript expression, so the comprehension form left xs/ys
    # typed as list[float | None] despite the filter guaranteeing no Nones.
    xs: list[float] = []
    ys: list[float] = []
    for t in range(len(x) - lag):
        xv, yv = x[t], y[t + lag]
        if xv is not None and yv is not None:
            xs.append(xv)
            ys.append(yv)

    if len(xs) < _MIN_RUNS_FOR_LAG:
        return None

    return _pearson(xs, ys)


def _ols_residuals(z: Sequence[float], w: Sequence[float]) -> list[float]:
    """
    Residuals of w after regressing out a linear effect of z (simple OLS,
    same-length paired series). If z has zero variance it carries no
    information to control for, so w is returned unchanged rather than
    dividing by zero.
    """
    n = len(z)
    mean_z = sum(z) / n
    mean_w = sum(w) / n
    var_z = sum((v - mean_z) ** 2 for v in z)
    if var_z == 0:
        return list(w)
    cov_zw = sum((z[i] - mean_z) * (w[i] - mean_w) for i in range(n))
    slope = cov_zw / var_z
    intercept = mean_w - slope * mean_z
    return [w[i] - (intercept + slope * z[i]) for i in range(n)]


def lagged_partial_pearson(
    x: Sequence[float | None],
    y: Sequence[float | None],
    zx: Sequence[float | None],
    zy: Sequence[float | None],
    lag: int,
) -> tuple[float, int] | None:
    """
    Partial correlation between x[t] and y[t + lag], controlling for each
    side's own same-run confound series -- zx[t] alongside x[t], zy[t+lag]
    alongside y[t+lag] -- before correlating. Two separate controls (not
    one shared control, since a confound like topic-article-volume takes a
    different value at run t than at run t+lag) via two independent OLS
    residualizations, so treat this as removing 2 degrees of freedom
    relative to plain lagged_pearson (see _pearson_p_value's `controls`).

    A None in zx/zy excludes that t the same way a None in x/y does --
    "we don't know this run's confound value" (e.g. a run whose article
    archive failed, see article_archive.record_articles) must not be
    silently treated as "confound was zero," which would bias the OLS fit
    rather than just shrinking n.

    Built for tag_rate_lagged_correlations' topic-volume control: two tags
    from the same busy topic (e.g. Defense) can ride that topic's article
    volume together at lag>0 without one predicting the other -- see
    module docstring.
    """
    if lag < 0:
        raise ValueError("lag must be >= 0")

    # See lagged_pearson for why this is an explicit loop with local
    # variables, not a comprehension re-indexing x[t]/y[t+lag]/zx[t]/
    # zy[t+lag] in the output expression.
    xs: list[float] = []
    ys: list[float] = []
    zxs: list[float] = []
    zys: list[float] = []
    for t in range(len(x) - lag):
        xv, yv, zxv, zyv = x[t], y[t + lag], zx[t], zy[t + lag]
        if xv is not None and yv is not None and zxv is not None and zyv is not None:
            xs.append(xv)
            ys.append(yv)
            zxs.append(zxv)
            zys.append(zyv)

    if len(xs) < _MIN_RUNS_FOR_LAG:
        return None

    x_resid = _ols_residuals(zxs, xs)
    y_resid = _ols_residuals(zys, ys)
    return _pearson(x_resid, y_resid)


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


def _pearson_p_value(r: float, n: int, controls: int = 0) -> float:
    """
    Two-tailed p-value for a Pearson r over n samples -- the same test
    scipy.stats.pearsonr reports, via the standard t = r*sqrt((df)/(1-r^2))
    transform and the incomplete-beta form of the t-distribution's CDF,
    since this project has no hard scipy/numpy dependency to reach for.

    `controls` is the number of variables already regressed out before r
    was computed (0 for plain lagged_pearson; 2 for lagged_partial_pearson,
    which runs two independent OLS residualizations) -- each one costs a
    degree of freedom, same as in a partial-correlation significance test.
    """
    r = max(-1.0, min(1.0, r))
    df = n - 2 - controls
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
    controls: int = 0,
) -> list[LaggedCorrelation]:
    """
    Apply BH FDR correction across every (subject_a, subject_b, lag, r, n)
    candidate from one scan, then keep only pairs clearing both the raw
    effect-size floor (r_threshold) and FDR-adjusted significance (fdr_q)
    -- see module docstring for why the effect-size filter alone isn't
    enough once a scan runs hundreds/thousands of simultaneous tests.
    Pass `controls` (see _pearson_p_value) when every r in this batch came
    from a partial correlation, not plain lagged_pearson.
    """
    p_values = [_pearson_p_value(r, n, controls) for _, _, _, r, n in candidates]
    q_values = _benjamini_hochberg_qvalues(p_values)
    results = [
        LaggedCorrelation(a, b, lag, round(r, 3), n, round(p, 4), round(q, 4))
        for (a, b, lag, r, n), p, q in zip(candidates, p_values, q_values, strict=True)
        if abs(r) >= r_threshold and q <= fdr_q
    ]
    return sorted(results, key=lambda c: -abs(c.correlation))


def _topic_urgency_control_series(
    topic: str, series: dict[str, list[float | None]], run_order: Sequence[str]
) -> list[float | None]:
    """
    Per-run leave-one-out mean urgency across every OTHER topic -- a
    stand-in for "how newsy was this day overall," the topic-urgency
    analogue of tag_rate_lagged_correlations' weighted topic-volume
    control (_weighted_topic_volume). Controlling this out before
    correlating two topics catches e.g. two topics both tracking a shared
    busy-news-day trend rather than one driving the other -- confirmed
    necessary, not theoretical: Energy<->Forex (r=0.79 at lag=0) survived
    on the live DB even after the article-archive-gap fix, and both
    topics separately correlated with this control almost as strongly as
    with each other (see energy_forex_correlation_artifact memory).

    None where fewer than 2 other topics have a real score that run --
    not enough to estimate a day-level trend from.
    """
    # See lagged_pearson for why this is an explicit loop with a local
    # variable (v), not a comprehension re-indexing series[t][i] twice --
    # mypy can't narrow the second re-evaluation to float.
    other_topics = [t for t in series if t != topic]
    control: list[float | None] = []
    for i in range(len(run_order)):
        values: list[float] = []
        for t in other_topics:
            v = series[t][i]
            if v is not None:
                values.append(v)
        control.append(sum(values) / len(values) if len(values) >= 2 else None)
    return control


def topic_urgency_lagged_correlations(
    database_url: str, max_lag: int = 3, r_threshold: float = 0.6, fdr_q: float = 0.05
) -> list[LaggedCorrelation]:
    """
    All-pairs lagged correlation over topic urgency scores. Directional:
    (A, B, lag) and (B, A, lag) are both computed, since a feedback loop
    (A drives B) is not the same claim as its reverse (B drives A).

    A *partial* correlation (lagged_partial_pearson), controlling out each
    side's own leave-one-out mean urgency across every other topic that
    run (_topic_urgency_control_series) -- see that function for why.
    """
    run_order, series = load_topic_urgency_series(database_url)

    def control_series(topic: str) -> list[float | None]:
        return _topic_urgency_control_series(topic, series, run_order)

    candidates: list[tuple[str, str, int, float, int]] = []
    for topic_a, topic_b in combinations(series, 2):
        for lag in range(max_lag + 1):
            for a, b in ((topic_a, topic_b), (topic_b, topic_a)):
                out = lagged_partial_pearson(
                    series[a], series[b], control_series(a), control_series(b), lag
                )
                if out is None:
                    continue
                r, n = out
                candidates.append((a, b, lag, r, n))
    return _significant_correlations(candidates, r_threshold, fdr_q, controls=2)


def _tags_with_enough_activity(
    series: dict[str, list[float | None]], min_active_runs: int
) -> set[str]:
    """
    Tags nonzero in at least `min_active_runs` runs -- see
    _MIN_ACTIVE_RUNS_FOR_TAG_CORRELATION for why this matters: below this
    floor, a "correlation" is often just two rare tags both being nonzero
    in the same handful of runs, not a real relationship. A None entry
    (see load_tag_rate_series -- a run whose tag_counts recording failed)
    counts as neither active nor inactive, same "skip rather than guess"
    treatment as everywhere else in this file.
    """
    return {
        tag
        for tag, rates in series.items()
        if sum(1 for rate in rates if rate is not None and rate > 0.0) >= min_active_runs
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


def _domain(link: str) -> str:
    """Network location of a URL (e.g. "www.orbex.com") -- a cheap proxy for "source"."""
    return urlparse(link).netloc


def _dominant_source_ratio(domains: Sequence[str]) -> tuple[float, int]:
    """
    (ratio, n) where n is the number of co-occurring articles and ratio is
    the fraction of them from the single most common domain. (0.0, 0) if
    there are no co-occurring articles at all.
    """
    if not domains:
        return 0.0, 0
    counts: dict[str, int] = {}
    for domain in domains:
        counts[domain] = counts.get(domain, 0) + 1
    return max(counts.values()) / len(domains), len(domains)


def _drop_single_source_pairs(
    edge_rows: list[tuple[str, str]],
    co_occurring_domains: dict[tuple[str, str], list[str]],
    max_source_dominance_ratio: float,
    min_co_occurring_articles: int,
) -> list[tuple[str, str]]:
    """
    Remove pairs whose source-dominance ratio is at or above
    max_source_dominance_ratio -- unless there are fewer than
    min_co_occurring_articles co-occurring articles to judge that from,
    in which case the pair is kept (not enough evidence, so don't guess).
    """
    kept = []
    for tag_a, tag_b in edge_rows:
        domains = co_occurring_domains.get((tag_a, tag_b), [])
        ratio, n = _dominant_source_ratio(domains)
        if n >= min_co_occurring_articles and ratio >= max_source_dominance_ratio:
            continue
        kept.append((tag_a, tag_b))
    return kept


def _topic_weights(tag_topic_rows: list[tuple[str, str, int]]) -> dict[str, dict[str, float]]:
    """
    {tag: {topic: weight}}, weight = that topic's share of the tag's total
    tag_topics rows across history -- e.g. "innovation" seen under
    Business 13x, Leadership 7x, Defense 6x, Artificial Intelligence 6x,
    ... out of N total gets {"Business": 13/N, "Leadership": 7/N, ...}.
    `tag_topic_rows` is (tag, topic, count) from "SELECT tag, topic,
    COUNT(*) FROM tag_topics GROUP BY tag, topic".

    A single mode/"primary" topic per tag isn't enough to control out a
    confound that spans several topics at once -- e.g. "drug discovery"
    (Biotechnology) and "innovation" (Business) rode the same broader
    "AI industry momentum" wave in the 2026-08-26 systems-thinking-
    prototype session despite having different single primary topics, so
    controlling each side by just its mode topic's volume missed it. A
    full weighted blend across every topic a tag has ever appeared under
    reflects the whole thematic spread it's actually drawn from, and
    still reduces to the old single-topic behavior for a tag whose
    coverage is concentrated in one topic (weight ~1.0 there).
    """
    totals: dict[str, dict[str, int]] = {}
    for tag, topic, count in tag_topic_rows:
        totals.setdefault(tag, {})
        totals[tag][topic] = totals[tag].get(topic, 0) + count
    weights: dict[str, dict[str, float]] = {}
    for tag, topic_counts in totals.items():
        total = sum(topic_counts.values())
        if total == 0:
            continue
        weights[tag] = {topic: count / total for topic, count in topic_counts.items()}
    return weights


def _weighted_topic_volume(
    tag: str,
    topic_weights: dict[str, dict[str, float]],
    topic_volume: dict[str, list[float | None]],
    run_order: list[str],
) -> list[float | None]:
    """
    Per-run control series for `tag`: the weighted sum, across every topic
    the tag has ever appeared under (weights from _topic_weights), of
    that topic's article volume that run. A run is a gap (None) unless at
    least one weighted topic has real (non-None) data that run -- see
    _topic_volume_series for why a run's article archive can be entirely
    missing. A tag with no known topic weights gets an all-zero series
    (no confound to control for, same as before).
    """
    weights = topic_weights.get(tag)
    if not weights:
        return [0.0] * len(run_order)
    series: list[float | None] = []
    for i in range(len(run_order)):
        total = 0.0
        any_known = False
        for topic, weight in weights.items():
            values = topic_volume.get(topic)
            value = values[i] if values is not None else 0.0
            if value is None:
                continue
            total += weight * value
            any_known = True
        series.append(total if any_known else None)
    return series


def _topic_volume_series(
    run_order: list[str],
    topic_article_counts: list[tuple[str, str, int]],
    runs_with_missing_articles: set[str],
) -> dict[str, list[float | None]]:
    """
    {topic: [article count that run, aligned to run_order]} from
    "SELECT run_id, topic, COUNT(*) FROM articles GROUP BY run_id, topic"
    -- the raw signal a shared-topic "busy week" confound would show up in.

    A run in `runs_with_missing_articles` gets None for every topic instead
    of 0.0. Without this, a run whose article archive failed (see
    article_archive.record_articles -- confirmed to have happened for two
    real runs, 2026-08-26 systems-thinking-prototype session) looks
    identical to a run that genuinely had zero articles on some topic: a
    false 0.0 biases lagged_partial_pearson's OLS fit rather than just
    shrinking its sample size the way a real gap correctly does.
    """
    run_index = {run_id: i for i, run_id in enumerate(run_order)}
    known_topics = {topic for _, topic, _ in topic_article_counts}
    series: dict[str, list[float | None]] = {
        topic: [0.0] * len(run_order) for topic in known_topics
    }
    for run_id, topic, count in topic_article_counts:
        idx = run_index.get(run_id)
        if idx is None:
            continue
        series[topic][idx] = float(count)
    for run_id in runs_with_missing_articles:
        idx = run_index.get(run_id)
        if idx is None:
            continue
        for topic in known_topics:
            series[topic][idx] = None
    return series


def tag_rate_lagged_correlations(
    database_url: str,
    max_lag: int = 3,
    r_threshold: float = 0.7,
    min_edge_weight: int = 2,
    fdr_q: float = 0.05,
    min_active_runs: int = _MIN_ACTIVE_RUNS_FOR_TAG_CORRELATION,
    max_containment_ratio: float = _MAX_CONTAINMENT_RATIO_FOR_TAG_CORRELATION,
    max_same_community_ratio: float = _MAX_SAME_COMMUNITY_RATIO_FOR_TAG_CORRELATION,
    max_source_dominance_ratio: float = _MAX_SOURCE_DOMINANCE_RATIO_FOR_TAG_CORRELATION,
) -> list[LaggedCorrelation]:
    """
    Lagged correlation over tag rates, restricted to tag pairs that have
    co-occurred at least once with weight >= min_edge_weight in tag_edges
    -- see module docstring for why an all-pairs scan over ~7k tags isn't
    the right default here -- further restricted to tags active in at
    least min_active_runs runs, so a pair isn't reported purely because
    both tags are rare and happened to be nonzero together, and with three
    kinds of structurally-trivial pairs dropped before any correlation is
    even computed:
      - near-synonymous pairs (containment ratio >= max_containment_ratio)
      - same-topic-cluster pairs (same-community ratio >= max_same_community_ratio)
      - single-source pairs (source-dominance ratio >= max_source_dominance_ratio)
        -- two tags that mostly co-occur because one recurring source
        republishes both together (e.g. a daily market-index digest
        mentioning both "dax" and "nikkei"), not because the tags
        themselves move together.
    None of these three is evidence of a cross-domain feedback loop.

    The correlation itself is a *partial* correlation (lagged_partial_pearson),
    controlling out each side's own topic article volume that run -- a
    weighted blend across every topic the tag has ever appeared under
    (_topic_weights), not just its single most common one, so a confound
    spanning several topics at once (e.g. "drug discovery" and
    "innovation" both riding a broader AI-industry-momentum wave despite
    having different single primary topics) gets removed too, not just a
    single busy topic (e.g. two Defense tags riding one busy Defense
    week). That pattern survives the three structural filters above since
    those look at tag-to-tag co-occurrence, not shared topic volume, and
    it isn't a sparsity or multiple-comparisons artifact either --
    confirmed by pulling actual articles in the 2026-08-26
    systems-thinking-prototype session.
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
        tag_totals: dict[str, int] = dict(
            conn.execute("SELECT tag, SUM(count) FROM tag_counts GROUP BY tag").fetchall()
        )
        tag_community_by_run: dict[str, dict[str, int]] = {}
        for run_id, tag, community_id in conn.execute(
            "SELECT run_id, tag, community_id FROM community_summary_tags"
        ).fetchall():
            tag_community_by_run.setdefault(run_id, {})[tag] = community_id
        topic_article_counts = conn.execute(
            "SELECT run_id, topic, COUNT(*) FROM articles GROUP BY run_id, topic"
        ).fetchall()
        run_article_counts: dict[str, int] = dict(
            conn.execute("SELECT run_id, article_count FROM runs").fetchall()
        )
        runs_with_articles = {
            row[0] for row in conn.execute("SELECT DISTINCT run_id FROM articles").fetchall()
        }
    runs_with_missing_articles = _runs_missing_from(run_article_counts, runs_with_articles)

    edge_rows = _drop_near_synonymous_pairs(
        edge_rows, co_occurrence_totals, tag_totals, max_containment_ratio
    )
    edge_rows = _drop_topically_clustered_pairs(
        edge_rows,
        tag_community_by_run,
        max_same_community_ratio,
        _MIN_SHARED_RUNS_FOR_COMMUNITY_FILTER,
    )

    # Scope the co-occurring-articles lookup to tags still in play after
    # the two filters above, then apply the single-source filter and
    # re-scope once more before pulling per-tag rate series.
    prefiltered_tags = list({t for a, b in edge_rows for t in (a, b)})
    with get_connection(database_url) as conn:
        co_occurring_links = conn.execute(
            "SELECT t1.tag, t2.tag, a.link "
            "FROM article_tags t1 "
            "JOIN article_tags t2 ON t1.article_id = t2.article_id AND t1.tag < t2.tag "
            "JOIN articles a ON a.id = t1.article_id "
            "WHERE t1.tag = ANY(%s) AND t2.tag = ANY(%s)",
            (prefiltered_tags, prefiltered_tags),
        ).fetchall()
    co_occurring_domains: dict[tuple[str, str], list[str]] = {}
    for tag_a, tag_b, link in co_occurring_links:
        co_occurring_domains.setdefault((tag_a, tag_b), []).append(_domain(link))
    edge_rows = _drop_single_source_pairs(
        edge_rows,
        co_occurring_domains,
        max_source_dominance_ratio,
        _MIN_CO_OCCURRING_ARTICLES_FOR_SOURCE_FILTER,
    )

    candidate_tags = {t for a, b in edge_rows for t in (a, b)}
    run_order, series = load_tag_rate_series(database_url, tags=candidate_tags)
    active_tags = _tags_with_enough_activity(series, min_active_runs)

    with get_connection(database_url) as conn:
        tag_topic_rows = conn.execute(
            "SELECT tag, topic, COUNT(*) FROM tag_topics WHERE tag = ANY(%s) "
            "GROUP BY tag, topic",
            (list(candidate_tags),),
        ).fetchall()
    topic_weights = _topic_weights(tag_topic_rows)
    topic_volume = _topic_volume_series(run_order, topic_article_counts, runs_with_missing_articles)

    def control_series(tag: str) -> list[float | None]:
        return _weighted_topic_volume(tag, topic_weights, topic_volume, run_order)

    candidates: list[tuple[str, str, int, float, int]] = []
    for tag_a, tag_b in edge_rows:
        if tag_a not in active_tags or tag_b not in active_tags:
            continue
        for lag in range(max_lag + 1):
            for a, b in ((tag_a, tag_b), (tag_b, tag_a)):
                out = lagged_partial_pearson(
                    series[a], series[b], control_series(a), control_series(b), lag
                )
                if out is None:
                    continue
                r, n = out
                candidates.append((a, b, lag, r, n))
    return _significant_correlations(candidates, r_threshold, fdr_q, controls=2)
