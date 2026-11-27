"""A device login's socket (Release B; protocols/device-login.md): the device's own SSH login questions, in the pane,
before any session exists. Async only: the TUI and drive are async.

    pending = client.create_device_login(target_id, "root", "xterm-256color", 120, 40)   # the 202's body
    login = await AsyncDeviceLogin.open(url, pending, ssl_context=None)
    frame = await login.recv()          # device_login_hostkey, device_login_prompt, device_login_result
    await login.answer_hostkey(True)
    await login.answer(frame["prompt_id"], ["an answer"])
    ...
    att = await login.attachment()      # after device_login_result {outcome: connected}

An answer goes into the frame it is sent in and nowhere else: this client never logs one, never keeps one and never puts
one in an exception. The device's text is plain text; the surface renders it inside a frame marked "device login".
"""

from __future__ import annotations

import ssl

from tsbc import _aws

from . import frames
from .attachment import AsyncAttachment, AttachRefused, _attached

OPEN_TIMEOUT = 10.0   # the connect and the attach's own answer; a person's answers are waited for without a bound


class DeviceLoginError(Exception):
    """The device login is not in a state for the call: `attachment()` before `connected`, or a call after the
    socket was handed to the attachment."""


class AsyncDeviceLogin:
    def __init__(self, ws: _aws.AsyncWebSocket) -> None:
        self._ws = ws
        self._connected = False
        self._handed_over = False

    @classmethod
    async def open(cls, url: str, pending: dict, *, ssl_context: ssl.SSLContext | None) -> AsyncDeviceLogin:
        """Connect and send `device_login_attach` with the 202's ticket and id. `pending` is the 202's body."""
        ws = await _aws.AsyncWebSocket.connect(url, ssl_context=ssl_context, timeout=OPEN_TIMEOUT)
        try:
            await ws.send_text(frames.encode(
                frames.device_login_attach_frame(pending["ticket"], pending["device_login_id"])))
        except BaseException:
            await ws.close(timeout=0.5)
            raise
        return cls(ws)

    @property
    def close_code(self) -> int | None:
        return self._ws.close_code

    @property
    def close_reason(self) -> str:
        return self._ws.close_reason

    @property
    def closed(self) -> bool:
        return self._ws.closed

    def _live(self) -> None:
        if self._handed_over:
            raise DeviceLoginError("the socket belongs to the attachment now")

    async def recv(self, timeout: float | None = None) -> dict | None:
        """The next device-login frame: device_login_hostkey, device_login_prompt, device_login_result or
        request_refused. None once the socket has closed (close_code says why: 4016 is the device login's end),
        and none after the result `connected`, whose next frame is `attached` (use attachment()). A frame of a
        type it does not know is skipped. Raises TimeoutError when `timeout` passes."""
        self._live()
        while not self._connected and not self._ws.closed:
            message = await self._ws.recv(timeout)
            if message.kind == "close":
                return None
            frame = frames.parse_device_login(message.data) if message.kind == "text" else None
            if frame is None:
                continue
            if frame["t"] == "device_login_result" and frame.get("outcome") == "connected":
                self._connected = True
            return frame
        return None

    async def answer_hostkey(self, accept: bool) -> None:
        self._live()
        await self._ws.send_text(frames.encode(frames.device_login_hostkey_answer_frame(accept)))

    async def answer(self, prompt_id: int, answers: list[str]) -> None:
        """One string per prompt (one for a password request); an empty string is sent as it is."""
        self._live()
        await self._ws.send_text(frames.encode(frames.device_login_answer_frame(prompt_id, answers)))

    async def cancel(self) -> None:
        self._live()
        await self._ws.send_text(frames.encode(frames.device_login_cancel_frame()))

    async def attachment(self) -> AsyncAttachment:
        """The same socket as an ordinary attach, built from its `attached` frame. Raises DeviceLoginError before
        `device_login_result` `connected` was received; AttachRefused if the socket closes before `attached`. A wait that
        fails (a timeout, a receive error, a cancel, a close) closes the socket before it raises, as the ordinary
        attachment opener does: the socket passes to the attachment only with a valid `attached` frame."""
        self._live()
        if not self._connected:
            raise DeviceLoginError("attachment() before device_login_result connected: no session exists yet")
        try:
            while True:
                message = await self._ws.recv(OPEN_TIMEOUT)
                if message.kind == "close":
                    raise AttachRefused(message.close_code, message.close_reason)
                attached = _attached(message)
                if attached is not None and attached.get("t") == "attached":
                    attachment = AsyncAttachment(self._ws, attached, OPEN_TIMEOUT)
                    self._handed_over = True
                    return attachment
        except BaseException:
            await self._ws.close(timeout=0.5)
            raise

    async def close(self) -> None:
        await self._ws.close()
