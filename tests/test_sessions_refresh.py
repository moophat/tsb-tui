"""Sessions-list refresh generations.

Why this matters (Rule 9): two `GET /sessions` calls can be in flight at once (the automatic
refresh `on_mount` fires, then the operator presses Refresh again before the first reply lands) —
network latency has no ordering guarantee, so the earlier request's reply can arrive *after* the
later one's. Applying whichever reply lands last, rather than whichever was issued last, would let
a slow, stale answer silently overwrite a list that already reflects a newer request.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from stub_server import SlowChunkedApp, StubServer
from textual.widgets import DataTable
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.sessions import SessionsScreen

TARGET_ID = "stub-target"


async def _wait_until(pilot, predicate: Callable[[], bool], attempts: int = 300) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await pilot.pause(0.01)
    raise AssertionError(f"condition never became true: {predicate!r}")


def test_a_slow_earlier_refresh_does_not_overwrite_a_newer_one() -> None:
    async def _check() -> None:
        # Call #0 (on_mount's own automatic refresh) sleeps 0.2s before answering; call #1 (the
        # explicit refresh issued below, while #0 is still asleep) answers immediately.
        stub = SlowChunkedApp(sessions_call_delays=(0.2,))
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                app.client.login("fixture-user", "fixture-password")
                app.username = "fixture-user"
                app.client.create_session(TARGET_ID, "new", "xterm-256color", 80, 24)

                screen = SessionsScreen()
                app.push_screen(screen)
                await pilot.pause()  # generation 1 (on_mount): issued now, answers in ~0.2s

                # A second session appears before generation 1's slow reply lands.
                app.client.create_session(TARGET_ID, "new", "xterm-256color", 80, 24)
                screen.action_refresh()  # generation 2: answers immediately, sees both sessions

                await _wait_until(pilot, lambda: len(screen._sessions) == 2)
                table = screen.query_one("#sessions-table", DataTable)
                assert table.row_count == 2

                # Give generation 1's slow reply — one session only — time to land well after
                # generation 2 already applied; it must be discarded, not overwrite the table.
                await pilot.pause(0.35)
                assert table.row_count == 2, "a stale, slower refresh overwrote the newer one"
                assert len(screen._sessions) == 2

    asyncio.run(_check())
