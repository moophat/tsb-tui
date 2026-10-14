"""The fleet model: the groups, need-you, the chips, and the poller's stale-reply rule (DT15, D9,
D41, D42, D27).

Why these matter (Rule 9):

  * **A stale reply never overwrites a newer one.** Two `GET /sessions` calls can be in flight at
    once; network latency has no ordering guarantee, so applying whichever reply lands last would
    let a slow, stale answer overwrite a list that already reflects a newer request (the lesson of
    `tui/tests/test_sessions_refresh.py`). The test gates the order with an event, never a sleep.
  * **A poll writes nothing** (session-list step 3): a read is not a byte of any session, so it
    resets no timer. A poll that caused a write would move a session's countdown.
  * A `state` it does not know is not attachable, and a `session_type` it does not know is opaque,
    never an error (session-list rule 7; V5).

Records come from `C1/session-record-shape-v0.3`'s `valid: true` documents (DT6), never written by
hand.
"""

from __future__ import annotations

import threading
from typing import Any

from fleet_support import derived as _derived
from fleet_support import fixture_records as _records
from tsb_tui.model.fleet import (
    Chip,
    Fleet,
    FleetPoller,
    is_attachable,
    is_known_type,
    shortest_prefix,
)


def _fleet(user: str = "fixture-user", filter_mode: str = "mine") -> Fleet:
    fleet = Fleet(own_user=user, id_min_hex=6, filter_mode=filter_mode)
    assert fleet.apply(1, _records())
    return fleet


def test_groups_split_the_visible_records_into_the_four_groups() -> None:
    """D42: Needs you, Open here, Yours someone else types, Yours and joinable, in that order.

    Why: the sidebar's grouping is how the operator finds the session that needs attention; a wrong
    group hides it behind a hundred others.
    """
    fleet = _fleet()
    groups = fleet.groups(open_here={"fixture-session-b"})
    assert list(groups) == ["needs_you", "open_here", "mine_held_by_others", "mine_and_joinable"]
    open_here = groups["open_here"]
    assert [r["id"] for r in open_here] == ["fixture-session-b"]


def test_needs_you_includes_unreachable_and_ending_records() -> None:
    """D9: sessions I own or have open here that are in a warning window, stale with me typing,
    asking me a question, or unreachable.

    Why: `unreachable` refuses input and ends nothing (session model); `ending` is inside a warning
    window (session-countdown). Both need the operator's eye.
    """
    fleet = _fleet()
    # No fixture carries an unreachable or an ending record, so both are built from a fixture
    # record and validated with the contract's validator (DT6).
    records = _records()
    unreachable = _derived(records[0], id="fixture-session-u", state="unreachable",
                           unreachable_reason="protocol")
    ending = _derived(records[0], id="fixture-session-e", ending=True,
                      ends_at="2026-10-07T12:00:00Z", ends_by="link_timeout")
    fleet.apply(2, [*records, unreachable, ending])
    ids = {r["id"] for r in fleet.needs_you()}
    assert {"fixture-session-u", "fixture-session-e"} <= ids
    assert "fixture-session-b" not in ids  # a detached session nobody needs is not a need


def test_needs_you_takes_stale_and_question_sessions_the_attachments_report() -> None:
    """D9: a session stale with me typing, or asking me a question, is a need although its record
    shows nothing; the attachment state names them. Neither is a need for a session that is not
    mine or open here."""
    fleet = _fleet()
    ids = {r["id"] for r in fleet.needs_you(stale_with_me={"fixture-session-b"},
                                            question_for_me={"fixture-session-c"})}
    assert ids == {"fixture-session-b", "fixture-session-c"}


def test_needs_you_excludes_other_peoples_sessions() -> None:
    """D9: only sessions you own or have open here. A session you neither own nor have open does
    not need you."""
    fleet = _fleet(user="someone-else")
    assert fleet.needs_you() == []


def test_chips_take_open_here_then_mine_held_by_others() -> None:
    """D41: one chip per session open here or owned by you and typed in by another login, numbered
    in that order.

    Why: the rail's chips are the operator's switch; a chip for a session they do not own and do not
    have open would steal a number from one they do.
    """
    fleet = _fleet()
    records = _records()
    held = dict(records[0])
    held["id"] = "held-by-peer"
    held["control_holder"] = {"client_id": "client-peer", "user": "peer"}
    held["owner"] = "fixture-user"
    records.append(held)
    fleet.apply(2, records)

    chips = fleet.chips(open_here={"fixture-session-b"}, front="held-by-peer")
    assert len(chips) >= 1
    assert all(isinstance(c, Chip) for c in chips)
    # The front session is marked, and numbers are 1-based and dense.
    assert [c.number for c in chips] == list(range(1, len(chips) + 1))
    assert any(c.mark == "\u25c2" for c in chips)  # ◂ front


def test_a_chip_shows_who_types() -> None:
    """D41's cell 2: ● me, ◆ another login, · nobody, based on `control_holder.user`.

    Why: "who types is never ambiguous" (design §2.2 principle 4).
    """
    fleet = _fleet()
    records = _records()
    # records[0] has control_holder user fixture-user (me) -> ●; add one held by another.
    held = dict(records[0])
    held["id"] = "held"
    held["control_holder"] = {"client_id": "c", "user": "other"}
    records.append(held)
    # escrow: holder null -> ·
    escrow = dict(records[1])
    escrow["id"] = "escrow"
    escrow["control_holder"] = None
    records.append(escrow)
    fleet.apply(2, records)
    from tsb_tui.model.fleet import who_types

    assert who_types(records[0], "fixture-user") == "\u25cf"  # ●
    assert who_types(held, "fixture-user") == "\u25c6"        # ◆
    assert who_types(escrow, "fixture-user") == "\u00b7"      # ·


def test_an_unknown_state_is_not_attachable() -> None:
    """session-list rule 7: a `state` this build does not know is not attachable (V5).

    Why: a session added by a later tag must not be offered for attach, because this build cannot
    render it; failing closed is safe, failing open is a crash on attach.
    """
    assert is_attachable("live") is True
    assert is_attachable("detached") is True
    assert is_attachable("unreachable") is True
    assert is_attachable("killed") is False
    assert is_attachable("terminated") is False
    assert is_attachable("brand-new-state") is False
    assert is_attachable(None) is False


def test_an_unknown_session_type_is_opaque_not_an_error() -> None:
    """session-list rule 7 / V5: a `session_type` this build does not name is opaque, never an
    error; `is_known_type` only distinguishes the names this build has text for.

    Why: a new session type from a later tag must render as an unknown type, not crash the list.
    """
    assert is_known_type("shell") is True
    assert is_known_type("exec") is True
    assert is_known_type("netconf") is True
    assert is_known_type("brand-new-type") is False
    assert is_known_type(None) is False


def test_the_shortest_unique_prefix_is_used_and_shared_ids_do_not_collide() -> None:
    """D27: the shortest prefix of a session id that no other id shares, at least `min_len`.

    Why: the operator reads `s.7f3a2c`, not the whole id; a prefix that collides with another
    session would switch the wrong session when typed.
    """
    ids = ["s-aaaaaa-0001", "s-aaaaaa-0002", "s-bbbbbb-0001"]
    prefix = shortest_prefix("s-aaaaaa-0001", ids, min_len=6)
    assert prefix == "s-aaaaaa-0001"[:len(prefix)]
    assert not any(o.startswith(prefix) for o in ids if o != "s-aaaaaa-0001")
    # No collision with the other shared-prefix id.
    assert shortest_prefix("s-aaaaaa-0001", ids, min_len=6) != \
        shortest_prefix("s-aaaaaa-0002", ids, min_len=6)


def test_display_id_prefixes_every_record() -> None:
    """D27: every record gets a `s.`-prefixed display id, unique across the set."""
    fleet = _fleet()
    displays = fleet.display_ids()
    assert len(set(displays.values())) == len(displays)
    assert all(v.startswith("s.") for v in displays.values())


# -- the poller's stale-reply rule --


class _Recorder:
    """Records each reply's generation and how many records it carried, so the test can wait on
    the newer reply having been applied before releasing the older one — a gate, never a sleep."""

    def __init__(self) -> None:
        self.applied: list[tuple[int, int]] = []
        self.release = threading.Event()
        self.gate_started = threading.Event()

    def __call__(self, generation: int, records: list[dict]) -> None:
        self.applied.append((generation, len(records)))


class _Client:
    """A client whose first `GET /sessions` is held on a gate and its second answers at once."""

    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder
        self.calls = 0

    def sessions(self) -> list[dict]:
        self.calls += 1
        if self.calls == 1:
            self.recorder.gate_started.set()
            if not self.recorder.release.wait(timeout=10):
                raise RuntimeError("gate never released")
            return _records()[:1]  # the older reply: one session
        return _records()  # the newer reply: every session


def test_a_stale_reply_never_overwrites_a_newer_one() -> None:
    """The generation rule (plan § Test conventions; the lesson of `test_sessions_refresh.py`).

    Why: a slower, earlier reply landing after a faster, later one must never win. The test forces
    the order with events: it waits until the first request is the one held, issues the second, then
    releases the first — no wall clock decides the winner.
    """
    recorder = _Recorder()
    client = _Client(recorder)
    fleet = Fleet(own_user="fixture-user")

    def on_reply(generation: int, records: list[dict]) -> None:
        if fleet.apply(generation, records):
            recorder.applied.append((generation, len(records)))

    poller = FleetPoller(client, interval_s=60.0, on_reply=on_reply)
    poller.start()
    try:
        # Generation 1 is the held request; wait until it is the one held.
        assert recorder.gate_started.wait(timeout=5.0)
        # Generation 2 answers immediately and applies first.
        poller.poll_once()
        # Generation 1's older reply is released; it must be discarded.
        recorder.release.set()
        poller._stop.wait(timeout=5.0)
    finally:
        poller.stop()

    # The newer reply (2) applied first; the older (1) was discarded.
    assert recorder.applied and recorder.applied[0][0] == 2
    # The stale reply never overwrote: the record count is the newer one's.
    assert fleet.applied_generation == 2
    assert len(fleet.records) == len(_records())


def test_a_poll_writes_nothing() -> None:
    """session-list step 3: a read is not a byte of any session, so it resets no timer.

    Why: a poll that wrote to the session would move `activity` or `client_touch` and hold off a
    timer the session should have fired on. The test proves `poll_once` only reads (calls
    `sessions()` and never any mutating method).
    """
    recorder = _Recorder()

    class _ReadOnly:
        def __init__(self) -> None:
            self.read_calls = 0
            self.write_calls = 0

        def sessions(self) -> list[dict]:
            self.read_calls += 1
            return _records()

        def close(self, *_: Any) -> None:
            self.write_calls += 1

        def create_session(self, *_: Any) -> None:
            self.write_calls += 1

    client = _ReadOnly()
    poller = FleetPoller(client, interval_s=60.0, on_reply=recorder)
    poller.poll_once()
    assert client.read_calls == 1
    assert client.write_calls == 0


# -- the rail's chips and the sidebar's groups, as phase 7 reads them (design §2.3, D9, D41, D42) --

ME, PEER = "fixture-user", "fixture-peer"


def _session(sid: str, **changes: object) -> dict:
    """A record for session `sid` owned by me, built from a fixture record and validated (DT6)."""
    return _derived(_records()[1], id=sid, **changes)


def _live_holder(user: str) -> dict:
    return {"client_id": f"client-{user}", "user": user}


def test_needs_you_is_a_highlight_over_open_here_and_not_a_move() -> None:
    """Mockups S1 and S3: `sw-agg-02` is in Needs you and in Open here (`4▲`), because Open here lists every
    session open here, numbered as on the rail, and Needs you says which of the sessions that matter need you.
    A group that took the session out of the other would leave the operator looking for chip 4 in Open here."""
    fleet = Fleet(own_user=ME)
    ending = _session("s-ending", state="live", control_holder=_live_holder(PEER), ending=True,
                      ends_at="2026-10-07T12:00:00Z", ends_by="link_timeout")
    fleet.apply(1, [ending, _session("s-quiet")])
    groups = fleet.groups(open_here=["s-ending"])
    assert [r["id"] for r in groups["needs_you"]] == ["s-ending"]
    assert [r["id"] for r in groups["open_here"]] == ["s-ending"]
    assert [r["id"] for r in groups["mine_and_joinable"]] == ["s-quiet"]


def test_a_needing_session_that_is_not_open_here_is_still_listed_in_the_group_that_holds_it() -> None:
    """Needs you says what needs you; it does not remove a session from where it is listed. An ending session
    nobody has open is yours and joinable too, so it is still there to open when the operator reads Needs you."""
    fleet = Fleet(own_user=ME)
    ending = _session("s-ending", state="detached", ending=True, ends_at="2026-10-07T12:00:00Z",
                      ends_by="idle")
    fleet.apply(1, [ending])
    groups = fleet.groups()
    assert [r["id"] for r in groups["needs_you"]] == ["s-ending"]
    assert [r["id"] for r in groups["mine_and_joinable"]] == ["s-ending"]


def test_open_here_keeps_the_order_the_sessions_were_opened_in() -> None:
    """D41: chips are numbered in the order the sessions were opened (`^\\ 1`-`^\\ 9` go by it), not in the order the
    broker lists them, or a number would move under the operator's finger when the poll reorders."""
    fleet = Fleet(own_user=ME)
    fleet.apply(1, [_session("s-a"), _session("s-b"), _session("s-c")])
    groups = fleet.groups(open_here=["s-c", "s-a"])
    assert [r["id"] for r in groups["open_here"]] == ["s-c", "s-a"]
    assert [(c.number, c.session_id) for c in fleet.chips(open_here=["s-c", "s-a"])] == [(1, "s-c"), (2, "s-a")]


def test_a_session_open_here_that_no_poll_lists_yet_still_appears_from_its_live_record() -> None:
    """Design §2.3: "sessions open here update at once, from their own frames". A session just opened (or created
    after the last poll) has no polled record, and its chip and row must not wait up to a poll for it."""
    fleet = Fleet(own_user=ME)
    fleet.apply(1, [])
    live = {"s-new": _session("s-new", state="live", control_holder=_live_holder(ME))}
    chips = fleet.chips(open_here=["s-new"], front="s-new", live=live)
    assert [(c.session_id, c.who, c.mark) for c in chips] == [("s-new", "●", "◂")]
    assert [r["id"] for r in fleet.groups(open_here=["s-new"], live=live)["open_here"]] == ["s-new"]


def test_chip_cell_two_says_needs_you_asks_unreachable_ended_and_who_types() -> None:
    """D41's cell 2, in its priority: ended, unreachable, asks you, needs you, then who types. An ending session
    reads `▲` (mockup T1's chip 4), not the glyph of whoever types in it, because the warning is the news."""
    fleet = Fleet(own_user=ME)
    records = [
        _session("s-me", state="live", control_holder=_live_holder(ME)),
        _session("s-other", state="live", control_holder=_live_holder(PEER)),
        _session("s-nobody"),
        _session("s-ending", state="live", control_holder=_live_holder(PEER), ending=True,
                 ends_at="2026-10-07T12:00:00Z", ends_by="idle"),
        _session("s-asks", state="live", control_holder=_live_holder(ME)),
        _session("s-unreachable", state="unreachable", unreachable_reason="protocol"),
    ]
    ended = {**_session("s-ended"), "state": "killed"}
    ended.pop("clients", None)
    fleet.apply(1, [*records, ended])
    ids = [r["id"] for r in records] + ["s-ended"]
    chips = fleet.chips(open_here=ids, question_for_me={"s-asks"})
    assert {c.session_id: c.who for c in chips} == {
        "s-me": "●", "s-other": "◆", "s-nobody": "·", "s-ending": "▲", "s-asks": "?",
        "s-unreachable": "◌", "s-ended": "✕"}


def test_more_than_nine_chips_are_capped_at_nine_and_counted() -> None:
    """D41: up to nine chips, then `…`, which opens the sidebar. The count past nine is what decides the `…`."""
    fleet = Fleet(own_user=ME)
    records = [_session(f"s-{n:02d}") for n in range(12)]
    fleet.apply(1, records)
    ids = [r["id"] for r in records]
    assert len(fleet.chips(open_here=ids)) == 9
    assert fleet.chip_count(open_here=ids) == 12
    assert fleet.chip_count(open_here=ids[:9]) == 9


def test_the_rail_knobs_choose_the_chip_sources_the_marks_and_the_needs_you_reasons() -> None:
    """`rail.chips`, `rail.chip_mark`, `rail.needs_you` (the knob file): each is honoured, so an operator who
    drops a source, a mark or a reason gets exactly that and no more."""
    peer_held = _session("s-held", state="live", control_holder=_live_holder(PEER))
    ending = _session("s-ending", state="live", control_holder=_live_holder(ME), ending=True,
                      ends_at="2026-10-07T12:00:00Z", ends_by="idle")

    only_open = Fleet(own_user=ME, chip_sources=["open_here"])
    only_open.apply(1, [peer_held, ending])
    assert [c.session_id for c in only_open.chips(open_here=["s-ending"])] == ["s-ending"]
    both = Fleet(own_user=ME)
    both.apply(1, [peer_held, ending])
    assert [c.session_id for c in both.chips(open_here=["s-ending"])] == ["s-ending", "s-held"]
    held_first = Fleet(own_user=ME, chip_sources=["mine_held_by_others", "open_here"])
    held_first.apply(1, [peer_held, ending])
    assert [c.session_id for c in held_first.chips(open_here=["s-ending"])] == ["s-held", "s-ending"]

    no_marks = Fleet(own_user=ME, chip_marks=[])
    no_marks.apply(1, [ending])
    assert [c.mark for c in no_marks.chips(open_here=["s-ending"], front="s-ending", unseen=["s-ending"])] == [" "]
    unseen_only = Fleet(own_user=ME, chip_marks=["unseen_output"])
    unseen_only.apply(1, [ending])
    assert [c.mark for c in unseen_only.chips(open_here=["s-ending"], front="s-ending", unseen=["s-ending"])] == ["•"]

    no_ending = Fleet(own_user=ME, needs_reasons=["unreachable"])
    no_ending.apply(1, [ending])
    assert no_ending.needs_you(open_here=["s-ending"]) == []
    assert [r["id"] for r in both.needs_you(open_here=["s-ending"])] == ["s-ending"]


def test_sidebar_groups_names_the_groups_shown_and_their_order() -> None:
    """`sidebar.groups` (the knob file): a group left out is not shown, and the order is D42's whatever order the
    knob lists them in, because the groups are a fixed reading order, not a menu."""
    fleet = Fleet(own_user=ME, groups=["mine_and_joinable", "open_here"])
    fleet.apply(1, [_session("s-a"), _session("s-b")])
    groups = fleet.groups(open_here=["s-a"])
    assert list(groups) == ["open_here", "mine_and_joinable"]


def test_the_needs_you_scope_other_than_mine_or_open_here_is_refused() -> None:
    """`rail.needs_you_scope`: the one scope built is the sessions you own or have open here (D9). Another is a
    value no code honours, so it fails instead of silently meaning the same."""
    import pytest

    with pytest.raises(ValueError, match="rail.needs_you_scope"):
        Fleet(own_user=ME, needs_scope="everyone")


def test_a_live_record_reads_this_clients_own_frames_over_the_polled_one() -> None:
    """The record of a session open here is the poll's with the attachment's frames read over it: who holds the
    keyboard, the warning window, an end, an unreachable worker. A poll up to five seconds old must never
    decide who types in a session whose `token` frame this client has already seen."""
    from model_frames import state_of, token_frame
    from tsb_tui.model.fleet import live_record

    state = state_of(me=("client-me", ME), owner=ME, holder=("client-me", ME),
                     clients=(("client-me", ME, True), ("client-peer", PEER, True)))
    polled = _session("s-1", state="live", control_holder=_live_holder(ME))
    state.apply_frame(token_frame(("client-peer", PEER), "handoff"))
    record = live_record(state, polled)
    assert record["control_holder"] == {"client_id": "client-peer", "user": PEER}
    assert [c["client_id"] for c in record["clients"]] == ["client-me", "client-peer"]

    state.refusal_active, state.last_input_refused = True, "unreachable"
    assert live_record(state, polled)["state"] == "unreachable"
    state.refusal_active = False
    state.apply_close(4010)
    assert live_record(state, polled)["state"] == "killed"


def test_a_poll_tells_an_attached_state_that_its_worker_is_unreachable_and_that_it_is_back() -> None:
    """Phase 6 left it: no frame of an attachment says the core cannot reach the worker, only the poll's record
    does. An attached client is never `detached` (an older poll must not say so), and `live` clears the reason."""
    from model_frames import state_of

    state = state_of(me=("client-me", ME), owner=ME, holder=("client-me", ME), clients=(("client-me", ME, True),))
    assert state.session["state"] == "live"
    assert not state.note_record({"state": "detached"})
    assert state.session["state"] == "live", "a poll older than the attach never reads as detached"
    assert state.note_record({"state": "unreachable", "unreachable_reason": "protocol"})
    assert state.session["state"] == "unreachable" and state.session["unreachable_reason"] == "protocol"
    assert not state.note_record({"state": "unreachable", "unreachable_reason": "protocol"})
    assert state.note_record({"state": "live"})
    assert state.session["state"] == "live" and "unreachable_reason" not in state.session


# -- the poller beyond the stale-reply rule --


def _wait(predicate, what: str, seconds: float = 5.0) -> None:
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def test_the_poller_polls_every_interval_and_a_manual_poller_polls_once_then_only_when_asked() -> None:
    """`fleet_view.refresh_s` (D10): every N seconds while shown; 0 is the knob file's "manual" — one poll, then
    only the ones asked for. A zero interval that looped would hammer the broker with no pause at all."""
    answered: list[int] = []

    class _Client:
        def sessions(self) -> list[dict]:
            return []

    timed = FleetPoller(_Client(), interval_s=0.02, on_reply=lambda g, r: answered.append(g))
    timed.start()
    try:
        _wait(lambda: len(answered) >= 3, "three timed polls")
    finally:
        timed.stop()

    manual_answers: list[int] = []
    manual = FleetPoller(_Client(), interval_s=0, on_reply=lambda g, r: manual_answers.append(g))
    manual.start()
    try:
        _wait(lambda: len(manual_answers) == 1, "the one poll a manual poller makes at start")
        manual._thread.join(timeout=2)
        assert manual_answers == [1], "a manual poller does not poll again by itself"
        manual.refresh_async()
        _wait(lambda: len(manual_answers) == 2, "the poll asked for")
    finally:
        manual.stop()


def test_a_failed_poll_goes_to_on_error_and_is_retried() -> None:
    """A poll that raised is not silent: `on_error` hears it (the app turns a 401 into the login screen), and the
    next interval tries again, so a broker that was briefly away does not leave the sidebar frozen."""
    errors: list[Exception] = []
    answered: list[int] = []
    calls = [0]

    class _Flaky:
        def sessions(self) -> list[dict]:
            calls[0] += 1
            if calls[0] == 1:
                raise OSError("the broker was away")
            return []

    poller = FleetPoller(_Flaky(), interval_s=0.02, on_reply=lambda g, r: answered.append(g),
                         on_error=errors.append)
    poller.start()
    try:
        _wait(lambda: answered, "the retry to be answered")
    finally:
        poller.stop()
    assert len(errors) == 1 and isinstance(errors[0], OSError)


def test_display_id_map_gives_every_id_the_prefix_display_id_gives_it() -> None:
    """D27. The map is built in one sort and must agree with the one-id-at-a-time rule for every id, including ids that
    share long prefixes, an id that is a prefix of another, a duplicate, and the six-character floor."""
    from tsb_tui.model.fleet import display_id, display_id_map

    ids = ["sess-1", "sess-10", "sess-11", "sess-110", "abcdef123456", "abcdef123999", "abcdef", "zz", "sess-1"]
    for min_len in (1, 3, 6):
        want = {sid: display_id(sid, ids, min_len) for sid in ids}
        assert display_id_map(ids, min_len) == want, min_len


def test_a_poll_of_n_sessions_builds_the_display_ids_once_and_never_rescans_the_fleet_per_row(
        serve, monkeypatch, wait_until) -> None:
    """Design §3.4 test 6 (the poll never blocks typing). A row's display id was found by scanning every id in the fleet,
    for every row of the sidebar and the full view: work that grows with the square of the fleet and ran on the event
    loop. This grades the work, not the time: while one poll of N sessions is applied, `display_id` and
    `shortest_prefix` are never called, and the map of every id is built at most once for the sidebar and the view
    (the same set of ids) over exactly the N ids, for 100 and for 500 sessions alike."""
    import asyncio

    from input_server import InputRecordingApp
    from tsb_tui.app import TsbTuiApp
    from tsb_tui.model import fleet as fleet_mod
    from tsb_tui.screens import terminal as terminal_mod
    from tsbc import _serve
    from workspace_support import fixture_login

    async def apply_poll_of(count: int) -> dict[str, int]:
        serve_app, url = serve(InputRecordingApp())
        for n in range(count):
            status, _headers, reply = serve_app._create(_serve.USER, {
                "target_id": _serve.TARGET, "connection": "new", "term": "xterm-256color", "cols": 80, "rows": 24})
            assert status == 201
            serve_app.sessions[reply["session"]["id"]]["target_id"] = f"rtr-{n % 7}"
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_sidebar()
            screen.open_fleet_view()
            calls = {"display_id": 0, "shortest_prefix": 0, "map": 0, "ids": 0}

            def counting(name, fn):
                def wrapped(*args, **kwargs):
                    calls[name] += 1
                    return fn(*args, **kwargs)
                return wrapped

            real_map = fleet_mod.display_id_map

            def counted_map(ids, *args, **kwargs):
                calls["map"] += 1
                result = real_map(ids, *args, **kwargs)
                calls["ids"] += len(result)
                return result

            for module in (fleet_mod, terminal_mod):
                monkeypatch.setattr(module, "display_id", counting("display_id", fleet_mod.display_id), raising=False)
            monkeypatch.setattr(fleet_mod, "shortest_prefix", counting("shortest_prefix", fleet_mod.shortest_prefix))
            monkeypatch.setattr(fleet_mod, "display_id_map", counted_map)
            # One more session appears, so the poll brings a set of ids the fleet has not built a map for.
            serve_app._create(_serve.USER, {"target_id": _serve.TARGET, "connection": "new", "term": "xterm-256color",
                                            "cols": 80, "rows": 24})
            records = app.client.sessions()
            assert len(records) == count + 1
            screen.fleet.generation += 1
            screen._apply_poll(screen.fleet.generation, records)
            await pilot.pause(0.1)
            return calls

    for count in (100, 500):
        calls = asyncio.run(apply_poll_of(count))
        assert calls["display_id"] == 0 and calls["shortest_prefix"] == 0, (count, calls)
        assert 1 <= calls["map"] <= 1, (count, calls)
        assert calls["ids"] == count + 1, (count, calls)
