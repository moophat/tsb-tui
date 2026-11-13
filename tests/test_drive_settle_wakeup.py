"""A quiet deadline crossed between checking and scheduling must still wake the waiter."""
from __future__ import annotations

import asyncio
import time

from test_drive_waits import FakeProbe

from tsb_tui.drive import waits
from tsb_tui.drive.runner import Driver, _Pending


def test_settled_deadline_crossed_during_evaluation_still_wakes() -> None:
    async def check() -> None:
        driver = object.__new__(Driver)
        driver._pending = []
        driver._scheduled = False
        probe = FakeProbe()
        wait = waits.Wait(waits.parse_until({"settled_ms": 20}, default_settled_ms=800),
                          probe, started=probe.now())
        calls = 0

        def delayed_check() -> waits.Done | None:
            nonlocal calls
            calls += 1
            done = wait.check([])
            if calls == 1:
                # Deschedule after observing "not quiet yet", before the runner arms its timer.
                # A controlled probe clock makes crossing the deadline deterministic.
                time.sleep(0.03)
                probe.clock += 0.03
            return done

        pending = _Pending("quiet", delayed_check, asyncio.get_running_loop().create_future(),
                           settled_in=wait.settled_in)
        done = await asyncio.wait_for(driver._await(pending, 100, lambda: waits.Done(False, "timeout")), 1)
        assert done.ok, "the quiet deadline was crossed but no settle wake-up was scheduled"
        assert calls == 2
        assert not driver._pending

    asyncio.run(check())
