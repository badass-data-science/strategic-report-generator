"""
Tests for strategic_reports.daily.core.tag_tracking.

Covers:
  - record_tags / rebuild_graph_data round-trip (db content reconstructs
    tag_graph.json's shape exactly, for a given run_id)
  - load_tag_rate_history normalizing by each run's article_count
  - check_emerging_tags: thin-history skip, std==0 skip, statistical alert,
    no-alert-within-band, zero-article-count guard
  - record_emerging_tag_alerts: audit-trail persistence of fired alerts
  - record_bridge_tags: audit-trail persistence of bridge tags surfaced to
    cross-topic synthesis
  - record_community_summaries: persistence of LLM-written community
    summaries and their member tags
"""

from pathlib import Path
from typing import Any

import pytest
from strategic_reports.daily.core.db import get_connection, record_run
from strategic_reports.daily.core.models import ArticleSummary, TopicConfig, TopicResult
from strategic_reports.daily.core.tag_graph import build_graph_data
from strategic_reports.daily.core.tag_tracking import (
    EmergingTagAlert,
    check_emerging_tags,
    load_tag_rate_history,
    rebuild_graph_data,
    record_bridge_tags,
    record_community_summaries,
    record_emerging_tag_alerts,
    record_tags,
)


def _scalar(conn: Any, sql: str) -> Any:
    """conn.execute(sql).fetchone()[0], asserting the row exists (mypy-friendly)."""
    row = conn.execute(sql).fetchone()
    assert row is not None
    return row[0]


def _make_article(title: str, tags: list[str]) -> ArticleSummary:
    return ArticleSummary(
        title=title,
        link=f"https://example.com/{title}",
        publish_date="2026-07-31T09:00:00",
        summary=["a.", "b.", "c."],
        tags=tags,
    )


def _make_results(
    articles: list[ArticleSummary], topic_title: str = "Artificial Intelligence"
) -> list[TopicResult]:
    config = TopicConfig(slug="feeds_ai", title=topic_title, feeds_file=Path("/dev/null"))
    return [TopicResult(config=config, articles=articles)]


# Five tags per article to satisfy ArticleSummary's min_length=5 constraint.
_FIVE_TAGS = ["ai", "tech", "models", "research", "benchmarks"]


class TestRecordAndRebuildGraphData:
    def test_rebuild_matches_build_graph_data(self, database_url: str) -> None:
        results = _make_results([
            _make_article("A1", _FIVE_TAGS),
            _make_article("A2", ["ai", "tech", "policy", "regulation", "governance"]),
        ])
        graph_data = build_graph_data(results)

        record_run(database_url, "run-0", article_count=2)
        record_tags(database_url, "run-0", graph_data)

        rebuilt = rebuild_graph_data(database_url, "run-0")

        assert sorted(rebuilt["nodes"], key=lambda n: n["id"]) == sorted(
            [{**n, "topics": sorted(n["topics"])} for n in graph_data["nodes"]],
            key=lambda n: n["id"],
        )
        # rebuild_graph_data doesn't guarantee topics-list ordering matches
        # build_graph_data's insertion order, so compare topics as sets too.
        rebuilt_by_id = {n["id"]: n for n in rebuilt["nodes"]}
        for n in graph_data["nodes"]:
            assert set(rebuilt_by_id[n["id"]]["topics"]) == set(n["topics"])
            assert rebuilt_by_id[n["id"]]["count"] == n["count"]

        assert sorted(
            (link["source"], link["target"], link["weight"]) for link in rebuilt["links"]
        ) == sorted(
            (link["source"], link["target"], link["weight"]) for link in graph_data["links"]
        )

    def test_rebuild_empty_for_unknown_run_id(self, database_url: str) -> None:
        assert rebuild_graph_data(database_url, "nonexistent-run") == {"nodes": [], "links": []}

    def test_record_tags_returns_zero_on_full_success(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=1)
        graph_data = {
            "nodes": [{"id": "good_tag", "count": 1, "topics": ["Topic A"]}],
            "links": [],
        }
        assert record_tags(database_url, "run-0", graph_data) == 0

    def test_one_bad_tag_does_not_lose_the_whole_run(self, database_url: str) -> None:
        # A NUL byte is valid in a Python str but Postgres text columns
        # reject it outright -- a reliable way to force exactly one bad
        # row per table without mocking (see article_archive_savepoint_fix
        # for the article-archive counterpart of this bug/fix). The bad
        # tag contributes one bad tag_counts row and one bad tag_topics
        # row; the good nodes and the edge between them (no NUL bytes)
        # should all survive via the per-row savepoint fallback.
        record_run(database_url, "run-0", article_count=1)
        graph_data = {
            "nodes": [
                {"id": "good_tag_1", "count": 5, "topics": ["Topic A"]},
                {"id": "bad\x00tag", "count": 3, "topics": ["Topic A"]},
                {"id": "good_tag_2", "count": 2, "topics": []},
            ],
            "links": [{"source": "good_tag_1", "target": "good_tag_2", "weight": 1}],
        }

        failed_count = record_tags(database_url, "run-0", graph_data)

        assert failed_count == 2  # 1 tag_counts row + 1 tag_topics row
        rebuilt = rebuild_graph_data(database_url, "run-0")
        assert {n["id"] for n in rebuilt["nodes"]} == {"good_tag_1", "good_tag_2"}
        assert rebuilt["links"] == [
            {"source": "good_tag_1", "target": "good_tag_2", "weight": 1}
        ]


class TestLoadTagRateHistory:
    def test_empty_when_no_prior_runs(self, database_url: str) -> None:
        assert load_tag_rate_history(database_url) == {}

    def test_rate_normalizes_by_article_count(self, database_url: str) -> None:
        # Same raw count (10), but different article_count per run — rates differ.
        record_run(database_url, "run-0", article_count=50)
        record_tags(
            database_url,
            "run-0",
            {"nodes": [{"id": "ai", "count": 10, "topics": ["AI"]}], "links": []},
        )

        record_run(database_url, "run-1", article_count=100)
        record_tags(
            database_url,
            "run-1",
            {"nodes": [{"id": "ai", "count": 10, "topics": ["AI"]}], "links": []},
        )

        history = load_tag_rate_history(database_url)
        assert history["ai"] == pytest.approx([10 / 50, 10 / 100])

    def test_zero_article_count_gives_zero_rate(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=0)
        record_tags(
            database_url,
            "run-0",
            {"nodes": [{"id": "ai", "count": 0, "topics": ["AI"]}], "links": []},
        )
        history = load_tag_rate_history(database_url)
        assert history["ai"] == [0.0]


class TestCheckEmergingTags:
    _STABLE_RATES_RUN_ARTICLE_COUNT = 100

    def _seed_history(self, database_url: str, tag: str, rates: list[float]) -> None:
        """Seed database_url with one run per rate, using article_count=100 so rate == count/100."""
        for i, rate in enumerate(rates):
            run_id = f"hist-{i}"
            count = round(rate * self._STABLE_RATES_RUN_ARTICLE_COUNT)
            record_run(database_url, run_id, article_count=self._STABLE_RATES_RUN_ARTICLE_COUNT)
            record_tags(
                database_url, run_id,
                {"nodes": [{"id": tag, "count": count, "topics": ["AI"]}], "links": []},
            )

    def test_thin_history_never_alerts(self, database_url: str) -> None:
        """Fewer than 7 historical runs — skipped regardless of how anomalous the current
        rate looks."""
        self._seed_history(database_url, "ai", [0.10, 0.11, 0.09, 0.10, 0.11, 0.10])  # 6 runs
        history = load_tag_rate_history(database_url)
        current = {"nodes": [{"id": "ai", "count": 90, "topics": ["AI"]}], "links": []}  # rate 0.9
        alerts = check_emerging_tags(current, current_article_count=100, history=history)
        assert alerts == []

    def test_brand_new_tag_never_alerts(self, database_url: str) -> None:
        """A tag with zero history is skipped, not flagged, no matter its current rate."""
        history = load_tag_rate_history(database_url)  # empty db
        current = {"nodes": [{"id": "brand-new", "count": 50, "topics": ["AI"]}], "links": []}
        alerts = check_emerging_tags(current, current_article_count=100, history=history)
        assert alerts == []

    def test_statistical_alert_fires(self, database_url: str) -> None:
        # 7 runs
        self._seed_history(database_url, "ai", [0.10, 0.11, 0.09, 0.105, 0.095, 0.10, 0.11])
        history = load_tag_rate_history(database_url)
        # rate 0.6, far above ~0.10 mean
        current = {"nodes": [{"id": "ai", "count": 60, "topics": ["AI"]}], "links": []}
        alerts = check_emerging_tags(
            current, current_article_count=100, history=history, z_score_threshold=2.0
        )
        assert len(alerts) == 1
        a = alerts[0]
        assert a.tag == "ai"
        assert a.count == 60
        assert a.rate == pytest.approx(0.6)
        assert a.z_score > 2.0

    def test_no_alert_within_band(self, database_url: str) -> None:
        self._seed_history(database_url, "ai", [0.10, 0.11, 0.09, 0.105, 0.095, 0.10, 0.11])
        history = load_tag_rate_history(database_url)
        # rate 0.10, at the mean
        current = {"nodes": [{"id": "ai", "count": 10, "topics": ["AI"]}], "links": []}
        alerts = check_emerging_tags(
            current, current_article_count=100, history=history, z_score_threshold=2.0
        )
        assert alerts == []

    def test_std_zero_never_alerts(self, database_url: str) -> None:
        """All historical rates identical (std=0) — no absolute fallback for tags, so no alert."""
        self._seed_history(database_url, "ai", [0.10] * 7)
        history = load_tag_rate_history(database_url)
        current = {"nodes": [{"id": "ai", "count": 90, "topics": ["AI"]}], "links": []}
        alerts = check_emerging_tags(
            current, current_article_count=100, history=history, z_score_threshold=2.0
        )
        assert alerts == []

    def test_zero_current_article_count_returns_empty(self, database_url: str) -> None:
        self._seed_history(database_url, "ai", [0.10] * 7)
        history = load_tag_rate_history(database_url)
        current = {"nodes": [{"id": "ai", "count": 0, "topics": ["AI"]}], "links": []}
        alerts = check_emerging_tags(current, current_article_count=0, history=history)
        assert alerts == []

    def test_only_anomalous_tags_returned(self, database_url: str) -> None:
        """Multiple tags with history; only the one with an anomalous rate is flagged."""
        self._seed_history(database_url, "ai", [0.10, 0.11, 0.09, 0.105, 0.095, 0.10, 0.11])
        self._seed_history(database_url, "biotech", [0.05, 0.06, 0.04, 0.05, 0.055, 0.05, 0.06])
        history = load_tag_rate_history(database_url)
        current = {
            "nodes": [
                {"id": "ai", "count": 60, "topics": ["AI"]},       # anomalous: rate 0.6
                {"id": "biotech", "count": 5, "topics": ["AI"]},   # normal: rate 0.05
            ],
            "links": [],
        }
        alerts = check_emerging_tags(
            current, current_article_count=100, history=history, z_score_threshold=2.0
        )
        assert {a.tag for a in alerts} == {"ai"}


class TestEmergingTagAlertSummary:
    def test_summary_format(self) -> None:
        a = EmergingTagAlert(tag="ai", count=60, rate=0.6, mean=0.1, std=0.01, z_score=50.0)
        s = a.summary()
        assert "ai" in s
        assert "count=60" in s
        assert "0.6000" in s
        assert "z=50.0" in s


class TestRecordEmergingTagAlerts:
    def test_noop_on_empty_alerts(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=100)
        record_emerging_tag_alerts(database_url, "run-0", [])
        with get_connection(database_url) as conn:
            count = _scalar(conn, "SELECT COUNT(*) FROM emerging_tag_alerts")
        assert count == 0

    def test_persists_fired_alerts(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=100)
        alert = EmergingTagAlert(tag="ai", count=60, rate=0.6, mean=0.1, std=0.05, z_score=10.0)
        record_emerging_tag_alerts(database_url, "run-0", [alert])

        with get_connection(database_url) as conn:
            row = conn.execute(
                "SELECT run_id, tag, count, rate, mean, std, z_score "
                "FROM emerging_tag_alerts WHERE tag = 'ai'"
            ).fetchone()
        assert row == ("run-0", "ai", 60, 0.6, 0.1, 0.05, 10.0)

    def test_persists_own_timestamp(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=100)
        alert = EmergingTagAlert(tag="ai", count=60, rate=0.6, mean=0.1, std=0.05, z_score=10.0)
        record_emerging_tag_alerts(database_url, "run-0", [alert])

        with get_connection(database_url) as conn:
            created_at = _scalar(
                conn, "SELECT created_at FROM emerging_tag_alerts WHERE tag = 'ai'"
            )
        assert created_at  # non-empty timestamp string

    def test_multiple_alerts_same_run(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=100)
        alerts = [
            EmergingTagAlert(tag="ai", count=60, rate=0.6, mean=0.1, std=0.05, z_score=10.0),
            EmergingTagAlert(tag="biotech", count=40, rate=0.4, mean=0.05, std=0.02, z_score=17.5),
        ]
        record_emerging_tag_alerts(database_url, "run-0", alerts)

        with get_connection(database_url) as conn:
            rows = conn.execute("SELECT tag FROM emerging_tag_alerts").fetchall()
        assert {row[0] for row in rows} == {"ai", "biotech"}


class TestRecordBridgeTags:
    def test_noop_on_empty_bridge_tags(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=100)
        record_bridge_tags(database_url, "run-0", [])
        with get_connection(database_url) as conn:
            counts = (
                _scalar(conn, "SELECT COUNT(*) FROM bridge_tags"),
                _scalar(conn, "SELECT COUNT(*) FROM bridge_tag_topics"),
            )
        assert counts == (0, 0)

    def test_persists_tag_count_and_rank(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=100)
        bridge_tags = [
            {"tag": "export controls", "topics": ["AI", "Defense", "Economics"], "count": 12},
            {"tag": "sanctions", "topics": ["Defense", "Economics", "Geopolitics"], "count": 8},
        ]
        record_bridge_tags(database_url, "run-0", bridge_tags)

        with get_connection(database_url) as conn:
            rows = conn.execute(
                "SELECT tag, count, rank FROM bridge_tags ORDER BY rank"
            ).fetchall()
        assert rows == [("export controls", 12, 1), ("sanctions", 8, 2)]

    def test_persists_topics_per_tag(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=100)
        bridge_tags = [
            {"tag": "export controls", "topics": ["AI", "Defense", "Economics"], "count": 12}
        ]
        record_bridge_tags(database_url, "run-0", bridge_tags)

        with get_connection(database_url) as conn:
            topics = {
                row[0]
                for row in conn.execute(
                    "SELECT topic FROM bridge_tag_topics WHERE tag = 'export controls'"
                ).fetchall()
            }
        assert topics == {"AI", "Defense", "Economics"}

    def test_persists_own_timestamp(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=100)
        bridge_tags = [{"tag": "export controls", "topics": ["AI"], "count": 1}]
        record_bridge_tags(database_url, "run-0", bridge_tags)

        with get_connection(database_url) as conn:
            created_at = _scalar(
                conn, "SELECT created_at FROM bridge_tags WHERE tag = 'export controls'"
            )
        assert created_at  # non-empty timestamp string

    def test_self_contained_no_dependency_on_tag_topics(self, database_url: str) -> None:
        """
        record_bridge_tags must work even when tag_topics has no rows for
        this run_id yet — the two entry points call this at different
        points relative to record_tags() in their pipeline order.
        """
        record_run(database_url, "run-0", article_count=100)
        # Deliberately do NOT call record_tags() first.
        bridge_tags = [{"tag": "export controls", "topics": ["AI", "Defense"], "count": 5}]
        record_bridge_tags(database_url, "run-0", bridge_tags)

        with get_connection(database_url) as conn:
            tag_topics_count = _scalar(conn, "SELECT COUNT(*) FROM tag_topics")
            bridge_topics = {
                row[0] for row in conn.execute("SELECT topic FROM bridge_tag_topics").fetchall()
            }
        assert tag_topics_count == 0
        assert bridge_topics == {"AI", "Defense"}


class TestRecordCommunitySummaries:
    def test_noop_on_empty_dict(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=100)
        record_community_summaries(database_url, "run-0", {})
        with get_connection(database_url) as conn:
            counts = (
                _scalar(conn, "SELECT COUNT(*) FROM community_summaries"),
                _scalar(conn, "SELECT COUNT(*) FROM community_summary_tags"),
            )
        assert counts == (0, 0)

    def test_persists_label_summary_and_article_count(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=100)
        community_summaries = {
            0: {
                "label": "policy",
                "tags": ["policy", "regulation"],
                "summary": "Coverage of new export-control policy.",
                "article_count": 4,
            },
        }
        record_community_summaries(database_url, "run-0", community_summaries)

        with get_connection(database_url) as conn:
            row = conn.execute(
                "SELECT community_id, label, summary, article_count FROM community_summaries"
            ).fetchone()
        assert row == (0, "policy", "Coverage of new export-control policy.", 4)

    def test_persists_member_tags(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=100)
        community_summaries = {
            0: {
                "label": "policy", "tags": ["policy", "regulation"],
                "summary": "x", "article_count": 1,
            },
        }
        record_community_summaries(database_url, "run-0", community_summaries)

        with get_connection(database_url) as conn:
            tags = {
                row[0] for row in conn.execute(
                    "SELECT tag FROM community_summary_tags WHERE community_id = 0"
                ).fetchall()
            }
        assert tags == {"policy", "regulation"}

    def test_persists_own_timestamp(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=100)
        community_summaries = {
            0: {"label": "policy", "tags": ["policy"], "summary": "x", "article_count": 1},
        }
        record_community_summaries(database_url, "run-0", community_summaries)
        with get_connection(database_url) as conn:
            created_at = _scalar(conn, "SELECT created_at FROM community_summaries")
        assert created_at

    def test_multiple_communities_same_run(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=100)
        community_summaries = {
            0: {"label": "policy", "tags": ["policy"], "summary": "x", "article_count": 1},
            1: {"label": "biotech", "tags": ["biotech"], "summary": "y", "article_count": 2},
        }
        record_community_summaries(database_url, "run-0", community_summaries)
        with get_connection(database_url) as conn:
            labels = {
                row[0] for row in conn.execute("SELECT label FROM community_summaries").fetchall()
            }
        assert labels == {"policy", "biotech"}

    def test_returns_zero_on_full_success(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=100)
        community_summaries = {
            0: {"label": "policy", "tags": ["policy"], "summary": "x", "article_count": 1},
        }
        assert record_community_summaries(database_url, "run-0", community_summaries) == 0

    def test_one_bad_tag_does_not_lose_the_whole_run(self, database_url: str) -> None:
        # Same NUL-byte trick as test_one_bad_tag_does_not_lose_the_whole_run
        # in TestRecordAndRebuildGraphData: valid in a Python str, rejected
        # by Postgres text columns -- forces exactly one bad
        # community_summary_tags row without mocking.
        record_run(database_url, "run-0", article_count=100)
        community_summaries = {
            0: {
                "label": "policy",
                "tags": ["good_tag", "bad\x00tag"],
                "summary": "x",
                "article_count": 1,
            },
            1: {"label": "biotech", "tags": ["another_good"], "summary": "y", "article_count": 2},
        }

        failed_count = record_community_summaries(database_url, "run-0", community_summaries)

        assert failed_count == 1
        with get_connection(database_url) as conn:
            labels = {
                row[0] for row in conn.execute("SELECT label FROM community_summaries").fetchall()
            }
            tags = {row[0] for row in conn.execute("SELECT tag FROM community_summary_tags").fetchall()}
        assert labels == {"policy", "biotech"}  # community_summaries itself untouched
        assert tags == {"good_tag", "another_good"}
