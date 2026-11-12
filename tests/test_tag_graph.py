"""
Tests for strategic_reports.daily.core.tag_graph: find_bridge_tags() and
group_articles_by_community().

Uses hand-built graph_data/display_data dicts (the shapes build_graph_data()/
build_display_graph() produce) rather than routing through those functions,
to keep these tests focused on find_bridge_tags'/group_articles_by_community's
own logic.
"""

import json
from pathlib import Path
from typing import Any

from strategic_reports.daily.core.models import ArticleSummary, TopicConfig, TopicResult
from strategic_reports.daily.core.tag_graph import (
    build_subset_display_graph,
    find_bridge_tags,
    group_articles_by_community,
    script_safe_json,
    write_tag_graph,
)


def _node(tag: str, count: int, topics: list[str]) -> dict[str, Any]:
    return {"id": tag, "count": count, "topics": topics}


def _display_node(tag: str, count: int, community: int, community_label: str) -> dict[str, Any]:
    return {
        "id": tag, "count": count, "topics": [],
        "community": community, "community_label": community_label,
    }


def _article(title: str, link: str, tags: list[str]) -> ArticleSummary:
    return ArticleSummary(
        title=title, link=link, publish_date="2026-07-31T09:00:00",
        summary=["Bullet one.", "Bullet two.", "Bullet three."], tags=tags,
    )


def _results(
    articles: list[ArticleSummary], topic_title: str = "Artificial Intelligence"
) -> list[TopicResult]:
    config = TopicConfig(
        slug=f"feeds_{topic_title.lower()}", title=topic_title, feeds_file=Path("/dev/null")
    )
    return [TopicResult(config=config, articles=articles)]


class TestFindBridgeTags:
    def test_below_min_topics_excluded(self) -> None:
        graph_data = {"nodes": [_node("ai", 10, ["AI", "Defense"])], "links": []}
        assert find_bridge_tags(graph_data, min_topics=3) == []

    def test_at_min_topics_included(self) -> None:
        graph_data = {"nodes": [_node("ai", 10, ["AI", "Defense", "Economics"])], "links": []}
        bridges = find_bridge_tags(graph_data, min_topics=3)
        assert len(bridges) == 1
        assert bridges[0]["tag"] == "ai"
        assert bridges[0]["topics"] == ["AI", "Defense", "Economics"]
        assert bridges[0]["count"] == 10

    def test_sorted_by_topic_breadth_first(self) -> None:
        graph_data = {
            "nodes": [
                _node("narrow", 100, ["AI", "Defense", "Economics"]),
                _node("wide", 5, ["AI", "Defense", "Economics", "Geopolitics", "Biotech"]),
            ],
            "links": [],
        }
        bridges = find_bridge_tags(graph_data, min_topics=3)
        assert [b["tag"] for b in bridges] == ["wide", "narrow"]

    def test_ties_broken_by_count(self) -> None:
        graph_data = {
            "nodes": [
                _node("low-count", 5, ["AI", "Defense", "Economics"]),
                _node("high-count", 50, ["AI", "Defense", "Economics"]),
            ],
            "links": [],
        }
        bridges = find_bridge_tags(graph_data, min_topics=3)
        assert [b["tag"] for b in bridges] == ["high-count", "low-count"]

    def test_limit_caps_results(self) -> None:
        graph_data = {
            "nodes": [
                _node(f"tag{i}", i, ["AI", "Defense", "Economics"])
                for i in range(10)
            ],
            "links": [],
        }
        bridges = find_bridge_tags(graph_data, min_topics=3, limit=3)
        assert len(bridges) == 3
        # Highest count first (9, 8, 7) since all have equal topic breadth.
        assert [b["tag"] for b in bridges] == ["tag9", "tag8", "tag7"]

    def test_empty_nodes_returns_empty(self) -> None:
        assert find_bridge_tags({"nodes": [], "links": []}) == []

    def test_no_qualifying_tags_returns_empty(self) -> None:
        graph_data = {
            "nodes": [_node("single-topic", 100, ["AI"])],
            "links": [],
        }
        assert find_bridge_tags(graph_data, min_topics=3) == []


class TestGroupArticlesByCommunity:
    # Two communities: 0 = policy/regulation, 1 = biotech/genomics.
    _DISPLAY_DATA = {
        "nodes": [
            _display_node("policy", 10, community=0, community_label="policy"),
            _display_node("regulation", 8, community=0, community_label="policy"),
            _display_node("biotech", 12, community=1, community_label="biotech"),
            _display_node("genomics", 6, community=1, community_label="biotech"),
        ],
        "links": [],
    }
    _FILLER = ["tech", "research", "innovation", "markets"]

    def test_article_grouped_under_matching_community(self) -> None:
        results = _results([_article("A", "https://example.com/a", ["policy"] + self._FILLER)])
        grouped = group_articles_by_community(results, self._DISPLAY_DATA)
        assert set(grouped.keys()) == {0}
        assert grouped[0]["label"] == "policy"
        assert grouped[0]["tags"] == ["policy", "regulation"]
        assert [a.title for a in grouped[0]["articles"]] == ["A"]

    def test_article_with_no_matching_tags_excluded(self) -> None:
        results = _results([_article("A", "https://example.com/a", self._FILLER + ["unrelated"])])
        grouped = group_articles_by_community(results, self._DISPLAY_DATA)
        assert grouped == {}

    def test_article_spanning_two_communities_appears_in_both(self) -> None:
        results = _results([
            _article("A", "https://example.com/a", ["policy", "biotech"] + self._FILLER[:3])
        ])
        grouped = group_articles_by_community(results, self._DISPLAY_DATA)
        assert set(grouped.keys()) == {0, 1}
        assert [a.title for a in grouped[0]["articles"]] == ["A"]
        assert [a.title for a in grouped[1]["articles"]] == ["A"]

    def test_duplicate_link_deduped_within_community(self) -> None:
        """The same article (by link) appearing via two tags in one community counts once."""
        results = _results([
            _article("A", "https://example.com/a", ["policy", "regulation"] + self._FILLER[:3])
        ])
        grouped = group_articles_by_community(results, self._DISPLAY_DATA)
        assert len(grouped[0]["articles"]) == 1

    def test_multiple_articles_same_community(self) -> None:
        results = _results([
            _article("A", "https://example.com/a", ["policy"] + self._FILLER),
            _article("B", "https://example.com/b", ["regulation"] + self._FILLER),
        ])
        grouped = group_articles_by_community(results, self._DISPLAY_DATA)
        assert {a.title for a in grouped[0]["articles"]} == {"A", "B"}

    def test_no_articles_returns_empty(self) -> None:
        assert group_articles_by_community([], self._DISPLAY_DATA) == {}


class TestSubsetDisplayGraph:
    """build_subset_display_graph(): the inline graph for one featured report."""

    def _three_articles(self, tags: list[str], prefix: str) -> list[ArticleSummary]:
        return [_article(f"{prefix} {i}", f"https://x.com/{prefix}/{i}", tags) for i in range(3)]

    def test_only_subset_tags_and_topic_order(self) -> None:
        retail_tags = ["stores", "shoppers", "pricing", "loyalty", "holiday"]
        econ_tags = ["pricing", "inflation", "rates", "labor", "wages"]
        retail = _results(self._three_articles(retail_tags, "r"), "Retail")
        econ = _results(self._three_articles(econ_tags, "e"), "Economics")
        graph = build_subset_display_graph(retail + econ, topic_order=["Retail", "Economics"])

        ids = {n["id"] for n in graph["nodes"]}
        assert ids == {"stores", "shoppers", "pricing", "loyalty", "holiday",
                       "inflation", "rates", "labor", "wages"}
        assert graph["topic_order"] == ["Retail", "Economics"]
        pricing = next(n for n in graph["nodes"] if n["id"] == "pricing")
        assert pricing["topics"] == ["Economics", "Retail"]

    def test_empty_subset_yields_empty_graph(self) -> None:
        graph = build_subset_display_graph([], topic_order=[])
        assert graph["nodes"] == [] and graph["links"] == []


class TestScriptSafeJson:
    def test_script_breakout_escaped_and_round_trips(self) -> None:
        payload = {"id": "</script><img src=x onerror=alert(1)>&"}
        out = script_safe_json(payload)
        assert "<" not in out and ">" not in out and "&" not in out
        assert json.loads(out) == payload


class TestWriteTagGraphHtml:
    def test_standalone_page_escapes_tags_and_mounts_viewer(self, tmp_path: Path) -> None:
        evil = "</script><script>alert(1)</script>"
        articles = [
            _article(f"a{i}", f"https://x.com/{i}", [evil, "safe tag", "other", "more", "extra"])
            for i in range(3)
        ]
        write_tag_graph(_results(articles), tmp_path)
        html = (tmp_path / "tag_graph.html").read_text()

        assert evil not in html
        assert "mountTagGraph(document.getElementById(\"graph-root\")" in html
        assert "__GRAPH_DATA__" not in html and "__JS__" not in html
        # Tooltip text is set via textContent, not innerHTML.
        assert ".html(" not in html
        # tag_graph.json / tag_graph_display.json are unchanged, plain JSON.
        assert json.loads((tmp_path / "tag_graph_display.json").read_text())["nodes"]
