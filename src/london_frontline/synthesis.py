"""Sampling primitives for marginal-constrained synthesis.

The design calls for independent per-attribute sampling constrained to published
marginals — not IPF, which would need a seed contingency table this pipeline
deliberately does not have. Concretely: each attribute's generated counts match
its published marginal as closely as integer rounding allows, and attributes are
paired at random, so the joint distribution is the product of the marginals.
"""

from __future__ import annotations

import re
from typing import Sequence

import numpy as np

# Ofcom reserves 07700 900000-900999 for drama and fiction, so a generated phone
# number can never collide with a real subscriber.
_FICTIONAL_PHONE_PREFIX = "07700 900"
_FICTIONAL_PHONE_RANGE = 1000

_FIRST_NAMES = (
    "Alice Amelia Ana Arthur Ayesha Ben Blake Cai Chloe Daniel Darcy Ehsan Eleanor "
    "Elias Emily Erin Ewan Farah Finlay Freya George Grace Hana Harry Hugo Ida Isaac "
    "Ivy Jack Jamal Jasmine Joel Kai Keira Kofi Lara Leon Lily Logan Maja Marcus Maya "
    "Mohammed Nadia Nathan Niamh Noah Olive Oscar Priya Rhys Rosa Ruby Sam Sana Seren "
    "Sofia Theo Tomas Uma Vikram Willow Yusuf Zara Zoe"
).split()

_LAST_NAMES = (
    "Abbott Ahmed Bailey Barnes Begum Bennett Brooks Campbell Carter Chen Clarke Cole "
    "Cooper Davies Dixon Ellis Evans Fisher Foster Gibson Graham Green Hall Harris "
    "Hughes Hunter Jenkins Jones Kaur Kelly Khan Knight Lewis Lloyd Marshall Mason "
    "Miller Mitchell Morgan Murray Newton Nolan Osei Owen Palmer Parker Patel Perry "
    "Price Read Reid Roberts Rose Shah Sharma Simpson Singh Stone Taylor Thomas Turner "
    "Walsh Ward Watson Webb Wells Wright Young"
).split()


<<<<<<< Updated upstream
def apportion(weights: Sequence[float], total: int) -> np.ndarray:
    """Split ``total`` across ``weights`` by largest remainder.

    Exposed separately from :func:`allocate_counts` because constrained
    assignment needs the per-category counts, not a shuffled label array.
    """
    if total <= 0:
        return np.zeros(len(weights), dtype=int)
    weight_array = np.asarray(weights, dtype=float)
    if weight_array.sum() <= 0:
        weight_array = np.ones_like(weight_array)
    shares = weight_array / weight_array.sum() * total
    counts = np.floor(shares).astype(int)
    shortfall = total - counts.sum()
    if shortfall > 0:
        for index in np.argsort(-(shares - counts))[:shortfall]:
            counts[index] += 1
    return counts


def assign_cars_capped_by_adults(
    rng: np.random.Generator,
    car_values: Sequence[int],
    category_counts: Sequence[int],
    adults: np.ndarray,
) -> tuple[np.ndarray, dict[int, int]]:
    """Give each household a car count without exceeding its adult count.

    ``car_values`` are the cars implied by each published category and
    ``category_counts`` how many households belong in each. Households are drawn
    at random from those *eligible* to hold a count — meaning they have at least
    that many adults — rather than ranked by size, which would make every
    three-car household one of the district's largest and is equally untrue.

    Counts are placed high-first, because a household eligible for three cars is
    also eligible for one but not the reverse. Where an Output Area has too few
    multi-adult households to absorb its published marginal, the cap is kept and
    the unplaced households are returned as a shortfall rather than the cap being
    quietly breached.
    """
    household_count = len(adults)
    cars = np.zeros(household_count, dtype=int)
    assigned = np.zeros(household_count, dtype=bool)
    shortfall: dict[int, int] = {}

    order = sorted(
        ((value, count) for value, count in zip(car_values, category_counts) if value > 0),
        key=lambda item: -item[0],
    )
    for value, needed in order:
        if needed <= 0:
            continue
        eligible = np.flatnonzero((~assigned) & (adults >= value))
        take = min(needed, len(eligible))
        if take:
            chosen = rng.choice(eligible, size=take, replace=False)
            cars[chosen] = value
            assigned[chosen] = True
        if take < needed:
            shortfall[value] = needed - take
    return cars, shortfall


def assign_car_licences(
    rng: np.random.Generator,
    is_adult: np.ndarray,
    household_position: np.ndarray,
    cars: np.ndarray,
    target_licences: int,
) -> tuple[np.ndarray, int]:
    """Assign car licences so every car-owning household can drive its cars.

    Each household first takes as many licences as it has cars, capped by its
    adults; the remaining budget is spread at random across other adults so the
    overall licence-holding proportion still matches. Licences are redistributed,
    never invented — if covering the cars already exceeds the target, the excess
    is returned so the conflict is reported rather than absorbed.
    """
    licensed = np.zeros(len(is_adult), dtype=bool)
    adults_by_household: dict[int, list[int]] = {}
    for position, (household, adult) in enumerate(zip(household_position, is_adult)):
        if adult:
            adults_by_household.setdefault(int(household), []).append(position)

    for household, count in enumerate(cars):
        if count <= 0:
            continue
        members = adults_by_household.get(household)
        if not members:
            continue
        required = min(int(count), len(members))
        for position in rng.choice(members, size=required, replace=False):
            licensed[position] = True

    required_total = int(licensed.sum())
    remaining = target_licences - required_total
    if remaining > 0:
        candidates = np.flatnonzero(is_adult & ~licensed)
        take = min(remaining, len(candidates))
        if take:
            licensed[rng.choice(candidates, size=take, replace=False)] = True

    return licensed, max(0, required_total - target_licences)


=======
>>>>>>> Stashed changes
def allocate_counts(
    rng: np.random.Generator,
    labels: Sequence[str],
    weights: Sequence[float],
    total: int,
) -> np.ndarray:
    """Return ``total`` labels whose counts follow ``weights`` as closely as possible.

    Uses largest-remainder apportionment rather than independent draws, so the
    generated counts match the marginal exactly (up to the rounding the marginal
    itself forces) instead of only in expectation. The result is shuffled, so
    pairing it against another allocation gives an independent joint distribution.
    """
    if total <= 0:
        return np.empty(0, dtype=object)

    weight_array = np.asarray(weights, dtype=float)
    if weight_array.sum() <= 0:
        # A marginal that is entirely zero carries no information; fall back to a
        # uniform split rather than failing the whole Output Area.
        weight_array = np.ones_like(weight_array)
<<<<<<< Updated upstream

    counts = apportion(weight_array, total)
=======
    shares = weight_array / weight_array.sum() * total

    counts = np.floor(shares).astype(int)
    shortfall = total - counts.sum()
    if shortfall > 0:
        # Hand the remaining units to the largest fractional parts.
        remainders = shares - counts
        for index in np.argsort(-remainders)[:shortfall]:
            counts[index] += 1
>>>>>>> Stashed changes

    allocation = np.repeat(np.asarray(labels, dtype=object), counts)
    rng.shuffle(allocation)
    return allocation


def leading_int(text: str, default: int) -> int:
    """Extract the first integer in a category label, e.g. "3 people" -> 3."""
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else default


def age_from_band(rng: np.random.Generator, band_label: str) -> int:
    """Draw a single-year age uniformly within a five-year band label.

    TS007A labels look like "Aged 5 to 9 years" or "Aged 90 years and over"; the
    open-ended top band is drawn from a bounded tail so ages stay plausible.
    """
    numbers = [int(n) for n in re.findall(r"\d+", band_label)]
    if not numbers:
        return int(rng.integers(0, 90))
    if len(numbers) == 1:
        lower = numbers[0]
        # "Aged under 5" style labels are an upper bound, not a lower one.
        if "under" in band_label.lower():
            return int(rng.integers(0, lower))
        return int(rng.integers(lower, lower + 10))
    lower, upper = numbers[0], numbers[1]
    return int(rng.integers(lower, upper + 1))


def phone_numbers(rng: np.random.Generator, count: int, missing: float) -> list:
    """Generate fictional phone numbers, leaving a share explicitly absent.

    Absence is ``None``, never a placeholder that reads like a number, so the
    downstream notification fallback can detect it.
    """
    numbers = rng.integers(0, _FICTIONAL_PHONE_RANGE, size=count)
    absent = rng.random(count) < missing
    return [
        None if is_absent else f"{_FICTIONAL_PHONE_PREFIX}{value:03d}"
        for value, is_absent in zip(numbers, absent)
    ]


def names(rng: np.random.Generator, count: int) -> list[str]:
    """Generate display names for identification at a meeting point."""
    first = rng.choice(_FIRST_NAMES, size=count)
    last = rng.choice(_LAST_NAMES, size=count)
    return [f"{f} {l}" for f, l in zip(first, last)]
