"""Thin database access layer built on the `psql` command-line client.

Why psql instead of psycopg2 / SQLAlchemy?
-------------------------------------------
This keeps the project runnable with *zero* compiled/third-party DB driver
dependencies -- only `pandas`, `click`, `python-dotenv` (pure-ish Python,
commonly already present) plus the standard `psql` client that ships with
any PostgreSQL install. If you'd rather use psycopg2/SQLAlchemy in your own
environment, see the `driver` extra in pyproject.toml -- the SQL in
schema.sql and the queries in reports/ are plain, driver-agnostic SQL, so
swapping the access layer is a small, self-contained change.
"""
from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pandas as pd

from .config import get_config


class DbError(RuntimeError):
    pass


def _run_psql(args: list[str], input_text: str | None = None) -> str:
    cfg = get_config()
    cmd = ["psql", cfg.conninfo_uri, "-v", "ON_ERROR_STOP=1", *args]
    result = subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DbError(
            f"psql command failed (exit {result.returncode}):\n"
            f"  cmd: {' '.join(cmd)}\n"
            f"  stderr: {result.stderr.strip()}"
        )
    return result.stdout


def run_sql_file(path: str | Path) -> str:
    """Execute a .sql file (DDL, migrations, etc.)."""
    return _run_psql(["-f", str(path)])


def execute(sql: str) -> str:
    """Execute a single SQL statement (no result set expected)."""
    return _run_psql(["-q", "-c", sql])


def query_df(sql: str) -> pd.DataFrame:
    """Run a SELECT and return the results as a pandas DataFrame."""
    csv_out = _run_psql(["--csv", "-q", "-c", sql])
    if not csv_out.strip():
        return pd.DataFrame()
    return pd.read_csv(io.StringIO(csv_out))


def copy_csv_into(table: str, columns: list[str], csv_path: str | Path) -> None:
    """Bulk-load a CSV file into `table` using psql's client-side \\copy.

    `columns` must match the CSV's header order (and a subset/reordering of
    the table's own columns is fine -- unspecified columns keep their
    defaults).
    """
    csv_path = Path(csv_path).resolve()
    col_list = ", ".join(columns)
    copy_cmd = (
        f"\\copy {table} ({col_list}) FROM '{csv_path}' "
        f"WITH (FORMAT csv, HEADER true)"
    )
    _run_psql(["-q", "-c", copy_cmd])


def table_row_count(table: str) -> int:
    df = query_df(f"SELECT COUNT(*) AS n FROM {table};")
    return int(df.iloc[0]["n"])


def check_connection() -> None:
    _run_psql(["-q", "-c", "SELECT 1;"])
