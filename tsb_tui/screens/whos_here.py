"""Who's here (design F5): the clients of this session, who may type, who holds the keyboard, and the
buttons that move it — and the owner's brake.

Every button is an action of the one table (`model/actions.py`), enabled by the models and nothing
here: Take it (the owner, `handoff` to self), Hand to selected (the holder, or the owner to reassign),
Put it aside (the owner, `handoff` to null), Remove selected… (the owner, after a confirmation that names
the client: client-remove rule 9). A hand-off is sent through the pump, which marks it in flight until
the `token` frame answers it (DT4 b); a refusal comes back as `request_refused` and shows *inside this
dialog* (typing-ownership rule 8), never silently. While it is open the keys go to it and the status row
says `keys → who's here` (D14).
"""

from __future__ import annotations

from typing import ClassVar

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static
from tsb_contract_client import ApiError

from .._transport import ConnectionClosed, HttpError
from ..model import actions as A
from ..widgets.status_row import Hint, KeysTarget

# The reasons a `handoff` is refused (typing-ownership "Refusals"), in the TUI's own words. A reason
# this client does not know is shown as the contract gave it.
REFUSALS = {
    "not_allowed": "Refused: only the keyboard holder or the owner can do that.",
    "no_such_client": "Refused: that client is no longer attached.",
    "view_only": "Refused: that client's right is read-only, so it cannot hold the keyboard.",
}

ACTION_IDS = ("take", "hand", "aside", "remove")

COLUMNS = ("who", "client", "right", "keyboard")
# When the table is wider than the dialog, the columns give way in this order (indexes into COLUMNS): the keyboard's
# sentence first, then the right, then who; the client id, which names the client to hand to or remove, last.
GIVE_WAY = (3, 2, 0, 1)
CELL_PADDING = 1  # the table's blank cells either side of each column's text
PANEL_MAX_WIDTH = 90  # `#whos-panel { max-width }`
PANEL_FRAME = 4  # its border and its padding, left and right
SCROLLBAR = 2  # the cells a vertical scrollbar would take, kept free so the table never grows a horizontal one


def cut(text: str, width: int) -> str:
    """`text` cut to `width` cells with an ellipsis."""
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


def fit_columns(headers: tuple[str, ...], rows: list[tuple[str, ...]], room: int) -> list[int]:
    """The text width of each column: as wide as its longest cell, narrower in `GIVE_WAY` order (never below its
    header) until the columns fit in `room` cells."""
    widths = [max([len(header), *(len(row[i]) for row in rows)]) for i, header in enumerate(headers)]
    excess = sum(widths) - room
    for index in GIVE_WAY:
        if excess <= 0:
            break
        taken = min(excess, widths[index] - len(headers[index]))
        widths[index] -= taken
        excess -= taken
    return widths


class WhosHereScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    WhosHereScreen { align: center middle; }
    #whos-panel {
        width: 100%;
        max-width: 90;
        height: auto;
        border: round $primary;
        background: $surface;
        padding: 0 1;
    }
    #whos-title { text-style: bold; }
    #whos-clients { height: auto; max-height: 12; }
    /* The selected row is the theme's neutral surface, not the accent: the accent says "you type", and it must not
       sit on another client's row. */
    #whos-clients > .datatable--cursor, #whos-clients:focus > .datatable--cursor {
        background: $surface-3;
        color: $foreground;
    }
    #whos-buttons { layout: grid; grid-size: 2; grid-gutter: 0 1; height: auto; }
    #whos-buttons Button { width: 1fr; min-width: 0; }
    #whos-note { color: $warning; }
    """
    AUTO_FOCUS = "#whos-clients"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close"),
        Binding("t", "run('take')", "Take it", show=False),
        Binding("h", "run('hand')", "Hand to selected", show=False),
        Binding("e", "run('aside')", "Put it aside", show=False),
        Binding("x", "run('remove')", "Remove selected…", show=False),
    ]

    def __init__(self, terminal) -> None:
        super().__init__()
        self._terminal = terminal
        self._note = ""
        self._refusals_seen = terminal.pump.state.refusal_count if terminal.pump is not None else 0

    # -- layout --

    def compose(self) -> ComposeResult:
        with Vertical(id="whos-panel"):
            yield Static("", id="whos-title", markup=False)
            yield DataTable(id="whos-clients", cursor_type="row", zebra_stripes=False, cell_padding=CELL_PADDING)
            with Horizontal(id="whos-buttons"):
                for action_id in ACTION_IDS:
                    # Framed like the stale and quiet-prompt dialogs' buttons (`[ Label ]`); Remove is drawn in the
                    # theme's danger style, since it disconnects someone. A `Content`, not markup: the brackets are text.
                    yield Button(Content(f"[ {A.BY_ID[action_id].label} ]"), id=action_id, compact=True,
                                 variant="error" if action_id == "remove" else "default")
            yield Static("", id="whos-help", markup=False)
            yield Static("", id="whos-note", markup=False)

    def on_mount(self) -> None:
        self._terminal.dialogs.append(self)
        self.sync()

    def on_unmount(self) -> None:
        if self in self._terminal.dialogs:
            self._terminal.dialogs.remove(self)
        self._terminal.refresh_status()

    def on_resize(self, event: events.Resize) -> None:
        """The window changed: the columns are fitted to the dialog's new width."""
        self.sync()

    def _table_room(self) -> int:
        """The cells the columns' text may take: the dialog's width, less its frame, the scrollbar's and the cells'
        own padding."""
        panel = min(PANEL_MAX_WIDTH, self.app.size.width)
        return panel - PANEL_FRAME - SCROLLBAR - 2 * CELL_PADDING * len(COLUMNS)

    # -- what it shows, from the model --

    @property
    def state(self):
        return self._terminal.pump.state

    def selected(self) -> str | None:
        try:
            table = self.query_one("#whos-clients", DataTable)
        except NoMatches:
            return None  # the dialog is going away
        if table.row_count == 0:
            return None
        return table.ordered_rows[table.cursor_row].key.value

    def facts(self) -> A.Facts:
        return A.Facts(self._terminal.pump.state if self._terminal.pump is not None else None,
                       self.selected())

    def sync(self) -> None:
        """Redraw from the model: the clients, the buttons, the refusal if one just arrived."""
        if self._terminal.pump is None:
            return
        try:
            self._redraw()
        except NoMatches:
            return  # the dialog is going away: its widgets are already gone
        self._terminal.refresh_status()

    def _redraw(self) -> None:
        state = self.state
        table = self.query_one("#whos-clients", DataTable)
        keep = self.selected()
        rows = [(client.get("client_id"), self._row(state, client)) for client in state.clients]
        widths = fit_columns(COLUMNS, [cells for _, cells in rows], self._table_room())
        table.clear(columns=True)
        for header, width in zip(COLUMNS, widths, strict=True):
            table.add_column(header, width=width)
        for client_id, cells in rows:
            table.add_row(*(cut(cell, width) for cell, width in zip(cells, widths, strict=True)), key=client_id)
        # The cursor stays on the client it was on; the first time it lands on the first one that is not
        # this client, since handing the keyboard to oneself is Take it's job.
        for index, client in enumerate(state.clients):
            if client.get("client_id") == (keep or self._first_other(state)):
                table.move_cursor(row=index)
                break
        # The target and the session's short id, as the End dialog names it and as mockup F5 draws the title.
        self.query_one("#whos-title", Static).update(
            f"Who's here · {self._terminal._session_label(self._terminal.session_id)}")
        if state.refusal_count != self._refusals_seen:
            self._refusals_seen = state.refusal_count
            refused = state.last_request_refused or {}
            if refused.get("request") == "handoff":
                self._note = REFUSALS.get(refused.get("reason"), f"Refused: {refused.get('reason')}")
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        """What depends on which client is selected: the buttons, the help line, the status row's hints.
        Moving the cursor only does this and never rebuilds the table, so it cannot loop."""
        state = self.state
        facts = self.facts()
        for action_id in ACTION_IDS:
            button = self.query_one(f"#{action_id}", Button)
            why = A.blocked(action_id, facts)
            button.disabled = why is not None
            button.tooltip = why
        self.query_one("#whos-help", Static).update(self._help(state, facts))
        self.query_one("#whos-note", Static).update(self._note)

    @staticmethod
    def _first_other(state) -> str | None:
        return next((c.get("client_id") for c in state.clients if c.get("client_id") != state.client_id),
                    None)

    @staticmethod
    def _row(state, client) -> tuple[str, str, str, str]:
        client_id = client.get("client_id")
        user = client.get("user")
        mine = client_id == state.client_id
        keyboard = []
        if client_id == state.holder_client_id:
            keyboard.append("● types")
        if user == state.owner:
            keyboard.append("owner")
        if mine:
            keyboard.append("this client")
        return ("you" if mine else str(user), str(client_id),
                "can type" if client.get("may_control") else "read-only", " · ".join(keyboard))

    @staticmethod
    def _help(state, facts: A.Facts) -> str:
        """One line under the buttons: what Hand to selected would do, or why it cannot."""
        why = A.blocked("hand", facts)
        if why is not None:
            return f"Hand to selected: {why}."
        chosen = next(c for c in state.clients if c.get("client_id") == facts.selected)
        text = f"Hand to selected gives {chosen.get('user')} the keyboard."
        return text + (" Typing in the session takes it back." if state.is_owner_client else "")

    def status_target(self) -> KeysTarget:
        facts = self.facts()
        hints = [Hint("↑↓", "choose")]
        for action_id in ACTION_IDS:
            if A.blocked(action_id, facts) is None:
                action = A.BY_ID[action_id]
                hints.append(Hint(action.key or "", action.hint or action.label))
        hints.append(Hint("Esc", "close"))
        return KeysTarget("who's here", tuple(hints))

    # -- what the buttons do --

    @on(DataTable.RowHighlighted)
    def _row_moved(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        try:
            self._refresh_selection()
        except NoMatches:
            return
        self._terminal.refresh_status()

    @on(Button.Pressed)
    async def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id in ACTION_IDS:
            await self.action_run(event.button.id)

    def action_close(self) -> None:
        self.dismiss(None)

    async def action_run(self, action_id: str) -> None:
        """Run one button's action, or say why it cannot run now."""
        self._note = ""
        why = A.blocked(action_id, self.facts())
        if why is not None:
            self._note = f"Not available: {why}."
            self.sync()
            return
        pump = self._terminal.pump
        selected = self.selected()
        try:
            if action_id == "take":
                await pump.handoff(pump.state.client_id)
            elif action_id == "hand":
                await pump.handoff(selected)
            elif action_id == "aside":
                await pump.handoff(None)
            elif action_id == "remove":
                self._confirm_remove(selected)
        except ConnectionClosed:
            pass  # the pump's read loop sees the close and handles it
        self.sync()

    # -- removal: asked first, naming the client (client-remove rule 9) --

    def _confirm_remove(self, client_id: str) -> None:
        client = next((c for c in self.state.clients if c.get("client_id") == client_id), None)
        user = client.get("user") if client else client_id
        self.app.push_screen(ConfirmRemoveScreen(self._terminal, str(user), client_id),
                             lambda confirmed: self._removal_confirmed(confirmed, str(user), client_id))

    def _removal_confirmed(self, confirmed: bool | None, user: str, client_id: str) -> None:
        if not confirmed:
            return
        self.app.call_api(
            self.app.client.remove_client, self._terminal.session_id, client_id,
            on_done=lambda _: self._set_note(f"Removed {user} ({client_id})."),
            on_error=self._removal_failed)

    def _set_note(self, text: str) -> None:
        self._note = text
        self.sync()

    def _removal_failed(self, exc: ApiError | HttpError | OSError) -> None:
        # The core's refusals (403 `forbidden`, 404 `client_not_found`) belong to this dialog; anything
        # else (a lapsed login, an unreachable broker) is the app's usual toast.
        if isinstance(exc, ApiError) and exc.status in (403, 404):
            self._set_note(f"Not removed: {exc}")
        else:
            self.app.notify_api_error(exc)


class ConfirmRemoveScreen(ModalScreen[bool]):
    """The question that comes before the owner's kill button sends anything."""

    DEFAULT_CSS = """
    ConfirmRemoveScreen { align: center middle; }
    #confirm-panel { width: 100%; max-width: 70; height: auto; border: round $error;
                     background: $surface; padding: 0 1; }
    #confirm-buttons { height: auto; }
    #confirm-buttons Button { margin-right: 2; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]
    AUTO_FOCUS = "#cancel"

    def __init__(self, terminal, user: str, client_id: str) -> None:
        super().__init__()
        self._terminal = terminal
        self._user = user
        self._client_id = client_id

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-panel"):
            yield Static(
                f"Remove {self._user} ({self._client_id}) from this session?\n"
                "That client is disconnected and does not reconnect by itself. The session keeps running.",
                id="confirm-text", markup=False)
            with Horizontal(id="confirm-buttons"):
                yield Button("Remove", id="confirm", variant="error", compact=True)
                yield Button("Cancel", id="cancel", compact=True)

    def on_mount(self) -> None:
        self._terminal.dialogs.append(self)
        self._terminal.refresh_status()

    def on_unmount(self) -> None:
        if self in self._terminal.dialogs:
            self._terminal.dialogs.remove(self)
        self._terminal.refresh_status()

    def sync(self) -> None:
        """Nothing here follows the models: the question is about one client, asked once."""

    def status_target(self) -> KeysTarget:
        return KeysTarget("confirm", (Hint("Tab", "choose"), Hint("⏎", "press"), Hint("Esc", "cancel")))

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)
