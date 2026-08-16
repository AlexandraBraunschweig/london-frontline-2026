"""Every chart in the report, also written out on its own.

The report is one page; a slide deck, a memo or an email needs one figure. These
are the same charts from the same JSON — not redrawn by hand — so a figure lifted
out of here cannot drift from the page it came from.

Charts are SVG (sharp at any size, and editable), maps are PNG (they are rasters
already), and the comparison table is CSV so the numbers can be re-used rather
than retyped.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
from pathlib import Path

from build_report import (
    SERIES,
    STANDALONE_STYLE,
    _line_chart,
    arrivals_for,
    series_for,
    ticks,
)

TABLE_ROWS = [
    ("Cars sent out", "vehicles_departed"),
    ("People carried", "people_carried"),
    ("Mean people per car", "mean_occupancy"),
    ("People clear of the district", "people_cleared_by_horizon"),
    ("Share of carried people clear (%)", "people_cleared_pct"),
    ("Cars clear of the district", "vehicles_arrived"),
    ("Cars still on the road", "final_running"),
    ("Cars stationary", "final_halting"),
    ("Share stationary (%)", "stationary_share_pct"),
    ("Speed as a share of the limit", "final_speed_relative"),
    ("Peak cars at once", "peak_running"),
    ("Gridlock teleports", "teleports"),
]


def _exit_chart(loading: list[dict], width: int = 760) -> str:
    """Horizontal bars for how the fleet splits across the ways out."""
    row_h, pad_t, pad_l, pad_r = 46, 44, 210, 90
    height = pad_t + row_h * len(loading) + 16
    total = sum(row["vehicles"] for row in loading) or 1
    track_w = width - pad_l - pad_r

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="vehicles per district exit">',
        f"<style>{STANDALONE_STYLE}"
        ".exit-name{font:500 13px ui-sans-serif,system-ui,sans-serif;fill:#14181d}"
        ".exit-class{font:500 10px ui-monospace,SFMono-Regular,Menlo,monospace;"
        "fill:#79828d;letter-spacing:.06em}"
        ".exit-count{font:500 13px ui-sans-serif,system-ui,sans-serif;fill:#4d555f;"
        "text-anchor:end}"
        "</style>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        '<text class="chart-title" x="14" y="26">Vehicles per district exit</text>',
    ]
    for index, row in enumerate(loading):
        y = pad_t + index * row_h
        share = row["vehicles"] / total
        parts.append(
            f'<text class="exit-name" x="14" y="{y + 16}">{row["name"]}</text>'
        )
        parts.append(
            f'<text class="exit-class" x="14" y="{y + 31}">'
            f'{row["highway"].upper()}</text>'
        )
        parts.append(
            f'<rect x="{pad_l}" y="{y + 4}" width="{track_w}" height="20" rx="3" '
            f'fill="#eceef0"/>'
        )
        parts.append(
            f'<rect x="{pad_l}" y="{y + 4}" width="{max(track_w * share, 2):.1f}" '
            f'height="20" rx="3" fill="{SERIES["pooled"]["colour"]}"/>'
        )
        parts.append(
            f'<text class="exit-count" x="{width - 14}" y="{y + 19}">'
            f'{row["vehicles"]:,}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def write_all(comparison: dict, maps: dict, meta: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def emit(name: str, text: str) -> None:
        path = out_dir / name
        path.write_text(text.encode("ascii", "xmlcharrefreplace").decode("ascii"),
                        encoding="ascii")
        written.append(path)

    people = arrivals_for(comparison, "people")
    running = series_for(comparison, "running")
    speed = series_for(comparison, "speed_relative_pct")

    people_max = max(max(s["y"]) for s in people)
    people_t = max(max(s["x"]) for s in people)
    emit("fig_people_cleared.svg", _line_chart(
        people, x_max=people_t, y_max=people_max,
        x_ticks=ticks(people_t, 6), y_ticks=ticks(people_max),
        x_label="minutes after the alarm",
        y_label="people clear of the district",
        y_format=lambda v: f"{v / 1000:,.0f}k" if v else "0",
        standalone=True, title="People clear of the district",
    ))

    running_max = max(max(s["y"]) for s in running)
    running_t = max(max(s["x"]) for s in running)
    emit("fig_cars_on_road.svg", _line_chart(
        running, x_max=running_t, y_max=running_max,
        x_ticks=ticks(running_t, 6), y_ticks=ticks(running_max),
        x_label="minutes after the alarm", y_label="cars on the road",
        standalone=True, title="Cars on the road",
    ))

    speed_max = max(max(s["y"]) for s in speed)
    speed_t = max(max(s["x"]) for s in speed)
    emit("fig_speed.svg", _line_chart(
        speed, x_max=speed_t, y_max=speed_max,
        x_ticks=ticks(speed_t, 6), y_ticks=ticks(speed_max),
        x_label="minutes after the alarm",
        y_label="speed as a share of the limit",
        y_format=lambda v: f"{v:,.0f}%",
        standalone=True, title="Speed as a share of the limit",
    ))

    if meta.get("exit_loading"):
        emit("fig_exit_loading.svg", _exit_chart(meta["exit_loading"]))

    # Maps come back from render_map as data URIs; write the bytes out plainly.
    for scenario, uri in maps["maps"].items():
        payload = uri.split(",", 1)[1]
        path = out_dir / f"map_{scenario}.png"
        path.write_bytes(base64.b64decode(payload))
        written.append(path)

    # The table, so the numbers can be re-used without retyping them.
    csv_path = out_dir / "comparison.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["measure", "one_car_per_household", "ride_shared_plan"])
        for label, key in TABLE_ROWS:
            writer.writerow([label, comparison["baseline"][key],
                             comparison["pooled"][key]])
        writer.writerow(["horizon (minutes after notification)",
                         round(comparison["baseline"]["horizon_s"] / 60),
                         round(comparison["pooled"]["horizon_s"] / 60)])
    written.append(csv_path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", default="data/sumo/comparison.json")
    parser.add_argument("--maps", default="data/sumo/maps.json")
    parser.add_argument("--meta", default="data/sumo/meta.json")
    parser.add_argument("--out-dir", default="data/sumo/figures")
    args = parser.parse_args()

    written = write_all(
        json.loads(Path(args.comparison).read_text()),
        json.loads(Path(args.maps).read_text()),
        json.loads(Path(args.meta).read_text()),
        Path(args.out_dir),
    )
    for path in written:
        print(f"  {path}  ({path.stat().st_size / 1024:.0f} kB)")
    print(f"wrote {len(written)} files to {args.out_dir}")


if __name__ == "__main__":
    main()
