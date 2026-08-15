"""Collapsing boundary crossings into corridors (tasks 3.10, 3.11).

Pure, so these run without a warehouse.
"""

import pytest

from london_frontline.exits import Crossing, cluster_crossings

# Most major first, as the config list is.
PRIORITY = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified"]
RADIUS = 500.0


def crossing(crossing_id, easting, highway="tertiary", way_id=None, outside=100.0):
    return Crossing(
        crossing_id=crossing_id,
        way_id=way_id if way_id is not None else crossing_id,
        highway=highway,
        easting=easting,
        northing=0.0,
        length_outside_m=outside,
    )


def test_crossings_within_the_radius_collapse_to_one_exit():
    """Task 3.10: one corridor, one destination."""
    exits = cluster_crossings(
        [crossing(0, 0.0), crossing(1, 100.0), crossing(2, 250.0)], RADIUS, PRIORITY
    )
    assert len(exits) == 1
    assert exits[0].crossing_count == 3


def test_crossings_beyond_the_radius_stay_separate():
    """Task 3.10: genuinely distinct ways out are not merged."""
    exits = cluster_crossings([crossing(0, 0.0), crossing(1, 900.0)], RADIUS, PRIORITY)
    assert len(exits) == 2
    assert all(exit_.crossing_count == 1 for exit_ in exits)


def test_linkage_is_transitive():
    """Task 3.10: a chain of near crossings is one corridor, not three.

    Ends are 800 m apart, beyond the radius, but each step is within it.
    """
    exits = cluster_crossings(
        [crossing(0, 0.0), crossing(1, 400.0), crossing(2, 800.0)], RADIUS, PRIORITY
    )
    assert len(exits) == 1
    assert exits[0].crossing_count == 3


def test_a_gap_wider_than_the_radius_breaks_the_chain():
    exits = cluster_crossings(
        [crossing(0, 0.0), crossing(1, 400.0), crossing(2, 1200.0), crossing(3, 1500.0)],
        RADIUS,
        PRIORITY,
    )
    assert sorted(exit_.crossing_count for exit_ in exits) == [2, 2]


def test_exactly_on_the_radius_merges():
    exits = cluster_crossings([crossing(0, 0.0), crossing(1, RADIUS)], RADIUS, PRIORITY)
    assert len(exits) == 1


def test_the_most_major_class_represents_the_cluster():
    """Task 3.11: the corridor is entered by its largest road.

    This is the Ramsgate Road case in miniature: the tertiary crossing is nearer
    the middle of the cluster, but the trunk crossing is the one traffic should
    be directed onto.
    """
    exits = cluster_crossings(
        [
            crossing(0, 0.0, highway="tertiary"),
            crossing(1, 100.0, highway="trunk"),
            crossing(2, 200.0, highway="unclassified"),
        ],
        RADIUS,
        PRIORITY,
    )
    assert len(exits) == 1
    assert exits[0].representative.highway == "trunk"
    assert exits[0].representative.crossing_id == 1


def test_the_representative_is_always_a_real_crossing():
    """Task 3.11: never a centroid — an exit off any way has no way reference."""
    members = [crossing(0, 0.0, highway="trunk"), crossing(1, 300.0, highway="trunk")]
    exits = cluster_crossings(members, RADIUS, PRIORITY)
    assert exits[0].representative in exits[0].members
    assert exits[0].representative.easting in {0.0, 300.0}


def test_ties_on_class_fall_to_the_longest_continuation():
    """A road running further beyond the boundary is the more convincing exit."""
    exits = cluster_crossings(
        [
            crossing(0, 0.0, highway="trunk", outside=100.0),
            crossing(1, 100.0, highway="trunk", outside=2500.0),
        ],
        RADIUS,
        PRIORITY,
    )
    assert exits[0].representative.crossing_id == 1


def test_clustering_does_not_depend_on_input_order():
    members = [
        crossing(0, 0.0, highway="tertiary"),
        crossing(1, 100.0, highway="trunk"),
        crossing(2, 5000.0, highway="primary"),
    ]
    straight = cluster_crossings(members, RADIUS, PRIORITY)
    reversed_ = cluster_crossings(list(reversed(members)), RADIUS, PRIORITY)

    assert [e.representative.crossing_id for e in straight] == [
        e.representative.crossing_id for e in reversed_
    ]


def test_no_crossings_yields_no_exits():
    assert cluster_crossings([], RADIUS, PRIORITY) == []


def test_every_crossing_belongs_to_exactly_one_exit():
    """Nothing is dropped and nothing is double counted."""
    members = [crossing(n, n * 300.0) for n in range(10)]
    exits = cluster_crossings(members, RADIUS, PRIORITY)
    seen = [m.crossing_id for exit_ in exits for m in exit_.members]
    assert sorted(seen) == [m.crossing_id for m in members]


def test_an_unranked_road_class_is_rejected():
    """A class that qualified but cannot be ranked would pick a silent winner."""
    with pytest.raises(ValueError, match="priority order"):
        cluster_crossings([crossing(0, 0.0, highway="service")], RADIUS, PRIORITY)


def test_negative_radius_is_rejected():
    with pytest.raises(ValueError):
        cluster_crossings([crossing(0, 0.0)], -1.0, PRIORITY)


def test_a_district_with_no_exit_is_refused_by_name():
    """Task 7.4: an empty destination set must fail loudly, naming the district.

    Otherwise it surfaces as an unexplained empty export rather than as the
    modelling problem it is.
    """
    from london_frontline.exits import require_exits

    with pytest.raises(ValueError) as raised:
        require_exits(0, "E07000114")
    message = str(raised.value)
    assert "E07000114" in message
    assert "district_exits" in message


def test_a_district_with_exits_passes():
    from london_frontline.exits import require_exits

    require_exits(1, "E07000114")
