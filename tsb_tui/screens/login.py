"""Login — TSB's own auth, a plain password gate (decision 5.34): username + password, a session
cookie held in the client's memory only, nothing written to disk."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import Button, Input, Static
from tsb_contract_client import ApiError

from .._transport import HttpError


class LoginScreen(Screen):
    """The one screen that does not yet have a logged-in client behind it."""

    def compose(self) -> ComposeResult:
        with Vertical(id="login-panel"):
            yield Static("tsb-tui — log in", id="login-title")
            yield Input(placeholder="username", id="username")
            yield Input(placeholder="password", password=True, id="password")
            yield Static("", id="login-error", markup=False)
            yield Button("Log in", id="login-button", variant="primary")

    def on_mount(self) -> None:
        try:
            self.query_one("#username", Input).focus()
        except NoMatches:
            return  # the screen is being torn down or children aren't mounted yet

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._attempt_login()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "login-button":
            self._attempt_login()

    def _attempt_login(self) -> None:
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        error = self.query_one("#login-error", Static)
        if not username or not password:
            error.update("username and password are both required")
            return
        error.update("")
        self.query_one("#login-button", Button).disabled = True
        self.app.call_api(self.app.client.login, username, password,
                          on_done=self._on_login_done, on_error=self._on_login_error)

    def _on_login_done(self, principal: dict) -> None:
        # The password never needs to survive a successful login, and a `push_screen` here would
        # leave this screen (and the password still sitting in its Input) on the stack underneath
        # New Session — `switch_screen` replaces it instead.
        self.query_one("#password", Input).value = ""
        # Read back by screens/new_session.py to tell this principal's own connections apart
        # from ones GET /sessions lists for others (Contract §12: the choice is never guessed).
        self.app.username = principal["username"]
        self.app.apply_saved_look(principal["username"])
        self.app.start_alerts()
        from .terminal import TerminalScreen

        # With no session open the next screen is the workspace with the sidebar open (design §2.5, G1).
        self.app.switch_screen(TerminalScreen())

    def _on_login_error(self, exc: ApiError | HttpError | OSError) -> None:
        self.query_one("#login-button", Button).disabled = False
        message = exc.message if isinstance(exc, ApiError) else str(exc)
        self.query_one("#login-error", Static).update(message)
