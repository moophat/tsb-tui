"""Several sessions at once (design §2.3, D33, D41): one attachment, engine and canvas per session open here, only the one
in front painted, the others parsed, with unseen output marked on their chip.

Why it matters (Rule 9): an operator with an agent working in three of their sessions needs all three live, and the screen
in front to be the one they are looking at. A background session that painted would cost the typist's keystrokes their
latency; one that did not parse would show a stale screen on the switch; one that answered a terminal query through the
front session's attachment would put a reply into the wrong session (DT4 b) — or, as a viewer, take a keyboard it does
not hold. Servers: the contract's in-process fixture server (DT7) and the TUI's own stub for the slow-poll case.
"""

from __future__ import annotations

import asyncio
import re
import threading

from input_server import InputRecordingApp, other_client
from stub_server import SlowChunkedApp, StubServer, emit_live_output
from tsbc import _serve
from workspace_support import (
    click_chip,
    fixture_login,
    login,
    named_sessions,
    open_here,
    rail_row,
)

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.prompts import EndedScreen

LEADER = "ctrl+backslash"
CURSOR_REPORT = re.compile(rb"\x1b\[\d+;\d+R")
DSR = b"\x1b[6n"


def _chip(screen, sid: str) -> str:
    """The chip of a session as the rail shows it, or None."""
    return next(row.text for row in screen.rail.rows if row.arg == sid)


def test_each_open_session_has_its_own_attachment_engine_and_canvas(serve, wait_until) -> None:
    """Three sessions open here are three attachments to three sessions, three engines and three canvases, in the order
    they were opened; the last opened is in front, and the others are still attached (a client each)."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        ids = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02", "k8s-node-07")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, *ids)
            assert [slot.session_id for slot in screen.slots] == ids
            assert len({id(slot.pump) for slot in screen.slots}) == 3
            assert len({id(slot.canvas.backend) for slot in screen.slots}) == 3
            assert screen.front.session_id == ids[-1]
            assert [slot.canvas.in_front for slot in screen.slots] == [False, False, True]
            assert all(len(serve_app.clients[sid]) == 1 for sid in ids), "every session has this client attached"

    asyncio.run(_check())


def test_output_in_the_background_marks_the_chip_is_parsed_and_is_not_painted(serve, wait_until) -> None:
    """D33: a session out of front parses what arrives (so the switch shows it) and marks its chip `•` (mockup T1's
    chip 2), but paints nothing, so a firehose in the background cannot cost the front session's keystrokes a frame."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        back, front = named_sessions(url, serve_app, "infra-db-02", "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, back, front)
            screen.close_sidebar()
            slot = screen.slot_for(back)
            paints = slot.canvas.paint_count
            emit_live_output(serve_app, serve_app.sessions[back], b"output nobody is looking at")
            await wait_until(pilot, lambda: any("output nobody" in row for row in slot.canvas.backend.visible_rows()),
                             "the background grid to parse the output")
            await wait_until(pilot, lambda: _chip(screen, back).endswith("•"), "the chip to mark unseen output")
            await pilot.pause(0.2)
            assert slot.canvas.paint_count == paints, "a session out of front painted"
            assert not _chip(screen, front).endswith("•") and _chip(screen, front).endswith("◂")

    asyncio.run(_check())


def test_switching_paints_the_new_front_at_once_from_its_own_grid_and_clears_the_mark(
        serve, monkeypatch, wait_until, screen_text) -> None:
    """The switch must not wait for the paint timer: with one frame a second, what arrived in the background is on
    screen one refresh after the click, from the grid that already parsed it, and the `•` is gone because the operator
    has now looked."""
    monkeypatch.setenv("TSB_TUI_RENDER_MAX_FPS", "1")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        back, front = named_sessions(url, serve_app, "infra-db-02", "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, back, front)
            screen.close_sidebar()
            slot = screen.slot_for(back)
            emit_live_output(serve_app, serve_app.sessions[back], b"\x1b[2J\x1b[1;1Hwaiting for you")
            await wait_until(pilot, lambda: _chip(screen, back).endswith("•"), "the chip to mark the output")
            assert "waiting for you" not in screen_text(app)

            await click_chip(pilot, screen, back)
            await pilot.pause()
            assert screen.front is slot and slot.canvas.painted, "the grid was painted on the switch"
            await wait_until(pilot, lambda: "waiting for you" in screen_text(app), "the output on screen", timeout_s=0.9)
            assert _chip(screen, back).endswith("◂") and not slot.unseen

    asyncio.run(_check())


def test_a_slow_poll_never_delays_a_keystroke(wait_until) -> None:
    """D10, design §3.4 test 6 ("the poll never blocks typing"): the first `GET /sessions` is held open at the server
    and the keystroke still reaches the session at once, because the poll runs on a thread of its own. Released
    afterwards, the held reply is applied like any other."""

    async def _check() -> None:
        gate = threading.Event()
        stub = SlowChunkedApp(delay_s=0.02, max_chunks=400, tail_chunks=5, sessions_first_call_gate=gate)
        try:
            with StubServer(stub) as url:
                app = TsbTuiApp(url)
                async with app.run_test(size=(100, 30)) as pilot:
                    screen = await login(app, pilot)
                    await wait_until(pilot, lambda: stub.sessions_first_call_started.is_set(), "the poll to be held")
                    app.client.create_session("stub-target", "new", "xterm-256color", 80, 24)
                    sid = next(iter(stub.sessions))
                    app.gated_sessions.add(sid)
                    screen.open_session(stub.sessions[sid])
                    await wait_until(pilot, lambda: stub.chunks_sent > 0, "the session to attach and stream")
                    await asyncio.wait_for(pilot.press("x"), timeout=2.0)
                    await wait_until(pilot, lambda: b"x" in stub.client_frames, "the keystroke to reach the target")
                    assert not gate.is_set(), "the poll was still held while the keystroke went through"
                    gate.set()  # released inside the app: its held reply is applied, and quitting does not wait for it
                    await wait_until(pilot, lambda: screen.fleet.applied_generation >= 1, "the held reply to apply")
        finally:
            gate.set()

    asyncio.run(_check())


def test_a_background_session_answers_its_own_queries_only_when_this_client_holds_its_keyboard(
        serve, wait_until) -> None:
    """DT4 (b), per attachment: each session keeps its own state. A cursor-position query in a background session this
    client holds the keyboard of is answered to that session; the same query in a background session a peer types
    in is dropped, never sent (a viewer's reply would take the keyboard, terminal-size §5) and never sent through the
    session that happens to be in front."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        viewing, typing = named_sessions(url, serve_app, "infra-db-02", "rtr-core-01")
        with other_client(url, _serve.PEER, viewing):  # the peer types in the first session
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await fixture_login(app, pilot)
                await open_here(app, pilot, wait_until, viewing, typing)
                view_slot, type_slot = screen.slot_for(viewing), screen.slot_for(typing)
                assert not view_slot.pump.state.holds_keyboard and type_slot.pump.state.holds_keyboard

                # The session this client types in goes to the background; the one it only watches is in front.
                screen.show_front(view_slot)
                emit_live_output(serve_app, serve_app.sessions[typing], DSR)
                await wait_until(pilot, lambda: any(CURSOR_REPORT.fullmatch(d)
                                                    for d in serve_app.received(type_slot.pump.state.client_id)),
                                 "the background session this client types in to be answered")
                dropped = screen.dropped_inputs

                # And the other way: the session this client does not type in is the one in the background.
                screen.show_front(type_slot)
                emit_live_output(serve_app, serve_app.sessions[viewing], DSR)
                await wait_until(pilot, lambda: screen.dropped_inputs == dropped + 1,
                                 "the query of a session this client does not type in to be dropped")
                assert serve_app.received(view_slot.pump.state.client_id) == []
                assert len([d for d in serve_app.received(type_slot.pump.state.client_id)
                            if CURSOR_REPORT.fullmatch(d)]) == 1, "a reply went through the wrong session"

    asyncio.run(_check())


def test_the_leader_and_a_digit_n_p_and_s_switch_sessions_without_reaching_any(serve, wait_until) -> None:
    """Design §2.4: `^\\ 1`-`^\\ 9` go to that chip's session, `n` and `p` step through the ones open here, `s` gives
    the keys to the sidebar; none of it is typed into a session (a digit after the leader is the operator's)."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        ids = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02", "k8s-node-07")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, *ids)
            screen.close_sidebar()
            clients = [slot.pump.state.client_id for slot in screen.slots]

            await pilot.press(LEADER, "1")
            await wait_until(pilot, lambda: screen.front.session_id == ids[0], "^\\ 1")
            await pilot.press(LEADER, "n")
            await wait_until(pilot, lambda: screen.front.session_id == ids[1], "^\\ n")
            await pilot.press(LEADER, "p", LEADER, "p")
            await wait_until(pilot, lambda: screen.front.session_id == ids[2], "^\\ p twice wraps round")
            await pilot.press(LEADER, "2")
            await wait_until(pilot, lambda: screen.front.session_id == ids[1], "^\\ 2")
            await pilot.press(LEADER, "9")
            await wait_until(pilot, lambda: any("No session is numbered 9" in n.message
                                                for n in app._notifications._notifications.values()), "the notice")
            assert screen.front.session_id == ids[1]
            await pilot.press(LEADER, "s")
            await wait_until(pilot, lambda: screen.sidebar_open and screen._sidebar_has_keys(), "^\\ s")
            assert all(serve_app.received(client) == [] for client in clients), "a switch key was typed into a session"

    asyncio.run(_check())


def test_detach_brings_the_next_session_forward_and_the_last_one_opens_the_sidebar(serve, wait_until) -> None:
    """Detach leaves one session, which lives on; the session after it comes to the front, and with none left the
    workspace shows the sidebar (design §2.5: the sidebar is open when no session is)."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        ids = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02", "k8s-node-07")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, *ids)
            screen.close_sidebar()
            screen.show_front(screen.slot_for(ids[1]))
            await pilot.press(LEADER, "d")
            await wait_until(pilot, lambda: [s.session_id for s in screen.slots] == [ids[0], ids[2]], "one detach")
            assert screen.front.session_id == ids[2] and not screen.sidebar_open
            await wait_until(pilot, lambda: serve_app.sessions[ids[1]]["state"] == "detached", "the session to live on")
            for _ in range(2):
                await pilot.press(LEADER, "d")
            await wait_until(pilot, lambda: not screen.slots and screen.sidebar_open, "the sidebar to open")
            assert all(serve_app.sessions[sid]["state"] in ("detached", "live") for sid in ids)

    asyncio.run(_check())


def test_an_ended_session_keeps_its_chip_with_a_cross_until_the_operator_moves_on(
        serve, monkeypatch, wait_until) -> None:
    """D36: "its chip shows `✕` until you move on". Killed while in the background it marks its chip, and shows its end
    dialog when it is brought forward; moving to another session takes it off the rail. The poll is switched off so the
    cross can only come from the session's own close: sessions open here update at once, from their own frames."""
    monkeypatch.setenv("TSB_TUI_FLEET_VIEW_REFRESH_S", "0")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        doomed, other = named_sessions(url, serve_app, "infra-db-02", "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, doomed, other)
            screen.close_sidebar()
            with serve_app.lock:
                serve_app._kill(serve_app.sessions[doomed])
            await wait_until(pilot, lambda: _chip(screen, doomed).startswith("1✕"), "the cross on the chip")
            assert screen.front.session_id == other and app.screen is screen, "an end in the background shows no dialog"

            await click_chip(pilot, screen, doomed)
            await wait_until(pilot, lambda: isinstance(app.screen, EndedScreen), "the end dialog on the switch")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "the dialog to close")
            assert _chip(screen, doomed).startswith("1✕")

            await click_chip(pilot, screen, other)
            await wait_until(pilot, lambda: screen.slot_for(doomed) is None, "the ended session to leave the rail")
            assert [row.arg for row in screen.rail.rows if row.action == "chip"] == [other]

    asyncio.run(_check())


def test_a_chip_for_a_session_held_by_another_login_attaches_after_the_gate(serve, wait_until) -> None:
    """D41: "your sessions that another login types in" have a chip although this TUI is not attached; a click brings
    it to the front, attaching first, and the gate comes before the attach (session-attach CR1)."""

    async def _check() -> None:
        from tsb_tui.screens.attach_confirm import AttachConfirmScreen

        serve_app, url = serve(InputRecordingApp())
        (held,) = named_sessions(url, serve_app, "k8s-node-07")
        with other_client(url, _serve.PEER, held):
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await fixture_login(app, pilot)
                screen.close_sidebar()
                await wait_until(pilot, lambda: rail_row(screen, "chip", held) is not None, "its chip")
                await click_chip(pilot, screen, held)
                await wait_until(pilot, lambda: isinstance(app.screen, AttachConfirmScreen), "the gate")
                assert len(serve_app.clients[held]) == 1, "nothing attached before the gate was passed"
                await pilot.click("#attach-button")
                await wait_until(pilot, lambda: screen.front is not None and screen.front.session_id == held
                                 and screen.front.pump is not None and screen.front.pump.state.client_id,
                                 "the session to attach")
                assert len(serve_app.clients[held]) == 2

    asyncio.run(_check())

