"""Assignment of synthetic households to real dwelling points."""

from dagster import AssetExecutionContext, asset

from london_frontline.assets.home_locations import dwelling_points
from london_frontline.assets.population import synthetic_population
from london_frontline.config import PlanningConfig
from london_frontline.resources import SpatialDuckDBResource


@asset(deps=[synthetic_population, dwelling_points], group_name="home_locations")
def household_locations(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Place one household per UPRN, within the household's own Output Area.

    Households and UPRNs are each shuffled deterministically within their Output
    Area and paired by rank, which gives a uniform random matching without
    replacement. Where an Output Area has fewer UPRNs than households, the
    surplus households are left unplaced and recorded as a shortfall rather than
    doubled up onto an occupied point.
    """
    with warehouse.connect() as conn:
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE household_locations AS
            WITH ranked_households AS (
                SELECT household_id, area_id,
                       row_number() OVER (
                           PARTITION BY area_id
                           ORDER BY hash(household_id + {planning.random_seed})
                       ) AS rank
                FROM households
            ),
            ranked_dwellings AS (
                SELECT uprn, area_id, easting, northing, latitude, longitude, geometry,
                       row_number() OVER (
                           PARTITION BY area_id
                           ORDER BY hash(uprn + {planning.random_seed})
                       ) AS rank
                FROM dwelling_points
            )
            SELECT h.household_id, h.area_id, d.uprn,
                   d.easting, d.northing, d.latitude, d.longitude, d.geometry
            FROM ranked_households h
            JOIN ranked_dwellings d
              ON d.area_id = h.area_id AND d.rank = h.rank
            """
        )
        conn.execute(
            """
            CREATE OR REPLACE TABLE home_location_shortfall AS
            SELECT h.area_id,
                   count(*) AS households,
                   any_value(d.dwelling_points) AS dwelling_points,
                   count(*) - any_value(d.dwelling_points) AS unplaced_households,
                   any_value(d.dwelling_points) * 1.0 / count(*) AS uprn_to_household_ratio
            FROM households h
            JOIN (
                SELECT area_id, count(*) AS dwelling_points
                FROM dwelling_points GROUP BY area_id
            ) d ON d.area_id = h.area_id
            GROUP BY h.area_id
            """
        )
        summary = conn.execute(
            """
            SELECT
                (SELECT count(*) FROM households) AS households,
                (SELECT count(*) FROM household_locations) AS placed,
                (SELECT count(*) FROM home_location_shortfall
                  WHERE unplaced_households > 0) AS areas_with_shortfall,
                (SELECT coalesce(sum(unplaced_households), 0)
                   FROM home_location_shortfall WHERE unplaced_households > 0)
                  AS unplaced_households,
                (SELECT round(min(uprn_to_household_ratio), 2)
                   FROM home_location_shortfall) AS min_ratio,
                (SELECT round(median(uprn_to_household_ratio), 2)
                   FROM home_location_shortfall) AS median_ratio
            """
        ).fetchdf().iloc[0]

        duplicates = conn.execute(
            "SELECT count(*) FROM (SELECT uprn FROM household_locations "
            "GROUP BY uprn HAVING count(*) > 1)"
        ).fetchone()[0]

    if duplicates:
        raise ValueError(f"{duplicates} UPRNs were assigned to more than one household")

    context.log.info(
        "Placed %s of %s households; %s unplaced across %s Output Areas "
        "(median UPRN:household ratio %.2f)",
        f"{int(summary['placed']):,}",
        f"{int(summary['households']):,}",
        f"{int(summary['unplaced_households']):,}",
        f"{int(summary['areas_with_shortfall']):,}",
        float(summary["median_ratio"]),
    )
    context.add_output_metadata(
        {
            "households": int(summary["households"]),
            "households_placed": int(summary["placed"]),
            "unplaced_households": int(summary["unplaced_households"]),
            "areas_with_shortfall": int(summary["areas_with_shortfall"]),
            "min_uprn_to_household_ratio": float(summary["min_ratio"]),
            "median_uprn_to_household_ratio": float(summary["median_ratio"]),
        }
    )
