"""What the models say, as the events an agent reads (design §2.9.4 "Events").

Every line of an attachment's event log (`model/events.py`) that an agent needs becomes one event dict here, from the
state the line was written into. Nothing reads the screen. The same dicts are what a wait's `{"event": {...}}` matches
(they are kept whether or not `drive.events` lets them out to stdout), and the ones that fail a wait carry the reason in
a field that starts with an underscore, which is never written.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..model.attachment import AttachmentState
from ..model.events import (
    KIND_CLIENTS,
    KIND_CLOSE,
    KIND_NOTICE,
    KIND_REFUSED,
    KIND_SIZE,
    KIND_STALE,
    KIND_TOKEN,
    Event,
)

# The events `drive.events` may name, and the values `drive.output_events` may hold.
EVENT_NAMES = ("gate", "token", "input_refused", "clients", "stale", "notice", "size", "ended", "removed", "detached",
               "device_login_hostkey", "device_login_prompt", "device_login_result", "request_refused")
OUTPUT_MODES = ("off", "counts")
# At most one `output` count a second per session (D53).
OUTPUT_PERIOD_S = 1.0

# The close a state shows, by the event it is told as. 4010 and 4012 are the two ends; 4014 is the owner removing
# this client; 4011 is the broker closing the view, after which the pump reattaches by itself (DT13).
_ENDED_STATE = {"ended_killed": "killed", "ended_terminated": "terminated"}


def size_text(cols: Any, rows: Any) -> str:
    return f"{cols}x{rows}"


def holder_words(holder: dict[str, Any] | None) -> dict[str, Any] | None:
    """The `holder` of a token frame as the agent reads it: the login and the client, or None for escrow."""
    if holder is None:
        return None
    return {"user": holder.get("user"), "client_id": holder.get("client_id")}


def token_event(session: str, state: AttachmentState, event: Event, own_handoff: bool) -> dict[str, Any]:
    """`token`: who holds the keyboard now, and why. When it comes to this client the holder is `me` and `from` is
    who had it; otherwise the holder is named, `you` is `view_only`, and `your_input: "refused from now"` says so when
    the keyboard left this client by someone else's hand and this client is not the owner's (an owner's typing takes
    it back, so its input is not refused). A keyboard this client handed over itself is no news (§2.9.5 hand_back)."""
    holder = event.detail.get("holder")
    previous = state.token_history[-1]["previous"] if state.token_history else None
    mine = holder is not None and holder.get("client_id") == state.client_id
    had = previous is not None and previous.get("client_id") == state.client_id
    out: dict[str, Any] = {"ev": "token", "session": session, "reason": event.detail.get("reason")}
    if mine:
        out["holder"] = "me"
        if previous is not None and previous.get("user"):
            out["from"] = previous["user"]
    else:
        out["holder"] = holder_words(holder)
        out["you"] = "view_only"
        if had and not own_handoff and not state.is_owner_client:
            out["your_input"] = "refused from now"
    out["_left_me"] = had and not mine
    out["_who"] = None if holder is None else holder.get("user")
    return out


def translate(session: str, event: Event, state: AttachmentState, *, own_handoff: bool) -> list[dict[str, Any]]:
    """The event dicts one log line stands for: none for a line an agent does not read, one for most."""
    kind, detail = event.kind, event.detail
    if kind == KIND_TOKEN:
        return [token_event(session, state, event, own_handoff)]
    if kind == KIND_REFUSED and detail.get("what") == "input":
        return [{"ev": "input_refused", "session": session, "reason": detail.get("reason")}]
    if kind == KIND_CLIENTS:
        return [{"ev": "clients", "session": session, "clients": [
            {"user": c.get("user"), "client_id": c.get("client_id"), "may_control": bool(c.get("may_control"))}
            for c in detail.get("clients", [])]}]
    if kind == KIND_SIZE:
        return [{"ev": "size", "session": session, "size": size_text(detail.get("cols"), detail.get("rows")),
                 "reason": detail.get("reason")}]
    if kind == KIND_NOTICE:
        return [{"ev": "notice", "session": session, "ending": bool(detail.get("ending")),
                 "ends_at": detail.get("ends_at"), "ends_by": detail.get("ends_by")}]
    if kind == KIND_STALE:
        return _stale_events(session, state, detail)
    if kind == KIND_CLOSE:
        show = detail.get("show")
        if show in _ENDED_STATE:
            return [{"ev": "ended", "session": session, "state": _ENDED_STATE[show]}]
        if show == "removed":
            return [{"ev": "removed", "session": session}]
        if show == "detached":
            return [{"ev": "detached", "session": session}]
    return []


def _stale_events(session: str, state: AttachmentState, detail: dict[str, Any]) -> list[dict[str, Any]]:
    """`stale`: the screen may be wrong. The holder is asked to choose, so its event carries the choices, from the
    prompt (`stale_prompt`); any other client is told only that it is stale. A choice made is told to everyone."""
    if "prompt_id" in detail:
        prompt = state.stale_prompt or {}
        choices = [{"n": n, "choice": c.get("choice"), "text": c.get("text")}
                   for n, c in enumerate(prompt.get("choices", []), start=1)]
        return [{"ev": "stale", "session": session, "state": "pending", "you_choose": True, "choices": choices,
                 "_stale": True}]
    if detail.get("state") == "pending":
        if state.holds_keyboard:
            return []  # the holder's prompt follows, and carries the choices
        return [{"ev": "stale", "session": session, "state": "pending", "cause": detail.get("cause"),
                 "you_choose": False, "_stale": True}]
    return [{"ev": "stale", "session": session, "state": detail.get("state"), "choice": detail.get("choice")}]


def public(event: dict[str, Any]) -> dict[str, Any]:
    """The event as it is written: the fields that only a wait reads are dropped."""
    return {key: value for key, value in event.items() if not key.startswith("_")}


class OutputCounter:
    """`output` events: how many bytes arrived, at most one line a second per session (D53). The first chunk after a
    quiet second is told at once; chunks inside the second are added up and told when it ends."""

    def __init__(self, emit: Callable[[dict[str, Any]], None], clock: Callable[[], float],
                 call_later: Callable[[float, Callable[[], None]], Any] | None = None) -> None:
        self._emit = emit
        self._clock = clock
        self._call_later = call_later or (lambda delay, fn: asyncio.get_running_loop().call_later(delay, fn))
        self._pending: dict[str, int] = {}
        self._last: dict[str, float] = {}
        self._armed: set[str] = set()

    def add(self, session: str, count: int) -> None:
        self._pending[session] = self._pending.get(session, 0) + count
        waited = self._clock() - self._last.get(session, float("-inf"))
        if waited >= OUTPUT_PERIOD_S:
            self._flush(session)
        elif session not in self._armed:
            self._armed.add(session)
            self._call_later(OUTPUT_PERIOD_S - waited, lambda: self._flush(session))

    def _flush(self, session: str) -> None:
        self._armed.discard(session)
        count = self._pending.pop(session, 0)
        if count:
            self._last[session] = self._clock()
            self._emit({"ev": "output", "session": session, "bytes": count})
