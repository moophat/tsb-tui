"""Terminal — the workspace: the rail, the fleet sidebar, the canvas of the session in front and the status row,
over any number of attachments, each through its own `AttachmentPump` (the asyncio transport, run on Textual's
own loop: never the blocking `attach`).

Contract §11 makes the client the sole renderer, and the design makes the *session* the owner of the grid's
size (D3): this screen sizes each engine from `attached`, `prefix` and `size` frames, never from the window.
The window decides only how much of the grid the canvas shows (it pans), and, when the operator typed last and
`size.auto_request` is on, one debounced request to the session in front for a size that fits the window (D18,
terminal-size §4). The sidebar opening or closing, and a session coming to the front, take width from the
canvas and ask for nothing (D44). The pump is the only reader of each socket (DT17): every answer to a request
arrives through it like any other frame.

Several sessions (design §2.3, D33): one pump, engine and canvas per session open here. Only the one in front
is painted; the others are parsed and mark unseen output on their rail chip. Each attachment keeps its own
DT4 (b) state, so a background session never answers a query on behalf of a keyboard it does not hold.

The chrome (design §2.4, D43): the rail at the left, one status row below says who types and what can be done
now, each hint a click; Ctrl-\\ is the leader — `^\\` then a letter runs an action, `^\\ 1`-`^\\ 9` switch
session, `^\\` alone opens the Menu, `^\\ ^\\` sends the key itself. Every action is in the one table
(`model/actions.py`) and reaches here as `run_action_id`; dialogs read the models, never the socket.

Input (DT4): everything that leaves for a session leaves through `send_input(source, data)`. *Typed* input
(keys, paste) is the person's: it goes out when this client holds the keyboard or is one of the owner's clients
(the owner's typing takes the keyboard, 5.53) and, while `input.guard` is on, is otherwise suppressed here,
counted and logged (Frontend §9; typing-ownership CR3). *Derived* input (an answer to a query the session
asked, a focus report, a canvas mouse report) is nobody's typing, so it never takes the keyboard and is never
under the guard: it goes out only while this client holds the keyboard with no hand-off of its own in flight,
no stale choice pending and no dump being applied, and is otherwise dropped, never queued for later. Every key
reaches the session (D17), re-encoded for the session's modes by libghostty's own encoder (D34).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from rich.style import Style
from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.geometry import Size
from textual.screen import Screen
from textual.timer import Timer
from tsb_contract_client import ApiError, AttachRefused

from .. import glyphs
from .._transport import ConnectionClosed, HandshakeError, HttpError, ProtocolError
from ..model import actions as A
from ..model.attachment import AttachmentPump, AttachmentState, PumpHooks
from ..model.countdown import format_duration, parse_timestamp
from ..model.events import TAP_RECORD, TAP_SLOT_CLOSED, EventLog
from ..model.fleet import (
    GROUP_NEEDS_YOU,
    MAX_CHIPS,
    WHO_OTHER,
    Fleet,
    FleetPoller,
    FleetScan,
    display_id,
    is_attachable,
    live_record,
    who_types,
)
from ..model.targets import (
    PAGE_LIMIT,
    Query,
    QueryError,
    TargetRow,
    TargetsModel,
    as_text,
    parse_query,
)
from ..term import (
    UnknownKey,
    VTBackend,
    encode_focus,
    encode_key,
    encode_mouse,
    encode_paste,
)
from ..term import ghostty as G
from ..term.search import SearchResult, reanchor, search
from ..term.selection import Selection
from ..theme import TermPalette
from ..widgets.canvas import Canvas, Highlight
from ..widgets.rail import (
    ACT_ALERTS,
    ACT_CHIP,
    ACT_FLEET,
    ACT_MENU,
    ACT_MORE,
    ACT_NEEDS,
    ACT_NEW,
    WHO_WORDS,
    Rail,
    RailModel,
)
from ..widgets.sidebar import (
    CMD_ALERTS,
    CMD_CLOSE,
    CMD_END,
    CMD_FILTER,
    CMD_FULL_VIEW,
    CMD_MODE,
    CMD_NEW,
    CMD_PICK,
    CMD_TAKE_BACK,
    CMD_WHOS_HERE,
    RowTime,
    Sidebar,
    SidebarEntry,
    SidebarModel,
)
from ..widgets.stale_banner import StaleBanner
from ..widgets.status_row import (
    REFUSAL_TEXT,
    EndedStatus,
    Hint,
    KeysTarget,
    ReattachStatus,
    StatusInputs,
    StatusRow,
    is_compact,
)
from . import prompts
from .chrome_dialogs import (
    ConfirmEndScreen,
    EventLogScreen,
    SearchPrompt,
    TextScreen,
    about_lines,
    event_log_lines,
    keys_lines,
    settings_lines,
)
from .fleet_view import (
    CMD_ENDED,
    CMD_MAP,
    CMD_MARK,
    CMD_MORE_TARGETS,
    CMD_OPEN_TARGET,
    CMD_QUERY,
    CMD_SAVE_LOGIN,
    CMD_TAB,
    CMD_TAB_CHANGED,
    CMD_TARGET_DELETE,
    CMD_TARGET_EDIT,
    CMD_TARGET_NEW,
    GROUP_TARGETS,
    TAB_ALERTS,
    TAB_TARGETS,
    FleetRow,
    FleetView,
    FleetViewModel,
    TargetEntry,
)
from .prompts import (
    AlertStack,
    AnswerScreen,
    ConfirmKillScreen,
    EndedScreen,
    QuietBox,
    StaleLayer,
    StalePanel,
    alert_label,
    alert_raised,
    alert_text,
    ended_view,
)
from .size_dialog import SizeDialog
from .whos_here import WhosHereScreen

# The status row is one row (design §2.6). The auto size request subtracts it, and the rail's width,
# from the window; both come from knobs/constants rather than from whatever is mounted, so the request
# is the same before the rail exists and after.
STATUS_ROWS = 1

# What a 401 on the reattach path is told as (`_on_login_needed`, which has no exception of its own to show):
# the same words `notify_api_error` uses for a 401 anywhere.
REATTACH_LOGIN_GONE = "the login this client held no longer works"

# The Menu is Textual's command palette (D10); while it is open the keys are its own (D14).
MENU_KEYS = KeysTarget("menu", (Hint("↑↓", "choose"), Hint("⏎", "run"), Hint("type", "to find"),
                                Hint("Esc", "close")))

# What the operator is told when the keyboard comes to this client, by the `token` frame's reason
# (typing-ownership step 16; Frontend §9, the auto-transfer notice). Taking it oneself is told apart below.
KEYBOARD_GAINED = {
    "handoff": "The keyboard was handed to you.",
    "detach": "The keyboard passed to you: its holder left.",
    "owner": "The owner gave you the keyboard.",
    "release": "The keyboard came back to you.",
}
# The sidebar's, the full view's and Pan's own keys as status-row hints are `sidebar:<command>`,
# `fleet_view:<command>` and `pan:<move>`: each a click (D11), none an action of the table.
SIDEBAR_HINT = "sidebar:"
FLEET_VIEW_HINT = "fleet_view:"
TARGETS_UNAVAILABLE = "the inventory is unavailable"
PAN_HINT = "pan:"
# the scroll view's own keys as status-row hints: `scroll:up`, `scroll:down`, `scroll:search`, `scroll:select`,
# `scroll:copy`, `scroll:done`
SCROLL_HINT = "scroll:"
# What the scroll view says when it drops a key it does not use: nothing reaches the session while it is open.
SCROLL_DROPPED_NOTE = "Esc to return to live, then type"
RECONNECT_DROPPED_NOTE = "Not sent: reconnecting to TSB. Type it again once the session is back."
SEARCH_NONE_YET = "no search yet: / to search"
SEARCH_NOT_OLDER = "no older match"
SEARCH_NOT_NEWER = "no newer match"
SEARCH_HISTORY_MOVED = "the history moved; searching from the view"
SELECT_NOTHING = "nothing selected: v to select"
SELECT_MOVED = "the history moved under the selection; select again"
COPY_BLANK = "nothing to copy: the selection is blank"
COPY_OFF = "copying is off (copy.osc52); use your terminal's own selection with the mouse off or Shift held"
SCROLL_DROPPED = "scroll_view"  # the reason the event log gives for input dropped while the view is open
# The wheel's two buttons in xterm's numbering, which the canvas reports as it has them.
WHEEL_UP, WHEEL_DOWN = 4, 5

# Where a byte string came from (DT4). The first two are typed by the person; the rest are derived.
SOURCE_KEY = "key"
SOURCE_PASTE = "paste"
SOURCE_REPLY = "reply"  # the engine's answer to a query the session asked (WRITE_PTY, DEVICE_ATTRIBUTES)
SOURCE_FOCUS = "focus"
SOURCE_MOUSE = "mouse"
_TYPED = frozenset({SOURCE_KEY, SOURCE_PASTE})
_DRIVE_GATE = object()
# The grid's tracked mark for the text since this client's last typed input (`SessionSlot.mark_input`).
MARK_INPUT = "input"

# Event-log kinds for input this screen did not send (the operator reads how a state arrived, D22).
KIND_INPUT_SUPPRESSED = "input_suppressed"  # typed input held back by the guard (DT4 a)
KIND_INPUT_DROPPED = "input_dropped"  # derived input, or a key the encoder has no name for (DT4 b)

# Why a reattach is under way, by the close that started it (F12, G7).
_REATTACH_WHY = {
    "detached": "the broker closed this view; the session lives on",
    "transport_lost": "the connection to the broker was lost; the session lives on",
}

# What each stale choice is called in a toast about who chose it (the choice's own contract text is
# long; the operator already read it in the dialog).
_STALE_CHOSEN = {"wiggle": "redraw the screen", "ctrl_l": "send Ctrl-L",
                 "continue": "keep going without a redraw", "fresh_session": "start a fresh session"}

# How the keyboard reached the client that holds it, as the sidebar's details say it (the `token` frame's reason).
_HOW_HELD = {"handoff": "handed over", "takeover": "taken by its owner", "attach": "attached",
             "detach": "its holder left", "owner": "the owner gave it", "release": "released to it"}
_ENDS_BY_WORD = {"idle": "idle", "unattended": "unattended", "link_timeout": "link"}
# What a session that needs you says in the full view's Needs you block (mockup F1's own wording where it draws one).
_NEEDS_NOTES = {
    "link_timeout": "not answering · ends in {clock}",
    "idle": "ends in {clock} (idle) · any byte keeps it",
    "unattended": "ends in {clock} (unattended) · any input from a client keeps it",
    "unreachable": "unreachable · input refused · not ended",
    "asks": "asks you a question",
    "stale": "the screen may be stale · a choice is waiting",
}
_STATE_CELLS = {"live": ("● live", "ok"), "detached": ("○ detached", "muted"),
                "unreachable": ("◌ unreachable", "warn"), "killed": ("✕ killed", "danger"),
                "terminated": ("✕ terminated", "danger")}


def age_text(seconds: float) -> str:
    """How long ago, in the sidebar's one or two characters of unit: `41s`, `3m`, `2h`, `4d`."""
    total = max(int(seconds), 0)
    for limit, unit, size in ((60, "s", 1), (3600, "m", 60), (86400, "h", 3600)):
        if total < limit:
            return f"{total // size}{unit}"
    return f"{total // 86400}d"


def end_refused_text(label: str) -> str:
    """What the core's 403 on ending a session means to the person, and to drive's `refused`."""
    return f"Only the owner of {label}, or an admin, can end it."


@dataclass(eq=False)
class SessionSlot:
    """One session open here: its engine and canvas, its pump once it has attached, and what this screen tracks
    for it. Slots are in the order they were opened, which is their number on the rail."""

    session_id: str
    canvas: Canvas
    record: dict[str, Any] | None = None  # the record it was opened from, until the first `attached`
    recording_bar: bool = False  # a later attach (D24): the recording text shows in the status row
    pump: AttachmentPump | None = None
    worker: Any = None
    attach_started: bool = False
    leaving: bool = False
    closed: bool = False
    # True between a `prefix` frame and its dump: the grid is about to be rebuilt at the prefix's geometry, so a
    # size seen in that window waits for the dump (session-attach R13).
    awaiting_dump: bool = False
    ended: prompts.EndedView | None = None  # the end state once the pump has stopped for good (F10)
    ended_dialog_shown: bool = False
    bar_until: float = 0.0
    refusals_seen: int = 0
    was_ending: bool = False
    keys_not_sent: bool = False  # typed keys the guard held back since the keyboard last moved
    input_notice: str = ""  # typed input dropped while this attachment was unavailable; cleared on attach
    unseen: bool = False  # output arrived while it was not in front (the chip's `•`)
    after_attach: list[Callable[[SessionSlot], Any]] = field(default_factory=list)
    # `tsb-tui drive`'s `read reply` is the text since this client's last input here: while set, each typed input marks
    # the grid's cursor just before it is written. A person's TUI leaves it off, so typing pays nothing for it.
    mark_input: bool = False
    # Why this session was left, when it was (an attach that failed, a detach): the sentence the operator was told.
    left: str | None = None
    device_login: Any = None
    device_gate: bool = False
    device_size_pending: bool = False
    login_status: str = ""
    login_status_until: float = 0.0

    @property
    def target(self) -> str:
        state = self.pump.state if self.pump is not None else None
        return ((state.session.get("target_id") if state is not None else None)
                or (self.record or {}).get("target_id") or self.session_id)


class TerminalScreen(Screen):
    DEFAULT_CSS = """
    TerminalScreen { layers: base overlay; }
    #body { layer: base; height: 1fr; width: 1fr; }
    #canvases { width: 1fr; height: 1fr; layers: base overlay; }
    Canvas { layer: base; }
    StatusRow { layer: base; }
    """
    AUTO_FOCUS = ""

    def __init__(self, session_id: str | None = None, *, recording_bar: bool = False) -> None:
        super().__init__()
        # A session to open as the screen mounts, as the gate's Attach does (design G2).
        self._first_session = session_id
        self._first_recording_bar = recording_bar
        self.slots: list[SessionSlot] = []
        self._front: SessionSlot | None = None
        self._stale_layer: StaleLayer | None = None
        self._stale_banner: StaleBanner | None = None
        self._stale_panel: StalePanel | None = None
        self._quiet_box: QuietBox | None = None
        self._alert_stack: AlertStack | None = None
        self._stop_alert_watch: Any = None
        self._last_stale: tuple[str | None, str | None] = (None, None)
        self._leaving = False
        self._unmounted = False
        self._auto_timer: Timer | None = None
        # The session that was in front when the window last changed: the pending debounced request
        # belongs to it, and is dropped if another session comes to the front before it fires (D44).
        self._auto_slot: SessionSlot | None = None
        self._window: Size | None = None  # the outer window as of its last change (the app's first size is the baseline)
        # Input this screen held back, for the status row to say so (phase 5): typed keys and paste
        # the guard suppressed, and derived input DT4 (b) dropped.
        self.suppressed_inputs = 0
        self.dropped_inputs = 0
        # Writes started from a callback that cannot await (a reply, a focus report), kept so they
        # are neither collected mid-flight nor left running after the screen goes.
        self._writes: set[asyncio.Task[None]] = set()
        self.status: StatusRow | None = None
        self.rail: Rail | None = None
        self.sidebar: Sidebar | None = None
        self.fleet_view: FleetView | None = None
        # What the sidebar was doing when the full view opened, so `Esc` returns to it: (it was open, it had the keys).
        self._sidebar_before_view: tuple[bool, bool] = (False, False)
        self._pan_mode = False  # `^\ v`: the arrow keys move the window over the grid
        # `^\ [`: PageUp, PageDown and the wheel move the front session's viewport through its scrollback. Only the
        # session in front is ever scrolled; every way off it puts the viewport back at the bottom.
        self._scroll_mode = False
        # What the status row says in place of the position, since the view last moved: why a key did nothing.
        self._scroll_notice: str | None = None
        # The last query's result while the view is open, and the key (row, start) of the match the view stands on.
        self._search: SearchResult | None = None
        self._search_at: tuple[int, int] | None = None
        self._search_text = ""  # the text of that match's row, to find the row again once the engine drops rows
        # The lines selected in the view, and the row a pressed button went down on while a drag may begin from it.
        self._selection: Selection | None = None
        self._drag_from: int | None = None
        self._dragged = False
        self._chord: str | None = None  # the first character of a two-character key after the leader (`:` of `:q`)
        # The dialogs open over this screen, innermost last; each has `sync()` and `status_target()`.
        self.dialogs: list[Any] = []
        self._menu_open = False
        self._leader_pending = False
        self._leader_timer: Timer | None = None
        self.fleet: Fleet | None = None
        self.poller: FleetPoller | None = None
        # The rail and the sidebar are rebuilt once per refresh of the screen: a trigger marks the model dirty and
        # schedules the rebuild with the app's own after-refresh hook, so poll, alerts, focus and attach that land
        # in one refresh run it once. `_sync_fleet_widgets` is the rebuild itself.
        self._fleet_dirty = False
        self._fleet_flush = False  # `call_after_refresh` answers True/False, not a Timer
        # The Targets tab's inventory, its query's refusal, whether a next page is on its way, and the timer that
        # reads it again while the tab is shown.
        self.targets = TargetsModel()
        self._targets_query_error = ""
        self._target_select: str | None = None
        # The page request in flight: its generation and cursor. Only its own reply may clear it or append.
        self._targets_page: tuple[int, str] | None = None
        self._targets_timer: Timer | None = None

    # -- the session in front, and what the rest of the screen asks of it --

    @property
    def front(self) -> SessionSlot | None:
        return self._front

    @property
    def scroll_view_open(self) -> bool:
        return self._scroll_mode

    @property
    def pump(self) -> AttachmentPump | None:
        return self._front.pump if self._front is not None else None

    @property
    def canvas(self) -> Canvas | None:
        return self._front.canvas if self._front is not None else None

    @property
    def backend(self) -> VTBackend:
        assert self.canvas is not None, "no session is in front"
        return self.canvas.backend

    @property
    def session_id(self) -> str:
        return self._front.session_id if self._front is not None else (self._first_session or "")

    @property
    def ended(self) -> prompts.EndedView | None:
        return self._front.ended if self._front is not None else None

    @property
    def keys_not_sent(self) -> bool:
        return self._front is not None and self._front.keys_not_sent

    @keys_not_sent.setter
    def keys_not_sent(self, value: bool) -> None:
        if self._front is not None:
            self._front.keys_not_sent = value

    @property
    def leader_pending(self) -> bool:
        """The next key is the leader's (a letter, or the second character of `:q`), not the focused widget's."""
        return self._leader_pending or self._chord is not None

    def slot_for(self, session_id: str) -> SessionSlot | None:
        return next((slot for slot in self.slots if slot.session_id == session_id), None)

    def slot_of(self, canvas: Canvas) -> SessionSlot | None:
        return next((slot for slot in self.slots if slot.canvas is canvas), None)

    def apply_look(self, palette: TermPalette) -> None:
        """A run-time look switch (B4): give every canvas the new palette, then rebuild the rail, sidebar,
        fleet view and status row's style tables from the config now in force. The status row is absent when
        `status.show` is false. New canvases already take `app.palette`, so they need no restyle."""
        cfg = self.app.cfg
        for slot in self.slots:
            slot.canvas.set_palette(palette)
        if self.rail is not None:
            self.rail.apply_theme(cfg)
        if self.sidebar is not None:
            self.sidebar.apply_theme(cfg)
        if self.fleet_view is not None:
            self.fleet_view.apply_theme(cfg)
        if self.status is not None:
            self.status.apply_theme(cfg)

    # -- layout --

    def compose(self) -> ComposeResult:
        cfg = self.app.cfg
        self.rail = Rail(cfg)
        self.sidebar = Sidebar(cfg, leader=cfg["keys.leader"])
        self.fleet_view = FleetView(cfg, leader=cfg["keys.leader"])
        self._stale_panel = StalePanel(self)
        self._stale_layer = StaleLayer(self._stale_panel)
        self._stale_banner = StaleBanner(cfg)
        self._quiet_box = QuietBox(self)
        self._quiet_box.display = False
        self._alert_stack = AlertStack(self)
        self._alert_stack.display = False
        # The overlays live in the canvas area, so a dialog over the session leaves the rail on screen (mockup A1).
        canvases = Container(self.fleet_view, self._stale_layer, self._stale_banner, self._quiet_box, self._alert_stack,
                             id="canvases")
        with Horizontal(id="body"):
            if cfg["rail.side"] == "left":
                yield self.rail
                yield self.sidebar
                yield canvases
            else:
                yield canvases
                yield self.sidebar
                yield self.rail
        if cfg["status.show"]:
            self.status = StatusRow(cfg)
            self.status.styles.dock = cfg["status.position"]
            yield self.status

    def on_mount(self) -> None:
        # Only the outer terminal's own focus change is a focus report (DT4 b): `app_focus` flips on
        # the terminal's FocusIn/FocusOut and on nothing else, where a widget's or a dialog's focus
        # never touches it.
        self.watch(self.app, "app_focus", self._on_outer_focus, init=False)
        self._stop_alert_watch = self.app.watch_alerts(self._alerts_changed)
        self.set_interval(1.0, self._tick)
        cfg = self.app.cfg
        # `watch.held`: "manual" is the one value built (D47), so this screen attaches by itself to none of the
        # sessions another login holds: an attach costs a client, and one redraw wiggle on that session's screen.
        self.watch_held = cfg["watch.held"]
        self.fleet = Fleet.from_config(cfg, own_user=self._me())
        self.poller = FleetPoller.from_config(self.app.client, cfg, on_reply=self._poll_reply,
                                              on_error=self._poll_failed)
        self._chrome_width = self.app.size.width
        self._layout_chrome()
        self._sync_alerts()
        if self._first_session is not None:
            self._add_slot({"id": self._first_session}, recording_bar=self._first_recording_bar)
        elif cfg["sidebar.open_when_no_session"]:
            self.open_sidebar(refresh=False)  # the poller's own first poll, just below, is the read
        self.poller.start()
        self.refresh_status()

    def on_unmount(self) -> None:
        # Anything still in flight must not touch a closed engine: flag first, then cancel, then free.
        self._unmounted = True
        if self._auto_timer is not None:
            self._auto_timer.stop()
        self._stop_targets_timer()
        self._cancel_leader()
        if self._stop_alert_watch is not None:
            self._stop_alert_watch()
        if self.poller is not None:
            self.poller.stop()
        for write in self._writes:
            write.cancel()
        for slot in self.slots:
            self._release_slot(slot)
        self.slots.clear()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """The sidebar took the keys (or the session got them back): the status row says where they go (D14)."""
        if self._scroll_mode and (self._sidebar_has_keys() or self._view_has_keys()):
            self.close_scroll_view()  # the view's keys are not what they go to now
        self.refresh_status()

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        self.refresh_status()

    def _me(self) -> str | None:
        """The login this client acts as: the app's (set at login), else the user an attachment learned."""
        if self.app.username:
            return self.app.username
        return next((slot.pump.state.user for slot in self.slots
                     if slot.pump is not None and slot.pump.state.user), None)

    def _window_width(self) -> int:
        return getattr(self, "_chrome_width", self.app.size.width)

    @property
    def compact(self) -> bool:
        """The chrome is compact (the phone's): by `view.chrome` and the window's width (design §2.6)."""
        return is_compact(self.app.cfg["view.chrome"], self._window_width())

    def _sidebar_width(self) -> int:
        """D42: 32 columns, 30 on a phone, 42 from `sidebar.wide_from` columns."""
        cfg = self.app.cfg
        if self.compact:
            return int(cfg["sidebar.width_phone"])
        if self._window_width() >= cfg["sidebar.wide_from"]:
            return int(cfg["sidebar.width_wide"])
        return int(cfg["sidebar.width"])

    def _rail_edge(self) -> bool:
        """The one rule for the rail's width (design §2.6): a compact window with `rail.phone = edge` shows the
        rail as one cell. `_layout_chrome` sets the widget's width from it, `_sync_fleet_widgets` gives the model
        its `edge`."""
        return self.compact and self.app.cfg["rail.phone"] == "edge"

    def _layout_chrome(self) -> None:
        """The rail and the sidebar take the width the window gives them. Nothing here asks for a size (D44)."""
        if self.sidebar is None or self.rail is None:
            return
        self.sidebar.set_window_width(self._window_width())
        self.sidebar.set_width(self._sidebar_width())
        # The rail takes its width here, not when its model lands: a deferred rebuild must not leave the first
        # layout with an unbounded rail, which squeezes the canvas to one column and puts that size in the
        # `attach` frame (DT16). The same rule `_sync_fleet_widgets` gives the model.
        self.rail.styles.width = 1 if self._rail_edge() else self.rail.rail_width
        if self.fleet_view is not None:
            self.fleet_view.set_window_width(self._window_width())
        self._mark_fleet_dirty()

    # -- the sidebar --

    @property
    def sidebar_open(self) -> bool:
        return self.sidebar is not None and bool(self.sidebar.display)

    def open_sidebar(self, group: str | None = None, *, focus: bool = True, refresh: bool = True) -> None:
        """The rail widens in place into the sidebar. It takes width from the canvas, which pans: no request goes
        to any session (D44). Opening it reads the fleet again (a read, session-list step 3), unless the poller has
        only just started."""
        if self.sidebar is None or self.rail is None:
            return
        if self.view_open:
            self.close_fleet_view(restore_sidebar=False)  # the sidebar is the other way to the same list
        self.rail.display = False
        self.sidebar.display = True
        self._layout_chrome()
        if refresh:
            self._refresh_fleet()
        if group is not None:
            if self._fleet_dirty:
                # The rows the sidebar has are from before the last poll; selecting from them picks a row that is
                # gone. Run the pending rebuild first, so the group's first row is the current one.
                self._sync_fleet_widgets()
            self.sidebar.select_group(group)
        if focus:
            self.sidebar.focus()
        self.refresh_status()

    def close_sidebar(self) -> None:
        if self.sidebar is None or self.rail is None:
            return
        self.sidebar.display = False
        self.rail.display = True
        self.set_focus(None)
        self.refresh_status()

    def toggle_sidebar(self) -> None:
        if self.sidebar_open:
            self.close_sidebar()
        else:
            self.open_sidebar()

    def _sidebar_has_keys(self) -> bool:
        return self.sidebar_open and self.sidebar is not None and self.sidebar.has_focus

    # -- the full Fleet view (D7, D8): the sidebar's long form, with the rail still beside it --

    @property
    def view_open(self) -> bool:
        return self.fleet_view is not None and bool(self.fleet_view.display)

    def _view_has_keys(self) -> bool:
        return self.view_open and self.fleet_view is not None and self.fleet_view.has_focus

    def open_fleet_view(self, tab: str | None = None) -> None:
        """Show the full view over the canvas, the rail beside it (mockup F1). Opening it asks no session for
        anything (D44); the sidebar, if it was open, steps back to the rail and returns when the view closes."""
        view = self.fleet_view
        if view is None or self.rail is None:
            return
        if self.view_open:
            if tab is not None:
                view.set_tab(tab)
            view.focus()
            self.refresh_status()
            return
        self._sidebar_before_view = (self.sidebar_open, self._sidebar_has_keys())
        if self.sidebar_open:
            self.close_sidebar()
        view.reset()
        self.targets.reset()
        self._targets_query_error = ""
        self._targets_page = None
        view.set_window_width(self._window_width())
        view.display = True
        self._sync_fleet_widgets()  # the model must be set before `set_tab` below: the tabs come from it
        self._refresh_fleet()
        if tab is not None:
            view.set_tab(tab)
        view.focus()
        self.refresh_status()

    def close_fleet_view(self, *, restore_sidebar: bool = True) -> None:
        """`Esc`: back to the session it came from, and to the sidebar if that is where it was opened."""
        view = self.fleet_view
        if view is None or not view.display:
            return
        view.display = False
        self._stop_targets_timer()
        self.set_focus(None)
        was_open, had_keys = self._sidebar_before_view
        self._sidebar_before_view = (False, False)
        if restore_sidebar and was_open:
            self.open_sidebar(focus=had_keys, refresh=False)
        self.refresh_status()

    def _after_pick(self) -> None:
        """A pick brings its session to the front; `sidebar.close_on_pick` says whether the sidebar goes
        (always, never, or on a phone). When it stays, the keys go back to the session."""
        mode = self.app.cfg["sidebar.close_on_pick"]
        if mode == "always" or (mode == "phone" and self.compact):
            self.close_sidebar()
        else:
            self.set_focus(None)
            self.refresh_status()

    # -- the fleet: polling, and the models the rail and the sidebar show --

    def _refresh_fleet(self) -> None:
        if self.poller is not None:
            self.poller.refresh_async()

    def _poll_reply(self, generation: int, records: list[dict]) -> None:
        """Runs on the poller's thread: hand the reply to the UI thread."""
        try:
            self.app.call_from_thread(self._apply_poll, generation, records)
        except RuntimeError:
            pass  # the app is going away

    def _poll_failed(self, exc: Exception) -> None:
        """A poll that failed is tried again at the next interval; only a lapsed login is the operator's to hear."""
        if isinstance(exc, ApiError) and exc.status == 401:
            try:
                self.app.call_from_thread(self.app.notify_api_error, exc)
            except RuntimeError:
                pass

    def _apply_poll(self, generation: int, records: list[dict]) -> None:
        if self._unmounted or self.fleet is None or not self.fleet.apply(generation, records):
            return
        changed = False
        for slot in self.slots:
            record = self.fleet.records.get(slot.session_id)
            if record is not None and slot.pump is not None and slot.pump.state.note_record(record):
                changed = True
                self.app.tapped(slot.session_id, TAP_RECORD, record)
        self._mark_fleet_dirty()
        if changed:
            self.refresh_status()

    def _live(self) -> dict[str, dict]:
        """A record per session open here: the last poll's with this client's own frames read over it."""
        assert self.fleet is not None
        live: dict[str, dict] = {}
        for slot in self.slots:
            polled = self.fleet.records.get(slot.session_id)
            opened_from = {"id": slot.session_id, "target_id": slot.session_id, "owner": None,
                           "control_holder": None, "state": "live", **(slot.record or {})}
            if slot.pump is None:
                live[slot.session_id] = {**opened_from, **(polled or {})}
            else:
                live[slot.session_id] = live_record(slot.pump.state, polled, opened_from)
        return live

    def _attention(self) -> tuple[set[str], set[str]]:
        """The open sessions with a stale screen this client types in, and with a question waiting for it."""
        stale, asks = set(), set()
        for slot in self.slots:
            state = slot.pump.state if slot.pump is not None else None
            if state is None or state.closed:
                continue
            if state.stale_state == "pending" and state.holds_keyboard:
                stale.add(slot.session_id)
            if state.quiet_prompt is not None:
                asks.add(slot.session_id)
        return stale, asks

    def _mark_fleet_dirty(self) -> None:
        """A trigger asks for the rail and the sidebar to be rebuilt: mark the model dirty and schedule one rebuild
        with the app's own after-refresh hook, so triggers that land in one refresh of the screen run it once. A
        caller that needs the models now (a test, a layout change) calls `_sync_fleet_widgets` directly."""
        if self._unmounted:
            return
        self._fleet_dirty = True
        if self._fleet_flush:
            return
        self._fleet_flush = self.call_after_refresh(self._flush_fleet_widgets)

    def _flush_fleet_widgets(self) -> None:
        self._fleet_flush = False
        if self._fleet_dirty:
            self._sync_fleet_widgets()

    def _sync_fleet_widgets(self) -> None:
        """Rebuild the rail and the sidebar from the models: the rebuild itself, run once per refresh by
        `_flush_fleet_widgets`, and directly by a caller that needs the models now. It consumes the dirty mark,
        so the next tick with unchanged records only moves the clocks."""
        self._fleet_dirty = False
        if self._unmounted or self.fleet is None or self.rail is None or self.sidebar is None:
            return
        fleet = self.fleet
        fleet.set_own_user(self._me())
        live = self._live()
        open_ids = [slot.session_id for slot in self.slots]
        stale, asks = self._attention()
        front_id = self._front.session_id if self._front is not None else None
        unseen = {slot.session_id for slot in self.slots if slot.unseen}
        scan = fleet.scan(open_here=open_ids, stale_with_me=stale, question_for_me=asks, live=live)
        chips = fleet.chips(scan=scan, front=front_id, unseen=unseen, question_for_me=asks,
                            stale_with_me=stale, live=live)
        needing = scan.needs_you
        alerts = self.app.alerts
        self.rail.set_model(RailModel(
            chips=tuple(chips), more=len(scan.chip_ids) > MAX_CHIPS,
            need_you=len(needing), alerts=alerts.count if alerts.allowed else None,
            edge=self._rail_edge(),
            names=tuple((chip.session_id, live[chip.session_id].get("target_id", chip.session_id))
                        for chip in chips if chip.session_id in live)))
        if self.sidebar_open:
            self.sidebar.set_model(self._sidebar_model(scan, live, chips, stale, asks),
                                   front_id=front_id)
        if self.view_open and self.fleet_view is not None:
            self.fleet_view.set_model(self._fleet_view_model(live, needing, stale, asks))

    def _sidebar_model(self, scan: FleetScan, live: dict[str, dict], chips: list, stale: set[str],
                       asks: set[str]) -> SidebarModel:
        """The sidebar's model: the filtered records as references, with what `fleet.scan` gave in its one pass —
        the groups, the target counts and each record's two column fields. Nothing in it formats a record:
        `record_entry` is what the view calls for a row it paints, so a rebuild of a long list builds about 28
        entries, not one per record."""
        fleet = self.fleet
        assert fleet is not None
        me = self._me()
        groups = scan.groups
        numbers = {chip.session_id: chip.number for chip in chips}
        needing_ids = {record["id"] for record in scan.needs_you}
        labels = fleet.display_ids(live)
        by_target = scan.by_target
        columns = scan.columns

        def fields_of(record: dict) -> tuple[str, str]:
            return columns[record["id"]]

        def search_of(record: dict) -> str:
            target, login = fields_of(record)
            return f"{target} {labels[record['id']]} {login} {record.get('owner') or ''}"

        def entry_for(record: dict, group: str) -> SidebarEntry:
            # The clock is read when the row is painted: a relayout after a tick that moved the painted clocks
            # builds this row at the current clock, never at the clock the model was built at.
            return self._entry(record, group, me, numbers, needing_ids, stale, asks, labels, by_target,
                                self.app.wall_clock())

        alerts = self.app.alerts
        leader = A.leader_label(self.app.cfg["keys.leader"])
        letter = A.leader_keys(self.app.cfg.leader_overrides())["fleet"]
        return SidebarModel(
            groups=tuple((name, tuple(records)) for name, records in groups.items()),
            mode=fleet.filter_mode, alerts=alerts.count if alerts.allowed else None,
            leader_hint=f"{leader} {letter}", detail_for=self._details_for, buttons_for=self._buttons_for,
            total=sum(len(records) for records in groups.values()),
            record_fields=fields_of, record_search=search_of, record_entry=entry_for)

    def _time_state(self, record: dict, sid: str, stale: set[str], asks: set[str], now: float) -> RowTime:
        """A row's time-bearing fields read from its record at `now`: why it needs you, the warning window's clock,
        the time to the end, and the last output this TUI saw. The tick reads exactly this for the painted rows."""
        reason = countdown = None
        ends = parse_timestamp(record.get("ends_at"))
        left = None if ends is None else format_duration(ends.timestamp() - now)
        if record.get("state") == "unreachable":
            reason = "unreachable"
        elif sid in asks:
            reason = "asks"
        elif sid in stale:
            reason = "stale"
        elif record.get("ending") and left is not None:
            reason, countdown = record.get("ends_by") or "ending", left
        out_age = None
        slot = self.slot_for(sid)
        if slot is not None and slot is not self._front and slot.pump is not None:
            at = slot.pump.state.last_output_at
            if at is not None:
                out_age = age_text(slot.pump.state.now() - at)
        ended = record.get("state") in ("killed", "terminated")
        return RowTime(reason=reason, countdown=countdown, remaining=None if ended else left,
                       out_age=out_age, rec=bool((record.get("recording") or {}).get("active")))

    def _entry(self, record: dict, group: str, me: str | None, numbers: dict[str, int], needing: set[str],
               stale: set[str], asks: set[str], labels: Mapping[str, str], by_target: dict[str, int],
               now: float) -> SidebarEntry:
        sid = record["id"]
        holder = record.get("control_holder")
        login = "·" if holder is None else ("you" if holder.get("user") == me else str(holder.get("user")))
        who = who_types(record, me, needs_you=sid in needing, asks_you=sid in asks)
        time_state = self._time_state(record, sid, stale, asks, now)
        target = record.get("target_id", "")
        id_label = labels[sid] if by_target.get(target, 0) > 1 else None
        short = labels[sid]
        words = f"{login} types" if who == WHO_OTHER else WHO_WORDS.get(who, "")
        tip = " · ".join(part for part in (f"{target} · {short}", words) if part)
        return SidebarEntry(
            session_id=sid, group=group, number=numbers.get(sid), who=who, target=target, id_label=id_label,
            login=login, rec=time_state.rec, front=self._front is not None and self._front.session_id == sid,
            reason=time_state.reason, countdown=time_state.countdown, remaining=time_state.remaining,
            out_age=time_state.out_age, search=f"{target} {short} {login} {record.get('owner') or ''}", tip=tip)

    def listed_sessions(self) -> list[dict]:
        """The records the fleet lists under the current filter, in the order the full view shows them: the sessions
        open here first, in rail order, then the rest in the core's order. What `tsb-tui drive`'s `sessions` reads."""
        assert self.fleet is not None
        open_ids = [slot.session_id for slot in self.slots]
        numbers = {sid: index for index, sid in enumerate(open_ids)}
        visible = self.fleet.visible(open_here=open_ids, live=self._live())
        first = sorted((r for r in visible if r["id"] in numbers), key=lambda r: numbers[r["id"]])
        return [*first, *(r for r in visible if r["id"] not in numbers)]

    def live_records(self) -> dict[str, dict]:
        """A record per session open here: the last poll's with this client's own frames read over it."""
        return self._live()

    def chip_number(self, session_id: str) -> int | None:
        """The session's number on the rail, or None when it has no chip."""
        assert self.fleet is not None
        stale, asks = self._attention()
        chips = self.fleet.chips(open_here=[s.session_id for s in self.slots], front=None, question_for_me=asks,
                                 stale_with_me=stale, live=self._live())
        return next((chip.number for chip in chips if chip.session_id == session_id), None)

    def _record_for(self, session_id: str) -> dict | None:
        assert self.fleet is not None
        return self._live().get(session_id) or self.fleet.records.get(session_id)

    def _details_for(self, session_id: str) -> tuple[str, ...]:
        """The selected session's details, under the groups from `sidebar.details_from` columns (mockup S3): who
        types and since when, the last output, the countdown, who is attached. What only an attachment knows (since
        when, the last output) shows only while this TUI is attached (D47)."""
        record = self._record_for(session_id)
        if record is None:
            return ()
        assert self.fleet is not None
        me = self._me()
        slot = self.slot_for(session_id)
        numbers = {s.session_id: i for i, s in enumerate(self.slots, start=1)}
        owner = record.get("owner")
        number = f"{numbers[session_id]} " if session_id in numbers else ""
        short = self.fleet.display_ids(self._live())[session_id]
        lines = [f"{number}{record.get('target_id', '')} · {short} · {'yours' if owner == me else owner}"]
        holder = record.get("control_holder")
        types = "nobody types" if holder is None else (
            "you type" if holder.get("user") == me else f"{holder.get('user')} types")
        state = slot.pump.state if slot is not None and slot.pump is not None else None
        sentence = types
        if state is not None and state.token_history and state.holder is not None:
            move = state.token_history[-1]
            when = datetime.fromtimestamp(self.app.wall_clock() - (state.now() - move["at"]), tz=UTC).astimezone()
            how = _HOW_HELD.get(move["reason"])
            if move["reason"] == "handoff" and move["previous"] is not None and (
                    move["previous"].get("user") == me):
                how = "you handed it over"
            sentence += f" since {when:%H:%M}" + (f" ({how})" if how else "")
        if state is not None and state.last_output_at is not None and slot is not self._front:
            sentence += f" · output {age_text(state.now() - state.last_output_at)} ago"
        unreachable = record.get("state") == "unreachable"
        # A session the core cannot reach is not idling toward its end: input is refused and nothing ends it (mockup
        # F1), so its details say that, never a countdown that reads as if it were healthy.
        lines.append(_NEEDS_NOTES["unreachable"] if unreachable else sentence)
        ends = parse_timestamp(record.get("ends_at"))
        if ends is not None and not unreachable and record.get("state") not in ("killed", "terminated"):
            left = format_duration(ends.timestamp() - self.app.wall_clock())
            by = _ENDS_BY_WORD.get(record.get("ends_by") or "")
            lines.append(f"ends in {left} if nothing happens" + (f" ({by})" if by else ""))
        clients = record.get("clients") or []
        if clients:
            held = holder.get("client_id") if holder else None
            names = []
            for client in clients:
                mine = state is not None and client.get("client_id") == state.client_id
                name = "you" if mine else str(client.get("user"))
                if mine and client.get("client_id") != held:
                    name += " (watching)"
                names.append(name)
            lines.append("attached: " + ", ".join(names))
        return tuple(lines)

    def _buttons_for(self, session_id: str) -> tuple[str, ...]:
        record = self._record_for(session_id)
        if record is None:
            return ()
        buttons = []
        if self._take_back_reason(record) is None:
            buttons.append("t Take back")
        if self.slot_for(session_id) is not None:
            buttons.append("w Who's here")
        buttons.append("⏎ Open")
        if record.get("state") not in ("killed", "terminated"):
            buttons.append("x End…")
        return tuple(buttons)

    # -- the full view's model --

    def _fleet_view_model(self, live: dict[str, dict], needing: list[dict], stale: set[str],
                          asks: set[str]) -> FleetViewModel:
        """The rows of the full view from the same records the sidebar reads: the sessions open here first, in the
        order they were opened (their rail numbers), then the rest in the order the core listed them."""
        fleet = self.fleet
        assert fleet is not None
        me = self._me()
        open_ids = [slot.session_id for slot in self.slots]
        merged = {**fleet.records, **live}
        labels = fleet.display_ids(live)
        by_target: dict[str, int] = {}
        for record in merged.values():
            by_target[record.get("target_id", "")] = by_target.get(record.get("target_id", ""), 0) + 1
        now = self.app.wall_clock()
        numbers = {sid: index for index, sid in enumerate(open_ids, start=1)}
        needing_ids = {record["id"] for record in needing}

        def row(record: dict) -> FleetRow:
            entry = self._entry(record, "view", me, numbers, needing_ids, stale, asks, labels, by_target, now)
            return self._fleet_row(record, entry, me, numbers, labels, now)

        visible = fleet.visible(open_here=open_ids, live=live)
        first = [record for record in visible if record["id"] in numbers]
        first.sort(key=lambda record: numbers[record["id"]])
        rest = [record for record in visible if record["id"] not in numbers]
        alerts = self.app.alerts
        alert_rows = None if not alerts.allowed else tuple(
            f"▲ {alert_label(a)} · {alert_text(a)} {alert_raised(a)}" for a in reversed(list(alerts.active.values())))
        front = self._front
        back = f"{numbers[front.session_id]} {front.target}" if front is not None else ""
        view = self.fleet_view
        on_targets = view is not None and view.tab == TAB_TARGETS
        return FleetViewModel(
            needs=tuple(row(record) for record in needing),
            rows=tuple(row(record) for record in [*first, *rest]), mode=fleet.filter_mode,
            alert_lines=alert_rows, back_label=back,
            tabs=tuple(self.app.cfg["fleet_view.tabs"]), detail_for=self._details_for,
            buttons_for=self._buttons_for,
            targets=self._target_entries(merged) if on_targets else (),
            target_edit="target.edit" in self.app.permissions,
            targets_count=self.targets.count_label(),
            targets_note=TARGETS_UNAVAILABLE if self.targets.unavailable else "",
            query_error=self._targets_query_error, target_details_for=self._target_details)

    # -- the Targets tab (F14): the inventory the core lists, queried through the broker --

    def _target_entries(self, records: Mapping[str, dict]) -> tuple[TargetEntry, ...]:
        """A line per target on show. The sessions this login sees there are counted from the fleet's records by
        `target_id`; `attach` is offered wherever one of them has not ended (live, detached or unreachable: a create's answer is not known before it, but a reattach is)."""
        here = self._sessions_by_target(records)
        entries = []
        for row in self.targets.rows:
            sessions = here.get(row.id, [])
            live = sum(1 for record in sessions if record.get("state") == "live")
            other = len(sessions) - live
            cell = " ".join(part for part in (f"{live} ● live" if live else "", f"{other} ○" if other else "")
                            if part)
            entries.append(TargetEntry(
                target_id=row.id, name=row.name, type=row.type, group=row.group, tags=" ".join(row.tags),
                source=row.discovered_by, sessions=cell or "·", can="attach" if sessions else ""))
        return tuple(entries)

    @staticmethod
    def _sessions_by_target(records: Mapping[str, dict]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for record in records.values():
            if record.get("state") not in ("killed", "terminated"):
                out.setdefault(record.get("target_id", ""), []).append(record)
        return out

    def _target_details(self, target_id: str) -> tuple[str, ...]:
        """F14's two lines under the table: the target's facts, then the sessions this login sees on it."""
        row = self.targets.row(target_id)
        if row is None or self.fleet is None:
            return ()
        facts = [row.label]
        if row.host:
            facts.append(f"host {row.host}")
        if row.port:
            facts.append(f"port {row.port}")
        if row.discovered_by:
            facts.append(f"discovered by {row.discovered_by}")
        seen = parse_timestamp(row.last_seen)
        if seen is not None:
            facts.append(f"last seen {age_text(self.app.wall_clock() - seen.timestamp())} ago")
        live = self._live()
        records = {**self.fleet.records, **live}
        mine = self._sessions_by_target(records).get(target_id, [])
        labels = self.fleet.display_ids(live)
        me = self._me()
        words = []
        for record in mine[:4]:
            holder = record.get("control_holder")
            typing = " (you type)" if holder is not None and holder.get("user") == me else ""
            words.append(f"{labels.get(record['id'], record['id'])} {as_text(record.get('state'))}{typing}")
        if len(mine) > 4:
            words.append(f"+{len(mine) - 4} more")
        return (" · ".join(facts), "sessions here: " + (" · ".join(words) if words else "none"))

    def _fetch_targets(self, *, query: Query | None = None, more: bool = False, background: bool = False) -> None:
        """`GET /targets` off the UI thread: the query now wanted (or `query`) from its first page, or with `more`
        the next page of the query on show. A page request carries its generation and cursor, and a reply older
        than the newest request is discarded (`TargetsModel`). A refresh waits while a page is in flight: its own
        reply must be the one that extends the list, not one that folds it back."""
        model = self.targets
        if more:
            if model.next_cursor is None or self._targets_page is not None or model.wanted != model.query:
                return  # no next page, one is in flight, or the query on show is no longer the one wanted
            cursor, limit = model.next_cursor, PAGE_LIMIT
            query = model.query
            generation = model.begin()
            self._targets_page = (generation, cursor)
        else:
            if background and self._targets_page is not None:
                return
            self._targets_page = None  # a fresh read supersedes any page still in flight
            query = model.wanted if query is None else query
            cursor, limit = None, (model.request_limit() if query == model.query else PAGE_LIMIT)
            generation = model.begin(query)
        call = functools.partial(self.app.client.list_targets, **query.filters(), limit=limit, cursor=cursor)
        self.app.call_api(
            call, on_done=lambda page: self._targets_reply(generation, query, page, more, cursor),
            on_error=lambda exc: self._targets_failed(generation, exc, more, background, cursor))

    def _targets_reply(self, generation: int, query: Query, page: dict, more: bool, cursor: str | None) -> None:
        if more:
            if self._targets_page != (generation, cursor):
                return  # not this page's reply: it may neither append nor clear the one in flight
            self._targets_page = None
        if self._unmounted:
            return
        if self.targets.apply(generation, query, page, append=more):
            self._mark_fleet_dirty()
            if self._target_select is not None and self.fleet_view is not None:
                if self.targets.row(self._target_select) is not None:
                    self.fleet_view.selected = (GROUP_TARGETS, self._target_select)
                    self._target_select = None
                elif self.targets.next_cursor:
                    self._fetch_targets(more=True)
                self._mark_fleet_dirty()

    def _targets_failed(self, generation: int, exc: Exception, more: bool, background: bool,
                        cursor: str | None) -> None:
        """503 `inventory_unavailable` is a state the tab shows; a refused query keeps the rows and says why in the
        query line; a 401 goes the way every 401 goes; anything else is a toast, unless it was only the timer's read
        (the next tick tries again). Only the newest request may change the tab."""
        if more:
            if self._targets_page != (generation, cursor):
                return  # an old page's refusal is not this page's, and it does not clear it
            self._targets_page = None
        if self._unmounted:
            return
        if isinstance(exc, ApiError) and exc.code == "inventory_unavailable":
            if self.targets.mark_unavailable(generation):
                self._mark_fleet_dirty()
        elif isinstance(exc, ApiError) and exc.code == "invalid_request":
            if self.targets.is_current(generation):
                # The refusal is the broker's answer to this query only: the rows stay, the message goes in the
                # query line, and the wanted query returns to the one the rows show, so paging and polls ask for
                # that and not for the refused one.
                self.targets.revert_to_shown()
                self._targets_query_error = as_text(exc.message)
                self._mark_fleet_dirty()
        elif not background or (isinstance(exc, ApiError) and exc.status == 401):
            self.app.notify_api_error(exc)

    def _run_query(self, text: str) -> None:
        """The query line was entered: parse it (a refusal is shown in the line, the rows stay), then ask the broker."""
        self._targets_query_error = ""
        try:
            query = parse_query(text)
        except QueryError as exc:
            self._targets_query_error = as_text(str(exc))
            self._mark_fleet_dirty()
            return
        self._mark_fleet_dirty()
        self._fetch_targets(query=query)

    def _targets_tab_changed(self) -> None:
        """The Targets tab was opened (read it now, then on `fleet_view.refresh_s`) or left (stop reading it)."""
        view = self.fleet_view
        self._stop_targets_timer()
        self._mark_fleet_dirty()
        if view is None or not view.display or view.tab != TAB_TARGETS:
            return
        self._fetch_targets()
        interval = self.app.cfg["fleet_view.refresh_s"]
        if interval > 0:
            self._targets_timer = self.set_interval(interval, self._targets_tick)

    def _targets_tick(self) -> None:
        view = self.fleet_view
        if self._unmounted or view is None or not view.display or view.tab != TAB_TARGETS:
            self._stop_targets_timer()
            return
        self._fetch_targets(background=True)

    def _stop_targets_timer(self) -> None:
        if self._targets_timer is not None:
            self._targets_timer.stop()
            self._targets_timer = None

    def _open_target(self, target_id: str | None) -> None:
        """`⏎` on a target: New session there, with its type known so the dialog can apply the telnet rule."""
        row: TargetRow | None = self.targets.row(target_id)
        if row is not None:
            self._new_session_dialog(row.id, row.type)

    def _fleet_row(self, record: dict, entry: SidebarEntry, me: str | None, numbers: dict[str, int],
                   labels: Mapping[str, str], now: float) -> FleetRow:
        sid = record["id"]
        state = record.get("state")
        state_text, state_role = _STATE_CELLS.get(state, (str(state or "?"), "muted"))
        ended = state in ("killed", "terminated")
        holder = record.get("control_holder")
        keys = "· nobody" if holder is None else entry.login
        types = ("yours · nobody" if record.get("owner") == me else "nobody types") if holder is None else (
            "you type" if entry.login == "you" else f"{entry.login} types")
        ends = ""
        stamp = parse_timestamp(record.get("ends_at"))
        if stamp is not None and not ended:
            left = format_duration(stamp.timestamp() - now)
            by = _ENDS_BY_WORD.get(record.get("ends_by") or "")
            ends = ("▲ " if record.get("ending") else "") + left + (f" · {by}" if by else "")
        note = ""
        if entry.reason is not None:
            note = _NEEDS_NOTES.get(entry.reason, "ends in {clock}").format(clock=entry.countdown or "")
        owner = record.get("owner")
        return FleetRow(
            session_id=sid, who=entry.who, target=entry.target,
            id_label=labels[sid], type=str(record.get("session_type") or ""),
            state=state_text, state_role=state_role, keys=keys, open=str(numbers[sid]) if sid in numbers else "",
            clients=str(len(record["clients"])) if "clients" in record else "·",
            owner="you" if owner == me else str(owner or ""), ends=ends, rec=entry.rec, front=entry.front,
            note=note, types=types, search=entry.search, ended=ended)

    def on_fleet_view_command(self, message: FleetView.Command) -> None:
        message.stop()
        self._fleet_view_command(message.name, message.arg)

    def _fleet_view_command(self, name: str, arg: str | None = None) -> None:
        view = self.fleet_view
        assert view is not None and self.fleet is not None
        sid = arg or view.selected_session
        if name == CMD_CLOSE:
            self.close_fleet_view()
        elif name == CMD_PICK and sid is not None:
            self.bring_forward(sid)
            self.close_fleet_view()
        elif name == CMD_TAKE_BACK and sid is not None:
            self.close_fleet_view()
            self.take_back_session(sid)
        elif name == CMD_WHOS_HERE and sid is not None:
            if self.slot_for(sid) is None:
                self.app.notify("Who's here: open the session first (Enter).", title="not available",
                                severity="warning", markup=False)
                return
            self.bring_forward(sid)
            self.close_fleet_view()
            self.run_worker(self.run_action_id("who"), exclusive=False)
        elif name == CMD_END and sid is not None:
            self.end_session(sid)
        elif name == CMD_NEW:
            self.run_worker(self.run_action_id("new_session"), exclusive=False)
        elif name == CMD_OPEN_TARGET:
            self._open_target(sid)
        elif name in (CMD_TARGET_NEW, CMD_TARGET_EDIT, CMD_TARGET_DELETE):
            self._target_write_command(name, sid)
        elif name == CMD_SAVE_LOGIN:
            self._targets_to_vault(view, save=True)
        elif name == CMD_MAP:
            self._targets_to_vault(view, save=False)
        elif name == CMD_QUERY:
            self._run_query(arg or "")
        elif name == CMD_MORE_TARGETS:
            self._fetch_targets(more=True)
        elif name == CMD_TAB_CHANGED:
            self._targets_tab_changed()
        elif name == CMD_MODE:
            self.fleet.filter_mode = arg if arg in ("mine", "all") else (
                "all" if self.fleet.filter_mode == "mine" else "mine")
            self._mark_fleet_dirty()

    def target_saved(self, record: dict | None) -> None:
        if record is not None:
            self._target_select = record["id"]
            self._targets_query_error = ""
            # A saved target must be selectable even when its edited fields no longer match
            # the query that opened the form. The broker still does all filtering.
            if self.fleet_view is not None:
                self.fleet_view.query_text = ""
                self.fleet_view.applied_query = ""
            self._fetch_targets(query=Query())

    def _target_write_command(self, command: str, target_id: str | None) -> None:
        from .target_form import ConfirmTargetDeleteScreen, TargetFormScreen

        if "target.edit" not in self.app.permissions:
            return
        if command == CMD_TARGET_NEW:
            self.app.push_screen(TargetFormScreen(), self.target_saved)
            return
        row = self.targets.row(target_id)
        if row is None:
            return
        if row.discovered_by != "tsb":
            self._targets_query_error = f"This target comes from {row.discovered_by} and is not edited through TSB."
            self._mark_fleet_dirty()
            return
        if command == CMD_TARGET_EDIT:
            self.app.call_api(self.app.client.get_target_definition, row.id,
                              on_done=lambda definition: self.app.push_screen(TargetFormScreen(definition), self.target_saved),
                              on_error=self._target_write_failed)
        else:
            def confirmed(yes: bool) -> None:
                if yes:
                    self.run_worker(self._delete_target(row.id), exclusive=False)
            self.app.push_screen(ConfirmTargetDeleteScreen(row.id), confirmed)

    async def delete_target(self, target_id: str) -> None:
        """The send half of confirmed deletion, shared with drive's explicit confirm."""
        await asyncio.to_thread(self.app.client.delete_target, target_id)
        self._targets_query_error = ""
        self._fetch_targets()

    async def _delete_target(self, target_id: str) -> None:
        try:
            await self.delete_target(target_id)
        except (ApiError, HttpError, OSError) as exc:
            self._target_write_failed(exc)

    def _target_write_failed(self, exc: Exception) -> None:
        self._targets_query_error = as_text(exc.message if isinstance(exc, ApiError) else str(exc))
        self._mark_fleet_dirty()

    def _targets_to_vault(self, view: FleetView, *, save: bool) -> None:
        """`l` and `p` on the Targets tab: Save a login for, or Map one of my credentials to, the targets marked with `space`
        (else the selected one). The marks go once the screen reports it did something."""
        from .credentials import MapScreen, SaveLoginScreen

        picked = view.picked_targets()
        if not picked:
            self.app.notify("Select a target first.", title="not available", severity="warning", markup=False)
            return

        def done(finished: bool | None) -> None:
            if finished:
                view.clear_marks()

        self.app.push_screen(SaveLoginScreen(picked) if save else MapScreen(targets=picked), done)

    # -- End session… (session-end-by-tsb step 1) --

    def _session_label(self, session_id: str) -> str:
        record = self._record_for(session_id) or {}
        assert self.fleet is not None
        ids = [*self.fleet.records, session_id]
        return f"{record.get('target_id', session_id)} · {display_id(session_id, ids, self.fleet.id_min_hex)}"

    def end_session(self, session_id: str) -> None:
        """Ask once, naming the session, then `DELETE /sessions/{id}`. Whether this login may end it is the core's
        to answer (the owner or an admin): a refusal is shown, never guessed at beforehand."""
        if self.has_ended(session_id):
            self.app.notify(f"{self._session_label(session_id)} has already ended.", title="not available",
                            severity="warning", markup=False)
            return
        label = self._session_label(session_id)

        def decided(confirmed: bool | None) -> None:
            if confirmed:
                self.send_end(session_id)

        self.app.push_screen(ConfirmEndScreen(self, label), decided)

    def has_ended(self, session_id: str) -> bool:
        """Whether the record this screen holds for the session says it is over."""
        record = self._record_for(session_id)
        return record is not None and record.get("state") in ("killed", "terminated")

    def send_end(self, session_id: str, on_result: Callable[[ApiError | HttpError | OSError | None], None] | None = None
                 ) -> None:
        """The send half of End session…: `DELETE /sessions/{id}`, then what a person sees (the toast, or the
        refusal). `on_result` gets None once the core answered 204, else the failure, after those have run. The dialog
        calls this when End it is pressed; drive's `end` calls it with the agent's `confirm`."""
        label = self._session_label(session_id)

        def done(_: None) -> None:
            self._ended_by_me(label)
            if on_result is not None:
                on_result(None)

        def failed(exc: ApiError | HttpError | OSError) -> None:
            self._end_refused(label, exc)
            if on_result is not None:
                on_result(exc)

        def stale(exc: ApiError | HttpError | OSError) -> None:
            # A 401 answered on a client sign-out has since replaced: no toast, no trip to Login, but a drive `end`
            # future still settles so nothing waits on it forever.
            if on_result is not None:
                on_result(exc)

        self.app.call_api(self.app.client.close, session_id, on_done=done, on_error=failed,
                          on_stale_error=stale)

    def _ended_by_me(self, label: str) -> None:
        self.app.toast(f"You ended {label}.", title="session")
        self._refresh_fleet()

    def _end_refused(self, label: str, exc: ApiError | HttpError | OSError) -> None:
        if isinstance(exc, ApiError) and exc.status == 403:
            self.app.notify(end_refused_text(label), title="refused",
                            severity="warning", markup=False)
            return
        self.app.notify_api_error(exc)

    def _alerts_changed(self) -> None:
        self._sync_alerts()
        self._mark_fleet_dirty()

    # -- several sessions: open, bring to the front, switch --

    def open_session(self, session: dict, hostkey: dict | None = None, *,
                     then: Callable[[SessionSlot], Any] | None = None, front: bool = True) -> None:
        """Attach to `session` and bring it to the front, or just bring it forward when it is open here. The
        gate comes first for the first attach to a session in this run, a first-use host key, or every attach when
        `gates.recording` says so (design G2, D24); nothing is attached until it is passed."""
        from .attach_confirm import AttachConfirmScreen

        if self._refuse_while_signing_out():
            return
        sid = session["id"]
        slot = self.slot_for(sid)
        if slot is not None:
            if front:
                self.show_front(slot)
            if then is not None:
                self._run_then(slot, then)
            return
        every = self.app.cfg["gates.recording"] == "every-attach"
        if hostkey is not None or every or sid not in self.app.gated_sessions:
            def attach(confirmed: bool | None) -> None:
                if confirmed and not self._refuse_while_signing_out():
                    self._add_slot(session, then=then, front=front)

            self.app.push_screen(AttachConfirmScreen(session, hostkey), attach)
            return
        self._add_slot(session, recording_bar=bool((session.get("recording") or {}).get("active")),
                       then=then, front=front)

    def _refuse_while_signing_out(self) -> bool:
        """Sign-out detaches every session and then tells the broker: a session opened or attached in between would
        outlive it. True, with a one-line reason, while sign-out runs; the same gate as Login's."""
        if not self.app.signing_out:
            return False
        self.app.notify("Signing out… the session was not opened.", title="signing out", severity="warning",
                        markup=False)
        return True

    def _add_slot(self, session: dict, *, recording_bar: bool = False,
                  then: Callable[[SessionSlot], Any] | None = None, front: bool = True,
                  defer_attach: bool = False) -> SessionSlot:
        cfg = self.app.cfg
        # The grid starts at a placeholder size; `attached` gives it the session's.
        backend = VTBackend(self.app.engine, 80, 24, scrollback_bytes=cfg["term.scrollback_bytes"])
        canvas = Canvas(backend, self.app.palette, cfg)
        canvas.in_front = False
        slot = SessionSlot(session["id"], canvas, record=dict(session), recording_bar=recording_bar)
        slot.attach_started = defer_attach
        # The engine's answers to the session's queries leave through the one input path (DT3, DT4 b), for
        # this session's attachment and no other.
        backend.set_reply_callback(functools.partial(self._on_reply, slot))
        if then is not None:
            slot.after_attach.append(then)
        self.slots.append(slot)
        self.query_one("#canvases").mount(canvas)
        if front or self._front is None:
            canvas.in_front = True
            self.show_front(slot)
        else:
            canvas.display = False
            # Never laid out, so no first resize starts the attach: it starts from the window now.
            self._start_attach(slot, *self.fit_size())
        self._mark_fleet_dirty()
        return slot

    def open_device_login(self, target: str, term: str, size: tuple[int, int], **kwargs: Any) -> Any:
        from uuid import uuid4

        from .device_login import DeviceLoginPane

        if self._refuse_while_signing_out():
            return None
        slot = self._add_slot({"id": f"pending-{uuid4().hex}", "target_id": target,
                               "state": "live", "owner": self.app.username}, defer_attach=True)
        pane = DeviceLoginPane(self, slot, target, term, size, **kwargs)
        slot.device_login = pane
        slot.device_gate = True
        self.query_one("#canvases").mount(pane)
        return pane

    def adopt_device_attachment(self, slot: SessionSlot, attachment: Any) -> None:
        if slot.closed:
            self._spawn(attachment.close())
            return
        slot.session_id = attachment.session["id"]
        slot.record = dict(attachment.session)
        slot.worker = self.run_worker(self._attach_and_pump(slot, *self.fit_size(), attachment=attachment),
                                      exclusive=False)
        self._mark_fleet_dirty()

    def request_device_size(self, slot: SessionSlot) -> None:
        slot.device_size_pending = True
        if slot.pump is not None and slot.pump.state.dump_seen:
            slot.device_size_pending = False
            state = slot.pump.state
            size = self.fit_size()
            if state.holds_keyboard and size != state.size_in_effect:
                self._spawn(slot.pump.resize(*size))

    def _run_then(self, slot: SessionSlot, then: Callable[[SessionSlot], Any]) -> None:
        """Run `then` once this session has attached: now, or when its `attached` frame has been applied."""
        if slot.pump is not None and slot.pump.state.client_id is not None:
            outcome = then(slot)
            if asyncio.iscoroutine(outcome):
                self.run_worker(outcome, exclusive=False)
        else:
            slot.after_attach.append(then)

    def show_front(self, slot: SessionSlot) -> None:
        """Bring `slot` to the front: paint it from its own grid at once, say whose keys these are. Nothing is
        sent to any session (D44; terminal-size §4: nothing the TUI opens or closes asks)."""
        previous = self._front
        if slot is not previous:
            self.close_scroll_view()  # the viewport goes back to the bottom while the session is still the one in front
        if self.view_open:
            self.close_fleet_view()  # a session came to the front: the operator wants to see it
        if slot is previous:
            self._mark_fleet_dirty()
            return
        if previous is not None:
            previous.canvas.send_to_back()
            previous.canvas.display = False
            if previous.device_login is not None:
                previous.device_login.display = False
                focused = self.focused
                if focused is not None and previous.device_login in focused.ancestors_with_self:
                    self.set_focus(None)  # a hidden pane gives up the keys held in its widgets
        self._front = slot
        slot.unseen = False
        slot.canvas.display = True
        slot.canvas.bring_to_front()
        if self._sidebar_has_keys():
            self.set_focus(None)  # a session came to the front: the keys go to it, and the sidebar stays as it is
        if slot.device_login is not None and slot.device_gate:
            slot.device_login.display = True  # the overlay shows over the canvas; a pane has no bring_to_front
            slot.device_login.take_front_focus()  # a login in front takes the keys its answers need
        self._sync_prompts()
        self._sync_dialogs()
        self._sync_alerts()
        self.refresh_status()
        self._mark_fleet_dirty()
        if previous is not None and previous.ended is not None:
            self._close_slot(previous)  # an ended session's chip stays until you move on (D36)
        if slot.ended is not None and not slot.ended_dialog_shown:
            self._show_end_dialog(slot)

    def switch_to_number(self, number: int) -> None:
        """`^\\ 1`-`^\\ 9`: the session on that rail chip. A chip that is not open here attaches (the gate first)."""
        assert self.fleet is not None
        live = self._live()
        stale, asks = self._attention()
        chips = self.fleet.chips(open_here=[s.session_id for s in self.slots], front=None, question_for_me=asks,
                                 stale_with_me=stale, live=live)
        chip = next((c for c in chips if c.number == number), None)
        if chip is None:
            self.app.notify(f"No session is numbered {number}.", title="not available", severity="warning",
                            markup=False)
            return
        self.bring_forward(chip.session_id)

    def bring_forward(self, session_id: str) -> None:
        """A chip or a sidebar row: the session comes to the front, attaching first when it is not open here."""
        slot = self.slot_for(session_id)
        if slot is not None:
            self.show_front(slot)
            return
        record = self._record_for(session_id)
        if record is None:
            self.app.notify(f"session {session_id} is not in the fleet any more", severity="warning",
                            markup=False)
            return
        if not is_attachable(record.get("state")):
            self.app.notify(f"session {session_id} is {record.get('state')} — cannot attach",
                            severity="warning", markup=False)
            return
        self.open_session(record)

    def _step(self, step: int) -> None:
        if len(self.slots) < 2 or self._front is None:
            return
        index = self.slots.index(self._front)
        self.show_front(self.slots[(index + step) % len(self.slots)])

    # -- Take back (D45) --

    def _take_back_reason(self, record: dict) -> str | None:
        """Why Take back does not apply to this session, or None: a session you own that another login types in."""
        me = self._me()
        if record.get("owner") != me:
            return "only the owner's clients can take the keyboard"
        holder = record.get("control_holder")
        if holder is None:
            return "nobody holds the keyboard"
        if holder.get("user") == me:
            return "you already hold the keyboard"
        return None

    def take_back_session(self, session_id: str) -> None:
        """`t` on a sidebar row: the session comes to the front (attaching first when it is not open here, the gate
        first), then `handoff` to this client's own (typing-ownership step 9)."""
        record = self._record_for(session_id)
        if record is None:
            return
        why = self._take_back_reason(record)
        if why is not None:
            self.app.notify(f"Take back: {why}.", title="not available", severity="warning", markup=False)
            return
        front = bool(self.app.cfg["watch.take_back_brings_to_front"])
        slot = self.slot_for(session_id)
        if slot is not None:
            if front:
                self.show_front(slot)
            self._run_then(slot, self._take_back)
            return
        if not is_attachable(record.get("state")):
            self.app.notify(f"session {session_id} is {record.get('state')} — cannot attach",
                            severity="warning", markup=False)
            return
        self.open_session(record, then=self._take_back, front=front)

    async def _take_back(self, slot: SessionSlot) -> None:
        pump = slot.pump
        if pump is None:
            return
        why = A.blocked("take_back", A.Facts(pump.state, open_sessions=len(self.slots)))
        if why is not None:
            self.app.notify(f"Take back: {why}.", title="not available", severity="warning", markup=False)
            return
        try:
            await pump.handoff(pump.state.client_id)
        except ConnectionClosed:
            pass  # the pump's read loop sees the close and handles it

    # -- the rail and the sidebar: clicks and keys --

    def on_rail_activated(self, message: Rail.Activated) -> None:
        message.stop()
        action = message.action
        if action == ACT_MENU:
            self.run_worker(self.run_action_id("menu"), exclusive=False)
        elif action in (ACT_FLEET, ACT_MORE):
            self.open_sidebar()
        elif action == ACT_NEEDS:
            self.open_sidebar(GROUP_NEEDS_YOU)
        elif action == ACT_ALERTS:
            self.open_fleet_view(TAB_ALERTS)
        elif action == ACT_CHIP and message.arg is not None:
            self.bring_forward(message.arg)
        elif action == ACT_NEW:
            self.run_worker(self.run_action_id("new_session"), exclusive=False)

    def on_sidebar_command(self, message: Sidebar.Command) -> None:
        message.stop()
        self._sidebar_command(message.name, message.arg)

    def _sidebar_command(self, name: str, arg: str | None = None) -> None:
        sidebar = self.sidebar
        assert sidebar is not None and self.fleet is not None
        sid = arg or sidebar.selected_session
        if name == CMD_CLOSE:
            self.close_sidebar()
        elif name == CMD_PICK and sid is not None:
            self.bring_forward(sid)
            self._after_pick()
        elif name == CMD_TAKE_BACK and sid is not None:
            self.take_back_session(sid)
            self._after_pick()
        elif name == CMD_WHOS_HERE and sid is not None:
            if self.slot_for(sid) is None:
                self.app.notify("Who's here: open the session first (Enter).", title="not available",
                                severity="warning", markup=False)
                return
            self.bring_forward(sid)
            self._after_pick()
            self.run_worker(self.run_action_id("who"), exclusive=False)
        elif name == CMD_MODE:
            self.fleet.filter_mode = arg if arg in ("mine", "all") else (
                "all" if self.fleet.filter_mode == "mine" else "mine")
            self._mark_fleet_dirty()
        elif name == CMD_FULL_VIEW:
            self.open_fleet_view()
        elif name == CMD_END and sid is not None:
            self.end_session(sid)
        elif name == CMD_NEW:
            self.run_worker(self.run_action_id("new_session"), exclusive=False)
        elif name == CMD_ALERTS:
            self.open_fleet_view(TAB_ALERTS)
        elif name == CMD_FILTER:
            sidebar.start_filter()
            sidebar.focus()

    # -- attach --

    def on_canvas_view_changed(self, event: Canvas.ViewChanged) -> None:
        event.stop()
        if self._front is not None and event.canvas is self._front.canvas:
            self.refresh_status()

    def on_canvas_resized(self, event: Canvas.Resized) -> None:
        event.stop()
        slot = self.slot_of(event.canvas)
        if slot is None:
            return
        if slot is self._front:
            self.refresh_status()
        if slot.attach_started or event.size.width <= 0:
            return
        # The canvas's size goes out in the `attach` frame as information only (DT16): no `resize`
        # follows it, so attaching never changes anyone's grid (session-attach R5, CR4).
        self._start_attach(slot, max(event.size.width, 1), max(event.size.height, 1))

    def _start_attach(self, slot: SessionSlot, cols: int, rows: int) -> None:
        if slot.attach_started or slot.closed:
            return
        slot.attach_started = True
        slot.worker = self.run_worker(self._attach_and_pump(slot, cols, rows), exclusive=False)

    async def _attach_and_pump(self, slot: SessionSlot, cols: int, rows: int, *, attachment: Any = None) -> None:
        cfg = self.app.cfg
        pump = AttachmentPump(
            self.app.client, slot.session_id, sink=functools.partial(self._sink, slot),
            state=AttachmentState(
                session_id=slot.session_id, user=self.app.username,
                event_log=EventLog.from_config(cfg, functools.partial(self.app.log_added, slot.session_id))),
            hooks=PumpHooks(on_event=functools.partial(self._on_event, slot),
                            on_reattach=functools.partial(self._on_reattach, slot),
                            on_backoff=functools.partial(self._on_backoff, slot),
                            on_attached=functools.partial(self._on_attached, slot),
                            on_run_done=functools.partial(self._on_run_done, slot),
                            on_login_needed=functools.partial(self._on_login_needed, slot)),
            backoff_ms=tuple(int(step) for step in cfg["reattach.backoff_ms"]),
            cols=cols, rows=rows, auto=cfg["reattach.auto"])
        slot.pump = pump
        try:
            try:
                await pump.start(attachment)
            except AttachRefused as exc:
                self._leave(slot, f"attach refused: {exc.reason or 'close ' + str(exc.code)}", cancel=False)
                return
            except ApiError as exc:
                # Every ApiError here — the ticket mint's own call, since async_attach runs it off
                # the loop — gets the same toast every other screen's API errors get, including the
                # 401-routes-to-Login rule (`notify_api_error`): a 401 anywhere sends the operator
                # back to Login, not just the ones that happen to originate from a screen's own
                # `call_api`.
                if slot.leaving:
                    return
                slot.leaving = True
                slot.left = str(exc)
                self.app.notify_api_error(exc)
                if exc.status != 401:
                    self._close_slot(slot, cancel=False)
                return
            except (HttpError, OSError, HandshakeError, ProtocolError) as exc:
                self._leave(slot, f"could not attach: {exc}", cancel=False)
                return
            self._sync_size(slot)
            self._sync_typist(slot)
            if slot.recording_bar and (pump.state.recording or {}).get("active"):
                slot.bar_until = self.app.wall_clock() + cfg["gates.recording_bar_s"]
            self._after_attached(slot)
            # The pump reads, applies frames and reattaches with backoff (DT13, D26); it returns
            # only when the session is over for this client.
            await pump.wait_closed()
            if not slot.leaving and pump.state.close is not None:
                self._show_end(slot, pump.state)
        finally:
            # The pump's own task is the one reader of the socket; an explicit detach cancels this
            # worker and lands here, so the socket closes without a second reader racing it.
            await pump.stop()

    def _after_attached(self, slot: SessionSlot) -> None:
        if slot is self._front:
            self._sync_prompts()
            self.refresh_status()
        self._mark_fleet_dirty()
        waiting, slot.after_attach = slot.after_attach, []
        for then in waiting:
            self._run_then(slot, then)

    # -- the pump's callbacks, each for the session it belongs to --

    def _sink(self, slot: SessionSlot, kind: str, data: bytes) -> list[bytes] | None:
        """Session bytes into the grid, on the attachment's feed thread (DT20). A `dump` resets the
        grid at the `prefix` geometry; output follows the size in effect. This half must not touch
        Textual or the state: it returns the engine's replies for the processor to deliver on the
        loop (`_on_run_done`). The dump geometry is read here from the state the loop set when it
        applied `prefix`, which cannot change while the processor is blocked on this run."""
        if self._unmounted or slot.closed or slot.pump is None:
            return None
        state = slot.pump.state
        canvas = slot.canvas
        if kind == "dump":
            cols = state.dump_cols or state.size_cols or canvas.backend.cols
            rows = state.dump_rows or state.size_rows or canvas.backend.rows
            return canvas.feed_run("dump", data, cols=cols, rows=rows)
        return canvas.feed_run("output", data)

    async def _on_run_done(self, slot: SessionSlot, state: AttachmentState, role: str,
                           data: bytes, replies: list[bytes]) -> None:
        """The loop-side half of a run (DT20), before the next frame is applied. A dump rebuilt the
        grid at the prefix geometry, so the canvas's row caches reset and the grid takes the latest
        size in effect (session-attach R13); output marks a background chip unseen. The engine's
        replies are delivered now, under this attachment's own DT4 (b) state, so a reply produced by
        byte *k* is judged against the state as of byte *k* (DT4 b)."""
        if self._unmounted or slot.closed:
            return
        canvas = slot.canvas
        if role == "dump":
            canvas.after_run("dump")
            slot.awaiting_dump = False
            self._sync_size(slot)
            if slot.device_size_pending and not slot.device_gate:
                self.request_device_size(slot)
            if self._scroll_mode and slot is self._front:
                self._check_selection()
        else:
            canvas.after_run("output")
            self._sync_size(slot)
            if self._scroll_mode and slot is self._front:
                if canvas.backend.active_screen() == G.SCREEN_ALTERNATE:
                    self.close_scroll_view()  # a full-screen program has no scrollback, and wants its keys
                else:
                    self._check_selection()
                    self.refresh_status()  # the rows held, and so how far back the view is, changed
            if slot is not self._front and not slot.unseen:
                slot.unseen = True
                self._mark_fleet_dirty()
        for reply in replies:
            # Await the write, so the reply leaves before the next frame is applied and DT4 (b) judges
            # it against the state as of the bytes that produced it (DT20).
            await self.send_input(SOURCE_REPLY, reply, slot)

    def _on_event(self, slot: SessionSlot, state: AttachmentState, message: object) -> None:
        if self._unmounted or slot.closed:
            return
        kind = message.get("t") if isinstance(message, dict) else None
        if kind == "prefix":
            slot.awaiting_dump = True
        if kind in ("token", "attached"):
            slot.keys_not_sent = False
        front = slot is self._front
        if kind == "token" and front:
            self._tell_keyboard_moved(slot, state)
        self._sync_size(slot)
        self._sync_typist(slot)
        if kind is None:
            return  # output bytes change nothing a dialog or the rail shows
        if front:
            self._toast_for(slot, kind, state, message)
            self._sync_prompts()
            self._sync_dialogs()
            self.refresh_status()
        elif kind == "notice":
            slot.was_ending = state.ending
        self._mark_fleet_dirty()

    # -- toasts: every state change says so once (D22); the status row keeps what persists --

    def _toast(self, text: str, title: str, severity: str = "information") -> None:
        self.app.toast(text, title=title, severity=severity)

    def _target(self, slot: SessionSlot | None = None) -> str:
        slot = slot or self._front
        if slot is None:
            return self.app.initial_target or ""
        return slot.target

    def _toast_for(self, slot: SessionSlot, kind: str, state: AttachmentState, message: dict) -> None:
        if kind == "stale":
            if state.stale_state == "pending":
                self._toast("The screen may be wrong: output was lost.", "stale", "warning")
            elif state.stale_state == "chosen":
                by = (message.get("by") or {}).get("user") or "someone"
                what = _STALE_CHOSEN.get(state.stale_choice or "", state.stale_choice or "a choice")
                self._toast(f"{by} chose to {what}.", "stale")
        elif kind == "quiet_prompt":
            self._toast(f"{self._target(slot)} asks: {message.get('text', 'Kill the session?')}", "question",
                        "warning")
        elif kind == "notice":
            if state.ending and not slot.was_ending:
                self._toast("This session is in a warning window: see the status row.", "ending",
                            "warning")
            elif not state.ending and slot.was_ending:
                self._toast("The warning was withdrawn.", "ending")
            slot.was_ending = state.ending
        elif kind == "data_path":
            if state.data_path == "lost":
                self._toast("The data path was lost; input is refused for now.", "data path", "warning")
            elif state.data_path == "restored":
                self._toast("The data path is restored.", "data path")
        elif kind == "input_refused":
            reason = state.last_input_refused
            text = REFUSAL_TEXT.get(reason, "input was refused").lstrip("◐◌ ")
            self._toast(text[:1].upper() + text[1:] + ".", "refused", "warning")

    def _on_attached(self, slot: SessionSlot, state: AttachmentState, attempt: object) -> None:
        if self._unmounted or slot.closed:
            return
        slot.was_ending = state.ending
        slot.input_notice = ""
        if slot is self._front:
            if isinstance(attempt, int) and attempt > 1:
                self._toast("Reattached.", "session")
            self._sync_prompts()
            self.refresh_status()
        self._mark_fleet_dirty()

    def _on_login_needed(self, slot: SessionSlot) -> None:
        """A 401 on the reattach path (DT13): the same rule as the first attach's 401 above, told through
        `notify_api_error` and routed to Login. The slot is marked leaving here because the pump ends with close
        4003 straight after this hook, and the end dialog must not be put on top of the login page."""
        if self._unmounted or slot.leaving:
            return
        exc = ApiError(401, "unauthenticated", REATTACH_LOGIN_GONE)
        slot.leaving = True
        slot.left = str(exc)
        self.app.notify_api_error(exc)

    def _on_backoff(self, slot: SessionSlot, state: AttachmentState, attempt: object) -> None:
        if self._unmounted or slot.closed:
            return
        if slot is self._front:
            if attempt == 1:
                self._toast("The connection was lost; reattaching. Esc stops.", "session", "warning")
            self.refresh_status()
        self._mark_fleet_dirty()

    def _tick(self) -> None:
        """Once a second the row is read again: the countdown, the reattach wait and the recording text all move
        with the clock. The sidebar's clocks move by re-laying out the right-hand fields of the rows it paints;
        its model is rebuilt only when the records changed since the last build. The full view formats every row
        of its model, so while it is open its own model is rebuilt at the new clock."""
        self.refresh_status()
        if self._fleet_dirty:
            self._sync_fleet_widgets()
            return
        self._refresh_painted_clocks()
        self._refresh_view_clocks()

    def _refresh_view_clocks(self) -> None:
        """The full view's clocks: its model formats every row it lists, so the only way to move them is to rebuild
        that model at the new clock. Only while the view is open; a closed view is never rebuilt by a tick."""
        if self._unmounted or self.fleet is None or self.fleet_view is None or not self.view_open:
            return
        self.fleet.set_own_user(self._me())
        live = self._live()
        stale, asks = self._attention()
        open_ids = [slot.session_id for slot in self.slots]
        scan = self.fleet.scan(open_here=open_ids, stale_with_me=stale, question_for_me=asks, live=live)
        self.fleet_view.set_model(self._fleet_view_model(live, scan.needs_you, stale, asks))

    def _refresh_painted_clocks(self) -> None:
        """The clocks of the rows the sidebar paints, read from the records at the new `now`. No entry is built
        and no model is rebuilt here: a tick changes rows with a clock, not 14,000 entries."""
        sidebar = self.sidebar
        if self._unmounted or sidebar is None or not self.sidebar_open or self.fleet is None:
            return
        rows = sidebar.view.rows
        if not rows:
            return
        merged = {**self.fleet.records, **self._live()}
        stale, asks = self._attention()
        now = self.app.wall_clock()
        times = {(group, sid): self._time_state(merged[sid], sid, stale, asks, now)
                 for group, sid in rows if sid in merged}
        if sidebar.refresh_times(times):
            # A painted row's right-hand fields changed width (a clock that grew, a REC that no longer fits), so
            # that row's login width is decided with them and only a rebuild paints it. The tick marks the model
            # dirty; the coalesced rebuild runs at the next refresh of the screen.
            self._mark_fleet_dirty()

    def _tell_keyboard_moved(self, slot: SessionSlot, state: AttachmentState) -> None:
        """A toast when the keyboard comes to this client or leaves it (D22; Frontend §9). The state
        already holds the move, with the holder before it."""
        move = state.token_history[-1]
        before = move["previous"]
        had = before is not None and before.get("client_id") == state.client_id
        has = state.holds_keyboard
        if has and not had and move["reason"] == "takeover":
            self._toast_took(slot, before)
            return
        if has and not had:
            text = KEYBOARD_GAINED.get(move["reason"], "You now hold the keyboard.")
        elif had and not has:
            holder = state.holder
            who = "nobody" if holder is None else str(holder.get("user") or holder.get("client_id"))
            if holder is None:
                text = "The keyboard was put aside."
            elif move["reason"] == "takeover":
                text = f"{who} took the keyboard (owner)."
            else:
                text = f"{who} now holds the keyboard."
        else:
            return
        self.app.notify(text, title="keyboard", markup=False)

    def _toast_took(self, slot: SessionSlot, before: dict | None) -> None:
        """This client took the keyboard (Take back, Take it, or typing as the owner): the row says it, and the
        toast says whose it was and how to give it back (mockup A1)."""
        ids = [s.session_id for s in self.slots] or [slot.session_id]
        label = display_id(slot.session_id, ids)
        who = None if before is None else str(before.get("user") or before.get("client_id"))
        sentence = ("You took the keyboard." if who is None else
                    f"You took the keyboard from {who}. Its input is refused from now.")
        self.app.toast(f"{slot.target} · {label}\n{sentence} To give it back: Who's here, then Hand to selected.",
                       title="keyboard · you took it")

    def _on_reattach(self, slot: SessionSlot, state: AttachmentState, step: object) -> None:
        # A new attachment starts clean: whatever dump the dropped one was owed will never arrive.
        slot.awaiting_dump = False
        if slot is self._front:
            self.refresh_status()

    # -- the prompts and alerts over the canvas --

    def _stale_wanted(self) -> bool:
        """The stale dialog is the keyboard holder's alone (stale-screen step 4); a token move voids it."""
        state = self.pump.state if self.pump is not None else None
        return (self.ended is None and state is not None and not state.closed
                and state.stale_state == "pending" and state.stale_prompt is not None
                and state.holds_keyboard)

    def _sync_prompts(self) -> None:
        if self._unmounted or self._stale_panel is None or self._stale_banner is None:
            return
        panel, layer, box = self._stale_panel, self._stale_layer, self._quiet_box
        slot = self._front
        state = slot.pump.state if slot is not None and slot.pump is not None else None
        if state is not None and self._stale_wanted():
            prompt = state.stale_prompt
            if panel.prompt_id != prompt["prompt_id"]:
                panel.open_for(state, prompt, self._target(), self.app.cfg["prompts.input_guard_ms"])
                slot.refusals_seen = state.refusal_count
            elif state.refusal_count != slot.refusals_seen:
                slot.refusals_seen = state.refusal_count
                refused = state.last_request_refused or {}
                if refused.get("request") == "stale_answer":
                    panel.refused(str(refused.get("reason")))
            layer.display = True
        else:
            layer.display = False
            panel.prompt_id = None
        # The notice is every attached client's, not the holder's alone (stale-screen rule 18).
        self._stale_banner.display = (state is not None and self.ended is None and not state.closed
                                      and state.stale_state == "pending")
        asked = state.quiet_prompt if state is not None and self.ended is None and not state.closed else None
        if asked is not None:
            holder = None if state.holds_keyboard or state.holder is None else state.holder.get("user")
            box.show_for(self._target(), asked, holder and str(holder),
                         A.blocked("take_back", self.facts()) is None)
        box.display = asked is not None

    def _sync_alerts(self) -> None:
        if self._unmounted or self._alert_stack is None or not self._alert_stack.is_attached:
            return
        model = self.app.alerts
        shown = ([a for a in reversed(list(model.active.values())) if a.alert_id not in model.dismissed]
                 if model.popup and model.allowed else [])
        self._alert_stack.show(shown)

    def stale_active(self) -> bool:
        return self._stale_layer is not None and bool(self._stale_layer.display)

    async def answer_stale(self, choice: str) -> None:
        """The holder's choice, sent with the outstanding `prompt_id` (stale-screen step 11). Choosing
        `fresh_session` also opens New session on this target; the old session is left running (rule 14)."""
        panel, pump = self._stale_panel, self.pump
        if panel is None or pump is None or panel.answered or panel.prompt_id is None:
            return
        panel.answered = True
        try:
            await pump.answer_stale(panel.prompt_id, choice)
        except ConnectionClosed:
            return  # the pump's read loop sees the close and handles it
        if choice == "fresh_session":
            slot = self._front
            target = self._target()
            self._leave(slot, "detached — the session lives on", warn=False)
            self._new_session_dialog(target)

    async def answer_quiet(self, kill: bool) -> None:
        """Answer the question with its own `prompt_id` (quiet-prompt rule 9)."""
        pump = self.pump
        if pump is None or pump.state.quiet_prompt is None:
            return
        try:
            await pump.answer_quiet_prompt(pump.state.quiet_prompt["prompt_id"], kill)
        except ConnectionClosed:
            return
        pump.state.quiet_answered()
        self._sync_prompts()
        self._sync_dialogs()
        self.refresh_status()
        self._mark_fleet_dirty()

    def ask_kill(self) -> None:
        def decided(kill: bool | None) -> None:
            if kill:
                self.run_worker(self.answer_quiet(True), exclusive=False)
            elif kill is False:
                self.run_worker(self.answer_quiet(False), exclusive=False)

        self.app.push_screen(ConfirmKillScreen(self, self._target()), decided)

    # -- the end of a session for this client (F10, D36) --

    def _show_end(self, slot: SessionSlot, state: AttachmentState) -> None:
        """The pump stopped for good. Reattaching that was stopped leaves the screen as it is, with its row;
        anything else is a state the operator reads in a dialog, over the last screen. A session that was not in
        front shows ✕ on its chip and its dialog when it comes to the front."""
        if state.reattach_stopped:
            if slot is self._front:
                self.refresh_status()
            return
        view = ended_view(state, self._target(slot))
        if view is None:
            return
        slot.ended = view
        slot.canvas.dimmed = view.ended
        if slot is self._front:
            self.close_scroll_view()
            self._sync_prompts()
            self._show_end_dialog(slot)
        self._mark_fleet_dirty()

    def _show_end_dialog(self, slot: SessionSlot) -> None:
        if self.app.screen_lock.locked:
            # The lock refuses a pop, so the screens over the workspace cannot be cleared for the dialog now: it waits
            # for the unlock and is shown after a re-check of the slot (it may have been closed, or left the front).
            self.app.screen_lock.after_unlock(lambda: self._show_end_dialog_after_lock(slot))
            return
        slot.ended_dialog_shown = True
        while self.app.screen is not self and len(self.app.screen_stack) > 1:
            self.app.pop_screen()
        self._toast(slot.ended.title, "session", "warning")
        self.app.push_screen(EndedScreen(self, slot.ended, self._target(slot)))
        self.refresh_status()

    def _show_end_dialog_after_lock(self, slot: SessionSlot) -> None:
        if (self.is_attached and self.app.workspace() is self and slot is self._front and not slot.closed
                and slot.ended is not None and not slot.ended_dialog_shown):
            self._show_end_dialog(slot)

    def _sync_size(self, slot: SessionSlot) -> None:
        """The grid has the session's size: from `attached`, `prefix` and `size`, never the window's (D3)."""
        if slot.closed or slot.pump is None or slot.awaiting_dump:
            return
        cols, rows = slot.pump.state.size_in_effect
        if cols and rows:
            canvas = slot.canvas
            before = (canvas.backend.cols, canvas.backend.rows)
            canvas.resize_grid(cols, rows)
            if slot is self._front and (canvas.backend.cols, canvas.backend.rows) != before:
                if self._scroll_mode:
                    # The engine moved its viewport with the grid: the view is no longer on the rows it showed.
                    self.close_scroll_view()
                    self._toast("The size changed; back to live.", "scroll")
                self.refresh_status()

    def _sync_typist(self, slot: SessionSlot) -> None:
        if slot.pump is not None and not slot.closed:
            slot.canvas.typist = slot.pump.state.holds_keyboard

    # -- the window changed: at most one request, debounced (D18, terminal-size §4) --

    def window_changed(self, size: Size) -> None:
        """Called by the app on an *outer* window change, and only then: a widget's own resize, a
        dialog opening, a token move or the sidebar opening never reach here (D18, D44)."""
        cfg = self.app.cfg
        # The size the app reports is the one the event carries; `app.size` may not have caught up when
        # this runs, and the dialog must show the size it will ask for.
        self._window = size
        self._chrome_width = size.width
        self._layout_chrome()
        if self.pump is not None:
            # A clamped answer was final only until now (terminal-size §3).
            self.pump.state.window_changed()
        self._sync_dialogs()
        self.refresh_status()
        if not cfg["size.auto_request"] or self.pump is None:
            return
        if self._auto_timer is not None:
            self._auto_timer.stop()
        # Bind the request to the session in front now: if another comes to the front before it
        # fires, it is dropped (bringing a session to the front asks for nothing, D44).
        self._auto_slot = self._front
        self._auto_timer = self.set_timer(cfg["size.debounce_ms"] / 1000.0, self._fire_auto_request)

    async def _fire_auto_request(self) -> None:
        """Ask for the window minus the rail and the status row, once, if this client still types.
        Whatever the session answers — applied, or clamped to its own bounds — is final until the
        window changes again: nothing here re-asks after an answer (terminal-size §4)."""
        slot, self._auto_slot = self._auto_slot, None
        if slot is None or slot is not self._front:
            # Another session came to the front since the window changed: the request it owes is
            # dropped, never sent to the session now in front (§4: bringing one forward asks nothing).
            return
        pump = slot.pump
        if slot.device_gate:
            return
        if self._unmounted or pump is None or pump.state.closed or not pump.state.holds_keyboard:
            return
        cols, rows = self.fit_size()
        try:
            # timeout=0 (DT17): the `size` answer comes back through the pump, not through a read here.
            await pump.resize(cols, rows, timeout=0)
        except ConnectionClosed:
            pass  # the pump's read loop sees the close and handles it

    def fit_size(self) -> tuple[int, int]:
        """The window minus the rail and the status row: what an explicit or an automatic request asks for, and
        what a new session is created with when auto size is on (terminal-size §4: session open carries the
        creator's size). The sidebar is not subtracted: opening it asks for nothing (D44)."""
        window = self._window or self.app.size
        cfg = self.app.cfg
        return (max(window.width - cfg["rail.width"], 1),
                max(window.height - (STATUS_ROWS if cfg["status.show"] else 0), 1))

    @property
    def auto_size_on(self) -> bool:
        return bool(self.app.cfg["size.auto_request"])

    def toggle_auto_size(self) -> bool:
        """The operator's switch, for this run only (D18). Switching it on asks for nothing: the next
        window change does."""
        self.app.cfg.set_runtime("size.auto_request", not self.auto_size_on)
        self._sync_dialogs()
        self.refresh_status()
        return self.auto_size_on

    async def ask_size(self) -> str | None:
        """An explicit request for the window's size, labelled as one (terminal-size §4). None when
        sent; else why it was not. The answer arrives through the pump (DT17)."""
        why = A.blocked("ask_size", self.facts())
        if why is not None:
            return why
        cols, rows = self.fit_size()
        try:
            await self.pump.resize(cols, rows, timeout=0)
        except ConnectionClosed:
            # The socket closed between the typist check and the send: not sent, and say so, as the
            # automatic path treats it, rather than letting the exception escape a dialog or drive.
            return "the session's connection closed"
        return None

    # -- leaving a session (it lives on), and taking one off the screen --

    def _release_slot(self, slot: SessionSlot, *, cancel: bool = True) -> None:
        """Nothing the engine says after this is answered: the feed thread and the reply callback go
        before the grid does (DT20). A feed slice still running finishes under the backend lock, and the
        engine is freed only after it, so nothing ever writes to a freed terminal. `cancel` stops the
        attach worker too; a worker that is releasing its own slot is already on its way out."""
        if slot.closed:
            return
        if slot is self._front:
            self.close_scroll_view()
        slot.closed = True
        if slot.device_login is not None:
            slot.device_login.dispose()
            slot.device_login.remove()
        if cancel and slot.worker is not None:
            slot.worker.cancel()
        if slot.pump is not None:
            # The pump's feed thread (DT20): shut it down before the grid is freed. A worker already
            # inside `stop` has shut it down, which is harmless.
            slot.pump.shutdown_feed()
        slot.canvas.backend.set_reply_callback(None)
        slot.canvas.close()

    def _close_slot(self, slot: SessionSlot, *, cancel: bool = True) -> None:
        """Stop this session's attachment and take it off the screen. The session lives on. When it was in front,
        the next session open comes to the front; with none left the sidebar opens (`sidebar.open_when_no_session`)."""
        if slot not in self.slots:
            return
        index = self.slots.index(slot)
        self.slots.remove(slot)
        slot.leaving = True
        self._release_slot(slot, cancel=cancel)
        slot.canvas.remove()
        self.app.tapped(slot.session_id, TAP_SLOT_CLOSED, slot)
        if slot is self._front:
            self._front = None
            if self.slots:
                self.show_front(self.slots[min(index, len(self.slots) - 1)])
            else:
                self._sync_prompts()
                if self.app.cfg["sidebar.open_when_no_session"]:
                    self.open_sidebar()
                self.refresh_status()
        self._mark_fleet_dirty()

    def _leave(self, slot: SessionSlot | None, message: str, *, warn: bool = True, cancel: bool = True) -> None:
        """The session could not be kept (an attach that failed) or is being left: say so, and take it off
        the screen. It lives on in the broker."""
        if slot is None or slot.leaving:
            return
        slot.leaving = True
        slot.left = message
        self.app.notify(message, title="session", severity="warning" if warn else "information", markup=False)
        self._close_slot(slot, cancel=cancel)

    def action_detach(self) -> None:
        """Leave the session in front, which lives on."""
        self._leave(self._front, "detached — the session lives on")

    async def detach_all(self) -> list[str]:
        """Leave every session open here (each lives on) and return their ids, in the order they were opened, only
        once every attach worker has finished: its `finally` is what closes the session's WebSocket, so by then the
        close has been sent for each of them (sign out's order: the detaches first, then `logout()`). No toast per
        session: the caller says one thing."""
        detached: list[str] = []
        # Until no slot remains: a slot admitted before sign-out's gate closed (`_refuse_while_signing_out`) may
        # still appear while a worker is awaited, and each pass leaves it too.
        while self.slots:
            slots = list(self.slots)
            workers = [slot.worker for slot in slots if slot.worker is not None]
            for slot in slots:
                slot.leaving = True
                slot.left = "signed out — the session lives on"
                self._close_slot(slot)
            detached.extend(slot.session_id for slot in slots)
            for worker in workers:
                with contextlib.suppress(Exception):  # a cancelled worker raises `WorkerCancelled`: that is the detach
                    await worker.wait()
        return detached

    # -- input: one path out (DT4) --

    async def send_input(self, source: str, data: bytes, slot: SessionSlot | None = None,
                         *, drive_gate: object | None = None) -> bool:
        """The one way bytes leave this screen for a session (the one in front unless `slot` says another). True
        when they were handed to the socket; False when DT4 held them back (typed input under the guard, derived
        input outside its conditions) or there is nothing to send to. The decision is made here, immediately
        before the write, against the state as it is now: a hand-off sent a moment ago has already set
        `handoff_in_flight`, so a reply that was waiting behind it is dropped, not sent as the keyboard
        moves away."""
        if self.app.under_drive and source in _TYPED and drive_gate is not _DRIVE_GATE:
            self.dropped_inputs += 1
            return False
        slot = slot or self._front
        pump = slot.pump if slot is not None else None
        if not data or pump is None or self._unmounted or slot.closed:
            return False
        if slot.device_gate:
            return False
        state = pump.state
        if state.client_id is None or state.closed:
            self._drop_unattached_input(source, data, slot, "not_attached")
            return False  # not attached (yet, or any more): there is no one to send to
        if self._scroll_mode and slot is self._front and source != SOURCE_REPLY:
            # The viewport is scrolled: no key, paste, mouse or focus report leaves for the session until the view is
            # closed. Dropped, never held. The engine's answers to the session's own queries are computed from the
            # active area, not the viewport, and a program waiting for one must not hang: they go as when live.
            self.dropped_inputs += 1
            state.log.add(KIND_INPUT_DROPPED, time.monotonic(), source=source, bytes=len(data),
                          reason=SCROLL_DROPPED)
            if source in _TYPED:
                self._scroll_notice = SCROLL_DROPPED_NOTE
                self.refresh_status()
            return False
        if source in _TYPED:
            if not state.may_type and self.app.cfg["input.guard"]:
                self.suppressed_inputs += 1
                state.log.add(KIND_INPUT_SUPPRESSED, time.monotonic(), source=source, bytes=len(data))
                if not slot.keys_not_sent:
                    slot.keys_not_sent = True
                    if slot is self._front:
                        self.refresh_status()
                return False
        else:
            reason = self._derived_input_blocked(slot, state)
            if reason is not None:
                self.dropped_inputs += 1
                state.log.add(KIND_INPUT_DROPPED, time.monotonic(), source=source, bytes=len(data),
                              reason=reason)
                return False
        if source in _TYPED and slot.mark_input:
            slot.canvas.backend.set_mark(MARK_INPUT)  # before the write: the echo may be read before it returns
        try:
            await pump.send(data)
        except ConnectionClosed:
            self._drop_unattached_input(source, data, slot, "connection_closed")
            return False  # the pump's read loop sees the close and handles it
        if source in _TYPED and state.refusal_active and state.refusal_at is not None and (
                state.now() - state.refusal_at >= self.app.cfg["toasts.seconds"]):
            # A refusal stays until a later input goes unrefused (G11): if the core refuses this one
            # too, its `input_refused` puts the line back at once.
            state.clear_refusal()
            if slot is self._front:
                self.refresh_status()
        return True

    def _drop_unattached_input(self, source: str, data: bytes, slot: SessionSlot, reason: str) -> None:
        if source not in _TYPED:
            return
        self.dropped_inputs += 1
        slot.pump.state.log.add(KIND_INPUT_DROPPED, time.monotonic(), source=source, bytes=len(data), reason=reason)
        slot.input_notice = RECONNECT_DROPPED_NOTE
        if slot is self._front:
            self.refresh_status()

    def _derived_input_blocked(self, slot: SessionSlot, state: AttachmentState) -> str | None:
        """Why derived input may not go out now (DT4 b), or None when it may. Not the guard's business:
        the harness profile may switch the guard off, and still a client that is only watching must
        never answer a query, report a focus or click, because that would take the keyboard from its
        holder (terminal-size §5: the owner takes it "by typing") or arm a false quiet prompt (session
        model §9, §11: any input from the holder counts as E1)."""
        if not state.holds_keyboard:
            return "not_holder"
        if state.handoff_in_flight:
            return "handoff_in_flight"
        if state.stale_state == "pending":
            return "stale_pending"
        if slot.awaiting_dump:
            return "awaiting_dump"
        return None

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run a write from a callback that cannot await. Tasks start in the order they were made and
        the socket's send lock is fair, so replies leave in the order the session asked."""
        task = asyncio.get_running_loop().create_task(coro)
        self._writes.add(task)
        task.add_done_callback(self._write_done)

    def _write_done(self, task: asyncio.Task[None]) -> None:
        self._writes.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            asyncio.get_running_loop().call_exception_handler(
                {"message": "an input write failed", "exception": error, "task": task})

    def _on_reply(self, slot: SessionSlot, data: bytes) -> None:
        """The engine answered a query the session asked (a status report, the device attributes): the answer goes
        to the session that asked, under that attachment's own DT4 (b) state."""
        self._spawn(self.send_input(SOURCE_REPLY, data, slot))

    def _on_outer_focus(self, focused: bool) -> None:
        if (self._unmounted or self.canvas is None or self.pump is None
                or not self.app.cfg["input.focus_reports"]):
            return
        data = encode_focus(self.backend, focused)  # empty unless the session set mode 1004
        if data:
            self._spawn(self.send_input(SOURCE_FOCUS, data))

    async def on_key(self, event: events.Key) -> None:
        """Every key reaches the session (D17), encoded for the session's modes (D34), except the leader.
        The leader (`keys.leader`, Ctrl-\\ by default) is the one key this screen takes: `^\\` then a
        letter runs that action, `^\\` then a digit switches to that chip's session; `^\\` and nothing within
        `menu.open_on_leader_after_ms` opens the Menu; `^\\ ^\\` sends the key itself, as a typed key under the
        guard. A key after the leader that names no action is not taken (D17): it goes to the session as an
        ordinary key."""
        leader = event.key == self.app.cfg["keys.leader"]
        if self.pump is None and not leader and not self._leader_pending:
            return  # nothing to send to yet: the key goes on to the app (Ctrl-Q quits while attaching)
        if (event.key == "escape" and self.pump is not None and self.pump.state.reattach is not None
                and not self._leader_pending and not self._scroll_mode):
            event.stop()
            self.pump.stop_reattach()  # Esc stops the reattaching: no further attempt (F12)
            return
        event.stop()
        if leader:
            self._end_pan()
            self.close_scroll_view()
            if self._leader_pending:
                self._cancel_leader()
                await self._send_key(event)
            else:
                self._start_leader()
            return
        if self._chord is not None:
            # The second character of a two-character key (`:q`): it runs the action, or the chord is dropped. The
            # operator typed `^\ :` on purpose, so nothing of it goes to the session.
            first, self._chord = self._chord, None
            self.refresh_status()
            action = (A.action_for_chord(first, event.character, self.app.cfg.leader_overrides())
                      if event.is_printable and event.character else None)
            if action is not None:
                await self.run_action_id(action.id)
            return
        if self._leader_pending:
            self._cancel_leader()
            letter = event.character if event.is_printable else None
            if letter is not None and letter in A.chord_starts(self.app.cfg.leader_overrides()):
                self._chord = letter
                self.refresh_status()
                return
            action = A.action_for_leader_key(letter, self.app.cfg.leader_overrides()) if letter else None
            if action is not None:
                await self.run_action_id(action.id)
                return
            if letter is not None and letter in "123456789":
                self.switch_to_number(int(letter))
                return
        if self._scroll_mode:
            self._scroll_key(event)  # the view's, or dropped: no key reaches the session while the view is open
            return
        if self._pan_mode:
            if self._pan_key(event.key):
                return
            self._end_pan()  # any other key ends Pan and is the session's, as ever (D17)
        if self._front is None:
            return
        if self.ended is not None:
            # Nothing is left to type into; `c` starts a new session on the same target (F10), `n` goes to the
            # next session open here.
            if event.character == "c" and not self.dialogs:
                await self.run_action_id("new_session")
            elif event.character == "n" and not self.dialogs:
                await self.run_action_id("next")
            else:
                await self._send_key(event)
            return
        if self.stale_active():
            # Only a digit that names a choice is taken, and only after the guard (F7, D23): a keystroke
            # aimed at the session must not choose by accident. Every other key goes to the session as ever
            # (D17), where the core refuses it and the row says why.
            panel = self._stale_panel
            if (not panel.guarded(self.app.cfg["prompts.input_guard_ms"]) and event.character
                    and event.character.isdigit() and 1 <= int(event.character) <= len(panel.choices)):
                await self.answer_stale(panel.choices[int(event.character) - 1])
                return
        await self._send_key(event)

    async def _send_key(self, event: events.Key) -> None:
        if self.pump is None:
            return  # still attaching: there is no session to send to (the leader still works)
        try:
            data = encode_key(self.backend, _key_name(event))
        except UnknownKey:
            self.dropped_inputs += 1
            self.pump.state.log.add(KIND_INPUT_DROPPED, time.monotonic(), source=SOURCE_KEY,
                                    reason="unknown_key", key=event.key)
            return
        await self.send_input(SOURCE_KEY, data)

    async def on_paste(self, event: events.Paste) -> None:
        if self.pump is None or self.canvas is None or not event.text:
            return
        event.stop()
        if self._scroll_mode:
            self._drop_while_scrolled(SOURCE_PASTE, len(event.text))
            return
        try:
            data = encode_paste(self.backend, event.text)
        except RuntimeError as exc:
            self.dropped_inputs += 1
            self.pump.state.log.add(KIND_INPUT_DROPPED, time.monotonic(), source=SOURCE_PASTE,
                                    reason="encode_failed", error=str(exc))
            return
        await self.send_input(SOURCE_PASTE, data)

    async def on_canvas_mouse_report(self, event: Canvas.MouseReport) -> None:
        """A click, drag or wheel on the canvas goes to the session only when the session asked for
        mouse reporting (the encoder returns nothing otherwise) and `mouse.enabled` is on. Shift keeps
        the mouse for the operator's own terminal (D13), so a shifted event is never reported. A click on the
        session is the operator turning to it, so the keys go back to it from an open sidebar."""
        event.stop()
        if self._sidebar_has_keys() and event.canvas is self.canvas:
            self.set_focus(None)
        cfg = self.app.cfg
        if not cfg["mouse.enabled"] or self.canvas is None or event.canvas is not self.canvas:
            return
        if self._scroll_mode:
            # The canvas's mouse belongs to the view: the wheel moves it, and nothing is reported to the session.
            if event.button in (WHEEL_UP, WHEEL_DOWN) and event.action == G.MOUSE_ACTION_PRESS:
                self._scroll_wheel(event.button == WHEEL_UP)
            else:
                self._scroll_pointer(event)
            return
        reported = False
        if not event.shift and cfg["mouse.canvas_clicks"] == "session_if_asked":
            data = encode_mouse(self.backend, action=event.action, button=event.button, x=event.x,
                                y=event.y, mods=event.mods)
            if data:
                reported = await self.send_input(SOURCE_MOUSE, data)
        if not reported and event.button in (WHEEL_UP, WHEEL_DOWN):
            # The session did not take the wheel (it never asked for the mouse, or the report is held back): it moves
            # this client's own window over the grid, and when that window already shows the grid from its top, a wheel
            # up opens the scroll view over the scrollback the engine holds. Older output than this client holds
            # arrives with broker pages, which extend the view; they do not replace it.
            if (event.button == WHEEL_UP and self.canvas.view_y == 0 and A.blocked("scroll", self.facts()) is None
                    and self.backend.scrollback_position().offset > 0):
                self.do_scroll()
                self._scroll_wheel(True)
                return
            step = cfg["view.pan_step"]
            self.canvas.pan_to(self.canvas.view_x, self.canvas.view_y + (-step if event.button == WHEEL_UP else step))

    # -- the leader --

    def _start_leader(self) -> None:
        self._leader_pending = True
        wait = self.app.cfg["menu.open_on_leader_after_ms"] / 1000.0
        self._leader_timer = self.set_timer(wait, self._leader_waited)
        self.refresh_status()

    def _cancel_leader(self) -> None:
        self._leader_pending = False
        if self._leader_timer is not None:
            self._leader_timer.stop()
            self._leader_timer = None
        if not self._unmounted:
            self.refresh_status()

    async def _leader_waited(self) -> None:
        """The leader alone: nothing followed it in time, so the Menu opens."""
        if self._leader_pending:
            self._cancel_leader()
            await self.run_action_id("menu")

    # -- actions: the one table, run from a key, a click or the Menu --

    def facts(self, selected: str | None = None) -> A.Facts:
        state = self.pump.state if self.pump is not None else None
        row = self.targets.row(state.session.get("target_id")) if state is not None else None
        return A.Facts(state, selected, open_sessions=len(self.slots), under_drive=self.app.under_drive,
                       alternate_screen=self.canvas is not None and self.backend.active_screen() == G.SCREEN_ALTERNATE,
                       target_type=row.type if row is not None else None)

    async def run_action_id(self, action_id: str) -> None:
        """Run one session action, or say why it is not available now. Every action of the table
        has a handler here or in its dialog (a test proves it)."""
        action = A.BY_ID[action_id]
        why = A.blocked(action_id, self.facts())
        if why is not None:
            self.app.notify(f"{action.label}: {why}.", title="not available", severity="warning",
                            markup=False)
            return
        handler = getattr(self, f"do_{action_id}")
        outcome = handler()
        if outcome is not None:
            await outcome

    def do_menu(self) -> None:
        self.app.open_menu()

    def do_who(self) -> None:
        self.app.push_screen(WhosHereScreen(self))

    def do_size(self) -> None:
        self.app.push_screen(SizeDialog(self))

    def do_auto_size(self) -> None:
        on = self.toggle_auto_size()
        self.app.notify(f"Auto size is {'on' if on else 'off'}.", title="size", markup=False)

    def do_detach(self) -> None:
        self.action_detach()

    def do_answer(self) -> None:
        self.app.push_screen(AnswerScreen(self, self._target(), self.pump.state.quiet_prompt))

    async def do_take_back(self) -> None:
        """Hand the keyboard to this client: the owner takes it back from whoever types (D45)."""
        if self._front is not None:
            await self._take_back(self._front)

    def do_new_session(self) -> None:
        """New session on the target of the session in front, else the one `--target` named. Every session open here
        stays open: the dialog is over the workspace."""
        self._new_session_dialog(self._target() if self._front is not None else self.app.initial_target)

    async def do_new_on_connection(self) -> None:
        from ..vault_flow import call
        from .new_session import CREATE_SIZE_OFF, TERM, NewSessionScreen, create_size

        if self._refuse_while_signing_out() or self._front is None or self._front.pump is None:
            return
        size = create_size(self.app)
        if size is None:
            self.app.notify(CREATE_SIZE_OFF, markup=False)
            return
        session = self._front.pump.state.session
        if self.facts().target_type == "telnet":  # stale: a telnet connection carries one session, nothing to reuse
            return
        try:
            created = await call(self.app, self.app.client.create_session, session["target_id"], "reuse", TERM,
                                 *size, session["connection_id"])
        except ApiError as exc:
            if exc.status == 401:
                self.app.notify_api_error(exc)
            else:
                self.app.push_screen(NewSessionScreen(session["target_id"], session.get("target_type"),
                                                      refusal=exc.message))
            return
        except (HttpError, OSError) as exc:
            self.app.notify_api_error(exc)
            return
        self.open_session(created["session"], created.get("hostkey"))

    def set_login_status(self, slot: SessionSlot, message: str) -> None:
        slot.login_status = message
        slot.login_status_until = self.app.wall_clock() + self.app.cfg["toasts.seconds"]
        until = slot.login_status_until

        def clear():
            if slot.login_status_until == until:
                slot.login_status = ""
                slot.login_status_until = 0.0
                self.refresh_status()

        self.set_timer(self.app.cfg["toasts.seconds"], clear)
        self.refresh_status()

    def _new_session_dialog(self, target: str | None, target_type: str | None = None) -> None:
        from .new_session import NewSessionScreen

        self.app.push_screen(NewSessionScreen(target=target, target_type=target_type))

    def do_fleet(self) -> None:
        self.toggle_sidebar()

    def do_full_view(self) -> None:
        self.open_fleet_view()

    def do_end(self) -> None:
        """End the session in front, after a confirmation naming it."""
        if self._front is not None:
            self.end_session(self._front.session_id)

    def do_event_log(self) -> None:
        self.app.push_screen(EventLogScreen(self, f"Event log · {self._target()} · this client's view of the session",
                                            lambda: event_log_lines(self), follow=True))

    def do_settings(self) -> None:
        self.app.push_screen(TextScreen(self, "Settings · the knobs in force, and where each came from",
                                        lambda: settings_lines(self.app.cfg)))

    def do_keys(self) -> None:
        self.app.push_screen(TextScreen(self, "All keys", lambda: keys_lines(self.app.cfg)))

    def do_about(self) -> None:
        self.app.push_screen(TextScreen(self, "About tsb-tui", lambda: about_lines(self.app)))

    def do_credentials(self) -> None:
        from .credentials import CredentialsScreen

        self.app.push_screen(CredentialsScreen())

    def do_devices(self) -> None:
        from .devices import DevicesScreen

        self.app.push_screen(DevicesScreen())

    def do_lock_now(self) -> None:
        """Purge every vault unlock this user holds and lock this screen (cred §5.5; vault-lock step 6)."""
        self.app.screen_lock.lock_now()

    async def do_quit(self) -> None:
        await self.app.action_quit()

    async def do_sign_out(self) -> None:
        """Detach everything here, sign out and return to Login (no confirmation, as Quit has none)."""
        await self.app.sign_out()

    def do_pan(self) -> None:
        """`^\\ v`: the arrow keys move the window over the grid by `view.pan_step` until Esc (D4, D28)."""
        self._pan_mode = True
        self.refresh_status()

    def do_scroll(self) -> None:
        """`^\\ [`: PageUp, PageDown and the wheel move the viewport through the scrollback this client's engine holds,
        until Esc. The session keeps running into the engine meanwhile; the view stays on the rows it shows."""
        self._end_pan()
        if self.view_open:
            self.close_fleet_view()  # run from the Menu over the full view: the terminal gets the keys back first
        if self._sidebar_has_keys():
            self.set_focus(None)
        self._scroll_mode = True
        self._scroll_notice = None
        if self.canvas is not None:
            self.canvas.follow_paused = True
        self.refresh_status()

    def close_scroll_view(self) -> None:
        """Close the view and put the session's viewport back on the live screen. Safe whatever state the slot is in."""
        if not self._scroll_mode:
            return
        self._scroll_mode = False
        self._scroll_notice = None
        self._search = self._search_at = None
        self._selection = self._drag_from = None
        front = self._front
        if front is not None and not front.closed:
            front.canvas.clear_highlights()
            front.canvas.follow_paused = False
            front.canvas.clear_selection()
            front.canvas.scroll_to_bottom()
        self.refresh_status()

    def _scroll_key(self, event: events.Key) -> None:
        if event.key == "escape":
            if self._selection is not None:
                self._clear_selection()
            else:
                self.close_scroll_view()
        elif event.key in ("pageup", "pagedown", "up", "down"):
            direction = -1 if event.key in ("pageup", "up") else 1
            pages = event.key in ("pageup", "pagedown")
            if self._selection is not None and self.canvas is not None:
                self._move_selection(direction * (self.canvas.page_rows if pages else 1))
            else:
                self._scroll_by(direction, pages=pages, rows=1)
        elif event.character == "v":
            self._toggle_selection()
        elif event.character == "y" or event.key == "enter":
            self._copy()
        elif event.character == "/":
            self.do_search()
        elif event.character in ("n", "N"):
            self._search_step(older=event.character == "n")
        else:
            self._drop_while_scrolled(SOURCE_KEY, 0, key=event.key)

    def _scroll_wheel(self, up: bool) -> None:
        self._scroll_by(-1 if up else 1, rows=self.app.cfg["scroll.wheel_lines"])

    def _scroll_by(self, direction: int, *, pages: bool = False, rows: int = 1) -> None:
        """Move the view `direction` (-1 older, 1 newer) by a page, or by `rows` rows."""
        canvas = self.canvas
        if canvas is None:
            return
        self._scroll_notice = None
        canvas.scroll_by(direction * (canvas.page_rows if pages else rows))
        self.refresh_status()

    def _drop_while_scrolled(self, source: str, size: int, **detail: Any) -> None:
        """A key or paste the view does not use: dropped, said so in the status row and the event log."""
        self.dropped_inputs += 1
        if self.pump is not None:
            self.pump.state.log.add(KIND_INPUT_DROPPED, time.monotonic(), source=source, bytes=size,
                                    reason=SCROLL_DROPPED, **detail)
        self._scroll_notice = SCROLL_DROPPED_NOTE
        self.refresh_status()

    # -- search in the view --

    def do_search(self) -> None:
        """`/` in the view: ask for the text to find. The view stays open under the prompt, and Esc in the prompt
        returns to it where it was."""
        if self._scroll_mode and not self.dialogs:
            self.app.push_screen(SearchPrompt(self), self._searched)

    def _searched(self, query: str | None) -> None:
        """The prompt's answer. Nothing typed, or nothing left to search in: the view is as it was. Otherwise the
        search runs from the top row on view toward older output."""
        if not query or not self._scroll_mode or self.canvas is None:
            return
        self._search = search(self.backend, query)
        self._search_at = None
        if not self._search.matches:
            self.canvas.clear_highlights()
            self._say_in_view(f"not found: {query}")
            return
        index = self._search.at_or_before_row(self.backend.scrollback_position().offset)
        if index is None:
            self._mark_matches()
            self._say_in_view(SEARCH_NOT_OLDER)
        else:
            self._show_match(index)

    def _search_step(self, *, older: bool) -> None:
        """`n` (older) and `N` (newer): the next match from the one the view stands on. New output since the query was
        run means the rows it read are not the engine's now, so it is run again first."""
        if self._search is None or self.canvas is None:
            self._say_in_view(SEARCH_NONE_YET)
            return
        moved = False
        if self._search.generation != self.backend.generation:
            fresh = search(self.backend, self._search.query)
            if self._search_at is not None:
                row = reanchor(fresh.rows, self._search_at[0], self._search_text)
                if row is None:
                    self._search_at = None  # its row is not held any more: step from the view
                    moved = True
                else:
                    self._search_at = (row, self._search_at[1])
            self._search = fresh
        result = self._search
        if not result.matches:
            self.canvas.clear_highlights()
            self._say_in_view(f"not found: {result.query}")
            return
        if self._search_at is not None:
            index = result.before(self._search_at) if older else result.after(self._search_at)
        else:
            top = self.backend.scrollback_position().offset
            index = result.at_or_before_row(top) if older else result.after((top, 1 << 62))
        if index is None:
            self._mark_matches()
            self._say_in_view(SEARCH_NOT_OLDER if older else SEARCH_NOT_NEWER)
        else:
            self._show_match(index)
            if moved:
                self._say_in_view(SEARCH_HISTORY_MOVED)

    def _show_match(self, index: int) -> None:
        """Stand on match `index`: its row in the window, every match marked and this one marked as the current."""
        result = self._search
        canvas = self.canvas
        if result is None or canvas is None:
            return
        match = result.matches[index]
        self._search_at = match.key
        self._search_text = result.rows[match.row]
        self._scroll_notice = None
        canvas.scroll_to_row(match.row)
        canvas.show_row(match.row - self.backend.scrollback_position().offset)
        self._mark_matches()
        self.refresh_status()

    def _mark_matches(self) -> None:
        result, canvas = self._search, self.canvas
        if result is None or canvas is None:
            return
        cfg = self.app.cfg
        other, current = Style.parse(cfg["scroll.match_style"]), Style.parse(cfg["scroll.current_match_style"])
        canvas.set_highlights([Highlight(m.row, m.start, m.end, current if m.key == self._search_at else other)
                               for m in result.matches], result.generation)

    # -- selecting and copying in the view --

    def _toggle_selection(self) -> None:
        """`v`: with no selection, start one on the current match's row when a match is shown, else on the first row on
        view; with one, take it off."""
        canvas = self.canvas
        if canvas is None:
            return
        if self._selection is not None:
            self._clear_selection()
            return
        shown = self._search is not None and self._search_at is not None \
            and self._search.generation == self.backend.generation
        row = self._search_at[0] if shown and self._search_at is not None \
            else self.backend.scrollback_position().offset + canvas.view_y  # the window may be panned down the grid
        self._begin_selection(row, row)
        self._show_row(row)

    def _begin_selection(self, anchor: int, cursor: int) -> None:
        """Select rows `anchor`..`cursor`, in place of any selection there was, keeping the text they hold now."""
        total = self.backend.scrollback_position().total
        selection = Selection(min(max(anchor, 0), total - 1), min(max(cursor, 0), total - 1))
        if not selection.fit(self.backend):
            self._clear_selection(SELECT_MOVED)
            return
        self._selection = selection
        self._paint_selection()

    def _move_selection(self, rows: int) -> None:
        """Move the selection's loose end `rows` rows (older is negative), with the view following it."""
        selection = self._selection
        if selection is None:
            return
        total = self.backend.scrollback_position().total
        selection.cursor = min(max(selection.cursor + rows, 0), total - 1)
        if not selection.fit(self.backend):
            self._clear_selection(SELECT_MOVED)
            return
        self._paint_selection()
        self._show_row(selection.cursor)

    def _show_row(self, row: int) -> None:
        """Scroll the view, and pan the window, the least that brings engine row `row` on screen."""
        canvas = self.canvas
        if canvas is None:
            return
        position = self.backend.scrollback_position()
        if row < position.offset:
            canvas.scroll_to_row(row)
        elif row >= position.offset + position.length:
            canvas.scroll_to_row(row - position.length + 1)
        canvas.show_row(row - self.backend.scrollback_position().offset)
        self._scroll_notice = None
        self.refresh_status()

    def _paint_selection(self) -> None:
        selection, canvas = self._selection, self.canvas
        if selection is None or canvas is None:
            return
        canvas.set_selection(selection.lo, selection.hi, Style.parse(self.app.cfg["scroll.selection_style"]))
        self._scroll_notice = None
        self.refresh_status()

    def _clear_selection(self, note: str | None = None) -> None:
        self._selection = self._drag_from = None
        if self.canvas is not None:
            self.canvas.clear_selection()
        self._scroll_notice = note
        self.refresh_status()

    def _check_selection(self) -> None:
        """Output arrived: if the selection's first or last row no longer holds the text it was selected with (the history
        was trimmed or a row rewritten), the selection is cleared now, so the mark never sits on other text. Every row is
        read again only at copy; this reads two."""
        selection = self._selection
        if selection is None:
            return
        for y in {selection.lo, selection.hi}:
            if self.backend.row_texts(y, y)[0] != selection.rows.get(y):
                self._clear_selection(SELECT_MOVED)
                return

    def _copy(self) -> None:
        """`y` or Enter: send the selected lines to the operator's clipboard, and keep the selection. Nothing is sent when
        copying is off, when the rows are not the text they were selected with, or when the text is over the limit."""
        selection, cfg = self._selection, self.app.cfg
        if selection is None:
            self._say_in_view(SELECT_NOTHING)
            return
        if not cfg["copy.osc52"]:
            self._say_in_view(COPY_OFF)
            self._toast(COPY_OFF, "copy")  # the note is clipped by a narrow status row; the toast is read whole
            return
        limit = cfg["copy.max_bytes"]
        state, text = selection.read_for_copy(self.backend, cfg["copy.join_wrapped"], limit)
        if state == "moved":
            self._clear_selection(SELECT_MOVED)
            return
        if state == "over":
            self._say_in_view(f"too large to copy (over {limit} bytes; copy.max_bytes is {limit})")
            return
        if not text.strip():
            self._say_in_view(COPY_BLANK)  # an empty OSC 52 write can clear the clipboard: nothing is sent
            return
        self.app.copy_out(text)
        lines = text.count("\n") + 1
        self._toast(f"Sent {lines} {'line' if lines == 1 else 'lines'} to your terminal's clipboard.", "copy")
        self._scroll_notice = None
        self.refresh_status()

    def _scroll_pointer(self, event: Canvas.MouseReport) -> None:
        """The pointer in the view, with the left button: a drag from one row to another selects the rows it covers, in
        place of any selection, and releasing copies them. A press and release on one row does nothing. Every left-button
        release ends the drag, but only an unshifted one copies; motion extends a drag only while the left button is
        held."""
        if event.action == G.MOUSE_ACTION_RELEASE and event.button == G.MOUSE_BUTTON_LEFT:
            dragged, self._drag_from, self._dragged = self._dragged, None, False
            if dragged and not event.shift and self._selection is not None:
                self._copy()
            return
        if event.shift:
            return  # the operator's own terminal's, as ever
        row = self.backend.scrollback_position().offset + event.y
        if event.action == G.MOUSE_ACTION_PRESS and event.button == G.MOUSE_BUTTON_LEFT:
            self._drag_from, self._dragged = row, False
        elif self._drag_from is None:
            return
        elif event.action == G.MOUSE_ACTION_MOTION and event.button == G.MOUSE_BUTTON_LEFT:
            if not self._dragged:
                if row != self._drag_from:
                    self._dragged = True
                    self._begin_selection(self._drag_from, row)
            elif self._selection is not None and self._selection.cursor != row:
                self._move_selection(row - self._selection.cursor)

    def _say_in_view(self, note: str) -> None:
        self._scroll_notice = note
        self.refresh_status()

    def _scroll_note(self) -> str:
        """Where the viewport is, from the engine's own position: rows back of the rows held, and a word at the top."""
        if self._scroll_notice:
            return self._scroll_notice
        position = self.backend.scrollback_position()
        held = position.total - position.length
        back = held - position.offset
        note = f"⇡ {_spaced(back)} rows back of {_spaced(held)}"
        return f"{note} · the oldest output this client holds" if position.offset == 0 else note

    def do_theme(self) -> None:
        """`^\\ T`: the Theme picker, previewing as the highlight moves (B5)."""
        from .theme_picker import ThemePicker

        self.app.push_screen(ThemePicker(self))

    def _end_pan(self) -> None:
        if self._pan_mode:
            self._pan_mode = False
            self.refresh_status()

    def _pan_key(self, key: str) -> bool:
        """A key while Pan has the keys: an arrow moves the window, Esc or Enter ends Pan. False for any other key."""
        if key in ("escape", "enter"):
            self._end_pan()
            return True
        moves = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}
        if key not in moves:
            return False
        self._pan_by(*moves[key])
        return True

    def _pan_by(self, dx: int, dy: int) -> None:
        canvas = self.canvas
        if canvas is not None:
            step = self.app.cfg["view.pan_step"]
            canvas.pan_to(canvas.view_x + dx * step, canvas.view_y + dy * step)

    def running_sessions(self) -> list[str]:
        """The sessions open here, which keep running when the TUI quits (D38): `target  id`, in rail order."""
        ids = [slot.session_id for slot in self.slots]
        return [f"{slot.target}  {display_id(slot.session_id, ids)}" for slot in self.slots if not slot.closed]

    def do_switch(self) -> None:
        """`^\\ s`: the sidebar takes the keys, for picking another session."""
        if self.sidebar_open and self.sidebar is not None:
            self.sidebar.focus()
            self.refresh_status()
        else:
            self.open_sidebar()

    def do_next(self) -> None:
        self._step(1)

    def do_prev(self) -> None:
        self._step(-1)

    async def on_status_row_activated(self, message: StatusRow.Activated) -> None:
        message.stop()
        if message.action.startswith(SIDEBAR_HINT):
            name = message.action.removeprefix(SIDEBAR_HINT)
            self._sidebar_command(name)
            return
        if message.action.startswith(FLEET_VIEW_HINT):
            name = message.action.removeprefix(FLEET_VIEW_HINT)
            if name == CMD_TAB:
                self.fleet_view.next_tab()
            elif name == CMD_ENDED:
                self.fleet_view.toggle_show_ended()
            elif name == CMD_FILTER:
                self.fleet_view.start_filter()
                self.fleet_view.focus()
            elif name == CMD_MARK:
                self.fleet_view.toggle_mark()
            else:
                self._fleet_view_command(name)
            return
        if message.action.startswith(SCROLL_HINT):
            move = message.action.removeprefix(SCROLL_HINT)
            if move == "done":
                self.close_scroll_view()
            elif move == "search":
                self.do_search()
            elif move == "select":
                self._toggle_selection()
            elif move == "copy":
                self._copy()
            else:
                self._scroll_by(-1 if move == "up" else 1, pages=True)
            return
        if message.action.startswith(PAN_HINT):
            move = message.action.removeprefix(PAN_HINT)
            if move == "done":
                self._end_pan()
            else:
                self._pan_by(*{"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}[move])
            return
        await self.run_action_id(message.action)

    def menu_entries(self) -> list[tuple[A.Action, str, str | None]]:
        """What the Menu lists: each action, its key as it is typed ("" for an action with no key of its own: the
        leader alone is the Menu itself), and why it is not available now."""
        facts = self.facts()
        leader = A.leader_label(self.app.cfg["keys.leader"])
        keys = A.leader_keys(self.app.cfg.leader_overrides())
        return [(action, f"{leader} {keys[action.id]}" if action.id in keys else "",
                 A.blocked(action.id, facts)) for action in A.menu_actions()]

    def set_menu_open(self, is_open: bool) -> None:
        self._menu_open = is_open
        self.refresh_status()

    # -- the status row --

    def _sync_dialogs(self) -> None:
        for dialog in list(self.dialogs):
            dialog.sync()

    def _keys_target(self) -> KeysTarget | None:
        """Where the keys go when it is not the session (D14): the leader waiting, a dialog, the Menu, the sidebar."""
        if self._leader_pending:
            keys = A.leader_keys(self.app.cfg.leader_overrides())
            facts = self.facts()
            hints = [Hint(keys[a.id], a.hint or a.label) for a in A.leader_actions()
                     if a.hint and A.blocked(a.id, facts) is None]
            hints.append(Hint(A.leader_label(self.app.cfg["keys.leader"]), "sends the key itself"))
            return KeysTarget("the leader", tuple(hints))
        if self.dialogs:
            return self.dialogs[-1].status_target()
        if self._menu_open:
            return MENU_KEYS
        if self._scroll_mode:
            return self._scroll_keys_target()
        if self._pan_mode:
            return self._pan_keys_target()
        if self._chord is not None:
            return KeysTarget("the command", (Hint("q", "Quit"),))
        if self._view_has_keys():
            return self._fleet_view_keys_target()
        if self._sidebar_has_keys():
            return self._sidebar_keys_target()
        return None

    def _scroll_keys_target(self) -> KeysTarget:
        """The scroll view's own keys as hints, each a click, so a touch screen can scroll too."""
        click = SCROLL_HINT
        select = Hint("y", "Copy", click + "copy") if self._selection is not None else Hint("v", "Select",
                                                                                          click + "select")
        return KeysTarget("scroll", (Hint("PgUp", "Older", click + "up"), Hint("PgDn", "Newer", click + "down"),
                                     Hint("/", "Search", click + "search"), select, Hint("Esc", "Live", click + "done")),
                          self._scroll_note())

    def _pan_keys_target(self) -> KeysTarget:
        """Pan's own keys as hints, each a click, so a touch screen can pan too (D13)."""
        click = PAN_HINT
        canvas = self.canvas
        note = canvas.view_range.label if canvas is not None else ""
        return KeysTarget("pan", (Hint("←", "Left", click + "left"), Hint("→", "Right", click + "right"),
                                  Hint("↑", "Up", click + "up"), Hint("↓", "Down", click + "down"),
                                  Hint("Esc", "Done", click + "done")), note)

    def _fleet_view_keys_target(self) -> KeysTarget:
        """The full view's row actions as hints, each a click (mockup F1): the status row is where they live."""
        click = FLEET_VIEW_HINT
        view = self.fleet_view
        if view is not None and view.tab == TAB_ALERTS:
            return KeysTarget("fleet view", (Hint("Tab", "Next tab", click + CMD_TAB),
                                             Hint("Esc", "Back", click + CMD_CLOSE)))
        if view is not None and view.tab == TAB_TARGETS:
            writes = ()
            row = self.targets.row(view.selected_session)
            editable = row is not None and row.discovered_by == "tsb"
            if "target.edit" in self.app.permissions:
                writes = (Hint("n", "New target", click + CMD_TARGET_NEW),
                          Hint("e", "Edit target", click + CMD_TARGET_EDIT if editable else None),
                          Hint("d", "Delete target…", click + CMD_TARGET_DELETE if editable else None))
            return KeysTarget("targets", (*writes,
                Hint("⏎", "New session here", click + CMD_OPEN_TARGET), Hint("/", "Query", click + CMD_FILTER),
                Hint("Space", "Mark", click + CMD_MARK), Hint("l", "Save login…", click + CMD_SAVE_LOGIN),
                Hint("p", "Map…", click + CMD_MAP),
                Hint("Tab", "Next tab", click + CMD_TAB), Hint("Esc", "Back", click + CMD_CLOSE)),
                self._targets_query_error)
        if self.compact:
            return KeysTarget("fleet view", (Hint("t", "Take back", click + CMD_TAKE_BACK),
                                             Hint("Esc", "Back", click + CMD_CLOSE)), "tap a row")
        return KeysTarget("fleet view", (
            Hint("⏎", "Open", click + CMD_PICK), Hint("c", "New session", click + CMD_NEW),
            Hint("t", "Take back", click + CMD_TAKE_BACK), Hint("w", "Who's here", click + CMD_WHOS_HERE),
            Hint("x", "End…", click + CMD_END), Hint("/", "Filter", click + CMD_FILTER),
            Hint("Esc", "Back", click + CMD_CLOSE)))

    def _sidebar_keys_target(self) -> KeysTarget:
        """The sidebar's own keys as hints, each a click (design §2.3): the whole row on a laptop, `t` and Esc
        on a phone (mockups S1, S2, S3)."""
        cfg = self.app.cfg
        click = SIDEBAR_HINT
        if self.compact:
            return KeysTarget("fleet", (Hint("t", "Take back", click + CMD_TAKE_BACK),
                                        Hint("Esc", "Close", click + CMD_CLOSE)), "tap a row")
        hints = [Hint("⏎", "Open", click + CMD_PICK), Hint("t", "Take back", click + CMD_TAKE_BACK),
                 Hint("w", "Who's here", click + CMD_WHOS_HERE), Hint("/", "Filter", click + CMD_FILTER)]
        if self._window_width() >= cfg["sidebar.wide_from"]:
            hints += [Hint("m", "Mine│all", click + CMD_MODE), Hint("F", "Full view", click + CMD_FULL_VIEW)]
        hints.append(Hint("Esc", "Close", click + CMD_CLOSE))
        range_label = self.canvas.view_range.label if self.canvas is not None else ""
        note = f"{self._target()} shows {range_label}" if range_label and self._front is not None else ""
        return KeysTarget("fleet", tuple(hints), note)

    def status_inputs(self) -> StatusInputs:
        cfg = self.app.cfg
        front = self._front
        state = self.pump.state if self.pump is not None else None
        session = self._target() if front is not None else ""
        return StatusInputs(
            state=state, session=session,
            range_label=self.canvas.view_range.label if self.canvas is not None else "",
            keys_not_sent=self.keys_not_sent, leader=A.leader_label(cfg["keys.leader"]),
            leader_keys=A.leader_keys(cfg.leader_overrides()),
            hint_priority=cfg["status.hint_priority"], keys_indicator=cfg["status.keys_indicator"],
            target=self._keys_target(), attaching=front is not None and state is None,
            input_notice=(front.input_notice if front is not None and state is not None
                          and state.reattach is not None and not state.reattach_stopped
                          and front.ended is None else ""),
            now=self.app.wall_clock(), recording_bar=self._recording_text(front, state),
            ended=self._ended_status(state), reattach=self._reattach_status(state),
            reattach_stopped=state is not None and state.reattach_stopped,
            chrome=cfg["view.chrome"], glyph_set=glyphs.check(cfg["glyphs.set"]),
            open_sessions=len(self.slots), login_status=(front.login_status if front is not None and (front.device_gate
                           or self.app.wall_clock() < front.login_status_until) else ""))

    def _recording_text(self, slot: SessionSlot | None, state: AttachmentState | None) -> str | None:
        if slot is None or state is None or not slot.bar_until or self.app.wall_clock() >= slot.bar_until:
            return None
        return (state.recording or {}).get("warning_text")

    def _ended_status(self, state: AttachmentState | None) -> EndedStatus | None:
        if self.ended is None or state is None:
            return None
        cfg = self.app.cfg
        leader = A.leader_label(cfg["keys.leader"])
        keys = A.leader_keys(cfg.leader_overrides())
        hints = [Hint("c", "New session here", "new_session")]
        if len(self.slots) > 1:
            hints.append(Hint("n", "Next open session", "next"))
        hints.append(Hint(f"{leader} {keys['fleet']}", "Fleet", "fleet"))
        if self.dialogs:
            hints.append(Hint("Esc", "close"))
        return EndedStatus(f"✕ {self.ended.row}", bool((state.recording or {}).get("active"))
                           and self.ended.ended, tuple(hints))

    def _reattach_status(self, state: AttachmentState | None) -> ReattachStatus | None:
        if state is None or state.reattach is None:
            return None
        wait = state.reattach
        why = _REATTACH_WHY.get(wait.get("cause"), _REATTACH_WHY["transport_lost"])
        return ReattachStatus(wait["attempt"], max(wait["due"] - state.now(), 0.0), why)

    def refresh_status(self) -> None:
        if self.status is not None and not self._unmounted:
            self.status.set_inputs(self.status_inputs())


def _spaced(count: int) -> str:
    """A row count with a space between thousands: `9 870`."""
    return f"{count:,}".replace(",", " ")


def _key_name(event: events.Key) -> str:
    """The name `encode_key` takes for a Textual key event. A printable key is its character, so a
    punctuation mark never depends on a name table; a modified key (`ctrl+a`, `alt+left`, `f5`) is
    named by Textual and passed through."""
    if event.is_printable and event.character is not None and "+" not in event.key:
        return event.character
    return event.key

