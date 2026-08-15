"""OpenStreetMap road centrelines via the Overpass API.

Only line geometry is fetched. No routed driving graph is built in this
iteration: roads exist solely so each vehicle can be snapped to a parking point
beside its owner's home (see design.md). The downstream traffic microsimulation
owns the routed model.
"""

from __future__ import annotations

import time

import pandas as pd
import requests

# Public Overpass instances, tried in order. The main one is frequently
# saturated and answers 504, so mirrors are a practical necessity rather than a
# nicety.
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
)

# Overpass rejects requests without an identifying User-Agent with 406.
_HEADERS = {
    "User-Agent": "london-frontline-2026/0.1 (evacuation planning research)",
}

_ATTEMPTS_PER_ENDPOINT = 2

# Highway classes a car can drive on. Footways, cycleways and tracks are
# excluded: parking a car on them would be meaningless.
DRIVABLE_HIGHWAY_CLASSES = (
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "service",
)


def _post_with_fallback(query: str, timeout: int) -> dict:
    """POST an Overpass query, moving to the next mirror when one is unavailable.

    504 and 429 mean the instance is busy rather than the query being wrong, so
    they are retried and then failed over rather than raised immediately.
    """
    errors: list[str] = []
    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(_ATTEMPTS_PER_ENDPOINT):
            try:
                response = requests.post(
                    endpoint, data={"data": query}, headers=_HEADERS, timeout=timeout
                )
            except requests.RequestException as error:
                errors.append(f"{endpoint}: {error}")
                continue
            if response.status_code in (429, 502, 503, 504):
                errors.append(f"{endpoint}: HTTP {response.status_code}")
                time.sleep(2 ** attempt)
                continue
            response.raise_for_status()
            return response.json()
    raise RuntimeError(
        "Every Overpass endpoint failed:\n  " + "\n  ".join(errors)
    )


def fetch_drivable_roads(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    timeout: int = 600,
) -> pd.DataFrame:
    """Return drivable road centrelines in the bounding box as WKT linestrings."""
    classes = "|".join(DRIVABLE_HIGHWAY_CLASSES)
    query = f"""
    [out:json][timeout:{timeout}];
    way["highway"~"^({classes})$"]({min_lat},{min_lon},{max_lat},{max_lon});
    out geom;
    """
    payload = _post_with_fallback(query, timeout)

    rows = []
    for element in payload.get("elements", []):
        geometry = element.get("geometry")
        if not geometry or len(geometry) < 2:
            continue
        coordinates = ", ".join(
            f"{point['lon']} {point['lat']}" for point in geometry
        )
        rows.append(
            {
                "way_id": element["id"],
                "highway": element.get("tags", {}).get("highway"),
                "name": element.get("tags", {}).get("name"),
                "geometry_wkt": f"LINESTRING({coordinates})",
            }
        )
    if not rows:
        raise RuntimeError(
            "Overpass returned no drivable roads for the bounding box; check the "
            "box and that the API is not rate-limiting"
        )
    return pd.DataFrame(rows)


