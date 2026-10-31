"""What the screen lock does when something else happens while it is up (E4 phase 12, Qodo's findings 1, 2, 3 and 5 on #494;
the orchestrator's ruling of 2026-10-31 23:15).

Why it matters (Rule 9): the lock covers the whole stack, and the app's own `pop_screen` refuses while it is up. Four things
must hold. (1) A session that ends under the lock never makes the UI thread loop on a pop the lock refuses: its ended dialog
waits and is shown after the unlock. (2) A passphrase prompt the flow closed while the lock covered it is not left as a spent
prompt: it dismisses itself as soon as it is on top again. (3) A screen-unlock answer counts only for the lock screen, and the
login, that sent it: a late success from before a sign-out never lifts a newer login's lock. (4) A failed lock-now says "the
vault lock was not confirmed" for the current login; one whose login was replaced is settled quietly and logged, and never
shown over the newer login, whose own lock stays. Server: `tsbc serve` (`VaultServe`), here with an answer held back after the
server has handled the request, so a test can answer "late" without racing a timer.
"""

from __future__ import annotations

import asyncio
import logging
import threading

import pytest
from test_screen_lock import (
    RecordingVaultServe,
    _locked,
    _unlock_with,
    _wire,
    _workspace_with_session,
)
from test_vault_unlock import _answer, _prompt
from textual.widgets import Static
from tsb_tui import vault_flow
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.lock import LockScreen
from tsb_tui.screens.login import LoginScreen
from tsb_tui.screens.prompts import EndedScreen
from tsb_tui.screens.vault_prompts import PassphraseScreen
from tsbc import _serve
from vault_support import CHAIN, flat
from workspace_support import fixture_login, login


class LateAnswerServe(RecordingVaultServe):
    """The server handles every request at once; the answer to `hold_route` is sent only after `release` is set.
    `arrived`: a held request has been handled. `answered`: its answer has gone out. `lock_status`: answer
    `POST /auth/lock` with this status instead of 204."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hold_route: tuple[str, str] | None = None
        self.release = threading.Event()
        self.arrived = threading.Event()
        self.answered = threading.Event()
        self.lock_status: int | None = None

    def vault_http(self, method, path, headers, body, query, user):
        if (method, path) == ("POST", "/auth/lock") and self.lock_status is not None:
            code = "unauthenticated" if self.lock_status == 401 else "internal"
            return self._err(self.lock_status, code, "the broker could not lock")
        return super().vault_http(method, path, headers, body, query, user)

    def http(self, method, path, headers, body):
        answer = super().http(method, path, headers, body)
        if (method, path) == self.hold_route:
            self.hold_route = None
            self.arrived.set()
            self.release.wait(10)
            self.answered.set()
        return answer


async def _sign_out_and_back_in(app, pilot, wait_until, clock):
    """The login ends under the lock and a new one begins and locks in its turn: returns that new login's lock screen."""
    await app.sign_out()
    await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen) and app.screen.is_mounted, "the login screen")
    await login(app, pilot, _serve.USER, _serve.PASSWORD)
    return await _locked(app, pilot, wait_until, clock, 31)


async def _held(pilot, wait_until, serve_app, what: str) -> None:
    await wait_until(pilot, serve_app.arrived.is_set, what)


def test_a_session_that_ends_under_the_lock_shows_its_dialog_after_the_unlock_and_never_loops_on_a_refused_pop(
        serve, wait_until) -> None:
    """Finding 1: `_show_end_dialog` popped until the terminal screen was on top, and `pop_screen` is a no-op under the lock.
    A counting wrapper turns the endless loop into a failure instead of a hung run."""

    async def check() -> None:
        serve_app, url = serve(LateAnswerServe(locks={"screen_lock_after": 30}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            workspace, sid = await _workspace_with_session(app, pilot, serve_app, url, wait_until)
            slot = workspace.slot_for(sid)
            lock = await _locked(app, pilot, wait_until, clock, 31)
            real_pop, pops = app.pop_screen, []

            def counting_pop():
                pops.append(1)
                assert len(pops) < 50, "pop_screen was called over and over while the lock refused it"
                return real_pop()

            app.pop_screen = counting_pop
            with serve_app.lock:
                serve_app._kill(serve_app.sessions[sid])
            await wait_until(pilot, lambda: slot.ended is not None, "the session's end to reach the screen")
            await pilot.pause(0.2)
            assert app.screen is lock, "the lock stays on top"
            assert len(pops) <= 1, "the lock refuses a pop: nothing may call it again and again"
            await _unlock_with(lock, _serve.PASSWORD)
            await wait_until(pilot, lambda: isinstance(app.screen, EndedScreen), "the ended dialog after the unlock")

    asyncio.run(check())


def test_a_prompt_closed_while_the_lock_covers_it_dismisses_itself_when_it_is_on_top_again(serve, wait_until) -> None:
    """Finding 2: `close()` dismissed only a prompt that was on top, so one closed under the lock stayed as a spent prompt."""

    async def check() -> None:
        serve_app, url = serve(LateAnswerServe(salt=CHAIN.salt, accept=CHAIN.wire, locks={"screen_lock_after": 100000}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = await fixture_login(app, pilot)
            serve_app.hold_route = ("POST", "/vault/unlock")
            flow = asyncio.ensure_future(vault_flow.unlock(app))
            prompt = await _prompt(app, pilot, wait_until)
            await _answer(app, pilot, CHAIN.passphrase)
            await _held(pilot, wait_until, serve_app, "the unlock to be handled")
            app.screen_lock.lock()
            await wait_until(pilot, lambda: isinstance(app.screen, LockScreen) and app.screen.is_mounted, "the lock")
            lock = app.screen
            serve_app.release.set()
            result = await asyncio.wait_for(flow, 10)
            assert result.status == "unlocked", "the flow ended while the lock covered its prompt"
            assert app.screen is lock
            await _unlock_with(lock, _serve.PASSWORD)
            await wait_until(pilot, lambda: app.screen is workspace and prompt not in app.screen_stack,
                             "the closed prompt to be gone once the lock is lifted")
            assert not any(isinstance(screen, PassphraseScreen) for screen in app.screen_stack)

    asyncio.run(check())


def test_a_late_screen_unlock_success_from_before_a_sign_out_never_lifts_a_newer_logins_lock(serve, wait_until) -> None:
    """Finding 3: `LockScreen._resumed` unlocked whatever lock the app held when the answer came."""

    async def check() -> None:
        serve_app, url = serve(LateAnswerServe(locks={"screen_lock_after": 30}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            await fixture_login(app, pilot)
            old_lock = await _locked(app, pilot, wait_until, clock, 31)
            serve_app.hold_route = ("POST", "/auth/screen-unlock")
            await _unlock_with(old_lock, _serve.PASSWORD)
            await _held(pilot, wait_until, serve_app, "the screen-unlock to be handled")
            new_lock = await _sign_out_and_back_in(app, pilot, wait_until, clock)
            assert new_lock is not old_lock
            serve_app.release.set()
            await wait_until(pilot, serve_app.answered.is_set, "the late answer to go out")
            await pilot.pause(0.3)
            assert app.screen is new_lock and app.screen_lock.locked, "the old login's answer lifted the new login's lock"

    asyncio.run(check())


def test_a_failed_lock_now_for_the_current_login_says_the_vault_lock_was_not_confirmed(
        serve, wait_until, screen_text) -> None:
    """Finding 5, the current login: the screen stays locked and the lock screen says the vault lock was not confirmed."""

    async def check() -> None:
        serve_app, url = serve(LateAnswerServe(locks={"screen_lock_after": 100000}))
        serve_app.lock_status = 500
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = await fixture_login(app, pilot)
            await workspace.run_action_id("lock_now")
            await wait_until(pilot, lambda: "the vault lock was not confirmed" in flat(screen_text(app)).lower(),
                             "the lock screen to say the vault lock was not confirmed")
            assert isinstance(app.screen, LockScreen), "the local screen lock stays"

    asyncio.run(check())


@pytest.mark.parametrize("status", [500, 401])
def test_a_lock_now_failure_that_arrives_after_a_sign_out_is_logged_and_never_shown_over_the_newer_login(
        status, serve, wait_until, screen_text, caplog) -> None:
    """Finding 5, a replaced login: the failure of an `/auth/lock` sent before a sign-out is settled quietly and logged. The
    newer login's own lock screen stays and shows nothing of it (a 401 and any other failure alike)."""
    caplog.set_level(logging.INFO)

    async def check() -> None:
        serve_app, url = serve(LateAnswerServe(locks={"screen_lock_after": 30}))
        serve_app.lock_status = status
        serve_app.hold_route = ("POST", "/auth/lock")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = _wire(app)
            workspace = await fixture_login(app, pilot)
            await workspace.run_action_id("lock_now")
            await wait_until(pilot, lambda: isinstance(app.screen, LockScreen), "the lock screen")
            await _held(pilot, wait_until, serve_app, "the lock-now request to be handled")
            serve_app.lock_status = None
            new_lock = await _sign_out_and_back_in(app, pilot, wait_until, clock)
            caplog.clear()      # what the sign-out and the new login logged is not this
            serve_app.release.set()
            await wait_until(pilot, serve_app.answered.is_set, "the late failure to go out")
            await pilot.pause(0.3)
            assert app.screen is new_lock and app.screen_lock.locked, "the newer login's lock stays"
            assert str(new_lock.query_one("#lock-error", Static).render()) == "", (
                f"the old login's failure was shown over the newer login: {screen_text(app)!r}")
            assert any("lock" in record.getMessage().lower() and record.name.startswith("tsb_tui")
                       for record in caplog.records), "the stale failure was settled without a trace"

    asyncio.run(check())
