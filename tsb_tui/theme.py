"""The "terracotta" theme and the terminal palette (design §2.8, D29).

Two jobs, both read from the knobs and nothing else:

* `build_theme` turns the `theme.*` roles into the Textual theme the app registers, so the rail, the
  status row and the dialogs share one set of values with the canvas (one accent for "you type", one
  steel for "view only").
* `TermPalette` turns an engine style (a cell's resolved SGR state) into the `rich` style the canvas
  paints. With `theme.term.palette = "theme"` the 16 ANSI colours come from `theme.term.colors`, so a
  target's red is the theme's red on every operator's terminal. With `"outer"` an index passes
  through as an ANSI colour and the operator's own terminal theme decides (the app then runs with
  `ansi_color` on, or Textual would convert the index back to a colour of its own).

A value that is not a colour fails with the key named: a typo in a config file must not paint a
screen in the wrong colours and say nothing (Rule 12).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from rich.color import Color as RichColor
from rich.style import Style
from textual.theme import Theme

from .term import ghostty as G

PALETTE_THEME = "theme"
PALETTE_OUTER = "outer"
CURSOR_SHAPES = ("block", "underline")

_HEX = re.compile(r"#[0-9a-fA-F]{6}")

# The xterm cube and grey ramp for the indexes above the 16 named colours (theme mode only).
_CUBE_LEVELS = (0, 95, 135, 175, 215, 255)


class ThemeError(ValueError):
    """A theme or palette knob holds a value the theme cannot use. The message names the key."""


def _colour(key: str, value: Any) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ThemeError(f"{key}: expected a #rrggbb colour, got {value!r}")
    return value.lower()


def _roles(config: Mapping[str, Any]) -> dict[str, str]:
    """Every role of design §2.8, validated. The literal keys are spelled out so a reader (and the
    knob-registry test) can see exactly which knobs this module reads."""
    raw = {
        "bg": config["theme.bg"],
        "surface": config["theme.surface"],
        "surface-2": config["theme.surface_2"],
        "surface-3": config["theme.surface_3"],
        "border": config["theme.border"],
        "border-on-surface": config["theme.border_on_surface"],
        "text": config["theme.text"],
        "text-muted": config["theme.text_muted"],
        "text-faint": config["theme.text_faint"],
        "accent": config["theme.accent"],
        "accent-hot": config["theme.accent_hot"],
        "accent-soft": config["theme.accent_soft"],
        "on-accent": config["theme.on_accent"],
        "steel": config["theme.steel"],
        "ok": config["theme.ok"],
        "warn": config["theme.warn"],
        "danger": config["theme.danger"],
    }
    return {name: _colour("theme." + name.replace("-", "_"), value) for name, value in raw.items()}


def build_theme(config: Mapping[str, Any]) -> Theme:
    """The Textual theme for `theme.*`. Roles Textual has no slot for become theme variables, so a
    stylesheet reads them as `$accent-soft`, `$text-muted` and so on."""
    roles = _roles(config)
    name = config["theme.name"]
    if not isinstance(name, str) or not name:
        raise ThemeError(f"theme.name: expected a non-empty name, got {name!r}")
    return Theme(
        name=name,
        primary=roles["accent"],
        secondary=roles["steel"],
        warning=roles["warn"],
        error=roles["danger"],
        success=roles["ok"],
        accent=roles["accent-hot"],
        foreground=roles["text"],
        background=roles["bg"],
        surface=roles["surface"],
        panel=roles["surface-2"],
        dark=True,
        variables={
            key: roles[key]
            for key in ("surface-2", "surface-3", "border", "border-on-surface", "text-muted",
                        "text-faint", "accent-hot", "accent-soft", "on-accent", "steel", "ok",
                        "warn", "danger")
        },
    )


def _indexed_hex(index: int) -> str:
    """The colour of palette index 16-255 in an xterm."""
    if index < 232:
        n = index - 16
        r, g, b = (_CUBE_LEVELS[n // 36], _CUBE_LEVELS[(n // 6) % 6], _CUBE_LEVELS[n % 6])
    else:
        r = g = b = 8 + 10 * (index - 232)
    return f"#{r:02x}{g:02x}{b:02x}"


@dataclass(frozen=True)
class CursorLook:
    """How one kind of cursor is drawn: its shape and the role colour it takes."""

    shape: str
    colour: str


class TermPalette:
    """Engine style -> `rich` style, cached by the style's own value.

    The cache key is the style's contents, never its engine id: the engine reuses an id once no cell
    holds it, so an id means different things at different times. The same key always yields the
    same `Style` object, which keeps a repainted row's segments cheap to compare.
    """

    def __init__(self, config: Mapping[str, Any]) -> None:
        mode = config["theme.term.palette"]
        if mode not in (PALETTE_THEME, PALETTE_OUTER):
            raise ThemeError(
                f"theme.term.palette: expected {PALETTE_THEME!r} or {PALETTE_OUTER!r}, got {mode!r}")
        colours = config["theme.term.colors"]
        if not isinstance(colours, list) or len(colours) != 16:
            raise ThemeError("theme.term.colors: expected a list of 16 #rrggbb colours")
        self.mode = mode
        self._colours = tuple(_colour(f"theme.term.colors[{i}]", c) for i, c in enumerate(colours))
        self.fg = _colour("theme.term.fg", config["theme.term.fg"])
        roles = _roles(config)
        self.bg = roles["bg"]
        self.cursor_typist = CursorLook(
            _shape("theme.term.cursor_typist", config["theme.term.cursor_typist"]), roles["accent"])
        self.cursor_viewer = CursorLook(
            _shape("theme.term.cursor_viewer", config["theme.term.cursor_viewer"]), roles["steel"])
        self._on_accent = roles["on-accent"]
        self._cache: dict[tuple, Style] = {}
        # Blank cells and cells with no SGR state: the default foreground, no background of their own.
        self.default = Style(color=RichColor.parse(self.fg))

    # -- engine colours --

    def _palette_colour(self, index: int) -> RichColor:
        if self.mode == PALETTE_OUTER:
            return RichColor.from_ansi(index)
        if index < 16:
            return RichColor.parse(self._colours[index])
        return RichColor.parse(_indexed_hex(index))

    def _colour_of(self, colour: Any) -> RichColor | None:
        tag = colour.tag
        if tag == G.STYLE_COLOR_PALETTE:
            return self._palette_colour(colour.value & 0xFF)
        if tag == G.STYLE_COLOR_RGB:
            value = colour.value
            return RichColor.from_rgb(value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF)
        return None

    def style(self, resolved: Any) -> Style:
        """The `rich` style of a resolved engine style (a `GhosttyStyle`); None is the default."""
        if resolved is None:
            return self.default
        fg, bg = resolved.fg_color, resolved.bg_color
        key = (fg.tag, fg.value if fg.tag else 0, bg.tag, bg.value if bg.tag else 0,
               resolved.bold, resolved.italic, resolved.faint, resolved.inverse, resolved.invisible,
               resolved.strikethrough, resolved.overline, resolved.underline)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        built = self._build(resolved)
        self._cache[key] = built
        return built

    def _build(self, resolved: Any) -> Style:
        fg = self._colour_of(resolved.fg_color)
        bg = self._colour_of(resolved.bg_color)
        default_fg = RichColor.parse(self.fg)
        default_bg = RichColor.parse(self.bg)
        if resolved.inverse:
            # The painted default background is the theme's `bg`, so a reversed blank cell is a
            # solid block of the foreground, as it is in a terminal.
            fg, bg = (bg or default_bg), (fg or default_fg)
        elif fg is None:
            fg = default_fg
        if resolved.invisible:
            fg = bg or default_bg
        underline = resolved.underline
        return Style(
            color=fg, bgcolor=bg,
            bold=bool(resolved.bold), dim=bool(resolved.faint), italic=bool(resolved.italic),
            strike=bool(resolved.strikethrough), overline=bool(resolved.overline),
            underline=underline == 1 or underline >= 3,
            underline2=underline == 2,
        )

    # -- cursors --

    def cursor_style(self, look: CursorLook, base: Style) -> Style:
        """`base` (the cell's own style) with the cursor drawn over it: a block fills the cell with
        the role colour; an underline keeps the cell and underlines it in the role colour."""
        if look.shape == "block":
            return base + Style(color=RichColor.parse(self._on_accent),
                                bgcolor=RichColor.parse(look.colour), underline=False)
        return base + Style(color=RichColor.parse(look.colour), underline=True)


def _shape(key: str, value: Any) -> str:
    if value not in CURSOR_SHAPES:
        raise ThemeError(f"{key}: expected one of {', '.join(CURSOR_SHAPES)}, got {value!r}")
    return value
