"""The terminal canvas: a Line API widget over a `VTBackend` (design §2.6, §3.3 "the paint path").

What it guarantees, and why (Rule 9):

* **The grid is the session's, the window only looks at it (D3, D4).** The backend keeps the size the
  session has; when the canvas is smaller the widget pans, never reflows. The target's own layout
  (a router's `show` table, a full-screen editor) therefore reads the same on every client.
* **Bytes are parsed at once, painting is coalesced (D33, DT20).** `feed` hands bytes to the engine
  immediately, under the engine's own lock, and only marks the canvas dirty; it runs on the
  attachment's feed thread. A timer capped at `render.max_fps` reads the engine's *dirty rows* (never
  the whole grid unless the engine says it all changed), rebuilding just those rows' strips from runs
  of equal style, and asks Textual to repaint just those lines. A firehose costs a bounded number of
  paints, and the loop that paints is the loop that answers the server's pings, so paint load must
  never be able to starve the socket. The paint holds the engine lock for its whole read (update,
  dirty rows, cursor, clean) and builds its strips after releasing it, so a feed slice loses at most
  one read per tick.
* **A hold is honoured (DT3).** While the session holds synchronized output (mode 2026) the canvas
  keeps showing the last finished frame, so a program that redraws in several steps never shows a
  half-drawn screen. The engine's own header tells a renderer to give up on a hold after one second,
  so that is the longest a stuck program can freeze the picture.
* **The mouse goes up, not out (D13).** A press, release, drag or wheel turn on the canvas is posted as
  a `MouseReport` in the *grid's* cells (the window's offset over the grid added back); the screen
  decides whether the session gets it. The canvas never sends anything.
* **The cursor says who types (D21).** A filled block in `accent` for the typist, an underline in
  `steel` for a viewer.

Style ids: the engine reuses a style id once no cell holds it and numbers ids per page, so a
viewport that spans two pages can hold one id with two meanings. Each `Row` therefore carries the
styles of its own ids, and a strip is built from its row's map alone; the palette keys its cache by
the style's contents rather than by id.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from itertools import groupby
from operator import attrgetter
from typing import Any, NamedTuple

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.geometry import Region, Size
from textual.message import Message
from textual.strip import Strip
from textual.timer import Timer
from textual.widget import Widget

from ..term import VTBackend
from ..term import ghostty as G
from ..term.backend import Cursor, Row
from ..theme import TermPalette

# The engine's own advice for a renderer that finds a program holding the screen: stop waiting after a second.
HOLD_LIMIT_S = 1.0

_STYLE_ID = attrgetter("style_id")
# Textual numbers the buttons 1 left, 2 middle, 3 right; a motion with no button down carries 0.
# The wheel is buttons four and five in xterm's numbering, which mouse/event.h keeps (FOUR, FIVE).
_WHEEL_UP, _WHEEL_DOWN = 4, 5
_BUTTONS = {1: G.MOUSE_BUTTON_LEFT, 2: G.MOUSE_BUTTON_MIDDLE, 3: G.MOUSE_BUTTON_RIGHT}


class ViewRange(NamedTuple):
    """What part of the session's grid the canvas shows, 1-based and inclusive, with the grid's size."""

    col_first: int
    col_last: int
    cols: int
    row_first: int
    row_last: int
    rows: int

    @property
    def label(self) -> str:
        """`cols a-b of N ▸` for the status row (design §2.6), or "" when every column is in view."""
        if self.col_first == 1 and self.col_last == self.cols:
            return ""
        return f"cols {self.col_first}-{self.col_last} of {self.cols} ▸"


class Canvas(Widget):
    """The session's screen. Owned and fed by one terminal screen; reads nothing from the network."""

    DEFAULT_CSS = """
    Canvas {
        width: 1fr;
        height: 1fr;
        background: $background;
    }
    """
    can_focus = False

    class MouseReport(Message):
        """A mouse event on the grid, for the screen to encode and send or not (D13). `x` and `y` are
        grid cells, 0-based, clamped to the grid; `mods` are the GhosttyMods bits of Ctrl and Alt;
        Shift travels apart, because a shifted event is the operator's own terminal's and is never
        reported to the session."""

        def __init__(self, canvas: Canvas, action: int, button: int | None, x: int, y: int, mods: int,
                     shift: bool) -> None:
            super().__init__()
            self.canvas = canvas
            self.action = action
            self.button = button
            self.x = x
            self.y = y
            self.mods = mods
            self.shift = shift

    class ViewChanged(Message):
        """The window moved over the grid (a pan, or the cursor followed): the status row's range changed."""

        def __init__(self, canvas: Canvas) -> None:
            super().__init__()
            self.canvas = canvas

    class Resized(Message):
        """The canvas widget's own size changed (not the session's grid)."""

        def __init__(self, canvas: Canvas, size: Size) -> None:
            super().__init__()
            self.canvas = canvas
            self.size = size

    def __init__(self, backend: VTBackend, palette: TermPalette, config: Mapping[str, Any],
                 *, typist: bool = False) -> None:
        super().__init__()
        max_fps = config["render.max_fps"]
        if not isinstance(max_fps, int) or not 1 <= max_fps <= 240:
            raise ValueError(f"render.max_fps: expected 1-240, got {max_fps!r}")
        follow = config["view.follow_cursor"]
        if follow not in ("auto", "on", "off"):
            raise ValueError(f"view.follow_cursor: expected auto, on or off, got {follow!r}")
        if config["view.narrow_mode"] != "pan":
            # Config refuses "wrap" at load (not built at E2); this keeps the canvas honest if that
            # ever changes before a wrapping view exists.
            raise ValueError(f"view.narrow_mode: only 'pan' is built, got {config['view.narrow_mode']!r}")
        self._backend = backend
        self._released = False
        self._timer: Timer | None = None
        self._palette = palette
        self._max_fps = max_fps
        self._follow_enabled = follow != "off"
        self._paint_background = bool(config["render.paint_background"])
        self._typist = typist
        self._dimmed = False
        # Set the canvas CSS background to term_bg when set (phase 1, step 5)
        if palette.term_bg:
            self.styles.background = palette.term_bg
        # False for an attachment the operator is not looking at: parsed, not painted (D33) unless
        # `render.paint_background` says to paint it anyway.
        self.in_front = True
        self._pending = True
        self._hold_since: float | None = None
        self._strips: list[Strip | None] = [None] * backend.rows
        self._rows: list[Row | None] = [None] * backend.rows
        self._restyle_pending = False
        self._cursor: Cursor | None = None
        self._view_x = 0
        self._view_y = 0
        self._last_motion: tuple[int, int] | None = None
        # Counters the tests (and phase 10's benchmark) read: how many paints, which rows the last
        # paint rebuilt.
        self.paint_count = 0
        # How long the paint has waited for the engine lock (DT20): the loop's cost of a firehose, which
        # the design note's §4 asks the bench to report. The lock is taken per paint; this is the total.
        self.lock_wait_ms = 0.0
        self.rebuilt_rows: list[int] = []

    # -- what the canvas exposes --

    @property
    def backend(self) -> VTBackend:
        return self._backend

    @property
    def typist(self) -> bool:
        return self._typist

    @typist.setter
    def typist(self, value: bool) -> None:
        if value != self._typist:
            self._typist = value
            self._refresh_rows(*self._cursor_rows())

    @property
    def dimmed(self) -> bool:
        return self._dimmed

    @dimmed.setter
    def dimmed(self, value: bool) -> None:
        """An ended session keeps its last screen, dimmed (D36): the terminal's own `dim` attribute, and
        no cursor, because nothing is left to type into."""
        if value != self._dimmed:
            self._dimmed = value
            self.refresh()

    @property
    def painted(self) -> bool:
        """True once everything fed so far is on screen (nothing waits for the next paint tick)."""
        return not self._pending

    @property
    def view_x(self) -> int:
        return self._view_x

    @property
    def view_y(self) -> int:
        return self._view_y

    @property
    def view_range(self) -> ViewRange:
        """The visible part of the grid, for the status row (phase 5)."""
        width, height = max(self.size.width, 1), max(self.size.height, 1)
        cols, rows = self._backend.cols, self._backend.rows
        return ViewRange(
            self._view_x + 1, min(self._view_x + width, cols), cols,
            self._view_y + 1, min(self._view_y + height, rows), rows,
        )

    # -- driving the grid (the screen calls these) --

    def close(self) -> None:
        """Stop painting and free the engine's grid. Idempotent, and nothing reaches the engine after
        it: a paint timer or a late frame must never touch a freed terminal."""
        self._released = True
        if self._timer is not None:
            self._timer.stop()
        self._backend.close()

    def feed(self, data: bytes) -> None:
        """Parse session output at once; the picture catches up on the next paint tick. A direct
        caller on the loop (a test, a rig): replies go through the backend's reply callback. The
        attachment's own output goes through `feed_run` on its feed thread (DT20)."""
        if self._released:
            return
        self._backend.feed(data)
        self._pending = True

    def feed_dump(self, cols: int, rows: int, data: bytes) -> None:
        """A reattach's dump: a fresh grid at the `prefix` geometry, fed with every reply muted so a
        query recorded in the dump is never answered to the session again (DT3). A direct caller on
        the loop; the attachment's own dump goes through `feed_run` (DT20)."""
        if self._released:
            return
        self._backend.reset(cols, rows)
        self._reset_rows()
        with self._backend.muted():
            self._backend.feed(data)
        self._pending = True

    def feed_run(self, role: str, data: bytes, *, cols: int | None = None,
                 rows: int | None = None) -> list[bytes]:
        """One run of the attachment's output, on its feed thread (DT20): the engine only, in 32 KiB
        slices under the backend lock, with replies collected and returned. A `dump` resets the grid
        at the `prefix` geometry first and mutes replies. The loop-side bookkeeping (`after_run`) is
        the processor's, after this returns; nothing here touches Textual."""
        if self._released:
            return []
        if role == "dump":
            self._backend.reset(cols or self._backend.cols, rows or self._backend.rows)
            with self._backend.muted():
                replies = self._backend.feed_run(data)
        else:
            replies = self._backend.feed_run(data)
        self._pending = True
        return replies

    def after_run(self, role: str) -> None:
        """The loop-side half of a run (DT20), on the processor after the feed thread returned: a
        `dump` rebuilt the grid at a new geometry, so the row caches reset and the window re-clamps;
        output only asks for the next paint."""
        if self._released:
            return
        if role == "dump":
            self._reset_rows()
        else:
            self.refresh()

    def resize_grid(self, cols: int, rows: int) -> None:
        """The session's size changed: the grid takes it. The window never calls this."""
        if self._released or (cols, rows) == (self._backend.cols, self._backend.rows):
            return
        self._backend.resize(cols, rows)
        self._reset_rows()
        self._pending = True

    def bring_to_front(self) -> None:
        """The session in front changed to this one: paint what arrived while it was in the background, at
        once, from its own grid. A hold on the screen (mode 2026) is honoured as ever, and the timer paints
        the frame when it ends."""
        self.in_front = True
        if self._restyle_pending:
            self._restyle()
            self._restyle_pending = False
        if self._pending and self.is_attached and not self._backend.hold and not self._released:
            self.repaint_now()
        else:
            self.refresh()

    def send_to_back(self) -> None:
        """Another session is in front: this grid keeps parsing and no longer paints (D33)."""
        self.in_front = False

    def set_palette(self, palette: TermPalette) -> None:
        """A run-time look switch (B4, TT15): the new palette colours every row the canvas has kept. The
        engine is not read, so a held frame (mode 2026) keeps its last content in the new colours. In front,
        rebuild and repaint at once; behind, mark for `bring_to_front` to rebuild when it next comes forward."""
        self._palette = palette
        if palette.term_bg:
            self.styles.background = palette.term_bg
        else:
            # term_bg is None (e.g. theme.term.bg = "outer"): clear the inline rule so the CSS $background applies.
            self.styles.clear_rule("background")
        if self.in_front:
            self._restyle()
            self.refresh()
        else:
            self._restyle_pending = True

    def _restyle(self) -> None:
        """Rebuild every kept row's strip with the current palette (TT15). The engine is not read, so a held
        frame keeps its last content, recoloured; the padding carries the new term background."""
        for y, row in enumerate(self._rows):
            if row is not None and y < len(self._strips):
                self._strips[y] = self._build_strip(row)

    def pan_to(self, x: int, y: int) -> None:
        """Move the window over the grid, clamped to it."""
        if self._set_view(x, y):
            self.refresh()

    # -- the paint timer --

    def on_mount(self) -> None:
        self._timer = self.set_interval(1.0 / self._max_fps, self._tick)

    def _tick(self) -> None:
        if self._released or not self._pending:
            return
        if not (self.in_front or self._paint_background):
            return
        if self._backend.hold:
            now = time.monotonic()
            if self._hold_since is None:
                self._hold_since = now
            if now - self._hold_since < HOLD_LIMIT_S:
                return
        self._hold_since = None
        self.repaint_now()

    def repaint_now(self) -> None:
        """Read the engine's dirty rows and repaint exactly those lines (plus the cursor's).

        The whole engine read (update, dirty rows, cursor, clean) is one unit under the backend lock
        (DT20), so a feed slice cannot land between reading the rows and marking them clean; the
        strips are built after the lock is released, so a busy feed loses at most this one read.
        """
        if self._released:
            return
        backend = self._backend
        waiting = time.perf_counter()
        with backend.lock():
            self.lock_wait_ms += (time.perf_counter() - waiting) * 1000.0
            rows = backend.dirty_rows()
            cursor = backend.cursor()
            backend.mark_clean()
        self._pending = False
        self.paint_count += 1
        if len(self._strips) != backend.rows:
            self._reset_rows()
        before = self._cursor_rows()
        for row in rows:
            if row.y < len(self._strips):
                self._strips[row.y] = self._build_strip(row)
                self._rows[row.y] = row
        self.rebuilt_rows = [row.y for row in rows]
        self._cursor = cursor if cursor.visible else None
        if self._follow_cursor():
            self.refresh()
            return
        self._refresh_rows(*self.rebuilt_rows, *before, *self._cursor_rows())

    # -- strips --

    def _build_strip(self, row: Row) -> Strip:
        """One row as runs of equal style. A wide character's spacer tail adds no text: the head
        already spans both columns. Style ids are resolved against this row's own map."""
        segments: list[Segment] = []
        memo: dict[int, Style] = {}
        for style_id, run in groupby(row.cells, _STYLE_ID):
            text = "".join([c.text or " " for c in run if not c.wide_tail])
            if text:
                segments.append(Segment(text, self._style_of(row, style_id, memo)))
        strip = Strip(segments)
        cols = self._backend.cols
        if strip.cell_length != cols:
            # The engine and the renderer disagree on some character's width; keep the row the
            # grid's width so the rows below it do not slide.
            strip = strip.adjust_cell_length(cols, self._palette.default)
        return strip

    def _style_of(self, row: Row, style_id: int, memo: dict[int, Style]) -> Style:
        style = memo.get(style_id)
        if style is None:
            style = self._palette.default if style_id == 0 else self._palette.style(
                row.style_for(style_id))
            memo[style_id] = style
        return style

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        grid_y = y + self._view_y
        strip = self._strips[grid_y] if 0 <= grid_y < len(self._strips) else None
        # Phase 1: default cells carry term_bg. Strip.blank and adjust_cell_length use palette.default.
        if strip is None:
            return Strip.blank(width, self._palette.default)
        if self._dimmed:
            return strip.crop(self._view_x, self._view_x + width).adjust_cell_length(
                width, self._palette.default).apply_style(Style(dim=True))
        cursor = self._cursor
        if cursor is not None and cursor.y == grid_y:
            strip = self._with_cursor(strip, grid_y, cursor)
        return strip.crop(self._view_x, self._view_x + width).adjust_cell_length(width, self._palette.default)

    def _with_cursor(self, strip: Strip, grid_y: int, cursor: Cursor) -> Strip:
        x = cursor.x - 1 if cursor.wide_tail else cursor.x
        row = self._rows[grid_y]
        cells = row.cells if row is not None else None
        if cells is None or not 0 <= x < len(cells):
            return strip
        span = 2 if cells[x].wide == G.CELL_WIDE_WIDE else 1
        look = self._palette.cursor_typist if self._typist else self._palette.cursor_viewer
        under = strip.crop(x, x + span)
        drawn = Strip([
            Segment(seg.text, self._palette.cursor_style(look, seg.style or Style())) for seg in under
        ])
        return Strip.join([strip.crop(0, x), drawn, strip.crop(x + span)])

    # -- the window over the grid --

    def _set_view(self, x: int, y: int) -> bool:
        width, height = self.size.width, self.size.height
        x = max(0, min(x, max(self._backend.cols - width, 0)))
        y = max(0, min(y, max(self._backend.rows - height, 0)))
        if (x, y) == (self._view_x, self._view_y):
            return False
        self._view_x, self._view_y = x, y
        self.post_message(self.ViewChanged(self))
        return True

    def _follow_cursor(self) -> bool:
        """Pan the least distance that brings the cursor into view. True when the window moved."""
        cursor = self._cursor
        width, height = self.size.width, self.size.height
        if not self._follow_enabled or cursor is None or width <= 0 or height <= 0:
            return False
        x, y = self._view_x, self._view_y
        if cursor.x < x:
            x = cursor.x
        elif cursor.x >= x + width:
            x = cursor.x - width + 1
        if cursor.y < y:
            y = cursor.y
        elif cursor.y >= y + height:
            y = cursor.y - height + 1
        return self._set_view(x, y)

    def on_resize(self, event: events.Resize) -> None:
        # The widget's own size says how much of the grid is visible; it never changes the grid and
        # never asks the session for anything (D3, D18): the app's window change is the only trigger.
        if self._set_view(self._view_x, self._view_y) or self._follow_cursor():
            self.refresh()
        self.post_message(self.Resized(self, event.size))

    # -- the mouse, as grid cells for the screen (D13) --

    def on_mouse_down(self, event: events.MouseDown) -> None:
        # Held until the button comes up, so a drag that leaves the canvas still ends with a release
        # the session sees, and a program tracking buttons is never left thinking one is down.
        self.capture_mouse()
        self._report_mouse(event, G.MOUSE_ACTION_PRESS, _BUTTONS.get(event.button))

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self._report_mouse(event, G.MOUSE_ACTION_RELEASE, _BUTTONS.get(event.button))

    def on_mouse_move(self, event: events.MouseMove) -> None:
        cell = self._grid_cell(event)
        if cell == self._last_motion:
            return  # a report is per cell, never per pixel-row of travel
        self._last_motion = cell
        self._report_mouse(event, G.MOUSE_ACTION_MOTION, _BUTTONS.get(event.button))

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._report_mouse(event, G.MOUSE_ACTION_PRESS, _WHEEL_UP)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._report_mouse(event, G.MOUSE_ACTION_PRESS, _WHEEL_DOWN)

    def _grid_cell(self, event: events.MouseEvent) -> tuple[int, int]:
        x = min(max(event.x + self._view_x, 0), self._backend.cols - 1)
        y = min(max(event.y + self._view_y, 0), self._backend.rows - 1)
        return x, y

    def _report_mouse(self, event: events.MouseEvent, action: int, button: int | None) -> None:
        event.stop()
        if self._released:
            return
        x, y = self._grid_cell(event)
        mods = (G.MODS_CTRL if event.ctrl else 0) | (G.MODS_ALT if event.meta else 0)
        self.post_message(self.MouseReport(self, action, button, x, y, mods, event.shift))

    # -- bookkeeping --

    def _reset_rows(self) -> None:
        rows = self._backend.rows
        self._strips = [None] * rows
        self._rows = [None] * rows
        self._cursor = None
        self._set_view(self._view_x, self._view_y)
        self.refresh()

    def _cursor_rows(self) -> list[int]:
        return [] if self._cursor is None else [self._cursor.y]

    def _refresh_rows(self, *grid_rows: int) -> None:
        """Mark just these grid rows' lines dirty, so Textual re-renders them and no others."""
        width, height = self.size.width, self.size.height
        regions = [Region(0, y - self._view_y, width, 1) for y in set(grid_rows)
                   if 0 <= y - self._view_y < height]
        if regions:
            self.refresh(*regions)
