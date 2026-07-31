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


def _make_results(articles: list[ArticleSummary], topic_title: str = "Artificial Intelligence") -> list[TopicResult]:
    config = TopicConfig(slug=f"feeds_{topic_title.lower()}", title=topic_title, feeds_file="/dev/null")
    return [TopicResult(config=config, articles=articles)]


class TestRecordAndLoadArticles:
    def test_round_trip_single_article(self, db_path):
        record_run(db_path, "run-0", article_count=1)
        results = _make_results([_make_article("AI Surges", "https://example.com/ai")])
        record_articles(db_path, "run-0", results)

        articles = load_articles(db_path, "run-0")
        assert len(articles) == 1
        a = articles[0]
        assert a["topic"] == "Artificial Intelligence"
        assert a["title"] == "AI Surges"
        assert a["link"] == "https://example.com/ai"
        assert a["publish_date"] == "2026-07-31T09:00:00"
        assert a["summary"] == ["Bullet one.", "Bullet two.", "Bullet three."]
        assert a["tags"] == _FIVE_TAGS

    def test_bullet_order_preserved(self, db_path):
        record_run(db_path, "run-0", article_count=1)
        results = _make_results([_make_article("A", "https://example.com/a")])
        record_articles(db_path, "run-0", results)
        articles = load_articles(db_path, "run-0")
        assert articles[0]["summary"] == ["Bullet one.", "Bullet two.", "Bullet three."]

    def test_tag_order_preserved(self, db_path):
        record_run(db_path, "run-0", article_count=1)
        tags = ["zebra", "alpha", "middle", "extra", "final"]
        results = _make_results([_make_article("A", "https://example.com/a", tags=tags)])
        record_articles(db_path, "run-0", results)
        articles = load_articles(db_path, "run-0")
        assert articles[0]["tags"] == normalize_tags(tags)

    def test_multiple_articles_same_topic(self, db_path):
        record_run(db_path, "run-0", article_count=2)
        results = _make_results([
            _make_article("A1", "https://example.com/1"),
            _make_article("A2", "https://example.com/2"),
        ])
        record_articles(db_path, "run-0", results)
        articles = load_articles(db_path, "run-0")
        assert {a["title"] for a in articles} == {"A1", "A2"}

    def test_multiple_topics(self, db_path):
        record_run(db_path, "run-0", article_count=2)
        ai_config = TopicConfig(slug="feeds_ai", title="Artificial Intelligence", feeds_file="/dev/null")
        defense_config = TopicConfig(slug="feeds_defense", title="Defense", feeds_file="/dev/null")
        results = [
            TopicResult(config=ai_config, articles=[_make_article("AI News", "https://example.com/ai")]),
            TopicResult(config=defense_config, articles=[_make_article("Defense News", "https://example.com/def")]),
        ]
        record_articles(db_path, "run-0", results)
        articles = load_articles(db_path, "run-0")
        topics = {a["topic"] for a in articles}
        assert topics == {"Artificial Intelligence", "Defense"}

    def test_error_topic_contributes_nothing(self, db_path):
        record_run(db_path, "run-0", article_count=0)
        config = TopicConfig(slug="feeds_ai", title="Artificial Intelligence", feeds_file="/dev/null")
        results = [TopicResult(config=config, error="feed timeout")]
        record_articles(db_path, "run-0", results)
        assert load_articles(db_path, "run-0") == []

    def test_empty_topic_contributes_nothing(self, db_path):
        record_run(db_path, "run-0", article_count=0)
        config = TopicConfig(slug="feeds_ai", title="Artificial Intelligence", feeds_file="/dev/null")
        results = [TopicResult(config=config)]
        record_articles(db_path, "run-0", results)
        assert load_articles(db_path, "run-0") == []

    def test_unknown_run_id_returns_empty(self, db_path):
        record_run(db_path, "run-0", article_count=1)
        record_articles(db_path, "run-0", _make_results([_make_article("A", "https://example.com/a")]))
        assert load_articles(db_path, "nonexistent-run") == []

    def test_two_runs_kept_separate(self, db_path):
        record_run(db_path, "run-0", article_count=1)
        record_articles(db_path, "run-0", _make_results([_make_article("Day 1", "https://example.com/1")]))
        record_run(db_path, "run-1", article_count=1)
        record_articles(db_path, "run-1", _make_results([_make_article("Day 2", "https://example.com/2")]))

        assert [a["title"] for a in load_articles(db_path, "run-0")] == ["Day 1"]
        assert [a["title"] for a in load_articles(db_path, "run-1")] == ["Day 2"]
