from __future__ import annotations

import asyncio
import json

from drive_support import Rig
from input_server import make_session
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
