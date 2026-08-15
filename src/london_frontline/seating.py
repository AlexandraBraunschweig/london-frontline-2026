"""The tiered seat-assignment algorithm.

Because each muster point is exactly one parked vehicle, choosing a group's
muster point and choosing its seats are the same decision, made once here.

Tiers, in order:

1. the owner household's own travel groups, into its own vehicle;
2. Class A walk-in groups — those containing a dependent;
3. home-collection groups — those with any member who cannot walk;
4. Class B walk-in groups — independent adults only.

Kept free of Dagster and DuckDB so the ordering and splitting rules can be
tested directly.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

TIER_OWNER = 1
TIER_WALK_IN_DEPENDENT = 2
TIER_HOME_COLLECTION = 3
TIER_WALK_IN_INDEPENDENT = 4

# Why a group ended up unseated. Distinguished so a candidate-list cap is never
# reported as though it were a shortage of seats.
REASON_NO_CANDIDATE = "no muster point within range"
REASON_CANDIDATES_FULL = "every candidate muster point was full"
REASON_NO_DRIVER = "vehicle had no occupant licensed to drive"


@dataclass(frozen=True)
class Group:
    """An indivisible travel group seeking seats."""

    group_id: int
    household_id: int
    area_id: str
    size: int
    contains_dependent: bool
    all_can_walk: bool

    @property
    def collection_mode(self) -> str:
        return "walk_in" if self.all_can_walk else "home_collection"


@dataclass(frozen=True)
class Vehicle:
    """A parked vehicle, which is also its muster point."""

    muster_point_id: int
    owner_household_id: int
    capacity: int


@dataclass(frozen=True)
class Seating:
    """A group, or part of one, seated in a vehicle."""

    group_id: int
    muster_point_id: int
    tier: int
    seats: int
    split: bool = False


@dataclass(frozen=True)
class PersonSeat:
    """One person in one vehicle, after a group's seats are handed to members."""

    person_id: int
    group_id: int
    muster_point_id: int
    tier: int
    split: bool


@dataclass(frozen=True)
class Unseated:
    """A group that could not be seated, and why."""

    group_id: int
    area_id: str
    tier: int
    size: int
    collection_mode: str
    reason: str


@dataclass
class _State:
    remaining: dict[int, int]
    capacity: dict[int, int]
    stops: dict[int, int] = field(default_factory=dict)


def _order_candidates(
    candidates: Sequence[tuple[int, float]],
    state: _State,
    by_stops: bool,
) -> list[tuple[int, float]]:
    """Order candidate vehicles for a group.

    Walk-in groups take the nearest. Home-collection groups prefer the vehicle
    with fewest collection stops already assigned, so unavoidable stops spread
    across the fleet instead of piling onto one route.
    """
    if not by_stops:
        return list(candidates)
    return sorted(candidates, key=lambda item: (state.stops.get(item[0], 0), item[1]))


def _try_seat(
    group: Group,
    candidates: Sequence[tuple[int, float]],
    state: _State,
    tier: int,
    max_stops: int | None,
) -> list[Seating] | None:
    """Seat a whole group in one vehicle, or return None."""
    by_stops = max_stops is not None
    for muster_point_id, _distance in _order_candidates(candidates, state, by_stops):
        if by_stops and state.stops.get(muster_point_id, 0) >= max_stops:
            continue
        if state.remaining.get(muster_point_id, 0) >= group.size:
            state.remaining[muster_point_id] -= group.size
            if by_stops:
                state.stops[muster_point_id] = state.stops.get(muster_point_id, 0) + 1
            return [Seating(group.group_id, muster_point_id, tier, group.size)]
    return None


def _try_split(
    group: Group,
    candidates: Sequence[tuple[int, float]],
    state: _State,
    tier: int,
    max_stops: int | None,
) -> list[Seating] | None:
    """Split an oversized group across the fewest vehicles that seat it.

    Only reached when the group is larger than any single available vehicle's
    capacity — a group that merely does not fit today's occupancy is not split.
    Filling the emptiest vehicles first minimises the number used.
    """
    by_stops = max_stops is not None
    usable = [
        (muster_point_id, distance)
        for muster_point_id, distance in candidates
        if state.remaining.get(muster_point_id, 0) > 0
        and not (by_stops and state.stops.get(muster_point_id, 0) >= max_stops)
    ]
    usable.sort(key=lambda item: -state.remaining[item[0]])

    seats_needed = group.size
    plan: list[tuple[int, int]] = []
    for muster_point_id, _distance in usable:
        take = min(state.remaining[muster_point_id], seats_needed)
        plan.append((muster_point_id, take))
        seats_needed -= take
        if seats_needed == 0:
            break
    if seats_needed:
        return None

    seatings = []
    for muster_point_id, take in plan:
        state.remaining[muster_point_id] -= take
        if by_stops:
            state.stops[muster_point_id] = state.stops.get(muster_point_id, 0) + 1
        seatings.append(
            Seating(group.group_id, muster_point_id, tier, take, split=True)
        )
    return seatings


def _seat_tier(
    groups: Iterable[Group],
    candidates_by_household: dict[int, Sequence[tuple[int, float]]],
    state: _State,
    tier: int,
    seatings: list[Seating],
    unseated: list[Unseated],
    max_stops: int | None = None,
) -> None:
    """Seat every group in one tier, recording those that cannot be seated."""
    for group in groups:
        candidates = candidates_by_household.get(group.household_id, ())
        if not candidates:
            unseated.append(
                Unseated(
                    group.group_id,
                    group.area_id,
                    tier,
                    group.size,
                    group.collection_mode,
                    REASON_NO_CANDIDATE,
                )
            )
            continue

        placed = _try_seat(group, candidates, state, tier, max_stops)
        if placed is None:
            largest = max(
                (state.capacity.get(muster_point_id, 0) for muster_point_id, _ in candidates),
                default=0,
            )
            if group.size > largest:
                placed = _try_split(group, candidates, state, tier, max_stops)
        if placed is None:
            unseated.append(
                Unseated(
                    group.group_id,
                    group.area_id,
                    tier,
                    group.size,
                    group.collection_mode,
                    REASON_CANDIDATES_FULL,
                )
            )
        else:
            seatings.extend(placed)


def assign_seats(
    groups: Sequence[Group],
    vehicles: Sequence[Vehicle],
    walk_candidates: dict[int, Sequence[tuple[int, float]]],
    collection_candidates: dict[int, Sequence[tuple[int, float]]],
    max_collection_stops_per_vehicle: int,
) -> tuple[list[Seating], list[Unseated]]:
    """Run the four tiers in order and return seatings and unseated groups."""
    state = _State(
        remaining={vehicle.muster_point_id: vehicle.capacity for vehicle in vehicles},
        capacity={vehicle.muster_point_id: vehicle.capacity for vehicle in vehicles},
        stops={vehicle.muster_point_id: 0 for vehicle in vehicles},
    )
    owned: dict[int, list[Vehicle]] = defaultdict(list)
    for vehicle in vehicles:
        owned[vehicle.owner_household_id].append(vehicle)

    seatings: list[Seating] = []
    unseated: list[Unseated] = []

    # --- Tier 1: the owner household rides in its own car ------------------
    by_household: dict[int, list[Group]] = defaultdict(list)
    for group in groups:
        by_household[group.household_id].append(group)

    pending: list[Group] = []
    for household_id, household_groups in by_household.items():
        own_vehicles = owned.get(household_id)
        if not own_vehicles:
            pending.extend(household_groups)
            continue
        # Groups containing a dependent take the household's own seats first,
        # then larger groups, since they are the hardest to place elsewhere.
        ordered = sorted(
            household_groups,
            key=lambda group: (not group.contains_dependent, -group.size),
        )
        own_candidates = [(vehicle.muster_point_id, 0.0) for vehicle in own_vehicles]
        for group in ordered:
            placed = _try_seat(group, own_candidates, state, TIER_OWNER, None)
            if placed is None:
                pending.append(group)
            else:
                seatings.extend(placed)

    # --- Tiers 2 to 4 ------------------------------------------------------
    class_a = [g for g in pending if g.all_can_walk and g.contains_dependent]
    collection = [g for g in pending if not g.all_can_walk]
    class_b = [g for g in pending if g.all_can_walk and not g.contains_dependent]

    _seat_tier(
        class_a, walk_candidates, state, TIER_WALK_IN_DEPENDENT, seatings, unseated
    )
    _seat_tier(
        collection,
        collection_candidates,
        state,
        TIER_HOME_COLLECTION,
        seatings,
        unseated,
        max_stops=max_collection_stops_per_vehicle,
    )
    _seat_tier(
        class_b, walk_candidates, state, TIER_WALK_IN_INDEPENDENT, seatings, unseated
    )

    return seatings, unseated


def expand_to_persons(
    seatings: Sequence[Seating],
    members_by_group: dict[int, Sequence[int]],
<<<<<<< Updated upstream
    licensed_person_ids: set[int] | None = None,
=======
>>>>>>> Stashed changes
) -> list[PersonSeat]:
    """Hand each group's seats to specific members.

    Members are taken in person order and seatings in muster-point order, so a
    split group's members land deterministically and the same run reproduces the
    same allocation.
<<<<<<< Updated upstream

    When a group is split across vehicles and ``licensed_person_ids`` is given,
    one licensed member is placed in each part before the rest are distributed.
    Without that, a family's only driver could land in one car and leave the
    other undrivable — which is the same fault as counting drivers per group.
=======
>>>>>>> Stashed changes
    """
    by_group: dict[int, list[Seating]] = defaultdict(list)
    for seat in seatings:
        by_group[seat.group_id].append(seat)

    person_seats: list[PersonSeat] = []
    for group_id, group_seatings in by_group.items():
        members = sorted(members_by_group.get(group_id, ()))
<<<<<<< Updated upstream
        ordered = sorted(group_seatings, key=lambda s: s.muster_point_id)

        allocation: dict[int, list[int]] = {
            seat.muster_point_id: [] for seat in ordered
        }
        pending = list(members)

        # Spread drivers across the parts first, one each, while there are parts
        # left that have none.
        if licensed_person_ids and len(ordered) > 1:
            drivers = [m for m in pending if m in licensed_person_ids]
            for seat, driver in zip(ordered, drivers):
                allocation[seat.muster_point_id].append(driver)
                pending.remove(driver)

        for seat in ordered:
            room = seat.seats - len(allocation[seat.muster_point_id])
            for _ in range(room):
                if not pending:
                    break
                allocation[seat.muster_point_id].append(pending.pop(0))

        for seat in ordered:
            for person_id in allocation[seat.muster_point_id]:
                person_seats.append(
                    PersonSeat(
                        person_id, group_id, seat.muster_point_id,
                        seat.tier, seat.split,
                    )
                )
=======
        position = 0
        for seat in sorted(group_seatings, key=lambda s: s.muster_point_id):
            for _ in range(seat.seats):
                if position >= len(members):
                    break
                person_seats.append(
                    PersonSeat(
                        members[position], group_id, seat.muster_point_id,
                        seat.tier, seat.split,
                    )
                )
                position += 1
>>>>>>> Stashed changes
    return person_seats


def enforce_driver_availability(
    person_seats: Sequence[PersonSeat],
    groups: Sequence[Group],
    licensed_person_ids: set[int],
) -> tuple[list[PersonSeat], list[Unseated]]:
    """Drop any vehicle whose actual occupants include no licensed driver.

    Checked per person rather than per group: a group split across two vehicles
    puts its members — and therefore its drivers — in only one of them, so
    crediting the group's driver count to every vehicle it touches would let a
    car with nobody licensed in it depart.
    """
    group_by_id = {group.group_id: group for group in groups}
    drivers_by_vehicle: dict[int, int] = defaultdict(int)
    for seat in person_seats:
        if seat.person_id in licensed_person_ids:
            drivers_by_vehicle[seat.muster_point_id] += 1

    kept: list[PersonSeat] = []
    dropped_by_group: dict[tuple[int, int], int] = defaultdict(int)
    for seat in person_seats:
        if drivers_by_vehicle[seat.muster_point_id] > 0:
            kept.append(seat)
        else:
            dropped_by_group[(seat.group_id, seat.tier)] += 1

    dropped = [
        Unseated(
            group_id,
            group_by_id[group_id].area_id,
            tier,
            people,
            group_by_id[group_id].collection_mode,
            REASON_NO_DRIVER,
        )
        for (group_id, tier), people in dropped_by_group.items()
    ]
    return kept, dropped
