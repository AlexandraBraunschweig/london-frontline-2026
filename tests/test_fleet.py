"""Fleet-activation tests (tasks 4.1-4.7)."""

from london_frontline.fleet import Reach, activate_fleet, build_reach, packing_floor
from london_frontline.seating import (
    REASON_CANDIDATES_FULL,
    TIER_HOME_COLLECTION,
    TIER_WALK_IN_DEPENDENT,
    TIER_WALK_IN_INDEPENDENT,
    Group,
    Vehicle,
)

MAX_STOPS = 3


def walk_reach(mapping):
    return Reach(walk=mapping, collection={})


def run(groups, vehicles, reach, drivers, **kwargs):
    return activate_fleet(groups, vehicles, reach, drivers, MAX_STOPS, **kwargs)


def test_car_owning_household_rides_with_a_neighbour_to_save_a_vehicle():
    """Task 4.1: ownership no longer guarantees your own car departs."""
    # Two one-person car-owning households, each able to reach both cars.
    a = Group(1, household_id=10, area_id="A", size=1,
              contains_dependent=False, all_can_walk=True)
    b = Group(2, household_id=11, area_id="A", size=1,
              contains_dependent=False, all_can_walk=True)
    vehicles = [
        Vehicle(muster_point_id=100, owner_household_id=10, capacity=5),
        Vehicle(muster_point_id=101, owner_household_id=11, capacity=5),
    ]
    reach = walk_reach({10: {100, 101}, 11: {100, 101}})

    seatings, unseated, activated = run(
        [a, b], vehicles, reach, {1: 1, 2: 1}
    )
    assert unseated == []
    assert len(activated) == 1, "one car should carry both households"
    assert len({s.muster_point_id for s in seatings}) == 1


def test_unactivated_vehicle_carries_nobody():
    """Task 4.2."""
    group = Group(1, household_id=10, area_id="A", size=2,
                  contains_dependent=False, all_can_walk=True)
    vehicles = [
        Vehicle(muster_point_id=100, owner_household_id=10, capacity=5),
        Vehicle(muster_point_id=101, owner_household_id=11, capacity=5),
    ]
    reach = walk_reach({10: {100, 101}})

    seatings, _, activated = run([group], vehicles, reach, {1: 1})
    assert len(activated) == 1
    idle = ({100, 101} - set(activated)).pop()
    assert all(s.muster_point_id != idle for s in seatings)


def test_vehicle_reachable_only_by_unlicensed_people_is_never_activated():
    """Task 4.3: those people wait for a car someone can drive."""
    unlicensed = Group(1, household_id=10, area_id="A", size=1,
                       contains_dependent=False, all_can_walk=True)
    licensed = Group(2, household_id=11, area_id="A", size=1,
                     contains_dependent=False, all_can_walk=True)
    vehicles = [
        Vehicle(muster_point_id=100, owner_household_id=10, capacity=5),
        Vehicle(muster_point_id=101, owner_household_id=11, capacity=5),
    ]
    # 100 is reachable only by the unlicensed household; 101 by both.
    reach = walk_reach({10: {100, 101}, 11: {101}})

    seatings, unseated, activated = run(
        [unlicensed, licensed], vehicles, reach, {1: 0, 2: 1}
    )
    assert 100 not in activated
    assert unseated == [], "the unlicensed household rides in the drivable car"
    assert {s.muster_point_id for s in seatings} == {101}


def test_nobody_is_unmet_for_want_of_a_driver():
    """Task 4.4: unlicensed passengers ride with an owner who can drive."""
    groups = [
        Group(i, household_id=10 + i, area_id="A", size=1,
              contains_dependent=False, all_can_walk=True)
        for i in range(4)
    ]
    # The car belongs to household 10, whose member can drive it.
    vehicles = [Vehicle(muster_point_id=100, owner_household_id=10, capacity=5)]
    reach = walk_reach({g.household_id: {100} for g in groups})
    drivers = {0: 1, 1: 0, 2: 0, 3: 0}

    _, unseated, activated = run(groups, vehicles, reach, drivers)
    assert activated == [100]
    assert unseated == []


def test_a_car_whose_owner_cannot_drive_never_departs():
    """Only the owner drives, so an unlicensed household's car stays parked."""
    unlicensed_owner = Group(1, household_id=10, area_id="A", size=1,
                             contains_dependent=False, all_can_walk=True)
    licensed_neighbour = Group(2, household_id=11, area_id="A", size=1,
                               contains_dependent=False, all_can_walk=True)
    vehicles = [
        Vehicle(muster_point_id=100, owner_household_id=10, capacity=5),
        Vehicle(muster_point_id=101, owner_household_id=11, capacity=5),
    ]
    reach = walk_reach({10: {100, 101}, 11: {100, 101}})

    _, unseated, activated = run(
        [unlicensed_owner, licensed_neighbour], vehicles, reach, {1: 0, 2: 1}
    )
    # 100 cannot depart even though a licensed neighbour could reach it.
    assert activated == [101]
    assert unseated == []


def test_a_neighbour_never_drives_someone_elses_car():
    """A licensed passenger does not make another household's car drivable."""
    owner = Group(1, household_id=10, area_id="A", size=1,
                  contains_dependent=False, all_can_walk=True)
    neighbour = Group(2, household_id=11, area_id="A", size=1,
                      contains_dependent=False, all_can_walk=True)
    vehicles = [Vehicle(muster_point_id=100, owner_household_id=10, capacity=5)]
    reach = walk_reach({10: {100}, 11: {100}})

    _, unseated, activated = run(
        [owner, neighbour], vehicles, reach, {1: 0, 2: 1}
    )
    assert activated == []
    assert {u.group_id for u in unseated} == {1, 2}


def test_only_the_owning_households_car_is_used():
    """Task 4.5: of two reachable cars, only the one they own can be driven."""
    owner = Group(1, household_id=10, area_id="A", size=2,
                  contains_dependent=False, all_can_walk=True)
    vehicles = [
        Vehicle(muster_point_id=100, owner_household_id=99, capacity=5),
        Vehicle(muster_point_id=101, owner_household_id=10, capacity=5),
    ]
    reach = walk_reach({10: {100, 101}})

    _, _, activated = run([owner], vehicles, reach, {1: 1})
    assert activated == [101]


def test_dependent_groups_are_filled_before_independent_adults():
    """Task 4.6: tier order still governs who takes the seats."""
    driver = Group(3, household_id=12, area_id="A", size=1,
                   contains_dependent=False, all_can_walk=True)
    dependent = Group(1, household_id=10, area_id="A", size=3,
                      contains_dependent=True, all_can_walk=True)
    adults = Group(2, household_id=11, area_id="A", size=3,
                   contains_dependent=False, all_can_walk=True)
    vehicles = [Vehicle(muster_point_id=100, owner_household_id=12, capacity=5)]
    reach = walk_reach({10: {100}, 11: {100}, 12: {100}})

    seatings, unseated, _ = run(
        [dependent, adults, driver], vehicles, reach, {1: 1, 2: 1, 3: 1}
    )
    seated = {s.group_id for s in seatings}
    assert seated == {3, 1}, "the owner-driver, then the dependent-bearing group"
    assert [u.group_id for u in unseated] == [2]
    assert unseated[0].reason == REASON_CANDIDATES_FULL


def test_travel_groups_are_never_split_by_activation():
    """Task 4.7: a group that does not fit is passed over, not broken up."""
    big = Group(1, household_id=10, area_id="A", size=4,
                contains_dependent=True, all_can_walk=True)
    small = Group(2, household_id=11, area_id="A", size=2,
                  contains_dependent=False, all_can_walk=True)
    vehicles = [Vehicle(muster_point_id=100, owner_household_id=10, capacity=5)]
    reach = walk_reach({10: {100}, 11: {100}})

    seatings, _, _ = run([big, small], vehicles, reach, {1: 1, 2: 1})
    seated_sizes = {s.group_id: s.seats for s in seatings}
    assert seated_sizes.get(1) == 4, "the whole family rides together"
    assert 2 not in seated_sizes, "the pair does not fit in the last seat"


def test_home_collection_group_needs_collection_reach_not_walking_reach():
    """A non-walker cannot be expected to walk to a car in range on foot."""
    collected = Group(1, household_id=10, area_id="A", size=1,
                      contains_dependent=False, all_can_walk=False)
    vehicles = [Vehicle(muster_point_id=100, owner_household_id=10, capacity=5)]
    # Reachable on foot only — which is meaningless for a group that cannot walk.
    seatings, unseated, activated = run(
        [collected], vehicles, walk_reach({10: {100}}), {1: 1}
    )
    assert seatings == []
    assert activated == []
    assert unseated[0].collection_mode == "home_collection"


def test_collection_stops_per_vehicle_are_capped():
    groups = [
        Group(i, household_id=10 + i, area_id="A", size=1,
              contains_dependent=False, all_can_walk=False)
        for i in range(5)
    ]
    vehicles = [Vehicle(muster_point_id=100, owner_household_id=10, capacity=10)]
    reach = Reach(walk={}, collection={g.household_id: {100} for g in groups})

    seatings, unseated, _ = activate_fleet(
        groups, vehicles, reach, {g.group_id: 1 for g in groups}, 2
    )
    assert len(seatings) == 2
    assert len(unseated) == 3


def test_packing_floor():
    assert packing_floor(127820, 5) == 25564
    assert packing_floor(0, 5) == 0
    assert packing_floor(6, 5) == 2


def test_build_reach_separates_modes():
    reach = build_reach([(1, 100), (1, 101)], [(1, 102)])
    assert reach.walk == {1: {100, 101}}
    assert reach.collection == {1: {102}}


def test_oversized_group_is_split_across_departing_vehicles():
    """A family larger than any single car is split, not abandoned.

    Distinct from not fitting today's occupancy, which is never a reason to
    break up a family.
    """
    big = Group(1, household_id=10, area_id="A", size=7,
                contains_dependent=True, all_can_walk=True)
    filler = Group(2, household_id=11, area_id="A", size=1,
                   contains_dependent=False, all_can_walk=True)
    # The family owns both cars; only their own vehicles can carry the split.
    vehicles = [
        Vehicle(muster_point_id=100, owner_household_id=10, capacity=5),
        Vehicle(muster_point_id=101, owner_household_id=10, capacity=5),
    ]
    reach = walk_reach({10: {100, 101}, 11: {100, 101}})

    # Two drivers, because a split needs one per part — a seven-person family
    # with a single licence cannot fill two cars.
    seatings, unseated, activated = run(
        [big, filler], vehicles, reach, {1: 2, 2: 1}
    )
    big_seatings = [s for s in seatings if s.group_id == 1]
    assert sum(s.seats for s in big_seatings) == 7
    assert len(big_seatings) == 2
    assert all(s.split for s in big_seatings)
    assert not any(u.group_id == 1 for u in unseated)


def test_oversized_group_with_one_driver_is_not_split_into_fresh_vehicles():
    """One licence cannot drive two cars, so the split is refused, not faked."""
    big = Group(1, household_id=10, area_id="A", size=7,
                contains_dependent=True, all_can_walk=True)
    vehicles = [
        Vehicle(muster_point_id=100, owner_household_id=10, capacity=5),
        Vehicle(muster_point_id=101, owner_household_id=10, capacity=5),
    ]
    reach = walk_reach({10: {100, 101}})

    seatings, unseated, activated = run([big], vehicles, reach, {1: 1})
    assert seatings == []
    assert [u.group_id for u in unseated] == [1]
