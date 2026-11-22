"""The target details view (rule 10, 2026-11-21): `i` on a target row opens the record the row shows, every
field on its own `field: value` line, each value through `as_text` and wrapped whole to the width. `Esc` and
`i` close it; `i` on a session row opens nothing (the screen's key handler says so).

The rows read the record fields in the contract's order; nothing is invented here: a field the record does not
carry shows empty, an unknown type shows as it is (conventions V5).
"""

from __future__ import annotations

import textwrap
from collections.abc import Mapping
from typing import Any, ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Static

from ..model.targets import as_text
from .chrome_dialogs import _Dialog

# The record's fields in the order the view reads them (`TargetRecord`, contract §targets).
FIELDS = ("id", "name", "type", "host", "port", "group", "tags", "discovered_by", "last_seen")


def detail_lines(record: Mapping[str, Any], width: int) -> list[str]:
    """One `field: value` per record field, each value through `as_text`, wrapped whole (no word cut) to
    `width`; a wrapped continuation stands on its own line."""
    lines: list[str] = []
    for field in FIELDS:
        value = record.get(field)
        text = ", ".join(as_text(item) for item in value) if isinstance(value, list) else as_text(value)
        head = f"{field}: {text}"
        lines.extend(textwrap.wrap(head, width=max(width, 1)) or [f"{field}:"])
    return lines


class TargetDetailsScreen(_Dialog):
    """The details view as a dialog over the workspace: the fields in one block, the whole width at 80 columns.
    It registers with the screen like every dialog, so the status row says `keys → …` while it is open (D14)."""

    DEFAULT_CSS = """
    TargetDetailsScreen {
        align: center middle;
    }
    TargetDetailsScreen .panel {
        width: 100%;
        max-width: 100;
        height: auto;
        max-height: 90%;
        border: round $frame;
        background: $surface;
        padding: 0 1;
    }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "close", "Close"),
                                          Binding("i", "close", "Close", priority=True)]

    def __init__(self, terminal: Any, record: Mapping[str, Any]) -> None:
        super().__init__(terminal)
        self._record = record

    def action_close(self) -> None:
        # `pop_screen` (the way every push here pops): the same call the Menu's dialogs make.
        self.app.pop_screen()

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel"):
            yield Static("", id="detail-body", markup=False)

    def on_mount(self) -> None:
        # `_Dialog.on_mount` (the registration with the terminal) runs on its own: Textual calls every handler.
        self.sync()

    def on_resize(self, event: events.Resize) -> None:
        # The panel's laid-out content width changed: wrap again to it (rule 6, 2026-11-23).
        self.sync()

    def sync(self) -> None:
        """The fields at the panel's laid-out content width, wrapped whole. The screen is full-width, so the
        screen's own width is not the panel's: wrap at the body Static's content width, which is the panel box
        inside its border and padding (rule 6, 2026-11-23)."""
        body = self.query_one("#detail-body", Static)
        body.update("\n".join(detail_lines(self._record, max(body.size.width, 1))))

__all__ = ["FIELDS", "TargetDetailsScreen", "detail_lines"]
