"""
Tests for strategic_reports.daily.core.systems_signals's lagged_pearson.

Pure-function tests against synthetic series -- deliberately no
database_url fixture here. The live tracking database only has a handful
of real runs since the PostgreSQL migration (2026-08-13), nowhere near
_MIN_RUNS_FOR_LAG, so a DB-backed test would only ever exercise the
"too little history, return None" path. Correctness of the correlation
arithmetic itself is validated here instead, independent of how much real
history exists.
"""

import pytest
from strategic_reports.daily.core.systems_signals import (
    _benjamini_hochberg_qvalues,
    _containment_ratio,
    _drop_near_synonymous_pairs,
    _pearson_p_value,
    _tags_with_enough_activity,
    lagged_pearson,
)

_N = 10  # >= _MIN_RUNS_FOR_LAG (8), so these exercise the real computation


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
    assert _pearson_p_value(0.0, 20) == pytest.approx(1.0)


def test_pearson_p_value_is_zero_for_perfect_correlation() -> None:
    assert _pearson_p_value(1.0, 20) == pytest.approx(0.0)
    assert _pearson_p_value(-1.0, 20) == pytest.approx(0.0)


def test_pearson_p_value_symmetric_in_sign_of_r() -> None:
    assert _pearson_p_value(0.6, 15) == pytest.approx(_pearson_p_value(-0.6, 15))


def test_pearson_p_value_decreases_as_r_grows_for_fixed_n() -> None:
    p_small = _pearson_p_value(0.3, 15)
    p_large = _pearson_p_value(0.8, 15)
    assert p_large < p_small


def test_pearson_p_value_matches_textbook_critical_value() -> None:
    # Standard two-tailed r-critical-value table: df=10 (n=12), alpha=.05 -> r ~= 0.576.
    assert _pearson_p_value(0.576, 12) == pytest.approx(0.05, abs=0.01)


def test_benjamini_hochberg_qvalues_matches_hand_worked_example() -> None:
    p_values = [0.005, 0.011, 0.02, 0.04, 0.13, 0.26, 0.35, 0.5, 0.7, 0.9]

    q_values = _benjamini_hochberg_qvalues(p_values)

    expected = [0.05, 0.055, 0.0667, 0.1, 0.26, 0.4333, 0.5, 0.625, 0.7778, 0.9]
    for actual, want in zip(q_values, expected, strict=True):
        assert actual == pytest.approx(want, abs=1e-3)


def test_benjamini_hochberg_qvalues_never_below_the_raw_p_value() -> None:
    p_values = [0.001, 0.2, 0.03, 0.9, 0.5]

    q_values = _benjamini_hochberg_qvalues(p_values)

    for p, q in zip(p_values, q_values, strict=True):
        assert q >= p


def test_benjamini_hochberg_qvalues_empty_input() -> None:
    assert _benjamini_hochberg_qvalues([]) == []


def test_tags_with_enough_activity_excludes_rare_tags() -> None:
    series = {
        "rare_a": [0.0] * 12 + [0.1, 0.2],  # nonzero in only 2 runs
        "rare_b": [0.0] * 13 + [0.3],  # nonzero in only 1 run
        "common": [0.1] * 14,  # nonzero in every run
    }

    active = _tags_with_enough_activity(series, min_active_runs=5)

    assert active == {"common"}


def test_tags_with_enough_activity_boundary_is_inclusive() -> None:
    series = {"exactly_five": [0.1] * 5 + [0.0] * 9}

    assert _tags_with_enough_activity(series, min_active_runs=5) == {"exactly_five"}
    assert _tags_with_enough_activity(series, min_active_runs=6) == set()


def test_containment_ratio_always_together() -> None:
    # "ugg" and "deckers outdoor" appear on exactly the same 10 articles.
    assert _containment_ratio(co_occurrence=10, count_a=10, count_b=10) == pytest.approx(1.0)


def test_containment_ratio_uses_the_rarer_tag_as_denominator() -> None:
    # tag_a co-occurs with tag_b every single time tag_a shows up, even
    # though tag_b (the more common tag) mostly appears without tag_a.
    assert _containment_ratio(co_occurrence=5, count_a=5, count_b=500) == pytest.approx(1.0)


def test_containment_ratio_rarely_together() -> None:
    assert _containment_ratio(co_occurrence=2, count_a=100, count_b=100) == pytest.approx(0.02)


def test_containment_ratio_zero_denominator() -> None:
    assert _containment_ratio(co_occurrence=0, count_a=0, count_b=0) == 0.0


def test_drop_near_synonymous_pairs_excludes_high_containment() -> None:
    edge_rows = [("qualcomm", "wireless technology"), ("energy", "forex")]
    co_occurrence_totals = {
        ("qualcomm", "wireless technology"): 20,
        ("energy", "forex"): 3,
    }
    tag_totals = {"qualcomm": 20, "wireless technology": 25, "energy": 50, "forex": 60}

    kept = _drop_near_synonymous_pairs(
        edge_rows, co_occurrence_totals, tag_totals, max_containment_ratio=0.8
    )

    assert kept == [("energy", "forex")]


def test_drop_near_synonymous_pairs_boundary_is_exclusive() -> None:
    # containment ratio hits the threshold exactly -- ">=" excludes it, not "<".
    edge_rows = [("a", "b")]
    co_occurrence_totals = {("a", "b"): 8}
    tag_totals = {"a": 10, "b": 10}

    kept = _drop_near_synonymous_pairs(
        edge_rows, co_occurrence_totals, tag_totals, max_containment_ratio=0.8
    )

    assert kept == []
