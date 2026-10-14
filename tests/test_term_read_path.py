"""The grid read path: the fast bulk read gives exactly the cells the engine's own per-cell API does.

Why this matters (phase 1 go/no-go, plan R4): `VTBackend.dirty_rows` was made fast by decoding each
row's packed cells in bulk and interning the decoded `Cell` objects. A speed-up that drops a wide
tail, a style or a glyph would paint a wrong screen and pass every text-only test. So this compares
the fast read, cell for cell, with a deliberately slow reference that asks the engine about every
cell by itself (`ghostty_render_state_row_cells_select` + `_get`, and the engine's own
`ghostty_cell_get` decoder instead of this package's bit layout), over the golden replays and a
synthetic grid that carries every attribute the decode distinguishes.
"""

from __future__ import annotations

import ctypes
from pathlib import Path

import pytest
from test_term_engine import (  # noqa: F401  (shared golden decoder)
    GOLDENS,
    _decode,
    _replay,
)
from tsb_tui.term import VTBackend, load
from tsb_tui.term import ghostty as G

# GhosttyCellData (screen.h:147..): the engine decodes a packed cell for us.
CELL_CODEPOINT, CELL_CONTENT_TAG, CELL_WIDE, CELL_HAS_TEXT, CELL_STYLE_ID = 1, 2, 3, 4, 6
STYLE_FIELDS = [name for name, _ in G.GhosttyStyle._fields_ if name != "size"]


@pytest.fixture(scope="module")
def lib(term_so: Path):
    return load(term_so)


def _style_tuple(style: G.GhosttyStyle) -> tuple:
    out = []
    for name in STYLE_FIELDS:
        value = getattr(style, name)
        if isinstance(value, G.GhosttyStyleColor):
            value = (value.tag, value.value if value.tag else 0)
        out.append(value)
    return tuple(out)


def _reference_rows(backend: VTBackend) -> dict[int, list[tuple]]:
    """Every dirty row, every cell, by the engine's own per-cell calls. Slow on purpose."""
    libc = backend._libc
    cell_get = libc.ghostty_cell_get
    cell_get.argtypes = [ctypes.c_uint64, ctypes.c_int, ctypes.c_void_p]
    cell_get.restype = ctypes.c_int
    backend.sync_render_state()
    libc.ghostty_render_state_get(
        backend._render_state, G.RS_DATA_ROW_ITERATOR, ctypes.byref(backend._row_iterator)
    )
    out: dict[int, list[tuple]] = {}
    y = ctypes.c_uint16()
    while libc.ghostty_render_state_row_iterator_next_dirty(backend._row_iterator, ctypes.byref(y)):
        libc.ghostty_render_state_row_get(
            backend._row_iterator, G.RS_ROW_DATA_CELLS, ctypes.byref(backend._row_cells)
        )
        cells = []
        for x in range(backend.cols):
            assert libc.ghostty_render_state_row_cells_select(backend._row_cells, x) == G.GHOSTTY_SUCCESS
            raw = ctypes.c_uint64()
            assert (
                libc.ghostty_render_state_row_cells_get(
                    backend._row_cells, G.RS_CELL_DATA_RAW, ctypes.byref(raw)
                )
                == G.GHOSTTY_SUCCESS
            )
            cp, has_text, wide, sid = ctypes.c_uint32(), ctypes.c_bool(), ctypes.c_int(), ctypes.c_uint16()
            cell_get(raw.value, CELL_CODEPOINT, ctypes.byref(cp))
            cell_get(raw.value, CELL_HAS_TEXT, ctypes.byref(has_text))
            cell_get(raw.value, CELL_WIDE, ctypes.byref(wide))
            cell_get(raw.value, CELL_STYLE_ID, ctypes.byref(sid))
            style = None
            if sid.value:
                s = G.GhosttyStyle()
                s.size = ctypes.sizeof(G.GhosttyStyle)
                assert (
                    libc.ghostty_render_state_row_cells_get(
                        backend._row_cells, G.RS_CELL_DATA_STYLE, ctypes.byref(s)
                    )
                    == G.GHOSTTY_SUCCESS
                )
                style = _style_tuple(s)
            text = chr(cp.value) if has_text.value and cp.value else ""
            cells.append((text, sid.value, wide.value, wide.value == 2, wide.value == 3, style))
        out[y.value] = cells
    return out


def _fast_rows(backend: VTBackend) -> dict[int, list[tuple]]:
    out = {}
    for row in backend.dirty_rows():
        cells = []
        for c in row.cells:
            style = row.style_for(c.style_id) if c.style_id else None
            cells.append(
                (c.text, c.style_id, c.wide, c.wide_tail, c.wide_head, style and _style_tuple(style))
            )
        out[row.y] = cells
    return out


def _assert_same_read(backend: VTBackend, label: str) -> None:
    fast = _fast_rows(backend)
    slow = _reference_rows(backend)
    assert fast.keys() == slow.keys(), f"{label}: different dirty rows"
    for y in slow:
        assert len(fast[y]) == len(slow[y]) == backend.cols, f"{label}: row {y} width"
        for x, (f, s) in enumerate(zip(fast[y], slow[y])):
            assert f == s, f"{label}: cell ({x},{y}) fast {f} != reference {s}"
    cur = G.GhosttyRenderStateCursor()
    cur.size = ctypes.sizeof(G.GhosttyRenderStateCursor)
    backend._libc.ghostty_render_state_get(backend._render_state, G.RS_DATA_CURSOR, ctypes.byref(cur))
    got = backend.cursor()
    if cur.viewport_has_value:
        assert (got.x, got.y, got.visible) == (cur.viewport_x, cur.viewport_y, cur.visible), label


GOLDEN_NAMES = ["golden-sgr", "golden-wide-chars", "golden-reflow", "golden-alt-screen", "golden-resize-mid-stream"]


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_fast_read_matches_the_per_cell_reference_on_each_golden(lib, name: str) -> None:
    """Over each golden replay the bulk read and the per-cell reference agree on every cell.

    Why: the goldens carry the real sequences the core checks (colour runs, wide glyphs, the
    alternate screen, a reflow). If the fast decode disagreed on any glyph, style or wide flag, the
    TUI would paint what the engine did not hold.
    """
    backend = _replay(lib, _decode(name))
    try:
        _assert_same_read(backend, name)
    finally:
        backend.close()


SYNTHETIC = (
    "\x1b[1;31mbold red\x1b[0m \x1b[4;38;5;208munder 208\x1b[0m \x1b[7;48;2;10;20;30minv rgb\x1b[0m"
    "\r\n世界 wide \x1b[3;32mitalic\x1b[2m faint\x1b[0m é \U0001F600 x\r\n"
    "\x1b[9mstrike\x1b[0m \x1b[53moverline\x1b[0m \x1b[5mblink\x1b[0m \x1b[8minvisible\x1b[0m\r\n"
).encode()


def test_fast_read_matches_the_reference_on_a_grid_with_every_attribute(lib) -> None:
    """Bold, underline, palette and RGB colours, wide glyphs, combining marks and emoji, one grid.

    Why: the goldens do not carry every attribute the decode and the style lookup distinguish; this
    grid does, so a wrong shift or a dropped style field shows up as a named cell.
    """
    backend = VTBackend(lib, 40, 8, scrollback_bytes=1 << 20)
    try:
        backend.feed(SYNTHETIC)
        _assert_same_read(backend, "synthetic")
    finally:
        backend.close()


def test_a_partial_read_after_a_clean_matches_the_reference(lib) -> None:
    """After `mark_clean`, a one-line change reads back only the rows it dirtied, identically.

    Why: a keystroke repaints a row or two, not the grid; the dirty-row path must give the same
    cells there as the full read does.
    """
    backend = VTBackend(lib, 40, 8, scrollback_bytes=1 << 20)
    try:
        backend.feed(SYNTHETIC)
        backend.dirty_rows()
        backend.mark_clean()
        backend.feed(b"\x1b[5;1H\x1b[1;34mtyped\x1b[0m")
        assert len(backend.dirty_rows()) < backend.rows
        _assert_same_read(backend, "partial")
    finally:
        backend.close()


def test_a_reused_style_id_resolves_to_the_new_style(lib) -> None:
    """The engine reuses a style id once no cell holds it; `style_for` must follow the new meaning.

    Why: caching styles by id across reads kept the first colour for ever (a screen cleared and
    redrawn in green was still painted red). Each read re-resolves the ids it sees.
    """
    backend = VTBackend(lib, 20, 4, scrollback_bytes=1 << 20)
    try:
        seen = []
        for sgr in (31, 32, 34):
            backend.feed(b"\x1b[2J\x1b[H\x1b[%dmx" % sgr)
            row = {r.y: r for r in backend.dirty_rows()}[0]
            backend.mark_clean()
            cell = row.cells[0]
            style = row.style_for(cell.style_id)
            assert style is not None
            seen.append(style.fg_color.value & 0xFF)
        assert seen == [1, 2, 4], "the palette index follows the SGR, not the first style the id held"
    finally:
        backend.close()


def test_style_ids_are_resolved_against_their_own_page(lib) -> None:
    """A viewport that spans two engine pages paints each row with its own page's style.

    Why: the engine numbers style ids per page, so one id can mean two styles on two rows. A read
    that resolved each id once for the whole grid painted the later page's rows in the earlier
    page's colour (wrong colours on thousands of rows of a scrolling three-colour stream, the
    operator would have seen them). Every row's styles must equal the engine's per-cell answer, and
    be the colour its line was written in.
    """
    colours = {0: 11, 1: 22, 2: 33}  # the blue byte of the 24-bit foreground, by line number mod 3
    backend = VTBackend(lib, 80, 24, scrollback_bytes=1 << 22)
    try:
        painted = 0
        for n in range(1, 1200):
            backend.feed(b"\x1b[38;2;1;2;%dmL%05d\x1b[0m\r\n" % (colours[n % 3], n))
            if n % 10 == 0:  # the slow per-cell reference, often enough to land on a two-page view
                _assert_same_read(backend, f"line {n}")
            for row in backend.dirty_rows():
                text = "".join(c.text for c in row.cells).rstrip()
                if not text.startswith("L"):
                    continue
                style = row.style_for(row.cells[0].style_id)
                assert style is not None, f"line {text} lost its style"
                assert style.fg_color.value >> 16 & 0xFF == colours[int(text[1:6]) % 3], (
                    f"line {text} is painted in another line's colour"
                )
                painted += 1
            backend.mark_clean()
        assert painted > 10_000
    finally:
        backend.close()


def test_the_decoded_cell_table_is_bounded_and_the_read_stays_right(lib, monkeypatch) -> None:
    """A session that prints endlessly new glyphs cannot grow the decode table without bound.

    Why: decoded cells persist across reads so a repaint only decodes values it has not seen; with a
    cap of four entries the table refills many times inside one grid, and every read must still
    equal the reference — emptying the table must never leak a half-built cell into a row.
    """
    from tsb_tui.term import backend as backend_module

    monkeypatch.setattr(backend_module, "_DECODED_CELLS_MAX", 4)
    backend = VTBackend(lib, 40, 8, scrollback_bytes=1 << 20)
    try:
        backend.feed(SYNTHETIC)
        _assert_same_read(backend, "capped")
        assert len(backend._decoded_cells) <= 4
        _assert_same_read(backend, "capped again")
    finally:
        backend.close()
