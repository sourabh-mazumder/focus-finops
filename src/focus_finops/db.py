"""Database access layer: a pooled SQLAlchemy engine over psycopg2.

Replaces an earlier design that shelled out to the `psql` CLI once per
query. That kept the dependency footprint to zero compiled drivers, but
paid for it in ways that matter for anything beyond a single local run:

  - No connection pooling -- every query paid full TCP + auth handshake
    cost, and nothing prevented unbounded concurrent `psql` processes under
    load.
  - No concurrency control -- no shared pool, no limit on simultaneous
    connections to Postgres.
  - Fragile error handling -- failures surfaced as a parsed subprocess
    exit code and stderr text, not a typed, catchable exception carrying
    the original driver error.

A pooled `Engine` (created once per process, reused across every call)
fixes all three: connections are recycled rather than re-established,
`pool_size`/`max_overflow` bound concurrent connections, and driver errors
raise as normal Python exceptions wrapped in `DbError`.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import sqlalchemy as sa
from psycopg2 import extensions as _pg_extensions
from sqlalchemy.engine import Engine

from .config import get_config

# NUMERIC/DECIMAL columns come back from psycopg2 as Decimal by default;
# the rest of this codebase (numpy/pandas arithmetic, JSON serialization
# for the dashboard) expects plain floats, matching how the previous
# psql-CSV-text layer always rendered them. Registering this cast is
# process-wide and only needs to happen once.
_DEC2FLOAT = _pg_extensions.new_type(
    _pg_extensions.DECIMAL.values,
    "DEC2FLOAT",
    lambda value, curs: float(value) if value is not None else None,
)
_pg_extensions.register_type(_DEC2FLOAT)


class DbError(RuntimeError):
    pass


_engine: Engine | None = None


def get_engine() -> Engine:
    """The process-wide pooled engine, created lazily on first use."""
    global _engine
    if _engine is None:
        cfg = get_config()
        _engine = sa.create_engine(
            cfg.sqlalchemy_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,  # detect/replace a connection Postgres dropped
            pool_recycle=1800,   # recycle idle connections every 30 min
        )
    return _engine


def _execute_raw(sql_text: str, error_prefix: str) -> None:
    """Run raw SQL text through psycopg2 directly, bypassing SQLAlchemy's
    `exec_driver_sql` -- which always forwards a (possibly empty)
    parameters object to `cursor.execute()`, and psycopg2 treats *any*
    non-None second argument as "do %-style substitution," raising on a
    literal `%` in the text (e.g. a `'%'` in a SQL comment, or a LIKE
    pattern) even though no bind parameters were ever intended. Calling
    `cursor.execute(sql_text)` with truly no second argument sidesteps that
    entirely -- this is the same raw-connection path `copy_csv_into` uses.
    """
    raw_conn = get_engine().raw_connection()
    try:
        with raw_conn.cursor() as cur:
            cur.execute(sql_text)
        raw_conn.commit()
    except Exception as exc:
        raw_conn.rollback()
        raise DbError(f"{error_prefix}: {exc}") from exc
    finally:
        raw_conn.close()


def run_sql_file(path: str | Path) -> None:
    """Execute a .sql file (DDL, migrations, etc.) -- may contain multiple
    semicolon-separated statements."""
    sql_text = Path(path).read_text(encoding="utf-8")
    _execute_raw(sql_text, f"Failed to run {path}")


def execute(sql: str) -> None:
    """Execute a single SQL statement (no result set expected)."""
    _execute_raw(sql, f"Query failed\n  sql: {sql}")


def _normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Render date/timestamp columns as plain ISO strings.

    The native driver returns `date`/`timestamptz` columns as real
    pandas Timestamps rather than text -- more correct, but different from
    the plain "YYYY-MM-DD" strings the old psql-CSV layer always produced,
    which report code throughout this project embeds directly (chart
    labels, JSON keys, `.astype(str)` on a date column). Converting back to
    text here, once, keeps every caller's existing string handling working
    unchanged.
    """
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            has_time_component = (df[col].dt.time != pd.Timestamp(0).time()).any()
            fmt = "%Y-%m-%d %H:%M:%S" if has_time_component else "%Y-%m-%d"
            df[col] = df[col].dt.strftime(fmt)
    return df


def query_df(sql: str) -> pd.DataFrame:
    """Run a SELECT and return the results as a pandas DataFrame."""
    try:
        with get_engine().connect() as conn:
            df = pd.read_sql_query(sa.text(sql), conn)
    except sa.exc.SQLAlchemyError as exc:
        raise DbError(f"Query failed: {exc}\n  sql: {sql}") from exc
    return _normalize_dates(df)


def copy_csv_into(table: str, columns: list[str], csv_path: str | Path) -> None:
    """Bulk-load a CSV file into `table` via `COPY ... FROM STDIN`
    (psycopg2's fast bulk-copy path, not row-by-row INSERTs).

    `columns` must match the CSV's header order (and a subset/reordering of
    the table's own columns is fine -- unspecified columns keep their
    defaults).
    """
    csv_path = Path(csv_path).resolve()
    col_list = ", ".join(columns)
    copy_sql = f"COPY {table} ({col_list}) FROM STDIN WITH (FORMAT csv, HEADER true)"

    raw_conn = get_engine().raw_connection()
    try:
        with raw_conn.cursor() as cur, open(csv_path, "r", encoding="utf-8", newline="") as f:
            cur.copy_expert(copy_sql, f)
        raw_conn.commit()
    except Exception as exc:
        raw_conn.rollback()
        raise DbError(f"Bulk load into {table} failed: {exc}") from exc
    finally:
        raw_conn.close()


def table_row_count(table: str) -> int:
    df = query_df(f"SELECT COUNT(*) AS n FROM {table};")
    return int(df.iloc[0]["n"])


def check_connection() -> None:
    execute("SELECT 1;")
