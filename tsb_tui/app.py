"""The Textual application: wires the contract client to the E0 screen flow (Contract §2/§3 — the
TUI is a northbound consumer like any other, no privileged path). `Client`'s REST calls are
blocking (urllib underneath); every one of them runs through `call_api` below, off the UI thread,
so a slow login or a slow target never freezes the terminal. The attach stream is the one
exception: `Client.async_attach` is already awaitable and runs directly on Textual's own loop
(screens/terminal.py) — never the blocking `attach`.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from rich.text import Text
from textual import events, work
from textual.app import App
from textual.binding import Binding
from textual.command import (
    CommandPalette,
    DiscoveryHit,
    Hit,
    Hits,
    Provider,
    SearchIcon,
)
from textual.content import Content
from textual.geometry import Size
from textual.markup import escape as markup_escape
from textual.notifications import Notification
from tsb_contract_client import ApiError, Client

from . import config as config_mod
from ._transport import HttpError
from .model.alerts import Alert, AlertModel, AlertStreamer
from .model.events import Event
from .term import AbiError, GhosttyLib, LibraryError, load
from .theme import PALETTE_OUTER, TermPalette, build_theme

T = TypeVar("T")

# Where the deploy hook installs the engine next to the venv (DT2), when `term.library` names none.
DEFAULT_LIBRARY = Path("lib") / "tsb-tui" / "libghostty-vt.so"

# The fixed warning text a session record carries when recording is active (session-record
# schema.json): shown before a surface attaches or sends input (open_decisions §5, LOCKED).
NO_RESOLVING_CREDENTIAL = "no_resolving_credential"


class EngineError(Exception):
    """The engine (libghostty-vt) cannot be found or loaded. The message names the knob and the path."""


def library_path(cfg: config_mod.Config) -> Path:
    """The engine's path: the `term.library` knob, else `<sys.prefix>/lib/tsb-tui/libghostty-vt.so` (DT2)."""
    named = cfg["term.library"]
    return Path(named) if named else Path(sys.prefix) / DEFAULT_LIBRARY


def load_engine(cfg: config_mod.Config) -> GhosttyLib:
    """Load the engine, or raise `EngineError` naming the knob and the path that failed. A missing
    engine is a broken install, not something to render around: there is no second renderer."""
    path = library_path(cfg)
    try:
        return load(path)
    except (LibraryError, AbiError) as exc:
        raise EngineError(
            f"cannot load the terminal engine at {path} ({exc}); set the `term.library` knob "
            f"(--set term.library=PATH, or TSB_TUI_TERM_LIBRARY) to a libghostty-vt.so, or install "
            f"it at {Path(sys.prefix) / DEFAULT_LIBRARY}") from exc


class MenuProvider(Provider):
    """The Menu (design F3, D10): every action of the one table with its description and key, grouped,
    searchable, filled from the screen that opened it. An action that is not available now is listed
    too and says why, so the Menu is the complete list and never a guess at what exists."""

    def _entries(self) -> list[tuple[Any, str, str | None]]:
        return self.screen.menu_entries()

    def _entry(self, action: Any, keys: str, why: str | None) -> tuple[Any, str]:
        """The callback that runs `action` and the help line under its name. An action with no key of its own
        shows none (`keys` is empty): the leader alone is the Menu itself. One that is not available now is drawn in
        the theme's muted colour, its reason kept."""
        parts = [action.group, action.description]
        if keys:
            parts.append(keys)
        if why is not None:
            parts.append(f"not available: {why}")
        help_text = " · ".join(parts)
        if why is not None:
            help_text = f"[{self.app.cfg['theme.text_muted']}]{markup_escape(help_text)}[/]"
        screen = self.screen

        async def run() -> None:
            await screen.run_action_id(action.id)

        return run, help_text

    def _label(self, label: Content, why: str | None) -> Content:
        """The action's name, muted when it cannot run now (the match highlight, if any, stays on top)."""
        return label if why is None else label.stylize_before(self.app.cfg["theme.text_muted"])

    async def discover(self) -> Hits:
        for action, keys, why in self._entries():
            run, help_text = self._entry(action, keys, why)
            yield DiscoveryHit(self._label(Content(action.label), why), run, text=action.label, help=help_text)

    async def search(self, query: str) -> Hits:
        if not self.app.cfg["menu.search"]:
            async for hit in self.discover():  # search off: the list does not narrow
                yield hit
            return
        matcher = self.matcher(query)
        for action, keys, why in self._entries():
            score = matcher.match(f"{action.label} {action.description}")
            if score > 0:
                run, help_text = self._entry(action, keys, why)
                yield Hit(score, self._label(matcher.highlight(action.label), why), run, text=action.label,
                          help=help_text)


class MenuPalette(CommandPalette):
    """The Menu's palette. Its search icon is the ASCII `/`, which every font has (Textual's own is an emoji that a
    terminal font without it draws as a hole)."""

    def on_mount(self) -> None:
        # The palette's children are not in the DOM yet when it is mounted: set the icon once they are.
        self.call_after_refresh(self._ascii_icon)

    def _ascii_icon(self) -> None:
        icons = list(self.query(SearchIcon))
        if icons:
            icons[0].icon = "/"
        else:
            self.call_after_refresh(self._ascii_icon)


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

    def __init__(self, broker_url: str, ca_file: str | None = None, target: str | None = None,
                 *, cfg: config_mod.Config | None = None) -> None:
        # Fail before any UI exists: a config that cannot start (an unknown key, a bad type) or an
        # engine that cannot load must exit naming itself, not paint a half-working screen. Raises
        # `ConfigError`, `EngineError` or `ThemeError`.
        resolved = cfg if cfg is not None else config_mod.load()
        engine = load_engine(resolved)
        palette = TermPalette(resolved)
        theme = build_theme(resolved)
        # `outer` hands colour indexes to the operator's own terminal, so Textual must not convert
        # them to colours of its own on the way out.
        super().__init__(ansi_color=resolved["theme.term.palette"] == PALETTE_OUTER)
        self.cfg = resolved
        self.engine = engine
        self.palette = palette
        self.register_theme(theme)
        self._theme_name = theme.name
        self.client = Client(broker_url, ca_file=ca_file)
        # The outer window's last size: only a change to it is a "window change" (D18).
        self._window_size: Size | None = None
        self.initial_target = target
        # Set once login succeeds (screens/login.py); read back by screens/new_session.py to
        # tell this principal's own connections apart from ones GET /sessions lists for others.
        self.username: str | None = None
        # The client's wall clock, read by the countdown (a test sets its own).
        self.wall_clock: Callable[[], float] = time.time
        # Sessions whose recording gate was shown in this run (D24): a later attach shows the text in
        # the status row instead, unless `gates.recording` says every attach.
        self.gated_sessions: set[str] = set()
        # The operator alert stream, one per login (DT14). The model is the app's, so a popup outlives
        # the screen that showed it.
        self.alerts = AlertModel.from_config(resolved)
        self._alert_streamer: AlertStreamer | None = None
        self._alert_listeners: list[Callable[[], None]] = []
        # Who is told when a session's model changes: each line of an attachment's event log, a poll changing a
        # record, a session leaving the screen. `tsb-tui drive` is the one listener; nothing here reads the screen.
        self.taps: list[Callable[[str, str, Any], None]] = []

    def tapped(self, session_id: str, kind: str, detail: Any = None) -> None:
        """Tell every tap that something changed for `session_id`. Runs on the UI thread, in the middle of
        whatever changed it, so a tap must only note the change and look at the models afterwards."""
        for tap in list(self.taps):
            tap(session_id, kind, detail)

    def log_added(self, session_id: str, event: Event) -> None:
        """The `on_add` of each attachment's event log."""
        self.tapped(session_id, event.kind, event)

    def run(self, **kwargs: Any) -> Any:
        """Run the app. `mouse.enabled = false` is the terminal's own end of turning the mouse off: the TUI then
        never asks the operator's terminal to report it, so the terminal keeps its text selection and nothing
        Textual draws sees a click (D13)."""
        kwargs.setdefault("mouse", bool(self.cfg["mouse.enabled"]))
        return super().run(**kwargs)

    def on_mount(self) -> None:
        # `motion.level`: nothing animates on the canvas or the rail (D37); "none" also stops Textual's own.
        self.animation_level = self.cfg["motion.level"]
        self.theme = self._theme_name
        # Imported here, not at module scope: screens/login.py in turn imports this module for
        # `TsbTuiApp`'s type only in a few places, and keeping the wiring in on_mount avoids a
        # circular import between app.py and screens/*.py at module load time.
        from .screens.login import LoginScreen

        self.push_screen(LoginScreen())

    # -- toasts (D22) --

    def toast(self, text: str, *, title: str = "", severity: str = "information") -> None:
        """A state change's toast: at most `toasts.max` at once (the oldest goes first), each for
        `toasts.seconds`. The change itself is also an event-log line and, while it lasts, in the
        status row."""
        # Textual's `notify` only posts a message, so two toasts in a row could not see each other and the
        # cap would never bite: add to the collection here, as its own handler does.
        live = [n for n in self._notifications if not n.has_expired]
        for old in live[: max(len(live) - self.cfg["toasts.max"] + 1, 0)]:
            self._unnotify(old, refresh=False)
        self._notifications.add(Notification(
            text, title, severity, float(self.cfg["toasts.seconds"]), markup=False))
        self._refresh_notifications()

    # -- operator alerts (DT14) --

    def start_alerts(self) -> None:
        """Open the alert stream for this login. A 403 (not a `broker.health` holder) stops it for good."""
        if self._alert_streamer is not None:
            return
        self.alerts = AlertModel.from_config(self.cfg)
        self._alert_streamer = AlertStreamer.from_config(
            self.client, self.alerts, self.cfg,
            on_event=lambda alert: self._alerts_from_thread(),
            on_change=self._alerts_from_thread)
        self._alert_streamer.start()

    def stop_alerts(self) -> None:
        """End the stream: at logout and at quit."""
        streamer, self._alert_streamer = self._alert_streamer, None
        if streamer is not None:
            streamer.stop()

    def watch_alerts(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call `listener` on the UI thread whenever the active or dismissed set changes; returns the
        function that stops listening."""
        self._alert_listeners.append(listener)
        return lambda: self._alert_listeners.remove(listener) if listener in self._alert_listeners else None

    def alerts_changed(self) -> None:
        if self._exit:
            return  # leaving: the screens are coming down, and nothing is left to draw an alert
        for listener in list(self._alert_listeners):
            listener()

    def _alerts_from_thread(self) -> None:
        try:
            self.call_from_thread(self.alerts_changed)
        except RuntimeError:
            pass  # the app is going away

    def dismiss_alert(self, alert: Alert) -> None:
        self.alerts.dismiss(alert.alert_id)
        self.alerts_changed()

    def on_unmount(self) -> None:
        self.stop_alerts()

    async def action_quit(self) -> None:
        """Leave the TUI (D38): `Ctrl-Q` in a full view, Menu ▸ Quit and `^\\ :q`. Nothing is sent to any session and no
        session is ended: they keep running, and the TUI prints which it was holding open."""
        workspace = self.workspace()
        running = workspace.running_sessions() if workspace is not None else []
        if running:
            lines = ["tsb-tui: you left. These sessions keep running:", *(f"  {line}" for line in running)]
        else:
            lines = ["tsb-tui: you left. No session was open here; every session keeps running."]
        self.exit(message=Text("\n".join(lines)))

    def open_menu(self) -> None:
        """Open the Menu. Only the leader, `≡ Menu` and a click on it reach this: Ctrl-P stays the session's."""
        if not CommandPalette.is_open(self):
            self.push_screen(MenuPalette(providers=[MenuProvider], placeholder="Find an action",
                                         id="--command-palette"))

    def workspace(self):
        """The terminal screen under whatever is open over it: where every session lives, with the rail and the
        sidebar. None before login."""
        from .screens.terminal import TerminalScreen

        return next((screen for screen in reversed(self.screen_stack) if isinstance(screen, TerminalScreen)),
                    None)

    def on_command_palette_opened(self, event: CommandPalette.Opened) -> None:
        self._menu_changed(True)

    def on_command_palette_closed(self, event: CommandPalette.Closed) -> None:
        self._menu_changed(False)

    def _menu_changed(self, is_open: bool) -> None:
        from .screens.terminal import TerminalScreen

        for screen in self.screen_stack:
            if isinstance(screen, TerminalScreen):
                screen.set_menu_open(is_open)

    def on_resize(self, event: events.Resize) -> None:
        """The outer window changed (the App's own resize, never a widget's): tell each terminal
        screen, which decides whether to ask its session for a size (D18)."""
        previous, self._window_size = self._window_size, event.size
        if previous is None or event.size == previous:
            return  # the first size is the baseline the operator started with, not a change
        from .screens.terminal import TerminalScreen

        for screen in self.screen_stack:
            if isinstance(screen, TerminalScreen):
                screen.window_changed(event.size)

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

        self.stop_alerts()
        # Every screen above the base is removed first: `switch_screen` replaces only the top of the stack, so
        # a dialog on top (the end dialog) leaves the workspace mounted under the login page, and a mounted
        # TerminalScreen is what this app and drive count as logged in (`workspace()` scans the whole stack).
        # Popping to the base is what a fresh start after a logout looks like.
        while len(self.get_screen_stack()) > 1:
            self.pop_screen()
        self.push_screen(LoginScreen())

