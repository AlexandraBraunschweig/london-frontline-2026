"""Turn the evacuation plan into SUMO trip definitions for two scenarios.

`baseline`  — every car-owning household drives its own car out, carrying only
              its own occupants. One trip per car-owning household.
`pooled`    — only the vehicles the seat-assignment actually activates depart.
              One trip per departing muster point.

Both scenarios send every vehicle to the single fleet-wide destination in
``PlanningConfig`` (Canterbury).

Departure timing is a modelling choice this script makes explicit, because it
turned out to decide the answer. ``PlanningConfig.fleet_departure_time`` is a
single instant, and releasing all ~46,000 cars in that one second gridlocks the
whole district under *either* plan — the network saturates, nobody moves, and
the two scenarios become indistinguishable. That is a real result about
simultaneous departure, not about ride-sharing, and ``--instant`` reproduces it.

The default instead spreads departures over a mobilisation curve: people take
time to gather, load, and go. Departure offsets are drawn from a Rayleigh
distribution, the usual shape for evacuation mobilisation, scaled so 95% of the
fleet has left within ``--mobilisation-minutes``. Both scenarios draw from the
same seeded curve, so the only difference between them remains the number of
cars on the road.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from xml.sax.saxutils import quoteattr

import duckdb
import numpy as np
import sumolib
from scipy.spatial import cKDTree

# Matches PlanningConfig.destination_* — Canterbury, the nearest large centre
# outside Thanet.
DESTINATION_LON = 1.0789
DESTINATION_LAT = 51.2802

# A muster point is a parking spot already snapped to a road, so it is normally
# metres from an edge. The wider radii only matter where netconvert dropped the
# road the pipeline snapped to (service roads, driveways).
SEARCH_RADII_M = (50.0, 150.0, 500.0, 2000.0, 10000.0)

# Matches PlanningConfig.random_seed, so the mobilisation draw is reproducible
# and identical across scenarios.
RANDOM_SEED = 20260815


def _departure_offsets(count: int, mobilisation_minutes: float) -> np.ndarray:
    """Seconds after the alarm at which each vehicle actually pulls away.

    Rayleigh is the conventional mobilisation shape: nobody leaves instantly,
    the bulk goes in the middle, and a tail straggles. Scaled so 95% of the
    fleet has departed by ``mobilisation_minutes`` — for the Rayleigh CDF that
    is sigma = t / sqrt(6).
    """
    if mobilisation_minutes <= 0:
        return np.zeros(count)
    sigma = (mobilisation_minutes * 60.0) / np.sqrt(6.0)
    rng = np.random.default_rng(RANDOM_SEED)
    offsets = rng.rayleigh(sigma, count)
    # Clip the tail so a handful of outliers cannot stretch the run out.
    return np.clip(offsets, 0.0, mobilisation_minutes * 60.0 * 1.5)


def _edge_point_index(net: sumolib.net.Net) -> tuple[cKDTree, list]:
    """A KD-tree over the shape points of every passenger-drivable edge.

    ``sumolib.net.getNeighboringEdges`` is a linear scan per query without an
    rtree installed; at ~46,000 queries against ~45,000 edges that is a cross
    product. Indexing shape points instead is one build and 46,000 lookups.
    Nearest *shape point* rather than nearest perpendicular projection is close
    enough here — the pipeline already snapped these points onto a road.
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
        distances, indices = tree.query(
            xy[outstanding], distance_upper_bound=radius
        )
        still_missing = []
        for slot, distance, index in zip(outstanding, distances, indices):
            if np.isinf(distance):
                still_missing.append(slot)
            else:
                found[slot] = owners[index]
        outstanding = np.array(still_missing, dtype=int)
    return found


def _origins(conn: duckdb.DuckDBPyConnection, scenario: str):
    """Origin muster points for a scenario, as (id, lon, lat, occupants)."""
    if scenario == "baseline":
        # One car per car-owning household. A household with two cars still
        # drives one out; the baseline is one-car-per-household, not per car.
        # Occupants are that household's own people, nobody else's.
        sql = """
            WITH chosen AS (
                SELECT household_id, min(muster_point_id) AS muster_point_id
                FROM muster_points GROUP BY household_id
            )
            SELECT c.muster_point_id,
                   ST_X(m.geometry) AS lon, ST_Y(m.geometry) AS lat,
                   (SELECT count(*) FROM persons p
                     WHERE p.household_id = c.household_id) AS occupants
            FROM chosen c JOIN muster_points m USING (muster_point_id)
            ORDER BY c.muster_point_id
        """
    elif scenario == "pooled":
        # Only the vehicles the plan actually activates, with the occupants the
        # plan gives them — including people walked in from other households.
        sql = """
            SELECT s.muster_point_id,
                   ST_X(m.geometry) AS lon, ST_Y(m.geometry) AS lat,
                   sum(s.seats) AS occupants
            FROM seat_assignments s JOIN muster_points m USING (muster_point_id)
            GROUP BY s.muster_point_id, m.geometry
            ORDER BY s.muster_point_id
        """
    else:
        raise ValueError(f"unknown scenario {scenario!r}")
    return conn.execute(sql).fetchdf()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=["baseline", "pooled"])
    parser.add_argument("--warehouse", default="data/warehouse.duckdb")
    parser.add_argument("--net", default="data/sumo/thanet.net.xml")
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--mobilisation-minutes", type=float, default=60.0,
        help="minutes by which 95%% of the fleet has departed (default: 60)",
    )
    parser.add_argument(
        "--instant", action="store_true",
        help="release the entire fleet at t=0, as fleet_departure_time literally "
             "specifies; this gridlocks the district under either plan",
    )
    args = parser.parse_args()

    mobilisation = 0.0 if args.instant else args.mobilisation_minutes
    out_path = Path(args.out or f"data/sumo/{args.scenario}.trips.xml")

    print(f"[{args.scenario}] reading the plan")
    with duckdb.connect(args.warehouse, read_only=True) as conn:
        conn.execute("INSTALL spatial; LOAD spatial;")
        frame = _origins(conn, args.scenario)
    print(f"[{args.scenario}] {len(frame):,} vehicles, "
          f"{int(frame.occupants.sum()):,} people aboard")

    print(f"[{args.scenario}] loading network")
    net = sumolib.net.readNet(args.net)
    tree, owners = _edge_point_index(net)

    origin_edges = _nearest_edges(
        net, tree, owners, frame.lon.to_numpy(), frame.lat.to_numpy()
    )
    destination_edge = _nearest_edges(
        net, tree, owners,
        np.array([DESTINATION_LON]), np.array([DESTINATION_LAT]),
    )[0]
    if destination_edge is None:
        raise SystemExit("the destination is not on the network")

    unplaced = sum(edge is None for edge in origin_edges)
    # A vehicle whose origin edge is also the destination edge has no journey to
    # make and would be dropped by the router; keep the count visible.
    trivial = 0

    # SUMO requires departures in ascending order, so build the rows first and
    # sort on departure time rather than streaming them out in muster-point order.
    offsets = _departure_offsets(len(frame), mobilisation)
    rows = sorted(
        zip(offsets, frame.itertuples(), origin_edges), key=lambda t: t[0]
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
        for (offset, row, edge) in rows:
            if edge is None:
                continue
            if edge.getID() == destination_edge.getID():
                trivial += 1
                continue
            # SUMO still holds any vehicle it cannot physically fit onto the road
            # yet, so the queue to get moving remains part of what is measured —
            # the mobilisation curve sets when each driver *wants* to leave.
            handle.write(
                f'  <trip id="{args.scenario[0]}{row.muster_point_id}" type="evac"'
                f' depart="{offset:.1f}" from={quoteattr(edge.getID())}'
                f' to={quoteattr(destination_edge.getID())}'
                f' departLane="best" departSpeed="max"'
                f' occupancy="{int(row.occupants)}"/>\n'
            )
            written += 1
        handle.write("</routes>\n")

    if mobilisation:
        print(f"[{args.scenario}] departures spread over a {mobilisation:.0f}-minute "
              f"mobilisation curve (95% away by then)")
    else:
        print(f"[{args.scenario}] all vehicles released at t=0")
    print(f"[{args.scenario}] wrote {written:,} trips to {out_path}")
    if unplaced:
        print(f"[{args.scenario}] {unplaced:,} vehicles had no edge within "
              f"{SEARCH_RADII_M[-1]:.0f} m and were dropped")
    if trivial:
        print(f"[{args.scenario}] {trivial:,} vehicles already start on the "
              f"destination edge and were dropped")


if __name__ == "__main__":
    main()
