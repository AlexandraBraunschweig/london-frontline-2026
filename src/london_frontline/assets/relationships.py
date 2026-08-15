"""The person_relationships edge table and the indivisible travel groups it forms."""

import pandas as pd
from dagster import AssetExecutionContext, asset

from london_frontline import relationships
from london_frontline.assets.population import synthetic_population
from london_frontline.config import PlanningConfig
from london_frontline.resources import SpatialDuckDBResource


@asset(deps=[synthetic_population], group_name="relationships")
def person_relationships(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Derive mandatory ``dependent_of`` edges for every household.

    Stored normalized — one row per (person, related person, type) — rather than
    as a list column, so referential integrity holds, reverse lookups are an
    indexed join, and the future trust network is a new relationship_type rather
    than a schema change.
    """
    with warehouse.connect() as conn:
        persons = conn.execute(
            "SELECT person_id, household_id, age FROM persons ORDER BY household_id, person_id"
        ).fetchdf()

    edges: list[tuple[int, int]] = []
    children_without_parent: list[int] = []
    households_without_parent: set[int] = set()

    for household_id, group in persons.groupby("household_id", sort=False):
        members = [
            relationships.Member(int(row.person_id), int(row.age))
            for row in group.itertuples()
        ]
        household_edges, orphaned = relationships.household_dependency_edges(
            members,
            planning.minimum_parent_child_age_gap_years,
            planning.both_parents_required_below_age,
            planning.one_parent_required_below_age,
        )
        edges.extend(household_edges)
        if orphaned:
            children_without_parent.extend(orphaned)
            households_without_parent.add(int(household_id))

    edge_frame = pd.DataFrame(
        edges, columns=["person_id", "related_person_id"]
    ).assign(relationship_type=relationships.RELATIONSHIP_DEPENDENT_OF)
    orphan_frame = pd.DataFrame(
        {"person_id": children_without_parent}
    ).assign(reason="no co-resident member meets the minimum parent-child age gap")

    with warehouse.connect() as conn:
        conn.register("edges_df", edge_frame)
        conn.register("orphans_df", orphan_frame)
        conn.execute(
            "CREATE OR REPLACE TABLE person_relationships AS SELECT * FROM edges_df"
        )
        conn.execute(
            "CREATE OR REPLACE TABLE children_without_co_resident_parent AS "
            "SELECT * FROM orphans_df"
        )

    children_total = int((persons["age"] < planning.one_parent_required_below_age).sum())
    context.log.info(
        "%s dependent_of edges; %s of %s under-%d children have no qualifying "
        "co-resident parent (%.1f%%), across %s households",
        f"{len(edge_frame):,}",
        f"{len(children_without_parent):,}",
        f"{children_total:,}",
        planning.one_parent_required_below_age,
        100 * len(children_without_parent) / children_total if children_total else 0,
        f"{len(households_without_parent):,}",
    )
    context.add_output_metadata(
        {
            "dependent_of_edges": len(edge_frame),
            "children_under_dependency_age": children_total,
            "children_without_qualifying_parent": len(children_without_parent),
            "households_without_qualifying_parent": len(households_without_parent),
        }
    )


@asset(deps=[person_relationships], group_name="relationships")
def travel_groups(
    context: AssetExecutionContext,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Compute indivisible travel groups as connected components of the edges.

    Edges never cross households, so components never do either; a person with no
    mandatory edge forms a group of one.
    """
    with warehouse.connect() as conn:
        persons = conn.execute(
            "SELECT person_id, household_id, area_id, mobility_status FROM persons"
        ).fetchdf()
        edges = conn.execute(
            "SELECT person_id, related_person_id FROM person_relationships "
            "WHERE relationship_type = ?",
            [relationships.RELATIONSHIP_DEPENDENT_OF],
        ).fetchdf()

    assignment = relationships.travel_groups(
        persons["person_id"].astype(int).tolist(),
        list(
            zip(
                edges["person_id"].astype(int),
                edges["related_person_id"].astype(int),
            )
        ),
    )
    persons["travel_group_id"] = persons["person_id"].map(assignment)

    with warehouse.connect() as conn:
        conn.register("groups_df", persons[["person_id", "travel_group_id"]])
        conn.execute(
            """
            CREATE OR REPLACE TABLE travel_group_members AS
            SELECT g.travel_group_id, p.person_id, p.household_id, p.area_id
            FROM groups_df g JOIN persons p USING (person_id)
            """
        )
        # One row per group, carrying what seat assignment needs to decide on it:
        # size, whether it contains a dependent, and whether everyone can walk.
        conn.execute(
            """
            CREATE OR REPLACE TABLE travel_groups AS
            SELECT m.travel_group_id,
                   any_value(m.household_id) AS household_id,
                   any_value(m.area_id) AS area_id,
                   count(*) AS group_size,
                   count(*) > 1 AS contains_dependent,
                   bool_and(p.mobility_status) AS all_can_walk
            FROM travel_group_members m
            JOIN persons p USING (person_id)
            GROUP BY m.travel_group_id
            """
        )
        summary = conn.execute(
            """
            SELECT count(*) AS groups,
                   max(group_size) AS largest_group,
                   count(*) FILTER (WHERE group_size = 1) AS singleton_groups,
                   count(*) FILTER (WHERE contains_dependent) AS groups_with_dependent,
                   count(*) FILTER (WHERE NOT all_can_walk) AS home_collection_groups,
                   count(DISTINCT household_id) AS households_spanned
            FROM travel_groups
            """
        ).fetchdf().iloc[0]

        crossing = conn.execute(
            """
            SELECT count(*) FROM (
                SELECT travel_group_id FROM travel_group_members
                GROUP BY travel_group_id HAVING count(DISTINCT household_id) > 1
            )
            """
        ).fetchone()[0]

    if crossing:
        raise ValueError(
            f"{crossing} travel groups span more than one household; mandatory "
            f"edges must never cross household boundaries"
        )

    context.log.info(
        "%s travel groups (%s singletons, %s with a dependent, largest %d)",
        f"{int(summary['groups']):,}",
        f"{int(summary['singleton_groups']):,}",
        f"{int(summary['groups_with_dependent']):,}",
        int(summary["largest_group"]),
    )
    context.add_output_metadata({key: int(summary[key]) for key in summary.index})
