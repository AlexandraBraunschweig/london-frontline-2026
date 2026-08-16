"""Compare the simulated scenarios and write the numbers the visual reads.

Two curves matter. Vehicles cleared over time says how long the road is busy;
people cleared over time says how long the *evacuation* takes, and those are not
the same question once cars carry different numbers of people.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

SCENARIOS = ("baseline", "pooled")
LABELS = {
    "baseline": "One car per household",
    "pooled": "Ride-shared plan",
}


def _occupancy(trips_path: Path) -> dict[str, int]:
    """Vehicle id -> people aboard, read from the trip file we generated."""
    occupancy: dict[str, int] = {}
    for _, element in ET.iterparse(trips_path, events=("end",)):
        if element.tag == "trip":
            occupancy[element.get("id")] = int(element.get("occupancy", 1))
            element.clear()
    return occupancy


def _tripinfo(path: Path, occupancy: dict[str, int]) -> dict:
    """Per-vehicle outcomes, plus the arrival curves in vehicles and in people."""
    arrivals: list[tuple[float, int]] = []
    durations: list[float] = []
    time_losses: list[float] = []
    waiting: list[float] = []
    lengths: list[float] = []
    depart_delays: list[float] = []

    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag != "tripinfo":
            continue
        people = occupancy.get(element.get("id"), 1)
        arrivals.append((float(element.get("arrival")), people))
        durations.append(float(element.get("duration")))
        time_losses.append(float(element.get("timeLoss")))
        waiting.append(float(element.get("waitingTime")))
        lengths.append(float(element.get("routeLength")))
        depart_delays.append(float(element.get("departDelay")))
        element.clear()

    arrivals.sort()
    times = np.array([a for a, _ in arrivals])
    people = np.array([p for _, p in arrivals])
    return {
        "arrival_times": times,
        "vehicles_cleared": np.arange(1, len(times) + 1),
        "people_cleared": np.cumsum(people),
        "durations": np.array(durations),
        "time_losses": np.array(time_losses),
        "waiting": np.array(waiting),
        "lengths": np.array(lengths),
        "depart_delays": np.array(depart_delays),
    }


def _summary(path: Path) -> dict:
    """The per-step network state: how many cars are out there, and how fast."""
    time, running, halting, mean_speed = [], [], [], []
    relative, teleports, arrived = [], [], []
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag != "step":
            continue
        time.append(float(element.get("time")))
        running.append(int(element.get("running")))
        halting.append(int(element.get("halting")))
        speed = float(element.get("meanSpeed", -1))
        mean_speed.append(speed if speed >= 0 else np.nan)
        rel = float(element.get("meanSpeedRelative", -1))
        relative.append(rel if rel >= 0 else np.nan)
        teleports.append(int(element.get("teleports", 0)))
        arrived.append(int(element.get("arrived", 0)))
        element.clear()
    return {
        "time": np.array(time),
        "running": np.array(running),
        "halting": np.array(halting),
        "mean_speed": np.array(mean_speed),
        "speed_relative": np.array(relative),
        "teleports": np.array(teleports),
        "arrived": np.array(arrived),
    }


def _truncate(steps: dict, horizon: float) -> dict:
    """Clip a summary series to a horizon both scenarios reached."""
    keep = steps["time"] <= horizon
    return {key: value[keep] for key, value in steps.items()}


def _percentile_time(
    times: np.ndarray, cleared: np.ndarray, fraction: float, total: float
):
    """When did `fraction` of the whole fleet get clear? None if it never did.

    The denominator is everyone who set off, not everyone who happens to have
    arrived by the horizon. Against the arrived subset these percentiles read
    like clearance times while describing only the fastest few per cent — a run
    with 4,000 arrivals out of 46,000 would report "half the people clear" for
    2,000 of them.
    """
    if times.size == 0 or total <= 0:
        return None
    target = total * fraction
    if cleared[-1] < target:
        return None
    index = int(np.searchsorted(cleared, target))
    return float(times[min(index, times.size - 1)])


def _teleports(stats_path: Path, steps: dict) -> int:
    """Teleport count, from the statistics file or the last summary step.

    A run that is still going — or one stopped at a horizon — has no statistics
    file yet, so fall back to the running total the summary already carries.
    """
    if stats_path.exists():
        root = ET.parse(stats_path).getroot()
        node = root.find("teleports")
        if node is not None:
            return int(node.get("total", 0))
    return int(steps["teleports"][-1]) if steps["teleports"].size else 0


def analyse(scenario: str, out_dir: Path, data_dir: Path, horizon: float) -> dict:
    occupancy = _occupancy(data_dir / f"{scenario}.trips.xml")
    trips = _tripinfo(out_dir / f"{scenario}.tripinfo.xml", occupancy)
    steps = _truncate(_summary(out_dir / f"{scenario}.summary.xml"), horizon)

    times = trips["arrival_times"]
    vehicles_total = len(occupancy)
    people_total = sum(occupancy.values())

    result = {
        "scenario": scenario,
        "label": LABELS[scenario],
        "vehicles_departed": vehicles_total,
        "people_carried": people_total,
        "vehicles_arrived": int(trips["vehicles_cleared"][-1]) if times.size else 0,
        "people_arrived": int(trips["people_cleared"][-1]) if times.size else 0,
        "mean_occupancy": round(people_total / vehicles_total, 2)
        if vehicles_total
        else 0,
        # Clearance times, in seconds after notification, against the whole
        # fleet. None means that share never got out before the horizon.
        "t50_vehicles": _percentile_time(
            times, trips["vehicles_cleared"], 0.50, vehicles_total),
        "t90_vehicles": _percentile_time(
            times, trips["vehicles_cleared"], 0.90, vehicles_total),
        "t95_vehicles": _percentile_time(
            times, trips["vehicles_cleared"], 0.95, vehicles_total),
        "t100_vehicles": (
            float(times[-1])
            if times.size and len(times) >= vehicles_total else None
        ),
        "t50_people": _percentile_time(
            times, trips["people_cleared"], 0.50, people_total),
        "t90_people": _percentile_time(
            times, trips["people_cleared"], 0.90, people_total),
        "t95_people": _percentile_time(
            times, trips["people_cleared"], 0.95, people_total),
        # The honest headline for a run that never finishes: how many actually
        # got out, and what share of everyone that is.
        "people_cleared_by_horizon": int(
            np.interp(horizon, times, trips["people_cleared"], left=0,
                      right=float(trips["people_cleared"][-1]))
        ) if times.size else 0,
        "people_cleared_pct": round(
            100 * float(np.interp(horizon, times, trips["people_cleared"], left=0,
                                  right=float(trips["people_cleared"][-1])))
            / people_total, 1
        ) if times.size and people_total else 0.0,
        # Delay. timeLoss is SUMO's own measure: time lost against travelling the
        # same route unobstructed at the vehicle's desired speed.
        "mean_duration_s": float(np.mean(trips["durations"])) if times.size else 0,
        "median_duration_s": float(np.median(trips["durations"])) if times.size else 0,
        "mean_time_loss_s": float(np.mean(trips["time_losses"])) if times.size else 0,
        "total_time_loss_h": float(np.sum(trips["time_losses"]) / 3600),
        "total_vehicle_h": float(np.sum(trips["durations"]) / 3600),
        "mean_depart_delay_s": float(np.mean(trips["depart_delays"]))
        if times.size
        else 0,
        "mean_route_km": float(np.mean(trips["lengths"]) / 1000) if times.size else 0,
        "peak_running": int(np.max(steps["running"])) if steps["time"].size else 0,
        "peak_halting": int(np.max(steps["halting"])) if steps["time"].size else 0,
        "teleports": _teleports(out_dir / f"{scenario}.stats.xml", steps),
        "horizon_s": horizon,
        # The state of the road at the horizon: this, not clearance time, is what
        # a saturated network actually has to say.
        "final_running": int(steps["running"][-1]) if steps["time"].size else 0,
        "final_halting": int(steps["halting"][-1]) if steps["time"].size else 0,
        "final_speed_relative": float(steps["speed_relative"][-1])
        if steps["time"].size else 0.0,
        "worst_speed_relative": float(np.nanmin(steps["speed_relative"]))
        if steps["time"].size else 0.0,
        "arrived_by_horizon": int(steps["arrived"][-1]) if steps["time"].size else 0,
        "stationary_share_pct": round(
            100 * steps["halting"][-1] / max(steps["running"][-1], 1), 1
        ) if steps["time"].size else 0.0,
    }

    # Thinned series for plotting: one point a minute is plenty.
    minute = slice(None, None, 60)
    result["series"] = {
        "time_s": steps["time"][minute].tolist(),
        "running": steps["running"][minute].tolist(),
        "halting": steps["halting"][minute].tolist(),
        "mean_speed_kph": (steps["mean_speed"][minute] * 3.6).tolist(),
        "speed_relative_pct": (steps["speed_relative"][minute] * 100).tolist(),
        "teleports": steps["teleports"][minute].tolist(),
    }
    # Arrival curves, sampled at a fixed grid so both scenarios line up — and cut
    # at the shared horizon. The faster run reaches a later simulated time before
    # both are stopped, so an untruncated curve would credit it with arrivals from
    # minutes the other scenario never got to play.
    last = min(times[-1] if times.size else 0.0, horizon)
    grid = np.arange(0, last + 60, 60)
    result["arrivals"] = {
        "time_s": grid.tolist(),
        "vehicles": np.searchsorted(times, grid, side="right").tolist(),
        "people": np.interp(
            grid, times, trips["people_cleared"], left=0,
            right=float(trips["people_cleared"][-1]) if times.size else 0,
        ).tolist(),
    }
    return result


def run_analysis(
    data_dir="data/sumo",
    out="data/sumo/comparison.json",
    horizon: float | None = None,
) -> dict:
    """Compare both runs at one shared moment and write the JSON the page reads."""

    data_dir = Path(data_dir)
    out = Path(out)
    out_dir = data_dir / "out"

    # Both scenarios must be read at the same simulated moment, or a run that
    # happened to get further would look better purely for having run longer.
    # horizon may be None: fall back to the shared end of both runs
    if horizon is None:
        horizon = min(
            _summary(out_dir / f"{s}.summary.xml")["time"][-1] for s in SCENARIOS
        )
    print(f"comparing both scenarios at t={horizon:.0f}s "
          f"({horizon / 60:.0f} min after the alarm)")
    results = {s: analyse(s, out_dir, data_dir, horizon) for s in SCENARIOS}

    out.write_text(json.dumps(results, indent=2))

    for scenario in SCENARIOS:
        r = results[scenario]
        print(f"\n=== {r['label']} ===")
        print(f"  cars departed      {r['vehicles_departed']:,}")
        print(f"  people carried     {r['people_carried']:,} "
              f"(mean {r['mean_occupancy']} per car)")
        print(f"  people clear       {r['people_cleared_by_horizon']:,} "
              f"({r['people_cleared_pct']}% of those carried)")
        print(f"  cars clear         {r['vehicles_arrived']:,}")
        for label, key in (("half the people", "t50_people"),
                           ("90% of people", "t90_people"),
                           ("every car clear", "t100_vehicles")):
            value = r[key]
            print(f"  {label:18s} {value / 60:.0f} min" if value
                  else f"  {label:18s} not reached by the horizon")
        print(f"  completed journeys mean {r['mean_duration_s'] / 60:.1f} min, "
              f"delay {r['mean_time_loss_s'] / 60:.1f} min "
              f"(arrivals only, so survivorship-biased)")
        print(f"  cars on road       {r['final_running']:,}")
        print(f"  of those stopped   {r['final_halting']:,} "
              f"({r['stationary_share_pct']}%)")
        print(f"  speed vs limit     {r['final_speed_relative'] * 100:.1f}% "
              f"(worst {r['worst_speed_relative'] * 100:.1f}%)")
        print(f"  peak cars on road  {r['peak_running']:,}")
        print(f"  teleports          {r['teleports']:,}")

    print(f"\nwrote {out}")


    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/sumo")
    parser.add_argument("--out", default="data/sumo/comparison.json")
    parser.add_argument(
        "--horizon", type=float, default=None,
        help="compare both scenarios at this many seconds after the alarm "
             "(default: the last moment both runs reached)",
    )
    args = parser.parse_args()
    run_analysis(args.data_dir, args.out, args.horizon)


if __name__ == "__main__":
    main()
