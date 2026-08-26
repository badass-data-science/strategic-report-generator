"""
Prefect flow for the daily strategic report pipeline.

This flow is configured for a hosted Ollama instance. instructor_mode
defaults to TOOLS, which requires the configured model (via LLM_MODEL) to
support tool/function calling — e.g. glm-5.2:cloud. If you switch to a model
without tool-calling support, override instructor_mode to JSON instead.

WHY PREFECT? The CLI (cli.py) is fine for running the pipeline manually.
Prefect adds scheduling, run history, per-task retries, and per-task
observability in a UI, on top of the same pipeline code.

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
       export LLM_MODEL=ollama_chat/glm-5.2:cloud

4. Start the scheduler (keep this terminal open):

       python -m strategic_reports.daily.flows.daily_report

   The flow registers with the local server and polls for scheduled runs.
   The schedule is 16:00 America/Los_Angeles, Monday through Friday
   (no weekend runs).

5. Trigger a one-off run immediately (in a separate terminal):

       prefect deployment run 'daily-strategic-report/daily-strategic-report'

   Override parameters for a one-off run:

       prefect deployment run 'daily-strategic-report/daily-strategic-report' \\
           --param hours_cutoff=48
"""

import os
from pathlib import Path

import instructor
from dotenv import load_dotenv
from prefect import flow, get_run_logger, task
from prefect.client.schemas.schedules import CronSchedule

# Must run before _DEFAULT_MODEL and the daily_report_flow signature's
# ollama_api_base/ollama_api_key defaults below, since those evaluate at
# import time — a .env file wouldn't be loaded in time otherwise. No-op if
# no .env file exists (e.g. production, where systemd's EnvironmentFile=
# injects the environment directly).
load_dotenv()

from strategic_reports.daily.config.topic_order import list_directories_and_titles
from strategic_reports.daily.core import LLMClient, configure_logging, run_pipeline
from strategic_reports.daily.core.article_archive import record_articles
from strategic_reports.daily.core.bullet_diff import (
    append_bullet_run,
    diff_all_topics,
    load_bullet_history,
)
from strategic_reports.daily.core.db import ensure_database_reachable, record_run
from strategic_reports.daily.core.models import (
    BulletDiff,
    CrossTopicSynthesis,
    TopicConfig,
    TopicResult,
)
from strategic_reports.daily.core.overview_archive import record_overview
from strategic_reports.daily.core.pipeline import summarize_communities, synthesize_cross_topic
from strategic_reports.daily.core.rdf_export import export_rdf
from strategic_reports.daily.core.renderer import render_report
from strategic_reports.daily.core.systems_signals import (
    LaggedCorrelation,
    tag_rate_lagged_correlations,
    topic_urgency_lagged_correlations,
)
from strategic_reports.daily.core.tag_graph import (
    build_display_graph,
    build_graph_data,
    find_bridge_tags,
    write_tag_graph,
)
from strategic_reports.daily.core.tag_tracking import (
    check_emerging_tags,
    load_tag_rate_history,
    record_bridge_tags,
    record_community_summaries,
    record_emerging_tag_alerts,
    record_tags,
)
from strategic_reports.daily.core.tracing import generate_run_id, setup_tracing
from strategic_reports.daily.core.urgency import (
    append_run,
    check_alerts,
    load_history,
)
from strategic_reports.daily.paths import default_data_dir

_DEFAULT_MODEL = os.environ.get("LLM_MODEL", "ollama_chat/glm-5.2:cloud")

_INSTRUCTOR_MODES: dict[str, instructor.Mode] = {
    "TOOLS": instructor.Mode.TOOLS,
    "JSON": instructor.Mode.JSON,
    "MD_JSON": instructor.Mode.MD_JSON,
}


# ---------------------------------------------------------------------------
# Task 1: Load configuration
# ---------------------------------------------------------------------------
# A @task (rather than a plain function call in the flow) so config-load
# failures show up as a distinct node in the Prefect UI.

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
# The expensive step — RSS fetching + dozens of LLM API calls. retries=2
# covers whole-task failures (network/provider outage); individual topic
# failures are already isolated inside run_pipeline itself.
#
# LLMClient is created inside this task (not passed in) because it holds an
# async HTTP session that can't be serialized across Prefect's task result
# storage boundary.

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

    logger.info(
        f"Pipeline starting: {len(topics)} topics, model={model}, "
        f"instructor_mode={instructor_mode_str}"
    )

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
#

@task(name="render-html-report")
def render_html_report(
    results: list[TopicResult],
    output_dir: Path,
    hours_cutoff: int,
    overview: CrossTopicSynthesis | None = None,
    diffs: dict[str, BulletDiff] | None = None,
    topic_signals: list[LaggedCorrelation] | None = None,
    tag_signals: list[LaggedCorrelation] | None = None,
) -> None:
    """Render TopicResults into the HTML report using Jinja2 templates."""
    logger = get_run_logger()
    render_report(
        results,
        output_dir=output_dir,
        hours_cutoff=hours_cutoff,
        overview=overview,
        diffs=diffs,
        topic_signals=topic_signals,
        tag_signals=tag_signals,
    )
    logger.info(f"Report written to {output_dir / 'index.html'}")


# ---------------------------------------------------------------------------
# Task 4: Cross-topic strategic synthesis
# ---------------------------------------------------------------------------
# Fails gracefully: a synthesis failure returns None rather than crashing the
# flow. The renderer omits the overview section when overview is None, so the
# rest of the report is unaffected. Also persists the overview (see
# overview_archive.py) — folded into this task rather than a separate one
# since it's a one-line persist directly tied to this task's own output, not
# an independently-failing concern.

@task(name="run-cross-topic-synthesis", retries=2, retry_delay_seconds=60)
async def run_cross_topic_synthesis(
    results: list[TopicResult],
    model: str,
    temperature: float,
    instructor_mode_str: str,
    run_id: str,
    database_url: str,
    api_base: str | None = None,
    api_key: str | None = None,
) -> CrossTopicSynthesis | None:
    """Synthesize cross-cutting strategic themes across all topic results."""
    logger = get_run_logger()
    mode = _INSTRUCTOR_MODES.get(instructor_mode_str.upper(), instructor.Mode.TOOLS)
    client = LLMClient(
        model=model,
        temperature=temperature,
        run_metadata={"trace_id": run_id, "trace_name": "strategic-report-cross-topic"},
        instructor_mode=mode,
        api_base=api_base,
        api_key=api_key,
    )
    try:
        synthesis = await synthesize_cross_topic(results, client)
        logger.info("Cross-topic synthesis complete")
        try:
            record_overview(database_url, run_id, synthesis.bullets)
        except Exception as exc:
            logger.warning(f"Overview archiving failed: {exc} — continuing without archiving")
        return synthesis
    except Exception as exc:
        logger.warning(f"Cross-topic synthesis failed: {exc} — report will render without overview")
        return None


# ---------------------------------------------------------------------------
# Task 5: Archive article summaries
# ---------------------------------------------------------------------------
# Persists this run's article summaries (title, link, publish_date, summary
# bullets, tags) — the source material every derived signal (tags, bullets,
# urgency scores) is computed from, which otherwise only exists in memory
# during this run.
#
# Fails gracefully: a failure here logs a warning but does not prevent
# rendering.

@task(name="archive-articles")
def archive_articles(results: list[TopicResult], run_id: str, database_url: str) -> None:
    """Persist this run's article summaries into the tracking database."""
    logger = get_run_logger()
    try:
        article_total = sum(len(r.articles) for r in results)
        failed_count = record_articles(database_url, run_id, results)
        if failed_count:
            logger.warning(
                f"Archived {article_total - failed_count}/{article_total} article summaries "
                f"({failed_count} failed — see per-article warnings above)"
            )
        else:
            logger.info(f"Archived {article_total} article summaries")
    except Exception as exc:
        logger.warning(f"Article archiving failed: {exc!r} — continuing without archiving")


# ---------------------------------------------------------------------------
# Task 6: Urgency alert check
# ---------------------------------------------------------------------------
# Runs after the LLM pipeline so all urgency scores are available.
# Order within each run: load history → check alerts → append run → save.
# The current run is never part of its own baseline.
#
# Fails gracefully: a failure here logs a warning but does not prevent
# rendering.

@task(name="check-urgency-alerts")
def check_urgency_alerts(
    results: list[TopicResult],
    run_id: str,
    database_url: str,
    absolute_threshold: float,
    z_score_threshold: float,
) -> None:
    """Check per-topic urgency scores against history; log alerts and update history."""
    logger = get_run_logger()
    try:
        history = load_history(database_url)
        alerts = check_alerts(results, history, absolute_threshold, z_score_threshold)
        append_run(database_url, results, run_id)

        if alerts:
            logger.warning(f"URGENCY ALERTS ({len(alerts)} topic(s)):")
            for alert in alerts:
                logger.warning(f"  *** {alert.summary()}")
        else:
            logger.info("Urgency check: no alerts")

        scored = [(r.config.title, r.strategy.urgency_score) for r in results if r.strategy]
        scored.sort(key=lambda x: -x[1])
        logger.info("Urgency scores: " + ", ".join(f"{t}={s:.2f}" for t, s in scored))
    except Exception as exc:
        logger.warning(f"Urgency check failed: {exc} — continuing without alert check")


# ---------------------------------------------------------------------------
# Task 7: Emerging-tag check
# ---------------------------------------------------------------------------
# Compares today's tag rates (tag count / article_count) against each tag's
# own historical baseline. Order: load rate history → check → persist
# today's tag graph (current run never biases its own baseline).
#
# Fails gracefully: a failure here logs a warning but does not prevent
# rendering or the tag graph HTML/JSON files.

@task(name="check-emerging-tags")
def check_emerging_tag_alerts(
    results: list[TopicResult],
    run_id: str,
    database_url: str,
    article_count: int,
    tag_z_score_threshold: float,
) -> None:
    """Check today's tag rates against history; log alerts and persist today's tag graph."""
    logger = get_run_logger()
    try:
        graph_data = build_graph_data(results)
        history = load_tag_rate_history(database_url)
        alerts = check_emerging_tags(graph_data, article_count, history, tag_z_score_threshold)
        failed_count = record_tags(database_url, run_id, graph_data)
        if failed_count:
            logger.warning(f"Tag graph: {failed_count} row(s) failed to insert")
        record_emerging_tag_alerts(database_url, run_id, alerts)
        record_bridge_tags(database_url, run_id, find_bridge_tags(graph_data))

        if alerts:
            logger.warning(f"EMERGING TAG ALERTS ({len(alerts)} tag(s)):")
            for alert in alerts:
                logger.warning(f"  *** {alert.summary()}")
        else:
            logger.info("Emerging-tag check: no alerts")
    except Exception as exc:
        logger.warning(f"Emerging-tag check failed: {exc} — continuing without tag tracking")


# ---------------------------------------------------------------------------
# Task 8: Summarize tag communities
# ---------------------------------------------------------------------------
# One LLM call per Louvain tag-community, grounded in that community's
# articles — replaces "labeled by top tag" with an actual paragraph.
# Recomputes graph_data/display_data (cheap, pure) rather than sharing
# check_emerging_tag_alerts' — Prefect tasks don't share local variables.
#
# Fails gracefully: a failure here logs a warning but does not prevent
# rendering or the tag graph HTML/JSON files.

@task(name="summarize-communities", retries=2, retry_delay_seconds=60)
async def summarize_community_tags(
    results: list[TopicResult],
    run_id: str,
    database_url: str,
    model: str,
    temperature: float,
    instructor_mode_str: str,
    api_base: str | None = None,
    api_key: str | None = None,
) -> None:
    """Generate and persist an LLM-written summary for each Louvain tag-community."""
    logger = get_run_logger()
    try:
        display_data = build_display_graph(build_graph_data(results))
        mode = _INSTRUCTOR_MODES.get(instructor_mode_str.upper(), instructor.Mode.TOOLS)
        client = LLMClient(
            model=model,
            temperature=temperature,
            run_metadata={"trace_id": run_id, "trace_name": "strategic-report-community-summary"},
            instructor_mode=mode,
            api_base=api_base,
            api_key=api_key,
        )
        community_summaries = await summarize_communities(results, display_data, client)
        failed_count = record_community_summaries(database_url, run_id, community_summaries)
        if failed_count:
            logger.warning(f"Community summaries: {failed_count} row(s) failed to insert")
        logger.info(
            f"Community summaries: {len(community_summaries)} of "
            f"{display_data['n_communities']} communities"
        )
    except Exception as exc:
        logger.warning(
            f"Community summarization failed: {exc} — continuing without community summaries"
        )


# ---------------------------------------------------------------------------
# Task 9: Historical bullet diffing
# ---------------------------------------------------------------------------
# Compares today's strategic bullets against yesterday's using an LLM to
# classify changes as new / continued / dropped.
# Order: load history → diff → append (current run never biases itself).
# Fails gracefully to empty dict; the renderer omits diff sections when
# diffs={}, so the rest of the report is unaffected.

@task(name="run-bullet-diff", retries=2, retry_delay_seconds=60)
async def run_bullet_diff(
    results: list[TopicResult],
    database_url: str,
    model: str,
    temperature: float,
    instructor_mode_str: str,
    run_id: str,
    api_base: str | None = None,
    api_key: str | None = None,
) -> dict[str, BulletDiff]:
    """Diff today's strategic bullets against yesterday's and return per-topic results."""
    logger = get_run_logger()
    try:
        yesterday = load_bullet_history(database_url)
        if not yesterday:
            logger.info("No bullet history yet — skipping diff on first run")
            append_bullet_run(database_url, results, run_id)
            return {}

        mode = _INSTRUCTOR_MODES.get(instructor_mode_str.upper(), instructor.Mode.TOOLS)
        client = LLMClient(
            model=model,
            temperature=temperature,
            run_metadata={"trace_id": run_id, "trace_name": "strategic-report-bullet-diff"},
            instructor_mode=mode,
            api_base=api_base,
            api_key=api_key,
        )
        diffs = await diff_all_topics(results, yesterday, client)
        append_bullet_run(database_url, results, run_id)

        new_count = sum(len(d.new) for d in diffs.values())
        dropped_count = sum(len(d.dropped) for d in diffs.values())
        logger.info(
            f"Bullet diff complete: {new_count} new, {dropped_count} dropped "
            f"across {len(diffs)} topics"
        )
        return diffs
    except Exception as exc:
        logger.warning(f"Bullet diff failed: {exc} — report will render without diffs")
        return {}


# ---------------------------------------------------------------------------
# Task 12: Compute systems signals
# ---------------------------------------------------------------------------
# Candidate feedback-loop correlations across topic urgency and tag
# coverage rate history (see core/systems_signals.py). Reads this run's
# own urgency_scores/tag_counts/tag_edges/tag_topics/community_summary_tags/
# articles rows, all written by earlier tasks -- must run after all of
# them and before render-html-report, hence the definition placement
# here even though it isn't numbered 3 (render-html-report, defined
# earlier, is likewise called last in the flow body below -- task numbers
# here track definition order, not call order).
# Fails gracefully: a failure here logs a warning but does not block
# rendering, same pattern as every other optional stage in this file.

@task(name="compute-systems-signals")
def compute_systems_signals(
    database_url: str,
) -> tuple[list[LaggedCorrelation], list[LaggedCorrelation]]:
    """Candidate feedback-loop correlations across topic urgency and tag coverage rate history."""
    logger = get_run_logger()
    try:
        topic_signals = topic_urgency_lagged_correlations(database_url)
        tag_signals = tag_rate_lagged_correlations(database_url)
        logger.info(
            f"Systems signals: {len(topic_signals)} topic-level, {len(tag_signals)} tag-level"
        )
        return topic_signals, tag_signals
    except Exception as exc:
        logger.warning(f"Systems signals failed: {exc!r} — report will render without them")
        return [], []


# ---------------------------------------------------------------------------
# Task 10: Build tag co-occurrence network graph
# ---------------------------------------------------------------------------

@task(name="build-tag-graph")
def build_tag_graph(results: list[TopicResult], output_dir: Path) -> None:
    """Build tag co-occurrence graph and write tag_graph.json + tag_graph.html."""
    logger = get_run_logger()
    write_tag_graph(results, output_dir)
    logger.info(f"Tag graph written to {output_dir / 'tag_graph.html'}")


# ---------------------------------------------------------------------------
# Task 11: Export RDF knowledge graph
# ---------------------------------------------------------------------------
# Formerly its own Prefect flow/deployment (export_rdf_flow.py), triggered
# separately after this one finished. Folded in as this flow's last task
# instead, so there's a single Prefect flow/deployment to operate — see
# AGENTS.md. Full rebuild every run (no `--since` watermark tracking),
# same v1 scope as the `export-rdf` CLI command. Written next to output_dir
# (output_dir.parent), not inside it, since output_dir is deleted and
# recreated on every run — output_dir.parent is the same stable directory
# the old SQLite db_path used to live in (see .serve(parameters={...})
# below), now that there's no local db file whose parent to piggyback on.
#
# Fails gracefully: a failure here logs a warning but does not prevent the
# report from rendering — it already has by the time this task runs.

@task(name="export-rdf")
def export_rdf_task(database_url: str, output_dir: Path) -> None:
    """Export the tracking database's accumulated archive to Turtle."""
    logger = get_run_logger()
    output = output_dir.parent / "knowledge_graph.ttl"
    try:
        triple_count = export_rdf(database_url, output, since=None)
        logger.info(f"RDF export written to {output} ({triple_count:,} triples)")
    except Exception as exc:
        logger.warning(f"RDF export failed: {exc} — continuing without exporting")


# ---------------------------------------------------------------------------
# Flow — the top-level unit that Prefect schedules and tracks
# ---------------------------------------------------------------------------
# All parameters have defaults so the flow runs without any arguments on
# schedule, and are exposed in the Prefect UI as overridable fields for
# one-off runs.

@flow(
    name="daily-strategic-report",
    description=(
        "Fetches recent news across 11 topic feeds (AI, biotech, geopolitics, …) "
        "and synthesizes per-topic strategic bullet points into a linked HTML report."
    ),
    log_prints=True,
)
async def daily_report_flow(
    output_dir: Path,
    database_url: str,
    model: str = _DEFAULT_MODEL,
    hours_cutoff: int = 24,
    data_dir: Path = default_data_dir(),
    batch_size: int = 50,
    max_concurrent: int = 3,
    temperature: float = 0.1,
    # TOOLS mode requires the configured model to support tool/function
    # calling (glm-5.2:cloud does; gpt-oss:120b did not — see LLM_MODEL).
    instructor_mode: str = "TOOLS",
    ollama_api_base: str | None = os.environ.get("OLLAMA_API_BASE"),
    ollama_api_key: str | None = os.environ.get("OLLAMA_API_KEY"),
    log_level: str = "INFO",
    absolute_threshold: float = 0.8,
    z_score_threshold: float = 2.0,
    tag_z_score_threshold: float = 2.0,
) -> None:
    """Daily strategic report: ingest RSS feeds, summarize, synthesize, render HTML."""
    # Fail fast if database_url is unreachable/misconfigured, before doing
    # any real work (expensive LLM calls). Schema is applied separately,
    # once, via `alembic upgrade head` (or `strategic-reports db upgrade`)
    # — not implicitly here, unlike the old SQLite connect()-creates-schema
    # idiom this replaced.
    ensure_database_reachable(database_url)

    # Validate before doing any real work — otherwise an invalid instructor_mode
    # silently falls back to TOOLS inside each task's own _INSTRUCTOR_MODES.get(),
    # rather than failing loudly the way cli.py run does.
    if instructor_mode.upper() not in _INSTRUCTOR_MODES:
        raise ValueError(
            f"Invalid instructor_mode '{instructor_mode}'. "
            f"Valid options: {', '.join(_INSTRUCTOR_MODES)}"
        )

    configure_logging(log_level)
    setup_tracing()
    run_id = generate_run_id()

    topics = build_topic_configs(data_dir)

    if not topics:
        raise ValueError(
            f"No valid topic configs found in {data_dir}. Check the data_dir parameter."
        )

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

    # Register this run in the tracking database, with the total number of
    # articles considered — the denominator any future cross-run tag-weight
    # comparison divides by. Must happen before the urgency/bullet tasks
    # below, since those insert rows referencing this run_id.
    article_count = sum(len(r.articles) for r in results)
    record_run(database_url, run_id, article_count)

    archive_articles(results=results, run_id=run_id, database_url=database_url)

    overview = await run_cross_topic_synthesis(
        results=results,
        model=model,
        temperature=temperature,
        instructor_mode_str=instructor_mode,
        run_id=run_id,
        database_url=database_url,
        api_base=ollama_api_base,
        api_key=ollama_api_key,
    )

    check_urgency_alerts(
        results=results,
        run_id=run_id,
        database_url=database_url,
        absolute_threshold=absolute_threshold,
        z_score_threshold=z_score_threshold,
    )

    check_emerging_tag_alerts(
        results=results,
        run_id=run_id,
        database_url=database_url,
        article_count=article_count,
        tag_z_score_threshold=tag_z_score_threshold,
    )

    await summarize_community_tags(
        results=results,
        run_id=run_id,
        database_url=database_url,
        model=model,
        temperature=temperature,
        instructor_mode_str=instructor_mode,
        api_base=ollama_api_base,
        api_key=ollama_api_key,
    )

    diffs = await run_bullet_diff(
        results=results,
        database_url=database_url,
        model=model,
        temperature=temperature,
        instructor_mode_str=instructor_mode,
        run_id=run_id,
        api_base=ollama_api_base,
        api_key=ollama_api_key,
    )

    topic_signals, tag_signals = compute_systems_signals(database_url)

    render_html_report(
        results, output_dir, hours_cutoff, overview, diffs, topic_signals, tag_signals
    )
    build_tag_graph(results, output_dir)
    export_rdf_task(database_url, output_dir)


# ---------------------------------------------------------------------------
# Entry point — registers the deployment and starts the local scheduler
# ---------------------------------------------------------------------------
# flow.serve() is Prefect's "lightweight deployment" pattern: the process
# running this file IS the worker — no Docker/Kubernetes/separate worker
# process required.
#
# To keep this running persistently on a Linux server, see README for the
# systemd unit file (strategic-reports.service).

if __name__ == "__main__":
    daily_report_flow.serve(
        name="daily-strategic-report",
        schedules=[CronSchedule(cron="0 16 * * 1-5", timezone="America/Los_Angeles")],
        tags=["strategic-reports", "daily"],
        description=(
            "Daily strategic intelligence report. "
            "Runs at 16:00 Pacific time, Monday through Friday (no weekend runs). "
            "Synthesizes recent news across AI, biotech, economics, geopolitics, defense, and more."
        ),
        # output_dir has no function default (see daily_report_flow) — the
        # scheduled cron run has no CLI invocation to supply it, so its value
        # is fixed here, once, at deployment registration time. Path.home()
        # (not cwd) so this stays correct regardless of the working
        # directory the process is started from.
        #
        # database_url also has no function default, but unlike output_dir
        # it's read from DATABASE_URL here (via load_dotenv() + os.environ
        # at the top of this module) rather than hardcoded — it's a secret
        # (embeds the database password), so it belongs in the systemd
        # unit's EnvironmentFile=.env, not committed in this parameters
        # dict. Fails loudly at registration time if unset, rather than
        # registering a deployment that will fail on its first scheduled run.
        parameters={
            "output_dir": Path.home()
            / "output"
            / "daily-strategic-report-from-RSS-feeds"
            / "daily-report",
            "database_url": os.environ["DATABASE_URL"],
        },
    )
