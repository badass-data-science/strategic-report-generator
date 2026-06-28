"""Tests for HTML rendering — covers all three TopicResult states."""

from pathlib import Path

import pytest

from strategic_reports.daily.core.models import (
    ArticleSummary,
    StrategicInsight,
    TokenUsage,
    TopicConfig,
    TopicResult,
)
from strategic_reports.daily.core.renderer import render_report


@pytest.fixture
def successful_result(sample_topic_config, sample_article_summary, sample_strategy):
    return TopicResult(
        config=sample_topic_config,
        articles=[sample_article_summary],
        strategy=sample_strategy,
        token_usage=TokenUsage(total_tokens=1200),
    )


@pytest.fixture
def error_result(tmp_path):
    cfg = TopicConfig(slug="feeds_biotech", title="Biotechnology", feeds_file=tmp_path / "x.json")
    return TopicResult(config=cfg, error="Feed file not found")


@pytest.fixture
def empty_result(tmp_path):
    cfg = TopicConfig(slug="feeds_defense", title="Defense", feeds_file=tmp_path / "y.json")
    return TopicResult(config=cfg)


class TestIndexPage:
    def test_index_html_created(self, tmp_path, successful_result):
        render_report([successful_result], output_dir=tmp_path)
        assert (tmp_path / "index.html").exists()

    def test_contains_topic_title(self, tmp_path, successful_result):
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "Artificial Intelligence" in html

    def test_contains_strategy_bullets(self, tmp_path, successful_result):
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        for bullet in successful_result.strategy.bullets:
            assert bullet in html

    def test_contains_source_link(self, tmp_path, successful_result):
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "ai_summaries.html" in html

    def test_error_state_shown(self, tmp_path, error_result):
        render_report([error_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "Feed file not found" in html
        assert "Biotechnology" in html

    def test_empty_state_shown(self, tmp_path, empty_result):
        render_report([empty_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "Defense" in html
        assert "No articles found" in html

    def test_token_count_in_footer(self, tmp_path, successful_result):
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "1200" in html

    def test_all_three_states_together(self, tmp_path, successful_result, error_result, empty_result):
        render_report([successful_result, error_result, empty_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "Artificial Intelligence" in html
        assert "Biotechnology" in html
        assert "Defense" in html

    def test_xss_escaping_in_title(self, tmp_path, sample_topic_config):
        malicious = ArticleSummary(
            title='<script>alert("xss")</script>',
            link="https://example.com",
            publish_date="2026-06-27",
            summary=["A.", "B.", "C."],
            tags=["a", "b", "c", "d", "e"],
        )
        result = TopicResult(
            config=sample_topic_config,
            articles=[malicious],
            strategy=StrategicInsight(bullets=["Insight 1.", "Insight 2.", "Insight 3."]),
        )
        render_report([result], output_dir=tmp_path)
        topic_html = (tmp_path / "ai_summaries.html").read_text()
        assert "<script>" not in topic_html
        assert "&lt;script&gt;" in topic_html

    def test_output_dir_created_if_missing(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "output"
        result = TopicResult(
            config=TopicConfig(slug="feeds_ai", title="AI", feeds_file=tmp_path / "x.json")
        )
        render_report([result], output_dir=nested)
        assert (nested / "index.html").exists()


class TestTopicSummaryPage:
    def test_summaries_file_created(self, tmp_path, successful_result):
        render_report([successful_result], output_dir=tmp_path)
        assert (tmp_path / "ai_summaries.html").exists()

    def test_no_summaries_file_for_empty_topic(self, tmp_path, empty_result):
        render_report([empty_result], output_dir=tmp_path)
        assert not (tmp_path / "defense_summaries.html").exists()

    def test_no_summaries_file_for_error_topic(self, tmp_path, error_result):
        render_report([error_result], output_dir=tmp_path)
        assert not (tmp_path / "biotech_summaries.html").exists()

    def test_contains_article_title(self, tmp_path, successful_result):
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "ai_summaries.html").read_text()
        assert "LLMs Keep Improving" in html

    def test_contains_article_link(self, tmp_path, successful_result):
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "ai_summaries.html").read_text()
        assert "example.com/llms" in html

    def test_contains_sorted_tags(self, tmp_path, successful_result):
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "ai_summaries.html").read_text()
        assert "artificial intelligence" in html

    def test_back_link_to_index(self, tmp_path, successful_result):
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "ai_summaries.html").read_text()
        assert "index.html" in html

    def test_slug_strip_prefix(self, tmp_path):
        """feeds_data_science → data_science_summaries.html"""
        feeds_file = tmp_path / "feeds_data_science.json"
        feeds_file.write_text('{"feeds": []}')
        result = TopicResult(
            config=TopicConfig(
                slug="feeds_data_science",
                title="Data Science",
                feeds_file=feeds_file,
            ),
            articles=[
                ArticleSummary(
                    title="Data Trends",
                    link="https://x.com",
                    publish_date="2026-06-27",
                    summary=["A.", "B.", "C."],
                    tags=["a", "b", "c", "d", "e"],
                )
            ],
            strategy=StrategicInsight(bullets=["X.", "Y.", "Z."]),
        )
        render_report([result], output_dir=tmp_path)
        assert (tmp_path / "data_science_summaries.html").exists()
