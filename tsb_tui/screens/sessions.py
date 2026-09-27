"""Sessions — every session the principal may see (`GET /sessions`), states rendered from the
session record (session model §12: `live`, `detached`, `killed`, `terminated`); re-attach to a
live or detached one, or start another."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer
from tsb_contract_client import ApiError

from .._transport import HttpError

_ATTACHABLE = frozenset({"live", "detached"})


class SessionsScreen(Screen):
    BINDINGS: ClassVar[list[Binding]] = [Binding("r", "refresh", "Refresh", show=True)]

    def __init__(self) -> None:
        super().__init__()
        self._sessions: dict[str, dict] = {}
        # Bumped on every refresh; a reply is applied only if its generation is still the
        # newest one issued (below) — Refresh is never disabled or serialised while one is in
        # flight, so a slower, earlier reply landing after a faster, later one must not win.
        self._refresh_generation = 0

    def compose(self) -> ComposeResult:
        yield DataTable(id="sessions-table", cursor_type="row")
        with Horizontal(id="sessions-actions"):
            yield Button("Attach", id="attach-button", variant="primary", disabled=True)
            yield Button("New session", id="new-session-button")
            yield Button("Refresh", id="refresh-button")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#sessions-table", DataTable)
        table.add_columns("id", "target", "state", "tier", "connection", "owner")
        self.action_refresh()

    def action_refresh(self) -> None:
        self._refresh_generation += 1
        generation = self._refresh_generation
        self.app.call_api(self.app.client.sessions,
                          on_done=lambda sessions: self._on_sessions(generation, sessions),
                          on_error=self._on_error)

    def _on_sessions(self, generation: int, sessions: list[dict]) -> None:
        if generation != self._refresh_generation:
            return  # superseded by a refresh issued after this one — never applied
        self._sessions = {session["id"]: session for session in sessions}
        table = self.query_one("#sessions-table", DataTable)
        table.clear()
        for session in sessions:
            table.add_row(
                session["id"], session["target_id"], session["state"], str(session["tier"]),
                session["connection_id"], session["owner"], key=session["id"],
            )

    def _on_error(self, exc: ApiError | HttpError | OSError) -> None:
        self.app.notify_api_error(exc)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._sync_attach_button(event.row_key.value)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._go_to_terminal(event.row_key.value)

    def _sync_attach_button(self, session_id: str | None) -> None:
        table = self.query_one("#sessions-table", DataTable)
        button = self.query_one("#attach-button", Button)
        if session_id is None:
            button.disabled = True
            return
        state = table.get_row(session_id)[2]
        button.disabled = state not in _ATTACHABLE

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new-session-button":
            from .new_session import NewSessionScreen

            self.app.switch_screen(NewSessionScreen(target=self.app.initial_target))
        elif event.button.id == "refresh-button":
            self.action_refresh()
        elif event.button.id == "attach-button":
            table = self.query_one("#sessions-table", DataTable)
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            if row_key.value is not None:
                self._go_to_terminal(row_key.value)

    def _go_to_terminal(self, session_id: str | None) -> None:
        if session_id is None:
            return
        session = self._sessions.get(session_id)
        state = session["state"] if session is not None else "unknown"
        if session is None or state not in _ATTACHABLE:
            self.app.notify(f"session {session_id} is {state} — cannot attach",
                           severity="warning", markup=False)
            return
        # Re-attaching passes through the same recording (and, if it ever applied, host-key)
        # confirmation a fresh attach does — a re-attach only never carries a *new* host key,
        # since only the session's own creation response can.
        from .attach_confirm import AttachConfirmScreen

        self.app.switch_screen(AttachConfirmScreen(session))
