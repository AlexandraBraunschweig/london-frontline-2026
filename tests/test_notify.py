"""ntfy transport tests. No network — payload shape and topic validation only."""

import pytest

from london_frontline.messaging import Notification, Person
from london_frontline.notify import build_payload, validate_topic

NOTIFICATION = Notification(
    title="Thanet wildfire - your lift is GK71 XPV at 08:03",
    body="line one\nline two",
    tags=("rotating_light",),
    priority=5,
    recipient=Person("Carol Brown", "07700 900177"),
)


def test_payload_carries_the_message_in_json_not_headers():
    """Titles and tags are latin-1 HTTP headers in ntfy's other publish form."""
    payload = build_payload(NOTIFICATION, "thanet-evac-x7k2m9")
    assert payload["topic"] == "thanet-evac-x7k2m9"
    assert payload["title"] == NOTIFICATION.title
    assert payload["message"] == "line one\nline two"
    assert payload["priority"] == 5
    assert payload["tags"] == ["rotating_light"]


def test_the_payload_is_only_the_message_with_nothing_to_tap_through_to():
    """No click target and no action buttons: the notification is the message."""
    payload = build_payload(NOTIFICATION, "thanet-evac-x7k2m9")
    assert "click" not in payload
    assert "actions" not in payload


@pytest.mark.parametrize("topic", ["has spaces", "punctuation!", "", "a" * 65])
def test_topics_ntfy_would_mangle_are_rejected(topic):
    with pytest.raises(ValueError, match="not a valid ntfy topic"):
        validate_topic(topic)


def test_a_guessable_topic_is_rejected_because_the_public_server_is_readable():
    with pytest.raises(ValueError, match="guessed"):
        validate_topic("thanet")
    assert validate_topic("thanet", require_unguessable=False) == "thanet"
