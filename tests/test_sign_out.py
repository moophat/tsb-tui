"""Sign out: the Menu detaches everything open here, calls the contract client's `logout()` and returns to Login.

Why these matter: signing out is the operator's way off a shared machine. Two things go wrong silently if it
is built carelessly. A session that is ended instead of detached kills a running network session for everyone; and a
logout sent before the detaches leaves what the core does with a principal's attached clients to the contract's open
item 5 (`login.md`) rather than to ordinary detaches. Each test reads the server's own table, never the TUI's idea of
what it did. Sign out is Menu-only with no confirmation, and a broker that does not confirm still
leaves the operator at Login with a warning saying so.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from input_server import make_session
from signout_support import HeldRequestApp, LogoutProbeApp
from tsb_contract_client import ApiError
from tsb_tui.app import TsbTuiApp
from tsb_tui.model import actions as A
from tsb_tui.screens.login import LoginScreen
from tsbc import _serve
from workspace_support import fixture_login, open_here

SIGNED_OUT = "Signed out. Your sessions keep running."


def _unauthorized() -> ApiError:
    """The 401 an unrelated request answers once a login has lapsed (what `notify_api_error` routes to Login)."""
    return ApiError(401, "unauthenticated", "login gone")


def _stack(app: TsbTuiApp) -> list[str]:
    return [type(s).__name__ for s in app.screen_stack]


def _toasts(app: TsbTuiApp) -> list[str]:
    return [n.message for n in app._notifications._notifications.values()]


async def _signed_out(app: TsbTuiApp, pilot, wait_until) -> None:
    await wait_until(pilot, lambda: app.workspace() is None and isinstance(app.screen, LoginScreen)
                     and app.screen.is_mounted and app.screen.query("#login-button"),
                     "Login with no workspace under it")
    assert _stack(app) == ["Screen", "LoginScreen"], _stack(app)
    await pilot.pause()  # whatever else was in flight has been handled: a toast that would be wrong is shown by now


async def _sign_out(screen) -> None:
    """Sign out as the Menu's entry does: through the action table."""
    assert "sign_out" in A.BY_ID, "the action table has no Sign out to run"
    await screen.run_action_id("sign_out")


async def _two_sessions_open(serve_app: LogoutProbeApp, url: str, app: TsbTuiApp, pilot, wait_until):
    """Two sessions of the fixture owner, both open in the workspace. Returns (screen, ids, the cookies this TUI's
    login holds on the server)."""
    first = make_session(url, _serve.USER)
    second = make_session(url, _serve.USER)
    others = set(serve_app.logins)  # the logins the two creating clients made: not this TUI's
    screen = await fixture_login(app, pilot)
    mine = set(serve_app.logins) - others
    assert len(mine) == 1, "the TUI's own login is the one new login"
    await open_here(app, pilot, wait_until, first, second)
    assert all(serve_app.clients[sid] for sid in (first, second)), "both sessions have this TUI attached"
    return screen, [first, second], mine


def test_menu_offers_sign_out_and_no_leader_key_runs_it() -> None:
    """Sign out is in the Menu, in Settings and help, beside Settings and About, with no key after the
    leader and so no `keys.leader_map` knob: a costly slip is never one keystroke away. It is available whenever the
    workspace exists, with a session open or none."""
    assert "sign_out" in A.BY_ID, "the action table has no Sign out"
    action = A.BY_ID["sign_out"]
    assert action.label == "Sign out"
    assert action.group == A.GROUP_SETTINGS
    assert action.key is None, "Sign out was given a leader key (it is Menu-only)"
    assert action in A.menu_actions(), "the Menu does not list Sign out"
    assert A.blocked("sign_out", A.Facts(None, open_sessions=0)) is None, "blocked with no session open"
    assert A.blocked("sign_out", A.Facts(None, open_sessions=2)) is None
    from tsb_tui import config

    assert "keys.leader_map.sign_out" not in config.load(profile="default").as_dict()


def test_sign_out_detaches_every_session_before_logout_then_shows_login(serve, wait_until) -> None:
    """At the moment the server handles `POST /auth/logout`, every session this TUI had open is `detached` with no
    client attached; afterwards Login is up with no workspace under it and the server no longer holds this TUI's
    login (the fixture's logout drops it)."""

    async def check() -> None:
        serve_app, url = serve(LogoutProbeApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, sessions, mine = await _two_sessions_open(serve_app, url, app, pilot, wait_until)
            await _sign_out(screen)
            await _signed_out(app, pilot, wait_until)
            assert serve_app.at_logout is not None, "the broker was never asked to log out"
            assert serve_app.at_logout == {sid: ("detached", 0) for sid in sessions}, (
                f"at logout the server held {serve_app.at_logout}: the detaches must reach it first")
            assert not mine & set(serve_app.logins), "the server still holds this TUI's login"
            assert SIGNED_OUT in _toasts(app)
            with pytest.raises(ApiError):  # the client's own cookie is gone too
                app.client.me()

    asyncio.run(check())


def test_sign_out_leaves_every_session_running(serve, wait_until) -> None:
    """Detached, never ended: the sessions are still there, `detached`, not `killed`, and the same login can reattach
    to them (the owner's session lives on)."""

    async def check() -> None:
        serve_app, url = serve(LogoutProbeApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, sessions, _mine = await _two_sessions_open(serve_app, url, app, pilot, wait_until)
            await _sign_out(screen)
            await _signed_out(app, pilot, wait_until)
            assert [serve_app.sessions[sid]["state"] for sid in sessions] == ["detached", "detached"]
            assert not any(serve_app.clients.get(sid) for sid in sessions)
            await fixture_login(app, pilot)  # the same principal comes back, and the sessions are there to join
            assert {sid: s["state"] for sid, s in serve_app.sessions.items()
                    if sid in sessions} == {sid: "detached" for sid in sessions}

    asyncio.run(check())


def test_sign_out_when_logout_fails_goes_to_login_and_says_the_broker_did_not_confirm(serve, wait_until) -> None:
    """A broker that answers 500 does not keep the operator in the workspace. They are at Login, this
    client holds no cookie, the sessions are still detached, and a warning names what the broker did not confirm.
    Not the error toast `notify_api_error` would raise."""

    async def check() -> None:
        serve_app, url = serve(LogoutProbeApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, sessions, mine = await _two_sessions_open(serve_app, url, app, pilot, wait_until)
            serve_app.logout_status = 500
            await _sign_out(screen)
            await _signed_out(app, pilot, wait_until)
            assert serve_app.at_logout == {sid: ("detached", 0) for sid in sessions}
            await wait_until(pilot, lambda: any("Signed out here" in t for t in _toasts(app)),
                             "the warning (Textual's notify posts a message)")
            toasts = _toasts(app)
            assert any("Signed out here" in t and "did not confirm" in t and "500" in t
                       and "ends when it expires" in t for t in toasts), toasts
            assert SIGNED_OUT not in toasts, "told it was signed out everywhere when the broker did not say so"
            assert not any(t.startswith("HTTP 500") for t in toasts), "the error toast for a failed request"
            assert mine <= set(serve_app.logins), "the server dropped a login it was never told to drop"
            with pytest.raises(ApiError):
                app.client.me()  # this client holds no cookie, whatever the broker said

    asyncio.run(check())


def test_sign_out_after_the_login_expired_shows_login_without_an_error(serve, wait_until) -> None:
    """A 401 from logout means the login was already gone: the operator wanted to be signed out and is. Login, the
    ordinary toast, and no error."""

    async def check() -> None:
        serve_app, url = serve(LogoutProbeApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, sessions, _mine = await _two_sessions_open(serve_app, url, app, pilot, wait_until)
            serve_app.logout_status = 401
            await _sign_out(screen)
            await _signed_out(app, pilot, wait_until)
            assert serve_app.at_logout == {sid: ("detached", 0) for sid in sessions}
            toasts = _toasts(app)
            assert SIGNED_OUT in toasts, toasts
            assert not any("HTTP 401" in t or "did not confirm" in t for t in toasts), toasts

    asyncio.run(check())


def test_a_request_held_across_sign_out_never_runs_under_the_next_login(serve, wait_until) -> None:
    """A request submitted before sign-out (an end, say) whose thread has not yet entered the
    client when the operator signs out and the next person logs in must not go out under the new principal. It is held
    at the client's transport, before the Cookie header is read: the moment a request that "has not yet entered the
    client" is at. The server reads the principal off the cookie that reaches it. Fenced, the request goes out on the
    client it was submitted with, whose jar logout cleared: no cookie, a 401. That 401 belongs to the old login and
    is ignored: the new login's screen shows no error and is not sent back to Login."""

    async def check() -> None:
        serve_app, url = serve(HeldRequestApp())
        sid = make_session(url, _serve.USER)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            held, release, finished = threading.Event(), threading.Event(), threading.Event()
            transport = app.client._http
            real_request = transport.request

            def request(method, path, **kwargs):
                if method != "DELETE" or not path.startswith("/sessions/"):
                    return real_request(method, path, **kwargs)
                held.set()
                assert release.wait(10), "the held end was never released"
                try:
                    return real_request(method, path, **kwargs)
                finally:
                    finished.set()

            transport.request = request  # only the client the end is submitted with is held
            screen.send_end(sid)
            await wait_until(pilot, held.is_set, "the end's thread, in the client and not yet sending")
            await _sign_out(screen)
            await _signed_out(app, pilot, wait_until)
            workspace = await fixture_login(app, pilot, _serve.PEER)  # the next login: a different principal
            assert app.username == _serve.PEER
            release.set()
            await wait_until(pilot, finished.is_set, "the held end to reach the server and be answered")
            await pilot.pause()
            await pilot.pause()  # its answer has been handled on the UI thread by now
            deletes = [who for method, path, who in serve_app.seen if method == "DELETE" and path == f"/sessions/{sid}"]
            assert deletes == [None], (
                f"the held end reached the server as {deletes}: only a request with no cookie is the old login's, "
                f"not the next login's ({_serve.PEER!r})")
            assert serve_app.sessions[sid]["state"] != "killed"
            assert app.workspace() is workspace and app.screen is workspace, "the old 401 sent the new login back to Login"
            assert not any("Request failed" in n.title or "HTTP 401" in n.message or "ended" in n.message.lower()
                           for n in app._notifications._notifications.values()), _toasts(app)

    asyncio.run(check())


def test_a_sign_out_cancelled_before_the_broker_answers_survives_the_late_answer(serve, wait_until) -> None:
    """A caller given up on (a drive command, the workspace torn down) is
    cancelled while `logout()` is at the broker; the answer then arrives and its callback must not raise
    `InvalidStateError` inside the api worker. (The sign-out itself runs on, shielded from its caller:
    `test_a_cancelled_caller_never_cancels_the_sign_out_it_started_or_shared`.)"""

    async def check() -> None:
        serve_app, url = serve(HeldRequestApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            release = threading.Event()
            serve_app.holds[("POST", "/auth/logout")] = release
            task = asyncio.create_task(app.sign_out())
            await wait_until(pilot, serve_app.arrived.is_set, "logout, waiting at the server")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
            await wait_until(pilot, lambda: serve_app.at_logout is not None, "the server to handle logout")
            await wait_until(pilot, lambda: not any(w.name == "_run_blocking" and not w.is_finished for w in app.workers),
                             "the api worker to finish: it hands its answer to the UI thread before it does")
            await pilot.pause()
            assert app._exception is None, f"the late answer errored the api worker: {app._exception!r}"
            assert app.is_running

    asyncio.run(check())


def test_a_cancelled_caller_never_cancels_the_sign_out_it_started_or_shared(serve, wait_until) -> None:
    """A cancelled caller. The Menu's `do_sign_out` runs under the workspace screen, and an unrelated
    401 that opens Login tears that screen down mid-sign-out, cancelling the caller. Cancelling a caller (the first,
    or one that joined) must not cancel the shared sign-out: it still tells the broker, replaces the client and
    shows Login, and a second caller waiting on it still gets its answer."""

    async def check() -> None:
        serve_app, url = serve(HeldRequestApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            old_client = app.client
            release = threading.Event()
            serve_app.holds[("POST", "/auth/logout")] = release
            first = asyncio.create_task(app.sign_out())
            await wait_until(pilot, serve_app.arrived.is_set, "the first logout, waiting at the server")
            second = asyncio.create_task(app.sign_out())
            await pilot.pause()
            first.cancel()  # the workspace screen that ran it was torn down
            with pytest.raises(asyncio.CancelledError):
                await first
            await pilot.pause()
            assert app.signing_out, "cancelling the caller cancelled the sign-out"
            release.set()
            result = await asyncio.wait_for(second, 10)  # the joiner still gets the sign-out's answer
            assert result.broker == "signed_out", result
            assert app.client is not old_client, "the sign-out never finished: the client was not replaced"
            assert isinstance(app.screen, LoginScreen), _stack(app)
            assert not app.signing_out

    asyncio.run(check())


def test_two_sign_outs_in_a_row_share_one_sign_out_and_settle_both(serve, wait_until) -> None:
    """Sign-out is single-flight: a second Sign out (or drive `logout`) while one runs shares
    the first and settles with it, so nothing waits forever. One `POST /auth/logout` reaches the broker, both tasks
    settle with the same `SignedOut`, and Login is up."""

    async def check() -> None:
        serve_app, url = serve(HeldRequestApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            release = threading.Event()
            serve_app.holds[("POST", "/auth/logout")] = release
            first = asyncio.create_task(app.sign_out())
            await wait_until(pilot, serve_app.arrived.is_set, "the first logout, waiting at the server")
            second = asyncio.create_task(app.sign_out())
            await pilot.pause()  # the second task is now awaiting the first's sign-out
            release.set()
            r1 = await asyncio.wait_for(first, 10)
            r2 = await asyncio.wait_for(second, 10)
            assert r1 is r2 and r1.broker == "signed_out", (r1, r2)
            logouts = [m for m, p, _who in serve_app.seen if (m, p) == ("POST", "/auth/logout")]
            assert len(logouts) == 1, f"{len(logouts)} logouts reached the broker: a second sign-out must share the first"
            assert isinstance(app.screen, LoginScreen), _stack(app)

    asyncio.run(check())


def test_a_401_opening_login_during_a_pending_sign_out_refuses_login_until_it_completes(serve, wait_until) -> None:
    """The submit gate. An unrelated 401 opens Login while sign-out awaits the broker; while it
    runs, a submit is refused with "Signing out…" and no login begins. Once sign-out completes, Login accepts one."""

    async def check() -> None:
        serve_app, url = serve(HeldRequestApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)

            def logins() -> list[str]:
                return [m for m, p, _who in serve_app.seen if (m, p) == ("POST", "/auth/login")]

            logins_before = len(logins())
            release = threading.Event()
            serve_app.holds[("POST", "/auth/logout")] = release
            task = asyncio.create_task(app.sign_out())
            await wait_until(pilot, serve_app.arrived.is_set, "logout, waiting at the server")
            app.notify_api_error(_unauthorized())  # the unrelated 401 opens Login mid-sign-out
            await pilot.pause()
            assert isinstance(app.screen, LoginScreen), _stack(app)
            login = app.screen
            login.query_one("#username").value = _serve.PEER
            login.query_one("#password").value = _serve.PASSWORD
            await pilot.click("#login-button")
            await pilot.pause()
            for _ in range(10):  # a login that slipped the gate would reach the server within these turns
                await pilot.pause(0.05)
            assert len(logins()) == logins_before, "a login reached the broker while sign-out was running"
            assert app.username == _serve.USER and isinstance(app.screen, LoginScreen), \
                "a login completed while sign-out was running"
            assert "Signing out…" in str(login.query_one("#login-error").content), \
                "the submit was not told it was signing out"
            release.set()
            await asyncio.wait_for(task, 10)
            await pilot.pause()
            # The sign-out is over, and Login accepts a login again.
            await fixture_login(app, pilot, _serve.PEER)
            assert app.username == _serve.PEER

    asyncio.run(check())


def test_a_login_completed_after_sign_out_began_is_never_discarded(serve, wait_until) -> None:
    """The generation check. The submit gate stops a new login, but a login that still completes
    after sign-out began (it started first, or slipped the gate) is backed by the generation check: sign-out returns
    without replacing its client and without navigating away, so the new login's workspace and client survive."""

    async def check() -> None:
        serve_app, url = serve(HeldRequestApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            release = threading.Event()
            serve_app.holds[("POST", "/auth/logout")] = release
            task = asyncio.create_task(app.sign_out())
            await wait_until(pilot, serve_app.arrived.is_set, "logout, waiting at the server")
            app.notify_api_error(_unauthorized())  # Login opens while sign-out awaits the broker
            await pilot.pause()
            assert isinstance(app.screen, LoginScreen), _stack(app)
            login = app.screen
            # Complete a login after sign-out began (the gate refuses this in real use; the generation check is the
            # backstop this test exercises) through the real client and the login's own done callback.
            app.call_api(app.client.login, _serve.PEER, _serve.PASSWORD,
                         on_done=login._on_login_done, on_error=login._on_login_error)
            await wait_until(pilot, lambda: app.username == _serve.PEER and app.workspace() is not None,
                             "the PEER login to complete")
            client = app.client
            workspace = app.workspace()
            release.set()
            await asyncio.wait_for(task, 10)
            assert app.username == _serve.PEER, "the login was discarded"
            assert app.client is client, "sign-out replaced the client the new login holds"
            assert app.workspace() is workspace and app.screen is workspace, \
                "sign-out navigated away from the new login's workspace"

    asyncio.run(check())


def _hold_first_stop(monkeypatch) -> tuple[asyncio.Event, asyncio.Event]:
    """Make the first attachment's `stop` wait: `detach_all` then awaits a worker while the test acts. Returns
    (entered, release)."""
    from tsb_tui.model.attachment import AttachmentPump

    entered, release = asyncio.Event(), asyncio.Event()
    real_stop = AttachmentPump.stop
    first = []

    async def stop(self) -> None:
        if not first:
            first.append(self)
            entered.set()
            await release.wait()
        await real_stop(self)

    monkeypatch.setattr(AttachmentPump, "stop", stop)
    return entered, release


def test_an_attach_opened_while_sign_out_waits_on_a_worker_is_refused(serve, wait_until, monkeypatch) -> None:
    """`detach_all` awaits an attach worker; a session opened in that wait must not attach (it is refused
    with a one-line reason), so at `POST /auth/logout` no session holds a client and no slot is left."""

    async def check() -> None:
        serve_app, url = serve(LogoutProbeApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            first = make_session(url, _serve.USER)
            late = make_session(url, _serve.USER)
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first)
            entered, release = _hold_first_stop(monkeypatch)
            task = asyncio.create_task(app.sign_out())
            await asyncio.wait_for(entered.wait(), 10)  # detach_all is awaiting the first worker
            app.gated_sessions.add(late)
            screen.open_session(app.client.session(late))  # an attach opened during the wait
            await pilot.pause()
            assert screen.slot_for(late) is None, "a session was opened while sign-out was running"
            assert any("Signing out" in text for text in _toasts(app)), _toasts(app)
            release.set()
            await asyncio.wait_for(task, 10)
            await _signed_out(app, pilot, wait_until)
            assert serve_app.at_logout is not None, "the broker was never asked to log out"
            assert serve_app.at_logout[first] == ("detached", 0), serve_app.at_logout
            assert serve_app.at_logout[late][1] == 0, f"the late attach was held at logout: {serve_app.at_logout}"

    asyncio.run(check())


def test_a_gate_confirmed_while_sign_out_waits_on_a_worker_attaches_nothing(serve, wait_until, monkeypatch) -> None:
    """The pending dialog. The attach gate was up before sign-out began and is confirmed during it: the
    session is not attached, and no client holds it at logout."""

    async def check() -> None:
        serve_app, url = serve(LogoutProbeApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            first = make_session(url, _serve.USER)
            late = make_session(url, _serve.USER)
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first)
            screen.open_session(app.client.session(late))  # not gated yet: the dialog opens
            await pilot.pause()
            dialog = app.screen
            assert type(dialog).__name__ == "AttachConfirmScreen", _stack(app)
            entered, release = _hold_first_stop(monkeypatch)
            task = asyncio.create_task(app.sign_out())
            await asyncio.wait_for(entered.wait(), 10)
            dialog.action_attach()  # the operator confirms during the wait
            await pilot.pause()
            assert screen.slot_for(late) is None, "a gate confirmed during sign-out opened a session"
            release.set()
            await asyncio.wait_for(task, 10)
            await _signed_out(app, pilot, wait_until)
            assert serve_app.at_logout[late][1] == 0, f"the late attach was held at logout: {serve_app.at_logout}"

    asyncio.run(check())


def test_a_held_logout_never_empties_the_jar_of_a_login_made_during_it(serve, wait_until) -> None:
    """The shared-client race. The logout is held at the server; Login is up and a login completes
    meanwhile (the submit gate refuses this in real use; here it goes through the real client, as a login that began
    before sign-out would). `Client.logout` clears the jar of the instance it was sent on, so that instance must not
    be the one the new login uses. After the release the new login is still the one the app holds: a request on its
    client is answered, its workspace is up, and sign-out neither replaced the client nor left that screen."""

    async def check() -> None:
        serve_app, url = serve(HeldRequestApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            release = threading.Event()
            serve_app.holds[("POST", "/auth/logout")] = release
            task = asyncio.create_task(app.sign_out())
            await wait_until(pilot, serve_app.arrived.is_set, "logout, waiting at the server")
            app.notify_api_error(_unauthorized())  # Login is up by now, or opens here
            await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen) and app.screen.is_mounted
                             and app.screen.query("#login-button"), "Login")
            login = app.screen
            app.call_api(app.client.login, _serve.PEER, _serve.PASSWORD,
                         on_done=login._on_login_done, on_error=login._on_login_error)
            await wait_until(pilot, lambda: app.username == _serve.PEER and app.workspace() is not None,
                             "the PEER login to complete")
            client, workspace = app.client, app.workspace()
            release.set()
            await asyncio.wait_for(task, 10)
            assert app.client is client, "sign-out replaced the client the new login holds"
            assert app.workspace() is workspace and app.screen is workspace, "sign-out left the new login's workspace"
            assert app.client.me()["username"] == _serve.PEER, "the held logout emptied the new login's jar"

    asyncio.run(check())
