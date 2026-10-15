"""The one status row (design D43, F12): who types, which session, its state, the actions you can take
now as clickable hints, always ending in the Menu and the Fleet.

Two parts, kept apart so the layout is a pure function a test can read at any width:

* `build_pieces(inputs, width)` turns the models into a list of `Piece`s that fill exactly `width` cells.
  Hints drop from the right by `status.hint_priority` as the width shrinks and `≡ Menu ^\\` is the last
  to go (D43). Then the words after the pill give way by a fixed survival order (`Item`, `_lay`): the
  session name first and, last, the state warnings; what stays is the pill, the state's key fact, the
  countdown value and `● REC`; the Menu hint outlasts them all. A word is whole or gone, and no separator
  is left dangling. The one row never grows a second line: the stale notice is a banner over the canvas
  (`stale_banner.py`).
* `StatusRow` paints those pieces as one line and turns a click on a hint, or on the keyboard pill,
  into the action's id (D11, D12). It is its own widget, never Textual's `Footer`, so a single click
  works on every width (plan R5).

The pill says who types (D21): `‹ you type ›` filled, `‹ view only ›` outlined, `‹ nobody types ›`
muted. It gives way to `keys → …` whenever the keys go somewhere other than the session (D14): a
dialog, the Menu, the leader waiting for its letter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from .. import glyphs
from ..model import actions as A
from ..model.attachment import AttachmentState
from ..model.countdown import countdown_parts
from .mouse import mouse_on, set_tip

# Below this many columns the row is compact: the pill loses its inner spaces and a live session
# says nothing about being live (design §2.6 T2). `view.chrome` "auto" cuts by the window's cells
# (compact < 80); "full" and "compact" force one or the other.
COMPACT_BELOW = 80


def is_compact(chrome: str, width: int) -> bool:
    """Whether the chrome is compact at this window width, by the `view.chrome` knob. The phone profile sets
    it to "compact", and a window under 80 columns is compact under "auto"."""
    return chrome == "compact" or (chrome == "auto" and width < COMPACT_BELOW)
GAP = "   "  # between hints
MIN_GAP = 2  # at least this much between the left part and the hints

# The hints' left-to-right order. Menu and Fleet end the row (D5, D43); the rest follow the design's
# own examples (F12). A hint's *priority* when the width shrinks is `status.hint_priority`'s, not this.
DISPLAY_ORDER = ("answer", "take_back", "detach", "who", "size", "scroll", "end", "inspector", "keys",
                 "menu", "fleet")

NOT_SENT = "your keys were not sent"

# What a refused input says in the status row, by the contract's reason (F12, G11). A reason this
# build does not know reads as the contract says: input was refused (V5).
REFUSAL_TEXT = {
    "not_typist": "your input was refused: you do not hold the keyboard",
    "stale": "your input was refused: the screen may be wrong",
    "replaying": "◐ restoring the data path · input refused for now",
    "unreachable": "◌ unreachable · TSB cannot reach its worker · input refused · not ended",
}
RESTORING = "◐ restoring the data path · input refused for now"
UNKNOWN_REFUSAL = "input was refused"
# The same refusals, shorter, for a row too narrow for the whole text: the fact that input is refused stays.
REFUSAL_SHORT = {
    "not_typist": ("input refused",),
    "stale": ("input refused",),
    "replaying": ("◐ restoring · input refused", "◐ input refused"),
    "unreachable": ("◌ unreachable · input refused · not ended", "◌ unreachable · input refused"),
}

# What survives when the row is too narrow, most important first (the Menu hint is a hint and outlasts the words
# after the pill): the pill, the state's key fact, the countdown value, REC, then the other state warnings; then
# the rest, the session name the first to go. `keys not sent` outranks the who-types phrase, whose fact the pill
# already carries.
RANK_PILL, RANK_FACT = 1, 2
RANK_COUNT, RANK_REC, RANK_WARN = 3, 4, 5
RANK_KEYS, RANK_LEAD, RANK_RANGE, RANK_TOOK, RANK_LIVE, RANK_NAME = 6, 7, 8, 9, 10, 11
RANK_CORE = RANK_WARN  # the ranks up to this one are the core: they shorten before they go
LEAD_SEP = " · "


@dataclass(frozen=True)
class EndedStatus:
    """A session that is over for this client (F10): the words of the row and its hints."""

    text: str  # `✕ ended · rtr-core-01 · killed`
    recording: bool  # the session was recorded: the row says the recording is kept
    hints: tuple[Hint, ...]


@dataclass(frozen=True)
class ReattachStatus:
    """The reattach in progress (F12): which attempt is next, in how many seconds, and why."""

    attempt: int
    seconds: float
    why: str


@dataclass(frozen=True)
class Hint:
    """One entry of a dialog's own hints: `key` then `label`. `action` is an action id, `close`, or None
    for a hint that explains a key and is not clickable."""

    key: str
    label: str
    action: str | None = None


@dataclass(frozen=True)
class KeysTarget:
    """Where the keys go when it is not the session: a dialog, the Menu, the leader, the sidebar (D14). `note`
    follows the name (`keys → fleet · rtr-core-01 shows cols 1-88 of 116 ▸`)."""

    name: str
    hints: tuple[Hint, ...]
    note: str = ""


@dataclass(frozen=True)
class StatusInputs:
    state: AttachmentState | None  # None until the first attach
    session: str  # the session's name for the operator (its target)
    range_label: str  # `cols a-b of N ▸`, or "" when the whole grid is in view
    keys_not_sent: bool
    leader: str  # the printed leader, `^\`
    leader_keys: Mapping[str, str]  # action id -> the letter after the leader
    hint_priority: Sequence[str]
    keys_indicator: bool
    target: KeysTarget | None = None
    # Seen by the status row only: an attach that is still starting.
    attaching: bool = False
    now: float = 0.0  # the client's wall clock, for the countdown (session-countdown rule 9)
    recording_bar: str | None = None  # the recording text, for a later attach (D24, G9)
    ended: EndedStatus | None = None
    reattach: ReattachStatus | None = None
    reattach_stopped: bool = False
    chrome: str = "auto"  # `view.chrome`
    glyph_set: str = glyphs.UNICODE  # `glyphs.set`
    open_sessions: int = 1  # sessions open here: Detach is offered only while one is


@dataclass(frozen=True)
class Piece:
    text: str
    role: str
    action: str | None = None  # the action id a click on this piece runs


@dataclass(frozen=True)
class Item:
    """One whole phrase of the words after the pill. `variants` are its wordings from the fullest to the
    shortest, each complete in itself; `rank` is how long it survives a narrow row (lower stays). `sep` goes
    before it when something kept comes first. A `lead` (who types) is joined to a following lead and to the
    session name (`joins_lead`) with ` · `; whatever else follows it is set apart as usual."""

    role: str
    variants: tuple[str, ...]
    rank: int
    sep: str = "  "
    lead: bool = False
    joins_lead: bool = False
    action: str | None = None


def _width(pieces: Sequence[Piece]) -> int:
    return sum(len(p.text) for p in pieces)  # every glyph here is one cell wide


# -- the left part: the pill and what follows it --


def _kind(state: AttachmentState) -> str:
    if state.holds_keyboard:
        return "type"
    return "none" if state.holder is None else "view"


def _pill(kind: str, compact: bool) -> Item:
    word = {"type": "you type", "view": "view only", "none": "nobody types"}[kind]
    text = f"‹{word}›" if compact else f"‹ {word} ›"
    return Item(f"pill_{kind}", (text,), RANK_PILL, action="who")


def _name(client: Mapping | None) -> str:
    return "nobody" if client is None else str(client.get("user") or client.get("client_id"))


def _last_token(state: AttachmentState) -> dict | None:
    return state.token_history[-1] if state.token_history else None


def _state_notes(inputs: StatusInputs, state: AttachmentState) -> list[Item]:
    """What is wrong with the session now, each its own phrase, in the order a reader needs it: the stale
    screen, the data path, a refusal, the countdown. Empty when nothing is. Each is the state's key fact, so
    each has shorter wordings that keep it (`▲ stale`, `input refused`, `ends in 4:12`). The stale notice line
    itself is not here: it is a banner over the canvas (rule 18)."""
    notes: list[Item] = []
    stale = state.stale_state == "pending"
    if stale:
        if state.holds_keyboard:
            variants = ("▲ stale · nobody's input goes through until you choose", "▲ stale")
        else:
            who = "nobody holds the keyboard" if state.holder is None else f"{_name(state.holder)} is choosing"
            variants = (f"▲ stale · {who} · input refused", "▲ stale · input refused", "▲ stale")
        notes.append(Item("warn", variants, RANK_WARN))
    restoring = state.data_path == "lost"
    reason = state.last_input_refused if state.refusal_active else None
    if reason == "replaying":
        restoring = True
    if restoring:
        notes.append(Item("warn", (RESTORING, *REFUSAL_SHORT["replaying"]), RANK_WARN))
    if reason is not None and reason != "replaying" and not (reason == "stale" and stale):
        full = REFUSAL_TEXT.get(reason, UNKNOWN_REFUSAL)
        notes.append(Item("warn", (full, *REFUSAL_SHORT.get(reason, ())), RANK_WARN))
    if state.ending:
        parts = countdown_parts(state.ends_by, state.ends_at, inputs.now)
        if parts is not None:
            why, value, tail = parts
            variants = [f"▲ {why} · {value}{tail}" if why else f"▲ {value}{tail}"]
            if tail:
                variants.append(f"▲ {why} · {value}" if why else f"▲ {value}")
            if why:
                variants.append(f"▲ {value}")
            notes.append(Item("warn", tuple(variants), RANK_COUNT))
    return notes


def _body(inputs: StatusInputs, state: AttachmentState, kind: str, compact: bool) -> list[Item]:
    """The words after the pill, in F12's order: who types (when it is not you) and why, the session,
    its state and REC, how the keyboard got here, the pan range, a note on keys not sent."""
    last = _last_token(state)
    items: list[Item] = []
    lead: list[tuple[str, ...]] = []  # each phrase's wordings, the fullest first
    if kind == "view":
        if last is not None and last["reason"] == "takeover" and last["holder"] == state.holder:
            name = _name(state.holder)
            lead.append((f"{name} took the keyboard (owner)", f"{name} took it (owner)"))
        else:
            lead.append((f"{_name(state.holder)} types",))
        if state.is_owner_client:
            lead.append(("you own it: typing takes it",))
        elif not state.may_control(state.client_id):
            lead.append(("your right is read-only",))
    elif kind == "none":
        if last is not None and last["reason"] == "owner":
            who = state.owner or "the owner"
            lead.append((f"{who} put the keyboard aside", f"{who} put it aside"))
        else:
            lead.append(("nobody has the keyboard",))
    notes = _state_notes(inputs, state)
    # Who types is the key fact when nothing else is said; when a warning follows it (you own it: typing takes
    # it) the warning is; when a state's own fact is on the row, or the keys were held back (the pill already
    # says who types), who types is only the rest.
    fact = not notes and not inputs.keys_not_sent
    for n, wordings in enumerate(lead):
        rank = RANK_FACT if fact and n == len(lead) - 1 else RANK_LEAD
        items.append(Item("text", wordings, rank, lead=True))
    items.append(Item("text", (inputs.session,), RANK_NAME, joins_lead=True))
    recording = state.recording or {}
    if notes:
        items.extend(notes)
    elif state.session.get("state") == "live":
        if not compact:
            items.append(Item("ok", ("● live",), RANK_LIVE))
    elif state.session.get("state"):
        items.append(Item("warn", (f"◌ {state.session['state']}",), RANK_FACT))
    if recording.get("active"):
        items.append(Item("rec", ("● REC",), RANK_REC))
    if kind == "type" and last is not None and last["reason"] == "takeover" and last["previous"]:
        # The note leaves before the pan range: the range is what tells the operator the screen is wider than their
        # window, and it stays whenever the row has room for it (a phone's 65 columns).
        items.append(Item("text", (f"you took the keyboard from {_name(last['previous'])}",), RANK_TOOK))
    if inputs.keys_not_sent:
        items.append(Item("warn", (NOT_SENT,), RANK_KEYS))
    if inputs.range_label:
        items.append(Item("muted", (inputs.range_label,), RANK_RANGE))
    return items


NO_SESSION = "no session open"


Left = list[Piece] | list[Item]  # the words before the hints: fixed pieces, or items laid out by `_lay`


def _left(inputs: StatusInputs, compact: bool) -> Left:
    state = inputs.state
    if inputs.target is not None and inputs.keys_indicator:
        note = f" · {inputs.target.note}" if inputs.target.note else ""
        return [Piece(f"keys → {inputs.target.name}{note}", "keys")]
    if state is None:
        if not inputs.session and not inputs.attaching:
            return [Piece(NO_SESSION, "muted")]
        text = f"◐ attaching · {inputs.session}" if inputs.attaching else inputs.session
        return [Piece(text, "muted")]
    kind = _kind(state)
    if inputs.recording_bar is not None:
        # A later attach: the fixed recording text takes the place of the words after the pill (D24).
        pill = _pill(kind, compact)
        return [Piece(pill.variants[0], pill.role, pill.action), Piece("  ", "text"),
                Piece(inputs.recording_bar, "rec")]
    return [_pill(kind, compact), *_body(inputs, state, kind, compact)]


def _seconds(value: float) -> str:
    """`2` for two seconds, `0.5` under one: the wait before a reattach attempt."""
    if value >= 1:
        return str(int(value + 0.999))
    return f"{max(value, 0.0):.1f}"


def _special(inputs: StatusInputs) -> tuple[Left, list[tuple[str, list[Piece]]]] | None:
    """A row that is not the session's own: it is over (F10), or being reattached (F12). None otherwise."""
    if inputs.ended is not None:
        ended = inputs.ended
        text = ended.text + (" · ● REC (the recording is kept)" if ended.recording else "")
        hints = [(f"ended{i}", [Piece(h.key, "hint_key", h.action), Piece(f" {h.label}", "hint", h.action)])
                 for i, h in enumerate(ended.hints)]
        return [Piece(text, "danger")], hints
    if inputs.reattach is not None:
        r = inputs.reattach
        left = [Item("warn", ("◐ reattaching",), RANK_PILL),
                Item("warn", (r.why,), RANK_NAME, sep=LEAD_SEP),
                Item("warn", (f"try {r.attempt} in {_seconds(r.seconds)} s",), RANK_FACT, sep=LEAD_SEP)]
        hints = [("esc", [Piece("Esc", "hint_key"), Piece(" stops", "hint")])]
        hints += [h for h in _session_hints(inputs) if h[0] == "menu"]
        return left, hints
    if inputs.reattach_stopped:
        return ([Piece("◌ not reattaching · the session lives on", "warn")],
                [h for h in _session_hints(inputs) if h[0] == "fleet"])
    return None


# -- the right part: the hints --


def _session_hints(inputs: StatusInputs) -> list[tuple[str, list[Piece]]]:
    """The enabled actions' hints, by their name, in display order. Key first (D11)."""
    facts = A.Facts(inputs.state, open_sessions=inputs.open_sessions)
    by_name: dict[str, list[Piece]] = {}
    for action in A.ACTIONS:
        if action.scope != A.SCOPE_SESSION or action.hint is None or A.blocked(action.id, facts):
            continue
        letter = inputs.leader_keys.get(action.id)
        if action.glyph:
            tail = f" {inputs.leader} {letter}" if letter else f" {inputs.leader}"
            by_name[action.id] = [Piece(action.glyph, "glyph", action.id),
                                  Piece(f" {action.hint}", "hint", action.id),
                                  Piece(tail, "hint_key", action.id)]
        else:
            by_name[action.id] = [Piece(f"{inputs.leader} {letter}", "hint_key", action.id),
                                  Piece(f" {action.hint}", "hint", action.id)]
    state = inputs.state
    if (state is not None and state.holds_keyboard and state.stale_state == "pending"
            and state.stale_prompt is not None):
        count = len(state.stale_prompt.get("choices", ()))
        by_name["choose"] = [Piece("1" if count == 1 else f"1-{count}", "hint_key"),
                             Piece(" choose", "hint")]
    return [(name, by_name[name]) for name in ("choose", *DISPLAY_ORDER) if name in by_name]


def _target_hints(target: KeysTarget) -> list[tuple[str, list[Piece]]]:
    return [(f"target{i}", [Piece(hint.key, "hint_key", hint.action),
                            Piece(f" {hint.label}", "hint", hint.action)])
            for i, hint in enumerate(target.hints)]


def _sep(before: Item, item: Item) -> str:
    return LEAD_SEP if before.lead and (item.lead or item.joins_lead) else item.sep


def _lay(items: Sequence[Item], avail: int | None) -> list[Piece]:
    """The items that fit `avail` cells, as pieces; `None` is no limit.

    The survival order, which only ever takes things away as the room shrinks, so nothing comes back when the
    window narrows further: first the rest of the words leave, lowest rank first (the session name, then the
    state word, the keyboard note, the pan range, who types, then the keys-not-sent note); then the core (the
    pill, the key fact, the countdown value, REC, the state warnings) takes its shorter wordings; and only then
    do the warnings, then REC, then the countdown, then the key fact, go. The pill stays. An item is whole or
    gone, and a separator is only ever put between two kept items, so none dangles."""
    pick = {i: 0 for i in range(len(items))}  # item index -> wording (0 the fullest); absent: gone
    if avail is not None:

        def width() -> int:
            total, before = 0, None
            for i, item in enumerate(items):
                if i in pick:
                    total += len(item.variants[pick[i]]) + (len(_sep(before, item)) if before is not None else 0)
                    before = item
            return total

        by_rank = sorted(range(len(items)), key=lambda i: (items[i].rank, i))
        core = [i for i in by_rank if items[i].rank <= RANK_CORE]
        for i in reversed([i for i in by_rank if items[i].rank > RANK_CORE]):
            if width() <= avail:
                break
            del pick[i]
        for i in core:
            while width() > avail and pick[i] < len(items[i].variants) - 1:
                pick[i] += 1
        for i in reversed(core):
            if width() > avail and items[i].rank != RANK_PILL:
                del pick[i]
    pieces: list[Piece] = []
    before: Item | None = None
    for i, item in enumerate(items):
        if i not in pick:
            continue
        if before is not None:
            pieces.append(Piece(_sep(before, item), "text"))
        pieces.append(Piece(item.variants[pick[i]], item.role, item.action))
        before = item
    return pieces


def _fit(left: Left, hints: list[tuple[str, list[Piece]]], drop_order: list[str],
         width: int) -> tuple[list[Piece], list[tuple[str, list[Piece]]]]:
    """Drop hints in `drop_order` until the row fits, never the last one named `menu` (or, in a
    dialog, the last hint); then give the words before them up by the survival order (`_lay`), or, for a
    row of fixed words, cut the last with an ellipsis."""
    hints = list(hints)
    items = bool(left) and isinstance(left[0], Item)

    def hints_width() -> int:
        gaps = len(GAP) * max(len(hints) - 1, 0)
        return (MIN_GAP if hints else 0) + sum(_width(p) for _, p in hints) + gaps

    def total() -> int:
        return (_width(_lay(left, None)) if items else _width(left)) + hints_width()

    for name in drop_order:
        if total() <= width:
            break
        hints = [h for h in hints if h[0] != name]
    if items:
        return _lay(left, width - hints_width()), hints
    left = list(left)
    while total() > width and left:
        last = left[-1]
        room = len(last.text) - (total() - width)
        if room >= 2:
            left[-1] = Piece(last.text[:room - 1] + "…", last.role, last.action)
            break
        if len(left) == 1:
            break
        left.pop()
    return left, hints


def build_pieces(inputs: StatusInputs, width: int) -> list[Piece]:
    """The status row's content at `width` cells: exactly that wide, hints right-aligned, in the chosen glyph set
    (`glyphs.set`): each chrome glyph is replaced by its ASCII stand-in when the operator's terminal needs that.
    One glyph is one cell either way. The row is always one line."""
    compact = is_compact(inputs.chrome, width)
    special = _special(inputs)
    left = special[0] if special is not None else _left(inputs, compact)
    if special is not None:
        hints = special[1]
        drop_order = [name for name, _ in hints[:-1]]
    elif inputs.target is not None:
        hints = _target_hints(inputs.target)
        # A dialog's own hints leave from the left: the key that closes it is the last to go.
        drop_order = [name for name, _ in hints[:-1]]
    else:
        hints = _session_hints(inputs)
        rank = {name: i for i, name in enumerate(inputs.hint_priority)}
        # Lowest priority first; a hint the knob does not name drops before any it does; Menu never.
        drop_order = sorted((name for name, _ in hints if name != "menu"),
                            key=lambda n: rank.get(n, len(rank)), reverse=True)
    left, hints = _fit(left, hints, drop_order, width)
    pieces = list(left)
    used = _width(left) + sum(_width(p) for _, p in hints) + len(GAP) * max(len(hints) - 1, 0)
    pieces.append(Piece(" " * max(width - used, 0), "text"))
    for i, (_, parts) in enumerate(hints):
        if i:
            pieces.append(Piece(GAP, "text"))
        pieces.extend(parts)
    if inputs.glyph_set != glyphs.UNICODE:
        pieces = [replace(piece, text=glyphs.render(piece.text, inputs.glyph_set)) for piece in pieces]
    return pieces


# -- the widget --


class StatusRow(Widget):
    """One line. Content comes from `set_inputs`; a click on a hint or the pill posts `Activated`."""

    DEFAULT_CSS = """
    StatusRow {
        height: 1;
        width: 1fr;
    }
    """
    can_focus = False

    class Activated(Message):
        """A click on a piece that runs an action (a hint, or the keyboard pill)."""

        def __init__(self, action: str) -> None:
            super().__init__()
            self.action = action

    def __init__(self, config: Mapping) -> None:
        super().__init__()
        text, surface = config["theme.text"], config["theme.surface"]
        base = Style(color=text, bgcolor=surface)
        accent = config["theme.accent"]
        self._styles: dict[str, Style] = {
            "text": base,
            "muted": base + Style(color=config["theme.text_muted"]),
            "hint_key": base + Style(color=config["theme.text_muted"]),
            "hint": base,
            "glyph": base + Style(color=accent),
            "keys": base + Style(bold=True),
            "ok": base + Style(color=config["theme.ok"]),
            "warn": base + Style(color=config["theme.warn"]),
            "danger": base + Style(color=config["theme.danger"]),
            "rec": base + Style(color=config["theme.danger"]),
            "pill_type": Style(color=config["theme.on_accent"], bgcolor=accent, bold=True),
            "pill_view": base + Style(color=config["theme.steel"], bold=True),
            "pill_none": base + Style(color=config["theme.text_muted"]),
        }
        self._inputs: StatusInputs | None = None
        self._line: list[Piece] = []

    # -- content --

    def set_inputs(self, inputs: StatusInputs) -> None:
        self._inputs = inputs
        self._layout()

    def _layout(self) -> None:
        width = self.size.width
        if self._inputs is None or width <= 0:
            return
        line = build_pieces(self._inputs, width)
        if line != self._line:
            self._line = line
            self.refresh()

    def on_resize(self, event: events.Resize) -> None:
        self._layout()

    @property
    def _pieces(self) -> list[Piece]:
        return self._line

    @property
    def text(self) -> str:
        """The row as the operator reads it."""
        return "".join(piece.text for piece in self._line)

    def hit_x(self, action: str, after: int = 0) -> int | None:
        """The column of the first cell, at or after column `after`, of the first piece that runs
        `action`, for a test that clicks (the keyboard pill and the Who's here hint run the same action)."""
        x = 0
        for piece in self._pieces:
            if piece.action == action and x >= after:
                return x
            x += len(piece.text)
        return None

    def actions(self) -> list[str]:
        """The action ids a click can reach on this row, left to right, once each."""
        seen: list[str] = []
        for piece in self._pieces:
            if piece.action is not None and piece.action not in seen:
                seen.append(piece.action)
        return seen

    # -- painting and clicking --

    def render_line(self, y: int) -> Strip:
        if y >= 1:
            return Strip.blank(self.size.width)
        segments = [Segment(piece.text, self._styles[piece.role]) for piece in self._line if piece.text]
        return Strip(segments).adjust_cell_length(self.size.width, self._styles["text"])

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """A hint's tooltip says what the action is in full, whatever its shortened label (D13)."""
        tip = None
        x = 0
        for piece in self._line:
            if x <= event.x < x + len(piece.text):
                action = A.BY_ID.get(piece.action) if piece.action else None
                if action is not None:
                    tip = f"{action.label} · {action.description}"
                break
            x += len(piece.text)
        set_tip(self, tip, self._inputs.glyph_set if self._inputs is not None else glyphs.UNICODE)

    def on_click(self, event: events.Click) -> None:
        if not mouse_on(self):
            return
        x = 0
        for piece in self._line:
            if x <= event.x < x + len(piece.text):
                if piece.action is not None:
                    event.stop()
                    self.post_message(self.Activated(piece.action))
                return
            x += len(piece.text)
