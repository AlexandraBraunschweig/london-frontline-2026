"""Dagster resources: the spatial DuckDB warehouse every asset reads and writes."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import duckdb
from dagster import ConfigurableResource

from london_frontline.paths import resolve


class SpatialDuckDBResource(ConfigurableResource):
    """A DuckDB connection with the spatial extension installed and loaded.

    Assets use it as a context manager::

        with duckdb_.connect() as conn:
            conn.execute("CREATE OR REPLACE TABLE ... AS SELECT ...")

    Every connection loads `spatial`, so assets never have to remember to.
    """

    database: str = "data/warehouse.duckdb"
    # Readers should set this. DuckDB permits one writer or many readers, so a
    # read-only consumer that opens for writing locks the pipeline out of its
    # own database for as long as it is attached.
    read_only: bool = False

    @contextmanager
    def connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        path = resolve(self.database)
        if self.read_only:
            if not path.exists():
                raise FileNotFoundError(f"No warehouse at {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = duckdb.connect(str(path), read_only=self.read_only)
        try:
            conn.execute("INSTALL spatial;")
            conn.execute("LOAD spatial;")
            yield conn
        finally:
            conn.close()
