#!/usr/bin/env python
"""Run the whole evacuation comparison, end to end.

One command takes the materialised warehouse and produces:

    data/sumo/report.html            the full comparison, one page
    data/sumo/evacuating-thanet.pdf  the same thing, paginated
    data/sumo/figures/               every chart and map as its own file
    data/sumo/frames/                one map per simulated minute
    data/sumo/timelapse.gif          those frames, animated
    data/sumo/out/*.xml              the raw SUMO record of both runs

Stages are separately skippable, because they cost very different amounts. The
simulations are tens of minutes; redrawing the charts is seconds. ``--from
analyse`` reuses the runs already on disk and redoes everything after them,
which is what you want while changing how the results are presented.

The warehouse must be materialised first (see the README) — this reads it, and
never writes to it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Run from the repository root whatever directory this was invoked from, so the
# relative paths below mean the same thing every time.
ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "sumo"))

# This runs for half an hour and its output is usually redirected to a file.
# Block buffering would leave that file empty until the very end, which is
# indistinguishable from the script having hung.
sys.stdout.reconfigure(line_buffering=True)

DATA = Path("data/sumo")
OUT = DATA / "out"
FIGURES = DATA / "figures"
FRAMES = DATA / "frames"
NET = DATA / "thanet.net.xml"
OSM = DATA / "thanet.osm.xml"
WAREHOUSE = Path("data/warehouse.duckdb")

SCENARIOS = ("baseline", "pooled")
STAGES = (
    "network",
    "trips",
    "simulate",
    "analyse",
    "maps",
    "figures",
    "timelapse",
    "report",
    "pdf",
)

OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
)


class Stage:
    """Prints a heading and how long the step took."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self):
        print(f"\n=== {self.name}")
        self.started = time.monotonic()
        return self

    def __exit__(self, *exc):
        elapsed = time.monotonic() - self.started
        if exc[0] is None:
            print(f"--- {self.name}: {elapsed / 60:.1f} min")
        return False


def run(command: list[str], **kwargs) -> None:
    """A subprocess that fails loudly rather than being ignored."""
    result = subprocess.run(command, **kwargs)
    if result.returncode != 0:
        raise SystemExit(f"{command[0]} failed with exit code {result.returncode}")


def fetch_osm() -> None:
    """Download the road extract, trying the mirrors in turn.

    Overpass mirrors return an HTML error page rather than an HTTP error when
    they are overloaded, so the result is checked for size and content instead
    of trusting the status code.
    """
    import requests

    query = (ROOT / "sumo" / "overpass.ql").read_text()
    for endpoint in OVERPASS_ENDPOINTS:
        print(f"  trying {endpoint}")
        try:
            response = requests.post(endpoint, data=query.encode(), timeout=900)
        except requests.RequestException as error:
            print(f"    {error}")
            continue
        body = response.content
        if len(body) > 5_000_000 and body.lstrip().startswith(b"<?xml"):
            OSM.parent.mkdir(parents=True, exist_ok=True)
            OSM.write_bytes(body)
            print(f"    got {len(body) / 1e6:.1f} MB")
            return
        print(f"    rejected: {len(body) / 1e6:.2f} MB, not an OSM document")
    raise SystemExit("every Overpass mirror refused; try again later")


def stage_network(force: bool) -> None:
    if NET.exists() and not force:
        print(f"  {NET} exists, skipping (use --force-network to rebuild)")
        return
    if not OSM.exists() or force:
        fetch_osm()
    run(["./sumo/build_network.sh"])


def stage_trips(args) -> None:
    from make_trips import build_trips

    for scenario in SCENARIOS:
        build_trips(
            scenario,
            warehouse=str(WAREHOUSE),
            net=str(NET),
            out=DATA / f"{scenario}.trips.xml",
        )


def stage_simulate(args) -> None:
    """Both scenarios at once — SUMO is single-threaded, the machine is not."""
    environment = dict(os.environ, SIM_END=str(args.sim_end),
                       EDGEDATA_FREQ=str(args.frame_seconds))
    OUT.mkdir(parents=True, exist_ok=True)
    logs = {s: OUT / f"{s}.run.log" for s in SCENARIOS}
    running = {}
    for scenario in SCENARIOS:
        handle = logs[scenario].open("w")
        running[scenario] = (
            subprocess.Popen(
                ["./sumo/run_scenario.sh", scenario],
                stdout=handle, stderr=subprocess.STDOUT, env=environment,
            ),
            handle,
        )
        print(f"  started {scenario} (log: {logs[scenario]})")

    failed = []
    for scenario, (process, handle) in running.items():
        code = process.wait()
        handle.close()
        print(f"  {scenario} finished with exit code {code}")
        if code != 0:
            failed.append(scenario)
    if failed:
        raise SystemExit(
            f"simulation failed for {', '.join(failed)}; see {OUT}/*.run.log"
        )


def stage_analyse(args) -> dict:
    from analyse import run_analysis

    return run_analysis(DATA, DATA / "comparison.json", horizon=args.horizon)


def stage_maps(args) -> None:
    from render_map import render_maps

    render_maps(str(NET), DATA, DATA / "maps.json", width=args.map_width)


def stage_meta(args) -> None:
    from make_meta import collect

    collect(str(WAREHOUSE), str(NET), args.sumo, args.date, DATA / "meta.json")


def stage_figures(args) -> None:
    from figures import write_all

    written = write_all(
        json.loads((DATA / "comparison.json").read_text()),
        json.loads((DATA / "maps.json").read_text()),
        json.loads((DATA / "meta.json").read_text()),
        FIGURES,
    )
    print(f"  {len(written)} files in {FIGURES}")


def stage_timelapse(args) -> None:
    from timelapse import render

    result = render(str(NET), DATA, FRAMES, width=args.timelapse_width)
    print(f"  {result['frames']} frames, {result['gif']} "
          f"({result['gif_mb']} MB)")


def stage_report(args) -> None:
    from build_report import build

    html = build(
        json.loads((DATA / "comparison.json").read_text()),
        json.loads((DATA / "maps.json").read_text()),
        json.loads((DATA / "meta.json").read_text()),
    )
    html = html.encode("ascii", "xmlcharrefreplace").decode("ascii")
    (DATA / "report.html").write_text(html, encoding="ascii")
    print(f"  wrote {DATA / 'report.html'} "
          f"({(DATA / 'report.html').stat().st_size / 1e6:.1f} MB)")


def stage_pdf(args) -> None:
    run(["./sumo/export_pdf.sh"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--from", dest="start", choices=STAGES, default=None,
                        help="begin at this stage, reusing earlier output")
    parser.add_argument("--only", choices=STAGES, default=None,
                        help="run exactly one stage")
    parser.add_argument("--sim-end", type=int, default=3600,
                        help="simulated seconds to run (default: 3600, one hour)")
    parser.add_argument("--frame-seconds", type=int, default=60,
                        help="edge-data aggregation window, and so the time-lapse "
                             "frame interval (default: 60)")
    parser.add_argument("--horizon", type=float, default=None,
                        help="compare at this many seconds (default: the last "
                             "moment both runs reached)")
    parser.add_argument("--map-width", type=int, default=1600)
    parser.add_argument("--timelapse-width", type=int, default=1500)
    parser.add_argument("--date", default=time.strftime("%d %B %Y"),
                        help="run date shown in the report header")
    parser.add_argument("--sumo", default=".venv/bin/sumo")
    parser.add_argument("--force-network", action="store_true",
                        help="re-download OSM and rebuild the network")
    args = parser.parse_args()

    if not WAREHOUSE.exists():
        raise SystemExit(
            f"no warehouse at {WAREHOUSE} — materialise the pipeline first "
            "(see the README)"
        )
    if shutil.which(args.sumo) is None and not Path(args.sumo).exists():
        raise SystemExit(f"no SUMO at {args.sumo}; pip install eclipse-sumo")

    if args.only:
        wanted = [args.only]
    else:
        first = STAGES.index(args.start) if args.start else 0
        wanted = list(STAGES[first:])

    # meta is cheap, and both figures and report read it, so it is refreshed
    # whenever either of them is going to run rather than being its own stage.
    needs_meta = {"figures", "report"} & set(wanted)

    started = time.monotonic()
    for stage in wanted:
        if stage == "network":
            with Stage("network"):
                stage_network(args.force_network)
        elif stage == "trips":
            with Stage("trips"):
                stage_trips(args)
        elif stage == "simulate":
            with Stage(f"simulate (to minute {args.sim_end // 60})"):
                stage_simulate(args)
        elif stage == "analyse":
            with Stage("analyse"):
                stage_analyse(args)
        elif stage == "maps":
            with Stage("maps"):
                stage_maps(args)
        elif stage == "figures":
            if needs_meta:
                with Stage("metadata"):
                    stage_meta(args)
                needs_meta = False
            with Stage("figures"):
                stage_figures(args)
        elif stage == "timelapse":
            with Stage("timelapse"):
                stage_timelapse(args)
        elif stage == "report":
            if needs_meta:
                with Stage("metadata"):
                    stage_meta(args)
                needs_meta = False
            with Stage("report"):
                stage_report(args)
        elif stage == "pdf":
            with Stage("pdf"):
                stage_pdf(args)

    print(f"\nall done in {(time.monotonic() - started) / 60:.1f} min")
    for path in (DATA / "report.html", DATA / "evacuating-thanet.pdf",
                 DATA / "timelapse.gif"):
        if path.exists():
            print(f"  {path}  ({path.stat().st_size / 1e6:.1f} MB)")
    for directory in (FIGURES, FRAMES):
        if directory.exists():
            print(f"  {directory}/  ({len(list(directory.iterdir()))} files)")


if __name__ == "__main__":
    main()
