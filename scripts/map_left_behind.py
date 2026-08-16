"""Map the people no vehicle collects, under the plan and under the baseline.

Two maps of the same district on the same projection, one per scenario, so the
difference between them is only the dots:

`plan`      — the tiered seat assignment. A household's own car takes its own
              people first, then spare seats go to neighbours who walk in or
              are collected from home.
`baseline`  — every household drives its own cars out with only its own people
              aboard. Nobody without a car in the household gets a lift.

A dot is one person left behind, placed at their home and jittered a few metres
so co-residents do not stack into a single pixel. Red is someone who would need
help to leave — aged over the configured threshold, or recorded in the Census as
disabled enough that they cannot walk to a neighbouring street. Green is
everyone else.

Writes one self-contained HTML file; no network, no build step.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import duckdb
import geopandas as gpd
import numpy as np
from shapely.ops import unary_union

# Matches PlanningConfig.random_seed, so the jitter is reproducible.
RANDOM_SEED = 20260815
# Age at or above which a person left behind is treated as needing assistance.
# Anyone with mobility_status = false counts regardless of age.
VULNERABLE_AGE = 70
# Radius of the jitter disc applied to each person's home point, in metres.
JITTER_M = 9.0
# Boundary simplification tolerances, in metres.
COASTLINE_TOLERANCE_M = 25.0
AREA_TOLERANCE_M = 35.0
# Padding around the district, in metres, so dots on the coast are not clipped.
FRAME_PADDING_M = 250.0

_TABLES = ("persons", "households", "household_locations", "person_seats", "vehicles")


def load_people(plan_dir: Path) -> "object":
    """Every person, with their home point and the facts each scenario needs."""
    conn = duckdb.connect()
    for table in _TABLES:
        conn.execute(
            f"CREATE VIEW {table} AS "
            f"SELECT * FROM read_parquet('{plan_dir / (table + '.parquet')}')"
        )
    return conn.execute(
        f"""
        WITH own_capacity AS (
            SELECT household_id, sum(capacity) AS seats FROM vehicles GROUP BY 1
        ),
        licensed AS (
            SELECT household_id,
                   count(*) FILTER (WHERE license_type = 'car') AS drivers
            FROM persons GROUP BY 1
        )
        SELECT p.person_id, p.household_id, p.age, p.mobility_status,
               (p.age >= {VULNERABLE_AGE} OR NOT p.mobility_status) AS vulnerable,
               l.easting, l.northing,
               coalesce(c.seats, 0) AS own_seats,
               coalesce(d.drivers, 0) AS own_drivers,
               (s.person_id IS NOT NULL) AS seated_in_plan
        FROM persons p
        JOIN household_locations l USING (household_id)
        LEFT JOIN own_capacity c USING (household_id)
        LEFT JOIN licensed d ON d.household_id = p.household_id
        LEFT JOIN person_seats s ON s.person_id = p.person_id
        ORDER BY p.person_id
        """
    ).fetchdf()


def baseline_seated(people) -> np.ndarray:
    """Who rides in their own household's cars when nobody shares.

    Seats go to the members least able to fend for themselves first — those who
    cannot walk, then the oldest — which only decides anything in the few
    hundred households whose people outnumber their own seats.
    """
    count = len(people)
    order = np.lexsort(
        (
            -people["age"].to_numpy(),
            people["mobility_status"].to_numpy(),
            people["household_id"].to_numpy(),
        )
    )
    household = people["household_id"].to_numpy()[order]
    starts = np.r_[0, np.flatnonzero(household[1:] != household[:-1]) + 1]
    within_household = np.arange(count) - np.repeat(
        starts, np.diff(np.r_[starts, count])
    )
    rank = np.empty(count, dtype=np.int64)
    rank[order] = within_household
    return rank < people["own_seats"].to_numpy()


def jittered_home_points(people) -> tuple[np.ndarray, np.ndarray]:
    """Home eastings and northings, nudged within a small disc."""
    rng = np.random.default_rng(RANDOM_SEED)
    count = len(people)
    angle = rng.uniform(0, 2 * np.pi, count)
    radius = JITTER_M * np.sqrt(rng.uniform(0, 1, count))
    return (
        people["easting"].to_numpy() + radius * np.cos(angle),
        people["northing"].to_numpy() + radius * np.sin(angle),
    )


class Frame:
    """A square British National Grid window quantised to 16-bit coordinates.

    Both maps and every point set share one instance, which is what makes the
    two panels comparable and lets them pan and zoom as one.
    """

    def __init__(self, bounds: np.ndarray) -> None:
        min_x, min_y, max_x, max_y = bounds
        min_x, min_y = min_x - FRAME_PADDING_M, min_y - FRAME_PADDING_M
        max_x, max_y = max_x + FRAME_PADDING_M, max_y + FRAME_PADDING_M
        self.span = max(max_x - min_x, max_y - min_y)
        self.origin_x = (min_x + max_x - self.span) / 2
        self.origin_y = (min_y + max_y - self.span) / 2
        self.scale = 65535.0 / self.span
        # The district is wider than it is tall, so the square frame carries
        # empty margins. The page crops to this ratio rather than showing them.
        self.aspect = (max_x - min_x) / (max_y - min_y)

    def quantise(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        """Interleaved x, y as unsigned 16-bit values within the frame."""
        qx = np.clip(np.rint((xs - self.origin_x) * self.scale), 0, 65535)
        qy = np.clip(np.rint((ys - self.origin_y) * self.scale), 0, 65535)
        return np.column_stack([qx.astype("<u2"), qy.astype("<u2")]).ravel()


def encode(array: np.ndarray) -> str:
    return base64.b64encode(array.tobytes()).decode("ascii")


def rings_of(geometry, tolerance: float) -> list[np.ndarray]:
    """Every closed ring of a geometry, simplified, as coordinate arrays."""
    geometry = geometry.simplify(tolerance)
    parts = geometry.geoms if geometry.geom_type.startswith("Multi") else [geometry]
    rings = []
    for part in parts:
        if part.is_empty:
            continue
        for ring in [part.exterior, *part.interiors]:
            coords = np.asarray(ring.coords)
            if len(coords) > 2:
                rings.append(coords)
    return rings


def encode_rings(rings: list[np.ndarray], frame: Frame) -> dict[str, str]:
    """Rings as one coordinate blob plus the length of each, both base64."""
    lengths = np.array([len(ring) for ring in rings], dtype="<u4")
    coords = np.concatenate([frame.quantise(r[:, 0], r[:, 1]) for r in rings])
    return {"lengths": encode(lengths), "coords": encode(coords)}


def build_payload(plan_dir: Path, boundaries_path: Path) -> dict:
    """Everything the page draws, ready to inline."""
    people = load_people(plan_dir)
    vulnerable = people["vulnerable"].to_numpy()
    in_plan = people["seated_in_plan"].to_numpy()
    in_own_car = baseline_seated(people)
    has_driver = people["own_drivers"].to_numpy() > 0

    scenarios = {
        "plan": ~in_plan,
        "baseline": ~in_own_car,
        "baseline_licensed": ~(in_own_car & has_driver),
    }

    areas = gpd.read_file(boundaries_path).to_crs(27700)
    frame = Frame(areas.total_bounds)
    east, north = jittered_home_points(people)

    points, counts = {}, {}
    for name, left_behind in scenarios.items():
        counts[name] = int(left_behind.sum())
        for band, in_band in (("vulnerable", vulnerable), ("other", ~vulnerable)):
            selected = left_behind & in_band
            counts[f"{name}_{band}"] = int(selected.sum())
            points[f"{name}_{band}"] = encode(
                frame.quantise(east[selected], north[selected])
            )

    return {
        "spanMetres": frame.span,
        "aspect": frame.aspect,
        "population": len(people),
        "vulnerableTotal": int(vulnerable.sum()),
        "vulnerableAge": VULNERABLE_AGE,
        "counts": counts,
        "points": points,
        "coast": encode_rings(
            rings_of(unary_union(areas.geometry.values), COASTLINE_TOLERANCE_M), frame
        ),
        "areas": encode_rings(
            [r for g in areas.geometry for r in rings_of(g, AREA_TOLERANCE_M)], frame
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", default="data/exports/plan")
    parser.add_argument(
        "--boundaries", default="data/exports/output_areas_E07000114.geojson"
    )
    parser.add_argument("--out", default="data/exports/left_behind.html")
    args = parser.parse_args()

    payload = build_payload(Path(args.plan_dir), Path(args.boundaries))
    template = (Path(__file__).parent / "left_behind_template.html").read_text()
    html = template.replace(
        "__PAYLOAD__", json.dumps(payload, separators=(",", ":"))
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)

    counts = payload["counts"]
    print(f"population           {payload['population']:>9,}")
    for name in ("plan", "baseline", "baseline_licensed"):
        print(
            f"{name:<20} {counts[name]:>9,} left behind  "
            f"({counts[name + '_vulnerable']:,} needing assistance)"
        )
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
