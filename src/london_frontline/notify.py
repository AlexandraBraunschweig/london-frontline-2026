"""Delivering a rendered message to a phone, over ntfy.

ntfy is a pub/sub notification service: a publisher POSTs to a topic, and every
device subscribed to that topic gets a push. There is no registration and no
address book, which is exactly what a demonstration needs and exactly why it is
not a real dispatch channel — see the warning on `publish`.

The JSON publish form is used rather than the header form because ntfy sends
titles and tags as HTTP headers, and HTTP headers are latin-1: a title with an
en dash or a non-ASCII name in it fails to encode. The JSON body is UTF-8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests

from london_frontline.messaging import Notification

DEFAULT_SERVER = "https://ntfy.sh"

# ntfy accepts almost anything as a topic name, which is a trap: a topic on the
# public server is readable by anyone who knows or guesses it, so short or
# guessable names leak. Enforce ntfy's own character set and a length that
# leaves room for a random suffix.
_TOPIC_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MIN_SAFE_TOPIC_LENGTH = 12


@dataclass(frozen=True)
class Delivery:
    """What the server said, kept so a caller can report rather than guess."""

    topic: str
    server: str
    message_id: str | None
    status_code: int


def validate_topic(topic: str, *, require_unguessable: bool = True) -> str:
    """Reject topics ntfy would mangle, and warn-by-error on guessable ones."""
    if not _TOPIC_PATTERN.match(topic):
        raise ValueError(
            f"{topic!r} is not a valid ntfy topic: use 1-64 characters from "
            f"A-Z a-z 0-9 _ -"
        )
    if require_unguessable and len(topic) < _MIN_SAFE_TOPIC_LENGTH:
        raise ValueError(
            f"{topic!r} is short enough to be guessed, and topics on the public "
            f"ntfy server are readable by anyone who knows the name. Use at "
            f"least {_MIN_SAFE_TOPIC_LENGTH} characters, or pass "
            f"require_unguessable=False if this is your own private server."
        )
    return topic


def build_payload(notification: Notification, topic: str) -> dict:
    """Map a rendered Notification onto ntfy's JSON publish schema."""
    payload: dict = {
        "topic": topic,
        "title": notification.title,
        "message": notification.body,
        "priority": notification.priority,
    }
    if notification.tags:
        payload["tags"] = list(notification.tags)
    return payload


def publish(
    notification: Notification,
    topic: str,
    *,
    server: str = DEFAULT_SERVER,
    token: str | None = None,
    timeout: float = 10.0,
    require_unguessable: bool = True,
) -> Delivery:
    """Publish one message to an ntfy topic.

    This is a demonstration transport, not an emergency dispatch channel. On the
    public server a topic is a shared secret and nothing more: there is no
    authentication of the recipient, no delivery receipt, and no guarantee of
    ordering or arrival. Real dispatch would go through a channel that can
    address a specific handset and prove it arrived.
    """
    validate_topic(topic, require_unguessable=require_unguessable)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = requests.post(
        server.rstrip("/"),
        json=build_payload(notification, topic),
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    message_id = None
    try:
        message_id = response.json().get("id")
    except ValueError:
        pass
    return Delivery(
        topic=topic,
        server=server.rstrip("/"),
        message_id=message_id,
        status_code=response.status_code,
    )
