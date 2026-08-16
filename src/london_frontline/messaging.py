"""Turning a planned route into the words one person actually receives.

`leader-route-planning` decides *who* is contacted and *how* — `stop_notifications`
records a mode of `direct`, `via_carer` or `physical_collection` per person. It
stops short of the text itself. This module is that last step: it reads a
finished plan out of the warehouse and renders two kinds of message.

A leader gets the whole route: their vehicle, every stop in order with a time,
and who to expect at each one. A passenger gets only their own line of it: where
to be, when, and which car with which driver. Neither message invents a fact the
warehouse does not hold — in particular there are no street addresses anywhere,
because `home-location-assignment` produces UPRNs and coordinates and no
geocoder resolves them (see that spec). Stops are therefore given as a UPRN, a
British National Grid reference, and a lat/long a phone can open in a map.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from collections import Counter
from typing import Callable
from urllib.parse import urlencode

import pandas as pd

# Looks up a printable address for a coordinate, or returns None. Kept as a
# plain callable so rendering never depends on the geocoder.
AddressResolver = Callable[[float, float], "str | None"]

from london_frontline.assets.routes import (
    NOTIFY_CARER,
    NOTIFY_PHYSICAL,
    STOP_COLLECTION,
    STOP_MUSTER,
)

# Prepended to every message unless explicitly disabled. These plans are built
# from a synthetic population, and an unmarked evacuation order landing on a
# real phone is indistinguishable from a real one.
EXERCISE_BANNER = "*** EXERCISE: SYNTHETIC PLAN, NOT A REAL EMERGENCY ***"

# The hazard the plan is being exercised against. The pipeline itself is
# hazard-agnostic: it plans a single simultaneous departure and never asks what
# is being fled, so the scenario lives here rather than in PlanningConfig.
ALERT_TITLE = "THANET WILDFIRE - EVACUATION ORDER"
HAZARD_SUMMARY = (
    "A wildfire is spreading across Thanet and the district is being evacuated. "
    "Leave now by the plan below. Do not drive yourself."
)

# ntfy turns a tag that matches an emoji shortcode into an icon on the title.
# One is a warning; a second alongside it is decoration.
ALERT_TAG = "rotating_light"

# What to bring. Deliberately short: a long list costs time at the door.
PACKING_LINE = "Bring medication, ID and a phone charger. Nothing else."

# Google's Maps URLs API. Links go in the message body rather than into ntfy's
# `click` field, so tapping the notification opens the message and opening a map
# stays a deliberate second tap.
_MAPS_SEARCH = "https://www.google.com/maps/search/"
_MAPS_DIRECTIONS = "https://www.google.com/maps/dir/"

# The directions URL accepts at most nine intermediate waypoints.
# `max_collection_stops_per_vehicle` is 3, so a Thanet route never approaches
# this, but a re-tuned config could, and a link that silently drops stops is
# worse than one that admits it.
MAX_MAP_WAYPOINTS = 9

# Longest pickup detour an example route may contain. The median collection leg
# across Thanet is 17 m and the longest is 805 m. Candidates are ordered towards
# this cap rather than away from it, so the example lands on a neighbouring
# street instead of two doors on the same one. It bounds the detour only: every
# route still ends with the 25 km run to the destination, which dominates the
# journey time either way.
MAX_EXAMPLE_PICKUP_LEG_M = 500.0


def _coordinate(latitude: float, longitude: float) -> str:
    return f"{latitude:.5f},{longitude:.5f}"


@dataclass(frozen=True)
class Place:
    """A stop, in the forms the warehouse can justify plus a looked-up address.

    `address` is not plan data. The warehouse has UPRNs and coordinates and no
    street names at all, so an address only appears here when a caller passed a
    resolver to look one up (see `london_frontline.addresses`).
    """

    uprn: int | None
    easting: float
    northing: float
    latitude: float
    longitude: float
    address: str | None = None

    @property
    def pin_url(self) -> str:
        """A dropped pin at this stop."""
        query = urlencode(
            {"api": 1, "query": _coordinate(self.latitude, self.longitude)}
        )
        return f"{_MAPS_SEARCH}?{query}"

    @property
    def walking_directions_url(self) -> str:
        """Walking directions to this stop from wherever the phone is now."""
        query = urlencode(
            {
                "api": 1,
                "destination": _coordinate(self.latitude, self.longitude),
                "travelmode": "walking",
            }
        )
        return f"{_MAPS_DIRECTIONS}?{query}"

    def describe(self) -> str:
        """How to name this stop in text.

        A street address when one could be resolved, since that is what a driver
        reads off a door. Failing that the UPRN, which at least distinguishes
        two flats sharing a dwelling point. Coordinates and grid references are
        deliberately absent either way: nobody can act on "grid 626219E
        168960N", and the map link beside it does that job properly.
        """
        if self.address:
            return self.address
        return "" if self.uprn is None else f"UPRN {self.uprn}"


@dataclass(frozen=True)
class Person:
    name: str
    phone_number: str | None = None
    # Only the messages that carry "they did not come" links need this, so it
    # stays optional: a briefing hand-built in a test needs no identity.
    person_id: int | None = None

    def describe(self) -> str:
        return f"{self.name} ({self.phone_number})" if self.phone_number else self.name


@dataclass(frozen=True)
class Expected:
    """Someone a driver should expect at a stop, and how they came to know.

    The mode matters to the driver, not just to the dispatcher: a person marked
    `physical_collection` has no phone and no carer, so nobody has told them
    anything and the only notice they will get is the driver at the door.
    """

    person: Person
    notification_mode: str
    carer: Person | None = None

    def describe(self) -> str:
        line = self.person.describe()
        if self.notification_mode == NOTIFY_CARER:
            via = f" via {self.carer.name}" if self.carer else ""
            return f"{line}: no phone, told{via}"
        if self.notification_mode == NOTIFY_PHYSICAL:
            return f"{line}: NOT CONTACTED, no phone and no carer. Knock."
        return line


@dataclass(frozen=True)
class Stop:
    stop_seq: int
    stop_type: str
    meeting_time: datetime
    place: Place
    people: tuple[Expected, ...] = ()


@dataclass(frozen=True)
class Destination:
    name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Notification:
    """A rendered message, in the shape ntfy publishes."""

    title: str
    body: str
    tags: tuple[str, ...] = ()
    # ntfy has no sound parameter: priority alone decides whether a phone rings.
    # 5 is its maximum, documented as a long vibration burst plus the default
    # notification sound. Anything quieter than that is a device-side setting.
    priority: int = 5
    recipient: Person | None = None
    # Set when the recipient is a carer being told on someone else's behalf.
    on_behalf_of: Person | None = None


class ReportLinks(Protocol):
    """Builds the one-tap link that reports somebody absent.

    Implemented by `london_frontline.no_show.LinkBuilder`, which signs the
    person ids into the URL. Kept as a protocol so rendering a message never
    depends on the tool being installed, let alone running.
    """

    def passenger_missing_url(
        self, subject_person_id: int, reporter_person_id: int
    ) -> str:
        """For a driver, about a passenger who is not at the stop."""

    def driver_missing_url(
        self, subject_person_id: int, reporter_person_id: int
    ) -> str:
        """For a passenger, about a car that has not arrived."""


@dataclass(frozen=True)
class ReportingRule:
    """Where the absence links point, and the earliest they may be tapped.

    The wait is the whole safeguard: a link that works the moment the car is a
    minute late would strand people over a red traffic light.
    """

    links: ReportLinks
    wait_minutes: float

    def opens_at(self, meeting_time: datetime) -> datetime:
        return meeting_time + timedelta(minutes=self.wait_minutes)


@dataclass(frozen=True)
class LeaderBriefing:
    """Everything one driver needs, straight off `vehicle_routes`."""

    muster_point_id: int
    license_plate: str
    capacity: int
    leader: Person
    stops: tuple[Stop, ...]
    destination: Destination
    from_owner_household: bool = True


@dataclass(frozen=True)
class PassengerBriefing:
    """One seat, from the point of view of the person sitting in it."""

    person: Person
    notification_mode: str
    stop: Stop
    license_plate: str
    leader: Person
    destination: Destination
    carer: Person | None = None
    fellow_passengers: tuple[Person, ...] = field(default=())
    # False when the seat is in a neighbour's car rather than the household's
    # own. True of 80,500 muster-point occupants and false of 28,931: the second
    # group is the ride-share the whole plan exists to arrange, and being told
    # to walk to "the car" is no use if it is not yours.
    rides_in_own_household_car: bool = True


def driving_route_url(
    stops: "tuple[Stop, ...]", destination: Destination
) -> tuple[str, int]:
    """One Google Maps link covering the whole run, pickups included.

    The car starts where it is parked, calls at each collection stop in the
    order the plan fixed, and ends at the evacuation destination. Returns the
    link and the number of stops that had to be dropped to stay inside
    `MAX_MAP_WAYPOINTS`, so the caller can say so rather than quietly mislead.
    """
    if not stops:
        raise ValueError("A route needs at least the muster point to start from.")

    origin, *intermediate = stops

    # Flats in one block share a dwelling point, so consecutive stops can carry
    # identical coordinates. They stay separate stops in the text — they are
    # separate doors — but as waypoints they would make the map plot the same
    # place twice and burn the waypoint budget doing it.
    coordinates: list[str] = []
    for stop in intermediate:
        coordinate = _coordinate(stop.place.latitude, stop.place.longitude)
        if not coordinates or coordinates[-1] != coordinate:
            coordinates.append(coordinate)

    dropped = max(0, len(coordinates) - MAX_MAP_WAYPOINTS)
    waypoints = coordinates[:MAX_MAP_WAYPOINTS]
    parameters = {
        "api": 1,
        "origin": _coordinate(origin.place.latitude, origin.place.longitude),
        "destination": _coordinate(destination.latitude, destination.longitude),
        "travelmode": "driving",
    }
    if waypoints:
        parameters["waypoints"] = "|".join(waypoints)
    return f"{_MAPS_DIRECTIONS}?{urlencode(parameters)}", dropped


def _plate(license_plate: str) -> str:
    """Set a plate off from the prose so it can be matched against a real car.

    Bracketed on one line rather than drawn as an ASCII box: a notification is
    rendered in the phone's proportional system font, where multi-line box art
    arrives ragged.
    """
    return f"[ {license_plate} ]"


def _stop_labels(stops: "tuple[Stop, ...]") -> dict[int, str]:
    """Name each stop, separating any two that resolve to the same address.

    A reverse geocoder returns the nearest addressed building, so two dwelling
    points tens of metres apart can come back with one address between them.
    Printed identically they read as a duplicate line rather than two doors, so
    the UPRN goes back on to break the tie.
    """
    labels = {stop.stop_seq: stop.place.describe() for stop in stops}
    repeated = {
        label for label, count in Counter(labels.values()).items() if label and count > 1
    }
    for stop in stops:
        if labels[stop.stop_seq] in repeated and stop.place.uprn is not None:
            labels[stop.stop_seq] += f" (UPRN {stop.place.uprn})"
    return labels


def _clock(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def _header(summary: str, exercise: bool) -> list[str]:
    """The opening of every message: what this is about, for this recipient.

    `summary` is the first body line, so it is what a phone shows folded up
    under the title. The hazard is already named in the title and is not
    repeated here.
    """
    lines = [summary, ""]
    if exercise:
        lines += [EXERCISE_BANNER, ""]
    return [*lines, HAZARD_SUMMARY, ""]


def _absence_link_lines(
    briefing: LeaderBriefing,
    stop: Stop,
    expected: Expected,
    reporting: ReportingRule | None,
) -> list[str]:
    """The absence link that sits under one name on a driver's route.

    Nothing is added for the driver's own name — a driver reporting himself
    absent is not a case — nor when no rule was supplied, so a message rendered
    without a running tool simply has no links in it.
    """
    if reporting is None:
        return []
    subject_id = expected.person.person_id
    leader_id = briefing.leader.person_id
    if subject_id is None or leader_id is None or subject_id == leader_id:
        return []
    return [
        f"            not here by {_clock(reporting.opens_at(stop.meeting_time))}? "
        f"phone first, then tap:",
        f"            {reporting.links.passenger_missing_url(subject_id, leader_id)}",
    ]


def render_leader_message(
    briefing: LeaderBriefing,
    *,
    exercise: bool = True,
    reporting: ReportingRule | None = None,
) -> Notification:
    """The driver's copy: the full ordered route, with who to expect where."""
    seats_used = sum(len(stop.people) for stop in briefing.stops)
    departure = _clock(briefing.stops[0].meeting_time)
    lines = _header(
        f"You are driving {briefing.license_plate}, leaving at {departure}",
        exercise,
    )
    lines += [
        f"{briefing.leader.name}, you are the driver.",
        "",
        "YOUR NUMBER PLATE",
        _plate(briefing.license_plate),
        f"{seats_used} of {briefing.capacity} seats filled.",
        "",
        "YOUR ROUTE",
    ]

    labels = _stop_labels(briefing.stops)
    for stop in briefing.stops:
        if stop.stop_type == STOP_MUSTER:
            heading = f"{_clock(stop.meeting_time)}  DEPART from your parked car"
        else:
            where = labels[stop.stop_seq] or "door pickup"
            heading = f"{_clock(stop.meeting_time)}  COLLECT at {where}"
        lines += ["", heading]
        if stop.people:
            for expected in stop.people:
                lines.append(f"          - {expected.describe()}")
                lines += _absence_link_lines(briefing, stop, expected, reporting)
        elif stop.stop_type == STOP_MUSTER:
            # Common: 2,474 of Thanet's routes have every occupant collected
            # from their own door, so nobody gathers at the car itself.
            lines.append("          - nobody joins here; this is where your car sits")
        else:
            lines.append("          - nobody listed for this stop")

    route_url, dropped = driving_route_url(briefing.stops, briefing.destination)
    lines += [
        "",
        f"THEN DRIVE TO {briefing.destination.name.upper()}",
        "",
        "WHOLE ROUTE IN GOOGLE MAPS",
        "(your car, every pickup in order, then the destination)",
        route_url,
    ]
    if dropped:
        lines.append(
            f"NOTE: the link holds the first {MAX_MAP_WAYPOINTS} pickups; "
            f"{dropped} more are listed above but not on the map."
        )
    lines += [
        "",
        "Do not exceed your seat count. If a stop is unreachable, go on to the "
        "next one and report it on arrival.",
    ]
    if reporting is not None:
        lines += [
            "",
            "IF SOMEBODY DOES NOT COME",
            f"Phone them. Only if you cannot reach them, and only after the "
            f"{reporting.wait_minutes:.0f} minutes are up, tap the link under "
            f"their name. You will be given a route without that stop.",
        ]

    return Notification(
        title=ALERT_TITLE,
        body="\n".join(lines),
        tags=(ALERT_TAG,),
        priority=5,
        recipient=briefing.leader,
    )


def render_passenger_message(
    briefing: PassengerBriefing,
    *,
    exercise: bool = True,
    reporting: ReportingRule | None = None,
) -> Notification:
    """One passenger's copy: their stop, their time, their driver.

    When the passenger has no phone, the plan notifies their `dependent_of`
    carer instead, so the message is addressed to the carer and speaks about the
    passenger in the third person.
    """
    if briefing.notification_mode == NOTIFY_PHYSICAL:
        raise ValueError(
            f"{briefing.person.name} is marked {NOTIFY_PHYSICAL}: no phone and no "
            f"carer, so there is nobody to send a message to"
        )
    if briefing.notification_mode == NOTIFY_CARER and briefing.carer is None:
        raise ValueError(
            f"{briefing.person.name} is marked {NOTIFY_CARER} but no carer was given"
        )

    via_carer = briefing.notification_mode == NOTIFY_CARER
    recipient = briefing.carer if via_carer else briefing.person
    assert recipient is not None  # guarded above
    subject = briefing.person.name
    at_home = briefing.stop.stop_type == STOP_COLLECTION
    time = _clock(briefing.stop.meeting_time)

    whose_lift = f"{subject}'s lift" if via_carer else "Your lift"
    lines = _header(
        f"{whose_lift} is {briefing.license_plate} at {time}", exercise
    )
    if via_carer:
        lines.append(
            f"{recipient.name}, this is about {subject}, who has no phone of "
            f"their own. Please pass it on and make sure they are ready."
        )
    else:
        lines.append(f"{recipient.name}, a seat is reserved for you.")
    lines += [
        "",
        "LOOK FOR THIS NUMBER PLATE",
        _plate(briefing.license_plate),
    ]

    if at_home:
        who = f"{subject} does" if via_carer else "You do"
        lines += [
            "",
            f"The car comes to the door. {who} not need to walk.",
            f"Be ready outside from {time}.",
            "",
            "PICKUP POINT IN GOOGLE MAPS",
            briefing.stop.place.pin_url,
        ]
    else:
        who = f"{subject} must" if via_carer else "You must"
        whose = (
            "your household's own car"
            if briefing.rides_in_own_household_car
            else "a neighbour's car nearby"
        )
        lines += [
            "",
            f"{who} walk to {whose} and be there by {time}.",
            "",
            "WALKING DIRECTIONS IN GOOGLE MAPS",
            briefing.stop.place.walking_directions_url,
        ]

    lines += [
        "",
        f"Driver: {briefing.leader.describe()}",
        f"Going to: {briefing.destination.name}",
    ]
    if briefing.fellow_passengers:
        others = ", ".join(person.name for person in briefing.fellow_passengers)
        travelling_with = f"Travelling with {subject}" if via_carer else "Travelling with you"
        lines.append(f"{travelling_with}: {others}")
    lines += ["", PACKING_LINE]

    lines += _no_car_link_lines(briefing, reporting)

    return Notification(
        title=ALERT_TITLE,
        body="\n".join(lines),
        tags=(ALERT_TAG,),
        priority=5,
        recipient=recipient,
        on_behalf_of=briefing.person if via_carer else None,
    )


def _no_car_link_lines(
    briefing: PassengerBriefing, reporting: ReportingRule | None
) -> list[str]:
    """What to do when the car never turns up.

    Addressed to whoever is holding the phone: for a passenger notified through
    a carer, that is the carer, and it is the carer who will be tapping.
    """
    if reporting is None:
        return []
    subject_id = briefing.person.person_id
    leader_id = briefing.leader.person_id
    if subject_id is None or leader_id is None:
        return []
    call = (
        f"Phone the driver first: {briefing.leader.phone_number}."
        if briefing.leader.phone_number
        else "The driver has no phone number in the plan, so there is nobody to ring."
    )
    return [
        "",
        "IF THE CAR DOES NOT COME",
        call,
        f"Only if you cannot reach them, and only after "
        f"{_clock(reporting.opens_at(briefing.stop.meeting_time))}, tap:",
        reporting.links.driver_missing_url(leader_id, subject_id),
        "You will be given another car, or a rescue bus to wait for.",
    ]


# --- Reading a briefing out of the warehouse --------------------------------
#
# Route stops are stored in British National Grid, because everything upstream
# of them is: dwellings, roads and parking spots all come from OSGB sources and
# distances are computed in metres. A phone map needs WGS84, so coordinates are
# projected at read time rather than duplicated in the table.

_TO_WGS84 = (
    "ST_Transform(ST_Point({easting}, {northing}), 'EPSG:27700', 'EPSG:4326', "
    "always_xy := true)"
)

def wgs84_columns(easting: str = "easting", northing: str = "northing") -> str:
    """SQL selecting `latitude, longitude` from a grid-referenced column pair."""
    point = _TO_WGS84.format(easting=easting, northing=northing)
    return f"ST_Y({point}) AS latitude, ST_X({point}) AS longitude"


_STOPS_SQL = f"""
    SELECT stop_seq, stop_type, uprn, easting, northing, meeting_time,
           {wgs84_columns()}
    FROM route_stops
    WHERE muster_point_id = ?
    ORDER BY stop_seq
"""

_PLAN_TABLES = ("vehicle_routes", "route_stops", "stop_notifications", "muster_points")


def require_plan_tables(conn) -> None:
    """Fail with something actionable when the pipeline has not been run."""
    present = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
    missing = [table for table in _PLAN_TABLES if table not in present]
    if missing:
        raise RuntimeError(
            "The warehouse has no finished plan in it — missing "
            + ", ".join(missing)
            + ". Materialise the pipeline, or point at a demo warehouse built by "
            "scripts/build_demo_warehouse.py."
        )


def select_example_leader(
    conn,
    accept_route: "Callable[[list[tuple[float, float]]], bool] | None" = None,
    candidates: int = 40,
) -> int:
    """The muster point of the most illustrative route.

    Wanted: the ordinary shape of the plan. Occupants gather at the car, which
    is what 94.3% of Thanet's routes look like, and the vehicle then makes a
    short hop to collect anyone who cannot walk that far before the run to the
    destination. Requiring the leader to be among those at the car selects that
    majority case.

    The remaining 5.7% are equally valid plans, not defects: where every
    occupant of a vehicle needs collecting from their own door, nobody gathers
    at the car and the driver is picked up along with everyone else. They are
    passed over here only because one example cannot show both shapes.

    Within the detour cap the longest leg wins, so the pickup is a visibly
    separate address rather than the 17 m median that reads as next door.

    `accept_route` is handed every collection stop's coordinates at once and
    vetoes the route. Its real use is rejecting routes whose pickups are not
    homes — NSUL has no property classification, so a household can be placed on
    a play area or a substation — and routes whose pickups cannot be told apart
    once resolved. Every candidate failing it, the first is returned anyway,
    since an unillustrative example beats none.

    Falls back to ignoring the detour cap, then to any route at all, so a
    warehouse whose routes all collect nobody still yields an example.
    """
    capped = f"""
        SELECT r.muster_point_id
        FROM vehicle_routes r
        JOIN (SELECT muster_point_id, max(leg_m) AS longest_leg
              FROM route_stops GROUP BY muster_point_id) l
          USING (muster_point_id)
        WHERE r.collection_stops >= 1
          AND l.longest_leg <= {MAX_EXAMPLE_PICKUP_LEG_M}
          AND EXISTS (
              SELECT 1 FROM stop_notifications n
              WHERE n.muster_point_id = r.muster_point_id
                AND n.stop_seq = 1
                AND n.person_id = r.leader_person_id
          )
        ORDER BY r.collection_stops DESC, l.longest_leg DESC, r.muster_point_id
        LIMIT {candidates}
    """
    uncapped = f"""
        SELECT muster_point_id FROM vehicle_routes
        ORDER BY collection_stops DESC, muster_point_id
        LIMIT {candidates}
    """
    for query in (capped, uncapped):
        shortlist = [int(row[0]) for row in conn.execute(query).fetchall()]
        if not shortlist:
            continue
        if accept_route is None:
            return shortlist[0]
        for muster_point_id in shortlist:
            stops = conn.execute(
                f"""
                SELECT ST_Y({_TO_WGS84.format(easting="easting", northing="northing")}),
                       ST_X({_TO_WGS84.format(easting="easting", northing="northing")})
                FROM route_stops
                WHERE muster_point_id = ? AND stop_type = '{STOP_COLLECTION}'
                ORDER BY stop_seq
                """,
                [muster_point_id],
            ).fetchall()
            if stops and accept_route([(lat, lon) for lat, lon in stops]):
                return muster_point_id
        return shortlist[0]
    raise RuntimeError("No vehicle routes in the warehouse.")


def select_example_passenger(conn, muster_point_id: int | None = None) -> int:
    """A contactable non-driver, preferring one collected from their own door.

    Preferring the same vehicle as the chosen leader makes the pair of messages
    two halves of one plan rather than two unrelated samples.
    """
    for restrict_to_vehicle in (muster_point_id, None):
        row = conn.execute(
            f"""
            SELECT n.person_id
            FROM stop_notifications n
            JOIN vehicle_routes r USING (muster_point_id)
            WHERE n.person_id <> r.leader_person_id
              AND n.notification_mode <> '{NOTIFY_PHYSICAL}'
              {"AND n.muster_point_id = ?" if restrict_to_vehicle is not None else ""}
            ORDER BY (n.stop_type = '{STOP_COLLECTION}') DESC, n.person_id
            LIMIT 1
            """,
            [restrict_to_vehicle] if restrict_to_vehicle is not None else [],
        ).fetchone()
        if row is not None:
            return int(row[0])
    raise RuntimeError("No contactable passenger in the warehouse.")


def _absent(value) -> bool:
    """True for every flavour of missing a DuckDB->pandas read can produce.

    A SQL NULL arrives as ``None``, ``pd.NA`` or ``NaN`` depending on the
    column's dtype, and only ``None`` is falsy in the obvious way.
    """
    return value is None or pd.isna(value)


def _place(row, resolve: "AddressResolver | None" = None) -> Place:
    uprn = row["uprn"]
    latitude, longitude = float(row["latitude"]), float(row["longitude"])
    return Place(
        uprn=None if _absent(uprn) else int(uprn),
        easting=float(row["easting"]),
        northing=float(row["northing"]),
        latitude=latitude,
        longitude=longitude,
        address=None if resolve is None else resolve(latitude, longitude),
    )


def _person(name, phone, person_id=None) -> Person:
    return Person(
        name=str(name),
        phone_number=None if _absent(phone) else str(phone),
        person_id=None if _absent(person_id) else int(person_id),
    )


def fetch_leader_briefing(
    conn, muster_point_id: int, resolve: "AddressResolver | None" = None
) -> LeaderBriefing:
    """Assemble one driver's route from `vehicle_routes` and its stops."""
    route = conn.execute(
        """
        SELECT r.license_plate, r.leader_person_id, r.leader_name, r.leader_phone,
               r.leader_from_owner_household, m.capacity,
               r.destination_name, r.destination_latitude, r.destination_longitude
        FROM vehicle_routes r
        JOIN muster_points m USING (muster_point_id)
        WHERE r.muster_point_id = ?
        """,
        [muster_point_id],
    ).fetchdf()
    if route.empty:
        raise RuntimeError(f"No route for muster point {muster_point_id}.")
    route = route.iloc[0]

    stop_rows = conn.execute(_STOPS_SQL, [muster_point_id]).fetchdf()
    people = conn.execute(
        """
        SELECT stop_seq, person_id, name, phone_number, notification_mode,
               carer_person_id, carer_name, carer_phone
        FROM stop_notifications
        WHERE muster_point_id = ? ORDER BY stop_seq, person_id
        """,
        [muster_point_id],
    ).fetchdf()

    stops = tuple(
        Stop(
            stop_seq=int(row["stop_seq"]),
            stop_type=str(row["stop_type"]),
            meeting_time=row["meeting_time"].to_pydatetime(),
            place=_place(row, resolve),
            people=tuple(
                Expected(
                    person=_person(
                        person["name"], person["phone_number"], person["person_id"]
                    ),
                    notification_mode=str(person["notification_mode"]),
                    carer=None
                    if _absent(person["carer_name"])
                    else _person(
                        person["carer_name"],
                        person["carer_phone"],
                        person["carer_person_id"],
                    ),
                )
                for _, person in people[people["stop_seq"] == row["stop_seq"]].iterrows()
            ),
        )
        for _, row in stop_rows.iterrows()
    )

    return LeaderBriefing(
        muster_point_id=muster_point_id,
        license_plate=str(route["license_plate"]),
        capacity=int(route["capacity"]),
        leader=_person(
            route["leader_name"], route["leader_phone"], route["leader_person_id"]
        ),
        stops=stops,
        destination=Destination(
            name=str(route["destination_name"]),
            latitude=float(route["destination_latitude"]),
            longitude=float(route["destination_longitude"]),
        ),
        from_owner_household=bool(route["leader_from_owner_household"]),
    )


def fetch_passenger_briefing(
    conn, person_id: int, resolve: "AddressResolver | None" = None
) -> PassengerBriefing:
    """Assemble one passenger's view of the vehicle they were seated in."""
    row = conn.execute(
        f"""
        SELECT n.muster_point_id, n.stop_seq, n.stop_type, n.name, n.phone_number,
               n.notification_mode, n.carer_name, n.carer_phone,
               n.carer_person_id,
               s.uprn, s.easting, s.northing, s.meeting_time,
               {wgs84_columns("s.easting", "s.northing")},
               r.license_plate, r.leader_person_id, r.leader_name, r.leader_phone,
               r.destination_name, r.destination_latitude, r.destination_longitude,
               (rider.household_id = m.household_id) AS rides_in_own_household_car
        FROM stop_notifications n
        JOIN route_stops s
          ON s.muster_point_id = n.muster_point_id AND s.stop_seq = n.stop_seq
        JOIN vehicle_routes r ON r.muster_point_id = n.muster_point_id
        JOIN persons rider ON rider.person_id = n.person_id
        JOIN muster_points m ON m.muster_point_id = n.muster_point_id
        WHERE n.person_id = ?
        """,
        [person_id],
    ).fetchdf()
    if row.empty:
        raise RuntimeError(f"Person {person_id} has no seat in the plan.")
    row = row.iloc[0]

    others = conn.execute(
        """
        SELECT name, phone_number FROM stop_notifications
        WHERE muster_point_id = ? AND person_id NOT IN (?, ?)
        ORDER BY person_id
        """,
        [int(row["muster_point_id"]), person_id, int(row["leader_person_id"])],
    ).fetchdf()

    carer_name = row["carer_name"]
    return PassengerBriefing(
        person=_person(row["name"], row["phone_number"], person_id),
        notification_mode=str(row["notification_mode"]),
        stop=Stop(
            stop_seq=int(row["stop_seq"]),
            stop_type=str(row["stop_type"]),
            meeting_time=row["meeting_time"].to_pydatetime(),
            place=_place(row, resolve),
        ),
        license_plate=str(row["license_plate"]),
        leader=_person(
            row["leader_name"], row["leader_phone"], row["leader_person_id"]
        ),
        destination=Destination(
            name=str(row["destination_name"]),
            latitude=float(row["destination_latitude"]),
            longitude=float(row["destination_longitude"]),
        ),
        carer=None
        if _absent(carer_name)
        else _person(carer_name, row["carer_phone"], row["carer_person_id"]),
        fellow_passengers=tuple(
            _person(person["name"], person["phone_number"])
            for _, person in others.iterrows()
        ),
        rides_in_own_household_car=bool(row["rides_in_own_household_car"]),
    )
