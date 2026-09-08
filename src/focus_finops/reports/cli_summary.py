"""Prints a plain-text cost summary to the terminal."""
from __future__ import annotations

from tabulate import tabulate

from . import queries


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
