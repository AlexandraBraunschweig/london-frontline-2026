"""Synthetic person and household generation, matched to Census OA marginals."""

import numpy as np
import pandas as pd
from dagster import AssetExecutionContext, asset

from london_frontline import synthesis
from london_frontline.assets.census import census_marginals_resolved
from london_frontline.config import PlanningConfig
from london_frontline.resources import SpatialDuckDBResource

# Census table ids this asset reads, named so the SQL below stays legible.
TABLE_COMPOSITION = "TS003"
TABLE_HOUSEHOLD_SIZE = "TS017"
TABLE_CARS = "TS045"
TABLE_AGE = "TS007A"
TABLE_SEX = "TS008"
TABLE_DISABILITY = "TS038"

LICENSE_NONE = "none"
LICENSE_CAR = "car"
LICENSE_BUS = "bus"


@asset(deps=[census_marginals_resolved], group_name="population")
def synthetic_population(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Generate households and persons for every Output Area in the LAD.

    Each attribute is apportioned to match its published marginal and then paired
    at random against the others, which is what marginal-only synthesis means
    here: exact marginals, independent joint distribution.
    """
    rng = np.random.default_rng(planning.random_seed)

    with warehouse.connect() as conn:
        leaves = conn.execute(
            """
            SELECT area_code, census_table, category_name, value
            FROM census_marginals_resolved WHERE is_leaf ORDER BY area_code, category_order
            """
        ).fetchdf()
        totals = conn.execute(
            """
            SELECT area_code, census_table, value
            FROM census_marginals_resolved WHERE is_total
            """
        ).fetchdf()

    by_area_table = {
        key: group for key, group in leaves.groupby(["area_code", "census_table"])
    }
    total_lookup = {
        (row.area_code, row.census_table): int(row.value)
        for row in totals.itertuples()
    }

    def marginal(area: str, table: str) -> pd.DataFrame:
        return by_area_table[(area, table)]

    household_frames: list[pd.DataFrame] = []
    person_frames: list[pd.DataFrame] = []
    shortfalls: list[dict] = []
    licence_conflicts: list[dict] = []
    next_household_id = 0
    next_person_id = 0

    non_walking = tuple(
        label.lower() for label in planning.non_walking_disability_labels
    )

    for area in sorted(leaves["area_code"].unique()):
        household_total = total_lookup[(area, TABLE_COMPOSITION)]
        if household_total <= 0:
            continue

        size_marginal = marginal(area, TABLE_HOUSEHOLD_SIZE)
        composition_marginal = marginal(area, TABLE_COMPOSITION)
        car_marginal = marginal(area, TABLE_CARS)

        sizes = synthesis.allocate_counts(
            rng,
            [
                synthesis.leading_int(name, planning.open_ended_household_size)
                for name in size_marginal["category_name"]
            ],
            size_marginal["value"].to_numpy(),
            household_total,
        ).astype(int)
        compositions = synthesis.allocate_counts(
            rng,
            composition_marginal["category_name"].tolist(),
            composition_marginal["value"].to_numpy(),
            household_total,
        )
        household_ids = np.arange(
            next_household_id, next_household_id + household_total
        )
        next_household_id += household_total

        # Persons are generated to fill the household sizes just allocated, so
        # the person count follows the household-size marginal rather than the
        # age marginal's total; the two differ slightly because each is rounded
        # independently by ONS.
        person_total = int(sizes.sum())
        age_marginal = marginal(area, TABLE_AGE)
        sex_marginal = marginal(area, TABLE_SEX)
        disability_marginal = marginal(area, TABLE_DISABILITY)

        bands = synthesis.allocate_counts(
            rng,
            age_marginal["category_name"].tolist(),
            age_marginal["value"].to_numpy(),
            person_total,
        )
        ages = np.array(
            [synthesis.age_from_band(rng, band) for band in bands], dtype=int
        )
        sexes = synthesis.allocate_counts(
            rng,
            sex_marginal["category_name"].tolist(),
            sex_marginal["value"].to_numpy(),
            person_total,
        )
        disability = synthesis.allocate_counts(
            rng,
            disability_marginal["category_name"].tolist(),
            disability_marginal["value"].to_numpy(),
            person_total,
        )
        mobility_status = np.array(
            [
                not any(token in str(label).lower() for token in non_walking)
                for label in disability
            ]
        )

        # Cars and licences are conditioned on the household's adults, so both
        # are decided here rather than alongside the other household attributes:
        # a household cannot own a car it has nobody old enough to drive.
        household_position = np.repeat(np.arange(household_total), sizes)
        is_adult = ages >= planning.minimum_driving_age
        adults_per_household = np.bincount(
            household_position[is_adult], minlength=household_total
        )

        car_values = [
            # "No cars or vans in household" has no digits and means zero.
            synthesis.leading_int(name, planning.open_ended_car_count)
            if any(ch.isdigit() for ch in name)
            else 0
            for name in car_marginal["category_name"]
        ]
        category_counts = synthesis.apportion(
            car_marginal["value"].to_numpy(), household_total
        )
        cars, car_shortfall = synthesis.assign_cars_capped_by_adults(
            rng, car_values, category_counts, adults_per_household
        )
        for value, missing in car_shortfall.items():
            shortfalls.append(
                {"area_id": area, "cars_per_household": value, "households": missing}
            )

        target_licences = int(round(is_adult.sum() * planning.car_license_proportion))
        licensed, licence_excess = synthesis.assign_car_licences(
            rng, is_adult, household_position, cars, target_licences
        )
        if licence_excess:
            licence_conflicts.append(
                {"area_id": area, "licences_above_target": licence_excess}
            )

        licences = np.where(licensed, LICENSE_CAR, LICENSE_NONE).astype(object)
        # Bus licences stay an independent draw: there is no bus ownership to
        # couple them to.
        bus_draw = rng.random(person_total) < planning.bus_license_proportion
        licences[is_adult & ~licensed & bus_draw] = LICENSE_BUS

        household_frames.append(
            pd.DataFrame(
                {
                    "household_id": household_ids,
                    "area_id": area,
                    "composition_type": compositions,
                    "num_persons": sizes,
                    "num_cars": cars,
                    "num_adults": adults_per_household,
                }
            )
        )

        person_ids = np.arange(next_person_id, next_person_id + person_total)
        next_person_id += person_total

        person_frames.append(
            pd.DataFrame(
                {
                    "person_id": person_ids,
                    "household_id": np.repeat(household_ids, sizes),
                    "area_id": area,
                    "age": ages,
                    "sex": sexes,
                    "license_type": licences,
                    "mobility_status": mobility_status,
                    "name": synthesis.names(rng, person_total),
                    "phone_number": synthesis.phone_numbers(
                        rng, person_total, planning.missing_phone_proportion
                    ),
                    "medical_skill": rng.random(person_total)
                    < planning.medical_skill_proportion,
                }
            )
        )

    households = pd.concat(household_frames, ignore_index=True)
    persons = pd.concat(person_frames, ignore_index=True)

    with warehouse.connect() as conn:
        conn.register("households_df", households)
        conn.register("persons_df", persons)
        conn.register(
            "car_shortfall_df",
            pd.DataFrame(
                shortfalls, columns=["area_id", "cars_per_household", "households"]
            ),
        )
        conn.register(
            "licence_conflict_df",
            pd.DataFrame(
                licence_conflicts, columns=["area_id", "licences_above_target"]
            ),
        )
        conn.execute("CREATE OR REPLACE TABLE households AS SELECT * FROM households_df")
        conn.execute("CREATE OR REPLACE TABLE persons AS SELECT * FROM persons_df")
        conn.execute(
            "CREATE OR REPLACE TABLE car_assignment_shortfall AS "
            "SELECT * FROM car_shortfall_df"
        )
        conn.execute(
            "CREATE OR REPLACE TABLE licence_target_conflicts AS "
            "SELECT * FROM licence_conflict_df"
        )

    without_phone = int(persons["phone_number"].isna().sum())
    context.log.info(
        "Generated %s households and %s persons across %d Output Areas",
        f"{len(households):,}",
        f"{len(persons):,}",
        households["area_id"].nunique(),
    )
    over_capped = int((households["num_cars"] > households["num_adults"]).sum())
    if over_capped:
        raise ValueError(
            f"{over_capped} households hold more cars than adults; the cap in "
            f"assign_cars_capped_by_adults has been breached"
        )

    context.add_output_metadata(
        {
            "households": len(households),
            "persons": len(persons),
            "persons_without_phone": without_phone,
            "cannot_walk": int((~persons["mobility_status"]).sum()),
            "car_licence_holders": int(
                (persons["license_type"] == LICENSE_CAR).sum()
            ),
            "car_owning_households": int((households["num_cars"] > 0).sum()),
            "vehicles_implied": int(households["num_cars"].sum()),
            "households_with_more_cars_than_adults": int(
                (households["num_cars"] > households["num_adults"]).sum()
            ),
            "car_shortfall_rows": len(shortfalls),
            "licence_target_conflicts": len(licence_conflicts),
        }
    )


@asset(deps=[synthetic_population], group_name="population")
def population_validation(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Compare generated aggregates against the source marginals, per Output Area.

    Three different standards apply, because the marginals themselves are not
    mutually consistent — ONS rounds and perturbs each table independently:

    * Household count is the apportionment base and must match exactly.
    * Car count can only match to within the disagreement between TS003's and
      TS045's household totals, so it is checked against a bound derived from
      that disagreement rather than against zero.
    * Person count is expected to fall short of the published resident total,
      because persons are generated to fill households and the residents of
      communal establishments (care homes, halls, prisons) belong to no
      household. Only the size of that shortfall is policed.
    """
    with warehouse.connect() as conn:
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE population_validation AS
            WITH totals AS (
                SELECT area_code AS area_id, census_table, value
                FROM census_marginals_resolved WHERE is_total
            ),
            car_categories AS (
                SELECT area_code AS area_id, count(*) AS n_categories,
                       sum(CASE
                             WHEN category_name ILIKE 'No cars%' THEN 0
                             WHEN category_name ILIKE '3 or more%'
                               THEN {{open_car}} * value
                             ELSE CAST(regexp_extract(category_name, '[0-9]+') AS INTEGER) * value
                           END) AS cars_published
                FROM census_marginals_resolved
                WHERE census_table = 'TS045' AND is_leaf
                GROUP BY area_code
            ),
            generated AS (
                SELECT area_id, count(*) AS households_generated,
                       sum(num_cars) AS cars_generated
                FROM households GROUP BY area_id
            ),
            generated_persons AS (
                SELECT area_id, count(*) AS persons_generated FROM persons GROUP BY area_id
            )
            SELECT g.area_id,
                   g.households_generated,
                   th.value AS households_published,
                   g.households_generated - th.value AS households_gap,
                   gp.persons_generated,
                   tp.value AS persons_published,
                   gp.persons_generated - tp.value AS persons_gap,
                   g.cars_generated,
                   cc.cars_published,
                   g.cars_generated - cc.cars_published AS cars_gap,
                   -- Rescaling TS045 onto TS003's household count can move at most
                   -- this many cars, plus one per category from largest-remainder
                   -- rounding.
                   abs(th.value - tc.value) * {{open_car}} + cc.n_categories
                     AS cars_gap_allowed
            FROM generated g
            JOIN generated_persons gp USING (area_id)
            JOIN car_categories cc USING (area_id)
            JOIN totals th ON th.area_id = g.area_id AND th.census_table = 'TS003'
            JOIN totals tc ON tc.area_id = g.area_id AND tc.census_table = 'TS045'
            JOIN totals tp ON tp.area_id = g.area_id AND tp.census_table = 'TS007A'
            """.replace("{open_car}", str(planning.open_ended_car_count))
        )
        row = conn.execute(
            """
            SELECT count(*) AS areas,
                   count(*) FILTER (WHERE households_gap <> 0) AS households_mismatched,
                   count(*) FILTER (WHERE abs(cars_gap) > cars_gap_allowed)
                     AS cars_beyond_bound,
                   sum(persons_generated) AS persons_generated,
                   sum(persons_published) AS persons_published
            FROM population_validation
            """
        ).fetchdf().iloc[0]

    coupling = None
    with warehouse.connect() as conn:
        conn.execute(
            """
            CREATE OR REPLACE TABLE report_car_licence_coupling AS
            WITH h AS (
                SELECT hh.household_id, hh.num_cars, hh.num_adults,
                       count(*) FILTER (WHERE p.license_type = 'car') AS drivers
                FROM households hh JOIN persons p USING (household_id)
                GROUP BY 1, 2, 3
            )
            SELECT num_cars,
                   count(*) AS households,
                   count(*) FILTER (WHERE drivers = 0) AS households_without_a_driver,
                   count(*) FILTER (WHERE drivers < num_cars) AS drivers_below_cars,
                   count(*) FILTER (WHERE num_cars > num_adults) AS cars_above_adults,
                   round(avg(drivers), 2) AS mean_drivers
            FROM h GROUP BY num_cars ORDER BY num_cars
            """
        )
        coupling = conn.execute(
            """
            SELECT coalesce(sum(households_without_a_driver)
                            FILTER (WHERE num_cars > 0), 0) AS car_owning_without_driver,
                   coalesce(sum(cars_above_adults), 0) AS cars_above_adults,
                   coalesce(sum(drivers_below_cars), 0) AS drivers_below_cars
            FROM report_car_licence_coupling
            """
        ).fetchdf().iloc[0]

    if int(coupling["car_owning_without_driver"]) or int(coupling["cars_above_adults"]):
        raise ValueError(
            f"Coupling breached: {int(coupling['car_owning_without_driver'])} "
            f"car-owning households have no licensed driver and "
            f"{int(coupling['cars_above_adults'])} hold more cars than adults"
        )

    if int(row["households_mismatched"]):
        raise ValueError(
            f"{int(row['households_mismatched'])} Output Areas have a household "
            f"count differing from TS003; apportionment must be exact"
        )
    if int(row["cars_beyond_bound"]):
        raise ValueError(
            f"{int(row['cars_beyond_bound'])} Output Areas have a car-count gap "
            f"larger than the TS003/TS045 total disagreement can explain"
        )

    generated = int(row["persons_generated"])
    published = int(row["persons_published"])
    shortfall = published - generated
    shortfall_fraction = shortfall / published if published else 0.0
    if shortfall_fraction > planning.max_person_shortfall_fraction:
        raise ValueError(
            f"Generated population is {shortfall_fraction:.1%} below the published "
            f"resident total, beyond the {planning.max_person_shortfall_fraction:.0%} "
            f"allowed for communal-establishment residents"
        )

    context.log.info(
        "Validation passed: %d areas, %s persons generated vs %s published "
        "(%.2f%% shortfall, communal-establishment residents)",
        int(row["areas"]),
        f"{generated:,}",
        f"{published:,}",
        shortfall_fraction * 100,
    )
    context.add_output_metadata(
        {
            "areas": int(row["areas"]),
            "households_mismatched": 0,
            "cars_beyond_bound": 0,
            "persons_generated": generated,
            "persons_published": published,
            "communal_establishment_shortfall": shortfall,
            "communal_establishment_shortfall_pct": round(shortfall_fraction * 100, 2),
            "car_owning_households_without_a_driver": 0,
            "households_with_more_cars_than_adults": 0,
            "households_with_fewer_drivers_than_cars": int(
                coupling["drivers_below_cars"]
            ),
        }
    )
