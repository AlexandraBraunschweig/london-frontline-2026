"""Census 2021 marginal tables from the NOMIS API.

NOMIS exposes each Census table as a dataset id (``NM_nnnn_1``) and lets a whole
Local Authority District's Output Areas be requested in one call with the
geography selector ``<LAD code>TYPE150`` (TYPE150 being 2021 Output Areas).
TYPE151 is the corresponding LSOA selector, used for the suppression fallback.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pandas as pd
import requests

NOMIS_BASE = "https://www.nomisweb.co.uk/api/v01/dataset"

# NOMIS geography type codes.
OA_TYPE = "TYPE150"
LSOA_TYPE = "TYPE151"

# Only the "Value" measure; NOMIS also publishes percentages we do not want.
VALUE_MEASURE = "20100"

# NOMIS encodes a table's category hierarchy in the category code. The overall
# total is "0"; intermediate aggregates get four-digit codes ("1001", "1002");
# leaf categories are prefixed with an underscore ("_1", "_2"). Only the leaves
# are a partition of the total — summing every non-total row double-counts any
# table with a hierarchy, such as TS003 household composition and TS038
# disability, which nest sub-categories under broader ones.
TOTAL_CATEGORY_CODE = "0"
LEAF_CODE_PREFIX = "_"


@dataclass(frozen=True)
class CensusTable:
    """A Census 2021 table this pipeline depends on."""

    key: str
    dataset_id: str
    census_id: str
    description: str


# The marginals synthesis needs. Age and sex are pulled as separate marginals
# rather than a joint table, which is what independent per-attribute sampling
# consumes (see design.md, population-synthesis method).
CENSUS_TABLES: dict[str, CensusTable] = {
    "age": CensusTable("age", "NM_2020_1", "TS007A", "Age by five-year age bands"),
    "sex": CensusTable("sex", "NM_2028_1", "TS008", "Sex"),
    "composition": CensusTable(
        "composition", "NM_2023_1", "TS003", "Household composition"
    ),
    "household_size": CensusTable(
        "household_size", "NM_2037_1", "TS017", "Household size"
    ),
    "cars": CensusTable("cars", "NM_2063_1", "TS045", "Car or van availability"),
    "disability": CensusTable("disability", "NM_2056_1", "TS038", "Disability"),
}

# Columns NOMIS returns for every table; anything else is the table's own
# category dimension, whose column name differs per table (C2021_CARS_5,
# C2021_AGE_19, ...). Detecting it rather than hardcoding keeps this working
# when a table is swapped for an equivalent.
_STRUCTURAL_PREFIXES = ("DATE", "GEOGRAPHY", "MEASURES", "OBS_", "URN", "RECORD_")


def _dimension_column(frame: pd.DataFrame) -> str:
    """Return the name of the table's category dimension, e.g. ``C2021_CARS_5``."""
    candidates = {
        column[: -len("_NAME")]
        for column in frame.columns
        if column.endswith("_NAME")
        and not column.startswith(_STRUCTURAL_PREFIXES)
    }
    if len(candidates) != 1:
        raise ValueError(
            f"Expected exactly one category dimension, found {sorted(candidates)}"
        )
    return next(iter(candidates))


def fetch_marginal(
    table: CensusTable,
    area_code: str,
    geography_type: str = OA_TYPE,
    timeout: int = 300,
) -> pd.DataFrame:
    """Fetch one Census table for every area of ``geography_type`` in ``area_code``.

    Returns a tidy frame: one row per (area, category) with the observed count and
    the ONS observation status, which the suppression check reads.
    """
    url = f"{NOMIS_BASE}/{table.dataset_id}.data.csv"
    params = {
        "geography": f"{area_code}{geography_type}",
        "measures": VALUE_MEASURE,
    }
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    raw = pd.read_csv(io.StringIO(response.text), low_memory=False)

    dimension = _dimension_column(raw)
    category_code = raw[f"{dimension}_CODE"].astype(str)
    tidy = pd.DataFrame(
        {
            "area_code": raw["GEOGRAPHY_CODE"],
            # Not named `table`: that is a reserved word in DuckDB.
            "census_table": table.census_id,
            "category_code": category_code,
            "category_name": raw[f"{dimension}_NAME"],
            "category_order": raw[f"{dimension}_SORTORDER"],
            "value": raw["OBS_VALUE"],
            "obs_status": raw["OBS_STATUS"],
            "is_total": category_code == TOTAL_CATEGORY_CODE,
            "is_leaf": category_code.str.startswith(LEAF_CODE_PREFIX),
        }
    )
    return tidy
