"""
Prefect flow for the export-rdf CLI command.

Not yet scheduled — this only wraps `cli.py export-rdf`'s logic as a
Prefect @flow so it can run under Prefect (retries, run history, UI
visibility) and be scheduled later the same way daily_report.py is,
without changing this flow's signature when that happens. Until then,
run it directly:

    python -m strategic_reports.daily.flows.export_rdf_flow \\
        --db-path output/daily/strategic_reports.db \\
        --output output/daily/knowledge_graph.ttl

See AGENTS.md and README.md's "Exporting an RDF knowledge graph" section
for what export-rdf does and why it's kept separate from daily_report_flow.
"""

from pathlib import Path

import typer
from prefect import flow, task, get_run_logger

from strategic_reports.daily.core.db import connect as connect_db
from strategic_reports.daily.core.rdf_export import export_rdf

app = typer.Typer(add_completion=False)


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


@app.command()
def main(
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
    """Run the export-rdf flow directly (no schedule/deployment yet)."""
    triple_count = export_rdf_flow(db_path=db_path, output=output, since=since)
    typer.echo(f"RDF export written to {output} ({triple_count:,} triples)")


if __name__ == "__main__":
    app()
