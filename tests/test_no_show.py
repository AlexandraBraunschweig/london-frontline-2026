"""Absence-reporting rules. Pure — no warehouse, no server."""

from datetime import datetime, timedelta

import pytest

from london_frontline.assets.routes import (
    NOTIFY_CARER,
    NOTIFY_DIRECT,
    NOTIFY_PHYSICAL,
    STOP_COLLECTION,
    STOP_MUSTER,
)
from london_frontline.config import PlanningConfig
from london_frontline.messaging import Destination, Person, Place, Stop
from london_frontline.no_show import (
    REPORT_DRIVER_MISSING,
    REPORT_PASSENGER_MISSING,
    Case,
    Claim,
    Clock,
    InvalidLink,
    LinkBuilder,
    NotYet,
    PhoneFirst,
    Wait,
    revise_route,
)

PLANNING = PlanningConfig()
DEPARTURE = datetime(2026, 1, 1, 8, 0)
CANTERBURY = Destination("Canterbury", 51.2802, 1.0789)

MUSTER = Place(None, 638126.0, 165235.0, 51.33630, 1.41731)
DOOR = Place(10009123458, 636900.0, 164200.0, 51.32753, 1.39905)
FAR_DOOR = Place(10009123459, 636000.0, 163000.0, 51.31680, 1.38560)

DRIVER = Person("Dave Hall", "07700 900131", person_id=1)
CAROL = Person("Carol Brown", "07700 900177", person_id=5)
CHILD = Person("Ellis Hall", None, person_id=3)


# --- links ------------------------------------------------------------------


def test_a_link_round_trips_through_its_signature():
    links = LinkBuilder("http://tool.example", b"key")
    claim = Claim(REPORT_PASSENGER_MISSING, subject_person_id=5, reporter_person_id=1)
    assert links.read(links.token(claim)) == claim


def test_a_renumbered_link_is_refused():
    """The signature is what stops one link being turned into another."""
    links = LinkBuilder("http://tool.example", b"key")
    token = links.token(Claim(REPORT_PASSENGER_MISSING, 5, 1))
    payload, _, signature = token.partition(".")
    with pytest.raises(InvalidLink):
        links.read(f"{payload.replace('5', '9')}.{signature}")


def test_a_link_signed_with_another_key_is_refused():
    token = LinkBuilder("http://tool.example", b"one").token(
        Claim(REPORT_DRIVER_MISSING, 1, 5)
    )
    with pytest.raises(InvalidLink):
        LinkBuilder("http://tool.example", b"other").read(token)


# --- the wait ---------------------------------------------------------------


def case(
    kind: str = REPORT_PASSENGER_MISSING,
    *,
    now: datetime,
    meeting_time: datetime = DEPARTURE,
    notification_mode: str = NOTIFY_DIRECT,
    subject: Person = CAROL,
    carer: Person | None = None,
) -> Case:
    return Case(
        claim=Claim(kind, subject.person_id or 0, DRIVER.person_id or 0),
        muster_point_id=1,
        capacity=5,
        license_plate="GK71 XPV",
        leader=DRIVER,
        subject=subject if kind == REPORT_PASSENGER_MISSING else DRIVER,
        reporter=DRIVER if kind == REPORT_PASSENGER_MISSING else subject,
        stop=Stop(2, STOP_COLLECTION, meeting_time, DOOR),
        subject_notification_mode=notification_mode,
        subject_carer=carer,
        destination=CANTERBURY,
        wait=Wait(
            due_at=meeting_time,
            opens_at=meeting_time + timedelta(minutes=PLANNING.no_show_wait_minutes),
            now=now,
        ),
        already_reported_at=None,
    )


def test_the_wait_is_not_up_a_minute_early():
    subject = case(now=DEPARTURE + timedelta(minutes=29))
    assert not subject.wait.ready
    assert subject.wait.minutes_remaining == pytest.approx(1.0)


def test_the_wait_is_up_on_the_half_hour():
    assert case(now=DEPARTURE + timedelta(minutes=30)).wait.ready


def test_nothing_is_recorded_before_the_wait_is_up():
    """The gate lives in the domain, so skipping the form does not skip it."""
    with pytest.raises(NotYet):
        from london_frontline.no_show import resolve_case

        resolve_case(
            None,
            None,
            case(now=DEPARTURE + timedelta(minutes=10)),
            phone_attempted=True,
            clock=Clock.frozen(DEPARTURE),
            planning=PLANNING,
        )


def test_nothing_is_recorded_without_trying_the_phone():
    with pytest.raises(PhoneFirst):
        from london_frontline.no_show import resolve_case

        resolve_case(
            None,
            None,
            case(now=DEPARTURE + timedelta(minutes=40)),
            phone_attempted=False,
            clock=Clock.frozen(DEPARTURE + timedelta(minutes=40)),
            planning=PLANNING,
        )


# --- who to ring ------------------------------------------------------------


def test_a_passenger_with_no_phone_is_chased_through_their_carer():
    call = case(
        now=DEPARTURE, notification_mode=NOTIFY_CARER, subject=CHILD, carer=DRIVER
    ).call
    assert call.via_carer and call.phone_number == DRIVER.phone_number


def test_a_passenger_nobody_could_tell_is_knocked_for_instead():
    call = case(
        now=DEPARTURE, notification_mode=NOTIFY_PHYSICAL, subject=CHILD
    ).call
    assert call.nobody_to_ring and not call.reachable


def test_a_passenger_rings_the_driver():
    assert case(REPORT_DRIVER_MISSING, now=DEPARTURE).call.phone_number == (
        DRIVER.phone_number
    )


# --- the revised route ------------------------------------------------------


def route(absent: set[int], now: datetime = DEPARTURE + timedelta(minutes=35)):
    """A car at its muster point with two doors still to call at."""
    return revise_route(
        standing_at=Stop(1, STOP_MUSTER, DEPARTURE, MUSTER),
        later_stops=[
            Stop(2, STOP_COLLECTION, DEPARTURE, DOOR),
            Stop(3, STOP_COLLECTION, DEPARTURE, FAR_DOOR),
        ],
        people_by_stop={1: ((1, "Dave Hall"), (4, "Marcus Odell")),
                        2: ((5, "Carol Brown"),),
                        3: ((7, "Theo Singh"), (1, "Dave Hall"))},
        absent_person_ids=absent,
        leader_person_id=1,
        capacity=5,
        destination=CANTERBURY,
        now=now,
        planning=PLANNING,
    )


def test_a_door_with_nobody_left_behind_it_is_dropped():
    revised = route(absent={5})
    assert [stop.stop_seq for stop in revised.stops] == [3]
    assert revised.later_stops_dropped == 1


def test_a_door_keeps_its_place_while_anybody_is_still_expected():
    revised = route(absent=set())
    assert [stop.stop_seq for stop in revised.stops] == [2, 3]


def test_the_drivers_own_stop_survives_losing_everybody_else():
    """A leader collected from their own door still has to call there."""
    revised = route(absent={7})
    assert [stop.stop_seq for stop in revised.stops] == [2, 3]
    assert revised.stops[-1].collects_driver
    assert revised.stops[-1].people == ()


def test_times_are_recomputed_from_now_not_from_the_departure():
    now = DEPARTURE + timedelta(minutes=35)
    revised = route(absent=set(), now=now)
    assert revised.leaves_at == now
    assert all(stop.meeting_time > now for stop in revised.stops)


def test_the_people_still_at_this_stop_are_named():
    """One absence at a stop is no reason to leave the rest of them on it."""
    revised = route(absent=set())
    assert revised.still_here == ("Marcus Odell",)


def test_a_seat_freed_by_an_absence_is_counted_as_free():
    before = route(absent=set()).seats_free
    assert route(absent={5}).seats_free == before + 1
