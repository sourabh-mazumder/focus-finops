"""Loads a FOCUS-format CSV export (synthetic sample or a real AWS FOCUS
export) into the `focus_cost_and_usage` table.

Works with any subset/ordering of the official FOCUS column headers -- a
column present in the CSV but not in the FOCUS v1.4 mapping is reported and
skipped (rather than failing the whole load), so this is reasonably
tolerant of vendor quirks or extra columns.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import db
from .focus_columns import FOCUS_ID_TO_DB, NOT_NULL_FOCUS_IDS


class IngestError(RuntimeError):
    pass


@dataclass
class IngestResult:
    source_file: str
    rows_loaded: int
    unknown_columns: list[str]
    used_columns: list[str]


def ingest_csv(csv_path: str | Path, table: str = "focus_cost_and_usage") -> IngestResult:
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise IngestError(f"File not found: {csv_path}")

    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False, na_values=[""])
    headers = list(df.columns)

    known = [h for h in headers if h in FOCUS_ID_TO_DB]
    unknown = [h for h in headers if h not in FOCUS_ID_TO_DB]

    missing_required = [c for c in NOT_NULL_FOCUS_IDS if c not in known]
    if missing_required:
        raise IngestError(
            "CSV is missing required FOCUS columns: " + ", ".join(missing_required)
        )

    df = df[known].copy()
    df["source_file"] = csv_path.name
    db_columns = [FOCUS_ID_TO_DB[h] for h in known] + ["source_file"]

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline=""
    ) as tmp:
        df.to_csv(tmp, index=False, na_rep="")
        tmp_path = Path(tmp.name)

    try:
        db.copy_csv_into(table, db_columns, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    return IngestResult(
        source_file=csv_path.name,
        rows_loaded=len(df),
        unknown_columns=unknown,
        used_columns=known,
    )
