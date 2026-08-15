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


<<<<<<< Updated upstream
# How far the interpolated point may sit from the recorded one. Linear
# referencing round-trips through a fraction of the way's length, so the error is
# floating-point noise scaled by that length, not a modelling tolerance.
_INTERPOLATION_TOLERANCE_M = 0.01


=======
>>>>>>> Stashed changes
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
<<<<<<< Updated upstream


def test_way_reference_is_populated_and_resolvable(conn):
    """Task 1.6: every muster point names a way that exists in the road table.

    The way reference is what lets a downstream microsimulation bind a parked car
    to a network link by lookup. A null or dangling reference would silently push
    that car back onto nearest-link guessing.
    """
    missing, dangling = conn.execute(
        """
        SELECT
          (SELECT count(*) FROM muster_points
            WHERE way_id IS NULL OR way_position_m IS NULL),
          (SELECT count(*) FROM muster_points m
            WHERE NOT EXISTS (
              SELECT 1 FROM road_centrelines r WHERE r.way_id = m.way_id
            ))
        """
    ).fetchone()
    assert missing == 0
    assert dangling == 0


def test_way_position_interpolates_back_to_the_parking_point(conn):
    """Task 1.5: the recorded position indexes into the recorded way correctly.

    Position is measured along the whole way, while the snap matched a two-point
    segment of it, so this is the check that the two agree.

    Checked over the whole fleet rather than a sample: it costs about 10 ms, and
    sampling would make a failure depend on the draw.
    """
    worst, checked = conn.execute(
        """
        SELECT max(error_m), count(*) FROM (
            SELECT ST_Distance(
                       ST_Point(m.easting, m.northing),
                       ST_LineInterpolatePoint(
                           r.geometry,
                           m.way_position_m / ST_Length(r.geometry)
                       )
                   ) AS error_m
            FROM muster_points m
            JOIN road_centrelines r USING (way_id)
        )
        """
    ).fetchone()
    assert checked > 0
    assert worst <= _INTERPOLATION_TOLERANCE_M, (
        f"worst interpolation error {worst:.4g} m over {checked:,} "
        f"muster points exceeds {_INTERPOLATION_TOLERANCE_M} m"
    )
=======
>>>>>>> Stashed changes
