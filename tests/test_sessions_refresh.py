"""Sessions-list refresh generations.

Why this matters (Rule 9): two `GET /sessions` calls can be in flight at once (the automatic
refresh `on_mount` fires, then the operator presses Refresh again before the first reply lands) —
network latency has no ordering guarantee, so the earlier request's reply can arrive *after* the
later one's. Applying whichever reply lands last, rather than whichever was issued last, would let
a slow, stale answer silently overwrite a list that already reflects a newer request.
"""

from __future__ import annotations

import asyncio
import threading
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


class _RecordingSessions(SessionsScreen):
    """Records every refresh reply this screen handles — applied or refused — so the test can wait
    on the stale reply having actually been handled instead of on a wall-clock pause."""

    def __init__(self) -> None:
        super().__init__()
        self.replies: list[tuple[int, int]] = []

    def _on_sessions(self, generation: int, sessions: list[dict]) -> None:
        self.replies.append((generation, len(sessions)))
        super()._on_sessions(generation, sessions)


def test_a_slow_earlier_refresh_does_not_overwrite_a_newer_one() -> None:
    async def _check() -> None:
        # The stub holds the first `GET /sessions` (on_mount's own automatic refresh) on a gate.
        # Waiting for that request to be the one held forces the order: the older request is the
        # slow one, and the explicit refresh below issues a newer request that answers at once.
        release_first = threading.Event()
        stub = SlowChunkedApp(sessions_first_call_gate=release_first)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                app.client.login("fixture-user", "fixture-password")
                app.username = "fixture-user"
                app.client.create_session(TARGET_ID, "new", "xterm-256color", 80, 24)

                screen = _RecordingSessions()
                app.push_screen(screen)
                # generation 1 (on_mount) is issued now; the stub holds it, so it is the older,
                # slower reply by construction, never by a race against a wall clock.
                await _wait_until(pilot, stub.sessions_first_call_started.is_set)

                # A second session appears before generation 1's held reply lands; generation 2
                # answers immediately and sees both sessions.
                app.client.create_session(TARGET_ID, "new", "xterm-256color", 80, 24)
                screen.action_refresh()

                await _wait_until(pilot, lambda: len(screen._sessions) == 2)
                table = screen.query_one("#sessions-table", DataTable)
                assert table.row_count == 2
                # Generation 1 is still held, so generation 2's is the only reply handled so far.
                assert screen.replies == [(2, 2)], "the newer refresh must be applied first"

                # Release generation 1's older reply — one session only — and wait for it to be
                # handled. Generation 2 is already applied, so it can only be discarded: it must
                # never overwrite the newer two-session list. The recorded order also has to be
                # explicit — the newer reply first, then the older.
                release_first.set()
                await _wait_until(pilot, lambda: len(screen.replies) == 2)
                assert [generation for generation, _ in screen.replies] == [2, 1], (
                    "the newer reply must be handled first, then the older one")
                assert screen.replies[1] == (1, 1), "the older reply carried the stale one-session list"
                assert table.row_count == 2, "a stale, slower refresh overwrote the newer one"
                assert len(screen._sessions) == 2

    asyncio.run(_check())
