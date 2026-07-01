"""
Tests for strategic_reports.daily.core.urgency.

Covers:
  - Absolute threshold (no history, thin history, std==0 fallback)
  - Statistical z-score (sufficient varied history)
  - No-alert cases (below threshold / within z-score band)
  - load/append/roundtrip
  - Ordering guarantee: load → check → append (current run never biases itself)
"""

import json

import pytest

from strategic_reports.daily.core.models import StrategicInsight, TopicConfig, TopicResult
from strategic_reports.daily.core.urgency import (
    UrgencyAlert,
    append_run,
    check_alerts,
    load_history,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(title: str) -> TopicConfig:
    return TopicConfig(slug=f"feeds_{title.lower()}", title=title, feeds_file="/dev/null")


def _make_result(title: str, score: float) -> TopicResult:
    return TopicResult(
        config=_make_config(title),
        strategy=StrategicInsight(
            bullets=["A.", "B.", "C."],
            urgency_score=score,
        ),
    )


def _append_n_runs(history_path, topic: str, scores: list[float]) -> None:
    """Append one historical run per score value for a topic."""
    for i, s in enumerate(scores):
        append_run(history_path, [_make_result(topic, s)], run_id=f"run-{i}")


# ---------------------------------------------------------------------------
# Absolute threshold tests (no / thin history)
# ---------------------------------------------------------------------------

class TestAbsoluteThreshold:
    def test_no_alert_no_history_below_threshold(self, tmp_path):
        history_path = tmp_path / "urgency_history.json"
        alerts = check_alerts(
            [_make_result("AI", 0.5)],
            load_history(history_path),
            absolute_threshold=0.8,
        )
        assert alerts == []

    def test_alert_no_history_above_threshold(self, tmp_path):
        history_path = tmp_path / "urgency_history.json"
        alerts = check_alerts(
            [_make_result("AI", 0.9)],
            load_history(history_path),
            absolute_threshold=0.8,
        )
        assert len(alerts) == 1
        assert alerts[0].reason == "absolute"
        assert alerts[0].topic == "AI"
        assert alerts[0].score == pytest.approx(0.9)

    def test_alert_thin_history_above_threshold(self, tmp_path):
        history_path = tmp_path / "urgency_history.json"
        # Only 6 runs — below _MIN_HISTORY_RUNS=7, so still uses absolute check.
        _append_n_runs(history_path, "AI", [0.3, 0.4, 0.35, 0.42, 0.38, 0.41])
        alerts = check_alerts(
            [_make_result("AI", 0.85)],
            load_history(history_path),
            absolute_threshold=0.8,
        )
        assert len(alerts) == 1
        assert alerts[0].reason == "absolute"

    def test_no_alert_thin_history_below_threshold(self, tmp_path):
        history_path = tmp_path / "urgency_history.json"
        _append_n_runs(history_path, "AI", [0.3, 0.4, 0.35, 0.42, 0.38, 0.41])
        alerts = check_alerts(
            [_make_result("AI", 0.6)],
            load_history(history_path),
            absolute_threshold=0.8,
        )
        assert alerts == []

    def test_absolute_fallback_when_std_zero(self, tmp_path):
        """
        When >=7 runs all have the same score, std==0.
        The statistical check cannot fire, so we fall back to absolute threshold.
        """
        history_path = tmp_path / "urgency_history.json"
        # 7 identical scores → std=0
        _append_n_runs(history_path, "AI", [0.4] * 7)
        alerts = check_alerts(
            [_make_result("AI", 0.85)],
            load_history(history_path),
            absolute_threshold=0.8,
        )
        assert len(alerts) == 1
        assert alerts[0].reason == "absolute"

    def test_no_alert_std_zero_below_absolute(self, tmp_path):
        """std==0 and score below absolute threshold → no alert."""
        history_path = tmp_path / "urgency_history.json"
        _append_n_runs(history_path, "AI", [0.4] * 7)
        alerts = check_alerts(
            [_make_result("AI", 0.6)],
            load_history(history_path),
            absolute_threshold=0.8,
        )
        assert alerts == []


# ---------------------------------------------------------------------------
# Statistical z-score tests (sufficient varied history)
# ---------------------------------------------------------------------------

class TestStatisticalAlert:
    # Varied historical scores: mean≈0.389, std≈0.024
    _VARIED_SCORES = [0.35, 0.40, 0.38, 0.42, 0.37, 0.41, 0.39]

    def test_statistical_alert_fires(self, tmp_path):
        """Score far above mean (z >> 2.0) with sufficient varied history."""
        history_path = tmp_path / "urgency_history.json"
        _append_n_runs(history_path, "AI", self._VARIED_SCORES)
        alerts = check_alerts(
            [_make_result("AI", 0.85)],
            load_history(history_path),
            absolute_threshold=0.8,
            z_score_threshold=2.0,
        )
        assert len(alerts) == 1
        a = alerts[0]
        assert a.reason == "statistical"
        assert a.z_score is not None and a.z_score > 2.0
        assert a.mean is not None
        assert a.std is not None and a.std > 0

    def test_statistical_no_alert_within_band(self, tmp_path):
        """Score within two standard deviations of the mean → no alert."""
        history_path = tmp_path / "urgency_history.json"
        _append_n_runs(history_path, "AI", self._VARIED_SCORES)
        # Score close to mean — well within z=2.0 band
        alerts = check_alerts(
            [_make_result("AI", 0.40)],
            load_history(history_path),
            absolute_threshold=0.8,
            z_score_threshold=2.0,
        )
        assert alerts == []

    def test_statistical_check_skips_absolute(self, tmp_path):
        """
        Once statistical check runs (std > 0 with enough history), the absolute
        check must NOT also fire — even if the score exceeds absolute_threshold.
        Prevents double-counting the same anomaly.
        """
        history_path = tmp_path / "urgency_history.json"
        _append_n_runs(history_path, "AI", self._VARIED_SCORES)
        # Score above absolute_threshold=0.8 but only just above z threshold
        alerts = check_alerts(
            [_make_result("AI", 0.85)],
            load_history(history_path),
            absolute_threshold=0.8,
            z_score_threshold=2.0,
        )
        # Should be exactly one alert (statistical), not two
        assert len(alerts) == 1
        assert alerts[0].reason == "statistical"

    def test_custom_z_threshold(self, tmp_path):
        """A very high z_score_threshold prevents the alert from firing."""
        history_path = tmp_path / "urgency_history.json"
        _append_n_runs(history_path, "AI", self._VARIED_SCORES)
        alerts = check_alerts(
            [_make_result("AI", 0.45)],
            load_history(history_path),
            absolute_threshold=0.8,
            z_score_threshold=100.0,  # impossibly high
        )
        assert alerts == []


# ---------------------------------------------------------------------------
# Multi-topic tests
# ---------------------------------------------------------------------------

class TestMultiTopic:
    def test_only_alerting_topic_returned(self, tmp_path):
        history_path = tmp_path / "urgency_history.json"
        results = [
            _make_result("AI", 0.9),       # above absolute 0.8
            _make_result("Defense", 0.5),  # below threshold
        ]
        alerts = check_alerts(results, load_history(history_path), absolute_threshold=0.8)
        assert len(alerts) == 1
        assert alerts[0].topic == "AI"

    def test_multiple_topics_can_alert(self, tmp_path):
        history_path = tmp_path / "urgency_history.json"
        results = [
            _make_result("AI", 0.9),
            _make_result("Defense", 0.85),
            _make_result("Economics", 0.5),
        ]
        alerts = check_alerts(results, load_history(history_path), absolute_threshold=0.8)
        alerted_topics = {a.topic for a in alerts}
        assert alerted_topics == {"AI", "Defense"}

    def test_topic_missing_from_history_uses_absolute(self, tmp_path):
        """A topic with zero historical entries for it falls back to absolute."""
        history_path = tmp_path / "urgency_history.json"
        # Only AI has history, Defense has none
        _append_n_runs(history_path, "AI", [0.35, 0.40, 0.38, 0.42, 0.37, 0.41, 0.39])
        alerts = check_alerts(
            [_make_result("Defense", 0.85)],
            load_history(history_path),
            absolute_threshold=0.8,
        )
        assert len(alerts) == 1
        assert alerts[0].reason == "absolute"

    def test_topic_without_strategy_ignored(self, tmp_path):
        history_path = tmp_path / "urgency_history.json"
        result = TopicResult(config=_make_config("AI"), strategy=None)
        alerts = check_alerts([result], load_history(history_path), absolute_threshold=0.0)
        assert alerts == []


# ---------------------------------------------------------------------------
# History persistence tests
# ---------------------------------------------------------------------------

class TestHistoryPersistence:
    def test_load_empty_when_no_file(self, tmp_path):
        history = load_history(tmp_path / "nonexistent.json")
        assert history == []

    def test_append_creates_file(self, tmp_path):
        history_path = tmp_path / "sub" / "urgency_history.json"
        append_run(history_path, [_make_result("AI", 0.5)], run_id="run-0")
        assert history_path.exists()
        data = json.loads(history_path.read_text())
        assert len(data) == 1
        assert data[0]["scores"]["AI"] == pytest.approx(0.5)

    def test_multiple_appends_accumulate(self, tmp_path):
        history_path = tmp_path / "urgency_history.json"
        for i, s in enumerate([0.3, 0.5, 0.7]):
            append_run(history_path, [_make_result("AI", s)], run_id=f"run-{i}")
        history = load_history(history_path)
        assert len(history) == 3
        assert [h["scores"]["AI"] for h in history] == pytest.approx([0.3, 0.5, 0.7])

    def test_ordering_current_run_not_in_own_baseline(self, tmp_path):
        """
        The correct call order is: load → check → append.
        If you accidentally pass the already-appended history to check_alerts,
        the current run's score would inflate the mean and reduce z-scores.
        This test verifies the baseline never includes the current run.
        """
        history_path = tmp_path / "urgency_history.json"
        # Build 7 runs with consistently low scores
        _append_n_runs(history_path, "AI", [0.35, 0.40, 0.38, 0.42, 0.37, 0.41, 0.39])

        current_results = [_make_result("AI", 0.99)]
        history = load_history(history_path)   # load before append
        alerts = check_alerts(current_results, history, absolute_threshold=0.8, z_score_threshold=2.0)
        append_run(history_path, current_results, run_id="current")

        # Alert must have fired based on the 7-run baseline (not 8-run with 0.99 included)
        assert len(alerts) == 1
        assert alerts[0].reason == "statistical"

        # After append, history has 8 entries
        new_history = load_history(history_path)
        assert len(new_history) == 8


# ---------------------------------------------------------------------------
# UrgencyAlert.summary() formatting
# ---------------------------------------------------------------------------

class TestUrgencyAlertSummary:
    def test_absolute_summary(self):
        a = UrgencyAlert(topic="AI", score=0.85, reason="absolute", threshold=0.8)
        s = a.summary()
        assert "AI" in s
        assert "0.85" in s
        assert "0.8" in s

    def test_statistical_summary(self):
        a = UrgencyAlert(
            topic="Defense",
            score=0.9,
            reason="statistical",
            threshold=2.0,
            mean=0.4,
            std=0.05,
            z_score=10.0,
        )
        s = a.summary()
        assert "Defense" in s
        assert "0.90" in s
        assert "z=10.0" in s
