"""`theme.term.minimum_contrast`: a float knob, off by default, and the lift it drives.

Why these matter (Rule 9): ruling 4 says "stay, dont drift" — a target's colours are shown
faithfully until a user asks for lifting, so at 1.0 every style must be exactly today's, in every
profile; and an invisible cell (SGR 8) is drawn in its background on purpose, so lifting it would
show what the target hid. A ratio outside 1.0-21.0, a non-number, or a lift under `outer` (where
the colours are unknown, TT21) must fail at start-up naming the key, not repaint the screen in
silence. The lifted colour must read as the same colour, only lighter: hue and saturation are kept.

The canvas is built like `test_canvas.py`: a real engine, a real config and the palette under test.
"""

from __future__ import annotations

import asyncio
import colorsys
from pathlib import Path

import pytest
from test_canvas import _first_paint, _Host, _style_at
from textual.app import App

from tsb_tui import config
from tsb_tui.term import VTBackend, load
from tsb_tui.theme import TermPalette, contrast_ratio, lift
from tsb_tui.theme_files import parse_scheme
from tsb_tui.widgets.canvas import Canvas

SHIPPED = Path(__file__).resolve().parents[1] / "tsb_tui" / "schemes"
SOLARIZED = parse_scheme(SHIPPED / "solarized-dark.json")
ANSI10 = SOLARIZED.ansi[10]
ANSI10_RGB = (int(ANSI10[1:3], 16), int(ANSI10[3:5], 16), int(ANSI10[5:7], 16))
GREY = "#808080"

# One cell per SGR state, each on its own row: the colour kinds, bold, faint, inverse, invisible.
SGR_STATES = [
    b"\x1b[31mR",                # a palette colour as foreground
    b"\x1b[32;44mG",             # palette foreground and background
    b"\x1b[38;2;200;100;50mT",   # an RGB foreground
    b"\x1b[38;5;200mY",          # an extended palette index
    b"\x1b[1mB",                 # bold
    b"\x1b[2mF",                 # faint
    b"\x1b[7mI",                 # inverse
    b"\x1b[8mV",                 # invisible
]


def _hue(hex_: str) -> float:
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)[0]


@pytest.fixture(scope="module")
def lib(term_so: Path):
    return load(term_so)


def _canvas(lib, cols: int, rows: int, *, typist: bool = False, **overrides: str) -> Canvas:
    """A canvas with a real config and palette, mirroring `test_canvas.py`'s builder."""
    cfg = config.load(config_file=Path("/nonexistent"), environ={},
                      overrides={"size.auto_request": "false", **overrides})
    backend = VTBackend(lib, cols, rows, scrollback_bytes=cfg["term.scrollback_bytes"])
    return Canvas(backend, TermPalette(cfg, cfg.scheme), cfg, typist=typist)


def test_lift_meets_the_ratio_and_keeps_the_hue() -> None:
    """The lifted colour must read as the same colour, only lighter. Solarized Dark's ansi[10] on its
    background is 2.79:1 (design §5); lifted at 4.5 it reaches at least 4.5 and its HLS hue stays
    within 1/360 of the original."""
    # fails today: no `lift`.
    assert contrast_ratio(ANSI10, SOLARIZED.background) == pytest.approx(2.79, abs=0.01)
    lifted = lift(ANSI10, SOLARIZED.background, 4.5)
    assert contrast_ratio(lifted, SOLARIZED.background) >= 4.5, (lifted, contrast_ratio(lifted, SOLARIZED.background))
    assert abs(_hue(lifted) - _hue(ANSI10)) < 1 / 360, (lifted, _hue(lifted), _hue(ANSI10))


def test_the_black_or_white_fallback() -> None:
    """A mid-grey background admits no lightness that reaches 21.0, so the fallback is the extreme
    that contrasts more — black (5.32:1 beats white's 3.95:1). A colour that cannot be lifted must
    still be readable, not left below the ratio."""
    # fails today: no `lift`.
    assert contrast_ratio("#000000", GREY) > contrast_ratio("#ffffff", GREY)
    for fg in (ANSI10, "#ff5555"):
        assert lift(fg, GREY, 21.0) == "#000000", fg


class _TwoHost(App):
    """Two canvases stacked, so the styles of one run with the knob and one without are compared
    while both apps are still mounted (a canvas clears its strips when its app shuts down)."""

    def __init__(self, first: Canvas, second: Canvas) -> None:
        super().__init__()
        self._first = first
        self._second = second

    def compose(self):
        yield self._first
        yield self._second


def test_nothing_changes_at_one(lib) -> None:
    """Ruling 4: "stay, dont drift". Off by default means every SGR state paints exactly as today, so
    a run that sets the knob to 1.0 builds the very same style for every colour kind, bold, faint,
    inverse and invisible as a run that never mentions it."""
    # fails today: `theme.term.minimum_contrast` is an unknown key, so a config that sets it raises.
    async def _check() -> None:
        rows = len(SGR_STATES)
        feeds = b"".join(f"\x1b[{i + 1};1H".encode() + state + b"\x1b[0m" for i, state in enumerate(SGR_STATES))
        plain = _canvas(lib, 20, rows)
        knob = _canvas(lib, 20, rows, **{"theme.term.minimum_contrast": "1.0"})
        async with _TwoHost(plain, knob).run_test(size=(20, 2 * rows)) as pilot:
            await _first_paint(pilot, plain)
            await _first_paint(pilot, knob)
            plain.feed(feeds)
            knob.feed(feeds)
            plain.repaint_now()
            knob.repaint_now()
            for row, state in enumerate(SGR_STATES):
                assert _style_at(plain.render_line(row), 0) == _style_at(knob.render_line(row), 0), state

    asyncio.run(_check())


def test_the_canvas_lifts_a_cell_and_leaves_an_invisible_one(lib) -> None:
    """A cell in ANSI 10 under Solarized Dark is 2.79:1 on the scheme's background, so at 4.5 it is
    painted lifted; an invisible cell is still drawn in its background, because lifting it would show
    what the target hid."""
    # fails today: the knob is unknown, so this config raises, and no cell is lifted.
    async def _check() -> None:
        canvas = _canvas(lib, 30, 3, **{
            "theme.term.scheme": "solarized-dark", "theme.term.minimum_contrast": "4.5",
        })
        async with _Host(canvas).run_test(size=(30, 3)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"\x1b[92mL\x1b[0m \x1b[8mV\x1b[0m")
            canvas.repaint_now()
            line = canvas.render_line(0)
            lifted_rgb = _style_at(line, 0).color.get_truecolor()[:3]
            assert lifted_rgb != ANSI10_RGB, "the ANSI 10 cell was not lifted"
            hex_lifted = f"#{lifted_rgb[0]:02x}{lifted_rgb[1]:02x}{lifted_rgb[2]:02x}"
            assert contrast_ratio(hex_lifted, SOLARIZED.background) >= 4.5, (hex_lifted, lifted_rgb)
            invisible = _style_at(line, 2)
            assert invisible.color.get_truecolor()[:3] == invisible.bgcolor.get_truecolor()[:3], (
                "an invisible cell must stay drawn in its background")

    asyncio.run(_check())


def test_refusals_name_the_key() -> None:
    """A ratio below 1.0, above 21.0, or not a number is a typo that would silently repaint the
    screen; above 1.0 under `palette = "outer"` the colours are unknown (TT21), so start-up refuses
    naming both keys."""
    # fails today: the knob is unknown, so every value below fails as an unknown key, not as a bad value.
    for refused in ("0.5", "22", "high"):
        with pytest.raises(config.ConfigError, match="theme.term.minimum_contrast"):
            config.load(config_file=Path("/nonexistent"), environ={},
                        overrides={"theme.term.minimum_contrast": refused})
    both = r"theme\.term\.minimum_contrast.*theme\.term\.palette|theme\.term\.palette.*theme\.term\.minimum_contrast"
    with pytest.raises(config.ConfigError, match=both):
        config.load(config_file=Path("/nonexistent"), environ={},
                    overrides={"theme.term.minimum_contrast": "4.5", "theme.term.palette": "outer"})


def test_every_profile_resolves_one() -> None:
    """Off in every profile (ruling 4): the drive and harness profiles must resolve 1.0, so a run
    that shows a target faithfully never lifts a pixel."""
    # fails today: the knob is unknown, so `load` cannot resolve it in any profile.
    for profile in config.PROFILES:
        cfg = config.load(config_file=Path("/nonexistent"), environ={}, profile=profile)
        assert cfg["theme.term.minimum_contrast"] == 1.0, profile
