"""
HTML rendering layer — converts pipeline results into HTML files.

Uses Jinja2 templates instead of string concatenation. The old pipeline's
report_inator.py built HTML by doing things like:
    report += '## ' + title + '\\n'
    report += '\\n\\n'.join(lines) + '\\n'

Problems with that approach:
  1. Hard to read — the HTML structure is buried inside Python string operations.
  2. No XSS protection — user-supplied content (article titles from RSS feeds)
     is inserted as raw strings, allowing script injection.
  3. No separation of concerns — formatting logic mixed with data logic.

Jinja2 templates solve all three:
  1. The template IS the HTML — you can open it in an editor and see the layout.
  2. autoescape=True automatically escapes < > & " ' in all template variables,
     so {{ article.title }} containing <script> becomes &lt;script&gt; in HTML.
  3. Python code only handles data preparation; templates handle presentation.

Template inheritance:
  base.html.j2   — shared <head>, styles, <body> wrapper
  index.html.j2  — extends base; the main strategic report
  topic.html.j2  — extends base; per-topic article summaries

  {% extends "base.html.j2" %} + {% block body %} lets each template override
  only the parts that differ, without duplicating the boilerplate.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .db_status import DbStatusReport
from .models import ArticleSummary, BulletDiff, CrossTopicSynthesis, TokenUsage, TopicResult
from .systems_signals import LaggedCorrelation

# Resolved relative to this file (core/renderer.py -> daily/templates/), so
# this works regardless of where the package is installed.
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _env() -> Environment:
    """
    Create a configured Jinja2 Environment.

    FileSystemLoader tells Jinja2 where to find template files.
    autoescape=True enables automatic HTML escaping for all {{ }} expressions.
    This is the most important security setting — without it, an article title
    containing <script>alert('xss')</script> would execute in the browser.

    We create a new Environment per call (rather than a module-level singleton)
    to keep the function pure and easy to test. The overhead is negligible since
    render_report is called once per pipeline run.
    """
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )


def _build_article_jsonld(articles: list[ArticleSummary]) -> str:
    """
    Build a JSON-LD array of schema:Article objects for a topic's articles —
    the same headline/url/datePublished fields rdf_export.py maps onto
    schema:Article, kept in sync for consistency.

    json.dumps() alone doesn't stop a string value like "</script>" from
    prematurely closing the surrounding <script> tag (the JSON spec allows
    literal < and > inside strings) — article titles come from RSS feeds we
    don't control, same threat model as the HTML escaping above. Escaping
    <, >, and & as \\uXXXX keeps the JSON semantically identical while
    making a breakout impossible.
    """
    payload = [
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": article.title,
            "url": article.link,
            "datePublished": article.publish_date,
        }
        for article in articles
    ]
    return (
        json.dumps(payload)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _dedupe_bidirectional_lag_zero(
    correlations: list[LaggedCorrelation],
) -> list[LaggedCorrelation]:
    """
    At lag=0, (A, B) and (B, A) report the identical correlation --
    systems_signals.py deliberately computes and returns both, since
    direction only matters once lag > 0 (does A's value predict B's
    later, not just move with it). Displaying both as separate rows would
    just show the same finding twice, so this keeps one entry per
    undirected lag=0 pair and leaves every lag > 0 entry untouched --
    a display-only transform, not a correctness fix to the underlying
    (already validated) analysis.
    """
    seen_lag_zero_pairs: set[frozenset[str]] = set()
    deduped = []
    for c in correlations:
        if c.lag == 0:
            key = frozenset({c.subject_a, c.subject_b})
            if key in seen_lag_zero_pairs:
                continue
            seen_lag_zero_pairs.add(key)
        deduped.append(c)
    return deduped


def render_report(
    results: list[TopicResult],
    output_dir: Path,
    hours_cutoff: int = 24,
    overview: CrossTopicSynthesis | None = None,
    diffs: dict[str, BulletDiff] | None = None,
    topic_signals: list[LaggedCorrelation] | None = None,
    tag_signals: list[LaggedCorrelation] | None = None,
) -> None:
    """
    Render all pipeline results to HTML files in output_dir.

    This is a pure rendering function: it takes data in, writes files out,
    and has no other side effects. It does not call the LLM, fetch feeds,
    or modify the results.

    topic_signals/tag_signals are systems_signals.py's
    topic_urgency_lagged_correlations()/tag_rate_lagged_correlations()
    output (already FDR-corrected and filtered at call time -- everything
    passed in here is treated as significant, there's no threshold logic
    left to apply in the template).

    Output files:
      index.html              — main strategic report (one section per topic)
      {topic}_summaries.html  — per-topic article summaries (only if articles exist)

    The slug → filename mapping strips the "feeds_" prefix:
      feeds_ai          → ai_summaries.html
      feeds_data_science → data_science_summaries.html
    """
    # Wipe any previous run's output before writing this run's files, so stale
    # pages (e.g. a topic's {slug}_summaries.html from a run where that topic
    # had articles, but this run doesn't) never linger alongside fresh ones.
    # mkdir(parents=True) then creates the directory fresh, including any
    # missing parent directories if it didn't exist at all.
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    env = _env()
    now = datetime.now().astimezone()
    date_str = str(now.date())                              # "2026-06-27"
    updated_str = now.strftime("%Y-%m-%d %H:%M:%S %Z")      # "2026-06-27 14:32:01 PDT"

    # Accumulate total token usage across all topics for the footer.
    total_usage = TokenUsage()
    for r in results:
        total_usage = total_usage + r.token_usage

    # env.get_template() loads and compiles the template file.
    # tmpl.render(**kwargs) substitutes the variables and returns the HTML string.
    # Path.write_text() writes the string to disk in one call.
    index_tmpl = env.get_template("index.html.j2")
    (output_dir / "index.html").write_text(
        index_tmpl.render(
            results=results,
            overview=overview,
            diffs=diffs or {},
            date=date_str,
            updated=updated_str,
            hours_cutoff=hours_cutoff,
            total_tokens=total_usage.total_tokens,
            topic_signals=_dedupe_bidirectional_lag_zero(topic_signals or []),
            tag_signals=_dedupe_bidirectional_lag_zero(tag_signals or []),
        )
    )

    # Per-topic summary pages — only written for topics that have articles.
    # Topics with errors or no news produce no summaries page, so the
    # template's source-material link only appears when there's something to link to.
    topic_tmpl = env.get_template("topic.html.j2")
    for result in results:
        if not result.articles:
            continue
        # str.removeprefix() (Python 3.9+) strips a prefix only if present.
        # "feeds_ai".removeprefix("feeds_") → "ai"
        # "other".removeprefix("feeds_")    → "other" (unchanged)
        slug = result.config.slug.removeprefix("feeds_")
        (output_dir / f"{slug}_summaries.html").write_text(
            topic_tmpl.render(
                result=result,
                date=date_str,
                updated=updated_str,
                article_jsonld=_build_article_jsonld(result.articles),
            )
        )


def render_db_status(report: DbStatusReport, output_path: Path) -> None:
    """
    Render a `db status` report to a single standalone HTML file at
    output_path.

    Unlike render_report(), this writes one file, not a directory, and
    doesn't wipe/recreate anything: `db status` is an on-demand diagnostic
    (see db_status.py), not a directory of output regenerated every run.
    Creates output_path's parent directories if needed -- the same
    mkdir(parents=True, exist_ok=True) rdf_export.export_rdf() already
    does for its --output path, since Path.write_text() doesn't create
    missing parent directories on its own.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    tmpl = _env().get_template("db_status.html.j2")
    output_path.write_text(
        tmpl.render(
            report=report,
            generated_at=now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        )
    )
