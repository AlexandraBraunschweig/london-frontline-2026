"""Dependency-rule tests (tasks 4.5 and 4.6)."""

from london_frontline.relationships import (
    Member,
    co_resident_parents,
    household_dependency_edges,
    travel_groups,
)

MIN_GAP = 16
BOTH_PARENTS_BELOW = 10
ONE_PARENT_BELOW = 16


def edges_for(members):
    return household_dependency_edges(
        members, MIN_GAP, BOTH_PARENTS_BELOW, ONE_PARENT_BELOW
    )


def groups_for(members):
    edges, _ = edges_for(members)
    return travel_groups([m.person_id for m in members], edges)


def test_child_under_ten_travels_with_both_parents():
    """Task 4.5: an under-10 with two qualifying adults shares their travel group."""
    child = Member(1, 6)
    mother = Member(2, 34)
    father = Member(3, 37)
    members = [child, mother, father]

    edges, orphaned = edges_for(members)
    assert orphaned == []
    assert set(edges) == {(1, 2), (1, 3)}

    assignment = groups_for(members)
    assert assignment[1] == assignment[2] == assignment[3]


def test_child_under_ten_with_one_parent_present():
    """Only one qualifying adult means one edge, not a failure."""
    members = [Member(1, 4), Member(2, 29)]
    edges, orphaned = edges_for(members)
    assert edges == [(1, 2)]
    assert orphaned == []


def test_child_ten_to_fifteen_needs_only_one_parent():
    """A 10-15 year old is bound to one parent even when two qualify."""
    members = [Member(1, 12), Member(2, 40), Member(3, 44)]
    edges, _ = edges_for(members)
    assert len(edges) == 1
    # The oldest qualifying member is chosen.
    assert edges[0] == (1, 3)

    assignment = groups_for(members)
    assert assignment[1] == assignment[3]


def test_person_sixteen_or_over_is_not_bound_to_household():
    """Task 4.6: a 16+ member carries no mandatory edge and forms its own group."""
    child = Member(1, 7)
    parent_one = Member(2, 38)
    parent_two = Member(3, 41)
    adult_sibling = Member(4, 19)
    members = [child, parent_one, parent_two, adult_sibling]

    edges, _ = edges_for(members)
    assert all(person != 4 for person, _ in edges)

    assignment = groups_for(members)
    assert assignment[4] != assignment[1]
    assert assignment[1] == assignment[2] == assignment[3]


def test_child_with_no_qualifying_adult_is_recorded():
    """A household with no member old enough is recorded, not given a parent."""
    members = [Member(1, 8), Member(2, 20)]
    edges, orphaned = edges_for(members)
    assert edges == []
    assert orphaned == [1]

    # The child still exists, in a group of its own.
    assignment = groups_for(members)
    assert assignment[1] != assignment[2]


def test_co_resident_parents_capped_at_two_oldest():
    """Three qualifying adults yield the two oldest, deterministically."""
    child = Member(1, 5)
    members = [child, Member(2, 30), Member(3, 62), Member(4, 45)]
    assert co_resident_parents(members, child, MIN_GAP) == [3, 4]


def test_siblings_share_one_group_with_their_parents():
    """Two children of the same parents form a single indivisible group."""
    members = [Member(1, 3), Member(2, 8), Member(3, 35), Member(4, 36)]
    assignment = groups_for(members)
    assert len({assignment[i] for i in (1, 2, 3, 4)}) == 1
