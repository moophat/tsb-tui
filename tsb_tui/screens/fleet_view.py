"""The full-screen Fleet view (design §2.3, D7, D8; mockup F1): an extra for long lists, opened from the sidebar's
`full view ▸`, the Menu or `^\\ F`, with the rail still on its left and `Esc` back to the session it came from.

Tabs: Sessions (Needs you first, then a table of the sessions this login owns or may join: target, id, type,
state, keys, open, clients, owner, ends in, REC), Targets (F14: the inventory the core lists for this login, queried
through the broker, a New session opened from a row) and Alerts (the active set, for a `broker.health` holder: a
login the core answered 403 has no tab, DT14). The row actions are the status row's hints while the view has the
keys, each a click (D11); details sit beside the table from `fleet_view.details_from` columns.

Two parts, kept apart as the sidebar's are, so the layout is a pure function a test reads at any size:

* `fleet_view_lines(model, ...)` turns the model into lines of styled parts, each part optionally a click target,
  and lists which rows can be selected. It reuses the sidebar's `Part` and `Line`.
* `FleetView` paints that layout, keeps the selection, the filter text and the scroll, and turns keys and clicks
  into `FleetView.Command` messages. The screen runs them: the view asks the core for nothing itself and sends
  nothing to a session.

While the view has the keys every key is its own (D14), except the leader; `Ctrl-Q` leaves the TUI (D38).
"""

from __future__ import annotations

import textwrap
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from .. import glyphs
from ..model.fleet import (
    WHO_ASKS,
    WHO_ENDED,
    WHO_ME,
    WHO_NEEDS_YOU,
    WHO_NOBODY,
    WHO_OTHER,
    WHO_UNREACHABLE,
)
from ..theme import role_colours
from ..widgets.mouse import mouse_on, set_tip
from ..widgets.sidebar import (
    CMD_CLOSE,
    CMD_END,
    CMD_FILTER,
    CMD_MODE,
    CMD_NEW,
    CMD_PICK,
    CMD_TAKE_BACK,
    CMD_WHOS_HERE,
    SELECTED,
    SEPARATOR,
    Line,
    Part,
)

CMD_TARGET_NEW = "target_new"
CMD_TARGET_EDIT = "target_edit"
CMD_TARGET_DELETE = "target_delete"

CMD_TAB = "tab"
CMD_ENDED = "ended"  # the show-ended box
CMD_OPEN_TARGET = "open_target"  # `⏎` on the Targets tab: New session on the selected target
CMD_QUERY = "query"  # the Targets query was entered: `arg` is its text
CMD_MORE_TARGETS = "more_targets"  # the selection reached the last row loaded: the next page, if there is one
CMD_TAB_CHANGED = "tab_changed"  # the tab shown changed: `arg` is its name
CMD_SAVE_LOGIN = "save_login"  # `l` on the Targets tab: save a login for the marked targets, else the selected one
CMD_MARK = "mark"  # `space` on the Targets tab: mark or unmark the selected target
CMD_MAP = "map_credential"  # `p` on the Targets tab: map one of my credentials to the marked targets, else the selected

TAB_SESSIONS = "sessions"
TAB_TARGETS = "targets"
TAB_ALERTS = "alerts"
TAB_LABELS = {TAB_SESSIONS: "Sessions", TAB_TARGETS: "Targets", TAB_ALERTS: "Alerts"}
GROUP_NEEDS = "needs_you"
GROUP_TABLE = "sessions"
GROUP_ALERTS = "alert"
GROUP_TARGETS = "targets"

TABLE_TITLE = "Sessions you own or may join"
DETAILS_WIDTH = 42  # the details pane beside the table, its hairline included
# The table's columns in the order they read, with their headers; and the order they leave as the view narrows
# (the last leaves first): the target and the keys stay on a phone.
COLUMNS = ("target", "id", "type", "state", "keys", "open", "clients", "owner", "ends", "rec")
HEADERS = {"target": "TARGET", "id": "ID", "type": "TYPE", "state": "STATE", "keys": "KEYS", "open": "OPEN",
           "clients": "CLIENTS", "owner": "OWNER", "ends": "ENDS IN", "rec": "REC"}
DROP_ORDER = ("rec", "type", "clients", "ends", "owner", "open", "id", "state", "keys", "target")
MAX_WIDTHS = {"target": 16, "id": 12, "type": 8, "state": 14, "keys": 12, "open": 4, "clients": 7, "owner": 12,
              "ends": 24, "rec": 3}
# The Targets table (F14): NAME, TYPE, GROUP, TAGS, SOURCE (`discovered_by`), SESSIONS and YOU CAN. As the view
# narrows the columns leave in this order (the last leaves first); NAME and TYPE stay.
TARGET_COLUMNS = ("name", "type", "group", "tags", "source", "sessions", "can")
TARGET_HEADERS = {"name": "NAME", "type": "TYPE", "group": "GROUP", "tags": "TAGS", "source": "SOURCE",
                  "sessions": "SESSIONS", "can": "YOU CAN"}
TARGET_DROP_ORDER = ("can", "source", "tags", "sessions", "group")
TARGET_MAX_WIDTHS = {"name": 32, "type": 8, "group": 16, "tags": 26, "source": 10, "sessions": 12, "can": 8}
TARGETS_TITLE = "Targets this login may see"
QUERY_HINT = "/ query the inventory through the broker: type:  tag:  group:  source:"
GAP = 2
_WHO_ROLES = {WHO_ME: "who_me", WHO_OTHER: "who_other", WHO_NOBODY: "who_none", WHO_NEEDS_YOU: "who_needs",
              WHO_ASKS: "who_asks", WHO_UNREACHABLE: "who_unreachable", WHO_ENDED: "who_ended"}


@dataclass(frozen=True)
class FleetRow:
    """One session in the view, every cell already in words (the screen reads the models; the layout reads this)."""

    session_id: str
    who: str  # the chip's second cell (D41)
    target: str
    id_label: str  # `s.7f3a2c`
    type: str
    state: str  # `● live`, `○ detached`, `◌ unreachable`, `✕ killed`
    state_role: str  # `ok`, `muted`, `warn`, `danger`
    keys: str  # `you`, a login, or `· nobody`
    open: str  # its rail number when it is open here
    clients: str
    owner: str
    ends: str  # `6d 23h · idle`, `▲ 4:12 · link`
    rec: bool
    front: bool
    note: str  # why it needs you (Needs you rows)
    types: str  # `linh types`, `yours · nobody` (Needs you rows)
    search: str
    ended: bool = False


@dataclass(frozen=True)
class TargetEntry:
    """One target in the Targets tab, every cell already text (`model.targets.as_text`) and in words."""

    target_id: str  # the record's own id: a key, never drawn
    name: str
    type: str
    group: str
    tags: str
    source: str
    sessions: str  # `2 ● live`, `1 ○`, or `·`
    can: str  # `attach` where this login sees a live session there, else empty


@dataclass(frozen=True)
class FleetViewModel:
    """What the view shows. `alert_lines` is None for a login that is not a `broker.health` holder: it has no
    Alerts tab at all (DT14)."""

    needs: tuple[FleetRow, ...]
    rows: tuple[FleetRow, ...]  # the table, before the filter and "show ended"
    mode: str  # "mine" | "all": the sidebar's own filter, one state for both
    alert_lines: tuple[str, ...] | None
    back_label: str  # `1 rtr-core-01`: the session `Esc` returns to; "" when none is open
    tabs: tuple[str, ...] = (TAB_SESSIONS,)
    detail_for: Callable[[str], tuple[str, ...]] | None = None
    buttons_for: Callable[[str], tuple[str, ...]] | None = None
    targets: tuple[TargetEntry, ...] = ()
    target_edit: bool = False
    targets_count: str | None = None  # `412`, `100+` while another page may follow; None before the first page
    targets_note: str = ""  # why there is no list: the inventory is unavailable
    query_error: str = ""  # the broker's (or the TUI's) refusal of the query, shown in the query line
    target_details_for: Callable[[str], tuple[str, ...]] | None = None  # the two lines under the table


@dataclass(frozen=True)
class FleetLayout:
    lines: tuple[Line, ...]
    rows: tuple[tuple[str, str], ...]  # the selectable rows: (group, session id), top to bottom
    top: int  # the first body line shown: the scroll
    shown: int  # how many sessions the table lists after the filter and "show ended"


def _fit(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) > width:
        return text[: width - 1] + "…"
    return text.ljust(width)


def visible_rows(model: FleetViewModel, filter_text: str, show_ended: bool) -> list[FleetRow]:
    """The table's rows after the filter and "show ended": an ended session is listed only when asked for."""
    needle = filter_text.strip().lower()
    return sorted((row for row in model.rows
                   if (show_ended or not row.ended) and (not needle or needle in row.search.lower())),
                  key=lambda row: row.ended)


def visible_needs(model: FleetViewModel, filter_text: str) -> list[FleetRow]:
    needle = filter_text.strip().lower()
    return [row for row in model.needs if not needle or needle in row.search.lower()]


def _columns_for(rows: Sequence[FleetRow], width: int) -> list[tuple[str, int]]:
    """The columns that fit in `width` cells, each with its width: by their header and what their cells hold,
    never more than `MAX_WIDTHS`. A column leaves in `DROP_ORDER` when the view is too narrow for all."""
    cells = {
        "target": [r.target for r in rows], "id": [r.id_label for r in rows], "type": [r.type for r in rows],
        "state": [r.state for r in rows], "keys": [r.keys for r in rows], "open": [r.open for r in rows],
        "clients": [r.clients for r in rows], "owner": [r.owner for r in rows], "ends": [r.ends for r in rows],
        "rec": ["●" if r.rec else "" for r in rows],
    }
    sizes = {name: min(max([len(HEADERS[name]), *(len(c) for c in cells[name])]), MAX_WIDTHS[name])
             for name in COLUMNS}
    return _keep_columns(COLUMNS, sizes, DROP_ORDER, width)


def _keep_columns(names: Sequence[str], sizes: Mapping[str, int], drop_order: Sequence[str],
                  width: int) -> list[tuple[str, int]]:
    kept = list(names)
    lead = 3  # the selection mark and its gap

    def total() -> int:
        return lead + sum(sizes[n] for n in kept) + GAP * (len(kept) - 1)

    for name in drop_order:
        if total() <= width or len(kept) == 1:
            break
        kept.remove(name)
    return [(name, sizes[name]) for name in kept]


def _target_cells(entry: TargetEntry) -> dict[str, str]:
    return {"name": entry.name, "type": entry.type, "group": entry.group, "tags": entry.tags,
            "source": entry.source, "sessions": entry.sessions, "can": entry.can}


def _target_columns(entries: Sequence[TargetEntry], width: int) -> list[tuple[str, int]]:
    """The Targets table's columns that fit in `width`, each with its width; they leave in `TARGET_DROP_ORDER`."""
    cells = [_target_cells(entry) for entry in entries]
    sizes = {name: min(max([len(TARGET_HEADERS[name]), *(len(c[name]) for c in cells)]), TARGET_MAX_WIDTHS[name])
             for name in TARGET_COLUMNS}
    return _keep_columns(TARGET_COLUMNS, sizes, TARGET_DROP_ORDER, width)


def _target_row(entry: TargetEntry, columns: list[tuple[str, int]], *, selected: bool, width: int,
                marked: bool = False) -> Line:
    parts = [Part(SELECTED if selected else " ", "mark" if selected else "blank"),
             Part("+ " if marked else "  ", "mark" if marked else "blank")]
    cells = _target_cells(entry)
    for index, (name, size) in enumerate(columns):
        if index:
            parts.append(Part(" " * GAP, "blank"))
        role = "muted" if name in ("type", "source") else ("front" if name == "can" and cells[name] else "text")
        parts.append(Part(_fit(cells[name], size), role))
    used = sum(len(p.text) for p in parts)
    parts.append(Part(" " * max(width - used, 0), "blank"))
    return Line(tuple(parts), entry.target_id, GROUP_TARGETS)


def _cell(row: FleetRow, name: str) -> tuple[str, str]:
    """The text and the role of one cell of a table row."""
    if name == "state":
        return row.state, row.state_role
    if name == "rec":
        return ("●" if row.rec else ""), "rec"
    text = {"target": row.target, "id": row.id_label, "type": row.type, "keys": row.keys, "open": row.open,
            "clients": row.clients, "owner": row.owner, "ends": row.ends}[name]
    role = "front" if name == "target" and row.front else ("muted" if name in ("id", "type") else "text")
    if name == "keys" and row.keys.startswith("·"):
        role = "muted"
    if name == "ends" and row.ends.startswith("▲"):
        role = "warn"
    return text, role


def _table_row(row: FleetRow, columns: list[tuple[str, int]], *, selected: bool, width: int) -> Line:
    parts = [Part(SELECTED if selected else " ", "mark" if selected else "blank"), Part("  ", "blank")]
    for index, (name, size) in enumerate(columns):
        if index:
            parts.append(Part(" " * GAP, "blank"))
        text, role = _cell(row, name)
        parts.append(Part(_fit(text, size), role))
    used = sum(len(p.text) for p in parts)
    parts.append(Part(" " * max(width - used, 0), "blank"))
    return Line(tuple(parts), row.session_id, GROUP_TABLE)


def _needs_row(row: FleetRow, *, selected: bool, width: int, target_w: int, id_w: int, types_w: int) -> Line:
    """`▲ sw-agg-02     s.b2c771   linh types       not answering · ends in 4:12` (mockup F1)."""
    parts = [Part(SELECTED if selected else " ", "mark" if selected else "blank"), Part(" ", "blank"),
             Part(row.who, _WHO_ROLES.get(row.who, "who_none")), Part(" ", "blank"),
             Part(_fit(row.target, target_w), "text"), Part("  ", "blank"),
             Part(_fit(row.id_label, id_w), "muted"), Part("  ", "blank"),
             Part(_fit(row.types, types_w), "text"), Part("  ", "blank")]
    used = sum(len(p.text) for p in parts)
    parts.append(Part(_fit(row.note, max(width - used, 0)), "warn"))
    return Line(tuple(parts), row.session_id, GROUP_NEEDS)


def _text(text: str, role: str, width: int) -> Line:
    return Line((Part(_fit(text, width), role),))


def _tab_parts(model: FleetViewModel, tab: str, counts: Mapping[str, int | str | None]) -> list[Part]:
    parts: list[Part] = [Part(" ", "blank")]
    for name in model.tabs:
        if name not in TAB_LABELS:
            continue
        count = counts.get(name)
        label = TAB_LABELS[name] + (f" {count}" if count is not None else "")
        if name == tab:
            parts.append(Part(f"[{label}]", "tab_on", CMD_TAB, name, f"{TAB_LABELS[name]} · Tab for the next tab"))
        else:
            parts.append(Part(f" {label} ", "tab", CMD_TAB, name, f"{TAB_LABELS[name]} · Tab for the next tab"))
        parts.append(Part("  ", "blank"))
    return parts


def _controls(model: FleetViewModel, filter_text: str, editing: bool, show_ended: bool) -> list[Part]:
    shown = f"/ {filter_text}▏" if editing else (f"/ {filter_text}" if filter_text else "/ filter")
    return [
        Part(shown, "filter" if filter_text or editing else "muted", CMD_FILTER,
             tip="/ filter the list by target, id, login or owner"),
        Part("    ", "blank"),
        Part("mine", "toggle_on" if model.mode == "mine" else "toggle", CMD_MODE, "mine",
             "mine: the sessions you own or have open here · m"),
        Part("│", "hairline", CMD_MODE),
        Part("joinable", "toggle_on" if model.mode == "all" else "toggle", CMD_MODE, "all",
             "joinable: every session you own or may join · m"),
        Part("    ", "blank"),
        Part(("☑" if show_ended else "☐") + " show ended", "toggle_on" if show_ended else "toggle", CMD_ENDED,
             tip="show the sessions that have ended · e"),
        Part(" ", "blank"),
    ]


def _query_controls(model: FleetViewModel, query_text: str, editing: bool) -> list[Part]:
    """The Targets tab's query line: what is typed (or `/ query`), and the refusal of the last query if there was one."""
    shown = f"/ {query_text}▏" if editing else (f"/ {query_text}" if query_text else "/ query")
    parts = [Part(shown, "filter" if query_text or editing else "muted", CMD_FILTER, tip=QUERY_HINT)]
    if model.query_error:
        parts += [Part("    ", "blank"), Part("✕ " + model.query_error, "danger")]
    parts.append(Part(" ", "blank"))
    return parts


def _joined(left: list[Part], right: list[Part], width: int) -> Line:
    used = sum(len(p.text) for p in left) + sum(len(p.text) for p in right)
    return Line((*left, Part(" " * max(width - used, 0), "blank"), *right))


def fleet_view_lines(model: FleetViewModel, *, width: int, height: int, tab: str,
                     selected: tuple[str, str] | None, filter_text: str, editing: bool, show_ended: bool,
                     group_by: str, details: bool, top: int, query_text: str = "",
                     marked: frozenset[str] = frozenset()) -> FleetLayout:
    """The view's lines at `width` by `height`: the header (title, tabs and controls), then the body, which scrolls
    from line `top`; beside it the selected session's details when `details` is set and the view is wide enough."""
    head: list[Line] = []
    back = f"Esc back to {model.back_label}" if model.back_label else "Esc back"
    head.append(_joined([Part(" ▦ Fleet · full view", "title")],
                        [Part(back, "muted", CMD_CLOSE, tip=f"{back} · or click here"), Part(" ", "blank")], width))
    needs = visible_needs(model, filter_text)
    table = visible_rows(model, filter_text, show_ended)
    counts: dict[str, int | str | None] = {
        TAB_SESSIONS: len(table), TAB_TARGETS: model.targets_count,
        TAB_ALERTS: None if model.alert_lines is None else len(model.alert_lines)}
    tab_parts = _tab_parts(model, tab, counts)
    if tab == TAB_SESSIONS:
        controls = _controls(model, filter_text, editing, show_ended)
    elif tab == TAB_TARGETS:
        controls = _query_controls(model, query_text, editing)
    else:
        controls = [Part(" ", "blank")]
    if sum(len(p.text) for p in tab_parts) + sum(len(p.text) for p in controls) <= width:
        head.append(_joined(tab_parts, controls, width))
    else:
        head.append(_joined(tab_parts, [], width))
        head.append(_joined([Part(" ", "blank")], controls, width))
    head.append(_text("", "blank", width))

    pane = details and width >= DETAILS_WIDTH * 2 and tab != TAB_TARGETS
    table_w = width - DETAILS_WIDTH if pane else width
    # A session's line is built only when it falls in the window shown: the layout lists every row (the selection and
    # the scroll need their order and place) but a keystroke must not pay for the lines of rows scrolled out of view.
    body: list[Line | Callable[[], Line]] = []
    rows: list[tuple[str, str]] = []
    row_at: dict[tuple[str, str], int] = {}

    if tab == TAB_TARGETS:
        title = f" {TARGETS_TITLE} · {model.targets_count}" if model.targets_count is not None else f" {TARGETS_TITLE}"
        if marked:
            title += f" · {len(marked)} marked"
        body.append(_text(title, "group", table_w))
        if model.targets_note:
            body.append(_text("   " + model.targets_note, "warn", table_w))
        elif not model.targets:
            body.append(_text("   No targets to show." if model.targets_count is not None
                              else "   Reading the inventory…", "muted", table_w))
        else:
            columns = _target_columns(model.targets, table_w)
            header = [Part(" ", "blank"), Part("  ", "blank")]
            for index, (name, size) in enumerate(columns):
                if index:
                    header.append(Part(" " * GAP, "blank"))
                header.append(Part(_fit(TARGET_HEADERS[name], size), "header"))
            header.append(Part(" " * max(table_w - sum(len(p.text) for p in header), 0), "blank"))
            body.append(Line(tuple(header)))
            for entry in model.targets:
                key = (GROUP_TARGETS, entry.target_id)
                row_at[key] = len(body)
                rows.append(key)
                body.append(partial(_target_row, entry, columns, selected=selected == key, width=table_w,
                                    marked=entry.target_id in marked))
    elif tab == TAB_ALERTS and model.alert_lines is not None:
        body.append(_text(" Active alerts · " + str(len(model.alert_lines)), "group", table_w))
        for text in model.alert_lines or ("No active alerts.",):
            for line in textwrap.wrap(text, width=max(table_w - 3, 8)) or [""]:
                body.append(_text("   " + line, "text" if text != "No active alerts." else "muted", table_w))
    else:
        if needs:
            body.append(_text(" Needs you", "group", table_w))
            target_w = min(max(len(r.target) for r in needs), 16)
            id_w = min(max(len(r.id_label) for r in needs), 12)
            types_w = min(max(len(r.types) for r in needs), 18)
            for row in needs:
                key = (GROUP_NEEDS, row.session_id)
                row_at[key] = len(body)
                rows.append(key)
                body.append(partial(_needs_row, row, selected=selected == key, width=table_w, target_w=target_w,
                                    id_w=id_w, types_w=types_w))
            body.append(_text("", "blank", table_w))
        body.append(_text(f" {TABLE_TITLE} · {len(table)}", "group", table_w))
        if not table:
            body.append(_text("   No sessions to show.", "muted", table_w))
        else:
            columns = _columns_for(table, table_w)
            header = [Part(" ", "blank"), Part("  ", "blank")]
            for index, (name, size) in enumerate(columns):
                if index:
                    header.append(Part(" " * GAP, "blank"))
                header.append(Part(_fit(HEADERS[name], size), "header"))
            used = sum(len(p.text) for p in header)
            header.append(Part(" " * max(table_w - used, 0), "blank"))
            body.append(Line(tuple(header)))
            last_target = None
            per_target = Counter((r.ended, r.target) for r in table) if group_by == "target" else {}
            ended_heading = False
            for row in table:
                if row.ended and not ended_heading:
                    body.append(_text(" Ended", "group", table_w))
                    ended_heading = True
                    last_target = None
                if group_by == "target" and row.target != last_target:
                    body.append(_text(f"   {row.target} · {per_target[(row.ended, row.target)]}", "muted", table_w))
                last_target = row.target
                key = (GROUP_TABLE, row.session_id)
                row_at[key] = len(body)
                rows.append(key)
                body.append(partial(_table_row, row, columns, selected=selected == key, width=table_w))

    room = max(height - len(head), 0)
    foot: list[Line] = []
    if tab == TAB_TARGETS and room >= 8:
        # F14's two lines under the table: the selected target's facts, and the sessions this login sees there.
        foot = _target_foot(model, selected, table_w)
        room -= len(foot)
    chosen = selected if selected in row_at else None
    if chosen is not None:
        line = row_at[chosen]
        if line < top:
            top = line
        elif line >= top + room:
            top = line - room + 1
    top = max(0, min(top, max(len(body) - room, 0)))
    window = [line() if callable(line) else line for line in body[top: top + room]]
    if len(window) < room:
        window += [_text("", "blank", table_w)] * (room - len(window))

    if pane:
        window = _with_details(window, model, chosen, table_w, room)
    shown = len(model.targets) if tab == TAB_TARGETS else len(table)
    return FleetLayout((*head, *window, *foot), tuple(rows), top, shown)


def _target_foot(model: FleetViewModel, selected: tuple[str, str] | None, width: int) -> list[Line]:
    """A blank line, then the two lines of the selected target's details; blank when nothing is selected."""
    lines: tuple[str, ...] = ()
    if selected is not None and selected[0] == GROUP_TARGETS and model.target_details_for is not None:
        lines = model.target_details_for(selected[1])
    first = f"─ {lines[0]} " if lines else ""
    second = f"  {lines[1]}" if len(lines) > 1 else ""
    return [_text("", "blank", width), Line((Part(_fit(first.ljust(width, "─") if first else "", width), "muted"),)),
            _text(second, "muted", width)]


def _with_details(window: list[Line], model: FleetViewModel, chosen: tuple[str, str] | None, table_w: int,
                  room: int) -> list[Line]:
    """The selected session's details at the right of each line, behind a hairline (mockup S3's sentences)."""
    inner = DETAILS_WIDTH - 2
    side: list[Part] = []
    lines: list[tuple[str, str]] = []
    buttons: tuple[str, ...] = ()
    if chosen is not None and model.detail_for is not None:
        for index, paragraph in enumerate(model.detail_for(chosen[1])):
            for text in textwrap.wrap(paragraph, width=inner) or [""]:
                lines.append((text if index == 0 else " " + text, "text" if index == 0 else "muted"))
        if model.buttons_for is not None:
            buttons = model.buttons_for(chosen[1])
    out: list[Line] = []
    for y, line in enumerate(window):
        if y < len(lines):
            text, role = lines[y]
            side = [Part("│", "hairline"), Part(" " + _fit(text, inner), role)]
        elif buttons and y == len(lines) + 1:
            row = [Part("│ ", "hairline")]
            row += [Part(f"[{b}] ", "button", f"detail:{b}", chosen[1], b) for b in buttons]
            used = sum(len(p.text) for p in row)
            side = [*row, Part(" " * max(DETAILS_WIDTH - used, 0), "blank")]
        else:
            side = [Part("│", "hairline"), Part(" " * (DETAILS_WIDTH - 1), "blank")]
        out.append(Line((*line.parts, *side), line.session_id, line.group, line.tip))
    return out


class FleetView(Widget):
    """The full view. It keeps its selection, its tab, its filter text and its scroll; everything else comes from
    the model the screen sets. Keys while it has them are its own (D14)."""

    DEFAULT_CSS = """
    FleetView {
        layer: overlay;
        width: 100%;
        height: 100%;
        display: none;
    }
    """
    can_focus = True

    class Command(Message):
        """The operator asked for something: `name` is one of the sidebar's `CMD_*` or this module's, `arg` what it
        concerns (a session id, a tab, a mode)."""

        def __init__(self, name: str, arg: str | None = None) -> None:
            super().__init__()
            self.name = name
            self.arg = arg

    def __init__(self, config: Mapping, *, leader: str) -> None:
        super().__init__()
        self._build_styles(role_colours(config))
        self._glyph_set = glyphs.check(config["glyphs.set"])
        self._leader = leader
        self._details_from = int(config["fleet_view.details_from"])
        self._group_by = config["fleet_view.group_by"]
        self._model = FleetViewModel((), (), config["sidebar.start_filter"], None, "")
        self.tab = TAB_SESSIONS
        self.selected: tuple[str, str] | None = None
        # The targets marked with `space` on the Targets tab: the ones Save a login and Map act on (one batch call).
        self.marked: set[str] = set()
        self.filter_text = ""
        self.query_text = ""  # the Targets tab's query as typed; `applied_query` is the one last entered
        self.applied_query = ""
        self.editing = False
        self.show_ended = bool(config["fleet_view.show_ended"])
        self.window_width = 0
        self.scroll_top = 0
        self._layout = FleetLayout((), (), 0, 0)

    def _build_styles(self, roles: dict[str, str]) -> None:
        """The fleet view's style table from the role colours (TT14): every colour is a role, so a theme
        switch that rebuilds this table recolours every row without moving any role."""
        base = Style(color=roles["text"], bgcolor=roles["surface"])
        accent, muted, faint = roles["accent"], roles["text_muted"], roles["text_faint"]
        self._styles: dict[str, Style] = {
            "blank": base, "text": base, "front": base + Style(color=accent), "muted": base + Style(color=muted),
            "title": base + Style(bold=True), "group": base + Style(color=muted, bold=True),
            "header": base + Style(color=muted), "hairline": base + Style(color=roles["border_on_surface"]),
            "mark": base + Style(color=accent, bold=True), "tab_on": base + Style(color=accent, bold=True),
            "tab": base + Style(color=muted), "toggle": base + Style(color=muted),
            "toggle_on": base + Style(color=accent, bold=True), "filter": base,
            "who_me": base + Style(color=accent), "who_other": base + Style(color=roles["steel"]),
            "who_none": base + Style(color=faint), "who_needs": base + Style(color=roles["warn"]),
            "who_asks": base + Style(color=roles["warn"], bold=True),
            "who_unreachable": base + Style(color=muted), "who_ended": base + Style(color=roles["danger"]),
            "warn": base + Style(color=roles["warn"]), "ok": base + Style(color=roles["ok"]),
            "danger": base + Style(color=roles["danger"]), "rec": base + Style(color=roles["rec_list"]),
            "button": base + Style(color=muted),
        }

    def apply_theme(self, config: Mapping) -> None:
        """A run-time look switch (B4): rebuild the table from the resolved roles and repaint."""
        self._build_styles(role_colours(config))
        self.refresh()

    # -- the model --

    @property
    def model(self) -> FleetViewModel:
        return self._model

    @property
    def layout(self) -> FleetLayout:
        return self._layout

    @property
    def tabs(self) -> tuple[str, ...]:
        """The tabs shown: Alerts only for a login that has them (DT14)."""
        return tuple(name for name in self._model.tabs if name in TAB_LABELS
                     and (name != TAB_ALERTS or self._model.alert_lines is not None))

    def set_model(self, model: FleetViewModel) -> None:
        self._model = model
        if self.tab not in self.tabs:
            self.tab = TAB_SESSIONS  # the Alerts tab went away (a 403): back to the sessions
        self._relayout()

    def set_window_width(self, width: int) -> None:
        if width != self.window_width:
            self.window_width = width
            self._relayout()

    def _build(self) -> FleetLayout:
        height = self.size.height or 40
        width = self.size.width or self.window_width or 80
        model = self._model
        # A model whose tabs the login cannot see never reaches the layout's tab row.
        visible = replace(model, tabs=self.tabs)
        return fleet_view_lines(
            visible, width=width, height=height, tab=self.tab, selected=self.selected,
            filter_text=self.filter_text, editing=self.editing, show_ended=self.show_ended,
            group_by=self._group_by, details=self.window_width >= self._details_from, top=self.scroll_top,
            query_text=self.query_text, marked=frozenset(self.marked))

    def _relayout(self) -> None:
        layout = self._build()
        if self.selected not in layout.rows:
            self.selected = layout.rows[0] if layout.rows else None
            layout = self._build()
        self.scroll_top = layout.top
        self._layout = layout
        self.refresh()

    def on_resize(self, event: events.Resize) -> None:
        self._relayout()

    # -- selection --

    @property
    def selected_session(self) -> str | None:
        return None if self.selected is None else self.selected[1]

    def _move(self, step: int) -> None:
        rows = self._layout.rows
        if not rows:
            return
        index = rows.index(self.selected) if self.selected in rows else -1
        self.selected = rows[max(0, min(index + step, len(rows) - 1))] if index >= 0 else rows[0]
        self._relayout()
        if step > 0:
            self._ask_more_targets()

    def _jump(self, last: bool) -> None:
        rows = self._layout.rows
        if rows:
            self.selected = rows[-1 if last else 0]
            self._relayout()
            if last:
                self._ask_more_targets()

    def _ask_more_targets(self) -> None:
        """The selection reached the last target loaded: the screen fetches the next page if the broker has one."""
        rows = self._layout.rows
        if self.tab == TAB_TARGETS and rows and self.selected == rows[-1]:
            self.post_message(self.Command(CMD_MORE_TARGETS))

    def next_tab(self, step: int = 1) -> None:
        tabs = self.tabs
        if len(tabs) > 1:
            self.set_tab(tabs[(tabs.index(self.tab) + step) % len(tabs)])

    def set_tab(self, name: str) -> None:
        if name in self.tabs and name != self.tab:
            self.tab = name
            self.editing = False
            self.selected = None
            self.scroll_top = 0
            self._relayout()
            self.post_message(self.Command(CMD_TAB_CHANGED, name))

    def toggle_show_ended(self) -> None:
        self.show_ended = not self.show_ended
        self._relayout()

    def start_filter(self) -> None:
        self.editing = True
        self._relayout()

    def reset(self) -> None:
        """Opened afresh: the sessions tab, no filter, the first row, the top."""
        self.tab = TAB_SESSIONS
        self.filter_text = ""
        self.query_text = ""
        self.applied_query = ""
        self.editing = False
        self.selected = None
        self.marked.clear()
        self.scroll_top = 0
        self._relayout()

    def toggle_mark(self) -> None:
        """Mark or unmark the selected target (Targets tab)."""
        if self.tab == TAB_TARGETS and self.selected is not None:
            self.marked ^= {self.selected[1]}
            self._relayout()

    def clear_marks(self) -> None:
        self.marked.clear()
        self._relayout()

    def picked_targets(self) -> list[str]:
        """The targets an action on the Targets tab concerns: every target marked (the ones in the rows loaded, in
        the order listed, then the marks a query hid, sorted by id - `self.marked` is a set, so the order they were
        marked is not kept), else the selected one."""
        if not self.marked:
            return [self.selected[1]] if self.tab == TAB_TARGETS and self.selected is not None else []
        listed = [entry.target_id for entry in self._model.targets]
        return [name for name in listed if name in self.marked] + sorted(self.marked.difference(listed))

    # -- text for a test and for the report --

    @property
    def text(self) -> str:
        return "\n".join(glyphs.render(line.text, self._glyph_set) for line in self._layout.lines)

    # -- painting --

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if y >= len(self._layout.lines):
            return Strip([Segment(" " * width, self._styles["blank"])])
        line = self._layout.lines[y]
        segments = [Segment(glyphs.render(part.text, self._glyph_set), self._styles.get(part.role, self._styles["text"]))
                    for part in line.parts if part.text]
        return Strip(segments).adjust_cell_length(width, self._styles["blank"])

    # -- the mouse --

    def _part_at(self, event: events.MouseEvent) -> tuple[Line | None, Part | None]:
        if event.y >= len(self._layout.lines):
            return None, None
        line = self._layout.lines[event.y]
        cursor = 0
        for part in line.parts:
            if cursor <= event.x < cursor + len(part.text):
                return line, part
            cursor += len(part.text)
        return line, None

    def on_mouse_move(self, event: events.MouseMove) -> None:
        line, part = self._part_at(event)
        tip = (part.tip if part is not None else None) or (line.tip if line is not None else None)
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
        if not mouse_on(self):
            return
        line, part = self._part_at(event)
        if line is None:
            return
        event.stop()
        if part is not None and part.command is not None:
            self._command_from_part(part)
            return
        if line.session_id is not None and line.group is not None:
            self.selected = (line.group, line.session_id)
            self._relayout()
            if line.group != GROUP_TARGETS:  # a click on a target selects it; `⏎ New session here` opens one
                self.post_message(self.Command(CMD_PICK, line.session_id))
            else:
                self._ask_more_targets()  # a click on the last target loaded asks for the next page, as `↓` does

    def _command_from_part(self, part: Part) -> None:
        if part.command == CMD_FILTER:
            self.start_filter()
        elif part.command == CMD_TAB and part.arg is not None:
            self.set_tab(part.arg)
        elif part.command == CMD_ENDED:
            self.toggle_show_ended()
        elif part.command == CMD_MODE:
            self.post_message(self.Command(CMD_MODE, part.arg))
        elif part.command and part.command.startswith("detail:"):
            name = {"t Take back": CMD_TAKE_BACK, "w Who's here": CMD_WHOS_HERE, "⏎ Open": CMD_PICK,
                    "x End…": CMD_END}.get(part.command.removeprefix("detail:"))
            if name is not None:
                self.post_message(self.Command(name, part.arg))
        else:
            self.post_message(self.Command(part.command, part.arg))

    # -- keys --

    def on_key(self, event: events.Key) -> None:
        """Every key is the view's while it has the keys (D14), except the leader and the key after it, which belong
        to the screen (`^\\ f` still works). `Ctrl-Q` leaves the TUI, the sessions running (D38)."""
        screen = self.screen
        if event.key == self._leader or getattr(screen, "leader_pending", False):
            return
        event.stop()
        key = event.key
        if key == "ctrl+q":
            self.app.run_worker(self.app.action_quit(), exclusive=False)
            return
        if self.editing:
            self._editing_key(event)
            return
        sid = self.selected_session if self.tab == TAB_SESSIONS else None
        if key in ("down", "j"):
            self._move(1)
        elif key in ("up", "k"):
            self._move(-1)
        elif key == "pagedown":
            self._move(max(self.size.height - 4, 1))
        elif key == "pageup":
            self._move(-max(self.size.height - 4, 1))
        elif key == "home":
            self._jump(False)
        elif key == "end":
            self._jump(True)
        elif key == "tab":
            self.next_tab(1)
        elif key == "shift+tab":
            self.next_tab(-1)
        elif key == "escape":
            self.post_message(self.Command(CMD_CLOSE))
        elif key == "slash" and self.tab in (TAB_SESSIONS, TAB_TARGETS):
            self.start_filter()
        elif key == "m" and self.tab == TAB_SESSIONS:
            self.post_message(self.Command(CMD_MODE))
        elif key == "e" and self.tab == TAB_SESSIONS:
            self.toggle_show_ended()
        elif key in ("a", "e", "d") and self.tab == TAB_TARGETS and self._model.target_edit:
            command = {"a": CMD_TARGET_NEW, "e": CMD_TARGET_EDIT, "d": CMD_TARGET_DELETE}[key]
            self.post_message(self.Command(command, self.selected_session))
        elif key == "space" and self.tab == TAB_TARGETS:
            self.toggle_mark()
        elif key == "l" and self.tab == TAB_TARGETS:
            self.post_message(self.Command(CMD_SAVE_LOGIN))
        elif key == "p" and self.tab == TAB_TARGETS:
            self.post_message(self.Command(CMD_MAP))
        elif key == "enter" and self.tab == TAB_TARGETS and self.selected_session is not None:
            self.post_message(self.Command(CMD_OPEN_TARGET, self.selected_session))
        elif key == "enter" and sid is not None:
            self.post_message(self.Command(CMD_PICK, sid))
        elif key == "t" and sid is not None:
            self.post_message(self.Command(CMD_TAKE_BACK, sid))
        elif key == "w" and sid is not None:
            self.post_message(self.Command(CMD_WHOS_HERE, sid))
        elif key == "x" and sid is not None:
            self.post_message(self.Command(CMD_END, sid))
        elif key in ("n", "c"):
            self.post_message(self.Command(CMD_NEW))

    def _editing_key(self, event: events.Key) -> None:
        if self.tab == TAB_TARGETS:
            self._editing_query_key(event)
            return
        key = event.key
        if key == "escape":
            self.filter_text = ""
            self.editing = False
        elif key == "enter":
            self.editing = False
        elif key == "backspace":
            self.filter_text = self.filter_text[:-1]
        elif event.is_printable and event.character:
            self.filter_text += event.character
        self._relayout()

    def _editing_query_key(self, event: events.Key) -> None:
        """The query is the broker's: typing changes nothing on screen but the line, `⏎` asks, and `Esc` drops what was
        typed since the last query asked."""
        key = event.key
        if key == "escape":
            self.query_text = self.applied_query
            self.editing = False
        elif key == "enter":
            self.editing = False
            self.applied_query = self.query_text
            self.post_message(self.Command(CMD_QUERY, self.query_text))
        elif key == "backspace":
            self.query_text = self.query_text[:-1]
        elif event.is_printable and event.character:
            self.query_text += event.character
        self._relayout()


__all__ = ["CMD_ENDED", "CMD_MAP", "CMD_MARK", "CMD_MORE_TARGETS", "CMD_OPEN_TARGET", "CMD_QUERY", "CMD_SAVE_LOGIN", "CMD_TAB",
           "CMD_TAB_CHANGED",
           "SEPARATOR", "FleetLayout", "FleetRow", "FleetView", "FleetViewModel", "TargetEntry", "fleet_view_lines",
           "visible_rows"]
