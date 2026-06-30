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

WHY TYPER INSTEAD OF ARGPARSE
------------------------------
typer generates a CLI from function type annotations. This means:
  1. No separate add_argument() calls — the function signature IS the CLI spec
  2. Types are enforced: --batch-size 5.5 raises an error because it's int
  3. --help is generated automatically from docstrings and help= strings
  4. envvar= on each Option means the same parameter can be set by CLI flag
     or environment variable without any extra code:
       $ LLM_MODEL=gpt-4o python -m strategic_reports.daily.cli run
       # is equivalent to:
       $ python -m strategic_reports.daily.cli run --model gpt-4o

ASYNCIO BRIDGE PATTERN
-----------------------
typer calls the run() function synchronously (it's a normal def, not async def).
The pipeline is async. asyncio.run() is the standard bridge:
  - It creates a new event loop
  - Runs the async coroutine until it's done
  - Returns the result (list of TopicResults)
  - Closes the event loop

This is the correct pattern for a sync entry point that needs to call async code.
Don't use loop.run_until_complete() — it requires manually managing the loop.

SEPARATION OF CONCERNS
-----------------------
The CLI layer is responsible for:
  - Reading configuration (CLI args + env vars)
  - Printing human-readable status
  - Bridging sync (CLI) to async (pipeline)
  - Calling setup functions (logging, tracing, run_id)

The CLI is NOT responsible for:
  - Business logic (that's in pipeline.py)
  - Rendering (that's in renderer.py)
  - LLM calls (that's in llm_client.py)

This separation makes each layer independently testable.
"""

import asyncio
import os
from pathlib import Path

import instructor
import typer

from strategic_reports.daily.config.topic_order import list_directories_and_titles
from strategic_reports.daily.core import configure_logging, LLMClient, run_pipeline
from strategic_reports.daily.core.models import TopicConfig
from strategic_reports.daily.core.renderer import render_report
from strategic_reports.daily.core.tracing import generate_run_id, setup_tracing

# Maps CLI string → instructor.Mode enum value.
# TOOLS requires model-level function-calling support (OpenAI, Anthropic, capable Ollama).
# JSON works with most Ollama models that lack tool-call support.
_INSTRUCTOR_MODES: dict[str, instructor.Mode] = {
    "TOOLS": instructor.Mode.TOOLS,
    "JSON": instructor.Mode.JSON,
    "MD_JSON": instructor.Mode.MD_JSON,
}

# typer.Typer() creates the CLI application.
# add_completion=False disables shell completion setup — optional feature we don't need.
app = typer.Typer(add_completion=False)

# Read defaults from environment variables at module load time.
# This lets users set project-wide defaults via a .env file or CI secrets
# rather than passing flags on every invocation.
_DEFAULT_HOME = Path(os.environ.get("STRATEGIC_REPORTS_HOME", Path.cwd()))
_DEFAULT_MODEL = os.environ.get("LLM_MODEL", "ollama_chat/llama3.1:70b")


def _build_topic_configs(data_dir: Path) -> list[TopicConfig]:
    """
    Build TopicConfig objects from the project's topic_order configuration.

    list_directories_and_titles is a list of {"slug": ..., "title": ...} dicts
    that defines which topics exist and in what order they appear in the report.

    For each topic, we check if the corresponding feeds JSON file exists in
    data_dir. If it doesn't, we warn and skip rather than crash — a missing
    feeds file for one topic shouldn't prevent all other topics from running.

    typer.echo(..., err=True) writes to stderr instead of stdout, so warnings
    don't interfere with stdout output that might be piped to other commands.
    """
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
    # typer.Option() creates a CLI flag. Arguments:
    #   first arg: default value (shown in --help)
    #   envvar:    environment variable name; CLI flag takes precedence if both are set
    #   help:      shown in --help output
    model: str = typer.Option(
        _DEFAULT_MODEL,
        envvar="LLM_MODEL",
        help="litellm model string (e.g. 'ollama_chat/llama3.1:70b', 'anthropic/claude-sonnet-4-6')",
    ),
    hours_cutoff: int = typer.Option(
        24,
        help="Only consider articles published within this many hours",
    ),
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
    instructor_mode: str = typer.Option(
        "TOOLS",
        help="Structured output mode: TOOLS (default, requires tool-calling support), "
             "JSON (for Ollama models without tool calling), MD_JSON (JSON in markdown fences)",
    ),
    log_level: str = typer.Option("INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)"),
) -> None:
    """Run the daily strategic report pipeline and write results to output_dir."""

    # Validate --instructor-mode before doing any real work.
    mode_upper = instructor_mode.upper()
    if mode_upper not in _INSTRUCTOR_MODES:
        typer.echo(
            f"Invalid --instructor-mode '{instructor_mode}'. "
            f"Valid options: {', '.join(_INSTRUCTOR_MODES)}",
            err=True,
        )
        raise typer.Exit(code=1)
    resolved_mode = _INSTRUCTOR_MODES[mode_upper]

    # Configure logging FIRST so that all subsequent code produces structured output.
    configure_logging(log_level)

    # setup_tracing() checks environment variables and activates Langfuse/Phoenix.
    # Returns a dict like {"langfuse": True, "phoenix": False}.
    active_backends = setup_tracing()

    # generate_run_id() creates a UUID to group all LLM calls from this run
    # under one trace in Langfuse (or equivalent tracing backend).
    run_id = generate_run_id()

    # Build the ordered list of topic configs, skipping any with missing feeds files.
    topics = _build_topic_configs(data_dir)
    if not topics:
        # raise typer.Exit(code=1) is typer's way to exit with a non-zero status code,
        # signaling failure to the shell or CI system.
        typer.echo("No valid topic configs found. Check --data-dir.", err=True)
        raise typer.Exit(code=1)

    # Print a startup summary to stdout so the user knows what's running.
    typer.echo(f"Model:            {model}")
    typer.echo(f"Instructor mode:  {mode_upper}")
    typer.echo(f"Topics:           {len(topics)}")
    typer.echo(f"Hours cutoff:     {hours_cutoff}h")
    typer.echo(f"Output:           {output_dir}")
    typer.echo(f"Run ID:           {run_id}")
    if any(active_backends.values()):
        active = [k for k, v in active_backends.items() if v]
        typer.echo(f"Tracing:      {', '.join(active)}")
    typer.echo("")

    # Create ONE LLMClient for the entire pipeline run.
    # Sharing the client means token usage accumulates in one place (client.total_usage),
    # and the run_metadata is identical for every call (same trace_id).
    client = LLMClient(
        model=model,
        temperature=temperature,
        run_metadata={"trace_id": run_id, "trace_name": "strategic-report-daily"},
        instructor_mode=resolved_mode,
    )

    # asyncio.run() is the sync→async bridge:
    # It starts a new event loop, runs the coroutine to completion, and returns
    # the result. This is the correct top-level entry point for async code.
    results = asyncio.run(
        run_pipeline(
            topics=topics,
            client=client,
            hours_cutoff=hours_cutoff,
            batch_size=batch_size,
            max_concurrent_llm_calls=max_concurrent,
        )
    )

    # render_report() is synchronous (Jinja2 rendering is fast, no I/O bottleneck).
    render_report(results, output_dir=output_dir, hours_cutoff=hours_cutoff)

    # Summarize the run outcome. Three non-overlapping categories:
    #   successful: error is None AND strategy was generated
    #   empty:      error is None AND no articles were found
    #   failed:     error is not None (either ingestion or LLM stage failed)
    successful = sum(1 for r in results if r.error is None and r.strategy is not None)
    failed = sum(1 for r in results if r.error is not None)
    empty = sum(1 for r in results if r.error is None and r.strategy is None)

    typer.echo("")
    typer.echo(f"Done.")
    typer.echo(f"  Topics with strategy:  {successful}")
    typer.echo(f"  Topics with no news:   {empty}")
    typer.echo(f"  Topics with errors:    {failed}")
    # {:,} formats integers with thousands separators: 12345 → "12,345"
    typer.echo(f"  Total tokens used:     {client.total_usage.total_tokens:,}")
    typer.echo(f"  Report written to:     {output_dir / 'index.html'}")


if __name__ == "__main__":
    # Allows running as: python cli.py run
    # The @app.command() decorator is what registers the run() function.
    app()
