"""The Textual application: wires the contract client to the E0 screen flow (Contract §2/§3 — the
TUI is a northbound consumer like any other, no privileged path). `Client`'s REST calls are
blocking (urllib underneath); every one of them runs through `call_api` below, off the UI thread,
so a slow login or a slow target never freezes the terminal. The attach stream is the one
exception: `Client.async_attach` is already awaitable and runs directly on Textual's own loop
(screens/terminal.py) — never the blocking `attach`.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, NamedTuple, TypeVar

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
from .config import ConfigError
from .model.alerts import Alert, AlertModel, AlertStreamer
from .model.events import Event
from .screen_lock import INPUT_EVENTS, ScreenLock
from .term import AbiError, GhosttyLib, LibraryError, load
from .theme import PALETTE_OUTER, TermPalette, build_theme, role_colours

T = TypeVar("T")


def default_engine_filename(platform: str | None = None) -> str:
    plat = sys.platform if platform is None else platform
    if plat == "win32":
        return "ghostty-vt.dll"
    if plat == "darwin":
        return "libghostty-vt.dylib"
    return "libghostty-vt.so"


def default_library_path(platform: str | None = None) -> Path:
    return Path("lib") / "tsb-tui" / default_engine_filename(platform)


# Where the deploy hook installs the engine next to the venv (DT2), when `term.library` names none.
DEFAULT_LIBRARY = default_library_path()

# The fixed warning text a session record carries when recording is active (session-record
# schema.json): shown before a surface attaches or sends input (open_decisions §5, LOCKED).
NO_RESOLVING_CREDENTIAL = "no_resolving_credential"


class SignedOut(NamedTuple):
    """What a sign-out did: the sessions it detached (in the order they were open) and whether the broker confirmed
    the logout (`signed_out`) or did not (`unconfirmed`: this client holds no login any more, the broker's may stand
    until it expires)."""

    detached: list[str]
    broker: str


class EngineError(Exception):
    """The engine (libghostty-vt) cannot be found or loaded. The message names the knob and the path."""


def library_path(cfg: config_mod.Config) -> Path:
    """The engine's path: the `term.library` knob, else `<sys.prefix>/lib/tsb-tui/libghostty-vt.so` (DT2)."""
    named = cfg["term.library"]
    return Path(named) if named else Path(sys.prefix) / default_library_path()


def load_engine(cfg: config_mod.Config) -> GhosttyLib:
    """Load the engine, or raise `EngineError` naming the knob and the path that failed. A missing
    engine is a broken install, not something to render around: there is no second renderer."""
    path = library_path(cfg)
    try:
        return load(path)
    except (LibraryError, AbiError) as exc:
        default_file = default_engine_filename()
        default_install = Path(sys.prefix) / default_library_path()
        raise EngineError(
            f"cannot load the terminal engine at {path} ({exc}); set the `term.library` knob "
            f"(--set term.library=PATH, or TSB_TUI_TERM_LIBRARY) to a {default_file}, or install "
            f"it at {default_install}") from exc


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
            help_text = f"[{role_colours(self.app.cfg)['text_muted']}]{markup_escape(help_text)}[/]"
        screen = self.screen

        async def run() -> None:
            await screen.run_action_id(action.id)

        return run, help_text

    def _label(self, label: Content, why: str | None) -> Content:
        """The action's name, muted when it cannot run now (the match highlight, if any, stays on top)."""
        return label if why is None else label.stylize_before(role_colours(self.app.cfg)["text_muted"])

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
        border: round $frame;
        padding: 1 2;
    }
    #login-fields, #login-row {
        height: auto;
    }
    #new-session-panel {
        padding: 0 2;
    }
    #login-row Input {
        width: 1fr;
    }
    /* The picker takes its rows only once the core has asked for a choice, so the login fields fit the dialog on a
       24-row window with Create in reach (login-at-connect phase A). */
    #connection-picker:disabled {
        display: none;
    }
    """

    def __init__(self, broker_url: str, ca_file: str | None = None, target: str | None = None,
                 *, cfg: config_mod.Config | None = None) -> None:
        # Fail before any UI exists: a config that cannot start (an unknown key, a bad type) or an
        # engine that cannot load must exit naming itself, not paint a half-working screen. Raises
        # `ConfigError`, `EngineError` or `ThemeError`.
        resolved = cfg if cfg is not None else config_mod.load()
        engine = load_engine(resolved)
        palette = TermPalette(resolved, resolved.scheme)
        theme = build_theme(resolved, resolved.chrome)
        # TT30: Textual reads the CSS variables inside `App.__init__` and refuses at start-up a
        # stylesheet naming a variable the active theme lacks, so the role variables are ready as an
        # attribute before `super().__init__()`; the TSB theme only becomes active in `on_mount`.
        self._theme_variables = dict(theme.variables)
        # `outer` hands colour indexes to the operator's own terminal, so Textual must not convert
        # them to colours of its own on the way out.
        super().__init__(ansi_color=resolved["theme.term.palette"] == PALETTE_OUTER)
        self.cfg = resolved
        self.engine = engine
        self.palette = palette
        # TT29: one flag says this app is driven by `tsb-tui drive`; the action table reads it.
        self.under_drive = False
        self.register_theme(theme)
        self._theme_name = theme.name
        # The last theme pushed into Textual: `_push_look` compares the rebuilt theme to it, so a same-named
        # theme whose content changed re-applies its CSS variables, and one that did not (a scheme-only move)
        # does not. It starts unset; the theme registered above is pushed by `on_mount`'s `self.theme` set.
        self._pushed_theme = None
        # `broker.timeout_s`: a create can take far longer than the client's 10 s default, so every
        # REST call and the attach's open wait on the knob.
        self._broker_url, self._ca_file, self._timeout = broker_url, ca_file, resolved["broker.timeout_s"]
        self.client = self._new_client()
        # The outer window's last size: only a change to it is a "window change" (D18).
        self._window_size: Size | None = None
        self.initial_target = target
        # Set once login succeeds (screens/login.py); read back by screens/new_session.py to
        # tell this principal's own connections apart from ones GET /sessions lists for others.
        self.username: str | None = None
        self.permissions: frozenset[str] = frozenset()
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
        # Phase 10: the per-login state file, and whether it was unreadable at login (Save refuses then,
        # TT17). `apply_saved_look` sets both after each login.
        self._saved_theme_path: Path | None = None
        self._saved_theme_unreadable = False
        # Sign-out is single-flight (`_sign_out_task`), and every completed login bumps
        # `_login_gen` so a sign-out that is still running never discards a login made after it began.
        self._sign_out_task: asyncio.Task[SignedOut] | None = None
        self._login_gen = 0
        # The screen lock and the activity signal (phase 12 § F, § G): this instance's own, from the core's settings.
        # `idle_clock` and `screen_lock_poll` are what a test replaces to move time without waiting for it.
        self.idle_clock: Callable[[], float] = time.monotonic
        self.screen_lock_poll = 0.5
        self.screen_lock = ScreenLock(self)

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """TT30: Textual's own defaults, updated with the role variables of the resolved config, so
        a stylesheet naming `$frame` parses before the TSB theme is active in `on_mount`."""
        return {**super().get_theme_variable_defaults(), **self._theme_variables}

    async def on_event(self, event: events.Event) -> None:
        """Every input from the user (a key, a paste, a click, a wheel turn) is the idle time starting again, and the
        activity signal's trigger (`ScreenLock.note_input`)."""
        if isinstance(event, INPUT_EVENTS):
            self.screen_lock.note_input()
        await super().on_event(event)

    def push_screen(self, screen, callback=None, wait_for_dismiss=False, *, mode=None):  # type: ignore[no-untyped-def,override]
        """While the lock screen is up a screen pushed by anything running in the background waits for the unlock: it
        could otherwise cover the lock screen with session content."""
        from .screens.lock import LockScreen

        if self.screen_lock.locked and not isinstance(screen, LockScreen):
            self.screen_lock.hold(screen, callback)
            return None
        return super().push_screen(screen, callback, wait_for_dismiss, mode=mode)

    def pop_screen(self):  # type: ignore[no-untyped-def]
        """Nothing under the lock screen takes it away: only the unlock does."""
        if self.screen_lock.locked:
            from textual.await_complete import AwaitComplete

            return AwaitComplete.nothing()
        return super().pop_screen()

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

    # -- switching the look at run time (B4) --

    def apply_look(self, theme_id: str | None = None, scheme_slug: str | None = None, *,
                   source: str = "this run") -> None:
        """Switch the chrome theme and/or the terminal scheme during this run (B4). Change the config, rebuild
        the Textual theme and the palette, and push both into every terminal screen. Before login there is no
        workspace, so only the theme and palette change."""
        self.cfg.select_look(theme_id, scheme_slug, source=source)
        self._push_look()

    def restore_look(self, saved: dict[str, Any]) -> None:
        """Put back a look `Config.look()` captured (the picker's Esc), sources included, and push it the same way."""
        self.cfg.restore_look(saved)
        self._push_look()

    def apply_saved_look(self, login: str) -> None:
        """Apply this TSB login's saved choice from the state file (TT16, TT17). Called in `_on_login_done`
        right after `app.username` is set and before the workspace is built. Nothing happens under drive (TT19).
        A state file that does not parse is toasted naming it and the field; Save refuses until it is fixed
        or removed. No entry for this login: `cfg.unsave_names()` is called (the names an earlier login set go
        back). An entry: each name is applied only when its current source is default, profile, or a previous
        `saved for ...`; a `ConfigError` is toasted naming the state file and the field, and the entry is kept."""
        if self.under_drive:
            return
        from . import saved_theme

        path = saved_theme.state_path()
        self._saved_theme_path = path
        try:
            entries = saved_theme.read(path)
        except saved_theme.SavedThemeError as exc:
            self._saved_theme_unreadable = True
            self.toast(str(exc), title="cannot read saved theme", severity="warning")
            return
        self._saved_theme_unreadable = False
        entry = entries.get(login)
        if entry is None:
            # unsave_names applies each fallback through select_look, which can raise ConfigError if a
            # fallback theme/scheme file is missing or invalid. It restores the captured look and re-raises,
            # so a failure leaves the look in force unchanged; toast it and keep that look.
            try:
                self.cfg.unsave_names()
            except ConfigError as exc:
                self.toast(f"{path}: could not restore the default look ({exc})", title="saved theme", severity="warning")
                return
            self._push_look()
            return
        source = f"saved for {login}"
        for field, key in (("theme", "theme.name"), ("scheme", "theme.term.scheme")):
            src = self.cfg.source(key)
            if src == "default" or src.startswith(("profile ", "saved for ")):
                try:
                    self.apply_look(entry[field] if field == "theme" else None,
                                    entry[field] if field == "scheme" else None,
                                    source=source)
                except ConfigError:
                    self.toast(f"{path}: [{login!r}].{field} — could not apply", title="saved theme", severity="warning")

    def _push_look(self) -> None:
        """Rebuild the theme and palette from the resolved config and hand them to every terminal screen."""
        cfg = self.cfg
        theme = build_theme(cfg, cfg.chrome)
        self.register_theme(theme)
        name_changed = theme.name != self._theme_name
        if name_changed:
            # A scheme-only switch leaves the chrome alone; setting the same name does nothing (Textual re-reads
            # every $ variable only when the name changes, so dialogs and app.CSS follow a real switch).
            self.theme = theme.name
            self._theme_name = theme.name
        # TT30: refresh the CSS variable defaults so a stylesheet naming a role variable still parses.
        self._theme_variables = dict(theme.variables)
        if not name_changed and theme != self._pushed_theme:
            # A user file edited while the picker is open, then Used, rebuilds the same-named theme: Textual
            # re-reads every $ variable only on a name change, so the rebuilt colours never reach the dialogs.
            # Re-apply them through Textual's own public refresh (the call `_watch_theme` makes), which
            # re-reads `get_css_variables()` from the freshly registered theme and the new defaults. A
            # scheme-only move leaves the chrome theme identical, so this does not run per arrow key.
            self.refresh_css(animate=False)
        self._pushed_theme = theme
        self.palette = TermPalette(cfg, cfg.scheme)
        from .screens.terminal import TerminalScreen

        for screen in self.screen_stack:
            if isinstance(screen, TerminalScreen):
                screen.apply_look(self.palette)

    # -- toasts (D22) --

    def toast(self, text: str, *, title: str = "", severity: str = "information") -> None:
        """A state change's toast: at most `toasts.max` at once (the oldest goes first), each for
        `toasts.seconds`. The change itself is also an event-log line and, while it lasts, in the
        status row."""
        # Textual's `notify` only posts a message, so two toasts in a row could not see each other and the
        # cap would never bite: add to the collection here, as its own handler does.
        if self.screen_lock.locked:
            return      # nothing is drawn over the lock screen: a toast could carry a session's text
        live = [n for n in self._notifications if not n.has_expired]
        for old in live[: max(len(live) - self.cfg["toasts.max"] + 1, 0)]:
            self._unnotify(old, refresh=False)
        self._notifications.add(Notification(
            text, title, severity, float(self.cfg["toasts.seconds"]), markup=False))
        self._refresh_notifications()

    def notify(self, message: str, **kwargs: Any) -> None:
        if self.screen_lock.locked:
            return      # as `toast`: nothing is drawn over the lock screen
        super().notify(message, **kwargs)

    # -- the operator's clipboard --

    def copy_out(self, text: str) -> None:
        """Write `text` to the outer terminal as one OSC 52 clipboard write: the only place this TUI writes to the
        operator's clipboard. Only the operator's own key or drag calls it; nothing a session sends does, and the
        engine's clipboard effects stay uninstalled. The terminal does not say whether it took the write."""
        self.copy_to_clipboard(text)

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

    def _new_client(self) -> Client:
        return Client(self._broker_url, ca_file=self._ca_file, timeout=self._timeout)

    def call_api(self, func: Callable[..., T], *args: Any, on_done: Callable[[T], None],
                 on_error: Callable[[ApiError | HttpError | OSError], None],
                 on_stale_error: Callable[[ApiError | HttpError | OSError], None] | None = None) -> None:
        """Run `func(*args)` (a blocking `Client` method) off the UI thread. Exactly one of
        `on_done`/`on_error` runs afterward, back on the UI thread (`call_from_thread`), so a
        screen's own callbacks never touch Textual state from the worker thread.

        The request is bound to the client this app holds *now*, at submission (every caller passes a method of
        `self.client`, taken on this thread in the same call). A sign-out replaces `self.client`, so a request
        submitted before it runs on the old client, whose cookie jar logout cleared, and never carries the next
        login's cookie. Its 401 is the old login's answer and nobody is waiting for it: it is dropped here, with no
        error toast and no trip back to Login over the new login's screen. A caller whose callback must run even
        then — a future that settles a sign-out or a drive `end` — passes `on_stale_error`, which runs in the
        dropped 401's place instead of `on_error` (it never toasts or navigates)."""
        client = self.client

        def failed(exc: ApiError | HttpError | OSError) -> None:
            if client is not self.client and isinstance(exc, ApiError) and exc.status == 401:
                if on_stale_error is not None:
                    on_stale_error(exc)
                return
            on_error(exc)

        self._run_blocking(func, args, on_done, failed)

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

    @property
    def signing_out(self) -> bool:
        """Whether a sign-out is running now: the single-flight task exists and has not finished. Login reads this
        to refuse a submit until the sign-out completes."""
        return self._sign_out_task is not None and not self._sign_out_task.done()

    async def sign_out(self) -> SignedOut:
        """Sign out, single-flight: a second Menu Sign out (or drive `logout`) while one runs shares the first
        and settles with it, so repeated sign-outs never leave a task waiting on a logout that never reaches it.
        Callers wait through `shield`: the Menu's caller runs under the workspace screen, which an unrelated 401
        tears down mid-sign-out, and cancelling a caller (the first or a joiner) must not cancel the sign-out the
        others share. Once begun it runs to its end."""
        if self._sign_out_task is None or self._sign_out_task.done():
            self._sign_out_task = asyncio.create_task(self._sign_out())
        return await asyncio.shield(self._sign_out_task)

    async def _sign_out(self) -> SignedOut:
        """The sign-out worker: detach every session open here, stop the alert feed, install a fresh client and show
        Login, then tell the broker on the old client. The detaches come first and are complete before `logout()`,
        so the core sees ordinary detaches whatever contract open item 5 (`login.md`: what logout does to a
        principal's attached clients) becomes. A 401 from `logout()` means the login was already gone and is no
        error. Any other failure still ends at Login, with a warning: `Client.logout` clears the
        cookie jar of the old client whatever the broker answered, so no client here holds the old login."""
        detached: list[str] = []
        # Every session is left before the broker is told of the logout, the scroll view closed first.
        screen = self.workspace()
        if screen is not None:
            screen.close_scroll_view()
            detached = await screen.detach_all()
        self.stop_alerts()
        generation = self._login_gen
        # Login first, on a fresh client: `Client.logout` clears the jar of the instance it was sent on,
        # so the logout goes on `old` and a login made while it is held lands on the new instance, whose jar it can
        # never empty. Login's submit stays refused until this sign-out ends. Requests still on `old` are fenced by
        # `call_api` (a 401 on a replaced client is dropped), as before.
        old = self.client
        self.client = self._new_client()
        self._to_login()
        answered: asyncio.Future[ApiError | HttpError | OSError | None] = asyncio.get_running_loop().create_future()

        def settle(failure: ApiError | HttpError | OSError | None) -> None:
            if not answered.done():  # this sign-out may have been cancelled while the broker was answering
                answered.set_result(failure)

        # `on_stale_error` too: a 401 answered after another sign-out already replaced the client still settles this
        # future — a dropped 401 must never leave a sign-out waiting forever.
        self.call_api(old.logout, on_done=lambda _: settle(None), on_error=settle, on_stale_error=settle)
        failure = await answered
        confirmed = failure is None or (isinstance(failure, ApiError) and failure.status == 401)
        result = SignedOut(detached, "signed_out" if confirmed else "unconfirmed")
        if self._login_gen != generation:
            # A login completed after this sign-out began (the Login submit gate is the first line of defence; this
            # generation check backs it). It is on the new client and its screen: nothing here touches either, and
            # no toast speaks over it.
            return result
        if confirmed:
            self.toast("Signed out. Your sessions keep running.", title="signed out")
            return result
        self.notify(f"Signed out here. The broker did not confirm ({failure}); your login there ends when it "
                    f"expires.", title="signed out", severity="warning", markup=False)
        return result

    def _to_login(self) -> None:
        # Imported here, not at module scope: see on_mount's own comment above.
        from .screens.login import LoginScreen

        # The first 401 after a restart already showed Login. Every other request in flight with the old
        # cookie can 401 too (the poller's GET /sessions, a reattach), so a later one must not replace the
        # screen that is already up: the form the operator — or drive — is filling in would be unmounted.
        if isinstance(self.screen, LoginScreen):
            return
        self.stop_alerts()
        self.permissions = frozenset()
        self.screen_lock.clear()      # the login ended: no lock, no settings, nothing held
        # Every screen above the base is removed first: `switch_screen` replaces only the top of the stack, so
        # a dialog on top (the end dialog) leaves the workspace mounted under the login page, and a mounted
        # TerminalScreen is what this app and drive count as logged in (`workspace()` scans the whole stack).
        # Popping to the base is what a fresh start after a logout looks like.
        while len(self.get_screen_stack()) > 1:
            self.pop_screen()
        self.push_screen(LoginScreen())

