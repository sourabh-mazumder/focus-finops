"""Generates simulated OpenTelemetry-style resource utilization metrics
(data/samples/otel_metrics_<start>_<end>.csv), based on the resources and
date range actually present in the `focus_cost_and_usage` table.

This is NOT independent random data: each resource's daily utilization is
generated so it can be meaningfully correlated against that same resource's
daily cost (already loaded in Postgres), which is the point of simulating
it in the first place -- see reports/otel_insights.py for the correlation
logic this feeds.

For each qualifying resource (Compute / Databases / Storage / Networking --
the categories where "utilization" is a meaningful concept):

  - A baseline utilization level and weekday/weekend pattern (reusing the
    same production-vs-non-production signal already in the FOCUS `Tags`
    column) give a realistic, non-random daily series.
  - Days already flagged as cost outliers (a simple z-score on that
    resource's own daily cost) are, on a coin flip, EITHER given a matching
    utilization spike (a real, demand-driven cost increase) OR left
    unchanged (an unexplained cost spike -- a pricing error, an orphaned
    resource, an untagged job). Both cases are deliberately produced so a
    correlation analysis has genuine examples of each to find.
  - A subset of committed/reserved resources are deliberately given a low
    utilization band regardless of any spike, to produce genuine
    "rightsizing candidate" cases (steady committed spend, low usage).

Metrics generated per category:
  - Compute:    system.cpu.utilization, system.memory.utilization
  - Databases:  system.cpu.utilization, system.memory.utilization,
                db.client.connections.active
  - Storage:    system.filesystem.utilization
  - Networking: network.io.utilization, network.client.errors

Analytics/Other resources are skipped -- "utilization" isn't a meaningful
concept for a BigQuery dataset or a tax/support line item. Networking
line items with no resource_id (generic inter-region data transfer, in the
sample data) are skipped too -- there's nothing to attach a resource-level
metric to; only named resources (CDN distributions) qualify.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import db
from .reports.queries import TABLE

RNG_SEED = 20260908

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = PROJECT_ROOT / "data" / "samples"

UTILIZATION_CATEGORIES = ("Compute", "Databases", "Storage", "Networking")

METRIC_SPECS = {
    "Compute": [
        ("system.cpu.utilization", "%", (15.0, 65.0)),
        ("system.memory.utilization", "%", (30.0, 70.0)),
    ],
    "Databases": [
        ("system.cpu.utilization", "%", (20.0, 55.0)),
        ("system.memory.utilization", "%", (40.0, 75.0)),
        ("db.client.connections.active", "count", (5.0, 80.0)),
    ],
    "Storage": [
        ("system.filesystem.utilization", "%", (35.0, 85.0)),
    ],
    "Networking": [
        ("network.io.utilization", "%", (20.0, 75.0)),
        ("network.client.errors", "count", (0.0, 15.0)),
    ],
}
# The metric each category's spike/rightsizing injection logic keys off of.
PRIMARY_METRIC = {
    "Compute": "system.cpu.utilization",
    "Databases": "system.cpu.utilization",
    "Storage": "system.filesystem.utilization",
    "Networking": "network.io.utilization",
}


def _resource_catalog() -> pd.DataFrame:
    """Distinct Compute/Database/Storage resources already in the FOCUS
    table, with each resource's own charge-period date range, committed
    status, and Environment tag (used for the weekday/weekend pattern).
    """
    categories = ", ".join(f"'{c}'" for c in UTILIZATION_CATEGORIES)
    return db.query_df(f"""
        SELECT
            resource_id,
            MAX(COALESCE(NULLIF(resource_name, ''), resource_id)) AS resource_name,
            MAX(resource_type)                                     AS resource_type,
            MAX(service_category)                                  AS service_category,
            MAX(service_provider_name)                              AS cloud_provider,
            MAX(sub_account_id)                                     AS cloud_account_id,
            MAX(COALESCE(sub_account_name, sub_account_id))         AS cloud_account_name,
            MAX(region_id)                                          AS region_id,
            MAX(tags->>'Environment')                               AS environment,
            BOOL_OR(commitment_discount_id IS NOT NULL)::int         AS committed,
            MIN(charge_period_start)::date                          AS start_day,
            MAX(charge_period_start)::date                          AS end_day
        FROM {TABLE}
        WHERE resource_id IS NOT NULL AND resource_id <> ''
          AND service_category IN ({categories})
        GROUP BY resource_id
        ORDER BY resource_id;
    """)


def _daily_cost_all() -> pd.DataFrame:
    """Daily cost per resource, for every Compute/Databases/Storage/
    Networking resource at once (a single query, grouped in pandas
    afterward -- avoids building a per-resource WHERE clause from a Python
    string).
    """
    categories = ", ".join(f"'{c}'" for c in UTILIZATION_CATEGORIES)
    return db.query_df(f"""
        SELECT resource_id,
               date_trunc('day', charge_period_start)::date AS day,
               SUM(billed_cost) AS cost
        FROM {TABLE}
        WHERE resource_id IS NOT NULL AND resource_id <> ''
          AND service_category IN ({categories})
        GROUP BY 1, 2
        ORDER BY 1, 2;
    """)


def _cost_spike_days(daily_cost: pd.Series, z_thresh: float = 2.0) -> pd.DatetimeIndex:
    """Simple z-score flag on the resource's own cost history -- deliberately
    independent of ml_insights.detect_zscore_anomalies (this generator has
    to run before that analysis is meaningful, and needs no more than a
    quick internal flag to decide where to inject/withhold a utilization
    spike).
    """
    if len(daily_cost) < 10:
        return pd.DatetimeIndex([])
    mean, std = daily_cost.mean(), daily_cost.std(ddof=0)
    if not std:
        return pd.DatetimeIndex([])
    z = (daily_cost - mean) / std
    return daily_cost.index[z >= z_thresh]


def _resource_rng(resource_id: str) -> np.random.Generator:
    seed = (RNG_SEED ^ (hash(resource_id) & 0xFFFFFFFF)) & 0xFFFFFFFF
    return np.random.default_rng(seed)


def _simulate_resource(row: pd.Series, daily_cost: pd.Series) -> list[dict]:
    rng = _resource_rng(row["resource_id"])
    category = row["service_category"]
    specs = METRIC_SPECS[category]
    primary_metric = PRIMARY_METRIC[category]

    days = pd.date_range(row["start_day"], row["end_day"], freq="D")
    is_weekend = days.weekday >= 5
    weekday_dampening = row["environment"] not in (None, "production")

    # Rightsizing case: some committed resources are pinned to a low
    # utilization band regardless of any cost spike -- steady spend, low
    # actual use.
    force_low = bool(row["committed"]) and rng.random() < 0.4

    spike_days = set(_cost_spike_days(daily_cost)) if not daily_cost.empty else set()
    # Roughly half of this resource's cost-spike days get a matching
    # utilization spike (explained); the rest are left alone (unexplained).
    explained_spike_days = {d for d in spike_days if rng.random() < 0.5}

    rows: list[dict] = []
    for metric_name, unit, (lo, hi) in specs:
        if force_low and metric_name == primary_metric:
            baseline = rng.uniform(4.0, 18.0)
        else:
            baseline = rng.uniform(lo, hi)
        noise_scale = max(baseline * 0.12, 2.0)

        values = baseline + rng.normal(0, noise_scale, size=len(days))
        if weekday_dampening:
            values[is_weekend] *= rng.uniform(0.55, 0.75)

        if metric_name == primary_metric:
            for i, day in enumerate(days):
                if day in explained_spike_days:
                    values[i] += rng.uniform(30.0, 50.0)

        cap = 100.0 if unit == "%" else None
        values = np.clip(values, 0.5, cap)

        for day, value in zip(days, values):
            rows.append({
                "resource_id": row["resource_id"],
                "resource_name": row["resource_name"],
                "resource_type": row["resource_type"],
                "service_category": category,
                "cloud_provider": row["cloud_provider"],
                "cloud_account_id": row["cloud_account_id"],
                "cloud_account_name": row["cloud_account_name"],
                "region_id": row["region_id"],
                "metric_name": metric_name,
                "metric_day": day.strftime("%Y-%m-%d"),
                "value": round(float(value), 4),
                "unit": unit,
            })
    return rows


def generate_otel_rows() -> list[dict]:
    catalog = _resource_catalog()
    cost_df = _daily_cost_all()
    cost_by_resource = {
        rid: pd.Series(g["cost"].to_numpy(), index=pd.to_datetime(g["day"]))
        for rid, g in cost_df.groupby("resource_id")
    }

    rows: list[dict] = []
    for _, resource in catalog.iterrows():
        daily_cost = cost_by_resource.get(resource["resource_id"], pd.Series(dtype=float))
        rows.extend(_simulate_resource(resource, daily_cost))
    return rows


def write_otel_csv(out_path: Path | None = None) -> Path:
    rows = generate_otel_rows()
    if not rows:
        raise RuntimeError(
            "No Compute/Databases/Storage/Networking resources found in focus_cost_and_usage -- "
            "run `focus-finops ingest` first."
        )
    df = pd.DataFrame(rows)

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    if out_path is None:
        start = df["metric_day"].min()
        end = df["metric_day"].max()
        out_path = SAMPLES_DIR / f"otel_metrics_{start}_to_{end}.csv"

    df.to_csv(out_path, index=False)
    return out_path


if __name__ == "__main__":
    path = write_otel_csv()
    print(f"Wrote {path}")
