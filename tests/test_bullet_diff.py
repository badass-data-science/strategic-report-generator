"""
Tests for strategic_reports.daily.core.bullet_diff's SQLite-backed storage
functions (load_bullet_history, append_bullet_run).

diff_all_topics (the LLM-calling part) is not covered here — it has no
storage concerns of its own; see test_pipeline.py for the project's pattern
for mocking LLMClient if that coverage gets added later.
"""


from pathlib import Path

from strategic_reports.daily.core.bullet_diff import append_bullet_run, load_bullet_history
from strategic_reports.daily.core.db import record_run
from strategic_reports.daily.core.models import StrategicInsight, TopicConfig, TopicResult


def _make_config(title: str) -> TopicConfig:
    return TopicConfig(slug=f"feeds_{title.lower()}", title=title, feeds_file=Path("/dev/null"))


def _make_result(title: str, bullets: list[str]) -> TopicResult:
    return TopicResult(
        config=_make_config(title),
        strategy=StrategicInsight(bullets=bullets, urgency_score=0.3),
    )


class TestLoadBulletHistory:
    def test_empty_when_no_prior_runs(self, database_url: str) -> None:
        assert load_bullet_history(database_url) == {}

    def test_returns_most_recent_run_only(self, database_url: str) -> None:
        """
        Two prior runs exist; load_bullet_history must return only the most
        recent one's bullets ("yesterday"), not older runs.
        """
        record_run(database_url, "run-0", article_count=0)
        append_bullet_run(
            database_url, [_make_result("AI", ["Old A.", "Old B.", "Old C."])], run_id="run-0"
        )

        record_run(database_url, "run-1", article_count=0)
        append_bullet_run(
            database_url, [_make_result("AI", ["New A.", "New B.", "New C."])], run_id="run-1"
        )

        yesterday = load_bullet_history(database_url)
        assert yesterday == {"AI": ["New A.", "New B.", "New C."]}

    def test_preserves_bullet_order(self, database_url: str) -> None:
        bullets = ["First.", "Second.", "Third.", "Fourth."]
        record_run(database_url, "run-0", article_count=0)
        append_bullet_run(database_url, [_make_result("AI", bullets)], run_id="run-0")
        assert load_bullet_history(database_url)["AI"] == bullets

    def test_multi_topic_single_run(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=0)
        append_bullet_run(
            database_url,
            [
                _make_result("AI", ["A1.", "A2.", "A3."]),
                _make_result("Defense", ["D1.", "D2.", "D3."]),
            ],
            run_id="run-0",
        )
        yesterday = load_bullet_history(database_url)
        assert yesterday == {
            "AI": ["A1.", "A2.", "A3."],
            "Defense": ["D1.", "D2.", "D3."],
        }

    def test_topic_absent_from_latest_run_missing_from_yesterday(self, database_url: str) -> None:
        """
        If a topic had bullets two runs ago but not in the most recent run,
        "yesterday" for that topic is absent — diff_all_topics skips topics
        not in the yesterday dict, matching the original JSON semantics
        (yesterday = the single most recent run's snapshot, not each topic's
        own most recent appearance independently).
        """
        record_run(database_url, "run-0", article_count=0)
        append_bullet_run(
            database_url,
            [
                _make_result("AI", ["A1.", "A2.", "A3."]),
                _make_result("Defense", ["D1.", "D2.", "D3."]),
            ],
            run_id="run-0",
        )
        record_run(database_url, "run-1", article_count=0)
        append_bullet_run(
            database_url, [_make_result("AI", ["A1b.", "A2b.", "A3b."])], run_id="run-1"
        )

        yesterday = load_bullet_history(database_url)
        assert "Defense" not in yesterday
        assert yesterday["AI"] == ["A1b.", "A2b.", "A3b."]

    def test_topic_without_strategy_not_persisted(self, database_url: str) -> None:
        result = TopicResult(config=_make_config("AI"), strategy=None)
        record_run(database_url, "run-0", article_count=0)
        append_bullet_run(database_url, [result], run_id="run-0")
        assert load_bullet_history(database_url) == {}


class TestAppendBulletRunFailureIsolation:
    def test_returns_zero_on_full_success(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=0)
        result = _make_result("AI", ["A.", "B.", "C."])
        assert append_bullet_run(database_url, [result], run_id="run-0") == 0

    def test_one_bad_bullet_does_not_lose_the_rest(self, database_url: str) -> None:
        # A NUL byte is valid in a Python str but Postgres text columns
        # reject it outright -- a reliable way to force exactly one bad
        # row without mocking (see article_archive_savepoint_fix for the
        # article-archive counterpart of this bug/fix). Before
        # db.insert_rows_isolating_failures, this would have aborted the
        # whole batch and lost every bullet for every topic in the run,
        # not just the bad one.
        record_run(database_url, "run-0", article_count=0)
        results = [
            _make_result("AI", ["Good bullet one.", "Bad \x00 bullet.", "Good bullet two."]),
            _make_result("Defense", ["D1.", "D2.", "D3."]),
        ]

        failed_count = append_bullet_run(database_url, results, run_id="run-0")

        assert failed_count == 1
        yesterday = load_bullet_history(database_url)
        assert yesterday["AI"] == ["Good bullet one.", "Good bullet two."]
        assert yesterday["Defense"] == ["D1.", "D2.", "D3."]
