"""Attach failures and close codes.

Why these matter (Rule 9): before this fix, `TerminalScreen._attach_and_pump` let `ApiError` (the
ticket mint failing), `HandshakeError`/`ProtocolError` (the WebSocket upgrade or its framing
failing outright) escape into the Textual worker, whose default `exit_on_error=True` crashed the
whole app — an expired login, a session that already ended, or a proxy answering the upgrade with
an error while the core restarts all reach here in real use, and none of them should end the
operator's session with the shell prompt. And a WS close code outside 4000-4999 (a normal close,
an abnormal one, or none at all) is never itself an end state (session model §13: a client's
WebSocket dying ends nothing) — the fix asks the broker what actually happened instead of
guessing "killed" for anything it does not recognize.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from stub_server import SlowChunkedApp, StubServer
from textual.css.query import NoMatches
from textual.widgets import DataTable
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.login import LoginScreen
from tsb_tui.screens.sessions import SessionsScreen
from tsb_tui.screens.terminal import TerminalScreen, banner_for

TARGET_ID = "stub-target"


def _create_session(app: TsbTuiApp) -> str:
    """Login and create bypass the UI here (blocking calls straight to the client): each of these
    tests is about what happens once the app tries to *attach*, not about the create flow, which
    the full pilot journey already covers end to end."""
    app.client.login("fixture-user", "fixture-password")
    created = app.client.create_session(TARGET_ID, "new", "xterm-256color", 80, 24)
    return created["session"]["id"]


async def _wait_until(pilot, predicate: Callable[[], bool], attempts: int = 300) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await pilot.pause(0.01)
    raise AssertionError(f"condition never became true: {predicate!r}")


def _notification_messages(app: TsbTuiApp) -> list[str]:
    return [n.message for n in app._notifications._notifications.values()]


async def _wait_for_notification(
    app: TsbTuiApp, pilot, *texts: str, timeout_s: float = 5.0
) -> list[str]:
    """Poll until a notification contains one of `texts`, or the timeout elapses; either way the
    latest messages are returned so the caller can assert the same way it would on a single read."""
    messages = _notification_messages(app)
    for _ in range(int(timeout_s / 0.05)):
        if any(t in m for t in texts for m in messages):
            return messages
        await pilot.pause(0.05)
        messages = _notification_messages(app)
    return messages


def _returned_screen_is_ready(screen) -> bool:
    """True once the screen the terminal switched back to is fully up. `app.screen` becomes that
    screen the instant it is switched in, before Textual has mounted its widgets, and
    `SessionsScreen.on_mount` then fetches `GET /sessions`; until that first reply lands and its
    rows are shown, a callback is still in flight and can run against an unmounted screen."""
    if not screen.is_mounted:
        return False
    if not isinstance(screen, SessionsScreen):
        return True
    try:
        return screen.query_one("#sessions-table", DataTable).row_count > 0
    except NoMatches:
        return False


async def _push_terminal_and_wait_for(app: TsbTuiApp, pilot, screen_type: type) -> None:
    session_id = _create_session(app)
    app.push_screen(TerminalScreen(session_id))
    await pilot.pause()
    await _wait_until(pilot, lambda: isinstance(app.screen, screen_type))
    await _wait_until(pilot, lambda: _returned_screen_is_ready(app.screen))
    # The app is still running: a crashed worker (`WorkerFailed`, the bug this fixes) would have
    # set a non-zero return code and stopped the app instead of navigating.
    assert app.return_code is None


def test_a_401_minting_the_attach_ticket_sends_the_operator_back_to_login() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp(ticket_status=401)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _push_terminal_and_wait_for(app, pilot, LoginScreen)

    asyncio.run(_check())


def test_a_404_minting_the_attach_ticket_returns_to_sessions_with_a_notice() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp(ticket_status=404)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _push_terminal_and_wait_for(app, pilot, SessionsScreen)
                assert any("404" in m for m in _notification_messages(app))

    asyncio.run(_check())


def test_a_refused_websocket_upgrade_returns_to_sessions() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp(refuse_upgrade=True)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _push_terminal_and_wait_for(app, pilot, SessionsScreen)
                assert any("could not attach" in m for m in _notification_messages(app))

    asyncio.run(_check())


def test_attach_refused_by_a_close_code_returns_to_sessions() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp(attach_close_code=4004)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _push_terminal_and_wait_for(app, pilot, SessionsScreen)
                assert any("attach refused" in m for m in _notification_messages(app))

    asyncio.run(_check())


def test_an_abrupt_drop_asks_the_broker_what_state_the_session_is_really_in() -> None:
    """A dropped WebSocket used to read as "session ended: killed" for any code the app did not
    recognize, including 1006 (no close frame at all) — a client treating its own dropped link as
    an end state the broker never declared. The fix fetches `GET /sessions/{id}` and shows
    whatever it actually says, proving the mechanism rather than assuming a particular state."""

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.02, drop_after_chunks=2)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _push_terminal_and_wait_for(app, pilot, SessionsScreen)
                expected = f"{banner_for(1006)} — the session is live; re-attach from Sessions"
                assert expected in _notification_messages(app)

    asyncio.run(_check())


def test_a_protocol_error_after_attach_returns_to_sessions_instead_of_crashing() -> None:
    """RFC 6455 says the receiver must fail the connection on a broken frame. Before this fix,
    `_attach_and_pump`'s receive loop only caught `ConnectionClosed` — a real protocol violation
    from the wire (not just an ordinary close) would have escaped into the Textual worker and
    crashed the app, the same bug this whole file is about, for a different trigger.

    Whether the broken frame is read together with `attached` in the same pass (raising from the
    attach step) or on its own afterward (raising from the receive loop) is TCP scheduling, not
    application behavior — `_push_terminal_and_wait_for`'s own `return_code is None` assertion is
    what actually pins the invariant this test is for; the message is one of the app's two known,
    non-crashing failure banners, never left blank. The notification is awaited because it is posted
    after the screen switch, so a single read can beat it."""

    async def _check() -> None:
        stub = SlowChunkedApp(invalid_frame_after_attach=True)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _push_terminal_and_wait_for(app, pilot, SessionsScreen)
                messages = await _wait_for_notification(
                    app, pilot, "connection to the broker lost", "could not attach")
                assert any("connection to the broker lost" in m or "could not attach" in m
                          for m in messages), messages

    asyncio.run(_check())
