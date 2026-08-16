"""Render the slide deck's figures straight from the warehouse.

Every number and every mark in `deck/figures/` comes from `data/warehouse.duckdb`,
read-only, so the deck cannot drift from the run it describes. Re-run after any
materialisation:

    uv run python deck/make_figures.py

SVG is written by hand rather than by a plotting library: the pipeline has no
plotting dependency and does not need one for five charts. Dense point layers are
emitted as a single <path> of tiny squares rather than 70,000 <circle> elements,
which is roughly a fifth of the bytes and one element for the browser to lay out.

Colours are the data-viz dark-mode tokens, matching the deck's surface (#1a1a19).
The fleet map uses emphasis encoding — the activated fleet in the series hue,
everything else in muted ink — rather than two categorical hues, because the two
classes are "the answer" and "the background it stands out from".
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import duckdb

REPO = Path(__file__).resolve().parent.parent
WAREHOUSE = REPO / "data" / "warehouse.duckdb"
FIGURES = Path(__file__).resolve().parent / "figures"

# --- Tokens (data-viz dark mode, surface #1a1a19) --------------------------
INK = "#ffffff"
SECONDARY = "#c3c2b7"
MUTED = "#898781"
GRID = "#2c2c2a"
BASELINE = "#383835"
SERIES = "#3987e5"  # categorical slot 1, dark step
CRITICAL = "#d03b3b"  # status: people left behind
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


# --- Tiny SVG helper -------------------------------------------------------
class Svg:
    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height
        self.parts: list[str] = []

    def add(self, markup: str) -> None:
        self.parts.append(markup)

    def text(
        self,
        x: float,
        y: float,
        content: str,
        *,
        size: float = 15,
        fill: str = SECONDARY,
        anchor: str = "start",
        weight: str = "400",
        tabular: bool = False,
    ) -> None:
        numeric = ' font-variant-numeric="tabular-nums"' if tabular else ""
        self.add(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family=\'{FONT}\' font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{numeric}>'
            f"{escape(content)}</text>"
        )

    def render(self) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.width:.0f} '
            f'{self.height:.0f}" width="{self.width:.0f}" height="{self.height:.0f}">'
            + "".join(self.parts)
            + "</svg>"
        )

    def write(self, name: str) -> None:
        FIGURES.mkdir(parents=True, exist_ok=True)
        path = FIGURES / name
        path.write_text(self.render(), encoding="utf-8")
        print(f"  {name:24s} {path.stat().st_size / 1024:7.0f} KB")


@dataclass
class Projection:
    """Equirectangular lon/lat to pixels. Exact enough over one district.

    With ``metric=True`` the inputs are British National Grid eastings and
    northings instead, which are already in metres, so no cosine correction
    applies. Street-level figures use that form: the road centrelines are
    published in EPSG:27700, and reprojecting them to agree with a lon/lat
    layer is a way to introduce a misalignment for no benefit.
    """

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float
    width: float
    height: float
    pad: float = 0.0
    metric: bool = False

    def __post_init__(self) -> None:
        mid_lat = math.radians((self.min_lat + self.max_lat) / 2)
        self.kx = 1.0 if self.metric else math.cos(mid_lat)
        span_x = (self.max_lon - self.min_lon) * self.kx
        span_y = self.max_lat - self.min_lat
        inner_w = self.width - 2 * self.pad
        inner_h = self.height - 2 * self.pad
        self.scale = min(inner_w / span_x, inner_h / span_y)
        self.dx = self.pad + (inner_w - span_x * self.scale) / 2
        self.dy = self.pad + (inner_h - span_y * self.scale) / 2

    def x(self, lon: float) -> float:
        return self.dx + (lon - self.min_lon) * self.kx * self.scale

    def y(self, lat: float) -> float:
        return self.dy + (self.max_lat - lat) * self.scale


def dot_path(points, proj: Projection, size: float) -> str:
    """One <path> of `size`-px squares, deduplicated on the pixel grid.

    Points that land on the same pixel are indistinguishable once drawn, so
    collapsing them costs nothing visually and cuts the file several-fold.
    """
    seen = set()
    d = []
    half = size / 2
    for lon, lat in points:
        px = round(proj.x(lon), 1)
        py = round(proj.y(lat), 1)
        if (px, py) in seen:
            continue
        seen.add((px, py))
        d.append(f"M{px - half:.1f} {py - half:.1f}h{size:g}v{size:g}h-{size:g}z")
    return "".join(d)


def nice_ticks(maximum: float, count: int = 4) -> list[float]:
    """Round tick values up to `maximum` — 1,000 reads; 2,826 is just the max."""
    raw = maximum / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    ticks = []
    value = step
    while value <= maximum:
        ticks.append(value)
        value += step
    return ticks


def connect() -> duckdb.DuckDBPyConnection:
    if not WAREHOUSE.exists():
        raise SystemExit(
            f"{WAREHOUSE} not found. Materialise the pipeline first: "
            "uv run dagster asset materialize -m london_frontline.definitions --select '*'"
        )
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    con.execute("INSTALL spatial; LOAD spatial;")
    return con


# --- Figures ---------------------------------------------------------------
def outline_path(con: duckdb.DuckDBPyConnection, proj: Projection) -> str:
    """The district boundary as one SVG path, in the given projection."""
    geometry = json.loads(
        con.execute("SELECT ST_AsGeoJSON(geometry) FROM admin_areas WHERE level = 'lad'").fetchone()[0]
    )
    polys = (
        geometry["coordinates"]
        if geometry["type"] == "MultiPolygon"
        else [geometry["coordinates"]]
    )
    d = []
    for poly in polys:
        for ring in poly:
            d.append(
                "M"
                + " L".join(f"{proj.x(lon):.1f} {proj.y(lat):.1f}" for lon, lat in ring)
                + "Z"
            )
    return " ".join(d)


def fleet_map(con: duckdb.DuckDBPyConnection) -> None:
    """Cars owned against cars that depart, as small multiples.

    Two panels rather than one two-colour map: at district scale the departing
    fleet sits on the same streets as the fleet that stays, so overplotting one
    on the other hides exactly the difference the figure exists to show. Same
    projection, same dot size, same district outline — only the point set changes.
    """
    parked = con.execute(
        "SELECT ST_X(geometry), ST_Y(geometry) FROM layer_fleet WHERE NOT activated"
    ).fetchall()
    active = con.execute(
        "SELECT ST_X(geometry), ST_Y(geometry) FROM layer_fleet WHERE activated"
    ).fetchall()
    everything = parked + active

    panel_w, panel_h = 770, 420
    gap, top = 34, 44
    width, height = panel_w * 2 + gap, top + panel_h
    lons = [p[0] for p in everything]
    lats = [p[1] for p in everything]
    svg = Svg(width, height)

    panels = [
        (0, "Cars owned", everything, MUTED, 0.75),
        (panel_w + gap, "Cars that depart", active, SERIES, 0.9),
    ]
    for offset, title, points, colour, opacity in panels:
        proj = Projection(min(lons), min(lats), max(lons), max(lats), panel_w, panel_h, pad=8)
        proj.dx += offset
        proj.dy += top
        svg.text(offset + 4, 22, title, size=19, fill=INK, weight="600")
        svg.text(offset + panel_w - 4, 22, f"{len(points):,}", size=19,
                 fill=colour if colour == SERIES else SECONDARY, anchor="end", weight="600")
        svg.add(
            f'<path d="{outline_path(con, proj)}" fill="#131312" stroke="{BASELINE}" '
            f'stroke-width="1" fill-rule="evenodd"/>'
        )
        svg.add(f'<path d="{dot_path(points, proj, 1.5)}" fill="{colour}" opacity="{opacity}"/>')

    svg.write("fleet-map.svg")


def street_zoom(con: duckdb.DuckDBPyConnection) -> None:
    """A few streets, close enough to see households walking to a shared car.

    Roads are drawn underneath because without them the walk lines read as
    abstract spaghetti rather than as people crossing a street.
    """
    # Pick the busiest window automatically rather than by eye, so the figure
    # survives a re-run against different data. Everything here is in metres on
    # the British National Grid.
    span_x, span_y = 900.0, 320.0
    cx, cy = con.execute(
        """
        SELECT median(m.easting), median(m.northing)
        FROM muster_points m
        WHERE m.area_id = (
            SELECT area_id FROM layer_oa_summary
            ORDER BY cars_activated / nullif(baseline_cars, 0) DESC, people DESC
            LIMIT 1)
        """
    ).fetchone()
    west, south, east, north = cx - span_x / 2, cy - span_y / 2, cx + span_x / 2, cy + span_y / 2

    roads = con.execute(
        "SELECT ST_AsGeoJSON(geometry) FROM road_centrelines "
        "WHERE ST_Intersects(geometry, ST_MakeEnvelope(?, ?, ?, ?))",
        [west, south, east, north],
    ).fetchall()
    walks = con.execute(
        """
        SELECT h.easting, h.northing, m.easting, m.northing, w.own_car
        FROM arc_walks w
        JOIN household_locations h USING (household_id)
        JOIN muster_points m USING (muster_point_id)
        WHERE h.easting BETWEEN ? AND ? AND h.northing BETWEEN ? AND ?
          AND m.easting BETWEEN ? AND ? AND m.northing BETWEEN ? AND ?
        """,
        [west, east, south, north, west, east, south, north],
    ).fetchall()
    cars = con.execute(
        """
        SELECT m.easting, m.northing, f.activated, f.occupants
        FROM layer_fleet f JOIN muster_points m USING (muster_point_id)
        WHERE m.easting BETWEEN ? AND ? AND m.northing BETWEEN ? AND ?
        """,
        [west, east, south, north],
    ).fetchall()

    width = 1180
    height = round(width * span_y / span_x) + 46  # the map, plus the legend bar
    proj = Projection(west, south, east, north, width, height - 46, metric=True)
    svg = Svg(width, height)
    svg.add(f'<clipPath id="frame"><rect width="{width}" height="{height}"/></clipPath>')
    svg.add('<g clip-path="url(#frame)">')

    for (geojson,) in roads:
        line = json.loads(geojson)
        segments = (
            line["coordinates"] if line["type"] == "MultiLineString" else [line["coordinates"]]
        )
        for coords in segments:
            d = "M" + " L".join(f"{proj.x(lon):.1f} {proj.y(lat):.1f}" for lon, lat in coords)
            svg.add(f'<path d="{d}" fill="none" stroke="{BASELINE}" stroke-width="9" stroke-linecap="round"/>')

    for lon1, lat1, lon2, lat2, own in walks:
        svg.add(
            f'<line x1="{proj.x(lon1):.1f}" y1="{proj.y(lat1):.1f}" '
            f'x2="{proj.x(lon2):.1f}" y2="{proj.y(lat2):.1f}" '
            f'stroke="{SECONDARY}" stroke-width="0.9" opacity="0.4"/>'
        )
    for lon, lat, activated, occupants in cars:
        if activated:
            svg.add(
                f'<circle cx="{proj.x(lon):.1f}" cy="{proj.y(lat):.1f}" r="5" '
                f'fill="{SERIES}" stroke="#1a1a19" stroke-width="2"/>'
            )
        else:
            svg.add(
                f'<circle cx="{proj.x(lon):.1f}" cy="{proj.y(lat):.1f}" r="3" '
                f'fill="none" stroke="{MUTED}" stroke-width="1.3" opacity="0.8"/>'
            )
    svg.add("</g>")

    # Legend on a full-width plate, so nothing has to be read through the map.
    bar = 46
    svg.add(f'<rect x="0" y="{height - bar}" width="{width}" height="{bar}" fill="#1a1a19"/>')
    svg.add(f'<circle cx="12" cy="{height - bar / 2}" r="5" fill="{SERIES}"/>')
    svg.text(26, height - bar / 2 + 5, f"{sum(1 for c in cars if c[2]):,} cars depart",
             size=17, fill=INK, weight="600")
    svg.add(f'<circle cx="196" cy="{height - bar / 2}" r="3" fill="none" stroke="{MUTED}" stroke-width="1.3"/>')
    svg.text(210, height - bar / 2 + 5, f"{sum(1 for c in cars if not c[2]):,} stay parked",
             size=17, fill=SECONDARY)
    svg.text(width - 4, height - bar / 2 + 5,
             "about 900 m across · each line is a household walking to its seat",
             size=15, fill=MUTED, anchor="end")
    svg.write("street-zoom.svg")


def fleet_comparison(con: duckdb.DuckDBPyConnection) -> None:
    """Cars owned, cars a one-per-household plan needs, cars this plan needs."""
    owned, baseline, activated = con.execute(
        "SELECT sum(cars_owned), sum(baseline_cars), sum(cars_activated) FROM layer_oa_summary"
    ).fetchone()
    rows = [
        ("Cars owned in the district", int(owned), MUTED),
        ("One car per car-owning household", int(baseline), MUTED),
        ("This plan, pooled", int(activated), SERIES),
    ]

    width, height = 1080, 330
    left, right = 400, width - 150
    svg = Svg(width, height)
    scale = (right - left) / max(r[1] for r in rows)

    for i, (label, value, colour) in enumerate(rows):
        y = 46 + i * 86
        bar_h = 34
        svg.text(left - 24, y + bar_h - 10, label, size=19,
                 fill=INK if colour == SERIES else SECONDARY, anchor="end",
                 weight="600" if colour == SERIES else "400")
        svg.add(
            f'<rect x="{left}" y="{y}" width="{value * scale:.1f}" height="{bar_h}" '
            f'rx="4" fill="{colour}" opacity="{1 if colour == SERIES else 0.55}"/>'
        )
        svg.text(left + value * scale + 14, y + bar_h - 9, f"{value:,}", size=21,
                 fill=INK if colour == SERIES else SECONDARY, weight="600", tabular=True)

    saved = int(baseline) - int(activated)
    svg.text(left, height - 26,
             f"{saved:,} fewer cars on the road than one-per-household "
             f"({saved / baseline:.0%})", size=18, fill=SERIES, weight="600")
    svg.write("fleet-comparison.svg")


def departures(con: duckdb.DuckDBPyConnection) -> None:
    """When the fleet actually leaves, once mobilisation delay is drawn per car."""
    bins = con.execute(
        """
        SELECT floor(departure_offset_s / 120) * 2 AS minute, count(*) AS vehicles
        FROM vehicle_departures GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    median_min, last_min = con.execute(
        "SELECT median(departure_offset_s) / 60, max(departure_offset_s) / 60 FROM vehicle_departures"
    ).fetchone()

    width, height = 1080, 400
    left, right, top, bottom = 78, width - 30, 34, height - 62
    max_minute = max(b[0] for b in bins) + 2
    max_vehicles = max(b[1] for b in bins)
    svg = Svg(width, height)

    def px(minute: float) -> float:
        return left + (minute / max_minute) * (right - left)

    def py(vehicles: float) -> float:
        return bottom - (vehicles / max_vehicles) * (bottom - top)

    for tick in nice_ticks(max_vehicles):
        y = py(tick)
        svg.add(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>')
        svg.text(left - 12, y + 5, f"{int(tick):,}", size=14, fill=MUTED, anchor="end", tabular=True)

    area = [f"M{px(0):.1f} {bottom:.1f}"]
    for minute, vehicles in bins:
        area.append(f"L{px(minute):.1f} {py(vehicles):.1f}")
        area.append(f"L{px(minute + 2):.1f} {py(vehicles):.1f}")
    area.append(f"L{right:.1f} {bottom:.1f}Z")
    svg.add(f'<path d="{"".join(area)}" fill="{SERIES}" opacity="0.22"/>')
    svg.add(
        f'<path d="{"".join(area[:-1]).replace(f"M{px(0):.1f} {bottom:.1f}", f"M{px(0):.1f} {py(bins[0][1]):.1f}", 1)}" '
        f'fill="none" stroke="{SERIES}" stroke-width="2" stroke-linejoin="round"/>'
    )

    svg.add(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="{BASELINE}" stroke-width="1"/>')
    for minute in range(0, int(max_minute) + 1, 30):
        svg.text(px(minute), bottom + 24, f"{minute}", size=14, fill=MUTED, anchor="middle", tabular=True)
    svg.text((left + right) / 2, height - 12, "minutes after the evacuation order",
             size=15, fill=SECONDARY, anchor="middle")

    svg.add(f'<line x1="{px(median_min):.1f}" y1="{top}" x2="{px(median_min):.1f}" y2="{bottom}" '
            f'stroke="{INK}" stroke-width="1" opacity="0.45" stroke-dasharray="0"/>')
    svg.text(px(median_min) + 10, top + 16, f"median {median_min:.0f} min", size=15, fill=INK)
    svg.text(right, top + 16, f"last car {last_min:.0f} min", size=15, fill=MUTED, anchor="end")
    svg.write("departures.svg")


def walk_distances(con: duckdb.DuckDBPyConnection) -> None:
    """How far people actually walk to the car they were assigned."""
    bins = con.execute(
        """
        SELECT least(floor(network_m / 20) * 20, 300) AS band, count(*) AS people
        FROM person_walks GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    median_m, p90_m = con.execute(
        "SELECT median(network_m), quantile_cont(network_m, 0.9) FROM person_walks"
    ).fetchone()

    width, height = 1080, 380
    left, right, top, bottom = 96, width - 30, 44, height - 62
    svg = Svg(width, height)
    max_people = max(b[1] for b in bins)
    slot = (right - left) / len(bins)

    for tick in nice_ticks(max_people):
        y = bottom - (tick / max_people) * (bottom - top)
        svg.add(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>')
        svg.text(left - 12, y + 5, f"{int(tick):,}", size=14, fill=MUTED, anchor="end", tabular=True)

    for band, people in bins:
        i = int(band // 20)
        h = (people / max_people) * (bottom - top)
        svg.add(
            f'<rect x="{left + i * slot + 1:.1f}" y="{bottom - h:.1f}" '
            f'width="{slot - 2:.1f}" height="{h:.1f}" rx="4" fill="{SERIES}" opacity="0.85"/>'
        )
    svg.add(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="{BASELINE}" stroke-width="1"/>')

    for i, label in ((0, "0"), (5, "100 m"), (10, "200 m")):
        svg.text(left + i * slot + slot / 2, bottom + 24, label, size=14, fill=MUTED, anchor="middle")
    # The last bar is everyone at or beyond 300 m, not a 20 m band like the rest,
    # so it is labelled as the pile-up it is.
    svg.text(right, bottom + 24, "300 m and over", size=14, fill=MUTED, anchor="end")

    svg.text(left, top - 12, f"median {median_m:.0f} m  ·  90th percentile {p90_m:.0f} m  "
             f"·  nobody is assigned a walk over 10 minutes",
             size=17, fill=INK, weight="600")
    svg.text((left + right) / 2, height - 12, "people, by walking distance to their assigned car",
             size=15, fill=SECONDARY, anchor="middle")
    svg.write("walk-distances.svg")


def headline_numbers(con: duckdb.DuckDBPyConnection) -> None:
    """Print the figures quoted on the slides, so they can be checked by eye."""
    facts = {
        "people": "SELECT count(*) FROM persons",
        "households": "SELECT count(*) FROM households",
        "cars owned": "SELECT count(*) FROM vehicles",
        "cars departing": "SELECT count(*) FROM activated_vehicles",
        "baseline cars": "SELECT sum(baseline_cars) FROM layer_oa_summary",
        "people seated": "SELECT count(*) FROM person_seats",
        # unmet_demand is one row per travel group, not per person.
        "people unseated": "SELECT sum(size) FROM unmet_demand",
        "mean occupancy": "SELECT round(avg(occupants), 2) FROM report_seat_utilisation WHERE departs",
        "full cars": "SELECT count(*) FROM report_seat_utilisation WHERE departs AND occupants = capacity",
        "median walk m": "SELECT round(median(network_m), 1) FROM person_walks",
        "home collections": "SELECT count(*) FROM route_stops WHERE stop_type = 'home_collection'",
        "travel groups": "SELECT count(*) FROM travel_groups",
        "groups split": "SELECT sum(split_groups) FROM report_group_splits",
        "output areas": "SELECT count(*) FROM admin_areas WHERE level = 'output_area'",
        "addresses": "SELECT count(*) FROM dwelling_points",
        "district exits": "SELECT count(*) FROM district_exits",
    }
    print("\nNumbers quoted on the slides:")
    for label, sql in facts.items():
        print(f"  {label:20s} {con.execute(sql).fetchone()[0]:,}")


if __name__ == "__main__":
    con = connect()
    print("Rendering figures:")
    fleet_map(con)
    street_zoom(con)
    fleet_comparison(con)
    departures(con)
    walk_distances(con)
    headline_numbers(con)
