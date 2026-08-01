"""
Prefect flow for the export-rdf CLI command — scheduled daily.

Wraps `cli.py export-rdf`'s logic as a Prefect @flow/@task for retries,
run history, and UI visibility. Runs as its own process (its own
`.serve()` call, its own systemd unit) rather than being folded into
daily_report.py's process — export-rdf is deliberately independent of the
daily pipeline (see AGENTS.md), so a crash/restart of one should never
touch the other. Full rebuild every run (no `--since` watermark tracking)
— same v1 scope choice as the CLI command.

RUNNING (mirrors daily_report.py — see its docstring for local Prefect
server setup):

    python -m strategic_reports.daily.flows.export_rdf_flow

Registers the deployment and polls for scheduled runs (04:00
America/Los_Angeles daily, well after daily_report_flow's 00:30 run, so
the day's data has finished writing to the tracking database first).
Trigger a one-off run, optionally with a different `--since`:

    prefect deployment run 'export-rdf/export-rdf'
    prefect deployment run 'export-rdf/export-rdf' --param since=2026-08-01

See AGENTS.md and README.md's "Exporting an RDF knowledge graph" section
for what export-rdf does and why it's kept separate from daily_report_flow.
"""

from pathlib import Path

from prefect import flow, task, get_run_logger
from prefect.client.schemas.schedules import CronSchedule

from strategic_reports.daily.core.db import connect as connect_db
from strategic_reports.daily.core.rdf_export import export_rdf


@task(name="export-rdf")
def export_rdf_task(db_path: Path, output: Path, since: str | None = None) -> int:
    """Export the tracking database to Turtle; returns the triple count."""
    logger = get_run_logger()
    connect_db(db_path).close()
    triple_count = export_rdf(db_path, output, since=since)
    logger.info(f"RDF export written to {output} ({triple_count:,} triples)")
    return triple_count


@flow(
    name="export-rdf",
    description=(
        "Exports the tracking database's accumulated archive (articles, tags, "
        "community summaries, bridge tags, strategic bullets, urgency scores, "
        "cross-topic overviews) as an RDF (Turtle) knowledge graph."
    ),
)
def export_rdf_flow(db_path: Path, output: Path, since: str | None = None) -> int:
    return export_rdf_task(db_path=db_path, output=output, since=since)


if __name__ == "__main__":
    export_rdf_flow.serve(
        name="export-rdf",
        schedules=[CronSchedule(cron="0 4 * * *", timezone="America/Los_Angeles")],
        tags=["strategic-reports", "rdf-export"],
        description=(
            "Daily RDF (Turtle) export of the tracking database's accumulated "
            "archive — a full rebuild every run."
        ),
        # db_path/output have no function default — same reason as
        # daily_report_flow's output_dir/db_path: a cron-triggered run has no
        # CLI invocation to supply them, so they're fixed here once. Read
        # from the same tracking database daily_report_flow writes to.
        parameters={
            "db_path": Path.home() / "output" / "daily-strategic-report-from-RSS-feeds" / "strategic-reports.db",
            "output": Path.home() / "output" / "daily-strategic-report-from-RSS-feeds" / "knowledge_graph.ttl",
        },
    )
