"""Message-rendering tests. Pure — no warehouse needed."""

from datetime import datetime

import pytest

from london_frontline import messaging
from london_frontline.assets.routes import (
    NOTIFY_CARER,
    NOTIFY_DIRECT,
    NOTIFY_PHYSICAL,
    STOP_COLLECTION,
    STOP_MUSTER,
)
from london_frontline.messaging import (
    Destination,
    Expected,
    MAX_MAP_WAYPOINTS,
    LeaderBriefing,
    PassengerBriefing,
    Person,
    Place,
    Stop,
    driving_route_url,
    render_leader_message,
    render_passenger_message,
)

MUSTER = Place(uprn=None, easting=638126.0, northing=165235.0,
               latitude=51.33630, longitude=1.41731)
HOME = Place(uprn=10009123458, easting=636900.0, northing=164200.0,
             latitude=51.32753, longitude=1.39905)
CANTERBURY = Destination(name="Canterbury", latitude=51.2802, longitude=1.0789)

DRIVER = Person("Dave Hall", "07700 900131")
CAROL = Person("Carol Brown", "07700 900177")
CHILD = Person("Ellis Hall", None)


def leader_briefing() -> LeaderBriefing:
    return LeaderBriefing(
        muster_point_id=1,
        license_plate="GK71 XPV",
        capacity=5,
        leader=DRIVER,
        stops=(
            Stop(
                1, STOP_MUSTER, datetime(2026, 1, 1, 8, 0), MUSTER,
                (
                    Expected(DRIVER, NOTIFY_DIRECT),
                    Expected(CHILD, NOTIFY_CARER, carer=DRIVER),
                ),
            ),
            Stop(
                2, STOP_COLLECTION, datetime(2026, 1, 1, 8, 3), HOME,
                (Expected(CAROL, NOTIFY_DIRECT),),
            ),
        ),
        destination=CANTERBURY,
    )


def passenger_briefing(**overrides) -> PassengerBriefing:
    defaults = dict(
        person=CAROL,
        notification_mode=NOTIFY_DIRECT,
        stop=Stop(2, STOP_COLLECTION, datetime(2026, 1, 1, 8, 3), HOME),
        license_plate="GK71 XPV",
        leader=DRIVER,
        destination=CANTERBURY,
    )
    return PassengerBriefing(**{**defaults, **overrides})


def test_leader_message_lists_every_stop_in_order_with_its_time():
    body = render_leader_message(leader_briefing()).body
    assert body.index("08:00") < body.index("08:03")
    assert "GK71 XPV" in body
    assert "UPRN 10009123458" in body
    assert "CANTERBURY" in body


def test_leader_message_names_who_to_expect_at_each_stop():
    body = render_leader_message(leader_briefing()).body
    assert "Dave Hall (07700 900131)" in body
    assert "Carol Brown (07700 900177)" in body
    # No phone means no bracketed number, not a placeholder that reads like one.
    assert "Ellis Hall: no phone, told via Dave Hall" in body


def test_a_person_nobody_could_reach_is_flagged_to_the_driver():
    """`physical_collection` means the driver at the door is their only notice."""
    briefing = leader_briefing()
    stop = briefing.stops[1]
    unreached = Stop(
        stop.stop_seq, stop.stop_type, stop.meeting_time, stop.place,
        (Expected(CHILD, NOTIFY_PHYSICAL),),
    )
    body = render_leader_message(
        LeaderBriefing(
            muster_point_id=briefing.muster_point_id,
            license_plate=briefing.license_plate,
            capacity=briefing.capacity,
            leader=briefing.leader,
            stops=(briefing.stops[0], unreached),
            destination=briefing.destination,
        )
    ).body
    assert "NOT CONTACTED" in body and "Knock." in body


def test_an_empty_muster_stop_is_not_reported_as_a_failed_contact():
    """Every occupant collected from home means nobody gathers at the car."""
    briefing = leader_briefing()
    empty = Stop(1, STOP_MUSTER, datetime(2026, 1, 1, 8, 0), MUSTER, ())
    body = render_leader_message(
        LeaderBriefing(
            muster_point_id=1, license_plate="GK71 XPV", capacity=5,
            leader=DRIVER, stops=(empty, briefing.stops[1]),
            destination=CANTERBURY,
        )
    ).body
    assert "nobody joins here" in body
    assert "NOT CONTACTED" not in body


def test_passenger_collected_at_home_is_told_not_to_walk():
    body = render_passenger_message(passenger_briefing()).body
    assert "do not need to walk" in body
    assert "08:03" in body
    assert "Dave Hall" in body


def test_a_walk_in_is_told_whose_car_they_are_walking_to():
    """28,931 walk-ins ride with a neighbour: "the car" alone does not locate it."""
    stop = Stop(1, STOP_MUSTER, datetime(2026, 1, 1, 8, 0), MUSTER)

    neighbour = render_passenger_message(
        passenger_briefing(stop=stop, rides_in_own_household_car=False)
    ).body
    assert "walk to a neighbour's car nearby" in neighbour
    assert "08:00" in neighbour

    own = render_passenger_message(
        passenger_briefing(stop=stop, rides_in_own_household_car=True)
    ).body
    assert "walk to your household's own car" in own


def test_only_the_warning_icon_rides_on_the_title():
    """ntfy renders each emoji-shortcode tag as an icon; one is enough."""
    for notification in (
        render_leader_message(leader_briefing()),
        render_passenger_message(passenger_briefing()),
    ):
        assert notification.tags == (messaging.ALERT_TAG,)


def test_carer_message_is_addressed_to_the_carer_about_the_passenger():
    notification = render_passenger_message(
        passenger_briefing(person=CHILD, notification_mode=NOTIFY_CARER, carer=DRIVER)
    )
    assert notification.recipient == DRIVER
    assert notification.on_behalf_of == CHILD
    assert notification.title == messaging.ALERT_TITLE
    # The subject moves out of the shared title into the opening body line.
    assert notification.body.startswith("Ellis Hall's lift is GK71 XPV at 08:03")
    assert "this is about Ellis Hall" in notification.body


def test_carer_mode_without_a_carer_is_an_error():
    with pytest.raises(ValueError, match="no carer"):
        render_passenger_message(
            passenger_briefing(person=CHILD, notification_mode=NOTIFY_CARER)
        )


def test_physical_collection_has_nobody_to_message():
    """Nobody to notify is the whole point of the mode; rendering must not fake one."""
    with pytest.raises(ValueError, match=NOTIFY_PHYSICAL):
        render_passenger_message(
            passenger_briefing(person=CHILD, notification_mode=NOTIFY_PHYSICAL)
        )


def test_every_message_shares_one_header_naming_the_emergency():
    for notification in (
        render_leader_message(leader_briefing()),
        render_passenger_message(passenger_briefing()),
    ):
        assert notification.title == "THANET WILDFIRE - EVACUATION ORDER"
        assert messaging.EXERCISE_BANNER in notification.body
        assert "Thanet" in notification.body
        assert "wildfire" in notification.body.lower()


def test_the_first_body_line_is_this_recipient_s_own_summary():
    """It is what a folded-up notification shows under the shared header."""
    leader = render_leader_message(leader_briefing()).body
    assert leader.startswith("You are driving GK71 XPV, leaving at 08:00")

    passenger = render_passenger_message(passenger_briefing()).body
    assert passenger.startswith("Your lift is GK71 XPV at 08:03")


def test_the_exercise_marker_can_be_dropped_but_the_scenario_cannot():
    notification = render_leader_message(leader_briefing(), exercise=False)
    assert messaging.EXERCISE_BANNER not in notification.body
    assert notification.title == messaging.ALERT_TITLE
    assert messaging.HAZARD_SUMMARY in notification.body


def test_no_message_contains_a_dash_a_phone_keyboard_cannot_type():
    """Requested explicitly: the rendered text stays free of em and en dashes."""
    briefing = leader_briefing()
    rendered = [
        render_leader_message(briefing),
        render_leader_message(briefing, exercise=False),
        render_passenger_message(passenger_briefing()),
        render_passenger_message(
            passenger_briefing(person=CHILD, notification_mode=NOTIFY_CARER,
                               carer=DRIVER)
        ),
    ]
    for notification in rendered:
        for dash in ("\u2014", "\u2013"):
            assert dash not in notification.body, notification.body
            assert dash not in notification.title


def test_messages_ask_for_the_loudest_alert_ntfy_offers():
    """ntfy has no sound field; priority 5 is the only way to make a phone ring."""
    assert render_leader_message(leader_briefing()).priority == 5
    assert render_passenger_message(passenger_briefing()).priority == 5


def test_the_driver_gets_one_link_covering_the_whole_run():
    """Car, then every pickup in plan order, then the destination."""
    body = render_leader_message(leader_briefing()).body
    assert "https://www.google.com/maps/dir/" in body
    url, dropped = driving_route_url(leader_briefing().stops, CANTERBURY)
    assert dropped == 0
    assert url in body
    # Origin is the parked car, destination is Canterbury, pickup is a waypoint.
    assert "origin=51.33630%2C1.41731" in url
    assert "destination=51.28020%2C1.07890" in url
    assert "waypoints=51.32753%2C1.39905" in url
    assert "travelmode=driving" in url


def test_a_single_stop_route_needs_no_waypoints():
    """Most Thanet vehicles collect nobody: car straight to the destination."""
    only = Stop(1, STOP_MUSTER, datetime(2026, 1, 1, 8, 0), MUSTER)
    url, dropped = driving_route_url((only,), CANTERBURY)
    assert "waypoints" not in url
    assert dropped == 0


def test_two_doors_at_one_dwelling_point_become_one_waypoint():
    """Flats share a coordinate; the map should not plot the same spot twice."""
    stops = (
        Stop(1, STOP_MUSTER, datetime(2026, 1, 1, 8, 0), MUSTER),
        Stop(2, STOP_COLLECTION, datetime(2026, 1, 1, 8, 1), HOME),
        Stop(3, STOP_COLLECTION, datetime(2026, 1, 1, 8, 1), HOME),
    )
    url, dropped = driving_route_url(stops, CANTERBURY)
    assert url.count("51.32753%2C1.39905") == 1
    assert "%7C" not in url
    assert dropped == 0


def test_a_route_beyond_the_waypoint_limit_says_what_the_link_omits():
    """Silently dropping pickups from the map would strand those people."""
    # Distinct coordinates, so none of them collapse into one waypoint.
    stops = (Stop(1, STOP_MUSTER, datetime(2026, 1, 1, 8, 0), MUSTER),) + tuple(
        Stop(
            i, STOP_COLLECTION, datetime(2026, 1, 1, 8, i),
            Place(uprn=HOME.uprn + i, easting=HOME.easting, northing=HOME.northing,
                  latitude=HOME.latitude + i / 1000, longitude=HOME.longitude),
        )
        for i in range(2, MAX_MAP_WAYPOINTS + 4)
    )
    _url, dropped = driving_route_url(stops, CANTERBURY)
    assert dropped == 2
    body = render_leader_message(
        LeaderBriefing(
            muster_point_id=1, license_plate="GK71 XPV", capacity=5, leader=DRIVER,
            stops=stops, destination=CANTERBURY,
        )
    ).body
    assert "2 more are listed above but not on the map" in body


def test_a_collected_passenger_gets_a_pin_and_a_walk_in_gets_directions():
    collected = render_passenger_message(passenger_briefing()).body
    assert "https://www.google.com/maps/search/?api=1&query=51.32753%2C1.39905" in collected
    assert "PICKUP POINT IN GOOGLE MAPS" in collected

    stop = Stop(1, STOP_MUSTER, datetime(2026, 1, 1, 8, 0), MUSTER)
    walking = render_passenger_message(passenger_briefing(stop=stop)).body
    assert "travelmode=walking" in walking
    assert "WALKING DIRECTIONS IN GOOGLE MAPS" in walking


def test_links_live_in_the_body_not_in_a_notification_tap_target():
    """Tapping the notification opens the message; the map is a second tap."""
    for notification in (
        render_leader_message(leader_briefing()),
        render_passenger_message(passenger_briefing()),
    ):
        assert "google.com/maps" in notification.body
        assert not hasattr(notification, "click_url")


def test_a_muster_stop_has_no_uprn_to_quote():
    """A parked car is not a property, so there is nothing to name it by."""
    assert MUSTER.describe() == ""
    assert HOME.describe() == "UPRN 10009123458"


def test_no_message_quotes_a_coordinate_or_a_grid_reference():
    """Nobody can act on "grid 626219E 168960N"; the map link does that job."""
    rendered = [
        render_leader_message(leader_briefing()),
        render_passenger_message(passenger_briefing()),
        render_passenger_message(
            passenger_briefing(
                stop=Stop(1, STOP_MUSTER, datetime(2026, 1, 1, 8, 0), MUSTER)
            )
        ),
    ]
    for notification in rendered:
        # Strip the map links first: those legitimately carry coordinates.
        prose = "\n".join(
            line for line in notification.body.splitlines()
            if "google.com/maps" not in line
        )
        assert "grid" not in prose.lower()
        assert "638126E" not in prose and "165235N" not in prose
        assert "51.3" not in prose and "1.4" not in prose


def test_the_number_plate_is_set_apart_on_its_own_line():
    """It is matched against a real car in a hurry, not read out of a sentence."""
    leader = render_leader_message(leader_briefing()).body
    assert "YOUR NUMBER PLATE\n[ GK71 XPV ]" in leader

    passenger = render_passenger_message(passenger_briefing()).body
    assert "LOOK FOR THIS NUMBER PLATE\n[ GK71 XPV ]" in passenger
