"""Account administration and own password change through the published client."""
from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Input, Static
from tsb_contract_client import ApiError

from .._transport import HttpError
from ..model.targets import as_text
from ..vault_flow import call, failure_message

LOGIN_ENDED = "Your login ended by an account change (disabled or a new password). Your sessions keep running."


async def account_request(app, operation: str, username: str = "", password: str = "",
                          current_password: str = ""):
    """One account act bound to its submitting login; stale answers never navigate or update it."""
    client, generation = app.client, app._login_gen

    def current():
        return client is app.client and generation == app._login_gen and app.workspace() is not None

    if app.screen_lock.locked:
        raise ValueError("the screen is locked")
    if not current():
        raise ValueError("the login changed")
    methods = {
        "list": (client.accounts, ()),
        "new": (client.create_account, (username, password)),
        "disable": (client.disable_account, (username,)),
        "enable": (client.enable_account, (username, password)),
        "password": (client.set_account_password, (username, password)),
        "own": (client.change_own_password, (current_password, password)),
    }
    func, args = methods[operation]
    methods = None
    try:
        result = await call(app, func, *args)
    except ApiError as exc:
        if current():
            if exc.status == 401:
                app.notify_api_error(exc)
            elif exc.status == 403 and operation != "own":
                await refresh_permissions(app)
        raise
    finally:
        password = current_password = ""
        args = ()
    if client is not app.client or generation != app._login_gen:
        raise ValueError("the login changed")
    if operation == "password" and username == app.username:
        workspace = app.workspace()
        if workspace is not None:
            workspace.account_login_ended()
    elif not current():
        raise ValueError("the login changed")
    return result


async def refresh_permissions(app) -> bool:
    client, generation = app.client, app._login_gen
    try:
        me = await call(app, client.me)
    except ApiError as exc:
        if client is app.client and generation == app._login_gen and exc.status == 401:
            app.notify_api_error(exc)
        raise
    if client is not app.client or generation != app._login_gen or app.workspace() is None:
        raise ValueError("the login changed")
    app.permissions = frozenset(me.get("permissions", ()))
    app.screen_lock.apply(me.get("locks"))
    permitted = "account.admin" in app.permissions
    if not permitted:
        def close_accounts():
            if client is not app.client or generation != app._login_gen:
                return
            while isinstance(app.screen, (AccountsScreen, AccountFormScreen)):
                app.pop_screen()
            app.notify("Account administration permission is gone; Accounts closed.", markup=False)

        if app.screen_lock.locked:
            app.screen_lock.after_unlock(close_accounts)
        else:
            close_accounts()
    return permitted


class AccountsScreen(Screen):
    DEFAULT_CSS = """
    AccountsScreen { align: center middle; }
    AccountsScreen #accounts-panel { width: 100%; max-width: 110; height: 100%; max-height: 40;
                                     border: round $frame; padding: 0 1; }
    AccountsScreen DataTable { height: 1fr; }
    AccountsScreen Horizontal { height: auto; }
    AccountsScreen Button { min-width: 12; margin-right: 1; }
    AccountsScreen #account-status { height: auto; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "back", "Back"), Binding("r", "refresh", "Refresh")]

    def __init__(self):
        super().__init__()
        self.accounts: list[dict] = []
        self.loaded = False
        self._load = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="accounts-panel"):
            yield Static("Accounts", markup=False)
            yield Static("You cannot disable your own account. Set password ends every login of that account.",
                         markup=False)
            yield DataTable(id="accounts-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="account-status", markup=False)
            for actions in ((("new", "Create…"), ("disable", "Disable…"), ("enable", "Enable…")),
                            (("password", "Set password…"), ("refresh", "Refresh"), ("back", "Back"))):
                with Horizontal():
                    for action, label in actions:
                        yield Button(label, id=f"account-{action}")

    def on_mount(self):
        self.query_one(DataTable).add_columns("Username", "Created", "Status")
        self.action_refresh()

    def action_refresh(self):
        self._load += 1
        self.loaded = False
        self.run_worker(self._refresh(self._load))

    async def _refresh(self, load):
        try:
            if not await refresh_permissions(self.app):
                return
            records = await account_request(self.app, "list")
            if not self.is_attached or load != self._load:
                return
            self.accounts = sorted(records, key=lambda r: r["username"])
            table = self.query_one(DataTable)
            table.clear()
            for row in self.accounts:
                table.add_row(Text(as_text(row["username"])), Text(as_text(row["created_at"])),
                              Text("disabled" if row["disabled"] else "enabled"))
            self._buttons()
        except (ApiError, HttpError, OSError, ValueError) as exc:
            self.say(failure_message(exc))
        finally:
            if load == self._load:
                self.loaded = True

    def say(self, text):
        if self.is_attached:
            self.query_one("#account-status", Static).update(as_text(text))

    def selected(self):
        row = self.query_one(DataTable).cursor_row
        return self.accounts[row] if 0 <= row < len(self.accounts) else None

    def _buttons(self):
        row = self.selected()
        for operation in ("disable", "enable", "password"):
            allowed = row is not None and (row["disabled"] if operation == "enable" else not row["disabled"])
            if operation == "disable" and row and row["username"] == self.app.username:
                allowed = False
            self.query_one(f"#account-{operation}", Button).disabled = not allowed

    def on_data_table_row_highlighted(self):
        self._buttons()

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        operation = (event.button.id or "").removeprefix("account-")
        if operation == "refresh":
            self.action_refresh()
        elif operation == "back":
            self.action_back()
        else:
            row = self.selected()
            if operation != "new" and row is None:
                self.say("Select an account first.")
                return
            self.app.push_screen(AccountFormScreen(operation, row["username"] if row else ""), self._changed)

    def _changed(self, result):
        if result is not None and self.is_attached:
            if "permissions" in result:
                permissions = ", ".join(result["permissions"])
                self.say(f"Created. Existing permissions: {permissions}" if permissions else
                         "Created: no rights yet. Rights are granted in the host policy.")
            else:
                self.say("Account updated.")
            self.action_refresh()

    def action_back(self):
        self.app.pop_screen()


class AccountFormScreen(ModalScreen[dict | None]):
    DEFAULT_CSS = """
    AccountFormScreen { align: center middle; }
    AccountFormScreen .panel { width: 80; max-width: 100%; height: auto; border: round $frame;
                               background: $surface; padding: 1 2; }
    AccountFormScreen Horizontal { height: auto; margin-top: 1; }
    AccountFormScreen Button { margin-right: 2; }
    AccountFormScreen Static { height: auto; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "back", "Cancel")]

    def __init__(self, operation: str, username: str = ""):
        super().__init__()
        self.operation = operation
        self.username = username
        self.busy = False
        self._generation = None

    def compose(self) -> ComposeResult:
        title = {"new": "Create account", "disable": "Disable account", "enable": "Enable account",
                 "password": "Set account password", "own": "Change my password"}[self.operation]
        with Vertical(classes="panel"):
            yield Static(f"{title}: {as_text(self.username)}" if self.username else title, markup=False)
            if self.operation == "new":
                yield Input(placeholder="Username", id="account-username")
            if self.operation == "disable":
                yield Static("Its logins end at once while its sessions run on. Disable this account?", markup=False)
            if self.operation == "password":
                yield Static("Every login of this account ends, including yours if this is your account. "
                             "Its sessions run on.", markup=False)
            if self.operation == "own":
                yield Static("This login stays signed in; your other logins end. Sessions run on.", markup=False)
                yield Input(placeholder="Current password", password=True, max_length=1024,
                            id="account-current-password")
            if self.operation != "disable":
                yield Input(placeholder="New password", password=True, max_length=1024, id="account-password")
            yield Static("", id="account-form-status", markup=False)
            with Horizontal():
                yield Button("Confirm", id="account-confirm", variant="primary")
                yield Button("Cancel", id="account-cancel")

    def on_mount(self):
        self._generation = self.app._login_gen

    def clear_passwords(self):
        for box in self.query(Input):
            if box.password:
                box.value = ""

    def on_unmount(self):
        self.clear_passwords()

    def action_back(self):
        self.clear_passwords()
        if self.app.screen is self:
            self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        if event.button.id == "account-confirm" and not self.busy:
            self.run_worker(self.submit())
        elif event.button.id == "account-cancel":
            self.action_back()

    async def submit(self):
        if self.busy or not self.is_attached or self._generation != self.app._login_gen:
            self.clear_passwords()
            return
        self.busy = True
        password = self.query_one("#account-password", Input).value if self.operation != "disable" else ""
        current = self.query_one("#account-current-password", Input).value if self.operation == "own" else ""
        username = self.query_one("#account-username", Input).value if self.operation == "new" else self.username
        self.clear_passwords()
        self.query_one("#account-confirm", Button).disabled = True
        try:
            result = await account_request(self.app, self.operation, username, password, current)
            def completed():
                if self.is_attached and self.app.screen is self and self._generation == self.app._login_gen:
                    self.dismiss(result if result is not None else {})

            if self.app.screen_lock.locked:
                self.app.screen_lock.after_unlock(completed)
            else:
                completed()
        except (ApiError, HttpError, OSError, ValueError) as exc:
            if self.is_attached and self._generation == self.app._login_gen:
                self.query_one("#account-form-status", Static).update(as_text(failure_message(exc)))
        finally:
            password = current = ""
            self.busy = False
            if self.is_attached:
                self.query_one("#account-confirm", Button).disabled = False
