"""
Tests for strategic_reports.daily.core.stats: lagged and partial Pearson
correlation, the scipy-free p-value, Benjamini-Hochberg q-values, the
leave-one-out mean control and the population mean/std behind the z-score
alerts. Pure-function tests against synthetic series, no database.
"""


import pytest
from strategic_reports.daily.core.stats import (
    _ols_residuals,
    benjamini_hochberg_qvalues,
    lagged_partial_pearson,
    lagged_pearson,
    leave_one_out_mean,
    pearson_p_value,
    population_mean_std,
)

_N = 10  # >= MIN_RUNS_FOR_LAG (8), so these exercise the real computation


def test_lagged_pearson_perfect_positive_correlation_at_correct_lag() -> None:
    x = [float(i) for i in range(_N)]
    y: list[float | None] = [None, None] + [float(i) for i in range(_N)]  # y[t] = x[t - 2]

    out = lagged_pearson(x, y, lag=2)

    assert out is not None
    r, n = out
    assert r == 1.0
    assert n == _N - 2  # the shift by 2 leaves only 8 of the 10 points aligned


def test_lagged_pearson_wrong_lag_flips_sign_on_an_oscillating_series() -> None:
    # A monotonic ramp stays perfectly correlated with itself at any lag (still
    # linear in t either way), so it can't distinguish "right lag" from "wrong
    # lag" -- an oscillating series can: shifting it by the wrong amount lands
    # exactly out of phase.
    x = [float(i % 2) for i in range(12)]
    y: list[float | None] = [None] + x[:-1]  # y[t] = x[t - 1]

    right_lag = lagged_pearson(x, y, lag=1)
    wrong_lag = lagged_pearson(x, y, lag=0)

    assert right_lag is not None and wrong_lag is not None
    assert right_lag[0] == pytest.approx(1.0)
    assert wrong_lag[0] == pytest.approx(-1.0)


def test_lagged_pearson_perfect_negative_correlation() -> None:
    x = [float(i) for i in range(_N)]
    y = [float(-i) for i in range(_N)]

    out = lagged_pearson(x, y, lag=0)

    assert out is not None
    r, _ = out
    assert r == -1.0


def test_lagged_pearson_below_min_history_returns_none() -> None:
    x = [1.0, 2.0, 3.0]
    y = [1.0, 2.0, 3.0]

    assert lagged_pearson(x, y, lag=0) is None


def test_lagged_pearson_zero_variance_returns_none() -> None:
    x = [1.0] * _N  # constant -- variance is 0, correlation is undefined
    y = [float(i) for i in range(_N)]

    assert lagged_pearson(x, y, lag=0) is None


def test_lagged_pearson_skips_gaps_from_none_entries() -> None:
    x = [1.0, 2.0, None, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    y = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

    out = lagged_pearson(x, y, lag=0)

    assert out is not None
    r, n = out
    assert n == _N - 1  # the one None pair dropped, not coerced to 0
    assert r == 1.0


def test_pearson_p_value_is_one_for_zero_correlation() -> None:
    # t = 0 regardless of n, so the two-tailed p-value is exactly 1.
    assert pearson_p_value(0.0, 20) == pytest.approx(1.0)


def test_pearson_p_value_is_zero_for_perfect_correlation() -> None:
    assert pearson_p_value(1.0, 20) == pytest.approx(0.0)
    assert pearson_p_value(-1.0, 20) == pytest.approx(0.0)


def test_pearson_p_value_symmetric_in_sign_of_r() -> None:
    assert pearson_p_value(0.6, 15) == pytest.approx(pearson_p_value(-0.6, 15))


def test_pearson_p_value_decreases_as_r_grows_for_fixed_n() -> None:
    p_small = pearson_p_value(0.3, 15)
    p_large = pearson_p_value(0.8, 15)
    assert p_large < p_small


def test_pearson_p_value_matches_textbook_critical_value() -> None:
    # Standard two-tailed r-critical-value table: df=10 (n=12), alpha=.05 -> r ~= 0.576.
    assert pearson_p_value(0.576, 12) == pytest.approx(0.05, abs=0.01)


def test_benjamini_hochberg_qvalues_matches_hand_worked_example() -> None:
    p_values = [0.005, 0.011, 0.02, 0.04, 0.13, 0.26, 0.35, 0.5, 0.7, 0.9]

    q_values = benjamini_hochberg_qvalues(p_values)

    expected = [0.05, 0.055, 0.0667, 0.1, 0.26, 0.4333, 0.5, 0.625, 0.7778, 0.9]
    for actual, want in zip(q_values, expected, strict=True):
        assert actual == pytest.approx(want, abs=1e-3)


def test_benjamini_hochberg_qvalues_never_below_the_raw_p_value() -> None:
    p_values = [0.001, 0.2, 0.03, 0.9, 0.5]

    q_values = benjamini_hochberg_qvalues(p_values)

    for p, q in zip(p_values, q_values, strict=True):
        assert q >= p


def test_benjamini_hochberg_qvalues_empty_input() -> None:
    assert benjamini_hochberg_qvalues([]) == []


def test_ols_residuals_perfect_linear_relationship() -> None:
    z = [1.0, 2.0, 3.0, 4.0]
    w = [2.0, 4.0, 6.0, 8.0]  # w = 2z exactly

    residuals = _ols_residuals(z, w)

    for r in residuals:
        assert r == pytest.approx(0.0, abs=1e-9)


def test_ols_residuals_zero_variance_control_returns_w_unchanged() -> None:
    z = [5.0, 5.0, 5.0, 5.0]
    w = [1.0, 2.0, 3.0, 4.0]

    assert _ols_residuals(z, w) == w


def test_lagged_partial_pearson_strips_a_shared_confound() -> None:
    # Z is a step confound (0 for the first half, 10 for the second) that
    # drives X and Y together; noise_x/noise_y are hand-constructed to be
    # exactly orthogonal (sum of elementwise products = 0) and mean-zero
    # within each Z level, so OLS recovers them as residuals exactly.
    z = [0.0, 0.0, 0.0, 0.0, 10.0, 10.0, 10.0, 10.0]
    noise_x = [1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0]
    noise_y = [1.0, 1.0, -1.0, -1.0, -1.0, -1.0, 1.0, 1.0]
    x = [z[i] + noise_x[i] for i in range(8)]
    y = [z[i] + noise_y[i] for i in range(8)]

    raw = lagged_pearson(x, y, lag=0)
    partial = lagged_partial_pearson(x, y, z, z, lag=0)

    assert raw is not None and partial is not None
    raw_r, _ = raw
    partial_r, partial_n = partial
    assert raw_r > 0.9  # dominated by the shared confound
    assert partial_r == pytest.approx(0.0, abs=1e-9)  # confound removed
    assert partial_n == 8


def test_lagged_partial_pearson_below_min_history_returns_none() -> None:
    x = [1.0, 2.0, 3.0]
    y = [1.0, 2.0, 3.0]
    z = [1.0, 2.0, 3.0]

    assert lagged_partial_pearson(x, y, z, z, lag=0) is None


def test_lagged_partial_pearson_excludes_a_none_control_point() -> None:
    # Same 8-point confound setup as the "strips a shared confound" test,
    # plus a 9th point with wild x/y values but a None control -- a run
    # whose topic-volume data is unknown (see _topic_volume_series). If
    # that None were ever treated as a 0.0 confound instead of a gap, the
    # extreme 9th point would corrupt the residuals and change the result.
    z = [0.0, 0.0, 0.0, 0.0, 10.0, 10.0, 10.0, 10.0, None]
    x = [1.0, -1.0, 1.0, -1.0, 11.0, 9.0, 11.0, 9.0, 99999.0]
    y = [1.0, 1.0, -1.0, -1.0, 9.0, 9.0, 11.0, 11.0, -99999.0]

    partial = lagged_partial_pearson(x, y, z, z, lag=0)

    assert partial is not None
    partial_r, partial_n = partial
    assert partial_n == 8  # the None-control point dropped, not coerced
    assert partial_r == pytest.approx(0.0, abs=1e-9)


def test_leave_one_out_mean_excludes_the_named_series() -> None:
    series: dict[str, list[float | None]] = {"a": [1.0, 2.0], "b": [3.0, 4.0], "c": [5.0, 6.0]}
    assert leave_one_out_mean(series, "a", 2) == [4.0, 5.0]


def test_leave_one_out_mean_ignores_none_and_needs_min_others() -> None:
    series: dict[str, list[float | None]] = {"a": [1.0, 1.0], "b": [3.0, None], "c": [5.0, 6.0]}
    # index 1 has only one other known value (c=6.0), below the default of 2
    assert leave_one_out_mean(series, "a", 2) == [4.0, None]
    assert leave_one_out_mean(series, "a", 2, min_others=1) == [4.0, 6.0]


def test_population_mean_std_divides_by_n() -> None:
    mean, std = population_mean_std([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    assert mean == pytest.approx(5.0)
    assert std == pytest.approx(2.0)  # population std; the sample std would be ~2.14


def test_population_mean_std_constant_series_has_zero_std() -> None:
    assert population_mean_std([3.0, 3.0, 3.0]) == (3.0, 0.0)
