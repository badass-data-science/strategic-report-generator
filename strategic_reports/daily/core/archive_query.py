"""
Graph-guided retrieval over the accumulated archive.

find_relevant_communities() is the retrieval step for a future/optional
interactive "ask questions about the archive" feature (see
pipeline.answer_archive_question and cli.py's `ask` command): given a
list of candidate tags, it finds which archived community_summaries rows
(across ALL runs, not just the current one) are relevant — by tag
membership first, falling back to a substring match against each
community's label/summary text.

This is graph-guided retrieval, not full-text search over every article:
the retrieval unit is the Louvain tag-community (already computed once per
run, see tag_graph.build_display_graph), and its LLM-written summary
(tag_tracking.record_community_summaries) is the material actually
retrieved — not a fresh scan of raw article text. Deliberately not a full
GraphRAG implementation (no embeddings, no hierarchical multi-level
community summarization) — see AGENTS.md.

No LLM calls happen in this module — it's pure SQL. LLM orchestration
(extracting candidate tags from a question, synthesizing the final answer)
lives in pipeline.py, consistent with this project's separation between
"pure DB/graph access" modules and pipeline.py's LLM-calling orchestration.
"""

from pathlib import Path

from .db import connect
from .tag_normalizer import normalize_tags


def find_relevant_communities(
    db_path: Path,
    candidate_tags: list[str],
    limit: int = 8,
) -> list[dict]:
    """
    Find community_summaries rows relevant to candidate_tags, across every
    run in the archive.

    Two-pass match:
      1. Exact tag membership via community_summary_tags (candidate_tags
         normalized the same way pipeline tags are, so this usually hits).
      2. A substring fallback against each community's label/summary text,
         for candidate tags/phrases that don't exactly match a stored tag.

    Results are deduplicated, sorted most-recent-first, and capped at
    `limit`. Returns [] if candidate_tags is empty or nothing matches.
    """
    if not candidate_tags:
        return []

    normalized = normalize_tags(candidate_tags)

    conn = connect(db_path)
    try:
        placeholders = ",".join("?" for _ in normalized)
        membership_rows = conn.execute(
            f"SELECT DISTINCT run_id, community_id FROM community_summary_tags "
            f"WHERE tag IN ({placeholders})",
            normalized,
        ).fetchall()
        matched: set[tuple[str, int]] = set(membership_rows)

        for tag in normalized:
            like_rows = conn.execute(
                "SELECT run_id, community_id FROM community_summaries "
                "WHERE label LIKE ? OR summary LIKE ?",
                (f"%{tag}%", f"%{tag}%"),
            ).fetchall()
            matched.update(like_rows)

        if not matched:
            return []

        results = []
        for run_id, community_id in matched:
            row = conn.execute(
                "SELECT run_id, created_at, community_id, label, summary, article_count "
                "FROM community_summaries WHERE run_id = ? AND community_id = ?",
                (run_id, community_id),
            ).fetchone()
            if row is not None:
                results.append({
                    "run_id": row[0],
                    "created_at": row[1],
                    "community_id": row[2],
                    "label": row[3],
                    "summary": row[4],
                    "article_count": row[5],
                })
    finally:
        conn.close()

    results.sort(key=lambda r: r["created_at"], reverse=True)
    return results[:limit]
