"""The one status row (design D43, F12): who types, which session, its state, the actions you can take
now as clickable hints, always ending in the Menu and the Fleet.

Two parts, kept apart so the layout is a pure function a test can read at any width:

* `build_pieces(inputs, width)` turns the models into a list of `Piece`s that fill exactly `width` cells.
  Hints drop from the right by `status.hint_priority` as the width shrinks and `≡ Menu ^\\` is the last
  to go (D43); the words after the pill are cut with an ellipsis only when even the Menu hint leaves
  no room for them.
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
from ..model.countdown import countdown_text
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

# The stale notice line, verbatim: stale-screen rule 18 and ws-frames `stale` fix it, so no surface
# softens it. F12's tails follow it.
STALE_NOTICE = "TSB: lost output - the screen may be stale until a choice is made."

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
    drop: int = 0  # 0: kept; else the order it leaves when the row is too narrow (highest first)
    keep: bool = False  # never cut or dropped to make the row fit (the stale notice line, rule 18)


def _width(pieces: Sequence[Piece]) -> int:
    return sum(len(p.text) for p in pieces)  # every glyph here is one cell wide


# -- the left part: the pill and what follows it --


def _kind(state: AttachmentState) -> str:
    if state.holds_keyboard:
        return "type"
    return "none" if state.holder is None else "view"


def _pill(kind: str, compact: bool) -> Piece:
    word = {"type": "you type", "view": "view only", "none": "nobody types"}[kind]
    text = f"‹{word}›" if compact else f"‹ {word} ›"
    return Piece(text, f"pill_{kind}", "who")


def _name(client: Mapping | None) -> str:
    return "nobody" if client is None else str(client.get("user") or client.get("client_id"))


def _last_token(state: AttachmentState) -> dict | None:
    return state.token_history[-1] if state.token_history else None


def _state_notes(inputs: StatusInputs, state: AttachmentState) -> list[Piece]:
    """What is wrong with the session now, each its own phrase, in the order a reader needs it: the stale
    screen, the data path, a refusal, the countdown. Empty when nothing is. The stale notice line is whole
    or absent (stale-screen rule 18); only its tail gives way when the row is narrow."""
    notes: list[Piece] = []
    stale = state.stale_state == "pending"
    if stale:
        if state.holds_keyboard:
            tail = "nobody's input goes through until you choose"
        elif state.holder is None:
            tail = "nobody holds the keyboard · input refused"
        else:
            tail = f"{_name(state.holder)} is choosing · input refused"
        notes.append(Piece(f"  ▲ {STALE_NOTICE}", "warn", keep=True))
        notes.append(Piece(f" · {tail}", "warn", drop=9))
    restoring = state.data_path == "lost"
    reason = state.last_input_refused if state.refusal_active else None
    if reason == "replaying":
        restoring = True
    if restoring:
        notes.append(Piece("  " + RESTORING, "warn"))
    if reason is not None and reason != "replaying" and not (reason == "stale" and stale):
        notes.append(Piece("  " + REFUSAL_TEXT.get(reason, UNKNOWN_REFUSAL), "warn"))
    if state.ending:
        text = countdown_text(state.ends_by, state.ends_at, inputs.now)
        if text is not None:
            notes.append(Piece("  " + text, "warn"))
    return notes


def _body(inputs: StatusInputs, state: AttachmentState, kind: str, compact: bool) -> list[Piece]:
    """The words after the pill, in F12's order: who types (when it is not you) and why, the session,
    its state and REC, how the keyboard got here, the pan range, a note on keys not sent."""
    last = _last_token(state)
    pieces: list[Piece] = []
    lead: list[str] = []
    if kind == "view":
        if last is not None and last["reason"] == "takeover" and last["holder"] == state.holder:
            lead.append(f"{_name(state.holder)} took the keyboard (owner)")
        else:
            lead.append(f"{_name(state.holder)} types")
        if state.is_owner_client:
            lead.append("you own it: typing takes it")
        elif not state.may_control(state.client_id):
            lead.append("your right is read-only")
    elif kind == "none":
        if last is not None and last["reason"] == "owner":
            lead.append(f"{state.owner or 'the owner'} put the keyboard aside")
        else:
            lead.append("nobody has the keyboard")
    for text in lead:
        pieces.append(Piece(text + " · ", "text"))
    pieces.append(Piece(inputs.session, "text"))
    recording = state.recording or {}
    notes = _state_notes(inputs, state)
    if notes:
        pieces.extend(notes)
    elif state.session.get("state") == "live":
        if not compact:
            pieces.append(Piece("  ● live", "ok", drop=3))
    elif state.session.get("state"):
        pieces.append(Piece(f"  ◌ {state.session['state']}", "warn"))
    if recording.get("active"):
        pieces.append(Piece("  ● REC", "rec"))
    if kind == "type" and last is not None and last["reason"] == "takeover" and last["previous"]:
        # The note leaves before the pan range: the range is what tells the operator the screen is wider than their
        # window, and it stays whenever the row has room for it (a phone's 65 columns).
        pieces.append(Piece(f"  you took the keyboard from {_name(last['previous'])}", "text", drop=2))
    if inputs.keys_not_sent:
        pieces.append(Piece("  " + NOT_SENT, "warn"))
    if inputs.range_label:
        pieces.append(Piece("  " + inputs.range_label, "muted", drop=1))
    return pieces


NO_SESSION = "no session open"


def _left(inputs: StatusInputs, compact: bool) -> list[Piece]:
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
        return [_pill(kind, compact), Piece("  ", "text"), Piece(inputs.recording_bar, "rec")]
    return [_pill(kind, compact), Piece("  ", "text"), *_body(inputs, state, kind, compact)]


def _seconds(value: float) -> str:
    """`2` for two seconds, `0.5` under one: the wait before a reattach attempt."""
    if value >= 1:
        return str(int(value + 0.999))
    return f"{max(value, 0.0):.1f}"


def _special(inputs: StatusInputs) -> tuple[list[Piece], list[tuple[str, list[Piece]]]] | None:
    """A row that is not the session's own: it is over (F10), or being reattached (F12). None otherwise."""
    if inputs.ended is not None:
        ended = inputs.ended
        text = ended.text + (" · ● REC (the recording is kept)" if ended.recording else "")
        hints = [(f"ended{i}", [Piece(h.key, "hint_key", h.action), Piece(f" {h.label}", "hint", h.action)])
                 for i, h in enumerate(ended.hints)]
        return [Piece(text, "danger")], hints
    if inputs.reattach is not None:
        r = inputs.reattach
        left = [Piece(f"◐ reattaching · {r.why} · try {r.attempt} in {_seconds(r.seconds)} s", "warn")]
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


def _fit(left: list[Piece], hints: list[tuple[str, list[Piece]]], drop_order: list[str],
         width: int) -> tuple[list[Piece], list[tuple[str, list[Piece]]]]:
    """Drop hints in `drop_order` until the row fits, never the last one named `menu` (or, in a
    dialog, the last hint); then cut the left part's words with an ellipsis if it still does not."""
    hints = list(hints)

    def total() -> int:
        gaps = len(GAP) * max(len(hints) - 1, 0)
        return _width(left) + (MIN_GAP if hints else 0) + sum(_width(p) for _, p in hints) + gaps

    for name in drop_order:
        if total() <= width:
            break
        hints = [h for h in hints if h[0] != name]
    left = list(left)
    while total() > width:
        optional = [i for i, p in enumerate(left) if p.drop and not p.keep]
        if not optional:
            break
        left.pop(max(optional, key=lambda i: left[i].drop))
    while total() > width and left:
        last = left[-1]
        if last.keep:
            break  # the stale notice line is never cut: `build_lines` gives the row a second line instead
        room = len(last.text) - (total() - width)
        if room >= 2:
            left[-1] = Piece(last.text[:room - 1] + "…", last.role, last.action, last.drop)
            break
        if len(left) == 1:
            break
        left.pop()
    return left, hints


def build_pieces(inputs: StatusInputs, width: int) -> list[Piece]:
    """The status row's content at `width` cells: exactly that wide, hints right-aligned."""
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
        if inputs.state is not None and inputs.state.stale_state == "pending":
            # While a choice is pending the hints give way first, the Menu's included, then the notice's
            # tail: the notice line itself stays (stale-screen rule 18).
            drop_order.append("menu")
    left, hints = _fit(left, hints, drop_order, width)
    pieces = list(left)
    used = _width(left) + sum(_width(p) for _, p in hints) + len(GAP) * max(len(hints) - 1, 0)
    pieces.append(Piece(" " * max(width - used, 0), "text"))
    for i, (_, parts) in enumerate(hints):
        if i:
            pieces.append(Piece(GAP, "text"))
        pieces.extend(parts)
    return pieces


def build_lines(inputs: StatusInputs, width: int) -> list[list[Piece]]:
    """The status row in the chosen glyph set (`glyphs.set`): the lines of `_build_lines`, each chrome glyph
    replaced by its ASCII stand-in when the operator's terminal needs that. One glyph is one cell either way."""
    lines = _build_lines(inputs, width)
    if inputs.glyph_set == glyphs.UNICODE:
        return lines
    return [[replace(piece, text=glyphs.render(piece.text, inputs.glyph_set)) for piece in line]
            for line in lines]


def _build_lines(inputs: StatusInputs, width: int) -> list[list[Piece]]:
    """The status row as one line, or two while a stale choice is pending and the pill with the notice line
    do not fit one: line one keeps the pill and the session, and the notice follows whole on as many lines as it needs. Back to one line
    when the choice is made, because the notice is gone."""
    pieces = build_pieces(inputs, width)
    if sum(len(p.text) for p in pieces) <= width or not any(p.keep for p in pieces):
        return [pieces]
    notice = next(p for p in pieces if p.keep)
    first = [p for p in pieces if not p.keep and p.drop != 9 and p.text.strip()]
    first, _ = _fit(first, [], [], width)  # the existing rule for the words around the pill: cut with an ellipsis
    first.append(Piece(" " * max(width - _width(first), 0), "text"))
    # The notice on lines of its own, whole: wrapped at spaces when it is wider than the row, every word kept.
    wrapped: list[str] = []
    current = ""
    for word in notice.text.split():
        if current and len(current) + 1 + len(word) > width:
            wrapped.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    wrapped.append(current)
    rest = [[Piece(text, notice.role), Piece(" " * max(width - len(text), 0), "text")] for text in wrapped]
    return [first, *rest]


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
        self._lines: list[list[Piece]] = [[]]

    # -- content --

    def set_inputs(self, inputs: StatusInputs) -> None:
        self._inputs = inputs
        self._layout()

    def _layout(self) -> None:
        width = self.size.width
        if self._inputs is None or width <= 0:
            return
        lines = build_lines(self._inputs, width)
        if lines != self._lines:
            self._lines = lines
            self.styles.height = len(lines)
            self.refresh()

    def on_resize(self, event: events.Resize) -> None:
        self._layout()

    @property
    def _pieces(self) -> list[Piece]:
        return self._lines[0]

    @property
    def text(self) -> str:
        """The row as the operator reads it, one line per row of the widget."""
        return "\n".join("".join(piece.text for piece in line) for line in self._lines)

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
        if y >= len(self._lines):
            return Strip.blank(self.size.width)
        segments = [Segment(piece.text, self._styles[piece.role]) for piece in self._lines[y] if piece.text]
        return Strip(segments).adjust_cell_length(self.size.width, self._styles["text"])

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """A hint's tooltip says what the action is in full, whatever its shortened label (D13)."""
        tip = None
        x = 0
        for piece in self._lines[min(event.y, len(self._lines) - 1)]:
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
        for piece in self._lines[min(event.y, len(self._lines) - 1)]:
            if x <= event.x < x + len(piece.text):
                if piece.action is not None:
                    event.stop()
                    self.post_message(self.Activated(piece.action))
                return
            x += len(piece.text)
