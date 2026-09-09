"""ML-based cost optimization insights: anomaly detection, spend forecasting,
and commitment-candidate recommendations.

Unlike the plain SQL aggregations in `queries.py`, these pull row-level data
into pandas and fit lightweight scikit-learn models (IsolationForest,
LinearRegression, KMeans). The choices here favor speed and explainability
over sophistication -- they're sized for a multi-month synthetic/sample
dataset, not tuned as production-grade cost-anomaly or forecasting systems.

All three functions add `provider` / `account` / `application` / `owner`
columns (matching `queries.dashboard_cube()`'s dimension values) so their
output can be filtered the same way as the rest of the dashboard.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler

from .. import db
from .queries import TABLE, dashboard_cube


def _resource_daily(extra_where: str = "") -> pd.DataFrame:
    """Daily cost/quantity per resource-level usage line item (excludes
    account-level charges like tax/support/credits, which have no
    resource_id).
    """
    return db.query_df(f"""
        SELECT
            service_provider_name                                            AS provider,
            COALESCE(sub_account_name, sub_account_id, billing_account_name) AS account,
            COALESCE(tags->>'Application', '(untagged)')                    AS application,
            COALESCE(tags->>'Owner', '(untagged)')                          AS owner,
            service_category,
            service_name,
            COALESCE(resource_name, resource_id)                            AS resource,
            date_trunc('day', charge_period_start)::date                    AS day,
            SUM(consumed_quantity) AS quantity,
            SUM(effective_cost)    AS cost
        FROM {TABLE}
        WHERE resource_id IS NOT NULL AND resource_id <> '' {extra_where}
        GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
        ORDER BY resource, day;
    """)


def detect_cost_anomalies(contamination: float = 0.06, min_days: int = 14) -> pd.DataFrame:
    """Flags daily cost outliers per resource with an IsolationForest fit
    independently on each resource's own cost history, so e.g. a
    $500/day database and a $2/day Lambda function are judged against their
    own baseline rather than against each other.

    Resources with fewer than `min_days` days of history are skipped -- too
    little data for a per-resource baseline to mean anything.
    """
    daily = _resource_daily()
    if daily.empty:
        return pd.DataFrame()

    flagged = []
    for _, grp in daily.groupby(
        ["provider", "account", "application", "owner", "service_category", "service_name", "resource"],
        sort=False,
    ):
        if len(grp) < min_days:
            continue
        grp = grp.sort_values("day").reset_index(drop=True)
        X = grp[["cost"]].to_numpy()

        model = IsolationForest(contamination=contamination, random_state=0, n_estimators=200)
        preds = model.fit_predict(X)          # 1 = normal, -1 = anomaly
        scores = -model.decision_function(X)  # higher = more anomalous
        grp["is_outlier"] = preds == -1
        grp["anomaly_score"] = scores

        outliers = grp[grp["is_outlier"]].copy()
        if outliers.empty:
            continue

        baseline = grp.loc[~grp["is_outlier"], "cost"].median()
        if pd.isna(baseline):
            baseline = grp["cost"].median()
        outliers["baseline_cost"] = round(float(baseline), 2)
        outliers["pct_above_baseline"] = (
            ((outliers["cost"] - baseline) / baseline * 100).round(1) if baseline else np.nan
        )
        flagged.append(outliers)

    if not flagged:
        return pd.DataFrame()

    result = pd.concat(flagged, ignore_index=True)
    result["day"] = result["day"].astype(str)
    result = result.sort_values("anomaly_score", ascending=False)
    return result[[
        "provider", "account", "application", "owner", "service_category", "service_name",
        "resource", "day", "cost", "baseline_cost", "pct_above_baseline", "anomaly_score",
    ]].round({"cost": 2, "anomaly_score": 4})


def forecast_spend(min_months: int = 3, overrun_threshold_pct: float = 15.0) -> pd.DataFrame:
    """Projects next month's billed cost per provider/account/application/
    owner/service-category combination with a straight-line trend
    (LinearRegression on month index -> cost) fit over that combination's
    own monthly history, then flags combinations whose forecast exceeds the
    last actual month by more than `overrun_threshold_pct` as a budget
    overrun risk.

    A linear trend is a deliberately simple choice given the short history
    typical of this project's sample data (a handful of months) -- not a
    claim that cloud spend trends are linear in general. Combinations with
    fewer than `min_months` months of history are skipped.
    """
    cube = dashboard_cube()
    if cube.empty:
        return pd.DataFrame()

    group_cols = ["provider", "account", "application", "owner", "service_category"]
    monthly = (
        cube.groupby(group_cols + ["month"], as_index=False)["billed_cost"].sum()
    )

    rows = []
    for key, grp in monthly.groupby(group_cols, sort=False):
        months_sorted = sorted(grp["month"].unique())
        if len(months_sorted) < min_months:
            continue
        y = grp.set_index("month").reindex(months_sorted)["billed_cost"].fillna(0.0).to_numpy()
        x = np.arange(len(months_sorted)).reshape(-1, 1)

        model = LinearRegression().fit(x, y)
        forecast = max(float(model.predict([[len(months_sorted)]])[0]), 0.0)
        last_actual = float(y[-1])
        pct_change = ((forecast - last_actual) / last_actual * 100) if last_actual else None

        rows.append({
            **dict(zip(group_cols, key)),
            "months_of_history": len(months_sorted),
            "last_actual_month": months_sorted[-1],
            "last_actual_cost": round(last_actual, 2),
            "forecast_next_month_cost": round(forecast, 2),
            "forecast_pct_change": round(pct_change, 1) if pct_change is not None else None,
            "overrun_risk": bool(pct_change is not None and pct_change >= overrun_threshold_pct),
        })

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    return result.sort_values("forecast_pct_change", ascending=False, na_position="last")


def recommend_commitments(n_clusters: int = 3, min_days: int = 14) -> pd.DataFrame:
    """Flags currently *uncommitted* Compute/Database resources that look
    like good Savings Plan / Reserved Instance / Committed Use Discount
    candidates: steady, high-volume daily cost -- the usage profile FinOps
    guidance targets for commitment coverage -- versus bursty or low-volume
    usage that's better left on-demand.

    Uses KMeans (k=`n_clusters`) on two standardized features -- mean daily
    cost and day-to-day cost volatility (coefficient of variation) -- then
    labels the cluster with the highest mean cost and lowest volatility as
    "recommended". This surfaces *candidates* for a FinOps/engineering
    review, not an automated purchase decision.
    """
    daily = _resource_daily(
        extra_where="AND commitment_discount_id IS NULL AND service_category IN ('Compute', 'Databases')"
    )
    if daily.empty:
        return pd.DataFrame()

    group_cols = ["provider", "account", "application", "owner", "service_category", "service_name", "resource"]
    stats = daily.groupby(group_cols).agg(
        days=("day", "count"),
        mean_daily_cost=("cost", "mean"),
        cost_volatility=("cost", lambda s: float(s.std(ddof=0) / s.mean()) if s.mean() else 0.0),
        total_cost=("cost", "sum"),
    ).reset_index()
    stats = stats[stats["days"] >= min_days].drop(columns=["days"])
    if stats.empty:
        return pd.DataFrame()

    if len(stats) < 2:
        stats["recommended"] = True
    else:
        features = stats[["mean_daily_cost", "cost_volatility"]].to_numpy()
        k = min(n_clusters, len(stats))
        scaled = StandardScaler().fit_transform(features)
        clusters = KMeans(n_clusters=k, n_init=10, random_state=0).fit_predict(scaled)
        stats["_cluster"] = clusters

        profile = stats.groupby("_cluster").agg(
            avg_cost=("mean_daily_cost", "mean"), avg_vol=("cost_volatility", "mean")
        )
        # Best commitment candidates: high average cost, low volatility.
        profile["rank_score"] = profile["avg_cost"] * (1 - profile["avg_vol"].clip(upper=1.0))
        best_cluster = profile["rank_score"].idxmax()
        stats["recommended"] = stats["_cluster"] == best_cluster
        stats = stats.drop(columns=["_cluster"])

    stats["mean_daily_cost"] = stats["mean_daily_cost"].round(2)
    stats["cost_volatility"] = stats["cost_volatility"].round(3)
    stats["total_cost"] = stats["total_cost"].round(2)
    return stats.sort_values(["recommended", "mean_daily_cost"], ascending=[False, False])
