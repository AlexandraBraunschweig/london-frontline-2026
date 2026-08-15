"""Output Area codes and boundaries from the ONS Open Geography Portal.

Two services are used:

* ``OA21_LAD22_LSOA21_MSOA21_LEP22_EN_LU_V2`` — the OA to LSOA to LAD lookup,
  which is how the pipeline learns which Output Areas belong to a LAD and which
  LSOA each one sits in (the latter feeds the suppression fallback).
* ``Output_Areas_2021_EW_BGC_V2`` — generalised-clipped OA boundaries. The
  generalised version is deliberate: boundaries are used for mapping and area
  context, never for allocating UPRNs, so full-resolution geometry would be
  weight without purpose.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import pandas as pd
import requests

ARCGIS_BASE = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"

OA_LOOKUP_SERVICE = "OA21_LAD22_LSOA21_MSOA21_LEP22_EN_LU_V2"
OA_BOUNDARY_SERVICE = "Output_Areas_2021_EW_BGC_V2"

_PAGE_SIZE = 2000


def _paged_query(
    service: str,
    where: str,
    out_fields: str,
    return_geometry: bool,
    timeout: int = 180,
) -> Iterator[dict[str, Any]]:
    """Yield features from an ArcGIS FeatureServer layer, following pagination.

    ArcGIS caps a single response (commonly at 2000 features) and signals more
    with ``exceededTransferLimit``; without following that, a LAD with more
    Output Areas than the cap would silently return a truncated list.
    """
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": str(return_geometry).lower(),
            "outSR": "4326",
            "f": "geojson" if return_geometry else "json",
            "resultOffset": offset,
            "resultRecordCount": _PAGE_SIZE,
        }
        # POSTed rather than GETed: a LAD's worth of OA codes in an IN (...)
        # clause overruns the URL length the hosted service accepts, which it
        # reports as a 404 rather than a 414.
        response = requests.post(
            f"{ARCGIS_BASE}/{service}/FeatureServer/0/query",
            data=params,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            raise RuntimeError(f"ArcGIS error from {service}: {payload['error']}")

        features = payload.get("features", [])
        if not features:
            return
        yield from features

        if not payload.get("exceededTransferLimit") and len(features) < _PAGE_SIZE:
            return
        offset += len(features)


def fetch_oa_lookup(lad_code: str) -> pd.DataFrame:
    """Return one row per Output Area in ``lad_code``, with its LSOA and LAD."""
    features = _paged_query(
        OA_LOOKUP_SERVICE,
        where=f"LAD22CD = '{lad_code}'",
        out_fields="OA21CD,LSOA21CD,LSOA21NM,MSOA21CD,LAD22CD,LAD22NM",
        return_geometry=False,
    )
    rows = [feature["attributes"] for feature in features]
    if not rows:
        raise RuntimeError(
            f"No Output Areas returned for LAD {lad_code}. Check the code is a "
            f"current English LAD present in {OA_LOOKUP_SERVICE}."
        )
    return pd.DataFrame(rows)


def fetch_oa_boundaries(oa_codes: list[str], batch_size: int = 150) -> pd.DataFrame:
    """Return OA boundary geometry as WKT, one row per Output Area.

    Codes are requested in batches because the ``IN (...)`` clause goes into a
    URL, which has a practical length limit well below a LAD's OA count.
    """
    rows: list[dict[str, Any]] = []
    for start in range(0, len(oa_codes), batch_size):
        batch = oa_codes[start : start + batch_size]
        quoted = ",".join(f"'{code}'" for code in batch)
        for feature in _paged_query(
            OA_BOUNDARY_SERVICE,
            where=f"OA21CD IN ({quoted})",
            out_fields="OA21CD",
            return_geometry=True,
        ):
            rows.append(
                {
                    "oa_code": feature["properties"]["OA21CD"],
                    "geometry_geojson": json.dumps(feature["geometry"]),
                }
            )
    return pd.DataFrame(rows)
