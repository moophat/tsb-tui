"""The Textual pilot through the whole E0 journey — login, create, acknowledge, attach, type,
resize, detach, see the session `detached` in the full view — against the slow, chunked stub
(stub_server.py).

Why this test matters (Rule 9): Contract §11 makes the broker a raw byte pipe and the client the
sole renderer, so the one way this surface can fail silently is by not draining that pipe
concurrently with everything else Textual does. A regression that made the attach/render loop
synchronous (e.g. a blocking call slipped into `_attach_and_pump`, or into the engine's `feed`) would
make a keystroke wait for the whole burst instead of interleaving with it — invisible in a fast
unit test with no timing, and exactly what a real slow target (a network device, a busy log tail)
would expose immediately. The bounded `asyncio.wait_for` below turns that into a fast,
deterministic failure instead of a flaky timing symptom found later against real hardware.
"""

from __future__ import annotations

import asyncio

from stub_server import SlowChunkedApp, StubServer, create_owner_session
from tsb_contract_client import Client
from tsbc import _serve
from workspace_support import login, open_new_session

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.attach_confirm import AttachConfirmScreen
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.screens.terminal import TerminalScreen

TARGET_ID = "stub-target"


async def _run_flow(screen_text, wait_until) -> None:
    stub = SlowChunkedApp(delay_s=0.02, max_chunks=400, tail_chunks=5)
    with StubServer(stub) as url:
        app = TsbTuiApp(url, target=TARGET_ID)
        async with app.run_test(size=(100, 30)) as pilot:
            # -- Login lands on the workspace with the sidebar open; New session is a dialog over it. --
            workspace = await login(app, pilot)
            assert workspace.sidebar_open and not workspace.slots
            assert isinstance(await open_new_session(app, pilot), NewSessionScreen)

            # -- New session: target prefilled from --target; new connection is the default. --
            assert app.screen.query_one("#target-id").value == TARGET_ID
            await pilot.click("#create-button")
            await pilot.pause()
            assert len(stub.sessions) == 1
            session_id = next(iter(stub.sessions))

            # -- Attach confirm: the session records, so the gate shows the recording text exactly as
            # the record carries it before the first attach (session-attach CR1). It is a warning, not
            # a question: nothing has to be ticked for it. --
            assert isinstance(app.screen, AttachConfirmScreen)
            assert stub.sessions[session_id]["recording"]["warning_text"] in str(
                app.screen.query_one("#attach-recording-warning").render())
            await pilot.click("#attach-button")
            await pilot.pause()
            assert isinstance(app.screen, TerminalScreen)

            # -- Wait until the slow stub has sent some, but not all, chunks. --
            for _ in range(500):
                if 0 < stub.chunks_sent < stub.max_chunks:
                    break
                await pilot.pause(0.01)
            assert 0 < stub.chunks_sent, "the stub never started its burst"

            # -- The core assertion: a keystroke is handled promptly while output is still
            # arriving. A blocked event loop would make this hang past the bound instead of
            # completing in one UI tick. --
            await asyncio.wait_for(pilot.press("x"), timeout=2.0)
            await pilot.pause()

            # -- The stub keeps bursting until it has seen the keystroke, or hits its hard cap;
            # reaching the cap without ever seeing it is the deterministic failure this proves —
            # never a race against wall-clock timing. --
            for _ in range(1000):
                if stub.chunk_at_first_client_frame is not None and \
                        stub.chunks_sent >= stub.chunk_at_first_client_frame + stub.tail_chunks:
                    break
                await pilot.pause(0.01)
            assert stub.chunk_at_first_client_frame is not None, (
                "the stub's hard cap was hit before it ever saw the keystroke")
            assert b"x" in stub.client_frames
            # Output kept arriving after the keystroke — proof the app read it while chunks were
            # still pending, not only after the whole burst had already finished.
            assert stub.chunks_sent > stub.chunk_at_first_client_frame
            await pilot.pause(0.05)
            rendered = screen_text(app)
            # The screen only holds what still fits in the viewport — many more lines than that
            # may have scrolled past by now, so it is the *last* line sent, not the first, that
            # proves the render kept up with the stream.
            assert f"line {stub.chunks_sent - 1:03d} of slow output" in rendered

            # -- An outer window change -> one C2 resize frame from the typist (PTY §7.2), asking
            # for the window minus the rail (4 columns) and the status row (1 row): D18. --
            assert stub.resizes == [], "attaching must not send a resize (DT16)"
            await pilot.resize_terminal(90, 26)
            for _ in range(300):
                if stub.resizes:
                    break
                await pilot.pause(0.01)
            assert stub.resizes == [(90 - 12 - 4, 26 - 1 - 2)]  # less the 12-column rail, the frame and the status row

            # -- Detach: the session lives on, `detached`, and the workspace has nothing open, so the sidebar is
            # open, listing it among the sessions to come back to. --
            app.screen.action_detach()
            await pilot.pause()
            assert isinstance(app.screen, TerminalScreen) and not app.screen.slots and app.screen.sidebar_open
            for _ in range(200):
                if stub.sessions[session_id]["state"] == "detached":
                    break
                await pilot.pause(0.01)
            assert stub.sessions[session_id]["state"] == "detached"
            app.screen._refresh_fleet()
            # The panel lists only the sessions open here; the others are the full view's.
            app.screen.open_fleet_view()
            await wait_until(pilot, lambda: any(row[1] == session_id for row in app.screen.fleet_view.layout.rows),
                            "the detached session to appear in the full view after refresh")


def test_full_flow_stays_responsive_through_a_slow_chunked_attach(monkeypatch, screen_text, wait_until) -> None:
    monkeypatch.setenv("TSB_TUI_SIZE_AUTO_REQUEST", "true")
    monkeypatch.setenv("TSB_TUI_SIZE_DEBOUNCE_MS", "50")
    asyncio.run(_run_flow(screen_text, wait_until))


def test_a_session_on_the_contracts_fixture_server_attaches_types_and_sees_the_echo(
        serve, screen_text, wait_until) -> None:
    """The whole path against the contract's own server rather than the TUI's slow stub: attach, the
    dump, a typed key reaching the target as a frame, and the target's echo coming back onto the
    screen. A canvas that parsed output but dropped what came back from a *typed* byte would pass
    every test that only reads a scripted burst."""

    async def _check() -> None:
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, "normal", 80, 24)
            app.push_screen(TerminalScreen(session_id))
            screen = app.screen
            await wait_until(pilot, lambda: screen.pump is not None and screen.pump.state.holds_keyboard,
                             "the attach and the keyboard")
            await pilot.press("h", "e", "l", "l", "o")
            await wait_until(pilot, lambda: "hello" in screen_text(app), "the echo of what was typed")

    asyncio.run(_check())


def test_two_clients_on_one_session_both_see_what_the_typist_types(
        serve, screen_text, wait_until) -> None:
    """The `two-client` scenario: the TUI types, a second client attached to the same session reads
    the same bytes (C2: the core sends every client the same output). Typing reaches the session, not
    just this client's own screen."""

    async def _check() -> None:
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, "two-client", 80, 24)
            app.push_screen(TerminalScreen(session_id))
            screen = app.screen
            await wait_until(pilot, lambda: screen.pump is not None and screen.pump.state.holds_keyboard,
                             "the attach and the keyboard")
            peer = Client(url)
            peer.login(_serve.PEER, _serve.PASSWORD)
            peer_attachment = peer.attach(session_id)
            try:
                await pilot.press("h", "i")
                await wait_until(pilot, lambda: "hi" in screen_text(app), "the typist's own echo")

                def peer_reads() -> bool:
                    seen = b""
                    while b"hi" not in seen:
                        chunk = peer_attachment.recv(5.0)
                        if chunk is None:
                            return False
                        seen += chunk
                    return True

                assert await asyncio.to_thread(peer_reads), "the second client never saw the typed bytes"
            finally:
                peer_attachment.close()

    asyncio.run(_check())
