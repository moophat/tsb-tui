"""The gate before an attach (design G2, D24): what the operator must see before the first attach to a session
in this TUI run — the recording text exactly as the record carries it (session-attach CR1; open_decisions §5,
LOCKED), and a first-use host key with its tick (session-open step 22). Only the create response carries a host
key, so a re-attach never shows one.

The recording text is a warning, not a question: it is shown and nothing has to be ticked for it. Only a host
key needs a tick, and Attach stays greyed until it is given. Later attaches to the same session in this run
skip the gate and show the text in the status row for `gates.recording_bar_s` instead; with
`gates.recording = "every-attach"` the gate shows every time. `open_attach` is the one place that decides.
"""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Static

from ..model.fleet import display_id

HOSTKEY_TICK = "I checked the host key"
GATE_KEYS = "keys → the gate   Space tick   ⏎ Attach   Esc Back"


def open_attach(app: Any, session: dict, hostkey: dict | None = None) -> None:
    """Open `session` in the workspace: through the gate or straight to the terminal. The gate is for the first
    attach to this session in this run, a first-use host key, or every attach when `gates.recording` says so
    (D24); `TerminalScreen.open_session` is the one place that decides."""
    app.workspace().open_session(session, hostkey)


class AttachConfirmScreen(ModalScreen[bool]):
    """The gate over the workspace (mockup G2): the rail stays on screen, and the session is attached only when
    this dialog returns True."""

    DEFAULT_CSS = """
    AttachConfirmScreen { align: center middle; }
    #attach-confirm-keys { dock: bottom; width: 100%; color: $text-muted; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "back", "Back"),
        Binding("space", "tick", "Tick", show=False),
        Binding("enter", "attach", "Attach", show=False),
    ]

    def __init__(self, session: dict, hostkey: dict | None = None) -> None:
        super().__init__()
        self._session = session
        self._hostkey = hostkey
        self._recording = bool((session.get("recording") or {}).get("active"))

    @property
    def session_id(self) -> str:
        return self._session["id"]

    def compose(self) -> ComposeResult:
        if not self.app.cfg["gates.hostkey_tick"]:
            # Config refuses `false` at load: a first-use host key must be acknowledged (session-open step 22).
            raise ValueError("gates.hostkey_tick: only true is built")
        with Vertical(id="attach-confirm-panel"):
            yield Static("Before you attach", id="attach-confirm-heading", markup=False)
            yield Static(
                f"{self._session['target_id']} · {display_id(self.session_id, [self.session_id])} · "
                f"{self._session['state']}", id="attach-confirm-title", markup=False)
            if self._hostkey is not None:
                yield Static(
                    f"Host key accepted on first use\n{self._hostkey['fingerprint']}\n"
                    "Check it out of band before you trust this host.",
                    id="attach-hostkey-notice", markup=False)
            if self._recording:
                # The contract fixes this wording; a surface renders it, never drafts its own.
                yield Static("● REC\n" + self._session["recording"]["warning_text"],
                             id="attach-recording-warning", markup=False)
            if self._hostkey is not None:
                yield Checkbox(HOSTKEY_TICK, id="hostkey-ack")
            yield Button("Attach", id="attach-button", variant="primary", disabled=self._hostkey is not None)
            yield Button("Back", id="back-button")
        yield Static(GATE_KEYS, id="attach-confirm-keys", markup=False)

    def on_mount(self) -> None:
        self.call_after_refresh(self._focus_initial)

    def _focus_initial(self) -> None:
        target = self.query("#hostkey-ack" if self._hostkey is not None else "#attach-button")
        if target:
            target.first().focus()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        # Attach stays greyed until the host key is ticked: the one acknowledgement the gate asks for.
        self.query_one("#attach-button", Button).disabled = not event.value

    def action_tick(self) -> None:
        if self._hostkey is not None:
            self.query_one("#hostkey-ack", Checkbox).toggle()

    def action_attach(self) -> None:
        if not self.query_one("#attach-button", Button).disabled:
            self.app.gated_sessions.add(self.session_id)
            self.dismiss(True)

    def action_back(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "attach-button":
            self.action_attach()
        elif event.button.id == "back-button":
            self.action_back()
