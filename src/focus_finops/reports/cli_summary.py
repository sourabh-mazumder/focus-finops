"""Prints a plain-text cost summary to the terminal."""
from __future__ import annotations

import pandas as pd
from tabulate import tabulate

from . import cost_prediction, ml_insights, otel_insights, queries


def _money(v) -> str:
    return f"${float(v):,.2f}"


def run() -> None:
    totals = queries.overall_totals()
    if totals.empty or not totals.get("line_items"):
        print("No data loaded yet. Run `focus-finops ingest <file.csv>` first.")
        return

    print("=" * 60)
    print("FOCUS FinOps Summary")
    print("=" * 60)
    print(f"Period covered:   {totals['period_start']}  ->  {totals['period_end']}")
    print(f"Line items:       {int(totals['line_items']):,}")
    print(f"Providers:        {int(totals['providers'])}")
    print(f"Sub-accounts:     {int(totals['sub_accounts'])}")
    print(f"Services:         {int(totals['services'])}")
    print()
    print(f"Total List Cost:      {_money(totals['list_cost'])}")
    print(f"Total Effective Cost: {_money(totals['effective_cost'])}")
    print(f"Total Billed Cost:    {_money(totals['billed_cost'])}")
    savings = float(totals["list_cost"]) - float(totals["effective_cost"])
    pct = (savings / float(totals["list_cost"]) * 100) if totals["list_cost"] else 0
    print(f"Savings vs List:      {_money(savings)}  ({pct:.1f}%)")
    print()

    print("-- Cost by provider " + "-" * 38)
    df = queries.cost_by_provider()
    df["billed_cost"] = df["billed_cost"].map(_money)
    print(tabulate(df, headers="keys", tablefmt="simple", showindex=False))
    print()

    print("-- Cost by service category " + "-" * 30)
    df = queries.cost_by_service_category()
    df["billed_cost"] = df["billed_cost"].map(_money)
    print(tabulate(df, headers="keys", tablefmt="simple", showindex=False))
    print()

    print("-- Cost by account " + "-" * 39)
    df = queries.cost_by_account()
    df["billed_cost"] = df["billed_cost"].map(_money)
    print(tabulate(df, headers="keys", tablefmt="simple", showindex=False))
    print()

    print("-- Cost by application " + "-" * 35)
    df = queries.cost_by_application()
    df["billed_cost"] = df["billed_cost"].map(_money)
    print(tabulate(df, headers="keys", tablefmt="simple", showindex=False))
    print()

    print("-- Cost by owner " + "-" * 41)
    df = queries.cost_by_owner()
    df["billed_cost"] = df["billed_cost"].map(_money)
    print(tabulate(df, headers="keys", tablefmt="simple", showindex=False))
    print()

    print("-- Top 10 resources by cost " + "-" * 30)
    df = queries.top_resources(10)
    df["billed_cost"] = df["billed_cost"].map(_money)
    print(tabulate(df, headers="keys", tablefmt="simple", showindex=False))
    print()

    print("-- Monthly trend " + "-" * 41)
    df = queries.cost_trend_monthly()
    df["billed_cost"] = df["billed_cost"].map(_money)
    print(tabulate(df, headers="keys", tablefmt="simple", showindex=False))
    print()

    print("-- Commitment / reservation coverage " + "-" * 21)
    df = queries.commitment_utilization_summary()
    if df.empty:
        print("(no commitment-covered usage in this data)")
    else:
        df["coverage_pct"] = df["coverage_pct"].map(lambda v: f"{float(v):.1f}%" if pd.notna(v) else "-")
        df["savings"] = df["savings"].map(_money)
        print(tabulate(df, headers="keys", tablefmt="simple", showindex=False))
        print("Coverage = share of usage billed at the committed rate, not used-vs-purchased")
        print("utilization -- FOCUS usage rows don't carry the commitment's purchased quantity.")
    print()

    print("-- Statistical: cost anomalies (top 10, rolling z-score) " + "-" * 1)
    df = ml_insights.detect_zscore_anomalies()
    if df.empty:
        print("(not enough per-service daily history to compute a rolling baseline)")
    else:
        counts = df["severity"].value_counts()
        show = df.head(10).copy()
        show["cost"] = show["cost"].map(_money)
        show["rolling_mean"] = show["rolling_mean"].map(_money)
        show["rolling_std"] = show["rolling_std"].map(_money)
        print(tabulate(show, headers="keys", tablefmt="simple", showindex=False))
        print(
            f"{len(df)} day(s) flagged total ({int(counts.get('critical', 0))} critical z>=3.0, "
            f"{int(counts.get('warning', 0))} warning z>=2.0) -- 7-day trailing mean/std per service,"
        )
        print("today's cost excluded from its own baseline. A simpler complement to the")
        print("IsolationForest method below; the two can and do disagree on some days.")
    print()

    print("-- ML: cost anomalies (top 10, IsolationForest) " + "-" * 9)
    df = ml_insights.detect_cost_anomalies()
    if df.empty:
        print("(not enough per-resource history to flag anomalies)")
    else:
        show = df.head(10).copy()
        show["cost"] = show["cost"].map(_money)
        show["baseline_cost"] = show["baseline_cost"].map(_money)
        show["pct_above_baseline"] = show["pct_above_baseline"].map(lambda v: f"+{v:.0f}%" if pd.notna(v) else "-")
        show = show.drop(columns=["anomaly_score"])
        print(tabulate(show, headers="keys", tablefmt="simple", showindex=False))
    print()

    print("-- ML: spend forecast / overrun risk (linear trend) " + "-" * 6)
    df = ml_insights.forecast_spend()
    if df.empty:
        print("(not enough monthly history to forecast)")
    else:
        risky = df[df["overrun_risk"]].head(10).copy()
        show = risky if not risky.empty else df.head(10).copy()
        show["last_actual_cost"] = show["last_actual_cost"].map(_money)
        show["forecast_next_month_cost"] = show["forecast_next_month_cost"].map(_money)
        show["forecast_pct_change"] = show["forecast_pct_change"].map(lambda v: f"+{v:.0f}%" if pd.notna(v) and v >= 0 else (f"{v:.0f}%" if pd.notna(v) else "-"))
        show = show.drop(columns=["months_of_history"])
        print(tabulate(show, headers="keys", tablefmt="simple", showindex=False))
        print("Straight-line trend per combination's own monthly history -- a short-history estimate,")
        print("not a claim that cloud spend trends are linear in general.")
    print()

    print("-- ML: commitment candidates (KMeans, top 10) " + "-" * 12)
    df = ml_insights.recommend_commitments()
    if df.empty:
        print("(no uncommitted Compute/Database resources with enough history)")
    else:
        show = df[df["recommended"]].head(10).drop(columns=["recommended"]).copy()
        if show.empty:
            print("(no resources currently look like good commitment candidates)")
        else:
            show["mean_daily_cost"] = show["mean_daily_cost"].map(_money)
            show["total_cost"] = show["total_cost"].map(_money)
            print(tabulate(show, headers="keys", tablefmt="simple", showindex=False))
            print("Recommended = steady, high-volume usage cluster (good Savings Plan/RI/CUD fit) --")
            print("a candidate list for FinOps review, not a purchase decision.")
    print()

    print("-- 3-month cost prediction (blended model, portfolio-wide) " + "-" * 1)
    prediction = cost_prediction.predict_cost()
    df = prediction["monthly"]
    if df.empty:
        print("(not enough history to forecast)")
    else:
        show = df.copy()
        show["forecast"] = show["forecast"].map(_money)
        show["lower"] = show["lower"].map(_money)
        show["upper"] = show["upper"].map(_money)
        show = show.rename(columns={"lower": "80% low", "upper": "80% high"})
        print(tabulate(show, headers="keys", tablefmt="simple", showindex=False))
        print("Blends: fixed-fee model for committed/reserved usage, linear regression for")
        print("storage growth, and Prophet (trend + weekly seasonality) for compute/network/")
        print("other -- summed as simulated sample paths so the interval reflects all three")
        print("sources of uncertainty together. Portfolio-wide; not affected by filters.")
    print()

    print("-- Telemetry: cost vs. utilization correlation (simulated OTel) " + "-" * 1)
    df = otel_insights.cost_utilization_correlation()
    if df.empty:
        print("(no OTel telemetry loaded -- run `generate-otel` then `ingest-otel` first)")
    else:
        counts = df["classification"].value_counts()
        show = df.head(10).drop(columns=["resource_id"]).copy()
        print(tabulate(show, headers="keys", tablefmt="simple", showindex=False))
        print(f"{len(df)} resources analyzed: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
        print("Simulated telemetry correlated against real FOCUS cost -- demonstrates the")
        print("cost/utilization correlation method, not a finding about real infrastructure.")
