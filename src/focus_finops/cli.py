"""Command-line entry point: `python -m focus_finops <command>`."""
from __future__ import annotations

from pathlib import Path

import click

from . import db
from .generate_sample_data import write_sample_csv
from .ingest import ingest_csv
from .reports import cli_summary, csv_exports, html_dashboard

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


@click.group()
def main():
    """Sample AWS FinOps project: FOCUS v1.4 data -> Postgres -> reports."""


@main.command("setup-db")
def setup_db_cmd():
    """Create the focus_cost_and_usage table (idempotent)."""
    db.run_sql_file(SCHEMA_PATH)
    click.echo("Schema applied.")


@main.command("generate-sample")
@click.option("--months", default=6, show_default=True, help="How many months of history to generate.")
def generate_sample_cmd(months: int):
    """Generate a synthetic FOCUS v1.4 sample CSV under data/samples/."""
    path = write_sample_csv(months_back=months)
    click.echo(f"Wrote sample data: {path}")


@main.command("ingest")
@click.argument("csv_file", type=click.Path(exists=True))
def ingest_cmd(csv_file: str):
    """Load a FOCUS-format CSV file into the database."""
    result = ingest_csv(csv_file)
    click.echo(f"Loaded {result.rows_loaded} rows from {result.source_file}.")
    if result.unknown_columns:
        click.echo(
            "Note: ignored columns not in the FOCUS v1.4 mapping: "
            + ", ".join(result.unknown_columns)
        )


@main.group("report")
def report_group():
    """Generate reports from the data already loaded in the database."""


@report_group.command("summary")
def report_summary_cmd():
    """Print a cost summary to the terminal."""
    cli_summary.run()


@report_group.command("export")
@click.option("--out-dir", default="reports_output", show_default=True)
def report_export_cmd(out_dir: str):
    """Write CSV summary exports (cost by service/account/month)."""
    paths = csv_exports.run(Path(out_dir))
    for p in paths:
        click.echo(f"Wrote {p}")


@report_group.command("dashboard")
@click.option("--out-file", default="reports_output/dashboard.html", show_default=True)
def report_dashboard_cmd(out_file: str):
    """Build a single-file interactive HTML cost dashboard."""
    path = html_dashboard.run(Path(out_file))
    click.echo(f"Wrote {path}")


if __name__ == "__main__":
    main()
