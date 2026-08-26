"""
Tests for strategic_reports.daily.core.article_archive.

Covers:
  - record_articles / load_articles round-trip (title, link, publish_date,
    summary bullets, tags all survive)
  - bullet and tag ordering preserved
  - multi-topic, multi-article runs
  - error/empty topics (no articles) contribute nothing
  - load_articles for an unknown run_id returns an empty list
"""

from pathlib import Path

from strategic_reports.daily.core.article_archive import load_articles, record_articles
from strategic_reports.daily.core.db import record_run
from strategic_reports.daily.core.models import ArticleSummary, TopicConfig, TopicResult
from strategic_reports.daily.core.tag_normalizer import normalize_tags

# ArticleSummary normalizes tags via a Pydantic validator (synonym map,
# singularization, etc. — see tag_normalizer.py), so tests compare against
# the normalized form rather than hardcoding what that transform produces.
_FIVE_TAGS = normalize_tags(["ai", "tech", "models", "research", "benchmarks"])


def _make_article(title: str, link: str, tags: list[str] = _FIVE_TAGS) -> ArticleSummary:
    return ArticleSummary(
        title=title,
        link=link,
        publish_date="2026-07-31T09:00:00",
        summary=["Bullet one.", "Bullet two.", "Bullet three."],
        tags=tags,
    )


def _make_results(
    articles: list[ArticleSummary], topic_title: str = "Artificial Intelligence"
) -> list[TopicResult]:
    config = TopicConfig(
        slug=f"feeds_{topic_title.lower()}", title=topic_title, feeds_file=Path("/dev/null")
    )
    return [TopicResult(config=config, articles=articles)]


class TestRecordAndLoadArticles:
    def test_round_trip_single_article(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=1)
        results = _make_results([_make_article("AI Surges", "https://example.com/ai")])
        record_articles(database_url, "run-0", results)

        articles = load_articles(database_url, "run-0")
        assert len(articles) == 1
        a = articles[0]
        assert a["topic"] == "Artificial Intelligence"
        assert a["title"] == "AI Surges"
        assert a["link"] == "https://example.com/ai"
        assert a["publish_date"] == "2026-07-31T09:00:00"
        assert a["summary"] == ["Bullet one.", "Bullet two.", "Bullet three."]
        assert a["tags"] == _FIVE_TAGS

    def test_bullet_order_preserved(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=1)
        results = _make_results([_make_article("A", "https://example.com/a")])
        record_articles(database_url, "run-0", results)
        articles = load_articles(database_url, "run-0")
        assert articles[0]["summary"] == ["Bullet one.", "Bullet two.", "Bullet three."]

    def test_tag_order_preserved(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=1)
        tags = ["zebra", "alpha", "middle", "extra", "final"]
        results = _make_results([_make_article("A", "https://example.com/a", tags=tags)])
        record_articles(database_url, "run-0", results)
        articles = load_articles(database_url, "run-0")
        assert articles[0]["tags"] == normalize_tags(tags)

    def test_multiple_articles_same_topic(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=2)
        results = _make_results([
            _make_article("A1", "https://example.com/1"),
            _make_article("A2", "https://example.com/2"),
        ])
        record_articles(database_url, "run-0", results)
        articles = load_articles(database_url, "run-0")
        assert {a["title"] for a in articles} == {"A1", "A2"}

    def test_multiple_topics(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=2)
        ai_config = TopicConfig(
            slug="feeds_ai", title="Artificial Intelligence", feeds_file=Path("/dev/null")
        )
        defense_config = TopicConfig(
            slug="feeds_defense", title="Defense", feeds_file=Path("/dev/null")
        )
        results = [
            TopicResult(config=ai_config, articles=[_make_article("AI News", "https://example.com/ai")]),
            TopicResult(config=defense_config, articles=[_make_article("Defense News", "https://example.com/def")]),
        ]
        record_articles(database_url, "run-0", results)
        articles = load_articles(database_url, "run-0")
        topics = {a["topic"] for a in articles}
        assert topics == {"Artificial Intelligence", "Defense"}

    def test_error_topic_contributes_nothing(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=0)
        config = TopicConfig(
            slug="feeds_ai", title="Artificial Intelligence", feeds_file=Path("/dev/null")
        )
        results = [TopicResult(config=config, error="feed timeout")]
        record_articles(database_url, "run-0", results)
        assert load_articles(database_url, "run-0") == []

    def test_empty_topic_contributes_nothing(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=0)
        config = TopicConfig(
            slug="feeds_ai", title="Artificial Intelligence", feeds_file=Path("/dev/null")
        )
        results = [TopicResult(config=config)]
        record_articles(database_url, "run-0", results)
        assert load_articles(database_url, "run-0") == []

    def test_unknown_run_id_returns_empty(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=1)
        record_articles(database_url, "run-0", _make_results([_make_article("A", "https://example.com/a")]))
        assert load_articles(database_url, "nonexistent-run") == []

    def test_two_runs_kept_separate(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=1)
        record_articles(database_url, "run-0", _make_results([_make_article("Day 1", "https://example.com/1")]))
        record_run(database_url, "run-1", article_count=1)
        record_articles(database_url, "run-1", _make_results([_make_article("Day 2", "https://example.com/2")]))

        assert [a["title"] for a in load_articles(database_url, "run-0")] == ["Day 1"]
        assert [a["title"] for a in load_articles(database_url, "run-1")] == ["Day 2"]

    def test_record_articles_returns_zero_on_full_success(self, database_url: str) -> None:
        record_run(database_url, "run-0", article_count=1)
        results = _make_results([_make_article("A", "https://example.com/a")])
        assert record_articles(database_url, "run-0", results) == 0

    def test_one_bad_article_does_not_lose_the_whole_run(self, database_url: str) -> None:
        # A NUL byte is valid in a Python str (so ArticleSummary's plain
        # `title: str` field accepts it) but Postgres text columns reject it
        # outright -- a reliable, realistic way to make exactly one
        # article's insert fail without mocking. Before the per-article
        # savepoint fix, this exception would abort get_connection()'s
        # whole transaction and silently lose every article in the run,
        # not just the bad one -- see article_archive.py's docstring.
        record_run(database_url, "run-0", article_count=3)
        results = _make_results([
            _make_article("Good 1", "https://example.com/1"),
            _make_article("Bad \x00 title", "https://example.com/2"),
            _make_article("Good 3", "https://example.com/3"),
        ])

        failed_count = record_articles(database_url, "run-0", results)

        assert failed_count == 1
        titles = {a["title"] for a in load_articles(database_url, "run-0")}
        assert titles == {"Good 1", "Good 3"}
