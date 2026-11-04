"""The keys Textual reserves for its own use by default.

Why these matter (Rule 9): Textual's own `ctrl+q` (quit) and `ctrl+p` (command palette) are
*priority* bindings, checked before any screen's `on_key` runs at all — no amount of code in `on_key`
could ever have forwarded them to a target where they mean something; only removing the interception
itself can. (What each key encodes to is `test_term_keys.py`'s; which keys leave, and when, is
`test_input_path.py`'s.)
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from stub_server import SlowChunkedApp, StubServer

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.terminal import TerminalScreen

TARGET_ID = "stub-target"


# -- Pilot: keys Textual would otherwise intercept before they reach on_key at all. --

async def _wait_until(pilot, predicate: Callable[[], bool], attempts: int = 300) -> None:
    """Poll a condition on the event loop, bounded; a frame proving a key was forwarded crosses
    into the stub's own reader thread, so it may not be visible at the instant of one pause."""
    for _ in range(attempts):
        if predicate():
            return
        await pilot.pause(0.01)
    raise AssertionError(f"condition never became true: {predicate!r}")


async def _login_and_attach(app: TsbTuiApp) -> str:
    app.client.login("fixture-user", "fixture-password")
    created = app.client.create_session(TARGET_ID, "new", "xterm-256color", 80, 24)
    session_id = created["session"]["id"]
    app.push_screen(TerminalScreen(session_id))
    return session_id


def test_ctrl_p_and_ctrl_q_reach_the_target_instead_of_the_palette_or_quitting() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp()
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _login_and_attach(app)
                await pilot.pause()
                assert isinstance(app.screen, TerminalScreen)

                await pilot.press("ctrl+p")
                await pilot.pause()
                assert isinstance(app.screen, TerminalScreen), "ctrl+p opened the command palette"
                assert app.return_code is None
                await _wait_until(pilot, lambda: b"\x10" in stub.client_frames)  # ctrl+p

                await pilot.press("ctrl+q")
                await pilot.pause()
                assert isinstance(app.screen, TerminalScreen), "ctrl+q quit instead of forwarding"
                assert app.return_code is None
                assert app._exit is False
                await _wait_until(pilot, lambda: b"\x11" in stub.client_frames)  # ctrl+q

    asyncio.run(_check())


def test_quit_still_works_away_from_the_terminal_screen() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp()
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                # The app starts on the login form: a screen that is not the terminal.
                assert not isinstance(app.screen, TerminalScreen)
                await pilot.pause()
                assert app._exit is False
                await pilot.press("ctrl+q")
                await pilot.pause()
                assert app._exit is True

    asyncio.run(_check())
