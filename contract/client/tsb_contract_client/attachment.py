"""An attached session stream, over the blocking or the asyncio transport (one codec, two I/O
models). Use AsyncAttachment inside an event loop — a Textual app — and Attachment elsewhere.

recv() returns terminal output only, as at contract-v0.1. recv_message() returns output and the
server frames this client knows (frames.SERVER_TYPES) alike: bytes for output, a dict for a frame."""

from __future__ import annotations

import ssl
import time
from collections import deque

from tsbc import _aws, _ws
from tsbc import _wsframe as wf

from . import frames


class AttachRefused(Exception):
    """The server closed the socket instead of answering `attached` (close codes: ws-frames x-close-codes)."""

    def __init__(self, code: int | None, reason: str) -> None:
        super().__init__(f"attach refused: {code} {reason}".strip())
        self.code = code
        self.reason = reason


def _attached(message: wf.Message) -> dict | None:
    if message.kind == "text":
        return frames.parse_server(message.data)
    return None


def _pop_bytes(pending: deque) -> bytes | None:
    """Remove and return the first queued output chunk, leaving queued server frames in place for
    recv_message()."""
    for index, item in enumerate(pending):
        if isinstance(item, bytes):
            del pending[index]
            return item
    return None


class Attachment:
    """Blocking. `recv()` returns the next chunk of terminal output, or None once the server
    has closed the stream (then `close_code` and `close_reason` say why)."""

    def __init__(self, ws: _ws.WebSocket, attached: dict, bound: float) -> None:
        self._ws = ws
        self._bound = bound
        self.session: dict = attached["session"]
        self.client_id: str = attached["client_id"]
        # Frames read while chasing a `size` answer: recv()/recv_message() drain these first so
        # output the PTY sent between a resize request and its answer is never lost.
        self._pending: deque[bytes | dict] = deque()

    @classmethod
    def open(cls, url: str, ticket: str, session_id: str, *, ssl_context: ssl.SSLContext | None,
             timeout: float, cols: int | None = None, rows: int | None = None) -> Attachment:
        ws = _ws.WebSocket.connect(url, ssl_context=ssl_context, timeout=timeout)
        try:
            ws.send_text(frames.encode(frames.attach_frame(ticket, session_id, cols, rows)))
            while True:
                message = ws.recv(timeout)
                if message.kind == "close":
                    raise AttachRefused(message.close_code, message.close_reason)
                attached = _attached(message)
                if attached is not None:
                    return cls(ws, attached, timeout)
        except BaseException:
            ws.close(timeout=0.5)
            raise

    @property
    def close_code(self) -> int | None:
        return self._ws.close_code

    @property
    def close_reason(self) -> str:
        return self._ws.close_reason

    @property
    def closed(self) -> bool:
        return self._ws.closed

    def send(self, data: bytes) -> None:
        self._ws.send_binary(data)

    def resize(self, cols: int, rows: int, timeout: float | None = None) -> dict | None:
        """Ask the broker for a target PTY size; returns the next `size` frame (cols, rows, reason),
        or None if none arrives within `timeout` (default: the socket bound; 0 sends and returns
        at once, as at contract-v0.2.1, for a v0.2.x core that never answers). Reads past output and
        other frames to reach it, queueing them so a later recv()/recv_message() still returns them.
        The applied size is also broadcast to every attached client (size-control.md step 12), so
        the frame returned may be another client's broadcast rather than this request's answer."""
        self._ws.send_text(frames.encode(frames.resize_frame(cols, rows)))
        return self._wait_for("size", self._bound if timeout is None else timeout)

    def recv(self, timeout: float | None = None) -> bytes | None:
        """Raises TimeoutError when `timeout` passes with nothing to return."""
        queued = _pop_bytes(self._pending)
        if queued is not None:
            return queued
        while not self._ws.closed:
            message = self._ws.recv(timeout)
            if message.kind == "binary":
                return message.data
            if message.kind == "close":
                return None
            # text frames: recv() returns output only; recv_message() returns the frames too
        return None

    def recv_message(self, timeout: float | None = None) -> bytes | dict | None:
        """The next chunk of output (bytes) or server frame this client knows (a dict: attached,
        prefix, clients, notice, quiet_prompt, token, input_refused, data_path, size,
        request_refused, stale, stale_prompt); None once the server has closed the stream. A frame
        of a type it does not know is skipped. Raises TimeoutError like recv()."""
        if self._pending:
            return self._pending.popleft()
        while not self._ws.closed:
            message = self._ws.recv(timeout)
            if message.kind == "binary":
                return message.data
            if message.kind == "close":
                return None
            frame = frames.parse_server(message.data) if message.kind == "text" else None
            if frame is not None:
                return frame
        return None

    def _wait_for(self, kind: str, wait: float) -> dict | None:
        """The next known server frame of type `kind`; None if the stream closes or nothing of that
        type arrives within `wait` seconds in all (one deadline, not one per frame read). Output
        chunks and other known frames are queued for recv()/recv_message() rather than dropped."""
        deadline = time.monotonic() + wait
        try:
            while not self._ws.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                message = self._ws.recv(remaining)
                if message.kind == "close":
                    return None
                if message.kind == "binary":
                    self._pending.append(message.data)
                    continue
                if message.kind == "text":
                    frame = frames.parse_server(message.data)
                    if frame is None:
                        continue
                    if frame.get("t") == kind:
                        return frame
                    self._pending.append(frame)
        except TimeoutError:
            # No answer within the bound (e.g. a v0.2.x core that never sends `size`): return None,
            # as documented, rather than raising.
            return None
        return None

    def answer_quiet_prompt(self, prompt_id: str, kill: bool) -> None:
        """Answer a quiet_prompt frame; the core accepts it only from a client it asked."""
        self._ws.send_text(frames.encode(frames.quiet_prompt_answer_frame(prompt_id, kill)))

    def handoff(self, to: str | None) -> None:
        """Move the control token. `to` names the destination client_id, None revokes it to escrow,
        and `to` equal to this attachment's own client_id is the owner's explicit take-over."""
        self._ws.send_text(frames.encode(frames.handoff_frame(to)))

    def answer_stale(self, prompt_id: str, choice: str) -> None:
        """Answer a stale_prompt: choice is one of the sigil's stale_choice enum."""
        self._ws.send_text(frames.encode(frames.stale_answer_frame(prompt_id, choice)))

    def close(self) -> None:
        self._ws.close()


class AsyncAttachment:
    """The same, for an asyncio event loop: every I/O method is a coroutine."""

    def __init__(self, ws: _aws.AsyncWebSocket, attached: dict, bound: float) -> None:
        self._ws = ws
        self._bound = bound
        self.session: dict = attached["session"]
        self.client_id: str = attached["client_id"]
        self._pending: deque[bytes | dict] = deque()

    @classmethod
    async def open(cls, url: str, ticket: str, session_id: str, *, ssl_context: ssl.SSLContext | None,
                   timeout: float, cols: int | None = None, rows: int | None = None) -> AsyncAttachment:
        ws = await _aws.AsyncWebSocket.connect(url, ssl_context=ssl_context, timeout=timeout)
        try:
            await ws.send_text(frames.encode(frames.attach_frame(ticket, session_id, cols, rows)))
            while True:
                message = await ws.recv(timeout)
                if message.kind == "close":
                    raise AttachRefused(message.close_code, message.close_reason)
                attached = _attached(message)
                if attached is not None:
                    return cls(ws, attached, timeout)
        except BaseException:
            await ws.close(timeout=0.5)
            raise

    @property
    def close_code(self) -> int | None:
        return self._ws.close_code

    @property
    def close_reason(self) -> str:
        return self._ws.close_reason

    @property
    def closed(self) -> bool:
        return self._ws.closed

    async def send(self, data: bytes) -> None:
        await self._ws.send_binary(data)

    async def resize(self, cols: int, rows: int, timeout: float | None = None) -> dict | None:
        """As Attachment.resize: the next `size` frame, or None if none arrives within `timeout`."""
        await self._ws.send_text(frames.encode(frames.resize_frame(cols, rows)))
        return await self._wait_for("size", self._bound if timeout is None else timeout)

    async def recv(self, timeout: float | None = None) -> bytes | None:
        queued = _pop_bytes(self._pending)
        if queued is not None:
            return queued
        while not self._ws.closed:
            message = await self._ws.recv(timeout)
            if message.kind == "binary":
                return message.data
            if message.kind == "close":
                return None
        return None

    async def recv_message(self, timeout: float | None = None) -> bytes | dict | None:
        """As Attachment.recv_message."""
        if self._pending:
            return self._pending.popleft()
        while not self._ws.closed:
            message = await self._ws.recv(timeout)
            if message.kind == "binary":
                return message.data
            if message.kind == "close":
                return None
            frame = frames.parse_server(message.data) if message.kind == "text" else None
            if frame is not None:
                return frame
        return None

    async def _wait_for(self, kind: str, wait: float) -> dict | None:
        """As Attachment._wait_for: queue non-matching output/frames; None on a closed stream or when
        the one deadline passes (a v0.2.x core that never answers `size`)."""
        deadline = time.monotonic() + wait
        try:
            while not self._ws.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                message = await self._ws.recv(remaining)
                if message.kind == "close":
                    return None
                if message.kind == "binary":
                    self._pending.append(message.data)
                    continue
                if message.kind == "text":
                    frame = frames.parse_server(message.data)
                    if frame is None:
                        continue
                    if frame.get("t") == kind:
                        return frame
                    self._pending.append(frame)
        except TimeoutError:
            return None
        return None

    async def answer_quiet_prompt(self, prompt_id: str, kill: bool) -> None:
        await self._ws.send_text(frames.encode(frames.quiet_prompt_answer_frame(prompt_id, kill)))

    async def handoff(self, to: str | None) -> None:
        """As Attachment.handoff."""
        await self._ws.send_text(frames.encode(frames.handoff_frame(to)))

    async def answer_stale(self, prompt_id: str, choice: str) -> None:
        """As Attachment.answer_stale."""
        await self._ws.send_text(frames.encode(frames.stale_answer_frame(prompt_id, choice)))

    async def close(self) -> None:
        await self._ws.close()
