"""The evacuation as a traffic microsimulation scenario.

This capability makes no allocation decisions. Who rides in which car, which car
departs, and which collection stops it makes were all fixed upstream; this says
where the evacuation ends, when each vehicle sets off, and renders the result
into something a simulator can load.

Two things here are modelling assumptions rather than derivations, and both are
reported so they stay visible: vehicles head for their *nearest* exit, and an
exit is an infinite-capacity sink, so nothing queues beyond the district
boundary.
"""

from datetime import datetime

import pandas as pd
from dagster import AssetExecutionContext, MetadataValue, asset

from london_frontline.assets.geography import LEVEL_LAD, admin_areas
from london_frontline.assets.routes import vehicle_routes
from london_frontline.assets.vehicles import muster_points
from london_frontline.assets.walking_report import report_walking_by_demographic
from london_frontline.assets.routes import STOP_COLLECTION
from london_frontline.config import PlanningConfig
from london_frontline.exits import Crossing, cluster_crossings, require_exits
from london_frontline.matsim import (
    ACTIVITY_COLLECTION,
    ACTIVITY_EVACUATION,
    ACTIVITY_HOME,
    ACTIVITY_MUSTER,
    CRS,
    MODE_CAR,
    MODE_RIDE,
    MODE_WALK,
    Activity,
    Leg,
    Plan,
    write_config,
    write_households,
    write_population,
    write_vehicles,
)
from london_frontline.mobilisation import MobilisationProfile, departure_offsets_seconds
from london_frontline.paths import resolve
from london_frontline.resources import SpatialDuckDBResource

BNG_SRS = "EPSG:27700"
WGS84_SRS = "EPSG:4326"

SHORTFALL_NO_EXIT = "no qualifying district exit"
SHORTFALL_UNRESOLVED_REF = "network reference could not be resolved to a road way"

# Where a leader's walk to their vehicle came from. The network figure is the
# real one; the straight line is a floor used only where the pair is missing from
# the candidate list, which happens when a household was seated in its own
# vehicle without that vehicle ranking among its nearest candidates.
ACCESS_FROM_NETWORK = "network"
ACCESS_FROM_STRAIGHT_LINE = "straight_line"


@asset(deps=[admin_areas, muster_points], group_name="scenario")
def district_exits(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Where drivable roads leave the district.

    The evacuation is scoped to clearing the district rather than reaching a town
    outside it, because the road network only covers the district: a destination
    beyond its edge is a point on no network.

    An exit qualifies on two tests. Its road must be a through class — a driveway
    or car-park access crossing the boundary is not an evacuation route — and it
    must continue far enough beyond the boundary to be leaving rather than
    grazing it. The second test exists because much of a coastal district's
    boundary is coastline: in Thanet it rejects the two Royal Harbour Approach
    ways, which cross by 29 m and 8 m at Ramsgate harbour, while admitting Plucks
    Gutter at 66 m.

    Surviving crossings are then collapsed into corridors, because one road out
    of a district rarely crosses its boundary exactly once — a dual carriageway
    crosses per carriageway, and one road is often several ways of different
    classes. Left separate, the exit a vehicle heads for turns on a few metres:
    Thanet's four Ramsgate Road crossings sit within 292 m, and proximity put 90%
    of the fleet onto the tertiary one while a trunk crossing 250 m away took
    seven vehicles. Each corridor is represented by its most major road instead.
    """
    classes = ", ".join(f"'{value}'" for value in planning.exit_highway_classes)

    with warehouse.connect() as conn:
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE district_boundary_crossings AS
            WITH district AS (
                SELECT ST_Transform(geometry, '{WGS84_SRS}', '{BNG_SRS}',
                                    always_xy := true) AS area
                FROM admin_areas WHERE level = '{LEVEL_LAD}'
            ),
            candidates AS (
                SELECT r.way_id, r.highway, r.name, r.geometry,
                       ST_Length(ST_Intersection(r.geometry, d.area))
                         AS length_inside_m,
                       ST_Length(ST_Difference(r.geometry, d.area))
                         AS length_outside_m,
                       ST_Intersection(r.geometry, ST_Boundary(d.area))
                         AS crossings
                FROM road_centrelines r, district d
                WHERE r.highway IN ({classes})
                  AND ST_Intersects(r.geometry, ST_Boundary(d.area))
            ),
            qualified AS (
                SELECT * FROM candidates
                WHERE length_outside_m >= {planning.minimum_exit_length_outside_m}
            ),
            -- A way may cross the boundary more than once; each crossing is its
            -- own way out, so they are exploded rather than reduced to one.
            points AS (
                SELECT q.way_id, q.highway, q.name, q.geometry,
                       q.length_inside_m, q.length_outside_m,
                       unnest(ST_Dump(q.crossings)).geom AS crossing
                FROM qualified q
            )
            SELECT row_number() OVER (ORDER BY way_id, ST_X(crossing),
                                      ST_Y(crossing)) - 1 AS crossing_id,
                   way_id, highway, name,
                   ST_X(crossing) AS easting,
                   ST_Y(crossing) AS northing,
                   ST_LineLocatePoint(geometry, crossing) * ST_Length(geometry)
                     AS way_position_m,
                   length_inside_m, length_outside_m
            FROM points
            WHERE ST_GeometryType(crossing) = 'POINT'
            """
        )

        # Crossings collapse into corridors in Python: single-linkage grouping
        # and a class-ranked representative are clearer as code than as a
        # recursive join, and there are only ever a handful of crossings.
        crossings = conn.execute(
            """
            SELECT crossing_id, way_id, highway, easting, northing, length_outside_m
            FROM district_boundary_crossings ORDER BY crossing_id
            """
        ).fetchdf()
        corridors = cluster_crossings(
            [
                Crossing(
                    crossing_id=int(row.crossing_id),
                    way_id=int(row.way_id),
                    highway=str(row.highway),
                    easting=float(row.easting),
                    northing=float(row.northing),
                    length_outside_m=float(row.length_outside_m),
                )
                for row in crossings.itertuples()
            ],
            radius_m=planning.exit_cluster_radius_m,
            class_priority=planning.exit_highway_classes,
        )
        chosen = pd.DataFrame(
            [
                {
                    "exit_id": exit_id,
                    "crossing_id": corridor.representative.crossing_id,
                    "crossing_count": corridor.crossing_count,
                    "merged_way_ids": ",".join(
                        str(member.way_id)
                        for member in sorted(corridor.members, key=lambda m: m.way_id)
                    ),
                }
                for exit_id, corridor in enumerate(corridors)
            ],
            columns=["exit_id", "crossing_id", "crossing_count", "merged_way_ids"],
        )

        conn.register("chosen_df", chosen)
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE district_exits AS
            SELECT c.exit_id, b.way_id, b.highway, b.name,
                   b.easting, b.northing, b.way_position_m,
                   b.length_inside_m, b.length_outside_m,
                   c.crossing_count, c.merged_way_ids,
                   ST_Transform(ST_Point(b.easting, b.northing),
                                '{BNG_SRS}', '{WGS84_SRS}',
                                always_xy := true) AS geometry
            FROM chosen_df c
            JOIN district_boundary_crossings b USING (crossing_id)
            ORDER BY c.exit_id
            """
        )

        conn.execute(
            f"""
            CREATE OR REPLACE TABLE scenario_shortfalls AS
            SELECT '{SHORTFALL_NO_EXIT}' AS reason,
                   '{planning.lad_code}' AS detail,
                   0 AS affected
            WHERE (SELECT count(*) FROM district_exits) = 0
            """
        )

        listing = conn.execute(
            """
            SELECT exit_id, coalesce(name, '(unnamed)') AS name, highway,
                   crossing_count,
                   round(length_outside_m, 1) AS beyond_boundary_m,
                   round(length_inside_m, 1) AS within_district_m
            FROM district_exits ORDER BY length_outside_m DESC
            """
        ).fetchdf()
        rejected = conn.execute(
            f"""
            WITH district AS (
                SELECT ST_Transform(geometry, '{WGS84_SRS}', '{BNG_SRS}',
                                    always_xy := true) AS area
                FROM admin_areas WHERE level = '{LEVEL_LAD}'
            )
            SELECT count(*) FILTER (WHERE r.highway NOT IN ({classes}))
                     AS wrong_class,
                   count(*) FILTER (
                       WHERE r.highway IN ({classes})
                         AND ST_Length(ST_Difference(r.geometry, d.area))
                             < {planning.minimum_exit_length_outside_m}
                   ) AS too_short
            FROM road_centrelines r, district d
            WHERE ST_Intersects(r.geometry, ST_Boundary(d.area))
            """
        ).fetchdf().iloc[0]

    if listing.empty:
        context.log.error(
            "No road leaves %s as a through route continuing at least %.0f m "
            "beyond the boundary; the scenario has nowhere to evacuate to",
            planning.lad_code,
            planning.minimum_exit_length_outside_m,
        )
    else:
        context.log.info(
            "%s district exits qualify for %s; rejected %s crossings of the wrong "
            "class and %s that do not continue %.0f m beyond the boundary",
            len(listing),
            planning.lad_code,
            int(rejected["wrong_class"]),
            int(rejected["too_short"]),
            planning.minimum_exit_length_outside_m,
        )

    context.add_output_metadata(
        {
            "exits": len(listing),
            "lad_code": planning.lad_code,
            "minimum_length_outside_m": planning.minimum_exit_length_outside_m,
            "rejected_wrong_class": int(rejected["wrong_class"]),
            "rejected_too_short": int(rejected["too_short"]),
            "qualifying_exits": MetadataValue.md(
                listing.to_markdown(index=False) if not listing.empty else "_none_"
            ),
        }
    )


@asset(
    deps=[district_exits, vehicle_routes, report_walking_by_demographic],
    group_name="scenario",
)
def vehicle_departures(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """When each vehicle sets off, and which way out it is heading for.

    Departure is notification plus a mobilisation delay drawn per vehicle, plus
    however long the driver needs to reach the car. The middle term is what stops
    the whole fleet appearing on the network at once; the third is near zero
    while leaders come from the owner household, and becomes material as soon as
    they do not.

    The exit is the nearest one to where the vehicle finishes collecting. Drivers
    really choose by expected travel time, so this will over-concentrate demand
    on the closest way out — the loading per exit is reported for exactly that
    reason.
    """
    profile = MobilisationProfile(
        median_minutes=planning.mobilisation_median_minutes,
        sigma=planning.mobilisation_sigma,
    )
    walking_speed_m_per_s = planning.walking_speed_kph * 1000.0 / 3600.0

    with warehouse.connect() as conn:
        require_exits(
            conn.execute("SELECT count(*) FROM district_exits").fetchone()[0],
            planning.lad_code,
        )

        # The driver's walk to the car. network_minutes is missing where the
        # household's assigned vehicle was not among its nearest walk candidates,
        # so the straight line stands in rather than the walk silently becoming
        # zero.
        fleet = conn.execute(
            f"""
            WITH final_stop AS (
                SELECT muster_point_id, easting, northing
                FROM (
                    SELECT muster_point_id, easting, northing,
                           row_number() OVER (PARTITION BY muster_point_id
                                              ORDER BY stop_seq DESC) AS rank
                    FROM route_stops
                ) WHERE rank = 1
            )
            SELECT v.vehicle_id, v.muster_point_id, v.leader_person_id,
                   f.easting AS final_easting, f.northing AS final_northing,
                   coalesce(
                       w.network_minutes * 60.0,
                       w.straight_line_m / {walking_speed_m_per_s}
                   ) AS access_seconds,
                   CASE WHEN w.network_minutes IS NULL
                        THEN '{ACCESS_FROM_STRAIGHT_LINE}'
                        ELSE '{ACCESS_FROM_NETWORK}' END AS access_source
            FROM vehicle_routes v
            JOIN final_stop f USING (muster_point_id)
            LEFT JOIN person_walks w
              ON w.person_id = v.leader_person_id
             AND w.muster_point_id = v.muster_point_id
            ORDER BY v.vehicle_id
            """
        ).fetchdf()

        if fleet["access_seconds"].isna().any():
            unresolved = int(fleet["access_seconds"].isna().sum())
            raise ValueError(
                f"{unresolved:,} departing vehicles have no walk to the car by "
                "either measure; the leader is seated at a muster point their "
                "household has no location for"
            )

        offsets, delays, access = departure_offsets_seconds(
            fleet["vehicle_id"].to_numpy(),
            fleet["access_seconds"].to_numpy(),
            profile,
            seed=planning.random_seed,
            include_driver_access_time=planning.include_driver_access_time,
        )
        fleet["departure_offset_s"] = offsets
        fleet["mobilisation_delay_s"] = delays
        fleet["driver_access_s"] = access

        conn.register("fleet_df", fleet)
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE vehicle_departures AS
            WITH assigned AS (
                SELECT f.*, e.exit_id, e.exit_distance_m
                FROM fleet_df f
                JOIN LATERAL (
                    SELECT d.exit_id,
                           ST_Distance(ST_Point(f.final_easting, f.final_northing),
                                       ST_Point(d.easting, d.northing))
                             AS exit_distance_m
                    FROM district_exits d
                    ORDER BY exit_distance_m, d.exit_id
                    LIMIT 1
                ) e ON true
            )
            SELECT vehicle_id, muster_point_id, leader_person_id,
                   exit_id, exit_distance_m,
                   mobilisation_delay_s, driver_access_s, access_source,
                   departure_offset_s,
                   TIMESTAMP '{planning.evacuation_notification_time}' AS notified_at,
                   TIMESTAMP '{planning.evacuation_notification_time}'
                     + to_seconds(departure_offset_s) AS departs_at
            FROM assigned
            """
        )

        stats = conn.execute(
            """
            SELECT count(*) AS vehicles,
                   count(*) FILTER (WHERE access_source = ?) AS access_by_straight_line,
                   round(min(departure_offset_s) / 60.0, 2) AS first_departure_min,
                   round(median(departure_offset_s) / 60.0, 2) AS median_departure_min,
                   round(quantile_cont(departure_offset_s, 0.9) / 60.0, 2) AS p90_min,
                   round(max(departure_offset_s) / 60.0, 2) AS last_departure_min,
                   round(max(driver_access_s) / 60.0, 2) AS max_access_min,
                   round(median(driver_access_s) / 60.0, 2) AS median_access_min
            FROM vehicle_departures
            """,
            [ACCESS_FROM_STRAIGHT_LINE],
        ).fetchdf().iloc[0]
        loading = conn.execute(
            """
            SELECT d.exit_id, coalesce(e.name, '(unnamed)') AS name, e.highway,
                   count(*) AS vehicles,
                   round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS share_pct,
                   round(median(d.exit_distance_m)) AS median_distance_m
            FROM vehicle_departures d
            JOIN district_exits e USING (exit_id)
            GROUP BY d.exit_id, e.name, e.highway
            ORDER BY vehicles DESC
            """
        ).fetchdf()

    context.log.info(
        "%s vehicles depart between %.1f and %.1f minutes after notification "
        "(median %.1f); busiest exit takes %.1f%% of them",
        f"{int(stats['vehicles']):,}",
        stats["first_departure_min"],
        stats["last_departure_min"],
        stats["median_departure_min"],
        loading.iloc[0]["share_pct"],
    )
    if int(stats["access_by_straight_line"]) > 0:
        context.log.info(
            "%s leaders' walk to the car came from the straight line, having no "
            "network walk recorded for that pair",
            f"{int(stats['access_by_straight_line']):,}",
        )

    context.add_output_metadata(
        {
            "vehicles_departing": int(stats["vehicles"]),
            "access_by_straight_line": int(stats["access_by_straight_line"]),
            "first_departure_min": float(stats["first_departure_min"]),
            "median_departure_min": float(stats["median_departure_min"]),
            "p90_departure_min": float(stats["p90_min"]),
            "last_departure_min": float(stats["last_departure_min"]),
            "departure_span_min": float(
                stats["last_departure_min"] - stats["first_departure_min"]
            ),
            "median_driver_access_min": float(stats["median_access_min"]),
            "max_driver_access_min": float(stats["max_access_min"]),
            "mobilisation_median_minutes": planning.mobilisation_median_minutes,
            "mobilisation_sigma": planning.mobilisation_sigma,
            "driver_access_included": planning.include_driver_access_time,
            "exit_loading": MetadataValue.md(loading.to_markdown(index=False)),
        }
    )


@asset(deps=[vehicle_departures], group_name="scenario")
def matsim_scenario(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Render the plan as a MATSim scenario.

    One plan per seated person. The driver's plan is the one that puts a vehicle
    on the network — passengers travel by ``ride``, which MATSim teleports — so
    the number of vehicles entering the network is exactly the number of cars
    that depart, which is the quantity the whole ride-share comparison rests on.

    The network itself is not built here. Plans carry British National Grid
    coordinates and every network-bound location is also published by OSM way and
    position, so MATSim's own OpenStreetMap reader builds the network and the
    scenario binds to it. Link ids are deliberately absent: they are assigned at
    build time and differ between readers and between builds, so a scenario keyed
    on them would break every time the network was rebuilt.
    """
    out_dir = resolve(planning.data_dir) / "exports" / "matsim"
    driving_speed_m_per_s = planning.average_driving_speed_kph * 1000.0 / 3600.0
    walking_speed_m_per_s = planning.walking_speed_kph * 1000.0 / 3600.0
    notified = datetime.fromisoformat(planning.evacuation_notification_time)
    notification_s = (
        notified.hour * 3600 + notified.minute * 60 + notified.second
    )

    with warehouse.connect() as conn:
        require_exits(
            conn.execute("SELECT count(*) FROM district_exits").fetchone()[0],
            planning.lad_code,
        )

        # When the vehicle reaches each collection stop: its own departure, plus
        # the drive so far, plus the time spent at every earlier stop.
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE scenario_collection_schedule AS
            SELECT s.muster_point_id, s.stop_seq, s.household_id,
                   s.easting, s.northing,
                   d.departure_offset_s
                     + s.cumulative_m / {driving_speed_m_per_s}
                     + {planning.collection_stop_dwell_seconds}
                       * (row_number() OVER (PARTITION BY s.muster_point_id
                                             ORDER BY s.stop_seq) - 1)
                     AS arrival_offset_s
            FROM route_stops s
            JOIN vehicle_departures d USING (muster_point_id)
            WHERE s.stop_type = '{STOP_COLLECTION}'
            """
        )

        # Every location the scenario needs bound to a network link, published by
        # way rather than by link id.
        conn.execute(
            """
            CREATE OR REPLACE TABLE scenario_network_refs AS
            SELECT 'muster_point' AS reference_type, m.muster_point_id AS reference_id,
                   m.way_id, m.way_position_m, m.easting, m.northing
            FROM muster_points m
            WHERE EXISTS (SELECT 1 FROM vehicle_departures d
                          WHERE d.muster_point_id = m.muster_point_id)
            UNION ALL
            SELECT 'district_exit', e.exit_id, e.way_id, e.way_position_m,
                   e.easting, e.northing
            FROM district_exits e
            """
        )

        stops = conn.execute(
            """
            SELECT muster_point_id, easting, northing
            FROM scenario_collection_schedule ORDER BY muster_point_id, stop_seq
            """
        ).fetchdf()
        collection_stops: dict[int, list[tuple[float, float]]] = {}
        for row in stops.itertuples():
            collection_stops.setdefault(int(row.muster_point_id), []).append(
                (float(row.easting), float(row.northing))
            )

        people = conn.execute(
            f"""
            SELECT s.person_id, p.household_id, s.muster_point_id,
                   d.vehicle_id, d.exit_id,
                   (d.leader_person_id = s.person_id) AS is_driver,
                   g.all_can_walk,
                   l.easting AS home_easting, l.northing AS home_northing,
                   m.easting AS muster_easting, m.northing AS muster_northing,
                   e.easting AS exit_easting, e.northing AS exit_northing,
                   d.departure_offset_s, d.mobilisation_delay_s,
                   coalesce(w.network_minutes * 60.0,
                            w.straight_line_m / {walking_speed_m_per_s})
                     AS walk_seconds,
                   c.arrival_offset_s AS collection_offset_s
            FROM person_seats s
            JOIN persons p USING (person_id)
            JOIN travel_groups g ON g.travel_group_id = s.travel_group_id
            JOIN household_locations l ON l.household_id = p.household_id
            JOIN muster_points m ON m.muster_point_id = s.muster_point_id
            JOIN vehicle_departures d ON d.muster_point_id = s.muster_point_id
            JOIN district_exits e ON e.exit_id = d.exit_id
            LEFT JOIN person_walks w
              ON w.person_id = s.person_id AND w.muster_point_id = s.muster_point_id
            LEFT JOIN scenario_collection_schedule c
              ON c.muster_point_id = s.muster_point_id
             AND c.household_id = p.household_id
            ORDER BY s.person_id
            """
        ).fetchdf()

        def plans():
            for row in people.itertuples():
                home = (float(row.home_easting), float(row.home_northing))
                muster = (float(row.muster_easting), float(row.muster_northing))
                destination = Activity(
                    ACTIVITY_EVACUATION, float(row.exit_easting), float(row.exit_northing)
                )
                departs_at = notification_s + float(row.departure_offset_s)

                if row.is_driver:
                    # Must be at the car to drive it, whatever their group's
                    # walking status: for these drivers the car is their own, a
                    # median 16 m from the front door.
                    elements = [
                        Activity(
                            ACTIVITY_HOME, *home,
                            end_time_s=notification_s + float(row.mobilisation_delay_s),
                        ),
                        Leg(MODE_WALK),
                        Activity(ACTIVITY_MUSTER, *muster, end_time_s=departs_at),
                    ]
                    for easting, northing in collection_stops.get(
                        int(row.muster_point_id), []
                    ):
                        elements.append(Leg(MODE_CAR))
                        elements.append(
                            Activity(
                                ACTIVITY_COLLECTION, easting, northing,
                                max_duration_s=planning.collection_stop_dwell_seconds,
                            )
                        )
                    elements.extend([Leg(MODE_CAR), destination])
                    yield Plan(
                        person_id=int(row.person_id),
                        elements=tuple(elements),
                        vehicle_id=int(row.vehicle_id),
                    )
                elif row.all_can_walk:
                    # Leave home in time to reach the car before it goes, but
                    # never before the order is given.
                    leaves_home = max(
                        float(notification_s), departs_at - float(row.walk_seconds)
                    )
                    yield Plan(
                        person_id=int(row.person_id),
                        elements=(
                            Activity(ACTIVITY_HOME, *home, end_time_s=leaves_home),
                            Leg(MODE_WALK),
                            Activity(ACTIVITY_MUSTER, *muster, end_time_s=departs_at),
                            Leg(MODE_RIDE),
                            destination,
                        ),
                    )
                else:
                    # Collected at their own door; no walk to the muster point.
                    yield Plan(
                        person_id=int(row.person_id),
                        elements=(
                            Activity(
                                ACTIVITY_HOME, *home,
                                end_time_s=notification_s
                                + float(row.collection_offset_s),
                            ),
                            Leg(MODE_RIDE),
                            destination,
                        ),
                    )

        persons_written = write_population(out_dir / "population.xml.gz", plans())

        vehicle_ids = [
            int(value)
            for (value,) in conn.execute(
                "SELECT vehicle_id FROM vehicle_departures ORDER BY vehicle_id"
            ).fetchall()
        ]
        vehicles_written = write_vehicles(
            out_dir / "vehicles.xml.gz", vehicle_ids, seats=planning.vehicle_capacity
        )

        household_rows = conn.execute(
            """
            SELECT p.household_id,
                   list(DISTINCT s.person_id ORDER BY s.person_id) AS members,
                   coalesce(
                       list(DISTINCT d.vehicle_id ORDER BY d.vehicle_id)
                         FILTER (WHERE d.vehicle_id IS NOT NULL),
                       []
                   ) AS owned
            FROM person_seats s
            JOIN persons p USING (person_id)
            LEFT JOIN vehicles v ON v.household_id = p.household_id
            LEFT JOIN vehicle_departures d ON d.vehicle_id = v.vehicle_id
            GROUP BY p.household_id
            ORDER BY p.household_id
            """
        ).fetchdf()
        households_written = write_households(
            out_dir / "households.xml.gz",
            (
                (int(row.household_id), list(row.members), list(row.owned))
                for row in household_rows.itertuples()
            ),
        )

        write_config(
            out_dir / "config.xml",
            network_file="network.xml.gz",
            plans_file="population.xml.gz",
            vehicles_file="vehicles.xml.gz",
            households_file="households.xml.gz",
        )

        # Who the scenario leaves out, reconciled against the shortfall the
        # assignment stage already recorded. The two must agree: a discrepancy
        # means people are vanishing between the plan and the export rather than
        # being recorded as unseated.
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE report_scenario_coverage AS
            SELECT 'people in the population' AS measure,
                   (SELECT count(*) FROM persons) AS value,
                   'the household population; communal establishments are absent'
                     AS note
            UNION ALL
            SELECT 'people holding a seat',
                   (SELECT count(*) FROM person_seats),
                   'one plan each in the emitted scenario'
            UNION ALL
            SELECT 'people with no seat',
                   (SELECT count(*) FROM persons)
                     - (SELECT count(*) FROM person_seats),
                   'absent from the scenario; they cannot evacuate by car'
            UNION ALL
            SELECT 'people recorded as unmet demand',
                   (SELECT coalesce(sum(size), 0) FROM unmet_demand),
                   'should equal the people with no seat'
            UNION ALL
            SELECT 'vehicles on the network',
                   (SELECT count(*) FROM vehicle_departures),
                   'one per departing car, whatever its number of stops'
            UNION ALL
            SELECT 'network references published',
                   (SELECT count(*) FROM scenario_network_refs),
                   'muster points and exits, keyed by OSM way'
            """
        )

        # A location whose way cannot be resolved falls back to nearest-link
        # matching in the simulator, which is exactly the guessing the way
        # reference exists to avoid. Recorded rather than raised.
        conn.execute(
            f"""
            INSERT INTO scenario_shortfalls
            SELECT '{SHORTFALL_UNRESOLVED_REF}' AS reason,
                   reference_type AS detail,
                   count(*) AS affected
            FROM scenario_network_refs r
            WHERE r.way_id IS NULL OR r.way_position_m IS NULL
               OR NOT EXISTS (
                 SELECT 1 FROM road_centrelines c WHERE c.way_id = r.way_id
               )
            GROUP BY reference_type
            """
        )

        excluded = conn.execute(
            """
            SELECT (SELECT count(*) FROM persons)
                   - (SELECT count(*) FROM person_seats) AS unseated,
                   (SELECT coalesce(sum(size), 0) FROM unmet_demand) AS unmet_recorded
            """
        ).fetchdf().iloc[0]
        unresolved_refs = conn.execute(
            f"""
            SELECT coalesce(sum(affected), 0) FROM scenario_shortfalls
            WHERE reason = '{SHORTFALL_UNRESOLVED_REF}'
            """
        ).fetchone()[0]
        coverage = conn.execute("SELECT * FROM report_scenario_coverage").fetchdf()
        collection_drivers = conn.execute(
            """
            SELECT count(*) FROM vehicle_departures d
            JOIN person_seats s ON s.person_id = d.leader_person_id
                               AND s.muster_point_id = d.muster_point_id
            JOIN travel_groups g ON g.travel_group_id = s.travel_group_id
            WHERE NOT g.all_can_walk
            """
        ).fetchone()[0]

    unseated = int(excluded["unseated"])
    context.log.info(
        "Wrote %s plans, %s vehicles and %s households to %s; %s people hold no "
        "seat and are absent from the scenario",
        f"{persons_written:,}",
        f"{vehicles_written:,}",
        f"{households_written:,}",
        out_dir,
        f"{unseated:,}",
    )
    if unresolved_refs:
        context.log.warning(
            "%s network references could not be resolved to a road way; those "
            "locations fall back to nearest-link matching in the simulator",
            f"{unresolved_refs:,}",
        )

    context.add_output_metadata(
        {
            "path": MetadataValue.path(str(out_dir)),
            "plans": persons_written,
            "vehicles": vehicles_written,
            "households": households_written,
            "people_without_a_seat": unseated,
            "unmet_demand_recorded": int(excluded["unmet_recorded"]),
            "unresolved_network_refs": unresolved_refs,
            "drivers_from_collection_groups": collection_drivers,
            "collection_stop_dwell_seconds": planning.collection_stop_dwell_seconds,
            "coordinate_reference_system": CRS,
            "coverage": MetadataValue.md(coverage.to_markdown(index=False)),
        }
    )
