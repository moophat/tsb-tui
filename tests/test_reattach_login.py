# fails today: an attached driver never notices the core restart — the end dialog is pushed on top of its
# workspace, `switch_screen` puts Login over the dialog only, so a TerminalScreen stays mounted under the login
# page and drive keeps answering "already logged in" (the harness rig's `prefix-restart` failed this way on
# 1d032f4).

"""A 401 on the reattach path goes to Login and leaves no workspace (the ruling
`plans/reports/rulings-261016-tui-reattach-login.md`; DT13; the 401-routes-to-Login rule, `app.notify_api_error`).

Why these matter (Rule 9): a core restart erases every login (`core/internal/auth/logins.go`), so an attached
client's next ticket mint answers 401. The pump reports that with `on_login_needed`
(`model/attachment.py:505,803-808`), which nothing wired. Unwired, the app stays logged in for drive: the end
dialog is pushed on top of the workspace (`screens/terminal.py:1270-1271`), the fleet poll's 401 then
`switch_screen`s Login over the dialog only (Textual's `switch_screen` replaces the top of the stack), and
`workspace()` — which scans the whole stack, and is what `op_login` checks — still finds a TerminalScreen. So
every `login` is refused "already logged in", for as long as a rig retries.

Servers: the contract's in-process fixture server (DT7) for the restart, and the TUI's own stub (DT7) for the
first attach's own refusal. The tests read the state drive reads: `Driver.screen()` is `app.workspace()`
(`drive/runner.py:88-90`), and `op_login` refuses "already logged in" exactly when it is not None
(`drive/commands.py:245-246`). A driver that reaches `[base, LoginScreen]` with `workspace()` None is a driver
whose first `login` is accepted. No test performs a second login itself: a drive app that logs in twice under
`run_test` dies at teardown on a pre-existing Textual context-variable race (`LookupError: active_app`), which
happens on the old code too, and a flaky test is not evidence.
"""

from __future__ import annotations

import asyncio
import threading

from drive_support import Rig
from prompt_support import STUB_TARGET, create_stub_session, open_stub_terminal
from stub_server import SlowChunkedApp, StubServer
from tsb_contract_client import Client
from tsb_tui import config
from tsb_tui.app import TsbTuiApp
from tsb_tui.drive.runner import Driver
from tsb_tui.screens.terminal import TerminalScreen
from tsbc._serve import PASSWORD, TARGET, USER, ServeApp
from workspace_support import login, open_new_session

RESTART_PATHS = ("/sessions", "/auth/me", "/auth/ws-ticket")


class RestartApp(ServeApp):
    """The fixture server with a "core restart": every login-bound call answers 401 once the restart is
    declared, until the client logs in again (a new cookie ends it), as a restarted core's logins do."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.restart = False

    def http(self, method, path, headers, body):
        if method == "POST" and path == "/auth/login":
            self.restart = False
        if self.restart and path.split("?")[0] in RESTART_PATHS:
            return 401, [], {"error": {"code": "unauthenticated", "message": "login gone"}}
        return super().http(method, path, headers, body)


class RestartStub(SlowChunkedApp):
    """The TUI's stub with the same "core restart": every login-bound call answers 401 once declared.
    `stream_close=(4011, n)` closes the attached client after n chunks, which is a detach (DT13), so the pump
    reattaches and its ticket mint gets the 401."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.restart = False

    def http(self, method, path, headers, body):
        if method == "POST" and path == "/auth/login":
            self.restart = False
        if self.restart and path.split("?")[0] in RESTART_PATHS:
            return 401, [], {"error": {"code": "unauthenticated", "message": "login gone"}}
        return super().http(method, path, headers, body)


class LateCreateStub(RestartStub):
    """The restart stub with `POST /sessions` held in flight until the test releases `create_gate`, so a
    create request is running while the login goes 401, and its completion arrives after Login. The create
    itself keeps succeeding: a restarted core erases logins, it does not make a valid create fail."""

    def __init__(self, *, create_gate: threading.Event, **kw) -> None:
        super().__init__(**kw)
        self.create_gate = create_gate

    def http(self, method, path, headers, body):
        if method == "POST" and path == "/sessions":
            self.create_gate.wait(10)
            # The base stub's create, not `super()`: the restart must not answer this one.
            return SlowChunkedApp.http(self, method, path, headers, body)
        return super().http(method, path, headers, body)


def _session(url: str) -> str:
    """One session created by a logged-in client, as a rig does before its drivers attach."""
    c = Client(url)
    c.login(USER, PASSWORD)
    return c.create_session(TARGET, "new", "xterm-256color", 80, 24)["session"]["id"]


def _stack(app) -> list[str]:
    return [type(s).__name__ for s in app.screen_stack]


async def _at_login_with_no_workspace(app, pilot, wait_until) -> None:
    """The client is at the login page with nothing mounted under it — the state `op_login` needs, and the
    state a fresh start after a logout looks like."""
    await wait_until(pilot, lambda: app.workspace() is None, "the login screen with no workspace under it")
    assert _stack(app) == ["Screen", "LoginScreen"], _stack(app)


def test_an_attached_client_that_loses_its_login_ends_at_login_with_no_workspace(
        serve, tmp_path, wait_until) -> None:
    # fails today: the stack ends ['Screen', 'LoginScreen', 'TerminalScreen', 'LoginScreen'] — the workspace
    # stays mounted under the login page, so `workspace()` is never None and the wait times out.
    """The ruled behaviour: the pump's 401 on the reattach path routes to Login through `notify_api_error`, the
    workspace goes with it, and the end dialog never sits on top of the login page."""

    async def check() -> None:
        stub = RestartStub(delay_s=0.02, stream_close=(4011, 3))
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=STUB_TARGET)
            async with app.run_test(size=(80, 24)) as pilot:
                await open_stub_terminal(app, pilot, wait_until)
                stub.restart = True
                await _at_login_with_no_workspace(app, pilot, wait_until)
                assert "EndedScreen" not in _stack(app), _stack(app)

    asyncio.run(check())


def test_login_screen_on_mount_survives_missing_children() -> None:
    """LoginScreen.on_mount must not raise when its username field is absent. An exception in
    on_mount crashes the app (Textual propagates it), so on_mount must handle the case where
    compose hasn't yielded the Input yet, or the screen is being torn down and its widgets are gone."""

    async def check() -> None:
        from tsb_tui.screens.login import LoginScreen
        
        # A LoginScreen subclass whose compose yields nothing, so on_mount runs with no #username Input.
        class EmptyLoginScreen(LoginScreen):
            def compose(self):
                return iter([])
        
        stub = RestartStub(delay_s=0.02, stream_close=(4011, 1))
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=STUB_TARGET)
            async with app.run_test(size=(80, 24)) as pilot:
                # Push the empty LoginScreen. Its on_mount runs but #username doesn't exist.
                # Old code: raises NoMatches and crashes. New code: returns early without focusing.
                app.push_screen(EmptyLoginScreen())
                await pilot.pause()
                # The screen mounted successfully without raising
                assert isinstance(app.screen, EmptyLoginScreen)

    asyncio.run(check())


def test_an_attached_driver_that_lost_its_login_is_logged_in_again(serve, tmp_path, wait_until) -> None:
    # fails today: `rig.driver.screen()` is not None, which is exactly what makes drive answer
    # "already logged in as fixture-user; one driver is one login" (the harness rig's 10 s bound ran out).
    """The same restart seen through a drive process: the driver that was attached reaches `[base, LoginScreen]`
    with `workspace()` None, so drive's first `login` is accepted — `op_login` refuses only when
    `self.d.screen()`, which is `app.workspace()`, is not None."""

    async def check() -> None:
        app, url = serve(RestartApp())
        sid = _session(url)
        async with Rig(url, tmp_path=tmp_path, profile="harness",
                       overrides=("fleet_view.refresh_s=1",)) as rig:
            await rig.login(USER)
            answer = await rig.cmd("attach", session=sid)
            assert answer.get("ok"), answer
            app.restart = True
            with app.lock:
                client_id = next(iter(app.clients[sid]))
                app._drop_client(app.sessions[sid], client_id, code=4011, reason="")
            await _at_login_with_no_workspace(rig.app, rig.pilot, wait_until)
            assert rig.driver.screen() is None, "drive still counts this driver as logged in"

    asyncio.run(check())


def test_the_first_attach_401_path_is_unchanged_and_leaves_no_workspace(wait_until) -> None:
    # fails today: `switch_screen` replaces the top of the stack only, so the login screen this path shows sits
    # on the startup LoginScreen, and any workspace above the base stays mounted under it.
    """The first attach's own 401 — the ticket mint refuses — keeps sending the client back to Login (the rule
    at `terminal.py`'s attach worker), and now leaves no screen above the base: the stack is `[base,
    LoginScreen]` and `workspace()` is None."""

    async def check() -> None:
        stub = SlowChunkedApp(ticket_status=401)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=STUB_TARGET)
            async with app.run_test(size=(80, 24)) as pilot:
                sid = create_stub_session(app)
                app.push_screen(TerminalScreen(sid))
                await wait_until(pilot, lambda: app.workspace() is None,
                                 "the login screen with no workspace")
                assert _stack(app) == ["Screen", "LoginScreen"], _stack(app)

    asyncio.run(check())


def test_a_driver_with_nothing_attached_still_drops_to_login_on_the_fleet_poll_401(
        serve, tmp_path, wait_until) -> None:
    # passes today and must keep passing: TUI B logged in again correctly on 1d032f4, and the ruling says that
    # path stays as it is (`terminal.py`'s `_poll_failed` → `notify_api_error`).
    """A driver with no session open sees the restart through the fleet poll's 401, drops to Login, and has no
    workspace left under it."""

    async def check() -> None:
        app, url = serve(RestartApp())
        _session(url)
        async with Rig(url, tmp_path=tmp_path, profile="harness",
                       overrides=("fleet_view.refresh_s=1",)) as rig:
            await rig.login(USER)
            app.restart = True
            await _at_login_with_no_workspace(rig.app, rig.pilot, wait_until)
            assert rig.driver.screen() is None

    asyncio.run(check())


def test_a_drive_attach_after_a_relogin_waits_for_the_workspace_to_be_ready(
        serve, tmp_path, wait_until) -> None:
    # fails today: drive's next `attach` after the re-login reads `screen.fleet.records`
    # (`drive/commands.py:272`) while that workspace's `on_mount` has not built the fleet yet, so it raises
    # `AttributeError: 'NoneType' object has no attribute 'records'` — the exact failure `rig-prefix-restart`
    # reported for TUI A's `restart-compare.jsonl:5 attach` on 9c007d2.
    """The prefix-restart sequence through a drive app: log in, attach, core restart, re-login, then attach at
    once. The re-login's `switch_screen` puts the TerminalScreen on the stack before its `on_mount` runs, so
    `workspace()` is not None while `screen.fleet` is None; drive's attach must wait for that workspace to be
    ready (the same readiness the login's own wait gives on a first start), not read a fleet that is not built.
    The app runs in this task, as `tsb-tui drive` runs one app, so no second-login teardown race is involved."""

    async def check() -> None:
        server, url = serve(RestartApp())
        sid = _session(url)
        cfg = config.load(profile="harness", overrides=["fleet_view.refresh_s=1"])
        app = TsbTuiApp(url, cfg=cfg)
        async with app.run_test(size=(120, 40)) as pilot:
            driver = Driver(app, pilot, emit=lambda o: None)
            await driver.log_in(USER, PASSWORD)
            answer = await driver.commands.op_attach({"session": sid})
            assert answer["session"] == sid
            server.restart = True
            with server.lock:
                client_id = next(iter(server.clients[sid]))
                server._drop_client(server.sessions[sid], client_id, code=4011, reason="")
            await _at_login_with_no_workspace(app, pilot, wait_until)
            assert driver.screen() is None
            # The re-login lands (resetting the restart), and drive's next `attach` arrives in the same iteration
            # as the login's `switch_screen` pushes the workspace, before its `on_mount` builds the fleet. This is
            # the window that made the rig fail.
            await driver.log_in(USER, PASSWORD)
            app.switch_screen(TerminalScreen())
            screen = driver.screen()
            assert isinstance(screen, TerminalScreen) and screen.fleet is None, \
                "the workspace must be pushed but not yet mounted for this to test the race"
            answer = await driver.commands.op_attach({"session": sid})
            assert answer["session"] == sid

    asyncio.run(check())


def test_a_drive_login_answers_only_after_the_workspace_has_mounted(serve) -> None:
    # A guard, not a regression proof: it pins that a drive login answers "in" only with the workspace's fleet and
    # poller built. In-process every await inside `log_in` drains Textual's queue and mounts the workspace first,
    # so the old outcome check passes here too; the race it closes opens only under load (the post-merge loop),
    # and the attach-wait test above is the one that fails on the old code.
    """The login half of the fix: a re-login answers ready only after its workspace has mounted. Log in, go to
    Login the way a 401 does (`_to_login` pops to base and shows `LoginScreen`), then re-login with drive's
    `log_in`. The moment it returns "in" — no pause, no extra await — the workspace drive sees must be the one
    login produced, with its fleet and poller built. On the old `runner.py`, `log_in` answered as soon as
    `workspace()` was non-None, i.e. before `on_mount`."""

    async def check() -> None:
        _server, url = serve(RestartApp())
        cfg = config.load(profile="harness", overrides=["fleet_view.refresh_s=1"])
        app = TsbTuiApp(url, cfg=cfg)
        async with app.run_test(size=(120, 40)) as pilot:
            driver = Driver(app, pilot, emit=lambda o: None)
            await driver.log_in(USER, PASSWORD)
            # Go to Login the way a 401 does: `_to_login` pops every screen above the base and shows LoginScreen,
            # so `workspace()` is None — the state drive's `login` accepts.
            app._to_login()
            assert app.workspace() is None
            # Let the LoginScreen the 401 pushed come up and lay out its form, so `log_in` can fill it (the pause
            # is before the login, not after it — the assertion below has no pause).
            await pilot.pause()
            # Re-login. The moment `log_in` returns, with no pause and no extra await, read the readiness it
            # answered on: it must be the workspace login produced, fully mounted.
            await driver.log_in(USER, PASSWORD)
            screen = driver.screen()
            assert isinstance(screen, TerminalScreen), type(screen).__name__
            assert screen.fleet is not None and screen.poller is not None, \
                "drive answered a login ready before its workspace's on_mount built the fleet/poller"

    asyncio.run(check())


def test_a_create_that_completes_after_the_login_is_gone_is_discarded(wait_until) -> None:
    # fails today: `_on_create_done` pops the top of the stack — LoginScreen, once the 401 has routed to Login —
    # and opens the session through `app.workspace()`, which is None, so the app crashes and the login page is
    # gone from the stack.
    """A create request in flight across a core restart: the fleet poll's 401 takes the client to Login with
    nothing above the base, and the completion that arrives afterwards is discarded — the stack stays
    `[base, LoginScreen]`, and the session the create made stays on the broker as a detached session, for the
    operator to attach to after logging in (the ruling's item 3: nothing reattaches on its own across a
    logout). The create is held in flight by a gate so the order is fixed, not raced, and the assertions wait
    on a spy wrapped around the dialog's own `_on_create_done`, so they read the state after that callback has
    run — never after a server-side insert or a fixed pause, which say nothing about the UI thread."""

    async def check() -> None:
        done = threading.Event()
        gate = threading.Event()
        stub = LateCreateStub(create_gate=gate, recording_on_create=False)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=STUB_TARGET)
            app.cfg.set_runtime("fleet_view.refresh_s", 1)
            async with app.run_test(size=(80, 24)) as pilot:
                await login(app, pilot)
                dialog = await open_new_session(app, pilot, auto_size=True)
                original = dialog._on_create_done

                def spy(created: dict, login: dict | None = None) -> None:
                    original(created, login)
                    done.set()

                dialog._on_create_done = spy
                stub.restart = True
                await pilot.click("#create-button")
                await _at_login_with_no_workspace(app, pilot, wait_until)
                gate.set()
                await wait_until(pilot, done.is_set, "_on_create_done to run after Login")
                assert _stack(app) == ["Screen", "LoginScreen"], _stack(app)
                assert app.workspace() is None
                assert next(iter(stub.sessions.values()))["state"] == "detached"

    asyncio.run(check())
