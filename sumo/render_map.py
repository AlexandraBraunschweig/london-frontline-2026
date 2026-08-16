"""Draw the road network coloured by how badly each road is moving.

SUMO's ``speedRelative`` — realised speed as a fraction of the road's own limit
— is the jam measure. It needs no normalisation between scenarios and no
assumption about what counts as busy: 1.0 is free-flowing, 0.1 is a car park.

Emits one raster map per scenario, in the same projected frame and on the same colour
scale, so the two maps can be read against each other directly.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import sumolib

# Congestion bands, worst first. Colours are a status ramp, not a categorical
# one: traffic maps are read by convention, and inventing a new scale here would
# cost more in legibility than it gained in novelty.
BANDS = [
    (0.25, "#c0202a", "Gridlocked", "under 25% of the limit"),
    (0.50, "#e8641c", "Crawling", "25–50%"),
    (0.75, "#eda100", "Slowed", "50–75%"),
    (1.01, "#2f9e5f", "Free-flowing", "over 75%"),
]
UNUSED = "#d7d5d0"


def band_colour(speed_relative: float) -> str:
    for ceiling, colour, _, _ in BANDS:
        if speed_relative < ceiling:
            return colour
    return BANDS[-1][1]


def read_edgedata(path: Path) -> tuple[dict[str, dict[str, float]], list[float]]:
    """Per-interval speedRelative by edge, plus the interval start times."""
    per_interval: dict[str, dict[str, float]] = {}
    starts: list[float] = []
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag != "interval":
            continue
        begin = float(element.get("begin"))
        starts.append(begin)
        key = f"{begin:.0f}"
        bucket: dict[str, float] = {}
        for edge in element.findall("edge"):
            relative = edge.get("speedRelative")
            if relative is not None:
                bucket[edge.get("id")] = float(relative)
        per_interval[key] = bucket
        element.clear()
    return per_interval, starts


def worst_interval(per_interval: dict[str, dict[str, float]]) -> str:
    """The interval with the most jammed road, i.e. the peak of the jam."""
    best_key, best_score = None, -1.0
    for key, bucket in per_interval.items():
        score = sum(1 for v in bucket.values() if v < 0.5)
        if score > best_score:
            best_key, best_score = key, score
    return best_key or "0"


# Road classes drawn as the unloaded backdrop. Every residential street in the
# district would be 27,000 polylines per map — enough to stall the renderer for
# geography the reader does not need. The arterial skeleton locates the jam just
# as well, and congested roads are drawn whatever their class.
BACKDROP_TYPES = ("motorway", "trunk", "primary", "secondary", "tertiary")


def build_geometry(net: sumolib.net.Net) -> tuple[dict[str, list], set[str], tuple]:
    """Edge id -> projected polyline, the backdrop subset, and the bounding box."""
    shapes: dict[str, list] = {}
    backdrop: set[str] = set()
    for edge in net.getEdges():
        if edge.isSpecial() or not edge.allows("passenger"):
            continue
        edge_id = edge.getID()
        shapes[edge_id] = edge.getShape()
        if any(kind in edge.getType() for kind in BACKDROP_TYPES):
            backdrop.add(edge_id)
    xs = [p[0] for s in shapes.values() for p in s]
    ys = [p[1] for s in shapes.values() for p in s]
    return shapes, backdrop, (min(xs), min(ys), max(xs), max(ys))


def _frame(
    shapes: dict[str, list], used: set[str], fallback: tuple, margin: float = 0.04
) -> tuple:
    """Bounding box around the roads in use, with a little air around them."""
    points = [p for edge_id in used for p in shapes.get(edge_id, ())]
    if not points:
        return fallback
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    pad_x = (max_x - min_x) * margin
    pad_y = (max_y - min_y) * margin
    return (min_x - pad_x, min_y - pad_y, max_x + pad_x, max_y + pad_y)


def render_png(
    shapes: dict[str, list],
    backdrop: set[str],
    bbox: tuple,
    congestion: dict[str, float],
    width: int,
) -> str:
    """One map as a base64 PNG data URI.

    Drawn as a raster rather than as SVG on purpose. Two maps of this network
    are roughly 16,000 separate polylines, and a browser asked to lay all of
    them out leaves the page blank while it works — verified, not assumed. A
    LineCollection rasterised once here renders instantly everywhere.
    """
    import base64
    import io

    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.collections import LineCollection
    from matplotlib import pyplot as plt

    min_x, min_y, max_x, max_y = bbox
    span_x, span_y = max_x - min_x, max_y - min_y
    height = width * span_y / span_x

    quiet_segments: list = []
    # Grouped by band so congested roads can be drawn last, on top.
    busy: dict[str, list] = {colour: [] for _, colour, _, _ in BANDS}
    for edge_id, shape in shapes.items():
        if len(shape) < 2:
            continue
        relative = congestion.get(edge_id)
        if relative is None:
            if edge_id in backdrop:
                quiet_segments.append(shape)
        else:
            busy[band_colour(relative)].append(shape)

    dpi = 100
    figure = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    axes = figure.add_axes([0, 0, 1, 1])
    axes.set_xlim(min_x, max_x)
    axes.set_ylim(min_y, max_y)
    axes.set_axis_off()
    axes.set_facecolor("none")

    axes.add_collection(
        LineCollection(quiet_segments, colors=UNUSED, linewidths=0.6, alpha=0.55)
    )
    # Worst last: a gridlocked road should never be hidden by a free-flowing one.
    for ceiling, colour, _, _ in reversed(BANDS):
        if busy[colour]:
            axes.add_collection(
                LineCollection(busy[colour], colors=colour, linewidths=1.4)
            )

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=dpi, transparent=True,
                   bbox_inches=None, pad_inches=0)
    plt.close(figure)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def render_maps(
    net_path: str = "data/sumo/thanet.net.xml",
    data_dir="data/sumo",
    out="data/sumo/maps.json",
    width: int = 1600,
) -> dict:
    """Draw both congestion maps at the same worst moment, on one frame."""

    out_dir = Path(data_dir) / "out"
    out = Path(out)
    net = sumolib.net.readNet(net_path)
    shapes, backdrop, bbox = build_geometry(net)
    print(f"network: {len(shapes):,} drivable edges, "
          f"{len(backdrop):,} drawn as backdrop")

    # Both scenarios are drawn at the same clock time — the baseline's peak — so
    # the pair is a like-for-like snapshot rather than each at its own worst.
    baseline_intervals, _ = read_edgedata(out_dir / "baseline.edgedata.xml")
    snapshot_key = worst_interval(baseline_intervals)
    print(f"peak interval: t={snapshot_key}s")

    pooled_intervals, _ = read_edgedata(out_dir / "pooled.edgedata.xml")
    per_scenario = {"baseline": baseline_intervals, "pooled": pooled_intervals}

    # Frame both maps on the roads that actually carry traffic in either
    # scenario, not on the whole downloaded extract — otherwise most of the
    # picture is empty countryside and the district is a thumbnail in a corner.
    used = {
        edge_id
        for intervals in per_scenario.values()
        for edge_id in intervals.get(snapshot_key, {})
    }
    bbox = _frame(shapes, used, bbox)

    result = {"snapshot_s": float(snapshot_key), "maps": {}, "stats": {}}
    for scenario in ("baseline", "pooled"):
        congestion = per_scenario[scenario].get(snapshot_key, {})
        values = np.array(list(congestion.values())) if congestion else np.array([])
        result["maps"][scenario] = render_png(
            shapes, backdrop, bbox, congestion, width
        )
        result["stats"][scenario] = {
            "edges_carrying_traffic": int(values.size),
            "edges_gridlocked": int((values < 0.25).sum()),
            "edges_crawling": int(((values >= 0.25) & (values < 0.5)).sum()),
            "edges_slowed": int(((values >= 0.5) & (values < 0.75)).sum()),
            "edges_free": int((values >= 0.75).sum()),
            "median_speed_relative": float(np.median(values)) if values.size else 0.0,
        }
        s = result["stats"][scenario]
        print(f"{scenario:9s} {s['edges_carrying_traffic']:6,} edges in use, "
              f"{s['edges_gridlocked']:5,} gridlocked, "
              f"median {s['median_speed_relative']:.2f} of the limit")

    result["bands"] = [
        {"ceiling": c, "colour": col, "label": lab, "detail": det}
        for c, col, lab, det in BANDS
    ]
    out.write_text(json.dumps(result))
    size_mb = out.stat().st_size / 1e6
    print(f"wrote {out} ({size_mb:.1f} MB)")


    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--net", default="data/sumo/thanet.net.xml")
    parser.add_argument("--data-dir", default="data/sumo")
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--out", default="data/sumo/maps.json")
    args = parser.parse_args()
    render_maps(args.net, args.data_dir, args.out, args.width)


if __name__ == "__main__":
    main()
