"""
Tests for HTML rendering (renderer.py) — covers all TopicResult states and XSS.

WHAT WE'RE TESTING
-------------------
The renderer is a pure function: it takes data (list[TopicResult]) and writes
HTML files to disk. Tests verify:
  1. Correct files are created (or not created) based on result state
  2. Key data appears in the output HTML
  3. Malicious content is escaped (XSS prevention via Jinja2 autoescape)
  4. The output directory is created if it doesn't exist

THREE RESULT STATES
--------------------
TopicResult has three states that render differently:
  successful: strategy is not None, articles non-empty → full report section
  empty:      strategy is None, articles empty, error None → "No articles found"
  error:      error is not None → error message shown instead of strategy

Each state gets at least one test, plus a combined test to verify the renderer
handles a mixed list of states correctly.

TESTING XSS ESCAPING
----------------------
This is the most important security test in the suite. Article titles come from
RSS feeds we don't control — anyone who can publish an RSS feed could inject
<script> tags into their article title.

Jinja2's autoescape=True converts < to &lt; and > to &gt; automatically.
The test verifies that "<script>" does NOT appear in the rendered HTML, and
"&lt;script&gt;" DOES appear (the escaped version).

We test on the topic summaries page (ai_summaries.html) rather than index.html
because that's where article.title is rendered directly. The index page shows
strategy bullets (our own generated content, not raw feed content).

PATTERN: tmp_path FIXTURE
--------------------------
tmp_path is a pytest built-in that provides a fresh temporary directory for
each test. render_report writes its output there, and we read the files back
to verify content. tmp_path is automatically cleaned up after each test.

PATTERN: FIXTURE COMPOSITION IN TEST FILES
-------------------------------------------
Local fixtures (successful_result, error_result, empty_result) are defined
in this file because they're specific to renderer tests. They compose fixtures
from conftest.py (sample_topic_config, sample_article_summary, sample_strategy)
to avoid duplicating data setup.
"""

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
    """A TopicResult with articles and a completed strategy — the 'success' state."""
    return TopicResult(
        config=sample_topic_config,
        articles=[sample_article_summary],
        strategy=sample_strategy,
        token_usage=TokenUsage(total_tokens=1200),
    )


@pytest.fixture
def error_result(tmp_path):
    """A TopicResult where ingestion failed — the 'error' state."""
    cfg = TopicConfig(slug="feeds_biotech", title="Biotechnology", feeds_file=tmp_path / "x.json")
    return TopicResult(config=cfg, error="Feed file not found")


@pytest.fixture
def empty_result(tmp_path):
    """A TopicResult where no recent articles were found — the 'empty' state."""
    cfg = TopicConfig(slug="feeds_defense", title="Defense", feeds_file=tmp_path / "y.json")
    return TopicResult(config=cfg)


class TestIndexPage:
    """Tests for index.html — the main strategic report page."""

    def test_index_html_created(self, tmp_path, successful_result):
        """index.html must always be created, regardless of topic states."""
        render_report([successful_result], output_dir=tmp_path)
        assert (tmp_path / "index.html").exists()

    def test_contains_topic_title(self, tmp_path, successful_result):
        """The topic title must appear so the reader can identify each section."""
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "Artificial Intelligence" in html

    def test_contains_strategy_bullets(self, tmp_path, successful_result):
        """All strategy bullet points must appear in the index page."""
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        # Verify every bullet from the strategy fixture appears in the HTML.
        for bullet in successful_result.strategy.bullets:
            assert bullet in html

    def test_contains_source_link(self, tmp_path, successful_result):
        """
        The index page should link to the per-topic summaries page.
        slug "feeds_ai" → file "ai_summaries.html" (removeprefix("feeds_")).
        """
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "ai_summaries.html" in html

    def test_error_state_shown(self, tmp_path, error_result):
        """Error topics must show the error message (not crash silently)."""
        render_report([error_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "Feed file not found" in html
        assert "Biotechnology" in html

    def test_empty_state_shown(self, tmp_path, empty_result):
        """Empty topics must show a 'no articles' message (not crash silently)."""
        render_report([empty_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "Defense" in html
        assert "No articles found" in html

    def test_token_count_in_footer(self, tmp_path, successful_result):
        """Token usage (1200) should appear somewhere on the index page."""
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "1200" in html

    def test_all_three_states_together(self, tmp_path, successful_result, error_result, empty_result):
        """
        Renderer must handle a mixed list of result states in one call.
        All three topic titles should appear in the single index.html file.
        """
        render_report([successful_result, error_result, empty_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "Artificial Intelligence" in html
        assert "Biotechnology" in html
        assert "Defense" in html

    def test_xss_escaping_in_title(self, tmp_path, sample_topic_config):
        """
        SECURITY TEST: article titles from RSS feeds must be HTML-escaped.

        Jinja2's autoescape=True converts special HTML characters automatically:
          < → &lt;
          > → &gt;
          & → &amp;
          " → &#34;

        If autoescape were False (the dangerous default in some Jinja2 setups),
        the raw <script> tag would be injected into the HTML and execute in browsers.

        We test the topic summaries page because that's where article.title is
        rendered — it comes directly from the RSS feed content.
        """
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
        # The raw tag must NOT appear (that would be a XSS vulnerability).
        assert "<script>" not in topic_html
        # The escaped version MUST appear (that's what Jinja2 should produce).
        assert "&lt;script&gt;" in topic_html

    def test_output_dir_created_if_missing(self, tmp_path):
        """
        render_report should create the output directory if it doesn't exist.
        mkdir(parents=True, exist_ok=True) handles nested missing directories.
        """
        nested = tmp_path / "deep" / "nested" / "output"
        # Note: nested doesn't exist yet — render_report must create it.
        result = TopicResult(
            config=TopicConfig(slug="feeds_ai", title="AI", feeds_file=tmp_path / "x.json")
        )
        render_report([result], output_dir=nested)
        assert (nested / "index.html").exists()


class TestTopicSummaryPage:
    """Tests for {topic}_summaries.html pages — per-topic article detail pages."""

    def test_summaries_file_created(self, tmp_path, successful_result):
        """feeds_ai slug → ai_summaries.html (after removeprefix("feeds_"))."""
        render_report([successful_result], output_dir=tmp_path)
        assert (tmp_path / "ai_summaries.html").exists()

    def test_no_summaries_file_for_empty_topic(self, tmp_path, empty_result):
        """
        Topics with no articles should NOT produce a summaries file.
        There's nothing to link to, so the file shouldn't exist.
        """
        render_report([empty_result], output_dir=tmp_path)
        assert not (tmp_path / "defense_summaries.html").exists()

    def test_no_summaries_file_for_error_topic(self, tmp_path, error_result):
        """Topics with errors also don't get a summaries file."""
        render_report([error_result], output_dir=tmp_path)
        assert not (tmp_path / "biotech_summaries.html").exists()

    def test_contains_article_title(self, tmp_path, successful_result):
        """The article title from the fixture must appear in the summaries page."""
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "ai_summaries.html").read_text()
        assert "LLMs Keep Improving" in html

    def test_contains_article_link(self, tmp_path, successful_result):
        """The article URL must appear as a link in the summaries page."""
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "ai_summaries.html").read_text()
        assert "example.com/llms" in html

    def test_contains_sorted_tags(self, tmp_path, successful_result):
        """At least one tag from the fixture should appear in the summaries page."""
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "ai_summaries.html").read_text()
        assert "artificial intelligence" in html

    def test_back_link_to_index(self, tmp_path, successful_result):
        """The summaries page should have a link back to index.html for navigation."""
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "ai_summaries.html").read_text()
        assert "index.html" in html

    def test_slug_strip_prefix(self, tmp_path):
        """
        Verify the slug → filename mapping works for multi-word slugs.
        feeds_data_science → data_science_summaries.html

        This tests the str.removeprefix("feeds_") call in renderer.py.
        """
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
