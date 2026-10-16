"""The prompts and end states a session raises (design §2.7: F7 stale, F8 quiet prompt, F9 operator alert,
F10 ended; D22, D23, D35, D36).

Two kinds, by whether they take the keyboard (D23):

* **Blocking** — the stale choice. It is a panel over the canvas for the keyboard holder only (stale-screen
  step 4): the prompt's text and each choice's text exactly as the core sent them (rule 18), a button each,
  `1`-`n` answers, and keys are ignored for `prompts.input_guard_ms` after it opens so a keystroke aimed at
  the session cannot choose by accident. Only a digit that names a choice is ever taken; every other key
  goes to the session as ever (D17), where the core refuses it while the choice is pending.
* **Not blocking** — the quiet prompt and the operator alerts. They never capture keys: their buttons are
  plain clickable text that cannot hold focus, so a click never moves the session's keys away, and `^\\ a`
  reaches the same answer from the keyboard. The TUI raises no question of its own (quiet-prompt rule 9);
  these show what the core asked.

The ended dialog (F10) and the two small modals the answers need are ordinary modal screens.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Static

from ..model.alerts import Alert
from ..model.attachment import AttachmentState
from ..model.countdown import parse_timestamp
from ..model.fleet import display_id
from ..widgets.status_row import Hint, KeysTarget

# The clock the input guard reads; a test replaces it to move past the guard without waiting.
monotonic = time.monotonic

# -- the stale choice (F7) --

STALE_KEYS_NOTE = "Your choice is recorded as TSB's own action."
GAP_SENTENCE = "output was lost after seq {seq} (a gap)"
GRID_LOST_SENTENCE = "the screen could not be rebuilt; no output was lost"

# A refused `stale_answer` (stale-screen "Refusals"), in the TUI's own words.
STALE_REFUSALS = {
    "not_pending": "Refused: no choice is waiting any more.",
    "not_allowed": "Refused: only the keyboard holder can choose.",
}


def stale_cause_sentence(state: AttachmentState) -> str:
    if state.stale_cause == "gap" and state.stale_gap_seq is not None:
        return GAP_SENTENCE.format(seq=state.stale_gap_seq)
    return GRID_LOST_SENTENCE


def guard_note(count: int, guard_ms: int) -> str:
    keys = "1" if count == 1 else f"1-{count}"
    seconds = f"{guard_ms / 1000:g} s"
    return f"Click one, or press {keys}. Keys are ignored for {seconds} after this opens."


class Choice(Static):
    """Plain text that takes a click and never the focus: pressing it must not move the session's keys."""

    can_focus = False

    class Pressed(Message):
        def __init__(self, choice: Choice) -> None:
            super().__init__()
            self.choice = choice

    def __init__(self, text: str, key: str, **kwargs: Any) -> None:
        super().__init__(text, markup=False, **kwargs)
        self.key = key

    def on_click(self, event: Any) -> None:
        event.stop()
        self.post_message(self.Pressed(self))


class StalePanel(Vertical):
    DEFAULT_CSS = """
    StalePanel { width: 100%; max-width: 90; height: auto; border: round $warning;
                 background: $surface; padding: 0 1; }
    StalePanel .stale-title { text-style: bold; }
    StalePanel Choice { height: auto; }
    StalePanel Choice:hover { background: $surface-3; }
    StalePanel .stale-note { color: $text-muted; }
    StalePanel .stale-refused { color: $warning; }
    """
    MAX_CHOICES = 4

    def __init__(self, terminal: Any) -> None:
        super().__init__()
        self._terminal = terminal
        self.prompt_id: str | None = None
        self.opened_at = 0.0
        self.answered = False
        self._choices: list[str] = []

    def compose(self) -> ComposeResult:
        yield Static("", id="stale-title", classes="stale-title", markup=False)
        yield Static("", id="stale-where", markup=False)
        yield Static("", markup=False)
        for index in range(self.MAX_CHOICES):
            yield Choice("", f"stale:{index}", id=f"stale-choice-{index}")
        yield Static("", markup=False)
        yield Static("", id="stale-guard", classes="stale-note", markup=False)
        yield Static(STALE_KEYS_NOTE, classes="stale-note", markup=False)
        yield Static("", id="stale-refused", classes="stale-refused", markup=False)

    def open_for(self, state: AttachmentState, prompt: dict[str, Any], label: str, guard_ms: int) -> None:
        """A prompt arrived, or a new one replaced the old (a token move): show it, and restart the guard."""
        self.prompt_id = prompt["prompt_id"]
        self.opened_at = monotonic()
        self.answered = False
        choices = list(prompt.get("choices", ()))[: self.MAX_CHOICES]
        self._choices = [c["choice"] for c in choices]
        self.query_one("#stale-title", Static).update(prompt["text"])
        self.query_one("#stale-where", Static).update(
            f"{label} · {display_id(state.session.get('id', ''), [state.session.get('id', '')])} · "
            f"{stale_cause_sentence(state)}")
        for index in range(self.MAX_CHOICES):
            widget = self.query_one(f"#stale-choice-{index}", Choice)
            if index < len(choices):
                widget.update(f"[{index + 1}] {choices[index]['text']}")
                widget.display = True
            else:
                widget.display = False
        self.query_one("#stale-guard", Static).update(guard_note(len(choices), guard_ms))
        self.query_one("#stale-refused", Static).update("")

    @property
    def choices(self) -> list[str]:
        return list(self._choices)

    def guarded(self, guard_ms: int) -> bool:
        return monotonic() - self.opened_at < guard_ms / 1000.0

    def refused(self, reason: str) -> None:
        self.answered = False
        self.query_one("#stale-refused", Static).update(STALE_REFUSALS.get(reason, f"Refused: {reason}"))

    def on_choice_pressed(self, message: Choice.Pressed) -> None:
        message.stop()
        index = int(message.choice.key.split(":")[1])
        if index < len(self._choices):
            self._terminal.run_worker(self._terminal.answer_stale(self._choices[index]), exclusive=False)


# -- the quiet prompt (F8) --

QUIET_DETAIL = "Something typed got no answer."


class QuietBox(Vertical):
    """The question the core put to the owner's clients: kill the session? It never captures keys."""

    DEFAULT_CSS = """
    QuietBox { dock: bottom; layer: overlay; width: 100%; max-width: 70; height: auto;
               margin-bottom: 1; border: round $warning; background: $surface; padding: 0 1; }
    QuietBox .quiet-title { text-style: bold; }
    QuietBox Horizontal { height: 1; }
    QuietBox Choice { width: auto; margin-right: 2; }
    QuietBox Choice:hover { background: $surface-3; }
    """

    def __init__(self, terminal: Any) -> None:
        super().__init__()
        self._terminal = terminal
        self.prompt_id: str | None = None

    def compose(self) -> ComposeResult:
        if self.app.cfg["prompts.capture_unblocking"]:
            # Config refuses `true` at load (no design says what a capturing box would do); this keeps
            # the box honest if that ever changes before one is built.
            raise ValueError("prompts.capture_unblocking: only false is built")
        yield Static("", id="quiet-title", classes="quiet-title", markup=False)
        yield Static("", id="quiet-text", markup=False)
        yield Static("", id="quiet-holder", markup=False)
        with Horizontal():
            yield Choice("[ Keep it ]", "quiet:keep", id="quiet-keep")
            yield Choice("[ Kill it… ]", "quiet:kill", id="quiet-kill")
            yield Choice("[ Take back ]", "quiet:take_back", id="quiet-take-back")

    def show_for(self, target: str, prompt: dict[str, Any], holder: str | None, can_take_back: bool) -> None:
        self.prompt_id = prompt["prompt_id"]
        self.query_one("#quiet-title", Static).update(f"{target} asks you")
        self.query_one("#quiet-text", Static).update(f"{prompt['text']}  {QUIET_DETAIL}")
        holder_line = self.query_one("#quiet-holder", Static)
        holder_line.update(f"{holder} holds the keyboard." if holder else "")
        holder_line.display = holder is not None
        self.query_one("#quiet-take-back", Choice).display = can_take_back

    def on_choice_pressed(self, message: Choice.Pressed) -> None:
        message.stop()
        key = message.choice.key
        if key == "quiet:keep":
            self._terminal.run_worker(self._terminal.answer_quiet(False), exclusive=False)
        elif key == "quiet:kill":
            self._terminal.ask_kill()
        elif key == "quiet:take_back":
            self._terminal.run_worker(self._terminal.run_action_id("take_back"), exclusive=False)


class AnswerScreen(ModalScreen[None]):
    """`^\\ a`: the quiet prompt's two answers as a small dialog, for the keyboard (G3)."""

    DEFAULT_CSS = """
    AnswerScreen { align: center middle; }
    #answer-panel { width: 100%; max-width: 60; height: auto; border: round $warning;
                    background: $surface; padding: 0 1; }
    #answer-buttons { height: 1; }
    #answer-buttons Choice { width: auto; margin-right: 2; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close"),
        Binding("k", "keep", "Keep it", show=False),
        Binding("x", "kill", "Kill it…", show=False),
    ]

    def __init__(self, terminal: Any, target: str, prompt: dict[str, Any]) -> None:
        super().__init__()
        self._terminal = terminal
        self._target = target
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="answer-panel"):
            yield Static(f"{self._target} asks you", id="answer-title", markup=False)
            yield Static(f"{self._prompt['text']}  {QUIET_DETAIL}", markup=False)
            with Horizontal(id="answer-buttons"):
                yield Choice("[ Keep it ]", "keep", id="answer-keep")
                yield Choice("[ Kill it… ]", "kill", id="answer-kill")

    def on_mount(self) -> None:
        self._terminal.dialogs.append(self)
        self._terminal.refresh_status()

    def on_unmount(self) -> None:
        if self in self._terminal.dialogs:
            self._terminal.dialogs.remove(self)
        self._terminal.refresh_status()

    def sync(self) -> None:
        """The question can be answered elsewhere (the box) or withdrawn by the session ending."""
        if self._terminal.pump is None or self._terminal.pump.state.quiet_prompt is None:
            self.dismiss(None)

    def status_target(self) -> KeysTarget:
        return KeysTarget("the question", (Hint("k", "Keep it"), Hint("x", "Kill it…"), Hint("Esc", "close")))

    def on_choice_pressed(self, message: Choice.Pressed) -> None:
        message.stop()
        if message.choice.key == "keep":
            self.action_keep()
        else:
            self.action_kill()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_keep(self) -> None:
        self.dismiss(None)
        self._terminal.run_worker(self._terminal.answer_quiet(False), exclusive=False)

    def action_kill(self) -> None:
        self._terminal.ask_kill()


class ConfirmKillScreen(ModalScreen[bool]):
    """Kill it… asks once more (F8, G4): ending a session is not undone."""

    DEFAULT_CSS = """
    ConfirmKillScreen { align: center middle; }
    #kill-panel { width: 100%; max-width: 60; height: auto; border: round $error;
                  background: $surface; padding: 0 1; }
    #kill-buttons { height: 1; }
    #kill-buttons Choice { width: auto; margin-right: 2; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, terminal: Any, target: str) -> None:
        super().__init__()
        self._terminal = terminal
        self._target = target

    def compose(self) -> ComposeResult:
        with Vertical(id="kill-panel"):
            yield Static(f"Kill {self._target}? This ends it for everyone.", id="kill-text", markup=False)
            with Horizontal(id="kill-buttons"):
                yield Choice("[ Kill it ]", "kill", id="kill-confirm")
                yield Choice("[ Keep it ]", "keep", id="kill-keep")

    def on_mount(self) -> None:
        self._terminal.dialogs.append(self)
        self._terminal.refresh_status()

    def on_unmount(self) -> None:
        if self in self._terminal.dialogs:
            self._terminal.dialogs.remove(self)
        self._terminal.refresh_status()

    def sync(self) -> None:
        """Nothing here follows the models: the question is asked once."""

    def status_target(self) -> KeysTarget:
        return KeysTarget("confirm", (Hint("Esc", "cancel"),))

    def on_choice_pressed(self, message: Choice.Pressed) -> None:
        message.stop()
        self.dismiss(message.choice.key == "kill")

    def action_cancel(self) -> None:
        self.dismiss(None)


# -- operator alerts (F9) --

ALERT_ADMIN_LINE = "Acting on it is host administration, outside TSB."
SUPERVISOR_UNTOUCHED = "Its workers and their sessions are untouched."
_KIND_LABELS = {
    "supervisor_unresponsive": "supervisor not answering",
    "worker_unkillable": "worker will not die",
    "vt_unavailable": "VT worker unavailable",
}
_ID_PREFIXES = {"alert": "al.", "supervisor": "sv.", "worker": "wk."}


def short_id(kind: str, value: str) -> str:
    """`al.` / `sv.` / `wk.` and the first four characters of the id (D27's style for sessions)."""
    prefix = _ID_PREFIXES[kind]
    return prefix + value.removeprefix(prefix)[:4]


def alert_label(alert: Alert) -> str:
    return _KIND_LABELS.get(alert.kind) or (alert.kind.replace("_", " ") or "alert")


def alert_text(alert: Alert) -> str:
    """The one line an alert says, naming TSB's components by id only (Contract §4; V11)."""
    if alert.kind == "supervisor_unresponsive":
        supervisor = alert.subjects.get("supervisor_id")
        return f"A supervisor stopped answering: {short_id('supervisor', supervisor) if supervisor else '?'}."
    if alert.kind == "worker_unkillable":
        worker = alert.subjects.get("worker_id")
        return f"A worker will not die: {short_id('worker', worker) if worker else '?'}."
    return alert.text


def alert_raised(alert: Alert) -> str:
    when = parse_timestamp(alert.at)
    clock = when.astimezone().strftime("%H:%M:%S") if isinstance(when, datetime) else alert.at
    return f"raised {clock} · alert {short_id('alert', alert.alert_id)}"


def alert_lines(alert: Alert) -> list[str]:
    lines = [alert_text(alert)]
    if alert.kind == "supervisor_unresponsive":
        lines.append(SUPERVISOR_UNTOUCHED)
    lines += [ALERT_ADMIN_LINE, alert_raised(alert)]
    return lines


class AlertPopup(Vertical):
    DEFAULT_CSS = """
    AlertPopup { width: 100%; height: auto; border: round $warning; background: $surface; padding: 0 1; }
    AlertPopup .alert-title { text-style: bold; }
    AlertPopup Horizontal { height: 1; }
    AlertPopup Choice { width: auto; margin-right: 2; }
    AlertPopup Choice:hover { background: $surface-3; }
    """

    def __init__(self, alert: Alert) -> None:
        super().__init__()
        self.alert = alert
        self.border_title = f"▲ TSB alert · {alert_label(alert)}"

    def compose(self) -> ComposeResult:
        for line in alert_lines(self.alert):
            yield Static(line, markup=False)
        with Horizontal():
            yield Choice("[ Dismiss here ]", "alert:dismiss")
            yield Choice("[ All alerts ]", "alert:all")


class AlertStack(Vertical):
    """The popups for the alerts not dismissed, newest on top. Sits at the top right and takes no keys."""

    DEFAULT_CSS = """
    AlertStack { dock: right; layer: overlay; width: 60; height: auto; margin-top: 1; }
    """

    def __init__(self, terminal: Any) -> None:
        super().__init__()
        self._terminal = terminal
        self.shown: list[str] = []

    def show(self, alerts: list[Alert]) -> None:
        """Show exactly these alerts, newest first. A popup already up is left alone, so a click on it is
        never lost to a rebuild; a new one goes on top."""
        wanted = {a.alert_id: a for a in alerts}
        for popup in list(self.query(AlertPopup)):
            if popup.alert.alert_id not in wanted:
                popup.remove()
        have = {p.alert.alert_id for p in self.query(AlertPopup) if p.is_attached}
        for alert in reversed(alerts):  # oldest first, each put on top: the newest ends up first
            if alert.alert_id not in have:
                self.mount(AlertPopup(alert), before=0 if self.children else None)
        self.shown = [a.alert_id for a in alerts]
        self.display = bool(alerts)

    def on_choice_pressed(self, message: Choice.Pressed) -> None:
        message.stop()
        popup = next(a for a in message.choice.ancestors if isinstance(a, AlertPopup))
        if message.choice.key == "alert:dismiss":
            self.app.dismiss_alert(popup.alert)
        else:
            self.app.push_screen(AlertsScreen(self._terminal))


class AlertsScreen(ModalScreen[None]):
    """All alerts: one line per active alert, until the Alerts tab exists (phase 8)."""

    DEFAULT_CSS = """
    AlertsScreen { align: center middle; }
    #alerts-panel { width: 100%; max-width: 100; height: auto; border: round $warning;
                    background: $surface; padding: 0 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "close", "Close")]

    def __init__(self, terminal: Any) -> None:
        super().__init__()
        self._terminal = terminal

    def compose(self) -> ComposeResult:
        with Vertical(id="alerts-panel"):
            yield Static("", id="alerts-list", markup=False)

    def on_mount(self) -> None:
        self._stop = self.app.watch_alerts(self.sync)
        self._terminal.dialogs.append(self)
        self.sync()
        self._terminal.refresh_status()

    def on_unmount(self) -> None:
        self._stop()
        if self in self._terminal.dialogs:
            self._terminal.dialogs.remove(self)
        self._terminal.refresh_status()

    def sync(self) -> None:
        active = list(self.app.alerts.active.values())
        text = "\n".join(
            f"▲ {alert_label(a)} · {alert_text(a)} {alert_raised(a)}" for a in reversed(active)
        ) or "No active alerts."
        self.query_one("#alerts-list", Static).update(f"All alerts\n{text}")
        self.border_title = "▲ TSB alerts"

    def status_target(self) -> KeysTarget:
        return KeysTarget("alerts", (Hint("Esc", "close"),))

    def action_close(self) -> None:
        self.dismiss(None)


# -- an end state (F10) --

KILLED_BODY = ("Ended by TSB or through it: one of its timers, its owner or an admin.\n"
               "Which one is not sent to clients yet (idle, unattended, orphan, owner, admin).")
LOST_BODY = "The connection to the broker was lost; the session lives on."
_LIVE_RECORD_STATES = ("live", "detached", "unreachable")
TERMINATED_BODY = "Ended from outside TSB: by the target, the network or its worker."


@dataclass(frozen=True)
class EndedView:
    """What the end dialog and the status row say about a session that is over for this client."""

    title: str
    body: str
    row: str  # the words after `✕ `: `ended · rtr-core-01 · killed`
    ended: bool  # the session itself ended; else only this client's place in it did


def ended_view(state: AttachmentState, target: str) -> EndedView | None:
    """The end state for the close the pump stopped on (DT13's table), or None when there is none."""
    close = state.close
    if close is None:
        return None
    show, code = close.show, state.close_code
    if show in ("ended_killed", "ended_terminated"):
        word = "killed" if show == "ended_killed" else "terminated"
        return _ended(word, target, f"Session ended · {word} (close {code})")
    if show in ("session_gone", "transport_lost"):
        word = state.record_state
        if word in ("killed", "terminated"):
            return _ended(word, target, f"Session ended · {word}")
        if word is not None and word not in _LIVE_RECORD_STATES:
            return EndedView(f"Session ended · {word}", "", f"ended · {target} · {word}", True)
        if show == "session_gone":
            return EndedView("Session ended", "The session no longer exists.",
                             f"ended · {target} · gone", True)
    if show == "removed":
        return EndedView("Removed by the owner",
                         "You were removed from this session; it lives on without this client.",
                         f"{target} · removed by the owner", False)
    if show == "may_not_join":
        return EndedView("You may no longer join this session",
                         "The session lives on without this client.",
                         f"{target} · you may no longer join", False)
    if show == "attach_defect":
        return EndedView("Could not stay attached", "The broker refused the attach as malformed (close 4000).",
                         f"{target} · could not stay attached", False)
    if show == "ticket_refused":
        return EndedView("Could not stay attached", "The broker refused the attach ticket twice.",
                         f"{target} · could not stay attached", False)
    # A socket that died and could not be brought back while the record says the session lives.
    return EndedView("Could not stay attached", LOST_BODY, f"{target} · could not stay attached", False)


def _ended(word: str, target: str, title: str) -> EndedView:
    body = KILLED_BODY if word == "killed" else TERMINATED_BODY
    return EndedView(title, body, f"ended · {target} · {word}", True)


class EndedScreen(ModalScreen[None]):
    """The end dialog: the last screen stays behind it, dimmed (D36). Esc closes the dialog and leaves the
    row, with `c` and `^\\ f` still there."""

    DEFAULT_CSS = """
    EndedScreen { align: center middle; }
    #ended-panel { width: 100%; max-width: 90; height: auto; border: round $error;
                   background: $surface; padding: 0 1; }
    #ended-title { text-style: bold; }
    #ended-buttons { height: 1; margin-top: 1; }
    #ended-buttons Choice { width: auto; margin-right: 2; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close"),
        Binding("c", "new_session", "New session here", show=False),
        Binding("n", "next_session", "Next open session", show=False),
    ]

    def __init__(self, terminal: Any, view: EndedView, target: str) -> None:
        super().__init__()
        self._terminal = terminal
        self._view = view
        self._target = target

    def compose(self) -> ComposeResult:
        with Vertical(id="ended-panel"):
            yield Static(self._view.title, id="ended-title", markup=False)
            if self._view.body:
                yield Static(self._view.body, id="ended-body", markup=False)
            with Horizontal(id="ended-buttons"):
                yield Choice(f"[ New session on {self._target} ]", "new", id="ended-new")
                if len(self._terminal.slots) > 1:
                    yield Choice("[ Next open session ]", "next", id="ended-next")
                yield Choice("[ Fleet sidebar ]", "fleet", id="ended-fleet")

    def on_mount(self) -> None:
        self._terminal.dialogs.append(self)
        self._terminal.refresh_status()

    def on_unmount(self) -> None:
        if self in self._terminal.dialogs:
            self._terminal.dialogs.remove(self)
        self._terminal.refresh_status()

    def sync(self) -> None:
        """The end does not change."""

    def status_target(self) -> KeysTarget:
        # The ended row (F10) has its own hints and takes the place of `keys →`; this is only the fallback.
        return KeysTarget("the end", (Hint("c", "New session here"), Hint("Esc", "close")))

    def on_choice_pressed(self, message: Choice.Pressed) -> None:
        message.stop()
        if message.choice.key == "new":
            self.action_new_session()
        elif message.choice.key == "next":
            self.dismiss(None)
            self._terminal.run_worker(self._terminal.run_action_id("next"), exclusive=False)
        else:
            self.dismiss(None)
            self._terminal.open_sidebar()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_new_session(self) -> None:
        self.dismiss(None)
        self._terminal.run_worker(self._terminal.run_action_id("new_session"), exclusive=False)

    def action_next_session(self) -> None:
        if len(self._terminal.slots) > 1:
            self.dismiss(None)
            self._terminal.run_worker(self._terminal.run_action_id("next"), exclusive=False)


class StaleLayer(Container):
    """Centres the stale panel over the canvas. Shown only while a choice is waiting for this client."""

    DEFAULT_CSS = """
    StaleLayer { layer: overlay; width: 100%; height: 100%; align: center middle; display: none; }
    """
