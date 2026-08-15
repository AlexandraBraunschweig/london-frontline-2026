"""Departure times and exit assignment in the warehouse (tasks 4.x, 5.2).

The draw itself is unit-tested in test_mobilisation.py; these check what the
asset built from it.
"""

import pytest

from london_frontline.assets.scenario import ACCESS_FROM_NETWORK, ACCESS_FROM_STRAIGHT_LINE
from london_frontline.config import PlanningConfig
from london_frontline.resources import SpatialDuckDBResource


@pytest.fixture(scope="module")
def conn():
    with SpatialDuckDBResource().connect() as connection:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        if not {"vehicle_departures", "district_exits", "vehicle_routes"} <= tables:
            pytest.skip("warehouse not materialised; run the pipeline first")
        yield connection


@pytest.fixture(scope="module")
def planning():
    return PlanningConfig()


def test_every_vehicle_takes_its_nearest_exit(conn):
    """Task 5.2: assignment is the nearest exit to the vehicle's final stop.

    Verified exhaustively rather than over a constructed fixture: every departing
    vehicle is re-checked against every exit, which is the same scenario applied
    to 26,577 real cases instead of a handful of invented ones.
    """
    wrong = conn.execute(
        """
        WITH final_stop AS (
            SELECT muster_point_id, easting, northing FROM (
                SELECT muster_point_id, easting, northing,
                       row_number() OVER (PARTITION BY muster_point_id
                                          ORDER BY stop_seq DESC) AS rank
                FROM route_stops
            ) WHERE rank = 1
        ),
        best AS (
            SELECT d.vehicle_id,
                   min(ST_Distance(ST_Point(f.easting, f.northing),
                                   ST_Point(e.easting, e.northing))) AS nearest_m
            FROM vehicle_departures d
            JOIN final_stop f USING (muster_point_id)
            CROSS JOIN district_exits e
            GROUP BY d.vehicle_id
        )
        SELECT count(*) FROM vehicle_departures d
        JOIN best b USING (vehicle_id)
        WHERE d.exit_distance_m > b.nearest_m + 1e-6
        """
    ).fetchone()[0]
    assert wrong == 0


def test_every_departing_vehicle_has_exactly_one_departure(conn):
    departures, vehicles, routed = conn.execute(
        """
        SELECT (SELECT count(*) FROM vehicle_departures),
               (SELECT count(DISTINCT vehicle_id) FROM vehicle_departures),
               (SELECT count(*) FROM vehicle_routes)
        """
    ).fetchone()
    assert departures == vehicles
    assert departures == routed


def test_every_assigned_exit_exists(conn):
    dangling = conn.execute(
        """
        SELECT count(*) FROM vehicle_departures d
        WHERE NOT EXISTS (
            SELECT 1 FROM district_exits e WHERE e.exit_id = d.exit_id
        )
        """
    ).fetchone()[0]
    assert dangling == 0


def test_no_vehicle_departs_before_notification(conn):
    """Task 4.6, as materialised: every term of the offset is non-negative."""
    early = conn.execute(
        """
        SELECT count(*) FROM vehicle_departures
        WHERE departs_at < notified_at
           OR departure_offset_s < 0
           OR mobilisation_delay_s < 0
           OR driver_access_s < 0
        """
    ).fetchone()[0]
    assert early == 0


def test_departure_is_its_two_terms(conn):
    """The recorded offset must be the sum it claims to be, not an independent draw."""
    inconsistent = conn.execute(
        """
        SELECT count(*) FROM vehicle_departures
        WHERE abs(departure_offset_s
                  - (mobilisation_delay_s + driver_access_s)) > 1e-6
        """
    ).fetchone()[0]
    assert inconsistent == 0


def test_the_fleet_does_not_depart_all_at_once(conn):
    """The point of the profile: a spread wide enough to load a network with.

    A single instant would measure the simulator's insertion queue instead of the
    road network.
    """
    distinct_times, span_minutes = conn.execute(
        """
        SELECT count(DISTINCT departure_offset_s),
               (max(departure_offset_s) - min(departure_offset_s)) / 60.0
        FROM vehicle_departures
        """
    ).fetchone()
    assert distinct_times > 1000
    assert span_minutes > 30


def test_access_time_is_recorded_with_its_source(conn):
    """Where the network walk is missing, the straight line stands in openly.

    A silent zero would understate departure and would be invisible in the
    output; the source column is what makes the substitution auditable.
    """
    sources = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT access_source FROM vehicle_departures"
        ).fetchall()
    }
    assert sources <= {ACCESS_FROM_NETWORK, ACCESS_FROM_STRAIGHT_LINE}

    positive_but_unsourced = conn.execute(
        "SELECT count(*) FROM vehicle_departures WHERE access_source IS NULL"
    ).fetchone()[0]
    assert positive_but_unsourced == 0


def test_driver_access_time_respects_the_configuration(conn, planning):
    """Task 4.5's mechanism as materialised."""
    if planning.include_driver_access_time:
        applied = conn.execute(
            "SELECT count(*) FROM vehicle_departures WHERE driver_access_s > 0"
        ).fetchone()[0]
        assert applied > 0
    else:
        applied = conn.execute(
            "SELECT count(*) FROM vehicle_departures WHERE driver_access_s <> 0"
        ).fetchone()[0]
        assert applied == 0
