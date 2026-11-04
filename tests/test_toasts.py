"""Toasts for state changes (design D22; `toasts.seconds`, `toasts.max`).

Why these matter (Rule 9): persistent state lives in the status row, but a change that happens while the
operator is looking at the session (a stale screen, a warning window, a lost data path) is easy to miss, so it
also raises a toast and leaves an event-log line. Toasts are capped, or a burst of frames would bury the
screen, and each lasts a set time.
"""

from __future__ import annotations

import asyncio

from prompt_support import NOW, iso, notice, open_stub_terminal
from stub_server import SlowChunkedApp, StubServer

from tsb_tui.app import TsbTuiApp
from tsb_tui.model.events import (
    KIND_DATA_PATH,
    KIND_NOTICE,
    KIND_QUIET,
    KIND_REFUSED,
    KIND_STALE,
)


def _messages(app) -> list[str]:
    return [n.message for n in app._notifications._notifications.values()]


def test_each_state_change_raises_a_toast_and_an_event_log_line(wait_until, server_frame_validator) -> None:
    async def _check() -> None:
        frames = [
            notice(252, "link_timeout"),
            {"t": "data_path", "v": 1, "session_id": "stub-session-1", "state": "lost", "at": iso(NOW)},
            {"t": "input_refused", "v": 1, "reason": "not_typist"},
            {"t": "quiet_prompt", "v": 1, "prompt_id": "q" * 32, "code": "quiet_prompt.kill",
             "text": "Kill the session?"},
            {"t": "stale", "v": 1, "session_id": "stub-session-1", "state": "pending", "cause": "grid_lost",
             "at": iso(NOW)},
        ]
        for frame in frames:
            server_frame_validator(frame)
        with StubServer(SlowChunkedApp(delay_s=0.05, after_attach_frames=frames)) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(160, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                log = screen.pump.state.log
                await wait_until(pilot, lambda: log.latest(KIND_STALE) is not None, "every frame")
                for kind in (KIND_NOTICE, KIND_DATA_PATH, KIND_REFUSED, KIND_QUIET, KIND_STALE):
                    assert log.latest(kind) is not None, kind
                shown = " | ".join(_messages(app))
                # `toasts.max` is 3: the three newest changes are on screen, the older ones went first.
                assert "The screen may be wrong: output was lost." in shown
                assert "asks: Kill the session?" in shown
                assert "Your input was refused: you do not hold the keyboard." in shown
                assert "warning window" not in shown and "data path was lost" not in shown

    asyncio.run(_check())


def test_toasts_last_toasts_seconds_and_at_most_toasts_max_show(wait_until, monkeypatch) -> None:
    monkeypatch.setenv("TSB_TUI_TOASTS_MAX", "2")
    monkeypatch.setenv("TSB_TUI_TOASTS_SECONDS", "7")

    async def _check() -> None:
        with StubServer(SlowChunkedApp(delay_s=0.05)) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 30)) as pilot:
                await open_stub_terminal(app, pilot, wait_until)
                for number in range(5):
                    app.toast(f"change {number}", title="test")
                live = [n for n in app._notifications if not n.has_expired]
                assert [n.message for n in live][-2:] == ["change 3", "change 4"] and len(live) == 2
                assert all(n.timeout == 7.0 for n in live)

    asyncio.run(_check())
