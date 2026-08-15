"""Who walks how far, once muster points are assigned.

The walking ceiling is a single number applied to everyone, so the question this
answers is who it actually falls on: whether the burden of reaching a car lands
disproportionately on the old, the young, or people without a licence of their
own.
"""

from dagster import AssetExecutionContext, MetadataValue, asset

from london_frontline.assets.assignment import seat_assignments
from london_frontline.config import PlanningConfig
from london_frontline.resources import SpatialDuckDBResource

# Bands chosen to separate the groups the model treats differently: dependants
# under 10 and 10-15, people below driving age, working age, and the ages where
# mobility and isolation start to matter.
_AGE_BANDS = """
    CASE
        WHEN p.age < 10 THEN '00-09'
        WHEN p.age < 16 THEN '10-15'
        WHEN p.age < 25 THEN '16-24'
        WHEN p.age < 45 THEN '25-44'
        WHEN p.age < 65 THEN '45-64'
        WHEN p.age < 80 THEN '65-79'
        ELSE '80+'
    END
"""


@asset(deps=[seat_assignments], group_name="reporting")
def report_walking_by_demographic(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Walking distance and time to the assigned muster point, by demographic.

    Covers only people who walk. Home-collection groups are picked up at their
    own address and are reported separately as a count, not folded into the
    distance distribution where they would understate it.
    """
    ceiling_m = (
        planning.walking_ceiling_minutes / 60.0 * planning.walking_speed_kph * 1000.0
    )

    with warehouse.connect() as conn:
        # The walk each seated person actually faces: from their own home to the
        # vehicle they were assigned to.
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE person_walks AS
            SELECT s.person_id, s.muster_point_id, p.household_id, p.age, p.sex,
                   p.license_type, p.mobility_status, p.medical_skill,
                   {_AGE_BANDS} AS age_band,
                   g.all_can_walk,
                   g.contains_dependent,
                   -- A person riding in a vehicle their own household owns walks
                   -- only as far as the kerb outside.
                   (m.household_id = p.household_id) AS own_household_vehicle,
                   -- Two measures, because they say different things.
                   -- Pandana reports network distance between the nodes a home
                   -- and a vehicle snap to; where both snap to the same node
                   -- that is 0, which understates a real walk.
                   w.walk_distance_m AS network_m,
                   -- Straight line from the door to the car: a lower bound on
                   -- the real walk, but door-to-door rather than node-to-node.
                   ST_Distance(ST_Point(l.easting, l.northing),
                               ST_Point(m.easting, m.northing)) AS straight_line_m,
                   w.walk_minutes AS network_minutes
            FROM person_seats s
            JOIN persons p USING (person_id)
            JOIN travel_groups g ON g.travel_group_id = s.travel_group_id
            JOIN muster_points m ON m.muster_point_id = s.muster_point_id
            JOIN household_locations l ON l.household_id = p.household_id
            LEFT JOIN walk_candidates w
              ON w.household_id = p.household_id
             AND w.muster_point_id = s.muster_point_id
            """
        )

        conn.execute(
            """
            CREATE OR REPLACE TABLE report_walking_by_demographic AS
            SELECT age_band,
                   sex,
                   count(*) AS people,
                   round(median(straight_line_m), 1) AS median_m,
                   round(quantile_cont(straight_line_m, 0.9), 1) AS p90_m,
                   round(quantile_cont(straight_line_m, 0.99), 1) AS p99_m,
                   round(max(straight_line_m), 1) AS max_m,
                   round(median(straight_line_m) / 1000.0
                         / {walking_speed} * 60.0, 2) AS median_minutes,
                   round(quantile_cont(straight_line_m, 0.9) / 1000.0
                         / {walking_speed} * 60.0, 2) AS p90_minutes,
                   count(*) FILTER (WHERE straight_line_m < 25) AS at_own_kerb,
                   round(median(network_m), 1) AS median_network_m
            FROM person_walks
            WHERE all_can_walk AND straight_line_m IS NOT NULL
            GROUP BY age_band, sex
            ORDER BY age_band, sex
            """.replace("{walking_speed}", str(planning.walking_speed_kph))
        )

        # A second cut on the attributes the assignment actually keys on, since
        # age and sex are not what determines a person's walk here.
        conn.execute(
            """
            CREATE OR REPLACE TABLE report_walking_by_status AS
            SELECT CASE WHEN own_household_vehicle THEN 'own household car'
                        ELSE 'neighbour''s car' END AS vehicle_source,
                   CASE WHEN contains_dependent THEN 'group with a dependent'
                        ELSE 'independent adults' END AS group_type,
                   count(*) AS people,
                   round(median(straight_line_m), 1) AS median_m,
                   round(quantile_cont(straight_line_m, 0.9), 1) AS p90_m,
                   round(quantile_cont(straight_line_m, 0.99), 1) AS p99_m,
                   round(max(straight_line_m), 1) AS max_m
            FROM person_walks
            WHERE all_can_walk AND straight_line_m IS NOT NULL
            GROUP BY 1, 2 ORDER BY 1, 2
            """
        )

        by_demographic = conn.execute(
            "SELECT * FROM report_walking_by_demographic"
        ).fetchdf()
        by_status = conn.execute("SELECT * FROM report_walking_by_status").fetchdf()
        coverage = conn.execute(
            """
            SELECT count(*) FILTER (WHERE all_can_walk) AS walkers,
                   count(*) FILTER (WHERE NOT all_can_walk) AS collected_at_home,
                   count(*) FILTER (WHERE all_can_walk AND straight_line_m IS NULL)
                     AS walkers_without_a_measured_walk,
                   round(median(straight_line_m) FILTER (WHERE all_can_walk), 1)
                     AS median_walk_m,
                   round(quantile_cont(straight_line_m, 0.99)
                         FILTER (WHERE all_can_walk), 1) AS p99_walk_m
            FROM person_walks
            """
        ).fetchdf().iloc[0]

    context.log.info(
        "Walking: %s walkers (median %.1f m, p99 %.1f m of a %.0f m ceiling); "
        "%s collected at home",
        f"{int(coverage['walkers']):,}",
        coverage["median_walk_m"],
        coverage["p99_walk_m"],
        ceiling_m,
        f"{int(coverage['collected_at_home']):,}",
    )
    context.add_output_metadata(
        {
            "walkers": int(coverage["walkers"]),
            "collected_at_home": int(coverage["collected_at_home"]),
            "walkers_without_a_measured_walk": int(
                coverage["walkers_without_a_measured_walk"]
            ),
            "median_walk_m": float(coverage["median_walk_m"]),
            "p99_walk_m": float(coverage["p99_walk_m"]),
            "walking_ceiling_m": round(ceiling_m, 1),
            "by_age_and_sex": MetadataValue.md(by_demographic.to_markdown(index=False)),
            "by_vehicle_source": MetadataValue.md(by_status.to_markdown(index=False)),
        }
    )
