"""Vehicle generation, road ingestion, and the muster points vehicles park at."""

from dagster import AssetExecutionContext, asset

from london_frontline.assets.geography import LEVEL_LAD, admin_areas
from london_frontline.assets.home_assignment import household_locations
from london_frontline.config import PlanningConfig
from london_frontline.resources import SpatialDuckDBResource
from london_frontline.sources import osm

VEHICLE_TYPE_CAR = "car"

BNG_SRS = "EPSG:27700"
WGS84_SRS = "EPSG:4326"

# Roads are fetched for the LAD's bounding box grown by this margin, so a home
# near the boundary can still snap to a road just outside it.
_BBOX_MARGIN_DEGREES = 0.02


@asset(deps=[household_locations], group_name="vehicles")
def vehicles(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Generate one Vehicle per car owned by a household."""
    with warehouse.connect() as conn:
        # A household with num_cars = n yields n vehicles, via a row per car.
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE vehicles AS
            SELECT
                row_number() OVER (ORDER BY h.household_id, car.n) - 1 AS vehicle_id,
                h.household_id,
                h.area_id,
                '{VEHICLE_TYPE_CAR}' AS vehicle_type,
                {planning.vehicle_capacity} AS capacity
            FROM households h
            CROSS JOIN LATERAL (
                SELECT unnest(generate_series(1, CAST(h.num_cars AS INTEGER))) AS n
            ) car
            WHERE h.num_cars > 0
            """
        )
        # A UK-shaped plate (AB12 CDE) derived deterministically from the id, so a
        # rerun with the same seed produces the same plates.
        conn.execute(
            """
            CREATE OR REPLACE TABLE vehicles AS
            SELECT *,
                   concat(
                       chr(65 + (hash(vehicle_id) % 26)::INT),
                       chr(65 + (hash(vehicle_id + 1) % 26)::INT),
                       lpad(((hash(vehicle_id + 2) % 99) + 1)::VARCHAR, 2, '0'),
                       ' ',
                       chr(65 + (hash(vehicle_id + 3) % 26)::INT),
                       chr(65 + (hash(vehicle_id + 4) % 26)::INT),
                       chr(65 + (hash(vehicle_id + 5) % 26)::INT)
                   ) AS license_plate
            FROM vehicles
            """
        )
        total, owners, plates = conn.execute(
            "SELECT count(*), count(DISTINCT household_id), count(DISTINCT license_plate) "
            "FROM vehicles"
        ).fetchone()

    context.log.info(
        "Generated %s vehicles for %s car-owning households", f"{total:,}", f"{owners:,}"
    )
    context.add_output_metadata(
        {
            "vehicles": total,
            "car_owning_households": owners,
            "distinct_license_plates": plates,
            "capacity_each": planning.vehicle_capacity,
        }
    )


@asset(deps=[admin_areas], group_name="vehicles")
def road_centrelines(
    context: AssetExecutionContext,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Fetch drivable road centrelines for the LAD as spatial line geometry."""
    with warehouse.connect() as conn:
        min_lon, min_lat, max_lon, max_lat = conn.execute(
            """
            SELECT ST_XMin(geometry), ST_YMin(geometry),
                   ST_XMax(geometry), ST_YMax(geometry)
            FROM admin_areas WHERE level = ?
            """,
            [LEVEL_LAD],
        ).fetchone()

    frame = osm.fetch_drivable_roads(
        min_lat - _BBOX_MARGIN_DEGREES,
        min_lon - _BBOX_MARGIN_DEGREES,
        max_lat + _BBOX_MARGIN_DEGREES,
        max_lon + _BBOX_MARGIN_DEGREES,
    )
    context.log.info("Fetched %s drivable ways from Overpass", f"{len(frame):,}")

    with warehouse.connect() as conn:
        conn.register("roads_df", frame)
        # Stored in British National Grid so that snap distances are in metres.
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE road_centrelines AS
            SELECT way_id, highway, name,
                   ST_Transform(ST_GeomFromText(geometry_wkt),
                                '{WGS84_SRS}', '{BNG_SRS}', always_xy := true) AS geometry
            FROM roads_df
            """
        )
        ways, length_km = conn.execute(
            "SELECT count(*), round(sum(ST_Length(geometry)) / 1000, 1) FROM road_centrelines"
        ).fetchone()

    context.add_output_metadata({"ways": ways, "network_length_km": length_km})


@asset(deps=[vehicles, road_centrelines], group_name="vehicles")
def muster_points(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Park each vehicle on the nearest road and call that point its muster point.

    A muster point is exactly one parked vehicle, with capacity equal to that
    vehicle's seats.

    Snapping has no distance cap. OpenStreetMap has not mapped many residential
    access roads, service roads and driveways, so a home that is far from a mapped
    road is a gap in the map, not a dwelling without road access — dropping those
    vehicles removed a quarter of the fleet for no real-world reason. The snap
    distance is recorded and reported instead.

    Nearest is found by expanding the search radius in steps rather than by one
    unbounded join, so the spatial index stays useful; each vehicle is resolved at
    the smallest radius that reaches a road, which is the true nearest.
<<<<<<< Updated upstream

    The way the snap matched, and how far along it the car sits, are recorded
    alongside the coordinate. Both fall out of the snap for free, and they are
    what lets a downstream microsimulation bind this car to a network link by
    lookup instead of re-deriving the nearest link and possibly disagreeing about
    which road the car is on.
=======
>>>>>>> Stashed changes
    """
    with warehouse.connect() as conn:
        # Two-point segments give tight bounding boxes, so the index prunes; a
        # whole way's box can span the district.
        conn.execute(
            """
            CREATE OR REPLACE TABLE road_segments AS
            SELECT way_id,
                   ST_MakeLine(
                       ST_PointN(geometry, CAST(point.n AS INTEGER)),
                       ST_PointN(geometry, CAST(point.n + 1 AS INTEGER))
                   ) AS geometry
            FROM road_centrelines
            CROSS JOIN LATERAL (
                SELECT unnest(generate_series(1, ST_NPoints(geometry) - 1)) AS n
            ) point
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS road_segment_idx ON road_segments USING RTREE (geometry)"
        )
        conn.execute(
            """
            CREATE OR REPLACE TABLE vehicle_homes AS
            SELECT v.vehicle_id, v.household_id, v.area_id, v.capacity,
                   ST_Point(l.easting, l.northing) AS home
            FROM vehicles v JOIN household_locations l USING (household_id)
            """
        )
        conn.execute(
            """
            CREATE OR REPLACE TABLE muster_points (
                muster_point_id BIGINT, vehicle_id BIGINT, household_id BIGINT,
                area_id VARCHAR, capacity INTEGER, snap_distance_m DOUBLE,
<<<<<<< Updated upstream
                easting DOUBLE, northing DOUBLE, geometry GEOMETRY,
                way_id BIGINT, way_position_m DOUBLE
=======
                easting DOUBLE, northing DOUBLE, geometry GEOMETRY
>>>>>>> Stashed changes
            )
            """
        )

        remaining = conn.execute("SELECT count(*) FROM vehicle_homes").fetchone()[0]
        for radius in planning.parking_search_radii_m:
            if remaining == 0:
                break
            conn.execute(
                f"""
                INSERT INTO muster_points
                WITH pending AS (
                    SELECT h.* FROM vehicle_homes h
                    WHERE NOT EXISTS (
                        SELECT 1 FROM muster_points m WHERE m.vehicle_id = h.vehicle_id
                    )
                ),
                nearest AS (
                    SELECT p.vehicle_id, p.household_id, p.area_id, p.capacity,
<<<<<<< Updated upstream
                           s.way_id,
=======
>>>>>>> Stashed changes
                           ST_Distance(p.home, s.geometry) AS snap_distance_m,
                           ST_ClosestPoint(s.geometry, p.home) AS parking_point,
                           row_number() OVER (
                               PARTITION BY p.vehicle_id
<<<<<<< Updated upstream
                               -- way_id breaks ties so a rerun parks every
                               -- vehicle on the same way. Ties are almost always
                               -- two segments meeting at a shared vertex, where
                               -- the closest point is that vertex either way, so
                               -- this fixes the recorded way without moving the
                               -- car.
                               ORDER BY ST_Distance(p.home, s.geometry), s.way_id
=======
                               ORDER BY ST_Distance(p.home, s.geometry)
>>>>>>> Stashed changes
                           ) AS rank
                    FROM pending p
                    JOIN road_segments s ON ST_DWithin(p.home, s.geometry, {radius})
                )
<<<<<<< Updated upstream
                -- Position is measured along the whole way, not the two-point
                -- segment the snap matched, so it indexes into the same geometry
                -- a simulator builds its network links from.
                SELECT n.vehicle_id, n.vehicle_id, n.household_id, n.area_id,
                       n.capacity, n.snap_distance_m,
                       ST_X(n.parking_point), ST_Y(n.parking_point),
                       ST_Transform(n.parking_point, '{BNG_SRS}', '{WGS84_SRS}',
                                    always_xy := true),
                       n.way_id,
                       ST_LineLocatePoint(r.geometry, n.parking_point)
                         * ST_Length(r.geometry) AS way_position_m
                FROM nearest n
                JOIN road_centrelines r USING (way_id)
                WHERE n.rank = 1
=======
                SELECT vehicle_id, vehicle_id, household_id, area_id, capacity,
                       snap_distance_m,
                       ST_X(parking_point), ST_Y(parking_point),
                       ST_Transform(parking_point, '{BNG_SRS}', '{WGS84_SRS}',
                                    always_xy := true)
                FROM nearest WHERE rank = 1
>>>>>>> Stashed changes
                """
            )
            resolved = conn.execute("SELECT count(*) FROM muster_points").fetchone()[0]
            total = conn.execute("SELECT count(*) FROM vehicle_homes").fetchone()[0]
            remaining = total - resolved
            context.log.info(
                "Search radius %.0f m: %s of %s vehicles parked, %s remaining",
                radius,
                f"{resolved:,}",
                f"{total:,}",
                f"{remaining:,}",
            )

        # Should be empty: the widest radius spans the district. Kept so a vehicle
        # can never vanish silently if it somehow is not.
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE unresolved_parking AS
            SELECT v.vehicle_id, v.household_id, v.area_id,
                   'no drivable road found within {planning.parking_search_radii_m[-1]} m'
                     AS reason
            FROM vehicles v
            WHERE NOT EXISTS (
                SELECT 1 FROM muster_points m WHERE m.vehicle_id = v.vehicle_id
            )
            """
        )
        stats = conn.execute(
            f"""
            SELECT count(*) AS parked,
                   round(median(snap_distance_m), 2) AS median_m,
                   round(quantile_cont(snap_distance_m, 0.95), 2) AS p95_m,
                   round(max(snap_distance_m), 2) AS max_m,
                   count(*) FILTER (
                       WHERE snap_distance_m > {planning.parking_snap_review_distance_m}
                   ) AS beyond_review_threshold,
<<<<<<< Updated upstream
                   count(*) FILTER (WHERE way_id IS NULL) AS without_way_reference,
=======
>>>>>>> Stashed changes
                   (SELECT count(*) FROM unresolved_parking) AS unresolved
            FROM muster_points
            """
        ).fetchdf().iloc[0]

    context.log.info(
        "Parked %s vehicles (median %.2f m, p95 %.2f m, max %.2f m); "
        "%s beyond the %.0f m review threshold; %s unresolved",
        f"{int(stats['parked']):,}",
        stats["median_m"],
        stats["p95_m"],
        stats["max_m"],
        f"{int(stats['beyond_review_threshold']):,}",
        planning.parking_snap_review_distance_m,
        f"{int(stats['unresolved']):,}",
    )
    context.add_output_metadata(
        {
            "muster_points": int(stats["parked"]),
            "median_snap_distance_m": float(stats["median_m"]),
            "p95_snap_distance_m": float(stats["p95_m"]),
            "max_snap_distance_m": float(stats["max_m"]),
            "beyond_review_threshold": int(stats["beyond_review_threshold"]),
            "review_threshold_m": planning.parking_snap_review_distance_m,
<<<<<<< Updated upstream
            "without_way_reference": int(stats["without_way_reference"]),
=======
>>>>>>> Stashed changes
            "unresolved_parking": int(stats["unresolved"]),
        }
    )
