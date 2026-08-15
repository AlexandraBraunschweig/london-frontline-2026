"""Home-location assignment tests (tasks 5.6 and 5.7).

These read the materialised warehouse rather than a fixture, because what they
assert is a property of the assignment over real NSUL data. They skip when the
pipeline has not been run.
"""

import pytest

from london_frontline.resources import SpatialDuckDBResource


@pytest.fixture(scope="module")
def conn():
    resource = SpatialDuckDBResource()
    with resource.connect() as connection:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        if not {"household_locations", "dwelling_points", "households"} <= tables:
            pytest.skip("warehouse not materialised; run the pipeline first")
        yield connection


def test_assigned_uprn_is_in_the_households_output_area(conn):
    """Task 5.6: a household's UPRN must carry that household's own OA code."""
    mismatched = conn.execute(
        """
        SELECT count(*)
        FROM household_locations l
        JOIN households h USING (household_id)
        JOIN dwelling_points d ON d.uprn = l.uprn
        WHERE d.area_id <> h.area_id
        """
    ).fetchone()[0]
    assert mismatched == 0


def test_no_uprn_serves_two_households(conn):
    """Task 5.7: one household per dwelling point."""
    duplicated = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT uprn FROM household_locations GROUP BY uprn HAVING count(*) > 1
        )
        """
    ).fetchone()[0]
    assert duplicated == 0


def test_every_household_is_placed_or_recorded_as_shortfall(conn):
    """Households are either given a location or counted in the shortfall."""
    households, placed, unplaced = conn.execute(
        """
        SELECT (SELECT count(*) FROM households),
               (SELECT count(*) FROM household_locations),
               (SELECT coalesce(sum(greatest(unplaced_households, 0)), 0)
                  FROM home_location_shortfall)
        """
    ).fetchone()
    assert placed + unplaced == households
