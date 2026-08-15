"""What happens when the other person does not come.

The plan assumes everybody keeps the appointment it made for them. Two ways
that fails at the kerb, and both leave somebody standing in the road with a
phone in their hand:

* a driver reaches a door and nobody answers it;
* a passenger waits at the pickup and no car arrives.

This is the loop that closes over those two cases. A message carries a link
against each name (`london_frontline.messaging.ReportingRule`); tapping it opens
the page in `london_frontline.no_show_web`, which asks the one question worth
asking — *have you phoned them?* — and then, if the wait is up, records the
absence and answers with a plan. A driver gets their route without that stop. A
passenger gets another car if one can still reach them, and a rescue bus if none
can.

Two rules gate every report, and both exist because a wrong report strands a
real person:

* **The wait.** `no_show_wait_minutes` must have passed since the meeting time
  being waited on. Before then the link opens a page that says when it opens.
* **The phone first.** The tool will not take a report from somebody who has
  not tried to ring. Where there is no number to ring — a passenger with no
  phone and no carer, whom nobody has been able to tell anything — the question
  becomes whether they knocked.

Reports are written to their own DuckDB file, never to the warehouse. DuckDB
permits one writer, the pipeline is that writer, and a tool that took the lock
would stop the plan being rebuilt underneath it. The log is the record: every
revised route is derived from the plan plus the reports, so re-deriving is
always safe and the tool holds no state it could get out of step with.
"""

from __future__ import annotations

import base64
import hmac
import os
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
from math import hypot

import duckdb
import pandas as pd

from london_frontline.assets.routes import (
    NOTIFY_CARER,
    NOTIFY_PHYSICAL,
    STOP_COLLECTION,
    STOP_MUSTER,
)
from london_frontline.config import PlanningConfig
from london_frontline.messaging import (
    Destination,
    Person,
    Place,
    Stop,
    driving_route_url,
    wgs84_columns,
)
from london_frontline.paths import resolve as resolve_path

# The two things that can be reported. Named from the reporter's point of view:
# a driver reports a missing passenger, a passenger reports a missing driver.
REPORT_PASSENGER_MISSING = "passenger_missing"
REPORT_DRIVER_MISSING = "driver_missing"

# What the tool did about it.
OUTCOME_REROUTED = "rerouted"
OUTCOME_DIVERTED = "diverted_to_another_car"
OUTCOME_RESCUE_BUS = "rescue_bus"

# URL path the links point at. Short because the whole link has to survive
# being read off a notification and, at worst, typed.
REPORT_PATH = "/r"

# Truncated HMAC length, in bytes. 12 bytes is 96 bits of tag: far more than a
# roadside attacker gets to guess at, and short enough to keep the link legible.
_SIGNATURE_BYTES = 12

_KIND_CODES = {REPORT_PASSENGER_MISSING: "p", REPORT_DRIVER_MISSING: "d"}
_CODE_KINDS = {code: kind for kind, code in _KIND_CODES.items()}


# Both the tool and whatever renders the messages have to sign links with the
# same key, and only one process at a time may hold the incident database open.
# An environment variable shared between them is the way out; without it, the
# key in the database is used, which works whenever the tool is not running.
LINK_SECRET_ENVIRONMENT = "THANET_LINK_SECRET"


class InvalidLink(Exception):
    """The link is not one this tool issued, or names people who are not paired."""


class NotYet(Exception):
    """The wait is not up, so there is nothing to report yet."""


# --- Clock ------------------------------------------------------------------


@dataclass(frozen=True)
class Clock:
    """The moment the tool believes it is.

    The plan departs at `fleet_departure_time` — a fixed instant that is
    unlikely to be today — so exercising the tool against real plan output means
    moving the tool's clock rather than the plan's. `pinned_to` starts it at a
    chosen moment and lets it run forward at the usual rate; `stopped_at` holds
    it still, which is what a test wants.
    """

    offset: timedelta = timedelta()
    stopped_at: datetime | None = None

    @classmethod
    def pinned_to(cls, moment: datetime) -> "Clock":
        return cls(offset=moment - datetime.now())

    @classmethod
    def frozen(cls, moment: datetime) -> "Clock":
        return cls(stopped_at=moment)

    def now(self) -> datetime:
        return self.stopped_at if self.stopped_at else datetime.now() + self.offset


# --- Links ------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """Who is being reported missing, by whom, and in which direction."""

    kind: str
    subject_person_id: int
    reporter_person_id: int


class LinkBuilder:
    """Signs a claim into a URL, and reads one back out.

    The signature stops one link being renumbered into another person's name.
    It is not authentication: whoever holds the link can file the report it
    carries, exactly as whoever holds the phone can. Real dispatch would tie a
    report to an identity a control room can challenge, and would not put the
    identity in the URL at all.
    """

    def __init__(self, base_url: str, secret: bytes) -> None:
        self.base_url = base_url.rstrip("/")
        self._secret = secret

    def _signature(self, payload: str) -> str:
        digest = hmac.new(self._secret, payload.encode(), sha256).digest()
        return base64.urlsafe_b64encode(digest[:_SIGNATURE_BYTES]).decode().rstrip("=")

    def token(self, claim: Claim) -> str:
        payload = (
            f"{_KIND_CODES[claim.kind]}"
            f"{claim.subject_person_id}-{claim.reporter_person_id}"
        )
        return f"{payload}.{self._signature(payload)}"

    def url(self, claim: Claim) -> str:
        return f"{self.base_url}{REPORT_PATH}/{self.token(claim)}"

    def read(self, token: str) -> Claim:
        payload, _, signature = token.partition(".")
        if not signature or not hmac.compare_digest(
            signature, self._signature(payload)
        ):
            raise InvalidLink("This link was not issued by this tool.")
        try:
            kind = _CODE_KINDS[payload[0]]
            subject, reporter = (int(part) for part in payload[1:].split("-"))
        except (KeyError, IndexError, ValueError) as error:  # pragma: no cover
            raise InvalidLink("This link is malformed.") from error
        return Claim(kind, subject, reporter)

    # The protocol `london_frontline.messaging` renders against.
    def passenger_missing_url(
        self, subject_person_id: int, reporter_person_id: int
    ) -> str:
        return self.url(
            Claim(REPORT_PASSENGER_MISSING, subject_person_id, reporter_person_id)
        )

    def driver_missing_url(
        self, subject_person_id: int, reporter_person_id: int
    ) -> str:
        return self.url(
            Claim(REPORT_DRIVER_MISSING, subject_person_id, reporter_person_id)
        )


# --- The log ----------------------------------------------------------------


@dataclass(frozen=True)
class Amendment:
    """One person moved off the plan and onto something else."""

    person_id: int
    travel_group_id: int
    from_muster_point_id: int
    to_muster_point_id: int | None
    rescue_bus_id: int | None
    meeting_time: datetime
    stop_type: str
    place: Place
    outcome: str


class IncidentLog:
    """Reports received, and the seat changes made in reply.

    Its own database file, opened read-write, kept apart from the warehouse for
    the reason given in this module's docstring. Both tables are append-only:
    nothing here is ever corrected in place, because the sequence of reports is
    the thing an inquiry would want to read.
    """

    def __init__(self, database: str = "data/incidents.duckdb") -> None:
        path = resolve_path(database)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(path))
        self._conn.execute("INSTALL spatial;")
        self._conn.execute("LOAD spatial;")
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.execute("CREATE SEQUENCE IF NOT EXISTS report_ids START 1")
        self._conn.execute("CREATE SEQUENCE IF NOT EXISTS amendment_ids START 1")
        self._conn.execute("CREATE SEQUENCE IF NOT EXISTS rescue_bus_ids START 1")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS no_show_reports (
                report_id BIGINT, reported_at TIMESTAMP, kind VARCHAR,
                muster_point_id BIGINT, subject_person_id BIGINT,
                reporter_person_id BIGINT, phone_attempted BOOLEAN,
                outcome VARCHAR
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seat_amendments (
                amendment_id BIGINT, decided_at TIMESTAMP, person_id BIGINT,
                travel_group_id BIGINT, from_muster_point_id BIGINT,
                to_muster_point_id BIGINT, rescue_bus_id BIGINT,
                meeting_time TIMESTAMP, stop_type VARCHAR,
                uprn BIGINT, easting DOUBLE, northing DOUBLE,
                latitude DOUBLE, longitude DOUBLE, outcome VARCHAR
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rescue_buses (
                rescue_bus_id BIGINT, opened_at TIMESTAMP, departs_at TIMESTAMP,
                capacity BIGINT, easting DOUBLE, northing DOUBLE,
                latitude DOUBLE, longitude DOUBLE
            )
            """
        )
        # The signing key lives with the reports so that links survive a restart
        # of the tool. A link that stopped working when the server was bounced
        # would be a link that fails at the only moment it is ever used.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS signing_key (secret VARCHAR)"
        )

    def close(self) -> None:
        self._conn.close()

    @property
    def secret(self) -> bytes:
        row = self._conn.execute("SELECT secret FROM signing_key").fetchone()
        if row is None:
            generated = secrets.token_urlsafe(32)
            self._conn.execute("INSERT INTO signing_key VALUES (?)", [generated])
            return generated.encode()
        return str(row[0]).encode()

    # --- reports ---
    def record_report(
        self,
        claim: Claim,
        muster_point_id: int,
        reported_at: datetime,
        phone_attempted: bool,
    ) -> int:
        report_id = self._conn.execute("SELECT nextval('report_ids')").fetchone()[0]
        self._conn.execute(
            "INSERT INTO no_show_reports VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            [
                report_id,
                reported_at,
                claim.kind,
                muster_point_id,
                claim.subject_person_id,
                claim.reporter_person_id,
                phone_attempted,
            ],
        )
        return int(report_id)

    def set_outcome(self, report_id: int, outcome: str) -> None:
        self._conn.execute(
            "UPDATE no_show_reports SET outcome = ? WHERE report_id = ?",
            [outcome, report_id],
        )

    def first_report(self, claim: Claim) -> datetime | None:
        """When this exact claim was first made, if it has been made before."""
        row = self._conn.execute(
            """
            SELECT min(reported_at) FROM no_show_reports
            WHERE kind = ? AND subject_person_id = ? AND reporter_person_id = ?
            """,
            [claim.kind, claim.subject_person_id, claim.reporter_person_id],
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def missing_person_ids(self) -> set[int]:
        """Everybody reported absent, in either direction."""
        return {
            int(row[0])
            for row in self._conn.execute(
                "SELECT DISTINCT subject_person_id FROM no_show_reports"
            ).fetchall()
        }

    def stood_down_muster_points(self) -> set[int]:
        """Vehicles whose driver was reported missing, so nobody can drive them."""
        return {
            int(row[0])
            for row in self._conn.execute(
                "SELECT DISTINCT muster_point_id FROM no_show_reports WHERE kind = ?",
                [REPORT_DRIVER_MISSING],
            ).fetchall()
        }

    # --- amendments ---
    def record_amendment(self, amendment: Amendment, decided_at: datetime) -> None:
        self._conn.execute(
            """
            INSERT INTO seat_amendments
            SELECT nextval('amendment_ids'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            """,
            [
                decided_at,
                amendment.person_id,
                amendment.travel_group_id,
                amendment.from_muster_point_id,
                amendment.to_muster_point_id,
                amendment.rescue_bus_id,
                amendment.meeting_time,
                amendment.stop_type,
                amendment.place.uprn,
                amendment.place.easting,
                amendment.place.northing,
                amendment.place.latitude,
                amendment.place.longitude,
                amendment.outcome,
            ],
        )

    def amendments(self) -> list[Amendment]:
        rows = self._conn.execute(
            """
            SELECT person_id, travel_group_id, from_muster_point_id,
                   to_muster_point_id, rescue_bus_id, meeting_time, stop_type,
                   uprn, easting, northing, latitude, longitude, outcome
            FROM seat_amendments ORDER BY amendment_id
            """
        ).fetchall()
        return [
            Amendment(
                person_id=int(row[0]),
                travel_group_id=int(row[1]),
                from_muster_point_id=int(row[2]),
                to_muster_point_id=None if row[3] is None else int(row[3]),
                rescue_bus_id=None if row[4] is None else int(row[4]),
                meeting_time=row[5],
                stop_type=str(row[6]),
                place=Place(
                    uprn=None if row[7] is None else int(row[7]),
                    easting=float(row[8]),
                    northing=float(row[9]),
                    latitude=float(row[10]),
                    longitude=float(row[11]),
                ),
                outcome=str(row[12]),
            )
            for row in rows
        ]

    def amendment_for(self, person_id: int) -> Amendment | None:
        """The latest seat this person was moved to, if any."""
        found = [a for a in self.amendments() if a.person_id == person_id]
        return found[-1] if found else None

    # --- rescue buses ---
    def rescue_buses(self) -> list["RescueBus"]:
        """Every bus dispatched so far, with how many seats it has taken."""
        rows = self._conn.execute(
            """
            SELECT b.rescue_bus_id, b.departs_at, b.capacity,
                   b.easting, b.northing, b.latitude, b.longitude,
                   (SELECT count(*) FROM seat_amendments a
                     WHERE a.rescue_bus_id = b.rescue_bus_id) AS taken
            FROM rescue_buses b ORDER BY b.rescue_bus_id
            """
        ).fetchall()
        return [
            RescueBus(
                rescue_bus_id=int(row[0]),
                departs_at=row[1],
                capacity=int(row[2]),
                place=Place(None, float(row[3]), float(row[4]), float(row[5]),
                            float(row[6])),
                seats_taken=int(row[7]),
            )
            for row in rows
        ]

    def open_rescue_bus(
        self, departs_at: datetime, capacity: int, place: Place, opened_at: datetime
    ) -> int:
        bus_id = self._conn.execute("SELECT nextval('rescue_bus_ids')").fetchone()[0]
        self._conn.execute(
            "INSERT INTO rescue_buses VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                bus_id,
                opened_at,
                departs_at,
                capacity,
                place.easting,
                place.northing,
                place.latitude,
                place.longitude,
            ],
        )
        return int(bus_id)


@dataclass(frozen=True)
class RescueBus:
    """A bus sent to people the cars could not reach.

    It goes to them rather than the other way round. The people most likely to
    be standing at the kerb with no ride are the ones who could not walk to a
    muster point in the first place, so a rescue point they have to reach on
    foot would strand exactly the wrong people.
    """

    rescue_bus_id: int
    departs_at: datetime
    capacity: int
    place: Place
    seats_taken: int

    @property
    def seats_free(self) -> int:
        return self.capacity - self.seats_taken

    @property
    def label(self) -> str:
        return f"Rescue bus {self.rescue_bus_id}"


# --- The case in front of the tool ------------------------------------------


@dataclass(frozen=True)
class Wait:
    """The half hour, and how much of it is left."""

    due_at: datetime
    opens_at: datetime
    now: datetime

    @property
    def ready(self) -> bool:
        return self.now >= self.opens_at

    @property
    def minutes_remaining(self) -> float:
        return max(0.0, (self.opens_at - self.now).total_seconds() / 60.0)

    @property
    def minutes_waited(self) -> float:
        return max(0.0, (self.now - self.due_at).total_seconds() / 60.0)


@dataclass(frozen=True)
class CallTarget:
    """Who to ring before reporting anybody missing, if anybody can be rung."""

    name: str | None
    phone_number: str | None
    via_carer: bool = False
    # True when the missing person has no phone and no carer: nobody has been
    # able to tell them anything, so the question is whether the door was
    # knocked, not whether the phone was answered.
    nobody_to_ring: bool = False

    @property
    def reachable(self) -> bool:
        return self.phone_number is not None


@dataclass(frozen=True)
class Case:
    """One tap on one link, resolved against the plan."""

    claim: Claim
    muster_point_id: int
    capacity: int
    license_plate: str
    leader: Person
    subject: Person
    reporter: Person
    # The stop whose meeting time the wait is measured from: the door the driver
    # is standing at, or the pickup the passenger is standing at.
    stop: Stop
    subject_notification_mode: str
    subject_carer: Person | None
    destination: Destination
    wait: Wait
    already_reported_at: datetime | None

    @property
    def reporter_is_driver(self) -> bool:
        return self.claim.kind == REPORT_PASSENGER_MISSING

    @property
    def call(self) -> CallTarget:
        if not self.reporter_is_driver:
            return CallTarget(self.leader.name, self.leader.phone_number)
        if self.subject_notification_mode == NOTIFY_CARER and self.subject_carer:
            return CallTarget(
                self.subject_carer.name, self.subject_carer.phone_number, via_carer=True
            )
        if self.subject_notification_mode == NOTIFY_PHYSICAL:
            return CallTarget(None, None, nobody_to_ring=True)
        return CallTarget(self.subject.name, self.subject.phone_number)


_ANCHOR_SQL = f"""
    SELECT n.muster_point_id, n.stop_seq, n.stop_type, n.person_id, n.name,
           n.phone_number, n.notification_mode, n.carer_person_id, n.carer_name,
           n.carer_phone,
           s.uprn, s.easting, s.northing, s.meeting_time, {wgs84_columns("s.easting", "s.northing")},
           r.license_plate, r.leader_person_id, r.leader_name, r.leader_phone,
           r.destination_name, r.destination_latitude, r.destination_longitude,
           m.capacity
    FROM stop_notifications n
    JOIN route_stops s
      ON s.muster_point_id = n.muster_point_id AND s.stop_seq = n.stop_seq
    JOIN vehicle_routes r ON r.muster_point_id = n.muster_point_id
    JOIN muster_points m ON m.muster_point_id = n.muster_point_id
    WHERE n.person_id = ?
"""


def _missing(value) -> bool:
    """True for every flavour of missing a DuckDB->pandas read can produce."""
    return value is None or pd.isna(value)


def _person(name, phone=None, person_id=None) -> Person:
    return Person(
        name=str(name),
        phone_number=None if _missing(phone) else str(phone),
        person_id=None if _missing(person_id) else int(person_id),
    )


def _place(row, prefix: str = "") -> Place:
    uprn = row[f"{prefix}uprn"]
    return Place(
        uprn=None if _missing(uprn) else int(uprn),
        easting=float(row[f"{prefix}easting"]),
        northing=float(row[f"{prefix}northing"]),
        latitude=float(row[f"{prefix}latitude"]),
        longitude=float(row[f"{prefix}longitude"]),
    )


def load_case(
    warehouse, log: IncidentLog, claim: Claim, clock: Clock, planning: PlanningConfig
) -> Case:
    """Resolve a link into the situation it describes, or refuse it.

    The person named in the link is looked up in the plan, and the other half of
    the pair is read off the same vehicle rather than trusted from the link. A
    link that names two people who were never in the same car is refused, which
    is what a renumbered link looks like.
    """
    if claim.kind not in _KIND_CODES:
        raise InvalidLink("This link does not name a kind of report.")

    # For a driver reporting a passenger the anchor is the passenger, because
    # the wait is measured at the passenger's stop. For a passenger reporting a
    # driver it is the passenger again — the one standing in the road.
    anchor_id = (
        claim.subject_person_id
        if claim.kind == REPORT_PASSENGER_MISSING
        else claim.reporter_person_id
    )
    frame = warehouse.execute(_ANCHOR_SQL, [anchor_id]).fetchdf()
    if frame.empty:
        raise InvalidLink("Nobody in the plan has that seat any more.")
    row = frame.iloc[0]

    leader = _person(row["leader_name"], row["leader_phone"], row["leader_person_id"])
    anchor = _person(row["name"], row["phone_number"], row["person_id"])
    if claim.kind == REPORT_PASSENGER_MISSING:
        if leader.person_id != claim.reporter_person_id:
            raise InvalidLink("That driver is not the driver of that passenger's car.")
        subject, reporter = anchor, leader
    else:
        if leader.person_id != claim.subject_person_id:
            raise InvalidLink("That car is not the car that passenger was given.")
        subject, reporter = leader, anchor

    meeting_time = row["meeting_time"].to_pydatetime()
    now = clock.now()
    return Case(
        claim=claim,
        muster_point_id=int(row["muster_point_id"]),
        capacity=int(row["capacity"]),
        license_plate=str(row["license_plate"]),
        leader=leader,
        subject=subject,
        reporter=reporter,
        stop=Stop(
            stop_seq=int(row["stop_seq"]),
            stop_type=str(row["stop_type"]),
            meeting_time=meeting_time,
            place=_place(row),
        ),
        subject_notification_mode=str(row["notification_mode"]),
        subject_carer=None
        if _missing(row["carer_name"])
        else _person(row["carer_name"], row["carer_phone"], row["carer_person_id"]),
        destination=Destination(
            name=str(row["destination_name"]),
            latitude=float(row["destination_latitude"]),
            longitude=float(row["destination_longitude"]),
        ),
        wait=Wait(
            due_at=meeting_time,
            opens_at=meeting_time + timedelta(minutes=planning.no_show_wait_minutes),
            now=now,
        ),
        already_reported_at=log.first_report(claim),
    )


# --- A driver's route, with the stop that failed taken out ------------------


@dataclass(frozen=True)
class RevisedStop:
    stop_seq: int
    stop_type: str
    place: Place
    meeting_time: datetime
    people: tuple[str, ...]
    # A leader whose own travel group is collected from home is expected at
    # their own door, in their own car. The stop is theirs, so it survives even
    # when nobody else is left at it.
    collects_driver: bool = False


@dataclass(frozen=True)
class RevisedRoute:
    """Where the driver goes from where they are standing.

    Times are recomputed from now rather than from the fleet departure. The
    driver has just spent the wait at a door; every remaining time in their
    original message is already wrong, and a route that still quotes them is
    worse than no route.
    """

    from_place: Place
    leaves_at: datetime
    stops: tuple[RevisedStop, ...]
    destination: Destination
    map_url: str
    map_stops_dropped: int
    # People still expected at the stop the driver is standing at. A door can
    # hold a whole household, and one absent member is no reason to drive off
    # and leave the rest of them on the step.
    still_here: tuple[str, ...]
    # Later stops that lost their last occupant and were cut out.
    later_stops_dropped: int
    passengers_expected: int
    seats_free: int


def _distance_m(one: Place, other: Place) -> float:
    """Straight line on the British National Grid, which is in metres."""
    return hypot(one.easting - other.easting, one.northing - other.northing)


def revise_route(
    *,
    standing_at: Stop,
    later_stops: "list[Stop]",
    people_by_stop: "dict[int, tuple[tuple[int, str], ...]]",
    absent_person_ids: "set[int]",
    leader_person_id: int,
    capacity: int,
    destination: Destination,
    now: datetime,
    planning: PlanningConfig,
) -> RevisedRoute:
    """Re-time and re-cut a route around the people who are not coming.

    A collection stop with nobody left to collect is dropped: it is a door with
    nobody behind it. The muster point is never dropped — it is where the car
    is — and it is behind the driver in any case by the time this runs.

    Kept free of DuckDB so the cutting and the arithmetic can be tested on
    hand-built stops.
    """
    speed_m_per_second = planning.average_driving_speed_kph * 1000.0 / 3600.0
    dwell = timedelta(minutes=planning.stop_dwell_minutes)

    def remaining(stop_seq: int) -> tuple[tuple[int, str], ...]:
        return tuple(
            (person_id, name)
            for person_id, name in people_by_stop.get(stop_seq, ())
            if person_id not in absent_person_ids
        )

    def still_coming(stop_seq: int) -> tuple[str, ...]:
        """Passengers, so never the driver: a driver is not their own pickup."""
        return tuple(
            name for person_id, name in remaining(stop_seq)
            if person_id != leader_person_id
        )

    kept: list[RevisedStop] = []
    for stop in later_stops:
        left = remaining(stop.stop_seq)
        # Dropped only when everybody expected here has gone — not merely when
        # the last name on the list is the driver's own.
        if not left and stop.stop_type == STOP_COLLECTION:
            continue
        people = still_coming(stop.stop_seq)
        previous = kept[-1].place if kept else standing_at.place
        moment = kept[-1].meeting_time + dwell if kept else now
        moment += timedelta(
            seconds=_distance_m(previous, stop.place) / speed_m_per_second
        )
        kept.append(
            RevisedStop(
                stop_seq=stop.stop_seq,
                stop_type=stop.stop_type,
                place=stop.place,
                meeting_time=moment,
                people=people,
                collects_driver=any(
                    person_id == leader_person_id for person_id, _ in left
                ),
            )
        )

    route = [
        Stop(standing_at.stop_seq, standing_at.stop_type, now, standing_at.place),
        *(
            Stop(stop.stop_seq, stop.stop_type, stop.meeting_time, stop.place)
            for stop in kept
        ),
    ]
    map_url, map_stops_dropped = driving_route_url(tuple(route), destination)

    expected = sum(len(still_coming(stop_seq)) for stop_seq in people_by_stop)
    return RevisedRoute(
        from_place=standing_at.place,
        leaves_at=now,
        stops=tuple(kept),
        destination=destination,
        map_url=map_url,
        map_stops_dropped=map_stops_dropped,
        still_here=still_coming(standing_at.stop_seq),
        later_stops_dropped=len(later_stops) - len(kept),
        passengers_expected=expected,
        seats_free=capacity - 1 - expected,
    )


# --- A passenger's replacement ride -----------------------------------------


@dataclass(frozen=True)
class NewSeat:
    """What the stranded passenger is told to do instead."""

    outcome: str
    label: str
    meeting_time: datetime
    place: Place
    # True when they have to walk to it; False when it comes to them.
    walk_to_it: bool
    destination: Destination
    reason: str
    driver: Person | None = None
    travelling_with: tuple[str, ...] = ()
    seats_taken: int | None = None
    capacity: int | None = None

    @property
    def on_a_bus(self) -> bool:
        return self.outcome == OUTCOME_RESCUE_BUS


@dataclass(frozen=True)
class Resolution:
    """The answer given back to whoever tapped the link."""

    outcome: str
    revised_route: RevisedRoute | None = None
    new_seat: NewSeat | None = None
    # Other people who were in the stood-down car and have been moved too. They
    # get their own message; the reporter is told they were not forgotten.
    also_moved: tuple[str, ...] = ()
    # True when this claim had already been filed and nothing new was recorded.
    replayed: bool = False
    # True when the driver had already reported the reporter absent. They are
    # plainly not absent — they are holding the phone — so they are replanned
    # anyway, and told that the record said otherwise.
    was_marked_absent: bool = False


class PhoneFirst(Exception):
    """Nobody may be reported missing before somebody has tried to reach them."""


@dataclass(frozen=True)
class _Occupant:
    person_id: int
    travel_group_id: int
    name: str
    all_can_walk: bool
    stop_type: str
    place: Place


@dataclass(frozen=True)
class _Candidate:
    muster_point_id: int
    license_plate: str
    leader: Person
    capacity: int
    final_stop_time: datetime
    place: Place
    last_place: Place
    distance_m: float


_STOPS_OF_VEHICLE_SQL = f"""
    SELECT stop_seq, stop_type, uprn, easting, northing, meeting_time,
           {wgs84_columns()}
    FROM route_stops WHERE muster_point_id = ? ORDER BY stop_seq
"""

_OCCUPANTS_SQL = f"""
    SELECT ps.person_id, ps.travel_group_id, p.name, g.all_can_walk,
           n.stop_type, s.uprn, s.easting, s.northing,
           {wgs84_columns("s.easting", "s.northing")}
    FROM person_seats ps
    JOIN persons p USING (person_id)
    JOIN travel_groups g ON g.travel_group_id = ps.travel_group_id
    JOIN stop_notifications n
      ON n.person_id = ps.person_id AND n.muster_point_id = ps.muster_point_id
    JOIN route_stops s
      ON s.muster_point_id = n.muster_point_id AND s.stop_seq = n.stop_seq
    WHERE ps.muster_point_id = ?
    ORDER BY ps.travel_group_id, ps.person_id
"""

_CANDIDATES_SQL = f"""
    SELECT m.muster_point_id, m.capacity, m.easting, m.northing,
           {wgs84_columns("m.easting", "m.northing")},
           r.license_plate, r.leader_person_id, r.leader_name, r.leader_phone,
           r.final_stop_time,
           ST_Distance(ST_Point(m.easting, m.northing), ST_Point(?, ?)) AS distance_m
    FROM muster_points m
    JOIN vehicle_routes r USING (muster_point_id)
    WHERE m.muster_point_id <> ?
      AND ST_DWithin(ST_Point(m.easting, m.northing), ST_Point(?, ?), ?)
    ORDER BY distance_m
    LIMIT ?
"""


def _vehicle_stops(warehouse, muster_point_id: int) -> list[Stop]:
    frame = warehouse.execute(_STOPS_OF_VEHICLE_SQL, [muster_point_id]).fetchdf()
    return [
        Stop(
            stop_seq=int(row["stop_seq"]),
            stop_type=str(row["stop_type"]),
            meeting_time=row["meeting_time"].to_pydatetime(),
            place=_place(row),
        )
        for _, row in frame.iterrows()
    ]


def _people_by_stop(
    warehouse, muster_point_id: int
) -> dict[int, tuple[tuple[int, str], ...]]:
    rows = warehouse.execute(
        "SELECT stop_seq, person_id, name FROM stop_notifications "
        "WHERE muster_point_id = ? ORDER BY stop_seq, person_id",
        [muster_point_id],
    ).fetchall()
    people: dict[int, list[tuple[int, str]]] = {}
    for stop_seq, person_id, name in rows:
        people.setdefault(int(stop_seq), []).append((int(person_id), str(name)))
    return {seq: tuple(names) for seq, names in people.items()}


def _occupants(warehouse, muster_point_id: int) -> list[_Occupant]:
    frame = warehouse.execute(_OCCUPANTS_SQL, [muster_point_id]).fetchdf()
    return [
        _Occupant(
            person_id=int(row["person_id"]),
            travel_group_id=int(row["travel_group_id"]),
            name=str(row["name"]),
            all_can_walk=bool(row["all_can_walk"]),
            stop_type=str(row["stop_type"]),
            place=_place(row),
        )
        for _, row in frame.iterrows()
    ]


def _candidates(
    warehouse, origin: Place, radius_m: float, exclude: int, limit: int
) -> list[_Candidate]:
    frame = warehouse.execute(
        _CANDIDATES_SQL,
        [
            origin.easting,
            origin.northing,
            exclude,
            origin.easting,
            origin.northing,
            radius_m,
            limit,
        ],
    ).fetchdf()
    if frame.empty:
        return []
    last_stops = {
        int(row[0]): (float(row[1]), float(row[2]), float(row[3]), float(row[4]))
        for row in warehouse.execute(
            f"""
            SELECT muster_point_id, easting, northing, {wgs84_columns()}
            FROM route_stops
            WHERE muster_point_id IN (SELECT unnest(?))
            QUALIFY row_number() OVER (
                PARTITION BY muster_point_id ORDER BY stop_seq DESC
            ) = 1
            """,
            [[int(value) for value in frame["muster_point_id"]]],
        ).fetchall()
    }
    candidates = []
    for _, row in frame.iterrows():
        muster_point_id = int(row["muster_point_id"])
        easting, northing, latitude, longitude = last_stops[muster_point_id]
        candidates.append(
            _Candidate(
                muster_point_id=muster_point_id,
                license_plate=str(row["license_plate"]),
                leader=_person(
                    row["leader_name"], row["leader_phone"], row["leader_person_id"]
                ),
                capacity=int(row["capacity"]),
                final_stop_time=row["final_stop_time"].to_pydatetime(),
                place=Place(
                    None,
                    float(row["easting"]),
                    float(row["northing"]),
                    float(row["latitude"]),
                    float(row["longitude"]),
                ),
                last_place=Place(None, easting, northing, latitude, longitude),
                distance_m=float(row["distance_m"]),
            )
        )
    return candidates


def _seats_free(
    warehouse, log: IncidentLog, muster_point_ids: "list[int]"
) -> dict[int, int]:
    """Spare seats per vehicle, after everything the log has been told.

    Seats freed by a no-show are real seats: the whole point of reporting one is
    that somebody else can have it.
    """
    if not muster_point_ids:
        return {}
    seated = {
        int(row[0]): int(row[1])
        for row in warehouse.execute(
            "SELECT muster_point_id, count(*) FROM person_seats "
            "WHERE muster_point_id IN (SELECT unnest(?)) GROUP BY 1",
            [muster_point_ids],
        ).fetchall()
    }
    capacity = {
        int(row[0]): int(row[1])
        for row in warehouse.execute(
            "SELECT muster_point_id, capacity FROM muster_points "
            "WHERE muster_point_id IN (SELECT unnest(?))",
            [muster_point_ids],
        ).fetchall()
    }
    missing = log.missing_person_ids()
    if missing:
        for row in warehouse.execute(
            "SELECT muster_point_id, count(*) FROM person_seats "
            "WHERE person_id IN (SELECT unnest(?)) GROUP BY 1",
            [sorted(missing)],
        ).fetchall():
            if int(row[0]) in seated:
                seated[int(row[0])] -= int(row[1])
    for amendment in log.amendments():
        if amendment.from_muster_point_id in seated:
            seated[amendment.from_muster_point_id] -= 1
        if amendment.to_muster_point_id in seated:
            seated[amendment.to_muster_point_id] += 1
    return {
        muster_point_id: capacity.get(muster_point_id, 0)
        - seated.get(muster_point_id, 0)
        for muster_point_id in muster_point_ids
    }


def _replan_group(
    warehouse,
    log: IncidentLog,
    *,
    group: "list[_Occupant]",
    from_muster_point_id: int,
    now: datetime,
    planning: PlanningConfig,
    stood_down: "set[int]",
) -> "tuple[list[Amendment], NewSeat]":
    """Find one travel group another ride, or put it on a rescue bus.

    The group moves whole. It was seated whole for a reason — a group is the
    people who must not be separated — and a driverless car is no reason to
    start splitting families at the kerb.

    A car qualifies only if it has the seats, has not been stood down itself,
    and has not yet gone. That last test is what usually sends people to the
    bus: the fleet departs together, so by the time a wait of
    `no_show_wait_minutes` is up, every other car has been on the road for
    almost that long.
    """
    first = group[0]
    size = len(group)
    walk_in = first.all_can_walk
    grace = timedelta(minutes=planning.divert_grace_minutes)
    speed_m_per_second = planning.average_driving_speed_kph * 1000.0 / 3600.0
    walk_m_per_minute = planning.walking_speed_kph * 1000.0 / 60.0
    reach_m = (
        planning.walking_ceiling_minutes * walk_m_per_minute / planning.walk_detour_factor
        if walk_in
        else planning.max_collection_distance_m
    )

    nearby = [
        candidate
        for candidate in _candidates(
            warehouse,
            first.place,
            reach_m,
            from_muster_point_id,
            planning.walk_candidates_per_household,
        )
        if candidate.muster_point_id not in stood_down
    ]
    seats = _seats_free(warehouse, log, [c.muster_point_id for c in nearby])
    gone = full = 0

    for candidate in nearby:
        if seats.get(candidate.muster_point_id, 0) < size:
            full += 1
            continue
        if now > candidate.final_stop_time + grace:
            gone += 1
            continue
        if walk_in:
            walk_minutes = (
                candidate.distance_m * planning.walk_detour_factor / walk_m_per_minute
            )
            meeting_time = now + timedelta(minutes=walk_minutes)
            if meeting_time > candidate.final_stop_time + grace:
                gone += 1
                continue
            place, stop_type = candidate.place, STOP_MUSTER
            reason = (
                f"{candidate.license_plate} is parked {candidate.distance_m:.0f} m "
                f"away and has not left yet."
            )
        else:
            drive = timedelta(
                seconds=_distance_m(candidate.last_place, first.place)
                / speed_m_per_second
            )
            meeting_time = max(now, candidate.final_stop_time) + drive
            place, stop_type = first.place, STOP_COLLECTION
            reason = (
                f"{candidate.license_plate} was still {candidate.distance_m:.0f} m "
                f"away and can come to the door."
            )
        amendments = [
            Amendment(
                person_id=occupant.person_id,
                travel_group_id=occupant.travel_group_id,
                from_muster_point_id=from_muster_point_id,
                to_muster_point_id=candidate.muster_point_id,
                rescue_bus_id=None,
                meeting_time=meeting_time,
                stop_type=stop_type,
                place=place,
                outcome=OUTCOME_DIVERTED,
            )
            for occupant in group
        ]
        seat = NewSeat(
            outcome=OUTCOME_DIVERTED,
            label=candidate.license_plate,
            meeting_time=meeting_time,
            place=place,
            walk_to_it=walk_in,
            destination=Destination("", 0.0, 0.0),  # filled in by the caller
            reason=reason,
            driver=candidate.leader,
        )
        return amendments, seat

    # Nothing could be diverted, so a bus is sent to them. Reports close
    # together in space share one, which is what makes it a sweep rather than a
    # taxi per person.
    bus = next(
        (
            candidate
            for candidate in log.rescue_buses()
            if candidate.seats_free >= size
            and candidate.departs_at > now
            and _distance_m(candidate.place, first.place)
            <= planning.rescue_bus_pickup_radius_m
        ),
        None,
    )
    if bus is None:
        departs_at = now + timedelta(minutes=planning.rescue_bus_delay_minutes)
        bus_id = log.open_rescue_bus(
            departs_at, planning.rescue_bus_capacity, first.place, now
        )
        seats_taken, capacity = 0, planning.rescue_bus_capacity
        label = f"Rescue bus {bus_id}"
    else:
        bus_id, departs_at = bus.rescue_bus_id, bus.departs_at
        seats_taken, capacity, label = bus.seats_taken, bus.capacity, bus.label

    if not nearby:
        reason = (
            "No other car in the plan is parked close enough to reach you."
            if walk_in
            else "No other car in the plan was near enough to come to the door."
        )
    elif gone and not full:
        reason = (
            f"All {gone} car(s) near you had already left — the whole fleet "
            f"departs together, so waiting the {planning.no_show_wait_minutes:.0f} "
            f"minutes outlasts them."
        )
    elif full and not gone:
        reason = f"The {full} car(s) near you had no free seats."
    else:
        reason = f"Of the cars near you, {gone} had left and {full} were full."

    amendments = [
        Amendment(
            person_id=occupant.person_id,
            travel_group_id=occupant.travel_group_id,
            from_muster_point_id=from_muster_point_id,
            to_muster_point_id=None,
            rescue_bus_id=bus_id,
            meeting_time=departs_at,
            stop_type=STOP_COLLECTION,
            place=occupant.place,
            outcome=OUTCOME_RESCUE_BUS,
        )
        for occupant in group
    ]
    seat = NewSeat(
        outcome=OUTCOME_RESCUE_BUS,
        label=label,
        meeting_time=departs_at,
        place=first.place,
        walk_to_it=False,
        destination=Destination("", 0.0, 0.0),
        reason=reason,
        seats_taken=seats_taken + size,
        capacity=capacity,
    )
    return amendments, seat


def _with_destination(seat: NewSeat, destination: Destination, others) -> NewSeat:
    return replace(seat, destination=destination, travelling_with=tuple(others))


def _reroute(
    warehouse, log: IncidentLog, case: Case, now: datetime, planning: PlanningConfig
) -> RevisedRoute:
    """The driver's remaining route, with the absent people taken out of it."""
    moved_away = {
        amendment.person_id
        for amendment in log.amendments()
        if amendment.from_muster_point_id == case.muster_point_id
    }
    stops = _vehicle_stops(warehouse, case.muster_point_id)
    return revise_route(
        standing_at=case.stop,
        later_stops=[stop for stop in stops if stop.stop_seq > case.stop.stop_seq],
        people_by_stop=_people_by_stop(warehouse, case.muster_point_id),
        absent_person_ids=log.missing_person_ids() | moved_away,
        leader_person_id=case.leader.person_id or -1,
        capacity=case.capacity,
        destination=case.destination,
        now=now,
        planning=planning,
    )


def _reseat_vehicle(
    warehouse, log: IncidentLog, case: Case, now: datetime, planning: PlanningConfig
) -> Resolution:
    """Move everybody out of a car whose driver never came.

    Everybody, not just whoever tapped the link: the car is not going anywhere,
    and the other occupants are standing at the same kerb without yet knowing
    it. They are re-planned here and told separately.
    """
    already_moved = {amendment.person_id for amendment in log.amendments()}
    missing = log.missing_person_ids()
    stood_down = log.stood_down_muster_points() | {case.muster_point_id}

    # The reporter is never treated as missing, whatever the log says. Two
    # people can each report the other: a driver at the wrong door and a
    # passenger at the right one both tell the truth as they see it, and the
    # one who is demonstrably present is the one holding the phone.
    marked_absent = case.reporter.person_id in missing
    occupants = [
        occupant
        for occupant in _occupants(warehouse, case.muster_point_id)
        if occupant.person_id != case.leader.person_id
        and occupant.person_id not in already_moved
        and (
            occupant.person_id not in missing
            or occupant.person_id == case.reporter.person_id
        )
    ]
    groups: dict[int, list[_Occupant]] = {}
    for occupant in occupants:
        groups.setdefault(occupant.travel_group_id, []).append(occupant)

    reporter_group_id = next(
        (
            occupant.travel_group_id
            for occupant in occupants
            if occupant.person_id == case.reporter.person_id
        ),
        None,
    )
    # The reporter's own group first, so the page can answer the person who is
    # standing there holding the phone before anything else is decided.
    ordering = sorted(groups, key=lambda gid: (gid != reporter_group_id, gid))

    reporter_seat: NewSeat | None = None
    also_moved: list[str] = []
    for group_id in ordering:
        group = groups[group_id]
        amendments, seat = _replan_group(
            warehouse,
            log,
            group=group,
            from_muster_point_id=case.muster_point_id,
            now=now,
            planning=planning,
            stood_down=stood_down,
        )
        for amendment in amendments:
            log.record_amendment(amendment, now)
        if group_id == reporter_group_id:
            reporter_seat = _with_destination(
                seat,
                case.destination,
                [o.name for o in group if o.person_id != case.reporter.person_id],
            )
        else:
            also_moved.extend(occupant.name for occupant in group)

    if reporter_seat is None:  # pragma: no cover - the reporter always has a seat
        raise RuntimeError(f"{case.reporter.name} has no seat in that vehicle.")
    return Resolution(
        outcome=reporter_seat.outcome,
        new_seat=reporter_seat,
        also_moved=tuple(also_moved),
        was_marked_absent=marked_absent,
    )


def _seat_already_given(
    warehouse, log: IncidentLog, case: Case
) -> Resolution | None:
    """Read back the answer a repeated tap was given the first time.

    A link tapped twice must not book two seats, and must not tell somebody
    standing in the road a different story on the second reading.
    """
    amendment = log.amendment_for(case.reporter.person_id or -1)
    if amendment is None:
        return None
    if amendment.to_muster_point_id is not None:
        row = warehouse.execute(
            "SELECT license_plate, leader_person_id, leader_name, leader_phone "
            "FROM vehicle_routes WHERE muster_point_id = ?",
            [amendment.to_muster_point_id],
        ).fetchone()
        label = str(row[0])
        driver = _person(row[2], row[3], row[1])
        reason = f"Already arranged: {label} was given your seat."
        seats_taken = capacity = None
    else:
        buses = {bus.rescue_bus_id: bus for bus in log.rescue_buses()}
        bus = buses[amendment.rescue_bus_id]
        label, driver = bus.label, None
        reason = "Already arranged: a rescue bus is coming to you."
        seats_taken, capacity = bus.seats_taken, bus.capacity
    return Resolution(
        outcome=amendment.outcome,
        new_seat=NewSeat(
            outcome=amendment.outcome,
            label=label,
            meeting_time=amendment.meeting_time,
            place=amendment.place,
            walk_to_it=amendment.stop_type == STOP_MUSTER,
            destination=case.destination,
            reason=reason,
            driver=driver,
            seats_taken=seats_taken,
            capacity=capacity,
        ),
        replayed=True,
    )


def resolve_case(
    warehouse,
    log: IncidentLog,
    case: Case,
    *,
    phone_attempted: bool,
    clock: Clock,
    planning: PlanningConfig,
) -> Resolution:
    """Record an absence and answer it with a plan.

    Both gates are enforced here rather than only in the page, so nothing that
    skips the form can skip the wait or the phone call either.
    """
    if not case.wait.ready:
        raise NotYet(
            f"Nothing can be reported until "
            f"{case.wait.opens_at.strftime('%H:%M')}."
        )
    if not phone_attempted:
        raise PhoneFirst("Try to reach them by phone before reporting them missing.")

    now = clock.now()
    replayed = case.already_reported_at is not None
    report_id = (
        None
        if replayed
        else log.record_report(case.claim, case.muster_point_id, now, phone_attempted)
    )

    if case.claim.kind == REPORT_PASSENGER_MISSING:
        # Recomputed on every tap rather than stored: the times in a route are
        # relative to now, and a route quoted an hour ago is no longer a route.
        resolution = Resolution(
            outcome=OUTCOME_REROUTED,
            revised_route=_reroute(warehouse, log, case, now, planning),
            replayed=replayed,
        )
    else:
        # Checked before replanning, not only on a repeated tap: an earlier
        # report from somebody else in the same car will already have moved
        # this person, and they must be told the same answer as everybody else.
        resolution = _seat_already_given(
            warehouse, log, case
        ) or _reseat_vehicle(warehouse, log, case, now, planning)

    if report_id is not None:
        log.set_outcome(report_id, resolution.outcome)
    return resolution


def previous_resolution(
    warehouse, log: IncidentLog, case: Case, planning: PlanningConfig
) -> Resolution | None:
    """The answer this link was given before, recording nothing new.

    Opening a link twice is normal — the page is read at a kerb, on a phone, by
    somebody with other things happening. The second reading must not book a
    second seat, so the read path is kept separate from the write path.
    """
    if case.already_reported_at is None:
        return None
    if case.claim.kind == REPORT_PASSENGER_MISSING:
        return Resolution(
            outcome=OUTCOME_REROUTED,
            revised_route=_reroute(warehouse, log, case, case.wait.now, planning),
            replayed=True,
        )
    return _seat_already_given(warehouse, log, case)


def signing_key(log: "IncidentLog | None" = None) -> bytes:
    """The key links are signed with: the environment first, then the log."""
    from_environment = os.environ.get(LINK_SECRET_ENVIRONMENT)
    if from_environment:
        return from_environment.encode()
    if log is None:
        raise RuntimeError(
            f"No signing key: set {LINK_SECRET_ENVIRONMENT}, or point at an "
            f"incident database the tool is not currently holding open."
        )
    return log.secret
