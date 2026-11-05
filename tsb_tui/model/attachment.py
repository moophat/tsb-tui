"""One attachment's contract state and the pump that owns its socket (design §3.3, DT17, DT20).

`AttachmentState` is pure: it applies every server frame and close of one attachment and exposes
typed state. `AttachmentPump` owns one `AsyncAttachment` and is its **only reader** (DT17): it
reads `recv_message()`, applies text frames to the state, and hands binary data to an output sink
with its role (`dump` after a `prefix`, else `output`). Every send (`send`, `resize(timeout=0)`,
`handoff`, the answers) goes through the pump's own methods, which never read — the contract
client's `resize` would otherwise read the socket (attachment.py:219-222) with no read lock beside
the pump.

DT20 splits that pump into a reader and a processor, so a firehose cannot starve the pings. The
**reader** only reads: it answers pings inside `recv` as it always did, appends each message to an
in-order queue, and stops reading while that queue's output passes `READ_AHEAD_BYTES` (1 MiB), so
TCP pushes back and the core's drain rule applies. The **processor** is a task on the loop that
takes the queue in order: a frame is applied exactly as before, consecutive output chunks become one
run, and each run goes to the attachment's feed thread (`run_in_executor` on a one-thread executor).
Because the processor waits for a run before taking the next item, bytes, frames and replies keep
exactly today's order — DT3's muting, DT4 (b), DT12 and session-attach CR3 are unchanged. The feed
thread's reply effects are collected on the thread and delivered on the loop before the next frame,
so a reply produced by byte *k* is judged against the state as of byte *k*.

Why the close table matters (DT13, conventions V3): every WebSocket close means something
different, and getting one wrong loses the operator's session. 4014 must never auto-reattach
(client-remove rule 8: "MUST NOT re-attach by itself after 4014"); 4011 is a detach, never an end
(session-detach rule 10); a code-less close or 1006 is the transport dying, and the *record* — not
the close — says whether the session lives (session model §13: a client's WebSocket dying ends
nothing). 4001 retries exactly once, because a one-time ticket may have expired between mint and
presentation (session-attach R4).

Nothing here imports Textual (design §3.3).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from tsb_contract_client import ApiError, AsyncAttachment, AttachRefused

from .._transport import ConnectionClosed
from .events import (
    KIND_ATTACHED,
    KIND_CLIENTS,
    KIND_CLOSE,
    KIND_DATA_PATH,
    KIND_HANDOFF_SENT,
    KIND_NOTICE,
    KIND_OUTPUT,
    KIND_PREFIX,
    KIND_QUIET,
    KIND_REFUSED,
    KIND_SIZE,
    KIND_STALE,
    KIND_TOKEN,
    EventLog,
)

# The close codes this client acts on (ws-frames.schema.json#/x-close-codes).
CLOSE_KILLED = 4010
CLOSE_TERMINATED = 4012
CLOSE_REMOVED = 4014
CLOSE_SESSION_NOT_FOUND = 4004
CLOSE_FORBIDDEN = 4003
CLOSE_BAD_ATTACH = 4000
CLOSE_TICKET_INVALID = 4001
CLOSE_DETACHED_BY_SERVER = 4011

# Transport codes that mean "the socket died", never an end state (RFC 6455; _wsframe).
CLOSE_NORMAL = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_ABNORMAL = 1006
CLOSE_INTERNAL = 1011

# The states in which a transport loss invites a reattach (DT13: while the record is live,
# detached or unreachable).
_LIVE_STATES = frozenset({"live", "detached", "unreachable"})

# D26: the reattach backoff, and the steady cadence after the last step.
BACKOFF_MS = (500, 1000, 2000, 5000, 10000, 30000)
STEADY_MS = 30000

# DT20: the reader stops reading once this many output bytes sit unfed, so TCP pushes back and the
# core's 8 MiB drain rule (session-detach rule 3) still applies. The feed thread hands the engine
# 32 KiB at a time (FEED_SLICE_BYTES, term/backend.py), so it can let a loop-side caller take the
# backend lock between slices.
READ_AHEAD_BYTES = 1 << 20


def _is_unknown_4xxx(code: int | None) -> bool:
    return code is not None and 4000 <= code <= 4999


@dataclass(frozen=True)
class CloseOutcome:
    """What one close means: the state to show, whether to reattach, and whether to read the
    record. `show` is a stable label the UI and drive map to text; `read_record` asks
    `GET /sessions/{id}` for the truth, because the close alone does not carry it."""

    show: str
    reattach: str  # "never" | "once" | "backoff"
    read_record: bool = False
    go_login: bool = False
    loud: bool = False


# DT13's close table, one row per code. Unknown 4xxx reads as 4010 (V3).
_CLOSE_TABLE: dict[int, CloseOutcome] = {
    CLOSE_KILLED: CloseOutcome("ended_killed", "never"),
    CLOSE_TERMINATED: CloseOutcome("ended_terminated", "never"),
    CLOSE_REMOVED: CloseOutcome("removed", "never"),
    CLOSE_SESSION_NOT_FOUND: CloseOutcome("session_gone", "never", read_record=True),
    CLOSE_FORBIDDEN: CloseOutcome("may_not_join", "never"),
    CLOSE_BAD_ATTACH: CloseOutcome("attach_defect", "never", loud=True),
    CLOSE_TICKET_INVALID: CloseOutcome("ticket_refused", "once"),
    CLOSE_DETACHED_BY_SERVER: CloseOutcome("detached", "backoff"),
}
_TRANSPORT_CODES = frozenset({CLOSE_NORMAL, CLOSE_GOING_AWAY, CLOSE_ABNORMAL, CLOSE_INTERNAL})


def close_outcome(code: int | None) -> CloseOutcome:
    """The row of DT13's table for one close. `None` is a socket that died with no close frame."""
    if code is None:
        return CloseOutcome("transport_lost", "backoff", read_record=True)
    if code in _CLOSE_TABLE:
        return _CLOSE_TABLE[code]
    if code in _TRANSPORT_CODES:
        return CloseOutcome("transport_lost", "backoff", read_record=True)
    if _is_unknown_4xxx(code):
        return _CLOSE_TABLE[CLOSE_KILLED]  # unknown 4xxx reads as 4010
    return CloseOutcome("transport_lost", "backoff", read_record=True)


# The fields `snapshot()` compares; a test proves an unknown frame changes none of them.
_SNAPSHOT_FIELDS = (
    "client_id", "user", "owner", "holder", "handoff_in_flight", "clients", "size_cols",
    "size_rows", "size_reason", "resizable", "pinned", "stale_state", "stale_cause",
    "stale_gap_seq", "stale_choice", "stale_prompt", "quiet_prompt", "ends_at", "ends_by",
    "ending", "data_path", "data_path_at", "last_input_refused", "last_request_refused",
    "recording", "dump_seen", "closed", "dump_cols", "dump_rows", "size_final", "refusal_active",
    "reattach", "reattach_stopped", "record_state", "close_code",
)


class AttachmentState:
    """Every frame and close of one attachment, as state. No I/O."""

    def __init__(self, client_id: str | None = None, *, user: str | None = None,
                 session_id: str | None = None, event_log: EventLog | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.session_id = session_id
        # Received items include frames as well as bytes; applied items trail a blocked feed.
        self.received_items = 0
        self.applied_items = 0
        self.received_keyboard_items = 0
        self.applied_keyboard_items = 0
        self.received_stale_items = 0
        self.applied_stale_items = 0
        self.output_bytes_applied = 0
        self.keyboard_moves = 0
        self.size_changes = 0
        self.stale_changes = 0
        self.attachment_generation = 0
        self.client_id = client_id
        self.user = user
        self._clock = clock
        self.log = event_log if event_log is not None else EventLog(2000)
        self.session: dict[str, Any] = {}
        self.owner: str | None = None
        self.holder: dict[str, Any] | None = None
        self.handoff_in_flight = False
        self.clients: list[dict[str, Any]] = []
        self.size_cols: int | None = None
        self.size_rows: int | None = None
        self.resizable: bool | None = None
        self.pinned: bool | None = None
        self.last_size: dict[str, Any] | None = None
        self.size_reason: str | None = None
        self.stale_state: str | None = None
        self.stale_cause: str | None = None
        self.stale_gap_seq: int | None = None
        self.stale_prompt: dict[str, Any] | None = None
        self.stale_choice: str | None = None
        self.quiet_prompt: dict[str, Any] | None = None
        self.ends_at: str | None = None
        self.ends_by: str | None = None
        self.ending = False
        self.data_path: str | None = None
        self.data_path_at: str | None = None
        self.last_input_refused: str | None = None
        self.last_request_refused: dict[str, Any] | None = None
        # How many `request_refused` frames this attachment has seen: a dialog that sent a request
        # shows a refusal that arrived after it, not one it already showed (typing-ownership step 13).
        self.refusal_count = 0
        # Explicit and automatic size requests this client sent, and what came back (size-control
        # steps 1, 11): an answer is the next `size` frame after a request. `size_final` is a clamped
        # answer: the surface asks no more until its own window changes (terminal-size §3).
        self.size_requests_pending = 0
        self.last_size_request: tuple[int, int] | None = None
        self.last_size_answer: dict[str, Any] | None = None
        self.size_final = False
        self.recording: dict[str, Any] | None = None
        self.last_output_at: float | None = None
        self.token_history: list[dict[str, Any]] = []
        self.closed = False
        self.close: CloseOutcome | None = None
        self.close_code: int | None = None
        # What `GET /sessions/{id}` said when a close left the record to decide (4004, a socket that
        # died): the state the session is really in, or None when it could not be read.
        self.record_state: str | None = None
        # A refusal the operator should still see: set by `input_refused`, cleared by a restored data
        # path, a new attachment, or by the screen once a later input goes unrefused (D22; G11).
        self.refusal_active = False
        self.refusal_at: float | None = None
        # The reattach in progress: the attempt about to be made, when it is due (this state's clock)
        # and why; None when nothing is pending. `reattach_stopped` is set when the operator stopped
        # it, or `reattach.auto` is off: the session lives on and nothing more is tried.
        self.reattach: dict[str, Any] | None = None
        self.reattach_stopped = False
        self.prefix: dict[str, Any] | None = None
        self.dump_seen = False
        self.dump_cols: int | None = None
        self.dump_rows: int | None = None

    # -- derived --

    @property
    def holder_client_id(self) -> str | None:
        return None if self.holder is None else self.holder.get("client_id")

    @property
    def holds_keyboard(self) -> bool:
        return self.holder is not None and self.holder.get("client_id") == self.client_id

    @property
    def is_owner_client(self) -> bool:
        return self.user is not None and self.user == self.owner

    @property
    def may_type(self) -> bool:
        """DT4 (a): typed keys go out when this client holds the keyboard or is one of the owner's
        clients (the owner's typing takes the keyboard, 5.53)."""
        return self.holds_keyboard or self.is_owner_client

    @property
    def size_in_effect(self) -> tuple[int | None, int | None]:
        return self.size_cols, self.size_rows

    def may_control(self, client_id: str | None) -> bool:
        for client in self.clients:
            if client.get("client_id") == client_id:
                return bool(client.get("may_control"))
        return False

    def snapshot(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in _SNAPSHOT_FIELDS}

    # -- applying frames --

    def apply_frame(self, frame: dict[str, Any]) -> None:
        """Apply one server frame. An unknown `t`, a known `t` with an unknown `v`, and unknown
        fields change nothing (V1, V2)."""
        if not isinstance(frame, dict) or frame.get("v") != 1:
            return
        if frame.get("t") == "token":
            self.applied_keyboard_items += 1
        elif frame.get("t") in ("stale", "stale_prompt"):
            self.applied_stale_items += 1
        handler = _HANDLERS.get(frame.get("t"))
        if handler is not None:
            handler(self, frame)

    def now(self) -> float:
        """This state's own clock (monotonic): what `reattach["due"]` is measured against."""
        return self._clock()

    def _add_event(self, kind: str, **detail: Any) -> None:
        self.log.add(kind, self._clock(), **detail)

    def apply_attached(self, frame: dict[str, Any]) -> None:
        self.attachment_generation += 1
        self.client_id = frame["client_id"]
        record = frame["session"]
        self.session = record
        self.owner = record.get("owner")
        self.holder = record.get("control_holder")
        self.clients = list(record.get("clients", []))
        self.size_cols = record.get("cols")
        self.size_rows = record.get("rows")
        self.resizable = record.get("resizable")
        self.pinned = record.get("pinned")
        self.recording = record.get("recording")
        self.ends_at = record.get("ends_at")
        self.ends_by = record.get("ends_by")
        self.ending = bool(record.get("ending", False))
        # A (re)attach starts a new attachment: nothing in flight, no prompt or close carried over.
        # A stale choice made while this client was detached is over, and one still pending is told
        # again after `clients` (session-attach step 16; stale-screen step 5): the model takes it
        # from that frame, so a state kept from the dropped attachment can never block input.
        self.handoff_in_flight = False
        self.stale_state = None
        self.stale_cause = None
        self.stale_gap_seq = None
        self.stale_choice = None
        self.stale_prompt = None
        self.quiet_prompt = None
        # The data path is attachment-local: a marker from the dropped connection must not survive,
        # or a recovered session still shows "restoring" when its new data path is fine.
        self.data_path = None
        self.data_path_at = None
        self.size_requests_pending = 0
        self.last_size_request = None
        self.last_size_answer = None
        self.size_final = False
        self.closed = False
        self.close = None
        self.close_code = None
        self.reattach = None
        self.reattach_stopped = False
        self.refusal_active = False
        self.dump_seen = False
        self._learn_user()
        self._add_event(KIND_ATTACHED, session_id=record.get("id"), client_id=self.client_id,
                        holder=self.holder)

    def apply_prefix(self, frame: dict[str, Any]) -> None:
        self.prefix = frame
        # The prefix's cols/rows are the dump's geometry (CR3), not the size in effect. A `size`
        # frame that arrived before the prefix is the latest size in effect and is kept for after
        # the dump (phase 2, order rule); the dump still renders at the prefix's geometry.
        self.dump_cols = frame.get("cols")
        self.dump_rows = frame.get("rows")
        if self.size_cols is None:
            self.size_cols = frame.get("cols")
        if self.size_rows is None:
            self.size_rows = frame.get("rows")
        self._add_event(KIND_PREFIX, through_seq=frame.get("through_seq"),
                        cols=self.dump_cols, rows=self.dump_rows)

    def apply_clients(self, frame: dict[str, Any]) -> None:
        self.clients = list(frame.get("clients", []))
        self._learn_user()
        self._add_event(KIND_CLIENTS, clients=self.clients)

    def apply_token(self, frame: dict[str, Any]) -> None:
        # CR1: the holder is the frame's holder, whatever the reason; an unknown reason reads by
        # `holder`. Any token frame clears our in-flight handoff (DT4 b).
        previous = self.holder
        if previous != frame.get("holder"):
            self.keyboard_moves += 1
        self.holder = frame.get("holder")
        self.handoff_in_flight = False
        # Any token move voids the outstanding stale prompt: whoever receives the token is asked anew
        # (stale-screen step 8), so a prompt id from before the move must never be answered.
        self.stale_prompt = None
        self._learn_user()
        self.token_history.append({"holder": self.holder, "reason": frame.get("reason"),
                                   "previous": previous, "at": self._clock()})
        self._add_event(KIND_TOKEN, holder=self.holder, reason=frame.get("reason"))

    def apply_size(self, frame: dict[str, Any]) -> None:
        # An unknown reason reads cols/rows as the size in effect (V5).
        if (self.size_cols, self.size_rows) != (frame.get("cols"), frame.get("rows")):
            self.size_changes += 1
        self.size_cols = frame.get("cols")
        self.size_rows = frame.get("rows")
        self.last_size = frame
        self.size_reason = frame.get("reason")
        if self.size_requests_pending:
            # The answer to a request this client sent (one per request, in order). A size another
            # client's request applied reaches every client the same way and is not an answer.
            self.size_requests_pending -= 1
            self.last_size_answer = {"asked": self.last_size_request, "cols": self.size_cols,
                                     "rows": self.size_rows, "reason": self.size_reason}
            self.size_final = str(self.size_reason).startswith("clamped_")
        self._add_event(KIND_SIZE, cols=self.size_cols, rows=self.size_rows, reason=self.size_reason)

    def apply_notice(self, frame: dict[str, Any]) -> None:
        # Only the core computes the countdown (session-countdown rule 9). An unknown ends_by shows
        # ends_at (rule 10); ending false withdraws the warning (rule 11).
        self.ends_at = frame.get("ends_at")
        self.ends_by = frame.get("ends_by")
        self.ending = bool(frame.get("ending", False))
        self._add_event(KIND_NOTICE, ends_at=self.ends_at, ends_by=self.ends_by, ending=self.ending)

    def apply_stale(self, frame: dict[str, Any]) -> None:
        self.stale_changes += 1
        self.stale_state = frame.get("state")
        self.stale_cause = frame.get("cause")
        self.stale_gap_seq = frame.get("gap_seq")
        if self.stale_state == "chosen":
            self.stale_choice = frame.get("choice")
            self.stale_prompt = None
        self._add_event(KIND_STALE, state=self.stale_state, cause=self.stale_cause,
                        gap_seq=self.stale_gap_seq, choice=self.stale_choice)

    def apply_stale_prompt(self, frame: dict[str, Any]) -> None:
        self.stale_changes += 1
        self.stale_prompt = frame
        self._add_event(KIND_STALE, prompt_id=frame.get("prompt_id"), code=frame.get("code"))

    def apply_quiet_prompt(self, frame: dict[str, Any]) -> None:
        self.quiet_prompt = frame
        self._add_event(KIND_QUIET, prompt_id=frame.get("prompt_id"), code=frame.get("code"))

    def apply_input_refused(self, frame: dict[str, Any]) -> None:
        # An unknown reason reads as "input was refused" (V5).
        self.last_input_refused = frame.get("reason", "refused")
        self.refusal_active = True
        self.refusal_at = self._clock()
        self._add_event(KIND_REFUSED, what="input", reason=self.last_input_refused)

    def apply_request_refused(self, frame: dict[str, Any]) -> None:
        self.last_request_refused = {"request": frame.get("request"), "reason": frame.get("reason")}
        self.refusal_count += 1
        if frame.get("request") == "handoff":
            # A refused hand-off moves nothing and no `token` frame follows it (typing-ownership
            # steps 12-13), so it is no longer in flight: left set, derived input would stay blocked
            # for good.
            self.handoff_in_flight = False
        self._add_event(KIND_REFUSED, what="request", **self.last_request_refused)

    def apply_data_path(self, frame: dict[str, Any]) -> None:
        self.data_path = frame.get("state")
        self.data_path_at = frame.get("at")
        if self.data_path == "restored":
            self.refusal_active = False
        self._add_event(KIND_DATA_PATH, state=self.data_path, at=self.data_path_at)

    def note_record(self, record: dict[str, Any]) -> bool:
        """A poll's record for this session: whether the core can reach its worker (`live` or `unreachable`),
        which no frame of this attachment carries. A client that is attached is never `detached`, and an end
        reaches it as a close, so a poll that says either is older than the attach and changes nothing.
        Returns whether the state changed."""
        if not self.session or record.get("state") not in ("live", "unreachable"):
            return False
        fields = {key: record[key] for key in ("state", "unreachable_reason") if key in record}
        if all(self.session.get(key) == value for key, value in fields.items()) and (
                record["state"] != "live" or "unreachable_reason" not in self.session):
            return False
        updated = {**self.session, **fields}
        if updated.get("state") == "live":
            updated.pop("unreachable_reason", None)
        self.session = updated
        return True

    def quiet_answered(self) -> None:
        """This client answered the quiet prompt: it is over for everyone asked (quiet-prompt step 8)."""
        self.quiet_prompt = None

    def clear_refusal(self) -> None:
        self.refusal_active = False

    def note_size_request(self, cols: int, rows: int) -> None:
        """This client sent a size request; the next `size` frame answers it."""
        self.size_requests_pending += 1
        self.last_size_request = (cols, rows)

    def window_changed(self) -> None:
        """The outer window changed: a clamped answer is no longer final, so a request may be made
        again (terminal-size §3)."""
        self.size_final = False

    def note_output(self, data: bytes) -> None:
        self.output_bytes_applied += len(data)
        self.last_output_at = self._clock()
        self._add_event(KIND_OUTPUT, bytes=len(data))

    def apply_close(self, code: int | None, reason: str = "") -> CloseOutcome:
        self.closed = True
        self.close_code = code
        self.close = close_outcome(code)
        self.reattach = None
        self._add_event(KIND_CLOSE, code=code, reason=reason, show=self.close.show)
        return self.close

    def _learn_user(self) -> None:
        if self.user is not None:
            return
        for client in self.clients:
            if client.get("client_id") == self.client_id:
                self.user = client.get("user")
                return
        if self.holder is not None and self.holder.get("client_id") == self.client_id:
            self.user = self.holder.get("user")


_HANDLERS: dict[str, Callable[[AttachmentState, dict[str, Any]], None]] = {
    "attached": AttachmentState.apply_attached,
    "prefix": AttachmentState.apply_prefix,
    "clients": AttachmentState.apply_clients,
    "token": AttachmentState.apply_token,
    "size": AttachmentState.apply_size,
    "notice": AttachmentState.apply_notice,
    "stale": AttachmentState.apply_stale,
    "stale_prompt": AttachmentState.apply_stale_prompt,
    "quiet_prompt": AttachmentState.apply_quiet_prompt,
    "input_refused": AttachmentState.apply_input_refused,
    "request_refused": AttachmentState.apply_request_refused,
    "data_path": AttachmentState.apply_data_path,
}


# -- the pump --

# The sink runs on the attachment's feed thread (DT20): it is handed the role ("dump" or "output") and
# the run's bytes, and returns the reply bytes the engine produced (DT3), which the processor delivers
# on the loop before it applies the next frame. A sink that produces no replies returns None.
OutputSink = Callable[[str, bytes], "list[bytes] | None"]
StateHook = Callable[[AttachmentState, Any], None]
RunHook = Callable[[AttachmentState, str, bytes, "list[bytes]"], Awaitable[None]]

# One item the reader appends and the processor consumes, in order: a frame dict, a bytes chunk, or
# the sentinel that ends the processor.
_STOP = object()


@dataclass
class PumpHooks:
    """What a caller (the app, or a test) observes. All optional."""
    on_event: StateHook | None = None       # after every applied frame, close or output
    on_reattach: StateHook | None = None    # when a reattach attempt starts
    on_attached: StateHook | None = None    # after a (re)attach has applied its `attached` frame
    on_backoff: StateHook | None = None     # when a wait before an attempt starts (`state.reattach`)
    on_login_needed: Callable[[], None] | None = None  # a 401 during a reattach
    # After a run has been fed (DT20), on the loop and before the next frame is applied: the caller
    # delivers the run's replies and marks a background chip unseen. Awaited, so a reply is sent
    # against the state as of the run, before a later `token` frame can change it (DT4 b).
    on_run_done: RunHook | None = None


class AttachmentPump:
    """Owns one `AsyncAttachment`, reads it, applies frames, and reattaches per DT13/D26.

    DT20: a reader coroutine reads the socket and queues items; a processor task consumes them in
    order, applying frames on the loop and feeding runs of bytes on a one-thread executor. The reader
    never feeds, so it answers a ping the moment the parser reaches it, even behind unfed output.
    """

    def __init__(self, client: Any, session_id: str, *, sink: OutputSink,
                 state: AttachmentState | None = None, hooks: PumpHooks | None = None,
                 backoff_ms: tuple[int, ...] = BACKOFF_MS, steady_ms: int = STEADY_MS,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 cols: int | None = None, rows: int | None = None, auto: bool = True) -> None:
        self._client = client
        self.session_id = session_id
        self._sink = sink
        self.state = state if state is not None else AttachmentState(session_id=session_id)
        self._hooks = hooks or PumpHooks()
        self._backoff_ms = backoff_ms
        self._steady_ms = steady_ms
        self._sleep = sleep
        self._cols = cols
        self._rows = rows
        # `reattach.auto` off, or the operator pressing Esc: a loss that would be retried is final.
        self._auto = auto
        self._halted = False
        self._waiting: asyncio.Future[None] | None = None
        self._att: AsyncAttachment | None = None
        self._expect_dump = False
        self._stop = False
        self._task: asyncio.Task[None] | None = None
        # DT20's reader/processor split. Both live on the loop; the executor is the one feed thread.
        self._queue: deque[Any] = deque()
        self._wake = asyncio.Event()
        self._room = asyncio.Event()
        self._room.set()
        self._drained = asyncio.Event()
        self._drained.set()
        self._queued_output = 0
        self._feeding = False
        self._process_task: asyncio.Task[None] | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tsb-feed")
        self.attach_attempts = 0
        self.reattach_attempts = 0
        # 4001 retries exactly once (DT13: "once at once with a new ticket, then never"): a fresh
        # ticket is minted once, and a second 4001 is final.
        self._ticket_retry_used = False

    @property
    def attachment(self) -> AsyncAttachment | None:
        return self._att

    # -- lifecycle --

    async def start(self, attachment: AsyncAttachment | None = None) -> None:
        """Attach and begin the reader and processor. Raises `AttachRefused`/`ApiError` for a
        first-attach refusal, exactly as `Client.async_attach` does."""
        if attachment is None:
            await self._attach()
        else:
            self.attach_attempts += 1
            self._apply_attached(attachment)
        self._process_task = asyncio.create_task(self._process_loop())
        self._task = asyncio.create_task(self._read_loop())

    async def wait_closed(self) -> None:
        if self._task is not None:
            await self._task
        if self._process_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._process_task

    async def stop(self) -> None:
        """Stop reading, end the processor after its current run, and shut the feed thread down before
        the canvas closes the backend. Safe to call twice."""
        self._stop = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(BaseException):  # stop is best-effort
                await self._task
            self._task = None
        if self._process_task is not None:
            self._queue.append(_STOP)
            self._wake.set()
            with contextlib.suppress(BaseException):
                await self._process_task
            self._process_task = None
        self._executor.shutdown(wait=True)
        if self._att is not None and not self._att.closed:
            with contextlib.suppress(Exception):
                await self._att.close()

    def shutdown_feed(self) -> None:
        """Shut the feed thread down now, synchronously (DT20), before the caller frees the engine: a
        feed slice that already started finishes under the backend lock, the processor is told to end,
        and no further slice starts. Called from the loop when a slot is released; `stop` also shuts
        it down, harmlessly."""
        self._stop = True
        self._queue.append(_STOP)
        self._wake.set()
        self._executor.shutdown(wait=True)

    async def _attach(self) -> None:
        self.attach_attempts += 1
        att = await self._client.async_attach(self.session_id, cols=self._cols, rows=self._rows)
        self._apply_attached(att)

    def _apply_attached(self, att: AsyncAttachment) -> None:
        self._att = att
        self._expect_dump = False
        # `async_attach` consumes the `attached` frame while opening, so recv_message never yields it:
        # the pump applies it here, or the state would never learn its client id, the holder or the size.
        self.state.apply_frame({"t": "attached", "v": 1, "client_id": att.client_id,
                                "session": att.session})
        if self._hooks.on_attached is not None:
            self._hooks.on_attached(self.state, self.attach_attempts)

    # -- the reader (DT17: the only reader) --

    async def _read_loop(self) -> None:
        while not self._stop:
            await self._wait_for_room()
            try:
                message = await self._att.recv_message()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a broken transport is a close with no code
                message = None
            if message is None:
                # Every byte that arrived is fed before the close shows, and a reattach starts from an
                # empty queue (DT20): wait for the processor to empty it, then apply the close.
                await self._drain()
                code = self._att.close_code
                reason = self._att.close_reason
                if await self._handle_close(code, reason):
                    continue
                break
            self.state.received_items += 1
            if isinstance(message, dict):
                if message.get("t") == "token":
                    self.state.received_keyboard_items += 1
                elif message.get("t") in ("stale", "stale_prompt"):
                    self.state.received_stale_items += 1
            # An attachment that delivered traffic was accepted: a later 4001 is a new loss.
            self._ticket_retry_used = False
            if isinstance(message, (bytes, bytearray)):
                data = bytes(message)
                self._queue.append(data)
                self._queued_output += len(data)
            else:
                self._queue.append(message)
            self._wake.set()
        # The session is over for this client: end the processor after whatever is queued.
        self._queue.append(_STOP)
        self._wake.set()

    async def _wait_for_room(self) -> None:
        """Stop reading while the queue holds more than `READ_AHEAD_BYTES` of unfed output, so TCP
        pushes back and the core's 8 MiB drain rule still applies (DT20)."""
        while not self._stop and self._queued_output >= READ_AHEAD_BYTES:
            self._room.clear()
            await self._room.wait()

    async def _drain(self) -> None:
        """Wait until the processor has taken everything the reader queued."""
        while (self._queue and self._queue[0] is not _STOP) or self._feeding:
            self._drained.clear()
            await self._drained.wait()

    # -- the processor (DT20) --

    async def _process_loop(self) -> None:
        while True:
            item = await self._next_item()
            if item is _STOP:
                return
            if isinstance(item, dict):
                self._deliver_frame(item)
                if self._hooks.on_event is not None:
                    self._hooks.on_event(self.state, item)
            else:
                await self._feed_item(item)
            if not self._queue and not self._feeding:
                self._drained.set()

    async def _next_item(self) -> Any:
        while not self._queue:
            self._wake.clear()
            await self._wake.wait()
        return self._queue.popleft()

    async def _feed_item(self, first: bytes) -> None:
        """Feed one run: a dump on its own, or consecutive output chunks coalesced into one run. The
        run is fed on the feed thread (`sink`), then the loop-side effects run before the next frame
        is applied: `note_output` (or `dump_seen`), then the caller's `on_run_done`, which delivers
        the run's replies.

        `dump_seen` is set here, after the run returned, not when the dump was picked up: like
        `note_output`, it means the bytes are in the grid (design note §3.2). A waiter that sees it
        then knows the dump is no longer in flight, so a later direct `canvas.feed` cannot land
        between the dump's slices while its replies are still muted, and the `clients` frame the core
        sends after the dump has been applied too.
        """
        item_count = 1
        if self._expect_dump:
            self._expect_dump = False
            role, data = "dump", first
        else:
            role = "output"
            run = bytearray(first)
            while self._queue and isinstance(self._queue[0], bytes):
                run += self._queue.popleft()
                item_count += 1
            data = bytes(run)
        self._queued_output = max(0, self._queued_output - len(data))
        self._room.set()
        self._feeding = True
        try:
            loop = asyncio.get_running_loop()
            replies = await loop.run_in_executor(self._executor, self._sink, role, data)
        finally:
            self._feeding = False
        self.state.applied_items += item_count
        replies = list(replies or [])
        if role == "dump":
            self.state.dump_seen = True
        else:
            self.state.note_output(data)
        if self._hooks.on_run_done is not None:
            await self._hooks.on_run_done(self.state, role, data, replies)
        if self._hooks.on_event is not None:
            self._hooks.on_event(self.state, data)

    def _deliver_frame(self, frame: dict[str, Any]) -> None:
        if frame.get("t") == "prefix":
            self._expect_dump = True
        self.state.apply_frame(frame)
        self.state.applied_items += 1

    # -- reattach (DT13, D26) --

    async def _handle_close(self, code: int | None, reason: str) -> bool:
        """Apply the close. Return True if the caller should keep reading (a reattach happened)."""
        outcome = self.state.apply_close(code, reason)
        if self._stop:
            return False
        if outcome.read_record:
            # The close alone does not say how the session stands: the record does (4004, a dead socket).
            state = await self._record_state()
            self.state.record_state = state
            if outcome.reattach != "never" and state is not None and state not in _LIVE_STATES:
                return False
        if outcome.reattach == "never":
            return False
        if outcome.reattach == "backoff" and not self._auto:
            self.state.reattach_stopped = True
            return False
        if outcome.reattach == "once":
            return await self._reattach_once()
        return await self._reattach_backoff()

    async def _reattach_once(self) -> bool:
        """4001 retries exactly once with a new ticket (session-attach CR2); then never. A second
        4001 for the same loss is final (DT13)."""
        if self._ticket_retry_used:
            self.state.closed = True
            return False
        self._ticket_retry_used = True
        self.reattach_attempts += 1
        try:
            await self._attach()
            return True
        except (AttachRefused, ApiError, OSError):
            self.state.closed = True
            return False

    async def _reattach_backoff(self) -> bool:
        steps = list(self._backoff_ms)
        step = 0
        while not self._stop:
            delay = steps[step] if step < len(steps) else self._steady_ms
            if step < len(steps):
                step += 1
            if not await self._wait_to_retry(step, delay):
                return False
            self.reattach_attempts += 1
            if self._hooks.on_reattach is not None:
                self._hooks.on_reattach(self.state, step)
            try:
                await self._attach()
                return True
            except AttachRefused as exc:
                if exc.code in (CLOSE_FORBIDDEN, CLOSE_SESSION_NOT_FOUND):
                    self.state.apply_close(exc.code, exc.reason)
                    return False
                if exc.code == CLOSE_TICKET_INVALID:
                    # A refused ticket retries once with a new one, then never (DT13).
                    if self._ticket_retry_used:
                        self.state.apply_close(exc.code, exc.reason)
                        return False
                    self._ticket_retry_used = True
                    continue
                if close_outcome(exc.code).reattach == "backoff":
                    continue  # the broker detached us again (4011): keep backing off
                # The session ended, or this client was removed, while it was away: that is the state
                # to show, and nothing is tried again (4010, 4012, 4014, 4000).
                self.state.apply_close(exc.code, exc.reason)
                return False
            except ApiError as exc:
                if exc.status == 401:
                    self.state.apply_close(CLOSE_FORBIDDEN, "401")
                    if self._hooks.on_login_needed is not None:
                        self._hooks.on_login_needed()
                    return False
                if exc.status in (403, 404):
                    code = CLOSE_FORBIDDEN if exc.status == 403 else CLOSE_SESSION_NOT_FOUND
                    self.state.apply_close(code, str(exc.status))
                    return False
                continue  # a 5xx or another ApiError: keep backing off
            except OSError:
                continue  # an HttpError/transport failure: keep backing off
        return False

    async def _wait_to_retry(self, attempt: int, delay_ms: int) -> bool:
        """Wait out one backoff step, telling the screen when the attempt is due. False when the pump
        was stopped, or the operator stopped the reattaching (`stop_reattach`)."""
        self.state.reattach = {"attempt": attempt, "delay": delay_ms / 1000.0,
                               "due": self.state._clock() + delay_ms / 1000.0,
                               "cause": self.state.close.show if self.state.close else None}
        if self._hooks.on_backoff is not None:
            self._hooks.on_backoff(self.state, attempt)
        self._waiting = asyncio.ensure_future(self._sleep(delay_ms / 1000.0))
        try:
            await self._waiting
        except asyncio.CancelledError:
            if not self._halted:
                raise  # the pump's own task was cancelled
            self.state.reattach = None
            self.state.reattach_stopped = True
            return False
        finally:
            self._waiting = None
        return not self._stop

    def stop_reattach(self) -> None:
        """The operator stopped the reattaching (Esc): no further attempt; the session lives on."""
        self._halted = True
        if self._waiting is not None:
            self._waiting.cancel()

    async def _record_state(self) -> str | None:
        try:
            record = await asyncio.to_thread(self._client.session, self.session_id)
        except (ApiError, OSError):
            return None
        return record.get("state")

    # -- sends (never read) --

    async def send(self, data: bytes) -> None:
        if self._att is None or self._att.closed:
            raise ConnectionClosed()
        await self._att.send(data)

    async def resize(self, cols: int, rows: int, timeout: float = 0) -> None:
        """Send a size request. `timeout=0` means never read (DT17): the answer arrives through the
        pump like every other frame."""
        if self._att is not None and not self._att.closed:
            # Counted before the send yields: the answer is read by this same loop and must find the request.
            self.state.note_size_request(cols, rows)
            try:
                await self._att.resize(cols, rows, timeout=timeout)
            except BaseException:
                self.state.size_requests_pending -= 1
                raise

    async def handoff(self, to: str | None) -> None:
        """Send a handoff and set `handoff_in_flight` (DT4 b), cleared by the next `token`."""
        if self._att is not None and not self._att.closed:
            # Set before the send yields: the token that answers it is read by this same loop, and
            # a flag set after the await could land after the token that should have cleared it.
            self.state.handoff_in_flight = True
            try:
                await self._att.handoff(to)
            except BaseException:
                self.state.handoff_in_flight = False
                raise
            self.state.log.add(KIND_HANDOFF_SENT, time.monotonic(), to=to)

    async def answer_stale(self, prompt_id: str, choice: str) -> None:
        if self._att is not None and not self._att.closed:
            await self._att.answer_stale(prompt_id, choice)

    async def answer_quiet_prompt(self, prompt_id: str, kill: bool) -> None:
        if self._att is not None and not self._att.closed:
            await self._att.answer_quiet_prompt(prompt_id, kill)
