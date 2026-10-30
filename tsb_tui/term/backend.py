"""`VTBackend`: bytes in, a paint-ready grid and text reads out, over libghostty-vt.

Why a seam like this (design §3.2, §3.3): the TUI must render with the *same engine the core's VT
worker uses*, so the attach prefix it rebuilds is the one the core checked (immortality §5.2: "each
surface checks it with its own renderer, the TUI first"). This module is that renderer. Nothing
above it (canvas, models, drive) knows libghostty exists.

What this phase builds and later phases build on:

- **The grid.** `feed(bytes)`, `resize(cols, rows)`, `reset(cols, rows)`. `resize` keeps exactly the
  size asked; `reset` gives a fresh terminal. `dirty_rows()` reads the render state's dirty rows in
  one pass, each row's cells through the bulk `ROW_CELLS_RAW` view copied into an `array('Q')`, and
  interns the decoded `Cell` by its packed value, and resolves each row's `style_id`s onto that
  `Row` (ids are per engine page), so painting never builds a style or a cell per repetition (plan R4; see the phase report's "Go/no-go follow-up").
- **Modes.** `modes()` reports the modes the input path and the encoders need: alternate screen,
  DECCKM, keypad, bracketed paste, the mouse modes, focus events, the kitty keyboard flags and
  synchronized output.
- **Effects (DT3).** `WRITE_PTY` and `DEVICE_ATTRIBUTES` deliver their bytes to **one** reply
  callback the backend's owner sets. `RENDER_HOLD` is a flag the canvas reads. Clipboard, `XTVERSION`,
  `ENQUIRY` and `COLOR_SCHEME` are **never** installed — an OSC 52 read must never pull the
  operator's clipboard. `reset` and a `muted()` context stop every reply, so a query recorded in a
  dump is never answered again.
- **Text reads for drive (D50).** The visible rows, the scrollback, and the text since a tracked
  mark — which survives scrolling, because the mark is a tracked grid reference that follows its
  cell (grid_ref_tracked.h). Wrapped lines can be joined; every read is capped by the caller.
"""

from __future__ import annotations

import array
import ctypes
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import NamedTuple

from . import ghostty as G
from .ghostty import GhosttyLib

# How much text any single read returns when the caller names no cap. Every read takes its own cap
# (D50 caps `reply` at 5000 lines); this is only the "you did not say" default.
DEFAULT_TEXT_CAP = 5000

# Cell size for `terminal_resize`'s pixel arguments. The TUI renders with the Line API, not in
# pixels, but the engine takes a cell size; a nominal one keeps size reports and XTWINOPS answers
# sane without pulling in a font metric.
CELL_WIDTH_PX = 10
CELL_HEIGHT_PX = 20

# DT20: the feed thread hands the engine this many bytes at a time, releasing the lock between
# slices so a loop-side caller (a paint, a `modes()` read) can take it. Small enough that a busy
# feed yields often; large enough that the per-slice overhead is not the cost.
FEED_SLICE_BYTES = 32 << 10


# The packed-cell field layout (screen.h), as plain ints: the decode runs once per distinct cell value
# on the paint path, so it must not look attributes up per field.
_TAG_LSB, _TAG_MASK = G.CELL_CONTENT_TAG_LSB, (1 << G.CELL_CONTENT_TAG_WIDTH) - 1
_CODEPOINT_LSB, _CODEPOINT_MASK = G.CELL_CODEPOINT_LSB, (1 << G.CELL_CODEPOINT_WIDTH) - 1
_STYLE_ID_LSB, _STYLE_ID_MASK = G.CELL_STYLE_ID_LSB, (1 << G.CELL_STYLE_ID_WIDTH) - 1
_WIDE_LSB, _WIDE_MASK = G.CELL_WIDE_LSB, (1 << G.CELL_WIDE_WIDTH) - 1
_TAG_CODEPOINT = G.CELL_CONTENT_CODEPOINT
_TAG_GRAPHEME = G.CELL_CONTENT_CODEPOINT_GRAPHEME
_WIDE_TAIL = G.CELL_WIDE_SPACER_TAIL
_WIDE_HEAD = G.CELL_WIDE_SPACER_HEAD
# A decoded Cell depends only on its packed value, so decodes persist across reads; the cap bounds a
# session that keeps printing new glyphs (the table is rebuilt from scratch when it fills).
_DECODED_CELLS_MAX = 1 << 16


class BackendError(RuntimeError):
    """The engine refused an operation the backend cannot recover from."""


class Cell:
    """One grid cell: the grapheme, the style it resolves to, and its wide-cell flag.

    `text` is the grapheme cluster the cell holds (a base codepoint plus any combining marks);
    a wide character's spacer tail carries no text and is not painted. `style_id` is the engine's
    identity for the cell's style, which the backend caches so painting resolves it once per
    distinct id, not once per cell (plan R4).

    `Cell` is a `__slots__` class rather than a dataclass: the go/no-go profile showed the full-grid
    read was almost entirely per-cell Python object construction, not FFI (see the phase report).
    Cells are interned by their packed raw value, so a grid of blanks builds one object, and each
    distinct glyph/style pair builds one — the fields and their values are unchanged.
    """

    __slots__ = ("style_id", "text", "wide", "wide_head", "wide_tail")

    def __init__(
        self, text: str, style_id: int, wide: int, wide_tail: bool = False, wide_head: bool = False
    ) -> None:
        self.text = text
        self.style_id = style_id
        self.wide = wide  # one of GhosttyCellWide
        self.wide_tail = wide_tail
        self.wide_head = wide_head

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Cell):
            return NotImplemented
        return (
            self.text == other.text
            and self.style_id == other.style_id
            and self.wide == other.wide
            and self.wide_tail == other.wide_tail
            and self.wide_head == other.wide_head
        )

    def __repr__(self) -> str:
        return (
            f"Cell(text={self.text!r}, style_id={self.style_id}, wide={self.wide}, "
            f"wide_tail={self.wide_tail}, wide_head={self.wide_head})"
        )

    def __hash__(self) -> int:
        return hash((self.text, self.style_id, self.wide, self.wide_tail, self.wide_head))


@dataclass(frozen=True)
class Row:
    """One row of the render state: its cells and its row identity."""

    y: int
    cells: Sequence[Cell]
    row_id: tuple[int, int]
    # The styles of this row's non-default `style_id`s. Ids are numbered per engine page, so the same
    # id on two rows can mean two styles when the viewport spans two pages: an id is only meaningful
    # with the row that carries it.
    styles: Mapping[int, G.GhosttyStyle] = field(default_factory=dict)

    def style_for(self, style_id: int) -> G.GhosttyStyle | None:
        """The resolved style of a cell on this row, or None for the default style (id 0)."""
        return self.styles.get(style_id)


@dataclass(frozen=True)
class Cursor:
    """Where the cursor is and whether it is on the tail of a wide character."""

    x: int
    y: int
    visible: bool
    wide_tail: bool = False


class RowText(NamedTuple):
    """One row of the engine's row space as text: what its cells hold across the full width (a blank cell is a space, a
    wide character is one character, a spacer is nothing), and whether the engine marked the row as soft-wrapped into
    the next row."""

    text: str
    wrapped: bool


class ScrollPosition(NamedTuple):
    """Where the viewport is in the scrollable area, in the engine's row space: `total` rows (scrollback plus the
    active area), the viewport's `offset` from the top, and its `length`. At the bottom, `offset + length == total`.
    Row n of this space is line n of `grid_lines()`."""

    total: int
    offset: int
    length: int


@dataclass(frozen=True)
class Modes:
    """The input-side modes the key, paste and mouse encoders must honour.

    libghostty's own key encoder reads these from the terminal when the session sets them
    (key/encoder.h:186 `setopt_from_terminal`); the TUI reads them too, so the input path and the
    status row can show what the session asked for without duplicating the encoder's logic.
    """

    alternate_screen: bool
    cursor_keys_application: bool  # DECCKM, mode 1
    keypad_application: bool  # mode 66
    bracketed_paste: bool  # mode 2004
    mouse_tracking: bool
    mouse_mode: int  # GhosttyMouseTrackingMode
    focus_events: bool  # mode 1004
    kitty_flags: int
    synchronized_output: bool  # mode 2026


def _mode(value: int, ansi: bool) -> int:
    """Pack a mode number the way modes.h:118 `ghostty_mode_new` does: value | (ansi << 15)."""
    return (value & 0x7FFF) | ((1 << 15) if ansi else 0)


# The modes `modes()` reports, as (manifest_mode_number, is_ansi).
_MODE_DECCKM = _mode(1, False)
_MODE_KEYPAD = _mode(66, False)
_MODE_BRACKETED_PASTE = _mode(2004, False)
_MODE_FOCUS = _mode(1004, False)
_MODE_MOUSE_X10 = _mode(9, False)
_MODE_MOUSE_NORMAL = _mode(1000, False)
_MODE_MOUSE_BUTTON = _mode(1002, False)
_MODE_MOUSE_ANY = _mode(1003, False)
_MODE_SYNC_OUTPUT = _mode(2026, False)
_MODE_KITTY_PASTE_EVENTS = _mode(5522, False)


class VTBackend:
    """One session's screen state, fed the broker's byte stream verbatim.

    The backend owns a libghostty terminal plus a render state. Every method that touches the engine
    holds one re-entrant lock (`_lock`, DT20), because feeding now happens on a per-attachment feed
    thread while the paint and the input path read the terminal on the app's loop. The lock covers
    feed, reset, resize, `modes`, the drive text reads, `set_mark`, and the encoders'
    `setopt_from_terminal` (through `terminal_handle`). The paint holds it for its whole engine read
    (update, dirty rows, cursor, clean) and builds its strips after releasing it. A slice of feed
    that finds the lock taken by a loop-side caller yields rather than spins, so a busy feed never
    starves the paint; `close` takes the lock too, so a slice still waiting after a close does
    nothing rather than writing to a freed terminal.
    """

    def __init__(self, lib: GhosttyLib, cols: int, rows: int, *, scrollback_bytes: int) -> None:
        """A fresh terminal at cols × rows with the given scrollback budget (DT18)."""
        if cols < 1 or rows < 1:
            raise ValueError(f"cols and rows must be positive, got {cols}x{rows}")
        self._lib = lib
        self._libc = lib.lib
        self.cols = cols
        self.rows = rows
        self.scrollback_bytes = scrollback_bytes
        self._lock = threading.RLock()
        self._muted = 0
        # Changes whenever the rows the engine holds may have changed (bytes fed, a resize, a reset), never for a
        # move of the viewport: what a reader formatted at one value is stale at another.
        self.generation = 0
        self._reply_callback: Callable[[bytes], None] | None = None
        # While a DT20 feed run is in flight on the feed thread, replies are collected here instead
        # of going to `_reply_callback`, so the processor delivers them on the loop in order.
        self._reply_sink: list[bytes] | None = None
        self._held = False
        self._decoded_cells: dict[int, Cell] = {}
        self._marks: dict[str, ctypes.c_void_p] = {}
        self._cells_buf = ctypes.create_string_buffer(64)
        self._make_terminal(cols, rows)

        # Effect callbacks must outlive the terminal; keep them referenced.
        self._write_pty_cb = G.WritePtyFn(self._on_write_pty)
        self._device_attributes_cb = G.DeviceAttributesFn(self._on_device_attributes)
        self._render_hold_cb = G.RenderHoldFn(self._on_render_hold)
        self._install_effects()

        self._render_state = ctypes.c_void_p()
        self._make_render_state()
        self._row_iterator = ctypes.c_void_p()
        self._row_cells = ctypes.c_void_p()
        self._make_row_iterators()

    # -- construction / teardown --

    def _make_terminal(self, cols: int, rows: int) -> None:
        handle = ctypes.c_void_p()
        rc = self._libc.ghostty_terminal_new(
            None, ctypes.byref(handle), ctypes.c_uint16(cols), ctypes.c_uint16(rows)
        )
        if rc != G.GHOSTTY_SUCCESS or not handle:
            raise BackendError(f"ghostty_terminal_new failed with result {rc}")
        self._terminal = handle
        self._set_size_option(scrollback_bytes=self.scrollback_bytes)
        # Kitty image storage is 0 (VT plan DV10): images never enter a dump, and target output
        # must not pin N × 10 MB per session in this process.
        zero = ctypes.c_uint64(0)
        self._libc.ghostty_terminal_set(
            self._terminal, G.OPT_KITTY_IMAGE_STORAGE_LIMIT, ctypes.byref(zero)
        )

    def _set_size_option(self, *, scrollback_bytes: int) -> None:
        limit = ctypes.c_size_t(scrollback_bytes)
        self._libc.ghostty_terminal_set(
            self._terminal, G.OPT_SCROLLBACK_MAX_BYTES, ctypes.byref(limit)
        )

    def _install_effects(self) -> None:
        """Install WRITE_PTY, DEVICE_ATTRIBUTES and RENDER_HOLD, and leave every other reply
        effect unset (DT3). A NULL callback makes the engine silently ignore that sequence."""
        L = self._libc
        L.ghostty_terminal_set(
            self._terminal,
            G.OPT_WRITE_PTY,
            ctypes.cast(self._write_pty_cb, ctypes.c_void_p),
        )
        L.ghostty_terminal_set(
            self._terminal,
            G.OPT_DEVICE_ATTRIBUTES,
            ctypes.cast(self._device_attributes_cb, ctypes.c_void_p),
        )
        L.ghostty_terminal_set(
            self._terminal,
            G.OPT_RENDER_HOLD,
            ctypes.cast(self._render_hold_cb, ctypes.c_void_p),
        )

    def _make_render_state(self) -> None:
        handle = ctypes.c_void_p()
        rc = self._libc.ghostty_render_state_new(None, ctypes.byref(handle))
        if rc != G.GHOSTTY_SUCCESS or not handle:
            raise BackendError(f"ghostty_render_state_new failed with result {rc}")
        self._render_state = handle

    def _make_row_iterators(self) -> None:
        it = ctypes.c_void_p()
        if self._libc.ghostty_render_state_row_iterator_new(None, ctypes.byref(it)) != G.GHOSTTY_SUCCESS:
            raise BackendError("ghostty_render_state_row_iterator_new failed")
        self._row_iterator = it
        cells = ctypes.c_void_p()
        if self._libc.ghostty_render_state_row_cells_new(None, ctypes.byref(cells)) != G.GHOSTTY_SUCCESS:
            raise BackendError("ghostty_render_state_row_cells_new failed")
        self._row_cells = cells

    def close(self) -> None:
        """Free every engine handle and any tracked mark. Idempotent. Holds the lock (DT20), so a feed
        slice still waiting when this runs finds the terminal freed and does nothing."""
        with self._lock:
            if getattr(self, "_terminal", None) is None:
                return
            for mark in self._marks.values():
                self._libc.ghostty_tracked_grid_ref_free(mark)
            self._marks.clear()
            if self._row_cells:
                self._libc.ghostty_render_state_row_cells_free(self._row_cells)
                self._row_cells = ctypes.c_void_p()
            if self._row_iterator:
                self._libc.ghostty_render_state_row_iterator_free(self._row_iterator)
                self._row_iterator = ctypes.c_void_p()
            if self._render_state:
                self._libc.ghostty_render_state_free(self._render_state)
                self._render_state = ctypes.c_void_p()
            if self._terminal:
                self._libc.ghostty_terminal_free(self._terminal)
                self._terminal = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001, S110  (a finalizer must never raise)
            pass

    # -- input: feed, resize, reset --

    def feed(self, data: bytes) -> None:
        """Parse raw session output into the grid, under the lock (DT20). Never raises on bad bytes
        (terminal.h:2977: the input is untrusted and must not corrupt the terminal). A direct caller
        (a test, drive) gets replies through the callback `set_reply_callback` installed."""
        if not data:
            return
        with self._lock:
            if self._terminal is None:
                return  # closed under us; never write to a freed terminal
            self._libc.ghostty_terminal_vt_write(self._terminal, data, len(data))
            self.generation += 1

    def feed_run(self, data: bytes) -> list[bytes]:
        """Feed one run on the DT20 feed thread: `FEED_SLICE_BYTES` at a time, each slice under the
        lock, releasing it between slices so a loop-side caller (a paint, a `modes()` read) waiting
        for the lock goes first. Replies (WRITE_PTY, DEVICE_ATTRIBUTES) are collected and returned
        instead of calling `_reply_callback`, so the processor delivers them on the loop in order,
        before the next frame is applied. Runs on the executor's own thread, never the loop."""
        replies: list[bytes] = []
        self._reply_sink = replies
        try:
            for start in range(0, len(data), FEED_SLICE_BYTES):
                chunk = data[start:start + FEED_SLICE_BYTES]
                with self._lock:
                    if self._terminal is None:
                        break  # closed under us; never write to a freed terminal
                    self._libc.ghostty_terminal_vt_write(self._terminal, chunk, len(chunk))
                    self.generation += 1
                # Python's lock is not fair: without yielding, a busy feed would starve the paint.
                time.sleep(0)
        finally:
            self._reply_sink = None
        return replies

    def resize(self, cols: int, rows: int) -> None:
        """Resize to exactly cols × rows, under the lock (DT20). The phase's step 3: the backend keeps the
        size asked; a reflow happens only when the session itself resized, never because the window did."""
        if cols < 1 or rows < 1:
            raise ValueError(f"cols and rows must be positive, got {cols}x{rows}")
        with self._lock:
            rc = self._libc.ghostty_terminal_resize(
                self._terminal,
                ctypes.c_uint16(cols),
                ctypes.c_uint16(rows),
                ctypes.c_uint32(CELL_WIDTH_PX),
                ctypes.c_uint32(CELL_HEIGHT_PX),
            )
            if rc != G.GHOSTTY_SUCCESS:
                raise BackendError(f"ghostty_terminal_resize failed with result {rc}")
            self.cols = cols
            self.rows = rows
            self.generation += 1

    def reset(self, cols: int, rows: int) -> None:
        """A fresh terminal at cols × rows, muted for the duration (DT3), under the lock (DT20).

        `reset` is what a reattach's `prefix` frame and a `dump` feed use: every reply effect is
        muted so a device-status report recorded inside the dump is never answered to the session.
        """
        with self._lock:
            self.close()
            self._muted += 1
            try:
                self._make_terminal(cols, rows)
                self._install_effects()
                self._make_render_state()
                self._make_row_iterators()
                self.cols = cols
                self.rows = rows
                self._held = False
                self.generation += 1
            finally:
                self._muted -= 1

    @contextmanager
    def muted(self) -> Iterator[None]:
        """Stop every reply effect for the block, so a dump is never answered (DT3)."""
        self._muted += 1
        try:
            yield
        finally:
            self._muted -= 1

    @contextmanager
    def lock(self) -> Iterator[None]:
        """The backend's engine lock (DT20), re-entrant. A caller that must hold it across several
        engine reads — the paint holds it for update, dirty rows, cursor and clean as one unit — uses
        this; the methods below take it themselves, so a direct caller (a test, drive) is safe too."""
        with self._lock:
            yield

    @property
    def lib(self) -> GhosttyLib:
        """The loaded library, for the encoders in `keys.py`."""
        return self._lib

    @property
    def terminal_handle(self) -> ctypes.c_void_p:
        """The engine's terminal handle. The encoders read the session's modes from it."""
        return self._terminal

    # -- effects --

    def set_reply_callback(self, callback: Callable[[bytes], None] | None) -> None:
        """Set the single callback that receives WRITE_PTY and DEVICE_ATTRIBUTES reply bytes.

        The backend's owner (the input path, phase 4) sets this; while it is None every reply is
        dropped, which is what phase 3 wants before the input path exists.
        """
        self._reply_callback = callback

    def _deliver_reply(self, data: bytes) -> None:
        if self._muted:
            return
        if self._reply_sink is not None:
            # A DT20 feed run is in flight on the feed thread: collect the reply for the processor to
            # deliver on the loop, so DT4 (b) judges it against the state as of the bytes that made it.
            if data:
                self._reply_sink.append(data)
            return
        cb = self._reply_callback
        if cb is not None and data:
            cb(data)

    def _on_write_pty(self, _terminal, _userdata, data, length) -> None:  # pragma: no cover - via C
        # Called by the engine during vt_write with borrowed bytes; copy them before returning.
        self._deliver_reply(ctypes.string_at(data, length))

    def _on_device_attributes(self, _terminal, _userdata, out_attrs) -> bool:  # pragma: no cover - via C
        # A fixed DA answer the TUI declares once (DT3). Fill the struct the engine handed us.
        attrs = ctypes.cast(out_attrs, ctypes.POINTER(G.GhosttyDeviceAttributes)).contents
        # DA1 (primary): the feature list holds DA1 *parameter numbers*, not bits. Conformance 1 is
        # VT100 with the advanced video option; parameter 9 is national replacement character sets.
        # Parameter 1 ("132 columns") must never be advertised: it invites a program to switch to a
        # 132-column screen the backend is not resizing (DECCOLM).
        attrs.primary.conformance_level = 1
        attrs.primary.num_features = 1
        attrs.primary.features[0] = 9  # national replacement character sets
        # DA2 (secondary): the fixed VT220 identity. Unit type 1, firmware version 10, and no ROM
        # cartridge ("Always 0 for emulators").
        attrs.secondary.device_type = 1  # VT220
        attrs.secondary.firmware_version = 10
        attrs.secondary.rom_cartridge = 0
        # DA3 (tertiary): unit id 0.
        attrs.tertiary.unit_id = 0
        return True

    def _on_render_hold(self, _terminal, _userdata, held) -> None:  # pragma: no cover - via C
        self._held = bool(held)

    @property
    def hold(self) -> bool:
        """True while synchronized output (mode 2026) asks the renderer to hold its paint."""
        return self._held

    # -- reading the grid for painting --

    def sync_render_state(self) -> None:
        """Refresh the render state from the terminal, consuming dirty state, under the lock (DT20)."""
        with self._lock:
            self._libc.ghostty_render_state_update(self._render_state, self._terminal)

    def mark_clean(self) -> None:
        """Tell the render state the frame has been painted (render.h:702), under the lock (DT20)."""
        with self._lock:
            self._libc.ghostty_render_state_clean(self._render_state)

    def dirty(self) -> int:
        """The render state's dirty state: 0 clean, 1 partial, 2 full, under the lock (DT20)."""
        with self._lock:
            v = ctypes.c_int()
            self._libc.ghostty_render_state_get(self._render_state, G.RS_DATA_DIRTY, ctypes.byref(v))
            return int(v.value)

    def cursor(self) -> Cursor:
        """The cursor position and visibility (render.h:522), under the lock (DT20)."""
        with self._lock:
            cur = G.GhosttyRenderStateCursor()
            cur.size = ctypes.sizeof(G.GhosttyRenderStateCursor)
            rc = self._libc.ghostty_render_state_get(
                self._render_state, G.RS_DATA_CURSOR, ctypes.byref(cur)
            )
            if rc != G.GHOSTTY_SUCCESS or not cur.viewport_has_value:
                return Cursor(x=0, y=0, visible=False)
            return Cursor(x=cur.viewport_x, y=cur.viewport_y, visible=cur.visible, wide_tail=cur.wide_tail)

    def live_cursor(self) -> Cursor:
        """The cursor in the active area, under the lock: where the session's next byte lands, whichever rows the
        viewport shows. `cursor()` is the render state's view of the viewport, and has nothing to report while the
        viewport is scrolled off the active area; this does not move with it, so a read that must stay the session's
        (drive's) uses this one."""
        with self._lock:
            x, y, visible = ctypes.c_uint16(), ctypes.c_uint16(), ctypes.c_bool()
            if self._terminal is None:
                return Cursor(x=0, y=0, visible=False)
            get = self._libc.ghostty_terminal_get
            get(self._terminal, G.DATA_CURSOR_X, ctypes.byref(x))
            get(self._terminal, G.DATA_CURSOR_Y, ctypes.byref(y))
            get(self._terminal, G.DATA_CURSOR_VISIBLE, ctypes.byref(visible))
            return Cursor(x=x.value, y=y.value, visible=visible.value)

    def dirty_rows(self) -> list[Row]:
        """The dirty rows, each as paint-ready cells with cached style ids.

        Reads the dirty rows in one pass through the row iterator, and each row's cells in one bulk
        read (`ROW_CELLS_RAW`, render.h:460), so painting at 196×59 does not re-read 11.5k cells
        cell-by-cell through FFI (plan R4). The whole read holds the engine lock (DT20); the paint
        calls this as one unit and builds its strips after it returns, so the feed loses at most one
        read per paint tick.
        """
        with self._lock:
            self.sync_render_state()
            self._libc.ghostty_render_state_get(
                self._render_state, G.RS_DATA_ROW_ITERATOR, ctypes.byref(self._row_iterator)
            )
            rows: list[Row] = []
            # Interned cells for this read only. A style is resolved per row, against that row's own
            # page, once per distinct id on the row: the engine reuses an id once no cell holds it, and
            # numbers ids per page, so an id means nothing outside the row that carries it.
            cell_cache: dict[int, Cell] = {}
            y = ctypes.c_uint16()
            next_dirty = self._libc.ghostty_render_state_row_iterator_next_dirty
            get_row = self._libc.ghostty_render_state_row_get
            while next_dirty(self._row_iterator, ctypes.byref(y)):
                cells, styles = self._read_row_cells(cell_cache)
                row_id = G.GhosttyRenderStateRowId()
                get_row(self._row_iterator, 7, ctypes.byref(row_id))  # ROW_DATA_ID
                rows.append(
                    Row(y=y.value, cells=cells, row_id=(row_id.bits[0], row_id.bits[1]), styles=styles)
                )
            return rows

    def _read_row_cells(
        self, cell_cache: dict[int, Cell]
    ) -> tuple[list[Cell], dict[int, G.GhosttyStyle]]:
        """One row's cells as a bulk raw view, then decode each packed cell.

        The bulk raw view (`ROW_CELLS_RAW`, render.h:460) gives every cell's packed u64 in one FFI
        call, so a 196-column row is not 196 calls. The view's bytes are copied into an
        `array('Q')` in one C-level operation, so reading them is not a ctypes pointer index per
        cell either — the go/no-go profile showed both the FFI calls and the per-cell Python were
        the cost (phase report, "Go/no-go follow-up"). The row's `GhosttyRenderStateRowCells`
        container is bound lazily, only when a style id is first seen, so a grid of blanks never
        pays for it.
        """
        view = G.GhosttyCellsView()
        rc = self._libc.ghostty_render_state_row_get(
            self._row_iterator, G.RS_ROW_DATA_CELLS_RAW, ctypes.byref(view)
        )
        if rc != G.GHOSTTY_SUCCESS or not view.len:
            return [], {}
        raws = array.array("Q")
        raws.frombytes(ctypes.string_at(view.ptr, view.len * 8))
        styles: dict[int, G.GhosttyStyle] = {}
        row_cells_bound = False
        # The packed u64 fully determines the cell's text, style id and wide flags, so a Cell is
        # decoded once per distinct value in the read and interned: a grid of blanks builds one
        # object. Only the values not yet seen cost Python work; the row itself is one map lookup.
        persistent = self._decoded_cells
        for raw in set(raws):
            cell = cell_cache.get(raw) or persistent.get(raw)
            if cell is None:
                content_tag = (raw >> _TAG_LSB) & _TAG_MASK
                codepoint = (raw >> _CODEPOINT_LSB) & _CODEPOINT_MASK
                wide = (raw >> _WIDE_LSB) & _WIDE_MASK
                has_text = content_tag == _TAG_CODEPOINT or content_tag == _TAG_GRAPHEME
                cell = Cell(
                    text=chr(codepoint) if has_text and codepoint else "",
                    style_id=(raw >> _STYLE_ID_LSB) & _STYLE_ID_MASK,
                    wide=wide,
                    wide_tail=wide == _WIDE_TAIL,
                    wide_head=wide == _WIDE_HEAD,
                )
                if len(persistent) >= _DECODED_CELLS_MAX:
                    persistent.clear()
                persistent[raw] = cell
            style_id = cell.style_id
            # A distinct style id is resolved once per row; painting never rebuilds it per cell.
            if style_id != 0 and style_id not in styles:
                if not row_cells_bound:
                    self._libc.ghostty_render_state_row_get(
                        self._row_iterator, G.RS_ROW_DATA_CELLS, ctypes.byref(self._row_cells)
                    )
                    row_cells_bound = True
                self._libc.ghostty_render_state_row_cells_select(self._row_cells, raws.index(raw))
                style = G.GhosttyStyle()
                style.size = ctypes.sizeof(G.GhosttyStyle)
                if (
                    self._libc.ghostty_render_state_row_cells_get(
                        self._row_cells, G.RS_CELL_DATA_STYLE, ctypes.byref(style)
                    )
                    == G.GHOSTTY_SUCCESS
                ):
                    styles[style_id] = style
            cell_cache[raw] = cell
        return list(map(cell_cache.__getitem__, raws)), styles

    # -- modes --

    def _get_mode(self, mode: int) -> bool:
        with self._lock:
            cfg = G.GhosttyTerminalModeConfig()
            cfg.mode = mode
            cfg.value = False
            rc = self._libc.ghostty_terminal_get(self._terminal, G.DATA_MODE, ctypes.byref(cfg))
            return rc == G.GHOSTTY_SUCCESS and cfg.value

    def active_screen(self) -> int:
        """Which screen is active: 0 primary, 1 alternate (terminal.h GhosttyTerminalScreen)."""
        with self._lock:
            v = ctypes.c_int()
            self._libc.ghostty_terminal_get(self._terminal, G.DATA_ACTIVE_SCREEN, ctypes.byref(v))
            return int(v.value)

    def mode_is_set(self, value: int, ansi: bool = False) -> bool:
        """Whether the terminal mode `value` (DEC private unless `ansi`) is currently set.

        The public read the tests and drive use to assert the modes a golden carries; `modes()` is
        the curated set the input path needs.
        """
        return self._get_mode(_mode(value, ansi))

    def _mouse_mode(self) -> int:
        if self._get_mode(_MODE_MOUSE_ANY):
            return G.MOUSE_TRACKING_ANY
        if self._get_mode(_MODE_MOUSE_BUTTON):
            return G.MOUSE_TRACKING_BUTTON
        if self._get_mode(_MODE_MOUSE_NORMAL):
            return G.MOUSE_TRACKING_NORMAL
        if self._get_mode(_MODE_MOUSE_X10):
            return G.MOUSE_TRACKING_X10
        return G.MOUSE_TRACKING_NONE

    def kitty_flags(self) -> int:
        """The session's kitty keyboard protocol flags (terminal.h DATA_KITTY_KEYBOARD_FLAGS)."""
        with self._lock:
            v = ctypes.c_uint8()
            self._libc.ghostty_terminal_get(self._terminal, G.DATA_KITTY_KEYBOARD_FLAGS, ctypes.byref(v))
            return int(v.value)

    def modes(self) -> Modes:
        """Every mode the input path and the encoders need, read fresh from the terminal, under the
        lock (DT20), so a loop-side `modes()` waits at most one feed slice, never the whole run."""
        with self._lock:
            tracking = ctypes.c_bool()
            self._libc.ghostty_terminal_get(self._terminal, 11, ctypes.byref(tracking))  # MOUSE_TRACKING
            return Modes(
                alternate_screen=self.active_screen() == G.SCREEN_ALTERNATE,
                cursor_keys_application=self._get_mode(_MODE_DECCKM),
                keypad_application=self._get_mode(_MODE_KEYPAD),
                bracketed_paste=self._get_mode(_MODE_BRACKETED_PASTE),
                mouse_tracking=bool(tracking.value),
                mouse_mode=self._mouse_mode(),
                focus_events=self._get_mode(_MODE_FOCUS),
                kitty_flags=self.kitty_flags(),
                synchronized_output=self._get_mode(_MODE_SYNC_OUTPUT),
            )

    # -- text reads for drive --

    def _format(self, *, unwrap: bool) -> list[str]:
        """The whole active screen (scrollback included) as plain lines, via the formatter, under the
        lock (DT20).

        The plain formatter (emit = PLAIN) emits the screen plus its scrollback, newest last, with
        no trailing newline. `unwrap` joins soft-wrapped lines so a wrapped command reads as one
        logical line (D50 "wrapped lines can be joined").
        """
        with self._lock:
            opts = G.GhosttyFormatterTerminalOptions()
            opts.size = ctypes.sizeof(G.GhosttyFormatterTerminalOptions)
            opts.emit = 0  # PLAIN
            opts.unwrap = unwrap
            opts.trim = True
            opts.extra.size = ctypes.sizeof(G.GhosttyFormatterTerminalExtra)
            opts.extra.screen.size = ctypes.sizeof(G.GhosttyFormatterScreenExtra)
            formatter = ctypes.c_void_p()
            rc = self._libc.ghostty_formatter_terminal_new(
                None, ctypes.byref(formatter), self._terminal, opts
            )
            if rc != G.GHOSTTY_SUCCESS or not formatter:
                raise BackendError(f"ghostty_formatter_terminal_new failed with result {rc}")
            try:
                out_ptr = ctypes.c_void_p()
                out_len = ctypes.c_size_t()
                rc = self._libc.ghostty_formatter_format_alloc(
                    formatter, None, ctypes.byref(out_ptr), ctypes.byref(out_len)
                )
                if rc != G.GHOSTTY_SUCCESS:
                    raise BackendError(f"ghostty_formatter_format_alloc failed with result {rc}")
                try:
                    raw = ctypes.string_at(out_ptr, out_len.value) if out_ptr and out_len.value else b""
                finally:
                    if out_ptr:
                        self._libc.ghostty_free(None, out_ptr, out_len.value)
            finally:
                self._libc.ghostty_formatter_free(formatter)
        text = raw.decode("utf-8", "replace")
        return text.split("\n") if text else []

    def _physical_rows(self) -> list[str]:
        """Every row of the active screen, scrollback first, as the formatter prints it: the formatter trims blank rows
        from the end, so the list can be shorter than `scrollback_rows() + rows`, and a row's index in it is its row
        in the screen (the coordinates a tracked mark reports)."""
        return self._format(unwrap=False)

    def visible_rows(self, cap: int = DEFAULT_TEXT_CAP, join_wrapped: bool = False) -> list[str]:
        """The rows on screen, top to bottom, capped by the caller. Exactly `rows` of them (blank rows are `""`), so
        a row's index is its row on screen, unless `join_wrapped` asks for logical lines: a line that wrapped over
        several rows is then one (drive's `read screen`, D50), and blank rows at the bottom are dropped.

        The formatter prints scrollback then screen and trims trailing blank rows, so "the last `rows` lines" is
        the screen only when the screen is full: a short screen would be read as scrollback. The screen starts
        after the scrollback's own rows."""
        with self._lock:
            top = self.scrollback_rows()
            rows = self._physical_rows()
            if not join_wrapped:
                shown = rows[top:top + self.rows]
                return (shown + [""] * (self.rows - len(shown)))[:cap]
            lines, starts = self._logical_lines(rows)
            first = next((i for i in range(len(lines) - 1, -1, -1) if starts[i] <= top), 0)
            return lines[first:][:cap]

    def scrollback(self, cap: int = DEFAULT_TEXT_CAP) -> list[str]:
        """The lines that left the screen, oldest first, capped by the caller (D50)."""
        with self._lock:
            return self._physical_rows()[:self.scrollback_rows()][:cap]

    def _logical_lines(self, physical: list[str]) -> tuple[list[str], list[int]]:
        """The unwrapped lines of the screen and the row each one starts on, found by walking the physical rows
        against the unwrapped text (the engine reports no wrap flag for a row that has scrolled off). A row is
        matched by its non-blank characters, since the formatter trims each physical row's trailing space. If the two
        listings cannot be matched, nothing is joined."""
        logical = self._format(unwrap=True)
        starts: list[int] = []
        row = 0
        for line in logical:
            starts.append(row)
            want = "".join(line.split())
            seen = ""
            while row < len(physical):
                seen += "".join(physical[row].split())
                row += 1
                if seen == want:
                    break
            else:
                if seen != want:
                    return list(physical), list(range(len(physical)))
        if row != len(physical):
            return list(physical), list(range(len(physical)))
        return logical, starts

    def total_rows(self) -> int:
        """Total rows in the active screen including scrollback (terminal.h DATA_TOTAL_ROWS)."""
        with self._lock:
            v = ctypes.c_size_t()
            self._libc.ghostty_terminal_get(self._terminal, G.DATA_TOTAL_ROWS, ctypes.byref(v))
            return int(v.value)

    def scrollback_rows(self) -> int:
        """How many rows are in scrollback (total rows minus viewport)."""
        with self._lock:
            v = ctypes.c_size_t()
            self._libc.ghostty_terminal_get(self._terminal, G.DATA_SCROLLBACK_ROWS, ctypes.byref(v))
            return int(v.value)

    # -- the viewport: where the scroll view looks --

    def scroll_viewport(self, kind: int, amount: int = 0) -> None:
        """Move the viewport, under the lock: `G.SCROLL_VIEWPORT_TOP`, `_BOTTOM`, `_DELTA` (`amount` rows, up is
        negative) or `_ROW` (`amount` is a row from the top of the scrollable area; see `scrollback_position`). The
        engine clamps. Nothing the text reads return depends on where the viewport is; the render state does, so the
        next `dirty_rows()` shows the new rows."""
        value = G.GhosttyTerminalScrollViewportValue()
        if kind == G.SCROLL_VIEWPORT_DELTA:
            value.delta = amount
        elif kind == G.SCROLL_VIEWPORT_ROW:
            value.row = amount
        behavior = G.GhosttyTerminalScrollViewport()
        behavior.tag = kind
        behavior.value = value
        with self._lock:
            if self._terminal is None:
                return  # closed under us
            self._libc.ghostty_terminal_scroll_viewport(self._terminal, behavior)

    def scrollback_position(self) -> ScrollPosition:
        """The viewport's place in the scrollable area, under the lock."""
        with self._lock:
            bar = G.GhosttyTerminalScrollbar()
            if self._terminal is None or self._libc.ghostty_terminal_get(
                    self._terminal, G.DATA_SCROLLBAR, ctypes.byref(bar)) != G.GHOSTTY_SUCCESS:
                return ScrollPosition(self.rows, 0, self.rows)
            return ScrollPosition(int(bar.total), int(bar.offset), int(bar.len))

    def changed_since(self, generation: int) -> bool:
        """Whether the rows the engine holds may have changed (bytes fed, a resize, a reset) since `generation` was read
        from `self.generation`, under the lock."""
        with self._lock:
            return self.generation != generation

    def row_texts(self, first: int, last: int) -> list[RowText | None]:
        """Rows `first`..`last` of the engine's row space (the numbers `scrollback_position` and the ROW scroll use; row 0
        is the oldest the engine holds), however far they have scrolled off. None for a row the engine does not hold.
        Each row is read under the lock on its own, so a long read lets the feed through between rows."""
        return [self._row_text(y) for y in range(first, last + 1)]

    def _row_text(self, y: int) -> RowText | None:
        with self._lock:
            if self._terminal is None or y < 0:
                return None
            libc = self._libc
            point = G.GhosttyPoint()
            point.tag = G.POINT_SCREEN
            point.value.coordinate.x = 0
            point.value.coordinate.y = y
            ref = G.GhosttyGridRef()
            ref.size = ctypes.sizeof(ref)
            row = ctypes.c_uint64()
            if (libc.ghostty_terminal_grid_ref(self._terminal, point, ctypes.byref(ref)) != G.GHOSTTY_SUCCESS
                    or libc.ghostty_grid_ref_row(ctypes.byref(ref), ctypes.byref(row)) != G.GHOSTTY_SUCCESS):
                return None
            wrapped = ctypes.c_bool()
            libc.ghostty_row_get(row.value, G.ROW_DATA_WRAP, ctypes.byref(wrapped))
            cell = ctypes.c_uint64()
            parts: list[str] = []
            for x in range(self.cols):
                ref.x = x
                if libc.ghostty_grid_ref_cell(ctypes.byref(ref), ctypes.byref(cell)) != G.GHOSTTY_SUCCESS:
                    parts.append(" ")
                    continue
                raw = cell.value
                wide = (raw >> _WIDE_LSB) & _WIDE_MASK
                if wide == _WIDE_TAIL or wide == _WIDE_HEAD:
                    continue  # the second half of a wide character, or the blank where one did not fit
                tag = (raw >> _TAG_LSB) & _TAG_MASK
                if tag == _TAG_GRAPHEME:
                    parts.append(self._grapheme_at(ref))
                elif tag == _TAG_CODEPOINT:
                    codepoint = (raw >> _CODEPOINT_LSB) & _CODEPOINT_MASK
                    parts.append(chr(codepoint) if codepoint else " ")
                else:
                    parts.append(" ")
            return RowText("".join(parts), bool(wrapped.value))

    def _grapheme_at(self, ref: G.GhosttyGridRef) -> str:
        """The whole grapheme cluster of the cell `ref` is on: its base codepoint and any combining ones."""
        size = 8
        while True:
            buf = (ctypes.c_uint32 * size)()
            count = ctypes.c_size_t()
            rc = self._libc.ghostty_grid_ref_graphemes(ctypes.byref(ref), buf, size, ctypes.byref(count))
            if rc == G.GHOSTTY_OUT_OF_SPACE:
                size = max(count.value, size * 2)
                continue
            if rc != G.GHOSTTY_SUCCESS or not count.value:
                return " "
            return "".join(map(chr, buf[:count.value]))

    # -- the tracked mark, for `read reply` (D50) --

    def set_mark(self, name: str) -> None:
        """Record a tracked grid reference at the current cursor, under `name`, under the lock (DT20).

        The reference is *tracked* (grid_ref_tracked.h): it follows its cell as the screen scrolls,
        so "the text since the mark" survives the mark scrolling off the top. This is what makes
        `read reply` return output that scrolled off. The mark also records its column, so text
        written on the same line after the mark (a shell prompt's `$ `) is included.
        """
        with self._lock:
            self.sync_render_state()
            cur = self.cursor()
            point = G.GhosttyPoint()
            point.tag = G.POINT_VIEWPORT
            point.value.coordinate.x = cur.x
            point.value.coordinate.y = cur.y
            handle = ctypes.c_void_p()
            rc = self._libc.ghostty_terminal_grid_ref_track(self._terminal, point, ctypes.byref(handle))
            if rc != G.GHOSTTY_SUCCESS or not handle:
                raise BackendError(f"could not track a grid reference at ({cur.x},{cur.y})")
            old = self._marks.get(name)
            if old:
                self._libc.ghostty_tracked_grid_ref_free(old)
            self._marks[name] = handle

    def _mark_point(self, name: str) -> tuple[int, int] | None:
        """The tracked mark's (line, column) in the whole-screen (scrollback-inclusive) line list,
        or None if the mark no longer has a meaningful location (it was discarded by a reset)."""
        with self._lock:
            handle = self._marks.get(name)
            if handle is None:
                return None
            if not self._libc.ghostty_tracked_grid_ref_has_value(handle):
                return None
            coord = G.GhosttyPointCoordinate()
            rc = self._libc.ghostty_tracked_grid_ref_point(handle, G.POINT_SCREEN, ctypes.byref(coord))
            if rc != G.GHOSTTY_SUCCESS:
                return None
            return int(coord.y), int(coord.x)

    def has_mark(self, name: str) -> bool:
        """Whether the tracked mark `name` is set and still has a place (a reset frees every mark)."""
        return self._mark_point(name) is not None

    def text_since(self, name: str, cap: int = DEFAULT_TEXT_CAP, join_wrapped: bool = False) -> list[str]:
        """The text produced since the tracked mark `name`, capped by the caller.

        The whole screen (scrollback included) is read as lines; the mark's line and column index
        it. The mark's own line contributes from its column onward, then every following line. So
        text appended after a prompt (`$ `) is returned whole, and text written on a blank line the
        cursor sat on is returned too.

        Returns [] if the mark has been discarded (a reset) or was never set: `has_mark` tells those apart from a
        reply that is empty. With `join_wrapped` a line that wrapped over several rows is one; when the mark sits on
        a wrapped row, the cut is where its row's column is in the joined line (a full wrapped row is `cols` cells,
        counted as that many characters).
        """
        with self._lock:
            point = self._mark_point(name)
            if point is None:
                return []
            mark_row, mark_col = point
            physical = self._physical_rows()
            if mark_row >= len(physical):
                return []
            if not join_wrapped:
                tail = physical[mark_row][mark_col:]
                rest = physical[mark_row + 1:]
            else:
                lines, starts = self._logical_lines(physical)
                index = next(i for i in range(len(lines) - 1, -1, -1) if starts[i] <= mark_row)
                tail = lines[index][(mark_row - starts[index]) * self.cols + mark_col:]
                rest = lines[index + 1:]
            # A mark at a line's end contributes an empty first element, which is dropped so the reply starts at the
            # first real line of output.
            return (([tail] if tail else []) + rest)[:cap]

    # -- grid comparison, for tests and drive reads --

    def grid_lines(self, join_wrapped: bool = False) -> list[str]:
        """The whole active screen (scrollback included) as lines. Used by tests to compare grids
        and by drive's `read screen`. Unlike `visible_rows`, this includes scrollback."""
        with self._lock:
            return self._format(unwrap=join_wrapped)