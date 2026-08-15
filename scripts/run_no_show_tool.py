"""Serve the absence-reporting pages, and print links straight into them.

    python scripts/run_no_show_tool.py                       # demo warehouse
    python scripts/run_no_show_tool.py --database data/warehouse.duckdb

The plan departs at `fleet_departure_time` — 08:00 on 1 January 2026 — so a link
tapped today is either decades early or decades late. `--now` pins the tool's
clock onto the plan's timeline; it then runs forward normally. The default puts
it five minutes after the wait expires, which is where the interesting screens
are.

Links are printed for a real leader and a real passenger out of the plan, so
there is something to click without going through a phone.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from datetime import datetime, timedelta

from london_frontline import messaging, no_show
from london_frontline.config import PlanningConfig
from london_frontline.no_show import Clock
from london_frontline.no_show_web import NoShowTool, serve

DEFAULT_DATABASE = "data/demo.duckdb"
DEFAULT_INCIDENTS = "data/incidents.duckdb"


def example_links(tool: NoShowTool) -> list[tuple[str, str, str]]:
    """One link of each kind, out of the plan the warehouse actually holds."""
    conn = tool.warehouse
    messaging.require_plan_tables(conn)
    muster_point = messaging.select_example_leader(conn)
    person = messaging.select_example_passenger(conn, muster_point)
    leader_id = conn.execute(
        "SELECT leader_person_id FROM vehicle_routes WHERE muster_point_id = ?",
        [muster_point],
    ).fetchone()[0]
    names = dict(
        conn.execute(
            "SELECT person_id, name FROM persons WHERE person_id IN (?, ?)",
            [int(leader_id), int(person)],
        ).fetchall()
    )
    return [
        (
            "driver reports a passenger",
            f"{names[int(leader_id)]} (driving) about {names[int(person)]}",
            tool.links.passenger_missing_url(int(person), int(leader_id)),
        ),
        (
            "passenger reports the car",
            f"{names[int(person)]} about the car {names[int(leader_id)]} drives",
            tool.links.driver_missing_url(int(leader_id), int(person)),
        ),
    ]


# Past the wait for the muster point and for the collection stops behind it,
# which are a few minutes later again.
_PAST_THE_WAIT_MINUTES = 15


def lan_address() -> str | None:
    """The address this machine answers to on its own network.

    A link that says 127.0.0.1 works on the laptop and nowhere else: on a phone
    it points the phone at itself. Tapping a link from a real message therefore
    needs the address the phone can reach, which is this one.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Nothing is sent. Connecting a UDP socket only selects a route, and the
        # local end of that route is the address in question. TEST-NET-1 is
        # reserved and unrouted, so no packet could go anywhere even if one did.
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def default_now(planning: PlanningConfig) -> datetime:
    """A moment at which the links are live rather than counting down."""
    departure = datetime.fromisoformat(planning.fleet_departure_time)
    return departure + timedelta(
        minutes=planning.no_show_wait_minutes + _PAST_THE_WAIT_MINUTES
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--incidents", default=DEFAULT_INCIDENTS)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--phone",
        action="store_true",
        help="serve on this machine's network address so a phone on the same "
        "Wi-Fi can open the links. Sets --host and --base-url for you.",
    )
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument(
        "--base-url", help="what the links say; defaults to host and port"
    )
    parser.add_argument(
        "--now",
        help="pin the tool's clock, e.g. 2026-01-01T08:35. Defaults to five "
        "minutes after the wait expires.",
    )
    parser.add_argument(
        "--wait-minutes",
        type=float,
        help="override no_show_wait_minutes, to see the early screen",
    )
    parser.add_argument(
        "--divert-grace-minutes",
        type=float,
        help="override divert_grace_minutes. The fleet departs together, so "
        "with the configured 15 minutes every car has gone by the time a "
        "report is allowed and everybody gets a bus; raise it to exercise the "
        "other branch.",
    )
    parser.add_argument("--no-exercise-banner", action="store_true")
    args = parser.parse_args()

    overrides = {}
    if args.wait_minutes is not None:
        overrides["no_show_wait_minutes"] = args.wait_minutes
    if args.divert_grace_minutes is not None:
        overrides["divert_grace_minutes"] = args.divert_grace_minutes
    planning = PlanningConfig(**overrides)

    moment = datetime.fromisoformat(args.now) if args.now else default_now(planning)
    host, base_url = args.host, args.base_url
    if args.phone:
        address = lan_address()
        if address is None:
            parser.error("no network address found — is this machine on Wi-Fi?")
        host = "0.0.0.0"
        base_url = base_url or f"http://{address}:{args.port}"
    base_url = base_url or f"http://{host}:{args.port}"

    tool = NoShowTool(
        warehouse_database=args.database,
        incidents_database=args.incidents,
        base_url=base_url,
        clock=Clock.pinned_to(moment),
        planning=planning,
        exercise=not args.no_exercise_banner,
    )
    try:
        print(f"plan:      {args.database}")
        print(f"reports:   {args.incidents}")
        print(f"tool time: {moment:%Y-%m-%d %H:%M} (running forward from now)")
        print(f"wait:      {planning.no_show_wait_minutes:.0f} minutes\n")
        for kind, who, url in example_links(tool):
            print(f"{kind:<28} {who}\n  {url}\n")
        if args.phone:
            print(
                "open the links above on a phone on the same Wi-Fi. This serves "
                "to the whole network in clear text, which is fine for an "
                "exercise on a home network and not for anything else.\n"
            )
        # The two processes have to agree on the address and the key, and
        # getting either wrong produces a link that fails at the one moment it
        # matters, so the companion command is printed rather than described.
        print(
            "to put these links into the messages themselves:\n"
            f"  THANET_LINK_SECRET={os.environ.get(no_show.LINK_SECRET_ENVIRONMENT, '<same-key>')} \\\n"
            f"    python scripts/send_example_messages.py --dry-run "
            f"--base-url {base_url}\n"
        )
        print(f"serving on {base_url} — control-C to stop\n")
        serve(tool, host, args.port)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        tool.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
