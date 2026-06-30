"""
Prefect flow for the daily strategic report pipeline.

This flow is configured for a hosted Ollama instance running gpt-oss:120b.
instructor_mode defaults to JSON because gpt-oss:120b does not support the
tool-calling API that TOOLS mode requires.

WHY PREFECT?
------------
The CLI (cli.py) is fine for running the pipeline manually. Prefect adds:
  - Scheduling: define the cron once, Prefect triggers it automatically
  - Run history: every run is recorded with status, duration, and logs
  - Retries: transient LLM API failures are retried automatically at the
    task level, not by wrapping everything in a try/except loop
  - Observability: each task shows its own status in the UI

PREFECT'S TWO DECORATORS
--------------------------
@flow   — the top-level unit. One flow = one entry in the run history.
@task   — a step within a flow. Tasks get independent status, retry
          configuration, and their own log view.

RUNNING WITH LOCAL PREFECT (no cloud account required)
-------------------------------------------------------
1. Start the local Prefect server (keep this terminal open):

       prefect server start

   The UI is available at http://localhost:4200

2. Point the client at the local server:

       prefect config set PREFECT_API_URL=http://localhost:4200/api

3. Set required environment variables (add to .env or export in shell):

       export OLLAMA_API_BASE=http://your-ollama-server:11434
       export OLLAMA_API_KEY=your-key-if-required
       export LLM_MODEL=ollama_chat/gpt-oss:120b

4. Start the scheduler (keep this terminal open):

       cd <project-root>
       python flows/daily_report.py

   The flow registers with the local server and polls for scheduled runs.
   The schedule is 00:30 America/Los_Angeles daily.

5. Trigger a one-off run immediately (in a separate terminal):

       prefect deployment run 'daily-strategic-report/daily-strategic-report'

   Override parameters for a one-off run:

       prefect deployment run 'daily-strategic-report/daily-strategic-report' \\
           --param hours_cutoff=48
"""

import os
from pathlib import Path

import instructor
from prefect import flow, task, get_run_logger
from prefect.client.schemas.schedules import CronSchedule

from strategic_reports.daily.config.topic_order import list_directories_and_titles
from strategic_reports.daily.core import configure_logging, LLMClient, run_pipeline
from strategic_reports.daily.core.models import TopicConfig, TopicResult
from strategic_reports.daily.core.renderer import render_report
from strategic_reports.daily.core.tracing import generate_run_id, setup_tracing

# Anchor defaults to the project root (flows/../) regardless of the working
# directory from which this file is invoked. Path.cwd() would break if you ran
# `python flows/daily_report.py` from outside the project root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_HOME = Path(os.environ.get("STRATEGIC_REPORTS_HOME", _PROJECT_ROOT))
_DEFAULT_MODEL = os.environ.get("LLM_MODEL", "ollama_chat/llama3.1:70b")

_INSTRUCTOR_MODES: dict[str, instructor.Mode] = {
    "TOOLS": instructor.Mode.TOOLS,
    "JSON": instructor.Mode.JSON,
    "MD_JSON": instructor.Mode.MD_JSON,
}


# ---------------------------------------------------------------------------
# Task 1: Load configuration
# ---------------------------------------------------------------------------
# This is a sync @task — Prefect handles sync tasks just fine alongside async
# ones. Making it a task (rather than a plain function call in the flow) means
# config-load failures show up as a distinct "build-topic-configs FAILED" node
# in the Prefect UI, rather than being buried in the flow logs.

@task(name="build-topic-configs")
def build_topic_configs(data_dir: Path) -> list[TopicConfig]:
    """Scan data_dir for feed JSON files and return one TopicConfig per file found."""
    logger = get_run_logger()
    configs = []
    for item in list_directories_and_titles:
        feeds_file = data_dir / f"{item['slug']}.json"
        if not feeds_file.exists():
            logger.warning(f"feeds file not found, skipping: {feeds_file}")
            continue
        configs.append(TopicConfig(
            slug=item["slug"],
            title=item["title"],
            feeds_file=feeds_file,
        ))
    logger.info(f"Loaded {len(configs)} topic configs from {data_dir}")
    return configs


# ---------------------------------------------------------------------------
# Task 2: Run the LLM pipeline
# ---------------------------------------------------------------------------
# This is the expensive step — RSS fetching + dozens of LLM API calls.
#
# retries=2, retry_delay_seconds=60:
#   If the task raises (e.g. transient rate-limit, network timeout), Prefect
#   waits 60 seconds and tries again, up to 2 retries (3 total attempts).
#   This is cleaner than wrapping the pipeline in a while-loop with sleep().
#   Note that individual topic failures are already isolated inside run_pipeline
#   itself — retries here are for whole-task-level failures like a network
#   outage or a provider outage that kills all calls at once.
#
# The LLMClient is created inside this task rather than in the flow and passed
# in because it holds an async HTTP session that can't be serialized across
# Prefect's task result storage boundary (Prefect persists task results to
# handle retries and caching).

@task(
    name="run-llm-pipeline",
    retries=2,
    retry_delay_seconds=60,
)
async def run_llm_pipeline(
    topics: list[TopicConfig],
    model: str,
    hours_cutoff: int,
    batch_size: int,
    max_concurrent: int,
    temperature: float,
    instructor_mode_str: str,
    run_id: str,
    api_base: str | None = None,
    api_key: str | None = None,
) -> list[TopicResult]:
    """Run RSS ingestion and LLM summarization + synthesis for all topics."""
    logger = get_run_logger()
    mode = _INSTRUCTOR_MODES.get(instructor_mode_str.upper(), instructor.Mode.TOOLS)

    client = LLMClient(
        model=model,
        temperature=temperature,
        run_metadata={"trace_id": run_id, "trace_name": "strategic-report-daily"},
        instructor_mode=mode,
        api_base=api_base,
        api_key=api_key,
    )

    logger.info(f"Pipeline starting: {len(topics)} topics, model={model}, instructor_mode={instructor_mode_str}")

    results = await run_pipeline(
        topics=topics,
        client=client,
        hours_cutoff=hours_cutoff,
        batch_size=batch_size,
        max_concurrent_llm_calls=max_concurrent,
    )

    successful = sum(1 for r in results if r.error is None and r.strategy is not None)
    failed = sum(1 for r in results if r.error is not None)
    empty = sum(1 for r in results if r.error is None and r.strategy is None)
    logger.info(
        f"Pipeline complete — successful: {successful}, empty: {empty}, "
        f"failed: {failed}, tokens used: {client.total_usage.total_tokens:,}"
    )

    return results


# ---------------------------------------------------------------------------
# Task 3: Render HTML output
# ---------------------------------------------------------------------------
# Separated from the LLM task so that a rendering failure (template error,
# disk full, permissions problem) appears as "render-html-report FAILED" in
# the UI, clearly distinct from an LLM pipeline failure.

@task(name="render-html-report")
def render_html_report(results: list[TopicResult], output_dir: Path, hours_cutoff: int) -> None:
    """Render TopicResults into the HTML report using Jinja2 templates."""
    logger = get_run_logger()
    render_report(results, output_dir=output_dir, hours_cutoff=hours_cutoff)
    logger.info(f"Report written to {output_dir / 'index.html'}")


# ---------------------------------------------------------------------------
# Flow — the top-level unit that Prefect schedules and tracks
# ---------------------------------------------------------------------------
# async def flow: Prefect fully supports async flows. The async keyword is
# required here because run_llm_pipeline is an async task and we await it.
# Prefect manages the asyncio event loop — we don't need asyncio.run() as
# in the CLI entrypoint.
#
# log_prints=True: any print() call inside the flow or its tasks is captured
# as a Prefect log line, visible in the UI alongside get_run_logger() output.
#
# All parameters have defaults so the flow runs without any arguments on
# schedule. They're also exposed in the Prefect UI as overridable fields,
# making it easy to trigger a one-off run with e.g. a different model.

@flow(
    name="daily-strategic-report",
    description=(
        "Fetches recent news across 11 topic feeds (AI, biotech, geopolitics, …) "
        "and synthesizes per-topic strategic bullet points into a linked HTML report."
    ),
    log_prints=True,
)
async def daily_report_flow(
    model: str = _DEFAULT_MODEL,
    hours_cutoff: int = 24,
    output_dir: Path = _DEFAULT_HOME / "output" / "daily" / "strategic-report",
    data_dir: Path = _DEFAULT_HOME / "data" / "rss_feeds",
    batch_size: int = 50,
    max_concurrent: int = 3,
    temperature: float = 0.1,
    # JSON mode is the default here because this flow is configured for
    # gpt-oss:120b, which does not support the tool-calling API.
    # The CLI (cli.py) retains TOOLS as its default for general use.
    instructor_mode: str = "JSON",
    ollama_api_base: str | None = os.environ.get("OLLAMA_API_BASE"),
    ollama_api_key: str | None = os.environ.get("OLLAMA_API_KEY"),
    log_level: str = "INFO",
) -> None:
    """Daily strategic report: ingest RSS feeds, summarize, synthesize, render HTML."""
    # configure_logging sets up structlog for the pipeline's internal logging.
    # Prefect has its own logging layer on top; the two coexist fine.
    configure_logging(log_level)
    setup_tracing()
    run_id = generate_run_id()

    topics = build_topic_configs(data_dir)

    if not topics:
        raise ValueError(f"No valid topic configs found in {data_dir}. Check the data_dir parameter.")

    results = await run_llm_pipeline(
        topics=topics,
        model=model,
        hours_cutoff=hours_cutoff,
        batch_size=batch_size,
        max_concurrent=max_concurrent,
        temperature=temperature,
        instructor_mode_str=instructor_mode,
        run_id=run_id,
        api_base=ollama_api_base,
        api_key=ollama_api_key,
    )

    render_html_report(results, output_dir, hours_cutoff)


# ---------------------------------------------------------------------------
# Entry point — registers the deployment and starts the local scheduler
# ---------------------------------------------------------------------------
# flow.serve() is Prefect's "lightweight deployment" pattern:
#   - No Docker, no Kubernetes, no separate worker process required
#   - The process running this file IS the worker
#   - It registers (or updates) the deployment in Prefect Cloud on startup,
#     then polls for scheduled runs and executes them in-process
#
# CronSchedule(cron="30 0 * * *", timezone="America/Los_Angeles"):
#   "30 0 * * *" = minute 30, hour 0, every day → 00:30 daily
#   timezone="America/Los_Angeles" → Pacific time (PST/PDT, DST-aware)
#   Prefect Cloud handles DST transitions automatically.
#
# To keep this running persistently on a Linux server:
#   sudo systemctl edit --force --full strategic-reports.service
#   (see README for the full unit file)

if __name__ == "__main__":
    daily_report_flow.serve(
        name="daily-strategic-report",
        schedules=[CronSchedule(cron="30 0 * * *", timezone="America/Los_Angeles")],
        tags=["strategic-reports", "daily"],
        description=(
            "Daily strategic intelligence report. "
            "Runs at 00:30 Pacific time. "
            "Synthesizes recent news across AI, biotech, economics, geopolitics, defense, and more."
        ),
    )
