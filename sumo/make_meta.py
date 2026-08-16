"""Facts about the run itself, for the report header and its caveats.

Kept separate from the comparison so nothing in the page is a number typed by
hand: the population comes from the warehouse, the network size from the built
network, and the assumptions from the same config the pipeline ran under.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import duckdb

from london_frontline.config import PlanningConfig


def collect(
    warehouse: str = "data/warehouse.duckdb",
    net: str = "data/sumo/thanet.net.xml",
    sumo: str = ".venv/bin/sumo",
    date: str = "",
    out="data/sumo/meta.json",
) -> dict:
    """Facts about this run, written where the report and figures can read them."""

    planning = PlanningConfig()
    with duckdb.connect(warehouse, read_only=True) as conn:
        persons = conn.execute("SELECT count(*) FROM persons").fetchone()[0]
        households = conn.execute("SELECT count(*) FROM households").fetchone()[0]
        exits = conn.execute("SELECT count(*) FROM district_exits").fetchone()[0]
        # How the plan's fleet splits across the ways out. The nearest-exit rule
        # is the pipeline's own stated approximation, and this is where its cost
        # shows up, so the report states it rather than leaving it implied.
        exit_loading = conn.execute(
            """
            SELECT e.name, e.highway, count(*) AS vehicles
            FROM vehicle_departures d
            JOIN district_exits e USING (exit_id)
            GROUP BY e.name, e.highway
            ORDER BY vehicles DESC
            """
        ).fetchdf().to_dict("records")

    version = subprocess.run(
        [sumo, "--version"], capture_output=True, text=True
    ).stdout.splitlines()[0]
    version = version.replace("Eclipse SUMO sumo", "SUMO").strip()

    # Internal edges start with ':' and are junction internals, not roads.
    edges = len(re.findall(r'<edge id="[^:]', Path(net).read_text()))

    meta = {
        "persons": persons,
        "households": households,
        "edges": edges,
        "exits": exits,
        "sumo": version,
        "date": date,
        "mobilisation_median": planning.mobilisation_median_minutes,
        "mobilisation_sigma": planning.mobilisation_sigma,
        "vehicle_capacity": planning.vehicle_capacity,
        "exit_loading": [
            {"name": r["name"], "highway": r["highway"], "vehicles": int(r["vehicles"])}
            for r in exit_loading
        ],
    }
    Path(out).write_text(json.dumps(meta, indent=2))
    print(f"  {persons:,} residents, {households:,} households, "
          f"{edges:,} road links, {exits} exits, {version}")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", default="data/warehouse.duckdb")
    parser.add_argument("--net", default="data/sumo/thanet.net.xml")
    parser.add_argument("--sumo", default=".venv/bin/sumo")
    parser.add_argument("--date", required=True, help="run date, e.g. '15 August 2026'")
    parser.add_argument("--out", default="data/sumo/meta.json")
    args = parser.parse_args()
    collect(args.warehouse, args.net, args.sumo, args.date, args.out)


if __name__ == "__main__":
    main()
