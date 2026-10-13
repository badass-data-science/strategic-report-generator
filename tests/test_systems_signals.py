"""
Tests for strategic_reports.daily.core.systems_signals: the five tag-rate
filters, the topic-volume and leave-one-out trend controls, and
article-archive-gap handling. The correlation, p-value and FDR arithmetic
those build on lives in stats.py and is tested in test_stats.py.

Pure-function tests against synthetic series -- deliberately no
database_url fixture here, so they exercise the logic independent of how
much real history the live tracking database has.
"""

from typing import cast

import pytest
from strategic_reports.daily.core.systems_signals import (
    _containment_ratio,
    _domain,
    _dominant_source_ratio,
    _drop_near_synonymous_pairs,
    _drop_single_source_pairs,
    _drop_topically_clustered_pairs,
    _runs_missing_from,
    _same_community_ratio,
    _tags_with_enough_activity,
    _topic_urgency_control_series,
    _topic_volume_series,
    _topic_weights,
    _weighted_topic_volume,
)


def test_tags_with_enough_activity_excludes_rare_tags() -> None:
    # `cast` compensates for a known mypy limitation (list is invariant,
    # and `[0.0] * 12 + [0.1, 0.2]` infers as list[float] via __add__
    # before a list[float | None] annotation/context can apply to it --
    # plain `*` alone is fine, `+` isn't). These are valid
    # list[float | None] values at runtime; mypy just can't infer that
    # through concatenation on its own.
    rare_a = cast("list[float | None]", [0.0] * 12 + [0.1, 0.2])  # nonzero in only 2 runs
    rare_b = cast("list[float | None]", [0.0] * 13 + [0.3])  # nonzero in only 1 run
    common: list[float | None] = [0.1] * 14  # nonzero in every run
    series = {"rare_a": rare_a, "rare_b": rare_b, "common": common}

    active = _tags_with_enough_activity(series, min_active_runs=5)

    assert active == {"common"}


def test_tags_with_enough_activity_boundary_is_inclusive() -> None:
    exactly_five = cast("list[float | None]", [0.1] * 5 + [0.0] * 9)
    series = {"exactly_five": exactly_five}

    assert _tags_with_enough_activity(series, min_active_runs=5) == {"exactly_five"}
    assert _tags_with_enough_activity(series, min_active_runs=6) == set()


def test_tags_with_enough_activity_none_entries_count_as_neither() -> None:
    # A None entry (a run whose tag_counts recording failed entirely, see
    # load_tag_rate_series) must not crash the `rate > 0.0` comparison, and
    # must not count toward activity either way -- it's unknown, not zero.
    series = {
        "tag": [0.1, 0.2, 0.3, 0.4, None, None, None, None, None],  # 4 confirmed-active runs
    }

    assert _tags_with_enough_activity(series, min_active_runs=4) == {"tag"}
    assert _tags_with_enough_activity(series, min_active_runs=5) == set()


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


def test_same_community_ratio_always_together() -> None:
    tag_community_by_run = {
        "run1": {"electric utility": 0, "power generation": 0},
        "run2": {"electric utility": 3, "power generation": 3},
        "run3": {"electric utility": 1, "power generation": 1},
    }

    ratio, n = _same_community_ratio("electric utility", "power generation", tag_community_by_run)

    assert ratio == pytest.approx(1.0)
    assert n == 3


def test_same_community_ratio_never_together() -> None:
    tag_community_by_run = {
        "run1": {"energy": 0, "forex": 4},
        "run2": {"energy": 1, "forex": 5},
    }

    ratio, n = _same_community_ratio("energy", "forex", tag_community_by_run)

    assert ratio == pytest.approx(0.0)
    assert n == 2


def test_same_community_ratio_ignores_runs_missing_either_tag() -> None:
    tag_community_by_run = {
        "run1": {"a": 0, "b": 0},  # both present, same community
        "run2": {"a": 0},  # b missing this run (e.g. pruned out) -- not counted
    }

    ratio, n = _same_community_ratio("a", "b", tag_community_by_run)

    assert ratio == pytest.approx(1.0)
    assert n == 1


def test_same_community_ratio_no_shared_runs() -> None:
    assert _same_community_ratio("a", "b", {}) == (0.0, 0)


def test_drop_topically_clustered_pairs_excludes_high_overlap() -> None:
    edge_rows = [("electric utility", "power generation"), ("energy", "forex")]
    tag_community_by_run = {
        "run1": {"electric utility": 0, "power generation": 0, "energy": 1, "forex": 4},
        "run2": {"electric utility": 3, "power generation": 3, "energy": 1, "forex": 5},
        "run3": {"electric utility": 1, "power generation": 1, "energy": 2, "forex": 6},
    }

    kept = _drop_topically_clustered_pairs(
        edge_rows, tag_community_by_run, max_same_community_ratio=0.8, min_shared_runs=3
    )

    assert kept == [("energy", "forex")]


def test_drop_topically_clustered_pairs_keeps_pairs_below_min_shared_runs() -> None:
    # High overlap, but only 2 runs of evidence -- min_shared_runs=3 means
    # "not enough evidence to call it a cluster," so the pair survives.
    edge_rows = [("a", "b")]
    tag_community_by_run = {
        "run1": {"a": 0, "b": 0},
        "run2": {"a": 1, "b": 1},
    }

    kept = _drop_topically_clustered_pairs(
        edge_rows, tag_community_by_run, max_same_community_ratio=0.8, min_shared_runs=3
    )

    assert kept == [("a", "b")]


def test_topic_weights_splits_proportionally() -> None:
    rows = [("tagA", "Defense", 6), ("tagA", "Economics", 2), ("tagB", "Forex", 3)]

    weights = _topic_weights(rows)

    assert weights["tagA"] == pytest.approx({"Defense": 0.75, "Economics": 0.25})
    assert weights["tagB"] == pytest.approx({"Forex": 1.0})


def test_topic_weights_sums_duplicate_rows_for_the_same_tag_and_topic() -> None:
    # Multiple GROUP BY rows for the same (tag, topic) shouldn't happen from
    # the real query, but the function should still be correct if they did.
    rows = [("tagA", "Defense", 3), ("tagA", "Defense", 2), ("tagA", "Economics", 5)]

    weights = _topic_weights(rows)

    assert weights["tagA"] == pytest.approx({"Defense": 0.5, "Economics": 0.5})


def test_weighted_topic_volume_blends_by_weight() -> None:
    run_order = ["r1", "r2"]
    topic_weights = {"tagA": {"Defense": 0.75, "Economics": 0.25}}
    topic_volume: dict[str, list[float | None]] = {
        "Defense": [10.0, 20.0],
        "Economics": [4.0, 8.0],
    }

    series = _weighted_topic_volume("tagA", topic_weights, topic_volume, run_order)

    # r1: 0.75*10 + 0.25*4 = 8.5;  r2: 0.75*20 + 0.25*8 = 17.0
    assert series == pytest.approx([8.5, 17.0])


def test_weighted_topic_volume_reduces_to_single_topic_case() -> None:
    # A tag whose coverage is concentrated in one topic behaves exactly
    # like the old _primary_topics-based control did.
    run_order = ["r1", "r2"]
    topic_weights = {"tagA": {"Defense": 1.0}}
    topic_volume: dict[str, list[float | None]] = {"Defense": [10.0, 5.0]}

    assert _weighted_topic_volume("tagA", topic_weights, topic_volume, run_order) == [10.0, 5.0]


def test_weighted_topic_volume_no_weights_returns_all_zero() -> None:
    run_order = ["r1", "r2", "r3"]

    assert _weighted_topic_volume("unknown_tag", {}, {}, run_order) == [0.0, 0.0, 0.0]


def test_weighted_topic_volume_gap_only_when_every_weighted_topic_is_none() -> None:
    run_order = ["r1", "r2"]
    topic_weights = {"tagA": {"Defense": 0.5, "Economics": 0.5}}
    # r2: Defense missing (archive failure) but Economics known -- still
    # computable from what's known, not a full gap.
    topic_volume: dict[str, list[float | None]] = {
        "Defense": [10.0, None],
        "Economics": [4.0, 8.0],
    }

    series = _weighted_topic_volume("tagA", topic_weights, topic_volume, run_order)

    assert series[0] == pytest.approx(7.0)  # 0.5*10 + 0.5*4
    assert series[1] == pytest.approx(4.0)  # only Economics known: 0.5*8


def test_weighted_topic_volume_full_gap_when_all_weighted_topics_are_none() -> None:
    run_order = ["r1", "r2"]
    topic_weights = {"tagA": {"Defense": 0.5, "Economics": 0.5}}
    topic_volume = {
        "Defense": [10.0, None],
        "Economics": [4.0, None],
    }

    series = _weighted_topic_volume("tagA", topic_weights, topic_volume, run_order)

    assert series == [pytest.approx(7.0), None]


def test_topic_urgency_control_series_excludes_the_topic_itself() -> None:
    run_order = ["r1", "r2"]
    series: dict[str, list[float | None]] = {
        "Energy": [0.5, 0.6],
        "Forex": [0.7, 0.8],
        "Defense": [0.3, 0.9],
    }

    control = _topic_urgency_control_series("Energy", series, run_order)

    # r1: mean(Forex=0.7, Defense=0.3) = 0.5;  r2: mean(0.8, 0.9) = 0.85
    assert control == pytest.approx([0.5, 0.85])


def test_topic_urgency_control_series_ignores_none_entries() -> None:
    run_order = ["r1"]
    series: dict[str, list[float | None]] = {
        "Energy": [0.5],
        "Forex": [0.7],
        "Geopolitics": [0.9],
        "Defense": [None],
    }

    control = _topic_urgency_control_series("Energy", series, run_order)

    # Defense is None that run -- mean(Forex=0.7, Geopolitics=0.9) = 0.8
    assert control == [pytest.approx(0.8)]


def test_topic_urgency_control_series_none_when_fewer_than_two_other_topics_known() -> None:
    run_order = ["r1"]
    series: dict[str, list[float | None]] = {
        "Energy": [0.5],
        "Forex": [None],
        "Defense": [None],
    }

    assert _topic_urgency_control_series("Energy", series, run_order) == [None]


def test_topic_volume_series_aligns_to_run_order_and_ignores_unknown_runs() -> None:
    run_order = ["r1", "r2"]
    rows = [
        ("r1", "Defense", 10),
        ("r2", "Defense", 5),
        ("r1", "Economics", 3),
        ("r99", "Defense", 999),
    ]

    series = _topic_volume_series(run_order, rows, runs_with_missing_articles=set())

    assert series == {"Defense": [10.0, 5.0], "Economics": [3.0, 0.0]}


def test_topic_volume_series_marks_missing_runs_as_none_not_zero() -> None:
    # r2's article archive failed (see article_archive.record_articles) --
    # every topic must show None there, not a false 0.0 that would claim
    # "this topic genuinely had zero coverage" when the truth is unknown.
    run_order = ["r1", "r2", "r3"]
    rows = [
        ("r1", "Defense", 10),
        ("r3", "Defense", 7),
        ("r1", "Economics", 3),
        ("r3", "Economics", 2),
    ]

    series = _topic_volume_series(run_order, rows, runs_with_missing_articles={"r2"})

    assert series == {"Defense": [10.0, None, 7.0], "Economics": [3.0, None, 2.0]}


def test_runs_missing_from_excludes_genuinely_empty_runs() -> None:
    # r1: has data, present. r2: article_count > 0 but absent from the
    # table -- recording failed. r3: article_count == 0, genuinely nothing
    # to record, correctly NOT flagged even though it's also absent.
    run_article_counts = {"r1": 500, "r2": 300, "r3": 0}
    runs_present = {"r1"}

    assert _runs_missing_from(run_article_counts, runs_present) == {"r2"}


def test_runs_missing_from_empty_when_everything_present() -> None:
    run_article_counts = {"r1": 500, "r2": 300}
    runs_present = {"r1", "r2"}

    assert _runs_missing_from(run_article_counts, runs_present) == set()


def test_domain_extracts_netloc() -> None:
    assert _domain("https://www.orbex.com/blog/en/2026/08/intraday-analysis-18-08-2026") == (
        "www.orbex.com"
    )


def test_domain_different_paths_same_domain() -> None:
    a = _domain("https://www.armstrongeconomics.com/market-talk/market-talk-august-17-2026/")
    b = _domain("https://www.armstrongeconomics.com/market-talk/market-talk-august-18-2026/")
    assert a == b == "www.armstrongeconomics.com"


def test_dominant_source_ratio_all_one_domain() -> None:
    domains = ["www.orbex.com"] * 5

    ratio, n = _dominant_source_ratio(domains)

    assert ratio == pytest.approx(1.0)
    assert n == 5


def test_dominant_source_ratio_spread_across_domains() -> None:
    domains = ["a.com", "b.com", "c.com", "d.com"]

    ratio, n = _dominant_source_ratio(domains)

    assert ratio == pytest.approx(0.25)
    assert n == 4


def test_dominant_source_ratio_empty() -> None:
    assert _dominant_source_ratio([]) == (0.0, 0)


def test_drop_single_source_pairs_excludes_high_dominance() -> None:
    edge_rows = [("dax", "nikkei"), ("energy", "forex")]
    co_occurring_domains = {
        ("dax", "nikkei"): ["www.armstrongeconomics.com"] * 4 + ["other.com"],
        ("energy", "forex"): ["a.com", "b.com", "c.com", "d.com", "e.com"],
    }

    kept = _drop_single_source_pairs(
        edge_rows, co_occurring_domains, max_source_dominance_ratio=0.8, min_co_occurring_articles=3
    )

    assert kept == [("energy", "forex")]


def test_drop_single_source_pairs_keeps_pairs_below_min_evidence() -> None:
    # 2 co-occurring articles, both the same domain (ratio 1.0) -- but
    # min_co_occurring_articles=3 means "not enough evidence," so kept.
    edge_rows = [("a", "b")]
    co_occurring_domains = {("a", "b"): ["x.com", "x.com"]}

    kept = _drop_single_source_pairs(
        edge_rows, co_occurring_domains, max_source_dominance_ratio=0.8, min_co_occurring_articles=3
    )

    assert kept == [("a", "b")]


def test_drop_single_source_pairs_keeps_pairs_with_no_recorded_co_occurrence() -> None:
    # A pair absent from co_occurring_domains entirely (e.g. all its
    # co-occurrences happened during a run with a missing article archive)
    # must not be penalized for evidence it doesn't have.
    edge_rows = [("a", "b")]

    kept = _drop_single_source_pairs(
        edge_rows, {}, max_source_dominance_ratio=0.8, min_co_occurring_articles=3
    )

    assert kept == [("a", "b")]
