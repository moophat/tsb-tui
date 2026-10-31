"""The two passphrase prompts of the untrusted unlock (phase 12 § B; DEFAULT 62): asking the passphrase, and setting it
the first time (init) with credential-protection §7.4's warning.

Each is a modal that outlives the attempts it takes: the flow (`vault_flow`) awaits `submitted()`, shows `busy()` while
argon2id and the unlock run, and calls `refuse()` to put the prompt back with a message when the core said no, so a wrong
passphrase is a retry on the same screen and never a lost create. A submission hands the flow the typed text and empties
the field at once; the screen keeps no copy.

Every passphrase prompt carries the one line that typed keys may be recorded when the TUI runs inside a TSB session
(open_decisions §5; DEFAULT 62). Everything shown is text, never markup.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

RECORDING_LINE = "Typed keys may be recorded when this TUI runs inside a TSB session."
# credential-protection §7.4 ("Warn users during initial setup"), in plain words.
INIT_WARNING = ("A forgotten passphrase cannot be recovered; credentials stored under it are lost; "
                "keep it in a password manager.")
MISMATCH = "The two entries do not match."
EMPTY = "A passphrase is required."


class PassphraseScreen(ModalScreen[None]):
    """Ask the passphrase to unlock the vault. The flow reads it with `await screen.submitted()` (None: cancelled)."""

    DEFAULT_CSS = """
    PassphraseScreen { align: center middle; }
    PassphraseScreen .panel { width: 84; max-width: 100%; height: auto; border: round $frame;
                              background: $surface; padding: 1 2; }
    PassphraseScreen .title { text-style: bold; }
    PassphraseScreen .note { color: $text-muted; }
    PassphraseScreen .warning { color: $warning; }
    PassphraseScreen .error { color: $error; }
    PassphraseScreen Input { margin-top: 1; }
    PassphraseScreen Horizontal { height: auto; margin-top: 1; }
    PassphraseScreen Button { margin-right: 2; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]
    TITLE_TEXT = "Unlock your vault"
    CONFIRM = False

    def __init__(self, why: str = "") -> None:
        super().__init__()
        self._why = why
        self._waiter: asyncio.Future[str | None] | None = None
        self._released = False      # the flow takes no more submissions from this prompt
        self._spent = False         # the flow is done with it: it is to leave the stack, as soon as it is on top
        self._busy = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel"):
            yield Static(self.TITLE_TEXT, classes="title", markup=False)
            if self._why:
                yield Static(self._why, id="vault-why", markup=False)
            if self.CONFIRM:
                yield Static(INIT_WARNING, id="vault-init-warning", classes="warning", markup=False)
            yield Static(RECORDING_LINE, id="vault-recording-line", classes="note", markup=False)
            yield Input(placeholder="passphrase", password=True, id="vault-passphrase")
            if self.CONFIRM:
                yield Input(placeholder="the same passphrase again", password=True, id="vault-passphrase-again")
            yield Static("", id="vault-error", classes="error", markup=False)
            yield Static("", id="vault-busy", classes="note", markup=False)
            with Horizontal():
                yield Button("Continue", id="vault-continue", variant="primary")
                yield Button("Cancel", id="vault-cancel")

    def on_mount(self) -> None:
        self.query_one("#vault-passphrase", Input).focus()

    # -- what the flow calls --

    async def submitted(self) -> str | None:
        """The next passphrase typed, or None when the operator cancelled."""
        if self._released:
            return None
        self._waiter = asyncio.get_running_loop().create_future()
        return await self._waiter

    def busy(self, text: str) -> None:
        """Work is running: show what, and take the entries and Cancel away until it ends."""
        self._busy = True
        self._set_enabled(False)
        self.query_one("#vault-error", Static).update("")
        self.query_one("#vault-busy", Static).update(text)

    def refuse(self, message: str) -> None:
        """Put the prompt back for another try, saying why."""
        self.query_one("#vault-busy", Static).update("")
        self.query_one("#vault-error", Static).update(message)
        self._busy = False
        self._set_enabled(True)
        self.query_one("#vault-passphrase", Input).focus()

    @property
    def gone(self) -> bool:
        """The prompt was closed or removed (a login that ended pops every screen): the flow must not call it or send on."""
        return self._released or not self.is_attached

    def close(self) -> None:
        """The flow is done with the prompt. It leaves the stack at once when it is on top; under the lock, or under another
        screen, it leaves as soon as it is on top again (`on_screen_resume`)."""
        self._released = True
        self._spent = True
        self._dismiss_if_spent()

    def _dismiss_if_spent(self) -> None:
        if self._spent and self.is_attached and self.app.screen is self:
            self._spent = False
            self.dismiss(None)

    def on_screen_resume(self) -> None:
        self._dismiss_if_spent()

    # -- what the operator does --

    def _set_enabled(self, enabled: bool) -> None:
        for widget in self.query("Input, #vault-continue, #vault-cancel"):
            widget.disabled = not enabled

    def _entry(self) -> str | None:
        box = self.query_one("#vault-passphrase", Input)
        typed = box.value
        if not typed:
            self.query_one("#vault-error", Static).update(EMPTY)
            return None
        return typed

    def _finish(self, result: str | None) -> None:
        for box in self.query(Input):
            box.value = ""      # the screen keeps no copy of what was typed
        if self._waiter is not None and not self._waiter.done():
            self._waiter.set_result(result)

    def _submit(self) -> None:
        if self._waiter is None or self._waiter.done():
            return
        typed = self._entry()
        if typed is not None:
            self._finish(typed)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "vault-continue":
            self._submit()
        elif event.button.id == "vault-cancel":
            self.action_cancel()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Escape is Cancel, and Cancel is off while the prompt works: what is being derived or sent is not taken back."""
        if action == "cancel" and self._busy:
            return False
        return super().check_action(action, parameters)

    def action_cancel(self) -> None:
        if self._busy:
            return
        self._finish(None)
        self.close()

    def on_unmount(self) -> None:
        self._released = True
        self._finish(None)


class InitScreen(PassphraseScreen):
    """Set the vault's passphrase for the first time: twice, with cred §7.4's warning in plain words before the confirm.
    Nothing is sent until the two entries are the same."""

    TITLE_TEXT = "Set up your vault"
    CONFIRM = True

    def _entry(self) -> str | None:
        typed = super()._entry()
        if typed is None:
            return None
        if typed != self.query_one("#vault-passphrase-again", Input).value:
            self.query_one("#vault-error", Static).update(MISMATCH)
            return None
        return typed
