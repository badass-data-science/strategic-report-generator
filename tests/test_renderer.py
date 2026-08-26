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

import json
from pathlib import Path

import pytest
from strategic_reports.daily.core.models import (
    ArticleSummary,
    StrategicInsight,
    TokenUsage,
    TopicConfig,
    TopicResult,
)
from strategic_reports.daily.core.renderer import _dedupe_bidirectional_lag_zero, render_report
from strategic_reports.daily.core.systems_signals import LaggedCorrelation


def _correlation(
    subject_a: str, subject_b: str, lag: int = 0, correlation: float = 0.9, q_value: float = 0.02
) -> LaggedCorrelation:
    return LaggedCorrelation(
        subject_a=subject_a,
        subject_b=subject_b,
        lag=lag,
        correlation=correlation,
        n=12,
        p_value=0.001,
        q_value=q_value,
    )


@pytest.fixture
def successful_result(
    sample_topic_config: TopicConfig,
    sample_article_summary: ArticleSummary,
    sample_strategy: StrategicInsight,
) -> TopicResult:
    """A TopicResult with articles and a completed strategy — the 'success' state."""
    return TopicResult(
        config=sample_topic_config,
        articles=[sample_article_summary],
        strategy=sample_strategy,
        token_usage=TokenUsage(total_tokens=1200),
    )


@pytest.fixture
def error_result(tmp_path: Path) -> TopicResult:
    """A TopicResult where ingestion failed — the 'error' state."""
    cfg = TopicConfig(slug="feeds_biotech", title="Biotechnology", feeds_file=tmp_path / "x.json")
    return TopicResult(config=cfg, error="Feed file not found")


@pytest.fixture
def empty_result(tmp_path: Path) -> TopicResult:
    """A TopicResult where no recent articles were found — the 'empty' state."""
    cfg = TopicConfig(slug="feeds_defense", title="Defense", feeds_file=tmp_path / "y.json")
    return TopicResult(config=cfg)


class TestIndexPage:
    """Tests for index.html — the main strategic report page."""

    def test_index_html_created(self, tmp_path: Path, successful_result: TopicResult) -> None:
        """index.html must always be created, regardless of topic states."""
        render_report([successful_result], output_dir=tmp_path)
        assert (tmp_path / "index.html").exists()

    def test_contains_topic_title(self, tmp_path: Path, successful_result: TopicResult) -> None:
        """The topic title must appear so the reader can identify each section."""
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "Artificial Intelligence" in html

    def test_contains_strategy_bullets(
        self, tmp_path: Path, successful_result: TopicResult
    ) -> None:
        """All strategy bullet points must appear in the index page."""
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        # Verify every bullet from the strategy fixture appears in the HTML.
        assert successful_result.strategy is not None  # guaranteed by the fixture
        for bullet in successful_result.strategy.bullets:
            assert bullet in html

    def test_contains_source_link(self, tmp_path: Path, successful_result: TopicResult) -> None:
        """
        The index page should link to the per-topic summaries page.
        slug "feeds_ai" → file "ai_summaries.html" (removeprefix("feeds_")).
        """
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "ai_summaries.html" in html

    def test_error_state_shown(self, tmp_path: Path, error_result: TopicResult) -> None:
        """Error topics must show the error message (not crash silently)."""
        render_report([error_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "Feed file not found" in html
        assert "Biotechnology" in html

    def test_empty_state_shown(self, tmp_path: Path, empty_result: TopicResult) -> None:
        """Empty topics must show a 'no articles' message (not crash silently)."""
        render_report([empty_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "Defense" in html
        assert "No articles found" in html

    def test_token_count_in_footer(self, tmp_path: Path, successful_result: TopicResult) -> None:
        """Token usage (1200) should appear somewhere on the index page."""
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "1200" in html

    def test_all_three_states_together(
        self,
        tmp_path: Path,
        successful_result: TopicResult,
        error_result: TopicResult,
        empty_result: TopicResult,
    ) -> None:
        """
        Renderer must handle a mixed list of result states in one call.
        All three topic titles should appear in the single index.html file.
        """
        render_report([successful_result, error_result, empty_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "Artificial Intelligence" in html
        assert "Biotechnology" in html
        assert "Defense" in html

    def test_xss_escaping_in_title(self, tmp_path: Path, sample_topic_config: TopicConfig) -> None:
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
            strategy=StrategicInsight(
                bullets=["Insight 1.", "Insight 2.", "Insight 3."], urgency_score=0.4
            ),
        )
        render_report([result], output_dir=tmp_path)
        topic_html = (tmp_path / "ai_summaries.html").read_text()
        # The raw tag must NOT appear (that would be a XSS vulnerability).
        assert "<script>" not in topic_html
        # The escaped version MUST appear (that's what Jinja2 should produce).
        assert "&lt;script&gt;" in topic_html

    def test_output_dir_created_if_missing(self, tmp_path: Path) -> None:
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

    def test_summaries_file_created(self, tmp_path: Path, successful_result: TopicResult) -> None:
        """feeds_ai slug → ai_summaries.html (after removeprefix("feeds_"))."""
        render_report([successful_result], output_dir=tmp_path)
        assert (tmp_path / "ai_summaries.html").exists()

    def test_no_summaries_file_for_empty_topic(
        self, tmp_path: Path, empty_result: TopicResult
    ) -> None:
        """
        Topics with no articles should NOT produce a summaries file.
        There's nothing to link to, so the file shouldn't exist.
        """
        render_report([empty_result], output_dir=tmp_path)
        assert not (tmp_path / "defense_summaries.html").exists()

    def test_no_summaries_file_for_error_topic(
        self, tmp_path: Path, error_result: TopicResult
    ) -> None:
        """Topics with errors also don't get a summaries file."""
        render_report([error_result], output_dir=tmp_path)
        assert not (tmp_path / "biotech_summaries.html").exists()

    def test_contains_article_title(self, tmp_path: Path, successful_result: TopicResult) -> None:
        """The article title from the fixture must appear in the summaries page."""
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "ai_summaries.html").read_text()
        assert "LLMs Keep Improving" in html

    def test_contains_article_link(self, tmp_path: Path, successful_result: TopicResult) -> None:
        """The article URL must appear as a link in the summaries page."""
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "ai_summaries.html").read_text()
        assert "example.com/llms" in html

    def test_contains_sorted_tags(self, tmp_path: Path, successful_result: TopicResult) -> None:
        """At least one tag from the fixture should appear in the summaries page."""
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "ai_summaries.html").read_text()
        assert "artificial intelligence" in html

    def test_back_link_to_index(self, tmp_path: Path, successful_result: TopicResult) -> None:
        """The summaries page should have a link back to index.html for navigation."""
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "ai_summaries.html").read_text()
        assert "index.html" in html

    def test_jsonld_article_markup(self, tmp_path: Path, successful_result: TopicResult) -> None:
        """
        SECURITY/STRUCTURED-DATA TEST: each article gets a schema:Article
        JSON-LD entry with the same headline/url/datePublished fields
        rdf_export.py maps onto schema:Article.
        """
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "ai_summaries.html").read_text()

        marker = '<script type="application/ld+json">'
        start = html.index(marker) + len(marker)
        end = html.index("</script>", start)
        payload = json.loads(html[start:end])

        assert len(payload) == 1
        entry = payload[0]
        assert entry["@type"] == "Article"
        assert entry["headline"] == "LLMs Keep Improving"
        assert entry["url"] == "https://example.com/llms"
        assert entry["datePublished"] == "2026-06-27T09:00:00"

    def test_jsonld_xss_escaping(self, tmp_path: Path, sample_topic_config: TopicConfig) -> None:
        """
        A malicious article title containing "</script>" must not be able to
        break out of the JSON-LD <script> block — it should appear as an
        escaped \\u003c...\\u003e sequence inside the JSON string instead.
        """
        malicious = ArticleSummary(
            title="</script><script>alert(1)</script>",
            link="https://example.com",
            publish_date="2026-06-27",
            summary=["A.", "B.", "C."],
            tags=["a", "b", "c", "d", "e"],
        )
        result = TopicResult(
            config=sample_topic_config,
            articles=[malicious],
            strategy=StrategicInsight(
                bullets=["Insight 1.", "Insight 2.", "Insight 3."], urgency_score=0.4
            ),
        )
        render_report([result], output_dir=tmp_path)
        html = (tmp_path / "ai_summaries.html").read_text()

        assert "<script>alert(1)</script>" not in html
        assert "\\u003cscript\\u003ealert(1)\\u003c/script\\u003e" in html

    def test_slug_strip_prefix(self, tmp_path: Path) -> None:
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
            strategy=StrategicInsight(bullets=["X.", "Y.", "Z."], urgency_score=0.4),
        )
        render_report([result], output_dir=tmp_path)
        assert (tmp_path / "data_science_summaries.html").exists()


class TestDedupeBidirectionalLagZero:
    """Tests for renderer._dedupe_bidirectional_lag_zero (display-only, not systems_signals.py)."""

    def test_lag_zero_mirror_pair_collapses_to_one(self) -> None:
        a_to_b = _correlation("Energy", "Forex", lag=0)
        b_to_a = _correlation("Forex", "Energy", lag=0)
        assert _dedupe_bidirectional_lag_zero([a_to_b, b_to_a]) == [a_to_b]

    def test_lag_greater_than_zero_keeps_both_directions(self) -> None:
        a_to_b = _correlation("Energy", "Forex", lag=1)
        b_to_a = _correlation("Forex", "Energy", lag=1)
        assert _dedupe_bidirectional_lag_zero([a_to_b, b_to_a]) == [a_to_b, b_to_a]

    def test_unrelated_pairs_all_kept(self) -> None:
        c1 = _correlation("Energy", "Forex", lag=0)
        c2 = _correlation("Defense", "Genomics", lag=0)
        assert _dedupe_bidirectional_lag_zero([c1, c2]) == [c1, c2]

    def test_empty_list(self) -> None:
        assert _dedupe_bidirectional_lag_zero([]) == []


class TestSystemsSignalsSection:
    """Tests for the "Systems Signals" section of index.html."""

    def test_omitted_content_when_no_signals(
        self, tmp_path: Path, successful_result: TopicResult
    ) -> None:
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert "Systems Signals" in html
        assert "No candidate feedback loops cleared significance today." in html

    def test_topic_signal_appears(self, tmp_path: Path, successful_result: TopicResult) -> None:
        render_report(
            [successful_result],
            output_dir=tmp_path,
            topic_signals=[_correlation("Energy", "Forex", lag=0, correlation=0.92, q_value=0.002)],
        )
        html = (tmp_path / "index.html").read_text()
        assert "Topic Urgency" in html
        assert "Energy" in html and "Forex" in html
        assert "0.002" in html
        assert "No candidate feedback loops cleared significance today." not in html

    def test_tag_signal_appears(self, tmp_path: Path, successful_result: TopicResult) -> None:
        render_report(
            [successful_result],
            output_dir=tmp_path,
            tag_signals=[_correlation("derivatives", "market", lag=1, correlation=0.99, q_value=0.01)],
        )
        html = (tmp_path / "index.html").read_text()
        assert "Tag Coverage" in html
        assert "derivatives" in html and "market" in html
        # lag > 0 is directional -- "led ... by 1 run", not the "↔" symmetric symbol.
        assert "led" in html

    def test_only_topic_signals_omits_tag_coverage_group(
        self, tmp_path: Path, successful_result: TopicResult
    ) -> None:
        render_report(
            [successful_result],
            output_dir=tmp_path,
            topic_signals=[_correlation("Energy", "Forex")],
        )
        html = (tmp_path / "index.html").read_text()
        assert "Topic Urgency" in html
        assert "Tag Coverage" not in html

    def test_bidirectional_lag_zero_pair_shown_once(
        self, tmp_path: Path, successful_result: TopicResult
    ) -> None:
        render_report(
            [successful_result],
            output_dir=tmp_path,
            topic_signals=[
                _correlation("Energy", "Forex", lag=0),
                _correlation("Forex", "Energy", lag=0),
            ],
        )
        html = (tmp_path / "index.html").read_text()
        assert html.count("Significant · q =") == 1

    def test_toc_entry_present_even_with_no_signals(
        self, tmp_path: Path, successful_result: TopicResult
    ) -> None:
        render_report([successful_result], output_dir=tmp_path)
        html = (tmp_path / "index.html").read_text()
        assert '<a href="#systems-signals">Systems Signals</a>' in html

    def test_xss_escaping_in_subject_names(
        self, tmp_path: Path, successful_result: TopicResult
    ) -> None:
        """Tag strings ultimately derive from feed content -- same threat model as article titles."""
        render_report(
            [successful_result],
            output_dir=tmp_path,
            tag_signals=[_correlation("<script>alert(1)</script>", "market")],
        )
        html = (tmp_path / "index.html").read_text()
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
