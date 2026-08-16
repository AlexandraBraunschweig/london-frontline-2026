"""The pages somebody reads while standing in the road.

Four screens, in the order they are met:

1. **Too early.** The link is live from the moment the message is sent, but the
   report is not. Tapping it inside the wait shows the clock and nothing else —
   no button to press, so no button to press by accident.
2. **Confirm.** The one question worth asking, which is whether they have
   phoned. The number is a `tel:` link, so the call is one tap, and the report
   button does nothing until the caller says the call failed.
3. **Done.** The new plan, on the same screen, immediately: a driver's route
   without the stop, or a passenger's replacement car or rescue bus.
4. **Never mind.** Reached from a link on the confirm screen, for the ordinary
   case where the person turns up while the page is open. Nothing is recorded.

Written for a phone held one-handed in bad light: one column, one decision per
screen, nothing below the fold that matters. The HTML is self-contained — no
fonts, no scripts, no network — because the moment it is read is the moment the
network is worst.
"""

from __future__ import annotations

import errno
import html
import threading
from contextlib import ExitStack
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from london_frontline import no_show
from london_frontline.assets.routes import STOP_COLLECTION
from london_frontline.config import PlanningConfig
from london_frontline.messaging import (
    EXERCISE_BANNER,
    ALERT_TITLE,
    Destination,
    Place,
)
from london_frontline.no_show import (
    Case,
    Clock,
    IncidentLog,
    InvalidLink,
    LinkBuilder,
    NotYet,
    PhoneFirst,
    Resolution,
    REPORT_PATH,
)
from london_frontline.resources import SpatialDuckDBResource

_CSS = """
:root {
  color-scheme: light dark;
  --page: #f4f4f5;
  --card: #ffffff;
  --ink: #18181b;
  --quiet: #52525b;
  --line: #d4d4d8;
  --alarm: #b91c1c;
  --alarm-ink: #ffffff;
  --good: #15803d;
  --flag: #92400e;
  --flag-back: #fef3c7;
}
@media (prefers-color-scheme: dark) {
  :root {
    --page: #09090b;
    --card: #18181b;
    --ink: #fafafa;
    --quiet: #a1a1aa;
    --line: #3f3f46;
    --alarm: #dc2626;
    --good: #22c55e;
    --flag: #fde68a;
    --flag-back: #451a03;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 0 0 3rem;
  background: var(--page);
  color: var(--ink);
  font: 17px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  -webkit-text-size-adjust: 100%;
}
.wrap { max-width: 34rem; margin: 0 auto; padding: 0 1rem; }
.exercise {
  background: var(--flag-back); color: var(--flag);
  font-size: 0.78rem; font-weight: 700; letter-spacing: 0.06em;
  text-align: center; padding: 0.5rem 1rem;
}
.hazard {
  background: var(--alarm); color: var(--alarm-ink);
  padding: 0.85rem 1rem; font-size: 0.85rem; font-weight: 700;
  letter-spacing: 0.04em; text-align: center;
}
h1 { font-size: 1.55rem; line-height: 1.25; margin: 1.5rem 0 0.5rem; }
h2 { font-size: 0.8rem; letter-spacing: 0.08em; text-transform: uppercase;
     color: var(--quiet); margin: 1.75rem 0 0.5rem; }
p { margin: 0.6rem 0; }
.lede { font-size: 1.05rem; color: var(--quiet); }
.card {
  background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 1rem 1.1rem; margin: 0.75rem 0;
}
.card.tight { padding: 0.85rem 1.1rem; }
.clock { font-size: 2.6rem; font-weight: 700; letter-spacing: -0.02em; margin: 0; }
.clock small { display: block; font-size: 0.85rem; font-weight: 400;
               letter-spacing: 0; color: var(--quiet); margin-top: 0.35rem; }
.step { display: flex; gap: 0.85rem; align-items: baseline; }
.step .n {
  flex: none; width: 1.7rem; height: 1.7rem; border-radius: 50%;
  background: var(--ink); color: var(--card); font-size: 0.9rem;
  font-weight: 700; display: grid; place-items: center;
}
a.button, button {
  display: block; width: 100%; text-align: center; font: inherit;
  font-weight: 600; padding: 0.95rem 1rem; border-radius: 12px;
  border: 1px solid var(--line); background: var(--card); color: var(--ink);
  text-decoration: none; margin: 0.6rem 0; cursor: pointer;
}
a.call { background: var(--ink); color: var(--card); border-color: var(--ink);
         font-size: 1.15rem; }
button.report { background: var(--alarm); color: var(--alarm-ink);
                border-color: var(--alarm); font-size: 1.1rem; }
button.report:disabled { opacity: 0.45; }
a.quiet { color: var(--quiet); border: 0; background: none;
          text-decoration: underline; font-weight: 400; }
label.check {
  display: flex; gap: 0.8rem; align-items: flex-start; padding: 0.9rem 0;
  cursor: pointer;
}
label.check input { width: 1.4rem; height: 1.4rem; flex: none; margin: 0.1rem 0 0; }
ol.route { list-style: none; margin: 0; padding: 0; }
ol.route li { display: flex; gap: 0.9rem; padding: 0.7rem 0;
              border-top: 1px solid var(--line); }
ol.route li:first-child { border-top: 0; }
.when { font-variant-numeric: tabular-nums; font-weight: 700; flex: none;
        width: 3.4rem; }
.who { color: var(--quiet); font-size: 0.92rem; }
.plate {
  font-size: 1.6rem; font-weight: 700; letter-spacing: 0.08em;
  text-align: center; padding: 0.7rem; border: 2px solid var(--ink);
  border-radius: 10px; margin: 0.3rem 0 0.6rem;
}
.dropped { text-decoration: line-through; color: var(--quiet); }
.tick { color: var(--good); font-weight: 700; }
.note { font-size: 0.9rem; color: var(--quiet); }
footer { margin-top: 2.5rem; font-size: 0.8rem; color: var(--quiet); }
"""


def _esc(value) -> str:
    return html.escape(str(value))


def _clock(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def page(title: str, body: str, *, exercise: bool = True) -> str:
    banner = f'<div class="exercise">{_esc(EXERCISE_BANNER)}</div>' if exercise else ""
    return f"""<!doctype html>
<html lang="en-GB"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{_esc(title)}</title>
<style>{_CSS}</style>
</head><body>
{banner}
<div class="hazard">{_esc(ALERT_TITLE)}</div>
<div class="wrap">
{body}
<footer>Thanet evacuation planning tool. This page is an exercise built from a
synthetic population; the people named in it do not exist.</footer>
</div>
</body></html>
"""


def _place_button(place: Place, label: str, *, walking: bool) -> str:
    url = place.walking_directions_url if walking else place.pin_url
    return f'<a class="button" href="{_esc(url)}">{_esc(label)}</a>'


def _subject_line(case: Case) -> str:
    """One sentence naming who has not come, and where they were due."""
    if case.reporter_is_driver:
        where = (
            "at their door" if case.stop.stop_type == STOP_COLLECTION else "at your car"
        )
        return (
            f"{_esc(case.subject.name)} was due {where} at "
            f"{_clock(case.stop.meeting_time)}."
        )
    return (
        f"{_esc(case.license_plate)} was due at {_clock(case.stop.meeting_time)}, "
        f"driven by {_esc(case.leader.name)}."
    )


def render_waiting(case: Case) -> str:
    """Tapped inside the wait: the clock, and no way to report anything yet."""
    minutes = int(case.wait.minutes_remaining + 0.5)
    heading = (
        f"Wait for {_esc(case.subject.name)}"
        if case.reporter_is_driver
        else "Wait for the car"
    )
    return page(
        "Not yet",
        f"""
<h1>{heading}</h1>
<p class="lede">{_subject_line(case)}</p>
<div class="card">
  <p class="clock">{minutes} min
    <small>before this can be reported — from
    {_clock(case.wait.opens_at)}</small></p>
</div>
<p>People are late for ordinary reasons: a wrong door, a slow flight of stairs, a
phone left indoors. Reporting somebody missing takes their seat away, so the
tool will not do it early.</p>
<p><strong>Use the time to ring them.</strong> Most absences end with somebody
picking up.</p>
<p class="note">This page refreshes itself. Keep it open, or tap the same link
again after {_clock(case.wait.opens_at)}.</p>
<meta http-equiv="refresh" content="60">
""",
    )


def _call_block(case: Case) -> str:
    call = case.call
    if call.nobody_to_ring:
        return """
<div class="card">
  <div class="step"><span class="n">1</span><div>
    <strong>There is no number to ring.</strong>
    <p class="note">This person has no phone and no carer, so nobody has been
    able to tell them anything at all. The plan expects you at the door. Knock,
    and wait, before you report them.</p>
  </div></div>
</div>
"""
    if not call.reachable:
        return """
<div class="card">
  <div class="step"><span class="n">1</span><div>
    <strong>No number for them in the plan.</strong>
    <p class="note">Nobody can be rung. If anyone with you can reach them
    another way, try that first.</p>
  </div></div>
</div>
"""
    whose = (
        f"{call.name}, who was told on {case.subject.name}'s behalf"
        if call.via_carer
        else call.name
    )
    digits = "".join(ch for ch in call.phone_number if ch.isdigit() or ch == "+")
    return f"""
<div class="card">
  <div class="step"><span class="n">1</span><div>
    <strong>Ring {_esc(whose)} first.</strong>
    <p class="note">Almost every absence ends here.</p>
  </div></div>
  <a class="button call" href="tel:{_esc(digits)}">Call {_esc(call.phone_number)}</a>
</div>
"""


def _confirmation_words(case: Case) -> str:
    if case.call.nobody_to_ring:
        return f"I knocked and waited. {_esc(case.subject.name)} did not come."
    if case.reporter_is_driver:
        return f"I could not reach {_esc(case.subject.name)} by phone."
    return "I could not reach the driver by phone."


def render_confirm(case: Case, *, error: str | None = None) -> str:
    """The report itself: call, tick, press. Nothing else on the screen."""
    heading = (
        f"{_esc(case.subject.name)} is not here"
        if case.reporter_is_driver
        else "The car has not come"
    )
    button = (
        f"They did not come — replan my route"
        if case.reporter_is_driver
        else "Report it — find me another ride"
    )
    waited = int(case.wait.minutes_waited)
    warning = (
        f'<div class="card tight"><strong style="color:var(--alarm)">'
        f"{_esc(error)}</strong></div>"
        if error
        else ""
    )
    return page(
        "Report an absence",
        f"""
<h1>{heading}</h1>
<p class="lede">{_subject_line(case)} You have waited {waited} minutes.</p>
{warning}
{_call_block(case)}
<form method="post">
<div class="card">
  <div class="step"><span class="n">2</span><div>
    <strong>Only if that failed, say so.</strong>
  </div></div>
  <label class="check">
    <input type="checkbox" name="phone_attempted" value="yes"
           onchange="document.getElementById('go').disabled = !this.checked">
    <span>{_confirmation_words(case)}</span>
  </label>
  <button class="report" id="go" type="submit" disabled>{_esc(button)}</button>
</div>
</form>
<p><a class="quiet button" href="?arrived=1">They are here after all — cancel</a></p>
<p class="note">Reporting somebody absent removes them from this vehicle and
replans around them. It is recorded with the time, and it cannot be undone from
this page.</p>
""",
    )


def render_cancelled(case: Case) -> str:
    return page(
        "Nothing recorded",
        f"""
<h1>Good — nothing recorded</h1>
<p class="lede">{_esc(case.subject.name)} stays on the plan exactly as it was.</p>
<p>Close this page and carry on. If they disappear again, the same link still
works.</p>
""",
    )


def _who(stop) -> str:
    """The names under a stop, with the driver's own pickup named as such."""
    names = list(stop.people)
    if stop.collects_driver:
        names.append("you")
    if not names:
        return ""
    return f'<div class="who">{_esc(", ".join(names))}</div>'


def _route_body(case: Case, resolution: Resolution) -> str:
    """A driver's answer: the stop taken out, and the run that is left."""
    route = resolution.revised_route
    assert route is not None
    if route.stops:
        stops = "\n".join(
            f'<li><span class="when">{_clock(stop.meeting_time)}</span>'
            f"<div><strong>"
            + (
                "Collect at "
                + (_esc(stop.place.describe()) or "the door")
                if stop.stop_type == STOP_COLLECTION
                else "Your car"
            )
            + "</strong>"
            + _who(stop)
            + "</div></li>"
            for stop in route.stops
        )
        remaining = f"""
<h2>Your route now</h2>
<div class="card"><ol class="route">
<li><span class="when">{_clock(route.leaves_at)}</span>
    <div><strong>Leave here now</strong></div></li>
{stops}
<li><span class="when">then</span><div><strong>Drive to
    {_esc(route.destination.name)}</strong></div></li>
</ol></div>
<a class="button call" href="{_esc(route.map_url)}">Open the new route in Google
Maps</a>
"""
    else:
        remaining = f"""
<h2>Your route now</h2>
<div class="card"><p><strong>Nobody else to collect.</strong> Drive to
{_esc(route.destination.name)} now — do not wait here any longer.</p></div>
<a class="button call" href="{_esc(route.map_url)}">Open directions to
{_esc(route.destination.name)}</a>
"""
    if route.still_here:
        here = (
            f'<div class="card tight"><p><strong>Do not leave yet.</strong> '
            f'Still expected at this stop: {_esc(", ".join(route.still_here))}.</p>'
            f"</div>"
        )
    else:
        here = "<p>Nothing more at this stop. Move on.</p>"
    if route.later_stops_dropped:
        here += (
            f'<p class="note">{_plural(route.later_stops_dropped, "later stop")} '
            f"also dropped: nobody left to collect there.</p>"
        )
    waypoints = (
        f'<p class="note">The map link holds the first stops only; '
        f"{route.map_stops_dropped} more are listed above.</p>"
        if route.map_stops_dropped
        else ""
    )
    return f"""
<h1><span class="tick">&check;</span> Recorded</h1>
<p class="lede"><span class="dropped">{_esc(case.subject.name)}</span> is marked
as not present and is no longer expected in {_esc(case.license_plate)}.</p>
{here}
{remaining}
{waypoints}
<div class="card tight">
  <p>{_plural(route.passengers_expected, "passenger")} still expected.
  {_plural(route.seats_free, "seat")} now free — control may fill them.</p>
</div>
<p class="note">Somebody will follow {_esc(case.subject.name)} up. Your part is
done: drive.</p>
"""


def _seat_body(case: Case, resolution: Resolution) -> str:
    """A passenger's answer: another car, or the bus that is coming to them."""
    seat = resolution.new_seat
    assert seat is not None
    if seat.on_a_bus:
        headline = "A rescue bus is coming to you"
        instruction = f"""
<p class="lede">Stay exactly where you are. The bus comes to this spot.</p>
<div class="card">
  <div class="plate">{_esc(seat.label)}</div>
  <p class="clock">{_clock(seat.meeting_time)}<small>when it reaches you</small></p>
</div>
{_place_button(seat.place, "Show where you are waiting", walking=False)}
<div class="card tight"><p class="note"><strong>Why a bus:</strong>
{_esc(seat.reason)}</p></div>
"""
    else:
        walk = seat.walk_to_it
        headline = "Another car will take you"
        driver = (
            f"<p>Driver: {_esc(seat.driver.name)}"
            + (f" ({_esc(seat.driver.phone_number)})" if seat.driver.phone_number else "")
            + "</p>"
            if seat.driver
            else ""
        )
        instruction = f"""
<p class="lede">{"Walk to it now." if walk else "It comes to you. Stay where you are."}</p>
<div class="card">
  <div class="plate">{_esc(seat.label)}</div>
  <p class="clock">{_clock(seat.meeting_time)}<small>{"be there by then" if walk else "when it reaches you"}</small></p>
  {driver}
</div>
{_place_button(seat.place, "Walking directions" if walk else "Show the pickup point", walking=walk)}
<div class="card tight"><p class="note">{_esc(seat.reason)}</p></div>
"""
    with_them = (
        f'<p>Travelling with you: {_esc(", ".join(seat.travelling_with))}.</p>'
        if seat.travelling_with
        else ""
    )
    others = (
        f'<p class="note">The other {len(resolution.also_moved)} people in '
        f"{_esc(case.license_plate)} have been replanned too and told separately: "
        f'{_esc(", ".join(resolution.also_moved))}.</p>'
        if resolution.also_moved
        else ""
    )
    contested = (
        '<div class="card tight"><p><strong>Note.</strong> The driver had '
        "already reported you as not there. You are plainly here, so you have "
        "been given a ride anyway and the record now says both.</p></div>"
        if resolution.was_marked_absent
        else ""
    )
    return f"""
<h1><span class="tick">&check;</span> {headline}</h1>
<p>{_esc(case.license_plate)} is stood down: its driver never arrived.</p>
{contested}
{instruction}
{with_them}
<p>Going to: <strong>{_esc(seat.destination.name)}</strong></p>
{others}
"""


def render_result(case: Case, resolution: Resolution) -> str:
    body = (
        _route_body(case, resolution)
        if resolution.revised_route
        else _seat_body(case, resolution)
    )
    if resolution.replayed:
        body = (
            '<div class="card tight"><p class="note">You have already reported '
            "this. Nothing has been recorded twice; this is the same answer as "
            "before.</p></div>" + body
        )
    return page("Done", body)


def render_invalid(message: str) -> str:
    return page(
        "Link not recognised",
        f"""
<h1>This link does not work</h1>
<p class="lede">{_esc(message)}</p>
<p>Do not retype the link — the address carries a signature and a single wrong
character breaks it. Open it from the message you were sent.</p>
<p>If you are stranded and no link works, ring the driver, then the number on
your original message.</p>
""",
    )


def render_index() -> str:
    return page(
        "Evacuation absence reporting",
        """
<h1>Absence reporting</h1>
<p class="lede">There is nothing to see here without a link.</p>
<p>Every evacuation message carries its own links: one under each name on a
driver's route, and one at the foot of a passenger's message. They open the
page for that person and nobody else.</p>
""",
    )


class NoShowTool:
    """One warehouse, one incident log, one lock.

    DuckDB gives no thread safety and wants a single writer, and the request
    handler is threaded, so every request takes the same lock. At the scale this
    runs — a few taps a minute across a district — serialising is free.

    The warehouse is opened read-only and held open, which does block the
    pipeline from writing to it. That is the right way round: while an
    evacuation is running, the plan should not be rebuilt underneath the people
    following it.
    """

    def __init__(
        self,
        *,
        warehouse_database: str,
        incidents_database: str,
        base_url: str,
        clock: Clock | None = None,
        planning: PlanningConfig | None = None,
        exercise: bool = True,
    ) -> None:
        self._stack = ExitStack()
        self.warehouse = self._stack.enter_context(
            SpatialDuckDBResource(database=warehouse_database, read_only=True).connect()
        )
        self.log = IncidentLog(incidents_database)
        self.links = LinkBuilder(base_url, no_show.signing_key(self.log))
        self.clock = clock or Clock()
        self.planning = planning or PlanningConfig()
        self.exercise = exercise
        self.lock = threading.Lock()

    def close(self) -> None:
        self.log.close()
        self._stack.close()

    def case(self, token: str) -> Case:
        claim = self.links.read(token)
        return no_show.load_case(
            self.warehouse, self.log, claim, self.clock, self.planning
        )


class _Handler(BaseHTTPRequestHandler):
    server_version = "thanet-evacuation"

    # --- plumbing ---
    def _send(self, body: str, status: int = 200) -> None:
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        # The link in the address bar is a capability. Keep it out of caches and
        # out of the referrer of anything the page links to.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:
        # The token is the whole of the security of a link, so it is not written
        # to a terminal that scrolls in a control room.
        redacted = tuple(
            f"{REPORT_PATH}/<token>" if str(arg).startswith(f"{REPORT_PATH}/") else arg
            for arg in args
        )
        super().log_message(format, *redacted)

    @property
    def tool(self) -> NoShowTool:
        return self.server.tool  # type: ignore[attr-defined]

    def _token(self, path: str) -> str | None:
        prefix = f"{REPORT_PATH}/"
        return path[len(prefix) :] if path.startswith(prefix) else None

    # --- requests ---
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        url = urlparse(self.path)
        if url.path in ("/", ""):
            self._send(render_index())
            return
        token = self._token(url.path)
        if token is None:
            self._send(render_invalid("That address is not part of this tool."), 404)
            return
        with self.tool.lock:
            try:
                case = self.tool.case(token)
            except InvalidLink as error:
                self._send(render_invalid(str(error)), 404)
                return
            if parse_qs(url.query).get("arrived"):
                self._send(render_cancelled(case))
                return
            if not case.wait.ready:
                self._send(render_waiting(case))
                return
            already = no_show.previous_resolution(
                self.tool.warehouse, self.tool.log, case, self.tool.planning
            )
        self._send(render_result(case, already) if already else render_confirm(case))

    def do_POST(self) -> None:  # noqa: N802
        token = self._token(urlparse(self.path).path)
        if token is None:
            self._send(render_invalid("That address is not part of this tool."), 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode())
        with self.tool.lock:
            try:
                case = self.tool.case(token)
            except InvalidLink as error:
                self._send(render_invalid(str(error)), 404)
                return
            try:
                resolution = no_show.resolve_case(
                    self.tool.warehouse,
                    self.tool.log,
                    case,
                    phone_attempted=bool(form.get("phone_attempted")),
                    clock=self.tool.clock,
                    planning=self.tool.planning,
                )
            except NotYet:
                self._send(render_waiting(case))
                return
            except PhoneFirst as error:
                self._send(render_confirm(case, error=str(error)), 400)
                return
        self._send(render_result(case, resolution))


def serve(tool: NoShowTool, host: str = "127.0.0.1", port: int = 8420) -> None:
    """Run until interrupted."""
    try:
        server = ThreadingHTTPServer((host, port), _Handler)
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        # Restarting the tool a moment after stopping it is the normal way to
        # meet this, and a bare traceback is a poor answer to it.
        raise SystemExit(
            f"Port {port} is busy. Another copy may still be shutting down — "
            f"wait a moment and try again, or use --port {port + 1}."
        ) from error
    server.tool = tool  # type: ignore[attr-defined]
    try:
        server.serve_forever()
    finally:
        server.server_close()
