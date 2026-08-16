"""Schematic trip paths for deck.gl's TripsLayer.

**These are an illustration of the plan, not a simulation of an evacuation.**

The plan contains no time model: every muster-point stop is at one fleet-wide
departure time, collection stops are offset by straight-line distance over an
assumed speed, and the drive to the destination is not modelled at all. Taken
literally the whole evacuation spans 111 seconds. Animating that as-is would
show a district clearing in under two minutes with no queues, which is exactly
the question the downstream traffic microsimulation exists to answer.

So the timings here are nominal and stretched to a plausible duration, and the
paths are straight lines rather than roads. What the animation honestly shows is
*structure*: who walks to whose car, in what order vehicles collect people, and
the shape of the exodus. Every output carries a `schematic` flag saying so.
"""

from dagster import AssetExecutionContext, MetadataValue, asset

from london_frontline.assets.map_layers import map_layers
from london_frontline.config import PlanningConfig
from london_frontline.paths import resolve
from london_frontline.resources import SpatialDuckDBResource

# Walkers all set off together; a vehicle leaves once its last occupant is aboard.
_WALK_START_S = 0

# How many walker trips to export. All 111,449 would render, but the file is the
# problem, not the GPU.
_WALK_TRIP_SAMPLE = 20_000


@asset(deps=[map_layers], group_name="presentation")
def trip_paths(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Write schematic walk and drive paths with nominal timestamps."""
    out_dir = resolve(planning.data_dir) / "exports" / "map"
    out_dir.mkdir(parents=True, exist_ok=True)

    walk_speed_ms = planning.walking_speed_kph * 1000.0 / 3600.0
    drive_speed_ms = planning.average_driving_speed_kph * 1000.0 / 3600.0

    with warehouse.connect() as conn:
        # --- Walk phase: home to the car you were assigned -------------------
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE trips_walks AS
            SELECT household_id, muster_point_id, walk_m, own_car,
                   [[source_lon, source_lat], [target_lon, target_lat]] AS path,
                   [{_WALK_START_S}, CAST(round(walk_m / {walk_speed_ms}) AS INTEGER)]
                     AS timestamps,
                   true AS schematic
            FROM arc_walks
            USING SAMPLE {_WALK_TRIP_SAMPLE} ROWS
            """
        )

        # --- Drive phase: muster point, collection stops, then destination ----
        # Stop times come from the plan; the destination leg is appended here
        # because the plan never modelled it.
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE trips_vehicles AS
            WITH stops AS (
                SELECT s.muster_point_id, s.stop_seq,
                       ST_X(ST_Transform(ST_Point(s.easting, s.northing),
                            'EPSG:27700', 'EPSG:4326', always_xy := true)) AS lon,
                       ST_Y(ST_Transform(ST_Point(s.easting, s.northing),
                            'EPSG:27700', 'EPSG:4326', always_xy := true)) AS lat,
                       CAST(date_diff('second',
                            TIMESTAMP '{planning.fleet_departure_time}',
                            s.meeting_time) AS INTEGER) AS t,
                       s.easting, s.northing
                FROM route_stops s
            ),
            journey AS (
                SELECT muster_point_id,
                       list(ARRAY[lon, lat] ORDER BY stop_seq) AS stop_path,
                       list(t ORDER BY stop_seq) AS stop_times,
                       max(t) AS last_t,
                       arg_max(easting, stop_seq) AS last_easting,
                       arg_max(northing, stop_seq) AS last_northing
                FROM stops GROUP BY muster_point_id
            ),
            destination AS (
                SELECT ST_X(ST_Transform(ST_Point({planning.destination_longitude},
                                                  {planning.destination_latitude}),
                            'EPSG:4326', 'EPSG:27700', always_xy := true)) AS easting,
                       ST_Y(ST_Transform(ST_Point({planning.destination_longitude},
                                                  {planning.destination_latitude}),
                            'EPSG:4326', 'EPSG:27700', always_xy := true)) AS northing
            )
            SELECT j.muster_point_id, a.occupants, a.capacity,
                   r.license_plate, r.collection_stops,
                   list_append(j.stop_path,
                       ARRAY[{planning.destination_longitude}::DOUBLE,
                             {planning.destination_latitude}::DOUBLE]) AS path,
                   list_append(j.stop_times,
                       j.last_t + CAST(round(
                           ST_Distance(ST_Point(j.last_easting, j.last_northing),
                                       ST_Point(d.easting, d.northing))
                           / {drive_speed_ms}) AS INTEGER)) AS timestamps,
                   true AS schematic
            FROM journey j
            CROSS JOIN destination d
            JOIN activated_vehicles a USING (muster_point_id)
            JOIN vehicle_routes r USING (muster_point_id)
            """
        )

        written = {}
        for name in ("trips_walks", "trips_vehicles"):
            path = out_dir / f"{name}.json"
            conn.execute(
                f"COPY (SELECT * FROM {name}) TO '{path}' (FORMAT JSON, ARRAY true)"
            )
            rows = conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            written[name] = (rows, path.stat().st_size)

        span = conn.execute(
            "SELECT max(list_max(timestamps)) FROM trips_vehicles"
        ).fetchone()[0]
        walkers_total = conn.execute("SELECT count(*) FROM arc_walks").fetchone()[0]

    context.log.info(
        "Schematic trips: %s walk paths (of %s), %s vehicle paths; "
        "animation spans %d seconds (%.0f minutes) of NOMINAL time",
        f"{written['trips_walks'][0]:,}",
        f"{walkers_total:,}",
        f"{written['trips_vehicles'][0]:,}",
        span,
        span / 60,
    )
    context.add_output_metadata(
        {
            "walk_paths": written["trips_walks"][0],
            "walk_paths_available": walkers_total,
            "vehicle_paths": written["trips_vehicles"][0],
            "animation_span_seconds": int(span),
            "total_mb": round(sum(s for _, s in written.values()) / 1e6, 2),
            "health_warning": MetadataValue.md(
                "**Schematic.** Paths are straight lines, not roads. Timings are "
                "nominal: the plan models no congestion, no queuing, and never "
                "computed a drive time to the destination — that leg is appended "
                "here at a flat assumed speed. This animates the *structure* of the "
                "plan, not an evacuation. Every row carries `schematic: true`. Label "
                "it as an illustration wherever it is shown, and do not read "
                "clearance times off it."
            ),
            "usage": MetadataValue.md(
                "```js\n"
                "new TripsLayer({\n"
                "  id: 'vehicles',\n"
                "  data: 'trips_vehicles.json',\n"
                "  getPath: d => d.path,\n"
                "  getTimestamps: d => d.timestamps,\n"
                "  getColor: d => d.collection_stops > 0 ? [240,140,40] : [60,120,220],\n"
                "  getWidth: d => Math.sqrt(d.occupants) * 2,\n"
                "  trailLength: 180,\n"
                "  currentTime: time,  // animate 0 -> animation_span_seconds\n"
                "})\n"
                "```\n\n"
                "Run the walk layer first (it finishes in the first minute or so), "
                "then the vehicle layer, for a two-phase gather-then-leave sequence."
            ),
        }
    )
