"""SQL aggregation queries shared by the CLI summary, CSV export, and HTML
dashboard reports. All return pandas DataFrames.
"""
from __future__ import annotations

import pandas as pd

from .. import db

TABLE = "focus_cost_and_usage"


def overall_totals() -> pd.Series:
    df = db.query_df(f"""
        SELECT
            SUM(billed_cost)                       AS billed_cost,
            SUM(effective_cost)                    AS effective_cost,
            SUM(list_cost)                         AS list_cost,
            MIN(charge_period_start)               AS period_start,
            MAX(charge_period_start)               AS period_end,
            COUNT(DISTINCT sub_account_id)         AS sub_accounts,
            COUNT(DISTINCT service_name)           AS services,
            COUNT(*)                               AS line_items
        FROM {TABLE};
    """)
    if df.empty:
        return pd.Series(dtype=object)
    return df.iloc[0]


def cost_by_service() -> pd.DataFrame:
    return db.query_df(f"""
        SELECT service_category, service_name,
               SUM(billed_cost) AS billed_cost,
               SUM(effective_cost) AS effective_cost,
               SUM(list_cost) AS list_cost
        FROM {TABLE}
        GROUP BY service_category, service_name
        ORDER BY billed_cost DESC;
    """)


def cost_by_service_category() -> pd.DataFrame:
    return db.query_df(f"""
        SELECT service_category,
               SUM(billed_cost) AS billed_cost
        FROM {TABLE}
        GROUP BY service_category
        ORDER BY billed_cost DESC;
    """)


def cost_by_account() -> pd.DataFrame:
    return db.query_df(f"""
        SELECT COALESCE(sub_account_name, sub_account_id, billing_account_name) AS account,
               SUM(billed_cost) AS billed_cost
        FROM {TABLE}
        GROUP BY 1
        ORDER BY billed_cost DESC;
    """)


def cost_by_region() -> pd.DataFrame:
    return db.query_df(f"""
        SELECT COALESCE(region_id, '(none)') AS region,
               SUM(billed_cost) AS billed_cost
        FROM {TABLE}
        WHERE region_id IS NOT NULL
        GROUP BY 1
        ORDER BY billed_cost DESC;
    """)


def cost_trend_monthly() -> pd.DataFrame:
    return db.query_df(f"""
        SELECT to_char(date_trunc('month', charge_period_start), 'YYYY-MM') AS month,
               SUM(billed_cost) AS billed_cost
        FROM {TABLE}
        GROUP BY 1
        ORDER BY 1;
    """)


def cost_trend_daily() -> pd.DataFrame:
    return db.query_df(f"""
        SELECT to_char(date_trunc('day', charge_period_start), 'YYYY-MM-DD') AS day,
               SUM(billed_cost) AS billed_cost
        FROM {TABLE}
        GROUP BY 1
        ORDER BY 1;
    """)


def top_resources(limit: int = 10) -> pd.DataFrame:
    return db.query_df(f"""
        SELECT resource_name, service_name,
               COALESCE(sub_account_name, sub_account_id) AS account,
               SUM(billed_cost) AS billed_cost
        FROM {TABLE}
        WHERE resource_id IS NOT NULL AND resource_id <> ''
        GROUP BY resource_name, service_name, account
        ORDER BY billed_cost DESC
        LIMIT {int(limit)};
    """)


def savings_by_commitment_type() -> pd.DataFrame:
    return db.query_df(f"""
        SELECT COALESCE(commitment_discount_type, 'On-Demand / Standard') AS coverage,
               SUM(list_cost) AS list_cost,
               SUM(effective_cost) AS effective_cost
        FROM {TABLE}
        GROUP BY 1
        ORDER BY list_cost DESC;
    """)


def cost_by_charge_category() -> pd.DataFrame:
    return db.query_df(f"""
        SELECT charge_category,
               SUM(billed_cost) AS billed_cost
        FROM {TABLE}
        GROUP BY 1
        ORDER BY billed_cost DESC;
    """)
