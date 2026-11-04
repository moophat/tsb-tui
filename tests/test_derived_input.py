"""Input nobody typed: answers to queries, focus reports and canvas mouse reports (DT4 b).

Why these matter (Rule 9): a status-report reply, a focus report and a mouse report are bytes the
core counts as input like any others (session model §9 E1: any binary frame from the keyboard
holder), and the core moves the keyboard for an owner's input without asking what it was (typing-
ownership step 8; terminal-size §5 "by typing"). Three real bugs hide in sending them carelessly:

* **An owner who is only watching takes the keyboard.** The owner's client answers a program's
  `ESC [ 6 n` for the holder's session; the core reads the reply as the owner typing and hands the
  owner the keyboard, mid-command, away from the agent or contractor who held it.
* **A hand-off is undone at once.** The holder passes the keyboard to someone, and before the `token`
  frame comes back, a query reply or a focus report leaves from the old holder: the core, which
  applies one client's frames in order, takes it as the owner typing and moves the keyboard straight
  back.
* **A false quiet prompt.** Every such byte is E1, so a focus report sent on a schedule keeps a
  session that is waiting on a person looking busy, or arms a prompt nobody earned (§10, §11).

So derived input is never under `input.guard` (the harness profile turns the guard off to test the
core), and goes out only while this client holds the keyboard, has no hand-off of its own in flight,
no stale choice is pending and no dump is being applied. It is dropped otherwise, never held.

Servers: the contract's in-process fixture server, recording what each client sends and able to hold
a `token` frame behind a gate (`input_server.py`); frames the TUI sends are what is asserted.
"""

from __future__ import annotations

import asyncio
import re
import threading

import pytest
from input_server import (
    InputRecordingApp,
    attached,
    flushed,
    make_session,
    other_client,
)
from stub_server import create_owner_session
from textual import events
from textual.screen import ModalScreen
from textual.widgets import Label
from tsbc import _serve

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.terminal import KIND_INPUT_DROPPED
from tsb_tui.widgets.canvas import Canvas

MOUSE_ON = b"\x1b[?1000h\x1b[?1006h"  # normal tracking, SGR coordinates
FOCUS_ON = b"\x1b[?1004h"
DSR = b"\x1b[6n"  # "report the cursor position"
DA1 = b"\x1b[c"  # "what are you?"
CURSOR_REPORT = re.compile(rb"\x1b\[\d+;\d+R")


def _reasons(screen) -> list[str]:
    return [e.detail["reason"] for e in screen.pump.state.log.of_kind(KIND_INPUT_DROPPED)]


def test_the_keyboard_holder_answers_a_session_query(serve, wait_until) -> None:
    """The control for every other test here: the reply path is alive. A program that asks where the
    cursor is, and what the terminal is, is answered by the holder, or the program stalls waiting
    for a reply that never comes (a full-screen editor does this on start-up)."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            assert screen.pump.state.holds_keyboard
            client_id = screen.pump.state.client_id

            screen.canvas.feed(DSR + DA1)
            await wait_until(pilot, lambda: len(serve_app.received(client_id)) == 2, "both replies")
            cursor, attributes = serve_app.received(client_id)
            assert CURSOR_REPORT.fullmatch(cursor), cursor
            assert attributes.startswith(b"\x1b[?") and attributes.endswith(b"c"), attributes

    asyncio.run(_check())


@pytest.mark.parametrize("guard", ["true", "false"])
def test_an_owner_who_is_only_watching_answers_nothing_and_takes_nothing(
        guard, serve, monkeypatch, wait_until) -> None:
    """The bug: an owner's client that answers queries, reports focus or clicks while another client
    holds the keyboard takes the keyboard (steps 8, 10) without the owner typing a thing. None of
    those is under `input.guard`: with the guard off (a rig's `--set input.guard=false`), the owner's
    typed keys go out (and take the keyboard, which is the brake), but the derived input still does
    not. Afterwards the owner types, holds the keyboard, and now its replies go out."""
    monkeypatch.setenv("TSB_TUI_INPUT_GUARD", guard)
    monkeypatch.setenv("TSB_TUI_INPUT_FOCUS_REPORTS", "true")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        session_id = make_session(url)
        with other_client(url, _serve.PEER, session_id):
            app = TsbTuiApp(url)
            async with app.run_test(size=(80, 24)) as pilot:
                app.client.login(_serve.USER, _serve.PASSWORD)
                screen = await attached(pilot, app, session_id, wait_until)
                state = screen.pump.state
                assert not state.holds_keyboard and state.is_owner_client
                client_id = state.client_id

                screen.canvas.feed(MOUSE_ON + FOCUS_ON + DSR + DA1)
                app.post_message(events.AppBlur())
                await pilot.click(Canvas, offset=(10, 5))
                await wait_until(pilot, lambda: screen.dropped_inputs >= 5,
                                 "the query replies, the focus report and the click to be dropped")
                await flushed(pilot, screen, serve_app, wait_until)

                assert serve_app.received(client_id) == []
                assert not state.holds_keyboard and not state.token_history, "the keyboard moved"
                assert set(_reasons(screen)) == {"not_holder"}

                await pilot.press("x")
                await wait_until(pilot, lambda: state.holds_keyboard, "the owner's typing to take the keyboard")
                screen.canvas.feed(DSR)
                await wait_until(pilot, lambda: len(serve_app.received(client_id)) == 2,
                                 "the key and then the reply, now that the keyboard is ours")
                key, reply = serve_app.received(client_id)
                assert key == b"x" and CURSOR_REPORT.fullmatch(reply)

    asyncio.run(_check())


def test_a_hand_off_in_flight_stops_derived_input_until_the_token_frame_comes_back(
        serve, monkeypatch, wait_until) -> None:
    """The bug: the holder (the owner) hands the keyboard to a peer; the `token` frame is on its way;
    a reply to a query, or a focus report, leaves from this client in that window and the core, which
    applies the owner's frames in order, reads it as the owner typing and gives the keyboard straight
    back (typing-ownership steps 5, 8, 10). The window is held open with a gate on the server's
    `token` frame; inside it nothing leaves (checked on this client's own socket, because the server
    is not reading yet), and after the frame, as a viewer, still nothing (checked at the server)."""
    monkeypatch.setenv("TSB_TUI_INPUT_FOCUS_REPORTS", "true")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        gate = threading.Event()
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            state = screen.pump.state
            client_id = state.client_id
            assert state.holds_keyboard and state.is_owner_client
            with other_client(url, _serve.PEER, session_id) as peer:
                await wait_until(pilot, lambda: any(c["client_id"] == peer.client_id for c in state.clients),
                                 "the peer to be listed")
                sent_on_socket: list[bytes] = []
                attachment = screen.pump.attachment
                send = attachment.send

                async def spy(data: bytes) -> None:
                    sent_on_socket.append(data)
                    await send(data)

                attachment.send = spy
                serve_app.hold_token = gate
                try:
                    await screen.pump.handoff(peer.client_id)
                    assert state.handoff_in_flight
                    await wait_until(pilot, serve_app.token_held.is_set, "the server to hold the token frame")

                    screen.canvas.feed(FOCUS_ON + DSR + DA1)
                    app.post_message(events.AppBlur())
                    await wait_until(pilot, lambda: screen.dropped_inputs == 3,
                                     "two replies and a focus report to be dropped")
                    assert state.handoff_in_flight and state.holds_keyboard, "the token frame is still held"
                    assert sent_on_socket == []
                    assert _reasons(screen) == ["handoff_in_flight"] * 3
                finally:
                    gate.set()

                await wait_until(pilot, lambda: not state.handoff_in_flight
                                 and state.holder_client_id == peer.client_id, "the token frame")
                screen.canvas.feed(DSR)
                app.post_message(events.AppFocus())
                await wait_until(pilot, lambda: screen.dropped_inputs == 5, "both to be dropped again")
                await flushed(pilot, screen, serve_app, wait_until)

                assert serve_app.received(client_id) == [], "something left this client after the hand-off"
                assert _reasons(screen)[3:] == ["not_holder"] * 2
                assert state.holder_client_id == peer.client_id, "the keyboard was taken back"
                assert [t["reason"] for t in state.token_history] == ["handoff"]

    asyncio.run(_check())


def test_focus_reports_are_off_by_default(serve, wait_until) -> None:
    """A focus report is input and counts as E1 (session model §9), so a session that turned on
    focus reporting (mode 1004, as vim and tmux do) must not hear from a client that is merely
    switched away from: that would read as the person being there, and could arm a quiet prompt.
    `input.focus_reports` is off unless the operator turns it on."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            client_id = screen.pump.state.client_id

            screen.canvas.feed(FOCUS_ON)
            app.post_message(events.AppBlur())
            app.post_message(events.AppFocus())
            await wait_until(pilot, lambda: app.app_focus, "the app to be told it has focus again")
            await pilot.press("z")
            await wait_until(pilot, lambda: len(serve_app.received(client_id)) >= 1, "the marker key")
            assert serve_app.received(client_id) == [b"z"]
            assert screen.dropped_inputs == 0, "off means not even attempted"

    asyncio.run(_check())


class _Dialog(ModalScreen[None]):
    def compose(self):
        yield Label("a dialog")


def test_with_focus_reports_on_only_the_outer_terminals_focus_sends_one(
        serve, monkeypatch, wait_until) -> None:
    """What "focus" means. The session's mode 1004 is about the *terminal
    window* gaining or losing focus; a widget or a dialog taking focus inside this TUI is not that,
    and reporting it would tell a program the person left when they only opened a menu. Only
    Textual's own `app_focus`, driven by the outer terminal's focus-in and focus-out, sends. And a
    session that did not turn the mode on hears nothing even then."""
    monkeypatch.setenv("TSB_TUI_INPUT_FOCUS_REPORTS", "true")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            client_id = screen.pump.state.client_id

            # Mode 1004 not set yet: a real focus change is still nothing to report.
            app.post_message(events.AppBlur())
            await wait_until(pilot, lambda: not app.app_focus, "the blur")
            app.post_message(events.AppFocus())
            await wait_until(pilot, lambda: app.app_focus, "the focus")

            screen.canvas.feed(FOCUS_ON)
            app.push_screen(_Dialog())
            await pilot.pause()
            app.pop_screen()
            await pilot.pause()
            assert app.screen is screen

            app.post_message(events.AppBlur())
            await wait_until(pilot, lambda: serve_app.received(client_id) == [b"\x1b[O"], "focus lost")
            app.post_message(events.AppFocus())
            await wait_until(pilot, lambda: serve_app.received(client_id) == [b"\x1b[O", b"\x1b[I"],
                             "focus gained")

    asyncio.run(_check())


def test_a_stale_choice_pending_stops_replies(serve, wait_until) -> None:
    """Stale-screen: while a choice is pending the grid may be wrong, and the core refuses every
    client's input until one is chosen (`input_refused` `stale`). A reply built from a grid that may
    be wrong is wrong too (a cursor position, most of all), so none is sent; the typed key after it
    is, because typing is the person's call (the core refuses it, and says why)."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, "stale-pending", 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            state = screen.pump.state
            await wait_until(pilot, lambda: state.stale_state == "pending", "the stale announcement")
            assert state.holds_keyboard

            screen.canvas.feed(DSR)
            await wait_until(pilot, lambda: screen.dropped_inputs == 1, "the reply to be dropped")
            assert _reasons(screen) == ["stale_pending"]
            await pilot.press("k")
            client_id = state.client_id
            await wait_until(pilot, lambda: serve_app.received(client_id) == [b"k"],
                             "the typed key, and only it")

    asyncio.run(_check())


def test_a_stale_choice_made_while_detached_leaves_the_reattached_client_able_to_answer_queries(
        serve, wait_until) -> None:
    """The reattach defect (session-attach step 16, stale-screen step 5): a client attached while a stale
    choice was pending, dropped, and came back after someone else had chosen. The core tells a reattaching
    client only a choice that is still pending, so no `stale` frame follows, and a `pending` kept from the
    first attachment would block this client's replies for good (DT4 b) on a screen that is fine. After the
    reattach its answer to a session query goes out."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, "stale-pending", 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            state = screen.pump.state
            await wait_until(pilot, lambda: state.stale_state == "pending", "the stale announcement")
            first_client = state.client_id

            sess = serve_app.sessions[session_id]
            with serve_app.lock:
                sess["stale"] = None  # someone chose while this client was gone
                sess["stale_wanted"] = False
                serve_app._drop_client(sess, first_client, code=4011, reason="")
            await wait_until(pilot, lambda: screen.pump.attach_attempts == 2 and state.client_id != first_client
                             and state.dump_seen, "the client to reattach")
            assert state.stale_state is None, "the pending choice of the dropped attachment survived"

            screen.canvas.feed(DSR)
            await wait_until(pilot, lambda: any(CURSOR_REPORT.fullmatch(b)
                                                for b in serve_app.received(state.client_id)),
                             "the answer to a query after the reattach")
            assert screen.dropped_inputs == 0

    asyncio.run(_check())


def test_canvas_mouse_goes_to_the_session_only_when_it_asked_and_shift_keeps_it(
        serve, wait_until) -> None:
    """D13. A click on the canvas is reported to a session that turned mouse reporting on (a pager, an
    editor, a router's menu), in the *session's* cells, and to nothing else: a shell would print the
    escape sequence as text. Shift keeps the mouse for the operator's own terminal (text selection),
    so a shifted click is never reported. The wheel is buttons 4 and 5 of the same protocol."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            client_id = screen.pump.state.client_id

            await pilot.click(Canvas, offset=(10, 5))  # no mouse mode: nothing
            screen.canvas.feed(MOUSE_ON)
            await pilot.click(Canvas, offset=(10, 5), shift=True)  # shifted: nothing
            await pilot.click(Canvas, offset=(10, 5))
            screen.canvas.post_message(events.MouseScrollDown(
                None, 2, 3, 0, 1, 0, False, False, False))
            await wait_until(pilot, lambda: len(serve_app.received(client_id)) >= 3, "the reports")
            await flushed(pilot, screen, serve_app, wait_until)
            assert serve_app.received(client_id) == [
                b"\x1b[<0;11;6M", b"\x1b[<0;11;6m", b"\x1b[<65;3;4M"]

    asyncio.run(_check())


def test_a_click_is_reported_in_the_sessions_cells_not_the_windows(
        serve, monkeypatch, wait_until) -> None:
    """D3, D4. The grid is the session's (132 columns here) and the window pans over it. A click 2
    cells into a window panned 40 columns over is column 42 of the session, and the program that asked
    for the mouse must be told 42, or every click in a wide session lands in the wrong place."""
    monkeypatch.setenv("TSB_TUI_VIEW_FOLLOW_CURSOR", "off")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 132, 40)
            screen = await attached(pilot, app, session_id, wait_until)
            client_id = screen.pump.state.client_id
            screen.canvas.feed(MOUSE_ON)
            screen.canvas.pan_to(40, 0)
            assert screen.canvas.view_x == 40

            await pilot.click(Canvas, offset=(2, 1))
            await wait_until(pilot, lambda: len(serve_app.received(client_id)) >= 2, "the click")
            assert serve_app.received(client_id) == [b"\x1b[<0;43;2M", b"\x1b[<0;43;2m"]

    asyncio.run(_check())


def test_a_release_outside_the_canvas_still_reaches_the_session(serve, wait_until) -> None:
    """A drag that ends outside the canvas (over the footer here) must still end: a program tracking
    buttons that never sees the release believes one is held, and treats every later move as a drag."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            client_id = screen.pump.state.client_id
            screen.canvas.feed(MOUSE_ON)
            assert screen.canvas.size.height < 24, "the footer row is outside the canvas"

            await pilot.mouse_down(Canvas, offset=(10, 5))
            await pilot.mouse_up(offset=(10, 23))
            await wait_until(pilot, lambda: len(serve_app.received(client_id)) >= 2, "press and release")
            press, release = serve_app.received(client_id)
            assert press == b"\x1b[<0;11;6M" and release.endswith(b"m")

    asyncio.run(_check())


def test_mouse_enabled_off_keeps_the_canvas_mouse_from_the_session(
        serve, monkeypatch, wait_until) -> None:
    """D13: `mouse.enabled` switches the mouse off. A session that asked for mouse reporting gets
    none of it, and the typed key after the click is the proof that the click was settled first."""
    monkeypatch.setenv("TSB_TUI_MOUSE_ENABLED", "false")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            client_id = screen.pump.state.client_id
            screen.canvas.feed(MOUSE_ON)
            await pilot.click(Canvas, offset=(10, 5))
            await pilot.press("z")
            await wait_until(pilot, lambda: serve_app.received(client_id) == [b"z"], "the marker key")

    asyncio.run(_check())
