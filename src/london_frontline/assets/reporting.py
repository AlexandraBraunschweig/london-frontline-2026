"""Summary reports, and the export a traffic microsimulation would consume.

Every shortfall this pipeline can produce is surfaced here rather than left
implicit: unmet demand, split families, concentrated collection stops, and the
data-quality gaps behind them.
"""

from dagster import AssetExecutionContext, MetadataValue, asset

from london_frontline.assets.routes import stop_notifications, vehicle_routes
from london_frontline.config import PlanningConfig
from london_frontline.paths import resolve
from london_frontline.resources import SpatialDuckDBResource

_EXPORT_TABLES = (
    "persons",
    "households",
    "household_locations",
    "person_relationships",
    "travel_groups",
    "travel_group_members",
    "vehicles",
    "muster_points",
    "seat_assignments",
    "person_seats",
    "unmet_demand",
    "vehicle_routes",
    "route_stops",
    "stop_notifications",
)


@asset(deps=[vehicle_routes], group_name="reporting")
def report_unmet_demand(
    context: AssetExecutionContext, warehouse: SpatialDuckDBResource
) -> None:
    """Unmet evacuation demand by Output Area, tier and reason."""
    with warehouse.connect() as conn:
        conn.execute(
            """
            CREATE OR REPLACE TABLE report_unmet_demand AS
            SELECT area_id, tier, collection_mode, reason,
                   count(*) AS groups, sum(size) AS people
            FROM unmet_demand
            GROUP BY ALL ORDER BY people DESC
            """
        )
        totals = conn.execute(
            """
            SELECT (SELECT coalesce(sum(people), 0) FROM report_unmet_demand)
                     AS people_unmet,
                   (SELECT count(*) FROM persons) AS people_total,
                   (SELECT count(DISTINCT area_id) FROM report_unmet_demand)
                     AS areas_affected
            """
        ).fetchdf().iloc[0]
        by_reason = conn.execute(
            "SELECT reason, sum(people) AS people FROM report_unmet_demand "
            "GROUP BY reason ORDER BY people DESC"
        ).fetchdf()

    unmet = int(totals["people_unmet"])
    total = int(totals["people_total"])
    context.log.info(
        "Unmet demand: %s of %s people (%.2f%%) across %s Output Areas",
        f"{unmet:,}",
        f"{total:,}",
        100 * unmet / total if total else 0,
        f"{int(totals['areas_affected']):,}",
    )
    context.add_output_metadata(
        {
            "people_unmet": unmet,
            "people_total": total,
            "unmet_rate_pct": round(100 * unmet / total, 2) if total else 0,
            "areas_affected": int(totals["areas_affected"]),
            "by_reason": MetadataValue.md(by_reason.to_markdown(index=False)),
        }
    )


@asset(deps=[vehicle_routes], group_name="reporting")
def report_seat_utilisation(
    context: AssetExecutionContext, warehouse: SpatialDuckDBResource
) -> None:
    """Occupancy per departing vehicle — the number the ride-share case rests on."""
    with warehouse.connect() as conn:
        conn.execute(
            """
            CREATE OR REPLACE TABLE report_seat_utilisation AS
            SELECT m.muster_point_id, m.capacity,
                   coalesce(sum(s.seats), 0) AS occupants,
                   m.capacity - coalesce(sum(s.seats), 0) AS empty_seats,
                   coalesce(sum(s.seats), 0) > 0 AS departs
            FROM muster_points m
            LEFT JOIN seat_assignments s USING (muster_point_id)
            GROUP BY m.muster_point_id, m.capacity
            """
        )
        stats = conn.execute(
            """
            SELECT count(*) FILTER (WHERE departs) AS vehicles_departing,
                   count(*) AS vehicles_total,
                   round(avg(occupants) FILTER (WHERE departs), 2) AS mean_occupancy,
                   sum(occupants) AS people_carried,
                   sum(capacity) FILTER (WHERE departs) AS seats_on_the_road,
                   round(100.0 * sum(occupants) FILTER (WHERE departs)
                         / nullif(sum(capacity) FILTER (WHERE departs), 0), 1)
                     AS seat_fill_pct
            FROM report_seat_utilisation
            """
        ).fetchdf().iloc[0]
        baseline = conn.execute(
            "SELECT count(*) FROM households WHERE num_cars > 0"
        ).fetchone()[0]

    departing = int(stats["vehicles_departing"])
    context.log.info(
        "%s vehicles depart carrying %s people (mean %.2f, %.1f%% of seats); "
        "baseline one-car-per-household would move %s cars",
        f"{departing:,}",
        f"{int(stats['people_carried']):,}",
        stats["mean_occupancy"],
        stats["seat_fill_pct"],
        f"{baseline:,}",
    )
    context.add_output_metadata(
        {
            "vehicles_departing": departing,
            "vehicles_total": int(stats["vehicles_total"]),
            "mean_occupancy": float(stats["mean_occupancy"]),
            "seat_fill_pct": float(stats["seat_fill_pct"]),
            "people_carried": int(stats["people_carried"]),
            "baseline_cars_one_per_car_owning_household": baseline,
            "cars_saved_vs_baseline": baseline - departing,
        }
    )


@asset(deps=[vehicle_routes], group_name="reporting")
def report_group_splits(
    context: AssetExecutionContext, warehouse: SpatialDuckDBResource
) -> None:
    """How often the oversized-group exception fired, so it stays exceptional."""
    with warehouse.connect() as conn:
        conn.execute(
            """
            CREATE OR REPLACE TABLE report_group_splits AS
            SELECT g.area_id, count(DISTINCT s.travel_group_id) AS split_groups,
                   count(DISTINCT g.travel_group_id) AS groups_total
            FROM travel_groups g
            LEFT JOIN seat_assignments s
              ON s.travel_group_id = g.travel_group_id AND s.split
            GROUP BY g.area_id
            """
        )
        stats = conn.execute(
            """
            SELECT sum(split_groups) AS split_groups,
                   sum(groups_total) AS groups_total,
                   round(100.0 * sum(split_groups) / nullif(sum(groups_total), 0), 3)
                     AS split_rate_pct
            FROM report_group_splits
            """
        ).fetchdf().iloc[0]

    context.log.info(
        "Travel-group split rate: %s of %s groups (%.3f%%)",
        f"{int(stats['split_groups']):,}",
        f"{int(stats['groups_total']):,}",
        stats["split_rate_pct"] or 0,
    )
    context.add_output_metadata(
        {
            "split_groups": int(stats["split_groups"]),
            "groups_total": int(stats["groups_total"]),
            "split_rate_pct": float(stats["split_rate_pct"] or 0),
        }
    )


@asset(deps=[vehicle_routes], group_name="reporting")
def report_collection_stops(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Collection stops per vehicle, to catch the spreading rule failing."""
    with warehouse.connect() as conn:
        conn.execute(
            """
            CREATE OR REPLACE TABLE report_collection_stops AS
            SELECT collection_stops, count(*) AS vehicles
            FROM vehicle_routes GROUP BY collection_stops ORDER BY collection_stops
            """
        )
        stats = conn.execute(
            """
            SELECT max(collection_stops) AS max_stops,
                   round(avg(collection_stops), 3) AS mean_stops,
                   count(*) FILTER (WHERE collection_stops > 0) AS vehicles_collecting,
                   sum(collection_stops) AS collection_stops_total
            FROM vehicle_routes
            """
        ).fetchdf().iloc[0]
        distribution = conn.execute(
            "SELECT * FROM report_collection_stops"
        ).fetchdf()

    cap = planning.max_collection_stops_per_vehicle
    if int(stats["max_stops"]) > cap:
        raise ValueError(
            f"A vehicle has {int(stats['max_stops'])} collection stops, above the "
            f"configured cap of {cap}"
        )
    context.log.info(
        "Collection stops: %s vehicles collect, mean %.3f, max %s (cap %s)",
        f"{int(stats['vehicles_collecting']):,}",
        stats["mean_stops"],
        int(stats["max_stops"]),
        cap,
    )
    context.add_output_metadata(
        {
            "vehicles_collecting": int(stats["vehicles_collecting"]),
            "collection_stops_total": int(stats["collection_stops_total"]),
            "mean_stops_per_vehicle": float(stats["mean_stops"]),
            "max_stops_per_vehicle": int(stats["max_stops"]),
            "configured_cap": cap,
            "distribution": MetadataValue.md(distribution.to_markdown(index=False)),
        }
    )


@asset(deps=[vehicle_routes], group_name="reporting")
def report_data_quality(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Every known gap between the source data and the model, in one table."""
    threshold = planning.parking_snap_review_distance_m
    with warehouse.connect() as conn:
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE report_data_quality AS
            SELECT 'vehicles parked beyond the {threshold:.0f} m review distance'
                     AS measure,
                   count(*) AS value,
                   'OSM has not mapped many access roads and driveways; no vehicle '
                   || 'is dropped for this' AS note
            FROM muster_points WHERE snap_distance_m > {threshold}
            UNION ALL
            SELECT 'children with no qualifying co-resident parent',
                   count(*),
                   'attributes are paired independently across marginals, so ages '
                   || 'are unrelated to household composition type'
            FROM children_without_co_resident_parent
            UNION ALL
            SELECT 'households with no dwelling point available',
                   coalesce(sum(greatest(unplaced_households, 0)), 0),
                   'Output Area had fewer UPRNs than synthetic households'
            FROM home_location_shortfall
            UNION ALL
            SELECT 'Output Areas needing an LSOA marginal fallback',
                   count(DISTINCT area_code),
                   'Census 2021 perturbation preserves published totals, so this '
                   || 'is expected to be zero'
            FROM marginal_fallbacks
            UNION ALL
            SELECT 'people absent: communal establishment residents',
                   (SELECT sum(persons_published) - sum(persons_generated)
                      FROM population_validation),
                   'the synthetic population is the household population; care '
                   || 'homes and halls belong to no household'
            """
        )
        rows = conn.execute("SELECT * FROM report_data_quality").fetchdf()

    context.log.info("Data-quality report:\n%s", rows.to_string(index=False))
    context.add_output_metadata(
        {"report": MetadataValue.md(rows.to_markdown(index=False))}
    )


@asset(
    deps=[
        report_unmet_demand,
        report_seat_utilisation,
        report_group_splits,
        report_collection_stops,
        report_data_quality,
        stop_notifications,
    ],
    group_name="reporting",
)
def evacuation_plan_export(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Export the finished plan as parquet for a downstream microsimulation."""
    out_dir = resolve(planning.data_dir) / "exports" / "plan"
    out_dir.mkdir(parents=True, exist_ok=True)

    written = {}
    with warehouse.connect() as conn:
        for table in _EXPORT_TABLES:
            path = out_dir / f"{table}.parquet"
            conn.execute(f"COPY {table} TO '{path}' (FORMAT PARQUET)")
            written[table] = path.stat().st_size

    total_bytes = sum(written.values())
    context.log.info(
        "Exported %d tables (%.1f MB) to %s",
        len(written),
        total_bytes / 1e6,
        out_dir,
    )
    context.add_output_metadata(
        {
            "path": MetadataValue.path(str(out_dir)),
            "tables": len(written),
            "total_mb": round(total_bytes / 1e6, 1),
        }
    )
