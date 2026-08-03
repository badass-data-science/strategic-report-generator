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

import pytest
from strategic_reports.daily.core.db import connect, record_run
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
    def test_rebuild_matches_build_graph_data(self, db_path: Path) -> None:
        results = _make_results([
            _make_article("A1", _FIVE_TAGS),
            _make_article("A2", ["ai", "tech", "policy", "regulation", "governance"]),
        ])
        graph_data = build_graph_data(results)

        record_run(db_path, "run-0", article_count=2)
        record_tags(db_path, "run-0", graph_data)

        rebuilt = rebuild_graph_data(db_path, "run-0")

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

    def test_rebuild_empty_for_unknown_run_id(self, db_path: Path) -> None:
        assert rebuild_graph_data(db_path, "nonexistent-run") == {"nodes": [], "links": []}


class TestLoadTagRateHistory:
    def test_empty_when_no_prior_runs(self, db_path: Path) -> None:
        assert load_tag_rate_history(db_path) == {}

    def test_rate_normalizes_by_article_count(self, db_path: Path) -> None:
        # Same raw count (10), but different article_count per run — rates differ.
        record_run(db_path, "run-0", article_count=50)
        record_tags(
            db_path, "run-0", {"nodes": [{"id": "ai", "count": 10, "topics": ["AI"]}], "links": []}
        )

        record_run(db_path, "run-1", article_count=100)
        record_tags(
            db_path, "run-1", {"nodes": [{"id": "ai", "count": 10, "topics": ["AI"]}], "links": []}
        )

        history = load_tag_rate_history(db_path)
        assert history["ai"] == pytest.approx([10 / 50, 10 / 100])

    def test_zero_article_count_gives_zero_rate(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=0)
        record_tags(
            db_path, "run-0", {"nodes": [{"id": "ai", "count": 0, "topics": ["AI"]}], "links": []}
        )
        history = load_tag_rate_history(db_path)
        assert history["ai"] == [0.0]


class TestCheckEmergingTags:
    _STABLE_RATES_RUN_ARTICLE_COUNT = 100

    def _seed_history(self, db_path: Path, tag: str, rates: list[float]) -> None:
        """Seed db_path with one run per rate, using article_count=100 so rate == count/100."""
        for i, rate in enumerate(rates):
            run_id = f"hist-{i}"
            count = round(rate * self._STABLE_RATES_RUN_ARTICLE_COUNT)
            record_run(db_path, run_id, article_count=self._STABLE_RATES_RUN_ARTICLE_COUNT)
            record_tags(
                db_path, run_id,
                {"nodes": [{"id": tag, "count": count, "topics": ["AI"]}], "links": []},
            )

    def test_thin_history_never_alerts(self, db_path: Path) -> None:
        """Fewer than 7 historical runs — skipped regardless of how anomalous the current
        rate looks."""
        self._seed_history(db_path, "ai", [0.10, 0.11, 0.09, 0.10, 0.11, 0.10])  # 6 runs
        history = load_tag_rate_history(db_path)
        current = {"nodes": [{"id": "ai", "count": 90, "topics": ["AI"]}], "links": []}  # rate 0.9
        alerts = check_emerging_tags(current, current_article_count=100, history=history)
        assert alerts == []

    def test_brand_new_tag_never_alerts(self, db_path: Path) -> None:
        """A tag with zero history is skipped, not flagged, no matter its current rate."""
        history = load_tag_rate_history(db_path)  # empty db
        current = {"nodes": [{"id": "brand-new", "count": 50, "topics": ["AI"]}], "links": []}
        alerts = check_emerging_tags(current, current_article_count=100, history=history)
        assert alerts == []

    def test_statistical_alert_fires(self, db_path: Path) -> None:
        self._seed_history(db_path, "ai", [0.10, 0.11, 0.09, 0.105, 0.095, 0.10, 0.11])  # 7 runs
        history = load_tag_rate_history(db_path)
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

    def test_no_alert_within_band(self, db_path: Path) -> None:
        self._seed_history(db_path, "ai", [0.10, 0.11, 0.09, 0.105, 0.095, 0.10, 0.11])
        history = load_tag_rate_history(db_path)
        # rate 0.10, at the mean
        current = {"nodes": [{"id": "ai", "count": 10, "topics": ["AI"]}], "links": []}
        alerts = check_emerging_tags(
            current, current_article_count=100, history=history, z_score_threshold=2.0
        )
        assert alerts == []

    def test_std_zero_never_alerts(self, db_path: Path) -> None:
        """All historical rates identical (std=0) — no absolute fallback for tags, so no alert."""
        self._seed_history(db_path, "ai", [0.10] * 7)
        history = load_tag_rate_history(db_path)
        current = {"nodes": [{"id": "ai", "count": 90, "topics": ["AI"]}], "links": []}
        alerts = check_emerging_tags(
            current, current_article_count=100, history=history, z_score_threshold=2.0
        )
        assert alerts == []

    def test_zero_current_article_count_returns_empty(self, db_path: Path) -> None:
        self._seed_history(db_path, "ai", [0.10] * 7)
        history = load_tag_rate_history(db_path)
        current = {"nodes": [{"id": "ai", "count": 0, "topics": ["AI"]}], "links": []}
        alerts = check_emerging_tags(current, current_article_count=0, history=history)
        assert alerts == []

    def test_only_anomalous_tags_returned(self, db_path: Path) -> None:
        """Multiple tags with history; only the one with an anomalous rate is flagged."""
        self._seed_history(db_path, "ai", [0.10, 0.11, 0.09, 0.105, 0.095, 0.10, 0.11])
        self._seed_history(db_path, "biotech", [0.05, 0.06, 0.04, 0.05, 0.055, 0.05, 0.06])
        history = load_tag_rate_history(db_path)
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
    def test_noop_on_empty_alerts(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=100)
        record_emerging_tag_alerts(db_path, "run-0", [])
        conn = connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM emerging_tag_alerts").fetchone()[0]
        conn.close()
        assert count == 0

    def test_persists_fired_alerts(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=100)
        alert = EmergingTagAlert(tag="ai", count=60, rate=0.6, mean=0.1, std=0.05, z_score=10.0)
        record_emerging_tag_alerts(db_path, "run-0", [alert])

        conn = connect(db_path)
        row = conn.execute(
            "SELECT run_id, tag, count, rate, mean, std, z_score "
            "FROM emerging_tag_alerts WHERE tag = 'ai'"
        ).fetchone()
        conn.close()
        assert row == ("run-0", "ai", 60, 0.6, 0.1, 0.05, 10.0)

    def test_persists_own_timestamp(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=100)
        alert = EmergingTagAlert(tag="ai", count=60, rate=0.6, mean=0.1, std=0.05, z_score=10.0)
        record_emerging_tag_alerts(db_path, "run-0", [alert])

        conn = connect(db_path)
        created_at = conn.execute(
            "SELECT created_at FROM emerging_tag_alerts WHERE tag = 'ai'"
        ).fetchone()[0]
        conn.close()
        assert created_at  # non-empty timestamp string

    def test_multiple_alerts_same_run(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=100)
        alerts = [
            EmergingTagAlert(tag="ai", count=60, rate=0.6, mean=0.1, std=0.05, z_score=10.0),
            EmergingTagAlert(tag="biotech", count=40, rate=0.4, mean=0.05, std=0.02, z_score=17.5),
        ]
        record_emerging_tag_alerts(db_path, "run-0", alerts)

        conn = connect(db_path)
        tags = {row[0] for row in conn.execute("SELECT tag FROM emerging_tag_alerts").fetchall()}
        conn.close()
        assert tags == {"ai", "biotech"}


class TestRecordBridgeTags:
    def test_noop_on_empty_bridge_tags(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=100)
        record_bridge_tags(db_path, "run-0", [])
        conn = connect(db_path)
        counts = (
            conn.execute("SELECT COUNT(*) FROM bridge_tags").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM bridge_tag_topics").fetchone()[0],
        )
        conn.close()
        assert counts == (0, 0)

    def test_persists_tag_count_and_rank(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=100)
        bridge_tags = [
            {"tag": "export controls", "topics": ["AI", "Defense", "Economics"], "count": 12},
            {"tag": "sanctions", "topics": ["Defense", "Economics", "Geopolitics"], "count": 8},
        ]
        record_bridge_tags(db_path, "run-0", bridge_tags)

        conn = connect(db_path)
        rows = conn.execute(
            "SELECT tag, count, rank FROM bridge_tags ORDER BY rank"
        ).fetchall()
        conn.close()
        assert rows == [("export controls", 12, 1), ("sanctions", 8, 2)]

    def test_persists_topics_per_tag(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=100)
        bridge_tags = [
            {"tag": "export controls", "topics": ["AI", "Defense", "Economics"], "count": 12}
        ]
        record_bridge_tags(db_path, "run-0", bridge_tags)

        conn = connect(db_path)
        topics = {
            row[0]
            for row in conn.execute(
                "SELECT topic FROM bridge_tag_topics WHERE tag = 'export controls'"
            ).fetchall()
        }
        conn.close()
        assert topics == {"AI", "Defense", "Economics"}

    def test_persists_own_timestamp(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=100)
        bridge_tags = [{"tag": "export controls", "topics": ["AI"], "count": 1}]
        record_bridge_tags(db_path, "run-0", bridge_tags)

        conn = connect(db_path)
        created_at = conn.execute(
            "SELECT created_at FROM bridge_tags WHERE tag = 'export controls'"
        ).fetchone()[0]
        conn.close()
        assert created_at  # non-empty timestamp string

    def test_self_contained_no_dependency_on_tag_topics(self, db_path: Path) -> None:
        """
        record_bridge_tags must work even when tag_topics has no rows for
        this run_id yet — the two entry points call this at different
        points relative to record_tags() in their pipeline order.
        """
        record_run(db_path, "run-0", article_count=100)
        # Deliberately do NOT call record_tags() first.
        bridge_tags = [{"tag": "export controls", "topics": ["AI", "Defense"], "count": 5}]
        record_bridge_tags(db_path, "run-0", bridge_tags)

        conn = connect(db_path)
        tag_topics_count = conn.execute("SELECT COUNT(*) FROM tag_topics").fetchone()[0]
        bridge_topics = {
            row[0] for row in conn.execute("SELECT topic FROM bridge_tag_topics").fetchall()
        }
        conn.close()
        assert tag_topics_count == 0
        assert bridge_topics == {"AI", "Defense"}


class TestRecordCommunitySummaries:
    def test_noop_on_empty_dict(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=100)
        record_community_summaries(db_path, "run-0", {})
        conn = connect(db_path)
        counts = (
            conn.execute("SELECT COUNT(*) FROM community_summaries").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM community_summary_tags").fetchone()[0],
        )
        conn.close()
        assert counts == (0, 0)

    def test_persists_label_summary_and_article_count(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=100)
        community_summaries = {
            0: {
                "label": "policy",
                "tags": ["policy", "regulation"],
                "summary": "Coverage of new export-control policy.",
                "article_count": 4,
            },
        }
        record_community_summaries(db_path, "run-0", community_summaries)

        conn = connect(db_path)
        row = conn.execute(
            "SELECT community_id, label, summary, article_count FROM community_summaries"
        ).fetchone()
        conn.close()
        assert row == (0, "policy", "Coverage of new export-control policy.", 4)

    def test_persists_member_tags(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=100)
        community_summaries = {
            0: {
                "label": "policy", "tags": ["policy", "regulation"],
                "summary": "x", "article_count": 1,
            },
        }
        record_community_summaries(db_path, "run-0", community_summaries)

        conn = connect(db_path)
        tags = {
            row[0] for row in conn.execute(
                "SELECT tag FROM community_summary_tags WHERE community_id = 0"
            ).fetchall()
        }
        conn.close()
        assert tags == {"policy", "regulation"}

    def test_persists_own_timestamp(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=100)
        community_summaries = {
            0: {"label": "policy", "tags": ["policy"], "summary": "x", "article_count": 1},
        }
        record_community_summaries(db_path, "run-0", community_summaries)
        conn = connect(db_path)
        created_at = conn.execute("SELECT created_at FROM community_summaries").fetchone()[0]
        conn.close()
        assert created_at

    def test_multiple_communities_same_run(self, db_path: Path) -> None:
        record_run(db_path, "run-0", article_count=100)
        community_summaries = {
            0: {"label": "policy", "tags": ["policy"], "summary": "x", "article_count": 1},
            1: {"label": "biotech", "tags": ["biotech"], "summary": "y", "article_count": 2},
        }
        record_community_summaries(db_path, "run-0", community_summaries)
        conn = connect(db_path)
        labels = {
            row[0] for row in conn.execute("SELECT label FROM community_summaries").fetchall()
        }
        conn.close()
        assert labels == {"policy", "biotech"}
