"""Account UI consumer checks against the released contract fixture server."""
from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Button, DataTable, Input
from tsbc import _serve
from workspace_support import fixture_login

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.login import LoginScreen

SECRET = "canary-account-password-7462"


class AccountServe(_serve.ServeApp):
    def _permissions(self, user):
        return ["session.create"] if user == "inherited" else super()._permissions(user) if user != "no-rights" else []


async def open_accounts(app, pilot, wait_until):
    workspace = await fixture_login(app, pilot)
    await workspace.run_action_id("accounts")
    await wait_until(pilot, lambda: bool(app.screen.query("#accounts-table")) and app.screen.loaded, "accounts")
    return workspace, app.screen


async def submit_form(app, pilot, wait_until, **values):
    await wait_until(pilot, lambda: bool(app.screen.query("#account-confirm")), "account form")
    form = app.screen
    for key, value in values.items():
        form.query_one(f"#{key}", Input).value = value
    form.query_one("#account-confirm", Button).press()
    await pilot.pause()
    await wait_until(pilot, lambda: not form.busy, "account request")
    return form


def test_account_list_create_permissions_and_no_rights(serve, wait_until, screen_text):
    async def check():
        server, url = serve(AccountServe())
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            _, screen = await open_accounts(app, pilot, wait_until)
            names = [r["username"] for r in screen.accounts]
            assert names == sorted(server.users)
            assert names == sorted(names)
            for name, rights in (("no-rights", "no rights yet"), ("inherited", "session.create")):
                screen.query_one("#account-new", Button).press()
                form = await submit_form(app, pilot, wait_until, **{"account-username": name, "account-password": SECRET})
                await wait_until(pilot, lambda: app.screen is screen, "create result")
                await pilot.pause()
                assert rights in screen_text(app), (screen_text(app), server.requests)
                assert all(box.value == "" for box in form.query(Input) if box.password)
                assert SECRET not in screen_text(app)
                assert SECRET not in repr(vars(form))
                assert all(SECRET not in repr(vars(box)) for box in form.query(Input))
                assert server.users[name]["disabled"] is False
                assert rights in screen_text(app)
    asyncio.run(check())


def test_menu_and_keys_hide_accounts_without_permission_and_refuse_dispatch(serve, wait_until):
    from tsb_tui.screens.chrome_dialogs import keys_lines

    class NonAdmin(_serve.ServeApp):
        def _permissions(self, user):
            return [p for p in super()._permissions(user) if p != "account.admin"]

    async def check():
        _, url = serve(NonAdmin())
        app = TsbTuiApp(url)
        async with app.run_test() as pilot:
            workspace = await fixture_login(app, pilot)
            assert "accounts" not in [a.id for a, _, _ in workspace.menu_entries()]
            assert "password_change" in [a.id for a, _, _ in workspace.menu_entries()]
            assert "Accounts" not in "\n".join(keys_lines(app.cfg, workspace.facts()))
            await workspace.run_action_id("accounts")
            assert app.screen is workspace
            await pilot.pause()
            assert any("not permitted" in str(n.message) for n in app._notifications)
    asyncio.run(check())


@pytest.mark.parametrize("operation,expected", [("disable", True), ("enable", False), ("password", False)])
def test_selected_account_actions_confirm_and_follow_state(serve, wait_until, screen_text, operation, expected):
    async def check():
        server, url = serve()
        server.users["subject"] = dict(server.users[_serve.USER], username="subject", disabled=operation == "enable")
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            _, screen = await open_accounts(app, pilot, wait_until)
            index = next(i for i, r in enumerate(screen.accounts) if r["username"] == "subject")
            screen.query_one(DataTable).move_cursor(row=index)
            await pilot.pause()
            screen.query_one(f"#account-{operation}", Button).press()
            await wait_until(pilot, lambda: bool(app.screen.query("#account-confirm")), "confirmation")
            await pilot.pause()
            assert "subject" in screen_text(app)
            if operation == "disable":
                assert "logins end at once" in screen_text(app)
                assert "sessions run on" in screen_text(app)
            form = await submit_form(app, pilot, wait_until, **({"account-password": SECRET} if operation != "disable" else {}))
            await wait_until(pilot, lambda: app.screen is screen, "completed")
            assert server.users["subject"]["disabled"] is expected
            assert all(b.value == "" for b in form.query(Input) if b.password)
    asyncio.run(check())


@pytest.mark.parametrize("status,code,message", [(403, "password_change_failed", "current password is incorrect"),
                                                 (429, "rate_limited", "try later"),
                                                 (401, "unauthenticated", "login ended"),
                                                 (422, "unknown-future-code", "password is too short")])
def test_own_change_errors_by_status_keep_or_end_login(serve, wait_until, screen_text, status, code, message):
    class Refuse(_serve.ServeApp):
        def _account_action(self, actor, name, action, body, keep=None):
            if action == "own-password":
                return self._err(status, code, message)
            return super()._account_action(actor, name, action, body, keep)

    async def check():
        _, url = serve(Refuse())
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            workspace = await fixture_login(app, pilot)
            await workspace.run_action_id("password_change")
            form = await submit_form(app, pilot, wait_until, **{"account-current-password": "old", "account-password": SECRET})
            await pilot.pause()
            if status == 401:
                await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen), "login")
            else:
                assert app.workspace() is workspace
                assert message in screen_text(app)
            assert all(b.value == "" for b in form.query(Input) if b.password)
            assert SECRET not in screen_text(app)
    asyncio.run(check())


def test_own_change_nonadmin_short_current_preserves_caller(serve, wait_until):
    class NonAdmin(_serve.ServeApp):
        def _permissions(self, user):
            return []

    async def check():
        server, url = serve(NonAdmin())
        server.users[_serve.USER]["password_hash"] = server._account_hash("old")
        from workspace_support import login
        app = TsbTuiApp(url)
        async with app.run_test() as pilot:
            workspace = await login(app, pilot, _serve.USER, "old")
            await workspace.run_action_id("password_change")
            await submit_form(app, pilot, wait_until, **{"account-current-password": "old", "account-password": SECRET})
            await wait_until(pilot, lambda: app.screen is workspace, "password changed")
            assert app.client.me()["username"] == _serve.USER
    asyncio.run(check())


def test_cancel_drops_password_and_lock_covers_form(serve, wait_until, screen_text):
    async def check():
        server, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test() as pilot:
            workspace = await fixture_login(app, pilot)
            await workspace.run_action_id("password_change")
            await pilot.pause()
            form = app.screen
            form.query_one("#account-password", Input).value = SECRET
            app.screen_lock.lock()
            await pilot.pause()
            assert SECRET not in screen_text(app)
            assert "Change my password" not in screen_text(app)
            app.screen_lock.clear()
            form.action_back()
            await pilot.pause()
            assert all(b.value == "" for b in form.query(Input) if b.password)
            assert not any(m == "POST" and p == "/auth/password" for m, p, _ in server.requests)
    asyncio.run(check())


def test_refused_account_act_keeps_its_code_when_refresh_fails(serve, wait_until, screen_text):
    """A refused account act (403) keeps its own code: the permissions refresh it runs after a 403
    swallows its own failure, so the original 403 is always what is re-raised. A refresh that raised
    straight through replaced the operator's refusal with the refresh's error."""

    class Refuse(_serve.ServeApp):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.arm = False

        def http(self, method, path, headers, body):
            if (method, path) == ("GET", "/auth/me") and self.arm:
                return self._err(500, "server_error", "REFRESH-FAILED")
            return super().http(method, path, headers, body)

        def _account_action(self, actor, name, action, body, keep=None):
            if action == "disable":
                return self._err(403, "forbidden", "DISABLE-REFUSED")
            return super()._account_action(actor, name, action, body, keep)

    async def check():
        server, url = serve(Refuse())
        server.users["subject"] = dict(server.users[_serve.USER], username="subject", disabled=False)
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            workspace, screen = await open_accounts(app, pilot, wait_until)
            index = next(i for i, r in enumerate(screen.accounts) if r["username"] == "subject")
            screen.query_one(DataTable).move_cursor(row=index)
            await pilot.pause()
            server.arm = True
            screen.query_one("#account-disable", Button).press()
            await submit_form(app, pilot, wait_until)
            await pilot.pause()
            assert "DISABLE-REFUSED" in screen_text(app)
            assert "REFRESH-FAILED" not in screen_text(app)
            assert app.workspace() is workspace
    asyncio.run(check())


def test_permissions_refreshed_on_open_and_after_forbidden(serve, wait_until):
    class Revoked(_serve.ServeApp):
        admin = True

        def _permissions(self, user):
            return super()._permissions(user) if self.admin else []

    async def check():
        server, url = serve(Revoked())
        app = TsbTuiApp(url)
        async with app.run_test() as pilot:
            workspace, screen = await open_accounts(app, pilot, wait_until)
            assert sum(m == "GET" and p == "/auth/me" for m, p, _ in server.requests) >= 2
            server.admin = False
            screen.action_refresh()
            await wait_until(pilot, lambda: app.screen is workspace, "permission lost closes accounts")
            assert "account.admin" not in app.permissions
    asyncio.run(check())


def test_late_account_result_cannot_reach_replacement_login(serve, wait_until):
    import threading

    entered, release = threading.Event(), threading.Event()

    class Delayed(_serve.ServeApp):
        def http(self, method, path, headers, body):
            result = super().http(method, path, headers, body)
            if method == "POST" and path == "/accounts":
                entered.set()
                assert release.wait(10)
            return result

    async def check():
        _, url = serve(Delayed())
        app = TsbTuiApp(url)
        try:
            async with app.run_test() as pilot:
                _, screen = await open_accounts(app, pilot, wait_until)
                screen.query_one("#account-new", Button).press()
                await wait_until(pilot, lambda: bool(app.screen.query("#account-confirm")), "form")
                form = app.screen
                form.query_one("#account-username", Input).value = "late"
                form.query_one("#account-password", Input).value = SECRET
                form.query_one("#account-confirm", Button).press()
                await wait_until(pilot, entered.is_set, "request pending")
                app._to_login()
                await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen), "login lost")
                await wait_until(pilot, lambda: bool(app.screen.query("#username")), "login mounted")
                replacement = await fixture_login(app, pilot, _serve.PEER)
                release.set()
                await wait_until(pilot, lambda: not form.busy, "old request settled")
                assert app.screen is replacement
                assert app.username == _serve.PEER
        finally:
            release.set()
    asyncio.run(check())


def test_lock_during_pending_account_request_defers_completion(serve, wait_until, screen_text):
    import threading

    entered, release = threading.Event(), threading.Event()

    class Delayed(_serve.ServeApp):
        def http(self, method, path, headers, body):
            result = super().http(method, path, headers, body)
            if method == "POST" and path == "/accounts":
                entered.set()
                assert release.wait(10)
            return result

    async def check():
        server, url = serve(Delayed())
        app = TsbTuiApp(url)
        try:
            async with app.run_test() as pilot:
                _, screen = await open_accounts(app, pilot, wait_until)
                screen.query_one("#account-new", Button).press()
                await pilot.pause()
                form = app.screen
                form.query_one("#account-username", Input).value = "under-lock"
                form.query_one("#account-password", Input).value = SECRET
                form.query_one("#account-confirm", Button).press()
                await wait_until(pilot, entered.is_set, "request pending")
                app.screen_lock.lock()
                await pilot.pause()
                lock = app.screen
                release.set()
                await wait_until(pilot, lambda: not form.busy, "request done under lock")
                assert app.screen is lock
                assert SECRET not in screen_text(app) and "under-lock" not in screen_text(app)
                assert form.query_one("#account-password", Input).value == ""
                app.screen_lock.unlock(lock)
                await wait_until(pilot, lambda: app.screen is screen, "completion after unlock")
                assert "under-lock" in server.users
        finally:
            release.set()
    asyncio.run(check())
