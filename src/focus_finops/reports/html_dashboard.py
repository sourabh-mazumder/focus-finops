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

from . import queries

CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>FOCUS FinOps Dashboard</title>
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
  .page {{ max-width: 1180px; margin: 0 auto; }}
  header.page-header {{ margin-bottom: 20px; }}
  header.page-header h1 {{ font-size: 22px; margin: 0 0 4px; }}
  header.page-header p {{ margin: 0; color: var(--text-secondary); font-size: 13px; }}

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
    margin-bottom: 20px;
  }}
  .stat-tile {{
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
  }}
  .stat-label {{ font-size: 12px; color: var(--text-secondary); margin-bottom: 6px; }}
  .stat-value {{ font-size: 24px; font-weight: 600; }}
  .stat-sub {{ font-size: 12px; color: var(--text-muted); margin-top: 4px; }}

  .chart-grid {{
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 16px;
    margin-bottom: 16px;
    align-items: stretch;
  }}
  .chart-grid.full {{ grid-template-columns: 1fr; }}
  @media (max-width: 860px) {{ .chart-grid {{ grid-template-columns: 1fr; }} }}

  .chart-card {{
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px 12px;
    min-width: 0;
  }}
  .chart-card h3 {{ font-size: 14px; margin: 0 0 2px; }}
  .chart-subtitle {{ font-size: 12px; color: var(--text-secondary); margin: 0 0 10px; }}
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
    padding: 8px 10px;
    border-bottom: 1px solid var(--gridline);
  }}
  table.data-table th {{ color: var(--text-secondary); font-weight: 600; font-size: 12px; }}
  table.data-table td:last-child, table.data-table th:last-child {{ text-align: right; font-variant-numeric: tabular-nums; }}

  footer.page-footer {{ margin-top: 32px; font-size: 11px; color: var(--text-muted); }}
</style>
</head>
<body>
<div class="viz-root">
  <div class="page">
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

    <section class="stat-grid">
      <div class="stat-tile"><div class="stat-label">Total Billed Cost</div><div class="stat-value" id="kpi-billed">-</div></div>
      <div class="stat-tile"><div class="stat-label">Total Effective Cost</div><div class="stat-value" id="kpi-effective">-</div></div>
      <div class="stat-tile"><div class="stat-label">Savings vs List Price</div><div class="stat-value" id="kpi-savings">-</div><div class="stat-sub" id="kpi-savings-pct"></div></div>
      <div class="stat-tile"><div class="stat-label">Providers</div><div class="stat-value" id="kpi-providers">-</div></div>
      <div class="stat-tile"><div class="stat-label">Accounts</div><div class="stat-value" id="kpi-accounts">-</div></div>
      <div class="stat-tile"><div class="stat-label">Applications</div><div class="stat-value" id="kpi-applications">-</div></div>
      <div class="stat-tile"><div class="stat-label">Line items</div><div class="stat-value" id="kpi-lineitems">-</div></div>
    </section>

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
      <h3>Top resources by billed cost</h3>
      <p class="chart-subtitle">Highest-spend individual resources for the current filter selection (excludes account-level charges like tax/support)</p>
      <div style="overflow-x:auto">
        <table class="data-table">
          <thead><tr><th>Resource</th><th>Service</th><th>Account</th><th>Provider</th><th>Billed Cost</th></tr></thead>
          <tbody id="topResourcesBody"></tbody>
        </table>
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

const PALETTE = ['#2a78d6','#eb6834','#2fa84f','#a24fd6','#d6b02a','#d6415f','#2ac2c2','#8a6d3b','#8f8f8f','#c74fc2'];
function paletteColor(i) {{ return PALETTE[i % PALETTE.length]; }}

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

// --- Charts ------------------------------------------------------------
let breakdownChart, trendChart, providerChart;

function axisColors() {{
  const isDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  return {{
    text: isDark ? '#c3c2b7' : '#52514e',
    grid: isDark ? '#2c2c2a' : '#e1e0d9',
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
      datasets: [{{ label: 'Billed Cost', data: pairs.map(p => p[1]),
                    backgroundColor: pairs.map((_, i) => paletteColor(i)) }}],
    }},
    options: {{
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{ callbacks: {{ label: (c) => moneyFull(c.parsed.x) }} }},
      }},
      scales: {{
        x: {{ ticks: {{ color: colors.text, callback: (v) => money(v) }}, grid: {{ color: colors.grid }} }},
        y: {{ ticks: {{ color: colors.text }}, grid: {{ display: false }} }},
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
    return {{
      label, data: months.map(mo => mm.get(mo) || 0),
      borderColor: paletteColor(i), backgroundColor: paletteColor(i),
      tension: 0.25, fill: false, pointRadius: 3,
    }};
  }});

  const cfg = {{
    type: 'line',
    data: {{ labels: months, datasets }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ labels: {{ color: colors.text }} }},
        tooltip: {{ callbacks: {{ label: (c) => c.dataset.label + ': ' + moneyFull(c.parsed.y) }} }},
      }},
      scales: {{
        x: {{ ticks: {{ color: colors.text }}, grid: {{ color: colors.grid }} }},
        y: {{ ticks: {{ color: colors.text, callback: (v) => money(v) }}, grid: {{ color: colors.grid }} }},
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
      datasets: [{{ data: pairs.map(p => p[1]), backgroundColor: pairs.map((_, i) => paletteColor(i)) }}],
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ position: 'bottom', labels: {{ color: colors.text }} }},
        tooltip: {{ callbacks: {{ label: (c) => c.label + ': ' + moneyFull(c.parsed) }} }},
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

function render() {{
  const rows = filteredCube();
  updateFilterCounts();
  renderKPIs(rows);
  renderBreakdownChart(rows);
  renderTrendChart(rows);
  renderProviderChart(rows);
  renderTopResources(rows);
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

    html = PAGE_TEMPLATE.format(
        chart_js_cdn=CHART_JS_CDN,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        period_start=str(totals["period_start"])[:10],
        period_end=str(totals["period_end"])[:10],
        cube_json=cube_json,
    )
    out_path.write_text(html, encoding="utf-8")
    return out_path
