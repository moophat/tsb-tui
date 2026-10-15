"""The stale notice as a banner at the top of the canvas (stale-screen rule 18; design F7, F12).

The contract's notice line is shown whole, whatever the window: it wraps at spaces and never loses a word. It
lives over the canvas, not in the status row, so the row stays one row (design §2.2 principle 3, §2.6) and keeps
the pill, `● REC` and `≡ Menu`. It overlays the canvas rows rather than taking them, so it never changes the
size the TUI asks for (terminal-size §1).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from textual.widgets import Static

from .. import glyphs

# The stale notice line, verbatim: stale-screen rule 18 and ws-frames `stale` fix it, so no surface
# softens it.
STALE_NOTICE = "TSB: lost output - the screen may be stale until a choice is made."


class StaleBanner(Static):
    DEFAULT_CSS = """
    StaleBanner { layer: overlay; dock: top; width: 100%; height: auto; display: none; text-style: bold; }
    """

    def __init__(self, config: Mapping[str, Any]) -> None:
        super().__init__(glyphs.render(f"▲ {STALE_NOTICE}", glyphs.check(config["glyphs.set"])), markup=False)
        self.styles.color = config["status.stale_banner_fg"]
        self.styles.background = config["status.stale_banner_bg"]
        self.styles.padding = (0, config["status.stale_banner_pad"])
