"""Tiered allocation of every person into a specific vehicle's seat."""

import pandas as pd
from dagster import AssetExecutionContext, asset

from london_frontline import seating
from london_frontline.assets.pedestrian import walk_candidates
from london_frontline.assets.relationships import travel_groups
from london_frontline.assets.vehicles import muster_points
from london_frontline.config import PlanningConfig
from london_frontline.resources import SpatialDuckDBResource

LICENSE_CAR = "car"

# Radii tried in order when finding the vehicles a home-collection group could be
# picked up by. Expanding keeps the candidate set small in dense areas while
# still reaching isolated homes.
_COLLECTION_RADII_M = (200.0, 600.0, 2000.0)


@asset(deps=[travel_groups, muster_points], group_name="assignment")
def collection_candidates(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Vehicles that could collect each home-collection group, by straight line.

    Straight-line rather than routed distance: this only ranks candidate vehicles
    and never becomes an output, so a routed driving graph would be cost without
    observable benefit (see design.md).
    """
    limit = planning.walk_candidates_per_household
    with warehouse.connect() as conn:
        conn.execute(
            """
            CREATE OR REPLACE TABLE collection_homes AS
            SELECT DISTINCT g.household_id, ST_Point(l.easting, l.northing) AS home
            FROM travel_groups g
            JOIN household_locations l USING (household_id)
            WHERE NOT g.all_can_walk
            """
        )
        conn.execute(
            """
            CREATE OR REPLACE TABLE muster_geometry AS
            SELECT muster_point_id, ST_Point(easting, northing) AS point
            FROM muster_points
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS muster_geometry_idx "
            "ON muster_geometry USING RTREE (point)"
        )
        conn.execute(
            """
            CREATE OR REPLACE TABLE collection_candidates (
                household_id BIGINT, muster_point_id BIGINT,
                distance_m DOUBLE, rank BIGINT
            )
            """
        )

        for radius in _COLLECTION_RADII_M:
            pending = conn.execute(
                """
                SELECT count(*) FROM collection_homes h
                WHERE NOT EXISTS (
                    SELECT 1 FROM collection_candidates c
                    WHERE c.household_id = h.household_id
                )
                """
            ).fetchone()[0]
            if pending == 0:
                break
            conn.execute(
                f"""
                INSERT INTO collection_candidates
                WITH pending AS (
                    SELECT h.* FROM collection_homes h
                    WHERE NOT EXISTS (
                        SELECT 1 FROM collection_candidates c
                        WHERE c.household_id = h.household_id
                    )
                )
                SELECT household_id, muster_point_id, distance_m, rank FROM (
                    SELECT p.household_id, m.muster_point_id,
                           ST_Distance(p.home, m.point) AS distance_m,
                           row_number() OVER (
                               PARTITION BY p.household_id
                               ORDER BY ST_Distance(p.home, m.point)
                           ) AS rank
                    FROM pending p
                    JOIN muster_geometry m ON ST_DWithin(p.home, m.point, {radius})
                ) WHERE rank <= {limit}
                """
            )
            context.log.info(
                "Collection radius %.0f m: %s households still without candidates",
                radius,
                f"{pending:,}",
            )

        summary = conn.execute(
            """
            SELECT count(DISTINCT household_id) AS households,
                   count(*) AS rows,
                   round(median(distance_m), 1) AS median_m
            FROM collection_candidates
            """
        ).fetchdf().iloc[0]
        total_homes = conn.execute("SELECT count(*) FROM collection_homes").fetchone()[0]

    context.add_output_metadata(
        {
            "home_collection_households": total_homes,
            "households_with_candidates": int(summary["households"]),
            "candidate_rows": int(summary["rows"]),
            "median_candidate_distance_m": float(summary["median_m"]),
        }
    )


@asset(deps=[walk_candidates, collection_candidates], group_name="assignment")
def seat_assignments(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Run the four assignment tiers and record seatings and unmet demand."""
    with warehouse.connect() as conn:
        groups_frame = conn.execute(
            """
            SELECT travel_group_id, household_id, area_id, group_size,
                   contains_dependent, all_can_walk
            FROM travel_groups ORDER BY travel_group_id
            """
        ).fetchdf()
        vehicles_frame = conn.execute(
            "SELECT muster_point_id, household_id AS owner_household_id, capacity "
            "FROM muster_points ORDER BY muster_point_id"
        ).fetchdf()
        walk_frame = conn.execute(
            "SELECT household_id, muster_point_id, walk_distance_m "
            "FROM walk_candidates ORDER BY household_id, walk_distance_m"
        ).fetchdf()
        collection_frame = conn.execute(
            "SELECT household_id, muster_point_id, distance_m "
            "FROM collection_candidates ORDER BY household_id, distance_m"
        ).fetchdf()
        members_frame = conn.execute(
            "SELECT travel_group_id, person_id FROM travel_group_members "
            "ORDER BY travel_group_id, person_id"
        ).fetchdf()
        licensed_frame = conn.execute(
            "SELECT person_id FROM persons WHERE license_type = ?", [LICENSE_CAR]
        ).fetchdf()

    groups = [
        seating.Group(
            int(row.travel_group_id),
            int(row.household_id),
            row.area_id,
            int(row.group_size),
            bool(row.contains_dependent),
            bool(row.all_can_walk),
        )
        for row in groups_frame.itertuples()
    ]
    vehicles = [
        seating.Vehicle(
            int(row.muster_point_id), int(row.owner_household_id), int(row.capacity)
        )
        for row in vehicles_frame.itertuples()
    ]

    def as_candidates(frame: pd.DataFrame, distance_column: str) -> dict:
        grouped: dict[int, list[tuple[int, float]]] = {}
        for household_id, muster_point_id, distance in zip(
            frame["household_id"].astype(int),
            frame["muster_point_id"].astype(int),
            frame[distance_column].astype(float),
        ):
            grouped.setdefault(household_id, []).append((muster_point_id, distance))
        return grouped

    seatings, unseated = seating.assign_seats(
        groups,
        vehicles,
        as_candidates(walk_frame, "walk_distance_m"),
        as_candidates(collection_frame, "distance_m"),
        planning.max_collection_stops_per_vehicle,
    )
    context.log.info(
        "Tiered assignment: %s seatings, %s groups unseated before the driver check",
        f"{len(seatings):,}",
        f"{len(unseated):,}",
    )

    # Hand each group's seats to specific members before checking for a driver:
    # a split group's driver sits in only one of its vehicles, so a group-level
    # count would let a car with nobody licensed in it depart.
    members_by_group: dict[int, list[int]] = {}
    for group_id, person_id in zip(
        members_frame["travel_group_id"].astype(int),
        members_frame["person_id"].astype(int),
    ):
        members_by_group.setdefault(group_id, []).append(person_id)

    expanded = seating.expand_to_persons(seatings, members_by_group)
    licensed = set(licensed_frame["person_id"].astype(int))
    kept, dropped = seating.enforce_driver_availability(expanded, groups, licensed)
    context.log.info(
        "Driver check: %s people kept, %s dropped for riding in a vehicle with "
        "no licensed driver",
        f"{len(kept):,}",
        f"{sum(u.size for u in dropped):,}",
    )

    person_seat_frame = pd.DataFrame(
        [(s.person_id, s.group_id, s.muster_point_id, s.tier, s.split) for s in kept],
        columns=["person_id", "travel_group_id", "muster_point_id", "tier", "split"],
    )
    seat_frame = (
        person_seat_frame.groupby(
            ["travel_group_id", "muster_point_id", "tier", "split"], as_index=False
        )
        .size()
        .rename(columns={"size": "seats"})
    )
    unmet_frame = pd.DataFrame(
        [
            (u.group_id, u.area_id, u.tier, u.size, u.collection_mode, u.reason)
            for u in (*unseated, *dropped)
        ],
        columns=["travel_group_id", "area_id", "tier", "size", "collection_mode", "reason"],
    )

    with warehouse.connect() as conn:
        conn.register("seats_df", seat_frame)
        conn.register("unmet_df", unmet_frame)
        conn.register("person_seats_df", person_seat_frame)
        conn.execute("CREATE OR REPLACE TABLE seat_assignments AS SELECT * FROM seats_df")
        conn.execute("CREATE OR REPLACE TABLE unmet_demand AS SELECT * FROM unmet_df")
        conn.execute(
            "CREATE OR REPLACE TABLE person_seats AS SELECT * FROM person_seats_df"
        )
        driverless = conn.execute(
            """
            SELECT count(*) FROM (
                SELECT s.muster_point_id
                FROM person_seats s JOIN persons p USING (person_id)
                GROUP BY s.muster_point_id
                HAVING count(*) FILTER (WHERE p.license_type = 'car') = 0
            )
            """
        ).fetchone()[0]
        if driverless:
            raise ValueError(
                f"{driverless} vehicles carry occupants but have no car-licensed "
                f"driver among them"
            )

        stats = conn.execute(
            """
            SELECT (SELECT count(*) FROM person_seats) AS people_seated,
                   (SELECT count(*) FROM persons) AS people_total,
                   (SELECT count(DISTINCT muster_point_id) FROM seat_assignments)
                     AS vehicles_used,
                   (SELECT count(*) FROM muster_points) AS vehicles_total,
                   (SELECT coalesce(sum(size), 0) FROM unmet_demand) AS people_unmet,
                   (SELECT count(*) FROM seat_assignments WHERE split) AS split_seatings
            """
        ).fetchdf().iloc[0]

    context.log.info(
        "Seated %s of %s people in %s of %s vehicles; %s unmet",
        f"{int(stats['people_seated']):,}",
        f"{int(stats['people_total']):,}",
        f"{int(stats['vehicles_used']):,}",
        f"{int(stats['vehicles_total']):,}",
        f"{int(stats['people_unmet']):,}",
    )
    context.add_output_metadata(
        {key: int(stats[key]) for key in stats.index}
    )
