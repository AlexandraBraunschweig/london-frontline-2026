"""Pedestrian network, and the walk from each home to nearby muster points."""

import pandas as pd
from dagster import AssetExecutionContext, asset

from london_frontline.assets.geography import LEVEL_LAD, admin_areas
from london_frontline.assets.home_assignment import household_locations
from london_frontline.assets.vehicles import muster_points
from london_frontline.config import PlanningConfig
from london_frontline.paths import resolve
from london_frontline.resources import SpatialDuckDBResource

# Match the road fetch, so a home near the boundary can still reach a footway
# just outside it.
_BBOX_MARGIN_DEGREES = 0.02

_POI_CATEGORY = "muster"

# Headroom between the distance pandana preprocesses and the ceiling queried.
_PREPROCESS_MARGIN = 1.05

_OVERPASS_TIMEOUT_S = 600


@asset(deps=[admin_areas], group_name="pedestrian")
def pedestrian_network(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Extract the OSM walking network for the LAD with osmnx.

    osmnx owns the Overpass conversation — rate limiting, retries, caching, query
    subdivision — and its ``walk`` filter encodes far more care about access,
    ``foot`` and service tags than a hand-written highway regex.

    ``simplify=False`` is deliberate. Simplification keeps nodes only at
    intersections, but 62,000 homes and 70,000 vehicles are snapped *to nodes*,
    so collapsing the mid-block geometry would coarsen exactly the short walks
    this model cares about. The unsimplified graph is a comparable size to the
    simplified one for pandana's purposes and costs nothing extra to build.

    Cached to parquet as well as DuckDB: Overpass is slow and intermittently
    unavailable, and the network does not change between runs.
    """
    import osmnx as ox

    ox.settings.requests_timeout = _OVERPASS_TIMEOUT_S

    with warehouse.connect() as conn:
        min_lon, min_lat, max_lon, max_lat = conn.execute(
            """
            SELECT ST_XMin(geometry), ST_YMin(geometry),
                   ST_XMax(geometry), ST_YMax(geometry)
            FROM admin_areas WHERE level = ?
            """,
            [LEVEL_LAD],
        ).fetchone()

    graph = ox.graph.graph_from_bbox(
        bbox=(
            min_lon - _BBOX_MARGIN_DEGREES,
            min_lat - _BBOX_MARGIN_DEGREES,
            max_lon + _BBOX_MARGIN_DEGREES,
            max_lat + _BBOX_MARGIN_DEGREES,
        ),
        network_type="walk",
        simplify=False,
    )
    # osmnx returns a MultiDiGraph, which stores every walk edge in both
    # directions; feeding that to pandana (which treats edges as two-way) would
    # duplicate the whole network.
    node_frame, edge_frame = ox.convert.graph_to_gdfs(
        ox.convert.to_undirected(graph), nodes=True, edges=True
    )
    nodes = pd.DataFrame(
        {
            "id": node_frame.index.astype("int64"),
            "x": node_frame["x"].to_numpy(),
            "y": node_frame["y"].to_numpy(),
        }
    )
    edges = pd.DataFrame(
        {
            "from": edge_frame.index.get_level_values("u").astype("int64"),
            "to": edge_frame.index.get_level_values("v").astype("int64"),
            "distance": edge_frame["length"].to_numpy(),
        }
    )
    context.log.info(
        "Pedestrian network: %s nodes, %s edges", f"{len(nodes):,}", f"{len(edges):,}"
    )

    cache_dir = resolve(planning.data_dir) / "network"
    cache_dir.mkdir(parents=True, exist_ok=True)
    nodes.to_parquet(cache_dir / "walk_nodes.parquet")
    edges.to_parquet(cache_dir / "walk_edges.parquet")

    with warehouse.connect() as conn:
        conn.register("nodes_df", nodes[["id", "x", "y"]])
        conn.register("edges_df", edges[["from", "to", "distance"]])
        conn.execute(
            "CREATE OR REPLACE TABLE walk_nodes AS SELECT * FROM nodes_df"
        )
        conn.execute(
            'CREATE OR REPLACE TABLE walk_edges AS '
            'SELECT "from" AS from_node, "to" AS to_node, distance FROM edges_df'
        )

    context.add_output_metadata(
        {
            "nodes": len(nodes),
            "edges": len(edges),
            "total_length_km": round(float(edges["distance"].sum()) / 1000, 1),
        }
    )


@asset(
    deps=[pedestrian_network, muster_points, household_locations],
    group_name="pedestrian",
)
def walk_candidates(
    context: AssetExecutionContext,
    planning: PlanningConfig,
    warehouse: SpatialDuckDBResource,
) -> None:
    """Nearest muster points within the walking ceiling, per household.

    A full home-to-muster-point walking matrix is not viable at this density, so
    each household gets its nearest N candidates by pedestrian-network distance.
    How often a group exhausts that list is reported by the assignment stage, so
    a candidate-cap effect is never silently reported as a capacity shortfall.
    """
    import pandana

    ceiling_metres = (
        planning.walking_ceiling_minutes / 60.0 * planning.walking_speed_kph * 1000.0
    )
    candidates = planning.walk_candidates_per_household

    with warehouse.connect() as conn:
        nodes = conn.execute("SELECT id, x, y FROM walk_nodes").fetchdf()
        edges = conn.execute(
            "SELECT from_node, to_node, distance FROM walk_edges"
        ).fetchdf()
        homes = conn.execute(
            "SELECT household_id, longitude, latitude FROM household_locations"
        ).fetchdf()
        points = conn.execute(
            "SELECT muster_point_id, ST_X(geometry) AS longitude, "
            "ST_Y(geometry) AS latitude FROM muster_points"
        ).fetchdf()

    nodes = nodes.set_index("id")
    network = pandana.Network(
        nodes["x"], nodes["y"], edges["from_node"], edges["to_node"], edges[["distance"]]
    )
    # pandana rejects a query distance that is not strictly below the distance
    # used during preprocessing, so both the contraction hierarchy and the POI
    # index are built with a margin above the ceiling actually queried.
    preprocess_metres = ceiling_metres * _PREPROCESS_MARGIN
    network.precompute(preprocess_metres)

    network.set_pois(
        category=_POI_CATEGORY,
        maxdist=preprocess_metres,
        maxitems=candidates,
        x_col=points["longitude"],
        y_col=points["latitude"],
    )
    nearest = network.nearest_pois(
        distance=ceiling_metres,
        category=_POI_CATEGORY,
        num_pois=candidates,
        include_poi_ids=True,
    )
    context.log.info(
        "Computed %s nearest muster points for %s network nodes within %.0f m",
        candidates,
        f"{len(nearest):,}",
        ceiling_metres,
    )

    # nearest_pois is indexed by network node; map households onto their node.
    homes["node_id"] = network.get_node_ids(homes["longitude"], homes["latitude"]).values

    distance_columns = [column for column in nearest.columns if isinstance(column, int)]
    poi_columns = [f"poi{n}" for n in distance_columns]

    distances = (
        nearest[distance_columns]
        .stack()
        .rename("walk_distance_m")
        .rename_axis(["node_id", "rank"])
        .reset_index()
    )
    identifiers = (
        nearest[poi_columns]
        .rename(columns={f"poi{n}": n for n in distance_columns})
        .stack()
        .rename("muster_point_index")
        .rename_axis(["node_id", "rank"])
        .reset_index()
    )
    per_node = distances.merge(identifiers, on=["node_id", "rank"])
    # A node with fewer than `candidates` muster points in range is padded with
    # infinite distances and missing ids, so those rows are dropped before the
    # ids are used to index anything.
    per_node = per_node[
        per_node["walk_distance_m"].le(ceiling_metres)
        & per_node["muster_point_index"].notna()
    ]
    # pandana returns the POI's positional index, not its identifier.
    per_node["muster_point_id"] = (
        points["muster_point_id"].to_numpy()[per_node["muster_point_index"].astype(int)]
    )

    result = homes[["household_id", "node_id"]].merge(per_node, on="node_id")
    result["walk_minutes"] = (
        result["walk_distance_m"] / 1000.0 / planning.walking_speed_kph * 60.0
    )
    result = result[
        ["household_id", "muster_point_id", "rank", "walk_distance_m", "walk_minutes"]
    ]

    with warehouse.connect() as conn:
        conn.register("candidates_df", result)
        conn.execute(
            "CREATE OR REPLACE TABLE walk_candidates AS SELECT * FROM candidates_df"
        )
        summary = conn.execute(
            """
            SELECT count(*) AS rows,
                   count(DISTINCT household_id) AS households_with_candidates,
                   round(avg(per_household), 1) AS mean_candidates,
                   round(median(nearest_m), 1) AS median_nearest_m
            FROM (
                SELECT household_id, count(*) AS per_household,
                       min(walk_distance_m) AS nearest_m
                FROM walk_candidates GROUP BY household_id
            )
            """
        ).fetchdf().iloc[0]
        households_total = conn.execute(
            "SELECT count(*) FROM household_locations"
        ).fetchone()[0]

    stranded = households_total - int(summary["households_with_candidates"])
    context.log.info(
        "%s of %s households have a muster point within %.0f m "
        "(mean %.1f candidates, median nearest %.1f m); %s have none",
        f"{int(summary['households_with_candidates']):,}",
        f"{households_total:,}",
        ceiling_metres,
        summary["mean_candidates"],
        summary["median_nearest_m"],
        f"{stranded:,}",
    )
    context.add_output_metadata(
        {
            "candidate_rows": int(summary["rows"]),
            "households_with_candidates": int(summary["households_with_candidates"]),
            "households_without_any_candidate": stranded,
            "mean_candidates_per_household": float(summary["mean_candidates"]),
            "median_nearest_walk_m": float(summary["median_nearest_m"]),
            "walking_ceiling_m": round(ceiling_metres, 1),
            "candidate_cap": candidates,
        }
    )
