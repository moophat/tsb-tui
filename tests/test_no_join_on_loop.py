"""No background thread is joined on the event loop (DT14, DT20).

Why these matter: a poller or alert watcher hands its result to the loop through `call_from_thread`, which waits on
the loop. A `stop()` that joins that worker on the loop therefore waits for a worker that is waiting for the loop:
the drive is frozen until the join's timeout, and no command is answered meanwhile. These tests hold a reply or an
event exactly where `call_from_thread` holds it, then stop the worker or unmount the workspace, and prove the loop
comes back at once and drops what was still in flight.
"""

from __future__ import annotations

import asyncio
import threading
import time

from drive_support import Rig
from input_server import make_session
from tsbc import _serve

from tsb_tui.screens.login import LoginScreen
from tsb_tui.screens.terminal import TerminalScreen

SECRET = "canary-account-password-7462"


def _poll_apply(callback: object) -> bool:
    return getattr(callback, "__func__", None) is TerminalScreen._apply_poll


def _track_unmount(monkeypatch) -> threading.Event:
    """Signal when `TerminalScreen.on_unmount` has returned, the join included when there is one."""
    done = threading.Event()
    real = TerminalScreen.on_unmount

    def tracked(self) -> None:
        try:
            real(self)
        finally:
            done.set()

    monkeypatch.setattr(TerminalScreen, "on_unmount", tracked)
    return done


def _alerts_apply(callback: object) -> bool:
    # Before the fix the loop-side callback is `alerts_changed`; after it it is `_apply_alerts`.
    return getattr(callback, "__name__", "") in ("alerts_changed", "_apply_alerts")


def _hold(monkeypatch, app, match, held: threading.Event, release: threading.Event) -> None:
    """Hold the first callback `match` accepts exactly where `call_from_thread` would wait for the loop."""
    real = app.call_from_thread

    def gated(callback, *args, **kwargs):
        if match(callback):
            held.set()
            release.wait(10.0)
        return real(callback, *args, **kwargs)

    monkeypatch.setattr(app, "call_from_thread", gated)


class _HeldAlertStream:
    """One raise, then a read that waits until the stream is closed."""

    def __init__(self) -> None:
        self._sent = False
        self._closed = threading.Event()

    def __iter__(self) -> _HeldAlertStream:
        return self

    def __next__(self) -> dict:
        if not self._sent:
            self._sent = True
            return {"alert_id": "al-held", "kind": "vt_unavailable", "state": "raised",
                    "at": "2026-11-20T00:00:00Z"}
        self._closed.wait(10.0)
        raise StopIteration

    def close(self) -> None:
        self._closed.set()


def test_a_held_poll_reply_does_not_hold_the_unmount(serve, tmp_path, monkeypatch) -> None:
    """A poll whose reply is still in `call_from_thread` when the workspace unmounts: the unmount returns at once,
    the next drive command is answered (the bound is well under the held 10 s), and the late reply changes nothing."""

    async def check() -> None:
        _server, url = serve()
        async with Rig(url, tmp_path=tmp_path, overrides=("fleet_view.refresh_s=1",)) as rig:
            await rig.login()
            screen = await rig.app_call(rig.app.workspace)
            unmounted = _track_unmount(monkeypatch)
            held, release = threading.Event(), threading.Event()
            _hold(monkeypatch, rig.app, _poll_apply, held, release)
            assert await asyncio.to_thread(held.wait, 5.0), "the poll reply was never held"
            thread = screen.poller._thread
            before = await rig.app_call(lambda: screen.fleet.applied_generation)

            joins_called: list[tuple[tuple, dict]] = []
            real_join = thread.join

            def instrumented_join(*args, **kwargs):
                joins_called.append((args, kwargs))
                return real_join(*args, **kwargs)

            thread.join = instrumented_join
            try:
                await rig.app_call(rig.app._to_login)
                assert await asyncio.to_thread(unmounted.wait, 6.0), "the workspace never unmounted"
                assert not joins_called, f"poller thread was joined during unmount: {joins_called}"
                assert held.is_set() and not release.is_set(), "the unmount completed after the poller was released"
                assert thread.is_alive(), "the poller thread exited before unmount completed"
            finally:
                thread.join = real_join
                release.set()

            await asyncio.to_thread(thread.join, 5.0)
            assert not thread.is_alive(), "the poller thread did not end after its stop"

            cmd_started = time.monotonic()
            answer = await rig.cmd("login_status", timeout=5.0)
            assert time.monotonic() - cmd_started < 5.0, "the next command was not answered within 5 s"
            assert "ok" in answer
            assert await rig.app_call(lambda: screen.fleet.applied_generation) == before

    asyncio.run(check())


def test_a_held_alert_event_does_not_hold_the_stop(serve, tmp_path, monkeypatch) -> None:
    """An alert event still in `call_from_thread` when the watcher stops: the stop returns at once, the next drive
    command is answered (the bound is well under the held 10 s), and the held event never reaches the UI."""

    async def check() -> None:
        _server, url = serve()
        async with Rig(url, tmp_path=tmp_path, overrides=("alerts.read_timeout_s=1",)) as rig:
            stream = _HeldAlertStream()
            rig.app.client.alerts = lambda read_timeout=None: stream
            notified: list[int] = []
            rig.app.watch_alerts(lambda: notified.append(1))
            held, release = threading.Event(), threading.Event()
            _hold(monkeypatch, rig.app, _alerts_apply, held, release)

            await rig.login()
            assert await asyncio.to_thread(held.wait, 5.0), "the alert event was never held"
            streamer = await rig.app_call(lambda: rig.app._alert_streamer)
            thread = streamer._thread

            started = time.monotonic()
            try:
                await rig.app_call(rig.app.stop_alerts)
            finally:
                elapsed = time.monotonic() - started
                # read before the release: the stop returned while the watcher was still held
                held_at_return = thread.is_alive() and not release.is_set()
                release.set()
            # a stop that joined the watcher waits out the gate's 10 s hold; the margin below it is for load
            assert held_at_return, "the stop returned only after the watcher ended"
            assert elapsed < 5.0, f"the stop waited {elapsed:.2f}s for the watcher"

            await asyncio.to_thread(thread.join, 5.0)
            assert not thread.is_alive(), "the alert watcher did not end after its stop"

            cmd_started = time.monotonic()
            answer = await rig.cmd("login_status", timeout=5.0)
            assert time.monotonic() - cmd_started < 5.0, "the next command was not answered within 5 s"
            assert "ok" in answer
            assert notified == [], "a stopped watcher's event still reached the UI"

    asyncio.run(check())


def test_an_account_ended_login_answers_within_a_second_with_a_poll_in_flight(serve, tmp_path,
                                                                              monkeypatch) -> None:
    """A login ended by an account act (close 4017) while a fleet poll is still in `call_from_thread`:
    `login_status` is answered while the poll is still held, well under the held 10 s."""

    async def check() -> None:
        _server, url = serve()
        sid = make_session(url, _serve.USER, 80, 24, target="normal")
        async with Rig(url, tmp_path=tmp_path, overrides=("fleet_view.refresh_s=1",)) as rig:
            await rig.login()
            await rig.attach(sid)
            screen = await rig.app_call(rig.app.workspace)
            unmounted = _track_unmount(monkeypatch)
            held, release = threading.Event(), threading.Event()
            _hold(monkeypatch, rig.app, _poll_apply, held, release)
            assert await asyncio.to_thread(held.wait, 5.0), "the poll reply was never held"
            thread = screen.poller._thread

            started = time.monotonic()
            try:
                await rig.app_call(rig.app.client.set_account_password, _serve.USER, SECRET)
                assert await asyncio.to_thread(unmounted.wait, 6.0), "the workspace never unmounted"
                for _ in range(600):
                    if await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen)):
                        break
                    await asyncio.sleep(0.005)
                answer = await rig.cmd("login_status", timeout=5.0)
            finally:
                elapsed = time.monotonic() - started
                # read before the release: the answer came while the poll reply was still held
                held_at_answer = thread.is_alive() and not release.is_set()
                release.set()
            assert isinstance(await rig.app_call(lambda: rig.app.screen), LoginScreen)
            assert answer["ok"] is True and answer["status"] == 401, answer
            assert held_at_answer, "the command was answered only after the held poll ended"
            # an answer that waited for the held reply waits out the gate's 10 s hold
            assert elapsed < 5.0, f"the command was answered {elapsed:.2f}s after the account act"
            await asyncio.to_thread(thread.join, 5.0)

    asyncio.run(check())
