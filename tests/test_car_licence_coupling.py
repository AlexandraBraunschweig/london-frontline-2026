"""Car and licence coupling tests (tasks 1.4-1.6, 2.5-2.6).

The unit tests drive the pure primitives; the warehouse tests assert the
invariants hold across the real population.
"""

import numpy as np
import pytest

from london_frontline.synthesis import (
    apportion,
    assign_car_licences,
    assign_cars_capped_by_adults,
)
from london_frontline.resources import SpatialDuckDBResource

SEED = 1


def rng():
    return np.random.default_rng(SEED)


# --- car assignment -------------------------------------------------------


def test_no_household_holds_more_cars_than_adults():
    """Task 1.4."""
    adults = np.array([0, 1, 1, 2, 2, 3, 3, 4])
    cars, _ = assign_cars_capped_by_adults(
        rng(), [0, 1, 2, 3], [2, 3, 2, 1], adults
    )
    assert np.all(cars <= adults)


def test_household_with_no_adult_gets_no_cars():
    """Task 1.5."""
    adults = np.array([0, 0, 0, 3, 3])
    cars, _ = assign_cars_capped_by_adults(rng(), [0, 1, 2], [2, 2, 1], adults)
    assert np.all(cars[adults == 0] == 0)


def test_category_counts_preserved_when_feasible():
    """Task 1.6: the marginal is untouched when the cap can be satisfied."""
    adults = np.array([3, 3, 3, 2, 2, 2, 1, 1, 1, 0])
    counts = [4, 3, 2, 1]  # 4 households with 0 cars, 3 with 1, 2 with 2, 1 with 3
    cars, shortfall = assign_cars_capped_by_adults(
        rng(), [0, 1, 2, 3], counts, adults
    )
    assert shortfall == {}
    assert sorted(cars.tolist()) == sorted([0] * 4 + [1] * 3 + [2] * 2 + [3])


def test_shortfall_is_recorded_rather_than_breaching_the_cap():
    """Task 1.3: too few large households means a recorded shortfall, not a breach."""
    adults = np.array([1, 1, 1, 1])
    # Two households are meant to hold three cars, but none has three adults.
    cars, shortfall = assign_cars_capped_by_adults(
        rng(), [0, 3], [2, 2], adults
    )
    assert np.all(cars <= adults)
    assert shortfall == {3: 2}


def test_assignment_is_random_not_ranked():
    """Eligible households are chosen at random, not by descending size.

    A ranked assignment would make every multi-car household one of the largest,
    which is as untrue as the independence it replaces.
    """
    adults = np.array([4] * 20 + [2] * 20)
    chosen = set()
    for seed in range(8):
        cars, _ = assign_cars_capped_by_adults(
            np.random.default_rng(seed), [0, 3], [36, 4], adults
        )
        chosen.update(np.flatnonzero(cars == 3).tolist())
    # Across seeds, more than the four largest households receive three cars.
    assert len(chosen) > 4


# --- licences -------------------------------------------------------------


def test_every_car_owning_household_can_drive():
    """Task 2.5."""
    #   household 0: 2 adults, 2 cars   household 1: 1 adult, 1 car
    #   household 2: 3 adults, 0 cars
    is_adult = np.array([True, True, True, True, True, False])
    household = np.array([0, 0, 1, 2, 2, 2])
    cars = np.array([2, 1, 0])

    licensed, excess = assign_car_licences(rng(), is_adult, household, cars, 4)
    for index, count in enumerate(cars):
        if count == 0:
            continue
        drivers = licensed[household == index].sum()
        assert drivers >= count, f"household {index} has {drivers} drivers for {count} cars"
    assert excess == 0


def test_licence_budget_is_respected():
    """Task 2.6: the target is met when it exceeds what the cars require."""
    is_adult = np.array([True] * 10)
    household = np.array([0, 0, 1, 1, 2, 2, 3, 3, 4, 4])
    cars = np.array([1, 1, 0, 0, 0])

    licensed, excess = assign_car_licences(rng(), is_adult, household, cars, 7)
    assert licensed.sum() == 7
    assert excess == 0


def test_conflict_reported_when_cars_need_more_licences_than_the_target():
    """Task 2.3: the conflict surfaces instead of one constraint silently losing."""
    is_adult = np.array([True] * 6)
    household = np.array([0, 0, 1, 1, 2, 2])
    cars = np.array([2, 2, 2])

    licensed, excess = assign_car_licences(rng(), is_adult, household, cars, 2)
    assert licensed.sum() == 6, "every car must still be drivable"
    assert excess == 4, "the overshoot against the target is reported"


def test_children_never_hold_a_licence():
    is_adult = np.array([True, False, False])
    household = np.array([0, 0, 0])
    licensed, _ = assign_car_licences(rng(), is_adult, household, np.array([1]), 1)
    assert licensed.tolist() == [True, False, False]


def test_apportion_matches_the_total_exactly():
    counts = apportion([3.0, 1.0, 1.0], 100)
    assert counts.sum() == 100


# --- warehouse invariants -------------------------------------------------


@pytest.fixture(scope="module")
def conn():
    with SpatialDuckDBResource().connect() as connection:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        if "report_car_licence_coupling" not in tables:
            pytest.skip("warehouse not materialised; run the pipeline first")
        yield connection


def test_no_car_owning_household_lacks_a_driver(conn):
    stranded = conn.execute(
        "SELECT coalesce(sum(households_without_a_driver), 0) "
        "FROM report_car_licence_coupling WHERE num_cars > 0"
    ).fetchone()[0]
    assert stranded == 0


def test_no_household_exceeds_its_adults_in_the_warehouse(conn):
    over = conn.execute(
        "SELECT count(*) FROM households WHERE num_cars > num_adults"
    ).fetchone()[0]
    assert over == 0
