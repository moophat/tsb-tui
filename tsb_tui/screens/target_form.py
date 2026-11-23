"""The eight editable target fields; credentials remain a separate vault resource."""
from __future__ import annotations

import asyncio
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, RadioButton, RadioSet, Static
from tsb_contract_client import ApiError

from .._transport import HttpError
from ..model.targets import as_text

CREDENTIAL_NOTE = ("After you save the target, press l on the Targets tab to save a login for it; "
                   "otherwise you will be asked at connect.")
FIELDS = ("id", "type", "name", "group", "tags", "host", "port", "user")
OPTIONAL_FIELDS = ("name", "group", "tags", "port", "user")


class TargetFormScreen(ModalScreen[dict | None]):
    DEFAULT_CSS = """
    TargetFormScreen { align: center middle; }
    TargetFormScreen #target-form-panel { width: 80; max-width: 100%; height: auto; max-height: 100%;
                                        border: round $frame; padding: 0 1; }
    # Every field fits inside a 24-row window (L1-focus/geometry): the inputs are compacted to their content height.
    TargetFormScreen Input { height: 1; margin: 0; border: none; }
    TargetFormScreen Horizontal { height: auto; }
    TargetFormScreen Horizontal > Vertical { width: 1fr; height: auto; }
    TargetFormScreen RadioSet { height: auto; padding: 0; border: none; }
    TargetFormScreen Button { height: 1; min-height: 1; border: none; margin: 0 1 0 0; }
    TargetFormScreen Static { height: auto; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, definition: dict | None = None) -> None:
        super().__init__()
        self.initial = dict(definition or {})
        # A definition with no `id` is the Add row's prefill (the address field, nothing else): a new target, not
        # an edit. Only a record that carries an id opens the editor.
        self.edit_id = definition.get("id") if definition is not None else None
        self.busy = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="target-form-panel"):
            yield Static("Edit target" if self.edit_id else "New target")
            with Horizontal():
                with Vertical():
                    yield Static("Id")
                    yield Input(id="target-id", disabled=self.edit_id is not None)
                with Vertical():
                    yield Static("Type")
                    with RadioSet(id="target-type"):
                        yield RadioButton("SSH", value=self.initial.get("type", "ssh") == "ssh", id="target-ssh")
                        yield RadioButton("Telnet", value=self.initial.get("type") == "telnet", id="target-telnet")
            with Horizontal():
                with Vertical():
                    yield Static("Name (optional)")
                    yield Input(id="target-name")
                with Vertical():
                    yield Static("Group (optional)")
                    yield Input(id="target-group")
            yield Static("Tags (comma-separated, optional)")
            yield Input(id="target-tags")
            with Horizontal():
                with Vertical():
                    yield Static("Host")
                    yield Input(id="target-host")
                with Vertical():
                    yield Static("Port (blank: default for type)")
                    yield Input(id="target-port")
            with Vertical(id="target-account"):
                yield Static("Account (optional, not a secret)")
                yield Input(id="target-user")
            yield Static("Credential")
            yield Static(CREDENTIAL_NOTE, id="target-credential-note", markup=False)
            yield Static("", id="target-error", markup=False)
            with Horizontal():
                yield Button("Save target", id="target-save", variant="primary")
                yield Button("Cancel", id="target-cancel")

    def on_mount(self) -> None:
        self.fill(self.initial)
        if self.edit_id is not None:
            self.query_one("#target-host", Input).focus()
        elif self.initial.get("id") or self.initial.get("host"):
            # The Add row's prefill lands in the address field, where the operator continues.
            self.query_one("#target-host", Input).focus()
        else:
            self.query_one("#target-id", Input).focus()

    def fill(self, definition: dict) -> None:
        """Fill the same controls for an editor and for drive."""
        self._filled = dict(definition)
        for key in FIELDS:
            if key == "type":
                continue
            value = definition.get(key, "")
            if key == "tags":
                value = ", ".join(value)
            self.query_one(f"#target-{key}", Input).value = str(value)
        ssh = definition.get("type", "ssh") == "ssh"
        self.query_one("#target-ssh", RadioButton).value = ssh
        self.query_one("#target-telnet", RadioButton).value = not ssh
        self.query_one("#target-account").display = ssh

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        self.query_one("#target-account").display = event.pressed.id == "target-ssh"

    def definition(self) -> dict:
        values = {key: self.query_one(f"#target-{key}", Input).value for key in FIELDS if key != "type"}
        result = {"id": self.edit_id or values["id"],
                  "type": "ssh" if self.query_one("#target-ssh", RadioButton).value else "telnet",
                  "host": values["host"]}
        if not result["id"] or not result["host"]:
            raise ValueError("Id and Host are required.")
        for key in ("name", "group", "user"):
            if (values[key] or self._filled.get(key) == "") and (key != "user" or result["type"] == "ssh"):
                result[key] = values[key]
        if values["tags"]:
            tags = [tag.strip() for tag in values["tags"].split(",")]
            if any(not tag for tag in tags):
                raise ValueError("Each comma-separated tag must be nonempty.")
            result["tags"] = tags
        elif self._filled.get("tags") == []:
            result["tags"] = []
        if values["port"]:
            if not values["port"].isascii() or not values["port"].isdigit() or not 1 <= int(values["port"]) <= 65535:
                raise ValueError("Port must be blank or a whole number from 1 to 65535.")
            result["port"] = int(values["port"])
        return result

    async def save(self) -> dict:
        """Submit once; a refusal leaves all controls and the core's message on screen."""
        if self.busy:
            raise ValueError("A target save is already pending.")
        self.busy = True
        button = self.query_one("#target-save", Button)
        button.disabled = True
        try:
            definition = self.definition()
            client = self.app.client
            if self.edit_id is None:
                record = await asyncio.to_thread(client.create_target, definition)
            else:
                record = await asyncio.to_thread(client.replace_target, self.edit_id, definition)
        except (ValueError, ApiError, HttpError, OSError) as exc:
            self.query_one("#target-error", Static).update(as_text(exc.message if isinstance(exc, ApiError) else str(exc)))
            raise
        else:
            self.dismiss(record)
            return record
        finally:
            self.busy = False
            button.disabled = False

    async def _submit(self) -> None:
        try:
            await self.save()
        except (ValueError, ApiError, HttpError, OSError):
            pass  # save already displayed the failure without discarding any field

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "target-save":
            self.run_worker(self._submit(), exclusive=False)
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        if not self.busy:
            self.dismiss(None)


class ConfirmTargetDeleteScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmTargetDeleteScreen { align: center middle; }
    ConfirmTargetDeleteScreen #target-delete-panel { width: 72; max-width: 100%; height: auto;
        border: round $error; background: $surface; padding: 1 2; }
    ConfirmTargetDeleteScreen Horizontal { height: auto; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "keep", "Keep it")]

    def __init__(self, target_id: str) -> None:
        super().__init__()
        self.target_id = target_id

    def compose(self) -> ComposeResult:
        with Vertical(id="target-delete-panel"):
            yield Static(f"Delete {as_text(self.target_id)}? Open sessions stay open. This cannot be undone.",
                         id="target-delete-text", markup=False)
            with Horizontal():
                yield Button("Delete it", id="target-delete-confirm", variant="error")
                yield Button("Keep it", id="target-delete-keep")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "target-delete-confirm")

    def action_keep(self) -> None:
        self.dismiss(False)
