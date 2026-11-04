"""Size: the one place the operator asks the target for a size (design F11, D19; size-control; terminal-size §3-§4).

Why these matter (Rule 9): the session's size belongs to the session (D3), and a request changes it for
everyone attached. So the dialog must (a) only let the typist ask, because the core answers anyone
else `not_typist` and changes nothing (typing-ownership step 14); (b) say that it is a request that may
come back different, never "set my size"; (c) show what came back and why, including a clamp, and not
ask again until the window changes (terminal-size §3); and (d) never ask by merely opening, which is how
a sidebar once resized a shared session (#176; D44). Servers: the contract's in-process fixture server,
whose target bounds are 40x12 to 200x60 (C2 decision 8), recording each client's frames (DT7).
"""

from __future__ import annotations

import asyncio

from input_server import (
    MARKER_SIZE,
    InputRecordingApp,
    attached,
    flushed,
    make_session,
    other_client,
)
from stub_server import create_owner_session, resize_requests
from textual.widgets import Button, Static
from tsbc import _serve

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.size_dialog import REASON_TEXT, SizeDialog, describe_answer

LEADER = "ctrl+backslash"


async def _open_size(pilot, app, wait_until) -> SizeDialog:
    await pilot.press(LEADER, "r")
    await wait_until(pilot, lambda: isinstance(app.screen, SizeDialog), "Size to open")
    dialog = app.screen
    await wait_until(pilot, lambda: dialog.query("#ask"), "Size to compose")
    return dialog


def _text(dialog: SizeDialog, widget_id: str) -> str:
    return str(dialog.query_one(widget_id, Static).render())


def test_the_typist_asks_for_the_window_minus_the_chrome_and_the_answer_shows_with_its_reason(
        serve, wait_until) -> None:
    """Size-control step 1 and surface rules: the holder's explicit request carries the window minus the
    rail and the status row, labelled with the size it asks for, as a request. Opening the dialog sends
    nothing (D44); the request goes out only on the click; and its answer comes back through the pump
    (DT17) and shows as "Last answer: applied", with what was asked and what the target is now."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            state = screen.pump.state
            dialog = await _open_size(pilot, app, wait_until)
            assert dialog.query_one("#size-panel").styles.border_top[1].hex.lower() == app.cfg["theme.frame"]  # fails today: $primary is the accent

            ask = dialog.query_one("#ask", Button)
            assert str(ask.label) == "Ask for 116×29", "the window minus the rail (4) and the status row (1)"
            assert not ask.disabled
            assert "may answer differently" in _text(dialog, "#ask-why"), "a request, not a command"
            assert "Target 80×24" in _text(dialog, "#size-title")
            assert "this window shows 116×29" in _text(dialog, "#size-title"), "the rail takes four of the 120"
            assert _text(dialog, "#size-last") == "Last answer: none since you attached"
            await flushed(pilot, screen, serve_app, wait_until)
            assert resize_requests(serve_app) == [MARKER_SIZE], "opening the dialog asked for nothing"

            await pilot.click("#ask")
            await wait_until(pilot, lambda: state.last_size_answer is not None, "the answer")
            assert resize_requests(serve_app) == [MARKER_SIZE, (116, 29)]
            assert (state.size_cols, state.size_rows) == (116, 29)
            assert state.last_size_answer["reason"] == "applied"
            await wait_until(pilot, lambda: "applied" in _text(dialog, "#size-last"), "the dialog to show it")
            assert _text(dialog, "#size-last") == "Last answer: applied · asked 116×29, the target is 116×29"

    asyncio.run(_check())


def test_only_the_typist_can_ask_and_the_button_says_who_types(serve, wait_until) -> None:
    """Typing-ownership step 14, size-control rule 2: a client without the keyboard is answered
    `not_typist` and nothing changes, so its button is grey, says who types now, and its `r` key sends
    nothing. This is both a view-only principal and an owner whose agent holds the keyboard."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        session_id = make_session(url)
        with other_client(url, _serve.PEER, session_id):
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 30)) as pilot:
                app.client.login(_serve.VIEWER, _serve.PASSWORD)
                screen = await attached(pilot, app, session_id, wait_until)
                dialog = await _open_size(pilot, app, wait_until)
                ask = dialog.query_one("#ask", Button)
                assert ask.disabled
                assert _text(dialog, "#ask-why") == "only the typist can ask; fixture-peer types now"

                await pilot.press("r")
                await wait_until(pilot, lambda: "Not sent" in _text(dialog, "#size-note"),
                                 "the dialog to say it sent nothing")
                await flushed(pilot, screen, serve_app, wait_until)
                assert resize_requests(serve_app) == [MARKER_SIZE], "a non-typist's request went out"

    asyncio.run(_check())


def test_a_clamped_answer_shows_its_reason_and_waits_for_the_window_to_change(
        serve, monkeypatch, wait_until) -> None:
    """Terminal-size §3, size-control surface rules: a request below the target's floor (40x12) is answered
    `clamped_floor` with the floor, the dialog shows that reason, and the button stays grey until the
    operator's own window changes: asking again would only be clamped again. A window change
    (the pilot resizes the terminal) opens it."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(44, 12)) as pilot:  # asks for 40x11: the target's rows floor is 12
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            state = screen.pump.state
            dialog = await _open_size(pilot, app, wait_until)
            assert str(dialog.query_one("#ask", Button).label) == "Ask for 40×11"

            await pilot.click("#ask")
            await wait_until(pilot, lambda: state.last_size_answer is not None, "the answer")
            assert state.last_size_answer["reason"] == "clamped_floor"
            assert (state.size_cols, state.size_rows) == (40, 12), "the target's own floor"
            await wait_until(pilot, lambda: "clamped" in _text(dialog, "#size-last"), "the reason to show")
            assert _text(dialog, "#size-last") == (
                "Last answer: clamped: the target's smallest size · asked 40×11, the target is 40×12")
            ask = dialog.query_one("#ask", Button)
            await wait_until(pilot, lambda: ask.disabled, "the button to grey")
            assert "ask again after the window changes" in _text(dialog, "#ask-why")

            await pilot.press("r")
            await wait_until(pilot, lambda: "Not sent" in _text(dialog, "#size-note"), "the refusal")
            # A byte sent now is read after every earlier frame of this client. (Not `flushed`: its marker
            # is itself a request, and its own `applied` answer would end the clamp.)
            await screen.send_input("key", b"x")
            await wait_until(pilot, lambda: serve_app.received(state.client_id) == [b"x"], "the marker byte")
            assert resize_requests(serve_app) == [(40, 11)], "asked again after a clamp"
            assert state.size_final, "nothing but a window change ends a clamp"

            await pilot.resize_terminal(60, 20)
            await wait_until(pilot, lambda: not ask.disabled, "the button to open after a window change")
            assert str(ask.label) == "Ask for 56×19"

    asyncio.run(_check())


def test_the_auto_toggle_switches_automatic_requests_for_this_run_and_asks_for_nothing(
        serve, monkeypatch, wait_until) -> None:
    """D18, D19, terminal-size §4: the toggle turns the debounced request on a window change on or off
    for this run. Switching it on asks for nothing by itself (a token or a dialog never asks); the next
    window change then asks once, for the new window. Off again, a window change asks for nothing. The
    leader's `R` flips the same switch."""
    monkeypatch.setenv("TSB_TUI_SIZE_DEBOUNCE_MS", "60")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            dialog = await _open_size(pilot, app, wait_until)
            auto = dialog.query_one("#auto", Button)
            assert str(auto.label) == "Auto size: off" and app.cfg["size.auto_request"] is False

            await pilot.click("#auto")
            await wait_until(pilot, lambda: str(auto.label) == "Auto size: on", "the toggle to flip")
            assert app.cfg["size.auto_request"] is True
            await flushed(pilot, screen, serve_app, wait_until)
            assert resize_requests(serve_app) == [MARKER_SIZE], "switching it on asked for something"

            await pilot.resize_terminal(100, 30)
            await wait_until(pilot, lambda: (96, 29) in resize_requests(serve_app),
                             "the window change to ask for the new window")

            await pilot.press("R")  # inside the dialog, the same key flips it back
            await wait_until(pilot, lambda: str(auto.label) == "Auto size: off", "R to flip it off")
            before = len(resize_requests(serve_app))
            await pilot.resize_terminal(90, 30)
            await flushed(pilot, screen, serve_app, wait_until)
            assert resize_requests(serve_app)[before:] == [MARKER_SIZE], "an off switch still asked"

    asyncio.run(_check())


def test_the_leader_r_flips_auto_size_from_the_terminal_and_says_so(serve, wait_until) -> None:
    """Design §2.4 (`R`): auto size on or off without opening a dialog, and a toast tells the operator
    which way it went (a silent toggle of something that resizes a shared session would be a trap)."""

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            await attached(pilot, app, session_id, wait_until)
            await pilot.press(LEADER, "R")
            await wait_until(pilot, lambda: app.cfg["size.auto_request"] is True, "the switch to flip on")
            messages = [n.message for n in app._notifications._notifications.values()]
            assert "Auto size is on." in messages

    asyncio.run(_check())


def test_every_known_reason_has_words_and_an_unknown_one_reads_by_its_sizes() -> None:
    """Size-control surface rules, conventions V5: a reason this client does not know is shown with the
    size in effect and never hides it, so a newer core adding a reason leaves the dialog truthful."""
    for reason in ("applied", "clamped_floor", "clamped_ceiling", "pinned", "not_resizable", "not_typist"):
        assert reason in REASON_TEXT
        text = describe_answer({"reason": reason, "asked": (10, 5), "cols": 80, "rows": 24})
        assert "asked 10×5" in text and "the target is 80×24" in text
    assert describe_answer({"reason": "below_threshold", "cols": 80, "rows": 24}) == (
        "Last answer: reason below_threshold · the target is 80×24")
    assert describe_answer(None) == "Last answer: none since you attached"
