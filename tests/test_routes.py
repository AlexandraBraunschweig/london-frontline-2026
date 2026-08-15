"""Route-output tests (task 9.6)."""

import pytest

from london_frontline.assets.routes import (
    NOTIFY_CARER,
    NOTIFY_DIRECT,
    NOTIFY_PHYSICAL,
    STOP_COLLECTION,
    STOP_MUSTER,
)
from london_frontline.resources import SpatialDuckDBResource


@pytest.fixture(scope="module")
def conn():
    with SpatialDuckDBResource().connect() as connection:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        if not {"vehicle_routes", "route_stops", "stop_notifications"} <= tables:
            pytest.skip("warehouse not materialised; run the pipeline first")
        yield connection


def test_walk_in_only_vehicle_has_a_single_stop(conn):
    """Task 9.6: with no home-collection occupants, the route is one stop."""
    wrong = conn.execute(
        """
        SELECT count(*) FROM vehicle_routes
        WHERE collection_stops = 0 AND stop_count <> 1
        """
    ).fetchone()[0]
    assert wrong == 0


def test_every_route_starts_at_its_muster_point(conn):
    """The vehicle is parked at its muster point, so it departs from there."""
    wrong = conn.execute(
        f"""
        SELECT count(*) FROM route_stops
        WHERE stop_seq = 1 AND stop_type <> '{STOP_MUSTER}'
        """
    ).fetchone()[0]
    assert wrong == 0

    duplicated = conn.execute(
        f"""
        SELECT count(*) FROM (
            SELECT muster_point_id FROM route_stops
            WHERE stop_type = '{STOP_MUSTER}'
            GROUP BY muster_point_id HAVING count(*) > 1
        )
        """
    ).fetchone()[0]
    assert duplicated == 0


def test_collection_stops_are_ordered_outward(conn):
    """Stops run in increasing distance from the muster point."""
    out_of_order = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT distance_from_muster_m,
                   lag(distance_from_muster_m) OVER (
                       PARTITION BY muster_point_id ORDER BY stop_seq
                   ) AS previous
            FROM route_stops
        ) WHERE previous IS NOT NULL AND distance_from_muster_m < previous
        """
    ).fetchone()[0]
    assert out_of_order == 0


def test_meeting_times_never_go_backwards(conn):
    """Later stops are met later; the fleet departs once."""
    backwards = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT meeting_time,
                   lag(meeting_time) OVER (
                       PARTITION BY muster_point_id ORDER BY stop_seq
                   ) AS previous
            FROM route_stops
        ) WHERE previous IS NOT NULL AND meeting_time < previous
        """
    ).fetchone()[0]
    assert backwards == 0


def test_every_route_has_a_car_licensed_leader(conn):
    """A vehicle nobody can drive should never have reached the route stage."""
    unlicensed = conn.execute(
        """
        SELECT count(*) FROM vehicle_routes r
        JOIN persons p ON p.person_id = r.leader_person_id
        WHERE p.license_type <> 'car'
        """
    ).fetchone()[0]
    assert unlicensed == 0


def test_notification_mode_matches_available_contact_details(conn):
    """Direct when there is a phone, carer when there is a carer, else physical."""
    wrong = conn.execute(
        f"""
        SELECT count(*) FROM stop_notifications
        WHERE (phone_number IS NOT NULL AND notification_mode <> '{NOTIFY_DIRECT}')
           OR (phone_number IS NULL AND carer_person_id IS NOT NULL
               AND notification_mode <> '{NOTIFY_CARER}')
           OR (phone_number IS NULL AND carer_person_id IS NULL
               AND notification_mode <> '{NOTIFY_PHYSICAL}')
        """
    ).fetchone()[0]
    assert wrong == 0


def test_home_collection_people_are_expected_at_their_own_address(conn):
    """A collected person is met at their household, not at the muster point."""
    misplaced = conn.execute(
        f"""
        SELECT count(*)
        FROM stop_notifications n
        JOIN route_stops s
          ON s.muster_point_id = n.muster_point_id AND s.stop_seq = n.stop_seq
        JOIN persons p ON p.person_id = n.person_id
        WHERE n.stop_type = '{STOP_COLLECTION}' AND s.household_id <> p.household_id
        """
    ).fetchone()[0]
    assert misplaced == 0
