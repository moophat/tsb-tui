"""The glyph set (design D37): Unicode by default, an ASCII fallback for a terminal whose font lacks the chrome's
glyphs. The mapping is the knob file's own line.

Why it matters (Rule 9): a font without the geometric-shape block draws boxes or shifts the grid under the rail, and
the fallback is the only way such an operator can read who types. A glyph left unmapped would put a box in the one
place that must be readable, and a mapping that changed a glyph's width would slide the rows beside it.
"""

from __future__ import annotations

import pytest

from tsb_tui import glyphs

UNICODE_LINE = ["≡", "▦", "▸", "◂", "▴", "●", "◆", "·", "▲", "?", "◌", "✕", "◐", "•", "┄", "┆"]
ASCII_LINE = ["=", "#", ">", "<", "^", "*", "+", ".", "!", "?", "o", "x", "~", "'", "-", ":"]


def test_every_glyph_of_the_knob_files_line_maps_to_the_ascii_one_at_the_same_position() -> None:
    """The knob file prints the two lines side by side: the nth Unicode glyph becomes the nth ASCII character."""
    assert len(UNICODE_LINE) == len(ASCII_LINE) == 16
    for unicode_glyph, ascii_char in zip(UNICODE_LINE, ASCII_LINE, strict=True):
        assert glyphs.render(unicode_glyph, glyphs.ASCII) == ascii_char
        assert glyphs.render(unicode_glyph, glyphs.UNICODE) == unicode_glyph


def test_the_ascii_set_keeps_every_cell_one_wide_so_the_rows_beside_it_do_not_slide() -> None:
    text = "1●◂ 2◆• ▲3 !1 ┄┄┄ ≡ ▦▸ ✕ ◌ ?"
    assert len(glyphs.render(text, glyphs.ASCII)) == len(text)


def test_a_glyph_set_that_is_not_one_is_refused_naming_the_key() -> None:
    with pytest.raises(glyphs.GlyphError, match="glyphs.set"):
        glyphs.check("emoji")
    assert glyphs.check("ascii") == "ascii"
