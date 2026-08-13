"""
Prefect flow for the export-rdf CLI command — triggered by daily_report's
completion.

Wraps `cli.py export-rdf`'s logic as a Prefect @flow/@task for retries,
run history, and UI visibility. Runs as its own process (its own
`.serve()` call, its own systemd unit) rather than being folded into
daily_report.py's process — export-rdf is deliberately independent of the
daily pipeline (see AGENTS.md), so a crash/restart of one should never
touch the other. Full rebuild every run (no `--since` watermark tracking)
— same v1 scope choice as the CLI command.

RUNNING (mirrors daily_report.py — see its docstring for local Prefect
server setup). daily_report_flow's deployment must already be registered
(i.e. `python -m strategic_reports.daily.flows.daily_report` has been run
at least once) before starting this one, since startup resolves that
deployment's id to build the trigger:

    python -m strategic_reports.daily.flows.export_rdf_flow

Registers the deployment with a Prefect Automation, not a cron schedule:
it fires as soon as a `daily-strategic-report` flow run completes, rather
than on its own fixed time. The two processes stay independent — this is
only a trigger, not a subflow call — a crash/restart of one still never
touches the other. Trigger a one-off run, optionally with a different
`--since`:

    prefect deployment run \
        'daily-strategic-report-export-rdf/daily-strategic-report-export-rdf'
    prefect deployment run \
        'daily-strategic-report-export-rdf/daily-strategic-report-export-rdf' \
        --param since=2026-08-01

See AGENTS.md and README.md's "Exporting an RDF knowledge graph" section
for what export-rdf does and why it's kept separate from daily_report_flow.
"""

from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.client.orchestration import get_client
from prefect.events.schemas.deployment_triggers import DeploymentEventTrigger

from strategic_reports.daily.core.db import connect as connect_db
from strategic_reports.daily.core.rdf_export import export_rdf

_UPSTREAM_DEPLOYMENT = "daily-strategic-report/daily-strategic-report"


@task(name="export-rdf")
def export_rdf_task(db_path: Path, output: Path, since: str | None = None) -> int:
    """Export the tracking database to Turtle; returns the triple count."""
    logger = get_run_logger()
    connect_db(db_path).close()
    triple_count = export_rdf(db_path, output, since=since)
    logger.info(f"RDF export written to {output} ({triple_count:,} triples)")
    return triple_count


@flow(
    name="daily-strategic-report-export-rdf",
    description=(
        "Exports the tracking database's accumulated archive (articles, tags, "
        "community summaries, bridge tags, strategic bullets, urgency scores, "
        "cross-topic overviews) as an RDF (Turtle) knowledge graph."
    ),
)
def export_rdf_flow(db_path: Path, output: Path, since: str | None = None) -> int:
    return export_rdf_task(db_path=db_path, output=output, since=since)


if __name__ == "__main__":
    # Resolve daily_report_flow's deployment id so the trigger below can
    # scope itself to "that specific deployment completed", not just any
    # flow run named daily-strategic-report. Requires that deployment to
    # already be registered; systemd's Restart=on-failure (README) retries
    # this every 30s until it is, rather than needing a custom wait loop
    # here.
    with get_client(sync_client=True) as client:
        upstream = client.read_deployment_by_name(_UPSTREAM_DEPLOYMENT)

    export_rdf_flow.serve(
        name="daily-strategic-report-export-rdf",
        triggers=[
            DeploymentEventTrigger(
                name="export-rdf-after-daily-report",
                expect={"prefect.flow-run.Completed"},
                match_related={
                    "prefect.resource.id": f"prefect.deployment.{upstream.id}",
                    "prefect.resource.role": "deployment",
                },
            )
        ],
        tags=["strategic-reports", "rdf-export"],
        description=(
            "RDF (Turtle) export of the tracking database's accumulated "
            "archive — a full rebuild every run. Triggered immediately on "
            f"completion of the '{_UPSTREAM_DEPLOYMENT}' deployment."
        ),
        # db_path/output have no function default — same reason as
        # daily_report_flow's output_dir/db_path: an automation-triggered
        # run has no CLI invocation to supply them, so they're fixed here
        # once. Read from the same tracking database daily_report_flow
        # writes to.
        parameters={
            "db_path": Path.home()
            / "output"
            / "daily-strategic-report-from-RSS-feeds"
            / "strategic-reports.db",
            "output": Path.home()
            / "output"
            / "daily-strategic-report-from-RSS-feeds"
            / "knowledge_graph.ttl",
        },
    )
