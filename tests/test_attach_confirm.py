"""The recording and host-key acknowledgement gate, the `no_resolving_credential` wording, and
re-attach from Sessions passing through the same gate a fresh attach does.

Why these matter (Rule 9): open_decisions §5 (LOCKED) requires a recording warning before every
attach, and the host-key fingerprint is the operator's one chance to catch a target that is not
who it claims to be on first use — a UI that lets Attach through before either is acknowledged, or
that only ever shows them once at creation and never again on re-attach, defeats the point of
requiring them at all. `no_resolving_credential` is a legitimate, expected state (a target visible
but with no credential resolving for this principal) and must never read in the same alarmed tone
as a real failure.
"""

from __future__ import annotations

import asyncio

from stub_server import SlowChunkedApp, StubServer
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.attach_confirm import AttachConfirmScreen
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.screens.sessions import SessionsScreen
from tsb_tui.screens.terminal import TerminalScreen

TARGET_ID = "stub-target"


async def _login_and_open_new_session(app: TsbTuiApp, pilot) -> None:
    screen = app.screen
    screen.query_one("#username").value = "fixture-user"
    screen.query_one("#password").value = "fixture-password"
    await pilot.click("#login-button")
    await pilot.pause()
    assert isinstance(app.screen, NewSessionScreen)


def test_host_key_and_recording_both_gate_attach_until_both_are_acknowledged() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp(hostkey_on_create=True, recording_on_create=True)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _login_and_open_new_session(app, pilot)
                await pilot.click("#create-button")
                await pilot.pause()
                assert isinstance(app.screen, AttachConfirmScreen)

                attach_button = app.screen.query_one("#attach-button")
                assert attach_button.disabled, "both acks are unticked — Attach must stay disabled"

                await pilot.click("#recording-ack")
                await pilot.pause()
                assert attach_button.disabled, "only one of two required acks is ticked"

                await pilot.click("#hostkey-ack")
                await pilot.pause()
                assert not attach_button.disabled, "both acks are ticked — Attach may proceed"

                await pilot.click("#attach-button")
                await pilot.pause()
                assert isinstance(app.screen, TerminalScreen)

    asyncio.run(_check())


def test_no_recording_and_no_hostkey_leaves_attach_enabled_immediately() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp(hostkey_on_create=False, recording_on_create=False)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _login_and_open_new_session(app, pilot)
                await pilot.click("#create-button")
                await pilot.pause()
                assert isinstance(app.screen, AttachConfirmScreen)
                assert not app.screen.query_one("#attach-button").disabled

    asyncio.run(_check())


def test_reattach_from_sessions_passes_through_the_same_recording_gate() -> None:
    """Only the session's own creation response ever carries a host key (contract-v0.1 has no
    field for it on `GET /sessions`), so a re-attach can only ever gate on recording — but it must
    still gate on that, every time, not just the first attach right after creation."""

    async def _check() -> None:
        stub = SlowChunkedApp(recording_on_create=True)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _login_and_open_new_session(app, pilot)
                await pilot.click("#create-button")
                await pilot.pause()
                await pilot.click("#recording-ack")
                await pilot.click("#attach-button")
                await pilot.pause()
                assert isinstance(app.screen, TerminalScreen)

                await pilot.press("f9")
                await pilot.pause()
                assert isinstance(app.screen, SessionsScreen)

                session_id = next(iter(stub.sessions))
                app.screen._go_to_terminal(session_id)
                await pilot.pause()
                assert isinstance(app.screen, AttachConfirmScreen)
                assert app.screen.query_one("#attach-button").disabled
                assert app.screen._hostkey is None

                await pilot.click("#recording-ack")
                await pilot.click("#attach-button")
                await pilot.pause()
                assert isinstance(app.screen, TerminalScreen)

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
