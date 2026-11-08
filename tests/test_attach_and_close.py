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
import gc
import warnings
from collections.abc import Callable

from device_login_support import password
from stub_server import DeviceLoginApp, SlowChunkedApp, StubServer
from tsb_contract_client.device_login import AsyncDeviceLogin
from workspace_support import fixture_login

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.login import LoginScreen
from tsb_tui.screens.prompts import EndedScreen
from tsb_tui.screens.terminal import TerminalScreen

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


def _is_empty_workspace(screen) -> bool:
    """The workspace with nothing open and the sidebar showing: where an attach that failed leaves the operator
    (design §2.5), the session still listed in the broker."""
    return (isinstance(screen, TerminalScreen) and screen.is_mounted and not screen.slots
            and screen.sidebar_open)


async def _push_terminal_and_wait_for(app: TsbTuiApp, pilot, screen_type: type | None) -> None:
    """Push the terminal on a session and wait for the outcome: the login screen (a 401), or `None` for the
    empty workspace with the sidebar open."""
    session_id = _create_session(app)
    app.push_screen(TerminalScreen(session_id))
    await pilot.pause()
    if screen_type is None:
        await _wait_until(pilot, lambda: _is_empty_workspace(app.screen))
    else:
        await _wait_until(pilot, lambda: isinstance(app.screen, screen_type))
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


def test_a_404_minting_the_attach_ticket_leaves_the_workspace_empty_with_a_notice() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp(ticket_status=404)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _push_terminal_and_wait_for(app, pilot, None)
                assert any("404" in m for m in _notification_messages(app))

    asyncio.run(_check())


def test_a_refused_websocket_upgrade_leaves_the_workspace_empty() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp(refuse_upgrade=True)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _push_terminal_and_wait_for(app, pilot, None)
                assert any("could not attach" in m for m in _notification_messages(app))

    asyncio.run(_check())


def test_attach_refused_by_a_close_code_leaves_the_workspace_empty() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp(attach_close_code=4004)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _push_terminal_and_wait_for(app, pilot, None)
                assert any("attach refused" in m for m in _notification_messages(app))

    asyncio.run(_check())


def test_an_abrupt_drop_reattaches_and_never_reads_as_an_end() -> None:
    """A dropped WebSocket used to read as "session ended: killed" for any code the app did not
    recognize, including 1006 (no close frame at all) — a client treating its own dropped link as
    an end state the broker never declared (session model §13: a client's WebSocket dying ends
    nothing). The pump's close table (DT13) reattaches with backoff while the session lives, so the
    operator stays on the terminal and no end-state banner is shown."""

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.02, drop_after_chunks=2)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                session_id = _create_session(app)
                app.push_screen(TerminalScreen(session_id))
                await _wait_until(pilot, lambda: stub.attaches >= 2)
                assert isinstance(app.screen, TerminalScreen)
                assert app.return_code is None
                assert not any("ended" in m for m in _notification_messages(app))

    asyncio.run(_check())


def test_a_protocol_error_after_attach_reattaches_instead_of_crashing() -> None:
    """RFC 6455 says the receiver must fail the connection on a broken frame. Before this fix,
    `_attach_and_pump`'s receive loop only caught `ConnectionClosed` — a real protocol violation
    from the wire (not just an ordinary close) would have escaped into the Textual worker and
    crashed the app, the same bug this whole file is about, for a different trigger.

    The pump reads the broken frame as a socket that died with no close code, which the close table
    treats as a transport loss: the app stays up and reattaches. Whether the broken frame is read
    together with `attached` (raising while attaching) or on its own afterward (raising from the
    read loop) is TCP scheduling, not application behaviour — either way the app must not crash."""

    async def _check() -> None:
        stub = SlowChunkedApp(invalid_frame_after_attach=True)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                session_id = _create_session(app)
                app.push_screen(TerminalScreen(session_id))
                await _wait_until(pilot, lambda: stub.attaches >= 2 or _is_empty_workspace(app.screen))
                assert app.return_code is None
                if _is_empty_workspace(app.screen):
                    # The break landed while attaching: the app's own, non-crashing failure banner.
                    messages = await _wait_for_notification(app, pilot, "could not attach")
                    assert any("could not attach" in m for m in messages), messages

    asyncio.run(_check())


def test_a_session_killed_while_attached_shows_the_end_and_is_not_reattached() -> None:
    """4010 is an end state (session model §12), never retried: the operator is told the session was killed,
    in a dialog over its last screen, instead of staring at a frozen one (D36)."""

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.01, stream_close=(4010, 3))
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test() as pilot:
                session_id = _create_session(app)
                app.push_screen(TerminalScreen(session_id))
                await _wait_until(pilot, lambda: isinstance(app.screen, EndedScreen))
                assert app.return_code is None
                messages = await _wait_for_notification(app, pilot, "Session ended · killed")
                assert "Session ended · killed (close 4010)" in messages
                assert stub.attaches == 1, "a killed session must not be reattached"

    asyncio.run(_check())


def test_a_slot_closed_before_its_pump_started_closes_the_handed_over_device_login_socket() -> None:
    """A device login answered to the end hands its socket to the terminal as an attachment
    (`adopt_device_attachment`), and the slot can be closed before the pump worker ever runs its
    body: cancelling a worker whose task never stepped leaves the handed-over socket open, and the
    coroutine is collected un-awaited (a RuntimeWarning). The close must close both — the socket
    the device login handed over, and the never-started coroutine."""

    async def _check() -> None:
        stub = DeviceLoginApp(rounds=[password()], outcome="connected")
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await fixture_login(app, pilot)
                pending = app.client.create_device_login("normal", "fixture-account",
                                                          "xterm-256color", 120, 40)
                login = await AsyncDeviceLogin.open(app.client._attach_url(), pending,
                                                    ssl_context=app.client._ssl())
                await login.answer(1, ["fixture-answer"])
                result = None
                while result is None:
                    frame = await login.recv()
                    if frame is not None and frame.get("t") == "device_login_result":
                        result = frame
                assert result["outcome"] == "connected"
                attachment = await login.attachment()
                assert not attachment._ws.closed, "the handed-over socket is open before the close"
                slot = screen._add_slot({"id": "pending-handoff", "target_id": "normal",
                                         "state": "live", "owner": app.username}, defer_attach=True)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    screen.adopt_device_attachment(slot, attachment)
                    screen._close_slot(slot)
                    await pilot.pause()
                    del slot
                    gc.collect()
                    never = [w for w in caught if issubclass(w.category, RuntimeWarning)
                             and "coroutine" in str(w.message)]
                assert attachment._ws.closed, "the slot closed and left the handed-over socket open"
                assert not never, (
                    f"a never-started coroutine was collected un-awaited: {[str(w.message) for w in never]}")

    asyncio.run(_check())
