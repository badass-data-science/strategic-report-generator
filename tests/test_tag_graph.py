"""
Tests for strategic_reports.daily.core.tag_graph.find_bridge_tags().

Uses hand-built graph_data dicts (the {"nodes": [...], "links": [...]}
shape build_graph_data() produces) rather than routing through
build_graph_data() itself, to keep these tests focused on find_bridge_tags'
own filtering/sorting/limiting logic.
"""

from strategic_reports.daily.core.tag_graph import find_bridge_tags


def _node(tag: str, count: int, topics: list[str]) -> dict:
    return {"id": tag, "count": count, "topics": topics}


class TestFindBridgeTags:
    def test_below_min_topics_excluded(self):
        graph_data = {"nodes": [_node("ai", 10, ["AI", "Defense"])], "links": []}
        assert find_bridge_tags(graph_data, min_topics=3) == []

    def test_at_min_topics_included(self):
        graph_data = {"nodes": [_node("ai", 10, ["AI", "Defense", "Economics"])], "links": []}
        bridges = find_bridge_tags(graph_data, min_topics=3)
        assert len(bridges) == 1
        assert bridges[0]["tag"] == "ai"
        assert bridges[0]["topics"] == ["AI", "Defense", "Economics"]
        assert bridges[0]["count"] == 10

    def test_sorted_by_topic_breadth_first(self):
        graph_data = {
            "nodes": [
                _node("narrow", 100, ["AI", "Defense", "Economics"]),
                _node("wide", 5, ["AI", "Defense", "Economics", "Geopolitics", "Biotech"]),
            ],
            "links": [],
        }
        bridges = find_bridge_tags(graph_data, min_topics=3)
        assert [b["tag"] for b in bridges] == ["wide", "narrow"]

    def test_ties_broken_by_count(self):
        graph_data = {
            "nodes": [
                _node("low-count", 5, ["AI", "Defense", "Economics"]),
                _node("high-count", 50, ["AI", "Defense", "Economics"]),
            ],
            "links": [],
        }
        bridges = find_bridge_tags(graph_data, min_topics=3)
        assert [b["tag"] for b in bridges] == ["high-count", "low-count"]

    def test_limit_caps_results(self):
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

    def test_empty_nodes_returns_empty(self):
        assert find_bridge_tags({"nodes": [], "links": []}) == []

    def test_no_qualifying_tags_returns_empty(self):
        graph_data = {
            "nodes": [_node("single-topic", 100, ["AI"])],
            "links": [],
        }
        assert find_bridge_tags(graph_data, min_topics=3) == []
