"""Per-vehicle leader, route, meeting times, and who to notify.

This capability makes no allocation decisions. Which occupant rides in which
vehicle, and which collection stops a vehicle makes, were fixed by
``evacuation-seat-assignment``; this renders them into something a leader could
act on.
"""

from dagster import AssetExecutionContext, asset

from london_frontline.assets.assignment import seat_assignments
from london_frontline.config import PlanningConfig
from london_frontline.relationships import RELATIONSHIP_DEPENDENT_OF
from london_frontline.resources import SpatialDuckDBResource

LICENSE_CAR = "car"

STOP_MUSTER = "muster_point"
STOP_COLLECTION = "home_collection"

NOTIFY_DIRECT = "direct"
NOTIFY_CARER = "via_carer"
NOTIFY_PHYSICAL = "physical_collection"


@asset(deps=[seat_assignments], group_name="routes")
def vehicle_routes(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Choose a leader per departing vehicle and lay out its ordered stops.

<<<<<<< Updated upstream
    The leader is a licensed member of the owner household: a car is driven by
    its owner, which activation already guarantees is possible.

    The muster point is always the first stop — it is where the vehicle is
    parked. Home-collection households follow, ordered outward from it. Meeting
    times accumulate along the route from a single fleet-wide departure, using
    straight-line distance at a configured average speed; no congestion is
    modelled, which is the downstream microsimulation's job.
    """
    speed_m_per_second = planning.average_driving_speed_kph * 1000.0 / 3600.0
=======
    The muster point is always the first stop — it is where the vehicle is
    parked. Home-collection households follow, ordered outward from it. Meeting
    times accumulate along the route from a single fleet-wide departure: a
    configured dwell for each stop already made, plus straight-line distance at
    a configured average speed. No congestion is modelled, which is the
    downstream microsimulation's job.
    """
    speed_m_per_second = planning.average_driving_speed_kph * 1000.0 / 3600.0
    dwell_seconds = planning.stop_dwell_minutes * 60.0
>>>>>>> Stashed changes

    with warehouse.connect() as conn:
        # --- Leader: a car-licensed occupant, preferring the owner household --
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE vehicle_leaders AS
            SELECT muster_point_id, vehicle_id, person_id AS leader_person_id,
                   leader_name, leader_phone, from_owner_household
            FROM (
                SELECT s.muster_point_id, m.vehicle_id, p.person_id,
                       p.name AS leader_name, p.phone_number AS leader_phone,
                       (p.household_id = m.household_id) AS from_owner_household,
                       row_number() OVER (
                           PARTITION BY s.muster_point_id
<<<<<<< Updated upstream
                           ORDER BY p.person_id
=======
                           ORDER BY (p.household_id = m.household_id) DESC, p.person_id
>>>>>>> Stashed changes
                       ) AS rank
                FROM person_seats s
                JOIN persons p USING (person_id)
                JOIN muster_points m ON m.muster_point_id = s.muster_point_id
                WHERE p.license_type = '{LICENSE_CAR}'
<<<<<<< Updated upstream
                  AND p.household_id = m.household_id
=======
>>>>>>> Stashed changes
            ) WHERE rank = 1
            """
        )

        # --- Stops: the muster point, then each collection household ---------
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE route_stops AS
            WITH occupants AS (
                SELECT s.muster_point_id, s.person_id, p.household_id,
                       g.all_can_walk
                FROM person_seats s
                JOIN persons p USING (person_id)
                JOIN travel_groups g ON g.travel_group_id = s.travel_group_id
            ),
            muster_stop AS (
                SELECT m.muster_point_id, '{STOP_MUSTER}' AS stop_type,
                       NULL::BIGINT AS household_id, NULL::BIGINT AS uprn,
                       m.easting, m.northing, 0.0 AS distance_from_muster_m
                FROM muster_points m
                WHERE EXISTS (SELECT 1 FROM vehicle_leaders v
                              WHERE v.muster_point_id = m.muster_point_id)
            ),
            collection_stops AS (
                SELECT DISTINCT o.muster_point_id, '{STOP_COLLECTION}' AS stop_type,
                       o.household_id, l.uprn, l.easting, l.northing,
                       ST_Distance(ST_Point(l.easting, l.northing),
                                   ST_Point(m.easting, m.northing))
                         AS distance_from_muster_m
                FROM occupants o
                JOIN household_locations l USING (household_id)
                JOIN muster_points m ON m.muster_point_id = o.muster_point_id
                JOIN vehicle_leaders v ON v.muster_point_id = o.muster_point_id
                WHERE NOT o.all_can_walk
            ),
            ordered AS (
                SELECT *,
                       row_number() OVER (
                           PARTITION BY muster_point_id
                           ORDER BY distance_from_muster_m, household_id
                       ) AS stop_seq
                FROM (SELECT * FROM muster_stop UNION ALL SELECT * FROM collection_stops)
            ),
            legs AS (
                SELECT *,
                       coalesce(
                           ST_Distance(
                               ST_Point(easting, northing),
                               ST_Point(
                                   lag(easting) OVER w,
                                   lag(northing) OVER w
                               )
                           ), 0.0
                       ) AS leg_m
                FROM ordered
                WINDOW w AS (PARTITION BY muster_point_id ORDER BY stop_seq)
            )
            SELECT muster_point_id, stop_seq, stop_type, household_id, uprn,
                   easting, northing, distance_from_muster_m, leg_m,
                   sum(leg_m) OVER (
                       PARTITION BY muster_point_id ORDER BY stop_seq
                   ) AS cumulative_m,
                   TIMESTAMP '{planning.fleet_departure_time}'
                     + to_seconds(
                         sum(leg_m) OVER (
                             PARTITION BY muster_point_id ORDER BY stop_seq
                         ) / {speed_m_per_second}
<<<<<<< Updated upstream
                       ) AS meeting_time
=======
                       )
                     -- Every earlier stop cost its dwell before this one begins.
                     + to_seconds({dwell_seconds} * (stop_seq - 1))
                       AS meeting_time
>>>>>>> Stashed changes
            FROM legs
            """
        )

        # --- The route itself, with its single fleet-wide destination --------
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE vehicle_routes AS
            SELECT v.muster_point_id, v.vehicle_id, m.license_plate,
                   v.leader_person_id, v.leader_name, v.leader_phone,
                   v.from_owner_household AS leader_from_owner_household,
                   count(s.stop_seq) AS stop_count,
                   count(s.stop_seq) FILTER (WHERE s.stop_type = '{STOP_COLLECTION}')
                     AS collection_stops,
                   max(s.meeting_time) AS final_stop_time,
                   '{planning.destination_name}' AS destination_name,
                   {planning.destination_latitude} AS destination_latitude,
                   {planning.destination_longitude} AS destination_longitude
            FROM vehicle_leaders v
            JOIN route_stops s USING (muster_point_id)
            JOIN (SELECT mp.muster_point_id, ve.license_plate
                  FROM muster_points mp JOIN vehicles ve USING (vehicle_id)) m
              USING (muster_point_id)
            GROUP BY ALL
            """
        )

        stats = conn.execute(
            """
            SELECT (SELECT count(*) FROM vehicle_routes) AS routes,
                   (SELECT count(*) FROM vehicle_routes
                     WHERE leader_from_owner_household) AS leaders_from_owner,
                   (SELECT count(*) FROM vehicle_routes WHERE stop_count = 1)
                     AS single_stop_routes,
                   (SELECT max(collection_stops) FROM vehicle_routes)
                     AS max_collection_stops,
                   (SELECT count(*) FROM route_stops) AS stops
            """
        ).fetchdf().iloc[0]

    context.log.info(
        "Built %s routes (%s single-stop, max %s collection stops); "
        "%s leaders come from the owner household",
        f"{int(stats['routes']):,}",
        f"{int(stats['single_stop_routes']):,}",
        int(stats["max_collection_stops"]),
        f"{int(stats['leaders_from_owner']):,}",
    )
    context.add_output_metadata({key: int(stats[key]) for key in stats.index})


@asset(deps=[vehicle_routes], group_name="routes")
def stop_notifications(
    context: AssetExecutionContext,
    warehouse: SpatialDuckDBResource,
) -> None:
    """List who is expected at each stop, and how each of them is contacted.

    A person with a phone is told directly. A person without one but with a
    ``dependent_of`` carer has the carer told on their behalf. A person with
    neither cannot be notified at all and is marked for physical collection.
    """
    with warehouse.connect() as conn:
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE stop_notifications AS
            WITH occupants AS (
                SELECT s.muster_point_id, s.person_id, p.household_id, p.name,
                       p.phone_number, g.all_can_walk
                FROM person_seats s
                JOIN persons p USING (person_id)
                JOIN travel_groups g ON g.travel_group_id = s.travel_group_id
            ),
            -- A person is expected at their own home when their group is being
            -- collected, and at the muster point otherwise.
            placed AS (
                SELECT o.muster_point_id, o.person_id, o.name, o.phone_number,
                       CASE WHEN o.all_can_walk THEN '{STOP_MUSTER}'
                            ELSE '{STOP_COLLECTION}' END AS stop_type,
                       CASE WHEN o.all_can_walk THEN NULL ELSE o.household_id END
                         AS household_id
                FROM occupants o
            ),
            carers AS (
                SELECT r.person_id, min(r.related_person_id) AS carer_person_id
                FROM person_relationships r
                WHERE r.relationship_type = '{RELATIONSHIP_DEPENDENT_OF}'
                GROUP BY r.person_id
            )
            SELECT st.muster_point_id, st.stop_seq, st.stop_type,
                   p.person_id, p.name, p.phone_number,
                   c.carer_person_id,
                   carer.name AS carer_name,
                   carer.phone_number AS carer_phone,
                   CASE
                       WHEN p.phone_number IS NOT NULL THEN '{NOTIFY_DIRECT}'
                       WHEN c.carer_person_id IS NOT NULL THEN '{NOTIFY_CARER}'
                       ELSE '{NOTIFY_PHYSICAL}'
                   END AS notification_mode
            FROM placed p
            JOIN route_stops st
              ON st.muster_point_id = p.muster_point_id
             AND st.stop_type = p.stop_type
             AND (p.household_id IS NULL OR st.household_id = p.household_id)
            LEFT JOIN carers c ON c.person_id = p.person_id
            LEFT JOIN persons carer ON carer.person_id = c.carer_person_id
            """
        )
        stats = conn.execute(
            """
            SELECT count(*) AS notified_rows,
                   count(*) FILTER (WHERE notification_mode = ?) AS direct,
                   count(*) FILTER (WHERE notification_mode = ?) AS via_carer,
                   count(*) FILTER (WHERE notification_mode = ?) AS physical
            FROM stop_notifications
            """,
            [NOTIFY_DIRECT, NOTIFY_CARER, NOTIFY_PHYSICAL],
        ).fetchdf().iloc[0]

    context.log.info(
        "Notifications: %s direct, %s via a carer, %s needing physical collection",
        f"{int(stats['direct']):,}",
        f"{int(stats['via_carer']):,}",
        f"{int(stats['physical']):,}",
    )
    context.add_output_metadata({key: int(stats[key]) for key in stats.index})
