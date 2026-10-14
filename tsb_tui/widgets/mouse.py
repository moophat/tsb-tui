"""What the mouse and touch knobs mean for TSB's own chrome (design D13): `mouse.enabled` turns every click, tap
and wheel turn on the rail, the sidebar, the status row and the full view off, and `mouse.tooltips` the words that
say what a glyph is and which key reaches it. One place, so every widget agrees.

The terminal side of `mouse.enabled` is `TsbTuiApp.run`: with it off the TUI never asks the operator's terminal to
report the mouse, so the terminal keeps its own text selection and Textual's other widgets (dialog buttons) see
nothing either.
"""

from __future__ import annotations

from typing import Any

from .. import glyphs


def mouse_on(widget: Any) -> bool:
    """Whether a click, a tap or the wheel may act (`mouse.enabled`)."""
    return bool(widget.app.cfg["mouse.enabled"])


def set_tip(widget: Any, text: str | None, glyph_set: str = glyphs.UNICODE) -> None:
    """The tooltip for what the pointer is over: the text in the glyph set in use, or none when tooltips (or the
    mouse) are off. Textual shows one tooltip per widget, so a widget of several parts sets it as the pointer
    moves from one to the next."""
    if text is None or not mouse_on(widget) or not widget.app.cfg["mouse.tooltips"]:
        widget.tooltip = None
        return
    widget.tooltip = glyphs.render(text, glyph_set)
