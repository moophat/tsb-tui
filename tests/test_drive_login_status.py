from __future__ import annotations

import asyncio
import json
import threading

from drive_support import Rig
from input_server import make_session
from tsb_contract_client import ApiError
from tsbc import _serve

from tsb_tui.screens.login import LoginScreen

SECRET = "canary-account-password-7462"


def test_login_status_never_logged_in(serve, tmp_path) -> None:
    async def check() -> None:
        _server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            ans = await rig.cmd("login_status")
            assert ans["ok"] is False
            assert ans["error"] == "not_logged_in"
            assert "cookie" not in json.dumps(ans).lower()

    asyncio.run(check())


def test_login_status_held_live_login(serve, tmp_path) -> None:
    async def check() -> None:
        _server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            ans = await rig.cmd("login_status")
            assert ans["ok"] is True
            assert ans["status"] == 200
            assert set(ans.keys()) - {"id", "ok"} == {"status"}
            assert "cookie" not in json.dumps(ans).lower()
            assert _serve.USER not in json.dumps(ans)

    asyncio.run(check())


def test_login_status_after_close_4017(serve, tmp_path) -> None:
    async def check() -> None:
        server, url = serve()
        sid = make_session(url, _serve.USER, 80, 24, target="normal")
        async with Rig(url, tmp_path=tmp_path, overrides=("drive.events=login_ended,ended",)) as rig:
            await rig.login()
            await rig.attach(sid)

            live = await rig.cmd("login_status")
            assert live["ok"] is True and live["status"] == 200

            await rig.app_call(rig.app.client.set_account_password, _serve.USER, SECRET)
            for _ in range(300):
                if await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen)):
                    break
                await rig.pilot.pause(0.01)
            assert await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen))
            assert rig.events("login_ended") == [{"ev": "login_ended", "session": sid, "reason": "account"}]
            assert rig.events("ended") == []
            assert server.sessions[sid]["state"] not in ("killed", "terminated")

            # login_status answers 401 from fake core
            ans = await rig.cmd("login_status")
            assert ans["ok"] is True
            assert ans["status"] == 401
            assert set(ans.keys()) - {"id", "ok"} == {"status"}
            assert "cookie" not in json.dumps(ans).lower()
            assert _serve.USER not in json.dumps(ans)

            # other commands keep answering not_logged_in
            for op in ("account_list", "sessions", "targets"):
                other = await rig.cmd(op)
                assert other["ok"] is False
                assert other["error"] == "not_logged_in"

            # kept until logout or exit: calling logout clears the ended login
            logout_res = await rig.cmd("logout")
            assert logout_res["ok"] is False
            assert logout_res["error"] == "not_logged_in"

            post_logout = await rig.cmd("login_status")
            assert post_logout["ok"] is False
            assert post_logout["error"] == "not_logged_in"

    asyncio.run(check())


def test_login_status_refused_retry_keeps_ended_login(serve, tmp_path) -> None:
    async def check() -> None:
        _server, url = serve()
        sid = make_session(url, _serve.USER, 80, 24, target="normal")
        async with Rig(url, tmp_path=tmp_path, overrides=("drive.events=login_ended,ended",)) as rig:
            await rig.login()
            await rig.attach(sid)

            live = await rig.cmd("login_status")
            assert live["ok"] is True and live["status"] == 200

            await rig.app_call(rig.app.client.set_account_password, _serve.USER, SECRET)
            for _ in range(300):
                if await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen)):
                    break
                await rig.pilot.pause(0.01)
            assert await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen))
            assert rig.events("login_ended") == [{"ev": "login_ended", "session": sid, "reason": "account"}]

            ans = await rig.cmd("login_status")
            assert ans["ok"] is True
            assert ans["status"] == 401

            # A refused retry with wrong password
            refused = await rig.cmd("login", user=_serve.USER, password_file=rig.password_file("wrong-password"))
            assert refused["ok"] is False
            assert refused["error"] == "login_failed"

            # Retained ended login still answers 401
            still = await rig.cmd("login_status")
            assert still["ok"] is True
            assert still["status"] == 401
            assert set(still.keys()) - {"id", "ok"} == {"status"}
            assert "cookie" not in json.dumps(still).lower()

    asyncio.run(check())


def test_login_status_active_401_ends_login(serve, tmp_path) -> None:
    async def check() -> None:
        server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()

            live = await rig.cmd("login_status")
            assert live["ok"] is True and live["status"] == 200

            # Invalidate the session on the server with no attachment event
            server.logins.clear()

            # login_status discovers 401 on the active login
            ans = await rig.cmd("login_status")
            assert ans["ok"] is True
            assert ans["status"] == 401
            assert set(ans.keys()) - {"id", "ok"} == {"status"}
            assert "cookie" not in json.dumps(ans).lower()

            # Moves app to the login screen
            for _ in range(300):
                if await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen)):
                    break
                await rig.pilot.pause(0.01)
            assert await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen))

            # Next session command answers not_logged_in
            sess = await rig.cmd("sessions")
            assert sess["ok"] is False
            assert sess["error"] == "not_logged_in"

            # Subsequent login_status still answers 401 from ended login until logout
            probe_again = await rig.cmd("login_status")
            assert probe_again["ok"] is True
            assert probe_again["status"] == 401

    asyncio.run(check())


def test_login_status_poller_401_retains_client(serve, tmp_path) -> None:
    async def check() -> None:
        server, url = serve()
        async with Rig(url, tmp_path=tmp_path, overrides=("fleet_view.refresh_s=1",)) as rig:
            await rig.login()

            live = await rig.cmd("login_status")
            assert live["ok"] is True and live["status"] == 200

            # Invalidate the session on the server with no attachment event and no probe
            server.logins.clear()

            # The poller meets the 401 first, returning the app to LoginScreen
            for _ in range(300):
                if await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen)):
                    break
                await rig.pilot.pause(0.01)
            assert await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen))

            # login_status answers 401, not not_logged_in
            ans = await rig.cmd("login_status")
            assert ans["ok"] is True
            assert ans["status"] == 401
            assert set(ans.keys()) - {"id", "ok"} == {"status"}
            assert "cookie" not in json.dumps(ans).lower()

            # Other commands keep answering not_logged_in
            sess = await rig.cmd("sessions")
            assert sess["ok"] is False
            assert sess["error"] == "not_logged_in"

            # Kept until logout
            logout_res = await rig.cmd("logout")
            assert logout_res["ok"] is False
            assert logout_res["error"] == "not_logged_in"

            post_logout = await rig.cmd("login_status")
            assert post_logout["ok"] is False
            assert post_logout["error"] == "not_logged_in"

    asyncio.run(check())


def test_login_status_replaced_client_does_not_end_active_login(serve, tmp_path) -> None:
    async def check() -> None:
        _server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()

            # A 401 on an old replaced client does not tear down the current workspace
            old_client = rig.app._new_client()
            err = ApiError(401, "unauthenticated", "Old login expired")
            await rig.app_call(lambda: rig.app.notify_api_error(err, client=old_client))

            # The current workspace remains active
            assert await rig.app_call(lambda: rig.app.workspace() is not None)
            live = await rig.cmd("login_status")
            assert live["ok"] is True and live["status"] == 200

    asyncio.run(check())


def test_login_status_login_ended_during_sign_out(serve, tmp_path) -> None:
    async def check() -> None:
        _server, url = serve()
        sid = make_session(url, _serve.USER, 80, 24, target="normal")
        async with Rig(url, tmp_path=tmp_path, overrides=("drive.events=login_ended,ended",)) as rig:
            await rig.login()
            await rig.attach(sid)

            live = await rig.cmd("login_status")
            assert live["ok"] is True and live["status"] == 200

            real_sign_out = rig.app.sign_out

            async def sign_out_with_login_ended():
                task = asyncio.create_task(real_sign_out())
                await asyncio.sleep(0.01)
                assert rig.app.signing_out
                rig.driver.emit_event({"ev": "login_ended", "session": sid, "reason": "account"})
                return await task

            rig.app.sign_out = sign_out_with_login_ended

            logout_res = await rig.cmd("logout")
            assert logout_res["ok"] is True

            post_logout = await rig.cmd("login_status")
            assert post_logout["ok"] is False
            assert post_logout["error"] == "not_logged_in"

    asyncio.run(check())


def test_login_status_sign_out_replaces_client_in_flight(serve, tmp_path) -> None:
    async def check() -> None:
        _server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()

            in_flight = threading.Event()
            signed_out = threading.Event()
            old_client = rig.app.client
            original_me = old_client.me

            def intercepted_me():
                in_flight.set()
                res = original_me()
                signed_out.wait(timeout=5.0)
                return res

            old_client.me = intercepted_me

            status_fut = rig.send("login_status")
            for _ in range(300):
                if in_flight.is_set():
                    break
                await asyncio.sleep(0.01)
            assert in_flight.is_set()

            await rig.app_call(rig.app.sign_out)
            assert await rig.app_call(lambda: rig.app.client is not old_client)
            assert await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen))
            signed_out.set()

            ans = await status_fut
            assert ans["ok"] is False
            assert ans["error"] == "not_logged_in"
            assert "cookie" not in json.dumps(ans).lower()

    asyncio.run(check())


def test_login_status_probe_in_flight_when_401_moves_to_login(serve, tmp_path) -> None:
    async def check() -> None:
        _server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()

            in_flight = threading.Event()
            moved_to_login = threading.Event()
            client = rig.app.client
            original_me = client.me

            def intercepted_me():
                in_flight.set()
                res = original_me()
                moved_to_login.wait(timeout=5.0)
                return res

            client.me = intercepted_me

            status_fut = rig.send("login_status")
            for _ in range(300):
                if in_flight.is_set():
                    break
                await asyncio.sleep(0.01)
            assert in_flight.is_set()

            # Another request encounters a 401 and moves the app to LoginScreen
            await rig.app_call(lambda: rig.app.notify_api_error(ApiError(401, "unauthenticated", "Other request 401")))
            assert await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen))
            moved_to_login.set()

            ans = await status_fut
            assert ans["ok"] is False
            assert ans["error"] == "not_logged_in"
            assert "cookie" not in json.dumps(ans).lower()

    asyncio.run(check())


def test_login_status_old_poll_401_after_sign_out_and_new_login(serve, tmp_path, monkeypatch) -> None:
    async def check() -> None:
        from textual.app import App

        _server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()

            live = await rig.cmd("login_status")
            assert live["ok"] is True and live["status"] == 200

            # Under Login 1, an old poll in a background thread schedules its 401 callback
            delayed_callbacks = []
            orig_cft = App.call_from_thread

            def defer_cft(self, callback, *args, **kwargs):
                if self is rig.app and (callback == rig.app.notify_api_error or getattr(callback, "__name__", "") == "notify_callback"):
                    delayed_callbacks.append((callback, args, kwargs))
                    return None
                return orig_cft(self, callback, *args, **kwargs)

            monkeypatch.setattr(App, "call_from_thread", defer_cft)

            poll_err = ApiError(401, "unauthenticated", "Fleet poll session expired")
            def poll_worker():
                rig.app.call_from_thread(rig.app.notify_api_error, poll_err)

            t = threading.Thread(target=poll_worker)
            t.start()
            t.join()
            assert len(delayed_callbacks) == 1

            # Sign out and log in with a new login (Login 2)
            await rig.cmd("logout")
            assert await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen))

            await rig.login()
            live2 = await rig.cmd("login_status")
            assert live2["ok"] is True and live2["status"] == 200

            # Now the old poll's delayed 401 callback runs
            cb, args, kwargs = delayed_callbacks[0]
            await rig.app_call(lambda: cb(*args, **kwargs))

            # The new login is not marked ended: workspace is active and login_status is 200
            assert await rig.app_call(lambda: rig.app.workspace() is not None)
            assert await rig.app_call(lambda: not isinstance(rig.app.screen, LoginScreen))

            post_cb = await rig.cmd("login_status")
            assert post_cb["ok"] is True and post_cb["status"] == 200
            assert rig.driver.commands._ended_login is None

    asyncio.run(check())


