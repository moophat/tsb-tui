"""What drive tells an agent unasked (design §2.9.4 "Events"): the shapes, the filter, the counts.

Why it matters (Rule 9): TSB sends no acknowledgement for input, so an event is the only way an agent learns the
keyboard left it, that its input was refused, that the screen may be wrong or that the session is ending. An event that
is missing, mis-shaped or says the wrong thing is a command the agent believes it did. The translation is tested from
the model state it reads (no screen, no socket), the filter and the counts against a real session.
"""

from __future__ import annotations

import asyncio
import json

from drive_support import Rig
from input_server import InputRecordingApp, make_session
from tsbc import _serve

from tsb_tui.drive import events as drive_events
from tsb_tui.model.attachment import AttachmentState
from tsb_tui.model.events import EventLog

ME = {"client_id": "client-1", "user": "ci-agent"}
OWNER = {"client_id": "client-2", "user": "tu"}
CLIENTS = [{"client_id": "client-1", "user": "ci-agent", "may_control": True},
           {"client_id": "client-2", "user": "tu", "may_control": True},
           {"client_id": "client-3", "user": "watcher", "may_control": False}]


def _state(holder: dict | None, *, user: str = "ci-agent", client: str = "client-1") -> AttachmentState:
    """An attachment as the pump leaves it after `attached`: this client, who owns the session, who holds the keyboard."""
    state = AttachmentState(client, user=user, session_id="s1")
    state.apply_frame({"t": "attached", "v": 1, "client_id": client, "session": {
        "id": "s1", "owner": "tu", "control_holder": holder, "clients": CLIENTS, "cols": 80, "rows": 24}})
    return state


def _told(state: AttachmentState, frame: dict, *, own_handoff: bool = False) -> list[dict]:
    """Apply one server frame and return the events its log line stands for."""
    before = len(state.log.all())
    state.apply_frame({"v": 1, **frame})
    out: list[dict] = []
    for line in state.log.all()[before:]:
        out += drive_events.translate("s1", line, state, own_handoff=own_handoff)
    return out


def test_a_token_that_comes_to_this_client_says_me_and_from_whom() -> None:
    """§2.9.5: `{"holder": "me", "reason": "handoff", "from": "tu"}`: the agent learns it may type, and who gave it."""
    state = _state(OWNER)
    (event,) = _told(state, {"t": "token", "holder": ME, "reason": "handoff"})
    assert drive_events.public(event) == {"ev": "token", "session": "s1", "reason": "handoff", "holder": "me",
                                          "from": "tu"}


def test_a_token_that_leaves_this_client_names_the_holder_and_says_its_input_is_refused_from_now() -> None:
    """§2.9.5: the owner's take-over reaches the agent as the holder, `you: view_only` and `your_input: "refused from
    now"`, so it stops. An owner's own client is not refused (its typing takes the keyboard back), a keyboard the
    agent handed over itself is no news, and a keyboard moving between two others says only who holds it."""
    leaving = {"t": "token", "holder": OWNER, "reason": "takeover"}
    (event,) = _told(_state(ME), leaving)
    assert drive_events.public(event) == {
        "ev": "token", "session": "s1", "reason": "takeover", "holder": {"user": "tu", "client_id": "client-2"},
        "you": "view_only", "your_input": "refused from now"}
    assert event["_left_me"] is True and event["_who"] == "tu"

    (own,) = _told(_state(ME), {"t": "token", "holder": OWNER, "reason": "handoff"}, own_handoff=True)
    assert "your_input" not in own and own["you"] == "view_only"
    (owners,) = _told(_state(OWNER, user="tu", client="client-2"), {"t": "token", "holder": {
        "client_id": "client-3", "user": "x"}, "reason": "handoff"})
    assert "your_input" not in owners and owners["_left_me"] is True, "an owner's client is not refused: it types back"
    (between,) = _told(_state(OWNER), {"t": "token", "holder": {"client_id": "client-3", "user": "x"},
                                      "reason": "handoff"})
    assert "your_input" not in between and between["_left_me"] is False
    (escrow,) = _told(_state(ME), {"t": "token", "holder": None, "reason": "owner"})
    assert escrow["holder"] is None and escrow["_who"] is None


def test_clients_size_notice_and_refusals_are_told_in_the_guides_shapes() -> None:
    """The other frames an agent acts on: who is attached (with who may type), the size in effect and why, the countdown
    entering or leaving its warning window, and a refusal of input with the core's reason."""
    state = _state(ME)
    (clients,) = _told(state, {"t": "clients", "session_id": "s1", "clients": CLIENTS})
    assert clients == {"ev": "clients", "session": "s1", "clients": CLIENTS}
    (size,) = _told(state, {"t": "size", "session_id": "s1", "cols": 132, "rows": 40, "reason": "clamped_ceiling"})
    assert size == {"ev": "size", "session": "s1", "size": "132x40", "reason": "clamped_ceiling"}
    (notice,) = _told(state, {"t": "notice", "session_id": "s1", "ends_at": "2026-10-12T12:00:00Z", "ends_by": "idle",
                              "ending": True})
    assert notice == {"ev": "notice", "session": "s1", "ending": True, "ends_at": "2026-10-12T12:00:00Z",
                      "ends_by": "idle"}
    (refused,) = _told(state, {"t": "input_refused", "reason": "stale"})
    assert refused == {"ev": "input_refused", "session": "s1", "reason": "stale"}
    (withdrawn,) = _told(state, {"t": "notice", "session_id": "s1", "ends_at": "2026-10-12T13:00:00Z",
                                 "ends_by": "idle", "ending": False})
    assert withdrawn["ending"] is False, "a withdrawn warning is told as withdrawn"
    assert _told(state, {"t": "request_refused", "request": "handoff", "reason": "not_allowed"}) == []
    assert _told(state, {"t": "data_path", "state": "lost", "at": "2026-10-12T12:00:00Z"}) == [], \
        "a frame the guide lists no event for is not one"


def test_a_stale_screen_tells_the_holder_its_choices_and_everyone_else_only_that_it_is_stale() -> None:
    """Stale-screen steps 4-5: only the keyboard holder is asked, so only its event carries the four choices (numbered
    as `answer` takes them); a client that is not asked is told the screen may be wrong, and that it does not choose. A
    choice made is told to all."""
    prompt = {"t": "stale_prompt", "prompt_id": "p1", "code": "stale.choose", "text": "TSB: lost output",
              "choices": [{"choice": "wiggle", "text": "Redraw"}, {"choice": "ctrl_l", "text": "Ctrl-L"},
                          {"choice": "continue", "text": "Keep going"}, {"choice": "fresh_session", "text": "Fresh"}]}
    holder = _state(ME)
    assert _told(holder, {"t": "stale", "session_id": "s1", "state": "pending", "cause": "gap", "gap_seq": 3,
                          "at": "x"}) == [], "the holder's prompt follows and carries the choices"
    (asked,) = _told(holder, prompt)
    assert drive_events.public(asked) == {"ev": "stale", "session": "s1", "state": "pending", "you_choose": True,
                                          "choices": [{"n": 1, "choice": "wiggle", "text": "Redraw"},
                                                      {"n": 2, "choice": "ctrl_l", "text": "Ctrl-L"},
                                                      {"n": 3, "choice": "continue", "text": "Keep going"},
                                                      {"n": 4, "choice": "fresh_session", "text": "Fresh"}]}
    (told,) = _told(_state(OWNER), {"t": "stale", "session_id": "s1", "state": "pending", "cause": "grid_lost",
                                    "at": "x"})
    assert drive_events.public(told) == {"ev": "stale", "session": "s1", "state": "pending", "cause": "grid_lost",
                                         "you_choose": False}
    (chosen,) = _told(holder, {"t": "stale", "session_id": "s1", "state": "chosen", "cause": "gap", "at": "x",
                               "choice": "wiggle", "by": OWNER})
    assert chosen == {"ev": "stale", "session": "s1", "state": "chosen", "choice": "wiggle"}


def test_the_closes_become_ended_removed_and_detached() -> None:
    """4010 and 4012 are the two ends (`killed`, `terminated`), 4014 the owner removing this client, 4011 the broker
    closing the view; the transport dying is none of them (the session lives and drive reattaches)."""
    for code, expected in ((4010, {"ev": "ended", "session": "s1", "state": "killed"}),
                           (4012, {"ev": "ended", "session": "s1", "state": "terminated"}),
                           (4014, {"ev": "removed", "session": "s1"}),
                           (4011, {"ev": "detached", "session": "s1"}),
                           (4999, {"ev": "ended", "session": "s1", "state": "killed"}),
                           (1006, None), (None, None)):
        state = _state(ME)
        before = len(state.log.all())
        state.apply_close(code, "")
        told = [e for line in state.log.all()[before:] for e in drive_events.translate("s1", line, state,
                                                                                       own_handoff=False)]
        assert told == ([expected] if expected else []), code


def test_the_event_log_tells_its_listener_each_line_as_it_is_written() -> None:
    """The one way drive learns of a change without reading the screen is the log's own listener: a line written is a
    line told, in order, from the first."""
    told: list[str] = []
    log = EventLog(10, lambda event: told.append(event.kind))
    state = AttachmentState("c", session_id="s", event_log=log)
    state.apply_frame({"t": "attached", "v": 1, "client_id": "c", "session": {"id": "s", "clients": []}})
    state.apply_frame({"t": "token", "v": 1, "holder": None, "reason": "owner"})
    assert told == ["attached", "token"]


def test_output_counts_are_one_line_a_second_per_session() -> None:
    """D53: `output` events say how many bytes arrived, at most one a second per session. The first chunk after a quiet
    second is told at once; what arrives inside the second is added up and told when it ends, so a firehose is one
    line a second, never a line per chunk."""
    now = [0.0]
    out: list[dict] = []
    later: list[tuple[float, object]] = []
    counter = drive_events.OutputCounter(out.append, lambda: now[0], lambda delay, fn: later.append((delay, fn)))
    counter.add("s1", 10)
    assert out == [{"ev": "output", "session": "s1", "bytes": 10}]
    now[0] = 0.2
    counter.add("s1", 5)
    now[0] = 0.4
    counter.add("s1", 7)
    counter.add("s2", 3)
    assert [e["session"] for e in out] == ["s1", "s2"] and len(later) == 1, "one timer for the second session's wait"
    assert later[0][0] == 0.8, "armed once, for what is left of the second"
    now[0] = 1.0
    later[0][1]()
    assert out[-1] == {"ev": "output", "session": "s1", "bytes": 12}
    assert len(out) == 3, "nothing else was told"


def test_the_events_knob_filters_what_is_written_and_a_wait_still_sees_every_event(serve, tmp_path) -> None:
    """`drive.events` lists what reaches stdout. An event left off is not written, but a wait for it still passes: the
    filter is about the agent's noise, never about what drive can judge a wait by."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER)
        frame = {"t": "size", "v": 1, "session_id": sid, "cols": 100, "rows": 30, "reason": "applied"}
        async with Rig(url, tmp_path=tmp_path, overrides=("drive.events=gate,clients",)) as rig:
            await rig.login()
            await rig.attach(sid)
            pending = rig.send("wait", until={"event": {"ev": "size", "reason": "applied"}}, timeout_ms=10000)
            for _ in range(500):
                if rig.driver._pending:
                    break
                await asyncio.sleep(0.01)
            with serve_app.lock:
                serve_app._broadcast(serve_app.sessions[sid], lambda _cid, ws: ws.send_text(json.dumps(frame)))
            answer = await asyncio.wait_for(pending, 10)
            assert answer["ok"] and answer["matched"]["event"]["size"] == "100x30"
            assert rig.events("size") == [], "an event the knob leaves off was written"
            assert {e["ev"] for e in rig.events()} == {"gate", "clients"}

    asyncio.run(check())


def test_the_harness_profile_counts_output_and_the_drive_profile_does_not(serve, tmp_path) -> None:
    """D53: `output` counts are off by default and the harness profile asks for them. Real output through a real
    session produces them, with the bytes counted, and the drive profile's stdout never carries one."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("input.guard=true",)) as counted, Rig(
                url, tmp_path=tmp_path) as plain:
            for rig in (counted, plain):
                await rig.login()
            await counted.attach(sid)
            await plain.attach(sid)
            with serve_app.lock:
                serve_app._emit_output(serve_app.sessions[sid], b"hello")
            assert (await counted.cmd("wait", until={"screen": "hello"}))["ok"]
            assert (await plain.cmd("wait", until={"screen": "hello"}))["ok"]
            assert counted.events("output") == [{"ev": "output", "session": sid, "bytes": 5}]
            assert plain.events("output") == []

    asyncio.run(check())


def test_a_stale_choice_is_answered_by_number_only_by_the_holder_with_the_prompts_own_id(serve, tmp_path) -> None:
    """D48 `answer`: the stale dialog's button 1 to 4, only for the keyboard holder, sent with the outstanding
    `prompt_id` (stale-screen step 11). The event told the choices, and drive answered none by itself (D53). A client
    that is not asked, or has nothing to answer, is told so and nothing is sent."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER, target="stale-pending")
        async with Rig(url, tmp_path=tmp_path) as owner:
            await owner.login(_serve.USER)
            await owner.attach(sid)
            event = None
            for _ in range(500):
                if owner.events("stale"):
                    event = owner.events("stale")[0]
                    break
                await asyncio.sleep(0.01)
            assert event is not None and event["you_choose"] is True and len(event["choices"]) == 4, owner.lines
            assert not [f for f in serve_app.frames if f.get("t") == "stale_answer"], "drive answered by itself"
            async with Rig(url, tmp_path=tmp_path) as viewer:
                await viewer.login(_serve.PEER)
                await viewer.attach(sid)
                told = await viewer.cmd("answer", choice=1)
                assert told["error"] == "not_typist" and not [f for f in serve_app.frames
                                                              if f.get("t") == "stale_answer"]
            assert (await owner.cmd("answer", choice=9))["error"] == "bad_request"
            answered = await owner.cmd("answer", choice=1)
            assert answered["ok"] and answered["choice"] == event["choices"][0]["choice"]
            for _ in range(500):
                if [f for f in serve_app.frames if f.get("t") == "stale_answer"]:
                    break
                await asyncio.sleep(0.01)
            (frame,) = [f for f in serve_app.frames if f.get("t") == "stale_answer"]
            assert frame["choice"] == "wiggle" and frame["prompt_id"]
            for _ in range(500):
                if any(e.get("state") == "chosen" for e in owner.events("stale")):
                    break
                await asyncio.sleep(0.01)
            assert any(e.get("state") == "chosen" and e["choice"] == "wiggle" for e in owner.events("stale"))
            nothing = await owner.cmd("answer", choice=1)
            assert nothing["error"] == "not_available", "nothing is waiting any more"

    asyncio.run(check())


def test_a_persons_typing_does_not_mark_the_grid(serve, wait_until) -> None:
    """`read reply` needs the grid marked at each typed input, and only drive asks for it: a person's TUI pays nothing
    on the typing path (design §3.4 test 4: keystroke to screen p95 under 50 ms)."""
    from workspace_support import fixture_login, open_here

    from tsb_tui.app import TsbTuiApp

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)

            await open_here(app, pilot, wait_until, sid)
            slot = screen.slot_for(sid)
            client = slot.pump.state.client_id
            await pilot.press("a")
            await wait_until(pilot, lambda: serve_app.received(client) == [b"a"], "the key to reach the core")
            assert slot.canvas.backend.has_mark("input") is False
            slot.mark_input = True
            await pilot.press("b")
            await wait_until(pilot, lambda: serve_app.received(client) == [b"a", b"b"], "the second key to arrive")
            assert slot.canvas.backend.has_mark("input") is True

    asyncio.run(check())
