"""
Shared statistical procedures for the tracking database's per-run time
series. Pure functions over plain sequences -- no database access, no
scipy/numpy dependency (the project deliberately carries neither, so the
t-distribution p-value is computed from scratch via the incomplete beta
function).

  - lagged_pearson / lagged_partial_pearson: Pearson r between x[t] and
    y[t + lag], optionally after regressing each side on its own same-run
    confound series (OLS residualization).
  - pearson_p_value: two-tailed p-value for r, charging a degree of freedom
    per control variable.
  - benjamini_hochberg_qvalues: FDR-adjusted p-values across one batch.
  - leave_one_out_mean: per-run mean of every OTHER series -- a shared-trend
    control ("how busy was this run overall").
  - population_mean_std: the baseline behind the z-score alerts.

Used by systems_signals.py, urgency.py and tag_tracking.py, and by the
scripts under prototypes/causal_loops/.
"""

import math
from collections.abc import Mapping, Sequence

# Minimum aligned (x, y) pairs remaining after a lag shift before a
# correlation is reported at all -- below this, r is noise dressed up as a
# number. Deliberately higher than tag_tracking's _MIN_HISTORY_RUNS=7: that
# gate guards a single tag's mean/std, this one guards a correlation
# between two already-shortened series.
MIN_RUNS_FOR_LAG = 8


def _pearson(xs: list[float], ys: list[float]) -> tuple[float, int] | None:
    """Plain Pearson r over two already-aligned, gap-free equal-length lists."""
    n = len(xs)
    if n < MIN_RUNS_FOR_LAG:
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
    both are defined. None if fewer than MIN_RUNS_FOR_LAG aligned pairs
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

    if len(xs) < MIN_RUNS_FOR_LAG:
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
    relative to plain lagged_pearson (see pearson_p_value's `controls`).

    A None in zx/zy excludes that t the same way a None in x/y does --
    "we don't know this run's confound value" (e.g. a run whose article
    archive failed, see article_archive.record_articles) must not be
    silently treated as "confound was zero," which would bias the OLS fit
    rather than just shrinking n.

    Built for systems_signals.tag_rate_lagged_correlations' topic-volume
    control: two tags from the same busy topic (e.g. Defense) can ride that
    topic's article volume together at lag>0 without one predicting the
    other -- see that module's docstring.
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

    if len(xs) < MIN_RUNS_FOR_LAG:
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
    """I_x(a, b) -- used by pearson_p_value to get a t-distribution p-value without scipy."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.log(x) * a + math.log(1.0 - x) * b - _log_beta(a, b))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def pearson_p_value(r: float, n: int, controls: int = 0) -> float:
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


def benjamini_hochberg_qvalues(p_values: Sequence[float]) -> list[float]:
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


def leave_one_out_mean(
    series: Mapping[str, Sequence[float | None]], exclude: str, length: int, min_others: int = 2
) -> list[float | None]:
    """
    Per-index mean across every series in `series` except `exclude`,
    ignoring None entries. None at an index where fewer than `min_others`
    other series have a value -- too few to estimate a shared trend from.
    `length` is the number of indices (runs) to produce.
    """
    # See lagged_pearson for why this is an explicit loop with a local
    # variable (v), not a comprehension re-indexing series[k][i] twice --
    # mypy can't narrow the second re-evaluation to float.
    others = [k for k in series if k != exclude]
    out: list[float | None] = []
    for i in range(length):
        values: list[float] = []
        for k in others:
            v = series[k][i]
            if v is not None:
                values.append(v)
        out.append(sum(values) / len(values) if len(values) >= min_others else None)
    return out


def population_mean_std(values: Sequence[float]) -> tuple[float, float]:
    """
    Mean and population standard deviation (divides by n, not n - 1) of a
    non-empty sequence -- the historical baseline both urgency.check_alerts
    and tag_tracking.check_emerging_tags compute a z-score against.
    """
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return mean, math.sqrt(variance)
