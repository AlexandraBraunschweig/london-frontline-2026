"""Co-resident parenthood and the mandatory dependency edges derived from it.

Marginal-only synthesis produces household membership and ages but no kinship, so
parenthood is derived by rule: a child's co-resident parents are the one or two
oldest household members at least a configured age gap older than the child.

Kept free of Dagster and DuckDB so the rules can be tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

RELATIONSHIP_DEPENDENT_OF = "dependent_of"

# A child has at most two co-resident parents.
MAX_CO_RESIDENT_PARENTS = 2


@dataclass(frozen=True)
class Member:
    """A household member, reduced to what the dependency rules need."""

    person_id: int
    age: int


def co_resident_parents(
    members: Sequence[Member], child: Member, minimum_age_gap: int
) -> list[int]:
    """Return the person ids treated as ``child``'s co-resident parents.

    The one or two oldest members at least ``minimum_age_gap`` years older than
    the child. Empty when no member qualifies — that household is recorded rather
    than given an implausible parent.
    """
    candidates = [
        member
        for member in members
        if member.person_id != child.person_id
        and member.age - child.age >= minimum_age_gap
    ]
    # Oldest first, tie-broken by id so the result does not depend on input order.
    candidates.sort(key=lambda member: (-member.age, member.person_id))
    return [member.person_id for member in candidates[:MAX_CO_RESIDENT_PARENTS]]


def household_dependency_edges(
    members: Sequence[Member],
    minimum_age_gap: int,
    both_parents_below_age: int,
    one_parent_below_age: int,
) -> tuple[list[tuple[int, int]], list[int]]:
    """Derive mandatory ``dependent_of`` edges for one household.

    Returns the edges as ``(dependent_person_id, parent_person_id)`` pairs, and
    the ids of children under the dependency age for whom no co-resident parent
    qualified.

    The age bands are the spec's: under ``both_parents_below_age`` a child is
    bound to both co-resident parents; from there up to ``one_parent_below_age``
    to one; at or above ``one_parent_below_age`` a person carries no mandatory
    edge and is free to be seated in another family's vehicle.
    """
    edges: list[tuple[int, int]] = []
    orphaned: list[int] = []

    for child in members:
        if child.age >= one_parent_below_age:
            continue
        parents = co_resident_parents(members, child, minimum_age_gap)
        if not parents:
            orphaned.append(child.person_id)
            continue
        if child.age < both_parents_below_age:
            edges.extend((child.person_id, parent) for parent in parents)
        else:
            edges.append((child.person_id, parents[0]))

    return edges, orphaned


def travel_groups(
    person_ids: Iterable[int], edges: Iterable[tuple[int, int]]
) -> dict[int, int]:
    """Map each person to a travel-group id, one per connected component.

    Groups are the connected components of the mandatory dependency edges. A
    person with no mandatory edge — anyone at or above the dependency age, or a
    child with no qualifying parent — forms a group of one.
    """
    import networkx as nx

    graph = nx.Graph()
    graph.add_nodes_from(person_ids)
    graph.add_edges_from(edges)

    assignment: dict[int, int] = {}
    for group_id, component in enumerate(nx.connected_components(graph)):
        for person_id in component:
            assignment[person_id] = group_id
    return assignment
