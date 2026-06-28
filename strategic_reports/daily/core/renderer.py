from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .models import TokenUsage, TopicResult

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )


def render_report(
    results: list[TopicResult],
    output_dir: Path,
    hours_cutoff: int = 24,
) -> None:
    """
    Render all pipeline results to HTML files in output_dir.

    Writes:
      index.html                     — main strategic report
      {topic}_summaries.html         — per-topic article summaries
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    env = _env()
    now = datetime.now()
    date_str = str(now.date())
    updated_str = str(now).split(".")[0]

    total_usage = TokenUsage()
    for r in results:
        total_usage = total_usage + r.token_usage

    # Main strategic report
    index_tmpl = env.get_template("index.html.j2")
    (output_dir / "index.html").write_text(
        index_tmpl.render(
            results=results,
            date=date_str,
            updated=updated_str,
            hours_cutoff=hours_cutoff,
            total_tokens=total_usage.total_tokens,
        )
    )

    # Per-topic summary pages
    topic_tmpl = env.get_template("topic.html.j2")
    for result in results:
        if not result.articles:
            continue
        slug = result.config.slug.removeprefix("feeds_")
        (output_dir / f"{slug}_summaries.html").write_text(
            topic_tmpl.render(
                result=result,
                date=date_str,
                updated=updated_str,
            )
        )
