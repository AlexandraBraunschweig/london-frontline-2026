"""Presentation-layer tests (tasks 3.1-3.3)."""

import pytest

from london_frontline.resources import SpatialDuckDBResource

LAYERS = (
    "layer_oa_summary",
    "layer_fleet",
    "layer_walk_lines",
    "layer_collection_routes",
    "layer_unmet",
)


@pytest.fixture(scope="module")
def conn():
    with SpatialDuckDBResource().connect() as connection:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        if not set(LAYERS) <= tables:
            pytest.skip("map layers not materialised; run the pipeline first")
        yield connection


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_has_geometry_and_features(conn, layer):
    """Task 3.1: a layer with no geometry is not a layer."""
    total, with_geometry = conn.execute(
        f"SELECT count(*), count(geometry) FROM {layer}"
    ).fetchone()
    assert total > 0, f"{layer} is empty"
    assert with_geometry == total, f"{layer} has features without geometry"


def test_fleet_shows_both_activated_and_parked(conn):
    """Task 3.2: the saving is only visible if parked cars are in the layer too."""
    activated, parked = conn.execute(
        """
        SELECT count(*) FILTER (WHERE activated),
               count(*) FILTER (WHERE NOT activated)
        FROM layer_fleet
        """
    ).fetchone()
    assert activated > 0
    assert parked > 0


def test_oa_summary_reconciles_with_the_plan(conn):
    """Task 3.3: the layer must agree with the reports it is drawn from."""
    layer_people, layer_seated, layer_cars = conn.execute(
        "SELECT sum(people), sum(people_seated), sum(cars_activated) FROM layer_oa_summary"
    ).fetchone()
    plan_people, plan_seated, plan_cars = conn.execute(
        """
        SELECT (SELECT count(*) FROM persons),
               (SELECT count(*) FROM person_seats),
               (SELECT count(*) FROM activated_vehicles)
        """
    ).fetchone()
    assert layer_people == plan_people
    assert layer_seated == plan_seated
    assert layer_cars == plan_cars


def test_oa_summary_covers_every_output_area(conn):
    """Every Output Area appears, including any with nobody left to carry."""
    layer, areas = conn.execute(
        """
        SELECT (SELECT count(*) FROM layer_oa_summary),
               (SELECT count(*) FROM admin_areas WHERE level = 'output_area')
        """
    ).fetchone()
    assert layer == areas


def test_walk_line_sample_is_a_subset(conn):
    """The sample exists to be readable, not to be different data."""
    sample_beyond_source = conn.execute(
        """
        SELECT count(*) FROM layer_walk_lines_sample s
        WHERE NOT EXISTS (
            SELECT 1 FROM layer_walk_lines f
            WHERE f.household_id = s.household_id
              AND f.muster_point_id = s.muster_point_id
        )
        """
    ).fetchone()[0]
    assert sample_beyond_source == 0


def test_unmet_layer_accounts_for_every_unseated_person(conn):
    """People left behind are all located, not just some of them."""
    layer_people, plan_people = conn.execute(
        """
        SELECT (SELECT sum(people) FROM layer_unmet),
               (SELECT sum(size) FROM unmet_demand)
        """
    ).fetchone()
    assert layer_people == plan_people


# --- schematic trip paths -------------------------------------------------


@pytest.fixture(scope="module")
def trips(conn):
    tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
    if not {"trips_walks", "trips_vehicles"} <= tables:
        pytest.skip("trip paths not materialised")
    return conn


def test_trip_paths_and_timestamps_are_the_same_length(trips):
    """TripsLayer pairs each waypoint with a timestamp; a mismatch renders wrong."""
    for table in ("trips_walks", "trips_vehicles"):
        mismatched = trips.execute(
            f"SELECT count(*) FROM {table} WHERE len(path) <> len(timestamps)"
        ).fetchone()[0]
        assert mismatched == 0, f"{table} has paths without matching timestamps"


def test_trip_timestamps_never_go_backwards(trips):
    """A vehicle reaches its stops in order."""
    backwards = trips.execute(
        """
        SELECT count(*) FROM trips_vehicles
        WHERE list_sort(timestamps) <> timestamps
        """
    ).fetchone()[0]
    assert backwards == 0


def test_every_vehicle_trip_ends_at_the_destination(trips):
    """The drive to the destination is appended; the plan itself omits it."""
    from london_frontline.config import PlanningConfig

    config = PlanningConfig()
    wrong = trips.execute(
        """
        SELECT count(*) FROM trips_vehicles
        WHERE abs(path[-1][1] - ?) > 1e-6 OR abs(path[-1][2] - ?) > 1e-6
        """,
        [config.destination_longitude, config.destination_latitude],
    ).fetchone()[0]
    assert wrong == 0


def test_trips_are_flagged_schematic(trips):
    """These illustrate the plan; nothing should read them as a simulation."""
    for table in ("trips_walks", "trips_vehicles"):
        unflagged = trips.execute(
            f"SELECT count(*) FROM {table} WHERE NOT schematic"
        ).fetchone()[0]
        assert unflagged == 0
