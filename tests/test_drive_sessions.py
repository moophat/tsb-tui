"""One driver in several sessions (design §2.9.4, D33, D49): one terminal in front, the rest live.

Why it matters (Rule 9): an agent works in three sessions without ever "being" in one. If a read or a wait moved the
front, an agent watching a router would pull a person's screen away from the session they type in; if an action did not
bring its session forward, a `type` could land in the wrong grid's idea of the keyboard. And a background session must
keep its own grid, so a reply read later still holds what arrived while another session was in front.
Servers: the contract's in-process fixture server.
"""

from __future__ import annotations

import asyncio

from drive_support import Rig
from input_server import InputRecordingApp, make_session
from tsbc import _serve


def _emit_output(serve_app, sid: str, data: bytes) -> None:
    with serve_app.lock:
        serve_app._emit_output(serve_app.sessions[sid], data)


def _front(rig: Rig) -> str:
    return rig.app.workspace().front.session_id


def test_reads_and_waits_on_background_sessions_leave_the_front_alone_and_actions_bring_theirs_forward(
        serve, tmp_path) -> None:
    """D49: "a read or a wait never switches"; an action on another session brings it to the front first, as a click on
    its chip would, and the answer says where it came from. Each attach is its own chip, in the order attached."""

    async def check() -> None:
        _app, url = serve(InputRecordingApp())
        first, second, third = (make_session(url, _serve.USER) for _ in range(3))
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            chips = [(await rig.attach(sid))["chip"] for sid in (first, second, third)]
            assert chips == [1, 2, 3] and _front(rig) == third

            for command in (("read", {"session": first, "what": "screen"}), ("read", {"session": second, "what": "who"}),
                            ("read", {"session": first, "what": "reply"}),
                            ("wait", {"session": second, "until": {"holder": "me"}})):
                assert (await rig.cmd(command[0], **command[1]))["ok"]
                assert _front(rig) == third, f"{command[0]} {command[1]} moved the front"

            moved = await rig.cmd("type", session=first, text="a", look=await rig.look(session=first))
            assert moved["front"] == first and moved["switched_from"] == third and _front(rig) == first
            again = await rig.cmd("type", session=first, text="b", look=await rig.look(session=first))
            assert again["front"] == first and "switched_from" not in again
            keys = await rig.cmd("keys", session=second, keys=["enter"], look=await rig.look(session=second))
            assert keys["front"] == second and keys["switched_from"] == first and _front(rig) == second
            switched = await rig.cmd("switch", session=third)
            assert switched["front"] == third and _front(rig) == third
            assert (await rig.cmd("detach", session=first))["ok"]
            assert rig.app.workspace().slot_for(first) is None, "detach is an action: it came forward, then left"
            assert [s.session_id for s in rig.app.workspace().slots] == [second, third]

    asyncio.run(check())


def test_each_session_keeps_its_own_grid_and_reply_while_another_is_in_front(serve, tmp_path) -> None:
    """D33: a background session is parsed, not painted, and keeps its own grid. 60 lines arrive in a session that is
    not in front; read later, its reply holds all of them (scrollback included) and the other session's screen does
    not hold any."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        quiet, busy = make_session(url, _serve.USER, 80, 24), make_session(url, _serve.USER, 80, 24)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(busy)
            await rig.attach(quiet)
            assert _front(rig) == quiet
            await rig.cmd("type", session=busy, text="start\r", look=await rig.look(session=busy))
            assert (await rig.cmd("wait", session=busy, until={"reply": "start"}))["ok"]
            await rig.cmd("switch", session=quiet)
            _emit_output(serve_app, busy, b"\r\n" + b"".join(f"row{i:02d}\r\n".encode() for i in range(60)))
            assert (await rig.cmd("wait", session=busy, until={"reply": "row59"}))["ok"]
            assert _front(rig) == quiet
            reply = await rig.cmd("read", session=busy, what="reply")
            assert reply["lines"][0] == "start" and "row00" in reply["lines"] and "row59" in reply["lines"]
            assert len(reply["lines"]) == 61, "every line, scrolled off or not"
            assert "row00" not in (await rig.cmd("read", session=busy, what="screen"))["lines"]
            quiet_screen = await rig.cmd("read", session=quiet, what="screen")
            assert not any("row" in line for line in quiet_screen["lines"])
            history = await rig.cmd("read", session=busy, what="history", lines=5)
            assert history["lines"] == [f"row{i:02d}" for i in range(55, 60)]

    asyncio.run(check())


def test_sessions_lists_sessions_the_driver_does_not_own_and_marks_those_open_here(serve, tmp_path) -> None:
    """§2.9.5, §4.4: today joining is granted per target, so a login sees every session on a target it may join, not
    only the ones it was lent. `sessions` shows them all (this profile's filter starts at `all`, DT15), with their
    owners, so the agent can leave the rest alone; it attaches only the one it names."""

    async def check() -> None:
        serve_app, url = serve()
        never_lent, mine, lent = (make_session(url, owner) for owner in (_serve.USER, _serve.PEER, _serve.USER))
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login(_serve.PEER)
            await rig.attach(lent)
            rows = {row["session"]: row for row in (await rig.cmd("sessions"))["sessions"]}
            assert set(rows) == {lent, never_lent, mine}
            assert rows[never_lent]["owner"] == _serve.USER and rows[mine]["owner"] == _serve.PEER
            assert rows[lent]["holder"] == _serve.PEER, "the agent attached first, so it holds the keyboard"
            listed = [row["session"] for row in (await rig.cmd("sessions"))["sessions"]]
            assert listed[0] == lent, "sessions open here come first, as in the sidebar"
            assert len(serve_app.clients.get(never_lent, {})) == 0, "listing a session attached nothing"

    asyncio.run(check())


def test_sessions_live_keeps_only_sessions_that_have_not_ended(serve, tmp_path) -> None:
    """#26 item 2: `live` keeps only sessions with no end state. A killed session is excluded; a detached one is kept."""

    async def check() -> None:
        serve_app, url = serve()
        mine = make_session(url, _serve.USER)
        killed = make_session(url, _serve.USER)
        with serve_app.lock:
            serve_app._kill(serve_app.sessions[killed])
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login(_serve.USER)
            all_rows = {row["session"] for row in (await rig.cmd("sessions"))["sessions"]}
            assert mine in all_rows and killed in all_rows
            live_rows = {row["session"] for row in (await rig.cmd("sessions", live=True))["sessions"]}
            assert mine in live_rows and killed not in live_rows

    asyncio.run(check())


def test_sessions_own_keeps_only_sessions_owned_by_this_login(serve, tmp_path) -> None:
    """#26 item 2: `own` keeps only sessions whose owner is this login."""

    async def check() -> None:
        _app, url = serve()
        mine = make_session(url, _serve.USER)
        theirs = make_session(url, _serve.PEER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login(_serve.USER)
            all_rows = {row["session"] for row in (await rig.cmd("sessions"))["sessions"]}
            assert mine in all_rows and theirs in all_rows
            own_rows = {row["session"] for row in (await rig.cmd("sessions", own=True))["sessions"]}
            assert mine in own_rows and theirs not in own_rows

    asyncio.run(check())


def test_sessions_live_and_own_together_filter_both_ways(serve, tmp_path) -> None:
    """#26 item 2: both filters together. A killed session of this login is excluded by live; a live session of
    another owner is excluded by own; a live session of this login passes both."""

    async def check() -> None:
        serve_app, url = serve()
        mine_live = make_session(url, _serve.USER)
        mine_dead = make_session(url, _serve.USER)
        make_session(url, _serve.PEER)
        with serve_app.lock:
            serve_app._kill(serve_app.sessions[mine_dead])
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login(_serve.USER)
            rows = {row["session"] for row in (await rig.cmd("sessions", live=True, own=True))["sessions"]}
            assert rows == {mine_live}

    asyncio.run(check())


def test_sessions_non_boolean_live_or_own_answers_bad_request(serve, tmp_path) -> None:
    """#26 item 2: a non-boolean value for `live` or `own` is bad_request."""

    async def check() -> None:
        _app, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            for field in ("live", "own"):
                answer = await rig.cmd("sessions", **{field: "yes"})
                assert answer["ok"] is False and answer["error"] == "bad_request", (field, answer)

    asyncio.run(check())
