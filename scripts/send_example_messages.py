"""Send two example messages from a finished plan to a phone, over ntfy.

Pulls one leader and one of their passengers out of the warehouse, renders each
into the message that person would receive, prints both, and publishes them to
an ntfy topic. Nothing is sent without `--topic`; with `--dry-run` nothing is
sent at all.

    python scripts/send_example_messages.py --dry-run
    python scripts/send_example_messages.py --topic thanet-evac-x7k2m9

The default database is the demo warehouse, because the real one takes a full
pipeline run to produce. Point `--database` at `data/warehouse.duckdb` once it
exists and the same command sends real plan output.
"""

from __future__ import annotations

import argparse
import os
import sys

from london_frontline import addresses, messaging, no_show, notify
from london_frontline.config import PlanningConfig
from london_frontline.resources import SpatialDuckDBResource

DEFAULT_DATABASE = "data/demo.duckdb"
DEFAULT_INCIDENTS = "data/incidents.duckdb"


def preview(label: str, notification: messaging.Notification) -> None:
    recipient = notification.recipient
    to = recipient.describe() if recipient else "nobody"
    if notification.on_behalf_of:
        to += f", on behalf of {notification.on_behalf_of.name}"
    rule = "=" * 72
    print(f"\n{rule}\n{label.upper()}  ->  {to}\n{rule}")
    print(f"Title: {notification.title}")
    print(f"Tags: {', '.join(notification.tags)}   Priority: {notification.priority}")
    print("-" * 72)
    print(notification.body)


def build_reporting(args) -> "messaging.ReportingRule | None":
    """The absence links, if a tool is running to receive them.

    Without `--base-url` the messages render exactly as they did before: a plan
    with nowhere to report an absence to is better off saying nothing about
    reporting one.
    """
    if not args.base_url:
        return None
    log = None
    if not os.environ.get(no_show.LINK_SECRET_ENVIRONMENT):
        # Only opened when the key is not in the environment: DuckDB allows one
        # writer, and a running tool is holding it.
        log = no_show.IncidentLog(args.incidents)
    try:
        links = no_show.LinkBuilder(args.base_url, no_show.signing_key(log))
    finally:
        if log is not None:
            log.close()
    return messaging.ReportingRule(
        links=links, wait_minutes=PlanningConfig().no_show_wait_minutes
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", help="ntfy topic to publish to")
    parser.add_argument("--server", default=notify.DEFAULT_SERVER)
    parser.add_argument("--token", help="bearer token, for a protected topic")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument(
        "--base-url",
        help="where scripts/run_no_show_tool.py is serving, e.g. "
        "http://127.0.0.1:8420. Puts a 'they did not come' link under each "
        "name. Set THANET_LINK_SECRET in both processes so the links match "
        "while the tool is running.",
    )
    parser.add_argument("--incidents", default=DEFAULT_INCIDENTS)
    parser.add_argument("--muster-point", type=int, help="pick a specific vehicle")
    parser.add_argument("--person", type=int, help="pick a specific passenger")
    parser.add_argument(
        "--dry-run", action="store_true", help="render and print, send nothing"
    )
    parser.add_argument(
        "--no-exercise-banner",
        action="store_true",
        help="drop the EXERCISE header. Only for a message nobody could mistake "
        "for a real evacuation order.",
    )
    parser.add_argument(
        "--no-addresses",
        action="store_true",
        help="skip the reverse geocoder and print UPRNs. Offline, and the only "
        "sane setting for anything beyond a handful of messages: Nominatim "
        "allows about one request a second and no bulk use.",
    )
    parser.add_argument(
        "--allow-short-topic",
        action="store_true",
        help="skip the guessable-topic check (for a private ntfy server)",
    )
    args = parser.parse_args()
    reporting = build_reporting(args)

    if not args.topic and not args.dry_run:
        parser.error("give --topic to send, or --dry-run to only print")

    exercise = not args.no_exercise_banner
    resolver = None if args.no_addresses else addresses.Resolver()
    resolve = None if resolver is None else resolver.line
    def accept_route(coordinates):
        """Every pickup a real front door, and each on a street of its own.

        Distinct streets rather than merely distinct addresses: two house
        numbers on one road are a single stop as far as a reader is concerned,
        and the point of the example is to show the vehicle actually going
        somewhere between pickups.
        """
        resolved = [resolver.lookup(lat, lon) for lat, lon in coordinates]
        if not all(a is not None and a.is_a_dwelling for a in resolved):
            return False
        roads = [a.road for a in resolved]
        return len(set(roads)) == len(roads)

    accept = None if resolver is None else accept_route

    warehouse = SpatialDuckDBResource(database=args.database, read_only=True)
    with warehouse.connect() as conn:
        messaging.require_plan_tables(conn)
        # `is None`, not `or`: person_id 0 and muster_point_id 0 both exist and
        # are both falsy, so `or` would silently ignore them and pick its own.
        muster_point = (
            messaging.select_example_leader(conn, accept_route=accept)
            if args.muster_point is None
            else args.muster_point
        )
        person = (
            messaging.select_example_passenger(conn, muster_point)
            if args.person is None
            else args.person
        )

        leader = messaging.render_leader_message(
            messaging.fetch_leader_briefing(conn, muster_point, resolve),
            exercise=exercise,
            reporting=reporting,
        )
        passenger = messaging.render_passenger_message(
            messaging.fetch_passenger_briefing(conn, person, resolve),
            exercise=exercise,
            reporting=reporting,
        )

    preview("leader message", leader)
    preview("passenger message", passenger)

    if args.dry_run:
        print("\n[dry run] nothing sent.")
        return 0

    print()
    for label, notification in (("leader", leader), ("passenger", passenger)):
        delivery = notify.publish(
            notification,
            args.topic,
            server=args.server,
            token=args.token,
            require_unguessable=not args.allow_short_topic,
        )
        print(
            f"sent {label:<9} -> {delivery.server}/{delivery.topic} "
            f"(id {delivery.message_id})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
