"""
Tests for report_rebuild.py (load_stored_run / resolve_run) and the
`render-from-db` CLI command that uses it. Seeds the tracking db through
the same record_*/append_* functions `run` uses, so these exercise the real
persisted shape rather than hand-written rows.
"""

from pathlib import Path

import pytest
from strategic_reports.daily.cli import app
from strategic_reports.daily.config import featured_reports as featured_reports_config
from strategic_reports.daily.config.featured_reports import FeaturedReport
from strategic_reports.daily.core.article_archive import record_articles
from strategic_reports.daily.core.bullet_diff import append_bullet_run
from strategic_reports.daily.core.db import record_run
from strategic_reports.daily.core.models import (
    ArticleSummary,
    StrategicInsight,
    TopicConfig,
    TopicResult,
)
from strategic_reports.daily.core.overview_archive import record_overview
from strategic_reports.daily.core.report_rebuild import (
    MISSING_BULLETS_ERROR,
    load_stored_run,
    resolve_run,
)
from strategic_reports.daily.core.urgency import append_run
from typer.testing import CliRunner


def _article(title: str) -> ArticleSummary:
    return ArticleSummary(
        title=title,
        link=f"https://example.com/{title.replace(' ', '-')}",
        publish_date="2026-11-11",
        summary=["One.", "Two.", "Three."],
        tags=["a", "b", "c", "d", "e"],
    )


def _result(slug: str, title: str, bullets: list[str] | None, articles: int) -> TopicResult:
    return TopicResult(
        config=TopicConfig(slug=slug, title=title, feeds_file=Path(f"{slug}.json")),
        articles=[_article(f"{title} story {i}") for i in range(articles)],
        strategy=StrategicInsight(bullets=bullets, urgency_score=0.7) if bullets else None,
    )


def _seed_run(database_url: str, run_id: str, retail_bullets: list[str]) -> None:
    results = [
        _result("feeds_retail", "Retail", retail_bullets, articles=2),
        _result("feeds_ai", "Artificial Intelligence", ["AI 1.", "AI 2.", "AI 3."], articles=1),
        # Articles archived but no strategy: synthesis failed that run.
        _result("feeds_energy", "Energy", None, articles=1),
    ]
    record_run(database_url, run_id, article_count=4)
    record_articles(database_url, run_id, results)
    append_run(database_url, results, run_id)
    append_bullet_run(database_url, results, run_id)
    record_overview(database_url, run_id, ["Cross 1.", "Cross 2.", "Cross 3."])


def _by_title(results: list[TopicResult]) -> dict[str, TopicResult]:
    return {r.config.title: r for r in results}


class TestLoadStoredRun:
    def test_latest_run_by_default(self, database_url: str) -> None:
        _seed_run(database_url, "run-old", ["Old 1.", "Old 2.", "Old 3."])
        _seed_run(database_url, "run-new", ["New 1.", "New 2.", "New 3."])

        stored = load_stored_run(database_url)

        assert stored.run_id == "run-new"
        retail = _by_title(stored.results)["Retail"]
        assert retail.strategy is not None
        assert retail.strategy.bullets == ["New 1.", "New 2.", "New 3."]
        assert retail.strategy.urgency_score == pytest.approx(0.7)
        assert len(retail.articles) == 2

    def test_specific_run_id(self, database_url: str) -> None:
        _seed_run(database_url, "run-old", ["Old 1.", "Old 2.", "Old 3."])
        _seed_run(database_url, "run-new", ["New 1.", "New 2.", "New 3."])

        stored = load_stored_run(database_url, "run-old")

        retail = _by_title(stored.results)["Retail"]
        assert retail.strategy is not None
        assert retail.strategy.bullets[0] == "Old 1."

    def test_results_follow_topic_order_and_cover_every_topic(self, database_url: str) -> None:
        from strategic_reports.daily.config.topic_order import list_directories_and_titles

        _seed_run(database_url, "r1", ["R 1.", "R 2.", "R 3."])
        stored = load_stored_run(database_url)
        assert [r.config.slug for r in stored.results] == [
            t["slug"] for t in list_directories_and_titles
        ]

    def test_topic_states(self, database_url: str) -> None:
        _seed_run(database_url, "r1", ["R 1.", "R 2.", "R 3."])
        by_title = _by_title(load_stored_run(database_url).results)

        # Articles but no bullets -> flagged as a synthesis failure.
        assert by_title["Energy"].error == MISSING_BULLETS_ERROR
        assert len(by_title["Energy"].articles) == 1
        # Nothing stored at all -> plain empty state.
        defense = by_title["Defense"]
        assert defense.error is None and defense.strategy is None and not defense.articles

    def test_overview_loaded(self, database_url: str) -> None:
        _seed_run(database_url, "r1", ["R 1.", "R 2.", "R 3."])
        stored = load_stored_run(database_url)
        assert stored.overview is not None
        assert stored.overview.bullets == ["Cross 1.", "Cross 2.", "Cross 3."]

    def test_unknown_run_id_raises(self, database_url: str) -> None:
        _seed_run(database_url, "r1", ["R 1.", "R 2.", "R 3."])
        with pytest.raises(LookupError, match="nope"):
            resolve_run(database_url, "nope")

    def test_empty_database_raises(self, database_url: str) -> None:
        with pytest.raises(LookupError, match="no runs"):
            resolve_run(database_url, None)


class TestRenderFromDbCommand:
    def test_renders_index_featured_and_summaries_without_signals(
        self, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin the featured-report config so this test doesn't depend on
        # whatever topics the real retail report happens to list.
        monkeypatch.setattr(
            featured_reports_config,
            "featured_reports",
            (FeaturedReport(name="retail", title="Retail", featured_topics=("feeds_retail",)),),
        )
        _seed_run(database_url, "r1", ["Retail lead.", "R 2.", "R 3."])
        out = tmp_path / "out"

        result = CliRunner().invoke(
            app, ["render-from-db", "--output-dir", str(out), "--database-url", database_url]
        )

        assert result.exit_code == 0, result.output
        assert "r1" in result.output
        assert (out / "index.html").exists()
        assert (out / "retail_summaries.html").exists()
        assert not (out / "systems_signals.json").exists()
        html = (out / "retail-report.html").read_text()
        assert html.index("Retail lead.") < html.index("Cross 1.")
        assert "AI 1." not in html  # AI isn't listed for the retail report

    def test_unknown_run_id_exits_nonzero(self, database_url: str, tmp_path: Path) -> None:
        _seed_run(database_url, "r1", ["R 1.", "R 2.", "R 3."])
        result = CliRunner().invoke(
            app,
            [
                "render-from-db",
                "--output-dir", str(tmp_path / "out"),
                "--database-url", database_url,
                "--run-id", "missing",
            ],
        )
        assert result.exit_code == 1
        assert not (tmp_path / "out").exists()
