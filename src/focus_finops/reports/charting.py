"""Minimal, dependency-free SVG chart + stat-tile renderers for the HTML
dashboard. No JS charting library, no CDN -- everything needed (including
hover tooltips) is inline SVG + a small shared <script> block, so the
generated dashboard.html opens and works completely offline.

Palette, mark specs (24px bar cap, 4px rounded data-end, 2px lines, 2px
surface rings, hairline gridlines) and interaction rules (hover tooltip,
legend for >=2 series, direct labels used sparingly) follow the project's
dataviz guidelines.
"""
from __future__ import annotations

from html import escape

# --- palette (light/dark aware; see dashboard.py for the CSS custom
# properties these roles map to) -----------------------------------------
SERIES_1 = "var(--series-1)"   # blue - the only series used in single-measure charts
SERIES_2 = "var(--series-2)"   # orange - used for a second series (e.g. list vs effective)


def fmt_money(v: float) -> str:
    v = float(v)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1_000_000:
        return f"{sign}${v/1_000_000:,.2f}M"
    if v >= 1_000:
        return f"{sign}${v/1_000:,.1f}K"
    return f"{sign}${v:,.2f}"


def fmt_money_full(v: float) -> str:
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def stat_tile(label: str, value: str, sublabel: str = "") -> str:
    sub = f'<div class="stat-sub">{escape(sublabel)}</div>' if sublabel else ""
    return f"""
    <div class="stat-tile">
      <div class="stat-label">{escape(label)}</div>
      <div class="stat-value">{escape(value)}</div>
      {sub}
    </div>
    """


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1.0
    import math
    magnitude = 10 ** math.floor(math.log10(v))
    for mult in (1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        step = mult * magnitude
        if step >= v:
            return step
    return v * 1.1


def bar_chart(
    title: str,
    subtitle: str,
    items: list[tuple[str, float]],
    value_fmt=fmt_money,
    chart_id: str = "bar",
) -> str:
    """Horizontal bar chart, one measure, sorted as given."""
    if not items:
        return f'<div class="chart-card"><h3>{escape(title)}</h3><p class="empty">No data.</p></div>'

    max_val = _nice_max(max(v for _, v in items))
    left_margin, right_margin, top_margin = 200, 70, 12
    row_h, bar_h = 32, 20
    width = 720
    plot_w = width - left_margin - right_margin
    height = top_margin + row_h * len(items) + 16

    grid_lines = []
    n_ticks = 4
    for i in range(n_ticks + 1):
        val = max_val * i / n_ticks
        x = left_margin + plot_w * i / n_ticks
        grid_lines.append(
            f'<line x1="{x:.1f}" y1="{top_margin}" x2="{x:.1f}" y2="{height-8}" class="gridline"/>'
            f'<text x="{x:.1f}" y="{height-2}" class="tick-label" text-anchor="middle">{value_fmt(val)}</text>'
        )

    bars = []
    for i, (label, value) in enumerate(items):
        y = top_margin + i * row_h + (row_h - bar_h) / 2
        bw = max(plot_w * (value / max_val), 1.0) if max_val else 1.0
        label_x = left_margin - 10
        val_x = left_margin + bw + 8
        val_inside = bw > 70
        text_x = (left_margin + bw - 8) if val_inside else val_x
        anchor = "end" if val_inside else "start"
        fill_cls = "on-fill" if val_inside else "value-label"
        bars.append(f"""
          <text x="{label_x:.1f}" y="{y+bar_h/2+4:.1f}" text-anchor="end" class="cat-label">{escape(label)}</text>
          <rect x="{left_margin}" y="{y:.1f}" width="{bw:.1f}" height="{bar_h}" rx="4" class="bar"
                data-tooltip="{escape(label)}: {escape(value_fmt(value))}"/>
          <text x="{text_x:.1f}" y="{y+bar_h/2+4:.1f}" text-anchor="{anchor}" class="{fill_cls}">{escape(value_fmt(value))}</text>
        """)

    svg = f"""
    <svg viewBox="0 0 {width} {height}" class="chart-svg" role="img" aria-label="{escape(title)}">
      {''.join(grid_lines)}
      {''.join(bars)}
    </svg>
    """
    return f"""
    <div class="chart-card">
      <h3>{escape(title)}</h3>
      <p class="chart-subtitle">{escape(subtitle)}</p>
      <div class="svg-wrap" id="{chart_id}">{svg}</div>
    </div>
    """


def line_chart(
    title: str,
    subtitle: str,
    points: list[tuple[str, float]],
    value_fmt=fmt_money,
    chart_id: str = "line",
) -> str:
    """Single-series area/line chart over an ordered category axis (dates)."""
    if not points:
        return f'<div class="chart-card"><h3>{escape(title)}</h3><p class="empty">No data.</p></div>'

    width, height = 720, 260
    left_margin, right_margin, top_margin, bottom_margin = 56, 16, 16, 32
    plot_w = width - left_margin - right_margin
    plot_h = height - top_margin - bottom_margin
    max_val = _nice_max(max(v for _, v in points))
    n = len(points)

    def xy(i, v):
        x = left_margin + (plot_w * i / (n - 1) if n > 1 else 0)
        y = top_margin + plot_h - (plot_h * v / max_val if max_val else 0)
        return x, y

    coords = [xy(i, v) for i, (_, v) in enumerate(points)]
    path_d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in coords)
    area_d = (
        path_d
        + f" L {coords[-1][0]:.1f} {top_margin+plot_h:.1f}"
        + f" L {coords[0][0]:.1f} {top_margin+plot_h:.1f} Z"
    )

    grid_lines = []
    for i in range(5):
        val = max_val * i / 4
        y = top_margin + plot_h - plot_h * i / 4
        grid_lines.append(
            f'<line x1="{left_margin}" y1="{y:.1f}" x2="{width-right_margin}" y2="{y:.1f}" class="gridline"/>'
            f'<text x="{left_margin-8}" y="{y+4:.1f}" text-anchor="end" class="tick-label">{value_fmt(val)}</text>'
        )

    # sparse x labels: first, last, and a few in between
    label_idxs = sorted(set([0, n - 1] + [round(i * (n - 1) / 4) for i in range(5)]))
    x_labels = []
    for i in label_idxs:
        x, _ = coords[i]
        x_labels.append(
            f'<text x="{x:.1f}" y="{height-8}" text-anchor="middle" class="tick-label">{escape(points[i][0])}</text>'
        )

    hit_targets = []
    for i, ((label, value), (x, y)) in enumerate(zip(points, coords)):
        hit_targets.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="10" class="hit-target" '
            f'data-tooltip="{escape(label)}: {escape(value_fmt(value))}"/>'
        )

    last_x, last_y = coords[-1]
    end_marker = f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4" class="end-marker"/>'
    end_label = (
        f'<text x="{last_x-8:.1f}" y="{last_y-10:.1f}" text-anchor="end" class="value-label">'
        f"{escape(value_fmt(points[-1][1]))}</text>"
    )

    svg = f"""
    <svg viewBox="0 0 {width} {height}" class="chart-svg" role="img" aria-label="{escape(title)}">
      {''.join(grid_lines)}
      <path d="{area_d}" class="area-fill"/>
      <path d="{path_d}" class="line"/>
      {end_marker}
      {end_label}
      {''.join(x_labels)}
      {''.join(hit_targets)}
    </svg>
    """
    return f"""
    <div class="chart-card">
      <h3>{escape(title)}</h3>
      <p class="chart-subtitle">{escape(subtitle)}</p>
      <div class="svg-wrap" id="{chart_id}">{svg}</div>
    </div>
    """


def two_series_bar_chart(
    title: str,
    subtitle: str,
    items: list[tuple[str, float, float]],
    series_names: tuple[str, str],
    value_fmt=fmt_money,
    chart_id: str = "bar2",
) -> str:
    """Grouped horizontal bar chart comparing two measures per category
    (e.g. List Cost vs Effective Cost) -- needs a legend since >=2 series.
    """
    if not items:
        return f'<div class="chart-card"><h3>{escape(title)}</h3><p class="empty">No data.</p></div>'

    max_val = _nice_max(max(max(a, b) for _, a, b in items))
    left_margin, right_margin, top_margin = 200, 70, 12
    group_h, bar_h, bar_gap = 46, 16, 2
    width = 720
    plot_w = width - left_margin - right_margin
    height = top_margin + group_h * len(items) + 16

    grid_lines = []
    for i in range(5):
        val = max_val * i / 4
        x = left_margin + plot_w * i / 4
        grid_lines.append(
            f'<line x1="{x:.1f}" y1="{top_margin}" x2="{x:.1f}" y2="{height-8}" class="gridline"/>'
            f'<text x="{x:.1f}" y="{height-2}" class="tick-label" text-anchor="middle">{value_fmt(val)}</text>'
        )

    bars = []
    for i, (label, v1, v2) in enumerate(items):
        gy = top_margin + i * group_h
        label_y = gy + group_h / 2 + 4
        bars.append(f'<text x="{left_margin-10}" y="{label_y:.1f}" text-anchor="end" class="cat-label">{escape(label)}</text>')
        for j, (v, cls) in enumerate([(v1, "bar-series-1"), (v2, "bar-series-2")]):
            y = gy + j * (bar_h + bar_gap)
            bw = max(plot_w * (v / max_val), 1.0) if max_val else 1.0
            bars.append(
                f'<rect x="{left_margin}" y="{y:.1f}" width="{bw:.1f}" height="{bar_h}" rx="4" class="{cls}" '
                f'data-tooltip="{escape(label)} — {escape(series_names[j])}: {escape(value_fmt(v))}"/>'
            )

    legend = f"""
    <div class="legend">
      <span class="legend-item"><span class="swatch swatch-1"></span>{escape(series_names[0])}</span>
      <span class="legend-item"><span class="swatch swatch-2"></span>{escape(series_names[1])}</span>
    </div>
    """

    svg = f"""
    <svg viewBox="0 0 {width} {height}" class="chart-svg" role="img" aria-label="{escape(title)}">
      {''.join(grid_lines)}
      {''.join(bars)}
    </svg>
    """
    return f"""
    <div class="chart-card">
      <h3>{escape(title)}</h3>
      <p class="chart-subtitle">{escape(subtitle)}</p>
      {legend}
      <div class="svg-wrap" id="{chart_id}">{svg}</div>
    </div>
    """


def data_table(headers: list[str], rows: list[list[str]]) -> str:
    thead = "".join(f"<th>{escape(h)}</th>" for h in headers)
    trs = []
    for row in rows:
        tds = "".join(f"<td>{escape(str(c))}</td>" for c in row)
        trs.append(f"<tr>{tds}</tr>")
    return f"""
    <table class="data-table">
      <thead><tr>{thead}</tr></thead>
      <tbody>{''.join(trs)}</tbody>
    </table>
    """
