"""The device's SSH authentication, before a session exists (device-login at v0.12.1)."""
from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import re
import traceback
from typing import Any

from rich.cells import cell_len
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Input, Static
from tsb_contract_client import ApiError, AsyncDeviceLogin, AttachRefused

from .. import vault_flow
from .._transport import ConnectionClosed, HandshakeError, HttpError, ProtocolError
from .attach_confirm import AttachConfirmScreen

LOG = logging.getLogger("tsb_tui.device_login")

RECORDED = "The user name, the device's prompts and every answer will be recorded. Continue?"
TOO_OLD = "This TUI is too old for this device login; update it."
MORE_THAN_PASSWORD = "Not saved: the device asked for more than a password, and only a password is saved."
LOST = "The device login's link to TSB was lost; a session may have opened."
UNATTACHED = "The device accepted the login and the session opened, but this pane could not attach to it; it is in the sessions list."
POST_ADOPT = "The session opened, but this pane hit an error finishing the login; it is in the sessions list."
OUTCOMES = {
    "refused": "The device refused the login.", "timeout": "The device login timed out.",
    "password_change_requested": "The device requires a password change.",
    "hostkey_refused": "The host key was refused.", "hostkey_changed": "The host key changed.",
    "cancelled": "The device login was cancelled.", "connect_failed": "The device connection failed.",
    "worker_unavailable": "The device login's worker is unavailable.",
    "too_long": "The device login exceeded its limits.", "forbidden": "You may no longer open this session.",
}


def proposed_reference(target: str, user: str) -> str:
    target, user = (re.sub(r"[^A-Za-z0-9._-]", "_", part) for part in (target, user))
    if len(target) + len(user) + 1 > 64:
        user = user[:62]
        target = target[:63 - len(user)]
    return f"{target}.{user}"


def device_text(value: str) -> str:
    """CRLF, LF, tab stops, then visible C0/DEL/C1; never markup or terminal escapes."""
    out, col = [], 0
    for ch in value.replace("\r\n", "\n"):
        n = ord(ch)
        if ch == "\n":
            out.append(ch)
            col = 0
        elif ch == "\t":
            spaces = 8 - col % 8
            out.append(" " * spaces)
            col += spaces
        elif n < 32 or 127 <= n <= 159:
            out.append(f"\\x{n:02x}")
            col += 4
        else:
            out.append(ch)
            col += cell_len(ch)
    return "".join(out)


def save_message(outcome: vault_flow.SaveOutcome, target: str) -> str:
    if outcome.status == "saved":
        return f"Saved {outcome.reference}; TSB now uses it for {target}."
    if isinstance(outcome.error, ApiError) and outcome.error.code == "credential_exists":
        return (f"Not saved: you already have a login named {outcome.reference}; "
                "delete it in Credentials first")
    return outcome.message


class DeviceLoginPane(VerticalScroll):
    DEFAULT_CSS = """
    DeviceLoginPane { layer: overlay; background: $background; width: 100%; height: 100%; padding: 1 2; }
    DeviceLoginPane Vertical { height: auto; }
    DeviceLoginPane Static { height: auto; }
    DeviceLoginPane #device-text { border: round $frame; padding: 0 1; }
    DeviceLoginPane Input { margin: 0; }
    """

    def __init__(self, terminal: Any, slot: Any, target: str, term: str, size: tuple[int, int], *,
                 recording_login: bool = False, user: str | None = None, pending: dict | None = None,
                 emit: Any = None, connected_answer: Any = None) -> None:
        super().__init__()
        self.terminal, self.slot = terminal, slot
        self.target, self.term, self.size_at_create = target, term, size
        self.recording_login = recording_login
        self.acknowledged = False
        self.user = user or ""
        self.pending = pending
        self.login: AsyncDeviceLogin | None = None
        self.prompt: dict | None = None
        self.hostkey_pending = False
        self.last_answered_attempt = 0
        self.candidate: str | None = None
        self.remember_allowed = True
        self.connected = False
        self.adopted = False
        self.ended = False
        self.cancelled = False
        self._too_old = False
        self._login_task: asyncio.Task | None = None
        self._emit, self._connected_answer = emit, connected_answer
        self._client = terminal.app.client
        self._generation = terminal.app._login_gen

    def compose(self) -> ComposeResult:
        yield Static(RECORDED, id="device-consent-warning", markup=False)
        yield Button("Yes, continue", id="login-consent")
        yield Static("login as:", id="device-user-label", markup=False)
        yield Input(id="device-user", max_length=256)
        yield Static("", id="device-status", markup=False)
        yield Static("", id="device-hostkey", markup=False)
        yield Button("Accept", id="device-accept")
        yield Button("Refuse", id="device-refuse")
        with Vertical(id="device-text"):
            yield Static("", id="device-banner", markup=False)
            yield Static("", id="device-name", markup=False)
            yield Static("", id="device-instruction", markup=False)
            yield Vertical(id="device-questions")
        yield Static("Password:", id="own-password-label", markup=False)
        with Vertical(id="own-password"):
            yield Input(password=True, classes="password-answer")
        yield Checkbox("Remember", id="device-remember")
        yield Button("Send answers", id="device-send")
        yield Button("Open again", id="device-open-again")
        yield Button("Cancel", id="device-cancel")

    def on_mount(self) -> None:
        self.query_one("#device-text").border_title = "device login"
        self.query_one("#device-text").display = False
        for selector in ("#device-hostkey", "#device-accept", "#device-refuse", "#own-password-label",
                         "#own-password", "#device-remember", "#device-send", "#device-open-again"):
            self.query_one(selector).display = False
        self._show_user(not self.recording_login and self.pending is None)
        for selector in ("#device-consent-warning", "#login-consent"):
            self.query_one(selector).display = self.recording_login and self.pending is None
        if self.pending is not None:
            self._login_task = asyncio.create_task(self._run())

    def _show_user(self, show: bool) -> None:
        self.query_one("#device-user-label").display = show
        self.query_one("#device-user").display = show
        if show:
            self.query_one("#device-user").focus()

    def take_front_focus(self) -> None:
        """The keys this pane takes when its slot comes to the front: the `login as:` field
        while it shows, else the first answer input. A pane that is not in front never takes focus."""
        if not self.is_mounted:
            return
        if self.query_one("#device-user").display:
            self.query_one("#device-user", Input).focus()
        elif self.query(".device-answer"):
            self.query(".device-answer").first().focus()

    def _live(self) -> bool:
        return (not self.slot.closed and self._client is self.app.client
                and self._generation == self.app._login_gen and self.app.workspace() is self.terminal)

    def status(self, text: str) -> None:
        self.slot.login_status = text
        if self.is_mounted:
            self.query_one("#device-status", Static).update(text)
        self.terminal.refresh_status()

    async def submit_user(self) -> None:
        if self.app.screen_lock.locked or not self._live() or self._login_task is not None:
            return
        if self.recording_login and not self.acknowledged:
            return
        user = self.query_one("#device-user", Input).value
        if not 1 <= len(user) <= 256:
            self.status("Type a user name of 1 to 256 characters.")
            return
        self.user = user
        self.query_one("#device-user", Input).value = ""
        self._show_user(False)
        self._login_task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            if self.pending is None:
                while True:
                    try:
                        call = functools.partial(self._client.create_device_login, self.target, self.user,
                                                 self.term, *self.size_at_create,
                                                 recording_acknowledged=self.acknowledged)
                        self.pending = await vault_flow.call(self.app, call)
                        break
                    except ApiError as exc:
                        if exc.status in (422, 503):
                            self.app.device_login_unavailable = self._client
                            self.finish("This TSB login cannot use device login on this core.")
                            return
                        if exc.code != "login_recording_not_acknowledged":
                            raise
                        me = await vault_flow.call(self.app, self._client.me)
                        self.app.screen_lock.apply(me.get("locks"))
                        if not self._live():
                            return
                        self.recording_login = True
                        self.acknowledged = False
                        self.query_one("#device-consent-warning").display = True
                        self.query_one("#login-consent").display = True
                        self._consent = asyncio.get_running_loop().create_future()
                        await self._consent
                        if not self._live():
                            return
            if not self._live() or self.cancelled:
                return
            self.login = await AsyncDeviceLogin.open(self._client._attach_url(), self.pending,
                                                     ssl_context=self._client._ssl())
            if self.cancelled:
                await self.login.cancel()
            while self._live():
                frame = await self.login.recv()
                if frame is None:
                    if self.login.close_code in (4015, 4017):
                        self.app.notify_api_error(ApiError(401, "unauthenticated", "Your TSB login ended."))
                    elif not self.ended:
                        self.finish(LOST)
                        self.emit({"t": "device_login_result", "outcome": "connect_failed",
                                   "error": "request_failed", "why": LOST})
                        self.terminal._refresh_fleet()
                    return
                if frame["t"] == "device_login_result":
                    if frame["outcome"] == "connected":
                        self.connected = True
                        try:
                            attachment = await self.login.attachment()
                        except (AttachRefused, HttpError, OSError, ConnectionClosed, HandshakeError,
                                ProtocolError, TimeoutError) as exc:
                            if (isinstance(exc, AttachRefused)
                                    and exc.code in (4015, 4017)):
                                self.app.notify_api_error(
                                    ApiError(401, "unauthenticated", "Your TSB login ended."))
                                return
                            await self._connected_without_attachment()
                            return
                        self.terminal.adopt_device_attachment(self.slot, attachment)
                        self.adopted = True
                        await self._complete(attachment.session)
                        return
                    self.finish(TOO_OLD if self._too_old else OUTCOMES.get(frame["outcome"], "The device login failed."))
                    self.emit(frame)
                    return
                await self.render_frame(frame)
        except asyncio.CancelledError:
            raise
        except ApiError as exc:
            if self._live():
                self.finish(exc.message)
                self.app.notify_api_error(exc)
        except (HttpError, OSError, ConnectionClosed, HandshakeError, ProtocolError, TimeoutError):
            if self._live():
                self.finish(LOST)
                self.emit({"t": "device_login_result", "outcome": "connect_failed",
                           "error": "request_failed", "why": LOST})
                self.terminal._refresh_fleet()
        except Exception as exc:  # noqa: BLE001 — an unexpected error is told by state: never connect_failed
            # after the device accepted the login (ruling 2026-11-07). Logged as type + frames only: a message can
            # carry device text or an answer, so it reaches neither the person nor drive, and never exc_info.
            LOG.warning("unexpected %s\n%s", type(exc).__name__, "".join(traceback.format_tb(exc.__traceback__)))
            if self._live():
                if not self.connected:
                    reason = OUTCOMES["connect_failed"]
                    self.finish(reason)
                    self.emit({"t": "device_login_result", "outcome": "connect_failed",
                               "error": "request_failed", "why": reason})
                    self.terminal._refresh_fleet()
                elif not self.adopted:
                    await self._connected_without_attachment()
                else:
                    # The handoff decides (ruling 2026-11-07, round 3). Cleared: the slot keeps its attachment, the
                    # pane is hidden so the words go to the slot's status line (`_complete`'s own route), and drive's
                    # result is sent only if it has had none (`emit` drops it once `_complete` sent it).
                    if self.slot.device_gate:
                        # The handoff never completed: the pane never half-attaches. Close the slot (it detaches; the
                        # session lives on), tell the person as the declined-gate path does — a toast, since a closed
                        # slot shows no status row — and give drive its result only if it has had none. Attaching
                        # later from the sessions list goes through the ordinary attach and its recording gate.
                        self.terminal._close_slot(self.slot)
                        self.app.toast(UNATTACHED, title="connected")
                        self.emit({"t": "device_login_result", "outcome": "connected", "why": UNATTACHED})
                    else:
                        self.terminal.set_login_status(self.slot, POST_ADOPT)
                        self.emit({"t": "device_login_result", "outcome": "connected", "why": POST_ADOPT})
        finally:
            if not self.adopted:
                self.candidate = None
                if self.login is not None:
                    with contextlib.suppress(Exception):
                        if not self.ended and not self.connected:
                            await self.login.cancel()
                        await self.login.close()

    def emit(self, frame: dict) -> None:
        if self._emit is not None and self.pending is not None:
            event = {"ev": frame["t"], "device_login": self.pending["device_login_id"],
                     **{k: v for k, v in frame.items() if k not in ("t", "v")}}
            if self._too_old:
                event.update(error="not_available", why="drive is too old for this device login; update it.")
            self._emit(event)

    async def render_frame(self, frame: dict) -> None:
        t = frame["t"]
        if ((t == "device_login_hostkey" and frame.get("kind") != "first_use")
                or (t == "device_login_prompt" and frame.get("method") not in
                    ("banner", "password", "keyboard-interactive"))):
            self._too_old = True
            self.candidate = None
            self.status(TOO_OLD)
            await self.login.cancel()
            return
        if t == "request_refused":
            self.status(f"TSB refused the answer: {frame['reason']}.")
            self.query_one("#device-send").disabled = False
            self.emit(frame)
            return
        if t == "device_login_hostkey":
            self.hostkey_pending = True
            self.query_one("#device-hostkey", Static).update(
                f"Host key seen on first use\n{frame['fingerprint']}\nCheck it out of band before you trust this host.")
            for selector in ("#device-hostkey", "#device-accept", "#device-refuse"):
                self.query_one(selector).display = True
            self.emit(frame)
            return
        method = frame["method"]
        self.query_one("#device-text").display = True
        if method == "banner":
            self.query_one("#device-banner", Static).update(device_text(frame["text"]))
            self.emit(frame)
            return
        self.prompt = frame
        password = method == "password"
        has_prompts = password or bool(frame.get("prompts"))
        if has_prompts:
            refused = frame["attempt"] > self.last_answered_attempt > 0
            if refused:
                self.status("The device refused the answer.")
            else:
                self.status("")
        for selector in ("#own-password-label", "#own-password"):
            self.query_one(selector).display = password
        self.query_one("#own-password Input", Input).set_class(password, "device-answer")
        questions = self.query_one("#device-questions")
        await questions.remove_children()
        self.query_one("#device-name", Static).update(device_text(frame.get("name", "")))
        self.query_one("#device-instruction", Static).update(device_text(frame.get("instruction", "")))
        if not password:
            if frame.get("prompts"):
                if self.candidate is not None:
                    self.status(MORE_THAN_PASSWORD)
                self.candidate = None
                self.remember_allowed = False
            for prompt in frame.get("prompts", []):
                await questions.mount(Static(device_text(prompt["text"]), classes="device-label", markup=False),
                                      Input(password=not prompt["echo"], classes="device-answer"))
        self.query_one("#device-remember").display = password and self.remember_allowed and not self.app.under_drive
        self.query_one("#device-send").display = "prompt_id" in frame
        self.query_one("#device-send").disabled = False
        if self.query(".device-answer"):
            self.query(".device-answer").first().focus()
        self.emit(frame)

    async def answer_hostkey(self, accept: bool) -> None:
        if ((self.app.screen_lock.locked and not self.app.under_drive)
                or not self._live() or not self.hostkey_pending or self.ended):
            return
        self.hostkey_pending = False
        for selector in ("#device-hostkey", "#device-accept", "#device-refuse"):
            self.query_one(selector).display = False
        await self.login.answer_hostkey(accept)

    async def send_answers(self, answers: list[str] | None = None, prompt_id: int | None = None) -> None:
        if ((self.app.screen_lock.locked and not self.app.under_drive)
                or not self._live() or self.prompt is None or ("prompt_id" not in self.prompt and prompt_id is None)
                or self.ended or self.connected):
            return
        if answers is None:
            answers = [box.value for box in self.query(".device-answer")]
        for box in self.query(".device-answer"):
            box.value = ""
        if (self.prompt["method"] == "password" and self.remember_allowed and not self.app.under_drive
                and self.query_one("#device-remember", Checkbox).value):
            self.candidate = answers[0]
        if "attempt" in self.prompt:
            self.last_answered_attempt = self.prompt["attempt"]
        self.query_one("#device-send").disabled = True
        await self.login.answer(self.prompt["prompt_id"] if prompt_id is None else prompt_id, answers)

    async def _unlocked(self) -> None:
        if self.app.screen_lock.locked and not self.app.under_drive:
            ready = asyncio.get_running_loop().create_future()
            self.app.screen_lock.after_unlock(lambda: ready.set_result(None) if not ready.done() else None)
            await ready

    async def _connected_without_attachment(self) -> None:
        """`connected` is success (ruling 2026-11-03 03:36): the device accepted the login and the core opened the
        session, which lives on detached. The link was lost before `attached`, so the pane cannot attach to it. Keep
        and save the Remember candidate as on any `connected` (waiting for a locked screen's re-check), say it
        plainly, refresh the sessions list, and offer no "Open again": it never creates again for this case."""
        try:
            await self._unlocked()
            if not self._live():
                return
            candidate, self.candidate = self.candidate, None
            self.finish(UNATTACHED, open_again=False)
            self.terminal._refresh_fleet()
            if self._emit is not None:
                self.emit({"t": "device_login_result", "outcome": "connected", "why": UNATTACHED})
            if candidate is not None:
                result = await vault_flow.save_login(self.app, self.target, self.user, password=candidate,
                                                     reference=proposed_reference(self.target, self.user))
                if self._live():
                    self.status(f"{UNATTACHED} {save_message(result, self.target)}")
        finally:
            self.candidate = None

    async def _complete(self, session: dict) -> None:
        try:
            await self._unlocked()
            if not self._live():
                return
            recording = session.get("recording") or {}
            if recording.get("active"):
                if self._emit:
                    self._emit({"ev": "gate", "session": session["id"], "recording": recording["warning_text"]})
                if not self.app.under_drive:
                    gate = asyncio.get_running_loop().create_future()
                    self.app.push_screen(AttachConfirmScreen(session),
                                         lambda yes: gate.set_result(yes) if not gate.done() else None)
                    if not await gate:
                        # Declining the gate means this pane does not attach; the device accepted the login and
                        # the session exists (device-login step 21), so the Remember save runs as on any
                        # `connected`, after the lock's re-check. The slot closes last: a closed slot is not
                        # `_live` and shows no status row, so the save's outcome is told as a toast that
                        # outlives the slot.
                        candidate, self.candidate = self.candidate, None
                        if candidate is not None:
                            await self._unlocked()
                            if not self._live():
                                return
                            result = await vault_flow.save_login(self.app, self.target, self.user,
                                                                 password=candidate,
                                                                 reference=proposed_reference(self.target, self.user))
                            if self._live():
                                # The slot closes right after, and a closed slot shows no status row: the outcome
                                # goes through the app's toast, the notice every state change uses, which stays
                                # on screen for `toasts.seconds` whatever the slot does.
                                self.app.toast(save_message(result, self.target), title=result.status)
                        self.terminal._close_slot(self.slot)
                        return
            self.app.gated_sessions.add(session["id"])
            self.slot.device_gate = False
            self.display = False
            if self.slot is self.terminal.front:
                self.terminal.set_focus(None)
            self.terminal.request_device_size(self.slot)
            if self._connected_answer:
                fields = await self._connected_answer(session["id"])
                self._emit({"ev": "device_login_result", "device_login": self.pending["device_login_id"],
                            "outcome": "connected", **fields})
                # Drive has had its result: `emit`'s guard on a pending result sends drive no second one (ruling
                # 2026-11-07), so a later error in this step gets no attach-field-free result.
                self.pending = None
            if self.candidate is not None and self._live():
                result = await vault_flow.save_login(self.app, self.target, self.user, password=self.candidate,
                                                     reference=proposed_reference(self.target, self.user))
                if self._live():
                    self.terminal.set_login_status(self.slot, save_message(result, self.target))
        finally:
            self.candidate = None

    def finish(self, text: str, *, open_again: bool = True) -> None:
        self.ended = True
        self.candidate = None
        self.status(text)
        for box in self.query(Input):
            box.value = ""
            box.disabled = True
        for selector in ("#device-accept", "#device-refuse", "#device-send", "#device-remember"):
            self.query_one(selector).display = False
        self.query_one("#device-open-again").display = open_again

    async def cancel(self) -> None:
        self.cancelled = True
        self.candidate = None
        if hasattr(self, "_consent") and not self._consent.done():
            self._consent.cancel()
        if self.connected:
            # Nothing to cancel: the session exists (device-login's two cancel points are the core's).
            # Detach as for any session; the session lives on.
            self.terminal._close_slot(self.slot)
        elif self.login is not None and not self.ended:
            with contextlib.suppress(ConnectionClosed, OSError):
                await self.login.cancel()
        elif self.login is None:
            self.finish(OUTCOMES["cancelled"])

    def dispose(self) -> None:
        self.candidate = None
        for box in self.query(Input):
            box.value = ""
        if self._login_task is not None:
            self._login_task.cancel()

    def on_unmount(self) -> None:
        self.dispose()

    async def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.prevent_default()
            event.stop()
            await self.cancel()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "device-user":
            await self.submit_user()
        else:
            await self.send_answers()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if not event.value:
            self.candidate = None

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if self.app.screen_lock.locked:
            return
        which = event.button.id
        if which == "login-consent":
            self.acknowledged = True
            self.query_one("#device-consent-warning").display = False
            self.query_one("#login-consent").display = False
            if hasattr(self, "_consent"):
                if not self._consent.done():
                    self._consent.set_result(True)
            else:
                self._show_user(True)
        elif which in ("device-accept", "device-refuse"):
            await self.answer_hostkey(which == "device-accept")
        elif which == "device-send":
            await self.send_answers()
        elif which == "device-cancel":
            await self.cancel()
        elif which == "device-open-again":
            target, recording = self.target, self.recording_login
            self.terminal._close_slot(self.slot)
            if getattr(self.app, "device_login_unavailable", None) is self.app.client:
                self.terminal._new_session_dialog(target, "ssh")
            else:
                self.terminal.open_device_login(target, self.term, self.size_at_create, recording_login=recording)
