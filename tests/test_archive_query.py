"""
Tests for strategic_reports.daily.core.archive_query.find_relevant_communities().

Covers:
  - exact tag-membership match (via community_summary_tags)
  - substring fallback match (label/summary text) for tags with no exact
    membership hit
  - dedup across multiple matching tags/runs
  - most-recent-first ordering and limit capping
  - empty candidate_tags / no matches
"""

from pathlib import Path
from typing import Any

from strategic_reports.daily.core.archive_query import find_relevant_communities
from strategic_reports.daily.core.db import record_run
from strategic_reports.daily.core.tag_tracking import record_community_summaries


def _seed(
    db_path: Path, run_id: str, article_count: int, communities: dict[int, dict[str, Any]]
) -> None:
    record_run(db_path, run_id, article_count=article_count)
    record_community_summaries(db_path, run_id, communities)


class TestFindRelevantCommunities:
    def test_empty_candidate_tags_returns_empty(self, db_path: Path) -> None:
        _seed(db_path, "run-0", 10, {
            0: {
                "label": "policy", "tags": ["policy"],
                "summary": "Coverage of policy.", "article_count": 1,
            },
        })
        assert find_relevant_communities(db_path, []) == []

    def test_no_matches_returns_empty(self, db_path: Path) -> None:
        _seed(db_path, "run-0", 10, {
            0: {
                "label": "policy", "tags": ["policy"],
                "summary": "Coverage of policy.", "article_count": 1,
            },
        })
        assert find_relevant_communities(db_path, ["unrelated-topic"]) == []

    def test_exact_tag_membership_match(self, db_path: Path) -> None:
        _seed(db_path, "run-0", 10, {
            0: {"label": "policy", "tags": ["export controls", "policy"],
                "summary": "Coverage of new export rules.", "article_count": 3},
        })
        results = find_relevant_communities(db_path, ["export controls"])
        assert len(results) == 1
        assert results[0]["label"] == "policy"
        assert results[0]["article_count"] == 3

    def test_substring_fallback_on_summary_text(self, db_path: Path) -> None:
        """A candidate tag with no exact tag-membership hit still matches via summary text."""
        _seed(db_path, "run-0", 10, {
            0: {
                "label": "biotech", "tags": ["genomics"],
                "summary": "New gene editing breakthroughs announced this week.",
                "article_count": 2,
            },
        })
        results = find_relevant_communities(db_path, ["gene editing"])
        assert len(results) == 1
        assert results[0]["label"] == "biotech"

    def test_dedup_across_multiple_matching_tags(self, db_path: Path) -> None:
        """A community matching on two different candidate tags is returned only once."""
        _seed(db_path, "run-0", 10, {
            0: {"label": "policy", "tags": ["export controls", "sanctions"],
                "summary": "Coverage of policy.", "article_count": 1},
        })
        results = find_relevant_communities(db_path, ["export controls", "sanctions"])
        assert len(results) == 1

    def test_most_recent_first(self, db_path: Path) -> None:
        _seed(db_path, "run-0", 10, {
            0: {
                "label": "policy", "tags": ["policy"],
                "summary": "Older coverage.", "article_count": 1,
            },
        })
        _seed(db_path, "run-1", 10, {
            0: {
                "label": "policy", "tags": ["policy"],
                "summary": "Newer coverage.", "article_count": 1,
            },
        })
        results = find_relevant_communities(db_path, ["policy"])
        assert [r["run_id"] for r in results] == ["run-1", "run-0"]

    def test_limit_caps_results(self, db_path: Path) -> None:
        for i in range(5):
            _seed(db_path, f"run-{i}", 10, {
                0: {
                    "label": "policy", "tags": ["policy"],
                    "summary": f"Coverage {i}.", "article_count": 1,
                },
            })
        results = find_relevant_communities(db_path, ["policy"], limit=2)
        assert len(results) == 2
        # Most recent runs first: run-4, run-3.
        assert [r["run_id"] for r in results] == ["run-4", "run-3"]

    def test_returns_expected_fields(self, db_path: Path) -> None:
        _seed(db_path, "run-0", 10, {
            0: {
                "label": "policy", "tags": ["policy"],
                "summary": "Coverage of policy.", "article_count": 7,
            },
        })
        result = find_relevant_communities(db_path, ["policy"])[0]
        assert set(result.keys()) == {
            "run_id", "created_at", "community_id", "label", "summary", "article_count",
        }
        assert result["community_id"] == 0
        assert result["summary"] == "Coverage of policy."
