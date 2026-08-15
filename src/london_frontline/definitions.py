"""Dagster code location for the planning pipeline."""

from dagster import Definitions, in_process_executor, load_assets_from_package_module

from london_frontline import assets
from london_frontline.config import PlanningConfig
from london_frontline.resources import SpatialDuckDBResource

defs = Definitions(
    assets=load_assets_from_package_module(assets),
    resources={
        "planning": PlanningConfig(),
        "warehouse": SpatialDuckDBResource(),
    },
    # Every asset reads and writes the one DuckDB file, and DuckDB permits a
    # single writer process. Dagster's default multiprocess executor would run
    # independent assets concurrently and deadlock on the file lock, so the whole
    # pipeline runs in one process.
    executor=in_process_executor,
)
