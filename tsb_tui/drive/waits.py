"""Waits (design §2.9.4, D51): predicates over this client's own models, and when a wait fails instead.

A wait is judged here, from bytes this client already received and frames it already applied, never by asking the
core to read output (Contract §6, §13). `until` holds one or more predicates and every one must hold. A predicate
reads a `Probe`, so this module knows neither Textual nor the screen. A wait is evaluated when a model event
arrives (plan R8), and at the two moments an event cannot say: a settled period running out and the timeout.

A wait fails at once, with the reason, when the keyboard leaves this client, its input is refused, a stale prompt or a
countdown warning arrives, or the session ends, removes this client, is detached or becomes unreachable: an agent that
waits through any of those is working on a screen it no longer owns. The exception is a wait for that very thing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

HOLDERS = ("me", "not_me", "nobody")
# How much of the screen a failed wait or a timeout hands back (D51: "the screen's last lines").
TAIL_LINES = 10


class BadWait(ValueError):
    """`until` or `timeout_ms` is malformed; the message says what is wrong."""


class Probe(Protocol):
    """What a wait reads of one session. Every method reads the models as they are now."""

    def holder(self) -> str: ...  # "me", "nobody" (escrow), or "other"
    def record_state(self) -> str | None: ...
    def closed_as(self) -> str | None: ...  # "ended", "removed", "detached", or None while attached
    def cursor_row(self) -> str: ...
    def screen_text(self) -> str: ...
    def reply_text(self) -> str: ...
    def last_output_at(self) -> float | None: ...
    def now(self) -> float: ...
    def tail(self) -> list[str]: ...


@dataclass(frozen=True)
class Until:
    settled_ms: int | None = None
    prompt: re.Pattern[str] | None = None
    screen: re.Pattern[str] | None = None
    reply: re.Pattern[str] | None = None
    holder: str | None = None
    state: str | None = None
    ended: bool | None = None
    event: dict[str, Any] | None = None

    def names(self) -> list[str]:
        return [name for name in ("settled_ms", "prompt", "screen", "reply", "holder", "state", "ended", "event")
                if getattr(self, name) is not None]


def _regex(name: str, value: Any) -> re.Pattern[str]:
    if not isinstance(value, str):
        raise BadWait(f"{name}: expected a regular expression string")
    try:
        # A line anchor (`$`, `^`) means a line: the predicates read lines of a screen, not one long string.
        return re.compile(value, re.MULTILINE)
    except re.error as exc:
        raise BadWait(f"{name}: not a regular expression ({exc})") from None


def parse_until(until: Any, *, default_settled_ms: int) -> Until:
    """`until` as a dict of predicates, or `BadWait`. `settled_ms: true` means the `drive.settled_ms` knob."""
    if not isinstance(until, dict) or not until:
        raise BadWait("until: expected an object with at least one predicate")
    known = {"settled_ms", "prompt", "screen", "reply", "holder", "state", "ended", "event"}
    unknown = sorted(set(until) - known)
    if unknown:
        raise BadWait(f"until: unknown predicate {unknown[0]!r} (one of {', '.join(sorted(known))})")
    fields: dict[str, Any] = {}
    for name, value in until.items():
        if name == "settled_ms":
            if value is True:
                value = default_settled_ms
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise BadWait("settled_ms: expected a number of milliseconds, or true")
            fields[name] = value
        elif name in ("prompt", "screen", "reply"):
            fields[name] = _regex(name, value)
        elif name == "holder":
            if value not in HOLDERS:
                raise BadWait(f"holder: expected one of {', '.join(HOLDERS)}")
            fields[name] = value
        elif name == "state":
            if not isinstance(value, str) or not value:
                raise BadWait("state: expected a session state such as \"live\"")
            fields[name] = value
        elif name == "ended":
            if not isinstance(value, bool):
                raise BadWait("ended: expected true or false")
            fields[name] = value
        else:
            if not isinstance(value, dict) or not value:
                raise BadWait("event: expected an object with at least one field, such as {\"ev\": \"token\"}")
            fields[name] = value
    return Until(**fields)


def timeout_for(value: Any, *, default_ms: int, max_ms: int) -> int:
    """The wait's timeout in ms: the knob's default when none is given, and never above the knob's cap."""
    if value is None:
        return min(default_ms, max_ms)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BadWait("timeout_ms: expected a number of milliseconds")
    return min(value, max_ms)


def matches(wanted: dict[str, Any], event: dict[str, Any]) -> bool:
    """An event has every field a wait's `event` names, with the value it names."""
    return all(key in event and event[key] == value for key, value in wanted.items())


@dataclass(frozen=True)
class Done:
    """The wait is over: `ok`, or the reason it failed (`error`, `why`, and what else the agent should see)."""

    ok: bool
    error: str | None = None
    why: str | None = None
    extra: dict[str, Any] | None = None
    matched: dict[str, Any] | None = None


def _event_failure(event: dict[str, Any], until: Until) -> Done | None:
    """Whether this event, told while the wait ran, fails it. None when the wait is for that very thing."""
    name = event.get("ev")
    waits_for = until.event or {}
    if waits_for.get("ev") == name:
        return None
    if name == "token":
        if not event.get("_left_me") or until.holder is not None:
            return None
        who = event.get("_who")
        if event.get("reason") == "takeover":
            return Done(False, "takeover", f"{who}, the owner, took the keyboard")
        return Done(False, "token_moved",
                    f"the keyboard moved to {who or 'nobody'} ({event.get('reason')})")
    if name == "input_refused":
        return Done(False, "refused", "your input reached TSB and was refused", {"reason": event.get("reason")})
    if name == "stale" and event.get("_stale"):
        return Done(False, "stale", "the screen may be wrong: output was lost",
                    {"you_choose": event.get("you_choose")})
    if name == "notice" and event.get("ending"):
        return Done(False, "notice", "the session is in a warning window and will end soon",
                    {"ends_at": event.get("ends_at"), "ends_by": event.get("ends_by")})
    if name == "ended" and until.ended is not True:
        return Done(False, "ended", f"the session ended ({event.get('state')})")
    if name == "removed":
        return Done(False, "removed", "the owner removed you from this session")
    if name == "detached":
        return Done(False, "detached", "TSB closed your view of this session; it lives on and drive reattaches")
    return None


class Wait:
    """One wait on one session. `check` reads the probe and the events told since the wait began."""

    def __init__(self, until: Until, probe: Probe, *, started: float) -> None:
        self.until = until
        self.probe = probe
        self.started = started
        self._event_seen: dict[str, Any] | None = None

    def check(self, events: list[dict[str, Any]]) -> Done | None:
        """The outcome, or None while it is still open. `events` are those told since the wait began, oldest first."""
        until, probe = self.until, self.probe
        if until.event is not None and self._event_seen is None:
            self._event_seen = next((e for e in events if matches(until.event, e)), None)
        for event in events:
            failure = _event_failure(event, until)
            if failure is not None:
                return failure
        closed = probe.closed_as()
        if closed == "ended" and until.ended is not True:
            return Done(False, "ended", "the session ended")
        if closed == "removed":
            return Done(False, "removed", "the owner removed you from this session")
        if probe.record_state() == "unreachable" and until.state is None:
            return Done(False, "unreachable", "the session cannot be reached; input is refused until it can")
        matched = self._holds()
        return None if matched is None else Done(True, matched=matched)

    def _holds(self) -> dict[str, Any] | None:
        until, probe = self.until, self.probe
        matched: dict[str, Any] = {}
        if until.holder is not None:
            holder = probe.holder()
            if not {"me": holder == "me", "nobody": holder == "nobody", "not_me": holder != "me"}[until.holder]:
                return None
        if until.state is not None and probe.record_state() != until.state:
            return None
        if until.ended is not None and (probe.closed_as() == "ended") != until.ended:
            return None
        for name, text in (("prompt", probe.cursor_row()), ("screen", probe.screen_text()),
                           ("reply", probe.reply_text())):
            pattern = getattr(until, name)
            if pattern is not None:
                found = pattern.search(text)
                if found is None:
                    return None
                matched[name] = found.group(0)
        if until.event is not None:
            if self._event_seen is None:
                return None
            matched["event"] = {k: v for k, v in self._event_seen.items() if not k.startswith("_")}
        if until.settled_ms is not None and self.settled_in() > 0:
            return None
        return matched

    def settled_in(self) -> float:
        """Seconds until the session has been quiet for `settled_ms`, counted from the later of the wait's start and
        the last byte; 0 or less when it has been. Only meaningful when the wait has a `settled_ms`."""
        if self.until.settled_ms is None:
            return 0.0
        last = self.probe.last_output_at()
        since = self.started if last is None else max(self.started, last)
        return since + self.until.settled_ms / 1000.0 - self.probe.now()
