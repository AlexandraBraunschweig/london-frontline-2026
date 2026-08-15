"""Census 2021 marginal ingestion, with the LSOA suppression fallback.

ONS applies disclosure control to Census 2021 Output Area tables (cell-key
perturbation and record swapping rather than classic suppression), so an OA's
categories can fail to sum to its published total. Where that happens the OA's
marginal is not trustworthy on its own, and the pipeline falls back to that OA's
LSOA marginal as a disaggregation prior, recording which OAs needed it.
"""

import pandas as pd
from dagster import AssetExecutionContext, asset

from london_frontline.assets.geography import admin_areas
from london_frontline.config import PlanningConfig
from london_frontline.resources import SpatialDuckDBResource
from london_frontline.sources import nomis

# An OA whose categories miss the published total by more than this is treated
# as perturbed. ONS perturbation moves counts by small amounts, so anything
# larger indicates the marginal should not be trusted at OA level.
_PERTURBATION_TOLERANCE = 2


@asset(deps=[admin_areas], group_name="census")
def census_marginals(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Pull every Census marginal the synthesis needs, at Output Area level."""
    frames = []
    for table in nomis.CENSUS_TABLES.values():
        frame = nomis.fetch_marginal(table, planning.lad_code, nomis.OA_TYPE)
        context.log.info(
            "%s (%s): %d rows across %d areas",
            table.census_id,
            table.description,
            len(frame),
            frame["area_code"].nunique(),
        )
        frames.append(frame)

    combined = pd.concat(frames, ignore_index=True)
    with warehouse.connect() as conn:
        conn.register("marginals_df", combined)
        conn.execute(
            "CREATE OR REPLACE TABLE census_marginals AS SELECT * FROM marginals_df"
        )

    context.add_output_metadata(
        {
            "tables": len(nomis.CENSUS_TABLES),
            "rows": len(combined),
            "output_areas": int(combined["area_code"].nunique()),
        }
    )


@asset(deps=[census_marginals], group_name="census")
def census_marginals_resolved(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Detect perturbed OA marginals and substitute an LSOA-derived fallback.

    Produces ``census_marginals_resolved`` — the marginal each Output Area is
    actually synthesised from — and ``marginal_fallbacks``, the record of which
    OA/table pairs needed the LSOA prior.
    """
    with warehouse.connect() as conn:
        # An OA/table is suspect if its non-total categories do not sum to the
        # published total, or if ONS flagged any cell as other than a normal value.
        conn.execute(
            """
            CREATE OR REPLACE TABLE marginal_fallbacks AS
            WITH totals AS (
                SELECT area_code, census_table, value AS published_total
                FROM census_marginals WHERE is_total
            ),
            summed AS (
                -- Leaves only: intermediate aggregates would be counted twice.
                SELECT area_code, census_table,
                       sum(value) AS category_sum,
                       count(*) FILTER (WHERE obs_status <> 'A') AS abnormal_cells
                FROM census_marginals WHERE is_leaf
                GROUP BY area_code, census_table
            )
            SELECT s.area_code, s.census_table, t.published_total, s.category_sum,
                   s.abnormal_cells,
                   abs(s.category_sum - t.published_total) AS total_gap
            FROM summed s JOIN totals t
              ON t.area_code = s.area_code AND t.census_table = s.census_table
            WHERE abs(s.category_sum - t.published_total) > ?
               OR s.abnormal_cells > 0
            """,
            [_PERTURBATION_TOLERANCE],
        )

        fallback_pairs = conn.execute(
            "SELECT count(*), count(DISTINCT area_code) FROM marginal_fallbacks"
        ).fetchone()

        # Fetch LSOA marginals only for the tables that actually need them.
        tables_needing_fallback = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT census_table FROM marginal_fallbacks"
            ).fetchall()
        ]

    lsoa_frames = []
    for table in nomis.CENSUS_TABLES.values():
        if table.census_id not in tables_needing_fallback:
            continue
        lsoa_frames.append(
            nomis.fetch_marginal(table, planning.lad_code, nomis.LSOA_TYPE)
        )

    with warehouse.connect() as conn:
        if lsoa_frames:
            conn.register("lsoa_df", pd.concat(lsoa_frames, ignore_index=True))
            conn.execute(
                "CREATE OR REPLACE TABLE census_marginals_lsoa AS SELECT * FROM lsoa_df"
            )
        else:
            conn.execute(
                """
                CREATE OR REPLACE TABLE census_marginals_lsoa AS
                SELECT * FROM census_marginals WHERE false
                """
            )

        # Where an OA/table was flagged, rescale its LSOA marginal down to the
        # OA's share of the LSOA total, so the fallback keeps the OA's own scale
        # while borrowing the LSOA's shape.
        conn.execute(
            """
            CREATE OR REPLACE TABLE census_marginals_resolved AS
            WITH flagged AS (SELECT area_code, census_table FROM marginal_fallbacks),
            oa_share AS (
                SELECT l.oa_code, l.lsoa_code, f.census_table,
                       t.published_total,
                       nullif(lt.value, 0) AS lsoa_total
                FROM marginal_fallbacks f
                JOIN oa_lsoa_lookup l ON l.oa_code = f.area_code
                JOIN (SELECT area_code, census_table, value AS published_total
                      FROM census_marginals WHERE is_total) t
                  ON t.area_code = f.area_code AND t.census_table = f.census_table
                JOIN (SELECT area_code, census_table, value FROM census_marginals_lsoa
                      WHERE is_total) lt
                  ON lt.area_code = l.lsoa_code AND lt.census_table = f.census_table
            )
            SELECT m.area_code, m.census_table, m.category_code, m.category_name,
                   m.category_order, m.value, m.is_total, m.is_leaf,
                   false AS used_lsoa_fallback
            FROM census_marginals m
            WHERE NOT EXISTS (
                SELECT 1 FROM flagged f
                WHERE f.area_code = m.area_code AND f.census_table = m.census_table
            )
            UNION ALL
            SELECT s.oa_code AS area_code, lm.census_table, lm.category_code,
                   lm.category_name, lm.category_order,
                   round(lm.value * s.published_total / s.lsoa_total) AS value,
                   lm.is_total, lm.is_leaf,
                   true AS used_lsoa_fallback
            FROM oa_share s
            JOIN census_marginals_lsoa lm
              ON lm.area_code = s.lsoa_code AND lm.census_table = s.census_table
            """
        )
        resolved_rows, fallback_rows = conn.execute(
            """
            SELECT count(*), count(*) FILTER (WHERE used_lsoa_fallback)
            FROM census_marginals_resolved
            """
        ).fetchone()

    context.log.info(
        "%d OA/table marginals fell back to LSOA (%d distinct Output Areas)",
        fallback_pairs[0],
        fallback_pairs[1],
    )
    context.add_output_metadata(
        {
            "resolved_rows": resolved_rows,
            "rows_from_lsoa_fallback": fallback_rows,
            "oa_table_pairs_flagged": fallback_pairs[0],
            "output_areas_flagged": fallback_pairs[1],
        }
    )
