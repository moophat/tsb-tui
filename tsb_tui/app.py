"""The Textual application: wires the contract client to the E0 screen flow (Contract §2/§3 — the
TUI is a northbound consumer like any other, no privileged path). `Client`'s REST calls are
blocking (urllib underneath); every one of them runs through `call_api` below, off the UI thread,
so a slow login or a slow target never freezes the terminal. The attach stream is the one
exception: `Client.async_attach` is already awaitable and runs directly on Textual's own loop
(screens/terminal.py) — never the blocking `attach`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar, TypeVar

from textual import work
from textual.app import App
from textual.binding import Binding
from tsb_contract_client import ApiError, Client

from ._transport import HttpError

T = TypeVar("T")

# The fixed warning text a session record carries when recording is active (session-record
# schema.json): shown before a surface attaches or sends input (open_decisions §5, LOCKED).
NO_RESOLVING_CREDENTIAL = "no_resolving_credential"


class TsbTuiApp(App):
    """One login, one contract client, a stack of screens. Screens read `self.app.client`."""

    TITLE = "tsb-tui"
    # The command palette would otherwise reserve ctrl+p app-wide, ahead of every screen's own
    # key handling — the terminal screen needs every key, ctrl+p included, to reach the target.
    ENABLE_COMMAND_PALETTE = False
    # Replaces the base App's own ctrl+q binding, which is a *priority* binding (checked before
    # any screen sees the key at all, no matter what that screen's own `on_key` does with it).
    # Non-priority, quit still fires from every screen that does not itself consume ctrl+q — the
    # terminal screen now does, for every key it knows a byte for, so ctrl+q reaches the target
    # there instead of quitting from underneath the operator.
    BINDINGS: ClassVar[list[Binding]] = [Binding("ctrl+q", "quit", "Quit", show=False)]
    CSS = """
    Screen {
        align: center middle;
    }
    #login-panel, #new-session-panel, #attach-confirm-panel {
        width: 60;
        border: round $primary;
        padding: 1 2;
    }
    """

    def __init__(self, broker_url: str, ca_file: str | None = None, target: str | None = None) -> None:
        super().__init__()
        self.client = Client(broker_url, ca_file=ca_file)
        self.initial_target = target
        # Set once login succeeds (screens/login.py); read back by screens/new_session.py to
        # tell this principal's own connections apart from ones GET /sessions lists for others.
        self.username: str | None = None

    def on_mount(self) -> None:
        # Imported here, not at module scope: screens/login.py in turn imports this module for
        # `TsbTuiApp`'s type only in a few places, and keeping the wiring in on_mount avoids a
        # circular import between app.py and screens/*.py at module load time.
        from .screens.login import LoginScreen

        self.push_screen(LoginScreen())

    # -- the one place a blocking Client call crosses into a worker thread --

    def call_api(self, func: Callable[..., T], *args: Any, on_done: Callable[[T], None],
                on_error: Callable[[ApiError | HttpError | OSError], None]) -> None:
        """Run `func(*args)` (a blocking `Client` method) off the UI thread. Exactly one of
        `on_done`/`on_error` runs afterward, back on the UI thread (`call_from_thread`), so a
        screen's own callbacks never touch Textual state from the worker thread."""
        self._run_blocking(func, args, on_done, on_error)

    @work(thread=True, exclusive=False)
    def _run_blocking(self, func: Callable[..., T], args: tuple[Any, ...], on_done: Callable[[T], None],
                      on_error: Callable[[ApiError | HttpError | OSError], None]) -> None:
        try:
            result = func(*args)
        except (ApiError, HttpError, OSError) as exc:
            self.call_from_thread(on_error, exc)
        else:
            self.call_from_thread(on_done, result)

    def notify_api_error(self, exc: ApiError | HttpError | OSError) -> None:
        """The one place an `ApiError` becomes a toast. `no_resolving_credential` is a legitimate
        state (Storage §10.1a) — worded as one, never in the error tone the rest get. A 401 means
        the login this client held no longer works, wherever it surfaced — this is the one place
        that sends the operator back to Login rather than leaving them stuck on a broken screen."""
        if isinstance(exc, ApiError) and exc.code == NO_RESOLVING_CREDENTIAL:
            self.notify("This target is visible, but no credential resolves for it.",
                       title="No credential", severity="warning", markup=False)
            return
        self.notify(str(exc), title="Request failed", severity="error", markup=False)
        if isinstance(exc, ApiError) and exc.status == 401:
            self._to_login()

    def _to_login(self) -> None:
        # Imported here, not at module scope: see on_mount's own comment above.
        from .screens.login import LoginScreen

        self.switch_screen(LoginScreen())
