"""Muster-point invariants (task 6.6).

A muster point is exactly one parked vehicle — there is no site-level capacity
distinct from that vehicle's seats. These check that identity holds in the
materialised warehouse.
"""

import pytest

from london_frontline.config import PlanningConfig
from london_frontline.resources import SpatialDuckDBResource


@pytest.fixture(scope="module")
def conn():
    with SpatialDuckDBResource().connect() as connection:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        if not {"muster_points", "vehicles", "unresolved_parking"} <= tables:
            pytest.skip("warehouse not materialised; run the pipeline first")
        yield connection


def test_each_muster_point_is_exactly_one_vehicle(conn):
    """Task 6.6: the mapping between muster points and vehicles is one to one."""
    points, vehicles_referenced = conn.execute(
        "SELECT count(*), count(DISTINCT vehicle_id) FROM muster_points"
    ).fetchone()
    assert points == vehicles_referenced

    duplicated = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT muster_point_id FROM muster_points
            GROUP BY muster_point_id HAVING count(*) > 1
        )
        """
    ).fetchone()[0]
    assert duplicated == 0


def test_muster_point_capacity_equals_its_vehicle_capacity(conn):
    """Task 6.6: capacity is the vehicle's seats, never a site figure."""
    mismatched = conn.execute(
        """
        SELECT count(*) FROM muster_points m
        JOIN vehicles v USING (vehicle_id)
        WHERE m.capacity <> v.capacity
        """
    ).fetchone()[0]
    assert mismatched == 0


def test_every_vehicle_is_parked(conn):
    """No vehicle leaves the fleet for being far from a mapped road.

    OSM's coverage of residential access and service roads is incomplete, so
    distance from a mapped road is a property of the map, not of the dwelling.
    """
    total, parked, unresolved = conn.execute(
        """
        SELECT (SELECT count(*) FROM vehicles),
               (SELECT count(*) FROM muster_points),
               (SELECT count(*) FROM unresolved_parking)
        """
    ).fetchone()
    assert parked == total
    assert unresolved == 0


def test_snap_distance_is_reported_not_used_as_a_filter(conn):
    """Vehicles beyond the review threshold stay in the fleet.

    Regression guard: an earlier version treated this threshold as a cap and
    silently dropped 27% of the fleet.
    """
    threshold = PlanningConfig().parking_snap_review_distance_m
    beyond, distinct_vehicles = conn.execute(
        """
        SELECT count(*), count(DISTINCT vehicle_id)
        FROM muster_points WHERE snap_distance_m > ?
        """,
        [threshold],
    ).fetchone()
    assert beyond > 0, "expected some homes further than the review threshold"
    assert beyond == distinct_vehicles
