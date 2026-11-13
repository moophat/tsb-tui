"""The timer a `wait` arms is never shorter than the time left to its deadline (issue #70).

A `wait` answers `timeout` no earlier than the `timeout_ms` the agent asked for. The guarantee is the timer's: the
remaining time is rounded up to a whole millisecond before it is armed, never truncated. This test reads the delay
armed, with the loop's clock fed by the test, so it needs no wall-clock sleep and cannot pass on a slow machine.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Self

import pytest

from tsb_tui.drive import waits
from tsb_tui.drive.runner import Driver


class _LockedCanvas:
    def lock(self) -> Self:
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@dataclass
class _Slot:
    session_id: str = "s"
    canvas: object = field(default_factory=lambda: type("Canvas", (), {"backend": _LockedCanvas()})())


class _Probe:
    """A quiet session that matches nothing: the wait can only end on its timeout."""

    clock = 100.0

    def holder(self) -> str:
        return "other"

    def record_state(self) -> str | None:
        return "live"

    def closed_as(self) -> str | None:
        return None

    def cursor_row(self) -> str:
        return ""

    def screen_text(self) -> str:
        return ""

    def reply_text(self) -> str:
        return ""

    def last_output_at(self) -> float | None:
        return None

    def now(self) -> float:
        return self.clock

    def tail(self) -> list[str]:
        return []


class _Unlocked:
    def check(self) -> bool:
        return False


class _Driver:
    """Only what `run_wait` and `_await` touch, with the real methods bound."""

    _await = Driver._await
    _evaluate_one = Driver._evaluate_one
    _events_since = Driver._events_since
    run_wait = Driver.run_wait

    def __init__(self) -> None:
        self._pending: list = []
        self._signals: list = []
        self._seq = 0
        self.screen_lock = _Unlocked()
        self._probe = _Probe()

    def probe(self, slot: _Slot) -> _Probe:
        return self._probe


def test_the_timer_a_wait_arms_rounds_the_remaining_time_up_to_the_deadline() -> None:
    """149.9 ms left must arm a 150 ms timer, not 149 ms: `int` truncation answered 1 ms early (issue #70)."""

    async def check() -> None:
        loop = asyncio.get_running_loop()
        started = 100.0
        # The clock reads `started` when the deadline is taken, then 100 µs later for the remaining time: 149.9 ms.
        readings = iter([started, started + 0.0001])
        loop.time = lambda: next(readings, started + 0.0001)  # type: ignore[method-assign]
        armed: list[float] = []
        real_call_later = loop.call_later

        def call_later(delay: float, callback, *args: object):
            armed.append(delay)
            return real_call_later(0, callback, *args)

        loop.call_later = call_later  # type: ignore[method-assign]
        driver = _Driver()
        done, _elapsed_ms = await driver.run_wait(_Slot(), waits.parse_until({"reply": "never"}, default_settled_ms=800), 150)
        assert done.error == "timeout"
        assert armed, "run_wait armed no timeout at all"
        assert armed[0] >= 0.1499, f"the timer was armed {armed[0] * 1000:.3f} ms early: the remaining time was truncated"
        assert armed[0] == pytest.approx(0.150), f"expected the 149.9 ms rounded up to 150 ms, got {armed[0]} s"

    asyncio.run(check())
