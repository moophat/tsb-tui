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
* **Marks sit on the engine's rows (search).** A `Highlight` names a row of the engine's row space, and the
  characters of that row's text it covers. At each paint the canvas turns the ones on view into cells of its
  own rows (a wide character is two cells), and `render_line` lays their style over the strip after it is cut to
  the window. A mark belongs to one `generation` of the engine's rows: once bytes arrive it is dropped, since the
  row it named may be another row or another text by then.
* **The cursor says who types (D21).** A filled block in `accent` for the typist, an underline in
  `steel` for a viewer.

Style ids: the engine reuses a style id once no cell holds it and numbers ids per page, so a
viewport that spans two pages can hold one id with two meanings. Each `Row` therefore carries the
styles of its own ids, and a strip is built from its row's map alone; the palette keys its cache by
the style's contents rather than by id.
"""

from __future__ import annotations

import ctypes
import time
from bisect import bisect_left
from collections.abc import Mapping, Sequence
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


def _column_extent(cells) -> int:
    """The columns the row's text occupies, trailing blanks trimmed. A wide glyph counts both its columns (the head
    spans two; its spacer tail adds none)."""
    col = 0
    last = 0
    for c in cells:
        if c.wide_tail:
            continue  # the head already spans this column
        col += 2 if c.wide_head else 1
        if c.text and c.text != " ":
            last = col
    return last

# Textual numbers the buttons 1 left, 2 middle, 3 right; a motion with no button down carries 0.
# The wheel is buttons four and five in xterm's numbering, which mouse/event.h keeps (FOUR, FIVE).
_WHEEL_UP, _WHEEL_DOWN = 4, 5
_BUTTONS = {1: G.MOUSE_BUTTON_LEFT, 2: G.MOUSE_BUTTON_MIDDLE, 3: G.MOUSE_BUTTON_RIGHT}


class Highlight(NamedTuple):
    """A mark: the characters [start, end) of the text of one row of the engine's row space, drawn in `style` over
    whatever the row's cells already show."""

    row: int
    start: int
    end: int
    style: Style


def _cells_of(row: Row, start: int, end: int) -> tuple[int, int] | None:
    """The cells [x_start, x_end) that show characters [start, end) of the row's text, or None when it has no such
    characters. A cell holds a whole grapheme (`len(text)` characters, a blank one space), a wide character's spacer
    tail holds none, and a wide character covers two cells."""
    first: int | None = None
    last = 0
    position = 0
    for x, cell in enumerate(row.cells):
        if cell.wide_tail:
            continue
        width = 2 if cell.wide == G.CELL_WIDE_WIDE else 1
        following = position + (len(cell.text) or 1)
        if following > start and position < end:
            if first is None:
                first = x
            last = x + width
        position = following
        if position >= end:
            break
    return None if first is None else (first, last)


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
        # True while the scroll view is open: the window follows the view then (the screen pans it to a match), and
        # never the live cursor, which the viewport is not showing.
        self.follow_paused = False
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
        # The marks, sorted by row, the engine generation they were made in, and the ones on view as cells:
        # grid row -> [(x_start, x_end, style)].
        self._highlights: Sequence[Highlight] = ()
        self._highlight_rows: list[int] = []
        self._highlight_generation = 0
        # The selected rows (first, last, style) of the engine's row space: whole rows, kept whatever the engine
        # generation, since the screen decides when a selection no longer holds.
        self._selection: tuple[int, int, Style] | None = None
        self._overlay: dict[int, list[tuple[int, int, Style]]] = {}
        # Counters the tests (and phase 10's benchmark) read: how many paints, which rows the last
        # paint rebuilt.
        self.paint_count = 0
        # How long the paint has waited for the engine lock (DT20): the loop's cost of a firehose, which
        # the design note's §4 asks the bench to report. The lock is taken per paint; this is the total.
        self.lock_wait_ms = 0.0
        self.rebuilt_rows: list[int] = []
        # Ruled O4/O7 prompt decoration. `_prompt_pattern` is the target's compiled I-Regexp (None when the target has
        # no pattern, or its pattern is unusable). A mark covers the prompt's cells, not a row (QODO-121-2 #2): each
        # `_pattern_marks` entry is (first_handle, last_handle, screen) — two tracked references made under the same
        # lock as the match, one at the prompt's first cell (column 0) and one at its last matched cell. A tracked
        # reference follows its cell, so the mark survives scrolling and a reflow that wraps the prompt across rows.
        # `_pattern_spans` maps each viewport row a mark covers now to the inclusive (start_col, end_col) of its prompt
        # cells in reading order; only cells inside a span are decorated, so cells written later on the row (the echoed
        # input) are not. Rows from before this attach are never marked: it starts empty.
        self._prompt_pattern = None
        self._pattern_marks: list[tuple[ctypes.c_void_p, ctypes.c_void_p, int]] = []
        self._pattern_spans: dict[int, tuple[int, int]] = {}

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
        self._clear_pattern_state()
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
        self._reset_rows(keep_pattern=True)  # QODO-121-1 #3: a resize reflows content; tracked marks survive.
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

    def show_row(self, grid_y: int) -> None:
        """Pan the window the least distance that brings grid row `grid_y` into it (a window shorter than the grid
        would otherwise leave the viewport's top rows out of sight)."""
        height = self.size.height
        if height <= 0:
            return
        y = grid_y if grid_y < self._view_y else max(self._view_y, grid_y - height + 1)
        self.pan_to(self._view_x, y)

    @property
    def page_rows(self) -> int:
        """The rows one page of the scroll view moves: those the window shows of the engine's viewport, which is no
        taller than the grid, so a window taller than the grid does not skip rows it never showed."""
        return max(1, min(self.size.height, self._backend.rows))

    def scroll_by(self, rows: int) -> None:
        """Move the engine's viewport `rows` rows (up is negative); the next paint shows the rows it lands on."""
        self._scroll(G.SCROLL_VIEWPORT_DELTA, rows)

    def scroll_to_bottom(self) -> None:
        """Put the viewport back on the live screen."""
        self._scroll(G.SCROLL_VIEWPORT_BOTTOM)

    def _scroll(self, kind: int, amount: int = 0) -> None:
        if self._released:
            return
        self._backend.scroll_viewport(kind, amount)
        self._pending = True

    def scroll_to_row(self, row: int) -> None:
        """Put `row` (of the engine's row space) at the top of the viewport; the engine keeps the last window on the
        bottom of what it holds, so the rows below the clamp show with the row somewhere in the window."""
        self._scroll(G.SCROLL_VIEWPORT_ROW, row)

    # -- marks --

    def set_highlights(self, highlights: Sequence[Highlight], generation: int) -> None:
        """Mark these (sorted by row and column), found in the engine's rows as of `generation`; the next paint puts
        them on the cells. They replace whatever was marked before."""
        self._highlights = tuple(highlights)
        self._highlight_rows = [h.row for h in self._highlights]
        self._highlight_generation = generation
        self._pending = True

    def clear_highlights(self) -> None:
        if self._highlights or self._overlay:
            self._highlights = ()
            self._highlight_rows = []
            self._overlay = {}
            self._pending = self._pending or self._selection is not None  # its cells are laid again at the next paint
            self.refresh()

    def set_selection(self, first: int, last: int, style: Style) -> None:
        """Mark the whole rows `first`..`last` of the engine's row space in `style`; the next paint puts it on them."""
        self._selection = (first, last, style)
        self._pending = True

    def clear_selection(self) -> None:
        if self._selection is not None:
            self._selection = None
            self._pending = True

    def _clear_pattern_state(self) -> None:
        """Free every tracked pattern handle and drop the marks and spans (set a new pattern, a reset, a close)."""
        for first, last, _screen in self._pattern_marks:
            self._backend.free_tracked(first)
            self._backend.free_tracked(last)
        self._pattern_marks = []
        self._pattern_spans = {}

    def set_prompt_pattern(self, compiled: Any) -> None:
        """The target's compiled prompt pattern (Ruled O4), or None. Marks from a prior pattern are dropped and their
        tracked references freed, so rows from before this attach, and rows marked by the previous pattern, stay
        unmarked; the next paint marks the cursor row when it matches."""
        self._clear_pattern_state()
        self._prompt_pattern = compiled
        self._pending = True

    def _update_pattern_rows(self, cursor: Cursor | None) -> None:
        """The prompt cells on view as reading-order spans per viewport row. A mark is the prompt's first and last
        cells (two tracked references); a tracked reference follows its cell, so the mark survives scrolling and a
        reflow that wraps the prompt across rows (QODO-121-2 #2). A mark whose ends have no value is freed; an end
        merely off the viewport, on a non-active screen, is kept so its mark reappears. OSC 133 wins where it marked
        the cursor row, so a shell-marked row gets no pattern track."""
        if self._prompt_pattern is None:
            self._pattern_spans = {}
            return
        # QODO-121-1 #5: read the scrollback offset once, and the active screen once, this update.
        offset = self._backend.scrollback_offset()
        active = self._backend.active_screen()
        cols = self._backend.cols
        spans: dict[int, tuple[int, int]] = {}

        def add(first, last, screen):
            # QODO-121-3: resolve both ends in whole-screen coordinates (checked against their owning screen) even when
            # one is scrolled out of the viewport, take the reading-order span between them, and decorate only the part
            # inside the viewport. A mark whose ends are on a non-active screen (or have no value) is kept, not drawn.
            fc = self._backend.tracked_screen_coord(first, active, screen)
            lc = self._backend.tracked_screen_coord(last, active, screen)
            if fc is None or lc is None:
                return
            (fr, fcol), (lr, lcol) = fc, lc
            for r in range(fr, lr + 1):
                y = r - offset
                if 0 <= y < self._backend.rows:
                    start = fcol if r == fr else 0
                    end = lcol if r == lr else cols - 1
                    spans[y] = (start, end)

        keep: list[tuple[ctypes.c_void_p, ctypes.c_void_p, int]] = []
        for first, last, screen in self._pattern_marks:
            # QODO-121-1 #5: a reference that no longer resolves to retained content is freed and dropped.
            if not self._backend.tracked_has_value(first) or not self._backend.tracked_has_value(last):
                self._backend.free_tracked(first)
                self._backend.free_tracked(last)
                continue
            keep.append((first, last, screen))
            add(first, last, screen)
        self._pattern_marks = keep
        # QODO-121-1 #4/#6 and QODO-121-2 #1/#2: capture the render-state cursor and its row, match, and create the
        # first + last tracked references under one backend lock. OSC 133 wins on the row (the backend drops a
        # shell-marked row); no match is made while the view is scrolled away from the live cursor.
        matched = self._backend.track_matching_prompt(
            lambda text: self._prompt_pattern.matches(text), _column_extent)
        if matched:
            first, last, screen = matched
            self._pattern_marks.append((first, last, screen))
            add(first, last, screen)
        self._pattern_spans = spans

    def _rebuild_overlay(self, origin: int) -> bool:
        """The marks on view as cells of this canvas's rows, given the engine row the viewport starts at. True when
        they differ from what was drawn."""
        if self._highlights and self._highlight_generation != self._backend.generation:
            self._highlights, self._highlight_rows = (), []
        overlay: dict[int, list[tuple[int, int, Style]]] = {}
        if self._selection is not None:
            first, last, style = self._selection
            for grid_y in range(max(first - origin, 0), min(last - origin, len(self._rows) - 1) + 1):
                overlay[grid_y] = [(0, self._backend.cols, style)]  # laid first, so a match's style goes over it
        if self._highlights:
            index = bisect_left(self._highlight_rows, origin)
            while index < len(self._highlights) and self._highlights[index].row < origin + len(self._rows):
                mark = self._highlights[index]
                index += 1
                grid_y = mark.row - origin
                row = self._rows[grid_y]
                cells = _cells_of(row, mark.start, mark.end) if row is not None else None
                if cells is not None:
                    overlay.setdefault(grid_y, []).append((cells[0], cells[1], mark.style))
        changed = overlay != self._overlay
        self._overlay = overlay
        return changed

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
            # Cleared under the lock, before the read: a feed that finishes after this paint leaves the lock sets
            # it again, so the rows it dirtied are never marked clean unseen.
            self._pending = False
            rows = backend.dirty_rows()
            cursor = backend.cursor()
            origin = backend.scrollback_position().offset if self._highlights or self._selection else 0
            backend.mark_clean()
        self.paint_count += 1
        if len(self._strips) != backend.rows:
            self._reset_rows(keep_pattern=True)  # row-cache reallocation at the new geometry; marks survive.
        before = self._cursor_rows()
        for row in rows:
            if row.y < len(self._rows):
                self._rows[row.y] = row
        # The pattern mark depends on the cursor row's text, so read every dirty row first, then mark, then build
        # each strip (a newly-marked row must paint in the same pass it was matched on).
        self._cursor = cursor if cursor.visible else None
        self._update_pattern_rows(cursor)
        dirty_set = {row.y for row in rows}
        for row in rows:
            if row.y < len(self._strips):
                self._strips[row.y] = self._build_strip(row)
        # QODO-121-1 #1: a pattern-marked row the engine reports clean (the pattern arrived after the row was painted,
        # and the shell emits nothing further) must still rebuild its cached strip, so the mark shows now.
        extra = [y for y in self._pattern_spans if y not in dirty_set
                 and y < len(self._rows) and self._rows[y] is not None]
        for y in extra:
            self._strips[y] = self._build_strip(self._rows[y])
        self.rebuilt_rows = [row.y for row in rows] + extra
        marked = self._rebuild_overlay(origin)
        if self._follow_cursor() or marked:
            self.refresh()
            return
        self._refresh_rows(*self.rebuilt_rows, *before, *self._cursor_rows())

    # -- strips --

    def _build_strip(self, row: Row) -> Strip:
        """One row as runs of equal style. A wide character's spacer tail adds no text: the head
        already spans both columns. Style ids are resolved against this row's own map. Ruled O4/O7: a cell the shell
        marked as prompt (OSC 133, its semantic content), and the cells below the matched extent of a row the target
        pattern matched, get the bold + underline decoration, never a colour. Cells written later on a pattern-matched
        row (the echoed input) are below the recorded extent and stay undecorated; nothing else is touched."""
        segments: list[Segment] = []
        memo: dict[int, Style] = {}
        # QODO-121-1 #2 (Ruled O4): OSC 133 wins on a row. If the shell marked any prompt cell here, the pattern's
        # span is dropped for this row — decoration comes from the shell's prompt cells only, so its input and output
        # stay plain even when the row was pattern-tracked before the marks arrived.
        span = None if any(c.semantic == G.CELL_SEMANTIC_PROMPT for c in row.cells) \
            else self._pattern_spans.get(row.y)
        keyed: list[tuple[int, bool, object]] = []
        col = 0
        for c in row.cells:
            decorated = c.semantic == G.CELL_SEMANTIC_PROMPT or (span is not None and span[0] <= col <= span[1])
            keyed.append((c.style_id, decorated, c))
            if not c.wide_tail:
                col += 2 if c.wide_head else 1
        # Group by style id and by decorated, so a prompt cell splits from the input/output cells it shares a
        # style id with. `run` is a lazy groupby sub-iterator, so materialize it before reading its cells twice.
        for (style_id, decorated), run in groupby(keyed, lambda x: (x[0], x[1])):
            cells = list(run)
            text = "".join([t[2].text or " " for t in cells if not t[2].wide_tail])
            if text:
                style = self._style_of(row, style_id, memo)
                if decorated:
                    style = style + self._palette.prompt_decoration
                segments.append(Segment(text, style))
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
        strip = strip.crop(self._view_x, self._view_x + width).adjust_cell_length(width, self._palette.default)
        marks = self._overlay.get(grid_y)
        return self._with_marks(strip, marks) if marks else strip

    def _with_marks(self, strip: Strip, marks: list[tuple[int, int, Style]]) -> Strip:
        """Lay each mark's style over the cells it covers, in the window's own columns (`strip` is already cut)."""
        width = strip.cell_length
        for x_start, x_end, style in marks:
            start, end = max(x_start - self._view_x, 0), min(x_end - self._view_x, width)
            if start >= end:
                continue
            marked = Strip([Segment(seg.text, (seg.style or Style.null()) + style, seg.control)
                            for seg in strip.crop(start, end)])
            strip = Strip.join([strip.crop(0, start), marked, strip.crop(end)])
        return strip

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
        if not self._follow_enabled or self.follow_paused or cursor is None or width <= 0 or height <= 0:
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

    def _reset_rows(self, keep_pattern: bool = False) -> None:
        rows = self._backend.rows
        # A dump rebuilt the grid at a new geometry: every tracked reference the engine held is gone, so free the
        # pattern's handles (the pattern itself stays — the target did not change) and start unmarked again. QODO-121-1
        # #3: a resize is not a reset — the engine preserves and reflows its content and a tracked reference follows
        # its cell, so `keep_pattern` retains the marks (and their extents) across the new geometry.
        if not keep_pattern:
            self._clear_pattern_state()
        self._strips = [None] * rows
        self._rows = [None] * rows
        self._cursor = None
        # The caches above are gone, and a paint that already ran has marked the engine's rows clean: ask the engine
        # for every row again, or nothing repaints them until the session happens to write.
        self._backend.mark_all_dirty()
        self._pending = True
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
