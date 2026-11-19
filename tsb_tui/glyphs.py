"""The glyph set (design D37, the knob file's `[glyphs]`): Unicode by default, with an ASCII fallback for a
terminal whose font lacks the box-drawing and geometric-shape blocks the chrome uses.

The mapping is the knob file's own line, glyph for glyph:

    unicode  ≡ ▦ ▸ ◂ ▴ ● ◆ · ▲ ? ◌ ✕ ◐ • ┄ ┆
    ascii    = # > < ^ * * . ! ? o x ~ ' - :

It touches the TUI's chrome only (the rail, the sidebar, the status row). What a session prints is the
session's and is never rewritten here.
"""

from __future__ import annotations

UNICODE = "unicode"
ASCII = "ascii"
SETS = (UNICODE, ASCII)

_PAIRS = (("≡", "="), ("▦", "#"), ("▸", ">"), ("◂", "<"), ("▴", "^"), ("●", "*"), ("◆", "*"), ("·", "."),
          ("▲", "!"), ("?", "?"), ("◌", "o"), ("✕", "x"), ("◐", "~"), ("•", "'"), ("┄", "-"), ("┆", ":"))

_TO_ASCII = str.maketrans(dict(_PAIRS))

# A glyph can mean two things in two places, so the ASCII fallback is picked by role, not by glyph. The knob line
# maps `◆` to `*` (the rail's Vault mark, the plan's visual rule 2.2); a who-types `◆` (another login types) takes
# `+`, as on main, so it is told from you-type `●`/`*`. Only that one role differs; every other part follows the line.
_WHO_OTHER_ASCII = str.maketrans({"◆": "+"})
_ROLE_ASCII = {"who_other": _WHO_OTHER_ASCII}


class GlyphError(ValueError):
    """`glyphs.set` holds a value that is not a glyph set. The message names the key."""


def check(value: object) -> str:
    if value not in SETS:
        raise GlyphError(f"glyphs.set = {value!r}: expected one of {', '.join(SETS)}")
    return str(value)


def render(text: str, glyph_set: str) -> str:
    """`text` in the chosen glyph set: unchanged for unicode, every chrome glyph replaced for ascii."""
    return text.translate(_TO_ASCII) if glyph_set == ASCII else text


def render_role(text: str, glyph_set: str, role: str) -> str:
    """`text` in the chosen glyph set for a chrome part of `role`: a who-types `◆` (another login types) takes `+`, so
    it is told from you-type `●`/`*`; every other part follows the knob line (`render`)."""
    if glyph_set == ASCII and role in _ROLE_ASCII:
        return text.translate(_ROLE_ASCII[role])
    return render(text, glyph_set)
