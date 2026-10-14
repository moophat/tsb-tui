"""Size (design F11, D19): the target's size and what this window shows of it, an explicit request, the
auto toggle, and the last answer with its reason.

The session owns its size (D3); this dialog is the one place the operator asks it to change. The button
is labelled with the size it asks for and says it is a request to the target that may come back
different (size-control surface rules; terminal-size §4). Only the typist's request counts, so for
anyone else the button is grey and says who types (typing-ownership step 14). After a clamped answer
the button waits for the window to change (terminal-size §3). A request goes out with `timeout=0`: its
answer arrives through the pump like any frame (DT17), and this dialog redraws when it does. Opening
or closing this dialog, like the sidebar, never asks for anything (D44).
"""

from __future__ import annotations

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from .._transport import ConnectionClosed
from ..model import actions as A
from ..widgets.status_row import Hint, KeysTarget

# What a `size` answer's reason says, in the TUI's own words. A reason this client does not know is
# read by its cols and rows alone (size-control surface rules; conventions V5).
REASON_TEXT = {
    "applied": "applied",
    "clamped_floor": "clamped: the target's smallest size",
    "clamped_ceiling": "clamped: the target's largest size",
    "pinned": "refused: the target's size is fixed",
    "not_resizable": "refused: this target has no size",
    "not_typist": "refused: you are not the typist",
}

REQUEST_NOTE = "asks the target for that size; it may answer differently"


def describe_answer(answer: dict | None) -> str:
    """`Last answer: …` for the dialog: what came back, why, and what was asked."""
    if answer is None:
        return "Last answer: none since you attached"
    reason = answer.get("reason")
    why = REASON_TEXT.get(reason, f"reason {reason}" if reason else "no reason given")
    asked = answer.get("asked")
    asked_text = f"asked {asked[0]}×{asked[1]}, " if asked else ""
    return f"Last answer: {why} · {asked_text}the target is {answer.get('cols')}×{answer.get('rows')}"


class SizeDialog(ModalScreen[None]):
    DEFAULT_CSS = """
    SizeDialog { align: center middle; }
    #size-panel { width: 100%; max-width: 90; height: auto; border: round $primary;
                  background: $surface; padding: 0 1; }
    #size-title { text-style: bold; }
    .size-row { height: auto; }
    .size-row Button { margin-right: 2; }
    .size-row Static { width: 1fr; }
    #size-note { color: $warning; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close"),
        Binding("r", "run('ask_size')", "Ask", show=False),
        Binding("R", "run('auto_size')", "Auto size", show=False),
    ]

    def __init__(self, terminal) -> None:
        super().__init__()
        self._terminal = terminal
        self._note = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="size-panel"):
            yield Static("", id="size-title", markup=False)
            with Horizontal(classes="size-row"):
                yield Button("Ask", id="ask", compact=True)
                yield Static("", id="ask-why", markup=False)
            with Horizontal(classes="size-row"):
                yield Button("Auto size", id="auto", compact=True)
                yield Static("asks on a window change, while you type", markup=False)
            yield Static("", id="size-last", markup=False)
            yield Static("Opening the sidebar never asks; the view pans to follow the cursor.",
                         id="size-help", markup=False)
            yield Static("", id="size-note", markup=False)

    def on_mount(self) -> None:
        self._terminal.dialogs.append(self)
        self.sync()

    def on_unmount(self) -> None:
        if self in self._terminal.dialogs:
            self._terminal.dialogs.remove(self)
        self._terminal.refresh_status()

    def facts(self) -> A.Facts:
        return A.Facts(self._terminal.pump.state if self._terminal.pump is not None else None)

    def sync(self) -> None:
        if self._terminal.pump is None:
            return
        try:
            self._redraw()
        except NoMatches:
            return  # the dialog is going away: its widgets are already gone
        self._terminal.refresh_status()

    def _redraw(self) -> None:
        state = self._terminal.pump.state
        canvas = self._terminal.canvas
        window = f"this window shows {canvas.size.width}×{canvas.size.height}"
        panning = canvas.view_range.label
        self.query_one("#size-title", Static).update(
            f"Target {state.size_cols}×{state.size_rows} · {window}" + (f" · {panning}" if panning else ""))
        cols, rows = self._terminal.fit_size()
        why = A.blocked("ask_size", self.facts())
        ask = self.query_one("#ask", Button)
        ask.label = f"Ask for {cols}×{rows}"
        ask.disabled = why is not None
        self.query_one("#ask-why", Static).update(why if why is not None else REQUEST_NOTE)
        self.query_one("#auto", Button).label = f"Auto size: {'on' if self._terminal.auto_size_on else 'off'}"
        self.query_one("#size-last", Static).update(describe_answer(state.last_size_answer))
        self.query_one("#size-note", Static).update(self._note)

    def status_target(self) -> KeysTarget:
        hints = []
        cols, rows = self._terminal.fit_size()
        if A.blocked("ask_size", self.facts()) is None:
            hints.append(Hint("r", f"Ask for {cols}×{rows}"))
        hints.append(Hint("R", "Auto size on│off"))
        hints.append(Hint("Esc", "close"))
        return KeysTarget("size", tuple(hints))

    @on(Button.Pressed)
    async def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        await self.action_run({"ask": "ask_size", "auto": "auto_size"}[event.button.id])

    def action_close(self) -> None:
        self.dismiss(None)

    async def action_run(self, action_id: str) -> None:
        self._note = ""
        if action_id == "auto_size":
            self._terminal.toggle_auto_size()
        else:
            try:
                why = await self._terminal.ask_size()
            except ConnectionClosed:
                why = None  # the pump's read loop sees the close and handles it
            if why is not None:
                self._note = f"Not sent: {why}."
        self.sync()
