"""The fleet model (design §2.3, DT15): session records from `GET /sessions`, the derived groups,
the rail's chips, and the shortest unique id prefix.

Why it is its own model (design §3.3): the rail, the sidebar and the full view all render the same
records, so the grouping rules live once, in a module with no Textual in it, and every surface
reads them from here.

Three rules this module owns:

  * **A stale reply never overwrites a newer one.** Two `GET /sessions` calls can be in flight at
    once; network latency has no ordering guarantee, so applying whichever reply lands last would
    let a slow, stale answer overwrite a list that already reflects a newer request (the lesson of
    `tui/tests/test_sessions_refresh.py`). Each request carries a generation; a reply older than
    the last applied one is discarded.
  * **The TUI filters nothing the core returns** (DT15). `mine│all` is a view over the same set;
    the core decides what a principal may see. `mine` keeps the sessions this principal owns or
    has open here; `all` keeps everything returned.
  * **A session open here is read from its own frames first.** The poll is up to `fleet_view.refresh_s`
    old; the attachment's `token`, `notice` and close frames are not. The views take a `live` record per
    open session and read it over the polled one (`live_record`), so a chip changes at once (design §2.3).

Consumer rules followed: a state it does not know is not attachable, and a session type it does
not know is opaque, never an error (session-list rule 7; common `session_state`, `session_type`).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .attachment import AttachmentState

from .targets import as_text

# The states a consumer may attach to. A state outside this set — including one a later tag adds,
# which this build does not know — is not attachable (session-list rule 7).
ATTACHABLE_STATES = frozenset({"live", "detached", "unreachable"})
ENDED_STATES = frozenset({"killed", "terminated"})
# The session types this build knows. An unknown one is opaque, never an error (V5).
KNOWN_TYPES = frozenset({"shell", "exec", "sftp", "netconf", "forward"})

# The sources a rail chip comes from, the marks its third cell can carry, and the reasons a session needs
# you (D41, D9): the names the `rail.chips`, `rail.chip_mark` and `rail.needs_you` knobs hold.
CHIP_SOURCES = ("open_here", "mine_held_by_others")
CHIP_MARKS = ("front", "unseen_output")
NEEDS_REASONS = ("question_for_me", "stale_and_i_type", "ending_window", "unreachable")

# D42's group order.
GROUP_NEEDS_YOU = "needs_you"
GROUP_OPEN_HERE = "open_here"
GROUP_MINE_HELD_BY_OTHERS = "mine_held_by_others"
GROUP_MINE_AND_JOINABLE = "mine_and_joinable"
GROUP_ORDER = (GROUP_NEEDS_YOU, GROUP_OPEN_HERE, GROUP_MINE_HELD_BY_OTHERS, GROUP_MINE_AND_JOINABLE)

# The rail's chip glyphs (design §2.3). Cell 2 is who types; cell 3 is the front/activity mark.
WHO_ME = "\u25cf"       # ●
WHO_OTHER = "\u25c6"    # ◆
WHO_NOBODY = "\u00b7"   # ·
WHO_NEEDS_YOU = "\u25b2"  # ▲
WHO_ASKS = "?"          # ?
WHO_UNREACHABLE = "\u25cc"  # ◌
WHO_ENDED = "\u2715"    # ✕
MARK_FRONT = "\u25c2"   # ◂
MARK_UNSEEN = "\u2022"  # •
MAX_CHIPS = 9


def is_attachable(state: str | None) -> bool:
    """An unknown state is not attachable (session-list rule 7)."""
    return state in ATTACHABLE_STATES


def is_known_type(session_type: str | None) -> bool:
    """An unknown type is opaque, never an error (V5)."""
    return session_type in KNOWN_TYPES


def shortest_prefix(session_id: str, all_ids: Iterable[str], min_len: int = 6) -> str:
    """The shortest prefix of `session_id`, at least `min_len` characters, that no other id in
    `all_ids` shares (D27). A hex id shows as `s.` plus this prefix in the UI."""
    others = [other for other in all_ids if other != session_id]
    length = min_len
    while length < len(session_id) and any(other.startswith(session_id[:length]) for other in others):
        length += 1
    return session_id[:length]


def display_id(session_id: str, all_ids: Iterable[str], min_len: int = 6) -> str:
    return "s." + shortest_prefix(session_id, all_ids, min_len)


def display_id_map(all_ids: Iterable[str], min_len: int = 6) -> dict[str, str]:
    """`display_id` of every id at once, in one sort instead of one scan of the fleet per id: in sorted order an id
    shares its longest prefixes with its two neighbours, so its shortest unique prefix is one character longer than the
    longer of those two overlaps (never shorter than `min_len`, never longer than the id). The same answer as
    `shortest_prefix` for each id, at the cost of a sort."""
    ids = sorted(set(all_ids))

    def overlap(a: str, b: str) -> int:
        n = 0
        for x, y in zip(a, b, strict=False):
            if x != y:
                break
            n += 1
        return n

    out: dict[str, str] = {}
    for i, sid in enumerate(ids):
        shared = max(overlap(sid, ids[i - 1]) if i else 0, overlap(sid, ids[i + 1]) if i + 1 < len(ids) else 0)
        out[sid] = "s." + sid[:min(len(sid), max(min_len, shared + 1))]
    return out


@dataclass(frozen=True)
class Chip:
    """One rail chip (D41): its number, the session, who types, and the front/activity mark."""
    number: int
    session_id: str
    who: str
    mark: str


@dataclass(frozen=True)
class FleetScan:
    """What one pass over the records gives the rail, the sidebar and the model build: the merged records, the
    needs-you list, the groups in D42's order, the rail's chip ids, the count of records on each target, and each
    record's two column fields (target, login). Every value is exactly what the separate scans give."""

    merged: Mapping[str, dict[str, Any]]
    needs_you: list[dict[str, Any]]
    groups: dict[str, list[dict[str, Any]]]
    chip_ids: list[str]
    by_target: dict[str, int]
    columns: dict[str, tuple[str, str]]


def who_types(record: dict[str, Any], me: str | None, *, needs_you: bool = False,
              asks_you: bool = False) -> str:
    """Cell 2's glyph, by D41's priority: ended, unreachable, asks, needs-you, then who types."""
    state = record.get("state")
    if state in ENDED_STATES:
        return WHO_ENDED
    if state == "unreachable":
        return WHO_UNREACHABLE
    if asks_you:
        return WHO_ASKS
    if needs_you:
        return WHO_NEEDS_YOU
    holder = record.get("control_holder")
    if holder is None:
        return WHO_NOBODY
    return WHO_ME if holder.get("user") == me else WHO_OTHER


def ended_word(state: AttachmentState) -> str | None:
    """The end state this attachment's close says the session is in (`killed`, `terminated`, or what the
    record read after a lost socket), or None while it is not over."""
    close = state.close
    if close is None:
        return None
    word = {"ended_killed": "killed", "ended_terminated": "terminated"}.get(close.show)
    if word is None and close.show in ("session_gone", "transport_lost"):
        word = state.record_state
    return word if word in ENDED_STATES else None


def live_record(state: AttachmentState, polled: dict[str, Any] | None,
                opened_from: dict[str, Any] | None = None) -> dict[str, Any]:
    """The record of a session open here: the last poll's (or the `attached` frame's), with what this
    client's own frames have said since read over it — who holds the keyboard, who is attached, the size, the
    warning window, an end and an unreachable worker (design §2.3: sessions open here update at once).
    `opened_from` is the record the session was opened from, which stands in until the first `attached`."""
    record = {**(opened_from or {}), **state.session, **(polled or {})}
    record["control_holder"] = state.holder
    if state.clients:
        record["clients"] = list(state.clients)
    if state.size_cols and state.size_rows:
        record["cols"], record["rows"] = state.size_cols, state.size_rows
    record["ending"], record["ends_at"], record["ends_by"] = state.ending, state.ends_at, state.ends_by
    if record["ends_at"] is None:
        record.pop("ends_at"), record.pop("ends_by")
    word = ended_word(state)
    if word is not None:
        record["state"] = word
    elif state.refusal_active and state.last_input_refused == "unreachable":
        record["state"] = "unreachable"
    return record


def _ending(record: dict[str, Any]) -> bool:
    return bool(record.get("ending", False))


def others_by_target(merged: Mapping[str, dict[str, Any]], me: str | None) -> dict[str, str]:
    """A target row's others word (ruled 2026-11-20), from the session list the TUI already reads: per target,
    the sessions on it in state live or detached whose owner is not the caller. One owner: `<name> on it`; two
    or more owners: `<n> others on it`. Targets with none are absent."""
    out: dict[str, set] = {}
    for record in merged.values():
        target = record.get("target_id")
        if (target is not None and record.get("state") in ("live", "detached")
                and record.get("owner") is not None and record.get("owner") != me):
            out.setdefault(target, set()).add(as_text(record.get("owner")))
    return {target: (f"{next(iter(owners))} on it" if len(owners) == 1 else f"{len(owners)} others on it")
            for target, owners in out.items()}


def session_rank(record: dict[str, Any], open_here: set[str], me: str | None) -> int:
    """The ruled session order (2026-11-20): open here, then yours live, then yours detached, then joinable
    (needs you is the full view's block over the same list). The sort is stable, so the core's order stands
    within a rank."""
    if record["id"] in open_here:
        return 0
    if record.get("owner") == me:
        if record.get("state") == "live":
            return 1
        if record.get("state") == "detached":
            return 2
    return 3


def _held_by_other(record: dict[str, Any], me: str | None) -> bool:
    holder = record.get("control_holder")
    return holder is not None and holder.get("user") != me


class Fleet:
    """The applied session records and the views derived from them. No I/O.

    A session open here is described twice: by the last `GET /sessions` (the poll) and by its own attachment's
    frames, which are fresher. The views take the attachment's side as `live`, a record per open session id,
    and read it over the polled one; a session open here that no poll lists yet still appears (design §2.3:
    "sessions open here update at once, from their own frames")."""

    def __init__(self, *, own_user: str | None = None, id_min_hex: int = 6,
                 filter_mode: str = "mine", needs_reasons: Iterable[str] = NEEDS_REASONS,
                 chip_sources: Iterable[str] = CHIP_SOURCES, chip_marks: Iterable[str] = CHIP_MARKS,
                 groups: Iterable[str] = GROUP_ORDER, needs_scope: str = "mine_or_open_here") -> None:
        if needs_scope != "mine_or_open_here":
            # `rail.needs_you_scope`: the sessions you own or have open here (D9) is the one scope built.
            raise ValueError(f"rail.needs_you_scope = {needs_scope!r}: only 'mine_or_open_here' is built")
        self.own_user = own_user
        self.id_min_hex = id_min_hex
        self.filter_mode = filter_mode  # "mine" | "all" (DT15)
        self.needs_reasons = frozenset(needs_reasons)
        self.chip_sources = tuple(chip_sources)
        self.chip_marks = frozenset(chip_marks)
        self.group_names = tuple(groups)
        self.records: dict[str, dict[str, Any]] = {}
        self._display_ids: dict[str, str] = {}
        self._display_ids_for: tuple[str, ...] | None = None
        self.generation = 0
        self.applied_generation = 0
        self.updated_at: float | None = None

    @classmethod
    def from_config(cls, config: Any, *, own_user: str | None = None) -> Fleet:
        """The fleet's start filter, id prefix, chip sources and marks, need-you reasons and groups come from
        the knobs (DT15, D27, D41, D9). `sidebar.groups` still loads and is ignored: the side panel has one section."""
        return cls(own_user=own_user, id_min_hex=config["sessions.id_min_hex"],
                   filter_mode=config["sidebar.start_filter"], needs_reasons=config["rail.needs_you"],
                   chip_sources=config["rail.chips"], chip_marks=config["rail.chip_mark"],
                   needs_scope=config["rail.needs_you_scope"])  # `sidebar.groups` is retired: loaded, then ignored

    def apply(self, generation: int, records: list[dict[str, Any]], *,
              now: float | None = None) -> bool:
        """Apply a reply only if it is newer than the last applied one. Returns whether it
        was applied (a stale or repeated reply returns False and changes nothing)."""
        if generation <= self.applied_generation:
            return False
        self.applied_generation = generation
        self.records = {record["id"]: record for record in records}
        self.updated_at = time.monotonic() if now is None else now
        return True

    def set_own_user(self, user: str | None) -> None:
        self.own_user = user

    def _merged(self, live: Mapping[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
        if not live:
            return self.records
        return {**self.records, **live}

    # -- visibility --

    def _is_mine_or_open(self, record: dict[str, Any], open_here: set[str]) -> bool:
        return record.get("owner") == self.own_user or record["id"] in open_here

    def visible(self, *, open_here: Iterable[str] = (),
                live: Mapping[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """The records the current filter shows. `mine` keeps what this principal owns or has open
        here; `all` keeps everything the core returned (DT15)."""
        records = list(self._merged(live).values())
        if self.filter_mode == "all":
            return records
        open_set = set(open_here)
        return [record for record in records if self._is_mine_or_open(record, open_set)]

    # -- derived views (D9, D42) --

    def scan(self, *, open_here: Iterable[str] = (), stale_with_me: Iterable[str] = (),
             question_for_me: Iterable[str] = (),
             live: Mapping[str, dict[str, Any]] | None = None) -> FleetScan:
        """The needs-you list, the groups, the rail's chip ids, the target counts and every record's column
        fields, from one pass over the merged records. `needs_you`, `groups`, `chips` and `chip_count` all read
        this, so a rebuild of a long fleet scans once, not five times."""
        open_list = list(dict.fromkeys(open_here))
        open_set = set(open_list)
        merged = self._merged(live)
        stale_set, ask_set = set(stale_with_me), set(question_for_me)
        reasons = self.needs_reasons
        me = self.own_user
        in_view = self.filter_mode == "all"
        needing: list[dict[str, Any]] = []
        held_by_others: list[dict[str, Any]] = []
        joinable: list[dict[str, Any]] = []
        chip_held_ids: list[str] = []
        by_target: dict[str, int] = {}
        columns: dict[str, tuple[str, str]] = {}
        for record in merged.values():
            sid = record["id"]
            target = record.get("target_id", "")
            by_target[target] = by_target.get(target, 0) + 1
            holder = record.get("control_holder")
            columns[sid] = (target, "·" if holder is None else (
                "you" if holder.get("user") == me else str(holder.get("user"))))
            if record.get("state") in ENDED_STATES:
                continue
            mine = record.get("owner") == me
            is_open = sid in open_set
            mine_or_open = mine or is_open
            held = _held_by_other(record, me)
            if mine and held and not is_open:
                chip_held_ids.append(sid)
            if mine_or_open and (("ending_window" in reasons and _ending(record))
                                 or ("unreachable" in reasons and record.get("state") == "unreachable")
                                 or ("stale_and_i_type" in reasons and sid in stale_set)
                                 or ("question_for_me" in reasons and sid in ask_set)):
                needing.append(record)
            # The groups follow the filter (DT15): `all` lists what the core returned, `mine` what is mine or open
            # here. Needs you keeps its own scope (D9), so it is tested against `mine_or_open`, not the view.
            if (in_view or mine_or_open) and not is_open:
                if mine and held:
                    held_by_others.append(record)
                else:
                    joinable.append(record)
        full: dict[str, list[dict[str, Any]]] = {
            GROUP_NEEDS_YOU: needing,
            GROUP_OPEN_HERE: [merged[sid] for sid in open_list
                              if sid in merged and merged[sid].get("state") not in ENDED_STATES],
            GROUP_MINE_HELD_BY_OTHERS: held_by_others,
            GROUP_MINE_AND_JOINABLE: joinable,
        }
        chip_ids: list[str] = []
        for source in self.chip_sources:
            if source == "open_here":
                chip_ids.extend(sid for sid in open_list if sid in merged)
            else:  # mine_held_by_others
                chip_ids.extend(chip_held_ids)
        return FleetScan(merged=merged, needs_you=needing,
                         groups={name: full[name] for name in GROUP_ORDER if name in self.group_names},
                         chip_ids=list(dict.fromkeys(chip_ids)), by_target=by_target, columns=columns)

    def needs_you(self, *, open_here: Iterable[str] = (), stale_with_me: Iterable[str] = (),
                  question_for_me: Iterable[str] = (),
                  live: Mapping[str, dict[str, Any]] | None = None,
                  scan: FleetScan | None = None) -> list[dict[str, Any]]:
        """D9: sessions I own or have open here that are in a warning window, stale with me typing,
        asking me a question, or unreachable (the reasons `rail.needs_you` names). The record carries the
        warning window and the unreachable state; stale and question come from the attachment state.
        `scan` is this pass over the records, when the caller has already run it."""
        return (scan if scan is not None else self.scan(open_here=open_here, stale_with_me=stale_with_me,
                                                        question_for_me=question_for_me, live=live)).needs_you

    def groups(self, *, open_here: Iterable[str] = (), stale_with_me: Iterable[str] = (),
               question_for_me: Iterable[str] = (),
               live: Mapping[str, dict[str, Any]] | None = None,
               scan: FleetScan | None = None) -> dict[str, list[dict[str, Any]]]:
        """The sidebar's groups in D42's order (the ones `sidebar.groups` names), over the visible records.

        Needs you is a highlight over the others, not a place a session moves to: a session open here that
        needs you is in both Needs you and Open here (mockups S1, S3). Open here keeps the order the sessions
        were opened in, which is their number on the rail. A session that is neither open here nor yours with
        another login typing is in Yours and joinable, a needing one included. `scan` is this pass over the
        records, when the caller has already run it."""
        return (scan if scan is not None else self.scan(open_here=open_here, stale_with_me=stale_with_me,
                                                        question_for_me=question_for_me, live=live)).groups

    def chip_count(self, *, open_here: Iterable[str] = (),
                   live: Mapping[str, dict[str, Any]] | None = None,
                   scan: FleetScan | None = None) -> int:
        """How many chips there would be without the cap of nine; more than nine end in `…` (D41)."""
        return len((scan if scan is not None else self.scan(open_here=open_here, live=live)).chip_ids)

    def chips(self, *, open_here: Iterable[str] = (), front: str | None = None,
              unseen: Iterable[str] = (), question_for_me: Iterable[str] = (),
              stale_with_me: Iterable[str] = (),
              live: Mapping[str, dict[str, Any]] | None = None,
              scan: FleetScan | None = None) -> list[Chip]:
        """D41: one chip per session open here (in the order they were opened) or owned by me and typed in by
        another login, numbered from 1 and at most nine. The sources and the marks are the knobs'. `scan` is
        this pass over the records, when the caller has already run it."""
        unseen_set = set(unseen)
        s = scan if scan is not None else self.scan(open_here=open_here, stale_with_me=stale_with_me,
                                                    question_for_me=question_for_me, live=live)
        merged = s.merged
        needing = {r["id"] for r in s.needs_you}
        ask_set = set(question_for_me)
        chips: list[Chip] = []
        for number, sid in enumerate(s.chip_ids[:MAX_CHIPS], start=1):
            if "front" in self.chip_marks and sid == front:
                mark = MARK_FRONT
            elif "unseen_output" in self.chip_marks and sid in unseen_set:
                mark = MARK_UNSEEN
            else:
                mark = " "
            chips.append(Chip(number=number, session_id=sid,
                              who=who_types(merged[sid], self.own_user, needs_you=sid in needing,
                                            asks_you=sid in ask_set),
                              mark=mark))
        return chips

    def display_ids(self, live: Mapping[str, dict[str, Any]] | None = None) -> dict[str, str]:
        """The shortest unique display prefix of every record (D27), built once for a given set of ids: every row of
        the sidebar and the full view reads this map, so a poll of N sessions costs one build and not one scan of the
        fleet per row."""
        ids = tuple(self._merged(live))
        if self._display_ids_for != ids:
            self._display_ids, self._display_ids_for = display_id_map(ids, self.id_min_hex), ids
        return self._display_ids


class FleetPoller:
    """`GET /sessions` in a worker thread every `refresh_s`, while a consumer asks for it.

    Results go to `on_reply(generation, records)`, which the app wraps in `call_from_thread`; the
    test wraps it in a recorder. A poll only reads: it calls `client.sessions()` and nothing else,
    so it resets no timer and writes nothing (session-list step 3: "a read is not a byte").

    `interval_s` 0 is the knob file's "manual": one poll when the poller starts and then only the ones
    `refresh_async` asks for (no push stream exists yet, OPEN-3). A failed poll goes to `on_error` (the
    app turns a 401 into the login screen) and is retried at the next interval.
    """

    def __init__(self, client: Any, *, interval_s: float, on_reply: Callable[[int, list[dict]], None],
                 on_error: Callable[[Exception], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._client = client
        self._interval = interval_s
        self._on_reply = on_reply
        self._on_error = on_error
        self._sleep = sleep
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._generation = 0
        self._lock = threading.Lock()  # a refresh and the timer both bump the generation
        self.calls = 0
        self.last_error: Exception | None = None  # the latest failed poll, for the status row

    @property
    def running(self) -> bool:
        return (not self._stop.is_set() and self._thread is not None
                and self._thread.is_alive())

    @classmethod
    def from_config(cls, client: Any, config: Any, *,
                    on_reply: Callable[[int, list[dict]], None],
                    on_error: Callable[[Exception], None] | None = None) -> FleetPoller:
        """Poll at `fleet_view.refresh_s` (D10, DT19); 0 is manual."""
        return cls(client, interval_s=config["fleet_view.refresh_s"], on_reply=on_reply, on_error=on_error)

    def poll_once(self) -> int:
        """Issue one request and hand its reply to `on_reply` under a fresh generation."""
        with self._lock:
            self._generation += 1
            generation = self._generation
            self.calls += 1
        records = self._client.sessions()
        self._on_reply(generation, records)
        return generation

    def refresh_async(self) -> None:
        """One more poll now, on a thread of its own, so a slow broker never holds the caller (a keystroke)."""
        if self._stop.is_set():
            return
        threading.Thread(target=self._poll_safely, daemon=True).start()

    def _poll_safely(self) -> None:
        try:
            self.poll_once()
        except Exception as exc:  # noqa: BLE001 — a failed poll is retried at the next interval
            self.last_error = exc
            if self._on_error is not None and not self._stop.is_set():
                self._on_error(exc)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._poll_safely()
            if self._interval <= 0:
                return  # manual: the first poll was the only one the timer makes
            self._stop.wait(self._interval)

    def stop(self) -> None:
        """Ask the poller to end and return at once: the thread is a daemon and ends after the step it is in.
        Nothing on the event loop waits for it, since its reply is itself waiting for that loop."""
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the worker to end: for a caller with no running loop (a test, a shutdown path). Never call
        it on the event loop: a reply in flight may be waiting for that same loop."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
