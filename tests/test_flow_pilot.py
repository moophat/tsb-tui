"""The Textual pilot through the whole E0 journey — login, create, acknowledge, attach, type,
resize, detach, see the session `detached` on the Sessions list — against the slow, chunked stub
(stub_server.py).

Why this test matters (Rule 9): Contract §11 makes the broker a raw byte pipe and the client the
sole renderer, so the one way this surface can fail silently is by not draining that pipe
concurrently with everything else Textual does. A regression that made the attach/render loop
synchronous (e.g. a blocking call slipped into `_attach_and_pump`, or into pyte's `feed`) would
make a keystroke wait for the whole burst instead of interleaving with it — invisible in a fast
unit test with no timing, and exactly what a real slow target (a network device, a busy log tail)
would expose immediately. The bounded `asyncio.wait_for` below turns that into a fast,
deterministic failure instead of a flaky timing symptom found later against real hardware.
"""

from __future__ import annotations

import asyncio

from stub_server import SlowChunkedApp, StubServer
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.attach_confirm import AttachConfirmScreen
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.screens.sessions import SessionsScreen
from tsb_tui.screens.terminal import TerminalScreen, banner_for

TARGET_ID = "stub-target"


async def _run_flow() -> None:
    stub = SlowChunkedApp(delay_s=0.02, max_chunks=400, tail_chunks=5)
    with StubServer(stub) as url:
        app = TsbTuiApp(url, target=TARGET_ID)
        async with app.run_test(size=(100, 30)) as pilot:
            # -- Login --
            screen = app.screen
            screen.query_one("#username").value = "fixture-user"
            screen.query_one("#password").value = "fixture-password"
            await pilot.click("#login-button")
            await pilot.pause()
            assert isinstance(app.screen, NewSessionScreen)

            # -- New session: target prefilled from --target; new connection is the default. --
            assert app.screen.query_one("#target-id").value == TARGET_ID
            await pilot.click("#create-button")
            await pilot.pause()
            assert len(stub.sessions) == 1
            session_id = next(iter(stub.sessions))

            # -- Attach confirm: the session records, so Attach stays disabled until the
            # recording warning is acknowledged (every attach passes through here, not just the
            # first one — session-record.schema.json). --
            assert isinstance(app.screen, AttachConfirmScreen)
            assert app.screen.query_one("#attach-button").disabled
            await pilot.click("#recording-ack")
            await pilot.click("#attach-button")
            await pilot.pause()
            assert isinstance(app.screen, TerminalScreen)
            terminal_screen = app.screen

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
            rendered = terminal_screen.view.emulator.render()
            # The screen only holds what still fits in the viewport — many more lines than that
            # may have scrolled past by now, so it is the *last* line sent, not the first, that
            # proves the render kept up with the stream.
            assert f"line {stub.chunks_sent - 1:03d} of slow output" in rendered

            # -- Resize -> the C2 resize frame (PTY §7.2: the control-token holder's resize). --
            await pilot.resize_terminal(90, 26)
            await pilot.pause(0.1)
            assert stub.resizes, "no resize frame reached the target"
            assert stub.resizes[-1] == (terminal_screen.view.size.width, terminal_screen.view.size.height)

            # -- Detach: the session lives on, `detached`, shown on the Sessions list. --
            await pilot.press("f9")
            await pilot.pause()
            assert isinstance(app.screen, SessionsScreen)
            for _ in range(200):
                if stub.sessions[session_id]["state"] == "detached":
                    break
                await pilot.pause(0.01)
            assert stub.sessions[session_id]["state"] == "detached"
            await pilot.pause()
            table = app.screen.query_one("#sessions-table")
            assert table.get_row(session_id)[2] == "detached"


def test_full_flow_stays_responsive_through_a_slow_chunked_attach() -> None:
    asyncio.run(_run_flow())


def test_banner_for_maps_every_close_code_the_contract_defines() -> None:
    """Calls the real function under test rather than re-deriving the mapping in the test
    (a mutation to `banner_for`'s own fallback must be able to fail this): a session that ended
    is never mistaken, in the UI, for one still attachable, and a close that is not itself an end
    state (session model §13) never gets one of those words at all."""
    assert banner_for(4010) == "session ended: killed (by TSB, or through it)"
    assert banner_for(4012) == "session ended: terminated (from outside TSB)"
    assert banner_for(4011) == (
        "detached by the server (missed heartbeats, or a send queue that would not drain) — "
        "the session lives on")
    # Any other 4xxx reads as 4010 — the contract's own rule for receivers.
    assert banner_for(4099) == banner_for(4010)
    # Not 4xxx at all: never one of the end-state words above.
    for code in (1000, 1001, 1006, None):
        assert banner_for(code) == "connection to the broker lost"
