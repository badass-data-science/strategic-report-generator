"""
Daily strategic report pipeline — CLI entrypoint.

Two commands (naming one explicitly is required now that there are two —
typer's single-command auto-invoke shorthand only applies to a one-command app):

    python -m strategic_reports.daily.cli run --output-dir /path/to/output --db-path /path/to/db
    python -m strategic_reports.daily.cli ask "What's happening with export controls?" --db-path /path/to/db

`run` key options:
    --output-dir        Where to write HTML output (required)
    --model             litellm model string  (default: env LLM_MODEL or ollama_chat/glm-5.2:cloud)
    --hours-cutoff      Article age window in hours (default: 24)
    --data-dir          Where to find rss_feeds/*.json files
    --db-path           SQLite tracking database (required; persists across
                        runs; must not be inside --output-dir)
    --batch-size        Articles per LLM summarization call
    --max-concurrent    Max concurrent LLM calls (semaphore width)
    --log-level         Logging verbosity

`ask` key options:
    question (positional)  Free-text question about the accumulated archive
    --db-path              SQLite tracking database to query (required)
    --max-communities       Max matching tag-communities used as context (default: 8)
    Graph-guided retrieval over community_summaries written by `run` — see
    archive_query.py and pipeline.answer_archive_question. Read-only;
    doesn't touch --output-dir.

SEPARATION OF CONCERNS
-----------------------
This layer only reads config, prints status, and bridges sync (CLI) to
async (pipeline) — business logic lives in pipeline.py, rendering in
renderer.py, LLM calls in llm_client.py.
"""

import asyncio
import os
from pathlib import Path

import instructor
import typer

from strategic_reports.daily.config.topic_order import list_directories_and_titles
from strategic_reports.daily.core import (
    answer_archive_question,
    append_bullet_run,
    append_run,
    build_display_graph,
    build_graph_data,
    check_alerts,
    check_emerging_tags,
    configure_logging,
    diff_all_topics,
    find_bridge_tags,
    load_bullet_history,
    load_history,
    load_tag_rate_history,
    LLMClient,
    record_articles,
    record_bridge_tags,
    record_community_summaries,
    record_emerging_tag_alerts,
    record_overview,
    record_tags,
    run_pipeline,
    summarize_communities,
    write_tag_graph,
)
from strategic_reports.daily.core.db import (
    connect as connect_db,
    ensure_safe_db_path,
    record_run,
)
from strategic_reports.daily.core.models import TopicConfig
from strategic_reports.daily.core.pipeline import synthesize_cross_topic
from strategic_reports.daily.core.rdf_export import export_rdf
from strategic_reports.daily.core.renderer import render_report
from strategic_reports.daily.core.tracing import generate_run_id, setup_tracing
from strategic_reports.daily.paths import default_data_dir

# Maps CLI string → instructor.Mode enum value.
# TOOLS requires model-level function-calling support (OpenAI, Anthropic, capable Ollama).
# JSON works with most Ollama models that lack tool-call support.
_INSTRUCTOR_MODES: dict[str, instructor.Mode] = {
    "TOOLS": instructor.Mode.TOOLS,
    "JSON": instructor.Mode.JSON,
    "MD_JSON": instructor.Mode.MD_JSON,
}

app = typer.Typer(add_completion=False)

_DEFAULT_MODEL = os.environ.get("LLM_MODEL", "ollama_chat/glm-5.2:cloud")


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
        ...,
        help="Directory to write HTML report files (required). "
             "WARNING: wiped and recreated on every run.",
    ),
    data_dir: Path = typer.Option(
        default_data_dir(),
        envvar="STRATEGIC_REPORTS_DATA_DIR",
        help="Directory containing rss_feeds/*.json files",
    ),
    db_path: Path = typer.Option(
        ...,
        help="SQLite tracking database for cross-run history (urgency scores, "
             "bullet history) (required). Persists across runs — never wiped, "
             "unlike --output-dir. Must not be located inside --output-dir.",
    ),
    batch_size: int = typer.Option(50, help="Max articles per LLM summarization call"),
    max_concurrent: int = typer.Option(3, help="Max topics hitting the LLM API simultaneously"),
    temperature: float = typer.Option(0.1, help="LLM sampling temperature"),
    instructor_mode: str = typer.Option(
        "TOOLS",
        help="Structured output mode: TOOLS (default, requires tool-calling support), "
             "JSON (for Ollama models without tool calling), MD_JSON (JSON in markdown fences)",
    ),
    ollama_api_base: str | None = typer.Option(
        None,
        envvar="OLLAMA_API_BASE",
        help="Ollama server base URL (e.g. http://my-server:11434). "
             "Also read from OLLAMA_API_BASE env var.",
    ),
    ollama_api_key: str | None = typer.Option(
        None,
        envvar="OLLAMA_API_KEY",
        help="API key for authenticated Ollama instances. "
             "Also read from OLLAMA_API_KEY env var.",
    ),
    absolute_threshold: float = typer.Option(
        0.8,
        help="Urgency score (0-1) above which an alert fires unconditionally",
    ),
    z_score_threshold: float = typer.Option(
        2.0,
        help="Standard deviations above a topic's historical mean urgency score "
             "that trigger a statistical alert (requires >=7 prior runs for that topic)",
    ),
    tag_z_score_threshold: float = typer.Option(
        2.0,
        help="Standard deviations above a tag's historical mean rate (tag count / "
             "articles considered) that trigger an emerging-tag alert "
             "(requires >=7 prior runs for that tag)",
    ),
    log_level: str = typer.Option("INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)"),
) -> None:
    """Run the daily strategic report pipeline and write results to output_dir."""

    # Validate --db-path before doing any real work: output_dir is deleted and
    # recreated by render_report() on every run, so the tracking database can
    # never live inside it.
    try:
        ensure_safe_db_path(db_path, output_dir)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)

    # connect_db() creates the database file (and parent dirs, and the schema)
    # on first use, and never touches existing data — this is what makes
    # cross-run history possible. Just a fail-fast check here (closed right
    # away); the urgency/bullet functions below each open their own short
    # connection per call rather than sharing this one.
    connect_db(db_path).close()

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
        typer.echo("No valid topic configs found. Check --data-dir.", err=True)
        raise typer.Exit(code=1)

    # Print a startup summary to stdout so the user knows what's running.
    typer.echo(f"Model:            {model}")
    typer.echo(f"Instructor mode:  {mode_upper}")
    typer.echo(f"Topics:           {len(topics)}")
    typer.echo(f"Hours cutoff:     {hours_cutoff}h")
    typer.echo(f"Output:           {output_dir}")
    typer.echo(f"Tracking DB:      {db_path}")
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
        api_base=ollama_api_base,
        api_key=ollama_api_key,
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

    # Register this run in the tracking database, with the total number of
    # articles considered — the denominator any future cross-run tag-weight
    # comparison divides by. Must happen before append_run()/append_bullet_run()
    # below, since those insert rows referencing this run_id.
    article_count = sum(len(r.articles) for r in results)
    record_run(db_path, run_id, article_count)

    # Archive today's article summaries — the source material every derived
    # signal (tags, bullets, urgency scores) is computed from, which
    # otherwise only exists in memory during this run. Never blocks
    # rendering on failure.
    try:
        record_articles(db_path, run_id, results)
    except Exception as exc:
        typer.echo(f"[warn] Article archiving failed: {exc} — continuing without archiving", err=True)

    # Emerging-tag check: compare today's tag rates (tag count / articles
    # considered) against each tag's own historical baseline, then persist
    # today's tag graph linked to run_id — for future runs' baselines, and
    # so tag_graph.json could be reconstructed from --db-path for this run.
    # Also persists the bridge tags that will be surfaced to cross-topic
    # synthesis below, as an audit trail. Never blocks rendering on failure.
    try:
        graph_data = build_graph_data(results)
        tag_rate_history = load_tag_rate_history(db_path)
        tag_alerts = check_emerging_tags(graph_data, article_count, tag_rate_history, tag_z_score_threshold)
        record_tags(db_path, run_id, graph_data)
        record_emerging_tag_alerts(db_path, run_id, tag_alerts)
        record_bridge_tags(db_path, run_id, find_bridge_tags(graph_data))
        if tag_alerts:
            typer.echo(f"EMERGING TAG ALERTS ({len(tag_alerts)} tag(s)):")
            for alert in tag_alerts:
                typer.echo(f"  *** {alert.summary()}")
        else:
            typer.echo("Emerging-tag check: no alerts")
    except Exception as exc:
        typer.echo(f"[warn] Emerging-tag check failed: {exc} — continuing without tag tracking", err=True)

    # Community summaries: an LLM-written paragraph per Louvain tag-community
    # (grounded in that community's articles), replacing "labeled by top
    # tag" with real substance. Reuses graph_data computed above. A
    # separate LLMClient (distinct trace_name), same as cross-topic
    # synthesis below. Never blocks rendering on failure.
    community_client = LLMClient(
        model=model,
        temperature=temperature,
        run_metadata={"trace_id": run_id, "trace_name": "strategic-report-community-summary"},
        instructor_mode=resolved_mode,
        api_base=ollama_api_base,
        api_key=ollama_api_key,
    )
    try:
        display_data = build_display_graph(graph_data)
        community_summaries = asyncio.run(summarize_communities(results, display_data, community_client))
        record_community_summaries(db_path, run_id, community_summaries)
        typer.echo(
            f"Community summaries: {len(community_summaries)} of {display_data['n_communities']} communities"
        )
    except Exception as exc:
        typer.echo(
            f"[warn] Community summarization failed: {exc} — continuing without community summaries", err=True
        )

    # Cross-topic synthesis: a separate LLMClient (distinct trace_name) so it's
    # distinguishable from the per-topic summarization calls in tracing. Fails
    # gracefully to None — render_report omits the Strategic Overview section
    # when overview is None, so a synthesis failure doesn't affect the rest of
    # the report.
    synthesis_client = LLMClient(
        model=model,
        temperature=temperature,
        run_metadata={"trace_id": run_id, "trace_name": "strategic-report-cross-topic"},
        instructor_mode=resolved_mode,
        api_base=ollama_api_base,
        api_key=ollama_api_key,
    )
    try:
        overview = asyncio.run(synthesize_cross_topic(results, synthesis_client))
        try:
            record_overview(db_path, run_id, overview.bullets)
        except Exception as exc:
            typer.echo(f"[warn] Overview archiving failed: {exc} — continuing without archiving", err=True)
    except Exception as exc:
        typer.echo(f"[warn] Cross-topic synthesis failed: {exc} — report will render without overview", err=True)
        overview = None

    # Urgency check: compare today's scores against history, log any alerts,
    # then append today's scores for future runs' baseline. Order matters —
    # the current run is checked before it's appended, so it never inflates
    # its own baseline. Never blocks rendering on failure.
    try:
        urgency_history = load_history(db_path)
        alerts = check_alerts(results, urgency_history, absolute_threshold, z_score_threshold)
        append_run(db_path, results, run_id)
        if alerts:
            typer.echo(f"URGENCY ALERTS ({len(alerts)} topic(s)):")
            for urgency_alert in alerts:
                typer.echo(f"  *** {urgency_alert.summary()}")
        else:
            typer.echo("Urgency check: no alerts")
    except Exception as exc:
        typer.echo(f"[warn] Urgency check failed: {exc} — continuing without alert check", err=True)

    # Bullet diff: compare today's strategic bullets against yesterday's via an
    # LLM classification call, then append today's bullets for tomorrow's diff.
    # Skipped (no diff) on the very first run. Never blocks rendering on failure.
    diffs: dict = {}
    try:
        yesterday = load_bullet_history(db_path)
        if not yesterday:
            typer.echo("No bullet history yet — skipping diff on first run")
            append_bullet_run(db_path, results, run_id)
        else:
            diff_client = LLMClient(
                model=model,
                temperature=temperature,
                run_metadata={"trace_id": run_id, "trace_name": "strategic-report-bullet-diff"},
                instructor_mode=resolved_mode,
                api_base=ollama_api_base,
                api_key=ollama_api_key,
            )
            diffs = asyncio.run(diff_all_topics(results, yesterday, diff_client))
            append_bullet_run(db_path, results, run_id)
    except Exception as exc:
        typer.echo(f"[warn] Bullet diff failed: {exc} — report will render without diffs", err=True)
        diffs = {}

    # render_report() is synchronous (Jinja2 rendering is fast, no I/O bottleneck).
    render_report(results, output_dir=output_dir, hours_cutoff=hours_cutoff, overview=overview, diffs=diffs)

    # Tag co-occurrence graph — written into the same output_dir as the HTML
    # report, matching the Prefect flow's build-tag-graph step.
    write_tag_graph(results, output_dir)

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
    typer.echo(f"  Total tokens used:     {client.total_usage.total_tokens:,}")
    typer.echo(f"  Report written to:     {output_dir / 'index.html'}")
    typer.echo(f"  Tag graph written to:  {output_dir / 'tag_graph.html'}")


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question to ask about the accumulated archive"),
    db_path: Path = typer.Option(
        ...,
        help="SQLite tracking database to query (required) — the same one "
             "--db-path pointed at during `run` invocations.",
    ),
    model: str = typer.Option(
        _DEFAULT_MODEL,
        envvar="LLM_MODEL",
        help="litellm model string (e.g. 'ollama_chat/llama3.1:70b', 'anthropic/claude-sonnet-4-6')",
    ),
    temperature: float = typer.Option(0.1, help="LLM sampling temperature"),
    instructor_mode: str = typer.Option(
        "TOOLS",
        help="Structured output mode: TOOLS (default, requires tool-calling support), "
             "JSON (for Ollama models without tool calling), MD_JSON (JSON in markdown fences)",
    ),
    ollama_api_base: str | None = typer.Option(
        None,
        envvar="OLLAMA_API_BASE",
        help="Ollama server base URL (e.g. http://my-server:11434). "
             "Also read from OLLAMA_API_BASE env var.",
    ),
    ollama_api_key: str | None = typer.Option(
        None,
        envvar="OLLAMA_API_KEY",
        help="API key for authenticated Ollama instances. "
             "Also read from OLLAMA_API_KEY env var.",
    ),
    max_communities: int = typer.Option(
        8,
        help="Max matching archived tag-communities to include as retrieved context",
    ),
    log_level: str = typer.Option("INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)"),
) -> None:
    """
    Ask a free-text question about the accumulated strategic-reports archive.

    Graph-guided retrieval, not full GraphRAG: extracts candidate tags from
    the question, matches them against archived tag-community summaries
    (see tag_tracking.record_community_summaries — written by `run` each
    day), then answers grounded only in what's retrieved. Doesn't touch
    --output-dir; read-only against --db-path.
    """
    mode_upper = instructor_mode.upper()
    if mode_upper not in _INSTRUCTOR_MODES:
        typer.echo(
            f"Invalid --instructor-mode '{instructor_mode}'. "
            f"Valid options: {', '.join(_INSTRUCTOR_MODES)}",
            err=True,
        )
        raise typer.Exit(code=1)
    resolved_mode = _INSTRUCTOR_MODES[mode_upper]

    configure_logging(log_level)

    # connect_db() creates the database (and schema) if it doesn't exist yet
    # — harmless; a fresh archive just has nothing to retrieve from.
    connect_db(db_path).close()

    client = LLMClient(
        model=model,
        temperature=temperature,
        run_metadata={"trace_id": generate_run_id(), "trace_name": "strategic-report-archive-query"},
        instructor_mode=resolved_mode,
        api_base=ollama_api_base,
        api_key=ollama_api_key,
    )

    try:
        result = asyncio.run(answer_archive_question(question, db_path, client, max_communities))
    except Exception as exc:
        typer.echo(f"Archive query failed: {exc}", err=True)
        raise typer.Exit(code=1)

    typer.echo("")
    typer.echo(result["answer"])
    if result["communities"]:
        typer.echo("")
        typer.echo(f"Grounded in {len(result['communities'])} archived cluster(s):")
        for c in result["communities"]:
            date = c["created_at"].split("T")[0]
            typer.echo(f"  - {date} — {c['label']} ({c['article_count']} articles)")


@app.command(name="export-rdf")
def export_rdf_command(
    db_path: Path = typer.Option(
        ...,
        help="SQLite tracking database to export (required) — the same one "
             "--db-path pointed at during `run` invocations.",
    ),
    output: Path = typer.Option(
        ...,
        help="Turtle (.ttl) file to write the RDF export to.",
    ),
    since: str | None = typer.Option(
        None,
        help="Only include runs at or after this point — a run_id or an "
             "ISO 8601 timestamp. Omit for a full rebuild from every run "
             "in the database.",
    ),
) -> None:
    """
    Export the accumulated archive as an RDF (Turtle) knowledge graph.

    Complements tag_graph.py's per-run co-occurrence JSON/HTML output —
    doesn't replace or recompute it. Reads durable, cross-run data from
    --db-path (articles, tags, community summaries, bridge tags, per-topic
    strategic bullets, urgency scores, cross-topic overviews) and gives it
    a standard RDF shape (SKOS for tags/communities, PROV-O for run
    lineage, schema.org for article metadata) intended for integration
    into a broader, multi-source knowledge base. Read-only against
    --db-path; never touches --output-dir.
    """
    connect_db(db_path).close()

    triple_count = export_rdf(db_path, output, since=since)

    typer.echo(f"RDF export written to {output} ({triple_count:,} triples)")


if __name__ == "__main__":
    app()
