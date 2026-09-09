"""Joins the simulated OpenTelemetry utilization metrics
(`otel_resource_metrics`) against FOCUS cost data (`focus_cost_and_usage`)
on (resource_id, day), and classifies each resource by how well its cost
tracks its actual utilization -- the analytical point of generating
correlated telemetry in the first place (see generate_otel_data.py).
"""
from __future__ import annotations

import pandas as pd

from .. import db
from .queries import TABLE as FOCUS_TABLE

OTEL_TABLE = "otel_resource_metrics"

# Keep in sync with generate_otel_data.PRIMARY_METRIC -- the metric each
# category's utilization signal is judged on. Duplicated (not imported)
# to keep this report module independent of the data-generation module.
PRIMARY_METRIC = {
    "Compute": "system.cpu.utilization",
    "Databases": "system.cpu.utilization",
    "Storage": "system.filesystem.utilization",
}


def _otel_long() -> pd.DataFrame:
    # otel_resource_metrics is optional (created by `setup-otel-db` /
    # populated by `generate-otel` + `ingest-otel`) -- treat "table doesn't
    # exist yet" the same as "no data yet" rather than failing the whole
    # report.
    try:
        return db.query_df(f"""
            SELECT resource_id, resource_name,
                   cloud_provider AS provider, cloud_account_name AS account,
                   service_category, metric_name, metric_day AS day, value
            FROM {OTEL_TABLE}
            ORDER BY resource_id, metric_day;
        """)
    except db.DbError:
        return pd.DataFrame()


def _daily_cost() -> pd.DataFrame:
    return db.query_df(f"""
        SELECT resource_id,
               date_trunc('day', charge_period_start)::date AS day,
               SUM(billed_cost) AS cost
        FROM {FOCUS_TABLE}
        WHERE resource_id IN (SELECT DISTINCT resource_id FROM {OTEL_TABLE})
        GROUP BY 1, 2
        ORDER BY 1, 2;
    """)


def resource_daily_series() -> pd.DataFrame:
    """One row per (resource, day): cost plus each of that resource's
    utilization metrics as columns (pivoted wide) -- the shape the
    dashboard's per-resource chart and the correlation analysis both need.
    Empty DataFrame if no OTel data has been ingested yet.
    """
    long_df = _otel_long()
    if long_df.empty:
        return pd.DataFrame()

    meta_cols = ["resource_id", "resource_name", "provider", "account", "service_category"]
    meta = long_df[meta_cols].drop_duplicates(subset="resource_id")

    wide = long_df.pivot_table(
        index=["resource_id", "day"], columns="metric_name", values="value"
    ).reset_index()
    wide["day"] = pd.to_datetime(wide["day"])

    cost_df = _daily_cost()
    cost_df["day"] = pd.to_datetime(cost_df["day"])

    merged = wide.merge(cost_df, on=["resource_id", "day"], how="left")
    merged["cost"] = merged["cost"].fillna(0.0)
    merged = merged.merge(meta, on="resource_id", how="left")
    merged["day"] = merged["day"].dt.strftime("%Y-%m-%d")
    return merged


def cost_utilization_correlation() -> pd.DataFrame:
    """Per resource: Pearson correlation between daily cost and daily
    utilization (its category's primary metric), average utilization, and
    a plain-language classification --

      - Cost tracks usage (healthy): cost and utilization move together.
      - Rightsizing candidate: utilization is persistently low regardless
        of cost -- a steady/committed charge for a mostly-idle resource.
      - Investigate: cost varies independently of utilization -- the
        pattern a pricing error, orphaned resource, or untagged job would
        produce.
      - Weak correlation / monitor: neither clearly healthy nor clearly
        worth flagging yet.

    This uses simulated telemetry -- see generate_otel_data.py -- so it
    demonstrates the correlation *method*, not a finding about real
    infrastructure.
    """
    daily = resource_daily_series()
    if daily.empty:
        return pd.DataFrame()

    rows = []
    for resource_id, grp in daily.groupby("resource_id"):
        category = grp["service_category"].iloc[0]
        metric_col = PRIMARY_METRIC.get(category)
        if metric_col not in grp.columns:
            continue

        sub = grp[["cost", metric_col]].dropna()
        if len(sub) < 5 or sub["cost"].std() == 0 or sub[metric_col].std() == 0:
            corr = 0.0
        else:
            corr = float(sub["cost"].corr(sub[metric_col]))
        avg_util = float(grp[metric_col].mean())

        if corr >= 0.5:
            label = "Cost tracks usage (healthy)"
        elif avg_util < 25 and corr < 0.3:
            label = "Rightsizing candidate (low utilization, steady cost)"
        elif corr < 0.15:
            label = "Investigate -- cost independent of utilization"
        else:
            label = "Weak correlation -- monitor"

        rows.append({
            "resource_id": resource_id,
            "resource_name": grp["resource_name"].iloc[0],
            "provider": grp["provider"].iloc[0],
            "account": grp["account"].iloc[0],
            "service_category": category,
            "avg_utilization": round(avg_util, 1),
            "correlation": round(corr, 2),
            "classification": label,
        })

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    return result.sort_values("correlation")
