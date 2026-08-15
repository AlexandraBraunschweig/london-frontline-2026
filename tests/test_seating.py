"""Seat-assignment tests (tasks 8.10, 8.11 and 8.12)."""

from london_frontline.seating import (
    REASON_CANDIDATES_FULL,
    REASON_NO_DRIVER,
    TIER_HOME_COLLECTION,
    TIER_OWNER,
    TIER_WALK_IN_DEPENDENT,
    TIER_WALK_IN_INDEPENDENT,
    Group,
    Vehicle,
    assign_seats,
    enforce_driver_availability,
    expand_to_persons,
)

MAX_STOPS = 3


def run(groups, vehicles, walk=None, collection=None, max_stops=MAX_STOPS):
    return assign_seats(groups, vehicles, walk or {}, collection or {}, max_stops)


def test_owner_household_rides_in_its_own_car():
    """Tier 1: the car is parked at their door, so they board it first."""
    owner = Group(1, household_id=10, area_id="A", size=2,
                  contains_dependent=True, all_can_walk=True)
    vehicle = Vehicle(muster_point_id=100, owner_household_id=10, capacity=5)

    seatings, unseated = run([owner], [vehicle])
    assert unseated == []
    assert seatings[0].muster_point_id == 100
    assert seatings[0].tier == TIER_OWNER


def test_class_a_is_seated_before_class_b():
    """Task 8.10: a dependent-bearing group outranks independent adults."""
    class_a = Group(1, household_id=10, area_id="A", size=3,
                    contains_dependent=True, all_can_walk=True)
    class_b = Group(2, household_id=11, area_id="A", size=3,
                    contains_dependent=False, all_can_walk=True)
    # One neighbour's car, owned by neither household, with room for one group.
    vehicle = Vehicle(muster_point_id=100, owner_household_id=99, capacity=5)
    walk = {10: [(100, 50.0)], 11: [(100, 40.0)]}

    seatings, unseated = run([class_b, class_a], [vehicle], walk=walk)

    seated_group_ids = {seating.group_id for seating in seatings}
    assert seated_group_ids == {1}, "Class A should take the seats"
    assert [u.group_id for u in unseated] == [2]
    assert unseated[0].reason == REASON_CANDIDATES_FULL
    # Class B is nearer, so proximity did not decide this — tier did.
    assert walk[11][0][1] < walk[10][0][1]


def test_group_with_a_non_walker_is_always_home_collection():
    """Task 8.11: one non-walking member routes the whole group to collection."""
    mixed = Group(1, household_id=10, area_id="A", size=3,
                  contains_dependent=True, all_can_walk=False)
    vehicle = Vehicle(muster_point_id=100, owner_household_id=99, capacity=5)

    # Reachable on foot and by road; the walk route must not be taken.
    seatings, unseated = run(
        [mixed], [vehicle], walk={10: [(100, 10.0)]}, collection={10: [(100, 10.0)]}
    )
    assert unseated == []
    assert seatings[0].tier == TIER_HOME_COLLECTION
    assert seatings[0].tier not in (TIER_WALK_IN_DEPENDENT, TIER_WALK_IN_INDEPENDENT)


def test_home_collection_groups_spread_across_vehicles():
    """Task 8.12: unavoidable collection stops fan out rather than stack up."""
    first = Group(1, household_id=10, area_id="A", size=1,
                  contains_dependent=False, all_can_walk=False)
    second = Group(2, household_id=11, area_id="A", size=1,
                   contains_dependent=False, all_can_walk=False)
    vehicles = [
        Vehicle(muster_point_id=100, owner_household_id=98, capacity=5),
        Vehicle(muster_point_id=101, owner_household_id=99, capacity=5),
    ]
    # Both groups prefer vehicle 100 on distance; spreading must override that.
    collection = {
        10: [(100, 10.0), (101, 20.0)],
        11: [(100, 11.0), (101, 21.0)],
    }

    seatings, unseated = run(
        [first, second], vehicles, collection=collection
    )
    assert unseated == []
    assert len({seating.muster_point_id for seating in seatings}) == 2


def test_collection_stops_per_vehicle_are_capped():
    """A vehicle stops at most max_stops times, even with seats to spare."""
    groups = [
        Group(i, household_id=10 + i, area_id="A", size=1,
              contains_dependent=False, all_can_walk=False)
        for i in range(4)
    ]
    vehicle = Vehicle(muster_point_id=100, owner_household_id=99, capacity=10)
    collection = {group.household_id: [(100, 10.0)] for group in groups}

    seatings, unseated = run(groups, [vehicle], collection=collection, max_stops=2)
    assert len(seatings) == 2
    assert len(unseated) == 2


def test_oversized_group_splits_across_fewest_vehicles():
    """A group larger than any single car is split, not abandoned."""
    big = Group(1, household_id=10, area_id="A", size=7,
                contains_dependent=True, all_can_walk=True)
    vehicles = [
        Vehicle(muster_point_id=100, owner_household_id=98, capacity=5),
        Vehicle(muster_point_id=101, owner_household_id=99, capacity=5),
    ]
    walk = {10: [(100, 10.0), (101, 20.0)]}

    seatings, unseated = run([big], vehicles, walk=walk)
    assert unseated == []
    assert sum(seating.seats for seating in seatings) == 7
    assert len(seatings) == 2
    assert all(seating.split for seating in seatings)


def test_group_that_merely_does_not_fit_is_not_split():
    """Occupancy is not a reason to split a family; only capacity is."""
    big = Group(1, household_id=10, area_id="A", size=4,
                contains_dependent=True, all_can_walk=True)
    # Capacity 5 exceeds the group, so it must never be split — but only 2 seats
    # remain once the owner household boards.
    owner = Group(2, household_id=99, area_id="A", size=3,
                  contains_dependent=True, all_can_walk=True)
    vehicle = Vehicle(muster_point_id=100, owner_household_id=99, capacity=5)
    walk = {10: [(100, 10.0)]}

    seatings, unseated = run([owner, big], [vehicle], walk=walk)
    assert [u.group_id for u in unseated] == [1]
    assert all(not seating.split for seating in seatings)


def test_vehicle_without_a_licensed_driver_does_not_depart():
    """A car nobody can drive is not a seat; its occupants become unmet demand."""
    group = Group(1, household_id=10, area_id="A", size=2,
                  contains_dependent=False, all_can_walk=True)
    vehicle = Vehicle(muster_point_id=100, owner_household_id=10, capacity=5)

    seatings, _ = run([group], [vehicle])
    people = expand_to_persons(seatings, {1: [501, 502]})
    kept, dropped = enforce_driver_availability(people, [group], licensed_person_ids=set())
    assert kept == []
    assert [u.reason for u in dropped] == [REASON_NO_DRIVER]


def test_vehicle_with_a_licensed_driver_departs():
    group = Group(1, household_id=10, area_id="A", size=2,
                  contains_dependent=False, all_can_walk=True)
    vehicle = Vehicle(muster_point_id=100, owner_household_id=10, capacity=5)

    seatings, _ = run([group], [vehicle])
    people = expand_to_persons(seatings, {1: [501, 502]})
    kept, dropped = enforce_driver_availability(people, [group], {501})
    assert len(kept) == 2
    assert dropped == []


def test_split_group_does_not_lend_its_driver_to_both_vehicles():
    """The driver rides in one car, so only that car may depart.

    Regression guard: counting drivers per group rather than per person let a
    split family's single licensed member make both of its vehicles look
    drivable, and 17 cars departed with nobody able to drive them.
    """
    big = Group(1, household_id=10, area_id="A", size=7,
                contains_dependent=True, all_can_walk=True)
    vehicles = [
        Vehicle(muster_point_id=100, owner_household_id=98, capacity=5),
        Vehicle(muster_point_id=101, owner_household_id=99, capacity=5),
    ]
    walk = {10: [(100, 10.0), (101, 20.0)]}

    seatings, unseated = run([big], vehicles, walk=walk)
    assert len(seatings) == 2, "expected the group to be split across two cars"

    members = {1: [501, 502, 503, 504, 505, 506, 507]}
    people = expand_to_persons(seatings, members)
    # Only 501 is licensed, and members fill vehicles in person order, so the
    # driver is in the vehicle with the lower muster point id.
    kept, dropped = enforce_driver_availability(people, [big], {501})

    driven = {seat.muster_point_id for seat in kept}
    assert len(driven) == 1, "only the car actually holding the driver may depart"
    assert sum(u.size for u in dropped) == 7 - len(kept)


def test_expansion_gives_every_seat_to_exactly_one_person():
    """Seats and people line up, including across a split."""
    big = Group(1, household_id=10, area_id="A", size=7,
                contains_dependent=True, all_can_walk=True)
    vehicles = [
        Vehicle(muster_point_id=100, owner_household_id=98, capacity=5),
        Vehicle(muster_point_id=101, owner_household_id=99, capacity=5),
    ]
    seatings, _ = run([big], vehicles, walk={10: [(100, 10.0), (101, 20.0)]})
    members = {1: list(range(501, 508))}
    people = expand_to_persons(seatings, members)

    assert len(people) == 7
    assert {seat.person_id for seat in people} == set(members[1])
