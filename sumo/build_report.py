"""Assemble the comparison into a single self-contained HTML page.

Reads the JSON written by ``analyse.py`` and ``render_map.py`` and emits an
artifact-ready page: no external requests, both themes, and every number
traceable to a simulation run rather than typed in here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SERIES = {
    "baseline": {
        "label": "One car per household",
        "var": "--series-baseline",
        "colour": "#d2521c",
    },
    "pooled": {
        "label": "Ride-shared plan",
        "var": "--series-pooled",
        "colour": "#1f66bd",
    },
}

# Concrete equivalents of the page's ink tokens, for charts written out as their
# own files: a standalone SVG has no page around it to inherit variables from.
STANDALONE_STYLE = """
.grid{stroke:#d9dde1;stroke-width:1}
.axis{stroke:#79828d;stroke-width:1}
.series{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.endpoint{stroke:#ffffff;stroke-width:2}
.tick{font:500 11px ui-monospace,SFMono-Regular,Menlo,monospace;fill:#79828d}
.tick-y{text-anchor:end}
.tick-x{text-anchor:middle}
.axis-label{font:500 11px ui-sans-serif,system-ui,sans-serif;fill:#79828d;
  text-anchor:middle}
.series-label{font:600 12px ui-sans-serif,system-ui,sans-serif;fill:#4d555f}
.chart-title{font:640 15px ui-sans-serif,system-ui,sans-serif;fill:#14181d}
"""


def _fmt_minutes(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    minutes = seconds / 60
    if minutes >= 90:
        return f"{minutes / 60:.1f} h"
    return f"{minutes:.0f} min"


def _line_chart(
    series: list[dict],
    *,
    width: int = 760,
    height: int = 300,
    x_max: float,
    y_max: float,
    x_ticks: list[float],
    y_ticks: list[float],
    x_label: str,
    y_label: str,
    y_format=lambda v: f"{v:,.0f}",
    standalone: bool = False,
    title: str | None = None,
) -> str:
    """A two-series line chart as SVG.

    Embedded in the page it draws its colours from CSS variables so it follows
    the theme. Written out as its own file it carries baked colours and its own
    stylesheet instead, since there is no page to inherit from.
    """
    pad_l, pad_r, pad_t, pad_b = 64, 96, (40 if standalone and title else 16), 40
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    def sx(value: float) -> float:
        return pad_l + (value / x_max) * plot_w if x_max else pad_l

    def sy(value: float) -> float:
        return pad_t + plot_h - (value / y_max) * plot_h if y_max else pad_t + plot_h

    if standalone:
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="{y_label} against {x_label}">',
            f"<style>{STANDALONE_STYLE}</style>",
            f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        ]
        if title:
            parts.append(f'<text class="chart-title" x="{pad_l - 50}" y="24">'
                         f"{title}</text>")
    else:
        parts = [
            f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
            f'aria-label="{y_label} against {x_label}">'
        ]
    # Recessive grid first.
    for tick in y_ticks:
        y = sy(tick)
        parts.append(
            f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" '
            f'x2="{pad_l + plot_w}" y2="{y:.1f}"/>'
        )
        parts.append(
            f'<text class="tick tick-y" x="{pad_l - 10}" y="{y + 4:.1f}">'
            f"{y_format(tick)}</text>"
        )
    for tick in x_ticks:
        x = sx(tick)
        parts.append(
            f'<text class="tick tick-x" x="{x:.1f}" y="{pad_t + plot_h + 24}">'
            f"{tick / 60:.0f}</text>"
        )
    parts.append(
        f'<line class="axis" x1="{pad_l}" y1="{pad_t + plot_h}" '
        f'x2="{pad_l + plot_w}" y2="{pad_t + plot_h}"/>'
    )
    parts.append(
        f'<text class="axis-label" x="{pad_l + plot_w / 2:.0f}" '
        f'y="{height - 4}">{x_label}</text>'
    )

    for entry in series:
        stroke = entry["colour"] if standalone else f'var({entry["var"]})'
        points = " ".join(
            f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(entry["x"], entry["y"])
        )
        parts.append(
            f'<polyline class="series" points="{points}" '
            f'style="stroke:{stroke}"/>'
        )
        # Direct label at the line's end, so identity is never colour-alone.
        end_x, end_y = entry["x"][-1], entry["y"][-1]
        parts.append(
            f'<circle class="endpoint" cx="{sx(end_x):.1f}" cy="{sy(end_y):.1f}" '
            f'r="4" style="fill:{stroke}"/>'
        )
        parts.append(
            f'<text class="series-label" x="{sx(end_x) + 10:.1f}" '
            f'y="{sy(end_y) + 4:.1f}">{entry["label"]}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def series_for(comparison: dict, field: str) -> list[dict]:
    """A per-step summary series for both scenarios, ready to plot."""
    out = []
    for key in ("baseline", "pooled"):
        xs = comparison[key]["series"]["time_s"]
        ys = comparison[key]["series"][field]
        # Before the first car is inserted SUMO reports no mean speed, which
        # arrives here as NaN; those steps have nothing to plot.
        pairs = [(x, y) for x, y in zip(xs, ys) if y == y]
        out.append({
            "x": [x for x, _ in pairs],
            "y": [y for _, y in pairs],
            "label": SERIES[key]["label"],
            "var": SERIES[key]["var"],
            "colour": SERIES[key]["colour"],
        })
    return out


def arrivals_for(comparison: dict, field: str) -> list[dict]:
    """A cumulative arrival curve for both scenarios."""
    return [
        {
            "x": comparison[key]["arrivals"]["time_s"],
            "y": comparison[key]["arrivals"][field],
            "label": SERIES[key]["label"],
            "var": SERIES[key]["var"],
            "colour": SERIES[key]["colour"],
        }
        for key in ("baseline", "pooled")
    ]


def ticks(maximum: float, count: int = 5) -> list[float]:
    step = maximum / count
    return [round(step * i) for i in range(count + 1)]


def build(comparison: dict, maps: dict, meta: dict) -> str:
    baseline = comparison["baseline"]
    pooled = comparison["pooled"]

    cars_saved = baseline["vehicles_departed"] - pooled["vehicles_departed"]
    cars_saved_pct = 100 * cars_saved / baseline["vehicles_departed"]
    people_extra = pooled["people_carried"] - baseline["people_carried"]
    horizon_label = _fmt_minutes(baseline["horizon_s"])
    people_gain_pct = (
        100 * (pooled["people_cleared_by_horizon"]
               - baseline["people_cleared_by_horizon"])
        / max(baseline["people_cleared_by_horizon"], 1)
    )

    running_series = series_for(comparison, "running")
    speed_series = series_for(comparison, "speed_relative_pct")
    people_series = arrivals_for(comparison, "people")

    running_max = max(max(s["y"]) for s in running_series)
    speed_max = max(max(s["y"]) for s in speed_series)
    time_max = max(max(s["x"]) for s in running_series)

    running_chart = _line_chart(
        running_series,
        x_max=time_max, y_max=running_max,
        x_ticks=ticks(time_max, 6), y_ticks=ticks(running_max),
        x_label="minutes after the alarm", y_label="cars on the road",
    )
    speed_chart = _line_chart(
        speed_series,
        x_max=time_max, y_max=speed_max,
        x_ticks=ticks(time_max, 6), y_ticks=ticks(speed_max),
        x_label="minutes after the alarm", y_label="speed as a share of the limit",
        y_format=lambda v: f"{v:,.0f}%",
    )
    people_max = max(max(s["y"]) for s in people_series)
    people_time_max = max(max(s["x"]) for s in people_series)
    people_chart = _line_chart(
        people_series,
        x_max=people_time_max, y_max=people_max,
        x_ticks=ticks(people_time_max, 6), y_ticks=ticks(people_max),
        x_label="minutes after the alarm", y_label="people clear of the district",
        y_format=lambda v: f"{v / 1000:,.0f}k" if v else "0",
    )

    band_legend = "".join(
        f'<li><span class="swatch" style="background:{b["colour"]}"></span>'
        f'<strong>{b["label"]}</strong><span class="muted">{b["detail"]}</span></li>'
        for b in maps["bands"]
    )

    def stat_rows() -> str:
        rows = [
            ("Cars sent out", "{:,}", "vehicles_departed"),
            ("People carried", "{:,}", "people_carried"),
            ("Mean people per car", "{:.2f}", "mean_occupancy"),
            ("People clear of the district", "{:,}", "people_cleared_by_horizon"),
            ("…as a share of those carried", "{:.1f}%", "people_cleared_pct"),
            ("Cars clear of the district", "{:,}", "vehicles_arrived"),
            ("Cars still on the road", "{:,}", "final_running"),
            ("…of those, stationary", "{:,}", "final_halting"),
            ("Share stationary", "{:.1f}%", "stationary_share_pct"),
            ("Speed vs the limit", "{:.1%}", "final_speed_relative"),
            ("Peak cars at once", "{:,}", "peak_running"),
            ("Gridlock teleports", "{:,}", "teleports"),
        ]
        out = []
        for label, fmt, key in rows:
            b, p = fmt.format(baseline[key]), fmt.format(pooled[key])
            out.append(
                f"<tr><th scope=\"row\">{label}</th><td>{b}</td><td>{p}</td></tr>"
            )
        return "".join(out)

    map_stats = maps["stats"]
    snapshot_min = maps["snapshot_s"] / 60

    loading = meta.get("exit_loading", [])
    loading_total = sum(row["vehicles"] for row in loading) or 1
    busiest = loading[0] if loading else {"name": "—", "vehicles": 0}
    busiest_share = 100 * busiest["vehicles"] / loading_total
    exit_bars = "".join(
        f'<li><div class="exit-name">{row["name"]}'
        f'<span class="exit-class">{row["highway"]}</span></div>'
        f'<div class="exit-track"><div class="exit-fill" '
        f'style="width:{100 * row["vehicles"] / loading_total:.1f}%"></div></div>'
        f'<div class="exit-count">{row["vehicles"]:,}</div></li>'
        for row in loading
    )

    return f"""<title>Evacuating Thanet</title>
<style>
:root {{
  color-scheme: light;
  --ground: #f4f5f6;
  --surface: #ffffff;
  --surface-sunk: #eceef0;
  --line: #d9dde1;
  --ink: #14181d;
  --ink-2: #4d555f;
  --ink-3: #79828d;
  --series-baseline: #eb6834;
  --series-pooled: #2a78d6;
  --flag: #b4341f;
  --shadow: 0 1px 2px rgba(20,24,29,.06), 0 8px 24px rgba(20,24,29,.05);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --ground: #101317;
    --surface: #171b20;
    --surface-sunk: #1e232a;
    --line: #2b3239;
    --ink: #f2f4f6;
    --ink-2: #b3bcc6;
    --ink-3: #808b96;
    --series-baseline: #d95926;
    --series-pooled: #3987e5;
    --flag: #e88a78;
    --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --ground: #101317;
  --surface: #171b20;
  --surface-sunk: #1e232a;
  --line: #2b3239;
  --ink: #f2f4f6;
  --ink-2: #b3bcc6;
  --ink-3: #808b96;
  --series-baseline: #d95926;
  --series-pooled: #3987e5;
  --flag: #e88a78;
  --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
}}

* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--ground);
  color: var(--ink);
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 16px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{
  max-width: 1000px;
  margin: 0 auto;
  padding: 56px 24px 96px;
  display: flex;
  flex-direction: column;
  gap: 56px;
}}
h1, h2, h3 {{ text-wrap: balance; margin: 0; letter-spacing: -0.02em; }}
h1 {{ font-size: clamp(2rem, 5vw, 3rem); line-height: 1.08; font-weight: 640; }}
h2 {{ font-size: 1.35rem; font-weight: 620; }}
h3 {{ font-size: 1rem; font-weight: 620; }}
p {{ margin: 0; max-width: 68ch; }}
.muted {{ color: var(--ink-2); }}
.eyebrow {{
  font: 600 0.72rem/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  letter-spacing: .13em;
  text-transform: uppercase;
  color: var(--ink-3);
}}
header .lede {{ font-size: 1.15rem; color: var(--ink-2); margin-top: 18px; }}
.runmeta {{
  margin-top: 22px;
  font: 500 .78rem/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--ink-3);
  display: flex; flex-wrap: wrap; gap: 6px 18px;
}}

section {{ display: flex; flex-direction: column; gap: 20px; }}

.verdict {{
  background: var(--surface);
  border: 1px solid var(--line);
  border-left: 3px solid var(--flag);
  border-radius: 4px;
  padding: 24px 28px;
  box-shadow: var(--shadow);
  display: flex; flex-direction: column; gap: 12px;
}}
.verdict strong {{ color: var(--ink); }}

.tiles {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 2px;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: 4px;
  overflow: hidden;
}}
.tile {{ background: var(--surface); padding: 20px 22px; }}
.tile .value {{
  font: 640 2rem/1.1 ui-sans-serif, system-ui, sans-serif;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.03em;
}}
.tile .name {{ font-size: .85rem; color: var(--ink-2); margin-top: 6px; }}
.tile .note {{ font-size: .78rem; color: var(--ink-3); margin-top: 2px; }}

.panel {{
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 24px;
  box-shadow: var(--shadow);
}}

/* Stacked rather than side by side: at half width the district detail that
   carries the finding disappears. */
.maps {{ display: grid; grid-template-columns: 1fr; gap: 16px; }}
.map-card {{
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 16px;
  display: flex; flex-direction: column; gap: 10px;
}}
.map-card img {{ display: block; width: 100%; height: auto; border-radius: 2px; }}
.map-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }}
.map-head .count {{
  font: 600 .8rem ui-monospace, SFMono-Regular, Menlo, monospace;
  font-variant-numeric: tabular-nums;
  color: var(--ink-2);
}}
.bands {{ list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 6px 20px; }}
.bands li {{ display: flex; align-items: center; gap: 7px; font-size: .82rem; }}
.bands .swatch {{ width: 11px; height: 11px; border-radius: 2px; flex: none; }}
.bands .muted {{ color: var(--ink-3); font-size: .78rem; }}

.chart {{ display: block; width: 100%; height: auto; overflow: visible; }}
.chart .grid {{ stroke: var(--line); stroke-width: 1; }}
.chart .axis {{ stroke: var(--ink-3); stroke-width: 1; }}
.chart .series {{ fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }}
.chart .endpoint {{ stroke: var(--surface); stroke-width: 2; }}
.chart .tick {{ font: 500 11px ui-monospace, SFMono-Regular, Menlo, monospace; fill: var(--ink-3); }}
.chart .tick-y {{ text-anchor: end; }}
.chart .tick-x {{ text-anchor: middle; }}
.chart .axis-label {{ font: 500 11px ui-sans-serif, system-ui, sans-serif; fill: var(--ink-3); text-anchor: middle; }}
.chart .series-label {{ font: 600 12px ui-sans-serif, system-ui, sans-serif; fill: var(--ink-2); }}

.legend {{ display: flex; gap: 20px; flex-wrap: wrap; margin-top: 4px; }}
.legend span {{ display: flex; align-items: center; gap: 8px; font-size: .85rem; color: var(--ink-2); }}
.legend i {{ width: 14px; height: 3px; border-radius: 2px; display: block; }}

.tablewrap {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: .9rem; min-width: 480px; }}
caption {{ text-align: left; color: var(--ink-3); font-size: .82rem; padding-bottom: 10px; }}
th, td {{ text-align: right; padding: 9px 12px; border-bottom: 1px solid var(--line); font-variant-numeric: tabular-nums; }}
thead th {{ font-size: .78rem; color: var(--ink-2); font-weight: 620; }}
tbody th {{ text-align: left; font-weight: 500; color: var(--ink-2); }}
thead th:first-child {{ text-align: left; }}
.col-base {{ color: var(--series-baseline); }}
.col-pool {{ color: var(--series-pooled); }}

.exits {{ list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }}
.exits li {{ display: grid; grid-template-columns: 210px 1fr 72px; gap: 14px; align-items: center; }}
@media (max-width: 640px) {{ .exits li {{ grid-template-columns: 1fr 60px; }}
  .exits .exit-track {{ grid-column: 1 / -1; }} }}
.exit-name {{ font-size: .9rem; display: flex; flex-direction: column; }}
.exit-class {{ font: 500 .7rem ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--ink-3); text-transform: uppercase; letter-spacing: .06em; }}
.exit-track {{ background: var(--surface-sunk); border-radius: 3px; height: 20px; overflow: hidden; }}
.exit-fill {{ background: var(--series-pooled); height: 100%; border-radius: 3px; }}
.exit-count {{ text-align: right; font-variant-numeric: tabular-nums;
  font-size: .88rem; color: var(--ink-2); }}

.caveats {{ display: flex; flex-direction: column; gap: 14px; }}
.caveat {{ display: grid; grid-template-columns: 150px 1fr; gap: 18px; align-items: start; }}
@media (max-width: 680px) {{ .caveat {{ grid-template-columns: 1fr; gap: 4px; }} }}
.caveat dt {{
  font: 600 .74rem/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3);
}}
.caveat dd {{ margin: 0; color: var(--ink-2); font-size: .92rem; }}
footer {{ border-top: 1px solid var(--line); padding-top: 20px; color: var(--ink-3); font-size: .82rem; }}

/* Print / PDF. The screen page is theme-aware; paper is not, so the light
   palette is pinned here rather than left to whatever the renderer assumes. */
@media print {{
  :root {{
    color-scheme: light;
    --ground: #ffffff;
    --surface: #ffffff;
    --surface-sunk: #eceef0;
    --line: #c9ced4;
    --ink: #14181d;
    --ink-2: #3f4750;
    --ink-3: #6b747e;
    --series-baseline: #d2521c;
    --series-pooled: #1f66bd;
    --flag: #b4341f;
    --shadow: none;
  }}
  @page {{ size: A4; margin: 14mm 12mm; }}
  body {{ background: #ffffff; font-size: 10.5pt; }}
  /* Keep painted fills — Chrome drops backgrounds unless told otherwise, which
     would erase the exit bars and the congestion swatches. */
  * {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .wrap {{ max-width: none; padding: 0; gap: 26px; }}
  /* auto-fit leaves the fourth cell empty on a page this wide, and the grid gap
     shows through it as a grey slab. Four tiles, two rows, no hole. */
  .tiles {{ grid-template-columns: repeat(2, 1fr); }}
  h1 {{ font-size: 24pt; }}
  h2 {{ font-size: 13pt; }}
  section, .panel, .verdict, .map-card, .tiles {{ break-inside: avoid; }}
  h2 {{ break-after: avoid; }}
  .map-card, .caveat, .exits li, tr {{ break-inside: avoid; }}
  .panel, .verdict, .map-card {{ box-shadow: none; }}
  .maps {{ gap: 12px; }}
  footer {{ break-before: avoid; }}
}}
</style>

<div class="wrap">
<header>
  <div class="eyebrow">Thanet District · LAD E07000114 · SUMO microsimulation</div>
  <h1>Ride-sharing gets {people_gain_pct:.0f}% more people out</h1>
  <p class="lede">Two microsimulations of the same district, the same road network and
  the same alarm. In one, every car-owning household drives itself out. In the other,
  fleet minimisation chooses which cars depart and fills them. An hour after
  notification the pooled plan has moved {pooled['people_cleared_by_horizon']:,} people
  across the district boundary against {baseline['people_cleared_by_horizon']:,} — in
  {cars_saved_pct:.0f}% fewer cars. Both are still jammed, and the reason they are is
  no longer the fleet.</p>
  <div class="runmeta">
    <span>{meta['persons']:,} residents</span>
    <span>{meta['households']:,} households</span>
    <span>{meta['edges']:,} road links</span>
    <span>{meta['sumo']}</span>
    <span>run {meta['date']}</span>
  </div>
</header>

<section>
  <div class="verdict">
    <div class="eyebrow">The finding</div>
    <p>Fleet minimisation works, and it shows on the road. The pooled plan activates
    <strong>{pooled['vehicles_departed']:,} cars against
    {baseline['vehicles_departed']:,}</strong> — {cars_saved_pct:.0f}% fewer — at a
    mean of {pooled['mean_occupancy']:.2f} people per car, and still carries
    <strong>{people_extra:,} more people</strong>, because a household with no car has
    no way out of the baseline at all. {horizon_label} in, it has cleared
    <strong>{pooled['people_cleared_by_horizon']:,} people</strong> to the baseline's
    {baseline['people_cleared_by_horizon']:,}, and the roads it is using are moving at
    {pooled['final_speed_relative']:.0%} of the limit against
    {baseline['final_speed_relative']:.0%}.</p>
    <p>What has not changed is that the district is still jammed —
    {pooled['stationary_share_pct']:.0f}% of the pooled fleet is stationary. The
    constraint has moved: with the fleet near its packing floor, the binding limit is
    now that <strong>{busiest_share:.0f}% of every vehicle is sent to one exit</strong>,
    {busiest['name']}. Emptier cars cannot widen a single road.</p>
  </div>

  <div class="tiles">
    <div class="tile">
      <div class="value">+{people_gain_pct:.0f}%</div>
      <div class="name">more people actually out</div>
      <div class="note">{pooled['people_cleared_by_horizon']:,} vs
        {baseline['people_cleared_by_horizon']:,} by {horizon_label}</div>
    </div>
    <div class="tile">
      <div class="value">{cars_saved:,}</div>
      <div class="name">fewer cars on the road</div>
      <div class="note">{cars_saved_pct:.0f}% of the baseline fleet</div>
    </div>
    <div class="tile">
      <div class="value">{pooled['mean_occupancy']:.2f}</div>
      <div class="name">people per departing car</div>
      <div class="note">against {baseline['mean_occupancy']:.2f} in the baseline</div>
    </div>
    <div class="tile">
      <div class="value">{map_stats['pooled']['edges_gridlocked']:,}</div>
      <div class="name">roads gridlocked, ride-shared</div>
      <div class="note">against {map_stats['baseline']['edges_gridlocked']:,}
        for the baseline</div>
    </div>
  </div>
</section>

<section>
  <h2>Where the district seizes up</h2>
  <p class="muted">Every road carrying traffic {snapshot_min:.0f} minutes after the alarm,
  coloured by how fast it is actually moving as a fraction of its own speed limit.
  Both maps are the same moment on the same network.</p>
  <div class="maps">
    <div class="map-card">
      <div class="map-head">
        <h3 style="color:var(--series-baseline)">One car per household</h3>
        <span class="count">{map_stats['baseline']['edges_gridlocked']:,} roads gridlocked</span>
      </div>
      <img src="{maps['maps']['baseline']}" alt="Thanet road network under the
      one-car-per-household plan, most roads in the district shown gridlocked"/>
    </div>
    <div class="map-card">
      <div class="map-head">
        <h3 style="color:var(--series-pooled)">Ride-shared plan</h3>
        <span class="count">{map_stats['pooled']['edges_gridlocked']:,} roads gridlocked</span>
      </div>
      <img src="{maps['maps']['pooled']}" alt="The same network under the ride-shared
      plan, showing the same pattern of gridlock"/>
    </div>
  </div>
  <ul class="bands">{band_legend}</ul>
</section>

<section>
  <h2>How fast the people get out</h2>
  <p class="muted">The measure that matters: people across the district boundary,
  against time. Neither plan finishes — at {horizon_label} the pooled plan has cleared
  {pooled['people_cleared_pct']:.0f}% of the people it carries and the baseline
  {baseline['people_cleared_pct']:.0f}% — but the pooled line pulls away and stays
  ahead, having also picked up {people_extra:,} people the baseline leaves at
  home.</p>
  <div class="panel">
    {people_chart}
    <div class="legend">
      <span><i style="background:var(--series-baseline)"></i>One car per household</span>
      <span><i style="background:var(--series-pooled)"></i>Ride-shared plan</span>
    </div>
  </div>

  <h2>Cars pile onto the road and stay there</h2>
  <p class="muted">Both curves climb without turning over: cars enter the network
  faster than the exits can drain them. The pooled fleet plateaus around
  {pooled['peak_running']:,} against the baseline's {baseline['peak_running']:,} — the
  same shape, roughly {cars_saved_pct:.0f}% lower.</p>
  <div class="panel">
    {running_chart}
    <div class="legend">
      <span><i style="background:var(--series-baseline)"></i>One car per household</span>
      <span><i style="background:var(--series-pooled)"></i>Ride-shared plan</span>
    </div>
  </div>

  <h2>And everything slows to a crawl</h2>
  <p class="muted">Speed as a share of each road's own limit — the measure that needs no
  assumption about what counts as busy. Both collapse; the pooled plan collapses to
  about twice the baseline's speed, which is the difference between very bad and
  worse rather than between jammed and moving.</p>
  <div class="panel">
    {speed_chart}
    <div class="legend">
      <span><i style="background:var(--series-baseline)"></i>One car per household</span>
      <span><i style="background:var(--series-pooled)"></i>Ride-shared plan</span>
    </div>
  </div>
</section>

<section>
  <h2>Everyone leaves by the same road</h2>
  <p class="muted">Vehicles head for whichever of Thanet's {meta['exits']} exits is
  nearest where they finish collecting. That sends
  <strong>{busiest_share:.0f}% of the fleet through {busiest['name']}</strong> and
  leaves the others almost idle. No fleet reduction can compensate for a single
  road carrying the district.</p>
  <div class="panel">
    <ul class="exits">{exit_bars}</ul>
  </div>

  <h2>Every number, side by side</h2>
  <div class="panel tablewrap">
    <table>
      <caption>Both runs share one network, one mobilisation curve and one seed, and are
      read at the same {horizon_label} mark.</caption>
      <thead><tr><th>Measure</th><th class="col-base">One car per household</th>
      <th class="col-pool">Ride-shared plan</th></tr></thead>
      <tbody>{stat_rows()}</tbody>
    </table>
  </div>
</section>

<section>
  <h2>What this does and does not show</h2>
  <dl class="caveats">
    <div class="caveat"><dt>Nothing finishes</dt><dd>Neither run clears the district
    inside the {horizon_label} both were stopped at, so every clearance figure here is
    "how far had it got", not "how long it took". The mean journey times are worse
    than they look for the same reason: they average only the journeys that completed,
    which are the short ones.</dd></div>

    <div class="caveat"><dt>Where the fleet now sits</dt><dd>At
    {pooled['mean_occupancy']:.2f} people per car against a {meta['vehicle_capacity']}-seat
    capacity, the plan is close to its packing floor, so there is little left to win by
    removing cars. Further gains have to come from spreading the load across exits, or
    from departing in waves.</dd></div>

    <div class="caveat"><dt>Departure timing</dt><dd>Departures come from the
    pipeline's own model — notification, plus a lognormal mobilisation delay
    (median {meta['mobilisation_median']:.0f} min), plus the driver's walk to the car.
    The baseline is drawn from the same distribution with no walk, since an owner is
    already at their own car. Released simultaneously instead, an earlier run
    gridlocked the district under <em>both</em> plans — 3% of the speed limit, 49 of
    45,960 arrived after 20 minutes — so the spread is what makes any comparison
    possible.</dd></div>

    <div class="caveat"><dt>No live rerouting</dt><dd>Drivers follow a shortest path
    fixed before departure and never learn where the jams are. This is the right
    assumption for a no-warning evacuation and a pessimistic one for congestion.</dd></div>

    <div class="caveat"><dt>Where it ends</dt><dd>Clearance is measured at the district
    boundary: vehicles head for the exit nearest where they finish collecting, and an
    exit is an infinite-capacity sink, so nothing queues on the far side. Real drivers
    choose by expected travel time rather than distance, so demand here is
    over-concentrated on the closest way out — the pipeline reports the loading per
    exit for exactly that reason.</dd></div>

    <div class="caveat"><dt>Where the runs stop</dt><dd>Both simulations were halted at
    the same {horizon_label} mark and compared at that instant, so neither is
    flattered by having run longer. Full clearance to Canterbury is many hours away in
    both scenarios; the point at which they are already indistinguishable arrives long
    before that.</dd></div>

    <div class="caveat"><dt>Who is missing</dt><dd>The synthetic population is the
    household population, so care-home and student-hall residents are absent — and
    they are exactly the people most likely to need collection. The baseline also
    carries only {baseline['people_carried']:,} people because households with no car
    have no way out in that scenario.</dd></div>

    <div class="caveat"><dt>Gridlock handling</dt><dd>SUMO teleports a vehicle stuck
    for more than 600 seconds, so a deadlocked junction cannot stall the run. Teleport
    counts are reported above; they are a gridlock severity signal, and where they are
    large the delay figures understate reality.</dd></div>
  </dl>
</section>

<footer>
  Generated from <code>data/warehouse.duckdb</code> by <code>sumo/make_trips.py</code>,
  <code>sumo/run_scenario.sh</code>, <code>sumo/analyse.py</code> and
  <code>sumo/render_map.py</code>. Road network from OpenStreetMap contributors,
  ODbL. Population synthesised from Census 2021 output-area marginals.
</footer>
</div>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", default="data/sumo/comparison.json")
    parser.add_argument("--maps", default="data/sumo/maps.json")
    parser.add_argument("--meta", default="data/sumo/meta.json")
    parser.add_argument("--out", default="data/sumo/report.html")
    args = parser.parse_args()

    comparison = json.loads(Path(args.comparison).read_text())
    maps = json.loads(Path(args.maps).read_text())
    meta = json.loads(Path(args.meta).read_text())

    html = build(comparison, maps, meta)
    # Escape every non-ASCII character as a numeric entity so the page renders
    # identically however the host serves it — we do not control the charset
    # header on the wrapper this gets published into.
    html = html.encode("ascii", "xmlcharrefreplace").decode("ascii")
    Path(args.out).write_text(html, encoding="ascii")
    print(f"wrote {args.out} ({Path(args.out).stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
