"""Builds a single-file, interactive HTML cost dashboard.

Two tabs, two different data-loading strategies:

  - **Cost Dashboard** tab: every dataset that responds to the
    Provider/Account/Application/Owner filters (the cost cube, commitment
    coverage, z-score/ML anomalies, forecast, recommendations) is fetched
    from the REST API (api.py, run via `focus-finops serve`) on every
    filter change, rather than embedded in the page -- see
    `fetchAndRenderCost()` in the generated JS. This tab requires the API
    server to be running; it shows a clear message (not a silent blank
    page) if it can't reach it.
  - **Resource Telemetry** tab: same fetch-on-demand approach, for the
    same reason (see otel_insights.py) -- fetches the correlation summary
    once per tab load and each resource's daily history only when that
    resource is selected.

Only the portfolio-wide 3-month cost prediction stays embedded as JSON at
generation time: it doesn't respond to any of the dashboard's filters, so
there's nothing to gain by fetching it per interaction.

This means the page is NOT usable fully offline -- it needs network access
for Chart.js (loaded from a CDN) on first load, and a running
`focus-finops serve` for anything beyond the prediction chart. That's a
deliberate trade-off: this dashboard used to embed every dataset as JSON
directly in the HTML, which doesn't scale (the per-resource OTel telemetry
alone was ~3MB embedded wholesale) -- the API is what lets a page ask for
only the page/filter it actually needs.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import cost_prediction, queries

CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>FinOps Framework Study - Cloud Cost Dashboard</title>
<script src="{chart_js_cdn}"></script>
<style>
  .viz-root {{
    color-scheme: light;
    --page-plane:     #f9f9f7;
    --surface-1:      #fcfcfb;
    --surface-2:      #f2f1ed;
    --text-primary:   #0b0b0b;
    --text-secondary: #52514e;
    --text-muted:     #898781;
    --gridline:       #e1e0d9;
    --border:         rgba(11,11,11,0.10);
    --accent:         #2a78d6;
    --good:           #006300;
    --status-good:      #0ca30c;
    --status-warning:   #fab219;
    --status-serious:   #ec835a;
    --status-critical:  #d03b3b;
    --shadow-card: 0 1px 2px rgba(11,11,11,0.04), 0 6px 16px rgba(11,11,11,0.05);
    --header-tint:       #eaf2fc;
    --header-tint-hover: #dceafa;
  }}
  @media (prefers-color-scheme: dark) {{
    .viz-root {{
      color-scheme: dark;
      --page-plane:     #0d0d0d;
      --surface-1:      #1a1a19;
      --surface-2:      #232322;
      --text-primary:   #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted:     #898781;
      --gridline:       #2c2c2a;
      --border:         rgba(255,255,255,0.10);
      --accent:         #3987e5;
      --good:           #0ca30c;
      --status-good:      #0ca30c;
      --status-warning:   #fab219;
      --status-serious:   #ec835a;
      --status-critical:  #e66767;
      --shadow-card: 0 1px 2px rgba(0,0,0,0.35);
      --header-tint:       #16253a;
      --header-tint-hover: #1c2f4a;
    }}
  }}

  * {{ box-sizing: border-box; }}
  body {{ margin: 0; }}
  .viz-root {{
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--page-plane);
    color: var(--text-primary);
    padding: 14px 14px 32px;
    -webkit-font-smoothing: antialiased;
  }}
  .page {{ max-width: 1440px; margin: 0 auto; }}

  .app-topbar {{
    position: sticky;
    top: 0;
    z-index: 30;
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    gap: 12px;
    padding: 10px 14px;
    margin: -14px -14px 14px;
    background: var(--surface-1);
    border-bottom: 1px solid var(--border);
    box-shadow: var(--shadow-card);
  }}
  .app-topbar-left, .footer-brand {{ display: flex; align-items: center; gap: 10px; }}
  .app-topbar-center {{
    font-size: 12.5px;
    font-weight: 750;
    letter-spacing: 0.04em;
    color: var(--accent);
    white-space: nowrap;
    padding: 4px 12px;
    border-radius: 999px;
    background: var(--header-tint);
  }}
  .app-topbar-right {{ display: flex; align-items: center; gap: 12px; justify-self: end; }}
  @media (max-width: 760px) {{ .app-topbar-center {{ display: none; }} }}
  .app-logo {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 8px;
    background: linear-gradient(135deg, var(--accent), #1baf7a);
    color: #fff;
    font-size: 12px;
    font-weight: 750;
    letter-spacing: 0.02em;
    flex-shrink: 0;
  }}
  .app-brand {{ font-size: 14.5px; font-weight: 700; letter-spacing: -0.01em; }}
  .env-badge {{
    font-size: 11px;
    font-weight: 650;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    padding: 3px 9px;
    border-radius: 999px;
    background: var(--surface-2);
    color: var(--text-secondary);
    border: 1px solid var(--border);
  }}
  .api-status {{ display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary); }}
  .api-status-dot {{ width: 7px; height: 7px; border-radius: 50%; background: var(--text-muted); flex-shrink: 0; }}
  .api-status.is-ok .api-status-dot {{ background: var(--status-good); }}
  .api-status.is-down .api-status-dot {{ background: var(--status-critical); }}

  header.study-header {{
    position: relative;
    overflow: hidden;
    text-align: center;
    padding: 7px 24px;
    margin-bottom: 10px;
    background: var(--header-tint);
    border: 1px solid var(--border);
    border-radius: 10px;
    box-shadow: var(--shadow-card);
  }}
  header.study-header::before {{
    content: '';
    position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: linear-gradient(90deg, #2a78d6, #1baf7a, #eda100, #e34948);
  }}
  header.study-header h1 {{ font-size: 12.5px; font-weight: 650; line-height: 1.4; margin: 0 0 2px; }}
  header.study-header p {{ margin: 0; color: var(--text-secondary); font-size: 11px; }}
  header.page-header {{ margin-bottom: 14px; }}
  header.page-header h1 {{ font-size: 22px; font-weight: 700; letter-spacing: -0.01em; margin: 0 0 4px; }}
  header.page-header p {{ margin: 0; color: var(--text-secondary); font-size: 13px; }}

  details.accordion {{
    margin: 14px 0 0;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--surface-2);
    box-shadow: var(--shadow-card);
    overflow: hidden;
  }}
  summary.accordion-summary {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    cursor: pointer;
    list-style: none;
    padding: 11px 18px;
    background: var(--header-tint);
    border-left: 3px solid var(--accent);
    border-bottom: 1px solid var(--border);
    font-size: 13.5px;
    font-weight: 650;
    letter-spacing: 0.01em;
    color: var(--text-primary);
    user-select: none;
    transition: background 120ms ease;
  }}
  summary.accordion-summary::-webkit-details-marker {{ display: none; }}
  summary.accordion-summary:hover {{ background: var(--header-tint-hover); }}
  details.accordion:not([open]) > summary.accordion-summary {{ border-bottom-color: transparent; }}
  .accordion-chevron {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    background: var(--surface-2);
    color: var(--text-secondary);
    flex-shrink: 0;
    transition: transform 200ms ease, background 120ms ease, color 120ms ease;
  }}
  .accordion-chevron::before {{
    content: '';
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid currentColor;
  }}
  details.accordion[open] > summary.accordion-summary .accordion-chevron {{ transform: rotate(180deg); }}
  summary.accordion-summary:hover .accordion-chevron {{ background: var(--surface-1); color: var(--accent); }}
  .accordion-body {{ padding: 12px 14px 14px; }}
  .accordion-body > .chart-card:last-child,
  .accordion-body > .chart-grid:last-child,
  .accordion-body > .chart-grid.full:last-child,
  .accordion-body > .stat-grid:last-child {{ margin-bottom: 0; }}

  .tab-bar {{
    display: flex;
    gap: 4px;
    margin-bottom: 14px;
    border-bottom: 1px solid var(--gridline);
  }}
  .tab-btn {{
    padding: 10px 18px;
    font-size: 13px;
    font-weight: 650;
    color: var(--text-secondary);
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    cursor: pointer;
    font-family: inherit;
  }}
  .tab-btn:hover {{ color: var(--text-primary); }}
  .tab-btn.active {{ color: var(--accent); border-bottom-color: var(--accent); }}
  .tab-panel[hidden] {{ display: none; }}
  #tabCost.is-loading {{ opacity: 0.6; transition: opacity 120ms ease; pointer-events: none; }}
  .api-error-banner {{
    display: none;
    padding: 14px 16px;
    margin-bottom: 14px;
    background: var(--surface-1);
    border: 1px solid var(--status-critical);
    border-radius: 10px;
    color: var(--text-primary);
    font-size: 13px;
  }}
  .api-error-banner code {{
    background: var(--surface-2);
    padding: 1px 5px;
    border-radius: 4px;
  }}

  .filter-bar {{
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    gap: 10px;
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 14px;
    margin-bottom: 14px;
    box-shadow: var(--shadow-card);
  }}
  .filter-group {{ position: relative; }}
  .filter-group summary {{
    list-style: none;
    cursor: pointer;
    font-size: 12.5px;
    padding: 6px 10px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--surface-2);
    color: var(--text-primary);
    user-select: none;
  }}
  .filter-group summary::-webkit-details-marker {{ display: none; }}
  .filter-group summary .filter-count {{ color: var(--text-secondary); }}
  .filter-options {{
    position: absolute;
    z-index: 5;
    top: calc(100% + 4px);
    left: 0;
    min-width: 220px;
    max-height: 260px;
    overflow-y: auto;
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px;
    box-shadow: 0 6px 20px rgba(0,0,0,0.15);
  }}
  .filter-option {{
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12.5px;
    padding: 4px 6px;
    border-radius: 4px;
    cursor: pointer;
  }}
  .filter-option:hover {{ background: var(--surface-2); }}
  .filter-option-actions {{
    display: flex;
    gap: 10px;
    font-size: 11.5px;
    color: var(--accent);
    padding: 2px 6px 6px;
    border-bottom: 1px solid var(--gridline);
    margin-bottom: 6px;
  }}
  .filter-option-actions span {{ cursor: pointer; }}

  .group-by {{ display: flex; align-items: center; gap: 8px; font-size: 12.5px; margin-left: auto; }}
  .group-by select {{
    font-size: 12.5px;
    padding: 6px 8px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--surface-2);
    color: var(--text-primary);
  }}
  .reset-btn {{
    font-size: 12.5px;
    padding: 6px 10px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-secondary);
    cursor: pointer;
  }}
  .reset-btn:hover {{ color: var(--text-primary); border-color: var(--text-secondary); }}

  .loading-indicator {{
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-size: 12.5px;
    color: var(--text-secondary);
  }}
  .loading-indicator[hidden] {{ display: none; }}
  .spinner {{
    width: 13px;
    height: 13px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    display: inline-block;
    animation: focus-finops-spin 700ms linear infinite;
  }}
  @keyframes focus-finops-spin {{
    to {{ transform: rotate(360deg); }}
  }}

  .stat-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
    margin-bottom: 8px;
  }}
  .stat-tile {{
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-top: 3px solid var(--gridline);
    border-radius: 10px;
    padding: 13px 16px;
    box-shadow: var(--shadow-card);
    transition: transform 120ms ease, box-shadow 120ms ease;
  }}
  .stat-tile.tone-accent   {{ border-top-color: var(--accent); }}
  .stat-tile.tone-good     {{ border-top-color: var(--status-good); }}
  .stat-tile.tone-warning  {{ border-top-color: var(--status-warning); }}
  .stat-tile.tone-critical {{ border-top-color: var(--status-critical); }}
  .stat-label {{ font-size: 12px; color: var(--text-secondary); margin-bottom: 6px; }}
  .stat-value {{ font-size: 25px; font-weight: 650; letter-spacing: -0.01em; }}
  .stat-sub {{ font-size: 12px; color: var(--text-muted); margin-top: 4px; }}
  .stat-sub.tone-good     {{ color: var(--status-good); }}
  .stat-sub.tone-critical {{ color: var(--status-critical); font-weight: 600; }}

  .chart-grid {{
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 12px;
    margin-bottom: 12px;
    align-items: stretch;
  }}
  .chart-grid.full {{ grid-template-columns: 1fr; }}
  .chart-grid > .chart-card {{ margin-bottom: 0; }}
  @media (max-width: 860px) {{ .chart-grid {{ grid-template-columns: 1fr; }} }}

  .chart-card {{
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 16px 12px;
    min-width: 0;
    box-shadow: var(--shadow-card);
    margin-bottom: 12px;
  }}
  .chart-card h3 {{ font-size: 14px; font-weight: 650; margin: 0 0 2px; }}
  .chart-subtitle {{ font-size: 12px; color: var(--text-secondary); margin: 0 0 8px; line-height: 1.5; }}
  .ml-subsection {{ margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--gridline); }}
  .ml-subsection:first-of-type {{ margin-top: 10px; padding-top: 0; border-top: none; }}
  .ml-subsection h4 {{ font-size: 13px; font-weight: 650; margin: 0 0 4px; }}
  .ml-subsection .chart-subtitle {{ margin: 0 0 8px; }}
  .empty {{ color: var(--text-muted); font-size: 13px; padding: 20px 0; }}
  .chart-canvas-wrap {{ position: relative; width: 100%; height: 320px; }}
  .chart-canvas-wrap.tall {{ height: 380px; }}
  .chart-canvas-wrap.short {{ height: 220px; }}

  table.data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  table.data-table th, table.data-table td {{
    text-align: left;
    padding: 7px 10px;
    border-bottom: 1px solid var(--gridline);
  }}
  table.data-table th {{
    color: var(--text-secondary);
    font-weight: 650;
    font-size: 11px;
    letter-spacing: 0.03em;
    text-transform: uppercase;
  }}
  table.data-table td:last-child, table.data-table th:last-child {{ text-align: right; font-variant-numeric: tabular-nums; }}
  table.data-table tbody tr:nth-child(even) {{ background: var(--surface-2); }}
  table.data-table tbody tr:hover {{ background: var(--border); }}
  .badge {{ font-weight: 650; font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.02em; }}
  .badge.critical {{ color: var(--status-critical); }}
  .badge.warning {{ color: var(--status-warning); }}
  .badge.good {{ color: var(--status-good); }}
  #otelCorrelationBody .badge {{ text-transform: none; letter-spacing: normal; font-size: 12.5px; white-space: nowrap; }}

  .app-footer {{
    margin: 28px -14px -32px;
    padding: 22px 14px 18px;
    background: var(--surface-2);
    border-top: 1px solid var(--border);
  }}
  .footer-inner {{ max-width: 1440px; margin: 0 auto; }}
  .footer-grid {{
    display: grid;
    grid-template-columns: 1.4fr 1fr 1fr;
    gap: 24px;
    margin-bottom: 16px;
  }}
  .footer-col h4 {{
    font-size: 12px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--text-secondary); margin: 0 0 8px;
  }}
  .footer-col p {{ margin: 0; font-size: 12.5px; color: var(--text-secondary); line-height: 1.6; }}
  .footer-col ul {{ list-style: none; margin: 0; padding: 0; }}
  .footer-col li {{ font-size: 12.5px; color: var(--text-secondary); line-height: 1.9; }}
  .footer-col li code {{ background: var(--surface-1); padding: 1px 5px; border-radius: 4px; font-size: 11.5px; }}
  a.footer-link {{ color: var(--text-secondary); text-decoration: none; }}
  a.footer-link:hover {{ color: var(--accent); text-decoration: underline; }}
  .footer-bottom {{
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
    padding-top: 14px;
    border-top: 1px solid var(--gridline);
    font-size: 11px;
    color: var(--text-muted);
  }}
  @media (max-width: 720px) {{ .footer-grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="viz-root">
  <header class="app-topbar">
    <div class="app-topbar-left">
      <span class="app-logo">FF</span>
      <span class="app-brand">FOCUS FinOps</span>
    </div>
    <div class="app-topbar-center">MBA Capstone Project &middot; FinOps Research</div>
    <div class="app-topbar-right">
      <span class="env-badge">Local environment</span>
      <span class="api-status" id="apiStatusBadge" title="{api_base}"><span class="api-status-dot"></span><span id="apiStatusText">Checking API…</span></span>
    </div>
  </header>
  <div class="page">
    <header class="study-header">
      <h1>A Study on the Development of a FinOps Framework to Optimize and Curb Runaway Cloud Computing (AWS / Azure / GCP) Expenditures</h1>
      <p>Supporting dashboard &mdash; FOCUS-based multi-cloud cost &amp; usage analysis</p>
    </header>

    <header class="page-header">
      <h1>FOCUS FinOps Dashboard</h1>
      <p>Multi-cloud cost &amp; usage (AWS + Azure + GCP), FOCUS v1.4 format · {period_start} to {period_end} · generated {generated_at}</p>
    </header>

    <div class="tab-bar">
      <button class="tab-btn active" data-tab="tabCost" type="button">Cost Dashboard</button>
      <button class="tab-btn" data-tab="tabTelemetry" type="button">Resource Telemetry</button>
    </div>

    <div class="tab-panel" id="tabCost">
    <div class="api-error-banner" id="costApiError"></div>
    <div class="filter-bar" id="filterBar">
      <details class="filter-group"><summary>Provider <span class="filter-count" id="count-provider"></span></summary>
        <div class="filter-options" id="options-provider"></div>
      </details>
      <details class="filter-group"><summary>Account <span class="filter-count" id="count-account"></span></summary>
        <div class="filter-options" id="options-account"></div>
      </details>
      <details class="filter-group"><summary>Application <span class="filter-count" id="count-application"></span></summary>
        <div class="filter-options" id="options-application"></div>
      </details>
      <details class="filter-group"><summary>Owner <span class="filter-count" id="count-owner"></span></summary>
        <div class="filter-options" id="options-owner"></div>
      </details>
      <button class="reset-btn" id="resetBtn" type="button">Reset filters</button>
      <span class="loading-indicator" id="costLoadingIndicator" hidden><span class="spinner"></span>Loading…</span>
      <div class="group-by">
        <label for="groupBySelect">Group by</label>
        <select id="groupBySelect">
          <option value="provider">Provider</option>
          <option value="account">Account</option>
          <option value="application">Application</option>
          <option value="owner">Owner</option>
          <option value="service_category" selected>Service Category</option>
          <option value="service_name">Service</option>
          <option value="region">Region</option>
        </select>
      </div>
    </div>

    <details class="accordion" open>
    <summary class="accordion-summary"><span>Cost overview</span><span class="accordion-chevron"></span></summary>
    <div class="accordion-body">
    <section class="stat-grid">
      <div class="stat-tile tone-accent"><div class="stat-label">Total Billed Cost</div><div class="stat-value" id="kpi-billed">-</div></div>
      <div class="stat-tile"><div class="stat-label">Total Effective Cost</div><div class="stat-value" id="kpi-effective">-</div></div>
      <div class="stat-tile tone-good"><div class="stat-label">Savings vs List Price</div><div class="stat-value" id="kpi-savings">-</div><div class="stat-sub" id="kpi-savings-pct"></div></div>
      <div class="stat-tile"><div class="stat-label">Providers</div><div class="stat-value" id="kpi-providers">-</div></div>
      <div class="stat-tile"><div class="stat-label">Accounts</div><div class="stat-value" id="kpi-accounts">-</div></div>
      <div class="stat-tile"><div class="stat-label">Applications</div><div class="stat-value" id="kpi-applications">-</div></div>
      <div class="stat-tile"><div class="stat-label">Line items</div><div class="stat-value" id="kpi-lineitems">-</div></div>
    </section>
    </div>
    </details>

    <details class="accordion" open>
    <summary class="accordion-summary"><span>Cost anomaly &amp; risk signals at a glance</span><span class="accordion-chevron"></span></summary>
    <div class="accordion-body">
    <section class="stat-grid">
      <div class="stat-tile" id="tile-zscore">
        <div class="stat-label">Z-score anomalies (7-day rolling)</div>
        <div class="stat-value" id="kpi-zscore">-</div>
        <div class="stat-sub" id="kpi-zscore-sub"></div>
      </div>
      <div class="stat-tile" id="tile-anomalies">
        <div class="stat-label">Cost anomalies flagged (IsolationForest)</div>
        <div class="stat-value" id="kpi-anomalies">-</div>
        <div class="stat-sub" id="kpi-anomalies-sub"></div>
      </div>
      <div class="stat-tile" id="tile-overrun">
        <div class="stat-label">Accounts at overrun risk</div>
        <div class="stat-value" id="kpi-overrun">-</div>
        <div class="stat-sub" id="kpi-overrun-sub"></div>
      </div>
      <div class="stat-tile tone-accent" id="tile-candidates">
        <div class="stat-label">Commitment candidates found</div>
        <div class="stat-value" id="kpi-candidates">-</div>
        <div class="stat-sub" id="kpi-candidates-sub"></div>
      </div>
    </section>
    </div>
    </details>

    <details class="accordion" id="accordionBreakdown" open>
    <summary class="accordion-summary"><span>Cost breakdown &amp; trends</span><span class="accordion-chevron"></span></summary>
    <div class="accordion-body">
    <section class="chart-grid">
      <div class="chart-card">
        <h3 id="breakdownTitle">Cost by Service Category</h3>
        <p class="chart-subtitle">Billed cost for the current filter selection</p>
        <div class="chart-canvas-wrap tall"><canvas id="breakdownChart"></canvas></div>
      </div>
      <div class="chart-card">
        <h3>Cost by provider</h3>
        <p class="chart-subtitle">Multi-cloud split, current filter selection</p>
        <div class="chart-canvas-wrap tall"><canvas id="providerChart"></canvas></div>
      </div>
    </section>

    <section class="chart-grid full">
      <div class="chart-card">
        <h3>Cost by Account</h3>
        <p class="chart-subtitle">Billed cost per account for the current filter selection (top 12 + Other) -- independent of the "Group by" selector above</p>
        <div class="chart-canvas-wrap tall"><canvas id="accountChart"></canvas></div>
      </div>
    </section>

    <section class="chart-grid full">
      <div class="chart-card">
        <h3 id="trendTitle">Monthly cost trend by Service Category</h3>
        <p class="chart-subtitle">Total billed cost per calendar month, split by the current "Group by" dimension (top 6 + Other)</p>
        <div class="chart-canvas-wrap"><canvas id="trendChart"></canvas></div>
      </div>
    </section>

    <section class="chart-card">
      <h3>Top Resources by Billed Cost</h3>
      <p class="chart-subtitle">Highest-spend individual resources for the current filter selection (excludes account-level charges like tax/support)</p>
      <div style="overflow-x:auto">
        <table class="data-table">
          <thead><tr><th>Resource</th><th>Service</th><th>Account</th><th>Provider</th><th>Billed Cost</th></tr></thead>
          <tbody id="topResourcesBody"></tbody>
        </table>
      </div>
    </section>
    </div>
    </details>

    <details class="accordion" open>
    <summary class="accordion-summary"><span>Commitment &amp; reservation analysis</span><span class="accordion-chevron"></span></summary>
    <div class="accordion-body">
    <section class="chart-card" id="commitmentSection">
      <h3>Commitment / reservation coverage</h3>
      <p class="chart-subtitle">Share of each committed resource's usage billed at the discounted commitment rate
        (Savings Plans / Reserved Instances / Committed Use Discounts) for the current filter selection, worst-covered
        first. "Coverage" is usage billed at the committed rate, not used-vs-purchased utilization -- FOCUS usage rows
        don't carry the commitment's purchased/entitled quantity, only what was applied.</p>
      <div style="overflow-x:auto">
        <table class="data-table">
          <thead><tr><th>Provider</th><th>Type</th><th>Resource</th><th>Consumed</th><th>Covered</th><th>Coverage</th><th>Savings</th></tr></thead>
          <tbody id="commitmentBody"></tbody>
        </table>
      </div>
    </section>
    </div>
    </details>

    <details class="accordion" open>
    <summary class="accordion-summary"><span>Statistical cost anomaly detection (Z-score)</span><span class="accordion-chevron"></span></summary>
    <div class="accordion-body">
    <section class="chart-card" id="zscoreSection">
      <h3>Rolling z-score anomalies</h3>
      <p class="chart-subtitle">Per service, each day's cost is compared to the trailing 7-day mean and standard
        deviation of the days <em>before</em> it (never its own value). A z-score of +2.0 or higher is a warning; +3.0
        or higher is critical -- the standard FinOps rule of thumb for a same-day spend spike. Only spikes are
        flagged; an unusually quiet day is not treated as an issue. For the current filter selection.</p>
      <div style="overflow-x:auto">
        <table class="data-table">
          <thead><tr><th>Provider</th><th>Account</th><th>Service</th><th>Day</th><th>Cost</th><th>7-Day Avg</th><th>7-Day StdDev</th><th>Z-Score</th><th>Severity</th></tr></thead>
          <tbody id="zscoreBody"></tbody>
        </table>
      </div>
    </section>
    </div>
    </details>

    <details class="accordion" open>
    <summary class="accordion-summary"><span>Machine learning-based optimization insights</span><span class="accordion-chevron"></span></summary>
    <div class="accordion-body">
    <section class="chart-card" id="mlSection">
      <h3>ML-identified cost optimization opportunities</h3>
      <p class="chart-subtitle">Anomaly detection (IsolationForest), spend forecasting (linear trend), and commitment
        candidates (KMeans clustering), for the current filter selection. These are decision-support signals for a
        FinOps review, not automated actions. The z-score method above is a simpler, complementary technique -- the
        two can and do flag different days.</p>

      <div class="ml-subsection">
        <h4>Cost anomalies</h4>
        <p class="chart-subtitle">Daily cost outliers per resource, judged against that resource's own history</p>
        <div style="overflow-x:auto">
          <table class="data-table">
            <thead><tr><th>Provider</th><th>Account</th><th>Service</th><th>Resource</th><th>Day</th><th>Cost</th><th>Baseline</th><th>vs. Baseline</th></tr></thead>
            <tbody id="anomalyBody"></tbody>
          </table>
        </div>
      </div>

      <div class="ml-subsection">
        <h4>Spend forecast / overrun risk</h4>
        <p class="chart-subtitle">Next-month cost projected from each combination's own monthly trend</p>
        <div style="overflow-x:auto">
          <table class="data-table">
            <thead><tr><th>Provider</th><th>Account</th><th>Application</th><th>Service Category</th><th>Last Month</th><th>Forecast</th><th>Change</th></tr></thead>
            <tbody id="forecastBody"></tbody>
          </table>
        </div>
      </div>

      <div class="ml-subsection">
        <h4>Commitment candidates</h4>
        <p class="chart-subtitle">Uncommitted Compute/Database resources with steady, high-volume usage -- good Savings Plan/RI/CUD fits</p>
        <div style="overflow-x:auto">
          <table class="data-table">
            <thead><tr><th>Provider</th><th>Account</th><th>Service</th><th>Resource</th><th>Avg Daily Cost</th><th>Volatility</th><th>Total Cost</th></tr></thead>
            <tbody id="recommendBody"></tbody>
          </table>
        </div>
      </div>
    </section>
    </div>
    </details>

    <details class="accordion" id="accordionPrediction" open>
    <summary class="accordion-summary"><span>3-month cost prediction</span><span class="accordion-chevron"></span></summary>
    <div class="accordion-body">
    <section class="chart-card" id="predictionSection">
      <h3>Blended cost prediction, next 3 months</h3>
      <p class="chart-subtitle">A 3-method blend, one per cost segment: a <strong>fixed-fee model</strong> for
        committed/reserved usage (Savings Plans, Reserved Instances, Committed Use Discounts -- treated as a stable
        recurring charge), <strong>linear regression</strong> for Storage growth, and <strong>Prophet</strong>
        (trend + weekly seasonality) for Compute/Networking/everything else. The three are combined by summing
        simulated sample paths (not by adding parametric intervals), so the shaded band reflects all three sources of
        uncertainty together. Shown as an 80% prediction interval. Portfolio-wide across all providers/accounts --
        not affected by the filters above.</p>
      <div class="chart-canvas-wrap tall"><canvas id="predictionChart"></canvas></div>
      <div style="overflow-x:auto; margin-top:14px;">
        <table class="data-table">
          <thead><tr><th>Month</th><th>Forecast</th><th>80% Low</th><th>80% High</th></tr></thead>
          <tbody id="predictionBody"></tbody>
        </table>
      </div>
    </section>
    </div>
    </details>
    </div>

    <div class="tab-panel" id="tabTelemetry" hidden>
      <div class="filter-bar">
        <div style="display:flex; align-items:center; gap:8px; font-size:12.5px;">
          <label for="otelTypeSelect">Resource type</label>
          <select id="otelTypeSelect" style="font-size:12.5px; padding:6px 8px; border-radius:6px; border:1px solid var(--border); background:var(--surface-2); color:var(--text-primary);"></select>
        </div>
        <span class="loading-indicator" id="otelLoadingIndicator" hidden><span class="spinner"></span>Loading…</span>
      </div>

      <details class="accordion" open>
      <summary class="accordion-summary"><span>Cost vs. utilization signals at a glance</span><span class="accordion-chevron"></span></summary>
      <div class="accordion-body">
      <section class="stat-grid">
        <div class="stat-tile"><div class="stat-label">Resources with telemetry</div><div class="stat-value" id="kpi-otel-resources">-</div></div>
        <div class="stat-tile tone-good"><div class="stat-label">Cost tracks usage</div><div class="stat-value" id="kpi-otel-healthy">-</div></div>
        <div class="stat-tile tone-warning"><div class="stat-label">Rightsizing candidates</div><div class="stat-value" id="kpi-otel-rightsizing">-</div></div>
        <div class="stat-tile tone-critical"><div class="stat-label">Investigate</div><div class="stat-value" id="kpi-otel-investigate">-</div></div>
      </section>
      </div>
      </details>

      <details class="accordion" id="accordionOtelDetail" open>
      <summary class="accordion-summary"><span>Cost vs. utilization by resource</span><span class="accordion-chevron"></span></summary>
      <div class="accordion-body">
      <section class="chart-card">
        <h3>Resource detail</h3>
        <p class="chart-subtitle">Simulated OpenTelemetry utilization metrics -- CPU/memory for Compute and
          Databases, filesystem for Storage, network I/O and client errors for Networking -- correlated against real
          FOCUS cost for the same resource and day. Not affected by the Cost Dashboard filters -- use the Resource
          type selector above and the resource picker below instead.</p>
        <div style="margin-bottom:10px;">
          <label for="otelResourceSelect" style="font-size:12.5px; color:var(--text-secondary); margin-right:8px;">Resource</label>
          <select id="otelResourceSelect" style="font-size:12.5px; padding:6px 8px; border-radius:6px; border:1px solid var(--border); background:var(--surface-2); color:var(--text-primary); max-width:100%;"></select>
        </div>
        <h4 style="font-size:13px; font-weight:650; margin:0 0 4px;">Daily billed cost</h4>
        <div class="chart-canvas-wrap short"><canvas id="otelCostChart"></canvas></div>
        <h4 style="font-size:13px; font-weight:650; margin:14px 0 4px;">Daily utilization</h4>
        <div class="chart-canvas-wrap short"><canvas id="otelUtilChart"></canvas></div>
      </section>
      </div>
      </details>

      <details class="accordion" open>
      <summary class="accordion-summary"><span>Cost / utilization correlation by resource</span><span class="accordion-chevron"></span></summary>
      <div class="accordion-body">
      <section class="chart-card">
        <h3>Classification</h3>
        <p class="chart-subtitle">Pearson correlation between each resource's daily cost and its primary utilization
          metric. Low/negative correlation with low utilization flags a rightsizing candidate; low/negative
          correlation with normal utilization flags a cost pattern worth investigating independently of usage. This
          demonstrates the correlation method against simulated telemetry -- not a finding about real infrastructure.</p>
        <div style="overflow-x:auto">
          <table class="data-table">
            <thead><tr><th>Provider</th><th>Account</th><th>Resource</th><th>Category</th><th>Avg Utilization</th><th>Correlation</th><th>Classification</th></tr></thead>
            <tbody id="otelCorrelationBody"></tbody>
          </table>
        </div>
      </section>
      </div>
      </details>
    </div>
  </div>

  <footer class="app-footer">
    <div class="footer-inner">
      <div class="footer-grid">
        <div class="footer-col">
          <div class="footer-brand">
            <span class="app-logo">FF</span>
            <span class="app-brand">FOCUS FinOps</span>
          </div>
          <p style="margin-top:8px;">A FOCUS v1.4-based multi-cloud cost &amp; usage dashboard, built to support MBA
            capstone research on curbing runaway cloud spend.</p>
        </div>
        <div class="footer-col">
          <h4>Dashboard</h4>
          <ul>
            <li><a class="footer-link" href="#" data-tab="tabCost">Cost Dashboard</a></li>
            <li><a class="footer-link" href="#" data-tab="tabTelemetry">Resource Telemetry</a></li>
          </ul>
        </div>
        <div class="footer-col">
          <h4>Data</h4>
          <ul>
            <li>Period: {period_start} to {period_end}</li>
            <li>Generated: {generated_at}</li>
            <li>API: <code>{api_base}</code></li>
          </ul>
        </div>
      </div>
      <div class="footer-bottom">
        <span>&copy; {generated_year} FOCUS FinOps &middot; MBA Capstone Project</span>
        <span>Charts render client-side via Chart.js &middot; Data via FOCUS v1.4</span>
      </div>
    </div>
  </footer>
</div>
<script>
// The Cost Dashboard tab's data is NOT embedded either (as of this
// version) -- every dataset that responds to the Provider/Account/
// Application/Owner filters (the cube, commitment coverage, z-score/ML
// anomalies, forecast, recommendations) is fetched from the API on every
// filter change instead, mirroring the Resource Telemetry tab. Only the
// 3-month prediction stays embedded: it's portfolio-wide and never
// changes with these filters, so there's nothing to gain by fetching it
// per interaction.
const PREDICTION_DAILY = {prediction_daily_json};
const PREDICTION_MONTHLY = {prediction_monthly_json};

// Resource Telemetry tab data is NOT embedded -- it's fetched on demand
// from the API (`focus-finops serve`) instead, since a resource's full
// daily history for every resource at once is the single largest dataset
// this dashboard would otherwise carry (~3MB for this project's synthetic
// data). See initOtelTab()/renderOtelCharts() below.
const API_BASE = "{api_base}";
let otelCorrelationData = [];  // small (one row per resource); fetched once per tab load

// Validated categorical palette (CVD-safe in fixed order; see the dataviz
// skill's palette reference) -- light/dark variants of the same 8 hues.
const PALETTE_LIGHT = ['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#008300','#4a3aa7','#e34948'];
const PALETTE_DARK  = ['#3987e5','#d95926','#199e70','#c98500','#d55181','#008300','#9085e9','#e66767'];
function isDarkMode() {{
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
}}
function paletteColor(i) {{
  const p = isDarkMode() ? PALETTE_DARK : PALETTE_LIGHT;
  return p[i % p.length];
}}

const DIM_LABELS = {{
  provider: 'Provider', account: 'Account', application: 'Application', owner: 'Owner',
  service_category: 'Service Category', service_name: 'Service', region: 'Region', month: 'Month',
}};
const FILTER_DIMS = ['provider', 'account', 'application', 'owner'];

function money(v) {{
  const sign = v < 0 ? '-' : '';
  const a = Math.abs(v);
  if (a >= 1e6) return sign + '$' + (a/1e6).toFixed(2) + 'M';
  if (a >= 1e3) return sign + '$' + (a/1e3).toFixed(1) + 'K';
  return sign + '$' + a.toFixed(2);
}}
function moneyFull(v) {{
  const sign = v < 0 ? '-' : '';
  return sign + '$' + Math.abs(v).toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
}}
function esc(s) {{
  const div = document.createElement('div');
  div.textContent = String(s);
  return div.innerHTML;
}}

function setLoading(indicatorId, on) {{
  const el = document.getElementById(indicatorId);
  if (el) el.hidden = !on;
}}

// Populated once from an unfiltered /api/cost/cube fetch (see
// initCostDashboard()) -- the full universe of values for each filter
// checkbox group. Filtering itself now happens server-side (each fetch
// sends the current selection as repeated query params), so there's no
// client-side "filteredX()" step left for the fetched datasets -- what
// comes back from the API is already exactly what should be shown.
let allDimValues = {{ provider: [], account: [], application: [], owner: [] }};
function distinctValuesFrom(rows, dim) {{
  return [...new Set(rows.map(r => r[dim]))].sort();
}}

const state = {{ filters: {{}}, groupBy: 'service_category' }};

function filterQueryParams(limit) {{
  const qs = new URLSearchParams();
  FILTER_DIMS.forEach(dim => {{
    (state.filters[dim] || new Set()).forEach(v => qs.append(dim, v));
  }});
  qs.set('limit', limit || 2000);
  return qs;
}}

async function costFetchItems(path, limit) {{
  const res = await fetch(API_BASE + path + '?' + filterQueryParams(limit).toString());
  if (!res.ok) throw new Error('API returned ' + res.status);
  const body = await res.json();
  return body.items;
}}

function costApiUnreachableMessage() {{
  return 'Could not reach the API at ' + API_BASE + '. Start it with '
    + '<code>focus-finops serve</code>, then reload this page.';
}}

function showCostApiError() {{
  const el = document.getElementById('costApiError');
  el.innerHTML = costApiUnreachableMessage();
  el.style.display = 'block';
}}

function hideCostApiError() {{
  document.getElementById('costApiError').style.display = 'none';
}}

function groupSum(rows, dim, key) {{
  const m = new Map();
  for (const r of rows) {{ m.set(r[dim], (m.get(r[dim]) || 0) + r[key]); }}
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}}

function topNWithOther(pairs, n) {{
  if (pairs.length <= n) return pairs;
  const top = pairs.slice(0, n);
  const otherSum = pairs.slice(n).reduce((s, [, v]) => s + v, 0);
  top.push(['Other', otherSum]);
  return top;
}}

// --- Filter UI -----------------------------------------------------
function renderFilterOptions() {{
  FILTER_DIMS.forEach(dim => {{
    const container = document.getElementById('options-' + dim);
    const values = allDimValues[dim];
    const actions = document.createElement('div');
    actions.className = 'filter-option-actions';
    actions.innerHTML = '<span data-act="all">Select all</span><span data-act="none">Clear</span>';
    actions.querySelector('[data-act=all]').onclick = () => {{ state.filters[dim] = new Set(values); fetchAndRenderCost(); }};
    actions.querySelector('[data-act=none]').onclick = () => {{ state.filters[dim] = new Set(); fetchAndRenderCost(); }};
    container.appendChild(actions);
    values.forEach(v => {{
      const row = document.createElement('label');
      row.className = 'filter-option';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = state.filters[dim].has(v);
      cb.onchange = () => {{
        if (cb.checked) state.filters[dim].add(v); else state.filters[dim].delete(v);
        fetchAndRenderCost();
      }};
      row.appendChild(cb);
      row.appendChild(document.createTextNode(v));
      container.appendChild(row);
    }});
  }});
}}

function updateFilterCounts() {{
  FILTER_DIMS.forEach(dim => {{
    const total = allDimValues[dim].length;
    const selected = state.filters[dim].size;
    document.getElementById('count-' + dim).textContent =
      selected === total ? '(all)' : '(' + selected + '/' + total + ')';
    document.querySelectorAll('#options-' + dim + ' input[type=checkbox]').forEach((cb, i) => {{
      const v = allDimValues[dim][i];
      cb.checked = state.filters[dim].has(v);
    }});
  }});
}}

// --- KPIs ------------------------------------------------------------
function renderKPIs(rows) {{
  const billed = rows.reduce((s, r) => s + r.billed_cost, 0);
  const effective = rows.reduce((s, r) => s + r.effective_cost, 0);
  const list = rows.reduce((s, r) => s + r.list_cost, 0);
  const lineItems = rows.reduce((s, r) => s + r.line_items, 0);
  const savings = list - effective;
  const savingsPct = list ? (savings / list * 100) : 0;

  document.getElementById('kpi-billed').textContent = moneyFull(billed);
  document.getElementById('kpi-effective').textContent = moneyFull(effective);
  document.getElementById('kpi-savings').textContent = moneyFull(savings);
  document.getElementById('kpi-savings-pct').textContent = savingsPct.toFixed(1) + '% off list';
  document.getElementById('kpi-providers').textContent = new Set(rows.map(r => r.provider)).size;
  document.getElementById('kpi-accounts').textContent = new Set(rows.map(r => r.account)).size;
  document.getElementById('kpi-applications').textContent = new Set(rows.map(r => r.application)).size;
  document.getElementById('kpi-lineitems').textContent = lineItems.toLocaleString();
}}

function setTone(tileId, tone) {{
  const tile = document.getElementById(tileId);
  tile.classList.remove('tone-good', 'tone-warning', 'tone-critical', 'tone-accent');
  if (tone) tile.classList.add(tone);
}}

function renderMlKPIs(zAnomalies, anomalies, forecast, recommendations) {{
  const zCritical = zAnomalies.filter(r => r.severity === 'critical').length;
  const zWarning = zAnomalies.length - zCritical;
  document.getElementById('kpi-zscore').textContent = zAnomalies.length.toLocaleString();
  document.getElementById('kpi-zscore-sub').textContent = zAnomalies.length
    ? `${{zCritical}} critical (z≥3.0), ${{zWarning}} warning (z≥2.0)`
    : 'no spikes above the 7-day baseline';
  setTone('tile-zscore', zCritical ? 'tone-critical' : (zWarning ? 'tone-warning' : 'tone-good'));

  document.getElementById('kpi-anomalies').textContent = anomalies.length.toLocaleString();
  if (anomalies.length) {{
    document.getElementById('kpi-anomalies-sub').textContent = 'review recommended';
    document.getElementById('kpi-anomalies-sub').className = 'stat-sub tone-critical';
    setTone('tile-anomalies', 'tone-critical');
  }} else {{
    document.getElementById('kpi-anomalies-sub').textContent = 'none detected';
    document.getElementById('kpi-anomalies-sub').className = 'stat-sub tone-good';
    setTone('tile-anomalies', 'tone-good');
  }}

  const riskyAccounts = new Set(forecast.filter(r => r.overrun_risk).map(r => r.provider + '||' + r.account));
  document.getElementById('kpi-overrun').textContent = riskyAccounts.size.toLocaleString();
  if (riskyAccounts.size) {{
    document.getElementById('kpi-overrun-sub').textContent = 'forecast +15% or more next month';
    document.getElementById('kpi-overrun-sub').className = 'stat-sub tone-critical';
    setTone('tile-overrun', 'tone-critical');
  }} else {{
    document.getElementById('kpi-overrun-sub').textContent = 'no overrun risk detected';
    document.getElementById('kpi-overrun-sub').className = 'stat-sub tone-good';
    setTone('tile-overrun', 'tone-good');
  }}

  const candidates = recommendations.filter(r => r.recommended);
  document.getElementById('kpi-candidates').textContent = candidates.length.toLocaleString();
  document.getElementById('kpi-candidates-sub').textContent = candidates.length
    ? 'steady, high-volume resources'
    : 'none for this selection';
}}

// --- Charts ------------------------------------------------------------
let breakdownChart, trendChart, providerChart, accountChart, predictionChart;

function axisColors() {{
  const isDark = isDarkMode();
  return {{
    text: isDark ? '#c3c2b7' : '#52514e',
    grid: isDark ? '#2c2c2a' : '#e1e0d9',
    surface: isDark ? '#1a1a19' : '#fcfcfb',
    surface2: isDark ? '#232322' : '#f2f1ed',
  }};
}}

function tooltipStyle(colors) {{
  return {{
    backgroundColor: colors.surface2,
    titleColor: colors.text,
    bodyColor: colors.text,
    borderColor: colors.grid,
    borderWidth: 1,
    cornerRadius: 6,
    padding: 10,
    boxPadding: 4,
  }};
}}

function horizontalBarConfig(pairs, colors) {{
  return {{
    type: 'bar',
    data: {{
      labels: pairs.map(p => p[0]),
      datasets: [{{
        label: 'Billed Cost', data: pairs.map(p => p[1]),
        backgroundColor: pairs.map((_, i) => paletteColor(i)),
        borderRadius: 4, borderSkipped: false, maxBarThickness: 22,
      }}],
    }},
    options: {{
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{ ...tooltipStyle(colors), callbacks: {{ label: (c) => moneyFull(c.parsed.x) }} }},
      }},
      scales: {{
        x: {{ ticks: {{ color: colors.text, callback: (v) => money(v) }}, grid: {{ color: colors.grid }}, border: {{ color: colors.grid }} }},
        y: {{ ticks: {{ color: colors.text }}, grid: {{ display: false }}, border: {{ color: colors.grid }} }},
      }},
    }},
  }};
}}

function renderBreakdownChart(rows) {{
  const dim = state.groupBy;
  document.getElementById('breakdownTitle').textContent = 'Cost by ' + DIM_LABELS[dim];
  const pairs = topNWithOther(groupSum(rows, dim, 'billed_cost'), 12);
  const cfg = horizontalBarConfig(pairs, axisColors());
  if (breakdownChart) {{ breakdownChart.data = cfg.data; breakdownChart.options = cfg.options; breakdownChart.update(); }}
  else {{ breakdownChart = new Chart(document.getElementById('breakdownChart'), cfg); }}
}}

function renderAccountChart(rows) {{
  const pairs = topNWithOther(groupSum(rows, 'account', 'billed_cost'), 12);
  const cfg = horizontalBarConfig(pairs, axisColors());
  if (accountChart) {{ accountChart.data = cfg.data; accountChart.options = cfg.options; accountChart.update(); }}
  else {{ accountChart = new Chart(document.getElementById('accountChart'), cfg); }}
}}

// Portfolio-wide, filter-independent -- computed once, not part of render().
function renderPredictionChart() {{
  const rows = PREDICTION_DAILY;
  const tbody = document.getElementById('predictionBody');
  if (!rows.length) {{
    document.getElementById('predictionChart').closest('.chart-canvas-wrap').outerHTML = '<p class="empty">Not enough history to forecast.</p>';
    tbody.innerHTML = '';
    return;
  }}

  const labels = rows.map(r => r.date);
  const actual = rows.map(r => r.actual);
  const forecast = rows.map(r => r.forecast);
  const lower = rows.map(r => r.lower);
  const upper = rows.map(r => r.upper);

  // Splice the forecast/band onto the last actual point so the two lines
  // visually meet with no gap -- same real value, not a fabricated point.
  let lastActualIdx = -1;
  actual.forEach((v, i) => {{ if (v !== null) lastActualIdx = i; }});
  if (lastActualIdx >= 0 && forecast[lastActualIdx] === null) {{
    forecast[lastActualIdx] = actual[lastActualIdx];
    lower[lastActualIdx] = actual[lastActualIdx];
    upper[lastActualIdx] = actual[lastActualIdx];
  }}

  const colors = axisColors();
  const lineColor = paletteColor(0);
  const bandColor = isDarkMode() ? 'rgba(57,135,229,0.20)' : 'rgba(42,120,214,0.14)';

  const cfg = {{
    type: 'line',
    data: {{
      labels,
      datasets: [
        {{ label: 'Upper bound (90th pct)', data: upper, borderWidth: 0, pointRadius: 0, fill: false, spanGaps: true }},
        {{ label: 'Lower bound (10th pct)', data: lower, borderWidth: 0, pointRadius: 0, fill: '-1', backgroundColor: bandColor, spanGaps: true }},
        {{ label: 'Forecast (median)', data: forecast, borderColor: lineColor, borderWidth: 2, borderDash: [6, 4], pointRadius: 0, fill: false, spanGaps: true }},
        {{ label: 'Actual', data: actual, borderColor: lineColor, borderWidth: 2, pointRadius: 0, fill: false, spanGaps: true }},
      ],
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{
          labels: {{
            color: colors.text, usePointStyle: true, pointStyle: 'circle', boxWidth: 8, boxHeight: 8, padding: 16,
            filter: (item) => item.text === 'Actual' || item.text === 'Forecast (median)',
          }},
        }},
        tooltip: {{ ...tooltipStyle(colors), callbacks: {{ label: (c) => c.dataset.label + ': ' + moneyFull(c.parsed.y) }} }},
      }},
      scales: {{
        x: {{ ticks: {{ color: colors.text, maxTicksLimit: 10 }}, grid: {{ display: false }}, border: {{ color: colors.grid }} }},
        y: {{ ticks: {{ color: colors.text, callback: (v) => money(v) }}, grid: {{ color: colors.grid }}, border: {{ color: colors.grid }} }},
      }},
    }},
  }};
  if (predictionChart) {{ predictionChart.data = cfg.data; predictionChart.options = cfg.options; predictionChart.update(); }}
  else {{ predictionChart = new Chart(document.getElementById('predictionChart'), cfg); }}

  tbody.innerHTML = PREDICTION_MONTHLY.map(r =>
    '<tr><td>' + esc(r.month) + '</td><td>' + moneyFull(r.forecast) + '</td><td>' +
    moneyFull(r.lower) + '</td><td>' + moneyFull(r.upper) + '</td></tr>'
  ).join('');
}}

function renderTrendChart(rows) {{
  const dim = state.groupBy;
  document.getElementById('trendTitle').textContent = 'Monthly cost trend by ' + DIM_LABELS[dim];
  const months = [...new Set(rows.map(r => r.month))].sort();
  const totals = topNWithOther(groupSum(rows, dim, 'billed_cost'), 6);
  const hasOther = totals.some(([label]) => label === 'Other');
  const topSet = new Set(totals.map(([label]) => label).filter(l => l !== 'Other'));

  const seriesMap = new Map();
  for (const r of rows) {{
    const key = topSet.has(r[dim]) ? r[dim] : (hasOther ? 'Other' : r[dim]);
    if (!seriesMap.has(key)) seriesMap.set(key, new Map());
    const mm = seriesMap.get(key);
    mm.set(r.month, (mm.get(r.month) || 0) + r.billed_cost);
  }}
  const orderedKeys = totals.map(([label]) => label);
  const colors = axisColors();
  const datasets = orderedKeys.map((label, i) => {{
    const mm = seriesMap.get(label) || new Map();
    const c = paletteColor(i);
    return {{
      label, data: months.map(mo => mm.get(mo) || 0),
      borderColor: c, backgroundColor: c,
      borderWidth: 2, tension: 0.25, fill: false,
      pointRadius: 4, pointHoverRadius: 6,
      pointBackgroundColor: c, pointBorderColor: colors.surface, pointBorderWidth: 2,
    }};
  }});

  const cfg = {{
    type: 'line',
    data: {{ labels: months, datasets }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: 'nearest', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ color: colors.text, usePointStyle: true, pointStyle: 'circle', boxWidth: 8, boxHeight: 8, padding: 16 }} }},
        tooltip: {{ ...tooltipStyle(colors), callbacks: {{ label: (c) => c.dataset.label + ': ' + moneyFull(c.parsed.y) }} }},
      }},
      scales: {{
        x: {{ ticks: {{ color: colors.text }}, grid: {{ color: colors.grid }}, border: {{ color: colors.grid }} }},
        y: {{ ticks: {{ color: colors.text, callback: (v) => money(v) }}, grid: {{ color: colors.grid }}, border: {{ color: colors.grid }} }},
      }},
    }},
  }};
  if (trendChart) {{ trendChart.data = cfg.data; trendChart.options = cfg.options; trendChart.update(); }}
  else {{ trendChart = new Chart(document.getElementById('trendChart'), cfg); }}
}}

function renderProviderChart(rows) {{
  const pairs = groupSum(rows, 'provider', 'billed_cost');
  const colors = axisColors();
  const cfg = {{
    type: 'doughnut',
    data: {{
      labels: pairs.map(p => p[0]),
      datasets: [{{
        data: pairs.map(p => p[1]), backgroundColor: pairs.map((_, i) => paletteColor(i)),
        borderColor: colors.surface, borderWidth: 2, hoverOffset: 6,
      }}],
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      cutout: '62%',
      plugins: {{
        legend: {{ position: 'bottom', labels: {{ color: colors.text, usePointStyle: true, pointStyle: 'circle', boxWidth: 8, boxHeight: 8, padding: 14 }} }},
        tooltip: {{ ...tooltipStyle(colors), callbacks: {{ label: (c) => c.label + ': ' + moneyFull(c.parsed) }} }},
      }},
    }},
  }};
  if (providerChart) {{ providerChart.data = cfg.data; providerChart.options = cfg.options; providerChart.update(); }}
  else {{ providerChart = new Chart(document.getElementById('providerChart'), cfg); }}
}}

function renderTopResources(rows) {{
  const m = new Map();
  for (const r of rows) {{
    if (r.resource_name === '(none)') continue;
    const key = r.resource_name + '||' + r.service_name + '||' + r.account + '||' + r.provider;
    m.set(key, (m.get(key) || 0) + r.billed_cost);
  }}
  const top = [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 15);
  const tbody = document.getElementById('topResourcesBody');
  if (!top.length) {{
    tbody.innerHTML = '<tr><td colspan="5" class="empty">No resource-level charges in this filter selection.</td></tr>';
    return;
  }}
  tbody.innerHTML = top.map(([key, val]) => {{
    const [resourceName, serviceName, account, provider] = key.split('||');
    return '<tr><td>' + esc(resourceName) + '</td><td>' + esc(serviceName) + '</td><td>' +
           esc(account) + '</td><td>' + esc(provider) + '</td><td>' + moneyFull(val) + '</td></tr>';
  }}).join('');
}}

function renderCommitmentTable(rows) {{
  const tbody = document.getElementById('commitmentBody');
  if (!rows.length) {{
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No commitment-covered usage for the current filter selection.</td></tr>';
    return;
  }}
  tbody.innerHTML = rows.map(r => {{
    const pct = (r.coverage_pct === null || r.coverage_pct === undefined) ? '-' : r.coverage_pct.toFixed(1) + '%';
    const qty = (v) => (v === null || v === undefined) ? '-' : v.toLocaleString(undefined, {{maximumFractionDigits: 1}}) + ' ' + (r.unit || '');
    return '<tr><td>' + esc(r.provider) + '</td><td>' + esc(r.commitment_type) + '</td><td>' + esc(r.resource) +
           '</td><td>' + qty(r.consumed_quantity) + '</td><td>' + qty(r.covered_quantity) +
           '</td><td>' + pct + '</td><td>' + moneyFull(r.savings) + '</td></tr>';
  }}).join('');
}}

function renderZscoreTable(allRows) {{
  const rows = allRows.slice(0, 15);
  const tbody = document.getElementById('zscoreBody');
  if (!rows.length) {{
    tbody.innerHTML = '<tr><td colspan="9" class="empty">No z-score anomalies for the current filter selection.</td></tr>';
    return;
  }}
  tbody.innerHTML = rows.map(r => {{
    const badgeClass = r.severity === 'critical' ? 'critical' : 'warning';
    const badgeLabel = r.severity === 'critical' ? 'Critical' : 'Warning';
    return '<tr><td>' + esc(r.provider) + '</td><td>' + esc(r.account) + '</td><td>' + esc(r.service_name) +
           '</td><td>' + esc(r.day) + '</td><td>' + moneyFull(r.cost) + '</td><td>' + moneyFull(r.rolling_mean) +
           '</td><td>' + moneyFull(r.rolling_std) + '</td><td>+' + r.zscore.toFixed(2) +
           '</td><td><span class="badge ' + badgeClass + '">' + badgeLabel + '</span></td></tr>';
  }}).join('');
}}

function renderAnomalyTable(allRows) {{
  const rows = allRows.slice(0, 15);
  const tbody = document.getElementById('anomalyBody');
  if (!rows.length) {{
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No cost anomalies flagged for the current filter selection.</td></tr>';
    return;
  }}
  tbody.innerHTML = rows.map(r => {{
    const pct = (r.pct_above_baseline === null || r.pct_above_baseline === undefined) ? '-' :
      (r.pct_above_baseline >= 0 ? '+' : '') + r.pct_above_baseline.toFixed(1) + '%';
    return '<tr><td>' + esc(r.provider) + '</td><td>' + esc(r.account) + '</td><td>' + esc(r.service_name) +
           '</td><td>' + esc(r.resource) + '</td><td>' + esc(r.day) + '</td><td>' + moneyFull(r.cost) +
           '</td><td>' + moneyFull(r.baseline_cost) + '</td><td>' + pct + '</td></tr>';
  }}).join('');
}}

function renderForecastTable(allRows) {{
  const rows = allRows.slice(0, 15);
  const tbody = document.getElementById('forecastBody');
  if (!rows.length) {{
    tbody.innerHTML = '<tr><td colspan="7" class="empty">Not enough monthly history to forecast for the current filter selection.</td></tr>';
    return;
  }}
  tbody.innerHTML = rows.map(r => {{
    const pct = (r.forecast_pct_change === null || r.forecast_pct_change === undefined) ? '-' :
      (r.forecast_pct_change >= 0 ? '+' : '') + r.forecast_pct_change.toFixed(1) + '%';
    const riskStyle = r.overrun_risk ? ' style="color:var(--text-primary);font-weight:600;"' : '';
    return '<tr><td>' + esc(r.provider) + '</td><td>' + esc(r.account) + '</td><td>' + esc(r.application) +
           '</td><td>' + esc(r.service_category) + '</td><td>' + moneyFull(r.last_actual_cost) +
           '</td><td>' + moneyFull(r.forecast_next_month_cost) + '</td><td' + riskStyle + '>' + pct + '</td></tr>';
  }}).join('');
}}

function renderRecommendTable(allRows) {{
  const rows = allRows.filter(r => r.recommended).slice(0, 15);
  const tbody = document.getElementById('recommendBody');
  if (!rows.length) {{
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No commitment candidates for the current filter selection.</td></tr>';
    return;
  }}
  tbody.innerHTML = rows.map(r => {{
    return '<tr><td>' + esc(r.provider) + '</td><td>' + esc(r.account) + '</td><td>' + esc(r.service_name) +
           '</td><td>' + esc(r.resource) + '</td><td>' + moneyFull(r.mean_daily_cost) +
           '</td><td>' + r.cost_volatility.toFixed(3) + '</td><td>' + moneyFull(r.total_cost) + '</td></tr>';
  }}).join('');
}}

// Cached results of the last successful fetch -- `renderFromCache()` runs
// off these without hitting the network (used when only "Group by"
// changes, which is a pure client-side re-aggregation of the same rows).
let cubeRows = [], commitmentRows = [], zscoreRows = [], anomalyRows = [], forecastRows = [], recommendRows = [];
let costRenderToken = 0;

function renderFromCache() {{
  updateFilterCounts();
  renderKPIs(cubeRows);
  renderMlKPIs(zscoreRows, anomalyRows, forecastRows, recommendRows);
  renderBreakdownChart(cubeRows);
  renderAccountChart(cubeRows);
  renderTrendChart(cubeRows);
  renderProviderChart(cubeRows);
  renderTopResources(cubeRows);
  renderCommitmentTable(commitmentRows);
  renderZscoreTable(zscoreRows);
  renderAnomalyTable(anomalyRows);
  renderForecastTable(forecastRows);
  renderRecommendTable(recommendRows);
}}

async function fetchAndRenderCost() {{
  const myToken = ++costRenderToken;
  // An empty checkbox group means "show nothing" (matching the old
  // client-side filter's semantics) -- but an empty query-param list means
  // "no filter" (show everything) to the API, so this case is handled
  // client-side rather than sent as a request.
  const anyDimEmpty = FILTER_DIMS.some(dim => (state.filters[dim] || new Set()).size === 0);

  document.getElementById('tabCost').classList.add('is-loading');
  setLoading('costLoadingIndicator', true);
  if (anyDimEmpty) {{
    cubeRows = []; commitmentRows = []; zscoreRows = []; anomalyRows = []; forecastRows = []; recommendRows = [];
  }} else {{
    try {{
      [cubeRows, commitmentRows, zscoreRows, anomalyRows, forecastRows, recommendRows] = await Promise.all([
        costFetchItems('/api/cost/cube'),
        costFetchItems('/api/cost/commitment-utilization'),
        costFetchItems('/api/ml/zscore-anomalies'),
        costFetchItems('/api/ml/anomalies'),
        costFetchItems('/api/ml/forecast'),
        costFetchItems('/api/ml/recommendations'),
      ]);
    }} catch (err) {{
      if (myToken === costRenderToken) {{
        document.getElementById('tabCost').classList.remove('is-loading');
        setLoading('costLoadingIndicator', false);
        showCostApiError();
      }}
      return;
    }}
  }}

  if (myToken !== costRenderToken) return;  // a newer filter change superseded this request
  hideCostApiError();
  document.getElementById('tabCost').classList.remove('is-loading');
  setLoading('costLoadingIndicator', false);
  renderFromCache();
}}

// --- Resource Telemetry tab --------------------------------------------
// Unlike the Cost Dashboard tab (which filters an already-embedded, fully
// in-memory dataset instantly), this tab fetches from the API on demand:
// the correlation summary once per tab load (small -- one row per
// resource), and each resource's daily history only when that resource is
// actually selected (potentially large across a whole fleet; not worth
// carrying for resources nobody is looking at).
let otelCostChart, otelUtilChart;
let otelTabInitialized = false;
let otelTypeFilter = 'all';
let otelCorrelationLoaded = false;

const OTEL_METRIC_LABELS = {{
  'system.cpu.utilization': 'CPU utilization',
  'system.memory.utilization': 'Memory utilization',
  'system.filesystem.utilization': 'Filesystem utilization',
  'db.client.connections.active': 'Active connections',
  'network.io.utilization': 'Network I/O utilization',
  'network.client.errors': 'Client errors',
}};

// Preferred display order; any other category present falls back to the
// order it's first seen in the data.
const OTEL_TYPE_ORDER = ['Compute', 'Databases', 'Storage', 'Networking'];

async function otelFetchItems(path) {{
  const res = await fetch(API_BASE + path);
  if (!res.ok) throw new Error('API returned ' + res.status);
  const body = await res.json();
  return body.items;
}}

function otelApiUnreachableMessage() {{
  return 'Could not reach the API at ' + API_BASE + '. Start it with '
    + '<code>focus-finops serve</code>, then reload this page.';
}}

function showOtelChartError(message) {{
  ['otelCostChart', 'otelUtilChart'].forEach(id => {{
    const el = document.getElementById(id);
    const wrap = el && el.closest('.chart-canvas-wrap');
    if (wrap) wrap.outerHTML = '<p class="empty">' + message + '</p>';
  }});
}}

function showOtelError(message) {{
  // The correlation fetch itself failed -- nothing on this tab loaded.
  ['kpi-otel-resources', 'kpi-otel-healthy', 'kpi-otel-rightsizing', 'kpi-otel-investigate']
    .forEach(id => {{ document.getElementById(id).textContent = '-'; }});
  document.getElementById('otelResourceSelect').innerHTML = '';
  document.getElementById('otelCorrelationBody').innerHTML =
    '<tr><td colspan="7" class="empty">' + message + '</td></tr>';
  showOtelChartError(message);
}}

function otelTypesPresent() {{
  const seen = new Set(otelCorrelationData.map(r => r.service_category));
  return OTEL_TYPE_ORDER.filter(t => seen.has(t)).concat([...seen].filter(t => !OTEL_TYPE_ORDER.includes(t)));
}}

function filteredOtelCorrelation() {{
  return otelTypeFilter === 'all'
    ? otelCorrelationData
    : otelCorrelationData.filter(r => r.service_category === otelTypeFilter);
}}

function populateOtelTypeSelect() {{
  const select = document.getElementById('otelTypeSelect');
  const types = otelTypesPresent();
  select.innerHTML = ['<option value="all">All types</option>']
    .concat(types.map(t => `<option value="${{esc(t)}}">${{esc(t)}}</option>`))
    .join('');
}}

function renderOtelKPIs() {{
  const rows = filteredOtelCorrelation();
  document.getElementById('kpi-otel-resources').textContent = rows.length.toLocaleString();
  const counts = {{}};
  rows.forEach(r => {{ counts[r.classification] = (counts[r.classification] || 0) + 1; }});
  document.getElementById('kpi-otel-healthy').textContent = (counts['Cost tracks usage (healthy)'] || 0).toLocaleString();
  document.getElementById('kpi-otel-rightsizing').textContent = (counts['Rightsizing candidate (low utilization, steady cost)'] || 0).toLocaleString();
  document.getElementById('kpi-otel-investigate').textContent = (counts['Investigate -- cost independent of utilization'] || 0).toLocaleString();
}}

function populateOtelResourceSelect() {{
  const select = document.getElementById('otelResourceSelect');
  const rows = filteredOtelCorrelation();
  select.innerHTML = rows.map(r =>
    `<option value="${{esc(r.resource_id)}}">${{esc(r.resource_name)}} (${{esc(r.provider)}} / ${{esc(r.account)}})</option>`
  ).join('');
  if (rows.length) select.value = rows[0].resource_id;
}}

async function renderOtelCharts(resourceId) {{
  let rows;
  setLoading('otelLoadingIndicator', true);
  try {{
    rows = await otelFetchItems('/api/otel/daily?limit=2000&resource_id=' + encodeURIComponent(resourceId));
  }} catch (err) {{
    setLoading('otelLoadingIndicator', false);
    showOtelChartError(otelApiUnreachableMessage());
    return;
  }}
  setLoading('otelLoadingIndicator', false);
  rows.sort((a, b) => a.day < b.day ? -1 : 1);
  const colors = axisColors();
  const labels = rows.map(r => r.day);

  const costCfg = {{
    type: 'bar',
    data: {{
      labels,
      datasets: [{{
        label: 'Billed Cost', data: rows.map(r => r.cost),
        backgroundColor: paletteColor(0), borderRadius: 3, maxBarThickness: 10,
      }}],
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }}, tooltip: {{ ...tooltipStyle(colors), callbacks: {{ label: (c) => moneyFull(c.parsed.y) }} }} }},
      scales: {{
        x: {{ ticks: {{ color: colors.text, maxTicksLimit: 8 }}, grid: {{ display: false }}, border: {{ color: colors.grid }} }},
        y: {{ ticks: {{ color: colors.text, callback: (v) => money(v) }}, grid: {{ color: colors.grid }}, border: {{ color: colors.grid }} }},
      }},
    }},
  }};
  if (otelCostChart) {{ otelCostChart.data = costCfg.data; otelCostChart.options = costCfg.options; otelCostChart.update(); }}
  else {{ otelCostChart = new Chart(document.getElementById('otelCostChart'), costCfg); }}

  const metricKeys = Object.keys(OTEL_METRIC_LABELS).filter(m => rows.some(r => r[m] !== null && r[m] !== undefined));
  const utilDatasets = metricKeys.map((m, i) => {{
    const c = paletteColor(i + 1);
    return {{
      label: OTEL_METRIC_LABELS[m], data: rows.map(r => r[m]),
      borderColor: c, backgroundColor: c, borderWidth: 2, tension: 0.2,
      pointRadius: 3, pointBackgroundColor: c, pointBorderColor: colors.surface, pointBorderWidth: 1,
      spanGaps: true,
    }};
  }});
  const utilCfg = {{
    type: 'line',
    data: {{ labels, datasets: utilDatasets }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ color: colors.text, usePointStyle: true, pointStyle: 'circle', boxWidth: 8, boxHeight: 8 }} }},
        tooltip: {{ ...tooltipStyle(colors), callbacks: {{ label: (c) => c.dataset.label + ': ' + c.parsed.y.toFixed(1) }} }},
      }},
      scales: {{
        x: {{ ticks: {{ color: colors.text, maxTicksLimit: 8 }}, grid: {{ display: false }}, border: {{ color: colors.grid }} }},
        y: {{ ticks: {{ color: colors.text }}, grid: {{ color: colors.grid }}, border: {{ color: colors.grid }} }},
      }},
    }},
  }};
  if (otelUtilChart) {{ otelUtilChart.data = utilCfg.data; otelUtilChart.options = utilCfg.options; otelUtilChart.update(); }}
  else {{ otelUtilChart = new Chart(document.getElementById('otelUtilChart'), utilCfg); }}
}}

function renderOtelCorrelationTable() {{
  const tbody = document.getElementById('otelCorrelationBody');
  const rows = filteredOtelCorrelation();
  if (!otelCorrelationData.length) {{
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No OTel telemetry loaded -- run `generate-otel` then `ingest-otel` first.</td></tr>';
    return;
  }}
  if (!rows.length) {{
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No resources of this type.</td></tr>';
    return;
  }}
  tbody.innerHTML = rows.map(r => {{
    let badgeClass = '';
    if (r.classification.indexOf('Investigate') === 0) badgeClass = 'critical';
    else if (r.classification.indexOf('Rightsizing') === 0) badgeClass = 'warning';
    else if (r.classification.indexOf('healthy') !== -1) badgeClass = 'good';
    const label = badgeClass ? `<span class="badge ${{badgeClass}}">${{esc(r.classification)}}</span>` : esc(r.classification);
    return '<tr><td>' + esc(r.provider) + '</td><td>' + esc(r.account) + '</td><td>' + esc(r.resource_name) +
           '</td><td>' + esc(r.service_category) + '</td><td>' + r.avg_utilization.toFixed(1) + '%</td><td>' +
           r.correlation.toFixed(2) + '</td><td>' + label + '</td></tr>';
  }}).join('');
}}

async function refreshOtelView() {{
  renderOtelKPIs();
  populateOtelResourceSelect();
  renderOtelCorrelationTable();
  const rows = filteredOtelCorrelation();
  if (rows.length) {{
    await renderOtelCharts(rows[0].resource_id);
  }}
}}

async function initOtelTab() {{
  if (otelTabInitialized) return;

  if (!otelCorrelationLoaded) {{
    setLoading('otelLoadingIndicator', true);
    try {{
      otelCorrelationData = await otelFetchItems('/api/otel/correlation?limit=2000');
      otelCorrelationLoaded = true;
    }} catch (err) {{
      // Don't set otelTabInitialized -- leaves this retryable, so starting
      // the API and reopening the tab (no full page reload needed) works.
      setLoading('otelLoadingIndicator', false);
      showOtelError(otelApiUnreachableMessage());
      return;
    }}
    setLoading('otelLoadingIndicator', false);
  }}

  otelTabInitialized = true;
  populateOtelTypeSelect();
  await refreshOtelView();

  document.getElementById('otelResourceSelect').addEventListener('change', (e) => {{
    renderOtelCharts(e.target.value);
  }});
  document.getElementById('otelTypeSelect').addEventListener('change', (e) => {{
    otelTypeFilter = e.target.value;
    refreshOtelView();
  }});
}}

function activateTab(tabId) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tabId));
  document.querySelectorAll('.tab-panel').forEach(p => {{ p.hidden = p.id !== tabId; }});
  if (tabId === 'tabTelemetry') initOtelTab();
  window.scrollTo({{ top: 0, behavior: 'smooth' }});
}}
document.querySelectorAll('.tab-btn').forEach(btn => {{
  btn.addEventListener('click', () => activateTab(btn.dataset.tab));
}});
document.querySelectorAll('a.footer-link[data-tab]').forEach(link => {{
  link.addEventListener('click', (e) => {{ e.preventDefault(); activateTab(link.dataset.tab); }});
}});

async function checkApiHealth() {{
  const badge = document.getElementById('apiStatusBadge');
  const text = document.getElementById('apiStatusText');
  try {{
    const res = await fetch(API_BASE + '/health');
    if (!res.ok) throw new Error('bad status');
    badge.className = 'api-status is-ok';
    text.textContent = 'API connected';
  }} catch (err) {{
    badge.className = 'api-status is-down';
    text.textContent = 'API unreachable';
  }}
}}

document.getElementById('groupBySelect').addEventListener('change', (e) => {{
  state.groupBy = e.target.value;
  renderFromCache();  // pure re-aggregation of already-fetched rows -- no new request needed
}});
document.getElementById('resetBtn').addEventListener('click', () => {{
  FILTER_DIMS.forEach(dim => {{ state.filters[dim] = new Set(allDimValues[dim]); }});
  fetchAndRenderCost();
}});

// Charts inside a collapsed accordion can be laid out at zero size; force a
// resize when the accordion containing them is reopened, in case the
// browser's own ResizeObserver-based handling doesn't catch it.
document.getElementById('accordionBreakdown').addEventListener('toggle', function () {{
  if (this.open) {{ [breakdownChart, providerChart, accountChart, trendChart].forEach(c => c && c.resize()); }}
}});
document.getElementById('accordionPrediction').addEventListener('toggle', function () {{
  if (this.open && predictionChart) predictionChart.resize();
}});
document.getElementById('accordionOtelDetail').addEventListener('toggle', function () {{
  if (this.open) {{ [otelCostChart, otelUtilChart].forEach(c => c && c.resize()); }}
}});

async function initCostDashboard() {{
  let initialCube;
  setLoading('costLoadingIndicator', true);
  try {{
    initialCube = await costFetchItems('/api/cost/cube');
  }} catch (err) {{
    setLoading('costLoadingIndicator', false);
    showCostApiError();
    return;
  }}
  setLoading('costLoadingIndicator', false);
  hideCostApiError();
  FILTER_DIMS.forEach(dim => {{
    allDimValues[dim] = distinctValuesFrom(initialCube, dim);
    state.filters[dim] = new Set(allDimValues[dim]);
  }});
  renderFilterOptions();
  await fetchAndRenderCost();
}}

initCostDashboard();
renderPredictionChart();
checkApiHealth();
</script>
</body>
</html>
"""


def run(out_path: Path, api_base: str = "http://127.0.0.1:8000") -> Path:
    totals = queries.overall_totals()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if totals.empty or not totals.get("line_items"):
        out_path.write_text(
            "<html><body style='font-family:sans-serif;padding:40px'>"
            "<h2>No data loaded yet</h2>"
            "<p>Run <code>focus-finops ingest &lt;file.csv&gt;</code> first, then regenerate the dashboard.</p>"
            "</body></html>",
            encoding="utf-8",
        )
        return out_path

    # The cube/commitment/z-score/anomaly/forecast/recommendation datasets
    # are deliberately NOT computed or embedded here anymore -- the Cost
    # Dashboard tab now fetches all of them from the API per filter change
    # (see api.py and this template's fetchAndRenderCost()). Only the
    # portfolio-wide 3-month prediction (unaffected by those filters) is
    # still embedded below.
    prediction = cost_prediction.predict_cost()
    prediction_daily_json = prediction["daily"].to_json(orient="records").replace("</", "<\\/")
    prediction_monthly_json = prediction["monthly"].to_json(orient="records").replace("</", "<\\/")

    now = datetime.now(timezone.utc)
    html = PAGE_TEMPLATE.format(
        chart_js_cdn=CHART_JS_CDN,
        generated_at=now.strftime("%Y-%m-%d %H:%M UTC"),
        generated_year=now.year,
        period_start=str(totals["period_start"])[:10],
        period_end=str(totals["period_end"])[:10],
        prediction_daily_json=prediction_daily_json,
        prediction_monthly_json=prediction_monthly_json,
        api_base=api_base,
    )
    out_path.write_text(html, encoding="utf-8")
    return out_path
