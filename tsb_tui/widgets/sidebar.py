"""The fleet sidebar (design §2.3, D42): the rail widened in place, beside the terminal, never an overlay.

It holds every session the principal owns or may join, in groups (Needs you · Open here · Yours, someone
else types · Yours and joinable), each row with who types, the login that types, the countdown or the last
output this TUI saw, and REC. A filter (`/`) and a `mine│all` toggle sit under the header; the selected
session's details show under the groups from `sidebar.details_from` columns; the long tail ends in
`full view ▸`.

Two parts, kept apart so the layout is a pure function a test can read at any width and height:

* `sidebar_view(model, ...)` turns the models into lines of styled parts, each part optionally a click
  target, and lists which rows can be selected.
* `Sidebar` paints that view, keeps the selection and the filter text, and turns keys and clicks into
  `Sidebar.Command` messages. The screen runs them: the sidebar sends nothing to a session, asks the core for
  nothing, and never asks for a size (D44: opening or closing it pans the canvas and sends no request).

While the sidebar has the keys every key is its own (D14), except the leader, which goes on to the screen so
`^\\ f` still closes it.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from .. import glyphs
from ..model.fleet import (
    GROUP_MINE_AND_JOINABLE,
    GROUP_MINE_HELD_BY_OTHERS,
    GROUP_NEEDS_YOU,
    GROUP_OPEN_HERE,
    WHO_ASKS,
    WHO_ENDED,
    WHO_ME,
    WHO_NEEDS_YOU,
    WHO_NOBODY,
    WHO_OTHER,
    WHO_UNREACHABLE,
)
from ..theme import role_colours
from .mouse import mouse_on, set_tip

# What a click or a key on the sidebar asks the screen to do: `Sidebar.Command.name`.
CMD_PICK = "pick"
CMD_TAKE_BACK = "take_back"
CMD_WHOS_HERE = "whos_here"
CMD_CLOSE = "close"
CMD_MODE = "mode"
CMD_FULL_VIEW = "full_view"
CMD_NEW = "new_session"
CMD_ALERTS = "alerts"
CMD_FILTER = "filter"  # the filter text changed
CMD_END = "end"  # End session… on a row

SEPARATOR = "┄"
FULL_VIEW = "full view ▸"
TAIL_MARK = "┆"
SELECTED = "▸"
GROUP_TITLES = {
    GROUP_NEEDS_YOU: ("Needs you",),
    GROUP_OPEN_HERE: ("Open here",),
    GROUP_MINE_HELD_BY_OTHERS: ("Yours, someone else types", "Yours, another types"),
    GROUP_MINE_AND_JOINABLE: ("Yours and joinable",),
}
_WHO_ROLES = {WHO_ME: "who_me", WHO_OTHER: "who_other", WHO_NOBODY: "who_none", WHO_NEEDS_YOU: "who_needs",
              WHO_ASKS: "who_asks", WHO_UNREACHABLE: "who_unreachable", WHO_ENDED: "who_ended"}
# A row in a group nobody has open shows a who glyph only for these: a joinable session that is typed in
# or idle says so by its login column instead.
_SPECIAL_WHO = frozenset({WHO_NEEDS_YOU, WHO_ASKS, WHO_UNREACHABLE, WHO_ENDED})
MAX_TARGET = 12
MAX_LOGIN = 8


@dataclass(frozen=True)
class SidebarEntry:
    """One session in one group, with everything its row says."""

    session_id: str
    group: str
    number: int | None  # its rail chip's number, when it has a chip
    who: str  # cell 2's glyph (D41)
    target: str
    id_label: str | None  # `s.9e014b`, shown only where two sessions share a target
    login: str  # `you`, the login that types, or `·` for nobody
    rec: bool
    front: bool
    reason: str | None  # why it needs you: ending, link_timeout... or unreachable, stale, asks
    countdown: str | None  # the warning window's clock, `4:12`
    remaining: str | None  # time to the end, `6d 23h`
    out_age: str | None  # the last output this TUI saw, `41s`
    search: str  # the text the filter matches against
    tip: str = ""  # the row's tooltip: the whole row, for a row that is cut short


@dataclass(frozen=True)
class RowTime:
    """A painted row's time-bearing fields, as the tick reads them from the record: enough to re-lay out the
    row's right-hand fields without formatting the row again."""

    reason: str | None
    countdown: str | None
    remaining: str | None
    out_age: str | None
    rec: bool


# What a row's right-hand fields are read from: a built entry, or the tick's `RowTime`.
RowText = SidebarEntry | RowTime


@dataclass(frozen=True)
class SidebarModel:
    """What the sidebar shows. `alerts` is None for a login that is not a `broker.health` holder (DT14).

    A group's items are `SidebarEntry` objects, or the session records themselves: in the second shape the model
    holds references and formats nothing, and the three callables below are what the view uses to filter, to size
    the columns and to build an entry for a row it paints — so a rebuild over a long list never builds an entry for
    a row that is not painted."""

    groups: tuple[tuple[str, tuple[SidebarEntry | Mapping[str, Any], ...]], ...]
    mode: str  # "mine" | "all"
    alerts: int | None
    leader_hint: str  # `^\ f`, how the leader closes it
    detail_for: Callable[[str], tuple[str, ...]] | None = None  # the selected session's details (wide only)
    buttons_for: Callable[[str], tuple[str, ...]] | None = None
    # How many entries are in the model before the filter: the header says so when it hides some.
    total: int = 0
    record_fields: Callable[[Mapping[str, Any]], tuple[str, str]] | None = None  # (target, login) of a record
    record_search: Callable[[Mapping[str, Any]], str] | None = None  # the text the filter matches a record against
    record_entry: Callable[[Mapping[str, Any], str], SidebarEntry] | None = None  # the entry for a painted row


@dataclass(frozen=True)
class Part:
    text: str
    role: str
    command: str | None = None  # what a click on this part asks
    arg: str | None = None
    tip: str | None = None  # its tooltip (D13)


@dataclass(frozen=True)
class Line:
    parts: tuple[Part, ...]
    session_id: str | None = None  # a row of a session: a click on it picks it
    group: str | None = None
    tip: str | None = None  # the row's tooltip, when no part of it has its own
    # How many parts of a session row stand before its right-hand fields, so the tick can re-lay out just those.
    prefix_len: int = 0

    @property
    def text(self) -> str:
        return "".join(part.text for part in self.parts)


@dataclass(frozen=True)
class SidebarView:
    lines: tuple[Line, ...]
    rows: tuple[tuple[str, str], ...]  # the selectable rows: (group, session id), top to bottom
    hidden: int  # joinable rows left out for want of room (`┆ N more · full view ▸`)
    # Where the selected session's detail block starts in `lines` and how long it is, so the tick can re-lay it.
    detail_start: int | None = None
    detail_len: int = 0


def _fit(text: str, width: int) -> str:
    """`text` cut to `width` cells with an ellipsis, or padded with blanks to it."""
    if len(text) > width:
        return text[: max(width - 1, 0)] + "…" if width > 0 else ""
    return text.ljust(width)


def _title(group: str, count: int, width: int) -> str:
    for title in GROUP_TITLES[group]:
        text = f"{title} · {count}"
        if len(text) <= width:
            return text
    return f"{GROUP_TITLES[group][-1]} · {count}"[:width]


def entry_matches(entry: SidebarEntry, needle: str) -> bool:
    return not needle or needle.lower() in entry.search.lower()


def _item_id(item: SidebarEntry | Mapping[str, Any]) -> str:
    return item.session_id if isinstance(item, SidebarEntry) else item["id"]


def _entry_for(record: Mapping[str, Any], group: str, model: SidebarModel) -> SidebarEntry:
    """The row for a record the view paints: the screen formats it, and only for a row that is shown."""
    assert model.record_entry is not None, "the model holds records and names no way to build a row"
    return model.record_entry(record, group)


def _item_fields(item: SidebarEntry | Mapping[str, Any], model: SidebarModel) -> tuple[str, str]:
    """(target, login) of a group's item, which is a built entry or the record itself."""
    if isinstance(item, SidebarEntry):
        return item.target, item.login
    assert model.record_fields is not None, "the model holds records and names no way to read their columns"
    return model.record_fields(item)


def _matches(item: SidebarEntry | Mapping[str, Any], needle: str, model: SidebarModel) -> bool:
    if not needle:
        return True
    if isinstance(item, SidebarEntry):
        text = item.search
    else:
        assert model.record_search is not None, "the model holds records and names no filter text for them"
        text = model.record_search(item)
    return needle.lower() in text.lower()


def _time_field(entry: RowText, group: str, wide: bool) -> tuple[str, str] | None:
    """The one clock a row shows: the warning window's, else the last output seen, else the time to the end.
    Narrow rows give it `•41s` and `▲ 4:12` (mockup S1); wide rows spell it (`out 41s`; `not answering 4:12` and
    `ends in 2d 23h` in Needs you, `▲ 4:12` in the groups below: mockup S3)."""
    if entry.reason is not None:
        text = {"unreachable": "unreachable" if wide else "unreach.", "stale": "stale",
                "asks": "asks you"}.get(entry.reason)
        if text is None:
            clock = entry.countdown or ""
            if wide and group == GROUP_NEEDS_YOU:
                text = {"link_timeout": f"not answering {clock}", "idle": f"ends in {clock}",
                        "unattended": f"ends in {clock}"}.get(entry.reason, f"▲ {clock}")
            else:
                text = f"▲ {clock}" if group == GROUP_NEEDS_YOU or wide else clock
        return text.strip(), "warn"
    if entry.out_age is not None:
        return (f"out {entry.out_age}" if wide else f"•{entry.out_age}"), "muted"
    if wide and entry.remaining is not None:
        return entry.remaining, "muted"
    return None


def _right_fields(entry: RowText, group: str, wide: bool, room: int) -> list[tuple[str, str]]:
    """What fits in `room` cells at the row's right edge, best first: the clock, then REC. A narrow row holds
    one of them (mockup S1); a wide row holds the clock and REC (S3)."""
    clock = _time_field(entry, group, wide)
    fields: list[tuple[str, str]] = []
    if clock is not None and len(clock[0]) <= room:
        fields.append(clock)
    if entry.rec and room >= 3 and (not fields or (wide and len(fields[0][0]) + 3 + 3 <= room)):
        fields.append(("REC", "rec"))
    return fields


def _tail_need(entry: RowText, group: str, wide: bool) -> int:
    """The cells a row's right-hand fields (the clock, REC) take, as `_right_fields` lays them out when they fit."""
    clock = _time_field(entry, group, wide)
    size = len(clock[0]) if clock is not None else 0
    if entry.rec:
        size = size + 6 if clock is not None and wide else (size or 3)
    return size


def _row_prefix(entry: SidebarEntry, group: str, *, width: int, target_w: int, login_w: int,
                selected: bool, wide: bool) -> tuple[Part, ...]:
    """Everything a session row shows before its right-hand fields: the mark, the number and who glyph, the
    target, the login. The login takes the row's free width after the clock and REC have had theirs."""
    mark = (SELECTED, "mark") if selected else (" ", "blank")
    who_role = _WHO_ROLES.get(entry.who, "who_none")
    number_role = "num_front" if entry.front else "num"
    parts: list[Part] = [Part(mark[0], mark[1])]
    if group == GROUP_NEEDS_YOU:
        parts += [Part(entry.who, who_role), Part(" ", "blank")]
    elif group in (GROUP_OPEN_HERE, GROUP_MINE_HELD_BY_OTHERS):
        parts += [Part(str(entry.number) if entry.number is not None else " ", number_role),
                  Part(entry.who, who_role), Part(" ", "blank")]
    else:
        glyph = entry.who if entry.who in _SPECIAL_WHO else " "
        parts += [Part(" ", "blank"), Part(glyph, who_role), Part(" ", "blank")]
    parts.append(Part(_fit(entry.target, target_w), "front" if entry.front else "text"))
    login_role = "text" if entry.login != "·" else "muted"
    if entry.id_label and group == GROUP_MINE_AND_JOINABLE:
        # Two sessions on one target: the id stands before the login (mockup S1: `rtr-core-01  s.9e014b  ·`), and
        # the login takes only the room it needs.
        room_for_id = max(width - sum(len(part.text) for part in parts) - 1 - 2 - 1 - 1, 0)  # sep, gap, sep, margin
        label = entry.id_label if len(entry.id_label) <= room_for_id else (
            entry.id_label[: room_for_id - 1] + "…" if room_for_id > 0 else "")
        parts += [Part(" ", "blank"), Part(label, "muted"), Part("  ", "blank")]
        used = sum(len(part.text) for part in parts)
        parts.append(Part(_fit(entry.login, max(min(len(entry.login), width - used - 2), 0)), login_role))
        parts.append(Part(" ", "blank"))
    else:
        # The login takes this row's free width before it is cut (principle 4: two logins with one prefix must stay
        # apart): what is left after what stands before it, a gap each side, the margin and this row's own clock and
        # REC. The right-hand fields are right-aligned, so a longer login moves nothing else. Never narrower than the
        # column's floor, so a row with no room to spare reads as it always did.
        before = sum(len(part.text) for part in parts) + 1
        free = width - before - 1 - 1 - _tail_need(entry, group, wide)
        shown = max(login_w, min(len(entry.login), free))
        parts += [Part(" ", "blank"), Part(_fit(entry.login, shown), login_role), Part(" ", "blank")]
    return tuple(parts)


def _row_tail(used: int, fields: list[tuple[str, str]], width: int) -> list[Part]:
    """The row's right-hand fields, right-aligned with one cell of margin (the clock, then REC)."""
    room = max(width - used - 1, 0)
    tail = "   ".join(text for text, _ in fields)
    parts = [Part(" " * max(room - len(tail), 0), "blank")]
    for index, (text, role) in enumerate(fields):
        if index:
            parts.append(Part("   ", "blank"))
        parts.append(Part(text, role))
    parts.append(Part(" ", "blank"))
    return parts


def _row_line(entry: SidebarEntry, group: str, *, width: int, target_w: int, login_w: int,
              selected: bool, wide: bool) -> Line:
    prefix = _row_prefix(entry, group, width=width, target_w=target_w, login_w=login_w,
                         selected=selected, wide=wide)
    used = sum(len(part.text) for part in prefix)
    fields = _right_fields(entry, group, wide, max(width - used - 1, 0))
    parts = [*prefix, *_row_tail(used, fields, width)]
    return Line(tuple(parts), entry.session_id, group, entry.tip or None, len(prefix))


def _columns(entries: Sequence[SidebarEntry | Mapping[str, Any]], model: SidebarModel) -> tuple[int, int]:
    """The widths of the target and login columns for these rows, read from each row's entry or record. The login
    column is a floor: a row whose own right-hand fields leave it room shows a longer login whole (`_row_line`)."""
    fields = [_item_fields(entry, model) for entry in entries]
    target_w = min(max((len(target) for target, _ in fields), default=0), MAX_TARGET)
    login_w = min(max((len(login) for _, login in fields), default=0), MAX_LOGIN)
    return target_w, login_w


@dataclass(frozen=True)
class Prepared:
    """What one filter text gives the view: the filtered groups, the rows they list, the column widths the groups
    below Needs you share, and Needs you's own. The widget keeps it for its current filter text, so a relayout
    that does not change the filter reads lookups instead of every record."""

    groups: tuple[tuple[str, tuple[SidebarEntry | Mapping[str, Any], ...]], ...]
    rows: tuple[tuple[str, str], ...]
    shared: tuple[int, int]
    needs_you_columns: tuple[int, int]


def _prepare(model: SidebarModel, needle: str) -> Prepared:
    """The filter and the column widths for one filter text, in one pass over the model's items."""
    groups = tuple((name, tuple(item for item in items if _matches(item, needle, model)))
                   for name, items in model.groups)
    rows = tuple((name, _item_id(item)) for name, items in groups for item in items)
    shared = _columns([item for name, items in groups if name not in (GROUP_NEEDS_YOU,)
                       for item in items], model)
    needs_you_columns = _columns([item for name, items in groups if name == GROUP_NEEDS_YOU
                                  for item in items], model)
    return Prepared(groups, rows, shared, needs_you_columns)


def _blank(width: int) -> Line:
    return Line((Part(" " * width, "blank"),))


def _text_line(text: str, role: str, width: int) -> Line:
    return Line((Part(_fit(text, width), role),))


def _wrapped(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, width=max(width, 1)) or [""]


def _detail_lines(model: SidebarModel, sid: str, width: int) -> list[Line]:
    """The selected session's detail block: the hairline, its wrapped paragraphs, and its buttons. Built from
    `model.detail_for`, which reads the clock, so the tick can rebuild the block at the new time."""
    lines: list[Line] = [_text_line(SEPARATOR * width, "hairline", width)]
    for index, paragraph in enumerate(model.detail_for(sid)):
        for text in _wrapped(paragraph, width - 1):
            lines.append(_text_line(text if index == 0 else " " + text, "text" if index == 0 else "muted", width))
    buttons = model.buttons_for(sid) if model.buttons_for is not None else ()
    if buttons:
        row = [Part(f"[{b}] ", "button", f"detail:{b}", sid, b) for b in buttons]
        row.append(Part(" " * max(width - sum(len(part.text) for part in row), 0), "blank"))
        lines.append(Line(tuple(row)))
    return lines


def sidebar_view(model: SidebarModel, *, width: int, height: int, selected: tuple[str, str] | None,
                 filter_text: str, editing: bool, wide: bool, show_details: bool,
                 hint_full_view: bool = True, prepared: Prepared | None = None) -> SidebarView:
    """The sidebar's lines at `width` cells (the hairline is the widget's) and `height` rows."""
    header: list[Line] = []
    close_tip = f"◂ close the sidebar · Esc, {model.leader_hint}"
    head_parts = [Part("▦ Fleet", "title"), Part("  ", "blank"),
                  Part("◂ close", "close", CMD_CLOSE, tip=close_tip),
                  Part(f" {model.leader_hint}", "muted", CMD_CLOSE, tip=close_tip)]
    head = sum(len(p.text) for p in head_parts)
    if wide and hint_full_view and width >= head + 2 + len(FULL_VIEW):
        head_parts += [Part(" " * (width - head - len(FULL_VIEW) - 1), "blank"),
                       Part(FULL_VIEW, "muted", CMD_FULL_VIEW, tip="▦ Full fleet view · F"), Part(" ", "blank")]
    else:
        head_parts.append(Part(" " * max(width - head, 0), "blank"))
    header.append(Line(tuple(head_parts)))
    # The filter row: the filter, and the mine│all toggle at the right edge.
    shown = f"/ {filter_text}▏" if editing else (f"/ {filter_text}" if filter_text else "/ filter")
    toggle = [Part("mine", "toggle_on" if model.mode == "mine" else "toggle", CMD_MODE, "mine",
                   "mine: the sessions you own or have open here · m"),
              Part("│", "hairline", CMD_MODE),
              Part("all", "toggle_on" if model.mode == "all" else "toggle", CMD_MODE, "all",
                   "all: every session the core lets you see · m")]
    room = max(width - len("mine│all") - 1, 1)
    header.append(Line((Part(_fit(shown, room), "filter" if filter_text or editing else "muted", CMD_FILTER,
                             tip="/ filter the list by target, id or login"),
                        *toggle, Part(" ", "blank"))))
    header.append(_text_line(SEPARATOR * width, "hairline", width))

    # The footer: the alerts and a way to a new session.
    footer: list[Line] = [_text_line(SEPARATOR * width, "hairline", width)]
    alert_text = None if model.alerts is None else f"! {model.alerts} alert{'' if model.alerts == 1 else 's'}"
    new_text = (SELECTED + " " if selected == (CMD_NEW, "") else "") + "+ New session · n"
    alerts_tip = "! TSB's own alerts · opens the Alerts tab"
    new_tip = "n to start a New session"
    if alert_text is not None and wide:
        pad = max(width - len(alert_text) - 3 - len(new_text), 0)
        footer.append(Line((Part(alert_text, "alert" if model.alerts else "muted", CMD_ALERTS, tip=alerts_tip),
                            Part("   ", "blank"), Part(new_text, "text", CMD_NEW, tip=new_tip),
                            Part(" " * pad, "blank"))))
    else:
        if alert_text is not None:
            footer.append(Line((Part(_fit(alert_text, width), "alert" if model.alerts else "muted",
                                     CMD_ALERTS, tip=alerts_tip),)))
        footer.append(Line((Part(_fit(new_text, width), "text", CMD_NEW, tip=new_tip),)))

    # The groups, filtered. A group's items are entries (a test's model) or the session records (the screen's),
    # and an entry is built only for a row this view paints. The filter and the column widths come from `prepared`
    # when the caller has them for this filter text, so a relayout of a long list reads lookups, not records.
    needle = filter_text.strip()
    prep = prepared if prepared is not None else _prepare(model, needle)
    groups = prep.groups

    details: list[Line] = []
    rows: tuple[tuple[str, str], ...] = prep.rows
    chosen = selected if selected in rows else None
    if show_details and model.detail_for is not None and chosen is not None:
        details = _detail_lines(model, chosen[1], width)

    fixed = len(header) + len(footer) + len(details)
    body: list[Line] = []
    hidden = 0
    shown_rows: list[tuple[str, str]] = []
    # Every group's heading costs a row; the last group (the long tail) is the one that gives way.
    used = fixed + sum(1 + len(items) for name, items in groups)
    spare = height - used
    # Needs you has its own columns; the groups below share theirs, so a session's login is in one column
    # wherever it is listed (mockups S1, S3). Only Yours and joinable shows an id, and only for the sessions that
    # share a target with another: the open ones are told apart by their number.
    for name, items in groups:
        target_w, login_w = prep.needs_you_columns if name == GROUP_NEEDS_YOU else prep.shared
        count = len(items)
        body.append(_text_line(_title(name, count, width), "group", width))
        keep = items
        if name == GROUP_MINE_AND_JOINABLE and spare < 0:
            allowed = max(len(items) + spare - 1, 0)  # one row for the `N more · full view ▸` line
            keep, hidden = items[:allowed], len(items) - allowed
        for item in keep:
            entry = item if isinstance(item, SidebarEntry) else _entry_for(item, name, model)
            is_selected = chosen == (name, entry.session_id)
            body.append(_row_line(entry, name, width=width, target_w=target_w, login_w=login_w,
                                  selected=is_selected, wide=wide))
            shown_rows.append((name, entry.session_id))
        if hidden and name == GROUP_MINE_AND_JOINABLE:
            more = f"{TAIL_MARK} {hidden} more · {FULL_VIEW}"
            body.append(Line((Part(" ", "blank"), Part(_fit(more, width - 1), "muted", CMD_FULL_VIEW,
                                                            tip="▦ Full fleet view · every session"))))
    lines = [*header, *body, *details, *footer]
    detail_start = len(header) + len(body) if details else None
    # The sidebar is exactly as tall as the window it sits in: blank rows fill the gap, between the groups
    # and the footer, so the footer stays at the bottom.
    gap = height - len(lines)
    if gap > 0:
        lines = [*header, *body, *details, *(_blank(width) for _ in range(gap)), *footer]
    return SidebarView(tuple(lines), tuple(shown_rows), hidden, detail_start, len(details))


class Sidebar(Widget):
    """The rail widened in place. It keeps its selection and its filter text; everything else comes from the
    model the screen sets. Keys while it is focused are its own (D14)."""

    DEFAULT_CSS = """
    Sidebar {
        height: 100%;
        display: none;
    }
    """
    can_focus = True

    class Command(Message):
        """The operator asked for something: `name` is one of the `CMD_*`, `arg` what it concerns (a session id,
        a mode)."""

        def __init__(self, name: str, arg: str | None = None) -> None:
            super().__init__()
            self.name = name
            self.arg = arg

    def __init__(self, config: Mapping, *, leader: str) -> None:
        super().__init__()
        self._build_styles(role_colours(config))
        self._glyph_set = glyphs.check(config["glyphs.set"])
        self._side = config["rail.side"]
        self._leader = leader
        self._wide_from = int(config["sidebar.wide_from"])
        self._details_from = int(config["sidebar.details_from"])
        self._model = SidebarModel((), config["sidebar.start_filter"], None, "")
        self.selected: tuple[str, str] | None = None
        self.filter_text = ""
        self.editing = False
        self.window_width = 0  # the outer window's width, which decides wide and details
        self.sidebar_width = int(config["sidebar.width"])  # the columns it takes, the hairline included
        self._view = SidebarView((), (), 0)
        self._front_id: str | None = None
        # The filter and the column widths for the current filter text and model, reused by every relayout that
        # changes neither (`_prepare`).
        self._prepared: Prepared | None = None
        self._prepared_for: tuple[str, SidebarModel] | None = None

    def _build_styles(self, roles: dict[str, str]) -> None:
        """The sidebar's style table from the role colours (TT14): every colour is a role, so a theme
        switch that rebuilds this table recolours every row without moving any role."""
        base = Style(color=roles["text"], bgcolor=roles["surface"])
        accent, muted, faint = roles["accent"], roles["text_muted"], roles["text_faint"]
        self._styles: dict[str, Style] = {
            "blank": base,
            "text": base,
            "front": base + Style(color=accent),
            "muted": base + Style(color=muted),
            "title": base + Style(bold=True),
            "close": base + Style(color=muted),
            "group": base + Style(color=muted, bold=True),
            "hairline": base + Style(color=roles["border_on_surface"]),
            "mark": base + Style(color=accent, bold=True),
            "num": base + Style(color=muted),
            "num_front": base + Style(color=accent, bold=True),
            "who_me": base + Style(color=accent),
            "who_other": base + Style(color=roles["steel"]),
            "who_none": base + Style(color=faint),
            "who_needs": base + Style(color=roles["warn"]),
            "who_asks": base + Style(color=roles["warn"], bold=True),
            "who_unreachable": base + Style(color=muted),
            "who_ended": base + Style(color=roles["danger"]),
            "warn": base + Style(color=roles["warn"]),
            "rec": base + Style(color=roles["rec_list"]),
            "alert": base + Style(color=roles["danger"], bold=True),
            "toggle": base + Style(color=muted),
            "toggle_on": base + Style(color=accent, bold=True),
            "filter": base,
            "button": base + Style(color=muted),
        }
        self._border = base + Style(color=roles["border_on_surface"])

    def apply_theme(self, config: Mapping) -> None:
        """A run-time look switch (B4): rebuild the table from the resolved roles and repaint."""
        self._build_styles(role_colours(config))
        self.refresh()

    # -- the model --

    @property
    def model(self) -> SidebarModel:
        return self._model

    @property
    def view(self) -> SidebarView:
        return self._view

    @property
    def wide(self) -> bool:
        return self.window_width >= self._wide_from

    @property
    def details_shown(self) -> bool:
        return self.window_width >= self._details_from

    def set_model(self, model: SidebarModel, *, front_id: str | None = None) -> None:
        self._model = model
        self._front_id = front_id
        self._relayout()

    def set_window_width(self, width: int) -> None:
        if width != self.window_width:
            self.window_width = width
            self._relayout()

    def set_width(self, width: int) -> None:
        """The columns the sidebar takes now (the hairline included): by the window, per D42."""
        if width != self.sidebar_width:
            self.sidebar_width = width
        self.styles.width = width
        self._relayout()

    def _build(self) -> SidebarView:
        """The view at the current width and filter. The filter, the rows and the column widths are computed once
        per filter text and model (`_prepare`); a relayout that changes neither reuses them."""
        needle = self.filter_text.strip()
        if self._prepared_for is None or self._prepared_for[1] is not self._model or self._prepared_for[0] != needle:
            self._prepared = _prepare(self._model, needle)
            self._prepared_for = (needle, self._model)
        height = self.size.height or 40
        return sidebar_view(
            self._model, width=max(self.sidebar_width - 1, 1), height=height, selected=self.selected,
            filter_text=self.filter_text, editing=self.editing, wide=self.wide,
            show_details=self.details_shown, prepared=self._prepared)

    def _relayout(self) -> None:
        view = self._build()
        if self.selected not in view.rows and self.selected != (CMD_NEW, ""):
            self.selected = self._default_selection(list(view.rows))
            view = self._build()
        self._view = view
        self.refresh()

    def refresh_times(self, times: Mapping[tuple[str, str], RowTime]) -> bool:
        """The tick's clocks: re-lay out the right-hand fields of the painted rows from their new time values, on
        each row's own prefix. No entry is built for a row here, and nothing else about the row moves.

        A row whose right-hand fields keep their width is re-laid on its stored prefix, which is what a full
        rebuild paints. A row whose fields change width (a clock that grows, a REC that no longer fits) cannot be
        re-laid: its login's width and its fields are decided together in `_row_prefix`, so that row is left as it
        is and the return value says the model must be rebuilt. Returns whether any painted row needs that. The
        selected session's detail block is recomputed at the same time by `_refresh_details`."""
        if not times:
            return False
        width = max(self.sidebar_width - 1, 1)
        wide = self.wide
        lines = list(self._view.lines)
        changed = False
        needs_rebuild = False
        for index, line in enumerate(lines):
            key = (line.group, line.session_id)
            if key not in times:
                continue
            prefix = line.parts[: line.prefix_len]
            used = sum(len(part.text) for part in prefix)
            fields = _right_fields(times[key], line.group, wide, max(width - used - 1, 0))
            new_width = len("   ".join(text for text, _ in fields))
            old_width = len("   ".join(part.text for part in line.parts[line.prefix_len:] if part.text.strip()))
            if new_width != old_width:
                needs_rebuild = True
                continue
            lines[index] = Line((*prefix, *_row_tail(used, fields, width)),
                                 line.session_id, line.group, line.tip, line.prefix_len)
            changed = True
        if changed:
            self._view = SidebarView(tuple(lines), self._view.rows, self._view.hidden,
                                     self._view.detail_start, self._view.detail_len)
            self.refresh()
        details_rebuild = self._refresh_details(width)
        return needs_rebuild or details_rebuild

    def _refresh_details(self, width: int) -> bool:
        """The selected session's detail block at the new clock, recomputed from the model with `detail_for`. A
        block whose lines keep their count is replaced here; one that grew or shrank cannot be re-laid, so this
        returns whether a rebuild is needed."""
        view = self._view
        start = view.detail_start
        if start is None or not self.details_shown or self.selected is None or self._model.detail_for is None:
            return False
        new = _detail_lines(self._model, self.selected[1], width)
        if len(new) != view.detail_len:
            return True
        block = view.lines[start:start + view.detail_len]
        if new == block:
            return False
        lines = list(view.lines)
        lines[start:start + view.detail_len] = new
        self._view = SidebarView(tuple(lines), view.rows, view.hidden, start, len(new))
        self.refresh()
        return False

    def _default_selection(self, rows: list[tuple[str, str]]) -> tuple[str, str] | None:
        """The row of the session in front, in Open here; else the first row."""
        for row in rows:
            if row[0] == GROUP_OPEN_HERE and row[1] == self._front_id:
                return row
        return rows[0] if rows else None

    def on_resize(self, event: events.Resize) -> None:
        self._relayout()

    # -- selection --

    @property
    def selected_session(self) -> str | None:
        return None if self.selected is None or self.selected == (CMD_NEW, "") else self.selected[1]

    def select_group(self, group: str) -> bool:
        """Move the selection to the first row of `group` (the `▲n` of the rail opens it at Needs you)."""
        for row in self._view.rows:
            if row[0] == group:
                self.selected = row
                self._relayout()
                return True
        return False

    def _move(self, step: int) -> None:
        rows = (*self._view.rows, (CMD_NEW, ""))
        index = rows.index(self.selected) if self.selected in rows else -1
        self.selected = rows[(index + step) % len(rows)] if index >= 0 else rows[0 if step > 0 else -1]
        self._relayout()

    # -- text for a test and for the report --

    @property
    def text(self) -> str:
        """The sidebar as the operator reads it, one line per row, the hairline included."""
        return "\n".join(self._line_text(line) for line in self._view.lines)

    def _line_text(self, line: Line) -> str:
        text = glyphs.render(line.text, self._glyph_set)
        return text + "│" if self._side == "left" else "│" + text

    # -- painting --

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if y >= len(self._view.lines):
            segments = [Segment(" " * max(width - 1, 0), self._styles["blank"])]
            line = None
        else:
            line = self._view.lines[y]
            segments = [Segment(glyphs.render(part.text, self._glyph_set), self._styles.get(part.role, self._styles["text"]))
                        for part in line.parts if part.text]
        border = Segment("│", self._border)
        segments = [*segments, border] if self._side == "left" else [border, *segments]
        return Strip(segments).adjust_cell_length(width, self._styles["blank"])

    # -- clicks --

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """The tooltip of the part (or the row) under the pointer: the words and key of a glyph, the whole of a row
        the sidebar had to cut short (D13)."""
        tip = None
        if event.y < len(self._view.lines):
            line = self._view.lines[event.y]
            x = event.x - (1 if self._side == "right" else 0)
            cursor = 0
            for part in line.parts:
                if cursor <= x < cursor + len(part.text):
                    tip = part.tip
                    break
                cursor += len(part.text)
            tip = tip or line.tip
        set_tip(self, tip, self._glyph_set)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if mouse_on(self):
            event.stop()
            self._move(-1)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if mouse_on(self):
            event.stop()
            self._move(1)

    def on_click(self, event: events.Click) -> None:
        if not mouse_on(self) or event.y >= len(self._view.lines):
            return
        event.stop()
        line = self._view.lines[event.y]
        x = event.x - (1 if self._side == "right" else 0)
        cursor = 0
        for part in line.parts:
            if cursor <= x < cursor + len(part.text):
                if part.command is not None:
                    self._command_from_part(part)
                    return
                break
            cursor += len(part.text)
        if line.session_id is not None and line.group is not None:
            self.selected = (line.group, line.session_id)
            self._relayout()
            self.post_message(self.Command(CMD_PICK, line.session_id))

    def _command_from_part(self, part: Part) -> None:
        if part.command == CMD_FILTER:
            self.start_filter()
        elif part.command and part.command.startswith("detail:"):
            label = part.command.removeprefix("detail:")
            name = {"t Take back": CMD_TAKE_BACK, "w Who's here": CMD_WHOS_HERE, "⏎ Open": CMD_PICK,
                    "x End…": CMD_END}.get(label)
            if name is not None:
                self.post_message(self.Command(name, part.arg))
        else:
            self.post_message(self.Command(part.command, part.arg))

    # -- the filter --

    def start_filter(self) -> None:
        self.editing = True
        self._relayout()

    def _filter_changed(self) -> None:
        self._relayout()
        self.post_message(self.Command(CMD_FILTER, self.filter_text))

    # -- keys --

    def on_key(self, event: events.Key) -> None:
        """Every key is the sidebar's while it has the keys (D14), except the leader and the key after it,
        which belong to the screen (`^\\ f` closes it, `^\\ 2` switches)."""
        screen = self.screen
        if event.key == self._leader or getattr(screen, "leader_pending", False):
            return  # on to the screen
        key = event.key
        if not self.editing and key in ("tab", "shift+tab"):
            event.stop()
            if key == "tab":
                self.app.action_focus_next()
            else:
                self.app.action_focus_previous()
            return
        event.stop()
        if key == "ctrl+q":
            # The app's own quit (D38): sessions keep running. The sidebar has the keys right after login, so it
            # must not swallow the one key that leaves.
            self.app.run_worker(self.app.action_quit(), exclusive=False)
            return
        if self.editing:
            self._editing_key(event)
            return
        if key in ("down", "j"):
            self._move(1)
        elif key in ("up", "k"):
            self._move(-1)
        elif key == "n":
            self.post_message(self.Command(CMD_NEW))
        elif key == "enter":
            if self.selected == (CMD_NEW, ""):
                self.post_message(self.Command(CMD_NEW))
            elif self.selected_session is not None:
                self.post_message(self.Command(CMD_PICK, self.selected_session))
        elif key == "escape":
            self.post_message(self.Command(CMD_CLOSE))
        elif key == "t":
            self.post_message(self.Command(CMD_TAKE_BACK, self.selected_session))
        elif key == "w":
            self.post_message(self.Command(CMD_WHOS_HERE, self.selected_session))
        elif key == "x":
            self.post_message(self.Command(CMD_END, self.selected_session))
        elif key == "slash":
            self.start_filter()
        elif key == "m":
            self.post_message(self.Command(CMD_MODE))
        elif key == "F":
            self.post_message(self.Command(CMD_FULL_VIEW))

    def _editing_key(self, event: events.Key) -> None:
        key = event.key
        if key == "escape":
            self.filter_text = ""
            self.editing = False
            self._filter_changed()
        elif key == "enter":
            self.editing = False
            self._relayout()
        elif key == "backspace":
            self.filter_text = self.filter_text[:-1]
            self._filter_changed()
        elif event.is_printable and event.character:
            self.filter_text += event.character
            self._filter_changed()
