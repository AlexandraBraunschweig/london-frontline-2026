"""The departure draw (tasks 2.4, 4.4, 4.6).

These run without a warehouse: the draw is pure, which is why it lives outside
the asset.
"""

import numpy as np
import pytest

from london_frontline.mobilisation import (
    MobilisationProfile,
    departure_offsets_seconds,
    mobilisation_delays_seconds,
)

SEED = 20260815
VEHICLES = [7, 1, 99, 42, 13]
ACCESS = [30.0, 0.0, 120.0, 0.0, 5.0]


def test_no_spread_and_no_access_time_departs_the_whole_fleet_at_notification():
    """Task 2.4: the single fleet-wide departure stays reachable by configuration.

    It is the degenerate case, not a separate code path — a lognormal with no
    spread and a zero median, with driver access time switched off.
    """
    offsets, _, _ = departure_offsets_seconds(
        VEHICLES,
        ACCESS,
        MobilisationProfile(median_minutes=0.0, sigma=0.0),
        seed=SEED,
        include_driver_access_time=False,
    )
    assert np.all(offsets == 0.0)


def test_no_spread_alone_mobilises_the_fleet_together_at_the_median():
    """Spread is what staggers the fleet; the median only shifts it."""
    profile = MobilisationProfile(median_minutes=15.0, sigma=0.0)
    delays = mobilisation_delays_seconds(VEHICLES, profile, seed=SEED)
    assert np.allclose(delays, 15.0 * 60.0)


def test_spread_staggers_departures():
    """Guard against the draw silently collapsing — the tail is the point."""
    profile = MobilisationProfile(median_minutes=15.0, sigma=0.6)
    delays = mobilisation_delays_seconds(range(500), profile, seed=SEED)
    assert delays.std() > 0
    # Lognormal: the mean sits above the median, and the far tail well above it.
    assert delays.mean() > np.median(delays)
    assert delays.max() > 2 * np.median(delays)


def test_same_seed_reproduces_every_departure():
    """Task 4.4: two runs with the same seed and config agree vehicle by vehicle."""
    profile = MobilisationProfile(median_minutes=15.0, sigma=0.6)
    first, _, _ = departure_offsets_seconds(VEHICLES, ACCESS, profile, seed=SEED)
    second, _, _ = departure_offsets_seconds(VEHICLES, ACCESS, profile, seed=SEED)
    assert np.array_equal(first, second)


def test_a_different_seed_gives_a_different_draw():
    profile = MobilisationProfile(median_minutes=15.0, sigma=0.6)
    first = mobilisation_delays_seconds(VEHICLES, profile, seed=SEED)
    second = mobilisation_delays_seconds(VEHICLES, profile, seed=SEED + 1)
    assert not np.array_equal(first, second)


def test_delay_follows_the_vehicle_not_the_row_order():
    """Reordering the input must not reshuffle who waits how long."""
    profile = MobilisationProfile(median_minutes=15.0, sigma=0.6)
    straight = mobilisation_delays_seconds(VEHICLES, profile, seed=SEED)
    shuffled_ids = list(reversed(VEHICLES))
    shuffled = mobilisation_delays_seconds(shuffled_ids, profile, seed=SEED)

    by_vehicle = dict(zip(VEHICLES, straight))
    for vehicle_id, delay in zip(shuffled_ids, shuffled):
        assert delay == by_vehicle[vehicle_id]


def test_departures_are_never_earlier_than_notification():
    """Task 4.6: every term is non-negative, so no vehicle leaves before the order."""
    profile = MobilisationProfile(median_minutes=15.0, sigma=0.6)
    offsets, delays, access = departure_offsets_seconds(
        range(2000), [0.0] * 2000, profile, seed=SEED
    )
    assert np.all(offsets >= 0)
    assert np.all(delays >= 0)
    assert np.all(access >= 0)


def test_access_time_is_added_when_included_and_dropped_when_not():
    """Task 4.5's mechanism: access time is a term, not baked into the draw."""
    profile = MobilisationProfile(median_minutes=15.0, sigma=0.6)
    with_access, delays_a, applied = departure_offsets_seconds(
        VEHICLES, ACCESS, profile, seed=SEED, include_driver_access_time=True
    )
    without, delays_b, dropped = departure_offsets_seconds(
        VEHICLES, ACCESS, profile, seed=SEED, include_driver_access_time=False
    )
    assert np.array_equal(delays_a, delays_b), "the draw must not depend on the switch"
    assert np.array_equal(applied, np.asarray(ACCESS))
    assert np.all(dropped == 0.0)
    assert np.allclose(with_access - without, ACCESS)


def test_a_driver_already_at_the_car_waits_only_to_mobilise():
    """Task 4.5: zero access time leaves notification plus mobilisation alone."""
    profile = MobilisationProfile(median_minutes=15.0, sigma=0.6)
    offsets, delays, _ = departure_offsets_seconds(
        VEHICLES, [0.0] * len(VEHICLES), profile, seed=SEED
    )
    assert np.array_equal(offsets, delays)


@pytest.mark.parametrize(
    "kwargs", [{"median_minutes": -1.0, "sigma": 0.6}, {"median_minutes": 15.0, "sigma": -0.1}]
)
def test_negative_profile_parameters_are_rejected(kwargs):
    with pytest.raises(ValueError):
        MobilisationProfile(**kwargs)


def test_negative_access_time_is_rejected():
    profile = MobilisationProfile(median_minutes=15.0, sigma=0.6)
    with pytest.raises(ValueError):
        departure_offsets_seconds([1, 2], [0.0, -5.0], profile, seed=SEED)


def test_duplicate_vehicles_are_rejected():
    """A vehicle departs once; two rows for one car would mean two departures."""
    profile = MobilisationProfile(median_minutes=15.0, sigma=0.6)
    with pytest.raises(ValueError):
        mobilisation_delays_seconds([1, 1, 2], profile, seed=SEED)


def test_mismatched_access_length_is_rejected():
    profile = MobilisationProfile(median_minutes=15.0, sigma=0.6)
    with pytest.raises(ValueError):
        departure_offsets_seconds([1, 2, 3], [0.0, 1.0], profile, seed=SEED)
