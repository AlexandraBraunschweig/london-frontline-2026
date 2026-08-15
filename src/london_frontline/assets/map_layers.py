"""Denormalised layers for putting a finished plan on a map.

Only three planning tables carry geometry, while every figure worth showing —
cars activated, occupancy, unmet demand, walking distance — lives in tables with
no location at all. These layers do those joins once, so a map tool can style
them directly.
"""

from dagster import AssetExecutionContext, MetadataValue, asset

from london_frontline.assets.reporting import report_seat_utilisation
from london_frontline.assets.walking_report import report_walking_by_demographic
from london_frontline.config import PlanningConfig
from london_frontline.paths import resolve
from london_frontline.resources import SpatialDuckDBResource

# Above this, a browser will struggle to load the layer whole, so it is written
# as FlatGeobuf (streamable, readable by loaders.gl and QGIS) instead of GeoJSON.
_GEOJSON_FEATURE_LIMIT = 20_000

# Walk lines are too dense to read as a picture at full size.
_WALK_LINE_SAMPLE = 3_000


@asset(
    deps=[report_seat_utilisation, report_walking_by_demographic],
    group_name="presentation",
)
def map_layers(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Build and export the five presentation layers."""
    out_dir = resolve(planning.data_dir) / "exports" / "map"
    out_dir.mkdir(parents=True, exist_ok=True)

    with warehouse.connect() as conn:
        # --- Output Area summary: the workhorse choropleth layer -------------
        conn.execute(
            """
            CREATE OR REPLACE TABLE layer_oa_summary AS
            WITH pop AS (
                SELECT area_id, count(*) AS people FROM persons GROUP BY 1
            ),
            hh AS (
                SELECT area_id, count(*) AS households, CAST(sum(num_cars) AS BIGINT) AS cars_owned,
                       count(*) FILTER (WHERE num_cars > 0) AS car_owning_households
                FROM households GROUP BY 1
            ),
            activated AS (
                SELECT m.area_id, count(*) AS cars_activated,
                       CAST(sum(a.occupants) AS BIGINT) AS people_carried,
                       CAST(round(avg(a.occupants), 2) AS DOUBLE) AS mean_occupancy
                FROM activated_vehicles a
                JOIN muster_points m USING (muster_point_id)
                GROUP BY 1
            ),
            seated AS (
                SELECT p.area_id, count(*) AS people_seated
                FROM person_seats s JOIN persons p USING (person_id) GROUP BY 1
            ),
            unseated AS (
                SELECT area_id, CAST(sum(size) AS BIGINT) AS people_unmet FROM unmet_demand GROUP BY 1
            ),
            walks AS (
                SELECT p.area_id, CAST(round(median(w.straight_line_m), 1) AS DOUBLE) AS median_walk_m
                FROM person_walks w JOIN persons p USING (person_id)
                WHERE w.all_can_walk AND w.straight_line_m IS NOT NULL
                GROUP BY 1
            )
            SELECT a.area_id,
                   coalesce(pop.people, 0) AS people,
                   coalesce(hh.households, 0) AS households,
                   CAST(coalesce(hh.cars_owned, 0) AS BIGINT) AS cars_owned,
                   coalesce(hh.car_owning_households, 0) AS baseline_cars,
                   coalesce(act.cars_activated, 0) AS cars_activated,
                   coalesce(hh.car_owning_households, 0)
                     - coalesce(act.cars_activated, 0) AS cars_saved,
                   coalesce(seated.people_seated, 0) AS people_seated,
                   CAST(coalesce(unseated.people_unmet, 0) AS BIGINT) AS people_unmet,
                   CAST(round(100.0 * coalesce(unseated.people_unmet, 0)
                              / nullif(pop.people, 0), 2) AS DOUBLE) AS unmet_pct,
                   act.mean_occupancy,
                   walks.median_walk_m,
                   a.geometry
            FROM admin_areas a
            LEFT JOIN pop ON pop.area_id = a.area_id
            LEFT JOIN hh ON hh.area_id = a.area_id
            LEFT JOIN activated act ON act.area_id = a.area_id
            LEFT JOIN seated ON seated.area_id = a.area_id
            LEFT JOIN unseated ON unseated.area_id = a.area_id
            LEFT JOIN walks ON walks.area_id = a.area_id
            WHERE a.level = 'output_area'
            """
        )

        # --- Fleet: every car, departing or parked ---------------------------
        conn.execute(
            """
            CREATE OR REPLACE TABLE layer_fleet AS
            SELECT m.muster_point_id, m.vehicle_id, m.area_id, m.capacity,
                   m.snap_distance_m,
                   a.muster_point_id IS NOT NULL AS activated,
                   coalesce(a.occupants, 0) AS occupants,
                   coalesce(a.empty_seats, m.capacity) AS empty_seats,
                   m.geometry
            FROM muster_points m
            LEFT JOIN activated_vehicles a USING (muster_point_id)
            """
        )

        # --- Walk lines: home to the car you were assigned -------------------
        conn.execute(
            """
            CREATE OR REPLACE TABLE layer_walk_lines AS
            SELECT DISTINCT ON (w.household_id, w.muster_point_id)
                   w.household_id, w.muster_point_id,
                   CAST(round(w.straight_line_m, 1) AS DOUBLE) AS walk_m,
                   w.own_household_vehicle,
                   ST_Transform(
                       ST_MakeLine(ST_Point(l.easting, l.northing),
                                   ST_Point(m.easting, m.northing)),
                       'EPSG:27700', 'EPSG:4326', always_xy := true
                   ) AS geometry
            FROM person_walks w
            JOIN household_locations l ON l.household_id = w.household_id
            JOIN muster_points m ON m.muster_point_id = w.muster_point_id
            WHERE w.all_can_walk AND w.straight_line_m IS NOT NULL
            """
        )

        # --- Collection routes: the vehicles that detour ---------------------
        conn.execute(
            """
            CREATE OR REPLACE TABLE layer_collection_routes AS
            SELECT s.muster_point_id, s.stop_seq, s.stop_type, s.household_id,
                   s.uprn, s.meeting_time,
                   CAST(round(s.distance_from_muster_m, 1) AS DOUBLE) AS distance_from_muster_m,
                   r.license_plate, r.leader_name, r.collection_stops,
                   ST_Transform(ST_Point(s.easting, s.northing),
                                'EPSG:27700', 'EPSG:4326', always_xy := true) AS geometry
            FROM route_stops s
            JOIN vehicle_routes r USING (muster_point_id)
            WHERE r.collection_stops > 0
            """
        )

        # --- Unmet demand: failure has a geography ---------------------------
        conn.execute(
            """
            CREATE OR REPLACE TABLE layer_unmet AS
            SELECT u.travel_group_id, u.area_id, u.size AS people, u.collection_mode,
                   u.reason, l.uprn, l.geometry
            FROM unmet_demand u
            JOIN travel_groups g USING (travel_group_id)
            JOIN household_locations l ON l.household_id = g.household_id
            """
        )

        # --- Arc data: deck.gl's ArcLayer takes source/target coordinates as
        # attributes, not LineString geometry, so these are flat tables rather
        # than spatial layers. They are a fraction of the size and load directly.
        conn.execute(
            """
            CREATE OR REPLACE TABLE arc_walks AS
            SELECT DISTINCT ON (w.household_id, w.muster_point_id)
                   w.household_id, w.muster_point_id,
                   CAST(round(w.straight_line_m, 1) AS DOUBLE) AS walk_m,
                   w.own_household_vehicle AS own_car,
                   CAST(l.longitude AS DOUBLE) AS source_lon,
                   CAST(l.latitude AS DOUBLE) AS source_lat,
                   CAST(ST_X(m.geometry) AS DOUBLE) AS target_lon,
                   CAST(ST_Y(m.geometry) AS DOUBLE) AS target_lat
            FROM person_walks w
            JOIN household_locations l ON l.household_id = w.household_id
            JOIN muster_points m ON m.muster_point_id = w.muster_point_id
            WHERE w.all_can_walk AND w.straight_line_m IS NOT NULL
            """
        )
        # Aggregated to Output Area pairs. Individual walks are 41 m at the
        # median, which is sub-pixel at district zoom; area-to-area flows span
        # far enough to actually read as arcs.
        conn.execute(
            """
            CREATE OR REPLACE TABLE arc_flows_oa AS
            WITH centroids AS (
                SELECT area_id, ST_Centroid(geometry) AS point
                FROM admin_areas WHERE level = 'output_area'
            ),
            flows AS (
                SELECT p.area_id AS source_area, m.area_id AS target_area,
                       count(*) AS people,
                       CAST(round(avg(w.straight_line_m), 1) AS DOUBLE) AS mean_walk_m
                FROM person_walks w
                JOIN persons p USING (person_id)
                JOIN muster_points m ON m.muster_point_id = w.muster_point_id
                WHERE w.all_can_walk AND p.area_id <> m.area_id
                GROUP BY 1, 2
            )
            SELECT f.source_area, f.target_area, f.people, f.mean_walk_m,
                   CAST(ST_X(s.point) AS DOUBLE) AS source_lon,
                   CAST(ST_Y(s.point) AS DOUBLE) AS source_lat,
                   CAST(ST_X(t.point) AS DOUBLE) AS target_lon,
                   CAST(ST_Y(t.point) AS DOUBLE) AS target_lat
            FROM flows f
            JOIN centroids s ON s.area_id = f.source_area
            JOIN centroids t ON t.area_id = f.target_area
            ORDER BY f.people DESC
            """
        )

        arc_written = {}
        for name, fmt in (
            ("arc_flows_oa", "json"),
            ("arc_walks", "json"),
            ("arc_walks", "parquet"),
        ):
            path = out_dir / f"{name}.{fmt}"
            if fmt == "json":
                conn.execute(f"COPY (SELECT * FROM {name}) TO '{path}' (FORMAT JSON, ARRAY true)")
            else:
                conn.execute(f"COPY (SELECT * FROM {name}) TO '{path}' (FORMAT PARQUET)")
            rows = conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            arc_written[path.name] = (rows, path.stat().st_size)

        # A sample, because a hundred thousand overlapping lines is not a picture.
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE layer_walk_lines_sample AS
            SELECT * FROM layer_walk_lines USING SAMPLE {_WALK_LINE_SAMPLE} ROWS
            """
        )

        layers = [
            "oa_summary",
            "fleet",
            "walk_lines",
            "walk_lines_sample",
            "collection_routes",
            "unmet",
        ]
        written = {}
        for layer in layers:
            count = conn.execute(f"SELECT count(*) FROM layer_{layer}").fetchone()[0]
            # Small layers go out as GeoJSON so they can be dropped straight into
            # a map; large ones as FlatGeobuf, which streams.
            if count <= _GEOJSON_FEATURE_LIMIT:
                suffix, driver = "geojson", "GeoJSON"
            else:
                suffix, driver = "fgb", "FlatGeobuf"
            path = out_dir / f"{layer}.{suffix}"
            conn.execute(
                f"COPY (SELECT * FROM layer_{layer}) TO '{path}' "
                f"WITH (FORMAT GDAL, DRIVER '{driver}')"
            )
            written[layer] = (count, path, path.stat().st_size)

    summary = "\n".join(
        f"  {name:22} {count:>8,} features  {size / 1e6:6.2f} MB  {path.name}"
        for name, (count, path, size) in written.items()
    )
    arc_summary = "\n".join(
        f"  {name:26} {rows:>8,} rows      {size / 1e6:6.2f} MB"
        for name, (rows, size) in arc_written.items()
    )
    context.log.info(
        "Map layers written to %s:\n%s\n  --- deck.gl ArcLayer data ---\n%s",
        out_dir,
        summary,
        arc_summary,
    )
    context.add_output_metadata(
        {
            "path": MetadataValue.path(str(out_dir)),
            "layers": len(written),
            "total_mb": round(sum(s for _, _, s in written.values()) / 1e6, 2),
            **{
                f"{name}_features": count for name, (count, _, _) in written.items()
            },
            "arc_layers": MetadataValue.md(
                "`arc_flows_oa.json` (Output Area to Output Area, aggregated) and "
                "`arc_walks.json` / `.parquet` (per household) feed deck.gl's "
                "`ArcLayer` directly:\n\n"
                "```js\n"
                "new ArcLayer({\n"
                "  id: 'flows',\n"
                "  data: 'arc_flows_oa.json',\n"
                "  getSourcePosition: d => [d.source_lon, d.source_lat],\n"
                "  getTargetPosition: d => [d.target_lon, d.target_lat],\n"
                "  getWidth: d => Math.sqrt(d.people),\n"
                "  getSourceColor: [60, 120, 220], getTargetColor: [240, 140, 40],\n"
                "  getHeight: 0.4,\n"
                "})\n"
                "```\n\n"
                "**Set `getHeight` low.** The default of 1 draws a loop as tall as the "
                "arc is long; individual walks are 41 m at the median, so anything "
                "near the default renders as absurd vertical hoops. Use ~0.3-0.5 for "
                "the aggregated flows and ~0.05 for per-household walks, which only "
                "read when zoomed to street level."
            ),
            "pmtiles": MetadataValue.md(
                "PMTiles are not produced here. DuckDB's GDAL exposes only *layer* "
                "creation options, while the PMTiles writer takes `MINZOOM`/`MAXZOOM` "
                "as *dataset* options, so it emits a valid but empty archive. To tile "
                "these layers:\n\n"
                "```bash\n"
                "brew install tippecanoe\n"
                "tippecanoe -o plan.pmtiles -Z8 -z14 --drop-densest-as-needed \\\n"
                "  -L oa:oa_summary.geojson -L unmet:unmet.geojson \\\n"
                "  -L walks:walk_lines_sample.geojson\n"
                "```\n\n"
                "Only the full walk-line layer really needs tiling; the rest load "
                "whole."
            ),
        }
    )
