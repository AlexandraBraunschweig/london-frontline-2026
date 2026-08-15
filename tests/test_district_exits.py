"""Exit qualification (tasks 3.4, 3.5, 3.6).

Thanet is a peninsula, so the real exits are few and nameable. That makes the
qualification rule checkable against the actual district rather than only
against its own output.
"""

import pytest

from london_frontline.assets.geography import LEVEL_LAD
from london_frontline.config import PlanningConfig
from london_frontline.resources import SpatialDuckDBResource

# The through routes out of Thanet, by name. Ramsgate Road appears three times:
# two trunk ways and one tertiary way, the last crossing the boundary twice.
EXPECTED_QUALIFYING_NAMES = {
    "Island Road",
    "Thanet Way",
    "Ramsgate Road",
    "Old Road",
    "Plucks Gutter",
}

# Crosses the boundary at Ramsgate harbour, where the boundary is coastline, and
# continues 29 m and 8 m beyond it. A trunk road that never leaves the district.
COASTAL_ARTIFACT_NAME = "Royal Harbour Approach"


@pytest.fixture(scope="module")
def conn():
    with SpatialDuckDBResource().connect() as connection:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        required = {"district_exits", "district_boundary_crossings",
                    "road_centrelines", "admin_areas"}
        if not required <= tables:
            pytest.skip("warehouse not materialised; run the pipeline first")
        yield connection


@pytest.fixture(scope="module")
def planning():
    return PlanningConfig()


@pytest.fixture(scope="module")
def crossings(conn):
    """Every way crossing the district boundary, with how far it continues past it.

    This is the ground truth the qualification rule is checked against.
    """
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW boundary_crossings AS
        WITH district AS (
            SELECT ST_Transform(geometry, 'EPSG:4326', 'EPSG:27700',
                                always_xy := true) AS area
            FROM admin_areas WHERE level = '{LEVEL_LAD}'
        )
        SELECT r.way_id, r.highway, r.name,
               ST_Length(ST_Difference(r.geometry, d.area)) AS length_outside_m
        FROM road_centrelines r, district d
        WHERE ST_Intersects(r.geometry, ST_Boundary(d.area))
        """
    )
    return conn


def test_the_expected_through_routes_qualify(crossings):
    """Task 3.4: the real ways out of Thanet all survive qualification.

    Checked before clustering: qualification decides what counts as a way out,
    clustering decides how many destinations those ways amount to.
    """
    names = {
        row[0]
        for row in crossings.execute(
            "SELECT DISTINCT name FROM district_boundary_crossings "
            "WHERE name IS NOT NULL"
        ).fetchall()
    }
    assert names == EXPECTED_QUALIFYING_NAMES


def test_a_way_crossing_twice_yields_one_exit(crossings):
    """Task 3.10: repeated crossings of one corridor are a single destination."""
    way_crossing_twice = crossings.execute(
        """
        SELECT count(*) FROM (
            SELECT way_id FROM district_boundary_crossings
            GROUP BY way_id HAVING count(*) > 1
        )
        """
    ).fetchone()[0]
    assert way_crossing_twice > 0, "expected a way to cross the boundary twice"

    crossing_total, exit_total = crossings.execute(
        """
        SELECT (SELECT count(*) FROM district_boundary_crossings),
               (SELECT count(*) FROM district_exits)
        """
    ).fetchone()
    assert exit_total < crossing_total

    duplicated = crossings.execute(
        "SELECT count(*) FROM (SELECT easting, northing FROM district_exits "
        "GROUP BY 1, 2 HAVING count(*) > 1)"
    ).fetchone()[0]
    assert duplicated == 0


def test_the_ramsgate_corridor_collapses_to_one_trunk_exit(crossings):
    """Task 3.12: the case that motivated clustering.

    Four crossings within 292 m — two trunk ways and a tertiary way crossing
    twice. Assigned per crossing, the tertiary one took 90% of the fleet while a
    trunk crossing 250 m away took seven vehicles.
    """
    rows = crossings.execute(
        """
        SELECT highway, crossing_count FROM district_exits
        WHERE name = 'Ramsgate Road'
        """
    ).fetchall()
    assert len(rows) == 1, "the Ramsgate Road crossings should be one exit"
    highway, crossing_count = rows[0]
    assert crossing_count == 4
    assert highway == "trunk", "the corridor must be entered by its largest road"


def test_every_qualifying_crossing_belongs_to_exactly_one_exit(crossings):
    """Task 3.10: clustering partitions the crossings — none lost, none doubled."""
    crossing_total = crossings.execute(
        "SELECT count(*) FROM district_boundary_crossings"
    ).fetchone()[0]
    claimed = crossings.execute(
        "SELECT sum(crossing_count) FROM district_exits"
    ).fetchone()[0]
    assert claimed == crossing_total


def test_every_exit_is_one_of_the_crossings_it_represents(crossings):
    """Task 3.11: a representative is a real crossing, never a centroid.

    A centroid would sit off every way, and the exit would lose the way reference
    the whole export is keyed on.
    """
    off_network = crossings.execute(
        """
        SELECT count(*) FROM district_exits e
        WHERE NOT EXISTS (
            SELECT 1 FROM district_boundary_crossings c
            WHERE c.way_id = e.way_id
              AND c.easting = e.easting AND c.northing = e.northing
        )
        """
    ).fetchone()[0]
    assert off_network == 0


def test_the_coastal_artifact_is_rejected(crossings):
    """Task 3.4: a trunk road grazing a coastline boundary is not an exit.

    Without the length test this would be admitted purely for being trunk class,
    and would put an evacuation destination inside Ramsgate harbour.
    """
    crossed, admitted = crossings.execute(
        """
        SELECT (SELECT count(*) FROM boundary_crossings WHERE name = ?),
               (SELECT count(*) FROM district_boundary_crossings WHERE name = ?)
        """,
        [COASTAL_ARTIFACT_NAME, COASTAL_ARTIFACT_NAME],
    ).fetchone()
    assert crossed > 0, f"{COASTAL_ARTIFACT_NAME} should still cross the boundary"
    assert admitted == 0


def test_service_crossings_are_rejected_however_far_they_continue(crossings):
    """Task 3.5: class is a veto, independent of length beyond the boundary."""
    service_crossings = crossings.execute(
        "SELECT count(*) FROM boundary_crossings WHERE highway = 'service'"
    ).fetchone()[0]
    assert service_crossings > 0, "expected service roads to cross the boundary"

    admitted = crossings.execute(
        "SELECT count(*) FROM district_boundary_crossings WHERE highway = 'service'"
    ).fetchone()[0]
    assert admitted == 0


def test_no_exit_falls_short_of_the_configured_length(crossings, planning):
    """Task 3.6: the length threshold holds over every admitted exit."""
    short = crossings.execute(
        "SELECT count(*) FROM district_boundary_crossings WHERE length_outside_m < ?",
        [planning.minimum_exit_length_outside_m],
    ).fetchone()[0]
    assert short == 0


def test_qualification_admits_exactly_what_the_rule_describes(crossings, planning):
    """Task 3.6: round-trip the rule — nothing qualifying is dropped, and nothing
    else is admitted.

    Checked against the road table rather than against district_exits alone, so
    the test would catch the rule being quietly narrowed as well as widened.
    """
    classes = list(planning.exit_highway_classes)
    placeholders = ", ".join("?" for _ in classes)

    missing, extra = crossings.execute(
        f"""
        SELECT
          (SELECT count(*) FROM boundary_crossings c
            WHERE c.highway IN ({placeholders})
              AND c.length_outside_m >= ?
              AND NOT EXISTS (
                SELECT 1 FROM district_boundary_crossings q WHERE q.way_id = c.way_id
              )),
          (SELECT count(DISTINCT q.way_id) FROM district_boundary_crossings q
            WHERE NOT EXISTS (
                SELECT 1 FROM boundary_crossings c
                 WHERE c.way_id = q.way_id
                   AND c.highway IN ({placeholders})
                   AND c.length_outside_m >= ?
              ))
        """,
        [*classes, planning.minimum_exit_length_outside_m,
         *classes, planning.minimum_exit_length_outside_m],
    ).fetchone()
    assert missing == 0, f"{missing} qualifying crossings were not admitted"
    assert extra == 0, f"{extra} admitted exits do not satisfy the rule"


def test_every_exit_carries_a_resolvable_way_reference(crossings):
    """An exit binds to the network the same way a muster point does."""
    bad = crossings.execute(
        """
        SELECT count(*) FROM district_exits e
        WHERE e.way_id IS NULL OR e.way_position_m IS NULL
           OR NOT EXISTS (
             SELECT 1 FROM road_centrelines r WHERE r.way_id = e.way_id
           )
        """
    ).fetchone()[0]
    assert bad == 0


def test_exit_position_interpolates_back_to_the_crossing(crossings):
    """The recorded position indexes into the recorded way, as for muster points."""
    worst = crossings.execute(
        """
        SELECT max(ST_Distance(
                   ST_Point(e.easting, e.northing),
                   ST_LineInterpolatePoint(
                       r.geometry, e.way_position_m / ST_Length(r.geometry))))
        FROM district_exits e JOIN road_centrelines r USING (way_id)
        """
    ).fetchone()[0]
    assert worst <= 0.01
