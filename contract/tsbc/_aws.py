"""asyncio WebSocket client over `asyncio.open_connection`, for surfaces inside an event loop.

Same handshake and codec as `_ws.py` (both use `_wsframe`), with coroutines instead of blocking
calls, so a Textual app can await output without freezing its UI.
"""

from __future__ import annotations

import asyncio
import ssl

from . import _wsframe as wf

ConnectionClosed = wf.ConnectionClosed  # the one class both transports raise


class AsyncWebSocket:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 leftover: bytes = b"") -> None:
        self._reader = reader
        self._writer = writer
        self._parser = wf.FrameParser(expect_masked=False)
        self._assembler = wf.MessageAssembler()
        self._pending: list[wf.Frame] = self._parser.feed(leftover) if leftover else []
        self._send_lock = asyncio.Lock()
        self._sent_close = False
        self.closed = False
        self.close_code: int | None = None
        self.close_reason = ""

    @classmethod
    async def connect(cls, url: str, *, ssl_context: ssl.SSLContext | None = None,
                      timeout: float = 10.0,
                      headers: tuple[tuple[str, str], ...] = ()) -> AsyncWebSocket:
        tls, host, port, resource = wf.parse_ws_url(url)
        context = (ssl_context or ssl.create_default_context()) if tls else None
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=context, server_hostname=host if tls else None),
            timeout)
        try:
            nonce = wf.new_nonce()
            writer.write(wf.handshake_request(host, port, tls, resource, nonce, headers))
            await writer.drain()
            buf, split = b"", None
            while split is None:
                chunk = await asyncio.wait_for(reader.read(4096), timeout)
                if not chunk:
                    raise wf.HandshakeError("connection closed during the upgrade")
                buf += chunk
                split = wf.take_head(buf)
            head, rest = split
            wf.parse_handshake_response(head, nonce)
        except BaseException:
            writer.close()
            raise
        return cls(reader, writer, rest)

    async def send_text(self, text: str) -> None:
        await self._send(wf.OP_TEXT, text.encode("utf-8"))

    async def send_binary(self, data: bytes) -> None:
        await self._send(wf.OP_BINARY, bytes(data))

    async def send_ping(self, data: bytes = b"") -> None:
        await self._send(wf.OP_PING, data)

    async def _send(self, opcode: int, payload: bytes, *, closing: bool = False) -> None:
        if self.closed or (self._sent_close and not closing):
            raise ConnectionClosed("the WebSocket is closing or closed")
        async with self._send_lock:
            try:
                self._writer.write(wf.encode_frame(opcode, payload, mask=True))
                await self._writer.drain()
            except (OSError, ConnectionError) as exc:
                self._drop()
                raise ConnectionClosed(f"send failed: {exc}") from None

    async def _frame(self, timeout: float | None) -> wf.Frame | None:
        while not self._pending:
            try:
                data = await asyncio.wait_for(self._reader.read(65536), timeout)
            except asyncio.TimeoutError:
                raise TimeoutError("no WebSocket frame before the timeout") from None
            except (OSError, ConnectionError):
                data = b""
            if not data:
                self._drop()
                return None
            try:
                self._pending.extend(self._parser.feed(data))
            except wf.ProtocolError as exc:
                await self._fail(exc)
                raise
        return self._pending.pop(0)

    async def recv(self, timeout: float | None = None) -> wf.Message:
        """Next text, binary or close message; pings answered, pongs dropped (see _ws.recv)."""
        if self.closed:
            raise ConnectionClosed("the WebSocket is closed")
        while True:
            frame = await self._frame(timeout)
            if frame is None:
                self.close_code = self.close_code or wf.CLOSE_ABNORMAL
                return wf.Message("close", b"", self.close_code, self.close_reason)
            try:
                message = self._assembler.push(frame)
            except wf.ProtocolError as exc:
                await self._fail(exc)
                raise
            if message is None or message.kind == "pong":
                continue
            if message.kind == "ping":
                if not self._sent_close:
                    try:
                        await self._send(wf.OP_PONG, message.data)
                    except ConnectionClosed:
                        pass
                continue
            if message.kind == "close":
                self.close_code, self.close_reason = message.close_code, message.close_reason
                if not self._sent_close:
                    code = message.close_code if message.close_code != wf.CLOSE_NO_STATUS else None
                    await self._send_close(code)
                self._drop()
            return message

    async def _send_close(self, code: int | None, reason: str = "") -> None:
        self._sent_close = True
        try:
            await self._send(wf.OP_CLOSE, wf.encode_close(code, reason), closing=True)
        except ConnectionClosed:
            pass

    async def close(self, code: int = wf.CLOSE_NORMAL, reason: str = "", timeout: float = 2.0) -> None:
        if self.closed:
            return
        if not self._sent_close:
            await self._send_close(code, reason)
        try:
            while not self.closed:
                message = await self.recv(timeout)
                if message.kind == "close":
                    break
        except (TimeoutError, ConnectionClosed, wf.ProtocolError, OSError):
            pass
        self._drop()

    async def _fail(self, exc: wf.ProtocolError) -> None:
        if not self._sent_close:
            await self._send_close(exc.code)
        self.close_code = exc.code
        self._drop()

    def _drop(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self._writer.close()
            except (OSError, RuntimeError):
                pass
