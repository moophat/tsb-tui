"""The key-encoding table, and the keys Textual reserves for its own use by default.

Why these matter (Rule 9): a key that silently encodes to nothing, or to the wrong bytes, is
invisible in the UI — the operator just sees their keystroke vanish, on real network gear where
Ctrl-^ aborts a hung ping/traceroute/DNS lookup and Ctrl-] is the telnet escape character. And
Textual's own `ctrl+q` (quit) and `ctrl+p` (command palette) are *priority* bindings, checked
before any screen's `on_key` runs at all — no amount of code in `on_key` could ever have forwarded
them; only removing the interception itself can.
"""

from __future__ import annotations

import asyncio

import pytest
from stub_server import SlowChunkedApp, StubServer
from textual import events
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.sessions import SessionsScreen
from tsb_tui.screens.terminal import TerminalScreen, _encode_key

TARGET_ID = "stub-target"


# -- _encode_key: a pure table, tested without any live app or stub. --

@pytest.mark.parametrize(("key", "character", "application_cursor_keys", "expected"), [
    # Plain printable characters go straight through.
    ("x", "x", False, b"x"),
    # Ctrl-letters, and the punctuation Textual names rather than gives a raw byte for.
    ("ctrl+c", None, False, b"\x03"),
    ("ctrl+a", None, False, b"\x01"),
    ("ctrl+backslash", None, False, b"\x1c"),
    ("ctrl+underscore", None, False, b"\x1f"),
    # Real, plain control bytes dropped before this fix: IOS's Ctrl-Shift-6 abort, the telnet
    # escape character, and NUL.
    ("ctrl+circumflex_accent", "\x1e", False, b"\x1e"),
    ("ctrl+right_square_bracket", "\x1d", False, b"\x1d"),
    ("ctrl+@", "\x00", False, b"\x00"),
    # Ctrl-Left/Right as the readline/emacs-mode word-jump bytes a real terminal driver sends for
    # them, not the raw CSI-modifier form _modified_arrow would otherwise compute.
    ("ctrl+left", None, False, b"\x1bb"),
    ("ctrl+right", None, False, b"\x1bf"),
    # A plain arrow honours DECCKM (pyte's own tracked mode).
    ("up", None, False, b"\x1b[A"),
    ("up", None, True, b"\x1bOA"),
    ("left", None, True, b"\x1bOD"),
    # A modified arrow always uses the CSI form, DECCKM or not (xterm's `ESC O` form has no room
    # for a modifier parameter).
    ("shift+up", None, True, b"\x1b[1;2A"),
    ("ctrl+shift+left", None, False, b"\x1b[1;6D"),
    ("alt+down", None, False, b"\x1b[1;3B"),
    # Alt+<letter/digit>: Textual folds this into "alt+<char>" but keeps the real character in
    # `.character` — forwarded as the Escape prefix a real terminal sends, plus that character.
    ("alt+a", "a", False, b"\x1ba"),
    ("alt+shift+a", "A", False, b"\x1bA"),
    ("alt+1", "1", False, b"\x1b1"),
    # An unknown key with no byte mapping at all.
    ("f20", None, False, None),
])
def test_encode_key_table(key: str, character: str | None, application_cursor_keys: bool,
                          expected: bytes | None) -> None:
    event = events.Key(key, character)
    assert _encode_key(event, application_cursor_keys=application_cursor_keys) == expected


# -- Pilot: keys Textual would otherwise intercept before they reach on_key at all. --

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

                await pilot.press("ctrl+q")
                await pilot.pause()
                assert isinstance(app.screen, TerminalScreen), "ctrl+q quit instead of forwarding"
                assert app.return_code is None
                assert app._exit is False

                assert b"\x10" in stub.client_frames  # ctrl+p
                assert b"\x11" in stub.client_frames  # ctrl+q

    asyncio.run(_check())


def test_quit_still_works_away_from_the_terminal_screen() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp()
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                app.push_screen(SessionsScreen())
                await pilot.pause()
                assert app._exit is False
                await pilot.press("ctrl+q")
                await pilot.pause()
                assert app._exit is True

    asyncio.run(_check())


def test_ctrl_c_forwards_to_the_target() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp()
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _login_and_attach(app)
                await pilot.pause()
                await pilot.press("ctrl+c")
                await pilot.pause()
                assert b"\x03" in stub.client_frames
                assert isinstance(app.screen, TerminalScreen)

    asyncio.run(_check())


def test_paste_is_bracketed_only_when_the_target_asked_for_it() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp()
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _login_and_attach(app)
                await pilot.pause()
                screen = app.screen
                assert isinstance(screen, TerminalScreen)

                screen.post_message(events.Paste("hello"))
                await pilot.pause()
                assert b"hello" in stub.client_frames
                assert b"\x1b[200~hello\x1b[201~" not in stub.client_frames

                screen.view.emulator.feed(b"\x1b[?2004h")  # DECSET 2004
                screen.post_message(events.Paste("world"))
                await pilot.pause()
                assert b"\x1b[200~world\x1b[201~" in stub.client_frames

    asyncio.run(_check())
