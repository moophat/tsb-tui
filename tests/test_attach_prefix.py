"""The attach prefix and dump, as the screen applies them.

Why these matter (Rule 9): a surface that joins mid-session is shown whatever its renderer makes of
the dump the core rebuilt (immortality §5.2: "the TUI first"), so the one way a TUI can lie to its
operator is to render that dump wrongly. The earlier renderer ignored mode 1049, so a dump made inside vim left the
operator reading the primary screen's leftovers under the editor (#174). And the geometry rules of
session-attach are all about *when* the grid takes which size: `prefix` carries the dump's own
geometry (CR3), a `size` can arrive before it or between the dump and `clients` (R13), and a dump
that was answered (a device-status query inside it) would type into a live session the operator is
only watching (DT3).

Servers: the contract's in-process fixture server (DT7) for everything it plays, and the TUI's own
stub for an E1-shaped core that sends no `prefix`. Frames built here are validated with tsbc.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from input_server import InputRecordingApp
from stub_server import (
    SlowChunkedApp,
    create_owner_session,
    emit_live_output,
    record_output,
)
from stub_server import StubServer as StubHttpServer
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.terminal import TerminalScreen
from tsbc import _serve
from tsbc._serve import ALT_SCREEN_DUMP, ServeApp


def test_the_alternate_screen_shows_at_once_and_leaving_it_restores_the_primary(
        serve, screen_text, wait_until) -> None:
    """#174. A client that attaches to a session sitting in vim must see the editor's screen at
    once, and when vim exits the shell's screen must come back from under it: that is what mode 1049
    does, and a renderer that ignores it shows the operator the wrong screen for as long as they look."""

    async def _check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, "alt-screen", 80, 24)
            # The scenario's own dump (the alternate-screen modes) with a shell screen recorded before
            # it and the editor's content after it.
            sess = record_output(serve_app, session_id, b"PRIMARY-TEXT\r\n", ALT_SCREEN_DUMP, b"ALT-ONLY")
            app.push_screen(TerminalScreen(session_id))
            await wait_until(pilot, lambda: "ALT-ONLY" in screen_text(app), "the alternate screen")
            assert "PRIMARY-TEXT" not in screen_text(app)

            emit_live_output(serve_app, sess, b"\x1b[?1049l")
            await wait_until(pilot, lambda: "PRIMARY-TEXT" in screen_text(app),
                             "the primary screen back after leaving the alternate one")
            assert "ALT-ONLY" not in screen_text(app)

    asyncio.run(_check())


def test_a_reattach_resets_the_grid_before_its_dump(serve, screen_text, wait_until) -> None:
    """Session-attach: every `prefix` rebuilds the screen from scratch. A grid that kept what the
    dropped attachment had drawn would show the replayed history on top of itself, each line twice."""

    async def _check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            sess = record_output(serve_app, session_id, b"LINE-A\r\nLINE-B\r\n")
            app.push_screen(TerminalScreen(session_id))
            await wait_until(pilot, lambda: "LINE-B" in screen_text(app), "the first dump")
            screen = app.screen
            assert isinstance(screen, TerminalScreen)
            emit_live_output(serve_app, sess, b"LINE-C\r\n")
            await wait_until(pilot, lambda: "LINE-C" in screen_text(app), "live output")

            # The server detaches this client (4011): the pump reattaches and a new dump follows.
            first_client = screen.pump.state.client_id
            with serve_app.lock:
                client_id = next(iter(serve_app.clients[session_id]))
                serve_app._drop_client(sess, client_id, code=4011, reason="")
            await wait_until(
                pilot, lambda: screen.pump.state.client_id != first_client
                and screen.pump.state.dump_seen and screen.canvas.painted,
                "the second attachment's dump to be painted")
            text = screen_text(app)
            assert (text.count("LINE-A"), text.count("LINE-B"), text.count("LINE-C")) == (1, 1, 1), text

    asyncio.run(_check())


class _SizesAroundTheDump(ServeApp):
    """An E2 core's worst legal order (session-attach R13): `token` and `size` before `prefix`, and
    optionally a `size` between the dump and `clients`."""

    def __init__(self, late_size: dict | None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.late_size = late_size

    def _send_clients(self, sess: dict) -> None:
        if self.late_size is not None:
            self._broadcast(sess, lambda _cid, ws: ws.send_text(json.dumps(self.late_size)))
        super()._send_clients(sess)


@pytest.mark.parametrize(("with_late_size", "expected"), [(False, (60, 20)), (True, (100, 30))])
def test_the_grid_ends_at_the_latest_size_whatever_the_order(
        with_late_size, expected, serve, screen_text, wait_until, server_frame_validator) -> None:
    """R13. The dump is rendered at the `prefix` geometry (80x24 here), and the grid then takes the
    latest size in effect: a `size` sent before the prefix still counts once the dump is in, and one
    between the dump and `clients` is newer still. Ending at the prefix's size would leave this client
    drawing a session at a size the target no longer has."""
    early = {"t": "size", "v": 1, "session_id": "ignored", "cols": 60, "rows": 20, "reason": "applied"}
    token = {"t": "token", "v": 1, "kind": "control", "holder": None, "reason": "owner"}
    late = {"t": "size", "v": 1, "session_id": "ignored", "cols": 100, "rows": 30, "reason": "applied"}
    for frame in (early, token, late):
        server_frame_validator(frame)

    async def _check() -> None:
        server = _SizesAroundTheDump(late if with_late_size else None, after_attach=(token, early))
        serve_app, url = serve(server)
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            record_output(serve_app, session_id, b"DUMPED-TEXT")
            app.push_screen(TerminalScreen(session_id))
            screen = app.screen
            await wait_until(pilot, lambda: "DUMPED-TEXT" in screen_text(app), "the dump")
            await wait_until(
                pilot, lambda: (screen.backend.cols, screen.backend.rows) == expected,
                "the grid to reach the latest size")
            # The canvas repaints a beat after the grid's size settles: wait for the paint to show what the dump drew,
            # never read the screen once (a single read caught it blank).
            await wait_until(pilot, lambda: "DUMPED-TEXT" in screen_text(app),
                             "resizing after the dump to keep what it drew")

    asyncio.run(_check())


def test_a_device_status_query_in_a_dump_is_not_answered_but_one_in_live_output_is(
        serve, wait_until) -> None:
    """DT3. A dump replays history, and history may hold a cursor-position query some program once
    asked. Answering it would type a reply into a live session nobody asked it from. The same query
    arriving live must still be answered, or the muting would be hiding a dead reply path.

    Since DT20 the reply leaves through the attachment's own path: the feed thread collects the
    engine's answer and the processor delivers it on the loop. The test therefore watches what the
    server receives from this client — no reply callback, which the attachment's output no longer
    uses — rather than what a direct `backend.feed` would have called."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            sess = record_output(serve_app, session_id, b"before\x1b[6nafter")
            app.push_screen(TerminalScreen(session_id))
            screen = app.screen
            await wait_until(pilot, lambda: screen.pump is not None and screen.pump.state.dump_seen,
                             "the first dump")

            first_client = screen.pump.state.client_id
            with serve_app.lock:
                client_id = next(iter(serve_app.clients[session_id]))
                serve_app._drop_client(sess, client_id, code=4011, reason="")
            await wait_until(
                pilot, lambda: screen.pump.state.client_id != first_client
                and screen.pump.state.dump_seen and screen.canvas.painted,
                "the second dump (it carries the query again)")
            assert serve_app.received(first_client) == [], "a query inside a dump was answered"

            second_client = screen.pump.state.client_id
            emit_live_output(serve_app, sess, b"\x1b[6n")
            await wait_until(pilot, lambda: len(serve_app.received(second_client)) == 1,
                             "the reply to a live query")
            reply = serve_app.received(second_client)[0]
            assert reply.startswith(b"\x1b[") and reply.endswith(b"R"), reply

    asyncio.run(_check())


def test_against_a_core_with_no_prefix_output_follows_attached(screen_text, wait_until) -> None:
    """Session-attach, E1 shape: a core that sends no `prefix` has no dump to apply, so output
    follows `attached` at once and the grid is the size `attached` named (the record's 100x30), not
    the window's (80x24)."""

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.0, max_chunks=3, tail_chunks=0)
        with StubHttpServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(80, 24)) as pilot:
                app.client.login("fixture-user", "fixture-password")
                session_id = app.client.create_session(
                    "stub-target", "new", "xterm-256color", 100, 30)["session"]["id"]
                app.push_screen(TerminalScreen(session_id))
                screen = app.screen
                await wait_until(pilot, lambda: "line 002 of slow output" in screen_text(app),
                                 "output after `attached`")
                assert (screen.backend.cols, screen.backend.rows) == (100, 30)

    asyncio.run(_check())
