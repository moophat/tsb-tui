"""How `TermPalette` maps the engine's colours to a scheme, and what the cursor looks like.

Why these matter (Rule 9): the palette is the only place a target's SGR colour becomes pixels. If
indexes 0-15 or the default foreground did not come from the selected scheme, `--set
theme.term.scheme=dracula` would paint something else and say nothing; if an `extendedAnsi` null were
read as black, a 256-colour program would be quietly wrong (the ruling: null is the xterm value); if
`outer` did not pass indexes through, the operator's own terminal theme would be ignored. The cursor
is how the operator knows whether their keys go anywhere, so its shape and colour are asserted.

The canvas is built like `test_canvas.py`: a real engine, a real config and the palette under test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from test_canvas import _first_paint, _Host, _style_at

from tsb_tui import config
from tsb_tui.term import VTBackend, load
from tsb_tui.theme import TermPalette
from tsb_tui.theme_files import parse_scheme
from tsb_tui.widgets.canvas import Canvas

ACCENT = (0xE5, 0x6A, 0x4A)     # theme.accent, terracotta's "you type" colour
STEEL = (0x7A, 0xA7, 0xD6)      # theme.steel, the viewer's
ON_ACCENT = (0x0A, 0x0A, 0x0A)  # theme.on_accent
DRACULA = {
    "red": (0xFF, 0x55, 0x55), "fg": (0xF8, 0xF8, 0xF2), "bg": (0x28, 0x2A, 0x36),
    "cursor": (0xF8, 0xF8, 0xF2), "cursor_text": (0x28, 0x2A, 0x36),
}
SHIPPED = Path(__file__).resolve().parents[1] / "tsb_tui" / "schemes"


def _rgb(hex_: str) -> tuple[int, int, int]:
    return (int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16))


@pytest.fixture(scope="module")
def lib(term_so: Path):
    return load(term_so)


def _canvas(lib, cols: int, rows: int, *, environ: dict | None = None, typist: bool = False,
            **overrides: str) -> Canvas:
    """A canvas whose `Config` resolves `overrides`, reading `environ` for `XDG_CONFIG_HOME` (so a
    test can put a user scheme on disk). Mirrors `test_canvas.py`'s builder."""
    cfg = config.load(config_file=Path("/nonexistent"), environ=environ or {},
                      overrides={"size.auto_request": "false", **overrides})
    backend = VTBackend(lib, cols, rows, scrollback_bytes=cfg["term.scrollback_bytes"])
    return Canvas(backend, TermPalette(cfg, cfg.scheme), cfg, typist=typist)


def test_a_scheme_paints_its_ansi_default_and_background(lib) -> None:
    """TT28/TT3: with `theme.term.scheme=dracula`, index 1 is Dracula's red, a cell with no SGR
    colour is Dracula's foreground on its background, and the padding right of the grid carries
    Dracula's background, not terracotta's.

    fails today: `theme.term.scheme` is an unknown key."""
    async def _check() -> None:
        canvas = _canvas(lib, 30, 3, **{"theme.term.scheme": "dracula"})
        async with _Host(canvas).run_test(size=(30, 3)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"\x1b[31mR\x1b[0mX")
            canvas.repaint_now()
            line = canvas.render_line(0)
            assert line.text.startswith("RX")
            assert _style_at(line, 0).color.get_truecolor()[:3] == DRACULA["red"], "ansi 1 is not Dracula's red"
            # The cell after the R has no SGR colour: Dracula's foreground on its background.
            default_cell = _style_at(line, 1)
            assert default_cell.color.get_truecolor()[:3] == DRACULA["fg"], "default fg is not Dracula's"
            assert default_cell.bgcolor.get_truecolor()[:3] == DRACULA["bg"], "default bg is not Dracula's"
            # Padding right of the grid carries the scheme's background.
            padding = [seg for seg in line if seg.text.strip() == "" and len(seg.text) > 5]
            assert padding and all(seg.style.bgcolor.get_truecolor()[:3] == DRACULA["bg"] for seg in padding), (
                "the padding does not carry Dracula's background")

    asyncio.run(_check())


def test_extended_ansi_reads_the_scheme_and_null_is_the_xterm_value(lib, tmp_path) -> None:
    """A scheme may carry `extendedAnsi`; a non-null entry paints, a null entry is the xterm value,
    never black (the ruling: 0 is a colour). Index 16 -> entry 0 (#123456), index 17 -> entry 1 (null
    -> xterm #00005f)."""
    # fails today: `theme.term.scheme` is an unknown key, and no `extendedAnsi` is read.
    config_home = tmp_path / "config"
    schemes = config_home / "tsb-tui" / "schemes"
    schemes.mkdir(parents=True)
    (schemes / "user-one.json").write_text(
        '{"name":"User One","variant":"dark","ansi":[' + ",".join(['"#000000"'] * 16) + '],'
        '"foreground":"#e6e6e6","background":"#0a0a0a","extendedAnsi":["#123456",null,'
        + ",".join(["null"] * 238) + ']}', encoding="utf-8")

    async def _check() -> None:
        canvas = _canvas(lib, 30, 3,
                         environ={"XDG_CONFIG_HOME": str(config_home), "HOME": str(tmp_path)},
                         **{"theme.term.scheme": "user-one"})
        async with _Host(canvas).run_test(size=(30, 3)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"\x1b[38;5;16mA\x1b[38;5;17mB")
            canvas.repaint_now()
            line = canvas.render_line(0)
            assert _style_at(line, 0).color.get_truecolor()[:3] == (0x12, 0x34, 0x56), "extended[0] not read"
            assert _style_at(line, 1).color.get_truecolor()[:3] == (0x00, 0x00, 0x5F), "null must be the xterm value"

    asyncio.run(_check())


def test_config_colors_and_fg_override_the_scheme(lib) -> None:
    """B2: `theme.term.colors` of 16 and `theme.term.fg` still override the scheme, so an operator
    who pinned colours keeps them."""
    # fails today: `theme.term.colors` must be 16 and `fg` is always the resolved hex.
    async def _check() -> None:
        canvas = _canvas(lib, 30, 3, **{
            "theme.term.scheme": "dracula",
            "theme.term.colors": ("#010101,#020202,#030303,#040404,#050505,#060606,#070707,#080808,"
                                  "#090909,#0a0a0a,#0b0b0b,#0c0c0c,#0d0d0d,#0e0e0e,#0f0f0f,#101010"),
            "theme.term.fg": "#abcdef",
        })
        async with _Host(canvas).run_test(size=(30, 3)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"\x1b[31mR\x1b[0mX")
            canvas.repaint_now()
            line = canvas.render_line(0)
            assert _style_at(line, 0).color.get_truecolor()[:3] == (0x02, 0x02, 0x02), "colors override lost"
            assert _style_at(line, 1).color.get_truecolor()[:3] == (0xAB, 0xCD, 0xEF), "fg override lost"

    asyncio.run(_check())


def test_outer_passes_the_scheme_by_and_reverses_without_a_default_foreground(lib) -> None:
    """TT4/TT31: under `palette = "outer"` the scheme is ignored: index 1 passes through as ANSI 1,
    a default cell has no foreground and no background (the terminal decides). With `fg = ""` there
    is nothing to swap, so a reversed blank cell is drawn with Rich's `reverse`."""
    # fails today: `theme.term.scheme` is an unknown key.
    async def _check() -> None:
        canvas = _canvas(lib, 30, 3, **{
            "theme.term.palette": "outer", "theme.term.scheme": "dracula", "theme.term.fg": "",
        })
        async with _Host(canvas).run_test(size=(30, 3)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"\x1b[31mR\x1b[0mX")
            canvas.repaint_now()
            line = canvas.render_line(0)
            assert _style_at(line, 0).color.number == 1, "outer must pass ANSI index 1 through"
            default_cell = _style_at(line, 1)
            assert default_cell.color is None and default_cell.bgcolor is None, (
                "under outer a default cell must have no colour of its own")
            # A reversed blank cell carries reverse and no swapped colours.
            canvas.feed(b"\x1b[7m \x1b[0m")
            canvas.repaint_now()
            reversed_cell = _style_at(canvas.render_line(0), 2)
            assert reversed_cell.reverse, "an inverse cell under outer must use reverse"

    asyncio.run(_check())


def test_block_cursor_on_a_reversed_cell_keeps_its_colours(lib) -> None:
    """TT31 stays as it is for cells without the cursor, but the block cursor must show the colours
    it is given: on a reversed cell the typist's block stays accent on its text colour, not swapped,
    so the cursor always reads as TSB's own colour."""
    # fails today: the base style keeps reverse=True under outer, so the block's colours are swapped.
    async def _check() -> None:
        canvas = _canvas(lib, 30, 3, **{"theme.term.palette": "outer", "theme.term.fg": ""})
        async with _Host(canvas).run_test(size=(30, 3)) as pilot:
            await _first_paint(pilot, canvas)
            # Two reversed cells; the cursor moves back onto the second one.
            canvas.feed(b"\x1b[7mab\x1b[0m\x1b[1;2H")
            canvas.typist = True
            canvas.repaint_now()
            block = _style_at(canvas.render_line(0), 1)
            assert not block.reverse, "the block cursor must clear reverse on a reversed cell"
            assert block.bgcolor.get_truecolor()[:3] == (0x88, 0xC0, 0xD0), "the block must show the accent it was given"
            assert block.color.get_truecolor()[:3] == (0x1F, 0x23, 0x2B)

    asyncio.run(_check())


def test_cursor_role_keeps_tsbs_colours(lib) -> None:
    """Ruling 6: `cursor_colour = "role"` (the default) draws the typist's block in theme.accent with
    on_accent text and the viewer's underline in theme.steel, exactly as today."""
    # fails today: `theme.term.cursor_colour` is an unknown key, though the look is today's.
    async def _check() -> None:
        canvas = _canvas(lib, 30, 3, **{"theme.term.cursor_colour": "role"})
        async with _Host(canvas).run_test(size=(30, 3)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"ab")
            canvas.typist = True
            canvas.repaint_now()
            block = _style_at(canvas.render_line(0), 2)
            assert block.bgcolor.get_truecolor()[:3] == (0x88, 0xC0, 0xD0)
            assert block.color.get_truecolor()[:3] == (0x1F, 0x23, 0x2B)
            canvas.typist = False
            viewer = _style_at(canvas.render_line(0), 2)
            assert viewer.color.get_truecolor()[:3] == (0x8F, 0xC4, 0xD3) and viewer.underline

    asyncio.run(_check())


def test_cursor_scheme_takes_the_schemes_cursor_colours(lib) -> None:
    """B2/ruling 6: `cursor_colour = "scheme"` under `palette = "theme"` draws both cursors in the
    scheme's `cursor` and the block's glyph in `cursorText`; the shapes are unchanged, so the shape
    still says who types."""
    # fails today: `theme.term.cursor_colour` is an unknown key.
    async def _check() -> None:
        canvas = _canvas(lib, 30, 3, **{
            "theme.term.scheme": "dracula", "theme.term.cursor_colour": "scheme",
        })
        async with _Host(canvas).run_test(size=(30, 3)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"ab")
            canvas.typist = True
            canvas.repaint_now()
            block = _style_at(canvas.render_line(0), 2)
            assert block.bgcolor.get_truecolor()[:3] == DRACULA["cursor"]
            assert block.color.get_truecolor()[:3] == DRACULA["cursor_text"]
            canvas.typist = False
            viewer = _style_at(canvas.render_line(0), 2)
            assert viewer.underline and viewer.color.get_truecolor()[:3] == DRACULA["cursor"]

    asyncio.run(_check())


def test_cursor_scheme_falls_back_when_the_scheme_has_none(lib) -> None:
    """A scheme without `cursor`/`cursorText` (shipped at one-half-dark) falls back to its foreground
    and background, so `cursor_colour = "scheme"` always has something to draw."""
    # fails today: `theme.term.cursor_colour` is an unknown key.
    scheme = parse_scheme(SHIPPED / "one-half-dark.json")
    assert scheme.cursor is None and scheme.cursor_text is None, "one-half-dark gained a cursor"

    async def _check() -> None:
        canvas = _canvas(lib, 30, 3, **{
            "theme.term.scheme": "one-half-dark", "theme.term.cursor_colour": "scheme",
        })
        async with _Host(canvas).run_test(size=(30, 3)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"ab")
            canvas.typist = True
            canvas.repaint_now()
            block = _style_at(canvas.render_line(0), 2)
            assert block.bgcolor.get_truecolor()[:3] == _rgb(scheme.foreground), "cursor did not fall back to fg"
            assert block.color.get_truecolor()[:3] == _rgb(scheme.background), "cursor text did not fall back to bg"

    asyncio.run(_check())


def test_cursor_scheme_under_outer_takes_tsb_roles_open_t4(lib) -> None:
    """OPEN-T4, ruled 2026-10-15: under `palette = "outer"` the scheme is ignored, so
    `cursor_colour = "scheme"` draws TSB's role colours exactly as `"role"` does, rather than a
    scheme the cells do not use."""
    # fails today: `theme.term.cursor_colour` is an unknown key.
    async def _check() -> None:
        canvas = _canvas(lib, 30, 3, **{
            "theme.term.palette": "outer", "theme.term.scheme": "dracula",
            "theme.term.cursor_colour": "scheme",
        })
        async with _Host(canvas).run_test(size=(30, 3)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"ab")
            canvas.typist = True
            canvas.repaint_now()
            block = _style_at(canvas.render_line(0), 2)
            assert block.bgcolor.get_truecolor()[:3] == (0x88, 0xC0, 0xD0), "outer must fall back to the role accent"
            assert block.color.get_truecolor()[:3] == (0x1F, 0x23, 0x2B)
            canvas.typist = False
            viewer = _style_at(canvas.render_line(0), 2)
            assert viewer.color.get_truecolor()[:3] == (0x8F, 0xC4, 0xD3)

    asyncio.run(_check())
