"""Builds a single self-contained HTML cost dashboard from the data
currently loaded in Postgres. No external JS/CSS -- opens and works fully
offline in any browser.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import queries
from .charting import (
    bar_chart,
    data_table,
    fmt_money,
    fmt_money_full,
    line_chart,
    stat_tile,
    two_series_bar_chart,
)

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>FOCUS FinOps Dashboard</title>
<style>
  .viz-root {{
    color-scheme: light;
    --page-plane:     #f9f9f7;
    --surface-1:      #fcfcfb;
    --text-primary:   #0b0b0b;
    --text-secondary: #52514e;
    --text-muted:     #898781;
    --gridline:       #e1e0d9;
    --baseline:       #c3c2b7;
    --border:         rgba(11,11,11,0.10);
    --series-1:       #2a78d6;
    --series-2:       #eb6834;
    --good:           #006300;
  }}
  @media (prefers-color-scheme: dark) {{
    .viz-root {{
      color-scheme: dark;
      --page-plane:     #0d0d0d;
      --surface-1:      #1a1a19;
      --text-primary:   #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted:     #898781;
      --gridline:       #2c2c2a;
      --baseline:       #383835;
      --border:         rgba(255,255,255,0.10);
      --series-1:       #3987e5;
      --series-2:       #d95926;
      --good:           #0ca30c;
    }}
  }}

  * {{ box-sizing: border-box; }}
  body {{ margin: 0; }}
  .viz-root {{
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--page-plane);
    color: var(--text-primary);
    padding: 32px 24px 64px;
  }}
  .page {{ max-width: 1080px; margin: 0 auto; }}
  header.page-header {{ margin-bottom: 28px; }}
  header.page-header h1 {{ font-size: 22px; margin: 0 0 4px; }}
  header.page-header p {{ margin: 0; color: var(--text-secondary); font-size: 13px; }}

  .stat-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
    margin-bottom: 28px;
  }}
  .stat-tile {{
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
  }}
  .stat-label {{ font-size: 12px; color: var(--text-secondary); margin-bottom: 6px; }}
  .stat-value {{ font-size: 26px; font-weight: 600; }}
  .stat-sub {{ font-size: 12px; color: var(--text-muted); margin-top: 4px; }}

  .chart-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 16px;
  }}
  .chart-grid.full {{ grid-template-columns: 1fr; }}
  @media (max-width: 780px) {{ .chart-grid {{ grid-template-columns: 1fr; }} }}

  .chart-card {{
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px 8px;
  }}
  .chart-card h3 {{ font-size: 14px; margin: 0 0 2px; }}
  .chart-subtitle {{ font-size: 12px; color: var(--text-secondary); margin: 0 0 10px; }}
  .empty {{ color: var(--text-muted); font-size: 13px; }}

  .svg-wrap {{ position: relative; width: 100%; }}
  .chart-svg {{ width: 100%; height: auto; display: block; overflow: visible; }}

  .gridline {{ stroke: var(--gridline); stroke-width: 1; }}
  .tick-label {{ fill: var(--text-muted); font-size: 10px; }}
  .cat-label {{ fill: var(--text-secondary); font-size: 11px; }}
  .value-label {{ fill: var(--text-primary); font-size: 11px; font-weight: 600; }}
  .on-fill {{ fill: #ffffff; font-size: 11px; font-weight: 600; }}
  .bar {{ fill: var(--series-1); cursor: pointer; }}
  .bar-series-1 {{ fill: var(--series-1); cursor: pointer; }}
  .bar-series-2 {{ fill: var(--series-2); cursor: pointer; }}
  .line {{ fill: none; stroke: var(--series-1); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }}
  .area-fill {{ fill: var(--series-1); opacity: 0.10; }}
  .end-marker {{ fill: var(--series-1); stroke: var(--surface-1); stroke-width: 2; }}
  .hit-target {{ fill: transparent; cursor: pointer; }}

  .legend {{ display: flex; gap: 16px; font-size: 12px; color: var(--text-secondary); margin-bottom: 8px; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .swatch {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; }}
  .swatch-1 {{ background: var(--series-1); }}
  .swatch-2 {{ background: var(--series-2); }}

  table.data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  table.data-table th, table.data-table td {{
    text-align: left;
    padding: 8px 10px;
    border-bottom: 1px solid var(--gridline);
  }}
  table.data-table th {{ color: var(--text-secondary); font-weight: 600; font-size: 12px; }}
  table.data-table td:last-child, table.data-table th:last-child {{ text-align: right; font-variant-numeric: tabular-nums; }}

  footer.page-footer {{ margin-top: 32px; font-size: 11px; color: var(--text-muted); }}

  #tooltip {{
    position: fixed;
    pointer-events: none;
    background: var(--text-primary);
    color: var(--surface-1);
    font-size: 12px;
    padding: 6px 9px;
    border-radius: 6px;
    opacity: 0;
    transform: translate(-50%, -120%);
    transition: opacity 0.08s ease;
    z-index: 10;
    white-space: nowrap;
  }}
</style>
</head>
<body>
<div class="viz-root">
  <div class="page">
    <header class="page-header">
      <h1>FOCUS FinOps Dashboard</h1>
      <p>AWS cost &amp; usage, FOCUS v1.4 format · generated {generated_at}</p>
    </header>

    <section class="stat-grid">
      {stat_tiles}
    </section>

    <section class="chart-grid">
      {service_chart}
      {account_chart}
    </section>

    <section class="chart-grid full">
      {trend_chart}
    </section>

    <section class="chart-grid">
      {region_chart}
      {savings_chart}
    </section>

    <section class="chart-card" style="margin-top:16px;">
      <h3>Top resources by billed cost</h3>
      <p class="chart-subtitle">Highest-spend individual resources across all accounts</p>
      {top_resources_table}
    </section>

    <footer class="page-footer">
      Generated by focus-finops from data currently loaded in the focus_cost_and_usage table.
    </footer>
  </div>
</div>
<div id="tooltip"></div>
<script>
  const tooltip = document.getElementById('tooltip');
  document.querySelectorAll('[data-tooltip]').forEach(function (el) {{
    el.addEventListener('mousemove', function (e) {{
      tooltip.textContent = el.getAttribute('data-tooltip');
      tooltip.style.left = e.clientX + 'px';
      tooltip.style.top = e.clientY + 'px';
      tooltip.style.opacity = '1';
    }});
    el.addEventListener('mouseleave', function () {{
      tooltip.style.opacity = '0';
    }});
  }});
</script>
</body>
</html>
"""


def _service_items(df) -> list[tuple[str, float]]:
    top = df.head(8)
    items = [(row.service_category, float(row.billed_cost)) for row in top.itertuples()]
    return items


def run(out_path: Path) -> Path:
    totals = queries.overall_totals()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if totals.empty or not totals.get("line_items"):
        out_path.write_text(
            "<html><body style='font-family:sans-serif;padding:40px'>"
            "<h2>No data loaded yet</h2>"
            "<p>Run <code>focus-finops ingest &lt;file.csv&gt;</code> first, then regenerate the dashboard.</p>"
            "</body></html>"
        )
        return out_path

    list_cost = float(totals["list_cost"])
    effective_cost = float(totals["effective_cost"])
    billed_cost = float(totals["billed_cost"])
    savings = list_cost - effective_cost
    savings_pct = (savings / list_cost * 100) if list_cost else 0

    stat_tiles = "".join([
        stat_tile("Total Billed Cost", fmt_money_full(billed_cost),
                   f"{totals['period_start'][:10]} to {totals['period_end'][:10]}"),
        stat_tile("Total Effective Cost", fmt_money_full(effective_cost)),
        stat_tile("Savings vs List Price", fmt_money_full(savings), f"{savings_pct:.1f}% off list"),
        stat_tile("Sub-accounts", str(int(totals["sub_accounts"]))),
        stat_tile("Services in use", str(int(totals["services"]))),
        stat_tile("Line items", f"{int(totals['line_items']):,}"),
    ])

    svc_df = queries.cost_by_service_category()
    service_chart = bar_chart(
        "Cost by service category", "Billed cost, all accounts and months",
        [(row.service_category, float(row.billed_cost)) for row in svc_df.itertuples()],
        chart_id="chart-service",
    )

    acct_df = queries.cost_by_account()
    account_chart = bar_chart(
        "Cost by account", "Billed cost per linked account",
        [(row.account, float(row.billed_cost)) for row in acct_df.itertuples()],
        chart_id="chart-account",
    )

    trend_df = queries.cost_trend_monthly()
    trend_chart = line_chart(
        "Monthly cost trend",
        "Total billed cost per calendar month (last point is the current month, month-to-date)",
        [(row.month, float(row.billed_cost)) for row in trend_df.itertuples()],
        chart_id="chart-trend",
    )

    region_df = queries.cost_by_region()
    region_chart = bar_chart(
        "Cost by region", "Billed cost per AWS region",
        [(row.region, float(row.billed_cost)) for row in region_df.itertuples()],
        chart_id="chart-region",
    )

    savings_df = queries.savings_by_commitment_type()
    savings_chart = two_series_bar_chart(
        "List vs. effective cost by coverage", "Savings Plans / Reserved Instances vs. on-demand",
        [(row.coverage, float(row.list_cost), float(row.effective_cost)) for row in savings_df.itertuples()],
        series_names=("List Cost", "Effective Cost"),
        chart_id="chart-savings",
    )

    top_df = queries.top_resources(10)
    top_rows = [
        [row.resource_name or "(unnamed)", row.service_name, row.account, fmt_money_full(row.billed_cost)]
        for row in top_df.itertuples()
    ]
    top_resources_table = data_table(["Resource", "Service", "Account", "Billed Cost"], top_rows)

    html = PAGE_TEMPLATE.format(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        stat_tiles=stat_tiles,
        service_chart=service_chart,
        account_chart=account_chart,
        trend_chart=trend_chart,
        region_chart=region_chart,
        savings_chart=savings_chart,
        top_resources_table=top_resources_table,
    )
    out_path.write_text(html)
    return out_path
