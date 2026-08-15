"""Collapsing boundary crossings into the corridors they describe.

A road corridor rarely crosses a district boundary exactly once. A dual
carriageway crosses once per carriageway; a way can leave the district and
re-enter; one road is often mapped as several ways of different classes meeting
at a junction. Treated as separate destinations, the one a vehicle heads for
turns on a few metres of proximity — which in Thanet put 90% of the fleet onto a
tertiary crossing while a trunk crossing 250 m away took seven vehicles.

So crossings close together are merged, and the merged exit is represented by
the most major road among them.

Kept free of Dagster and DuckDB so the grouping and representative rules can be
tested directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Crossing:
    """One place a qualifying way crosses the district boundary."""

    crossing_id: int
    way_id: int
    highway: str
    easting: float
    northing: float
    length_outside_m: float


@dataclass(frozen=True)
class Exit:
    """A corridor out of the district, standing for one or more crossings."""

    representative: Crossing
    members: tuple[Crossing, ...]

    @property
    def crossing_count(self) -> int:
        return len(self.members)


def _distance(a: Crossing, b: Crossing) -> float:
    return math.hypot(a.easting - b.easting, a.northing - b.northing)


def _single_linkage_groups(
    crossings: Sequence[Crossing], radius_m: float
) -> list[list[int]]:
    """Group indices so that any two within ``radius_m`` share a group.

    Single linkage, so a chain of crossings each within the radius of the next
    merges transitively — which is what a corridor crossing repeatedly looks
    like. The count of crossings behind each exit is reported downstream, so
    over-merging shows up rather than passing unnoticed.
    """
    parent = list(range(len(crossings)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for i in range(len(crossings)):
        for j in range(i + 1, len(crossings)):
            if _distance(crossings[i], crossings[j]) <= radius_m:
                union(i, j)

    grouped: dict[int, list[int]] = {}
    for index in range(len(crossings)):
        grouped.setdefault(find(index), []).append(index)
    return [grouped[key] for key in sorted(grouped)]


def cluster_crossings(
    crossings: Iterable[Crossing],
    radius_m: float,
    class_priority: Sequence[str],
) -> list[Exit]:
    """Merge co-located crossings, representing each corridor by its largest road.

    ``class_priority`` is ordered most major first. Ties fall to the crossing
    continuing furthest beyond the boundary, then to the lowest way and crossing
    id, so the choice is deterministic rather than dependent on input order.

    The representative is always one of the real crossings, never a centroid of
    them: an exit that lay on no way would have no way reference, and the way
    reference is what binds it to a simulator's network.
    """
    if radius_m < 0:
        raise ValueError(f"clustering radius must not be negative, got {radius_m}")

    ordered = sorted(crossings, key=lambda crossing: crossing.crossing_id)
    if not ordered:
        return []

    rank = {name: position for position, name in enumerate(class_priority)}
    unknown = {crossing.highway for crossing in ordered} - rank.keys()
    if unknown:
        raise ValueError(
            f"crossings carry road classes absent from the priority order: "
            f"{sorted(unknown)}"
        )

    exits = []
    for group in _single_linkage_groups(ordered, radius_m):
        members = tuple(ordered[index] for index in group)
        representative = min(
            members,
            key=lambda crossing: (
                rank[crossing.highway],
                -crossing.length_outside_m,
                crossing.way_id,
                crossing.crossing_id,
            ),
        )
        exits.append(Exit(representative=representative, members=members))
    return exits


def require_exits(exit_count: int, lad_code: str) -> None:
    """Refuse to emit a scenario with nowhere to evacuate to.

    A district whose roads all fail qualification is a real possibility — an
    island, or a boundary the road extract does not reach across — and an empty
    destination set would otherwise surface as an unexplained empty export rather
    than as the modelling problem it is.
    """
    if exit_count == 0:
        raise ValueError(
            f"no district exit qualifies for {lad_code}: no drivable through "
            "route crosses its boundary and continues far enough beyond it, so "
            "no vehicle has anywhere to evacuate to. See district_exits, "
            "district_boundary_crossings and scenario_shortfalls."
        )
