"""Choosing which vehicles depart, and filling them.

The previous approach seated every car-owning household in its own car, which
pinned cars on the road to the number of car-owning households however few
people were in them. Here departure is a decision: vehicles are activated by
greedy max-coverage — repeatedly the car that seats the most currently unseated
people who can reach it — so a household may ride with a neighbour and leave its
own car parked.

Driver availability is a *selection* constraint rather than a check applied
afterwards. A vehicle is only activated if a licensed driver will be aboard, and
the driver is seated first, so nobody is ever committed to a car that turns out
to be undrivable.

Kept free of Dagster and DuckDB so the objective and its tie-breaks can be
tested directly.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Iterable, Sequence

from london_frontline.seating import (
    REASON_CANDIDATES_FULL,
    REASON_NO_CANDIDATE,
    TIER_HOME_COLLECTION,
    TIER_WALK_IN_DEPENDENT,
    TIER_WALK_IN_INDEPENDENT,
    Group,
    Seating,
    Unseated,
    Vehicle,
)


@dataclass(frozen=True)
class Reach:
    """Which households can board which vehicles, and by which mode.

    Walking and collection are kept apart because they are not
    interchangeable: a walk-in group needs a vehicle within its walking ceiling,
    while a home-collection group needs one willing to detour to its address.
    """

    walk: dict[int, set[int]]
    collection: dict[int, set[int]]


def _tier_of(group: Group) -> int:
    if not group.all_can_walk:
        return TIER_HOME_COLLECTION
    return TIER_WALK_IN_DEPENDENT if group.contains_dependent else TIER_WALK_IN_INDEPENDENT


def _fill_order(group: Group) -> tuple[int, int]:
    """Sort key for filling: tier first, then larger groups, hardest to place."""
    return (_tier_of(group), -group.size)


def activate_fleet(
    groups: Sequence[Group],
    vehicles: Sequence[Vehicle],
    reach: Reach,
    group_driver_count: dict[int, int],
    max_collection_stops_per_vehicle: int,
    minimum_occupancy: int = 1,
) -> tuple[list[Seating], list[Unseated], list[int]]:
    """Activate vehicles by greedy coverage and fill them.

    Returns the seatings, the groups that could not be seated, and the ids of
    the vehicles that departed.
    """
    group_by_id = {group.group_id: group for group in groups}
    groups_by_household: dict[int, list[int]] = {}
    for group in groups:
        groups_by_household.setdefault(group.household_id, []).append(group.group_id)

    # Inverted reach: for each vehicle, the households that could board it.
    households_by_vehicle: dict[int, set[int]] = {}
    for mapping in (reach.walk, reach.collection):
        for household, vehicle_ids in mapping.items():
            for vehicle_id in vehicle_ids:
                households_by_vehicle.setdefault(vehicle_id, set()).add(household)

    unseated: set[int] = {group.group_id for group in groups}
    unseated_size_by_household: dict[int, int] = {}
    for group in groups:
        unseated_size_by_household[group.household_id] = (
            unseated_size_by_household.get(group.household_id, 0) + group.size
        )

    vehicle_by_id = {vehicle.muster_point_id: vehicle for vehicle in vehicles}

    def boardable(vehicle: Vehicle) -> list[Group]:
        """Unseated groups that could ride in this vehicle, by their own mode."""
        candidates: list[Group] = []
        for household in households_by_vehicle.get(vehicle.muster_point_id, ()):
            for group_id in groups_by_household.get(household, ()):
                if group_id not in unseated:
                    continue
                group = group_by_id[group_id]
                allowed = reach.collection if not group.all_can_walk else reach.walk
                if vehicle.muster_point_id in allowed.get(household, ()):
                    candidates.append(group)
        return candidates

    def coverage(vehicle: Vehicle) -> int:
        """Upper bound on people this vehicle could seat right now."""
        total = 0
        for household in households_by_vehicle.get(vehicle.muster_point_id, ()):
            total += unseated_size_by_household.get(household, 0)
            if total >= vehicle.capacity:
                return vehicle.capacity
        return total

    def owner_can_drive(vehicle: Vehicle) -> bool:
        """Cheap pre-filter: is anyone from the owning household still unseated?"""
        return unseated_size_by_household.get(vehicle.owner_household_id, 0) > 0

    # Lazy greedy: keys go stale as groups are seated, so a popped entry is
    # revalidated and pushed back if it has decayed. Recomputing every vehicle's
    # coverage on each activation would be quadratic.
    heap: list[tuple[int, int]] = []
    for vehicle in vehicles:
        if not owner_can_drive(vehicle):
            continue
        score = coverage(vehicle)
        if score:
            heapq.heappush(heap, (-score, vehicle.muster_point_id))

    seatings: list[Seating] = []
    activated: list[int] = []
    spare_seats: dict[int, int] = {}
    stops_used: dict[int, int] = {}

    while heap:
        negative_score, vehicle_id = heapq.heappop(heap)
        vehicle = vehicle_by_id[vehicle_id]
        if not owner_can_drive(vehicle):
            continue
        current = coverage(vehicle)
        if current == 0:
            continue
        if current < -negative_score:
            heapq.heappush(heap, (-current, vehicle_id))
            continue

        candidates = boardable(vehicle)
        if not candidates:
            continue

        # A car is driven by its owner, never by a neighbour: it is their vehicle
        # and their keys. So a vehicle is only activated when a licensed member of
        # the owning household is aboard to drive it, which also means no car
        # departs without its owner having chosen to travel in it.
        drivers = [
            g
            for g in candidates
            if g.household_id == vehicle.owner_household_id
            and group_driver_count.get(g.group_id, 0) > 0
        ]
        if not drivers:
            continue

        remaining = vehicle.capacity
        stops = 0
        chosen: list[Group] = []

        # Seat a driver-bearing group first; among them prefer the one the fill
        # order would have taken anyway.
        drivers.sort(key=_fill_order)
        driver_group = next((g for g in drivers if g.size <= remaining), None)
        if driver_group is None:
            continue
        chosen.append(driver_group)
        remaining -= driver_group.size
        if not driver_group.all_can_walk:
            stops += 1

        for group in sorted(candidates, key=_fill_order):
            if remaining <= 0:
                break
            if group.group_id == driver_group.group_id:
                continue
            if group.size > remaining:
                continue
            if not group.all_can_walk:
                if stops >= max_collection_stops_per_vehicle:
                    continue
                stops += 1
            chosen.append(group)
            remaining -= group.size

        occupants = sum(group.size for group in chosen)
        if occupants < minimum_occupancy:
            continue

        activated.append(vehicle_id)
        spare_seats[vehicle_id] = remaining
        stops_used[vehicle_id] = stops
        for group in chosen:
            seatings.append(
                Seating(group.group_id, vehicle_id, _tier_of(group), group.size)
            )
            unseated.discard(group.group_id)
            unseated_size_by_household[group.household_id] -= group.size

    # A group larger than any car available to it would otherwise never be
    # seated. The baseline spec requires splitting it across the fewest vehicles
    # that seat all its members, so this runs after activation, using the spare
    # seats of cars already departing — each of which already has a driver.
    for group_id in sorted(unseated):
        group = group_by_id[group_id]
        allowed = reach.collection if not group.all_can_walk else reach.walk
        reachable = [
            vehicle_by_id[vehicle_id]
            for vehicle_id in allowed.get(group.household_id, ())
            if vehicle_id in vehicle_by_id
        ]
        if not reachable:
            continue
        if group.size <= max(vehicle.capacity for vehicle in reachable):
            # It fits in a car in principle; it simply did not fit today's
            # occupancy, which is not a reason to break up a family.
            continue

        # Departing vehicles first: each already carries a driver. Then, if the
        # group still does not fit, activate fresh vehicles — but no more than
        # the group has drivers of its own to put in them, since a newly
        # activated car has nobody else aboard to drive it.
        usable = sorted(
            (v for v in reachable if spare_seats.get(v.muster_point_id, 0) > 0),
            key=lambda v: -spare_seats[v.muster_point_id],
        )
        spare_drivers = group_driver_count.get(group_id, 0)
        # Only the group's own household's vehicles can be brought into service
        # for a split: another household's car has no owner-driver aboard.
        fresh = sorted(
            (
                v
                for v in reachable
                if v.muster_point_id not in spare_seats
                and v.owner_household_id == group.household_id
            ),
            key=lambda v: -v.capacity,
        )[:spare_drivers]

        needed = group.size
        plan: list[tuple[int, int]] = []
        for vehicle in (*usable, *fresh):
            vehicle_id = vehicle.muster_point_id
            if not group.all_can_walk and stops_used.get(vehicle_id, 0) >= (
                max_collection_stops_per_vehicle
            ):
                continue
            available = spare_seats.get(vehicle_id, vehicle.capacity)
            take = min(available, needed)
            if take <= 0:
                continue
            plan.append((vehicle_id, take))
            needed -= take
            if needed == 0:
                break
        if needed:
            continue

        # Parts riding in already-departing vehicles inherit their driver, but a
        # freshly activated one has only this group aboard. Drivers are spread a
        # part at a time in person order, so the only way to guarantee every part
        # is drivable is to require a driver for each of them.
        uses_fresh = any(
            vehicle_id not in spare_seats for vehicle_id, _ in plan
        )
        if uses_fresh and spare_drivers < len(plan):
            continue

        for vehicle_id, take in plan:
            if vehicle_id not in spare_seats:
                activated.append(vehicle_id)
                spare_seats[vehicle_id] = vehicle_by_id[vehicle_id].capacity
                stops_used[vehicle_id] = 0
            spare_seats[vehicle_id] -= take
            if not group.all_can_walk:
                stops_used[vehicle_id] = stops_used.get(vehicle_id, 0) + 1
            seatings.append(
                Seating(group.group_id, vehicle_id, _tier_of(group), take, split=True)
            )
        unseated.discard(group_id)
        unseated_size_by_household[group.household_id] -= group.size

    # Anything still unseated could not reach an activated vehicle. Distinguish
    # having no candidate at all from every candidate being taken, so a coverage
    # limitation is never reported as a shortage of seats.
    leftovers: list[Unseated] = []
    for group_id in sorted(unseated):
        group = group_by_id[group_id]
        allowed = reach.collection if not group.all_can_walk else reach.walk
        has_candidate = bool(allowed.get(group.household_id))
        leftovers.append(
            Unseated(
                group.group_id,
                group.area_id,
                _tier_of(group),
                group.size,
                group.collection_mode,
                REASON_CANDIDATES_FULL if has_candidate else REASON_NO_CANDIDATE,
            )
        )

    return seatings, leftovers, activated


def packing_floor(people: int, capacity: int) -> int:
    """Fewest vehicles that could carry ``people`` if packing were perfect."""
    if capacity <= 0:
        return 0
    return -(-people // capacity)


def build_reach(
    walk_pairs: Iterable[tuple[int, int]],
    collection_pairs: Iterable[tuple[int, int]],
) -> Reach:
    """Build a Reach from (household, vehicle) pairs for each mode."""
    walk: dict[int, set[int]] = {}
    collection: dict[int, set[int]] = {}
    for household, vehicle in walk_pairs:
        walk.setdefault(household, set()).add(vehicle)
    for household, vehicle in collection_pairs:
        collection.setdefault(household, set()).add(vehicle)
    return Reach(walk=walk, collection=collection)
