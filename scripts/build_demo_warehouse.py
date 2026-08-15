"""Seed a tiny, hand-built warehouse so the messaging path can be demonstrated.

The real pipeline needs Census extracts, the NSUL address archive and an OSM
road network before a single seat exists. This builds the smallest plan that
still exercises every branch the messages have to render: a driver with a child
who has no phone and is notified through them, a walk-in neighbour, and two
residents who cannot walk and are collected from their own doors.

The addresses are real houses on real Margate streets, taken from OpenStreetMap,
about 390 m apart. The live plan cannot produce a spread like that — collection
groups are assigned to their nearest vehicle, so no two-pickup route in Thanet
has its stops more than 123 m apart — which is precisely why the demonstration
does not use it. Nothing here is real data about real people.

Only the *upstream* tables are hand-written. `vehicle_routes`, `route_stops` and
`stop_notifications` are then produced by materialising the real assets against
this database, so what the messages read is genuine pipeline output and not a
fixture pretending to be one.

    python scripts/build_demo_warehouse.py [--database data/demo.duckdb]
"""

from __future__ import annotations

import argparse

from dagster import materialize

from london_frontline.assets.routes import stop_notifications, vehicle_routes
from london_frontline.config import PlanningConfig
from london_frontline.paths import resolve
from london_frontline.relationships import RELATIONSHIP_DEPENDENT_OF
from london_frontline.resources import SpatialDuckDBResource
from london_frontline.seating import TIER_HOME_COLLECTION, TIER_OWNER, TIER_WALK_IN_INDEPENDENT

DEFAULT_DATABASE = "data/demo.duckdb"

# A Thanet Output Area code, and coordinates in Margate. British National Grid,
# matching every upstream source the real pipeline reads.
AREA_ID = "E00126789"

# household_id, uprn, easting, northing, composition, num_persons, num_cars
# Households 3 and 4 are the collection stops: Thanet Road and Osborne Terrace,
# 390 m and 395 m along the route from the car on Cecil Street.
HOUSEHOLDS = [
    (1, 10009123456, 635455.0, 170812.0, "Lone parent household: With dependent children", 2, 1),
    (2, 10009123457, 635530.0, 170760.0, "One person household: Other", 1, 0),
    (3, 10009123458, 635838.0, 170736.0, "One person household: Aged 66 years and over", 1, 0),
    (4, 10009123459, 635812.0, 170342.0, "One person household: Aged 66 years and over", 1, 0),
]

# person_id, household_id, name, age, sex, licence, can_walk, phone
PERSONS = [
    (1, 1, "Dave Hall", 44, "Male", "car", True, "07700 900131"),
    # No phone, but a co-resident parent: notified via_carer.
    (2, 1, "Ellis Hall", 8, "Male", "none", True, None),
    (3, 2, "Marcus Odell", 29, "Male", "car", True, "07700 900412"),
    # Cannot walk: collected from their own doors rather than the car.
    (4, 3, "Carol Brown", 78, "Female", "none", False, "07700 900177"),
    (5, 4, "Alan Whitfield", 82, "Male", "none", False, "07700 900864"),
]

# The one car, parked a few metres from its owner household — that spot is the
# muster point. Capacity 5 is exactly the demand, so nothing goes unmet.
VEHICLE_ID = 1
MUSTER_POINT_ID = 1
MUSTER_EASTING, MUSTER_NORTHING = 635459.0, 170808.0
LICENSE_PLATE = "GK71 XPV"
CAPACITY = 5

# travel_group_id, household_id, members, all_can_walk, tier
TRAVEL_GROUPS = [
    (1, 1, [1, 2], True, TIER_OWNER),
    (2, 2, [3], True, TIER_WALK_IN_INDEPENDENT),
    (3, 3, [4], False, TIER_HOME_COLLECTION),
    (4, 4, [5], False, TIER_HOME_COLLECTION),
]

DEPENDENCIES = [(2, 1)]  # Ellis depends on Dave


def seed(conn) -> None:
    """Write the upstream tables the route assets read."""
    conn.execute(
        """
        CREATE OR REPLACE TABLE households (
            household_id BIGINT, area_id VARCHAR, composition_type VARCHAR,
            num_persons BIGINT, num_cars BIGINT
        )
        """
    )
    conn.executemany(
        "INSERT INTO households VALUES (?, ?, ?, ?, ?)",
        [(hid, AREA_ID, comp, people, cars)
         for hid, _uprn, _e, _n, comp, people, cars in HOUSEHOLDS],
    )

    conn.execute(
        """
        CREATE OR REPLACE TABLE household_locations (
            household_id BIGINT, area_id VARCHAR, uprn BIGINT,
            easting DOUBLE, northing DOUBLE, latitude DOUBLE, longitude DOUBLE,
            geometry GEOMETRY
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO household_locations
        SELECT ?, ?, ?, ?, ?,
               ST_Y(wgs), ST_X(wgs), ST_Point(?, ?)
        FROM (SELECT ST_Transform(ST_Point(?, ?), 'EPSG:27700', 'EPSG:4326',
                                  always_xy := true) AS wgs)
        """,
        [(hid, AREA_ID, uprn, e, n, e, n, e, n)
         for hid, uprn, e, n, _comp, _people, _cars in HOUSEHOLDS],
    )

    conn.execute(
        """
        CREATE OR REPLACE TABLE persons (
            person_id BIGINT, household_id BIGINT, area_id VARCHAR, age BIGINT,
            sex VARCHAR, license_type VARCHAR, mobility_status BOOLEAN,
            name VARCHAR, phone_number VARCHAR, medical_skill BOOLEAN
        )
        """
    )
    conn.executemany(
        "INSERT INTO persons VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, false)",
        [(pid, hid, AREA_ID, age, sex, licence, walks, name, phone)
         for pid, hid, name, age, sex, licence, walks, phone in PERSONS],
    )

    conn.execute(
        """
        CREATE OR REPLACE TABLE person_relationships (
            person_id BIGINT, related_person_id BIGINT, relationship_type VARCHAR
        )
        """
    )
    conn.executemany(
        "INSERT INTO person_relationships VALUES (?, ?, ?)",
        [(pid, carer, RELATIONSHIP_DEPENDENT_OF) for pid, carer in DEPENDENCIES],
    )

    conn.execute(
        """
        CREATE OR REPLACE TABLE vehicles (
            vehicle_id BIGINT, household_id BIGINT, area_id VARCHAR,
            vehicle_type VARCHAR, capacity INTEGER, license_plate VARCHAR
        )
        """
    )
    conn.execute(
        "INSERT INTO vehicles VALUES (?, 1, ?, 'car', ?, ?)",
        [VEHICLE_ID, AREA_ID, CAPACITY, LICENSE_PLATE],
    )

    conn.execute(
        """
        CREATE OR REPLACE TABLE muster_points (
            muster_point_id BIGINT, vehicle_id BIGINT, household_id BIGINT,
            area_id VARCHAR, capacity INTEGER, snap_distance_m DOUBLE,
            easting DOUBLE, northing DOUBLE, geometry GEOMETRY
        )
        """
    )
    conn.execute(
        """
        INSERT INTO muster_points
        SELECT ?, ?, 1, ?, ?, 7.8, ?, ?, ST_Point(?, ?)
        """,
        [MUSTER_POINT_ID, VEHICLE_ID, AREA_ID, CAPACITY,
         MUSTER_EASTING, MUSTER_NORTHING, MUSTER_EASTING, MUSTER_NORTHING],
    )

    conn.execute(
        """
        CREATE OR REPLACE TABLE travel_group_members (
            travel_group_id BIGINT, person_id BIGINT, household_id BIGINT,
            area_id VARCHAR
        )
        """
    )
    conn.executemany(
        "INSERT INTO travel_group_members VALUES (?, ?, ?, ?)",
        [(gid, pid, hid, AREA_ID)
         for gid, hid, members, _walk, _tier in TRAVEL_GROUPS for pid in members],
    )

    conn.execute(
        """
        CREATE OR REPLACE TABLE travel_groups (
            travel_group_id BIGINT, household_id BIGINT, area_id VARCHAR,
            group_size BIGINT, contains_dependent BOOLEAN, all_can_walk BOOLEAN
        )
        """
    )
    conn.executemany(
        "INSERT INTO travel_groups VALUES (?, ?, ?, ?, ?, ?)",
        [(gid, hid, AREA_ID, len(members), len(members) > 1, walk)
         for gid, hid, members, walk, _tier in TRAVEL_GROUPS],
    )

    conn.execute(
        """
        CREATE OR REPLACE TABLE person_seats (
            person_id BIGINT, travel_group_id BIGINT, muster_point_id BIGINT,
            tier BIGINT, split BOOLEAN
        )
        """
    )
    conn.executemany(
        "INSERT INTO person_seats VALUES (?, ?, ?, ?, false)",
        [(pid, gid, MUSTER_POINT_ID, tier)
         for gid, _hid, members, _walk, tier in TRAVEL_GROUPS for pid in members],
    )

    conn.execute(
        """
        CREATE OR REPLACE TABLE seat_assignments (
            travel_group_id BIGINT, muster_point_id BIGINT, tier BIGINT,
            split BOOLEAN, seats BIGINT
        )
        """
    )
    conn.executemany(
        "INSERT INTO seat_assignments VALUES (?, ?, ?, false, ?)",
        [(gid, MUSTER_POINT_ID, tier, len(members))
         for gid, _hid, members, _walk, tier in TRAVEL_GROUPS],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    args = parser.parse_args()

    warehouse = SpatialDuckDBResource(database=args.database)
    with warehouse.connect() as conn:
        seed(conn)

    # The routes and notifications the messages read are real asset output.
    result = materialize(
        [vehicle_routes, stop_notifications],
        resources={"planning": PlanningConfig(), "warehouse": warehouse},
    )
    if not result.success:
        raise SystemExit("Materialising the route assets against the demo failed.")

    with warehouse.connect() as conn:
        stops = conn.execute(
            "SELECT stop_seq, stop_type, meeting_time FROM route_stops "
            "ORDER BY stop_seq"
        ).fetchall()
        notified = conn.execute(
            "SELECT name, notification_mode FROM stop_notifications ORDER BY person_id"
        ).fetchall()

    print(f"\nDemo warehouse: {resolve(args.database)}")
    print(f"  {len(stops)} stops: " + ", ".join(
        f"#{seq} {kind} at {when:%H:%M}" for seq, kind, when in stops
    ))
    for name, mode in notified:
        print(f"  {name:<14} {mode}")


if __name__ == "__main__":
    main()
