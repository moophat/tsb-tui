"""Attach confirm — the one screen every attach passes through: the recording warning when the
session records, shown again immediately before attaching rather than only once at creation
(session-record.schema.json: "every surface shows warning_text before it attaches or sends
input"), and the host-key fingerprint when this is the attach whose creation minted it
(open_decisions §5, LOCKED; decision 5.18). Reached from New Session right after create, and from
Sessions on re-attach — only the create response ever carries a host key, so a re-attach never
shows one."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Static


class AttachConfirmScreen(Screen):
    def __init__(self, session: dict, hostkey: dict | None = None) -> None:
        super().__init__()
        self._session = session
        self._hostkey = hostkey
        self._recording = bool(session["recording"]["active"])

    @property
    def session_id(self) -> str:
        return self._session["id"]

    def compose(self) -> ComposeResult:
        with Vertical(id="attach-confirm-panel"):
            yield Static(
                f"session {self._session['id']} on {self._session['target_id']}: "
                f"{self._session['state']}",
                id="attach-confirm-title", markup=False,
            )
            if self._recording:
                # The contract fixes this wording; a surface renders it, never drafts its own.
                yield Static(self._session["recording"]["warning_text"],
                           id="attach-recording-warning", markup=False)
                yield Checkbox("I acknowledge this session is recorded", id="recording-ack")
            if self._hostkey is not None:
                yield Static(
                    f"host key accepted on first use: {self._hostkey['fingerprint']}\n"
                    "Verify this fingerprint out of band before trusting it.",
                    id="attach-hostkey-notice", markup=False,
                )
                yield Checkbox("I have verified this host key fingerprint", id="hostkey-ack")
            yield Button("Attach", id="attach-button", variant="primary",
                        disabled=self._recording or self._hostkey is not None)
            yield Button("Back to sessions", id="back-button")

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self._sync_attach_button()

    def _sync_attach_button(self) -> None:
        # Attach stays disabled until every acknowledgement that applies is ticked — recording,
        # host key, or both; whichever of the two this session actually has.
        ok = True
        if self._recording:
            ok = ok and self.query_one("#recording-ack", Checkbox).value
        if self._hostkey is not None:
            ok = ok and self.query_one("#hostkey-ack", Checkbox).value
        self.query_one("#attach-button", Button).disabled = not ok

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "attach-button":
            from .terminal import TerminalScreen

            self.app.switch_screen(TerminalScreen(self._session["id"]))
        elif event.button.id == "back-button":
            from .sessions import SessionsScreen

            self.app.switch_screen(SessionsScreen())
