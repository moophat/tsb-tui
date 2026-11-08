"""The screen lock, the activity signal and lock now (phase 12 § F, § G; cred §5.5; Frontend §10.4; screen-lock; vault-lock;
phase-04 exit 6).

Why it matters (Rule 9): the screen lock is the guardrail between a walk-away and the session on the screen, so what is
graded is what a person at a left-open terminal could do. (1) After the screen-lock time the core served, with no input,
every screen is covered: a dump of the screen holds no session byte and no session's name (not the terminal, not the
sidebar), and a toast or a dialog raised meanwhile is held back until the unlock. (2) Keys typed then reach the lock screen's
password field only: the server records no input for the session. (3) The session is current when it resumes: output that
arrived behind the lock is on the grid. (4) A wrong password (403) keeps the lock and the login (the core was not asked
to end it); a 429 shows the core's own message; a 401 means the login really ended and the TUI goes to its login screen;
the right password resumes it. The passphrase never resumes it and the lock never unlocks the vault (no unlock is sent).
(5) The time is the served value, re-read when the TUI reads `/auth/me`, never a constant. (6) The activity signal is sent
at most once per the interval the settings carry, however much is typed, and not before there is a login; typing on the
lock screen sends none, and the first input after a correct password re-check signals as usual (ruling 2026-10-31 16:25). (7) Lock now
calls the operation and locks this screen, from a key and from the Menu. Server: `tsbc serve` (`VaultServe` with the lock
settings it answers, and the input-recording fixture); time is a fake clock the TUI reads, so the tests wait on no timer.
"""

from __future__ import annotations

import asyncio

from input_server import InputRecordingApp, flushed
from stub_server import emit_live_output, record_output
from textual.screen import Screen
from textual.widgets import Input, Static
from tsbc import _serve
from vault_support import CHAIN, VaultServe
from workspace_support import fixture_login, named_sessions, open_here

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.lock import LockScreen
from tsb_tui.screens.login import LoginScreen

SECRET_1 = b"SESSION-BYTE-ONE-d41d8cd9\r\n"
SECRET_2 = b"SESSION-BYTE-TWO-98f00b20\r\n"


class RecordingVaultServe(VaultServe, InputRecordingApp):
    """The vault server that also records every input frame a client sends."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.screen_limited = False

    def vault_http(self, method, path, headers, body, query, user):
        if (method, path) == ("POST", "/auth/screen-unlock") and self.screen_limited:
            return self._err(429, "rate_limited", "too many failed attempts; try again later")
        return super().vault_http(method, path, headers, body, query, user)


class Clock:
    """The idle clock the TUI reads: a test moves it, so nothing waits on a real timer."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _wire(app: TsbTuiApp) -> Clock:
    clock = Clock()
    app.idle_clock = clock
    app.screen_lock_poll = 0.02
    return clock


async def _workspace_with_session(app, pilot, serve_app, url, wait_until):
    workspace = await fixture_login(app, pilot)
    sid, = named_sessions(url, serve_app, "rtr-7")
    record_output(serve_app, sid, SECRET_1)
    await open_here(app, pilot, wait_until, sid)
    await wait_until(pilot, lambda: "SESSION-BYTE-ONE" in _dump(app), "the session's first output")
    return workspace, sid


def _dump(app) -> str:
    return "\n".join(strip.text.rstrip() for strip in app.screen._compositor.render_strips())


async def _locked(app, pilot, wait_until, clock: Clock, after: float) -> LockScreen:
    clock.now += after
    await wait_until(pilot, lambda: isinstance(app.screen, LockScreen) and app.screen.is_mounted, "the lock screen")
    return app.screen


async def _unlock_with(screen: LockScreen, password: str) -> None:
    screen.query_one("#lock-password", Input).value = password
    await screen.query_one("#lock-password", Input).action_submit()


def test_after_the_served_time_every_screen_is_covered_and_resuming_shows_the_output_that_came_meanwhile(
        serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(RecordingVaultServe(locks={"screen_lock_after": 30}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            workspace, sid = await _workspace_with_session(app, pilot, serve_app, url, wait_until)
            workspace.open_sidebar()
            await wait_until(pilot, lambda: "rtr-7" in _dump(app), "the session named on screen")
            clock.now += 29
            await pilot.pause(0.1)
            assert not isinstance(app.screen, LockScreen), "not before the time"
            lock = await _locked(app, pilot, wait_until, clock, 2)
            dump = _dump(app)
            assert "SESSION-BYTE-ONE" not in dump and "rtr-7" not in dump, "no session content shows anywhere"
            app.toast("session rtr-7 ended: SESSION-BYTE-ONE", title="ended")
            app.notify("SESSION-BYTE-ONE", title="x", markup=False)
            await pilot.pause(0.1)
            assert "SESSION-BYTE-ONE" not in _dump(app) and "rtr-7" not in _dump(app), "no toast over the lock"
            emit_live_output(serve_app, serve_app.sessions[sid], SECRET_2)      # the session runs on
            await pilot.pause(0.2)
            await _unlock_with(lock, _serve.PASSWORD)
            await wait_until(pilot, lambda: app.screen is workspace, "the workspace to resume")
            await wait_until(pilot, lambda: "SESSION-BYTE-TWO" in _dump(app),
                             "the output that arrived behind the lock to be current")

    asyncio.run(check())


def test_a_screen_pushed_while_locked_waits_for_the_unlock(serve, wait_until) -> None:
    class Late(Screen):
        def compose(self):
            yield Static("SESSION-BYTE-ONE in a late dialog", markup=False)

    async def check() -> None:
        _serve_app, url = serve(RecordingVaultServe(locks={"screen_lock_after": 30}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            await fixture_login(app, pilot)
            lock = await _locked(app, pilot, wait_until, clock, 31)
            app.push_screen(Late())
            await pilot.pause(0.1)
            assert app.screen is lock and "late dialog" not in _dump(app)
            await _unlock_with(lock, _serve.PASSWORD)
            await wait_until(pilot, lambda: isinstance(app.screen, Late), "the held screen to appear after the unlock")

    asyncio.run(check())


def test_keys_typed_while_locked_reach_only_the_password_field(serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(RecordingVaultServe(locks={"screen_lock_after": 30}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            workspace, sid = await _workspace_with_session(app, pilot, serve_app, url, wait_until)
            slot = workspace.slot_for(sid)
            lock = await _locked(app, pilot, wait_until, clock, 31)
            await pilot.press("l", "s", "ctrl+q", "ctrl+backslash", "x")
            await flushed(pilot, workspace, serve_app, wait_until)
            assert serve_app.received(slot.pump.state.client_id) == [], "no key reached the session"
            assert app.screen is lock, "and none dismissed the lock, quit or opened the Menu"
            assert lock.query_one("#lock-password", Input).value == "lsx", "they went to the password field"

    asyncio.run(check())


def test_a_wrong_password_keeps_the_lock_and_the_login_and_the_right_one_resumes(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(RecordingVaultServe(locks={"screen_lock_after": 30}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            workspace = await fixture_login(app, pilot)
            lock = await _locked(app, pilot, wait_until, clock, 31)
            assert lock.query_one("#lock-password", Input).password
            await _unlock_with(lock, "not the password")
            await wait_until(pilot, lambda: "that password is not right" in screen_text(app), "the core's refusal")
            assert app.screen is lock and lock.query_one("#lock-password", Input).value == ""
            assert app.client.me()["username"] == _serve.USER, "the login is in place"
            await _unlock_with(lock, _serve.PASSWORD)
            await wait_until(pilot, lambda: app.screen is workspace, "the right password resumes")
            assert [b["password"] for b in serve_app.calls("POST", "/auth/screen-unlock")] == [
                "not the password", _serve.PASSWORD]
            assert serve_app.unlock_bodies == [] and not serve_app.calls("GET", "/vault/salt"), (
                "the screen-lock password never unlocks the vault")

    asyncio.run(check())


def test_the_vault_passphrase_does_not_resume_the_lock(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        _serve_app, url = serve(RecordingVaultServe(locks={"screen_lock_after": 30}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            await fixture_login(app, pilot)
            lock = await _locked(app, pilot, wait_until, clock, 31)
            await _unlock_with(lock, CHAIN.passphrase)
            await wait_until(pilot, lambda: "that password is not right" in screen_text(app), "refused")
            assert app.screen is lock

    asyncio.run(check())


def test_a_429_shows_the_cores_message_and_stays_locked(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(RecordingVaultServe(locks={"screen_lock_after": 30}))
        serve_app.screen_limited = True
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            await fixture_login(app, pilot)
            lock = await _locked(app, pilot, wait_until, clock, 31)
            await _unlock_with(lock, _serve.PASSWORD)
            await wait_until(pilot, lambda: "too many failed attempts; try again later" in screen_text(app), "the message")
            assert app.screen is lock

    asyncio.run(check())


def test_a_401_means_the_login_ended_and_the_tui_returns_to_its_login_screen(serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(RecordingVaultServe(locks={"screen_lock_after": 30}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            await fixture_login(app, pilot)
            lock = await _locked(app, pilot, wait_until, clock, 31)
            serve_app.logins.clear()
            await _unlock_with(lock, _serve.PASSWORD)
            await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen) and app.screen.is_mounted,
                             "the login screen")
            assert not any(isinstance(s, LockScreen) for s in app.screen_stack)

    asyncio.run(check())


def test_the_time_is_the_served_value_and_is_re_read_when_the_tui_reads_auth_me(serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(RecordingVaultServe(locks={"screen_lock_after": 100}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            workspace = await fixture_login(app, pilot)
            clock.now += 5
            await pilot.pause(0.1)
            assert not isinstance(app.screen, LockScreen), "100 seconds were served"
            serve_app.locks["screen_lock_after"] = 2          # the core's resolver now says 2
            await workspace.run_action_id("new_session")       # the dialog reads GET /auth/me
            await wait_until(pilot, lambda: len(serve_app.calls("GET", "/auth/me")) >= 1, "the /auth/me read")
            await pilot.pause(0.1)
            await _locked(app, pilot, wait_until, clock, 0)

    asyncio.run(check())


def test_two_served_values_give_two_lock_times(serve, wait_until) -> None:
    async def run(after: int) -> bool:
        _serve_app, url = serve(RecordingVaultServe(locks={"screen_lock_after": after}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            await fixture_login(app, pilot)
            clock.now += 4
            await pilot.pause(0.15)
            return isinstance(app.screen, LockScreen)

    async def check() -> None:
        assert await run(3) is True
        assert await run(8) is False

    asyncio.run(check())


def test_input_resets_the_idle_time(serve, wait_until) -> None:
    async def check() -> None:
        _serve_app, url = serve(RecordingVaultServe(locks={"screen_lock_after": 30}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            await fixture_login(app, pilot)
            clock.now += 20
            await pilot.press("j")
            clock.now += 20      # 40 s since the start, 20 since the key
            await pilot.pause(0.15)
            assert not isinstance(app.screen, LockScreen)
            clock.now += 11
            await _locked(app, pilot, wait_until, clock, 0)

    asyncio.run(check())


def test_a_new_tui_logs_in_with_the_password_again(serve, wait_until) -> None:
    """The cookie lives in memory only: a second TUI against the same core starts at its login screen."""

    async def check() -> None:
        _serve_app, url = serve(RecordingVaultServe())
        first = TsbTuiApp(url)
        async with first.run_test(size=(120, 40)) as pilot:
            await fixture_login(first, pilot)
        second = TsbTuiApp(url)
        async with second.run_test(size=(120, 40)) as pilot:
            assert isinstance(second.screen, LoginScreen)
            try:
                second.client.me()
            except Exception as exc:     # noqa: BLE001
                assert getattr(exc, "status", None) == 401
            else:
                raise AssertionError("a new TUI held a login")

    asyncio.run(check())


def test_the_activity_signal_is_sent_at_most_once_per_interval_under_continuous_input(serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(RecordingVaultServe(locks={"activity_interval": 60, "screen_lock_after": 100000}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            await pilot.press("x")       # before any login: nothing to signal
            await pilot.pause(0.1)
            assert serve_app.calls("POST", "/auth/activity") == []
            await fixture_login(app, pilot)
            assert serve_app.calls("POST", "/auth/activity") == [], "no input yet, no signal"
            for _ in range(12):
                clock.now += 1
                await pilot.press("j")
            await wait_until(pilot, lambda: len(serve_app.calls("POST", "/auth/activity")) >= 1, "the first signal")
            await pilot.pause(0.2)
            assert len(serve_app.calls("POST", "/auth/activity")) == 1, "twelve keys in 12 s are one signal"
            clock.now += 60
            await pilot.press("j")
            await wait_until(pilot, lambda: len(serve_app.calls("POST", "/auth/activity")) == 2, "the next interval's signal")
            await pilot.pause(0.2)
            assert len(serve_app.calls("POST", "/auth/activity")) == 2

    asyncio.run(check())


def test_typing_on_the_lock_screen_sends_no_activity_signal_and_the_first_input_after_a_correct_recheck_does(
        serve, wait_until, screen_text) -> None:
    """A locked, unattended screen must not keep the vault open by itself (ruling 2026-10-31 16:25): what is typed on the lock
    screen is not the user's activity, a wrong password is not a re-check, and the signal resumes with the first input after
    the core accepted the password."""

    async def check() -> None:
        serve_app, url = serve(RecordingVaultServe(locks={"activity_interval": 5, "screen_lock_after": 30}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            await fixture_login(app, pilot)
            await pilot.press("j")
            await wait_until(pilot, lambda: len(serve_app.calls("POST", "/auth/activity")) == 1, "the signal before the lock")
            lock = await _locked(app, pilot, wait_until, clock, 31)
            for key in "abcdefghij":
                clock.now += 10                  # every key falls in a new interval: only the lock can withhold the signal
                await pilot.press(key)
            await _unlock_with(lock, "not the password")
            await wait_until(pilot, lambda: "that password is not right" in screen_text(app), "the refusal")
            for key in "klmn":
                clock.now += 10
                await pilot.press(key)
            await pilot.pause(0.3)
            assert len(serve_app.calls("POST", "/auth/activity")) == 1, "no signal for anything typed while locked"
            await _unlock_with(lock, _serve.PASSWORD)
            await wait_until(pilot, lambda: not isinstance(app.screen, LockScreen), "the right password resumes")
            await pilot.pause(0.3)
            assert len(serve_app.calls("POST", "/auth/activity")) == 1, "the re-check itself is no activity signal"
            clock.now += 10
            await pilot.press("j")
            await wait_until(pilot, lambda: len(serve_app.calls("POST", "/auth/activity")) == 2,
                             "the first signal after the unlock")

    asyncio.run(check())


def test_the_activity_interval_is_the_served_one(serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(RecordingVaultServe(locks={"activity_interval": 5, "screen_lock_after": 100000}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            await fixture_login(app, pilot)
            await pilot.press("j")
            clock.now += 6
            await pilot.press("j")
            await wait_until(pilot, lambda: len(serve_app.calls("POST", "/auth/activity")) == 2, "two signals 6 s apart")

    asyncio.run(check())


def test_lock_now_calls_the_operation_and_locks_this_screen_from_a_key_and_from_the_menu(
        serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(RecordingVaultServe(locks={"screen_lock_after": 100000}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = await fixture_login(app, pilot)
            serve_app.unlocked.update(serve_app.logins)
            entries = {action.id: keys for action, keys, _why in workspace.menu_entries()}
            assert entries.get("lock_now"), "a Menu entry, with its key"
            leader_key = entries["lock_now"].split()[-1]
            await pilot.press("ctrl+backslash", leader_key)
            await wait_until(pilot, lambda: isinstance(app.screen, LockScreen), "the lock screen")
            await wait_until(pilot, lambda: len(serve_app.calls("POST", "/auth/lock")) == 1, "the operation")
            assert serve_app.unlocked == set(), "the vault's unlocks are purged"
            await _unlock_with(app.screen, _serve.PASSWORD)
            await wait_until(pilot, lambda: app.screen is workspace, "resumed")
            await workspace.run_action_id("lock_now")             # what the Menu's entry runs
            await wait_until(pilot, lambda: isinstance(app.screen, LockScreen), "the lock screen again")
            await wait_until(pilot, lambda: len(serve_app.calls("POST", "/auth/lock")) == 2, "the second operation")

    asyncio.run(check())


def test_a_me_answer_held_past_the_login_lock_waits_for_unlock(serve, wait_until):
    """A slow Me read must neither replace the lock screen nor reveal a workspace behind it."""
    import threading

    from tsb_tui.screens.terminal import TerminalScreen

    class HeldMe(VaultServe):
        def __init__(self):
            super().__init__(locks={"screen_lock_after": 1})
            self.me_started = threading.Event()
            self.release_me = threading.Event()

        def http(self, method, path, headers, body):
            if (method, path) == ("GET", "/auth/me"):
                self.me_started.set()
                assert self.release_me.wait(10), "the held Me response was not released"
            return super().http(method, path, headers, body)

    async def check(current_login):
        server, url = serve(HeldMe())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            login = app.screen
            login.query_one("#username", Input).value = _serve.USER
            login.query_one("#password", Input).value = _serve.PASSWORD
            await pilot.click("#login-button")
            try:
                await wait_until(pilot, server.me_started.is_set, "the held post-login Me read")
                lock = await _locked(app, pilot, wait_until, clock, 2)
                assert app.workspace() is None
                server.release_me.set()
                await wait_until(pilot, lambda: bool(app.screen_lock._after), "the deferred workspace switch")
                assert app.screen is lock and app.workspace() is None
                assert not any(isinstance(screen, TerminalScreen) for screen in app.get_screen_stack())
                if not current_login:
                    app._login_gen += 1
                await _unlock_with(lock, _serve.PASSWORD)
                await wait_until(pilot, lambda: not app.screen_lock.locked and not app.screen_lock._after,
                                 "unlock and deferred callbacks")
                await pilot.pause()
                if current_login:
                    await wait_until(pilot, lambda: isinstance(app.screen, TerminalScreen), "the workspace after unlock")
                else:
                    assert app.screen is login and app.workspace() is None
            finally:
                server.release_me.set()

    asyncio.run(check(True))
    asyncio.run(check(False))
