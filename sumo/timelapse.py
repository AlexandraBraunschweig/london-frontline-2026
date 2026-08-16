"""The whole hour, minute by minute.

The report shows one snapshot at the worst moment. This renders every minute of
both runs side by side, so the jam can be watched forming instead of inferred
from a single frame: one PNG per minute, plus an animation stitched from them.

Frames are kept as files rather than only as an animation — a single minute is
often what you actually want to point at, and an animated GIF is a poor way to
get one out.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from render_map import (
    BANDS,
    UNUSED,
    band_colour,
    build_geometry,
    read_edgedata,
    _frame,
)

SCENARIOS = ("baseline", "pooled")
LABELS = {
    "baseline": "One car per household",
    "pooled": "Ride-shared plan",
}
TITLE_COLOUR = {"baseline": "#d2521c", "pooled": "#1f66bd"}


def _draw_panel(axes, shapes, backdrop, congestion, bbox, label, colour, subtitle):
    """One scenario's map into a prepared axes."""
    from matplotlib.collections import LineCollection

    min_x, min_y, max_x, max_y = bbox
    axes.set_xlim(min_x, max_x)
    axes.set_ylim(min_y, max_y)
    axes.set_axis_off()

    quiet = []
    busy: dict[str, list] = {colour_: [] for _, colour_, _, _ in BANDS}
    for edge_id, shape in shapes.items():
        if len(shape) < 2:
            continue
        relative = congestion.get(edge_id)
        if relative is None:
            if edge_id in backdrop:
                quiet.append(shape)
        else:
            busy[band_colour(relative)].append(shape)

    axes.add_collection(
        LineCollection(quiet, colors=UNUSED, linewidths=0.5, alpha=0.55)
    )
    for _, band, _, _ in reversed(BANDS):
        if busy[band]:
            axes.add_collection(
                LineCollection(busy[band], colors=band, linewidths=1.2)
            )

    axes.text(
        0.01, 0.98, label, transform=axes.transAxes, va="top", ha="left",
        fontsize=11, fontweight="bold", color=colour,
    )
    axes.text(
        0.01, 0.937, subtitle, transform=axes.transAxes, va="top", ha="left",
        fontsize=8.5, color="#6b747e", family="monospace",
    )


def render(
    net_path: str,
    data_dir: Path,
    frames_dir: Path,
    width: int = 1500,
    gif_name: str = "timelapse.gif",
    frame_ms: int = 320,
) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    import sumolib
    from PIL import Image

    out_dir = data_dir / "out"
    net = sumolib.net.readNet(net_path)
    shapes, backdrop, full_bbox = build_geometry(net)

    per_scenario = {
        scenario: read_edgedata(out_dir / f"{scenario}.edgedata.xml")[0]
        for scenario in SCENARIOS
    }

    # Every interval either run recorded, in order. Both runs end on the same
    # clock, so this is one shared timeline rather than two.
    keys = sorted(
        {key for intervals in per_scenario.values() for key in intervals},
        key=float,
    )
    if not keys:
        raise SystemExit("no edge data — run the simulations first")

    # One frame of reference for the whole animation: if the extent moved with
    # the traffic the map would appear to pan, and the two panels would not be
    # comparable frame to frame.
    used = {
        edge_id
        for intervals in per_scenario.values()
        for bucket in intervals.values()
        for edge_id in bucket
    }
    bbox = _frame(shapes, used, full_bbox)

    frames_dir.mkdir(parents=True, exist_ok=True)
    for stale in frames_dir.glob("frame_*.png"):
        stale.unlink()

    span_x = bbox[2] - bbox[0]
    span_y = bbox[3] - bbox[1]
    dpi = 100
    panel_h = width / 2 * span_y / span_x
    figure_h = panel_h + 54 / dpi * 100  # room for the caption strip

    written: list[Path] = []
    for index, key in enumerate(keys):
        minute = float(key) / 60.0
        figure = plt.figure(figsize=(width / dpi, figure_h / dpi), dpi=dpi)
        figure.patch.set_facecolor("#ffffff")

        for column, scenario in enumerate(SCENARIOS):
            congestion = per_scenario[scenario].get(key, {})
            jammed = sum(1 for v in congestion.values() if v < 0.25)
            axes = figure.add_axes(
                [column * 0.5, 0.0, 0.5, panel_h / figure_h]
            )
            _draw_panel(
                axes, shapes, backdrop, congestion, bbox,
                LABELS[scenario], TITLE_COLOUR[scenario],
                f"{jammed:,} roads gridlocked",
            )

        figure.text(
            0.5, 1 - 26 / figure_h, f"minute {minute:.0f}",
            ha="center", va="top", fontsize=15, fontweight="bold",
            color="#14181d",
        )
        # A progress rule, so a still frame still says where it sits in the hour.
        track_y = 1 - 46 / figure_h
        figure.add_artist(plt.Line2D(
            [0.06, 0.94], [track_y, track_y], color="#dfe3e7", linewidth=3,
            solid_capstyle="round",
        ))
        done = 0.06 + 0.88 * (index / max(len(keys) - 1, 1))
        figure.add_artist(plt.Line2D(
            [0.06, done], [track_y, track_y], color="#1f66bd", linewidth=3,
            solid_capstyle="round",
        ))

        path = frames_dir / f"frame_{index:03d}.png"
        figure.savefig(path, dpi=dpi, facecolor="#ffffff")
        plt.close(figure)
        written.append(path)
        if index % 10 == 0:
            print(f"  frame {index + 1}/{len(keys)} (minute {minute:.0f})")

    # Stitch. An adaptive palette keeps the reds and greens apart at 256 colours;
    # the default web palette muddles the congestion bands into each other.
    images = [Image.open(p).convert("RGB") for p in written]
    quantised = [
        im.convert("P", palette=Image.ADAPTIVE, colors=192) for im in images
    ]
    gif_path = data_dir / gif_name
    quantised[0].save(
        gif_path,
        save_all=True,
        append_images=quantised[1:],
        duration=frame_ms,
        loop=0,
        optimize=True,
        disposal=2,
    )
    for im in images:
        im.close()

    return {
        "frames": len(written),
        "frames_dir": str(frames_dir),
        "gif": str(gif_path),
        "gif_mb": round(gif_path.stat().st_size / 1e6, 1),
        "minutes": [float(k) / 60.0 for k in keys],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--net", default="data/sumo/thanet.net.xml")
    parser.add_argument("--data-dir", default="data/sumo")
    parser.add_argument("--frames-dir", default="data/sumo/frames")
    parser.add_argument("--width", type=int, default=1500)
    args = parser.parse_args()

    result = render(
        args.net, Path(args.data_dir), Path(args.frames_dir), width=args.width
    )
    print(f"wrote {result['frames']} frames to {result['frames_dir']}")
    print(f"wrote {result['gif']} ({result['gif_mb']} MB)")


if __name__ == "__main__":
    main()
