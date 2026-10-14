"""The per-attachment event log (design D22): a bounded, client-side record of what the contract
said to one attachment — the frames it applied, the refusals, the closes.

Why it exists (D22): a state change is persistent on the rail and in the status row, and also
leaves a line here, so the operator can see *how* the state arrived ("who took the keyboard, when,
and with what reason") rather than only that it did. The design's event log is client-side only
(D32): it reads no broker log and no worker log, and never reaches the broker (Contract §4).

The log is bounded by `event_log.lines`, so a session left attached for days cannot grow it
without limit; the oldest line drops. Each line is frozen once written: it is what the operator
read, and a later state change never rewrites it.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# The kinds of line the log holds. A frame the model does not know raises no line and changes no
# state (conventions V2), so it never reaches here either — the log is what happened, not every
# byte that crossed the socket.
KIND_ATTACHED = "attached"
KIND_PREFIX = "prefix"
KIND_OUTPUT = "output"
KIND_TOKEN = "token"
KIND_CLIENTS = "clients"
KIND_SIZE = "size"
KIND_NOTICE = "notice"
KIND_STALE = "stale"
KIND_QUIET = "quiet_prompt"
KIND_DATA_PATH = "data_path"
KIND_REFUSED = "refused"
KIND_CLOSE = "close"
KIND_HANDOFF_SENT = "handoff_sent"

# What the workspace taps besides the log's own lines (`app.tapped`): the poll changed a session's record, and a
# session left the screen. Together with every log line they are what `tsb-tui drive` evaluates its waits on.
TAP_RECORD = "record"
TAP_SLOT_CLOSED = "slot_closed"


@dataclass(frozen=True)
class Event:
    """One line of one attachment's log. `at` is a monotonic float, so ordering never depends on
    the wall clock moving; `detail` carries the frame or close as it arrived, never parsed."""

    kind: str
    at: float
    detail: dict[str, Any] = field(default_factory=dict)


class EventLog:
    """A bounded deque of `Event`s, oldest first. `since(seq)` returns only what a caller has not
    seen, so a waiter polls the log rather than racing the screen."""

    def __init__(self, limit: int, on_add: Callable[[Event], None] | None = None) -> None:
        if limit < 1:
            raise ValueError("an event log holds at least one line")
        self._limit = limit
        # Called with each line as it is written: the one way a consumer (drive) learns of a state change
        # without reading the screen.
        self._on_add = on_add
        self._lines: deque[Event] = deque(maxlen=limit)
        self._seq = 0  # a total order across the drops, so `since` never repeats a line it kept

    @classmethod
    def from_config(cls, config: Any, on_add: Callable[[Event], None] | None = None) -> EventLog:
        """The log bounded by `event_log.lines` (D22, DT19)."""
        return cls(config["event_log.lines"], on_add)

    def __len__(self) -> int:
        return len(self._lines)

    @property
    def limit(self) -> int:
        return self._limit

    def add(self, kind: str, when: float, **detail: Any) -> Event:
        self._seq += 1
        event = Event(kind=kind, at=when, detail=detail)
        self._lines.append(event)
        if self._on_add is not None:
            self._on_add(event)
        return event

    def all(self) -> list[Event]:
        return list(self._lines)

    def of_kind(self, kind: str) -> list[Event]:
        return [line for line in self._lines if line.kind == kind]

    def latest(self, kind: str) -> Event | None:
        for line in reversed(self._lines):
            if line.kind == kind:
                return line
        return None