"""Resolving a stop's coordinates to something a person can read.

The plan has no addresses in it and cannot have: `home-location-assignment`
places households on NSUL UPRNs, and NSUL publishes a UPRN, a coordinate and a
set of geography codes — no street, no house number. Addresses live in
AddressBase, which is licensed separately.

So an address here is looked up, not stored, by reverse geocoding the stop's
coordinate against OpenStreetMap's Nominatim. That is fine for the handful of
lookups a demonstration makes and is emphatically not fine for the fleet:
Nominatim's usage policy allows roughly one request a second and no bulk work,
and 127,919 notifications would be an abuse of it. Dispatching for real means
licensing AddressBase and joining on the UPRN the plan already carries.

The same lookup answers a second question: whether a UPRN is a home at all.
NSUL has no property classification, so a household can be placed on a play
area or a substation. A reverse geocode that comes back without a house number
is the signal.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from london_frontline.paths import resolve as resolve_path

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"

# Nominatim's usage policy requires an identifying User-Agent and at most one
# request a second. Both are conditions of use, not tuning knobs.
USER_AGENT = "london-frontline-2026/0.1 (evacuation planning; contact via repo)"
MINIMUM_REQUEST_INTERVAL_S = 1.1

DEFAULT_CACHE = "data/address_cache.json"

# Coordinates are rounded to about a metre before being used as a cache key, so
# that re-rendering the same stop never re-requests it.
_KEY_PRECISION = 5


@dataclass(frozen=True)
class Address:
    """One reverse-geocoded location."""

    display_name: str
    house_number: str | None
    road: str | None
    postcode: str | None
    category: str | None
    kind: str | None

    @property
    def is_a_dwelling(self) -> bool:
        """Whether this reads as somebody's front door.

        A house number is the test. Without one the coordinate landed on a road,
        a field or a play area, which is a UPRN the plan should not have treated
        as a home.
        """
        return bool(self.house_number and self.road)

    def short(self) -> str:
        """The address as a message would print it."""
        if self.is_a_dwelling:
            head = f"{self.house_number} {self.road}"
        elif self.road:
            head = self.road
        else:
            return self.display_name.split(",")[0]
        return f"{head}, {self.postcode}" if self.postcode else head


class Resolver:
    """Reverse geocoder with an on-disk cache and the mandated rate limit."""

    def __init__(self, cache_path: str | Path = DEFAULT_CACHE, *, offline: bool = False):
        self.path = resolve_path(cache_path)
        self.offline = offline
        self._last_request = 0.0
        self._cache: dict[str, dict | None] = {}
        if self.path.exists():
            self._cache = json.loads(self.path.read_text())

    def _key(self, latitude: float, longitude: float) -> str:
        return f"{latitude:.{_KEY_PRECISION}f},{longitude:.{_KEY_PRECISION}f}"

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._cache, indent=0, sort_keys=True))

    def _fetch(self, latitude: float, longitude: float) -> dict | None:
        wait = MINIMUM_REQUEST_INTERVAL_S - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        response = requests.get(
            NOMINATIM_URL,
            params={
                "lat": latitude,
                "lon": longitude,
                "format": "jsonv2",
                "zoom": 18,
                "addressdetails": 1,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        self._last_request = time.monotonic()
        if not response.ok:
            return None
        payload = response.json()
        return None if "error" in payload else payload

    def lookup(self, latitude: float, longitude: float) -> Address | None:
        """The address at a coordinate, or None if nothing could be resolved."""
        key = self._key(latitude, longitude)
        if key not in self._cache:
            if self.offline:
                return None
            self._cache[key] = self._fetch(latitude, longitude)
            self._save()

        payload = self._cache[key]
        if payload is None:
            return None
        parts = payload.get("address", {})
        return Address(
            display_name=payload.get("display_name", ""),
            house_number=parts.get("house_number"),
            road=parts.get("road"),
            postcode=parts.get("postcode"),
            category=payload.get("category"),
            kind=payload.get("type"),
        )

    def line(self, latitude: float, longitude: float) -> str | None:
        """The printable address, for use as a `Place` resolver."""
        address = self.lookup(latitude, longitude)
        return None if address is None else address.short()

    def is_a_dwelling(self, latitude: float, longitude: float) -> bool:
        address = self.lookup(latitude, longitude)
        return address is not None and address.is_a_dwelling
