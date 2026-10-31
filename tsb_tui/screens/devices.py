"""Own devices: list and revoke (phase 12 § E; credential §7.1 "User revokes via Settings UI"; DEFAULT 40).

The list shows what a person needs to recognise a device: its name as text, when it was registered, when it was last used
and whether it is revoked; never a key. Revoking asks once and says what revocation does not stop (someone who knows the TSB
password: change it). The TUI never registers a device and never sends a challenge (DEFAULT 64): it lists and revokes only.
"""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Static
from tsb_contract_client import ApiError

from .._transport import HttpError
from ..model.targets import as_text
from ..vault_flow import call, failure_message


class ConfirmRevokeScreen(ModalScreen[bool]):
    """Revoke asks once, naming the device, and says what revocation does not stop (DEFAULT 40)."""

    DEFAULT_CSS = """
    ConfirmRevokeScreen { align: center middle; }
    ConfirmRevokeScreen .panel { width: 78; max-width: 100%; height: auto; border: round $error;
                                 background: $surface; padding: 1 2; }
    ConfirmRevokeScreen Horizontal { height: auto; margin-top: 1; }
    ConfirmRevokeScreen Button { margin-right: 2; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "keep", "Keep it")]

    def __init__(self, name: str) -> None:
        super().__init__()
        self._device_name = name

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel"):
            yield Static(
                f"Revoke {as_text(self._device_name)}? That device can no longer unlock your vault, and its logins end. "
                f"It does not stop someone who knows your TSB password: change that password too.",
                id="revoke-text", markup=False)
            with Horizontal():
                yield Button("Revoke it", id="revoke-confirm", variant="error")
                yield Button("Keep it", id="revoke-keep")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "revoke-confirm")

    def action_keep(self) -> None:
        self.dismiss(False)


class DevicesScreen(Screen):
    """The principal's own devices."""

    DEFAULT_CSS = """
    DevicesScreen { align: center middle; }
    DevicesScreen #devices-panel { width: 100%; max-width: 110; height: 100%; max-height: 40; border: round $frame;
                                   padding: 0 1; }
    DevicesScreen #devices-table { height: 1fr; }
    DevicesScreen .title { text-style: bold; }
    DevicesScreen #dev-status { height: auto; }
    DevicesScreen Horizontal { height: auto; }
    DevicesScreen Button { margin-right: 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "back", "Back"), Binding("x", "revoke", "Revoke"), Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.loaded = False
        self._devices: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="devices-panel"):
            yield Static("Your devices", classes="title", markup=False)
            yield DataTable(id="devices-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="dev-status", markup=False)
            with Horizontal():
                yield Button("Revoke… (x)", id="dev-revoke", variant="primary")
                yield Button("Refresh (r)", id="dev-refresh")
                yield Button("Back (Esc)", id="dev-back")

    def on_mount(self) -> None:
        table = self.query_one("#devices-table", DataTable)
        # Timestamps and the status are fixed-width text: their columns do not wait on a measurement of the rows.
        table.add_column("Name")
        table.add_column("Registered", width=20)
        table.add_column("Last used", width=20)
        table.add_column("Status", width=40)
        table.focus()
        self.action_refresh()

    def action_refresh(self) -> None:
        self.loaded = False
        self.app.call_api(self.app.client.devices, on_done=self._listed, on_error=self._failed)

    def _listed(self, devices: list[dict]) -> None:
        if not self.is_attached:
            return
        table = self.query_one("#devices-table", DataTable)
        table.clear()
        self._devices = list(devices)
        for device in devices:
            status = "active"
            if device.get("revoked_at"):
                status = f"revoked {device['revoked_at']}"
                if device.get("revoke_reason"):
                    status += f" ({device['revoke_reason']})"
            table.add_row(Text(as_text(device.get("device_name"))), Text(as_text(device.get("registered_at"))),
                          Text(as_text(device.get("last_used_at"))), Text(as_text(status)))
        self.loaded = True

    def _failed(self, exc: ApiError | HttpError | OSError) -> None:
        if isinstance(exc, ApiError) and exc.status == 401:
            self.app.notify_api_error(exc)
            return
        self._say(failure_message(exc))
        self.loaded = True

    def _say(self, text: str) -> None:
        if self.is_attached:
            self.query_one("#dev-status", Static).update(as_text(text))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        action = {"dev-revoke": self.action_revoke, "dev-refresh": self.action_refresh,
                  "dev-back": self.action_back}.get(event.button.id or "")
        if action is not None:
            action()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_revoke(self) -> None:
        table = self.query_one("#devices-table", DataTable)
        if not self._devices or not 0 <= table.cursor_row < len(self._devices):
            self._say("Select a device first.")
            return
        device = self._devices[table.cursor_row]
        if device.get("revoked_at"):
            self._say("That device is already revoked.")
            return

        def decided(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(self._revoke(str(device["device_id"])), exclusive=True)

        self.app.push_screen(ConfirmRevokeScreen(device.get("device_name") or ""), decided)

    async def _revoke(self, device_id: str) -> None:
        try:
            await call(self.app, self.app.client.revoke_device, device_id)
        except ApiError as exc:
            if exc.status == 401:
                self.app.notify_api_error(exc)
                return
            self._say(exc.message)
        except (HttpError, OSError) as exc:
            self._say(str(exc))
        else:
            self._say("Revoked.")
        self.action_refresh()
