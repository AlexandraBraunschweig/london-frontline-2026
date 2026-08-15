"""Geography ingestion: Output Areas for the configured LAD, as AdminArea rows.

Geography is stored in the portable ``admin_areas`` shape — ``{area_id, level,
parent_id, geometry}`` — rather than as UK-specific OA/LSOA/MSOA columns, so
re-seeding from another country's census hierarchy needs no schema change.
"""

from dagster import AssetExecutionContext, MetadataValue, asset

from london_frontline.config import PlanningConfig
from london_frontline.paths import resolve
from london_frontline.resources import SpatialDuckDBResource
from london_frontline.sources import ons_geography

# Level names are generic on purpose; only the loader knows they came from the
# UK hierarchy. Ordered coarsest-last so parents exist before children.
LEVEL_LAD = "lad"
LEVEL_MSOA = "msoa"
LEVEL_LSOA = "lsoa"
LEVEL_OUTPUT_AREA = "output_area"


@asset(group_name="geography")
def admin_areas(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Resolve every Output Area in the configured LAD and its parent hierarchy.

    Writes ``admin_areas`` (one row per area at every level, with parent links and
    OA geometry) and ``oa_lsoa_lookup`` (the OA to LSOA mapping the suppression
    fallback joins on).
    """
    lookup = ons_geography.fetch_oa_lookup(planning.lad_code)
    context.log.info(
        "Resolved %d Output Areas in %d LSOAs for LAD %s",
        len(lookup),
        lookup["LSOA21CD"].nunique(),
        planning.lad_code,
    )

    boundaries = ons_geography.fetch_oa_boundaries(lookup["OA21CD"].tolist())
    missing = set(lookup["OA21CD"]) - set(boundaries["oa_code"])
    if missing:
        context.log.warning(
            "%d Output Areas have no boundary geometry: %s",
            len(missing),
            sorted(missing)[:5],
        )

    with warehouse.connect() as conn:
        conn.register("lookup_df", lookup)
        conn.register("boundaries_df", boundaries)

        # One row per area at each level. Geometry is attached at Output Area
        # level and unioned upward, so every level is mappable without holding
        # four separate boundary downloads.
        conn.execute(
            """
            CREATE OR REPLACE TABLE oa_lsoa_lookup AS
            SELECT OA21CD AS oa_code, LSOA21CD AS lsoa_code, MSOA21CD AS msoa_code,
                   LAD22CD AS lad_code
            FROM lookup_df
            """
        )
        conn.execute(
            """
            CREATE OR REPLACE TABLE admin_areas AS
            WITH oa AS (
                SELECT l.OA21CD AS area_id,
                       ? AS level,
                       l.LSOA21CD AS parent_id,
                       ST_GeomFromGeoJSON(b.geometry_geojson) AS geometry
                FROM lookup_df l
                LEFT JOIN boundaries_df b ON b.oa_code = l.OA21CD
            ),
            lsoa AS (
                SELECT DISTINCT l.LSOA21CD AS area_id, ? AS level,
                       l.MSOA21CD AS parent_id,
                       ST_Union_Agg(o.geometry) AS geometry
                FROM lookup_df l
                JOIN oa o ON o.area_id = l.OA21CD
                GROUP BY l.LSOA21CD, l.MSOA21CD
            ),
            msoa AS (
                SELECT l.MSOA21CD AS area_id, ? AS level, l.LAD22CD AS parent_id,
                       ST_Union_Agg(o.geometry) AS geometry
                FROM lookup_df l
                JOIN oa o ON o.area_id = l.OA21CD
                GROUP BY l.MSOA21CD, l.LAD22CD
            ),
            lad AS (
                SELECT l.LAD22CD AS area_id, ? AS level, NULL AS parent_id,
                       ST_Union_Agg(o.geometry) AS geometry
                FROM lookup_df l
                JOIN oa o ON o.area_id = l.OA21CD
                GROUP BY l.LAD22CD
            )
            SELECT * FROM oa
            UNION ALL SELECT * FROM lsoa
            UNION ALL SELECT * FROM msoa
            UNION ALL SELECT * FROM lad
            """,
            [LEVEL_OUTPUT_AREA, LEVEL_LSOA, LEVEL_MSOA, LEVEL_LAD],
        )
        counts = dict(
            conn.execute(
                "SELECT level, count(*) FROM admin_areas GROUP BY level"
            ).fetchall()
        )

    context.add_output_metadata(
        {
            "lad_code": planning.lad_code,
            "output_areas": counts.get(LEVEL_OUTPUT_AREA, 0),
            "lsoas": counts.get(LEVEL_LSOA, 0),
            "msoas": counts.get(LEVEL_MSOA, 0),
            "boundaries_missing": len(missing),
        }
    )


@asset(deps=[admin_areas], group_name="geography")
def admin_areas_geojson(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Export Output Area boundaries to GeoJSON for inspection and mapping."""
    out_dir = resolve(planning.data_dir) / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"output_areas_{planning.lad_code}.geojson"

    with warehouse.connect() as conn:
        # DuckDB's COPY target and format options are parsed, not bound, so the
        # path cannot be a query parameter. Both interpolated values are derived
        # from config rather than from ingested data.
        conn.execute(
            f"""
            COPY (
                SELECT area_id, level, parent_id, geometry
                FROM admin_areas
                WHERE level = '{LEVEL_OUTPUT_AREA}' AND geometry IS NOT NULL
            ) TO '{path}' WITH (FORMAT GDAL, DRIVER 'GeoJSON')
            """
        )

    context.add_output_metadata(
        {"path": MetadataValue.path(str(path)), "size_bytes": path.stat().st_size}
    )
