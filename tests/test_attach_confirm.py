"""The recording and host-key acknowledgement gate, the `no_resolving_credential` wording, and
re-attach from the sidebar passing through the same gate a fresh attach does.

Why these matter (Rule 9): open_decisions §5 (LOCKED) requires a recording warning before the first
attach and anything typed, and the host-key fingerprint is the operator's one chance to catch a target
that is not who it claims to be on first use — a UI that lets Attach through before the host key is
ticked, or that never shows the recording text, defeats the point of requiring them at all. `no_resolving_credential` is a legitimate, expected state (a target visible
but with no credential resolving for this principal) and must never read in the same alarmed tone
as a real failure.
"""

from __future__ import annotations

import asyncio

from stub_server import HOSTKEY, RECORDING, SlowChunkedApp, StubServer
from workspace_support import login, open_new_session, pick_in_sidebar

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.attach_confirm import AttachConfirmScreen
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.screens.terminal import TerminalScreen

TARGET_ID = "stub-target"


async def _login_and_open_new_session(app: TsbTuiApp, pilot) -> None:
    await login(app, pilot)
    assert isinstance(await open_new_session(app, pilot), NewSessionScreen)


def test_the_host_key_tick_and_the_recording_text_come_before_the_first_attach() -> None:
    """Session-open step 22 and session-attach CR1: a first-use host key needs the operator's tick before
    the attach, and the recording text must be shown exactly as the record carries it, before anything is
    attached or typed. The recording text is a warning, so nothing is ticked for it (G2); Attach stays
    greyed only for the host key, and until then no socket has been opened."""

    async def _check() -> None:
        stub = SlowChunkedApp(hostkey_on_create=True, recording_on_create=True)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _login_and_open_new_session(app, pilot)
                await pilot.click("#create-button")
                await pilot.pause()
                assert isinstance(app.screen, AttachConfirmScreen)
                text = str(app.screen.query_one("#attach-recording-warning").render())
                assert RECORDING["warning_text"] in text and text.startswith("● REC")
                assert HOSTKEY["fingerprint"] in str(app.screen.query_one("#attach-hostkey-notice").render())
                attach_button = app.screen.query_one("#attach-button")
                assert attach_button.disabled, "the host key is unticked: Attach must stay disabled"
                assert not app.screen.query("#recording-ack"), "the recording text is shown, not ticked"
                await pilot.click("#attach-button")
                await pilot.pause()
                assert isinstance(app.screen, AttachConfirmScreen) and stub.attaches == 0

                await pilot.press("space")  # the tick is Space, as the gate says
                await pilot.pause()
                assert not attach_button.disabled
                await pilot.click("#attach-button")
                await pilot.pause()
                assert isinstance(app.screen, TerminalScreen) and len(app.screen.slots) == 1

    asyncio.run(_check())


def test_a_recording_alone_does_not_hold_attach_back_and_neither_does_nothing() -> None:
    async def _check(recording: bool) -> None:
        stub = SlowChunkedApp(hostkey_on_create=False, recording_on_create=recording)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _login_and_open_new_session(app, pilot)
                await pilot.click("#create-button")
                await pilot.pause()
                assert isinstance(app.screen, AttachConfirmScreen)
                assert not app.screen.query_one("#attach-button").disabled
                assert bool(app.screen.query("#attach-recording-warning")) is recording

    asyncio.run(_check(True))
    asyncio.run(_check(False))


async def _first_attach_then_detach(app: TsbTuiApp, pilot) -> str:
    await _login_and_open_new_session(app, pilot)
    await pilot.click("#create-button")
    await pilot.pause()
    await pilot.click("#attach-button")
    await pilot.pause()
    assert isinstance(app.screen, TerminalScreen)
    session_id = app.screen.session_id
    app.screen.action_detach()
    await pilot.pause()
    # With nothing open the workspace shows the sidebar (design §2.5), where the session is listed to come back to.
    assert isinstance(app.screen, TerminalScreen) and not app.screen.slots and app.screen.sidebar_open
    return session_id


def test_a_later_attach_in_the_same_run_shows_the_recording_text_in_the_status_row_not_the_gate(
        wait_until) -> None:
    """D24: the gate is for the first attach to a session in this run; a later one skips it and says what is
    recorded in the status row for `gates.recording_bar_s`, then lets go of the row. REC is always shown."""

    async def _check() -> None:
        stub = SlowChunkedApp(recording_on_create=True, delay_s=0.05)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test(size=(140, 30)) as pilot:
                clock = [1000.0]
                app.wall_clock = lambda: clock[0]
                session_id = await _first_attach_then_detach(app, pilot)
                await pick_in_sidebar(app, pilot, wait_until, session_id)
                screen = app.screen
                await wait_until(pilot, lambda: len(screen.slots) == 1, "the session to open")
                await wait_until(pilot, lambda: screen.pump is not None and screen.pump.state.client_id,
                                 "the attach")
                await wait_until(pilot, lambda: RECORDING["warning_text"] in screen.status.text,
                                 "the recording text in the row")
                assert "‹ you type ›" in screen.status.text and "F1 Menu" in screen.status.text
                clock[0] += 6  # past `gates.recording_bar_s` (5)
                screen._tick()
                await wait_until(pilot, lambda: RECORDING["warning_text"] not in screen.status.text,
                                 "the text to let go of the row")
                assert "● REC" in screen.status.text

    asyncio.run(_check())


def test_every_attach_shows_the_gate_each_time_when_asked(monkeypatch, wait_until) -> None:
    monkeypatch.setenv("TSB_TUI_GATES_RECORDING", "every-attach")

    async def _check() -> None:
        stub = SlowChunkedApp(recording_on_create=True)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                session_id = await _first_attach_then_detach(app, pilot)
                await pick_in_sidebar(app, pilot, wait_until, session_id)
                await wait_until(pilot, lambda: isinstance(app.screen, AttachConfirmScreen), "the gate")

    asyncio.run(_check())


def test_a_session_not_yet_gated_in_this_run_gets_the_gate_on_re_attach(wait_until) -> None:
    """The gate is per session per run: a session first seen in the list (not created here) is gated too."""

    async def _check() -> None:
        stub = SlowChunkedApp(recording_on_create=True)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                session_id = await _first_attach_then_detach(app, pilot)
                app.gated_sessions.clear()  # as if this TUI run had never attached to it
                await pick_in_sidebar(app, pilot, wait_until, session_id)
                await wait_until(pilot, lambda: isinstance(app.screen, AttachConfirmScreen), "the gate")

    asyncio.run(_check())


def test_no_resolving_credential_reads_as_a_state_not_an_error() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp(
            create_error=(409, "no_resolving_credential", "no credential resolves for this target"))
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _login_and_open_new_session(app, pilot)
                await pilot.click("#create-button")
                await pilot.pause()
                assert isinstance(app.screen, NewSessionScreen)
                messages = [n.message for n in app._notifications._notifications.values()]
                assert "This target is visible, but no credential resolves for it." in messages
                assert not any("HTTP" in m for m in messages)

    asyncio.run(_check())
