"""The lock screen (phase 12 § F; screen-lock; cred §5.5; Frontend §10.4).

It covers every screen of this TUI after the user's screen-lock time with no input, so no session content shows anywhere and
input reaches nothing but its password field. The sessions stay attached and keep consuming bytes behind it. The login
password resumes it, checked by the core (`POST /auth/screen-unlock`): a wrong one is 403 `screen_unlock_failed` and the lock
stays with the login in place; 429 shows the core's own message; a 401 means the login really ended, and the app goes to its
login screen. The vault passphrase never resumes it, and this password never unlocks the vault.

In v1 this is a guardrail on this screen, not an access control (Frontend §10.2): the copy says what it does and no more.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Input, Static
from tsb_contract_client import ApiError

from .._transport import HttpError
from ..model.targets import as_text


class LockScreen(Screen):
    DEFAULT_CSS = """
    LockScreen { align: center middle; }
    LockScreen #lock-panel { width: 60; max-width: 100%; height: auto; border: round $frame; padding: 1 2; }
    LockScreen .title { text-style: bold; }
    LockScreen .note { color: $text-muted; }
    LockScreen .error { color: $error; }
    """
    # Ctrl-Q would leave the TUI and print which sessions it held open: nothing but the password field takes input here.
    BINDINGS: ClassVar[list[Binding]] = [Binding("ctrl+q", "swallow", show=False)]

    def __init__(self, note: str = "") -> None:
        super().__init__()
        self._note = note

    def compose(self) -> ComposeResult:
        with Vertical(id="lock-panel"):
            yield Static("Screen locked", classes="title", markup=False)
            yield Static("Your sessions are still running. Type your TSB login password to continue.",
                         classes="note", markup=False)
            yield Input(placeholder="login password", password=True, id="lock-password")
            yield Static(as_text(self._note), id="lock-error", classes="error", markup=False)

    def on_mount(self) -> None:
        self.query_one("#lock-password", Input).focus()

    def action_swallow(self) -> None:
        """Nothing."""

    def show(self, message: str) -> None:
        if self.is_attached:
            self.query_one("#lock-error", Static).update(as_text(message))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        box = self.query_one("#lock-password", Input)
        password = box.value
        if not password:
            return
        box.value = ""          # the screen keeps no copy; what is sent is a local of this call
        box.disabled = True
        self.show("")
        generation = self.app._login_gen      # the login that sends this answer; a later one is not its to unlock

        def resumed(_result: object) -> None:
            self._resumed(generation)

        self.app.call_api(self.app.client.screen_unlock, password, on_done=resumed, on_error=self._refused)

    def _resumed(self, generation: int) -> None:
        """The core accepted the password. It counts for this lock screen and the login that sent it, and for nothing newer:
        a success that comes late, after a sign-out, never lifts the lock of the login that followed."""
        if generation == self.app._login_gen:
            self.app.screen_lock.unlock(self)

    def _refused(self, exc: ApiError | HttpError | OSError) -> None:
        if isinstance(exc, ApiError) and exc.status == 401:
            # The login really ended: the lock goes with it, and the app returns to its login screen.
            self.app.notify_api_error(exc)
            return
        if not self.is_attached:
            return
        box = self.query_one("#lock-password", Input)
        box.disabled = False
        box.focus()
        self.show(exc.message if isinstance(exc, ApiError) else str(exc))
