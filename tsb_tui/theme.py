"""The "terracotta" theme and the terminal palette (design §2.8, D29).

Two jobs, both read from the knobs and nothing else:

* `build_theme` turns the `theme.*` roles into the Textual theme the app registers, so the rail, the
  status row and the dialogs share one set of values with the canvas (one accent for "you type", one
  steel for "view only").
* `TermPalette` turns an engine style (a cell's resolved SGR state) into the `rich` style the canvas
  paints. With `theme.term.palette = "theme"` the 16 ANSI colours, the default foreground and the
  background come from the selected scheme file, unless `theme.term.colors` / `theme.term.fg`
  override them, so a target's red is the scheme's red on every operator's terminal. With `"outer"`
  an index passes through as an ANSI colour and the operator's own terminal theme decides (the app
  then runs with `ansi_color` on, or Textual would convert the index back to a colour of its own).

A value that is not a colour fails with the key named: a typo in a config file must not paint a
screen in the wrong colours and say nothing (Rule 12).
"""

from __future__ import annotations

import colorsys
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


def role_colours(config: Mapping[str, Any]) -> dict[str, str]:
    """Every role of design §2.8, validated, keyed by the knob's role name (`text_muted`, `rec_list`).
    The one reader of `config["theme.<role>"]` (TT14): the rail, the sidebar, the status row, the fleet
    view and the Menu take every colour from here, so `ansi` values (phase 14) change one place. The
    literal keys are spelled out so a reader (and the knob-registry test) can see exactly which knobs
    this module reads."""
    raw = {
        "bg": config["theme.bg"],
        "surface": config["theme.surface"],
        "surface_2": config["theme.surface_2"],
        "surface_3": config["theme.surface_3"],
        "border": config["theme.border"],
        "border_on_surface": config["theme.border_on_surface"],
        "text": config["theme.text"],
        "text_muted": config["theme.text_muted"],
        "text_faint": config["theme.text_faint"],
        "accent": config["theme.accent"],
        "accent_hot": config["theme.accent_hot"],
        "accent_soft": config["theme.accent_soft"],
        "on_accent": config["theme.on_accent"],
        "steel": config["theme.steel"],
        "ok": config["theme.ok"],
        "warn": config["theme.warn"],
        "danger": config["theme.danger"],
        "frame": config["theme.frame"],
        "rec": config["theme.rec"],
        "rec_list": config["theme.rec_list"],
    }
    return {name: _colour("theme." + name, value) for name, value in raw.items()}


def _roles(config: Mapping[str, Any]) -> dict[str, str]:
    """Textual's hyphenated variables, built from `role_colours` so there is one reader of
    `config["theme.<role>"]` (TT14)."""
    return {name.replace("_", "-"): colour for name, colour in role_colours(config).items()}


def build_theme(config: Mapping[str, Any], chrome: Any) -> Theme:
    """The Textual theme for `theme.*`, registered as `tsb-<id>` (TT13), so it never replaces
    Textual's own `nord` or `dracula`; `dark` comes from the theme file's `variant`. Roles Textual
    has no slot for become theme variables, so a stylesheet reads them as `$accent-soft`,
    `$text-muted` and so on."""
    roles = _roles(config)
    return Theme(
        name=f"tsb-{chrome.id}",
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
        dark=chrome.variant == "dark",
        variables={
            key: roles[key]
            for key in ("surface-2", "surface-3", "border", "border-on-surface", "text-muted",
                        "text-faint", "accent-hot", "accent-soft", "on-accent", "steel", "ok",
                        "warn", "danger", "frame", "rec", "rec-list")
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


def relative_luminance(hex_: str) -> float:
    """WCAG 2 relative luminance of a `#rrggbb` colour (the formula `tools/contrast-check.py:12-23`
    uses). Phases 11, 12 and 13 reuse it to compute a scheme's contrast and to lift a colour's
    lightness until it meets `theme.term.minimum_contrast`."""

    def ch(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 2 contrast ratio of two `#rrggbb` colours, 1.0 (identical) to 21.0 (black on white)."""
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


_LIFT_CACHE: dict[tuple[str, str, float], str] = {}


def lift(fg: str, bg: str, ratio: float) -> str:
    """The foreground that meets `ratio` against `bg` (TT21), keeping hue and saturation: binary-search
    the HLS lightness toward white when `bg`'s relative luminance is at most 0.179 (where black and
    white text contrast equally), toward black otherwise, until the ratio is met. If neither extreme
    meets it, whichever of `#000000` and `#ffffff` contrasts more with `bg`. Cached per (fg, bg, ratio)."""
    key = (fg, bg, ratio)
    cached = _LIFT_CACHE.get(key)
    if cached is not None:
        return cached
    if contrast_ratio(fg, bg) >= ratio:
        _LIFT_CACHE[key] = fg
        return fg
    h, l, s = colorsys.rgb_to_hls(*(int(fg[i:i + 2], 16) / 255 for i in (1, 3, 5)))

    def hex_of(light: float) -> str:
        r, g, b = colorsys.hls_to_rgb(h, light, s)
        return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"

    target = 1.0 if relative_luminance(bg) <= 0.179 else 0.0
    if contrast_ratio(hex_of(target), bg) < ratio:
        lifted = "#ffffff" if contrast_ratio("#ffffff", bg) >= contrast_ratio("#000000", bg) else "#000000"
    else:
        lo, hi = l, target
        for _ in range(26):  # enough to converge at double precision
            mid = (lo + hi) / 2
            if contrast_ratio(hex_of(mid), bg) >= ratio:
                hi = mid
            else:
                lo = mid
        lifted = hex_of(hi)
    _LIFT_CACHE[key] = lifted
    return lifted


def surface_colour(bg: str, variant: str) -> str:
    """Nearest quantized HSL-lightness change reaching 1.2:1 (P1 O7).

    Hue and saturation stay fixed; the theme variant sets the direction.
    Bisection brackets the first passing RGB quantization boundary.
    """
    h, light, saturation = colorsys.rgb_to_hls(*(int(bg[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    low, high = light, 1.0 if variant == "dark" else 0.0

    def colour(value: float) -> str:
        return "#" + "".join(f"{round(c * 255):02x}" for c in colorsys.hls_to_rgb(h, value, saturation))

    for _ in range(48):
        mid = (low + high) / 2
        if contrast_ratio(colour(mid), bg) >= 1.2:
            high = mid
        else:
            low = mid
    return colour(high)


def _rich_hex(colour: Any) -> str:
    """A `RichColor` as the lowercase `#rrggbb` `lift` takes."""
    tc = colour.get_truecolor()
    return f"#{tc.red:02x}{tc.green:02x}{tc.blue:02x}"


@dataclass(frozen=True)
class CursorLook:
    """How one kind of cursor is drawn: its shape and the role colour it takes."""

    shape: str
    colour: str
    text_colour: str | None = None  # the glyph colour under a block; None means the theme's on_accent


class TermPalette:
    """Engine style -> `rich` style, cached by the style's own value.

    The cache key is the style's contents, never its engine id: the engine reuses an id once no cell
    holds it, so an id means different things at different times. The same key always yields the
    same `Style` object, which keeps a repainted row's segments cheap to compare.
    """

    def __init__(self, config: Mapping[str, Any], scheme: Any = None) -> None:
        from .theme_files import Scheme  # local import: theme_files imports from theme

        mode = config["theme.term.palette"]
        if mode not in (PALETTE_THEME, PALETTE_OUTER):
            raise ThemeError(
                f"theme.term.palette: expected {PALETTE_THEME!r} or {PALETTE_OUTER!r}, got {mode!r}")
        if scheme is None or not isinstance(scheme, Scheme):
            raise ThemeError("scheme: expected a Scheme")
        self.mode = mode
        self.scheme = scheme

        # Colours 0-15: theme.term.colors when it holds 16, else the scheme's ansi (TT28).
        raw_colors = config["theme.term.colors"]
        if not isinstance(raw_colors, list):
            raise ThemeError("theme.term.colors: expected a list")
        if len(raw_colors) == 16:
            self._colours = tuple(_colour(f"theme.term.colors[{i}]", c) for i, c in enumerate(raw_colors))
        elif len(raw_colors) == 0:
            self._colours = scheme.ansi
        else:
            raise ThemeError(f"theme.term.colors: expected [] or 16, got {len(raw_colors)}")

        # Default foreground: theme.term.fg when set (non-empty), else scheme.foreground.
        # Under palette="outer" with fg="" the outer terminal's own default is used (TT4): None.
        fg_value = config["theme.term.fg"]
        if fg_value == "":
            if mode == PALETTE_OUTER:
                self.fg: str | None = None
            else:
                self.fg = scheme.foreground
        else:
            self.fg = _colour("theme.term.fg", fg_value)

        roles = _roles(config)
        self.bg = roles["bg"]
        # TT3: term_bg for bg="scheme" is scheme.background (was theme.bg before phase 3).
        bg_value = config["theme.term.bg"]
        if not isinstance(bg_value, str) or not bg_value:
            raise ThemeError(f"theme.term.bg: expected a non-empty string, got {bg_value!r}")
        if bg_value not in ("scheme", "outer") and not _HEX.fullmatch(bg_value):
            raise ThemeError(
                f"theme.term.bg: expected 'scheme', 'outer' or #rrggbb, got {bg_value!r}")

        if mode == PALETTE_OUTER and bg_value in ("scheme", "outer"):
            self.term_bg: str | None = None
        elif _HEX.fullmatch(bg_value):
            self.term_bg = bg_value.lower()
        else:  # bg_value == "scheme" and mode == PALETTE_THEME
            self.term_bg = scheme.background

        # Cursor colours (ruling 6, B2, step 6, OPEN-T4).
        cursor_colour = config["theme.term.cursor_colour"]
        if cursor_colour == "scheme" and mode == PALETTE_THEME:
            # Both cursors take scheme.cursor (fallback scheme.foreground);
            # block glyph takes scheme.cursor_text (fallback scheme.background).
            c_colour = scheme.cursor or scheme.foreground
            c_text = scheme.cursor_text or scheme.background
            self.cursor_typist = CursorLook(
                _shape("theme.term.cursor_typist", config["theme.term.cursor_typist"]), c_colour, c_text)
            self.cursor_viewer = CursorLook(
                _shape("theme.term.cursor_viewer", config["theme.term.cursor_viewer"]), c_colour, c_text)
        else:
            # "role" (default) or "scheme" under palette="outer" (OPEN-T4 ruled: outer ignores the
            # scheme, so the cursor takes TSB's role colours, exactly as cursor_colour="role").
            self.cursor_typist = CursorLook(
                _shape("theme.term.cursor_typist", config["theme.term.cursor_typist"]), roles["accent"])
            self.cursor_viewer = CursorLook(
                _shape("theme.term.cursor_viewer", config["theme.term.cursor_viewer"]), roles["steel"])

        self._on_accent = roles["on-accent"]
        # B7: the minimum contrast ratio, off at 1.0 (ruling 4). Config refuses it above 1.0 under
        # palette="outer", so when it is above 1.0 the mode is "theme" and term_bg is a hex.
        ratio = config["theme.term.minimum_contrast"]
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
            raise ThemeError(f"theme.term.minimum_contrast: expected a number, got {ratio!r}")
        self._minimum_contrast = float(ratio)
        self._cache: dict[tuple, Style] = {}

        default_bg = RichColor.parse(self.term_bg) if self.term_bg else None
        fg_colour = RichColor.parse(self.fg) if self.fg else None
        if self._minimum_contrast > 1.0 and self.fg and self.term_bg:
            fg_colour = RichColor.parse(lift(self.fg, self.term_bg, self._minimum_contrast))
        self.default = Style(color=fg_colour, bgcolor=default_bg)

    # -- engine colours --

    def _palette_colour(self, index: int) -> RichColor:
        if self.mode == PALETTE_OUTER:
            return RichColor.from_ansi(index)
        if index < 16:
            return RichColor.parse(self._colours[index])
        # Indexes 16-255: scheme.extended[index - 16] when present and not None, else xterm value.
        if self.scheme.extended is not None:
            extended_colour = self.scheme.extended[index - 16]
            if extended_colour is not None:
                return RichColor.parse(extended_colour)
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
        # TT31: under outer with no default foreground (fg=None), an inverse cell uses Rich's reverse.
        default_fg = RichColor.parse(self.fg) if self.fg else None
        # A cell with no SGR background of its own gets term_bg; when term_bg is None the cell stays
        # see-through (TT4). Inverse and invisible use term_bg as the default background, or today's
        # `theme.bg` when term_bg is None (step 3: "when it is None, today's behaviour exactly").
        painted_bg = RichColor.parse(self.term_bg) if self.term_bg else None
        fallback_bg = painted_bg if painted_bg is not None else RichColor.parse(self.bg)

        reverse_attr = False
        if resolved.inverse:
            if default_fg is None:
                # TT31: palette="outer" with fg="": no default to swap, so use reverse=True.
                reverse_attr = True
                # Keep fg and bg as they are (both may be None), Rich will swap them.
            else:
                # The painted default background is term_bg, so a reversed blank cell is a solid block.
                fg, bg = (bg or fallback_bg), (fg or default_fg)
        elif fg is None:
            fg = default_fg
        if bg is None and not resolved.inverse:
            bg = painted_bg
        if resolved.invisible:
            fg = bg or fallback_bg
        # B7: above 1.0, lift the cell's final foreground against its final background (the cell's
        # own, else term_bg), after inverse is applied. An invisible cell is never lifted: its text is
        # drawn in its background on purpose, and lifting it would show what the target hid.
        if self._minimum_contrast > 1.0 and not resolved.invisible:
            lifted_bg = bg if bg is not None else painted_bg
            if fg is not None and lifted_bg is not None:
                fg = RichColor.parse(lift(_rich_hex(fg), _rich_hex(lifted_bg), self._minimum_contrast))
        underline = resolved.underline
        return Style(
            color=fg, bgcolor=bg,
            bold=bool(resolved.bold), dim=bool(resolved.faint), italic=bool(resolved.italic),
            strike=bool(resolved.strikethrough), overline=bool(resolved.overline),
            underline=underline == 1 or underline >= 3,
            underline2=underline == 2,
            reverse=reverse_attr,
        )

    # -- cursors --

    def cursor_style(self, look: CursorLook, base: Style) -> Style:
        """`base` (the cell's own style) with the cursor drawn over it: a block fills the cell with
        the cursor colour and draws the glyph in the scheme's cursorText; an underline keeps the cell
        and underlines it in the cursor colour."""
        if look.shape == "block":
            text = look.text_colour or self._on_accent
            # The block fills the cell with its own colours, so clear `reverse`: a base cell that is
            # reversed (TT31 under outer) would swap them, and the cursor must show what it is given.
            return base + Style(color=RichColor.parse(text),
                                bgcolor=RichColor.parse(look.colour), underline=False, reverse=False)
        return base + Style(color=RichColor.parse(look.colour), underline=True)


def _shape(key: str, value: Any) -> str:
    if value not in CURSOR_SHAPES:
        raise ThemeError(f"{key}: expected one of {', '.join(CURSOR_SHAPES)}, got {value!r}")
    return value
