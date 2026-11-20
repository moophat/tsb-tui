"""The responsive navigation rail: words above 80 columns, compact marks at 80 or below.

Sessions, Targets, Vault, permission-gated Alerts and Menu precede the existing session chips;
New session is last. Pure ``rail_rows`` builds the content and ``Rail`` paints it and posts
``Activated`` on clicks. The phone edge rail remains one tappable column.
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
ACT_VAULT = "credentials"
ACT_CHIP = "chip"
ACT_MORE = "more"
ACT_NEW = "new_session"
ACT_BACK = "back"  # Esc on the rail: the keys return to the session open here, if there is one

SEPARATOR = "┄"
EDGE_MARK = "▸"
_WHO_ROLES = {WHO_ME: "who_me", WHO_OTHER: "who_other", WHO_NOBODY: "who_none", WHO_NEEDS_YOU: "who_needs",
              WHO_ASKS: "who_asks", WHO_UNREACHABLE: "who_unreachable", WHO_ENDED: "who_ended"}
# What each who glyph says, for a chip's tooltip (design D41).
_COUNT_CAP = 99
WHO_WORDS = {WHO_ME: "you type", WHO_OTHER: "another login types", WHO_NOBODY: "nobody types",
              WHO_NEEDS_YOU: "needs you", WHO_ASKS: "asks you a question", WHO_UNREACHABLE: "unreachable",
              WHO_ENDED: "ended"}


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


def _worded(mark: str, word: str, count: str, width: int, *, count_first: bool = False) -> str:
    """`mark word count` in `width` cells: a word that does not fit beside its mark and count is cut with an
    ellipsis at its end; the mark and the count are never cut. With `count_first` the count follows the mark
    (`▲1 Sessions`) and the word comes last."""
    if count_first:
        head = f"{mark}{count}" if count else mark
        room = width - len(head) - 1
        if len(word) > room:
            word = word[:max(room - 1, 0)] + "…"
        return f"{head} {word}"
    tail = f" {count}" if count else ""
    room = width - 2 - len(tail)
    if len(word) > room:
        word = word[:max(room - 1, 0)] + "…"
    return f"{mark} {word}{tail}"


def rail_rows(model: RailModel, content_width: int = 3) -> list[RailRow]:
    """The rail's rows, top to bottom, each `content_width` cells wide (the hairline is the widget's)."""
    if model.edge:
        return [RailRow(_cells([(EDGE_MARK, "entry")], content_width), ACT_FLEET)]
    words = content_width >= 11
    # (mark, word, count): the same marks as the compact rail; the words and the mark come together from 81 columns.
    # While sessions need you, the Sessions mark is ▲ and its count follows it, so ▸ always means Sessions and ▲ always
    # means needs you; with none it is `▸` and no count.
    entries = [
        ("▲" if model.need_you else "▸", "Sessions", _count(model.need_you) if model.need_you else "", ACT_NEEDS),
        ("▦", "Targets", "", ACT_FLEET),
        ("◆", "Vault", "", ACT_VAULT),
    ]
    if model.alerts is not None:
        entries.append(("!", "Alerts", _count(model.alerts), ACT_ALERTS))
    entries.append(("≡", "Menu", "", ACT_MENU))
    rows = []
    for mark, word, count, action in entries:
        role = "entry"
        if action == ACT_NEEDS:
            role = "need" if model.need_you else "count_zero"
        elif action == ACT_ALERTS:
            role = "alert" if model.alerts else "count_zero"
        label = (_worded(mark, word, count, content_width, count_first=action == ACT_NEEDS) if words
                 else mark + count)
        rows.append(RailRow(_cells([(label, role)], content_width), action))
    rows.append(RailRow(_cells([(SEPARATOR * content_width, "hairline")], content_width)))
    names = dict(model.names)
    for chip in model.chips:
        front = chip.mark == MARK_FRONT
        # The mark cell: ✕ for a session that ended (it comes ahead of the front and activity marks), else `◂` for the
        # one in front or `•` for output not looked at.
        ended = chip.who == WHO_ENDED
        mark = WHO_ENDED if ended else chip.mark
        mark_role = "who_ended" if ended else ("mark_front" if front else ("mark_unseen" if chip.mark == MARK_UNSEEN
                                                                            else "blank"))
        number = str(chip.number)
        number_role = "num_front" if front else "num"
        if words:
            # `1 core-rtr-01`: the name is cut with an ellipsis before the cell the mark keeps at the row's end.
            room = max(content_width - len(number) - 2, 1)
            name = names.get(chip.session_id, chip.session_id)
            if len(name) > room:
                name = name[:max(room - 1, 0)] + "…"
            parts = [(number, number_role), (" ", "blank"), (name.ljust(room), "name_front" if front else "name"),
                     (mark, mark_role)]
        else:
            parts = [(number, number_role), (mark, mark_role)]
        rows.append(RailRow(_cells(parts, content_width), ACT_CHIP, chip.session_id))
    if model.more:
        rows.append(RailRow(_cells([(" ", "blank"), ("…", "more")], content_width), ACT_MORE))
    rows.append(RailRow(_cells([("+ New" if words else "+", "plus")], content_width), ACT_NEW))
    return rows


class Rail(Widget):
    """The collapsed rail. Content comes from `set_model`; a click on a row posts `Activated`."""

    DEFAULT_CSS = """
    Rail {
        height: 100%;
    }
    """
    can_focus = True

    class Activated(Message):
        """A click or Enter on a row of the rail: what it is (`ACT_*`) and, for a chip, the session it brings to the
        front. `by_key` tells Enter from a click: Enter on Sessions or Targets moves the keys to that list."""

        def __init__(self, action: str, arg: str | None = None, by_key: bool = False) -> None:
            super().__init__()
            self.action = action
            self.arg = arg
            self.by_key = by_key

    def focus_on_click(self) -> bool:
        """A click on a row runs its action; it does not leave the keys on the rail."""
        return False

    def __init__(self, config: Mapping) -> None:
        super().__init__()
        self._build_styles(role_colours(config))
        self._glyph_set = glyphs.check(config["glyphs.set"])
        self._side = config["rail.side"]
        self.rail_width = 4
        self._model = RailModel((), False, 0, None)
        self._rows: list[RailRow] = rail_rows(self._model, self.rail_width - 1)
        self._leader = A.leader_label(config["keys.leader"])
        self._leader_key = config["keys.leader"]
        self._letters = A.leader_keys(config.leader_overrides())
        # The row the keys are on, an index into `rows` (a row with an action); None until the rail has the keys.
        self.selected: int | None = None

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
            "name": base,
            "name_front": base + Style(color=accent),
        }
        self._border = base + Style(color=roles["border_on_surface"])
        # The row the keys are on while the rail has them: a bar in the soft accent, bold so it survives NO_COLOR.
        self._bar = Style(bgcolor=roles["accent_soft"], bold=True)

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
        self._set_rows(rail_rows(model, self.rail_width - 1))

    def set_window_width(self, width: int) -> None:
        """Use words above 80 columns and compact marks at the minimum window width."""
        rail_width = 12 if width >= 81 else 4
        if rail_width != self.rail_width:
            self.rail_width = rail_width
            self.styles.width = 1 if self.edge else rail_width
            self._set_rows(rail_rows(self._model, rail_width - 1))

    def set_width(self, width: int) -> None:
        """The `rail.width` knob (hairline included): rebuild the rows for the width actually used,
        not only set the widget's width after the responsive rows were built."""
        if width != self.rail_width:
            self.rail_width = width
            self.styles.width = 1 if self.edge else width
            self._set_rows(rail_rows(self._model, width - 1))

    def _set_rows(self, rows: list[RailRow]) -> None:
        """New rows: the selection stays on the same row (its action and session) when that row is still listed, else
        on the nearest row that has an action."""
        old = self.selected_row
        self._rows = rows
        if self.selected is not None:
            same = next((i for i, row in enumerate(rows) if old is not None and row.action is not None
                         and (row.action, row.arg) == (old.action, old.arg)), None)
            selectable = self._selectable()
            if same is not None:
                self.selected = same
            elif selectable:
                self.selected = min(selectable, key=lambda i: abs(i - self.selected))
            else:
                self.selected = None
        self.refresh()

    def _selectable(self) -> list[int]:
        return [i for i, row in enumerate(self._rows) if row.action is not None]

    @property
    def rows(self) -> list[RailRow]:
        return list(self._rows)

    @property
    def selected_row(self) -> RailRow | None:
        return None if self.selected is None or self.selected >= len(self._rows) else self._rows[self.selected]

    @property
    def text(self) -> str:
        """The rail as the operator reads it, one line per row of the widget (hairline included)."""
        return "\n".join(self._line_text(y) for y in range(len(self._rows)))

    def _line_text(self, y: int) -> str:
        row = self._rows[y]
        text = "".join(glyphs.render_role(part, self._glyph_set, role) for part, role in row.parts)
        if self._model.edge:
            return text
        edge = "┃" if self.has_focus else "│"
        return text + edge if self._side == "left" else edge + text

    # -- painting and clicking --

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if self._model.edge:
            if y < len(self._rows):
                text = glyphs.render(self._rows[y].text, self._glyph_set)
                return Strip([Segment(text, self._styles["edge"])]).adjust_cell_length(width, self._styles["blank"])
            return Strip([Segment("│" * width, self._border)]).adjust_cell_length(width, self._styles["blank"])
        edge = "┃" if self.has_focus else "│"
        bar = self._bar if self.has_focus and self.selected == y else None
        segments: list[Segment] = []
        if self._side == "right":
            segments.append(Segment(edge, self._border))
        if y < len(self._rows):
            for text, role in self._rows[y].parts:
                if text:
                    style = self._styles[role] if bar is None else self._styles[role] + bar
                    segments.append(Segment(glyphs.render_role(text, self._glyph_set, role), style))
        else:
            segments.append(Segment(" " * (width - 1), self._styles["blank"]))
        if self._side == "left":
            segments.append(Segment(edge, self._border))
        return Strip(segments).adjust_cell_length(width, self._styles["blank"])

    def tip_for(self, y: int) -> str | None:
        """The words, glyph and key of what a click on row `y` does (D13): `≡ Menu ^\\`, `▦ Fleet ^\\ f`, a chip's
        number, session and who types."""
        if self._model.edge:
            return f"▸ Targets and sessions {self._leader} {self._letters['fleet']} · opens the side panel"
        if y >= len(self._rows) or self._rows[y].action is None:
            return None
        row = self._rows[y]
        model = self._model
        letters, leader = self._letters, self._leader
        if row.action == ACT_MENU:
            return f"≡ Menu {leader} · every action, searchable"
        if row.action == ACT_FLEET:
            # The row now shows Targets and opens the side panel's targets, not the old fleet sidebar.
            return "▦ Targets · opens the targets in the side panel"
        if row.action == ACT_VAULT:
            return "◆ Vault · opens saved logins"
        if row.action == ACT_NEEDS:
            # The sessions that need you are the full view's, not a sidebar group.
            if model.need_you:
                return (f"▲{model.need_you} need you · opens the full view at the sessions that need you")
            return "▸ Sessions · opens the full view at the sessions that need you"
        if row.action == ACT_ALERTS:
            return f"! {model.alerts} TSB alert{'' if model.alerts == 1 else 's'} · opens the Alerts tab"
        if row.action == ACT_MORE:
            return "… more sessions · opens the side panel"
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

    # -- keys --

    def on_focus(self, event: events.Focus) -> None:
        if self.selected not in self._selectable():
            selectable = self._selectable()
            self.selected = selectable[0] if selectable else None
        self.refresh()

    def on_blur(self, event: events.Blur) -> None:
        self.refresh()

    def _move(self, step: int) -> None:
        selectable = self._selectable()
        if not selectable:
            return
        if self.selected not in selectable:
            self.selected = selectable[0 if step > 0 else -1]
        else:
            self.selected = selectable[(selectable.index(self.selected) + step) % len(selectable)]
        self.refresh()

    def on_key(self, event: events.Key) -> None:
        """While the rail has the keys every key is its own, except the leader (and the key after it) and Detach, which
        go on to the screen. Up and Down move the row, Enter runs it, `n` is New session, Esc returns to the session,
        Tab and Shift+Tab move along the chain (the screen owns where they go)."""
        screen = self.screen
        key = event.key
        if key == self._leader_key or key == A.DETACH_KEY or getattr(screen, "leader_pending", False):
            return
        event.stop()
        if key == "ctrl+q":
            self.app.run_worker(self.app.action_quit(), exclusive=False)
        elif key in ("tab", "shift+tab"):
            screen.chain_step(self, 1 if key == "tab" else -1)
        elif key in ("down", "j"):
            self._move(1)
        elif key in ("up", "k"):
            self._move(-1)
        elif key == "enter":
            row = self.selected_row
            if row is not None and row.action is not None:
                self.post_message(self.Activated(row.action, row.arg, by_key=True))
        elif key == "n":
            self.post_message(self.Activated(ACT_NEW, by_key=True))
        elif key == "escape":
            self.post_message(self.Activated(ACT_BACK, by_key=True))
