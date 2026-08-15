"""Dwelling points from NSUL, and the assignment of households to them."""

from dagster import AssetExecutionContext, asset

from london_frontline.assets.geography import LEVEL_OUTPUT_AREA, admin_areas
from london_frontline.config import PlanningConfig
from london_frontline.resources import SpatialDuckDBResource
from london_frontline.sources import nsul

# NSUL publishes coordinates in British National Grid; downstream network work
# needs WGS84.
BNG_SRS = "EPSG:27700"
WGS84_SRS = "EPSG:4326"


@asset(deps=[admin_areas], group_name="home_locations")
def dwelling_points(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Candidate dwelling points for the LAD: every NSUL UPRN in its Output Areas.

    Only the NSUL region member covering the LAD is fetched, and rows are filtered
    to the LAD's Output Areas while decoding, so neither the whole-GB archive nor
    the uncompressed region file is ever materialised.
    """
    item_id, title = nsul.find_latest_edition()
    edition = nsul.locate_region_member(item_id, title, planning.nsul_region_code)
    context.log.info(
        "NSUL %s, member %s (%.0f MB compressed)",
        edition.epoch,
        edition.member_name,
        edition.compressed_size / 1e6,
    )

    with warehouse.connect() as conn:
        area_codes = [
            row[0]
            for row in conn.execute(
                "SELECT area_id FROM admin_areas WHERE level = ?",
                [LEVEL_OUTPUT_AREA],
            ).fetchall()
        ]

    frame = nsul.fetch_uprns_for_areas(edition, area_codes)
    context.log.info(
        "Scanned %s NSUL rows, kept %s in %d Output Areas",
        f"{frame.attrs['rows_scanned']:,}",
        f"{len(frame):,}",
        frame["area_id"].nunique(),
    )

    with warehouse.connect() as conn:
        conn.register("nsul_df", frame)
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE dwelling_points AS
            SELECT uprn, area_id, easting, northing,
                   ST_X(wgs) AS longitude, ST_Y(wgs) AS latitude,
                   wgs AS geometry
            FROM (
                SELECT uprn, area_id, easting, northing,
                       ST_Transform(ST_Point(easting, northing),
                                    '{BNG_SRS}', '{WGS84_SRS}', always_xy := true) AS wgs
                FROM nsul_df
            )
            """
        )
        total, areas, bad = conn.execute(
            """
            SELECT count(*), count(DISTINCT area_id),
                   count(*) FILTER (
                       WHERE latitude NOT BETWEEN 49 AND 61
                          OR longitude NOT BETWEEN -9 AND 2
                   )
            FROM dwelling_points
            """
        ).fetchone()

    if bad:
        raise ValueError(
            f"{bad} dwelling points fell outside Great Britain after coordinate "
            f"transform; check the BNG to WGS84 axis order"
        )

    context.add_output_metadata(
        {
            "nsul_epoch": edition.epoch,
            "nsul_member": edition.member_name,
            "rows_scanned": frame.attrs["rows_scanned"],
            "dwelling_points": total,
            "output_areas_covered": areas,
        }
    )
