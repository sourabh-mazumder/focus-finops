"""Loads a simulated OpenTelemetry resource-metrics CSV (see
generate_otel_data.py) into the `otel_resource_metrics` table -- a
separate table from focus_cost_and_usage, whose structure this never
touches.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import db

TABLE = "otel_resource_metrics"

COLUMNS = [
    "resource_id", "resource_name", "resource_type", "service_category",
    "cloud_provider", "cloud_account_id", "cloud_account_name", "region_id",
    "metric_name", "metric_day", "value", "unit",
]


class OtelIngestError(RuntimeError):
    pass


@dataclass
class OtelIngestResult:
    source_file: str
    rows_loaded: int


def ingest_otel_csv(csv_path: str | Path, table: str = TABLE) -> OtelIngestResult:
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise OtelIngestError(f"File not found: {csv_path}")

    df = pd.read_csv(csv_path)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise OtelIngestError(f"CSV is missing expected columns: {', '.join(missing)}")

    df = df[COLUMNS].copy()
    df["source_file"] = csv_path.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as tmp:
        df.to_csv(tmp, index=False)
        tmp_path = Path(tmp.name)

    try:
        db.copy_csv_into(table, COLUMNS + ["source_file"], tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    return OtelIngestResult(source_file=csv_path.name, rows_loaded=len(df))
