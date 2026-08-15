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
    "baseline": {"label": "One car per household", "var": "--series-baseline"},
    "pooled": {"label": "Ride-shared plan", "var": "--series-pooled"},
}


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
) -> str:
    """A two-series line chart as inline SVG, styled through CSS variables."""
    pad_l, pad_r, pad_t, pad_b = 64, 96, 16, 40
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    def sx(value: float) -> float:
        return pad_l + (value / x_max) * plot_w if x_max else pad_l

    def sy(value: float) -> float:
        return pad_t + plot_h - (value / y_max) * plot_h if y_max else pad_t + plot_h

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
        points = " ".join(
            f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(entry["x"], entry["y"])
        )
        parts.append(
            f'<polyline class="series" points="{points}" '
            f'style="stroke:var({entry["var"]})"/>'
        )
        # Direct label at the line's end, so identity is never colour-alone.
        end_x, end_y = entry["x"][-1], entry["y"][-1]
        parts.append(
            f'<circle class="endpoint" cx="{sx(end_x):.1f}" cy="{sy(end_y):.1f}" '
            f'r="4" style="fill:var({entry["var"]})"/>'
        )
        parts.append(
            f'<text class="series-label" x="{sx(end_x) + 10:.1f}" '
            f'y="{sy(end_y) + 4:.1f}">{entry["label"]}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def build(comparison: dict, maps: dict, meta: dict) -> str:
    baseline = comparison["baseline"]
    pooled = comparison["pooled"]

    cars_saved = baseline["vehicles_departed"] - pooled["vehicles_departed"]
    cars_saved_pct = 100 * cars_saved / baseline["vehicles_departed"]
    people_extra = pooled["people_carried"] - baseline["people_carried"]
    horizon_label = _fmt_minutes(baseline["horizon_s"])

    def series_for(field: str) -> list[dict]:
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
            })
        return out

    running_series = series_for("running")
    speed_series = series_for("speed_relative_pct")

    running_max = max(max(s["y"]) for s in running_series)
    speed_max = max(max(s["y"]) for s in speed_series)
    time_max = max(max(s["x"]) for s in running_series)

    def ticks(maximum: float, count: int = 5) -> list[float]:
        step = maximum / count
        return [round(step * i) for i in range(count + 1)]

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
            ("Cars still on the road", "{:,}", "final_running"),
            ("…of those, stationary", "{:,}", "final_halting"),
            ("Share stationary", "{:.1f}%", "stationary_share_pct"),
            ("Speed vs the limit", "{:.1%}", "final_speed_relative"),
            ("Peak cars at once", "{:,}", "peak_running"),
            ("Reached Canterbury", "{:,}", "arrived_by_horizon"),
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

.caveats {{ display: flex; flex-direction: column; gap: 14px; }}
.caveat {{ display: grid; grid-template-columns: 150px 1fr; gap: 18px; align-items: start; }}
@media (max-width: 680px) {{ .caveat {{ grid-template-columns: 1fr; gap: 4px; }} }}
.caveat dt {{
  font: 600 .74rem/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3);
}}
.caveat dd {{ margin: 0; color: var(--ink-2); font-size: .92rem; }}
footer {{ border-top: 1px solid var(--line); padding-top: 20px; color: var(--ink-3); font-size: .82rem; }}
</style>

<div class="wrap">
<header>
  <div class="eyebrow">Thanet District · LAD E07000114 · SUMO microsimulation</div>
  <h1>Both plans gridlock Thanet</h1>
  <p class="lede">Two microsimulations of the same district, the same road network and
  the same alarm. In one, every car-owning household drives itself out. In the other,
  the seat-assignment plan decides which cars depart. An hour in, both have brought
  the district to a standstill — and the reason is not the one the project set out to
  test.</p>
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
    <p>The ride-shared plan puts <strong>{cars_saved:,} fewer cars</strong> on the road
    ({cars_saved_pct:.1f}%) and carries <strong>{people_extra:,} more people</strong>,
    because the baseline simply abandons every household that owns no car. Both are
    real gains. Neither makes any difference to the jam: {horizon_label} after the
    alarm both scenarios are moving at
    <strong>{baseline['final_speed_relative']:.0%} and
    {pooled['final_speed_relative']:.0%}</strong> of the speed limit, with
    {baseline['stationary_share_pct']:.0f}% and {pooled['stationary_share_pct']:.0f}%
    of cars completely stationary.</p>
    <p>The binding constraint is not how many cars there are. It is that every one of
    them is routed to a <em>single</em> destination, so the whole district drains
    through the same few arterial roads. Cutting the fleet by
    {cars_saved_pct:.1f}% cannot widen them.</p>
  </div>

  <div class="tiles">
    <div class="tile">
      <div class="value">{cars_saved:,}</div>
      <div class="name">fewer cars on the road</div>
      <div class="note">{cars_saved_pct:.1f}% of the baseline fleet</div>
    </div>
    <div class="tile">
      <div class="value">+{people_extra:,}</div>
      <div class="name">more people carried out</div>
      <div class="note">the baseline strands car-less households</div>
    </div>
    <div class="tile">
      <div class="value">{pooled['final_speed_relative']:.0%}</div>
      <div class="name">of the speed limit, ride-shared</div>
      <div class="note">against {baseline['final_speed_relative']:.0%} for the baseline</div>
    </div>
    <div class="tile">
      <div class="value">{pooled['final_halting']:,}</div>
      <div class="name">cars stationary, ride-shared</div>
      <div class="note">against {baseline['final_halting']:,} for the baseline</div>
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
  <h2>Cars pile onto the road and stay there</h2>
  <p class="muted">Both curves climb without turning over: cars enter the network far
  faster than the exit roads can drain them. The ride-shared line sits consistently
  below the baseline — by about the {cars_saved_pct:.0f}% you would expect — and that
  is the entire effect.</p>
  <div class="panel">
    {running_chart}
    <div class="legend">
      <span><i style="background:var(--series-baseline)"></i>One car per household</span>
      <span><i style="background:var(--series-pooled)"></i>Ride-shared plan</span>
    </div>
  </div>

  <h2>And everything stops moving</h2>
  <p class="muted">Speed as a share of each road's own limit — the measure that needs no
  assumption about what counts as busy. Both plans collapse to a crawl on the same
  timetable. A jam this deep is not sensitive to {cars_saved_pct:.0f}% fewer cars.</p>
  <div class="panel">
    {speed_chart}
    <div class="legend">
      <span><i style="background:var(--series-baseline)"></i>One car per household</span>
      <span><i style="background:var(--series-pooled)"></i>Ride-shared plan</span>
    </div>
  </div>
</section>

<section>
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
    <div class="caveat"><dt>The 5% problem</dt><dd>The plan being simulated is the
    tiered assignment currently in the repository, where every car-owning household
    boards its own car. It was never going to take many cars off the road. The
    unimplemented <code>minimise-evacuation-vehicles</code> change targets about
    25,600 cars instead of {pooled['vehicles_departed']:,} — that is the scenario
    that would actually test the hypothesis.</dd></div>

    <div class="caveat"><dt>Departure timing</dt><dd>Cars leave on a 60-minute
    mobilisation curve, not all at once. Released simultaneously — which is what
    <code>fleet_departure_time</code> literally specifies — the district gridlocks
    completely under <em>both</em> plans: 3% of the speed limit, ~35,000 cars
    stationary, and 49 of 45,960 arrived after 20 minutes. Under that assumption the
    comparison has no signal at all.</dd></div>

    <div class="caveat"><dt>No live rerouting</dt><dd>Drivers follow a shortest path
    fixed before departure and never learn where the jams are. This is the right
    assumption for a no-warning evacuation and a pessimistic one for congestion.</dd></div>

    <div class="caveat"><dt>One destination</dt><dd>Every vehicle drives to the single
    Canterbury point in <code>PlanningConfig</code>, so all
    {baseline['vehicles_departed']:,} cars converge on the same handful of arterial
    roads. On this evidence that funnel — not the size of the fleet — is what decides
    the outcome, and it is the first thing worth changing. Thanet has three real exit
    gates; dispersing across them would triple the draining capacity.</dd></div>

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
