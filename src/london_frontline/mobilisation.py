"""The mobilisation curve: when each vehicle actually sets off.

A fleet that departs at a single instant loads a microsimulation with an
insertion queue, and the clearance time that comes back describes the queue
rather than the road network. So departure is modelled as three terms:

    departure = notification + mobilisation delay + driver access time

The mobilisation delay is drawn per vehicle from a lognormal, which is
right-skewed: most people leave promptly, a long tail leaves much later, and it
is the tail that governs clearance. The driver access time is how long the
driver needs to reach the car — near zero while leaders are drawn from the owner
household, and material once they are not.

Kept free of Dagster and DuckDB so the draw can be tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class MobilisationProfile:
    """How long after notification a vehicle is ready to move.

    ``median_minutes`` is the median of the lognormal, not the mean of its
    underlying normal — the median is the parameter with a direct reading
    ("half the fleet has moved by then"), which is what makes it worth
    calibrating against response literature.

    ``sigma`` is the standard deviation of the underlying normal. At 0 the
    distribution is degenerate and every vehicle mobilises after exactly
    ``median_minutes``.
    """

    median_minutes: float
    sigma: float

    def __post_init__(self) -> None:
        if self.median_minutes < 0:
            raise ValueError(
                f"mobilisation median must not be negative, got {self.median_minutes}"
            )
        if self.sigma < 0:
            raise ValueError(f"mobilisation sigma must not be negative, got {self.sigma}")


def mobilisation_delays_seconds(
    vehicle_ids: Sequence[int], profile: MobilisationProfile, seed: int
) -> np.ndarray:
    """Draw one mobilisation delay per vehicle, in seconds.

    Delays are drawn in ascending vehicle order and then mapped back onto the
    order given, so the same fleet and seed produce the same delay for the same
    vehicle however the caller happened to sort its rows.
    """
    identifiers = np.asarray(vehicle_ids)
    if identifiers.size == 0:
        return np.empty(0, dtype=float)
    if len(np.unique(identifiers)) != identifiers.size:
        raise ValueError("vehicle_ids must be unique; a vehicle departs once")

    ascending = np.argsort(identifiers, kind="stable")
    generator = np.random.default_rng(seed)
    # Median parameterisation: exp(mu) is the lognormal's median, so drawing
    # median * exp(sigma * Z) puts the median exactly where it is configured.
    # sigma = 0 collapses this to the median for every vehicle.
    drawn = profile.median_minutes * np.exp(
        profile.sigma * generator.standard_normal(identifiers.size)
    )

    delays = np.empty(identifiers.size, dtype=float)
    delays[ascending] = drawn * 60.0
    return delays


def departure_offsets_seconds(
    vehicle_ids: Sequence[int],
    access_seconds: Sequence[float],
    profile: MobilisationProfile,
    seed: int,
    include_driver_access_time: bool = True,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Seconds after notification that each vehicle departs.

    Returns the total offset alongside the two terms it is made of, so the asset
    can record why a vehicle departs when it does rather than only when.
    """
    delays = mobilisation_delays_seconds(vehicle_ids, profile, seed)

    access = np.asarray(access_seconds, dtype=float)
    if access.size != delays.size:
        raise ValueError(
            f"got {access.size} access times for {delays.size} vehicles"
        )
    if np.any(access < 0):
        raise ValueError("driver access time must not be negative")

    applied = access if include_driver_access_time else np.zeros_like(access)
    return delays + applied, delays, applied
