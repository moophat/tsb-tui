"""`tsb-tui drive`'s commands (design §2.9.4, D48): each answers in the shape the guide promises, goes through the
action a person would fire, and never does what it was not asked.

Why it matters (Rule 9): an agent has no eyes. Everything it knows is an answer or an event line, and every byte it
sends reaches a production device. A command that answered `ok` without having sent, that sent without the guard,
that read the window instead of the session's grid, or that took a password from argv would each be a wrong action the
agent cannot see. Servers: the contract's in-process fixture server (DT7); the app runs on Textual's test runner, the
loop and the transcript are drive's own (`drive_support.Rig`).
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import threading
from typing import Any

import pytest
from drive_support import Rig
from input_server import InputRecordingApp, make_session
from signout_support import HeldRequestApp, LogoutProbeApp
from tsbc import _serve

from tsb_tui.drive import runner

GUIDE_TITLE = "# tsb-tui drive: a guide for agents"


def _run(coro) -> None:
    asyncio.run(coro)


async def _shown(rig: Rig, text: str, *, present: bool = True) -> None:
    """Wait, bounded, until a snapshot shows `text` (or stops showing it): a dialog opens a few loop turns after the
    click that asked for it."""
    for _ in range(300):
        if (text in (await rig.cmd("snapshot"))["text"]) == present:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"the screen never {'showed' if present else 'stopped showing'} {text!r}")


async def _mode(rig: Rig, sid: str, mode: str) -> None:
    """Wait, bounded, until the session's terminal in the TUI reports `mode` on: the key encoder reads the
    session's modes, so a settle is not enough — the condition is the mode itself."""
    for _ in range(300):
        if (await rig.cmd("read", session=sid, what="screen"))["modes"][mode]:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"the session never entered mode {mode!r}")


def _emit_output(serve_app, sid: str, data: bytes) -> None:
    """Output from the target, as the core relays it to every attached client."""
    with serve_app.lock:
        serve_app._emit_output(serve_app.sessions[sid], data)


def test_login_answers_the_principal_and_a_wrong_password_answers_in_the_cores_words(serve, tmp_path) -> None:
    """honesty rule 1: login is the login screen with its button clicked. The answer is the principal; a refusal is the
    core's own sentence, and the driver stays logged out (one driver is one login, so it may try again)."""

    async def check() -> None:
        _app, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            refused = await rig.cmd("login", user=_serve.USER, password_file=rig.password_file("not-the-password"))
            assert refused["ok"] is False and refused["error"] == "login_failed" and refused["why"], refused
            assert rig.driver.screen() is None
            assert await rig.login() == {"id": 2, "ok": True, "principal": _serve.USER}
            again = await rig.cmd("login", user=_serve.USER, password_file=rig.password_file())
            assert again["error"] == "bad_request" and "already logged in" in again["why"]

    _run(check())


def test_a_password_in_a_command_is_refused_and_nothing_reaches_the_core(serve, tmp_path) -> None:
    """honesty rule 5: passwords come only from a file or stdin. A `password` field in a login command is refused
    before any request is made, so a secret an agent was given never travels through a line drive writes or records."""

    async def check() -> None:
        serve_app, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            answer = await rig.cmd("login", user=_serve.USER, password=_serve.PASSWORD)
            assert answer["error"] == "bad_request" and "a password is never given in a command" in answer["why"], answer
            assert not serve_app.logins, "the core saw a login"
            dash = await rig.cmd("login", user=_serve.USER, password_file="-")
            assert dash["error"] == "bad_request" and "stdin carries commands" in dash["why"]
            assert not serve_app.logins

    _run(check())


def test_a_command_before_login_answers_not_logged_in(serve, tmp_path) -> None:
    """Nothing is attached or listed for a driver that has not logged in; it is told, and told how to."""

    async def check() -> None:
        _app, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            for op in ("sessions", "attach", "read", "type", "wait"):
                answer = await rig.cmd(op, session="sess-1", what="screen", text="x", until={"holder": "me"})
                assert answer["ok"] is False and answer["error"] == "not_logged_in", (op, answer)

    _run(check())


def test_bad_commands_answer_bad_request_and_the_loop_goes_on(serve, tmp_path) -> None:
    """A line that is not JSON, not an object, or has an op drive does not know is an answer, never a crash: an agent
    that mistypes one line must not lose its sessions' driver."""

    async def check() -> None:
        _app, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            rig.raw("{not json")
            rig.raw("[1, 2]")
            for _ in range(200):
                if len(rig.lines) >= 2:
                    break
                await asyncio.sleep(0.01)
            assert [line["error"] for line in rig.lines[:2]] == ["bad_request", "bad_request"]
            unknown = await rig.cmd("teleport")
            assert unknown["error"] == "bad_request" and "op" in unknown["why"]
            assert (await rig.login())["ok"]

    _run(check())


def test_sessions_lists_the_fleet_the_login_may_see_with_its_fields(serve, tmp_path) -> None:
    """D48 `sessions`: reads the sidebar's rows, which start at `all` in this profile (DT15), so a session the driver
    does not own is listed too. Each row carries what the guide names: id, target, state, owner, holder, rec, ends_in."""

    async def check() -> None:
        _app, url = serve()
        mine = make_session(url, _serve.PEER)
        theirs = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login(_serve.PEER)
            answer = await rig.cmd("sessions")
            rows = {row["session"]: row for row in answer["sessions"]}
            assert set(rows) == {mine, theirs}, "the filter starts at `all`, so a session of another owner is listed"
            row = rows[theirs]
            assert row["owner"] == _serve.USER and row["target"] == _serve.TARGET and row["rec"] is True
            assert row["state"] == "detached" and row["holder"] is None and "ends_in" in row

    _run(check())


def test_attach_prints_the_recording_text_before_it_attaches_then_answers(serve, tmp_path) -> None:
    """session-attach CR1: the recording text reaches the agent before anything is attached. The `gate` event comes
    first, while the core has no client on the session; the answer then says where the agent stands: its chip,
    whether it holds the keyboard, who does, whether it may type, and the session's own size."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER, 100, 30)
        async with Rig(url, tmp_path=tmp_path) as rig:
            clients_at_gate: list[int] = []
            rig.on_line = lambda line: clients_at_gate.append(len(serve_app.clients.get(sid, {}))) if line.get("ev") == "gate" else None
            await rig.login()
            answer = await rig.attach(sid)
            gate = rig.events("gate")
            assert len(gate) == 1 and gate[0]["session"] == sid
            assert gate[0]["recording"] == _serve.WARNING, "the text the record carries, unchanged"
            assert clients_at_gate == [0], "something was attached before the recording text was told"
            assert rig.lines.index(gate[0]) < rig.lines.index(answer), "the gate comes before the answer"
            assert answer["session"] == sid and answer["chip"] == 1 and answer["front"] is True
            assert answer["you"] == "type" and answer["holder"] == _serve.USER and answer["you_may_type"] is True
            assert answer["size"] == "100x30", "the session's own size, not the headless window's"

    _run(check())


def test_attach_to_a_session_that_is_not_there_or_has_ended_answers_why(serve, tmp_path) -> None:
    """`no_such_session` is "not a session you may see"; an ended one is `ended`. Neither opens a gate or a socket."""

    async def check() -> None:
        serve_app, url = serve()
        gone = make_session(url, _serve.USER)
        with serve_app.lock:
            serve_app._kill(serve_app.sessions[gone])
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            assert (await rig.cmd("attach", session="sess-404"))["error"] == "no_such_session"
            ended = await rig.cmd("attach", session=gone)
            assert ended["error"] == "ended" and "killed" in ended["why"], ended
            assert not rig.events("gate")

    _run(check())


def test_an_attach_that_fails_answers_attach_failed_with_the_cores_words_and_leaves_nothing_open(
        serve, tmp_path) -> None:
    """The ticket mint refuses (a login that may not reach the session): the attach fails before any socket, drive says
    so in the core's own words instead of waiting out its timeout, no session is left half-open here, and the one
    already in front stays in front."""
    from tsbc._serve import ServeApp

    class Refusing(ServeApp):
        def _may_attach(self, user, sid):
            return None if user == _serve.PEER else super()._may_attach(user, sid)

    async def check() -> None:
        _app, url = serve(Refusing())
        sid, other = make_session(url, _serve.USER), make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login(_serve.PEER)
            answer = await rig.cmd("attach", session=sid)
            assert answer["ok"] is False and answer["error"] == "attach_failed", answer
            assert "no such session" in answer["why"] or "session_not_found" in answer["why"], answer
            assert rig.app.workspace().slots == [], "a session was left half open"
            assert rig.events("gate") != [], "the recording text was told first, as it always is"
            assert (await rig.cmd("read", session=other, what="screen"))["error"] == "not_attached"

    _run(check())


def test_type_sends_the_text_literally_and_says_how_much_was_queued(serve, tmp_path) -> None:
    """`type` is literal text, `\\r` is Enter, and the server is the witness: the bytes it received are the text's
    bytes, nothing more, and `queued` counts them. TSB sends no acknowledgement, so this is all the answer can say."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            client = rig.app.workspace().slot_for(sid).pump.state.client_id
            answer = await rig.cmd("type", text="df -h /var\r", look=await rig.look())
            assert answer["ok"] and answer["queued"] == 11 and answer["front"] == sid
            assert "switched_from" not in answer
            marker = await rig.cmd("wait", until={"reply": "df -h /var"})
            assert marker["ok"], marker
            assert b"".join(serve_app.received(client)) == b"df -h /var\r"

    _run(check())


def test_keys_are_encoded_for_the_sessions_modes_and_an_unknown_name_sends_nothing(serve, tmp_path) -> None:
    """D34: `keys` go through libghostty's encoder for the session's modes (an arrow is CSI A, or SS3 A once the
    session asked for application cursor keys). An unknown name is `unknown_key` and *nothing* is sent, not the
    keys before it: a half-sent chord on a router is worse than none."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            client = rig.app.workspace().slot_for(sid).pump.state.client_id
            assert (await rig.cmd("keys", keys=["up"], look=await rig.look()))["queued"] == 3
            _emit_output(serve_app, sid, b"\x1b[?1h")
            await _mode(rig, sid, "cursor_keys_application")
            assert (await rig.cmd("keys", keys=["up", "ctrl+c"], look=await rig.look()))["error"] == "control_bytes"
            refused = await rig.cmd("keys", keys=["ctrl+d", "bogus-key"])
            assert refused["error"] == "unknown_key" and refused["key"] == "bogus-key" and refused["sent"] is False
            await rig.cmd("wait", until={"settled_ms": 50})
            assert serve_app.received(client) == [b"\x1b[A"], serve_app.received(client)

    _run(check())


def test_the_guard_answers_not_typist_and_sends_nothing(serve, tmp_path) -> None:
    """DT4 (a), D52: with `input.guard` on, a client that does not hold the keyboard and is not the owner's gets
    `not_typist`, `sent: false`, and the server records nothing from it: the agent's own command is stopped by the
    same guard a person's key is, and the answer says who holds the keyboard."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as owner, Rig(url, tmp_path=tmp_path) as agent:
            await owner.login(_serve.USER)
            await owner.attach(sid)
            await agent.login(_serve.PEER)
            attached = await agent.attach(sid)
            assert attached["you"] == "view_only" and attached["holder"] == _serve.USER
            client = agent.app.workspace().slot_for(sid).pump.state.client_id
            for op, fields in (("type", {"text": "x"}), ("keys", {"keys": ["enter"]})):
                answer = await agent.cmd(op, **fields)
                assert answer["ok"] is False and answer["error"] == "not_typist", answer
                assert answer["holder"] == _serve.USER and answer["sent"] is False
            await agent.cmd("wait", until={"settled_ms": 50})
            assert serve_app.received(client) == [], "the guard let a byte through"
            assert not agent.events("input_refused"), "the core was never asked"

    _run(check())


def test_ask_to_type_answers_not_available_and_asks_nothing(serve, tmp_path) -> None:
    """D55, OPEN-4: TSB has no way to ask for the keyboard yet, so the answer says so, in the guide's words, and no
    frame is sent."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            before = len(serve_app.frames)
            answer = await rig.cmd("ask_to_type", session=sid)
            assert answer["error"] == "not_available" and "ask the owner another way" in answer["why"]
            assert len(serve_app.frames) == before

    _run(check())


def test_read_screen_is_the_sessions_whole_grid_not_the_window(serve, tmp_path) -> None:
    """Honesty rule 2, D50: a read is made in this client's own grid at the session's full size. A window of 60x20
    over a 132x40 session still reads 132 columns and 40 rows; the cursor is a (row, column) in that grid; the
    input modes the session set are told."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER, 132, 40)
        async with Rig(url, tmp_path=tmp_path, size=(60, 20)) as rig:
            await rig.login()
            await rig.attach(sid)
            _emit_output(serve_app, sid, b"\x1b[?2004h\x1b[3;5Hhere")
            answer = await rig.cmd("wait", until={"screen": "here"})
            assert answer["ok"], answer
            screen = await rig.cmd("read", what="screen")
            assert (screen["cols"], screen["rows"]) == (132, 40) and len(screen["lines"]) == 40
            assert screen["lines"][2] == "    here" and screen["cursor"] == [2, 8]
            assert screen["alt"] is False and screen["modes"]["bracketed_paste"] is True
            history = await rig.cmd("read", what="history", lines=3)
            assert history["lines"] == screen["lines"][-3:] or len(history["lines"]) <= 3

    _run(check())


def test_read_reply_is_the_text_since_this_clients_last_input_scrollback_included(serve, tmp_path) -> None:
    """D50: `reply` is what arrived since this client's last typed input, including lines that scrolled off the
    screen: 60 lines of output in a 24-row session are all there. A later input moves the mark, so the second reply
    holds only the second command's output. `alt` says whether a full-screen program owns the screen."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER, 80, 24)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            await rig.cmd("type", text="first\r", look=await rig.look())
            assert (await rig.cmd("wait", until={"reply": "first"}))["ok"], "the echo must land before the output"
            _emit_output(serve_app, sid, b"\r\n" + b"".join(f"line{i:02d}\r\n".encode() for i in range(60)))
            answer = await rig.cmd("wait", until={"reply": "line59"})
            assert answer["ok"], answer
            reply = await rig.cmd("read", what="reply")
            assert reply["lines"][0] == "first" and "line00" in reply["lines"] and "line59" in reply["lines"]
            assert reply["alt"] is False and reply["truncated"] is False
            screen = await rig.cmd("read", what="screen")
            assert "line00" not in screen["lines"], "line00 should have scrolled off the 24-row screen"
            await rig.cmd("type", text="second\r", look=await rig.look())
            assert (await rig.cmd("wait", until={"reply": "second"}))["ok"]
            again = await rig.cmd("read", what="reply")
            assert all("line" not in line for line in again["lines"]), again

    _run(check())


def test_a_full_screen_program_owns_the_reply_and_the_screen_says_alt(serve, tmp_path) -> None:
    """Guide §6: in a full-screen program there is no scrollback, so `reply` is the screen and says `alt: true`: the
    shell's lines from before the program started must not be in it, or an agent reads vim's screen as a command's
    output."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER, 40, 8)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            await rig.cmd("type", text="vim notes\r", look=await rig.look())
            assert (await rig.cmd("wait", until={"reply": "vim notes"}))["ok"]
            _emit_output(serve_app, sid, b"\x1b[?1049h\x1b[2;3HINSERT -- notes")
            assert (await rig.cmd("wait", until={"screen": "INSERT"}))["ok"]
            reply = await rig.cmd("read", what="reply")
            assert reply["alt"] is True and reply["lines"] == ["", "  INSERT -- notes"], reply
            screen = await rig.cmd("read", what="screen")
            assert screen["alt"] is True and "vim notes" not in screen["lines"]
            _emit_output(serve_app, sid, b"\x1b[?1049l")
            assert (await rig.cmd("wait", until={"screen": "vim notes"}))["ok"]
            assert (await rig.cmd("read", what="reply"))["alt"] is False

    _run(check())


def test_read_who_names_the_holder_the_owner_and_every_client(serve, tmp_path) -> None:
    """`who`: holder, owner, attached clients, and whether this client may type: what an agent needs before it asks
    for the keyboard or hands it on."""

    async def check() -> None:
        _app, url = serve()
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as owner, Rig(url, tmp_path=tmp_path) as agent:
            await owner.login(_serve.USER)
            await owner.attach(sid)
            await agent.login(_serve.PEER)
            await agent.attach(sid)
            await agent.cmd("wait", until={"holder": "not_me"})
            who = await agent.cmd("read", what="who")
            assert who["holder"] == _serve.USER and who["owner"] == _serve.USER and who["you_may_type"] is True
            assert sorted(c["user"] for c in who["clients"]) == sorted([_serve.USER, _serve.PEER])
            assert who["you"] in {c["client_id"] for c in who["clients"]}

    _run(check())


def test_size_asks_for_the_windows_size_only_when_the_agent_holds_the_keyboard(serve, tmp_path) -> None:
    """D53: drive sends no size request by itself; `size` is the explicit one, and only the typist may ask. The
    typist gets the core's answer (here clamped to the fixture's floor); a client that is not the typist is told
    `not_typist` and nothing is sent."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER, 80, 24)
        async with Rig(url, tmp_path=tmp_path, size=(30, 10)) as rig, Rig(url, tmp_path=tmp_path) as viewer:
            await rig.login()
            await rig.attach(sid)
            assert not [f for f in serve_app.frames if f.get("t") == "resize"], "drive asked for a size by itself"
            answer = await rig.cmd("size")
            assert answer["ok"] and answer["reason"] == "clamped_floor" and answer["size"] == "40x12", answer
            assert answer["asked"] == "26x9", "the window minus the rail and the status row"
            await viewer.login(_serve.PEER)
            await viewer.attach(sid)
            before = len([f for f in serve_app.frames if f.get("t") == "resize"])
            refused = await viewer.cmd("size")
            assert refused["error"] == "not_typist"
            assert len([f for f in serve_app.frames if f.get("t") == "resize"]) == before

    _run(check())


def test_detach_leaves_the_session_running_and_a_later_command_says_not_attached(serve, tmp_path) -> None:
    """Detach is Detach: the client goes, the session lives (it is `detached` in the core's record), and the agent's
    next command to it is told `not_attached` rather than being sent anywhere."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            assert (await rig.cmd("detach"))["ok"]
            assert (await rig.cmd("type", session=sid, text="x"))["error"] == "not_attached"
            for _ in range(300):
                if serve_app.sessions[sid]["state"] == "detached":
                    break
                await asyncio.sleep(0.01)
            assert serve_app.sessions[sid]["state"] == "detached", "the session was ended, not detached"

    _run(check())


def test_logout_detaches_signs_out_and_a_new_login_is_accepted(serve, tmp_path) -> None:
    """`logout` is the Menu's Sign out. It leaves every session open here (each lives on, `detached` in the core's
    record, never ended), answers which ones and that the broker confirmed, and leaves drive logged out: the next
    command is told `not_logged_in` and a `login` is accepted (one driver is one login, one at a time)."""

    async def check() -> None:
        serve_app, url = serve(LogoutProbeApp())
        first, second = make_session(url, _serve.USER), make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(first)
            await rig.attach(second)
            answer = await rig.cmd("logout")
            assert answer["ok"], answer  # fails today: bad_request, `op: expected one of ...`
            assert answer["detached"] == [first, second] and answer["broker"] == "signed_out", answer
            assert serve_app.at_logout == {first: ("detached", 0), second: ("detached", 0)}, (
                "the sessions were not detached on the server before logout reached it")
            assert not any(s["state"] in ("killed", "terminated") for s in serve_app.sessions.values())
            assert (await rig.cmd("sessions"))["error"] == "not_logged_in"
            assert (await rig.cmd("snapshot"))["text"].count("log in") >= 1, "Login is not showing"
            assert (await rig.cmd("logout"))["error"] == "not_logged_in", "a second logout has nothing to sign out"
            again = await rig.login()
            assert again["principal"] == _serve.USER
            assert (await rig.cmd("sessions"))["ok"]

    _run(check())


def test_logout_answers_unconfirmed_when_the_broker_did_not_confirm(serve, tmp_path) -> None:
    """A failed logout seen through drive: the broker answered 500, drive is logged out anyway and says the broker did not
    confirm, so an agent never believes the login ended there when it did not."""

    async def check() -> None:
        serve_app, url = serve(LogoutProbeApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            serve_app.logout_status = 500
            answer = await rig.cmd("logout")
            assert answer["ok"], answer
            assert answer["detached"] == [sid] and answer["broker"] == "unconfirmed", answer
            assert (await rig.cmd("sessions"))["error"] == "not_logged_in"

    _run(check())


def test_logout_before_login_answers_not_logged_in(serve, tmp_path) -> None:
    """There is nothing to sign out of before the first login: the guide's `not_logged_in`, like every other command."""

    async def check() -> None:
        _app, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            answer = await rig.cmd("logout")
            assert answer.get("error") == "not_logged_in", answer  # fails today: bad_request

    _run(check())


def _deletes(serve_app) -> list[str]:
    """Every `DELETE /sessions/{id}` the server received: what ending a session sends, and nothing else does."""
    with serve_app.lock:
        return [path for method, path, _body in serve_app.requests if method == "DELETE" and path.startswith("/sessions/")]


def _toasts(rig: Rig) -> list[str]:
    return [n.message for n in rig.app._notifications._notifications.values()]


def test_end_ends_the_session_through_the_screens_path(serve, tmp_path) -> None:
    """`end` runs `TerminalScreen.end_session`'s send half, so what ^\\ x does is what drive does: one `DELETE`, the
    record `killed`, and the screen's own "You ended ..." toast. It answers after the core's 204, and the
    agent then waits for the end frame in `wait`, where every other wait is."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            answer = await rig.cmd("end", session=sid, confirm=True)
            assert answer["ok"] and answer["session"] == sid, answer  # fails today: bad_request, `op: expected one of`
            assert _deletes(serve_app) == [f"/sessions/{sid}"]
            assert serve_app.sessions[sid]["state"] == "killed"
            assert any(t.startswith("You ended ") for t in _toasts(rig)), _toasts(rig)
            waited = await rig.cmd("wait", session=sid, until={"ended": True})
            assert waited["ok"], waited

    _run(check())


def test_end_without_confirm_true_is_a_bad_request_and_sends_nothing(serve, tmp_path) -> None:
    """Ending is for everyone and cannot be undone, so the command must say so. A missing `confirm`, or
    one that is not the boolean true, is `bad_request` with the reason, no dialog opens and no `DELETE` leaves."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            for fields in ({}, {"confirm": False}, {"confirm": "true"}, {"confirm": 1}):
                answer = await rig.cmd("end", session=sid, **fields)
                assert answer["error"] == "bad_request" and "confirm: true" in answer["why"] \
                    and "cannot be undone" in answer["why"], (fields, answer)
            assert _deletes(serve_app) == []
            assert serve_app.sessions[sid]["state"] != "killed"
            assert "End it" not in (await rig.cmd("snapshot"))["text"], "a dialog was opened"

    _run(check())


def test_end_by_a_non_owner_answers_refused(serve, tmp_path) -> None:
    """The core says who may end (the owner, or `session.terminate`): PEER is attached to USER's session, the 403
    is `refused` with the dialog's own sentence, the screen shows it too, and the session still runs."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login(_serve.PEER)
            await rig.attach(sid)
            answer = await rig.cmd("end", session=sid, confirm=True)
            assert answer["error"] == "refused", answer  # fails today: bad_request, `op: expected one of`
            assert answer["why"].startswith("Only the owner of ") and answer["why"].endswith(", or an admin, can end it."), answer
            assert _deletes(serve_app) == [f"/sessions/{sid}"], "the op never pre-checks: the core answered"
            assert serve_app.sessions[sid]["state"] != "killed"
            assert any(t.startswith("Only the owner of ") for t in _toasts(rig)), _toasts(rig)

    _run(check())


def test_end_on_an_ended_session_answers_ended_and_sends_nothing(serve, tmp_path) -> None:
    """The record check `end_session` makes: a session whose record says it ended is `ended`, and no `DELETE` is sent
    (what the core answers to ending an ended session is contract open item 2)."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            with serve_app.lock:
                serve_app._kill(serve_app.sessions[sid])
            for _ in range(300):  # the record the screen holds says `killed` once it has polled
                await rig.cmd("sessions")
                if (rig.app.workspace()._record_for(sid) or {}).get("state") == "killed":
                    break
                await asyncio.sleep(0.02)
            answer = await rig.cmd("end", session=sid, confirm=True)
            assert answer["error"] == "ended", answer  # fails today: bad_request, `op: expected one of`
            assert _deletes(serve_app) == []

    _run(check())


def test_end_maps_a_missing_session_and_other_failures_to_the_guides_codes(serve, tmp_path) -> None:
    """404 is `no_such_session`; any other failure is `request_failed` with the core's words. Neither is the refusal."""

    class Failing(_serve.ServeApp):
        status = 404

        def _http(self, method, path, headers, body, query=None):
            if method == "DELETE" and path.startswith("/sessions/"):
                return self._err(self.status, "session_not_found" if self.status == 404 else "internal", "no")
            return super()._http(method, path, headers, body, query)

    async def check() -> None:
        serve_app, url = serve(Failing())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            missing = await rig.cmd("end", session=sid, confirm=True)
            assert missing["error"] == "no_such_session", missing  # fails today: bad_request, `op: expected one of`
            serve_app.status = 500
            failed = await rig.cmd("end", session=sid, confirm=True)
            assert failed["error"] == "request_failed" and "500" in failed["why"], failed

    _run(check())


def test_a_late_end_answer_after_the_timeout_leaves_the_tui_running(serve, tmp_path) -> None:
    """A late answer after a timeout. `end` answers `timeout` when the core is slower than `drive.wait_timeout_ms`, and
    `wait_for` cancels the future the callback would settle. The DELETE's answer then arrives: the callback finds
    the future done and leaves it. Not `InvalidStateError` inside the api worker, which ends the app. The late answer
    still did what a person sees (the toast), and drive goes on answering."""

    async def check() -> None:
        serve_app, url = serve(HeldRequestApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)  # under the normal timeout: the gate can take longer than 300 ms on a loaded CI node
            release = threading.Event()
            serve_app.holds[("DELETE", "/sessions/")] = release
            # Only `end`, which must time out, gets the short one.
            rig.app.cfg.set_runtime("drive.wait_timeout_ms", 300)
            answer = await rig.cmd("end", session=sid, confirm=True)
            assert answer["error"] == "timeout", answer
            release.set()  # the core answers now, after drive gave up
            for _ in range(500):
                if any(t.startswith("You ended ") for t in _toasts(rig)):
                    break
                await asyncio.sleep(0.01)
            assert any(t.startswith("You ended ") for t in _toasts(rig)), _toasts(rig)
            await asyncio.sleep(0)  # the callback that follows the toast is the one that used to raise
            assert rig.app._exception is None, f"the late answer errored the api worker: {rig.app._exception!r}"
            assert rig.app.is_running
            assert (await rig.cmd("sessions"))["ok"], "drive stopped answering"

    _run(check())


def test_an_end_held_across_sign_out_still_gets_its_answer(serve, tmp_path) -> None:
    """A drive `end` is at the broker when a sign-out replaces the client, so the DELETE reaches
    the broker without a cookie and answers 401 on a client that is no longer current. That 401 must not navigate or
    toast for the next login, but it must still reach the `end` waiting on it: it answers `request_failed` at once,
    not `timeout` after `drive.wait_timeout_ms`."""

    async def check() -> None:
        serve_app, url = serve(HeldRequestApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            release = threading.Event()
            serve_app.holds[("DELETE", "/sessions/")] = release
            ending = rig.send("end", session=sid, confirm=True)
            for _ in range(500):
                if serve_app.arrived.is_set():
                    break
                await asyncio.sleep(0.01)
            assert serve_app.arrived.is_set(), "the DELETE never reached the server"
            await rig.app_call(rig.app.sign_out)  # replaces the client; the DELETE is still held
            release.set()
            answer = await asyncio.wait_for(ending, 10)
            assert answer.get("error") == "request_failed", answer
            assert not any(t.startswith("Request failed") for t in _toasts(rig)), _toasts(rig)
            assert rig.app._exception is None

    _run(check())


def test_end_on_a_session_that_is_not_attached_answers_not_attached(serve, tmp_path) -> None:
    """Like `detach`, `end` addresses a session this drive has open: with none in front it is `not_attached`, and the
    session is not ended."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            answer = await rig.cmd("end", confirm=True)
            assert answer["error"] == "not_attached", answer  # fails today: bad_request, `op: expected one of`
            answer = await rig.cmd("end", session=sid, confirm=True)
            assert answer["error"] == "not_attached", answer
            assert _deletes(serve_app) == []

    _run(check())


def test_snapshot_text_is_what_a_pilot_of_the_same_app_shows(serve, tmp_path, screen_text) -> None:
    """`snapshot` is Textual's own export of the whole UI (mockup A2): the text it returns is the screen the operator
    of this headless TUI would read, row by row, and the svg is the same UI as an image."""

    async def check() -> None:
        _app, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            text = await rig.cmd("snapshot")
            assert text["format"] == "text" and text["text"] == screen_text(rig.app)
            assert "Fleet" in text["text"]
            svg = await rig.cmd("snapshot", format="svg")
            assert svg["svg"].lstrip().startswith("<svg")
            assert (await rig.cmd("snapshot", format="png"))["error"] == "bad_request"

    _run(check())


def test_click_and_press_fire_the_ui_by_label_and_by_key(serve, tmp_path) -> None:
    """`click` finds a visible control by its label and clicks it; `press` sends Textual key names. Both are the
    same actions a person fires (honesty rule 1): the Menu opens from the leader key or from its hint, and a label
    nothing on screen carries is `bad_request`, never a click somewhere else."""

    async def check() -> None:
        _app, url = serve()
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as agent:
            await agent.login()
            await agent.attach(sid)
            off = await agent.cmd("click", label="Who's here")
            assert off["error"] == "not_available" and "mouse.enabled" in off["why"], "a click that does nothing says so"
        async with Rig(url, tmp_path=tmp_path, profile="harness") as rig:
            await rig.login()
            await rig.attach(sid)
            assert (await rig.cmd("click", label="Who's here"))["ok"]
            await _shown(rig, "Who's here ·")
            assert (await rig.cmd("press", keys=["escape"]))["ok"]
            await _shown(rig, "Who's here ·", present=False)
            missing = await rig.cmd("click", label="no such control anywhere")
            assert missing["error"] == "bad_request"
            assert (await rig.cmd("press", keys=["ctrl+backslash", "w"]))["ok"]
            await _shown(rig, "Who's here ·")

    _run(check())


def test_an_empty_click_label_is_refused_and_clicks_nothing(serve, tmp_path) -> None:
    """An empty `click` label is refused as a usage error, and no click is fired. An agent has no eyes: a label of
    `""` (or only spaces) would match the first control on screen and press a button nobody named — a wrong action
    it could not see. The screen here is unchanged and nothing was clicked."""

    async def check() -> None:
        _app, url = serve()
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path, profile="harness") as rig:
            await rig.login()
            await rig.attach(sid)
            fired: list[Any] = []
            real_click = rig.pilot.click

            async def counting_click(*args: Any, **kwargs: Any) -> Any:
                fired.append((args, kwargs))
                return await real_click(*args, **kwargs)

            rig.pilot.click = counting_click
            for label in ("", "   "):
                before = (await rig.cmd("snapshot"))["text"]
                answer = await rig.cmd("click", label=label)
                assert answer["error"] == "bad_request" and "label" in answer["why"], answer
                assert not fired, f"a click was fired for the label {label!r}"
                assert (await rig.cmd("snapshot"))["text"] == before, "the screen changed"

    _run(check())


def test_resize_changes_the_window_and_never_the_sessions_grid(serve, tmp_path) -> None:
    """`resize` is the headless window: a snapshot's size follows it, the session's grid and what a read returns do not
    (D3, D4), and no size request is sent to the core (terminal-size §4: nothing the window does asks)."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER, 100, 30)
        async with Rig(url, tmp_path=tmp_path, size=(120, 40)) as rig:
            await rig.login()
            await rig.attach(sid)
            assert (await rig.cmd("resize", size="70x25"))["size"] == "70x25"
            text = (await rig.cmd("snapshot"))["text"]
            assert len(text.splitlines()) == 25
            screen = await rig.cmd("read", what="screen")
            assert (screen["cols"], screen["rows"]) == (100, 30)
            assert not [f for f in serve_app.frames if f.get("t") == "resize"]
            assert (await rig.cmd("resize", size="0x5"))["error"] == "bad_request"

    _run(check())


def test_quit_answers_then_ends_the_process_and_every_session_keeps_running(serve, tmp_path) -> None:
    """`quit` is Menu ▸ Quit: it answers, the loop ends, and nothing is sent to any session: the session is still
    there for the next client (D38)."""

    async def check() -> None:
        serve_app, url = serve()
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            assert (await rig.cmd("quit"))["ok"]
            for _ in range(500):
                if rig.rc is not None:
                    break
                await asyncio.sleep(0.01)
            assert rig.rc == 0
        assert serve_app.sessions[sid]["state"] != "killed"

    _run(check())


def test_the_record_keeps_the_exchange_as_the_design_shows_a_transcript(serve, tmp_path) -> None:
    """`--record`: a `>` line for each command, a `<` line for each answer and event, in the order they happened; it
    never holds a password, since none is ever in a line."""

    async def check() -> None:
        _app, url = serve()
        record = io.StringIO()
        async with Rig(url, tmp_path=tmp_path, record=record) as rig:
            await rig.login()
            await rig.cmd("sessions")
        text = record.getvalue().splitlines()
        assert text[0].startswith("> ") and json.loads(text[0][2:])["op"] == "login"
        assert text[1].startswith("< ") and json.loads(text[1][2:]) == {"id": 1, "ok": True, "principal": _serve.USER}
        assert [json.loads(line[2:])["op"] for line in text if line.startswith("> ")] == ["login", "sessions"]
        assert [line[:2] for line in text] == ["> ", "< ", "> ", "< "], "a command, its answer, in order"
        assert _serve.PASSWORD not in record.getvalue()

    _run(check())


# -- the process: flags, stdin, exit codes --


def _main(monkeypatch, capsys, argv: list[str], stdin: str = "") -> tuple[int, list[dict], str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    rc = runner.main(argv)
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines() if line.startswith("{")]
    return rc, lines, captured.err


def test_guide_prints_without_a_broker_and_help_names_it(monkeypatch, capsys) -> None:
    """DT10, D56: the guide ships in the package and `--guide` prints it with no broker and no login; `--help` says so,
    so an agent that only has the binary can find what it is told."""
    rc, _lines, _err = _main(monkeypatch, capsys, ["--guide"])
    assert rc == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert runner.main(["--guide"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(GUIDE_TITLE) and out == runner.GUIDE.read_text(encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        runner.main(["--help"])
    assert stop.value.code == 0
    assert "--guide" in capsys.readouterr().out


def test_bad_usage_exits_2_and_a_password_never_comes_from_argv_or_the_environment(
        monkeypatch, capsys, tmp_path) -> None:
    """honesty rule 5, design §2.9.4: no flag takes a password, so one on the command line is a usage error (2), and a
    `TSB_TUI_PASSWORD` in the environment is refused by name at start-up, the same way every other unknown knob is."""
    def started(*_args, **_kwargs):
        raise AssertionError("drive started: the usage error was not caught")

    monkeypatch.setattr(runner, "_run", started)
    for argv in (["--broker", "http://127.0.0.1:1", "--password", "secret"],
                 ["--broker", "http://127.0.0.1:1", "--user", "u"],
                 ["--broker", "http://127.0.0.1:1", "--size", "wide"],
                 ["--broker", "http://127.0.0.1:1", "--size", "0x5"],
                 ["--broker", "http://127.0.0.1:1", "--script", str(tmp_path / "missing")],
                 ["--profile", "default"], []):
        with pytest.raises(SystemExit) as stop:
            runner.main(argv)
        assert stop.value.code == 2, argv
    capsys.readouterr()
    monkeypatch.setenv("TSB_TUI_PASSWORD", "secret")
    rc, _lines, err = _main(monkeypatch, capsys, ["--broker", "http://127.0.0.1:1"])
    assert rc == 1 and "TSB_TUI_PASSWORD" in err and "unknown configuration key" in err


def test_password_file_dash_takes_the_first_stdin_line_then_stdin_carries_commands(
        serve, monkeypatch, capsys) -> None:
    """The design's password-from-stdin: the first line is the password, and the lines after it are commands. The
    password is not echoed anywhere drive writes."""
    _app, url = serve()
    sid = make_session(url, _serve.USER)
    commands = "\n".join([json.dumps({"id": 1, "op": "sessions"}), json.dumps({"id": 2, "op": "quit"})])
    rc, lines, _err = _main(monkeypatch, capsys,
                            ["--broker", url, "--user", _serve.USER, "--password-file", "-"],
                            f"{_serve.PASSWORD}\n{commands}\n")
    assert rc == 0
    assert lines[0]["ev"] == "ready" and lines[0]["principal"] == _serve.USER and lines[0]["profile"] == "drive"
    assert lines[0]["size"] == "120x40" and lines[0]["engine"]
    assert lines[1]["id"] == 1 and [row["session"] for row in lines[1]["sessions"]] == [sid]
    assert lines[2] == {"id": 2, "ok": True}
    assert _serve.PASSWORD not in json.dumps(lines)


def test_a_script_runs_every_step_or_stops_at_the_first_that_fails(serve, monkeypatch, capsys, tmp_path) -> None:
    """Exit codes (design §2.9.4): 0 when every step of a script passed, 1 when a step fails, and the script stops
    there: a script that went on typing after its wait failed would be typing into a screen it did not expect. Nothing
    after the failed step is run (it would have said so in a line)."""
    _app, url = serve()
    sid = make_session(url, _serve.USER)
    pw = tmp_path / "pw"
    pw.write_text(_serve.PASSWORD + "\n")
    pw.chmod(0o600)
    login = {"id": 1, "op": "login", "user": _serve.USER, "password_file": str(pw)}
    good = tmp_path / "good.jsonl"
    good.write_text("# a comment\n" + "\n".join(json.dumps(c) for c in (
        login, {"id": 2, "op": "attach", "session": sid},
        {"id": 3, "op": "wait", "until": {"holder": "me"}, "timeout_ms": 2000})) + "\n")
    rc, lines, _err = _main(monkeypatch, capsys, ["--broker", url, "--script", str(good)])
    assert rc == 0 and [line.get("ok") for line in lines if "id" in line] == [True, True, True]
    bad = tmp_path / "bad.jsonl"
    bad.write_text("\n".join(json.dumps(c) for c in (
        {**login, "id": 1}, {"id": 2, "op": "attach", "session": sid},
        {"id": 3, "op": "wait", "until": {"screen": "never appears"}, "timeout_ms": 200},
        {"id": 4, "op": "type", "text": "rm -rf /\r"})) + "\n")
    rc, lines, _err = _main(monkeypatch, capsys, ["--broker", url, "--script", str(bad)])
    answers = [line for line in lines if "id" in line]
    assert rc == 1 and answers[-1]["id"] == 3 and answers[-1]["error"] == "timeout"
    assert 4 not in [a["id"] for a in answers], "a step after the failed one ran"


def test_the_harness_profile_keeps_the_guard_on_and_can_still_switch_it_off(serve, monkeypatch, capsys) -> None:
    """D52 was ruled on 2026-10-12: the guard stays on in every profile, the harness included, so the
    harness profile starts with no override and reports `input.guard` on. A rig that wants to test the
    core's own refusal still switches it off explicitly with `--set input.guard=false`."""
    _app, url = serve()
    rc, lines, _err = _main(monkeypatch, capsys, ["--broker", url, "--profile", "harness"],
                            json.dumps({"op": "quit"}) + "\n")
    assert rc == 0 and lines[0]["ev"] == "ready" and lines[0]["profile"] == "harness", lines
    for value in ("true", "false"):
        rc, lines, _err = _main(monkeypatch, capsys, ["--broker", url, "--profile", "harness", "--set",
                                                      f"input.guard={value}"], json.dumps({"op": "quit"}) + "\n")
        assert rc == 0 and lines[0]["ev"] == "ready" and lines[0]["profile"] == "harness", (value, lines)


def test_the_guide_names_every_command_event_and_error_drive_has() -> None:
    """DT10: the guide is "updated to what is built (and nothing that is not)". An agent has only this text, so a
    command, an event or an error code drive has that the guide never mentions is one it will meet unprepared, and
    one the guide names that drive never produces is one it will wait for in vain. Read from drive's own source."""
    import re
    from pathlib import Path

    from tsb_tui.drive import commands, events, waits

    guide = runner.GUIDE.read_text(encoding="utf-8")
    sources = "\n".join(Path(module.__file__).read_text(encoding="utf-8") for module in (commands, events, waits, runner))
    table_of_commands = guide.split("## 5. Commands")[1].split("**Key names**")[0]
    for op in commands.OPS:
        assert f"| `{op}`" in table_of_commands or f"`{op}`, " in table_of_commands or f", `{op}` |" in table_of_commands, (
            f"the guide's command table does not name {op}")
    for name in (*events.EVENT_NAMES, "output", "ready"):
        assert f"`{name}`" in guide, f"the guide does not name the event {name}"
    built = set(re.findall(r'DriveError\(\s*"([a-z_]+)"', sources)) | set(re.findall(r'Done\(\s*False,\s*"([a-z_]+)"', sources))
    built |= set(re.findall(r'_refuse\(slot, "([a-z_]+)"', sources))
    built |= {"internal"}
    table = guide.split("## 11. Errors")[1].split("## 12.")[0]
    told = {code for row in re.findall(r"^\| ([^|]+) \|", table, re.MULTILINE) for code in re.findall(r"`([a-z_]+)`", row)}
    told.discard("error")  # the table's own header cell
    assert built <= told, f"errors drive gives that the guide does not name: {sorted(built - told)}"
    assert told <= built, f"errors the guide names that drive never gives: {sorted(told - built)}"
    for flag in ("--broker", "--profile", "--user", "--password-file", "--size", "--script", "--record", "--guide"):
        assert flag in guide, f"the guide does not name {flag}"


def test_a_click_lands_on_the_cell_a_label_starts_at_even_after_wide_characters() -> None:
    """`click` finds a label in the screen's text and clicks where it is: a character that takes two cells (a
    session named in CJK, an emoji) moves everything after it two columns right of its place in the string, so a count
    of characters would click the wrong control."""
    assert runner.cell_len("abc") == 3 and runner.cell_len("≡ Menu") == 6
    assert runner.cell_len("日本") == 4 and runner.cell_len("e\u0301") == 1


def test_drive_reads_are_live_while_the_view_is_open(serve, tmp_path) -> None:
    """Guide §6: `read screen`, `reply` and `history` are about the session, not about where a person is looking. With
    the scroll view open and the window moved a page up (the snapshot shows older output), the three reads answer
    exactly what they answered before, the newest line included."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER, 80, 24)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            await rig.cmd("type", text="first\r", look=await rig.look())
            assert (await rig.cmd("wait", until={"reply": "first"}))["ok"]
            _emit_output(serve_app, sid, b"".join(f"L{i:03d}\r\n".encode() for i in range(300)))
            assert (await rig.cmd("wait", until={"reply": "L299"}))["ok"]

            async def reads() -> list[dict[str, Any]]:
                answers = [await rig.cmd("read", what="screen"), await rig.cmd("read", what="reply"),
                           await rig.cmd("read", what="history", lines=50)]
                return [{key: value for key, value in answer.items() if key not in ("id", "look", "quiet_ms")} for answer in answers]

            live = await reads()
            await _shown(rig, "L299")
            assert "L299" in live[0]["lines"]
            assert (await rig.cmd("press", keys=["ctrl+backslash", "left_square_bracket"]))["ok"]
            await _shown(rig, "keys → scroll")
            assert (await rig.cmd("press", keys=["pageup"]))["ok"]
            await _shown(rig, "L299", present=False)
            assert "L275" in (await rig.cmd("snapshot"))["text"], "the window shows the page before the live one"
            assert await reads() == live, "an open view changes no read"

    _run(check())


def test_type_while_the_scroll_view_is_open_sends_nothing_and_says_not_available(serve, tmp_path) -> None:
    """Guide §6: with the scroll view open nothing reaches the session. `type` and `keys` answer `not_available`, the
    server receives no input, and after `escape` the same `type` is sent."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER, 80, 24)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            attached = await rig.attach(sid)
            _emit_output(serve_app, sid, b"".join(f"L{i:03d}\r\n".encode() for i in range(100)))
            assert (await rig.cmd("wait", until={"screen": "L099"}))["ok"]
            assert (await rig.cmd("press", keys=["ctrl+backslash", "left_square_bracket"]))["ok"]
            await _shown(rig, "keys → scroll")
            for op, fields in (("type", {"text": "ls\r"}), ("keys", {"keys": ["ctrl+c"]})):
                refused = await rig.cmd(op, **fields)
                assert refused["error"] == "not_available" and "scroll view" in refused["why"], refused
                assert refused["sent"] is False
            assert serve_app.received(attached["you"]) == [], "something reached the session while the view was open"
            assert (await rig.cmd("press", keys=["escape"]))["ok"]
            await _shown(rig, "keys → scroll", present=False)
            assert (await rig.cmd("type", text="ls\r", look=await rig.look()))["ok"]

    _run(check())
