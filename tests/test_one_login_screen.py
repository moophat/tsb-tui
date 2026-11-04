"""One login screen, however many 401s arrive (issue #326; the ruling
`plans/reports/rulings-261016-tui-reattach-login.md`; `app.notify_api_error` → `app._to_login`).

Why these matter (Rule 9): a core restart erases every login, so every request already in flight with the
old cookie can come back 401 at once — the poller's `GET /sessions`, a reattach. The first 401 shows Login;
`_to_login` then pushed a *new* `LoginScreen` on each one, unmounting the form a person was typing into, or
the one drive had just filled in. Drive's `log_in` then queried that unmounted screen and the rig step failed
with `NoMatches: No nodes match '#login-button'` (rig-prefix-restart). The fixes: `_to_login` returns when
Login is already on top, and drive's `log_in` fails a replaced attempt as `login_failed` rather than an
internal error. The tests read what a person and drive read: the screen stack, the form's own value, and the
answer `log_in` gives.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from prompt_support import STUB_TARGET, open_stub_terminal
from stub_server import SlowChunkedApp, StubServer
from tsb_contract_client import ApiError
from tsbc._serve import PASSWORD, USER
from workspace_support import open_new_session

from tsb_tui import config
from tsb_tui.app import TsbTuiApp
from tsb_tui.drive.commands import DriveError
from tsb_tui.drive.runner import Driver
from tsb_tui.screens.login import LoginScreen
from tsb_tui.screens.terminal import TerminalScreen


def _stack(app) -> list[str]:
    return [type(s).__name__ for s in app.screen_stack]


def _unauthorized() -> ApiError:
    """The 401 a restarted core answers with (core/internal/auth/logins.go: a restart erases every login)."""
    return ApiError(401, "unauthenticated", "login gone")


def test_a_second_401_does_not_replace_the_login_form_being_filled_in() -> None:
    # fails today: the first 401 pops to the base and pushes a *new* LoginScreen (`_to_login`), so
    # `app.screen is first` is already false after it — the same assertion the second 401 then makes
    # about the typed value.
    """Two 401s in a row, as two requests in flight with the old cookie produce: Login stays the one
    screen it was, and the value already typed into it survives."""

    async def check() -> None:
        with StubServer(SlowChunkedApp(delay_s=0.02)) as url:
            app = TsbTuiApp(url, target=STUB_TARGET)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                first = app.screen
                assert isinstance(first, LoginScreen), _stack(app)
                # One request in flight with the old cookie 401s...
                app.notify_api_error(_unauthorized())
                await pilot.pause()
                assert _stack(app) == ["Screen", "LoginScreen"], _stack(app)
                assert app.screen is first, "the 401 replaced the login screen"
                # ...and the form the person (or drive) is typing into keeps its value...
                app.screen.query_one("#username").value = "fixture-user"
                # ...when another one 401s: same screen, same typed value.
                app.notify_api_error(_unauthorized())
                await pilot.pause()
                assert _stack(app) == ["Screen", "LoginScreen"], _stack(app)
                assert app.screen is first, "the second 401 replaced the login screen"
                assert app.screen.query_one("#username").value == "fixture-user"

    asyncio.run(check())


def test_a_401_from_the_workspace_over_a_dialog_still_pops_to_login(wait_until) -> None:
    # passes today and must keep passing: `_to_login`'s early return only covers Login already on top;
    # with a workspace (and a dialog over it) it still pops every screen above the base and shows Login.
    """From the workspace with a dialog on top, a 401 still leaves `[base, LoginScreen]` with no
    workspace under it — the behaviour kept, and the state drive's `login` needs to be accepted."""

    async def check() -> None:
        with StubServer(SlowChunkedApp(delay_s=0.02)) as url:
            app = TsbTuiApp(url, target=STUB_TARGET)
            async with app.run_test(size=(120, 40)) as pilot:
                await open_stub_terminal(app, pilot, wait_until)
                await open_new_session(app, pilot, wait_until)
                assert isinstance(app.workspace(), TerminalScreen) and len(app.screen_stack) > 2, _stack(app)
                app.notify_api_error(_unauthorized())
                await pilot.pause()
                assert _stack(app) == ["Screen", "LoginScreen"], _stack(app)
                assert isinstance(app.screen, LoginScreen)
                assert app.workspace() is None, "a workspace is still mounted under the login page"

    asyncio.run(check())


def test_log_in_survives_a_second_401_mid_login_and_fails_loudly_when_replaced(serve, wait_until) -> None:
    # Part one fails today: the second 401 replaces the screen drive filled in, so `app.screen is login`
    # is false (and the attempt no longer reaches the workspace). Part two fails today with `internal`:
    # `log_in` queries the captured, unmounted `login` (`NoMatches` at `#login-button`), not `login_failed`.
    """Drive's `log_in` against a second 401 that lands mid-login: with `_to_login` as fixed it still
    answers "in" (the screen it filled in is not replaced); with `_to_login` forced back to replacing the
    screen, the attempt fails `login_failed`, never an internal error."""

    async def check() -> None:
        _server, url = serve()
        cfg = config.load(profile="harness", overrides=["fleet_view.refresh_s=1"])
        app = TsbTuiApp(url, cfg=cfg)
        async with app.run_test(size=(120, 40)) as pilot:
            driver = Driver(app, pilot, emit=lambda o: None)
            await driver.log_in(USER, PASSWORD)

            # Back to Login the way a first 401 does, then hold the login request until a second 401
            # has landed, so the order is fixed rather than raced.
            app._to_login()
            await pilot.pause()
            login = app.screen
            assert isinstance(login, LoginScreen), _stack(app)
            release = threading.Event()
            original = app.client.login

            def held_login(*args, **kw):
                release.wait(10)
                return original(*args, **kw)

            app.client.login = held_login
            task = asyncio.create_task(driver.log_in(USER, PASSWORD))
            await wait_until(pilot, lambda: login.query_one("#login-button").disabled,
                             "the login request to be in flight")
            app._to_login()  # a second 401, mid-login
            assert app.screen is login, "a 401 mid-login replaced the screen drive was filling in"
            release.set()
            assert await asyncio.wait_for(task, 10) == USER
            assert isinstance(driver.screen(), TerminalScreen)

            # Part two: force `_to_login` back to replacing the screen, and the replaced attempt must fail
            # `login_failed` — not `internal`, which is what querying the unmounted screen raised.
            app._to_login()  # back to Login (the fixed return: top was the workspace, so it pops and pushes)
            await pilot.pause()
            replaced = app.screen
            assert isinstance(replaced, LoginScreen), _stack(app)

            def replacing_to_login() -> None:
                app.stop_alerts()
                while len(app.get_screen_stack()) > 1:
                    app.pop_screen()
                app.push_screen(LoginScreen())

            app._to_login = replacing_to_login
            # The forced replace unmounts this screen; its own callback must not run on it.
            replaced._on_login_done = lambda principal: None
            replaced._on_login_error = lambda exc: None
            task = asyncio.create_task(driver.log_in(USER, PASSWORD))
            await wait_until(pilot, lambda: replaced.query_one("#login-button").disabled,
                             "the login request to be in flight")
            app._to_login()  # the second 401, forced to replace
            assert app.screen is not replaced and isinstance(app.screen, LoginScreen), _stack(app)
            with pytest.raises(DriveError) as caught:
                await asyncio.wait_for(task, 10)
            assert caught.value.code == "login_failed", caught.value

    asyncio.run(check())
