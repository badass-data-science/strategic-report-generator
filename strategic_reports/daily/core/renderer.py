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

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .models import BulletDiff, CrossTopicSynthesis, TokenUsage, TopicResult

# Resolve the templates directory relative to THIS file's location.
# Path(__file__) is the path to renderer.py.
# .parent       is core/
# .parent       is daily/
# / "templates" is daily/templates/
# This avoids hardcoded paths and works regardless of where the package is installed.
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


def render_report(
    results: list[TopicResult],
    output_dir: Path,
    hours_cutoff: int = 24,
    overview: CrossTopicSynthesis | None = None,
    diffs: dict[str, BulletDiff] | None = None,
) -> None:
    """
    Render all pipeline results to HTML files in output_dir.

    This is a pure rendering function: it takes data in, writes files out,
    and has no other side effects. It does not call the LLM, fetch feeds,
    or modify the results.

    Output files:
      index.html              — main strategic report (one section per topic)
      {topic}_summaries.html  — per-topic article summaries (only if articles exist)

    The slug → filename mapping strips the "feeds_" prefix:
      feeds_ai          → ai_summaries.html
      feeds_data_science → data_science_summaries.html
    """
    # mkdir(parents=True) creates any missing parent directories.
    # exist_ok=True means no error if the directory already exists.
    output_dir.mkdir(parents=True, exist_ok=True)

    env = _env()
    now = datetime.now()
    date_str = str(now.date())               # "2026-06-27"
    updated_str = str(now).split(".")[0]     # "2026-06-27 14:32:01" (no microseconds)

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
            )
        )
