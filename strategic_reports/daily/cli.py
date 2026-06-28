"""
Daily strategic report pipeline — CLI entrypoint.

Usage:
    python -m strategic_reports.daily.cli run

Key options (all have sensible defaults):
    --model             litellm model string  (default: env LLM_MODEL or ollama_chat/llama3.1:70b)
    --hours-cutoff      Article age window in hours (default: 24)
    --output-dir        Where to write HTML output
    --data-dir          Where to find rss_feeds/*.json files
    --batch-size        Articles per LLM summarization call
    --max-concurrent    Max concurrent LLM calls (semaphore width)
    --log-level         Logging verbosity
"""

import asyncio
import os
from pathlib import Path

import typer

from strategic_reports.daily.config.topic_order import list_directories_and_titles
from strategic_reports.daily.core import configure_logging, LLMClient, run_pipeline
from strategic_reports.daily.core.models import TopicConfig
from strategic_reports.daily.core.renderer import render_report
from strategic_reports.daily.core.tracing import generate_run_id, setup_tracing

app = typer.Typer(add_completion=False)

_DEFAULT_HOME = Path(os.environ.get("STRATEGIC_REPORTS_HOME", Path.cwd()))
_DEFAULT_MODEL = os.environ.get("LLM_MODEL", "ollama_chat/llama3.1:70b")


def _build_topic_configs(data_dir: Path) -> list[TopicConfig]:
    configs = []
    for item in list_directories_and_titles:
        feeds_file = data_dir / f"{item['slug']}.json"
        if not feeds_file.exists():
            typer.echo(f"[warn] feeds file not found, skipping: {feeds_file}", err=True)
            continue
        configs.append(
            TopicConfig(
                slug=item["slug"],
                title=item["title"],
                feeds_file=feeds_file,
            )
        )
    return configs


@app.command()
def run(
    model: str = typer.Option(
        _DEFAULT_MODEL,
        envvar="LLM_MODEL",
        help="litellm model string (e.g. 'ollama_chat/llama3.1:70b', 'anthropic/claude-sonnet-4-6')",
    ),
    hours_cutoff: int = typer.Option(24, help="Only consider articles published within this many hours"),
    output_dir: Path = typer.Option(
        _DEFAULT_HOME / "output" / "daily" / "strategic-report",
        envvar="STRATEGIC_REPORTS_OUTPUT_DIR",
        help="Directory to write HTML report files",
    ),
    data_dir: Path = typer.Option(
        _DEFAULT_HOME / "data" / "rss_feeds",
        envvar="STRATEGIC_REPORTS_DATA_DIR",
        help="Directory containing rss_feeds/*.json files",
    ),
    batch_size: int = typer.Option(50, help="Max articles per LLM summarization call"),
    max_concurrent: int = typer.Option(3, help="Max topics hitting the LLM API simultaneously"),
    temperature: float = typer.Option(0.1, help="LLM sampling temperature"),
    log_level: str = typer.Option("INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)"),
) -> None:
    """Run the daily strategic report pipeline and write results to output_dir."""
    configure_logging(log_level)

    active_backends = setup_tracing()
    run_id = generate_run_id()

    topics = _build_topic_configs(data_dir)
    if not topics:
        typer.echo("No valid topic configs found. Check --data-dir.", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Model:        {model}")
    typer.echo(f"Topics:       {len(topics)}")
    typer.echo(f"Hours cutoff: {hours_cutoff}h")
    typer.echo(f"Output:       {output_dir}")
    typer.echo(f"Run ID:       {run_id}")
    if any(active_backends.values()):
        active = [k for k, v in active_backends.items() if v]
        typer.echo(f"Tracing:      {', '.join(active)}")
    typer.echo("")

    client = LLMClient(
        model=model,
        temperature=temperature,
        run_metadata={"trace_id": run_id, "trace_name": "strategic-report-daily"},
    )

    results = asyncio.run(
        run_pipeline(
            topics=topics,
            client=client,
            hours_cutoff=hours_cutoff,
            batch_size=batch_size,
            max_concurrent_llm_calls=max_concurrent,
        )
    )

    render_report(results, output_dir=output_dir, hours_cutoff=hours_cutoff)

    successful = sum(1 for r in results if r.error is None and r.strategy is not None)
    failed = sum(1 for r in results if r.error is not None)
    empty = sum(1 for r in results if r.error is None and r.strategy is None)

    typer.echo("")
    typer.echo(f"Done.")
    typer.echo(f"  Topics with strategy:  {successful}")
    typer.echo(f"  Topics with no news:   {empty}")
    typer.echo(f"  Topics with errors:    {failed}")
    typer.echo(f"  Total tokens used:     {client.total_usage.total_tokens:,}")
    typer.echo(f"  Report written to:     {output_dir / 'index.html'}")


if __name__ == "__main__":
    app()
