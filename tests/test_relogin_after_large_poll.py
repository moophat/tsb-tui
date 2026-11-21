"""`login` after `logout` with a large session list loading (the re-login stall, v0.11.0).

Why this matters: on the lab the owner's drive fetched `GET /sessions` (~28,000 records) one second before
`logout`, and after `logout` no `POST /auth/login` reached the server before drive's 30 s wait ran out, with every
executor thread idle. This drives the same shape against the contract's fixture server: a client whose `sessions()`
returns ~28,000 records and completes slowly, timed so its reply lands before, during and after `logout`, then
`login`. It asserts `login` answers ok within drive's wait, that the server saw `POST /auth/login` before the answer,
and that a late poll reply changed nothing. A failing run records what the diagnosis needs to separate its two
candidates: whether `LoginScreen._attempt_login` ran, whether `call_api`'s worker reached the executor, what
`app.focused` and `app.screen` were when drive pressed enter, and the state of `#login` and `#login-error` at the
timeout. Tests only — no product change; the instrument is test-side wrappers, spies and monkeypatches.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from drive_support import Rig
from tsb_contract_client import Client
from tsbc import _serve

from tsb_tui.screens.login import LoginScreen

RECORD_COUNT = 28000
# The rig's own login wait is `drive.wait_timeout_ms` (30 s); give the answer a little more room so a timeout is
# drive's, not the test's.
DRIVE_WAIT_MS = 30000
CMD_TIMEOUT_S = DRIVE_WAIT_MS / 1000.0 + 5.0

_BASE = {
    "target_id": "fixture-target",
    "connection_id": "fixture-conn-1",
    "session_type": "shell",
    "state": "live",
    "tier": 1,
    "owner": _serve.USER,
    "control_holder": None,
    "created_at": "2026-10-06T12:00:00Z",
    "term": "xterm-256color",
    "cols": 120,
    "rows": 40,
    "recording": {"active": True},
}


def make_records(count: int) -> list[dict]:
    """`count` session records of the contract's valid shape, each with a distinct id."""
    out = []
    for i in range(count):
        rec = dict(_BASE)
        rec["id"] = f"s{i:06d}"
        out.append(rec)
    return out


class Recorder:
    """Everything the diagnosis needs to read a failing run, recorded by the test-side spies below."""

    def __init__(self) -> None:
        self.attempt_login_runs = 0
        self.focus_at_attempt: list[str] = []
        self.screen_at_attempt: list[str] = []
        self.call_api_calls: list[str] = []
        self.login_worker_ran = False
        self.login_thread = None
        self.focus_at_enter = None
        self.screen_at_enter = None
        self.button_disabled = None
        self.error_text = None
        # Round 2 (the real load path): every real `Client.sessions()` poll as (start, end, thread), and the two
        # marks a poll's in-flight window is read against.
        self.polls: list[tuple[float, float, str]] = []
        self.logout_mark = None
        self.enter_mark = None

    def in_flight_at(self, mark: float | None) -> int:
        """How many real polls were running (started, not ended) at `mark`."""
        if mark is None:
            return 0
        return sum(1 for start, end, _ in self.polls if start <= mark < end)

    def poll_measures(self) -> str:
        """The measured real-poll durations: count, the longest and the mean fetch-and-decode time."""
        if not self.polls:
            return "no real poll measured"
        times = [end - start for start, end, _ in self.polls]
        return f"{len(times)} real polls; longest {max(times):.3f}s; mean {sum(times) / len(times):.3f}s"

    def evidence(self) -> str:
        return (
            f"_attempt_login ran {self.attempt_login_runs} time(s); "
            f"call_api calls={self.call_api_calls}; login worker ran={self.login_worker_ran} "
            f"on thread={self.login_thread}; app.focused at enter={self.focus_at_enter}; "
            f"app.screen at enter={self.screen_at_enter}; #login-button disabled={self.button_disabled}; "
            f"#login-error={self.error_text!r}"
        )


def install_instrumentation(rig: Rig, monkeypatch, rec: Recorder, records: list[dict]) -> threading.Event:
    """The test-side spies: record the dispatch links the diagnosis separates, and gate the first big poll."""
    gate = threading.Event()
    calls: list[float] = []

    def fake_sessions(*args, **kwargs) -> list[dict]:
        calls.append(time.monotonic())
        if len(calls) == 1:
            gate.wait(15.0)  # the reply is held exactly where the diagnosis wants it
        else:
            time.sleep(0.2)
        return records

    monkeypatch.setattr(rig.app.client, "sessions", fake_sessions)

    real_attempt = LoginScreen._attempt_login

    def attempt(self) -> None:
        rec.attempt_login_runs += 1
        rec.focus_at_attempt.append(repr(self.app.focused))
        rec.screen_at_attempt.append(type(self.app.screen).__name__)
        real_attempt(self)

    monkeypatch.setattr(LoginScreen, "_attempt_login", attempt)

    real_call_api = rig.app.call_api

    def call_api(func, *args, **kwargs) -> None:
        rec.call_api_calls.append(getattr(func, "__qualname__", str(func)))
        real_call_api(func, *args, **kwargs)

    monkeypatch.setattr(rig.app, "call_api", call_api)

    real_login = Client.login

    def login(self, *args, **kwargs):
        rec.login_worker_ran = True
        rec.login_thread = threading.current_thread().name
        return real_login(self, *args, **kwargs)

    monkeypatch.setattr(Client, "login", login)

    real_press = rig.pilot._pilot.press

    async def press(*args, **kwargs):
        rec.focus_at_enter = repr(rig.app.focused)
        rec.screen_at_enter = type(rig.app.screen).__name__
        return await real_press(*args, **kwargs)

    monkeypatch.setattr(rig.pilot._pilot, "press", press)
    return gate


def _login_state(rig: Rig) -> tuple[bool, str]:
    """`#login-button` disabled and `#login-error` content on the login screen that is up now."""
    from textual.widgets import Button, Static

    screen = rig.app.screen
    button = screen.query_one("#login-button", Button)
    error = screen.query_one("#login-error", Static)
    return bool(button.disabled), str(error.content)


def seed_sessions(serve_app, count: int) -> None:
    """Put `count` detached shell sessions into the served store, the way the other tests seed sessions, so the
    workspace's poll is the real `Client.sessions()` over HTTP with the contract client's own decode and validation."""
    for i in range(count):
        sid = f"seed{i:06d}"
        serve_app.sessions[sid] = {
            "id": sid, "target_id": "normal", "connection_id": f"conn{i:06d}", "session_type": "shell",
            "state": "detached", "tier": 1, "owner": _serve.USER, "control_holder": None, "owner_revoked": False,
            "created_at": "2026-09-23T12:00:00Z", "term": "xterm-256color", "cols": 80, "rows": 24,
            "output": [], "next_seq": 0, "stale": None, "stale_wanted": False,
        }


def install_real_instrumentation(rig: Rig, monkeypatch, rec: Recorder) -> None:
    """The round-1 spies, plus a wrapper on the real `Client.sessions()` that measures each fetch-and-decode and
    marks every enter press, so a poll's in-flight window is read against the logout and login marks."""
    real_sessions = Client.sessions

    def sessions(self, *args, **kwargs):
        start = time.monotonic()
        try:
            return real_sessions(self, *args, **kwargs)
        finally:
            rec.polls.append((start, time.monotonic(), threading.current_thread().name))

    monkeypatch.setattr(Client, "sessions", sessions)

    real_attempt = LoginScreen._attempt_login

    def attempt(self) -> None:
        rec.attempt_login_runs += 1
        rec.focus_at_attempt.append(repr(self.app.focused))
        rec.screen_at_attempt.append(type(self.app.screen).__name__)
        real_attempt(self)

    monkeypatch.setattr(LoginScreen, "_attempt_login", attempt)

    real_call_api = rig.app.call_api

    def call_api(func, *args, **kwargs) -> None:
        rec.call_api_calls.append(getattr(func, "__qualname__", str(func)))
        real_call_api(func, *args, **kwargs)

    monkeypatch.setattr(rig.app, "call_api", call_api)

    real_login = Client.login

    def login(self, *args, **kwargs):
        rec.login_worker_ran = True
        rec.login_thread = threading.current_thread().name
        return real_login(self, *args, **kwargs)

    monkeypatch.setattr(Client, "login", login)

    real_press = rig.pilot._pilot.press

    async def press(*args, **kwargs):
        rec.focus_at_enter = repr(rig.app.focused)
        rec.screen_at_enter = type(rig.app.screen).__name__
        rec.enter_mark = time.monotonic()  # the last press is the login form's enter
        return await real_press(*args, **kwargs)

    monkeypatch.setattr(rig.pilot._pilot, "press", press)


def _relogin_with_real_large_load(serve, tmp_path, monkeypatch) -> None:
    async def run() -> None:
        server_app, url = serve()
        seed_sessions(server_app, RECORD_COUNT)  # the served store holds ~28,000 records
        rec = Recorder()
        # `refresh_s` as the app ships it (5 s): polls run on their own schedule around end, logout and login.
        async with Rig(url, tmp_path=tmp_path) as rig:
            install_real_instrumentation(rig, monkeypatch, rec)

            await rig.login()
            old_screen = await rig.app_call(rig.app.workspace)
            new = await rig.cmd("new", target="normal")
            sid = new["session"]
            await rig.cmd("type", text="hi\r", look=await rig.look())

            await rig.cmd("end", session=sid, confirm=True)
            await rig.cmd("wait", until={"ended": True}, timeout=CMD_TIMEOUT_S)
            rec.logout_mark = time.monotonic()  # the in-flight window is read against this instant
            await rig.cmd("logout", timeout=CMD_TIMEOUT_S)
            # The baseline is taken after logout, so only a reply landing after logout is judged as late.
            gen_after_logout = await rig.app_call(lambda: old_screen.fleet.applied_generation)

            login_started = time.monotonic()
            try:
                answer = await rig.cmd("login", user=_serve.USER, password_file=rig.password_file(),
                                       timeout=CMD_TIMEOUT_S)
            except TimeoutError:
                rec.button_disabled, rec.error_text = _login_state(rig)
                pytest.fail(f"login did not answer within drive's wait (real load): {rec.poll_measures()} "
                             f"in-flight at logout={rec.in_flight_at(rec.logout_mark)} "
                             f"in-flight at login enter={rec.in_flight_at(rec.enter_mark)} {rec.evidence()}")
            elapsed = time.monotonic() - login_started

            assert answer["ok"], (f"login refused: {answer}; {rec.poll_measures()} "
                                   f"in-flight at logout={rec.in_flight_at(rec.logout_mark)} "
                                   f"in-flight at login enter={rec.in_flight_at(rec.enter_mark)} {rec.evidence()}")
            assert elapsed * 1000 < DRIVE_WAIT_MS, f"login answered after {elapsed:.2f}s (drive's wait is 30 s)"
            logins = [p for method, p, _ in server_app.requests if (method, p) == ("POST", "/auth/login")]
            assert logins, f"no POST /auth/login reached the server (real load): {rec.evidence()}"
            # A late poll reply must change nothing on the workspace it belonged to.
            assert await rig.app_call(lambda: old_screen.fleet.applied_generation) == gen_after_logout, (
                f"a late poll reply changed the fleet after logout (real load): {rec.evidence()}")
            print(f"[real] {rec.poll_measures()} in-flight at logout={rec.in_flight_at(rec.logout_mark)} "
                  f"in-flight at login enter={rec.in_flight_at(rec.enter_mark)} login_posts={len(logins)} "
                  f"{rec.evidence()}")

    asyncio.run(run())


def _relogin_after_large_poll(timing: str, serve, tmp_path, monkeypatch) -> None:
    async def run() -> None:
        server_app, url = serve()
        rec = Recorder()
        # One controlled poll: the first call is gated, and `refresh_s` keeps the loop from issuing a second.
        async with Rig(url, tmp_path=tmp_path, overrides=("fleet_view.refresh_s=1000",)) as rig:
            gate = install_instrumentation(rig, monkeypatch, rec, make_records(RECORD_COUNT))

            await rig.login()  # the workspace mounts; its first poll blocks on the gate
            old_screen = await rig.app_call(rig.app.workspace)

            if timing == "before":
                gate.set()  # the big reply lands and applies fully before logout
                await _wait_applied(rig, old_screen)
                gen_baseline = await rig.app_call(lambda: old_screen.fleet.applied_generation)
                assert gen_baseline > 0, f"poll reply did not apply before sign-out: {gen_baseline}"
                await rig.cmd("logout", timeout=CMD_TIMEOUT_S)
            elif timing == "during":
                gen_baseline = await rig.app_call(lambda: old_screen.fleet.applied_generation)
                logout = rig.send("logout")
                await _wait_logout_began(rig, old_screen)
                gate.set()  # the reply is released only after sign-out has begun on the workspace
                await asyncio.wait_for(logout, CMD_TIMEOUT_S)
                await _wait_poller_finished(rig, old_screen)
                assert await rig.app_call(lambda: old_screen.fleet.applied_generation) == gen_baseline, (
                    f"a late poll reply changed the fleet after logout ({timing}): {rec.evidence()}")
            else:  # "after"
                gen_baseline = await rig.app_call(lambda: old_screen.fleet.applied_generation)
                await rig.cmd("logout", timeout=CMD_TIMEOUT_S)
                gate.set()  # the reply lands after logout; it must be dropped
                await _wait_poller_finished(rig, old_screen)
                assert await rig.app_call(lambda: old_screen.fleet.applied_generation) == gen_baseline, (
                    f"a late poll reply changed the fleet after logout ({timing}): {rec.evidence()}")

            gen_after_logout = await rig.app_call(lambda: old_screen.fleet.applied_generation)
            assert gen_after_logout == gen_baseline, (
                f"a late poll reply changed the fleet after logout ({timing}): {rec.evidence()}")
            login_started = time.monotonic()
            try:
                answer = await rig.cmd("login", user=_serve.USER, password_file=rig.password_file(),
                                       timeout=CMD_TIMEOUT_S)
            except TimeoutError:
                rec.button_disabled, rec.error_text = _login_state(rig)
                pytest.fail(f"login did not answer within drive's wait ({timing}): {rec.evidence()}")
            elapsed = time.monotonic() - login_started

            assert answer["ok"], f"login refused: {answer}"
            assert elapsed * 1000 < DRIVE_WAIT_MS, f"login answered after {elapsed:.2f}s (drive's wait is "
            # The POST that produces the workspace must reach the server before the answer.
            logins = [p for method, p, _ in server_app.requests if (method, p) == ("POST", "/auth/login")]
            assert logins, f"no POST /auth/login reached the server ({timing}): {rec.evidence()}"
            # A late poll reply must change nothing on the workspace it belonged to.
            assert await rig.app_call(lambda: old_screen.fleet.applied_generation) == gen_baseline, (
                f"a late poll reply changed the fleet after logout ({timing}): {rec.evidence()}")
            print(f"[{timing}] {rec.evidence()} applied_before_logout="
                  f"{gen_baseline} "
                  f"unmounted={await rig.app_call(lambda: old_screen._unmounted)} "
                  f"login_posts={len(logins)}")

    asyncio.run(run())


async def _wait_applied(rig: Rig, old_screen) -> None:
    """Wait until the gated big reply has been applied to the workspace."""
    for _ in range(600):
        if await rig.app_call(lambda: old_screen.fleet.applied_generation) > 0:
            return
        await asyncio.sleep(0.01)
    pytest.fail("the gated poll reply was never applied before logout")


async def _wait_logout_began(rig: Rig, old_screen) -> None:
    """Wait until sign-out has marked the workspace as begun on its first step (`detach_all`)."""
    for _ in range(200):
        if await rig.app_call(lambda: old_screen._logout_began):
            return
        await asyncio.sleep(0.005)
    pytest.fail("sign-out never began on the workspace")


async def _wait_poller_finished(rig: Rig, old_screen, timeout: float = 5.0) -> None:
    """Wait until the poller worker thread has finished processing."""
    poller = getattr(old_screen, "poller", None)
    if poller is not None:
        await asyncio.to_thread(poller.join, timeout)
        if poller._thread is not None and poller._thread.is_alive():
            pytest.fail(f"the poll worker did not finish within {timeout} s")
    await rig.app_call(lambda: None)


def test_relogin_after_a_poll_reply_before_logout(serve, tmp_path, monkeypatch) -> None:
    _relogin_after_large_poll("before", serve, tmp_path, monkeypatch)


def test_relogin_after_a_poll_reply_during_logout(serve, tmp_path, monkeypatch) -> None:
    _relogin_after_large_poll("during", serve, tmp_path, monkeypatch)


def test_relogin_after_a_poll_reply_after_logout(serve, tmp_path, monkeypatch) -> None:
    _relogin_after_large_poll("after", serve, tmp_path, monkeypatch)


def test_relogin_after_real_large_load(serve, tmp_path, monkeypatch) -> None:
    """The real path: the served store holds ~28,000 records, so the workspace's poll is the contract client's own
    `Client.sessions()` over HTTP (decode + validation), `refresh_s` as shipped; the rig runs its script (new, type,
    end, wait for the end, logout, login)."""
    _relogin_with_real_large_load(serve, tmp_path, monkeypatch)


def test_drive_presses_enter_only_once_password_holds_focus(serve, tmp_path, monkeypatch) -> None:
    """Ruling 1 (the press half): drive presses enter only once the LoginScreen is active and `#password` holds the
    focus. At the login form's enter, the focused widget is the password field."""

    async def run() -> None:
        _server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.cmd("logout", timeout=CMD_TIMEOUT_S)

            focused_at_press: list[object] = []
            real_press = rig.pilot._pilot.press

            async def press(*args, **kwargs):
                focused_at_press.append(rig.app.focused)
                return await real_press(*args, **kwargs)

            monkeypatch.setattr(rig.pilot._pilot, "press", press)
            answer = await rig.cmd("login", user=_serve.USER, password_file=rig.password_file(),
                                   timeout=CMD_TIMEOUT_S)
            assert answer["ok"], answer
            # At the login form's enter (the last press) the focused widget is the password field.
            assert getattr(focused_at_press[-1], "id", None) == "password", focused_at_press

    asyncio.run(run())


def test_drive_answers_login_not_submitted_when_password_focus_cannot_land(serve, tmp_path, monkeypatch) -> None:
    """Ruling 1 (the fail-loud half): when the password field never takes the focus, drive answers `login_not_submitted`
    with the reason that the login form could not take the focus; nothing was sent to TSB, never a silent `timeout`."""

    async def run() -> None:
        _server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.cmd("logout", timeout=CMD_TIMEOUT_S)
            from textual.widgets import Input

            monkeypatch.setattr(Input, "focus", lambda self, *args, **kwargs: None)
            # The form has no focused widget, and the focus never lands: drive must not press into an unfocused
            # screen, and must answer login_not_submitted, not a silent timeout.
            await rig.app_call(lambda: rig.app.set_focus(None))
            answer = await rig.cmd("login", user=_serve.USER, password_file=rig.password_file(),
                                   timeout=CMD_TIMEOUT_S)
            assert not answer["ok"], answer
            assert answer.get("error") == "login_not_submitted", answer
            assert answer.get("why") == "the login form could not take the focus; nothing was sent to TSB", answer

    asyncio.run(run())


def test_refresh_status_with_no_canvases_raises_nothing(serve, tmp_path) -> None:
    """Ruling 2: `refresh_status` returns quietly when the terminal is unmounted or `#canvases` is absent, so the
    logout screen-swap's `EndedScreen.on_unmount` never raises `NoMatches` into the terminal."""

    async def run() -> None:
        _server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            screen = await rig.app_call(rig.app.workspace)
            await rig.app_call(lambda: screen.query_one("#canvases").remove())
            await rig.app_call(screen.refresh_status)  # must not raise

    asyncio.run(run())


def test_a_reply_after_logout_began_leaves_the_fleet_generation_unchanged(serve, tmp_path) -> None:
    """Ruling 3: sign-out's first step (`detach_all`) marks logout as begun, and a poll reply that lands after that
    is dropped — the fleet's generation never advances on a workspace being signed out of."""

    async def run() -> None:
        _server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            screen = await rig.app_call(rig.app.workspace)
            before = await rig.app_call(lambda: screen.fleet.applied_generation)
            await rig.app_call(screen.detach_all)  # sign-out's first step, on the workspace
            assert await rig.app_call(lambda: screen._logout_began), "detach_all did not mark logout as begun"
            recs = make_records(10)
            await rig.app_call(lambda: screen._poll_reply(before + 1, recs))
            await rig.app_call(lambda: screen._apply_poll(before + 1, recs))
            assert await rig.app_call(lambda: screen.fleet.applied_generation) == before

    asyncio.run(run())
