"""File-only account automation and account-ended login consumer graders."""
from __future__ import annotations

import asyncio
import io
import json

import pytest
from drive_support import Rig
from tsbc import _serve

SECRET = "canary-account-password-7462"


@pytest.mark.parametrize("op", ["account_new", "account_disable", "account_enable", "account_password", "password_change"])
def test_account_writes_need_confirmation_and_refuse_inline_secrets(serve, tmp_path, op):
    async def check():
        server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            before = len(server.requests)
            result = await rig.cmd(op, username="subject")
            assert result["error"] == "bad_request" and "confirm" in result["why"]
            result = await rig.cmd(op, username="subject", password=SECRET, confirm=True)
            assert result["error"] == "bad_request" and SECRET not in json.dumps(result)
            assert not any(p.startswith("/accounts") or p == "/auth/password" for _, p, _ in server.requests[before:])
    asyncio.run(check())


def test_account_list_is_sorted_by_username(serve, tmp_path):
    """`account_list` answers `accounts` sorted by username, as the GUIDE promises and the Accounts
    screen sorts, whatever order the client returned. A drive that echoed the client's order would
    hand U11 a list the screen's own sort disagrees with."""
    async def check():
        _, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            unsorted = [{"username": "zeta", "created_at": "2026-01-03", "disabled": False},
                        {"username": "alpha", "created_at": "2026-01-01", "disabled": True},
                        {"username": "mid", "created_at": "2026-01-02", "disabled": False}]

            def fake_accounts():
                return unsorted
            await rig.app_call(lambda: setattr(rig.app.client, "accounts", fake_accounts))
            listed = await rig.cmd("account_list")
            assert listed["ok"]
            assert [a["username"] for a in listed["accounts"]] == ["alpha", "mid", "zeta"]
    asyncio.run(check())


def test_rejected_account_list_password_is_redacted(serve, tmp_path):
    """Under `--record`, the password fields are redacted for every `account_*` command, a rejected
    one included: a refused `account_list` that carried a `password` leaves no trace of it in the
    transcript. A redaction keyed on the five writes alone left this `account_list` verbatim."""
    async def check():
        _, url = serve()
        record = io.StringIO()
        async with Rig(url, tmp_path=tmp_path, record=record) as rig:
            await rig.login()
            result = await rig.cmd("account_list", password=SECRET)
            assert result["error"] == "bad_request"
            assert SECRET not in record.getvalue()
    asyncio.run(check())


def test_all_six_account_commands_and_secret_canary(serve, tmp_path):
    async def check():
        server, url = serve()
        record = io.StringIO()
        async with Rig(url, tmp_path=tmp_path, record=record) as rig:
            await rig.login()
            path = rig.password_file(SECRET)
            listed = await rig.cmd("account_list")
            assert listed["ok"]
            assert [a["username"] for a in listed["accounts"]] == sorted(server.users)
            made = await rig.cmd("account_new", username="subject", password_file=path, confirm=True)
            assert made["ok"] and made["account"]["username"] == "subject" and "permissions" in made
            for op in ("account_password", "account_disable", "account_enable"):
                result = await rig.cmd(op, username="subject", password_file=path, confirm=True)
                assert result["ok"], result
            result = await rig.cmd("password_change", current_password_file=rig.password_file(),
                                   new_password_file=path, confirm=True)
            assert result["ok"], result
            assert (await rig.cmd("account_list"))["ok"]
            assert SECRET not in record.getvalue()
            assert SECRET not in json.dumps(rig.lines)
            assert SECRET not in await rig.app_call(rig.driver.screen_text)
    asyncio.run(check())


@pytest.mark.parametrize("op", ["account_list", "account_new", "account_disable", "account_enable", "account_password", "password_change"])
def test_locked_account_commands_send_nothing(serve, tmp_path, op):
    async def check():
        server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.app_call(rig.app.screen_lock.lock)
            before = len(server.requests)
            result = await rig.cmd(op, username="subject", confirm=True)
            assert result["error"] == "not_available"
            assert server.requests[before:] == []
    asyncio.run(check())


def test_account_login_ended_close_4017(serve, tmp_path):
    from input_server import make_session

    from tsb_tui.screens.login import LoginScreen

    async def check():
        server, url = serve()
        sid = make_session(url, _serve.USER, 80, 24, target="normal")
        async with Rig(url, tmp_path=tmp_path, overrides=('drive.events=login_ended,ended',)) as rig:
            await rig.login()
            await rig.attach(sid)
            await rig.app_call(rig.app.client.set_account_password, _serve.USER, SECRET)
            for _ in range(300):
                if await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen)):
                    break
                await rig.pilot.pause(0.01)
            assert await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen))
            assert rig.events("login_ended") == [{"ev": "login_ended", "session": sid, "reason": "account"}]
            assert rig.events("ended") == []
            assert server.sessions[sid]["state"] not in ("killed", "terminated")
            assert not (await rig.cmd("account_list"))["ok"]
    asyncio.run(check())


def test_account_device_login_ended_close_4017(serve, wait_until):
    from textual.widgets import Input
    from workspace_support import fixture_login, open_new_session

    from tsb_tui.app import TsbTuiApp
    from tsb_tui.screens.login import LoginScreen

    async def check():
        server, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            workspace = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: any(row.id == "normal" for row in app.workspace().targets.rows),
                             "the inventory")
            dialog = await open_new_session(app, pilot)
            dialog.query_one("#new-session-filter", Input).value = "normal"
            await pilot.pause()
            dialog.query_one("#device-login-start").press()
            await wait_until(pilot, lambda: workspace.front is not None and workspace.front.device_login is not None,
                             "device login pane")
            pane = workspace.front.device_login
            await wait_until(pilot, lambda: pane.is_mounted, "pane mounted")
            if pane.query_one("#login-consent").display:
                pane.query_one("#login-consent").press()
                await pilot.pause()
            pane.query_one("#device-user", Input).value = "operator"
            await pane.submit_user()
            await wait_until(pilot, lambda: pane.hostkey_pending, "device host key")
            await pane.answer_hostkey(True)
            await wait_until(pilot, lambda: pane.prompt is not None, "device prompt")
            events = []
            pane._emit = events.append
            app.client.set_account_password(_serve.USER, SECRET)
            await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen), "account-ended login")
            assert pane.login.close_code == 4017
            assert not pane.connected
            assert not any(event["ev"] == "device_login_result" for event in events)
            assert not server.sessions
    asyncio.run(check())


@pytest.mark.parametrize("operation,username,error", [("account_disable", _serve.USER, "account_self_disable"),
    ("account_enable", _serve.USER, "account_already_enabled"),
    ("account_password", "missing", "user_not_found"),
    ("account_new", _serve.USER, "user_exists"),
    ("account_new", "shared", "invalid_request")])
def test_account_refusals_are_core_codes_and_fixed_text(serve, tmp_path, operation, username, error):
    async def check():
        _, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            result = await rig.cmd(operation, username=username, password_file=rig.password_file(SECRET), confirm=True)
            assert result["error"] == error
            assert SECRET not in json.dumps(result)
            assert (await rig.cmd("account_list"))["ok"]
    asyncio.run(check())


def test_admin_self_password_ends_login_without_attachment(serve, tmp_path):
    async def check():
        _, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            assert (await rig.cmd("account_password", username=_serve.USER,
                                  password_file=rig.password_file(SECRET), confirm=True))["ok"]
            assert (await rig.cmd("account_list"))["error"] == "not_logged_in"
    asyncio.run(check())


def test_inline_account_passwords_never_enter_transcript(serve, tmp_path, caplog):
    async def check():
        _, url = serve()
        record = io.StringIO()
        async with Rig(url, tmp_path=tmp_path, record=record) as rig:
            await rig.login()
            for field in ("password", "current_password", "new_password"):
                result = await rig.cmd("password_change", confirm=True, **{field: SECRET})
                assert result["error"] == "bad_request"
            assert SECRET not in record.getvalue()
            assert SECRET not in json.dumps(rig.lines)
            assert SECRET not in caplog.text
    asyncio.run(check())


@pytest.mark.parametrize("via_command", [False, True])
def test_account_end_reports_each_attached_session_once(serve, tmp_path, via_command):
    from input_server import make_session

    from tsb_tui.screens.login import LoginScreen

    async def check():
        server, url = serve()
        sessions = [make_session(url, _serve.USER, 80, 24, target="normal") for _ in range(2)]
        async with Rig(url, tmp_path=tmp_path, overrides=('drive.events=login_ended,ended',)) as rig:
            await rig.login()
            for sid in sessions:
                await rig.attach(sid)
            before = sum(p == "/auth/ws-ticket" for _, p, _ in server.requests)
            if via_command:
                result = await rig.cmd("account_password", username=_serve.USER,
                                       password_file=rig.password_file(SECRET), confirm=True)
                assert result["ok"], result
            else:
                await rig.app_call(rig.app.client.set_account_password, _serve.USER, SECRET)
            for _ in range(300):
                if await rig.app_call(lambda: isinstance(rig.app.screen, LoginScreen)):
                    break
                await rig.pilot.pause(0.01)
            await rig.pilot.pause()
            assert sorted(e["session"] for e in rig.events("login_ended")) == sorted(sessions)
            assert not rig.events("ended")
            assert before == sum(p == "/auth/ws-ticket" for _, p, _ in server.requests)
            assert all(server.sessions[sid]["state"] not in ("killed", "terminated") for sid in sessions)
    asyncio.run(check())


def test_own_change_preserves_attached_caller_and_ends_other_login(serve, tmp_path):
    from input_server import make_session

    from tsb_tui.screens.login import LoginScreen

    async def check():
        server, url = serve()
        sid = make_session(url, _serve.USER, 80, 24, target="normal")
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        second.mkdir()
        async with Rig(url, tmp_path=first) as caller, Rig(url, tmp_path=second,
                overrides=('drive.events=login_ended,ended',)) as other:
            await caller.login()
            await other.login()
            await caller.attach(sid)
            await other.attach(sid)
            current_file = caller.password_file()
            new_file = first / "new-password"
            new_file.write_text(SECRET + "\n")
            new_file.chmod(0o600)
            result = await caller.cmd("password_change", current_password_file=current_file,
                                      new_password_file=str(new_file), confirm=True)
            assert result["ok"], result
            for _ in range(300):
                if await other.app_call(lambda: isinstance(other.app.screen, LoginScreen)):
                    break
                await other.pilot.pause(0.01)
            assert await caller.app_call(lambda: caller.app.workspace() is not None)
            assert not await caller.app_call(lambda: caller.app.workspace().front.pump.state.closed)
            assert (await caller.cmd("account_list"))["ok"]
            assert other.events("login_ended") == [{"ev": "login_ended", "session": sid, "reason": "account"}]
            assert not other.events("ended")
            assert server.sessions[sid]["state"] not in ("killed", "terminated")
    asyncio.run(check())


def test_ops_order_equals_guide_table() -> None:
    """The op list follows the GUIDE's order. In commands.py, OPS lists every op in the exact order
    the GUIDE's table lists them."""
    import re

    from tsb_tui.drive import commands, runner

    guide = runner.GUIDE.read_text(encoding="utf-8")
    table_of_commands = guide.split("## 5. Commands")[1].split("**Key names**")[0]
    ops_in_guide: list[str] = []
    for line in table_of_commands.splitlines():
        if line.startswith("| `") and not line.startswith("| `op`"):
            cell = line.split("|")[1].strip()
            ops_in_guide.extend(re.findall(r"`([a-z0-9_]+)`", cell))
    assert commands.OPS == tuple(ops_in_guide)


def test_password_change_bad_password_file_names_field(serve, tmp_path):
    """A password-file error names the field that was read: current_password_file or new_password_file."""
    async def check():
        _, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            good_file = rig.password_file()
            bad_cur = str(tmp_path / "nonexistent-current-password-file")
            bad_new = str(tmp_path / "nonexistent-new-password-file")

            # Bad current_password_file, valid new_password_file
            res_cur = await rig.cmd(
                "password_change",
                current_password_file=bad_cur,
                new_password_file=good_file,
                confirm=True,
            )
            assert res_cur["error"] == "bad_request"
            assert "current_password_file" in res_cur["why"]
            assert res_cur["why"].startswith(f"current_password_file {bad_cur}")

            # Valid current_password_file, bad new_password_file
            res_new = await rig.cmd(
                "password_change",
                current_password_file=good_file,
                new_password_file=bad_new,
                confirm=True,
            )
            assert res_new["error"] == "bad_request"
            assert "new_password_file" in res_new["why"]
            assert res_new["why"].startswith(f"new_password_file {bad_new}")
    asyncio.run(check())


def test_non_admin_account_list_gets_refusal_and_no_closed_notice(serve, tmp_path):
    class NonAdmin(_serve.ServeApp):
        def _permissions(self, user):
            return [p for p in super()._permissions(user) if p != "account.admin"]

    async def check():
        _, url = serve(NonAdmin())
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            result = await rig.cmd("account_list")
            assert not result.get("ok")
            assert result.get("error") == "forbidden"
            await rig.pilot.pause()
            assert not any("Accounts closed" in str(n.message) for n in rig.app._notifications)
    asyncio.run(check())
