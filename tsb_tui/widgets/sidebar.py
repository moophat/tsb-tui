"""The side panel beside the terminal: Targets above a scrollable Sessions frame, never an overlay.

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

import bisect
import itertools
import textwrap
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from .. import glyphs
from ..model.actions import DETACH_KEY
from ..model.fleet import (
    ENDED_STATES,
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
from ..model.targets import TargetRow, natural_key
from ..theme import contrast_ratio, relative_luminance, role_colours, sessions_surface
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
CMD_FILTER = "filter"  # `/` or a click on the filter: start typing into the Targets filter
CMD_END = "end"  # End session… on a row
CMD_FOLD = "fold"  # `z`: fold the other list
CMD_BACK = "back"  # Esc in a list: the keys return to the session open here, if there is one
CMD_OPEN_TARGET = "open_target"  # Enter on a target: New session on it (the full view's own command, same name)
CMD_TARGET_NEW = "target_new"  # `a` on an empty Targets list: Add target (a `target.edit` holder's)
CMD_RETRY = "retry"  # `r` on a loading error: ask the core again (`arg` names what: targets or sessions)
CMD_SHOW_ENDED = "show_ended"  # `e` in the Sessions list: show or hide the ended sessions
CMD_KEYS = "keys"  # `?` on a list: All keys, without the leader (rule 5, 2026-11-21)
CMD_DETAILS = "details"  # `i` on a target row: the details view of that record (rule 10, 2026-11-21)
PANE_TARGETS = "targets"
PANE_SESSIONS = "sessions"
GROUP_ENDED = "ended"  # the ended rows shown behind `e` (ruled 2026-11-20)

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
    state: str = ""  # the state word: `live`, `detached`, `unreachable`, `killed`, `terminated` (ruled 2026-11-20)
    owner: str = ""  # `yours`, or the owner's login (ruled 2026-11-20)
    created_at: str = ""  # the record's `created_at`: the `since HH:MM` two alike rows say (ruled 2026-11-20)


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
    targets: tuple[TargetRow, ...] = ()
    target_total: int | None = None
    targets_loading: bool = False
    # The target listing has a next page: the title shows the loaded count followed by `+`, never a false total.
    target_has_next: bool = False
    # The ruled rows' context (2026-11-20): the others word per target, the empty/error states of the two lists,
    # and the ended records behind `e`.
    others: Mapping[str, str] = field(default_factory=dict)  # target id -> `ana on it` / `2 others on it`
    target_edit: bool = False  # the login holds `target.edit`: the empty Targets list offers `a add one`
    targets_unavailable: bool = False  # the inventory answered 503: `Could not load targets · r retry`
    targets_more_failed: bool = False  # a later page failed (not 401/503): rows stay, count shows no `…`, last line `Could not load more · r retry`
    sessions_loading: bool = False  # the first poll has not landed: `Loading sessions…`
    sessions_error: bool = False  # the poll failed with nothing yet read: `Could not load sessions · r retry`
    ended: tuple[Mapping[str, Any], ...] = ()  # the visible ended records, for `e show ended`


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
    # How many lines each painted session row takes, top to bottom (the framed view): a multi-line row paints
    # whole or not at all, so the list's scroll positions are row starts (ruled 2026-11-20 20:1x).
    row_lens: tuple[int, ...] = ()


def _fit(text: str, width: int) -> str:
    """`text` cut to `width` cells with an ellipsis, or padded with blanks to it."""
    if len(text) > width:
        return text[: max(width - 1, 0)] + "…" if width > 0 else ""
    return text.ljust(width)


def _cut_name(text: str, width: int) -> str:
    """A row's label cut to `width` cells with an ellipsis at a word boundary, never inside another word; a
    label that is one word cuts at the edge."""
    if len(text) <= width:
        return text
    if width <= 0:
        return ""
    head = text[: width - 1]
    space = head.rfind(" ")
    if space > 0:
        return text[:space] + "…"
    return head + "…"


def _empty_copy(text: str, width: int, role: str, command: str | None = None,
                arg: str | None = None) -> list[Line]:
    """A ruled empty-state copy as one or more lines: it wraps at its ` · ` segments so the exact words show
    whole at the narrow budget, and the advertised key keeps its click."""
    segments = text.split(" · ")
    lines: list[str] = []
    current = ""
    for segment in segments:
        candidate = segment if not current else f"{current} · {segment}"
        if len(candidate) <= width or not current:
            current = candidate
            continue
        lines.append(current + " ·")
        current = segment
    lines.append(current)
    return [Line((Part(_fit(line, width), role, command, arg, tip=text),)) for line in lines]


def _target_label(row: TargetRow) -> str:
    """A target row's label (ruled 2026-11-20): its name, then its host, then the type word and the target's id.
    Every string from a broker record goes through `as_text` first, so no control character reaches the row."""
    if row.has_name:
        return row.name
    if row.host:
        return row.host
    return f"{row.type} {row.label}".strip()


def _target_facts_budget(wide: bool, inner: int) -> int:
    """The facts budget (ruled 2026-11-21): at the wide budget the facts are composed within `inner - 3` cells
    (the mark, at least one label cell and the space); the narrow budget stays `inner - 1`. The span, the line
    count and the painted line all use this one budget."""
    return inner - 3 if wide else inner - 1


def _target_facts(row: TargetRow, numbers: Iterable[int], others: str, group: str = "",
                  budget: int | None = None) -> tuple[str, str]:
    """A target row's facts (ruled 2026-11-20, group added 2026-11-21): the host, the type word when the
    target is not ssh, the numbers of its sessions open in this window, the group, and the other logins'
    word, joined by ` · `. Returns the full facts (for the tooltip) and the facts composed within `budget`
    (the painted line). With a budget it is built one whole segment at a time, in that order: a segment
    that would overflow is left out whole, never cut (rule 1: no word is cut). The order puts the group
    after the open numbers and before the others word, so the greedy fit leaves `others on it` out first,
    then the group. The row pairs, the painted end and the painted line read this once per refresh from
    the spans cache."""
    segments: list[str] = []
    if row.host:
        segments.append(row.host)
    if numbers:
        segments.append("open " + ",".join(str(number) for number in sorted(numbers)))
    if group:
        segments.append(group)
    if others:
        segments.append(others)
    # ruling 17 (2026-11-22): the second line is `host · open N,M`, then the group, then the others word, each
    # whole; the type word rides last, after them, so it drops before the ruled segments overflow.
    if row.type and row.type != "ssh":
        segments.append(row.type)
    full = " · ".join(segments)
    facts = ""
    for segment in segments:
        candidate = segment if not facts else f"{facts} · {segment}"
        if budget is None or len(candidate) <= budget:
            facts = candidate
    return full, facts


def _title(group: str, count: int, width: int) -> str:
    titles = GROUP_TITLES.get(group, (group,))
    for title in titles:
        text = f"{title} · {count}"
        if len(text) <= width:
            return text
    return f"{titles[-1]} · {count}"[:width]


def title_parts(name: str, index: str, first: str, of_part: str, loading: bool, query: str,
                width: int) -> str:
    """The section title of rule 3 (2026-11-21): `▦ Targets · 14/230`, the filter form `3/12 of 230 · 'rtr'`,
    the loading form `14/500… loading`. The section mark (P2a's ruled marks, `▦`/`▸`) leads the title; the
    drop order is rule 16's: the quoted text first, then `of N`, then `loading`; the index pair always shows.
    `width` is the name-only budget: the mark and its space are taken out of it by the callers."""
    # `loading` is part of the count form: `14/500… loading`, so the ellipsis rides with the pair and the word
    # drops as one piece (rule 16).
    core = f"{name} · {index}/{first}"
    parts = [core + "…" if loading else core]
    if loading:
        parts.append("loading")
    if of_part:
        parts.append(of_part)
    if query:
        parts.append(f"· '{query}'")
    for drop in range(3):  # all, then the quoted text, then `of N`; the count pair stays
        fitted = " ".join(parts[: len(parts) - drop])
        if len(fitted) < width:  # the title box is two columns inside the frame (rule 16)
            return fitted
    core = parts[0]
    if loading:
        head, sep, counts = core.partition("· ")
        index, _slash, first = counts.partition("/")
        fitted = f"{head}{sep}{index}/{first}… loading"  # the count so far is never a false total (rule 3)
        return fitted if len(fitted) < width else parts[0]
    return core


def marked_title(mark: str, title: str, width: int) -> str:
    """The ruled mark (P2a: `▦ Targets`, `▸ Sessions`) leads the rule-3 title; the mark is never dropped."""
    fitted = f"{mark} {title}"
    return fitted if len(fitted) < width else title


def target_title(index: str, matches: int, total: int, loaded: int, query: str, loading: bool,
                 width: int) -> str:
    """The Targets section title. `matches` counts the filter's rows (the whole list when none), `total` the
    targets loaded when the pages are all in, `loaded` the rows so far while pages arrive."""
    first = str(matches)
    of_part = ""
    if query:
        of_part = f"of {loaded}…" if loading else f"of {total}"
    return title_parts("Targets", index, first, of_part, loading, query, width)


def session_title(index: str, matches: int, total: int, query: str, width: int, loading: bool = False) -> str:
    """The Sessions section title of rule 3: `Sessions · 2/5`, with a filter `1/2 of 5 · 'text'` and the
    loading form `–/… loading` before the first poll lands."""
    first = "…" if loading else str(matches)
    of_part = f"of {total}" if query else ""
    return title_parts("Sessions", index, first, of_part, loading, query, width)


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


def _right_fields(entry: RowText, group: str, wide: bool, room: int, *, rec: bool = True) -> list[tuple[str, str]]:
    """What fits in `room` cells at the row's right edge, best first: the clock, then REC. A narrow row holds
    one of them (mockup S1); a wide row holds the clock and REC (S3). `recorded` left the ruled rows (2026-11-20),
    so they pass `rec=False`."""
    clock = _time_field(entry, group, wide)
    fields: list[tuple[str, str]] = []
    if clock is not None and len(clock[0]) <= room:
        fields.append(clock)
    if rec and entry.rec and room >= 3 and (not fields or (wide and len(fields[0][0]) + 3 + 3 <= room)):
        fields.append(("REC", "rec"))
    return fields


def _tail_need(entry: RowText, group: str, wide: bool, *, rec: bool = True) -> int:
    """The cells a row's right-hand fields (the clock, REC) take, as `_right_fields` lays them out when they fit."""
    clock = _time_field(entry, group, wide)
    size = len(clock[0]) if clock is not None else 0
    if rec and entry.rec:
        size = size + 6 if clock is not None and wide else (size or 3)
    return size


def _typer_words(login: str) -> str:
    """The row's typer words (ruled 2026-11-20): `you type`, `<name> types`, or `nobody types`."""
    if login == "you":
        return "you type"
    if not login or login == "·":
        return "nobody types"
    return f"{login} types"


def _since_word(created_at: str) -> str:
    """`since HH:MM`, from a record's `created_at` in local time (ruled 2026-11-20); empty when it cannot be read."""
    try:
        moment = datetime.fromisoformat(created_at)
    except ValueError:
        return ""
    return f"since {moment.astimezone().strftime('%H:%M')}"


def _wide_segments(entry: SidebarEntry) -> list[tuple[str, str]]:
    """The words one wide session row shows after the name: the state beside its mark, ` · owner`, then the
    typer words — an ended row's words end at its end word (rule 6). `_row_lines` lays them; the frame's
    height reads the same words to know whether an alike row's one line can hold its alike word too."""
    state = f"{entry.who} {entry.state}".strip() if entry.state else entry.who
    ended = entry.state in ENDED_STATES
    owner = "" if ended else (entry.owner or ("·" if entry.login == "·" else ""))
    typer = "" if ended else _typer_words(entry.login)
    segments = [(state, "text")] + ([(owner, "muted")] if owner else [])
    if typer:
        segments.append((typer, "text" if entry.login not in ("·", "") else "muted"))
    return segments


def _wide_alike_fits(entry: SidebarEntry, width: int, alike: str) -> bool:
    """Whether an alike row's one wide line can hold the ruled words plus its alike word even with the name
    at one ellipsis cell (ruled 2026-11-20 23:4x). When it cannot, the row takes a second line."""
    segments = _wide_segments(entry)
    base = sum(len(text) + (1 if index in (0, 2) else 3)
               for index, (text, _role) in enumerate(segments) if text)
    return width - 3 - 1 - base - (3 if len(segments) == 1 else 1) - len(alike) >= 0


def _row_lines(entry: SidebarEntry, group: str, *, width: int, selected: bool, wide: bool,
               since: str = "") -> list[Line]:
    """The ruled session row (2026-11-20): at the narrow budget up to three lines — `N name`; the state word
    beside its mark and the owner; the typer words — and one line at the wide one. No clock on a session row
    (ruled 2026-11-20 20:1x): the words end at the typer words, or at an ended row's end word with nothing
    invented after it (rule 6). When two visible rows would read alike — same target, state, owner and typer;
    the row's number does not make two rows different (ruled 2026-11-20 20:1x) — each says `since HH:MM`:
    at the narrow budget on the typer line after the typer words, joined by ` · ` (an ended row, which has no
    typer line, beside its name); at the wide budget at the end of the one line. When the one line cannot
    hold the ruled words plus the alike word even with the name at its ellipsis, the row takes a second line
    with the alike word on it (ruled 2026-11-20 23:4x); a ruled word is never cut. The row's tooltip names
    the whole row (`entry.tip`, D13)."""
    who_role = _WHO_ROLES.get(entry.who, "who_none")
    mark = (SELECTED, "mark") if selected else (" ", "blank")
    number = str(entry.number) if entry.number is not None else " "
    ended = entry.state in ENDED_STATES
    owner = "" if ended else (entry.owner or ("·" if entry.login == "·" else ""))
    typer = "" if ended else _typer_words(entry.login)
    typer_role = "text" if entry.login not in ("·", "") else "muted"
    tip = f"{entry.tip} · {since}" if entry.tip and since else (entry.tip or since or None)
    if not wide:
        # The alike word stands after the typer words when the pair fits whole; when it does not (and on an
        # ended row, which has no typer line) it stands beside the name, which takes the ellipsis first.
        on_typer = bool(since) and not ended and 2 + len(typer) + 3 + len(since) <= width
        on_name = bool(since) and not on_typer
        name_room = width - 4 - ((len(since) + 3) if on_name else 0)
        parts = [Part(mark[0], mark[1]), Part(number, "num_front" if entry.front else "num"),
                 Part(" ", "blank"),
                 Part(_cut_name(entry.target, max(name_room, 1)), "front" if entry.front else "text")]
        if on_name:
            parts += [Part(" · ", "muted"), Part(since, "muted")]
        used = sum(len(part.text) for part in parts)
        lines = [Line((*parts, *_row_tail(used, [], width)), entry.session_id, group, tip,
                      len(parts))]
        second = [Part("  ", "blank"), Part(entry.who, who_role), Part(" ", "blank"), Part(entry.state, "text")]
        if owner:
            second += [Part(" · ", "muted"), Part(owner, "muted")]
        used = sum(len(part.text) for part in second)
        second.append(Part(" " * max(width - used, 0), "blank"))
        lines.append(Line(tuple(second), entry.session_id, group, tip, len(second) - 1))
        if typer:
            third = [Part("  ", "blank"), Part(typer, typer_role)]
            if on_typer:
                third += [Part(" · ", "muted"), Part(since, "muted")]
            used = sum(len(part.text) for part in third)
            third.append(Part(" " * max(width - used, 0), "blank"))
            lines.append(Line(tuple(third), entry.session_id, group, tip, len(third) - 1))
        return lines
    # One line at the wide budget: `N name`, the state beside its mark, ` · owner`, then the typer words and
    # the alike word at the line's end, each after one space; the name yields with an ellipsis first, and a
    # ruled word is never cut — when the alike word even the name at one ellipsis cell leaves no room for
    # does not fit, the row takes a second line with the alike word on it (ruled 2026-11-20 23:4x), never
    # left off and never cut.
    segments = _wide_segments(entry)
    alike = since
    over: list[Part] = []
    if alike and not _wide_alike_fits(entry, width, alike):
        alike = ""
        over = [Part("  ", "blank"), Part(since, "muted"),
                Part(" " * max(width - 2 - len(since), 0), "blank")]
    if alike:
        segments.append((alike, "muted"))
    room = width - 3 - sum(len(text) + (1 if index in (0, 2) else 3)
                           for index, (text, _role) in enumerate(segments) if text)
    parts = [Part(mark[0], mark[1]), Part(number, "num_front" if entry.front else "num"), Part(" ", "blank"),
             Part(_cut_name(entry.target, max(room, 1)), "front" if entry.front else "text")]
    for index, (text, role) in enumerate(segments):
        if not text:
            continue
        parts.append(Part(" · " if index == 1 else " ", "muted" if index != 0 else "blank"))
        parts.append(Part(text, role))
    used = sum(len(part.text) for part in parts)
    lines = [Line((*parts, *_row_tail(used, [], width)), entry.session_id, group, tip, len(parts))]
    if over:
        lines.append(Line(tuple(over), entry.session_id, group, tip, len(over) - 1))
    return lines


def _alike_since(rows: Sequence[tuple[str, SidebarEntry]]) -> dict[str, str]:
    """The `since HH:MM` word of every row in a group of alike rows (ruled 2026-11-20, re-read 20:1x): two
    visible session rows that would render the same words — same target, state, owner and typer; the row's
    number does not make two rows different — each add it, from `created_at` in local time."""
    counts: dict[tuple, int] = {}
    for _group, entry in rows:
        key = _alike_key(entry)
        counts[key] = counts.get(key, 0) + 1
    out: dict[str, str] = {}
    for _group, entry in rows:
        if counts[_alike_key(entry)] > 1 and entry.created_at:
            out[entry.session_id] = _since_word(entry.created_at)
    return out


def _alike_key(entry: SidebarEntry) -> tuple:
    """What two rows' rendered words share: the target, the state and — on a row that is not ended — the owner
    and the typer (an ended row's words end at its end word, rule 6)."""
    ended = entry.state in ENDED_STATES
    return (entry.target, entry.state, "" if ended else entry.owner, "" if ended else entry.login)


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
                 hint_full_view: bool = True, prepared: Prepared | None = None,
                 framed: bool = False, show_ended: bool = False) -> SidebarView:
    """The sidebar's lines at `width` cells (the hairline is the widget's) and `height` rows.

    `framed` is the side panel's Sessions section: the sessions open in this window, one ruled row each (`N
    name`; the state word with its mark and the owner; the typer words; one line at the wide budget), and —
    only behind `e show ended` — the ended rows (ruled 2026-11-20). No heading, header, footer or details (the
    other groups and the mine│all switch are the full view's, the filter and its count the Targets section's).
    Unframed is the full list the unit tests of the row grammar read."""
    if framed:
        prep = prepared if prepared is not None else _prepare(model, "")
        shown: list[tuple[str, str]] = []
        entries: list[tuple[str, SidebarEntry]] = []
        for name, items in prep.groups:
            if name != GROUP_OPEN_HERE:
                continue
            for item in items:
                entry = item if isinstance(item, SidebarEntry) else _entry_for(item, name, model)
                entries.append((name, entry))
        if show_ended:
            for record in model.ended:
                entries.append((GROUP_ENDED, _entry_for(record, GROUP_ENDED, model)))
        alike = _alike_since(entries)
        lines: list[Line] = []
        row_lens: list[int] = []
        for name, entry in entries:
            key = (name, entry.session_id)
            shown.append(key)
            row = _row_lines(entry, name, width=width, selected=selected == key, wide=wide,
                             since=alike.get(entry.session_id, ""))
            lines.extend(row)
            row_lens.append(len(row))
        return SidebarView(tuple(lines), tuple(shown), 0, None, 0, tuple(row_lens))
    header: list[Line] = []
    close_tip = f"◂ close the sidebar · Esc, {model.leader_hint}"
    head_parts = [Part("▦ Targets", "title"), Part("  ", "blank"),
                  Part("◂ close", "close", CMD_CLOSE, tip=close_tip),
                  Part(f" {model.leader_hint}", "muted", CMD_CLOSE, tip=close_tip)]
    head = sum(len(p.text) for p in head_parts)
    if wide and hint_full_view and width >= head + 2 + len(FULL_VIEW):
        head_parts += [Part(" " * (width - head - len(FULL_VIEW) - 1), "blank"),
                       Part(FULL_VIEW, "muted", CMD_FULL_VIEW, tip="▦ Full view · F"), Part(" ", "blank")]
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
                                                            tip="▦ Full view · every session"))))
    lines = [*header, *body, *details, *footer]
    detail_start = len(header) + len(body) if details else None
    # The sidebar is exactly as tall as the window it sits in: blank rows fill the gap, between the groups
    # and the footer, so the footer stays at the bottom.
    gap = height - len(lines)
    if gap > 0:
        lines = [*header, *body, *details, *(_blank(width) for _ in range(gap)), *footer]
    return SidebarView(tuple(lines), tuple(shown_rows), hidden, detail_start, len(details))


def _variant(config: Mapping) -> str:
    """The theme's direction, `dark` or `light`: the theme's own variant, else read from its background."""
    chrome = getattr(config, "chrome", None)
    variant = getattr(chrome, "variant", None)
    return variant or ("light" if relative_luminance(config["theme.bg"]) > 0.179 else "dark")


def _style_table(roles: dict[str, str], surface: str) -> tuple[dict[str, Style], Style]:
    """The sidebar's styles on one section's `surface`, and the frame's style on it."""
    base = Style(color=roles["text"], bgcolor=surface)
    accent, muted, faint = roles["accent"], roles["text_muted"], roles["text_faint"]
    styles: dict[str, Style] = {
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
    return styles, base + Style(color=roles["border_on_surface"])


# Every foreground role the side panel draws (the style table above): a theme whose Sessions tint cannot
# carry one of them at 4.5:1 falls back to the Targets surface (no second step), text contrast over the tint.
_PANEL_ROLES = ("text", "text_muted", "text_faint", "accent", "steel", "warn", "danger", "rec_list",
                "border_on_surface")


class Sidebar(Widget):
    """The framed side panel. It keeps its selection and its filter text; everything else comes from the
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
        self._build_styles(role_colours(config), _variant(config))
        self._glyph_set = glyphs.check(config["glyphs.set"])
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
        self._scroll_top = 0
        self._target_scroll_top = 0
        # The list the keys are on (the Targets list or the Sessions list), the selected target (its id), and the list
        # folded to its one-line title, if any; none of the three is saved.
        self.pane = PANE_SESSIONS
        self.target_selected: str | None = None
        self.folded: str | None = None
        self.show_ended = False  # `e` in the Sessions list: the ended rows behind it (ruled 2026-11-20)
        self._front_id: str | None = None
        # The filter and the column widths for the current filter text and model, reused by every relayout that
        # changes neither (`_prepare`).
        self._prepared: Prepared | None = None
        self._prepared_for: tuple[str, SidebarModel] | None = None
        # The filtered target rows for the current targets and filter text, reused by every paint that changes
        # neither: `(key, rows)`.
        self._targets_cache: tuple[tuple[object, str, tuple], tuple[TargetRow, ...]] | None = None
        # The Recent rows for the current model and filter text: `(key, rows)` (rule 9).
        self._recent_cache: tuple[object, tuple[TargetRow, ...]] | None = None
        self._painted_cache: tuple[object, tuple[TargetRow, ...]] | None = None
        self._filter_cache: tuple[object, tuple[TargetRow, ...]] | None = None
        # The row starts of the current row pairs, for the scroll's bisect (rule 11).
        # The selected rows' places, for rule 6 (2026-11-21): when a row disappears across a refresh, a page
        # arrival or a filter edit, the next surviving row below its place takes the highlight, else the last.
        self._target_pos = 0
        self._session_pos = 0
        # Each target's painted start index, cached beside the rows it was walked for.
        self._target_spans_cache: tuple[tuple[TargetRow, ...], tuple, object, tuple, tuple[int, ...],
                                         tuple[tuple[str, str], ...]] | None = None
        # The open sessions whose wide row paints its alike word on a second line, cached beside the model
        # and width the frame's height read them at (`_wide_alike_lines`).
        self._wide_alike_for: tuple[SidebarModel, int] | None = None
        self._wide_alike_cache: dict[str, int] = {}

    def _build_styles(self, roles: dict[str, str], variant: str) -> None:
        """The sidebar's two style tables from the role colours (TT14): every colour is a role, so a theme
        switch that rebuilds them recolours every row without moving any role. Targets keeps the surface; Sessions
        sits on the next step of the same walk (`sessions_surface`), unless a foreground role the panel draws
        would fall below 4.5:1 on that tint: then Sessions keeps the Targets surface too (no second step), text
        contrast winning over the tint."""
        self._styles, self._border = _style_table(roles, roles["surface"])
        tint = sessions_surface(roles["surface"], variant)
        if any(contrast_ratio(roles[role], tint) < 4.5 for role in _PANEL_ROLES):
            tint = roles["surface"]
        self._styles_sessions, self._border_sessions = _style_table(roles, tint)
        # The selected row's bar: the soft accent, bold, while its list has the keys; a dim bar in the second surface
        # (still with its ▸) while the keys are elsewhere.
        self._bar = Style(bgcolor=roles["accent_soft"], bold=True)
        self._bar_dim = Style(bgcolor=roles["surface_3"])

    def apply_theme(self, config: Mapping) -> None:
        """A run-time look switch (B4): rebuild the table from the resolved roles and repaint."""
        self._build_styles(role_colours(config), _variant(config))
        self.refresh()

    # -- the model --

    @property
    def model(self) -> SidebarModel:
        return self._model

    @property
    def view(self) -> SidebarView:
        return self._view

    @property
    def scroll_top(self) -> int:
        return self._scroll_top

    @property
    def wide(self) -> bool:
        return self.window_width >= self._wide_from

    @property
    def details_shown(self) -> bool:
        return self.window_width >= self._details_from

    def set_model(self, model: SidebarModel, *, front_id: str | None = None) -> None:
        self._model = model
        self._front_id = front_id
        self._target_scroll_top = min(self._target_scroll_top, self._target_scroll_limit())
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
        needle = ""  # the filter is the Targets section's; the sessions open here are all shown
        if self._prepared_for is None or self._prepared_for[1] is not self._model or self._prepared_for[0] != needle:
            self._prepared = _prepare(self._model, needle)
            self._prepared_for = (needle, self._model)
        height = self.size.height or 40
        return sidebar_view(
            self._model, width=max(self.sidebar_width - 4, 1), height=height, selected=self.selected,
            filter_text=self.filter_text, editing=self.editing, wide=self.wide,
            show_details=self.details_shown, prepared=self._prepared, framed=True, show_ended=self.show_ended)

    def _relayout(self) -> None:
        view = self._build()
        rows = list(view.rows)
        if self.selected is None:
            self.selected = self._default_selection(rows)
        elif self.selected not in rows:
            # Rule 6 (2026-11-21): the row that carried the highlight disappeared (a refresh, a page arrival, a
            # filter edit); the next surviving row below its place takes it, else the last row.
            following = rows[self._session_pos:]
            self.selected = following[0] if following else (rows[-1] if rows else None)
        self._session_pos = rows.index(self.selected) if self.selected in rows else min(self._session_pos, len(rows))
        if view.rows != tuple(rows):
            view = self._build()
        # The Targets list follows rule 6 (2026-11-21) over its painted rows (Recent included): the highlight
        # keeps its place when the entity is still shown; when the row that carried it disappears, the next
        # surviving row below takes it, else the last. A list that never had a selection stays without one
        # (e0c1031's rule: `–` in the title), and the scroll clamps follow the visible list.
        rows = self.painted_targets()
        ids = [row.id for row in rows]
        if self.target_selected is not None and self.target_selected in ids:
            self._target_pos = ids.index(self.target_selected)
        elif self.target_selected is not None:
            following = ids[self._target_pos:]
            self.target_selected = following[0] if following else (ids[-1] if ids else None)
            self._target_pos = ids.index(self.target_selected) if self.target_selected in ids else 0
        self._view = view
        # The Targets scroll top follows the visible list: a filter, a width change or fewer targets than it had
        # scrolled to must not leave it past the end. Clamp on every relayout.
        self._target_scroll_top = min(self._target_scroll_top, self._target_scroll_limit())
        self._scroll_to_selected()
        self.refresh()

    def _selected_line_index(self) -> int | None:
        if self.selected is not None:
            group, sid = self.selected
            for idx, line in enumerate(self._view.lines):
                if line.group == group and line.session_id == sid:
                    return idx
        return None

    def _scroll_to_selected(self) -> None:
        """Scroll the Sessions list by whole rows (ruled 2026-11-20 20:1x): the scroll position is a row's first
        line, and a selected row the window cannot show whole scrolls to the least that shows it whole."""
        height = max(self.sessions_height - 2, 1)
        starts = self._row_starts()
        lens = self._view.row_lens
        if not starts:
            self._scroll_top = 0
            return
        line_idx = self._selected_line_index()
        row = 0
        if line_idx is not None:
            for index, start in enumerate(starts):
                if start <= line_idx:
                    row = index
                else:
                    break
        top_row = self._scroll_row(starts)
        if starts[top_row] != self._scroll_top:
            self._scroll_top = starts[top_row]
        if starts[row] < self._scroll_top:
            self._scroll_top = starts[row]
        elif sum(lens[top_row:row + 1]) > height:
            for s in range(top_row + 1, row + 1):
                if sum(lens[s:row + 1]) <= height:
                    self._scroll_top = starts[s]
                    break
        self._scroll_top = max(0, min(self._scroll_top, self._session_max_scroll()))

    def _row_starts(self) -> tuple[int, ...]:
        """Where each painted session row's first line sits in the view's lines."""
        starts: list[int] = []
        at = 0
        for count in self._view.row_lens:
            starts.append(at)
            at += count
        return tuple(starts)

    def _scroll_row(self, starts: Sequence[int]) -> int:
        row = 0
        for index, start in enumerate(starts):
            if start <= self._scroll_top:
                row = index
            else:
                break
        return row

    def _session_painted_end(self) -> int:
        """One past the last line the Sessions list paints: a multi-line row paints whole or not at all, so the
        walk stops at the first row that does not fit under the section's inner height (ruled 2026-11-20 20:1x)."""
        height = max(self.sessions_height - 2, 1)
        starts = self._row_starts()
        if not starts:
            return self._scroll_top
        end = self._scroll_top
        for count in self._view.row_lens[self._scroll_row(starts):]:
            if end + count > self._scroll_top + height:
                break
            end += count
        return end

    def _session_max_scroll(self) -> int:
        """The lowest whole-row scroll position: a row start whose row fits the inner height."""
        height = max(self.sessions_height - 2, 1)
        fits = [start for start, count in zip(self._row_starts(), self._view.row_lens) if count <= height]
        return max(fits) if fits else 0

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
        width = max(self.sidebar_width - 4, 1)
        wide = self.wide
        lines = list(self._view.lines)
        changed = False
        needs_rebuild = False
        seen: set[tuple[str, str]] = set()
        for index, line in enumerate(lines):
            key = (line.group, line.session_id)
            if key not in times or key in seen:
                continue
            seen.add(key)  # only the row's first line carries the clock; the row's other lines have none
            if not any(part.text.strip() for part in line.parts[line.prefix_len:]):
                continue  # the row shows no right-hand fields (a ruled row carries no clock, 2026-11-20)
            prefix = line.parts[: line.prefix_len]
            used = sum(len(part.text) for part in prefix)
            fields = _right_fields(times[key], line.group, wide, max(width - used - 1, 0), rec=False)
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
                                     self._view.detail_start, self._view.detail_len, self._view.row_lens)
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
        self._view = SidebarView(tuple(lines), view.rows, view.hidden, start, len(new), view.row_lens)
        self.refresh()
        return False

    def toggle_show_ended(self) -> None:
        """`e` in the Sessions list: the ended rows appear with their end words, and disappear again (ruled 2026-11-20)."""
        self.show_ended = not self.show_ended
        self._relayout()
        self.refresh()

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
        return None if self.selected is None else self.selected[1]

    def select_group(self, group: str) -> bool:
        """Move the selection to the first row of `group` (the `▲n` of the rail opens it at Needs you)."""
        for row in self._view.rows:
            if row[0] == group:
                self.selected = row
                self._relayout()
                return True
        return False

    def _jump(self, last: bool) -> None:
        """`Home`/`End` on the Sessions list: the first, the last row."""
        rows = self._view.rows
        if not rows:
            return
        self.selected = rows[-1] if last else rows[0]
        self._session_pos = rows.index(self.selected)
        self._relayout()

    def _move(self, step: int) -> None:
        rows = self._view.rows
        if not rows:
            return
        index = rows.index(self.selected) if self.selected in rows else -1
        self.selected = rows[(index + step) % len(rows)] if index >= 0 else rows[0 if step > 0 else -1]
        self._session_pos = rows.index(self.selected) if self.selected in rows else 0
        self._relayout()

    @property
    def sessions_height(self) -> int:
        """The open rows size the Sessions frame; the grouped content scrolls inside it. A ruled row takes the
        lines it paints (up to three at the narrow budget, one at the wide one), the ended rows behind `e`
        included (ruled 2026-11-20)."""
        height = self.size.height or 40
        if self.folded == PANE_SESSIONS:
            return 1
        if self.folded == PANE_TARGETS:
            return max(height - 1, 1)
        lines = sum(self._row_line_count(item) for group, items in self._model.groups
                    if group == GROUP_OPEN_HERE for item in items)
        if not lines and not (self.show_ended and self._model.ended):
            lines = len(self._empty_lines(max(self.sidebar_width - 4, 1)))  # the empty copy wraps at the narrow one
        if self._model.targets_loading:
            lines = max(lines, len(self._loading_lines(max(self.sidebar_width - 4, 1))))
        if self.show_ended:
            lines += sum(self._row_line_count(record) for record in self._model.ended)
        return min(max(3, lines + 2), max(height // 2, 3))

    def _row_line_count(self, item: SidebarEntry | Mapping[str, Any]) -> int:
        """The lines one ruled row paints, open or ended: one at the wide budget — two when its alike word
        needs the second line (ruled 2026-11-20 23:4x); at the narrow one, three for a session still going
        (its state, owner and typer) and two for an ended one (its words end at the end word, rule 6)."""
        if self.wide:
            return self._wide_alike_lines().get(_item_id(item), 1)
        state = item.state if isinstance(item, SidebarEntry) else item.get("state")
        return 2 if state in ENDED_STATES else 3

    def _wide_alike_lines(self) -> dict[str, int]:
        """The sessions whose wide row paints its alike word on a second line, per session id (ruled
        2026-11-20 23:4x): two rows alike with the same words, and the row's one line cannot hold the
        ruled words plus `since HH:MM` with the name at its ellipsis. The ended rows behind `e` read alike
        with the open ones, as the frame's height counts them. Cached beside the model, width and `e`, as
        the frame's height reads it on every painted line."""
        width = max(self.sidebar_width - 4, 1)
        if self._wide_alike_for == (self._model, width, self.show_ended):
            return self._wide_alike_cache
        entries: list[SidebarEntry] = []
        for group, items in self._model.groups:
            if group == GROUP_OPEN_HERE:
                entries += [item if isinstance(item, SidebarEntry) else _entry_for(item, group, self._model)
                            for item in items]
        if self.show_ended:
            entries += [_entry_for(record, GROUP_ENDED, self._model) for record in self._model.ended]
        counts: dict[tuple, int] = {}
        for entry in entries:
            key = _alike_key(entry)
            counts[key] = counts.get(key, 0) + 1
        lines: dict[str, int] = {}
        for entry in entries:
            if counts[_alike_key(entry)] < 2:
                continue
            alike = _since_word(entry.created_at)
            if alike and not _wide_alike_fits(entry, width, alike):
                lines[entry.session_id] = 2
        self._wide_alike_for = (self._model, width, self.show_ended)
        self._wide_alike_cache = lines
        return lines

    def _open_numbers(self) -> dict[str, list[int]]:
        """Every target's open numbers (ruled 2026-11-20): each open-here item's own rail number, skipping an
        item with no chip. The rail chips keep every open slot, ended ones included, so a position in the group
        is not the number."""
        numbers: dict[str, list[int]] = {}
        for group, items in self._model.groups:
            if group != GROUP_OPEN_HERE:
                continue
            for item in items:
                entry = item if isinstance(item, SidebarEntry) else _entry_for(item, group, self._model)
                if entry.number is not None:
                    numbers.setdefault(entry.target, []).append(entry.number)
        return numbers

    def _filter_targets(self) -> tuple[TargetRow, ...]:
        """The raw filter over all targets (rule 1, 2026-11-21): the trimmed text is matched, case-insensitive,
        as one substring against the name, the host, the group, each tag and the label the row shows (the
        fallback, so a typed id matches an unnamed target). Nothing is resolved: no DNS lookup, no socket call."""
        needle = self.filter_text.strip().lower()
        cache = self._filter_cache
        if cache is not None and cache[0] == (self._model, needle):
            return cache[1]
        if not needle:
            rows = self._model.targets
        else:
            rows = tuple(row for row in self._model.targets
                         if needle in row.name.lower() or needle in row.host.lower()
                         or needle in row.group.lower()
                         or any(needle in tag.lower() for tag in row.tags)
                         or needle in _target_label(row).lower())
        ordered = self._ordered_targets(rows)
        self._filter_cache = ((self._model, needle), ordered)
        return ordered

    def _ordered_targets(self, rows: tuple[TargetRow, ...]) -> tuple[TargetRow, ...]:
        """The ruled target order (2026-11-20): targets with a session open in this window first, by their
        lowest open number; then the rest in natural order of the label (case-insensitive, digit runs as
        numbers: rtr-2 before rtr-10)."""
        numbers = self._open_numbers()
        here = [row for row in rows if row.id in numbers]
        here.sort(key=lambda row: min(numbers[row.id]))
        rest = [row for row in rows if row.id not in numbers]
        rest.sort(key=lambda row: natural_key(_target_label(row)))
        return (*here, *rest)

    def visible_targets(self) -> tuple[TargetRow, ...]:
        """The targets the filter leaves, in the ruled order, computed once per change: cached by the target
        rows, the filter text and the open-here sessions (the order reads them), so painting N rows calls the
        filter once, not N times. A change to any of the three recomputes it."""
        open_key = tuple(item if isinstance(item, SidebarEntry) else item.get("id", "")
                         for group, items in self._model.groups if group == GROUP_OPEN_HERE for item in items)
        key = (self._model.targets, self.filter_text, open_key)
        cache = self._targets_cache
        if cache is not None and cache[0] == key:
            return cache[1]
        result = self._filter_targets()
        self._targets_cache = (key, result)
        return result

    def _target_pos_of(self, target_id: str | None) -> int:
        """The painted place of a target id, by bisect on the cached spans (rule 11, 2026-11-21): a scan over
        8,000 rows per key is what made a key pause. The spans cache holds the rows it was walked for, so the
        ids are read from them without rebuilding the list."""
        spans, rows = self._target_spans(), self.painted_targets()
        at = bisect.bisect_right(spans, self._target_pos) - 1
        if at >= 0 and at < len(rows) and rows[at].id == target_id:
            return at
        cache = self._target_spans_cache
        span_rows = cache[0] if cache is not None else rows
        for i, row in enumerate(span_rows):
            if row.id == target_id:
                return i
        return min(self._target_pos, len(rows))

    def _recent_targets(self) -> tuple[TargetRow, ...]:
        """The `Recent` section (rule 9, 2026-11-21): the targets of this login's own sessions, newest
        `created_at` first, each target once, at most three. Hidden while the filter has text. Cached beside
        the model and the filter text, so a paint reads lookups."""
        # The model object is in the key (rule 11): a rebuilt model that compares equal to the old rows keeps
        # no rows built from the old one.
        key = (self._model, self._model.targets, self._model.groups, self._model.ended, self.filter_text)
        cache = self._recent_cache
        if cache is not None and cache[0] == key:
            return cache[1]
        rows: tuple[TargetRow, ...] = ()
        if not self.filter_text.strip():
            by_id = {row.id: row for row in self._model.targets}
            created: list[tuple[str, str]] = []
            for group, items in self._model.groups:
                for item in items:
                    if isinstance(item, SidebarEntry):
                        owner, at, target = item.owner, item.created_at, item.target
                    elif self._model.record_entry is not None:
                        entry = self._model.record_entry(item, group)
                        owner, at, target = entry.owner, entry.created_at, entry.target
                    else:
                        owner, at, target = item.get("owner"), item.get("created_at", ""), item.get("target_id", "")
                    if owner == "yours" and at:
                        created.append((at, target))
            for record in self._model.ended:
                if isinstance(record, SidebarEntry):
                    owner, at, target = record.owner, record.created_at, record.target
                elif self._model.record_entry is not None:
                    entry = self._model.record_entry(record, GROUP_ENDED)
                    owner, at, target = entry.owner, entry.created_at, entry.target
                else:
                    owner, at, target = record.get("owner"), record.get("created_at", ""), record.get("target_id", "")
                if owner == "yours" and at:
                    created.append((at, target))
            seen: set[str] = set()
            rows = tuple(by_id[target] for at, target in sorted(created, reverse=True)
                         if target in by_id and target not in seen and not seen.add(target))[:3]
        self._recent_cache = (key, rows)
        return rows

    def painted_targets(self) -> tuple[TargetRow, ...]:
        """Every target row the section paints: the Recent rows, then the filtered main list's. Cached beside
        the recent rows and the visible rows (rule 11, 2026-11-21): the spans cache keys on this tuple's
        identity, so a key press reads the cached rows, never rebuilds them."""
        key = (self._model, self._recent_targets(), self.visible_targets())
        if self._painted_cache is not None and self._painted_cache[0] == key:
            return self._painted_cache[1]
        rows = (*key[1], *key[2])
        self._painted_cache = (key, rows)
        return rows

    def _has_filter_line(self) -> bool:
        return self.editing or bool(self.filter_text)

    def _target_painted_count(self) -> int:
        """How many lines the Targets section paints: the filter line when one is set or typed, the `Recent`
        title and its rows when it shows, then each target's row (two lines at the narrow budget when it has
        facts, one otherwise), or the section's empty, loading or error lines."""
        head = (1 if self._has_filter_line() else 0) + (1 if self._recent_targets() else 0)
        rows = self.painted_targets()
        if not rows:
            return head + len(self._targets_empty_lines(max(self.sidebar_width - 4, 1)))
        lines = head + sum(self._target_row_lines(row) for row in rows)
        if self._model.targets_more_failed:
            lines += 1  # the `Could not load more · r retry` line below the last row (rule 9, 2026-11-22)
        return lines

    def _target_row_lines(self, row: TargetRow) -> int:
        """The lines one target's row takes: two at the narrow budget when its second line has words, else one."""
        if self.wide:
            return 1
        inner = max(self.sidebar_width - 4, 1)
        _, facts = _target_facts(row, self._open_numbers().get(row.id, ()), self._model.others.get(row.id, ""),
                                 row.group, max(_target_facts_budget(False, inner), 0))
        return 2 if facts else 1

    def _target_line_index(self, target_id: str) -> int | None:
        """The painted index of a target's first line, the filter and `Recent` title lines included; None when
        it is not shown. The place comes from the cached spans by bisect (rule 11)."""
        offset = (1 if self._has_filter_line() else 0) + (1 if self._recent_targets() else 0)
        spans = self._target_spans()
        rows = self.painted_targets()
        at = bisect.bisect_right(spans, self._target_pos) - 1
        if at >= 0 and at < len(rows) and rows[at].id == target_id:
            return offset + spans[at]
        cache = self._target_spans_cache
        span_rows = cache[0] if cache is not None else rows
        for i, row in enumerate(span_rows):
            if row.id == target_id:
                return offset + spans[i]
        return None

    def _build_pairs(self, rows: tuple[TargetRow, ...], spans: tuple[int, ...],
                     facts: tuple[tuple[str, str], ...], filter_line: bool,
                     recent: tuple[TargetRow, ...], wide: bool) -> tuple[tuple[tuple[int, int], ...], list[int]]:
        """(start, lines) of every painted row, built once with the spans cache: the filter line, the `Recent`
        title and its rows, then the main list's. The list paints whole rows and scrolls by them (ruled
        2026-11-20 20:1x), so its scroll positions are row starts. The line count follows the window's budget,
        not the row's own width (rule 11): a thin sidebar inside a wide window paints one-line rows."""
        pairs: list[tuple[int, int]] = []
        offset = 0
        if filter_line:
            pairs.append((0, 1))
            offset = 1
        if recent:
            # The `Recent` title line; its rows are the spans' first entries (painted_targets includes them).
            pairs.append((offset, 1))
            offset += 1
        for position, span in enumerate(spans):
            count = 1 if wide else (2 if facts[position][1] else 1)
            pairs.append((offset + span, count))
        if self._model.targets_more_failed and spans:
            # The last line of the list when a later page failed (rule 9, 2026-11-22): `r retry` sits below the
            # rows that stayed loaded, one pair at the end of the walk.
            last = spans[-1]
            pairs.append((offset + last + (1 if wide else (2 if facts[len(spans) - 1][1] else 1)), 1))
        result = tuple(pairs)
        return result, [start for start, _count in pairs]

    def _target_spans(self) -> tuple[int, ...]:
        """Each painted target row's start index, the filter and `Recent` title lines excluded. Cached for the
        painted rows at this width and wide mode, so painting N rows walks nothing. The cache also records each
        row's facts (the full facts and the facts composed within the budgeted line), so the painted line, the
        row pairs and the painted end read them once per refresh, never recomputing per painted line (ruled
        2026-11-21)."""
        cache = self._target_spans_cache
        numbers = self._open_numbers()
        others = self._model.others
        wide = self.wide
        rows = self.painted_targets()
        inner = max(self.sidebar_width - 4, 1)
        key = (wide, inner)
        # The model object is in the key: a rebuild that compares equal to the old `others` mapping must not
        # keep facts painted from the old model.
        if cache is not None and cache[0] is rows and cache[1] == key \
                and cache[2] is self._model and cache[3] == (numbers, others):
            return cache[4]
        # The cache stores the row pairs and their starts too (rule 11, 2026-11-21): a key on 8,000 rows reads
        # them once, never rebuilds them per painted line.
        # The budget follows the window, not the row's own width (P2b's ruled target-row geometry): a narrow
        # window keeps its two-line rows even when the sidebar is thin inside a wide window.
        budgeted_wide = self.window_width >= self._wide_from
        budget = max(_target_facts_budget(budgeted_wide, inner), 0)
        spans: list[int] = []
        facts: list[tuple[str, str]] = []
        at = 0
        if wide:
            # One line per row at the wide budget: the spans are 0..N-1 and the facts are the full segments.
            # The facts walk stops at the window's rows (rule 11, 2026-11-21): a key on 8,000 rows costs the
            # rows on view, never the whole list. The spans past the window are the same 0..N-1 arithmetic.
            limit = min(len(rows), max(self.targets_height, 1) + 1)
            for row in rows[:limit]:
                spans.append(at)
                at += 1
                segments = [row.host] if row.host else []
                open_numbers = numbers.get(row.id, ())
                if open_numbers:
                    segments.append("open " + ",".join(str(number) for number in sorted(open_numbers)))
                if row.group:
                    segments.append(row.group)
                others_word = others.get(row.id, "")
                if others_word:
                    segments.append(others_word)
                # ruling 17 (2026-11-22): `host · open N,M`, then the group, then the others word; the type
                # word rides last of the segments.
                if row.type and row.type != "ssh":
                    segments.append(row.type)
                # A row's second line shows only while its facts fit whole inside the window's budget (rule 11):
                # a thin sidebar inside a wide window paints one-line rows. The greedy fit is the same rule the
                # painted line uses, so it composes them here once.
                full = " · ".join(segments)
                budgeted = ""
                for segment in segments:
                    candidate = segment if not budgeted else f"{budgeted} · {segment}"
                    if len(candidate) <= budget:
                        budgeted = candidate
                facts.append((full, budgeted))
            spans.extend(range(at, len(rows)))
            result = tuple(spans)
            pairs, starts = self._build_pairs(rows, result, tuple(facts), self._has_filter_line(),
                                         self._recent_targets(), True)
            self._target_spans_cache = (rows, key, self._model, (numbers, others), result, tuple(facts),
                                        pairs, starts)
            return result
        for row in rows:
            spans.append(at)
            full, budgeted = _target_facts(row, numbers.get(row.id, ()), others.get(row.id, ""), row.group,
                                           budget)
            facts.append((full, budgeted))
            at += 1 if (budgeted_wide or not budgeted) else 2
        result = tuple(spans)
        pairs, starts = self._build_pairs(rows, result, tuple(facts), self._has_filter_line(),
                                     self._recent_targets(), False)
        self._target_spans_cache = (rows, key, self._model, (numbers, others), result, tuple(facts),
                                    pairs, starts)
        return result

    def _target_facts_cache(self) -> tuple[tuple[str, str], ...]:
        """Each visible target's (full facts, painted facts), built once per refresh by `_target_spans` and read
        by the painted line, the row pairs and the painted end."""
        self._target_spans()  # builds the cache when it is not current for this width and model
        return self._target_spans_cache[5]

    def _loading_lines(self, inner: int) -> list[Line]:
        """The loading copy as `_empty_copy`'s rule gives it (rule 16): `… loading` is a segment of its own, so
        the words show whole at the narrow budget too."""
        return _empty_copy("Loading targets… loading", inner, "muted")

    def _target_painted(self, index: int) -> Line | None:
        """The Targets section's painted line `index` (0-based, the filter line, then the `Recent` title, then
        the rows), built when it is painted or clicked."""
        if not (self._target_scroll_top <= index < self._target_painted_end()):
            return None  # scrolled off, or a row that does not fit whole at the section's bottom (ruled 2026-11-20 20:1x)
        inner = max(self.sidebar_width - 4, 1)
        recent = self._recent_targets()
        if self._has_filter_line():
            if index == 0:
                shown = f"/ {self.filter_text}▏" if self.editing else f"/ {self.filter_text}"
                return Line((Part(_fit(shown, inner), "filter", CMD_FILTER,
                                  tip="/ filter the targets by name, host, group, tag or id"),))
            index -= 1
        if recent:
            if index == 0:
                return Line((Part(_fit("Recent", inner), "group"),))
            index -= 1
        rows = self.painted_targets()
        if not rows:
            empty = self._targets_empty_lines(inner)
            return empty[index] if 0 <= index < len(empty) else None
        spans = self._target_spans()
        # the row whose span is the greatest at or below the line, by bisect (rule 11): a scan of 8,000 spans
        # per painted line is what made a key pause.
        position = bisect.bisect_right(spans, index) - 1
        if position < 0:
            return None
        row = rows[position]
        cache = self._target_spans_cache
        if cache is not None and position < len(cache[5]):
            full, facts = cache[5][position]
        else:
            full, facts = _target_facts(row, self._open_numbers().get(row.id, ()),
                                        self._model.others.get(row.id, ""), row.group,
                                        max(_target_facts_budget(self.wide, inner), 0))
        if self.wide and not facts:
            # The fast path stored the full facts; the painted line composes them within its own budget. The
            # numbers come from the open-session map the spans cache keyed on (rule 17's segment order).
            _full, facts = _target_facts(row, self._open_numbers().get(row.id, ()),
                                         self._model.others.get(row.id, ""), row.group,
                                         max(_target_facts_budget(self.wide, inner), 0))
        label = _target_label(row)
        # The tooltip carries the row's full facts, the segment the painted line omits included (ruled 2026-11-21).
        tip = " · ".join(part for part in (label, full) if part) or None
        selected = position == self._target_pos
        mark = (SELECTED, "mark") if selected else (" ", "blank")
        within = index - spans[position]
        row_lines = 1 if (self.wide or not facts) else 2
        if self._model.targets_more_failed and position == len(rows) - 1 and within >= row_lines:
            # `r retry` sits below the last loaded row when a later page failed (rule 9, 2026-11-22): the copy's
            # shape is `_empty_copy`'s, with CMD_RETRY naming the targets-more retry.
            return _empty_copy("Could not load more · r retry", inner, "muted", CMD_RETRY, "targets-more")[0]
        if within >= row_lines:
            return None  # past this row's own lines: the next row's start, or the section's end
        if self.wide or not facts or within == 0:
            if self.wide:
                room = max(inner - 2 - len(facts), 0) if facts else inner - 1
                return Line((Part(mark[0], mark[1]), Part(_cut_name(label, max(room, 1)), "text"),
                             Part(f" {facts}" if facts else "", "text")), row.id, PANE_TARGETS, tip)
            return Line((Part(mark[0], mark[1]), Part(_cut_name(label, max(inner - 1, 1)), "text")),
                        row.id, PANE_TARGETS, tip)
        return Line((Part(" ", "blank"), Part(facts.ljust(max(inner - 1, 1)), "text")),
                    row.id, PANE_TARGETS, tip)

    def _targets_empty_lines(self, inner: int) -> list[Line]:
        """The Targets section's lines when no target is on show: loading, the 503 with `r retry`, the ruled
        no-match copy once every page has arrived (rule 7), the ruled empty copy with its key (`a add one` for
        a `target.edit` holder). The copy wraps at its ` · ` segments, so its exact words show whole at the
        narrow budget too (rule 16)."""
        if self._model.targets_unavailable:
            return _empty_copy("Could not load targets · r retry", inner, "muted", CMD_RETRY, "targets")
        if self._model.targets_loading:
            return self._loading_lines(inner)
        needle = self.filter_text.strip()
        # Rule 7 (2026-11-21): the no-match copy shows only once every page has arrived; while pages are
        # outstanding the loading lines show, and `loading` is a segment of its own (rule 16).
        # Rule 9 (2026-11-21): `Recent` sits at the top of the list, so its rows show with the filter text;
        # the empty copy below says what the main list cannot answer.
        if needle and not self._model.target_has_next and not self._model.targets_loading:
            # Rule 16 (2026-11-21): the copy wraps by `_empty_copy`'s rule, so the quoted text and the key word
            # are one segment each and show whole at the narrow budget.
            return _empty_copy(f"No target matches '{needle}' · Esc clears", inner, "muted")
        if self._model.target_edit:
            return _empty_copy("No targets yet · a add one", inner, "muted", CMD_TARGET_NEW)
        return _empty_copy("No targets you can see · ask an admin for access", inner, "muted")

    # -- the Targets list's selection --

    @property
    def selected_target(self) -> str | None:
        """The selected target's id, as rule 6 (2026-11-21) keeps it: the chosen row while the painted list
        shows it; when its row disappears, the next surviving row below its place, else the last row. The
        rule runs in `_relayout`, so a filter change that has not relaid yet answers for the current rows."""
        if self.target_selected is not None and any(row.id == self.target_selected for row in self.painted_targets()):
            return self.target_selected
        ids = [row.id for row in self.painted_targets()]
        following = ids[self._target_pos:]
        if following:
            return following[0]
        return ids[-1] if ids else None

    def _move_target(self, step: int) -> None:
        painted = self.painted_targets()
        if not painted:
            return
        current = self.selected_target
        index = self._target_pos_of(current) if current is not None else 0
        place = max(0, min(index + step, len(painted) - 1))
        self.target_selected = painted[place].id
        self._target_pos = place
        self._scroll_target_to_selected()
        self.refresh()

    def _jump_target(self, last: bool) -> None:
        """`Home`/`End` on the Targets list (rule 11): the first, the last painted row."""
        painted = self.painted_targets()
        if not painted:
            return
        ids = [row.id for row in painted]
        self.target_selected = ids[-1] if last else ids[0]
        self._target_pos = len(ids) - 1 if last else 0
        self._scroll_target_to_selected()
        self.refresh()

    def _scroll_target_to_selected(self) -> None:
        """Scroll the Targets list by whole rows (ruled 2026-11-20 20:1x): the scroll position is a row's first
        line, and a selected row the section cannot show whole scrolls to the least that shows it whole."""
        chosen = self.selected_target
        index = self._target_line_index(chosen) if chosen is not None else None
        height = max(self.targets_height - 2, 1)
        if index is not None:
            if index < self._target_scroll_top:
                self._target_scroll_top = index
            elif not self._target_row_visible(index, height):
                pairs = self._target_row_pairs()
                starts = self._pair_starts(pairs)
                selected = bisect.bisect_right(starts, index) - 1
                top = bisect.bisect_left(starts, self._target_scroll_top)
                # The least row start at or below the selected row whose rows fit the window: scan up from the
                # selected row, bounded by the window's height in rows.
                for j in range(selected, top - 1, -1):
                    if sum(count for _start, count in pairs[j:selected + 1]) <= height:
                        self._target_scroll_top = pairs[j][0]
                        break
        self._target_scroll_top = max(0, min(self._target_scroll_top, self._target_scroll_limit()))

    # -- panes and folding --

    def focus_pane(self, pane: str, *, editing: bool = False) -> None:
        """Give the keys to one list: a folded list opens first, the filter is typed into only when asked."""
        if self.folded == pane:
            self.folded = None
        self.pane = pane
        self.editing = editing and pane == PANE_TARGETS
        self._relayout()
        self._scroll_target_to_selected()
        self.focus()
        self.refresh()

    def toggle_fold(self, section: str | None = None) -> None:
        """`z` folds the list the keys are not on (the one they are on fills the panel) and `z` again restores; a click
        on a title folds that list, and the same click unfolds it. The keys go to the list that stays open."""
        if section is None:
            self.folded = None if self.folded is not None else (
                PANE_SESSIONS if self.pane == PANE_TARGETS else PANE_TARGETS)
        else:
            self.folded = None if self.folded == section else section
        if self.folded == self.pane:
            self.pane = PANE_SESSIONS if self.pane == PANE_TARGETS else PANE_TARGETS
            self.editing = False
        self._target_scroll_top = min(self._target_scroll_top, self._target_scroll_limit())
        self._relayout()
        self._scroll_target_to_selected()
        self.refresh()

    def _target_row_pairs(self) -> tuple[tuple[int, int], ...]:
        """(start, lines) of every painted row of the Targets section, from the spans cache (rule 11): the filter
        line, the `Recent` title and its rows, then the main list's. Built once per refresh with the facts, never
        rebuilt per painted line."""
        self._target_spans()  # builds the cache when it is not current for this width and model
        return self._target_spans_cache[6]

    def _pair_starts(self, pairs: tuple[tuple[int, int], ...]) -> list[int]:
        """The row starts of the pairs, from the spans cache they were built with (rule 11, 2026-11-21): bisect
        needs them, and rebuilding a list of 8,000 starts per key is what made a key pause."""
        return self._target_spans_cache[7]

    def _target_first_pair(self) -> tuple[int, int]:
        """The first row pair at or below the scroll top, found by bisect on the cached starts (rule 11,
        2026-11-21): a walk over 8,000 rows per painted line is what made a key pause."""
        pairs = self._target_row_pairs()
        if not pairs:
            return (self._target_scroll_top, 0)
        at = bisect.bisect_right(self._pair_starts(pairs), self._target_scroll_top) - 1
        return pairs[max(at, 0)]

    def _target_painted_end(self) -> int:
        """One past the last line the Targets section paints: a multi-line row paints whole or not at all, so
        the walk stops at the first row that does not fit under the section's inner height (ruled 2026-11-20
        20:1x). The walk starts at the first row at or below the scroll top, never at the list's head; it stops
        once the window is full, so a paint over 8,000 rows costs the window's rows, not the list (rule 11)."""
        height = max(self.targets_height - 2, 0)
        if not self.painted_targets():
            return self._target_scroll_top + height
        top = self._target_scroll_top
        start, count = self._target_first_pair()
        if start < top:
            return top + height
        end = start + count
        pairs = self._target_row_pairs()
        # The pair at the walk's start is counted already; bisect finds its index, not the next one.
        index = bisect.bisect_left(self._pair_starts(pairs), start) + 1
        for _s, c in itertools.islice(pairs, index, None):
            if end + c > top + height:
                break
            end += c
            if end >= top + height:
                break
        return end

    def _target_row_visible(self, start: int, height: int) -> bool:
        """Whether the target row whose first line is `start` paints whole from the current scroll position."""
        pairs = self._target_row_pairs()
        starts = self._pair_starts(pairs)
        at = bisect.bisect_right(starts, self._target_scroll_top) - 1
        index = bisect.bisect_right(starts, start) - 1
        if index < 0:
            return False
        return sum(c for _s, c in pairs[max(at, 0):index + 1]) <= height

    def _target_scrollable(self) -> bool:
        """Whether the Targets list needs scrolling at all: more lines than the section shows. The count stops
        at the window's full height, so a key on a long list costs the rows on view (rule 11)."""
        height = max(self.targets_height - 2, 0)
        pairs = self._target_row_pairs()
        total = 0
        for _start, count in pairs:
            total += count
            if total > height:
                return True
        return False

    def _target_scroll_limit(self) -> int:
        """The lowest whole-row scroll position of the Targets list: a row start whose row fits the inner height
        (ruled 2026-11-20 20:1x). When every row fits, the limit is 0: the whole list shows (rule 11, 2026-11-21),
        so a scroll from a long list that the filter shrank lands back at the head."""
        height = max(self.targets_height - 2, 0)
        pairs = self._target_row_pairs()
        if not pairs:
            return 0
        total = 0
        for _start, count in pairs:
            total += count
            if total > height:
                break
        if total <= height:
            return 0
        last_start, last_count = pairs[-1]
        if last_count <= height:
            # the walk runs back from the tail, bounded by the window's rows (rule 11).
            acc = last_count
            for start, count in reversed(pairs[:-1]):
                if acc + count > height:
                    return min(start + 1, last_start)
                acc += count
            return 0
        return max((start for start, count in pairs if count <= height), default=0)

    @property
    def targets_height(self) -> int:
        return max((self.size.height or 40) - self.sessions_height, 0)

    def line_offset(self, index: int) -> int | None:
        """The screen row of a visible logical Sessions line."""
        row = index - self._scroll_top
        if row < 0 or index >= self._session_painted_end():
            return None
        return self.targets_height + 1 + row if row < self.sessions_height - 2 else None

    def _empty_lines(self, inner: int) -> list[Line]:
        """The Sessions section's lines when no row is on show (ruled 2026-11-20): loading, the poll's error
        with `r retry`, the only-ended state with `e show ended`, else the plain empty state. The copy wraps at
        its ` · ` segments, so its exact words show whole at the narrow budget too."""
        if self._model.sessions_error:
            return _empty_copy("Could not load sessions · r retry", inner, "muted", CMD_RETRY, "sessions")
        if self._model.sessions_loading:
            return [Line((Part(_fit("Loading sessions…", inner), "muted"),))]
        if self._model.ended and not self.show_ended:
            return _empty_copy("No live sessions · e show ended", inner, "muted", CMD_SHOW_ENDED)
        return _empty_copy("No live sessions · n new session", inner, "muted", CMD_NEW)

    def _line_at(self, y: int) -> Line | None:
        row = y - self.targets_height - 1
        index = row + self._scroll_top
        if not self._view.rows:
            empty = self._empty_lines(max(self.sidebar_width - 4, 1))
            return empty[row] if 0 <= row < len(empty) else None
        if not (self._scroll_top <= index < self._session_painted_end()):
            return None  # scrolled off, or a row that does not fit whole at the pane's bottom (ruled 2026-11-20 20:1x)
        if 0 <= row < self.sessions_height - 2 and index < len(self._view.lines):
            return self._view.lines[index]
        return None

    @property
    def text(self) -> str:
        """The actual framed rows, including both section perimeters."""
        return "\n".join(self.render_line(y).text for y in range(self.size.height))

    def on_focus(self, event: events.Focus) -> None:
        self.refresh()

    def on_blur(self, event: events.Blur) -> None:
        if self.editing:
            # The keys left the list: what was typed stays as the filter, and typing into it is over.
            self.editing = False
            self._relayout()
        self.refresh()

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        inner = max(width - 4, 0)
        targets = y < self.targets_height
        top = 0 if targets else self.targets_height
        height = self.targets_height if targets else self.sessions_height
        focused = self.has_focus and (self.pane == PANE_TARGETS if targets else self.pane == PANE_SESSIONS)
        styles, border_style = (self._styles, self._border) if targets else (self._styles_sessions,
                                                                             self._border_sessions)
        tl, horizontal, tr, vertical, bl, br = ("┏", "━", "┓", "┃", "┗", "┛") if focused else ("┌", "─", "┐", "│", "└", "┘")
        if y == top:
            inner_title = max(width - 4, 1)
            if targets:
                rows = self.visible_targets()
                total = len(self._model.targets)
                loaded = len(self._model.targets)
                loading = self._model.target_has_next
                chosen = self.selected_target
                place = next((i for i, row in enumerate(rows) if row.id == chosen), None)
                index = str(place + 1) if place is not None else "–"
                # The ruled mark (P2a: `▦ Targets`) leads the title; its two cells come out of the budget.
                title = target_title(index, len(rows), total, loaded, self.filter_text.strip(), loading,
                                     max(inner_title - 2, 1))
                mark = "▦"
            else:
                mark = "▸"
                if self.folded == PANE_SESSIONS:
                    title = f"Sessions · {len(self._view.rows)}"
                else:
                    place = next((i for i, row in enumerate(self._view.rows) if row == self.selected), None)
                    index = str(place + 1) if place is not None else "–"
                    title = session_title(index, len(self._view.rows), len(self._view.rows), "",
                                          max(inner_title - 2, 1),
                                          loading=self._model.sessions_loading)
            # Rule 16 (2026-11-21): the last part that does not fit whole drops, in order: the quoted text,
            # then `of N`, then `loading`; the index pair always shows. The mark leads and never drops.
            title = f"{mark} {title}"
            label = horizontal + " " + title + " "
            parts = title.split(" ")
            while len(label) > width - 2 and len(parts) > 3:
                parts.pop()
                title = " ".join(parts)
                label = horizontal + " " + title + " "
            if self._model.target_has_next and len(parts) == 2:
                # the word drops last (rule 16): at the narrow budget the pair with its ellipsis stands alone.
                head, sep, counts = title.partition("· ")
                index, _slash, first = counts.partition("/")
                title = f"{head}{sep}{index}/{first}…"
                label = horizontal + " " + title + " "
            text = tl + label[:max(width - 2, 0)].ljust(max(width - 2, 0), horizontal) + tr
            text = glyphs.render(text, self._glyph_set)
            return Strip([Segment(text, border_style)]).adjust_cell_length(width)
        if y == top + height - 1:
            return Strip([Segment(bl + horizontal * max(width - 2, 0) + br, border_style)])
        line = None
        if targets:
            index = y - top - 1 + self._target_scroll_top
            line = self._target_painted(index)
        else:
            line = self._line_at(y)
        bar = None
        if line is not None and line.session_id is not None:
            if targets:
                chosen = line.group == PANE_TARGETS and line.session_id == self.selected_target
            else:
                chosen = (line.group, line.session_id) == self.selected
            if chosen:
                bar = self._bar if focused else self._bar_dim
        base = styles["blank"] if bar is None else styles["blank"] + bar
        segments = [] if line is None else [
            Segment(glyphs.render(part.text, self._glyph_set),
                    styles.get(part.role, styles["text"]) + (bar if bar is not None else Style()))
            for part in line.parts if part.text]
        content = Strip(segments).adjust_cell_length(inner, base)
        return Strip([Segment(vertical, border_style), Segment(" ", base),
                      *content, Segment(" ", base), Segment(vertical, border_style)])

    # -- clicks --

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """The tooltip of the part (or the row) under the pointer: the words and key of a glyph, the whole of a row
        the sidebar had to cut short (D13). A pointer over the Targets section resolves through `_target_line_at`
        first, so a cut target row reveals its complete row."""
        tip = None
        line = self._target_line_at(event.y) if event.y < self.targets_height else self._line_at(event.y)
        if line is not None:
            x = event.x - 2
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
            self._wheel(event.y, -1)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if mouse_on(self):
            event.stop()
            self._wheel(event.y, 1)

    def _wheel(self, y: int, step: int) -> None:
        """The wheel moves the selection through the sessions, a row a turn; over a Targets list longer than its
        section it scrolls that list."""
        if y < self.targets_height and self._target_scrollable():
            self._scroll_section(y, step)
        else:
            self._move(step)

    def _scroll_section(self, y: int, step: int) -> None:
        """A wheel turn over a list scrolls it by a whole row (ruled 2026-11-20 20:1x)."""
        if y < self.targets_height:
            starts = [start for start, _count in self._target_row_pairs()]
            if step > 0:
                following = [start for start in starts if start > self._target_scroll_top]
                if following:
                    self._target_scroll_top = min(min(following), self._target_scroll_limit())
            else:
                above = [start for start in starts if start < self._target_scroll_top]
                if above:
                    self._target_scroll_top = max(above)
        else:
            starts = self._row_starts()
            if step > 0:
                following = [start for start in starts if start > self._scroll_top]
                if following:
                    self._scroll_top = min(min(following), self._session_max_scroll())
            else:
                above = [start for start in starts if start < self._scroll_top]
                if above:
                    self._scroll_top = max(above)
        self.refresh()

    def _target_line_at(self, y: int) -> Line | None:
        index = y - 1 + self._target_scroll_top
        return self._target_painted(index) if 0 < y < self.targets_height - 1 else None

    def on_click(self, event: events.Click) -> None:
        if event.y in (0, self.targets_height) and mouse_on(self):
            # A click on a section's title folds that list to it (the other fills the panel); the same click unfolds.
            event.stop()
            self.toggle_fold(PANE_TARGETS if event.y == 0 else PANE_SESSIONS)
            return
        if event.y < self.targets_height:
            line = self._target_line_at(event.y)
            if line is not None and mouse_on(self):
                event.stop()
                x = event.x - 2
                cursor = 0
                for part in line.parts:
                    if cursor <= x < cursor + len(part.text):
                        if part.command is not None:
                            self._command_from_part(part)
                        break
                    cursor += len(part.text)
                if line.group == PANE_TARGETS and line.session_id is not None:
                    self.pane = PANE_TARGETS
                    self.target_selected = line.session_id
                    # Highlight the row clicked (rule 10, 2026-11-22): a target in Recent and the main list marks
                    # the appearance clicked, not both; actions keep using the id.
                    index = event.y - 1 + self._target_scroll_top
                    spans = self._target_spans()
                    self._target_pos = max(0, bisect.bisect_right(spans, index) - 1)
                    self.refresh()
            return
        line = self._line_at(event.y)
        if not mouse_on(self) or line is None:
            return
        event.stop()
        x = event.x - 2
        cursor = 0
        for part in line.parts:
            if cursor <= x < cursor + len(part.text):
                if part.command is not None:
                    self._command_from_part(part)
                    return
                break
            cursor += len(part.text)
        if line.session_id is not None and line.group is not None:
            self.pane = PANE_SESSIONS
            self.selected = (line.group, line.session_id)
            self._relayout()
            self.post_message(self.Command(CMD_PICK, line.session_id))

    def _command_from_part(self, part: Part) -> None:
        if part.command == CMD_FILTER:
            self.start_filter()
        elif part.command == CMD_SHOW_ENDED:
            self.toggle_show_ended()
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
        """`/` types into the Targets list's filter (the sessions open here are all shown)."""
        if self.folded == PANE_TARGETS:
            self.folded = None
        self.pane = PANE_TARGETS
        self.editing = True
        self._relayout()

    def _filter_changed(self) -> None:
        # The filter text changed: the visible target list is a different, shorter set, so the scroll top returns to
        # its first row. `_relayout` clamps it to the new limit as well.
        self._target_scroll_top = 0
        self._relayout()
        self._scroll_target_to_selected()

    # -- keys --

    def on_key(self, event: events.Key) -> None:
        """Every key is the panel's while it has the keys (D14), except the leader and the key after it, which belong
        to the screen (`^\\ f` closes it, `^\\ 2` switches), and Detach, whose key the screen runs. Tab and Shift+Tab
        move along the chain (Rail, Targets, Sessions): the screen owns where they go."""
        screen = self.screen
        if event.key in (self._leader, DETACH_KEY) or getattr(screen, "leader_pending", False):
            return  # on to the screen
        key = event.key
        if key in ("tab", "shift+tab"):
            event.stop()
            self.editing = False
            self._relayout()
            screen.chain_step(self, 1 if key == "tab" else -1)
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
        if key == "escape":
            if self.filter_text:
                # Esc clears the filter first (rule 5, 2026-11-21); a second Esc follows P2b's list rule.
                self.filter_text = ""
                self._filter_changed()
            else:
                self.post_message(self.Command(CMD_BACK))
        elif key == "z":
            self.toggle_fold()
        elif self.pane == PANE_TARGETS:
            if self._targets_key(key):
                return  # the key was consumed by its own handler (rule 18)
        else:
            if self._sessions_key(key):
                return  # the key was consumed by its own handler (rule 18)

    def _targets_key(self, key: str) -> None:
        if key in ("down", "j"):
            self._move_target(1)
        elif key in ("up", "k"):
            self._move_target(-1)
        elif key == "home":
            self._jump_target(False)
        elif key == "end":
            self._jump_target(True)
        elif key == "n":
            self.post_message(self.Command(CMD_NEW))
        elif key == "enter":
            if self.selected_target is not None:
                self.post_message(self.Command(CMD_OPEN_TARGET, self.selected_target))
        elif key == "i" and self.selected_target is not None:
            self.post_message(self.Command(CMD_DETAILS, self.selected_target))
        elif key in ("?", "question_mark"):
            self.post_message(self.Command(CMD_KEYS))
        elif key == "slash":
            # The press that opens the filter is consumed by opening it, never typed into it (rule 18, 2026-11-22).
            self.start_filter()
            return True
        elif key == "F":
            self.post_message(self.Command(CMD_FULL_VIEW))
        elif key == "a" and self._model.target_edit:
            self.post_message(self.Command(CMD_TARGET_NEW))
        elif key == "r" and self._model.targets_unavailable:
            self.post_message(self.Command(CMD_RETRY, "targets"))
        elif key == "r" and self._model.targets_more_failed:
            # `r` retries the kept cursor when a later page failed (rule 9, 2026-11-22).
            self.post_message(self.Command(CMD_RETRY, "targets-more"))

    def _sessions_key(self, key: str) -> None:
        if key in ("down", "j"):
            self._move(1)
        elif key in ("up", "k"):
            self._move(-1)
        elif key == "home":
            self._jump(False)
        elif key == "end":
            self._jump(True)
        elif key == "n":
            self.post_message(self.Command(CMD_NEW))
        elif key == "enter":
            if self.selected_session is not None:
                self.post_message(self.Command(CMD_PICK, self.selected_session))
        elif key in ("?", "question_mark"):
            self.post_message(self.Command(CMD_KEYS))
        elif key == "t":
            self.post_message(self.Command(CMD_TAKE_BACK, self.selected_session))
        elif key == "w":
            self.post_message(self.Command(CMD_WHOS_HERE, self.selected_session))
        elif key == "x":
            self.post_message(self.Command(CMD_END, self.selected_session))
        elif key == "slash":
            # The press that opens the filter is consumed by opening it, never typed into it (rule 18, 2026-11-22).
            self.start_filter()
            return True
        elif key == "m":
            self.post_message(self.Command(CMD_MODE))
        elif key == "F":
            self.post_message(self.Command(CMD_FULL_VIEW))
        elif key == "e":
            self.toggle_show_ended()
        elif key == "r" and self._model.sessions_error:
            self.post_message(self.Command(CMD_RETRY, "sessions"))

    def _editing_key(self, event: events.Key) -> None:
        key = event.key
        if key == "escape":
            self.filter_text = ""
            self.editing = False
            self._filter_changed()
        elif key == "enter":
            # Enter during the filter opens the selected row, as outside it does (rule 5, 2026-11-21).
            self.editing = False
            self._relayout()
            if self.pane == PANE_TARGETS and self.selected_target is not None:
                self.post_message(self.Command(CMD_OPEN_TARGET, self.selected_target))
        elif key in ("down", "up"):
            self._move_target(1 if key == "down" else -1)
        elif key == "backspace":
            self.filter_text = self.filter_text[:-1]
            self._filter_changed()
        elif event.is_printable and event.character:
            self.filter_text += event.character
            self._filter_changed()
