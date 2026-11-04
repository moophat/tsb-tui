"""The grid is the session's, and what it shows is what the session drew (design §3.4 tests 2 and 3; DT12, DT16).

Why it matters (Rule 9): this is the property the whole client rests on (immortality §5.2: "each surface checks the
prefix with its own renderer, the TUI first"). A client that joins a session in the middle, or comes back after a
dropped connection, is shown the screen its renderer makes of the dump, so a wrong plumbing step (a grid that kept the
last attachment's cells, a resize that arrived before the prefix, a window that reflowed the session's lines) shows an
operator a screen that is not the device's, and nothing says so. And the other half: the grid has the session's size
and only the session's, so nothing the person does with their own window (a sidebar, a view, a dialog, a token that
moved) may ask the target to change, because the target's size is every client's.

DT12 splits "faithful" by owner. These tests are the TUI's half, the plumbing: for every `C4/golden-*` stream the grid
after the `prefix` and the dump, then the live bytes and the `size` frames, equals the grid after a raw replay, and
again after a reattach (the stream with no resize in it); the dump's own fidelity is U4's round trip and the surface
check is U11's two-TUI rig. Servers: the contract's in-process fixture server (DT7), recording what each client sends.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from input_server import InputRecordingApp, attached
from stub_server import (
    create_owner_session,
    emit_live_output,
    record_output,
    resize_requests,
)
from test_term_engine import GOLDENS, _decode, _replay
from tsbc import _serve

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.terminal import TerminalScreen
from tsb_tui.term import VTBackend, load

GOLDEN_NAMES = sorted(p.stem for p in GOLDENS.glob("golden-*.bin"))
OPEN = (80, 24)  # every golden is opened at 80x24 (their fixtures' `open`)
WINDOW = (70, 22)  # smaller than the session's grid, so the canvas pans and the window could "follow" if it were wrong


@pytest.fixture(scope="module")
def lib(term_so: Path):
    return load(term_so)


def _snapshot(backend: VTBackend, *, sync: bool = False) -> tuple:
    """What a person could tell apart: the grid with its history, the size, the cursor, the modes the session set.
    The cursor is the render state's, which a paint refreshes; a backend nobody paints is synced first (`sync`), and
    the one under a canvas is read only once the canvas has painted what it was fed."""
    if sync:
        backend.sync_render_state()
    cursor = backend.cursor()
    return (backend.grid_lines(), (backend.cols, backend.rows), (cursor.x, cursor.y, cursor.visible), backend.modes())


def test_every_c4_golden_is_replayed_here() -> None:
    """DT6. The goldens come from the pinned contract, never from memory: if the contract at the pin has more or fewer,
    this test says so instead of silently testing a different set."""
    assert GOLDEN_NAMES == ["golden-alt-screen", "golden-input-modes", "golden-mode-reset", "golden-reflow",
                            "golden-resize-mid-stream", "golden-sgr", "golden-wide-chars"]


def _size_frame(session_id: str, cols: int, rows: int) -> dict:
    return {"t": "size", "v": 1, "session_id": session_id, "cols": cols, "rows": rows, "reason": "applied"}


def _token_frame(holder: dict | None, reason: str) -> dict:
    return {"t": "token", "v": 1, "kind": "control", "holder": holder, "reason": reason}


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_a_session_joined_mid_stream_shows_the_grid_of_a_raw_replay_and_the_window_never_moves_it(
        name, serve, monkeypatch, wait_until, server_frame_validator, lib) -> None:
    """DT12, design §3.4 tests 2 and 3. Half of a golden stream is the core's ring when the TUI attaches (so it arrives
    as the dump after the `prefix`) and the rest is live, with each resize the stream carries arriving as the `size`
    frame a core would send. Between, the operator does everything a person does with their own window: opens and closes the
    sidebar, a full view and a dialog, and the keyboard moves away and comes back. The grid must end equal to a raw replay
    of the same stream (text, history, cursor, modes), at the session's size, and with auto size ON the TUI must not
    have asked the target for anything: not on attach, not on a token move, not for a sidebar, a view or a dialog. Then
    one change of the window itself does produce exactly one request, so the silence above is not a feature that
    never works."""
    monkeypatch.setenv("TSB_TUI_SIZE_AUTO_REQUEST", "true")
    chunks = _decode(name)
    first_resize = next((i for i, (kind, _) in enumerate(chunks) if kind == "resize"), None)
    split = first_resize if first_resize is not None else max(1, len(chunks) // 2)
    pre, live = chunks[:split], chunks[split:]
    if not live:
        # A golden that is one chunk long: half of its bytes are the ring and half arrive live, so the live path and
        # the keyboard's return are in the stream here too. A streaming parser keeps its state across the cut.
        (_, whole), = pre
        cut = whole.rfind(b"\n", 0, len(whole) // 2) + 1 or len(whole) // 2
        pre, live = [("feed", whole[:cut])], [("feed", whole[cut:])]
    expected_backend = _replay(lib, chunks, cols=OPEN[0], rows=OPEN[1])
    try:
        expected = _snapshot(expected_backend, sync=True)
    finally:
        expected_backend.close()

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=WINDOW) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, *OPEN)
            sess = record_output(serve_app, session_id, *(payload for _, payload in pre))
            screen = await attached(pilot, app, session_id, wait_until)
            client_id = screen.pump.state.client_id
            assert (screen.backend.cols, screen.backend.rows) == OPEN
            assert screen.canvas.size.width < OPEN[0], "the window must be smaller than the grid for this to mean anything"

            # What the person does with their own window and the keys.
            screen.open_sidebar()
            await wait_until(pilot, lambda: screen.sidebar_open, "the sidebar to open")
            screen.close_sidebar()
            screen.open_fleet_view()
            await wait_until(pilot, lambda: screen.view_open, "the full view to open")
            screen.close_fleet_view()
            await screen.run_action_id("who")
            await wait_until(pilot, lambda: app.screen is not screen, "Who's here to open")
            app.screen.dismiss(None)
            await wait_until(pilot, lambda: app.screen is screen and not screen.dialogs, "the dialog to close")
            with serve_app.lock:
                sess["control_holder"], sess["owner_revoked"] = None, True
                serve_app._send_token(sess, "owner")
            await wait_until(pilot, lambda: not screen.pump.state.holds_keyboard, "the keyboard to leave")

            for index, (kind, payload) in enumerate(live):
                if index == len(live) // 2:
                    with serve_app.lock:
                        sess["control_holder"] = {"client_id": client_id, "user": _serve.USER}
                        sess["owner_revoked"] = False
                        serve_app._send_token(sess, "takeover")
                if kind == "feed":
                    emit_live_output(serve_app, sess, payload)
                else:
                    cols, rows = payload
                    text = json.dumps(_size_frame(session_id, cols, rows))
                    server_frame_validator(json.loads(text))
                    with serve_app.lock:
                        sess["cols"], sess["rows"] = cols, rows
                        serve_app._broadcast(sess, lambda _cid, ws, text=text: ws.send_text(text))
            await wait_until(pilot, lambda: screen.canvas.painted and _snapshot(screen.backend) == expected,
                             f"{name}: the grid to equal a raw replay")
            assert _snapshot(screen.backend) == expected
            assert resize_requests(serve_app) == [], "the TUI asked the target for a size without the window changing"

            # The silence above is a rule and not a dead feature: a real change of the window asks once, debounced.
            await pilot.resize_terminal(WINDOW[0] + 6, WINDOW[1] + 2)
            await wait_until(pilot, lambda: len(resize_requests(serve_app)) == 1, "one debounced request", 10.0)
            await pilot.pause(0.5)
            assert len(resize_requests(serve_app)) == 1

    asyncio.run(check())


@pytest.mark.parametrize("name", [n for n in GOLDEN_NAMES if n not in ("golden-reflow", "golden-resize-mid-stream")])
def test_a_reattach_rebuilds_the_same_grid_from_the_dump(name, serve, wait_until, lib) -> None:
    """DT12, session-attach CR3. After the connection is dropped (4011) the pump reattaches, the core answers with a
    new `prefix` and a dump of everything so far, and the grid is reset and rebuilt from it: it must equal a raw replay
    of the stream again, not the old grid with the dump drawn over it. (The two streams with a resize in them are left
    out: the fixture server's dump is the raw bytes at the session's last size, which is not what a core's grid dump
    of a resized session is. U4's round trip grades that dump.)"""
    chunks = _decode(name)
    expected_backend = _replay(lib, chunks, cols=OPEN[0], rows=OPEN[1])
    try:
        expected = _snapshot(expected_backend, sync=True)
    finally:
        expected_backend.close()

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=WINDOW) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, *OPEN)
            sess = record_output(serve_app, session_id, *(p for _, p in chunks[: len(chunks) // 2]))
            screen = await attached(pilot, app, session_id, wait_until)
            for _, payload in chunks[len(chunks) // 2:]:
                emit_live_output(serve_app, sess, payload)
            await wait_until(pilot, lambda: screen.canvas.painted and _snapshot(screen.backend) == expected,
                             f"{name}: the live grid to equal a raw replay")

            first = screen.pump.state.client_id
            with serve_app.lock:
                serve_app._drop_client(sess, next(iter(serve_app.clients[session_id])), code=4011, reason="")
            await wait_until(pilot, lambda: screen.pump.state.client_id not in (None, first)
                             and screen.pump.state.dump_seen and screen.canvas.painted
                             and _snapshot(screen.backend) == expected, f"{name}: the reattached grid to equal a raw replay")
            assert resize_requests(serve_app) == []

    asyncio.run(check())


def test_the_grid_keeps_the_sessions_size_through_every_window_the_person_makes(serve, monkeypatch, wait_until) -> None:
    """D3, D4, DT16, design §3.4 test 3 ("the grid follows `size`"). A 132x40 session in an 80x24 window keeps a 132x40
    grid and shows a pan range; the window growing past it and shrinking again moves the pan, never the grid; and only
    the core's own `size` frame changes it. With auto size off nothing is asked at all (the window does not speak for
    the session)."""
    monkeypatch.setenv("TSB_TUI_SIZE_AUTO_REQUEST", "false")

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 132, 40)
            screen = await attached(pilot, app, session_id, wait_until)
            assert (screen.backend.cols, screen.backend.rows) == (132, 40)
            assert screen.canvas.view_range.label.startswith("cols 1-76 of 132"), screen.canvas.view_range.label
            for window in ((200, 60), (65, 30), (120, 40)):
                await pilot.resize_terminal(*window)
                await pilot.pause(0.4)
                assert (screen.backend.cols, screen.backend.rows) == (132, 40), f"the grid followed a {window} window"
            frame = _size_frame(session_id, 100, 30)
            with serve_app.lock:
                sess = serve_app.sessions[session_id]
                sess["cols"], sess["rows"] = 100, 30
                serve_app._broadcast(sess, lambda _cid, ws: ws.send_text(json.dumps(frame)))
            await wait_until(pilot, lambda: (screen.backend.cols, screen.backend.rows) == (100, 30), "the size frame")
            assert resize_requests(serve_app) == []
            assert isinstance(app.screen, TerminalScreen)

    asyncio.run(check())
