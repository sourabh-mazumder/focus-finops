"""Builds a single-file, interactive HTML cost dashboard from the data
currently loaded in Postgres.

Unlike the CSV/summary reports, this is fully dynamic: a pre-aggregated
cost cube (provider / account / application / owner / service / region /
month / resource) is queried once and embedded as JSON in the page, then
filtering, "group by" breakdowns, and chart drawing all happen client-side
in the browser via Chart.js (loaded from a CDN). This means the page is
NOT usable fully offline (it needs network access on first load to fetch
Chart.js) -- a deliberate trade-off for real interactivity vs. the
previous static-SVG version, which had no server round-trip requirement
but couldn't be filtered/drilled into after generation.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import ml_insights, queries

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
    }}
  }}

  * {{ box-sizing: border-box; }}
  body {{ margin: 0; }}
  .viz-root {{
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--page-plane);
    color: var(--text-primary);
    padding: 32px 24px 64px;
    -webkit-font-smoothing: antialiased;
  }}
  .page {{ max-width: 1180px; margin: 0 auto; }}
  header.study-header {{
    position: relative;
    overflow: hidden;
    text-align: center;
    padding: 22px 24px 22px;
    margin-bottom: 20px;
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 12px;
    box-shadow: var(--shadow-card);
  }}
  header.study-header::before {{
    content: '';
    position: absolute; top: 0; left: 0; right: 0; height: 4px;
    background: linear-gradient(90deg, #2a78d6, #1baf7a, #eda100, #e34948);
  }}
  header.study-header .eyebrow {{
    display: block;
    font-size: 10.5px; font-weight: 700; letter-spacing: 0.12em;
    text-transform: uppercase; color: var(--text-muted);
    margin: 4px 0 10px;
  }}
  header.study-header h1 {{ font-size: 18px; font-weight: 600; line-height: 1.45; margin: 0 0 8px; }}
  header.study-header p {{ margin: 0; color: var(--text-secondary); font-size: 12.5px; }}
  header.page-header {{ margin-bottom: 20px; }}
  header.page-header h1 {{ font-size: 22px; font-weight: 700; letter-spacing: -0.01em; margin: 0 0 4px; }}
  header.page-header p {{ margin: 0; color: var(--text-secondary); font-size: 13px; }}

  .section-divider {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 30px 0 14px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-muted);
  }}
  .section-divider:first-of-type {{ margin-top: 0; }}
  .section-divider::after {{ content: ''; flex: 1; height: 1px; background: var(--gridline); }}

  .filter-bar {{
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    gap: 10px;
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 20px;
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
    padding: 16px 18px;
    box-shadow: var(--shadow-card);
    transition: transform 120ms ease, box-shadow 120ms ease;
  }}
  .stat-tile.tone-accent   {{ border-top-color: var(--accent); }}
  .stat-tile.tone-good     {{ border-top-color: var(--status-good); }}
  .stat-tile.tone-critical {{ border-top-color: var(--status-critical); }}
  .stat-label {{ font-size: 12px; color: var(--text-secondary); margin-bottom: 6px; }}
  .stat-value {{ font-size: 25px; font-weight: 650; letter-spacing: -0.01em; }}
  .stat-sub {{ font-size: 12px; color: var(--text-muted); margin-top: 4px; }}
  .stat-sub.tone-good     {{ color: var(--status-good); }}
  .stat-sub.tone-critical {{ color: var(--status-critical); font-weight: 600; }}

  .chart-grid {{
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 16px;
    margin-bottom: 16px;
    align-items: stretch;
  }}
  .chart-grid.full {{ grid-template-columns: 1fr; }}
  .chart-grid > .chart-card {{ margin-bottom: 0; }}
  @media (max-width: 860px) {{ .chart-grid {{ grid-template-columns: 1fr; }} }}

  .chart-card {{
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px 14px;
    min-width: 0;
    box-shadow: var(--shadow-card);
    margin-bottom: 16px;
  }}
  .chart-card h3 {{ font-size: 14px; font-weight: 650; margin: 0 0 2px; }}
  .chart-subtitle {{ font-size: 12px; color: var(--text-secondary); margin: 0 0 10px; line-height: 1.5; }}
  .ml-subsection {{ margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--gridline); }}
  .ml-subsection:first-of-type {{ margin-top: 14px; padding-top: 0; border-top: none; }}
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
    padding: 9px 10px;
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

  footer.page-footer {{
    margin-top: 32px;
    padding-top: 16px;
    border-top: 1px solid var(--gridline);
    font-size: 11px;
    color: var(--text-muted);
  }}
</style>
</head>
<body>
<div class="viz-root">
  <div class="page">
    <header class="study-header">
      <span class="eyebrow">MBA Capstone Project &middot; FinOps Research</span>
      <h1>A Study on the Development of a FinOps Framework to Optimize and Curb Runaway<br/>
        Cloud Computing (AWS / Azure / GCP) Expenditures</h1>
      <p>Supporting dashboard &mdash; FOCUS-based multi-cloud cost &amp; usage analysis</p>
    </header>

    <header class="page-header">
      <h1>FOCUS FinOps Dashboard</h1>
      <p>Multi-cloud cost &amp; usage (AWS + Azure + GCP), FOCUS v1.4 format · {period_start} to {period_end} · generated {generated_at}</p>
    </header>

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

    <div class="section-divider"><span>Cost overview</span></div>
    <section class="stat-grid">
      <div class="stat-tile tone-accent"><div class="stat-label">Total Billed Cost</div><div class="stat-value" id="kpi-billed">-</div></div>
      <div class="stat-tile"><div class="stat-label">Total Effective Cost</div><div class="stat-value" id="kpi-effective">-</div></div>
      <div class="stat-tile tone-good"><div class="stat-label">Savings vs List Price</div><div class="stat-value" id="kpi-savings">-</div><div class="stat-sub" id="kpi-savings-pct"></div></div>
      <div class="stat-tile"><div class="stat-label">Providers</div><div class="stat-value" id="kpi-providers">-</div></div>
      <div class="stat-tile"><div class="stat-label">Accounts</div><div class="stat-value" id="kpi-accounts">-</div></div>
      <div class="stat-tile"><div class="stat-label">Applications</div><div class="stat-value" id="kpi-applications">-</div></div>
      <div class="stat-tile"><div class="stat-label">Line items</div><div class="stat-value" id="kpi-lineitems">-</div></div>
    </section>

    <div class="section-divider"><span>ML-based signals at a glance</span></div>
    <section class="stat-grid">
      <div class="stat-tile" id="tile-anomalies">
        <div class="stat-label">Cost anomalies flagged</div>
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

    <div class="section-divider"><span>Cost breakdown &amp; trends</span></div>
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

    <div class="section-divider"><span>Commitment &amp; reservation analysis</span></div>
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

    <div class="section-divider"><span>Machine learning-based optimization insights</span></div>
    <section class="chart-card" id="mlSection">
      <h3>ML-identified cost optimization opportunities</h3>
      <p class="chart-subtitle">Anomaly detection (IsolationForest), spend forecasting (linear trend), and commitment
        candidates (KMeans clustering), for the current filter selection. These are decision-support signals for a
        FinOps review, not automated actions.</p>

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

    <footer class="page-footer">
      Generated by focus-finops from data currently loaded in the focus_cost_and_usage table.
      Charts render client-side via Chart.js (loaded from a CDN) -- this page needs network access on first load.
    </footer>
  </div>
</div>
<script>
const CUBE = {cube_json};
const COMMITMENT_ROWS = {commitment_json};
const ML_ANOMALIES = {ml_anomalies_json};
const ML_FORECAST = {ml_forecast_json};
const ML_RECOMMENDATIONS = {ml_recommendations_json};

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

function distinctValues(dim) {{
  return [...new Set(CUBE.map(r => r[dim]))].sort();
}}

const state = {{ filters: {{}}, groupBy: 'service_category' }};
FILTER_DIMS.forEach(dim => {{ state.filters[dim] = new Set(distinctValues(dim)); }});

function filteredCube() {{
  return CUBE.filter(r => FILTER_DIMS.every(dim => state.filters[dim].has(r[dim])));
}}

function filteredCommitmentRows() {{
  return COMMITMENT_ROWS.filter(r => FILTER_DIMS.every(dim => state.filters[dim].has(r[dim])));
}}

function filteredByDims(arr) {{
  return arr.filter(r => FILTER_DIMS.every(dim => state.filters[dim].has(r[dim])));
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
    const values = distinctValues(dim);
    const actions = document.createElement('div');
    actions.className = 'filter-option-actions';
    actions.innerHTML = '<span data-act="all">Select all</span><span data-act="none">Clear</span>';
    actions.querySelector('[data-act=all]').onclick = () => {{ state.filters[dim] = new Set(values); render(); }};
    actions.querySelector('[data-act=none]').onclick = () => {{ state.filters[dim] = new Set(); render(); }};
    container.appendChild(actions);
    values.forEach(v => {{
      const row = document.createElement('label');
      row.className = 'filter-option';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = state.filters[dim].has(v);
      cb.onchange = () => {{
        if (cb.checked) state.filters[dim].add(v); else state.filters[dim].delete(v);
        render();
      }};
      row.appendChild(cb);
      row.appendChild(document.createTextNode(v));
      container.appendChild(row);
    }});
  }});
}}

function updateFilterCounts() {{
  FILTER_DIMS.forEach(dim => {{
    const total = distinctValues(dim).length;
    const selected = state.filters[dim].size;
    document.getElementById('count-' + dim).textContent =
      selected === total ? '(all)' : '(' + selected + '/' + total + ')';
    document.querySelectorAll('#options-' + dim + ' input[type=checkbox]').forEach((cb, i) => {{
      const v = distinctValues(dim)[i];
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
  tile.classList.remove('tone-good', 'tone-critical', 'tone-accent');
  if (tone) tile.classList.add(tone);
}}

function renderMlKPIs() {{
  const anomalies = filteredByDims(ML_ANOMALIES);
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

  const forecast = filteredByDims(ML_FORECAST);
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

  const candidates = filteredByDims(ML_RECOMMENDATIONS).filter(r => r.recommended);
  document.getElementById('kpi-candidates').textContent = candidates.length.toLocaleString();
  document.getElementById('kpi-candidates-sub').textContent = candidates.length
    ? 'steady, high-volume resources'
    : 'none for this selection';
}}

// --- Charts ------------------------------------------------------------
let breakdownChart, trendChart, providerChart;

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

function renderBreakdownChart(rows) {{
  const dim = state.groupBy;
  document.getElementById('breakdownTitle').textContent = 'Cost by ' + DIM_LABELS[dim];
  const pairs = topNWithOther(groupSum(rows, dim, 'billed_cost'), 12);
  const colors = axisColors();
  const cfg = {{
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
  if (breakdownChart) {{ breakdownChart.data = cfg.data; breakdownChart.options = cfg.options; breakdownChart.update(); }}
  else {{ breakdownChart = new Chart(document.getElementById('breakdownChart'), cfg); }}
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

function renderAnomalyTable() {{
  const rows = filteredByDims(ML_ANOMALIES).slice(0, 15);
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

function renderForecastTable() {{
  const rows = filteredByDims(ML_FORECAST).slice(0, 15);
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

function renderRecommendTable() {{
  const rows = filteredByDims(ML_RECOMMENDATIONS).filter(r => r.recommended).slice(0, 15);
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

function render() {{
  const rows = filteredCube();
  updateFilterCounts();
  renderKPIs(rows);
  renderMlKPIs();
  renderBreakdownChart(rows);
  renderTrendChart(rows);
  renderProviderChart(rows);
  renderTopResources(rows);
  renderCommitmentTable(filteredCommitmentRows());
  renderAnomalyTable();
  renderForecastTable();
  renderRecommendTable();
}}

document.getElementById('groupBySelect').addEventListener('change', (e) => {{
  state.groupBy = e.target.value;
  render();
}});
document.getElementById('resetBtn').addEventListener('click', () => {{
  FILTER_DIMS.forEach(dim => {{ state.filters[dim] = new Set(distinctValues(dim)); }});
  render();
}});

renderFilterOptions();
render();
</script>
</body>
</html>
"""


def run(out_path: Path) -> Path:
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

    cube_df = queries.dashboard_cube().round(2)
    cube_json = cube_df.to_json(orient="records").replace("</", "<\\/")

    commitment_df = queries.commitment_utilization().round(2)
    commitment_json = commitment_df.to_json(orient="records").replace("</", "<\\/")

    anomalies_df = ml_insights.detect_cost_anomalies()
    anomalies_json = anomalies_df.to_json(orient="records").replace("</", "<\\/")

    forecast_df = ml_insights.forecast_spend()
    forecast_json = forecast_df.to_json(orient="records").replace("</", "<\\/")

    recommendations_df = ml_insights.recommend_commitments()
    recommendations_json = recommendations_df.to_json(orient="records").replace("</", "<\\/")

    html = PAGE_TEMPLATE.format(
        chart_js_cdn=CHART_JS_CDN,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        period_start=str(totals["period_start"])[:10],
        period_end=str(totals["period_end"])[:10],
        cube_json=cube_json,
        commitment_json=commitment_json,
        ml_anomalies_json=anomalies_json,
        ml_forecast_json=forecast_json,
        ml_recommendations_json=recommendations_json,
    )
    out_path.write_text(html, encoding="utf-8")
    return out_path
