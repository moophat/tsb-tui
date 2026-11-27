"""drive's waits (design §2.9.4, D51): the predicates, the timeout, and the events that fail a wait at once.

Why it matters (Rule 9): an agent decides what to type next from the answer to a wait. A wait that holds when it should
not types into a screen that is not ready; a wait that rides through the owner taking the keyboard keeps working on a
session it no longer owns, and the owner's Ctrl-C and the agent's next command meet on a router. So the rules that
judge are tested without a screen (a fake probe and told events, nothing to race), and the ones that depend on a real
session and a real take-over run against the contract's in-process fixture server with two drivers.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

import pytest
from drive_support import Rig
from input_server import InputRecordingApp, make_session
from tsbc import _serve

from tsb_tui.drive import waits

# -- the rules, without a screen --


@dataclass
class FakeProbe:
    """What a wait reads, set by the test: the models as they are now."""

    holder_: str = "other"
    record: str | None = "live"
    closed: str | None = None
    row: str = ""
    screen: str = ""
    reply: str = ""
    last: float | None = None
    clock: float = 100.0
    tail_: list[str] = field(default_factory=list)

    def holder(self) -> str:
        return self.holder_

    def record_state(self) -> str | None:
        return self.record

    def closed_as(self) -> str | None:
        return self.closed

    def cursor_row(self) -> str:
        return self.row

    def screen_text(self) -> str:
        return self.screen

    def reply_text(self) -> str:
        return self.reply

    def last_output_at(self) -> float | None:
        return self.last

    def now(self) -> float:
        return self.clock

    def tail(self) -> list[str]:
        return self.tail_


def _wait(until: dict, probe: FakeProbe | None = None) -> tuple[waits.Wait, FakeProbe]:
    probe = probe or FakeProbe()
    return waits.Wait(waits.parse_until(until, default_settled_ms=800), probe, started=probe.clock), probe


def test_every_predicate_must_hold_not_just_one_of_them() -> None:
    """D51: "all of which must hold". A prompt on the cursor's row is not enough when the screen does not yet carry
    what was asked for: a half-matching wait would release the agent into a half-printed answer."""
    wait, probe = _wait({"prompt": r"\$\s*$", "screen": "Filesystem"})
    probe.row = "ops@db:~$ "
    assert wait.check([]) is None
    probe.screen = "Filesystem  Size\nops@db:~$ "
    done = wait.check([])
    assert done is not None and done.ok and done.matched == {"prompt": "$ ", "screen": "Filesystem"}


def test_prompt_reads_the_cursors_row_screen_the_whole_screen_and_reply_the_text_since_input() -> None:
    """The three regexes read three different texts: `prompt` only the cursor's row, `screen` the visible screen,
    `reply` what arrived since the last input (scrollback included). A `$` anchors a line, not the whole text."""
    wait, probe = _wait({"reply": r"^done$"})
    probe.reply = "running\ndone\nops@db:~$"
    assert wait.check([]).ok, "an anchor means a line of the reply"
    wait, probe = _wait({"prompt": "done"})
    probe.screen, probe.reply, probe.row = "done", "done", "ops@db:~$"
    assert wait.check([]) is None, "prompt is the cursor's row alone"


def test_settled_counts_from_the_later_of_the_wait_and_the_last_byte() -> None:
    """D51: settled is "no output for N ms, counted from the later of the command and the last byte". A byte from
    before the wait does not shorten it, and a byte inside the window starts it again: a hint that a command is quiet,
    and never earlier than N ms after the agent asked."""
    wait, probe = _wait({"settled_ms": 800}, FakeProbe(last=95.0))
    probe.clock = 100.5
    assert wait.check([]) is None and wait.settled_in() == pytest.approx(0.3)
    probe.clock = 100.8
    assert wait.check([]).ok
    wait, probe = _wait({"settled_ms": 800}, FakeProbe(last=None))
    probe.clock, probe.last = 100.7, 100.6
    assert wait.check([]) is None and wait.settled_in() == pytest.approx(0.7), "a byte at 100.6 moves it to 101.4"
    probe.clock = 101.4
    assert wait.check([]).ok


def test_settled_true_means_the_knob_and_the_other_predicates_still_hold() -> None:
    """`{"settled_ms": true}` is the `drive.settled_ms` knob's value. A settled screen with no prompt is not done: the
    guide says to pair them, and the rule is that every predicate holds."""
    wait, probe = _wait({"settled_ms": True, "prompt": "#"})
    assert wait.until.settled_ms == 800
    probe.clock = 102.0
    assert wait.check([]) is None, "quiet, but the prompt is not there"
    probe.row, probe.clock = "rtr-core-01#", 100.2
    assert wait.check([]) is None, "the prompt is there, but the session has not been quiet for 800 ms"
    probe.clock = 100.9
    assert wait.check([]).ok


def test_holder_state_and_ended_predicates() -> None:
    """`holder` me / not_me / nobody; `state` the record's; `ended` the session over. `not_me` includes escrow: when
    nobody holds the keyboard the agent does not, and a wait for the owner's brake to be on must see it."""
    for holder, until, holds in (("me", "me", True), ("other", "me", False), ("other", "not_me", True),
                                 ("nobody", "not_me", True), ("me", "not_me", False), ("nobody", "nobody", True),
                                 ("other", "nobody", False)):
        wait, _probe = _wait({"holder": until}, FakeProbe(holder_=holder))
        assert (wait.check([]) is not None) is holds, (holder, until)
    wait, probe = _wait({"state": "live"}, FakeProbe(record="unreachable"))
    assert wait.check([]) is None, "a wait for live is not failed by the session being unreachable"
    probe.record = "live"
    assert wait.check([]).ok
    wait, probe = _wait({"ended": True}, FakeProbe(closed="ended"))
    assert wait.check([]).ok, "a wait for the end is not failed by the end"
    wait, probe = _wait({"ended": False})
    assert wait.check([]).ok


def test_the_event_predicate_matches_by_fields_and_stays_matched() -> None:
    """`event`: an event with these fields arrives for the session. It is told once, so once seen it holds, and the
    wait's other predicates are then judged as they are now."""
    wait, probe = _wait({"event": {"ev": "token", "reason": "handoff"}, "prompt": "#"})
    told = [{"ev": "token", "reason": "takeover"}, {"ev": "token", "reason": "handoff", "holder": "me"}]
    assert wait.check(told[:1]) is None
    assert wait.check(told) is None, "the event came, the prompt has not"
    probe.row = "rtr#"
    done = wait.check([])
    assert done.ok and done.matched["event"]["reason"] == "handoff", "seen earlier, still counted"


@pytest.mark.parametrize(("told", "code", "extra"), [
    ({"ev": "token", "reason": "takeover", "_left_me": True, "_who": "tu"}, "takeover", {}),
    ({"ev": "token", "reason": "handoff", "_left_me": True, "_who": "tu"}, "token_moved", {}),
    ({"ev": "token", "reason": "owner", "_left_me": True, "_who": None}, "token_moved", {}),
    ({"ev": "token", "reason": "detach", "_left_me": True, "_who": "tu"}, "token_moved", {}),
    ({"ev": "input_refused", "reason": "not_typist"}, "refused", {"reason": "not_typist"}),
    ({"ev": "stale", "state": "pending", "_stale": True, "you_choose": False}, "stale", {"you_choose": False}),
    ({"ev": "notice", "ending": True, "ends_at": "2026-10-12T12:00:00Z", "ends_by": "idle"}, "notice",
     {"ends_at": "2026-10-12T12:00:00Z", "ends_by": "idle"}),
    ({"ev": "ended", "state": "killed"}, "ended", {}),
    ({"ev": "removed"}, "removed", {}),
    ({"ev": "detached"}, "detached", {}),
])
def test_a_wait_fails_at_once_on_each_event_the_design_lists(told: dict, code: str, extra: dict) -> None:
    """D51: the keyboard leaves you, your input is refused, a stale prompt or a countdown warning arrives, the session
    ends, you are removed or detached, or it becomes unreachable. Each is its own answer, so the agent knows what to
    do (the guide's table), and the reason a refusal carries is told."""
    wait, _probe = _wait({"reply": "never printed"})
    done = wait.check([told])
    assert done is not None and not done.ok and done.error == code, (told, done)
    assert (done.extra or {}) == extra


@pytest.mark.parametrize(("until", "told"), [
    ({"holder": "not_me"}, {"ev": "token", "reason": "takeover", "_left_me": True, "_who": "tu"}),
    ({"event": {"ev": "token"}}, {"ev": "token", "reason": "takeover", "_left_me": True, "_who": "tu"}),
    ({"event": {"ev": "input_refused", "reason": "not_typist"}}, {"ev": "input_refused", "reason": "not_typist"}),
    ({"event": {"ev": "stale"}}, {"ev": "stale", "state": "pending", "_stale": True}),
    ({"event": {"ev": "notice"}}, {"ev": "notice", "ending": True}),
    ({"ended": True}, {"ev": "ended", "state": "killed"}),
    ({"event": {"ev": "removed"}}, {"ev": "removed"}),
    ({"event": {"ev": "detached"}}, {"ev": "detached"}),
])
def test_a_wait_for_that_very_thing_is_not_failed_by_it(until: dict, told: dict) -> None:
    """D51: "unless it waits for that very thing". A wait for `holder not_me` is how an agent waits for the owner's
    brake, so it must pass on the take-over, not fail on it; likewise for each event a wait may be waiting for."""
    probe = FakeProbe(record="unreachable" if "state" in until else "live", closed="ended" if "ended" in until else None)
    wait, probe = _wait(until, probe)
    done = wait.check([told])
    assert done is None or done.ok, (until, done)


def test_a_notice_that_withdraws_the_warning_and_a_token_between_others_do_not_fail_a_wait() -> None:
    """Only a warning (`ending` true) fails a wait; its withdrawal is good news. And a keyboard that moves between two
    other clients never fails a wait that does not hold it."""
    wait, _probe = _wait({"reply": "x"})
    assert wait.check([{"ev": "notice", "ending": False}, {"ev": "token", "reason": "handoff",
                                                           "_left_me": False, "_who": "ci"}]) is None


def test_a_session_already_over_fails_a_new_wait_at_once() -> None:
    """The events are told once; a wait that starts after the end must still not wait 30 s for a screen that will never
    change."""
    wait, _probe = _wait({"reply": "x"}, FakeProbe(closed="ended"))
    assert wait.check([]).error == "ended"
    wait, _probe = _wait({"reply": "x"}, FakeProbe(closed="removed"))
    assert wait.check([]).error == "removed"
    wait, _probe = _wait({"reply": "x"}, FakeProbe(record="unreachable"))
    assert wait.check([]).error == "unreachable"


@pytest.mark.parametrize("until", [
    None, {}, [], {"prompt": "("}, {"prompt": 3}, {"holder": "you"}, {"settled_ms": "800"}, {"settled_ms": -1},
    {"settled_ms": False}, {"ended": "yes"}, {"event": {}}, {"state": ""}, {"colour": "red"}])
def test_a_malformed_until_is_refused_naming_what_is_wrong(until) -> None:
    """A predicate drive cannot evaluate is a `bad_request`, never a wait that silently holds or never does."""
    with pytest.raises(waits.BadWait):
        waits.parse_until(until, default_settled_ms=800)


def test_the_timeout_defaults_to_the_knob_and_is_capped_by_it() -> None:
    """D51: 30 s by default, 10 min at most, both knobs; asking for more is capped, never refused."""
    assert waits.timeout_for(None, default_ms=30000, max_ms=600000) == 30000
    assert waits.timeout_for(None, default_ms=1234, max_ms=600000) == 1234
    assert waits.timeout_for(900000, default_ms=30000, max_ms=600000) == 600000
    assert waits.timeout_for(0, default_ms=30000, max_ms=600000) == 0
    for bad in (-1, "5", True, 1.5):
        with pytest.raises(waits.BadWait):
            waits.timeout_for(bad, default_ms=30000, max_ms=600000)


# -- against a session --


def _run(coro) -> None:
    asyncio.run(coro)


def _emit_output(serve_app, sid: str, data: bytes) -> None:
    with serve_app.lock:
        serve_app._emit_output(serve_app.sessions[sid], data)


def _inject(serve_app, sid: str, frame: dict, validate) -> None:
    """A server frame to every client attached to the session, checked against the contract's schema first."""
    validate(frame)
    with serve_app.lock:
        serve_app._broadcast(serve_app.sessions[sid], lambda _cid, ws: ws.send_text(json.dumps(frame)))


async def _pending(rig: Rig) -> None:
    """Wait, bounded, until the driver has a wait registered: a frame told before it would not be one it waits through."""
    for _ in range(500):
        if rig.driver._pending:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the wait was never registered")


def test_a_timeout_is_an_ordinary_answer_with_the_last_lines_of_the_screen(serve, tmp_path) -> None:
    """D51: a timeout never crashes drive, and tells the agent where the screen stands, so it can decide without a
    second read: the lines are the screen's last, blank rows dropped."""

    async def check() -> None:
        _app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            await rig.cmd("type", text="show version\r", look=await rig.look())
            assert (await rig.cmd("wait", until={"reply": "show version"}))["ok"]
            answer = await rig.cmd("wait", until={"prompt": "never"}, timeout_ms=150)
            assert answer["ok"] is False and answer["error"] == "timeout" and answer["tail"] == ["show version"]
            assert answer["elapsed_ms"] >= 150
            capped = await rig.cmd("wait", until={"holder": "not_me"}, timeout_ms=100)
            assert capped["error"] == "timeout"

    _run(check())


def test_settled_never_answers_before_the_period_and_a_late_byte_pushes_it_out(serve, tmp_path) -> None:
    """The lower bounds are the guarantee (a slow machine only makes them longer): a quiet session answers no earlier
    than `settled_ms` after the wait began, and a byte inside the period restarts it."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            quiet = await rig.cmd("wait", until={"settled_ms": 300})
            assert quiet["ok"] and quiet["elapsed_ms"] >= 300
            pending = rig.send("wait", until={"settled_ms": 300})
            await _pending(rig)
            await asyncio.sleep(0.15)
            _emit_output(serve_app, sid, b"more")
            late = await asyncio.wait_for(pending, 10)
            assert late["ok"] and late["elapsed_ms"] >= 150 + 300 - 20, late

    _run(check())


def test_the_owner_taking_the_keyboard_fails_the_agents_wait_within_100_ms(serve, tmp_path) -> None:
    """Design §3.4 test 7, typing-ownership steps 8-10: the owner types while not the holder, the token moves in the
    same trip, and the agent's pending wait fails with `takeover` within 100 ms of the `token` frame reaching it, so its
    next command is never typed after the owner's Ctrl-C."""

    async def check() -> None:
        _app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as owner, Rig(url, tmp_path=tmp_path, profile="harness",
                                                             overrides=("input.guard=false",)) as agent:
            await owner.login(_serve.USER)
            await owner.attach(sid)
            await agent.login(_serve.PEER)
            await agent.attach(sid)
            agent_client = agent.app.workspace().slot_for(sid).pump.state.client_id
            assert (await owner.cmd("hand", to=agent_client))["ok"]
            assert (await agent.cmd("wait", until={"holder": "me"}))["ok"]
            seen: dict[str, float] = {}
            agent.on_line = lambda line: seen.setdefault(
                "token" if line.get("ev") == "token" and line["reason"] == "takeover" else
                "answer" if line.get("error") == "takeover" else "-", time.monotonic())
            pending = agent.send("wait", until={"reply": "never printed"}, timeout_ms=20000)
            await _pending(agent)
            assert (await owner.cmd("keys", keys=["ctrl+c"], look=await owner.look()))["ok"], "the owner types and takes the keyboard"
            answer = await asyncio.wait_for(pending, 10)
            assert answer["ok"] is False and answer["error"] == "takeover", answer
            assert answer["why"] == f"{_serve.USER}, the owner, took the keyboard"
            assert "tail" in answer
            assert seen["answer"] - seen["token"] < 0.1, f"the wait failed {seen['answer'] - seen['token']:.3f} s later"
            token = next(e for e in agent.events("token") if e["reason"] == "takeover")
            assert token["holder"]["user"] == _serve.USER and token["you"] == "view_only"
            assert token["your_input"] == "refused from now"

    _run(check())


def test_a_wait_for_holder_not_me_passes_on_the_same_takeover_frame(serve, tmp_path) -> None:
    """D51's exception: waiting for the owner's brake is how an agent notices it. The same `token` frame that fails
    every other wait is the one that satisfies this one."""

    async def check() -> None:
        _app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as owner, Rig(url, tmp_path=tmp_path) as agent:
            await owner.login(_serve.USER)
            await owner.attach(sid)
            await agent.login(_serve.PEER)
            await agent.attach(sid)
            agent_client = agent.app.workspace().slot_for(sid).pump.state.client_id
            await owner.cmd("hand", to=agent_client)
            assert (await agent.cmd("wait", until={"holder": "me"}))["ok"]
            pending = agent.send("wait", until={"holder": "not_me"}, timeout_ms=20000)
            await _pending(agent)
            await owner.cmd("keys", keys=["ctrl+c"], look=await owner.look())
            answer = await asyncio.wait_for(pending, 10)
            assert answer["ok"] is True, answer

    _run(check())


def test_each_frame_that_fails_a_wait_does_so_and_a_wait_for_it_passes(serve, server_frame_validator, tmp_path) -> None:
    """The frames the core sends, told to a real client while its wait is pending: a refused input, a stale screen for a
    client that does not choose, a countdown warning. Each fails a plain wait with the guide's code, and passes a wait
    that names it."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER)
        refused = {"t": "input_refused", "v": 1, "reason": "not_typist"}
        stale = {"t": "stale", "v": 1, "session_id": sid, "state": "pending", "cause": "gap", "gap_seq": 3,
                 "at": "2026-10-12T12:00:00Z"}
        notice = {"t": "notice", "v": 1, "session_id": sid, "ends_at": "2026-10-12T12:00:00Z", "ends_by": "idle",
                  "ending": True}
        async with Rig(url, tmp_path=tmp_path) as owner, Rig(url, tmp_path=tmp_path) as agent:
            await owner.login(_serve.USER)
            await owner.attach(sid)
            await agent.login(_serve.PEER)
            await agent.attach(sid)
            for frame, code, wanted in ((refused, "refused", {"ev": "input_refused", "reason": "not_typist"}),
                                        (stale, "stale", {"ev": "stale", "you_choose": False}),
                                        (notice, "notice", {"ev": "notice", "ending": True})):
                failing = agent.send("wait", until={"reply": "never printed"}, timeout_ms=20000)
                await _pending(agent)
                _inject(serve_app, sid, frame, server_frame_validator)
                answer = await asyncio.wait_for(failing, 10)
                assert answer["error"] == code, answer
                passing = agent.send("wait", until={"event": wanted}, timeout_ms=20000)
                await _pending(agent)
                _inject(serve_app, sid, frame, server_frame_validator)
                answer = await asyncio.wait_for(passing, 10)
                assert answer["ok"] is True and answer["matched"]["event"]["ev"] == wanted["ev"], answer
            assert [e["reason"] for e in agent.events("input_refused")] == ["not_typist", "not_typist"]

    _run(check())


def test_the_end_of_the_session_fails_a_wait_unless_it_waits_for_the_end(serve, tmp_path) -> None:
    """4010 is an end: a plain wait is told `ended` (and the event says `killed`); a wait for `ended: true` passes. A
    wait that starts after the end fails at once instead of timing out."""

    async def check() -> None:
        serve_app, url = serve()
        first, second = make_session(url, _serve.USER), make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(first)
            await rig.attach(second)
            plain = rig.send("wait", session=first, until={"reply": "never printed"}, timeout_ms=20000)
            await _pending(rig)
            with serve_app.lock:
                serve_app._kill(serve_app.sessions[first])
            answer = await asyncio.wait_for(plain, 10)
            assert answer["error"] == "ended" and "killed" in answer["why"], answer
            assert rig.events("ended") == [{"ev": "ended", "session": first, "state": "killed"}]
            again = await rig.cmd("wait", session=first, until={"reply": "x"}, timeout_ms=20000)
            # at once, not the wait's 20 s timeout: half of it is the margin for a loaded runner
            assert again["error"] == "ended" and again["elapsed_ms"] < 10000
            expected = rig.send("wait", session=second, until={"ended": True}, timeout_ms=20000)
            await _pending(rig)
            with serve_app.lock:
                serve_app._kill(serve_app.sessions[second])
            assert (await asyncio.wait_for(expected, 10))["ok"] is True

    _run(check())


def test_being_removed_fails_a_wait_and_a_broker_detach_fails_it_then_drive_reattaches(serve, tmp_path) -> None:
    """4014 and 4011. Removed: the owner took this client off the session, drive tells the agent and never attaches by
    itself again (client-remove rule 8). Detached: the broker closed the view and the session lives, so drive reattaches
    by itself (session-detach rule 10), while the agent's wait is told `detached`."""

    async def check() -> None:
        serve_app, url = serve()
        removed_sid, detached_sid = make_session(url, _serve.USER), make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(removed_sid)
            await rig.attach(detached_sid)
            slot = rig.app.workspace().slot_for(detached_sid)
            first_client = slot.pump.state.client_id
            plain = rig.send("wait", session=detached_sid, until={"reply": "never printed"}, timeout_ms=20000)
            await _pending(rig)
            with serve_app.lock:
                serve_app._drop_client(serve_app.sessions[detached_sid], first_client, 4011, "output queue full")
            assert (await asyncio.wait_for(plain, 10))["error"] == "detached"
            for _ in range(500):
                if slot.pump.state.client_id not in (None, first_client) and not slot.pump.state.closed:
                    break
                await asyncio.sleep(0.01)
            assert slot.pump.state.client_id not in (None, first_client), "drive did not reattach by itself"

            doomed = rig.send("wait", session=removed_sid, until={"reply": "never printed"}, timeout_ms=20000)
            await _pending(rig)
            client = rig.app.workspace().slot_for(removed_sid).pump.state.client_id
            with serve_app.lock:
                serve_app._drop_client(serve_app.sessions[removed_sid], client, 4014, "removed")
            answer = await asyncio.wait_for(doomed, 10)
            assert answer["error"] == "removed", answer
            assert {"ev": "removed", "session": removed_sid} in rig.events("removed")
            await asyncio.sleep(0.3)
            assert serve_app.clients.get(removed_sid, {}) == {}, "drive attached again after being removed"

    _run(check())


def test_a_session_that_becomes_unreachable_fails_a_wait_on_the_next_poll(serve, tmp_path) -> None:
    """D51 lists "the session becomes unreachable". No frame says so: the fleet's poll does, so the wait is evaluated
    when the poll changes the record, with `fleet_view.refresh_s` shortened for the test."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path, overrides=("fleet_view.refresh_s=1",)) as rig:
            await rig.login()
            await rig.attach(sid)
            pending = rig.send("wait", until={"reply": "never printed"}, timeout_ms=20000)
            await _pending(rig)
            with serve_app.lock:
                serve_app.sessions[sid]["state"] = "unreachable"
            answer = await asyncio.wait_for(pending, 15)
            assert answer["error"] == "unreachable", answer

    _run(check())
