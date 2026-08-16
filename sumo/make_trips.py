"""Turn the evacuation plan into SUMO trip definitions for two scenarios.

`pooled`    — the plan as the pipeline computes it. Only the vehicles
              `fleet.py` activates depart, each at the time `vehicle_departures`
              gives it, heading for the district exit that asset assigned.
`baseline`  — every car-owning household drives its own car out carrying only
              its own occupants, to its own nearest exit.

The baseline is built here rather than in the warehouse because it is not a
plan anyone made; it is the thing the plan is measured against. It reuses the
pipeline's own mobilisation draw and the same exits, so the only difference
between the two runs is which cars depart and who is in them.

Clearance is measured at the district boundary, which is where an evacuation is
actually over — the exits are the destinations, and nothing queues beyond them.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from xml.sax.saxutils import quoteattr

import duckdb
import numpy as np
import sumolib
from scipy.spatial import cKDTree

from london_frontline.config import PlanningConfig
from london_frontline.mobilisation import MobilisationProfile, departure_offsets_seconds

# A muster point is a parking spot already snapped to a road, so it is normally
# metres from an edge. The wider radii only matter where netconvert dropped the
# road the pipeline snapped to (service roads, driveways).
SEARCH_RADII_M = (50.0, 150.0, 500.0, 2000.0, 10000.0)


def _edge_point_index(net: sumolib.net.Net) -> tuple[cKDTree, list]:
    """A KD-tree over the shape points of every passenger-drivable edge.

    ``sumolib.net.getNeighboringEdges`` is a linear scan per query without an
    rtree installed; at ~46,000 queries against ~27,000 edges that is a cross
    product. Indexing shape points instead is one build and 46,000 lookups.
    """
    points: list[tuple[float, float]] = []
    owners: list = []
    for edge in net.getEdges():
        if edge.isSpecial() or not edge.allows("passenger"):
            continue
        for x, y in edge.getShape():
            points.append((x, y))
            owners.append(edge)
    return cKDTree(np.array(points)), owners


def _nearest_edges(
    net: sumolib.net.Net,
    tree: cKDTree,
    owners: list,
    lons: np.ndarray,
    lats: np.ndarray,
) -> list:
    """Map WGS84 coordinates to their nearest drivable edge, widening as needed."""
    xy = np.array(
        [net.convertLonLat2XY(float(lon), float(lat)) for lon, lat in zip(lons, lats)]
    )
    found: list = [None] * len(xy)
    outstanding = np.arange(len(xy))
    for radius in SEARCH_RADII_M:
        if outstanding.size == 0:
            break
        distances, indices = tree.query(xy[outstanding], distance_upper_bound=radius)
        still_missing = []
        for slot, distance, index in zip(outstanding, distances, indices):
            if np.isinf(distance):
                still_missing.append(slot)
            else:
                found[slot] = owners[index]
        outstanding = np.array(still_missing, dtype=int)
    return found


def _pooled(conn: duckdb.DuckDBPyConnection):
    """The plan's own fleet: activated vehicles, their departures and exits."""
    return conn.execute(
        """
        WITH occupancy AS (
            SELECT muster_point_id, sum(seats) AS occupants
            FROM seat_assignments GROUP BY muster_point_id
        )
        SELECT d.muster_point_id,
               d.departure_offset_s,
               ST_X(m.geometry) AS lon, ST_Y(m.geometry) AS lat,
               ST_X(e.geometry) AS exit_lon, ST_Y(e.geometry) AS exit_lat,
               o.occupants
        FROM vehicle_departures d
        JOIN muster_points m USING (muster_point_id)
        JOIN district_exits e USING (exit_id)
        JOIN occupancy o USING (muster_point_id)
        ORDER BY d.muster_point_id
        """
    ).fetchdf()


def _baseline(conn: duckdb.DuckDBPyConnection, planning: PlanningConfig):
    """One car per car-owning household, carrying only that household.

    Departure offsets are drawn from the same lognormal the pipeline uses, with
    no driver-access term: in this scenario the owner is already at their own
    car, so there is nothing to walk to.
    """
    frame = conn.execute(
        """
        WITH chosen AS (
            SELECT household_id, min(muster_point_id) AS muster_point_id
            FROM muster_points GROUP BY household_id
        ),
        located AS (
            SELECT c.muster_point_id, c.household_id, m.easting, m.northing,
                   ST_X(m.geometry) AS lon, ST_Y(m.geometry) AS lat,
                   (SELECT count(*) FROM persons p
                     WHERE p.household_id = c.household_id) AS occupants
            FROM chosen c JOIN muster_points m USING (muster_point_id)
        )
        SELECT l.muster_point_id, l.lon, l.lat, l.occupants,
               ST_X(e.geometry) AS exit_lon, ST_Y(e.geometry) AS exit_lat
        FROM located l
        JOIN LATERAL (
            SELECT d.geometry,
                   ST_Distance(ST_Point(l.easting, l.northing),
                               ST_Point(d.easting, d.northing)) AS exit_distance_m
            FROM district_exits d
            ORDER BY exit_distance_m, d.exit_id
            LIMIT 1
        ) e ON true
        ORDER BY l.muster_point_id
        """
    ).fetchdf()

    profile = MobilisationProfile(
        median_minutes=planning.mobilisation_median_minutes,
        sigma=planning.mobilisation_sigma,
    )
    offsets, _, _ = departure_offsets_seconds(
        frame.muster_point_id.tolist(),
        np.zeros(len(frame)),
        profile,
        planning.random_seed,
        include_driver_access_time=False,
    )
    frame["departure_offset_s"] = offsets
    return frame


def build_trips(
    scenario: str,
    warehouse: str = "data/warehouse.duckdb",
    net: str = "data/sumo/thanet.net.xml",
    out=None,
) -> dict:
    """Write one scenario's trips and report what went into them."""

    planning = PlanningConfig()
    out_path = Path(out or f"data/sumo/{scenario}.trips.xml")

    print(f"[{scenario}] reading the plan")
    with duckdb.connect(warehouse, read_only=True) as conn:
        conn.execute("INSTALL spatial; LOAD spatial;")
        frame = (
            _pooled(conn) if scenario == "pooled" else _baseline(conn, planning)
        )
    print(f"[{scenario}] {len(frame):,} vehicles, "
          f"{int(frame.occupants.sum()):,} people aboard, "
          f"departures {frame.departure_offset_s.min() / 60:.0f}–"
          f"{frame.departure_offset_s.max() / 60:.0f} min after notification")

    print(f"[{scenario}] loading network")
    net = sumolib.net.readNet(net)
    tree, owners = _edge_point_index(net)

    origin_edges = _nearest_edges(
        net, tree, owners, frame.lon.to_numpy(), frame.lat.to_numpy()
    )
    destination_edges = _nearest_edges(
        net, tree, owners, frame.exit_lon.to_numpy(), frame.exit_lat.to_numpy()
    )

    unplaced = trivial = 0
    rows = sorted(
        zip(frame.departure_offset_s.to_numpy(), frame.itertuples(),
            origin_edges, destination_edges),
        key=lambda t: t[0],
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as handle:
        handle.write('<?xml version="1.0" encoding="UTF-8"?>\n<routes>\n')
        handle.write(
            '  <vType id="evac" vClass="passenger" length="4.5" minGap="2.5"\n'
            '         accel="2.6" decel="4.5" sigma="0.5" tau="1.0"\n'
            '         maxSpeed="38.0" speedFactor="normc(1.0,0.10,0.8,1.2)"/>\n'
        )
        written = 0
        for offset, row, origin, destination in rows:
            if origin is None or destination is None:
                unplaced += 1
                continue
            if origin.getID() == destination.getID():
                # Already parked on its way out; it has no journey to simulate.
                trivial += 1
                continue
            handle.write(
                f'  <trip id="{scenario[0]}{row.muster_point_id}" type="evac"'
                f' depart="{offset:.1f}" from={quoteattr(origin.getID())}'
                f' to={quoteattr(destination.getID())}'
                f' departLane="best" departSpeed="max"'
                f' occupancy="{int(row.occupants)}"/>\n'
            )
            written += 1
        handle.write("</routes>\n")

    print(f"[{scenario}] wrote {written:,} trips to {out_path}")
    if unplaced:
        print(f"[{scenario}] {unplaced:,} vehicles had no edge within "
              f"{SEARCH_RADII_M[-1]:.0f} m and were dropped")
    if trivial:
        print(f"[{scenario}] {trivial:,} vehicles already sit on their exit "
              f"edge and were dropped")


    return {
        "scenario": scenario,
        "path": str(out_path),
        "vehicles": written,
        "unplaced": unplaced,
        "trivial": trivial,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=["baseline", "pooled"])
    parser.add_argument("--warehouse", default="data/warehouse.duckdb")
    parser.add_argument("--net", default="data/sumo/thanet.net.xml")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    build_trips(args.scenario, args.warehouse, args.net, args.out)


if __name__ == "__main__":
    main()
