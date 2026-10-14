"""New Session — target id (from `--target` or typed: no target list at E0), and the connection
choice Contract §15.3 requires from this first tag. The recording warning and the host-key
acknowledgement happen right before the attach itself (screens/attach_confirm.py), not here —
every attach passes through there, including a later re-attach from Sessions, so gating on it at
creation time only would miss that case."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, RadioButton, RadioSet, Select, Static
from tsb_contract_client import ApiError

from .._transport import HttpError

TERM = "xterm-256color"

# error.schema.json: reuse with two or more candidate connections and no connection_id — the
# broker presents the choice and never picks one itself (Contract §12).
CONNECTION_CHOICE_REQUIRED = "connection_choice_required"


# What the dialog says when the create size has no rule: with auto size off nothing in the design says what
# size a new session is created with, and guessing one would be a size request the operator never made
# (terminal-size §4). Loud, and nothing reaches the broker. OPEN-1 was ruled on 2026-10-12, so the default
# profile has auto size on and this is what an operator who turned it off sees.
CREATE_SIZE_OFF = ("auto size is off, so there is no size to create this session with — the size a new "
                   "session carries is this window's, and asking for one is what auto size is "
                   "(terminal-size §4); turn auto size on (--set size.auto_request=true, or ^\\ R) to create")


class NewSessionScreen(Screen):
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, target: str | None = None) -> None:
        super().__init__()
        self._initial_target = target or ""
        # The target id the create that raised connection_choice_required was attempted against
        # — the picker's retry (on_select_changed) reuses it rather than re-reading the Input,
        # which the operator may have already changed while the GET /sessions round trip was in
        # flight.
        self._pending_target_id: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="new-session-panel"):
            yield Static("New session", id="new-session-title")
            yield Input(value=self._initial_target, placeholder="target id", id="target-id")
            with RadioSet(id="connection-choice"):
                yield RadioButton("New connection (fresh authentication)", value=True, id="conn-new")
                yield RadioButton("Reuse a connection already open to this target", id="conn-reuse")
            yield Input(placeholder="connection id (blank: the only candidate, if there is one)",
                       id="connection-id", disabled=True)
            # Populated only on 409 connection_choice_required (_offer_connection_choice below);
            # none preselected (error.schema.json: the broker never picks one itself).
            yield Select[str]([], id="connection-picker",
                             prompt="choose a connection to reuse", disabled=True)
            yield Static("", id="new-session-error", markup=False)
            yield Button("Create session", id="create-button", variant="primary")
            yield Button("Cancel", id="cancel-button")

    def on_mount(self) -> None:
        self.query_one("#target-id", Input).focus()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id == "connection-choice":
            reuse = event.pressed.id == "conn-reuse"
            self.query_one("#connection-id", Input).disabled = not reuse

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create-button":
            self._attempt_create()
        elif event.button.id == "cancel-button":
            self.action_cancel()

    def action_cancel(self) -> None:
        """Back to the workspace: every session open there is as it was."""
        self.app.pop_screen()

    def _attempt_create(self) -> None:
        error = self.query_one("#new-session-error", Static)
        target_id = self.query_one("#target-id", Input).value.strip()
        if not target_id:
            error.update("a target id is required — there is no target list at this tag")
            return
        connection = "reuse" if self.query_one("#conn-reuse", RadioButton).value else "new"
        connection_id = self.query_one("#connection-id", Input).value.strip() or None
        if connection_id and connection != "reuse":
            connection_id = None
        error.update("")
        self._pending_target_id = target_id
        self.query_one("#create-button", Button).disabled = True
        self._create(target_id, connection, connection_id)

    def _create_size(self) -> tuple[int, int] | None:
        """The creator's size (terminal-size §4: session open carries it; size-control step 15), which is the
        canvas the window gives: the window minus the rail and the status row, as an explicit request asks for.
        None when `size.auto_request` is off, because nothing then says what size to carry."""
        if not self.app.cfg["size.auto_request"]:
            return None
        return self.app.workspace().fit_size()

    def _create(self, target_id: str, connection: str, connection_id: str | None) -> None:
        size = self._create_size()
        if size is None:
            self.query_one("#new-session-error", Static).update(CREATE_SIZE_OFF)
            self.query_one("#create-button", Button).disabled = False
            return
        cols, rows = size
        self.app.call_api(
            self.app.client.create_session, target_id, connection, TERM, cols, rows, connection_id,
            on_done=self._on_create_done, on_error=self._on_create_error,
        )

    def _on_create_done(self, created: dict) -> None:
        from .attach_confirm import open_attach

        # The dialog goes first, then the new session opens in the workspace under it, beside the others.
        self.app.pop_screen()
        open_attach(self.app, created["session"], created.get("hostkey"))

    def _on_create_error(self, exc: ApiError | HttpError | OSError) -> None:
        # Local widget updates first: `notify_api_error` may switch this screen away (a 401
        # routes to Login), and this screen should not still be queried after that happens.
        self.query_one("#create-button", Button).disabled = False
        if isinstance(exc, ApiError) and exc.code == CONNECTION_CHOICE_REQUIRED:
            self._offer_connection_choice()
            return
        if isinstance(exc, ApiError):
            self.query_one("#new-session-error", Static).update(exc.message)
        self.app.notify_api_error(exc)

    def _offer_connection_choice(self) -> None:
        """error.schema.json's own remedy for connection_choice_required: `GET /sessions` lists
        each session's connection_id. Offer the distinct ones this principal holds to this
        target — never one this principal cannot see, and never the broker's own guess."""
        self.query_one("#new-session-error", Static).update(
            "several connections could be reused — choose one below")
        self.app.call_api(self.app.client.sessions,
                          on_done=self._on_choice_sessions, on_error=self._on_create_error)

    def _on_choice_sessions(self, sessions: list[dict]) -> None:
        target_id = self._pending_target_id
        candidates = list(dict.fromkeys(
            session["connection_id"] for session in sessions
            if session["target_id"] == target_id and session["owner"] == self.app.username
        ))
        picker = self.query_one("#connection-picker", Select)
        if not candidates:
            # Between the 409 and this GET /sessions landing, every one of this principal's own
            # connections to the target closed — nothing left to offer; the operator can only
            # retry (a fresh "new" connection, or reuse if one reopens).
            self.query_one("#new-session-error", Static).update(
                "no connection of this principal's own to this target is listed anymore — retry")
            return
        picker.set_options([(connection_id, connection_id) for connection_id in candidates])
        picker.disabled = False
        picker.focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "connection-picker" or event.value is Select.NULL:
            return
        target_id = self._pending_target_id
        if target_id is None:
            return
        event.select.disabled = True
        self.query_one("#new-session-error", Static).update("")
        self.query_one("#create-button", Button).disabled = True
        self._create(target_id, "reuse", str(event.value))
