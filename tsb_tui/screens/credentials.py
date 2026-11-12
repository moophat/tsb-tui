"""Own credentials and saving a login for a target (phase 12 § C; plan D15; DEFAULT 42, 59; O30, O32; OPEN-12.2 (b)).

* `CredentialsScreen`: the user's own references and kinds (labels while this login holds an unlock), with Save a login…,
  Map to targets…, Delete… (which says it clears the reference's mappings, O30, and asks once) and Refresh.
* `SaveLoginScreen`: the form of `vault_flow.save_login`: targets, username (required), a password (masked) or a private
  key (pasted into a masked multi-line field, or read once from a regular file of at most 16 KiB at a path the user types
  on this host), a reference, an optional label. The secret and the path leave the form when they are read.
* `MapScreen`: map existing credentials to targets in one batch call (the targets typed here or marked in the inventory
  browser).

Everything a person wrote (a reference, a label) is shown as text: `as_text` replaces control characters and the widgets take
`Text`, so markup is never parsed.
"""

from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import Sequence
from typing import Any, ClassVar

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Input,
    RadioButton,
    RadioSet,
    Select,
    Static,
)
from tsb_contract_client import ApiError

from .._transport import HttpError
from ..model.targets import as_text
from ..vault_flow import call, check_login, failure_message, save_login, with_unlock

KEY_MAX_BYTES = 16 * 1024      # OPEN-12.2 (b): the largest key file read; also the core's bound on a secret
MASK = "•"
REFERENCE_MAX = 64             # common.schema.json credential_ref: ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$
LABEL_MAX = 128
LOGIN_NAME_MAX = 256
PASSWORD_MAX = 1024


class KeyField(Widget):
    """A masked, multi-line field for a private key's text: every character shows as a bullet, line by line, so the text
    cannot be read from the screen. Typed keys and a paste add to it; Backspace removes the last character; Ctrl-U clears."""

    can_focus = True
    DEFAULT_CSS = """
    KeyField { height: 6; border: round $frame; padding: 0 1; }
    KeyField:focus { border: round $accent; }
    """
    PLACEHOLDER = "paste the private key here (masked)"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.value = ""

    def render(self) -> Text:
        if not self.value:
            return Text(self.PLACEHOLDER, style="dim")
        width = max(self.size.width - 2, 1)
        lines = self.value.split("\n")
        return Text("\n".join(MASK * min(len(line), width) for line in lines))

    def clear(self) -> None:
        self.value = ""
        self.refresh()

    def on_paste(self, event: events.Paste) -> None:
        event.stop()
        self.value += event.text.replace("\r\n", "\n").replace("\r", "\n")
        self.refresh()

    def on_key(self, event: events.Key) -> None:
        key = event.key
        if key == "backspace":
            self.value = self.value[:-1]
        elif key == "ctrl+u":
            self.value = ""
        elif key == "enter":
            self.value += "\n"
        elif event.is_printable and event.character:
            self.value += event.character
        else:
            return      # Tab, Shift-Tab, Esc and the rest stay the screen's
        event.stop()
        self.refresh()


def read_key_file(path: str) -> tuple[str | None, str | None]:
    """(text, None) for a regular file of at most 16 KiB that holds UTF-8 text, read once; else (None, why). The path is
    opened without blocking and judged on the open descriptor, so a FIFO or a device is refused, never read, and a file
    that grows between the check and the read is still bounded."""
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0)
        fd = os.open(os.path.expanduser(path), flags)
    except OSError:
        return None, "The TUI cannot read that file."
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None, "That path is not a regular file."
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(KEY_MAX_BYTES + 1)
    except OSError:
        return None, "The TUI cannot read that file."
    finally:
        os.close(fd)
    if len(data) > KEY_MAX_BYTES:
        return None, "That file is larger than 16 KiB."
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "That file is not text: a private key is PEM text."


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Delete asks once, naming the reference, and says what the core does to its mappings (O30)."""

    DEFAULT_CSS = """
    ConfirmDeleteScreen { align: center middle; }
    ConfirmDeleteScreen .panel { width: 72; max-width: 100%; height: auto; border: round $error;
                                 background: $surface; padding: 1 2; }
    ConfirmDeleteScreen Horizontal { height: auto; margin-top: 1; }
    ConfirmDeleteScreen Button { margin-right: 2; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "keep", "Keep it")]

    def __init__(self, reference: str) -> None:
        super().__init__()
        self._reference = reference

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel"):
            yield Static(f"Delete {as_text(self._reference)}? This also clears every target mapping that names it, "
                         f"and cannot be undone.", id="delete-text", markup=False)
            with Horizontal():
                yield Button("Delete it", id="delete-confirm", variant="error")
                yield Button("Keep it", id="delete-keep")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "delete-confirm")

    def action_keep(self) -> None:
        self.dismiss(False)


class CredentialsScreen(Screen):
    """The user's own credentials."""

    DEFAULT_CSS = """
    CredentialsScreen { align: center middle; }
    CredentialsScreen #credentials-panel { width: 100%; max-width: 110; height: 100%; max-height: 40;
                                           border: round $frame; padding: 0 1; }
    CredentialsScreen #credentials-table { height: 1fr; }
    CredentialsScreen .title { text-style: bold; }
    CredentialsScreen .note { color: $text-muted; }
    CredentialsScreen #cred-status { height: auto; }
    CredentialsScreen Horizontal { height: auto; }
    CredentialsScreen Button { margin-right: 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "back", "Back"), Binding("n", "save_login", "Save a login"),
        Binding("m", "map", "Map"), Binding("d", "delete", "Delete"), Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.loaded = False
        self._references: list[str] = []
        self._generation = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="credentials-panel"):
            yield Static("Your credentials", classes="title", markup=False)
            yield Static("References and kinds always; labels show while this login holds an unlock.",
                         id="cred-note", classes="note", markup=False)
            yield DataTable(id="credentials-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="cred-status", markup=False)
            with Horizontal():
                yield Button("Save a login… (n)", id="cred-save", variant="primary")
                yield Button("Map to targets… (m)", id="cred-map")
                yield Button("Delete… (d)", id="cred-delete")
                yield Button("Refresh (r)", id="cred-refresh")
                yield Button("Back (Esc)", id="cred-back")

    def on_mount(self) -> None:
        table = self.query_one("#credentials-table", DataTable)
        table.add_columns("Reference", "Kind", "Label")
        table.focus()
        self.action_refresh()

    # -- the list --

    def action_refresh(self) -> None:
        self.loaded = False
        self._generation += 1
        generation = self._generation
        self.app.call_api(self.app.client.credentials, on_done=lambda entries: self._listed(generation, entries),
                          on_error=lambda exc: self._failed(generation, exc))

    def _listed(self, generation: int, credentials: list[dict]) -> None:
        if generation != self._generation:   # a newer load started since this one; its answer wins, drop this stale one
            return
        table = self._live_table()
        if table is None:   # the screen's table is gone (it was replaced/torn down); drop this answer, no exception
            return
        table.clear()
        self._references = []
        for entry in credentials:
            self._references.append(str(entry.get("reference", "")))
            table.add_row(Text(as_text(entry.get("reference"))), Text(as_text(entry.get("kind"))),
                          Text(as_text(entry.get("label"))))
        self.loaded = True

    def _failed(self, generation: int, exc: ApiError | HttpError | OSError) -> None:
        if generation != self._generation:   # a newer load started since this one; drop this stale failure
            return
        if self._live_table() is None:   # the screen's table is gone (it was replaced/torn down); drop this failure
            return
        if isinstance(exc, ApiError) and exc.status == 401:
            self.app.notify_api_error(exc)
            return
        if self.is_attached:
            self.query_one("#cred-status", Static).update(failure_message(exc))
            self.loaded = True

    def _live_table(self) -> DataTable | None:
        """The screen's table, or None when it is gone: the answer of a load that arrives after the screen was
        replaced has nowhere to go, so it is dropped — success and failure alike — with no exception."""
        nodes = self.query("#credentials-table")
        return nodes[0] if nodes else None

    def _selected(self) -> str | None:
        table = self.query_one("#credentials-table", DataTable)
        if not self._references or not 0 <= table.cursor_row < len(self._references):
            return None
        return self._references[table.cursor_row]

    def _say(self, text: str) -> None:
        if self.is_attached:
            self.query_one("#cred-status", Static).update(text)

    # -- actions --

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        action = {"cred-save": self.action_save_login, "cred-map": self.action_map, "cred-delete": self.action_delete,
                  "cred-refresh": self.action_refresh, "cred-back": self.action_back}.get(event.button.id or "")
        if action is not None:
            action()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_save_login(self) -> None:
        self.app.push_screen(SaveLoginScreen([]), self._after_form)

    def _after_form(self, saved: bool | None) -> None:
        self.action_refresh()

    def action_map(self) -> None:
        reference = self._selected()
        if reference is None:
            self._say("Select a credential first.")
            return
        self.app.push_screen(MapScreen(reference=reference, references=list(self._references)), self._after_form)

    def action_delete(self) -> None:
        reference = self._selected()
        if reference is None:
            self._say("Select a credential first.")
            return

        def decided(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(self._delete(reference), exclusive=True)

        self.app.push_screen(ConfirmDeleteScreen(reference), decided)

    async def _delete(self, reference: str) -> None:
        done = await with_unlock(self.app, self.app.client.delete_credential, reference)
        if done.status == "ok":
            self._say(f"Deleted {as_text(reference)}; the target mappings that named it are cleared.")
        elif done.status == "cancelled":
            self._say("Not deleted: the vault was not unlocked.")
        else:
            self._say(as_text(done.message))
        self.action_refresh()


class SaveLoginScreen(Screen):
    """Save a login for one or more targets (`vault_flow.save_login`). `targets` prefill the first field."""

    DEFAULT_CSS = """
    SaveLoginScreen { align: center middle; }
    SaveLoginScreen #save-panel { width: 100%; max-width: 90; height: 100%; max-height: 42; border: round $frame;
                                  padding: 0 1; }
    SaveLoginScreen .title { text-style: bold; }
    SaveLoginScreen .note { color: $text-muted; }
    SaveLoginScreen #save-result { height: auto; }
    SaveLoginScreen Horizontal { height: auto; }
    SaveLoginScreen Button { margin-right: 2; }
    SaveLoginScreen RadioSet { height: auto; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "back", "Back")]

    def __init__(self, targets: Sequence[str]) -> None:
        super().__init__()
        self._initial_targets = " ".join(targets)
        self._stored = False        # a credential was stored whose mapping failed: do not store it again
        self._finished = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="save-panel"):
            yield Static("Save a login for a target", classes="title", markup=False)
            yield Static("Stored in your own vault. The secret is sent once and not kept here.", classes="note",
                         markup=False)
            yield Input(value=as_text(self._initial_targets), placeholder="target ids, separated by spaces",
                        id="save-targets")
            yield Input(placeholder="username on the target (required)", id="save-username", max_length=LOGIN_NAME_MAX)
            with RadioSet(id="save-kind"):
                yield RadioButton("Password", value=True, id="kind-password")
                yield RadioButton("Private key (unencrypted)", id="kind-key")
            yield Input(placeholder="password", password=True, id="save-password", max_length=PASSWORD_MAX)
            with Vertical(id="save-key-group"):
                yield KeyField(id="save-key")
                yield Input(placeholder="or the path of a key file on this host (at most 16 KiB)", id="save-key-path")
            yield Input(placeholder="a name for it (its reference)", id="save-reference", max_length=REFERENCE_MAX)
            yield Input(placeholder="a label (optional)", id="save-label", max_length=LABEL_MAX)
            yield Static("", id="save-result", markup=False)
            with Horizontal():
                yield Button("Save", id="save-send", variant="primary")
                yield Button("Back (Esc)", id="save-cancel")

    def on_mount(self) -> None:
        self._show_kind()
        self.query_one("#save-username" if self._initial_targets else "#save-targets", Input).focus()

    def _key_chosen(self) -> bool:
        return bool(self.query_one("#kind-key", RadioButton).value)

    def _show_kind(self) -> None:
        key = self._key_chosen()
        self.query_one("#save-password").display = not key
        self.query_one("#save-key-group").display = key

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        event.stop()
        self._show_kind()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "save-send":
            self.run_worker(self._save(), exclusive=True)
        elif event.button.id == "save-cancel":
            self.action_back()

    def action_back(self) -> None:
        self.dismiss(self._finished)

    def on_screen_resume(self) -> None:
        if self._finished and self.app.screen is self:      # finished while a lock covered this screen
            self.dismiss(True)

    def _say(self, text: str) -> None:
        if self.is_attached:
            self.query_one("#save-result", Static).update(text)

    async def _save(self) -> None:
        if self._stored:
            self._say("This login is already stored; map it or delete it from the credentials list.")
            return
        targets = self.query_one("#save-targets", Input).value.replace(",", " ").split()
        username = self.query_one("#save-username", Input).value
        reference = self.query_one("#save-reference", Input).value.strip()
        label = self.query_one("#save-label", Input).value.strip() or None
        password = key = None
        if self._key_chosen():
            field = self.query_one("#save-key", KeyField)
            pathbox = self.query_one("#save-key-path", Input)
            typed_path = pathbox.value.strip()
            if field.value and typed_path:
                self._say("Paste the key or name a file, not both.")
                return
            if typed_path:
                key, problem = await asyncio.to_thread(read_key_file, typed_path)
                if problem is not None:
                    self._say(problem)
                    return
            else:
                key = field.value
        else:
            password = self.query_one("#save-password", Input).value
        secret = key or password
        if (problem := check_login(targets, username, secret, reference)) is not None:
            key = password = secret = None
            self._say(problem)
            return
        # Everything the form held of the secret goes now: what is sent is a local of this call.
        self.query_one("#save-password", Input).value = ""
        self.query_one("#save-key", KeyField).clear()
        self.query_one("#save-key-path", Input).value = ""
        self.query_one("#save-send", Button).disabled = True
        self._say("Saving…")
        outcome = await save_login(self.app, targets, username, password=password, key=key, reference=reference,
                                   label=label)
        key = password = secret = None
        if not self.is_attached:
            return
        self.query_one("#save-send", Button).disabled = False
        if outcome.status == "saved":
            self._finished = True
            self.app.toast(outcome.message, title="saved")
            if self.app.screen is self:
                self.dismiss(True)
            return
        self._stored = outcome.stored
        self._say(as_text(outcome.message))
        if outcome.stored:
            self._finished = True       # it is in the vault: the list behind this screen must show it


class MapScreen(Screen):
    """Map one of the user's own credentials to targets: one Layer 2a batch call."""

    DEFAULT_CSS = """
    MapScreen { align: center middle; }
    MapScreen #map-panel { width: 90; max-width: 100%; height: auto; border: round $frame; padding: 1 2; }
    MapScreen .title { text-style: bold; }
    MapScreen Horizontal { height: auto; margin-top: 1; }
    MapScreen Button { margin-right: 2; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "back", "Back")]

    def __init__(self, *, targets: Sequence[str] = (), reference: str | None = None,
                 references: Sequence[str] | None = None) -> None:
        super().__init__()
        self._initial_targets = " ".join(targets)
        self._reference = reference
        self._references = None if references is None else list(references)
        self.loaded = False
        self._finished = False

    def compose(self) -> ComposeResult:
        with Vertical(id="map-panel"):
            yield Static("Map a credential to targets", classes="title", markup=False)
            yield Input(value=as_text(self._initial_targets), placeholder="target ids, separated by spaces",
                        id="map-targets")
            yield Select([], id="map-reference", prompt="choose one of your credentials")
            yield Static("", id="map-result", markup=False)
            with Horizontal():
                yield Button("Map", id="map-send", variant="primary")
                yield Button("Back (Esc)", id="map-cancel")

    def on_mount(self) -> None:
        if self._references is not None:
            self._offer(self._references)
        else:
            self.app.call_api(self.app.client.credentials,
                              on_done=lambda entries: self._offer([str(e.get("reference", "")) for e in entries]),
                              on_error=self._failed)

    def _offer(self, references: list[str]) -> None:
        if not self.is_attached:
            return
        select = self.query_one("#map-reference", Select)
        select.set_options([(Text(as_text(ref)), ref) for ref in references])
        if self._reference in references:
            select.value = self._reference
        self.loaded = True

    def _failed(self, exc: ApiError | HttpError | OSError) -> None:
        if isinstance(exc, ApiError) and exc.status == 401:
            self.app.notify_api_error(exc)
            return
        self._say(failure_message(exc))
        self.loaded = True

    def _say(self, text: str) -> None:
        if self.is_attached:
            self.query_one("#map-result", Static).update(as_text(text))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "map-send":
            self.run_worker(self._map(), exclusive=True)
        elif event.button.id == "map-cancel":
            self.action_back()

    def action_back(self) -> None:
        self.dismiss(self._finished)

    async def _map(self) -> None:
        targets = self.query_one("#map-targets", Input).value.replace(",", " ").split()
        reference = self.query_one("#map-reference", Select).value
        if not targets:
            self._say("Name at least one target.")
            return
        if reference is Select.NULL or not isinstance(reference, str):
            self._say("Choose one of your credentials.")
            return
        try:
            await call(self.app, self.app.client.map_profiles, {name: reference for name in targets})
        except ApiError as exc:
            if exc.status == 401:
                self.app.notify_api_error(exc)
                return
            self._say(exc.message)
            return
        except (HttpError, OSError) as exc:
            self._say(str(exc))
            return
        self._finished = True
        self.app.toast(f"Mapped {as_text(reference)} to {', '.join(as_text(t) for t in targets)}.", title="mapped")
        if self.is_attached and self.app.screen is self:
            self.dismiss(True)
