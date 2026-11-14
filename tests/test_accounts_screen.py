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
            await wait_until(pilot, lambda: any(
                "Account administration permission is gone; Accounts closed." in str(n.message)
                for n in app._notifications), "permission lost notice")
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


def test_only_the_newest_permission_refresh_applies(serve, wait_until):
    import threading

    from tsb_tui.screens.accounts import AccountsScreen

    first_entered, second_done = threading.Event(), threading.Event()

    class Overlap(_serve.ServeApp):
        def __init__(self):
            super().__init__()
            self.me_calls = 0

        def http(self, method, path, headers, body):
            if method == "GET" and path == "/auth/me":
                result = super().http(method, path, headers, body)
                self.me_calls += 1
                if self.me_calls == 2:          # refresh on open: the older answer, without account.admin
                    first_entered.set()
                    assert second_done.wait(10)
                    perms = result[2].get("permissions", [])
                    result = (result[0], result[1], {**result[2], "permissions": [p for p in perms if p != "account.admin"]})
                elif self.me_calls == 3:        # the newer refresh answers first, granting account.admin
                    second_done.set()
                return result
            return super().http(method, path, headers, body)

    async def check():
        server, url = serve(Overlap())
        app = TsbTuiApp(url)
        try:
            async with app.run_test(size=(140, 40)) as pilot:
                workspace = await fixture_login(app, pilot)
                await workspace.run_action_id("accounts")
                await wait_until(pilot, first_entered.is_set, "first refresh in flight")
                screen = app.screen
                assert isinstance(screen, AccountsScreen)
                screen.action_refresh()          # the newer refresh: grants account.admin, answers first
                await wait_until(pilot, second_done.is_set, "newer refresh answered")
                await wait_until(pilot, lambda: server.me_calls >= 3, "older refresh answered")
                for _ in range(15):
                    await pilot.pause()
                assert "account.admin" in app.permissions, app.permissions
                assert isinstance(app.screen, AccountsScreen), app.screen
                assert app.workspace() is workspace
        finally:
            second_done.set()
    asyncio.run(check())


def test_a_stale_refresh_401_does_not_end_the_current_login(serve, wait_until):
    import threading

    from tsb_tui.screens.accounts import AccountsScreen

    first_entered, second_done = threading.Event(), threading.Event()

    class Stale401(_serve.ServeApp):
        def __init__(self):
            super().__init__()
            self.me_calls = 0

        def http(self, method, path, headers, body):
            if method == "GET" and path == "/auth/me":
                self.me_calls += 1
                n = self.me_calls
                if n == 2:                        # the older refresh answers 401 after the newer succeeded
                    first_entered.set()
                    assert second_done.wait(10)
                    return self._err(401, "unauthenticated", "log in first")
                result = super().http(method, path, headers, body)
                if n == 3:                        # the newer refresh succeeds first, granting account.admin
                    second_done.set()
                return result
            return super().http(method, path, headers, body)

    async def check():
        server, url = serve(Stale401())
        app = TsbTuiApp(url)
        try:
            async with app.run_test(size=(140, 40)) as pilot:
                workspace = await fixture_login(app, pilot)
                await workspace.run_action_id("accounts")
                await wait_until(pilot, first_entered.is_set, "older refresh in flight")
                screen = app.screen
                assert isinstance(screen, AccountsScreen)
                screen.action_refresh()          # the newer refresh, succeeds first
                await wait_until(pilot, second_done.is_set, "newer refresh answered")
                await wait_until(pilot, lambda: server.me_calls >= 3, "older refresh answered")
                for _ in range(15):
                    await pilot.pause()
                assert app.workspace() is workspace, app.screen
                assert isinstance(app.screen, AccountsScreen), app.screen
                assert "account.admin" in app.permissions, app.permissions
        finally:
            second_done.set()
    asyncio.run(check())


def test_a_stale_list_failure_does_not_overwrite_the_newer_result(serve, wait_until, screen_text):
    import threading

    from tsb_tui.screens.accounts import AccountsScreen

    first_entered, second_done = threading.Event(), threading.Event()
    MARKER = "STALE-LIST-FAILURE-429"

    class StaleList(_serve.ServeApp):
        def __init__(self):
            super().__init__()
            self.list_calls = 0

        def http(self, method, path, headers, body):
            if method == "GET" and path == "/accounts":
                self.list_calls += 1
                n = self.list_calls
                if n == 1:                        # the earlier load's list fails after the later one succeeded
                    first_entered.set()
                    assert second_done.wait(10)
                    return self._err(429, "rate_limited", MARKER)
                result = super().http(method, path, headers, body)
                if n == 2:                        # the later load's list succeeds first
                    second_done.set()
                return result
            return super().http(method, path, headers, body)

    async def check():
        server, url = serve(StaleList())
        app = TsbTuiApp(url)
        try:
            async with app.run_test(size=(140, 40)) as pilot:
                workspace = await fixture_login(app, pilot)
                await workspace.run_action_id("accounts")
                await wait_until(pilot, first_entered.is_set, "earlier load in flight")
                screen = app.screen
                assert isinstance(screen, AccountsScreen)
                screen.action_refresh()          # the later load, succeeds first
                await wait_until(pilot, second_done.is_set, "later list answered")
                await wait_until(pilot, lambda: server.list_calls >= 2, "earlier list answered")
                for _ in range(15):
                    await pilot.pause()
                assert [r["username"] for r in screen.accounts] == sorted(server.users)
                assert MARKER not in screen_text(app), screen_text(app)
        finally:
            second_done.set()
    asyncio.run(check())


def test_cancel_and_escape_do_not_dismiss_a_pending_submission(serve, wait_until, screen_text):
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
            async with app.run_test(size=(140, 40)) as pilot:
                _, screen = await open_accounts(app, pilot, wait_until)
                screen.query_one("#account-new", Button).press()
                await wait_until(pilot, lambda: bool(app.screen.query("#account-confirm")), "form")
                form = app.screen
                form.query_one("#account-username", Input).value = "while-busy"
                form.query_one("#account-password", Input).value = SECRET
                form.query_one("#account-confirm", Button).press()
                await wait_until(pilot, entered.is_set, "request pending")
                assert form.busy
                assert form.query_one("#account-cancel", Button).disabled
                await pilot.press("escape")
                await pilot.pause()
                assert app.screen is form
                form.query_one("#account-cancel", Button).press()
                await pilot.pause()
                assert app.screen is form
                release.set()
                await wait_until(pilot, lambda: app.screen is screen, "form dismissed with its result")
                await wait_until(pilot, lambda: "while-busy" in [r["username"] for r in screen.accounts],
                                 "list refreshed")
                assert "while-busy" in server.users
                assert "Created" in screen_text(app)
        finally:
            release.set()
    asyncio.run(check())


def test_own_change_notice_on_success_and_silent_cancel(serve, wait_until):
    notice = "Password changed. Your other logins ended; this one stays."

    async def check():
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            workspace = await fixture_login(app, pilot)
            await workspace.run_action_id("password_change")
            await wait_until(pilot, lambda: bool(app.screen.query("#account-cancel")), "form")
            app.screen.query_one("#account-cancel", Button).press()
            await wait_until(pilot, lambda: app.screen is workspace, "cancel dismissed the form")
            await pilot.pause()
            assert all(notice not in str(n.message) for n in app._notifications), app._notifications
            await workspace.run_action_id("password_change")
            await submit_form(app, pilot, wait_until, **{"account-current-password": _serve.PASSWORD,
                                                         "account-password": SECRET})
            await wait_until(pilot, lambda: app.screen is workspace, "own change dismissed the form")
            await wait_until(pilot, lambda: any(notice in str(n.message) for n in app._notifications),
                             "the change notice")
    asyncio.run(check())


def test_refresh_inserts_earlier_sorted_username_keeps_cursor_and_selection(serve, wait_until, screen_text):
    async def check():
        server, url = serve()
        server.users["subject"] = dict(server.users[_serve.USER], username="subject", disabled=False)
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            _, screen = await open_accounts(app, pilot, wait_until)
            index = next(i for i, r in enumerate(screen.accounts) if r["username"] == "subject")
            screen.query_one(DataTable).move_cursor(row=index)
            await pilot.pause()
            assert screen.selected() == "subject"

            server.users["aaa"] = dict(server.users[_serve.USER], username="aaa", disabled=False)
            screen.action_refresh()
            await wait_until(pilot, lambda: screen.loaded and any(r["username"] == "aaa" for r in screen.accounts),
                             "refresh")
            await pilot.pause()

            table = screen.query_one(DataTable)
            subject_idx = next(i for i, r in enumerate(screen.accounts) if r["username"] == "subject")
            assert table.cursor_row == subject_idx
            assert screen.selected() == "subject"

            screen.query_one("#account-disable", Button).press()
            await wait_until(pilot, lambda: bool(app.screen.query("#account-confirm")), "confirmation")
            await pilot.pause()
            assert app.screen.username == "subject"
            assert "subject" in screen_text(app)
            assert "Disable account: subject" in screen_text(app)
    asyncio.run(check())


def test_refresh_selected_user_gone_moves_cursor_to_first_row_and_confirmation_names_that_user(
        serve, wait_until, screen_text):
    async def check():
        server, url = serve()
        server.users["subject"] = dict(server.users[_serve.USER], username="subject", disabled=False)
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            _, screen = await open_accounts(app, pilot, wait_until)
            index = next(i for i, r in enumerate(screen.accounts) if r["username"] == "subject")
            screen.query_one(DataTable).move_cursor(row=index)
            await pilot.pause()
            assert screen.selected() == "subject"

            del server.users["subject"]
            screen.action_refresh()
            await wait_until(pilot, lambda: screen.loaded and not any(r["username"] == "subject" for r in screen.accounts),
                             "refresh")
            await pilot.pause()

            table = screen.query_one(DataTable)
            first_user = screen.accounts[0]["username"]
            assert table.cursor_row == 0
            assert screen.selected() == first_user
            assert screen.selected() != "subject"

            screen.query_one("#account-disable", Button).press()
            await wait_until(pilot, lambda: bool(app.screen.query("#account-confirm")), "confirmation")
            await pilot.pause()
            assert app.screen.username == first_user
            assert "subject" not in screen_text(app)
            assert f"Disable account: {first_user}" in screen_text(app)
    asyncio.run(check())

