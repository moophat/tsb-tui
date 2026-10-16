"""The rail (design §2.3, D41): four columns beside the terminal, always on screen, that say what needs you
and who types in each session and switch with one click.

Top to bottom: `≡` (the Menu), `▦▸` (the fleet sidebar, which widens this rail in place), `▲n` (how many
sessions need you), `!n` (TSB's alerts, only for a `broker.health` holder), a hairline, one 3-cell chip per
session (its number, who types, then `◂` in front or `•` for output not looked at), `…` when more than nine
chips exist, and `+` (new session). Every row is one click: nothing here needs a key (D5, D12).

Two parts, kept apart so the layout is a pure function a test can read: `rail_rows(model, ...)` turns the
models into rows of styled parts, and `Rail` paints them and turns a click into a `Rail.Activated` message
naming what was clicked. The rail asks the core for nothing and sends nothing to a session.

`rail.phone = "edge"` (on a phone) shrinks the rail to one tappable column: its first row is `▸` and a tap
anywhere on it opens the sidebar.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from .. import glyphs
from ..model import actions as A
from ..model.fleet import (
    MARK_FRONT,
    MARK_UNSEEN,
    WHO_ASKS,
    WHO_ENDED,
    WHO_ME,
    WHO_NEEDS_YOU,
    WHO_NOBODY,
    WHO_OTHER,
    WHO_UNREACHABLE,
    Chip,
)
from ..theme import role_colours
from .mouse import mouse_on, set_tip

# What a click on a row does: the action ids `Rail.Activated` carries.
ACT_MENU = "menu"
ACT_FLEET = "fleet"
ACT_NEEDS = "needs"
ACT_ALERTS = "alerts"
ACT_CHIP = "chip"
ACT_MORE = "more"
ACT_NEW = "new_session"

SEPARATOR = "┄"
EDGE_MARK = "▸"
_WHO_ROLES = {WHO_ME: "who_me", WHO_OTHER: "who_other", WHO_NOBODY: "who_none", WHO_NEEDS_YOU: "who_needs",
              WHO_ASKS: "who_asks", WHO_UNREACHABLE: "who_unreachable", WHO_ENDED: "who_ended"}
_COUNT_CAP = 99
# What each who glyph says, for a chip's tooltip (design D41).
WHO_WORDS = {WHO_ME: "you type", WHO_OTHER: "another login types", WHO_NOBODY: "nobody types",
              WHO_NEEDS_YOU: "needs you", WHO_ASKS: "asks you a question", WHO_UNREACHABLE: "unreachable",
              WHO_ENDED: "ended"}
_COUNT_CAP = 99


@dataclass(frozen=True)
class RailModel:
    """What the rail shows. `alerts` is None for a login that is not a `broker.health` holder (DT14)."""

    chips: tuple[Chip, ...]
    more: bool  # more chips exist than the nine shown
    need_you: int
    alerts: int | None
    edge: bool = False
    # The target each chip's session is on, for its tooltip.
    names: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RailRow:
    """One row of the rail: parts of `(text, role)` that fill the glyph cells, and what a click on it does."""

    parts: tuple[tuple[str, str], ...]
    action: str | None = None
    arg: str | None = None

    @property
    def text(self) -> str:
        return "".join(text for text, _ in self.parts)


def _count(n: int) -> str:
    return str(min(n, _COUNT_CAP))


def _cells(parts: list[tuple[str, str]], width: int) -> tuple[tuple[str, str], ...]:
    """The parts padded on the right with blanks to exactly `width` cells (every glyph here is one cell)."""
    used = sum(len(text) for text, _ in parts)
    return (*parts, (" " * max(width - used, 0), "blank"))


def rail_rows(model: RailModel, content_width: int = 3) -> list[RailRow]:
    """The rail's rows, top to bottom, each `content_width` cells wide (the hairline is the widget's)."""
    if model.edge:
        return [RailRow(_cells([(EDGE_MARK, "entry")], content_width), ACT_FLEET)]
    rows = [
        RailRow(_cells([(" ", "blank"), ("≡", "entry")], content_width), ACT_MENU),
        RailRow(_cells([(" ", "blank"), ("▦▸", "entry")], content_width), ACT_FLEET),
        RailRow(_cells([("▲" + _count(model.need_you), "need" if model.need_you else "count_zero")],
                       content_width), ACT_NEEDS),
    ]
    if model.alerts is not None:
        rows.append(RailRow(_cells([("!" + _count(model.alerts), "alert" if model.alerts else "count_zero")],
                                   content_width), ACT_ALERTS))
    rows.append(RailRow(_cells([(SEPARATOR * 3, "hairline")], content_width)))
    for chip in model.chips:
        front = chip.mark == MARK_FRONT
        mark_role = "mark_front" if front else ("mark_unseen" if chip.mark == MARK_UNSEEN else "blank")
        rows.append(RailRow(
            _cells([(str(chip.number), "num_front" if front else "num"),
                    (chip.who, _WHO_ROLES.get(chip.who, "who_none")), (chip.mark, mark_role)],
                   content_width), ACT_CHIP, chip.session_id))
    if model.more:
        rows.append(RailRow(_cells([(" ", "blank"), ("…", "more")], content_width), ACT_MORE))
    rows.append(RailRow(_cells([(" ", "blank"), ("+", "plus")], content_width), ACT_NEW))
    return rows


class Rail(Widget):
    """The collapsed rail. Content comes from `set_model`; a click on a row posts `Activated`."""

    DEFAULT_CSS = """
    Rail {
        height: 100%;
    }
    """
    can_focus = False

    class Activated(Message):
        """A click on a row of the rail: what it is (`ACT_*`) and, for a chip, the session it brings to the front."""

        def __init__(self, action: str, arg: str | None = None) -> None:
            super().__init__()
            self.action = action
            self.arg = arg

    def __init__(self, config: Mapping) -> None:
        super().__init__()
        self._build_styles(role_colours(config))
        self._glyph_set = glyphs.check(config["glyphs.set"])
        self._side = config["rail.side"]
        self.rail_width = int(config["rail.width"])
        self._model = RailModel((), False, 0, None)
        self._rows: list[RailRow] = rail_rows(self._model, self.rail_width - 1)
        self._leader = A.leader_label(config["keys.leader"])
        self._letters = A.leader_keys(config.leader_overrides())

    def _build_styles(self, roles: dict[str, str]) -> None:
        """The rail's style table from the role colours (TT14): every colour is a role, so a theme
        switch that rebuilds this table recolours every glyph without moving any role."""
        base = Style(color=roles["text"], bgcolor=roles["surface"])
        accent, muted, faint = roles["accent"], roles["text_muted"], roles["text_faint"]
        self._styles: dict[str, Style] = {
            "blank": base,
            "entry": base + Style(color=accent),
            "need": base + Style(color=roles["warn"], bold=True),
            "alert": base + Style(color=roles["danger"], bold=True),
            "count_zero": base + Style(color=faint),
            "hairline": base + Style(color=roles["border_on_surface"]),
            "num": base + Style(color=muted),
            "num_front": base + Style(color=accent, bold=True),
            "who_me": base + Style(color=accent),
            "who_other": base + Style(color=roles["steel"]),
            "who_none": base + Style(color=faint),
            "who_needs": base + Style(color=roles["warn"]),
            "who_asks": base + Style(color=roles["warn"], bold=True),
            "who_unreachable": base + Style(color=muted),
            "who_ended": base + Style(color=roles["danger"]),
            "mark_front": base + Style(color=accent),
            "mark_unseen": base,
            "more": base + Style(color=muted),
            "plus": base,
            "edge": base + Style(color=accent),
        }
        self._border = base + Style(color=roles["border_on_surface"])

    def apply_theme(self, config: Mapping) -> None:
        """A run-time look switch (B4): rebuild the table from the resolved roles and repaint."""
        self._build_styles(role_colours(config))
        self.refresh()

    # -- content --

    @property
    def edge(self) -> bool:
        return self._model.edge

    def set_model(self, model: RailModel) -> None:
        if model == self._model:
            return
        self._model = model
        self.styles.width = 1 if model.edge else self.rail_width
        self._rows = rail_rows(model, self.rail_width - 1)
        self.refresh()

    @property
    def rows(self) -> list[RailRow]:
        return list(self._rows)

    @property
    def text(self) -> str:
        """The rail as the operator reads it, one line per row of the widget (hairline included)."""
        return "\n".join(self._line_text(y) for y in range(len(self._rows)))

    def _line_text(self, y: int) -> str:
        row = self._rows[y]
        text = glyphs.render(row.text, self._glyph_set)
        if self._model.edge:
            return text
        return text + "│" if self._side == "left" else "│" + text

    # -- painting and clicking --

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if self._model.edge:
            if y < len(self._rows):
                text = glyphs.render(self._rows[y].text, self._glyph_set)
                return Strip([Segment(text, self._styles["edge"])]).adjust_cell_length(width, self._styles["blank"])
            return Strip([Segment("│" * width, self._border)]).adjust_cell_length(width, self._styles["blank"])
        segments: list[Segment] = []
        if self._side == "right":
            segments.append(Segment("│", self._border))
        if y < len(self._rows):
            for text, role in self._rows[y].parts:
                if text:
                    segments.append(Segment(glyphs.render(text, self._glyph_set), self._styles[role]))
        else:
            segments.append(Segment(" " * (width - 1), self._styles["blank"]))
        if self._side == "left":
            segments.append(Segment("│", self._border))
        return Strip(segments).adjust_cell_length(width, self._styles["blank"])

    def tip_for(self, y: int) -> str | None:
        """The words, glyph and key of what a click on row `y` does (D13): `≡ Menu ^\\`, `▦ Fleet ^\\ f`, a chip's
        number, session and who types."""
        if self._model.edge:
            return f"▸ Fleet {self._leader} {self._letters['fleet']} · opens the sidebar"
        if y >= len(self._rows) or self._rows[y].action is None:
            return None
        row = self._rows[y]
        model = self._model
        letters, leader = self._letters, self._leader
        if row.action == ACT_MENU:
            return f"≡ Menu {leader} · every action, searchable"
        if row.action == ACT_FLEET:
            return f"▦ Fleet {leader} {letters['fleet']} · widens the rail into the fleet sidebar"
        if row.action == ACT_NEEDS:
            return f"▲ {model.need_you} need you · opens the sidebar at Needs you"
        if row.action == ACT_ALERTS:
            return f"! {model.alerts} TSB alert{'' if model.alerts == 1 else 's'} · opens the Alerts tab"
        if row.action == ACT_MORE:
            return "… more sessions · opens the sidebar"
        if row.action == ACT_NEW:
            return f"+ New session {leader} {letters['new_session']}"
        chip = next((c for c in model.chips if c.session_id == row.arg), None)
        if chip is None:
            return None
        name = dict(model.names).get(chip.session_id, chip.session_id)
        words = [f"{chip.number} {name}", WHO_WORDS.get(chip.who, "")]
        if chip.mark == MARK_FRONT:
            words.append("in front")
        elif chip.mark == MARK_UNSEEN:
            words.append("output you have not looked at")
        words.append(f"{leader} {chip.number}")
        return " · ".join(word for word in words if word)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        set_tip(self, self.tip_for(event.y), self._glyph_set)

    def on_click(self, event: events.Click) -> None:
        if not mouse_on(self):
            return
        if self._model.edge:
            event.stop()
            self.post_message(self.Activated(ACT_FLEET))
            return
        if event.y >= len(self._rows):
            return
        row = self._rows[event.y]
        if row.action is not None:
            event.stop()
            self.post_message(self.Activated(row.action, row.arg))
