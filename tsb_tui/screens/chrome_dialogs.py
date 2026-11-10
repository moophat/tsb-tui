"""The dialogs the Menu's last groups and End session… open (design §2.4: View, Settings and help; session-end-by-tsb):
End session…'s confirmation, the event log, Settings…, All keys and About.

Each is an ordinary modal over the workspace with a `[ Close ]` button that takes one click and never the focus, and
the same three hooks every dialog has (`dialogs`, `sync()`, `status_target()`), so the status row says
`keys → …` while one is open (D14) and the models can redraw it.

What they show is read from the models and the configuration, never typed here: the event log is the front
attachment's own client-side log (D32), Settings… is the configuration with the source of each value, All keys is
the one action table, About is what is installed.
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

import tsb_contract_client
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from ..model import actions as A
from ..model.events import KIND_OUTPUT, Event
from ..widgets.status_row import Hint, KeysTarget
from .prompts import Choice

# The ghostty commit the engine is pinned to (`vt/third_party/libghostty-vt/PIN`, `upstream_commit`). The shared object
# reports only `0.1.0-dev` and no commit, and PIN is not in the installed package, so About carries the commit; a test
# keeps it equal to PIN.
ENGINE_PIN_COMMIT = "34f39002c6e3974b54e6a6d400bd83777c5ea558"

# The keys of the dialogs and views that are not leader actions, for All keys (D40: help is never a prerequisite).
SIDEBAR_KEYS = (
    ("↑ ↓  j k", "move through the rows (the wheel does too)"),
    ("⏎", "open the selected session"),
    ("t", "take back the keyboard of the selected session"),
    ("w", "who's here, for a session open here"),
    ("x", "end the selected session, after a confirmation"),
    ("/", "filter by target, id or login"),
    ("m", "mine│all"),
    ("F", "the full fleet view"),
    ("Esc", "close the sidebar"),
)
FLEET_VIEW_KEYS = (
    ("↑ ↓  j k", "move through the rows (PgUp, PgDn, Home, End, the wheel)"),
    ("⏎", "open the selected session"),
    ("c", "new session"),
    ("t  w  x", "take back, who's here, end the selected session"),
    ("/", "filter"),
    ("m  e", "mine│joinable, show ended"),
    ("space  l  p", "Targets tab: mark a target · save a login for the marked or selected · map a credential"),
    ("n", "Targets tab: create a target (needs target.edit)"),
    ("e", "Targets tab: edit a target (needs target.edit)"),
    ("d", "Targets tab: delete a target (needs target.edit)"),
    ("Tab", "the next tab"),
    ("Ctrl-Q", "quit; every session keeps running"),
    ("Esc", "back to the session"),
)
PAN_KEYS = (("← → ↑ ↓", "move the window over the grid by `view.pan_step`"), ("Esc  ⏎", "stop panning"))
SCROLL_KEYS = (
    ("PgUp  PgDn", "move the view one window up or down the output this client holds"),
    ("↑ ↓", "move it one row (with a selection, they and PgUp PgDn move its loose end, and the view follows)"),
    ("/", ("ask for text to find in the output this client holds, Enter to find the nearest older match from the top "
           "row on view (a literal string; a capital letter makes it case-sensitive), Esc to cancel")),
    ("n  N", "the next older, the next newer match; a match broken across a wrapped row is not found"),
    ("v", ("start selecting whole lines at the top row on view (at the current match when one is shown); again to take "
           "the selection off")),
    ("y  ⏎", ("copy the selected lines to your terminal's clipboard (one OSC 52 write; the selection stays), "
              "`copy.osc52`")),
    ("wheel", "move it `scroll.wheel_lines` rows; a wheel up opens it when the window shows the grid from its top"),
    ("drag", "with the mouse on, select the lines it covers and copy them on release; Shift keeps your terminal's own"),
    ("Esc", "clear the selection, then back to the live screen; any other key is dropped; no typed key, paste, mouse or focus report reaches the session meanwhile"),
)
DIALOG_SCOPES = {A.SCOPE_WHO: "In Who's here", A.SCOPE_SIZE: "In Size",
                  A.SCOPE_THEME: "In Theme picker"}


class _Dialog(ModalScreen[Any]):
    """A modal the status row knows about: `dialogs` for the keys line, `sync()` for a redraw."""

    DEFAULT_CSS = """
    _Dialog { align: center middle; }
    _Dialog .panel { width: 100%; max-width: 100; height: auto; max-height: 90%; border: round $frame;
                     background: $surface; padding: 0 1; }
    _Dialog .panel-title { text-style: bold; }
    _Dialog .scroll { height: auto; max-height: 30; }
    _Dialog .buttons { height: 1; }
    _Dialog .buttons Choice { width: auto; margin-right: 2; }
    _Dialog Choice:hover { background: $surface-3; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "close", "Close")]
    KEYS_NAME = "dialog"

    def __init__(self, terminal: Any) -> None:
        super().__init__()
        self._terminal = terminal

    def on_mount(self) -> None:
        self._terminal.dialogs.append(self)
        self._terminal.refresh_status()

    def on_unmount(self) -> None:
        if self in self._terminal.dialogs:
            self._terminal.dialogs.remove(self)
        self._terminal.refresh_status()

    def sync(self) -> None:
        """Nothing here follows the models unless the dialog says so."""

    def status_target(self) -> KeysTarget:
        return KeysTarget(self.KEYS_NAME, (Hint("Esc", "close"),))

    def action_close(self) -> None:
        self.dismiss(None)


class ConfirmEndScreen(_Dialog):
    """End session… asks once, naming the session: ending it is for everyone and is not undone (session-end-by-tsb
    step 1; mockup F8's wording for the same question). The caller sends `DELETE /sessions/{id}` when it returns True."""

    DEFAULT_CSS = """
    ConfirmEndScreen .panel { max-width: 70; border: round $error; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close"),
        Binding("x", "end_it", "End it", show=False),
        Binding("k", "keep_it", "Keep it", show=False),
    ]
    KEYS_NAME = "confirm"

    def __init__(self, terminal: Any, label: str) -> None:
        super().__init__(terminal)
        self._label = label

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel"):
            yield Static(f"End {self._label}? This ends it for everyone and cannot be undone.", id="end-text",
                         markup=False)
            with Horizontal(classes="buttons"):
                yield Choice("[ End it ]", "end", id="end-confirm")
                yield Choice("[ Keep it ]", "keep", id="end-keep")

    def status_target(self) -> KeysTarget:
        return KeysTarget(self.KEYS_NAME, (Hint("x", "End it"), Hint("k", "Keep it"), Hint("Esc", "close")))

    def on_choice_pressed(self, message: Choice.Pressed) -> None:
        message.stop()
        self.dismiss(message.choice.key == "end")

    def action_close(self) -> None:
        self.dismiss(None)

    def action_end_it(self) -> None:
        self.dismiss(True)

    def action_keep_it(self) -> None:
        self.dismiss(False)


class SearchPrompt(_Dialog):
    """The one-line prompt the scroll view's `/` opens, docked over the status row: it returns the query on Enter and
    None on Esc. It is the screen's only focus while open, so what is typed here reaches no session."""

    DEFAULT_CSS = """
    SearchPrompt { background: transparent; }
    SearchPrompt .search-line { width: 100%; height: 1; background: $surface; }
    SearchPrompt .search-mark { width: 1; }
    SearchPrompt Input { width: 1fr; height: 1; border: none; padding: 0; background: $surface; }
    """
    KEYS_NAME = "search"

    def compose(self) -> ComposeResult:
        with Horizontal(classes="search-line"):
            yield Static("/", classes="search-mark", markup=False)
            yield Input(placeholder="search the output this client holds", id="search-query")

    def on_mount(self) -> None:
        # Textual runs each class's own `on_mount` down the MRO, so `_Dialog`'s registration has run already.
        top = self._terminal.app.cfg["status.position"] == "top"
        self.styles.align_vertical = "top" if top else "bottom"
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    def status_target(self) -> KeysTarget:
        return KeysTarget(self.KEYS_NAME, (Hint("⏎", "Search"), Hint("Esc", "Cancel")))


class TextScreen(_Dialog):
    """A titled, scrollable block of lines with a `[ Close ]` button. `lines` is called again on `sync()`, so a
    dialog that follows a model (the event log) stays current."""

    def __init__(self, terminal: Any, title: str, lines: Callable[[], list[str]], *, follow: bool = False) -> None:
        super().__init__(terminal)
        self._title = title
        self._lines = lines
        self._follow = follow

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel"):
            yield Static(self._title, classes="panel-title", markup=False)
            with VerticalScroll(classes="scroll", id="text-scroll"):
                yield Static("", id="text-body", markup=False)
            with Horizontal(classes="buttons"):
                yield Choice("[ Close ]", "close", id="text-close")

    def on_mount(self) -> None:
        # `_Dialog.on_mount` (the registration with the terminal) runs on its own: Textual calls every class's handler.
        self.query_one("#text-scroll", VerticalScroll).focus()
        self.sync()
        if self._follow:
            self.query_one("#text-scroll", VerticalScroll).scroll_end(animate=False)

    def sync(self) -> None:
        lines = self._lines()
        if self._title == "Settings · the knobs in force, and where each came from" and self.app.update_notice:
            lines = [self.app.update_notice, "", *lines]
        body = self.query_one("#text-body", Static)
        body.update("\n".join(lines) if lines else "Nothing yet.")

    def status_target(self) -> KeysTarget:
        return KeysTarget(self.KEYS_NAME, (Hint("↑↓", "scroll"), Hint("Esc", "close")))

    def on_choice_pressed(self, message: Choice.Pressed) -> None:
        message.stop()
        self.dismiss(None)


class EventLogScreen(TextScreen):
    KEYS_NAME = "the event log"


def _value(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 60 else text[:59] + "…"


def event_log_lines(terminal: Any) -> list[str]:
    """The front session's event log, oldest first (design D22, D32): when it happened on this client's clock, what
    kind of line it is and its details. Consecutive output lines are one line (a burst of output is not news)."""
    pump = terminal.pump
    if pump is None:
        return []
    state = pump.state
    now_wall = terminal.app.wall_clock()
    out: list[str] = []
    burst: list[Event] = []

    def clock(event: Event) -> str:
        return datetime.fromtimestamp(now_wall - (state.now() - event.at), tz=UTC).astimezone().strftime("%H:%M:%S")

    def flush() -> None:
        if burst:
            total = sum(int(e.detail.get("bytes", 0)) for e in burst)
            out.append(f"{clock(burst[-1])}  output        {len(burst)} chunks, {total} bytes")
            burst.clear()

    for event in state.log.all():
        if event.kind == KIND_OUTPUT:
            burst.append(event)
            continue
        flush()
        detail = " ".join(f"{key}={_value(value)}" for key, value in event.detail.items())
        out.append(f"{clock(event)}  {event.kind:<13} {detail}".rstrip())
    flush()
    return out


def settings_lines(cfg: Any) -> list[str]:
    """Every knob in force, with the layer its value came from (design D40; D30's order of precedence).
    Phase 11 step 5: after the knobs, list any theme or scheme files that could not be read."""
    from ..theme_files import scheme_catalogue, theme_catalogue
    
    values = cfg.as_dict()
    width = max((len(key) for key in values), default=0)
    lines = [(f"profile {cfg.profile}; names: default, profile, saved for your login, config file, "
              "environment, --set; roles: default, theme, profile, config file, environment, --set"), ""]
    for key in sorted(values):
        value = values[key]
        shown = f'"{value}"' if isinstance(value, str) else str(value).lower() if isinstance(value, bool) else str(value)
        lines.append(f"{key.ljust(width)} = {_value(shown)}   ({cfg.source(key)})")
    
    # Phase 11 step 5: list broken theme and scheme files
    _, bad_themes = theme_catalogue(cfg.user_dir)
    _, bad_schemes = scheme_catalogue(cfg.user_dir)
    if bad_themes or bad_schemes:
        lines.append("")
        lines.append("Theme and scheme files that could not be read")
        for path, message in bad_themes + bad_schemes:
            lines.append(f"  {path}: {message}")
    
    return lines


def keys_lines(cfg: Any) -> list[str]:
    """Every action's key from the one table, then the keys of the sidebar, the full view and Pan."""
    leader = A.leader_label(cfg["keys.leader"])
    keys = A.leader_keys(cfg.leader_overrides())
    lines = [f"{leader} alone opens the Menu; {leader} {leader} sends {leader} to the session.", ""]
    for group in A.GROUPS:
        entries = [a for a in A.ACTIONS if a.scope == A.SCOPE_SESSION and a.group == group]
        if not entries:
            continue
        lines.append(group)
        for action in entries:
            key = f"{leader} {keys[action.id]}" if action.id in keys else leader if action.id == "menu" else "—"
            lines.append(f"  {key:<8} {action.label:<24} {action.description}")
    for scope, name in DIALOG_SCOPES.items():
        entries = [a for a in A.ACTIONS if a.scope == scope and a.key]
        if entries:
            lines.append(f"{name} (while it is open)")
            lines += [f"  {a.key:<8} {a.label:<24} {a.description}" for a in entries]
    for title, entries in (("In the fleet sidebar", SIDEBAR_KEYS), ("In the full fleet view", FLEET_VIEW_KEYS),
                           ("In Pan", PAN_KEYS), ("In the scroll view", SCROLL_KEYS)):
        lines.append(title)
        lines += [f"  {key:<8} {what}" for key, what in entries]
    return lines


def _package_version() -> str:
    try:
        return importlib.metadata.version("tsb-tui")
    except importlib.metadata.PackageNotFoundError:
        return "not installed as a package"


def tui_version() -> str:
    """The installed TUI identity, shared by About, the CLI and drive."""
    return f"tsb-tui {_package_version()}"


def about_lines(app: Any) -> list[str]:
    """What is running: the TUI, the contract client it was built against and the engine it loaded (DT2)."""
    from ..app import library_path

    info = app.engine.build_info
    return [
        tui_version(),
        f"contract client {tsb_contract_client.CONTRACT_VERSION} (tsb_contract_client)",
        (f"engine libghostty-vt {info.version_string}, pinned to ghostty {ENGINE_PIN_COMMIT[:7]} "
         "(the library itself reports no commit)"),
        f"engine library {library_path(app.cfg)}",
        f"configuration profile {app.cfg.profile}",
    ]
