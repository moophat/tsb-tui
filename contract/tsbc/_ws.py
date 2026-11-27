"""Blocking WebSocket client over `socket` + `ssl`, for the runner, scripts and rigs.

A surface running inside an event loop (the TUI inside Textual) must use `_aws.py` instead: a
blocking `recv()` there freezes the UI.
"""

from __future__ import annotations

import socket
import ssl

from . import _wsframe as wf

ConnectionClosed = wf.ConnectionClosed  # the one class both transports raise


class WebSocket:
    def __init__(self, sock: socket.socket, leftover: bytes = b"") -> None:
        self._sock = sock
        self._parser = wf.FrameParser(expect_masked=False)
        self._assembler = wf.MessageAssembler()
        self._pending: list[wf.Frame] = self._parser.feed(leftover) if leftover else []
        self._sent_close = False
        self.closed = False
        self.close_code: int | None = None
        self.close_reason = ""

    @classmethod
    def connect(cls, url: str, *, ssl_context: ssl.SSLContext | None = None, timeout: float = 10.0,
                headers: tuple[tuple[str, str], ...] = ()) -> WebSocket:
        tls, host, port, resource = wf.parse_ws_url(url)
        sock = socket.create_connection((host, port), timeout=timeout)
        try:
            if tls:
                context = ssl_context or ssl.create_default_context()
                sock = context.wrap_socket(sock, server_hostname=host)
            nonce = wf.new_nonce()
            sock.sendall(wf.handshake_request(host, port, tls, resource, nonce, headers))
            buf, split = b"", None
            while split is None:
                chunk = sock.recv(4096)
                if not chunk:
                    raise wf.HandshakeError("connection closed during the upgrade")
                buf += chunk
                split = wf.take_head(buf)
            head, rest = split
            wf.parse_handshake_response(head, nonce)
        except BaseException:
            sock.close()
            raise
        return cls(sock, rest)

    def send_text(self, text: str) -> None:
        self._send(wf.OP_TEXT, text.encode("utf-8"))

    def send_binary(self, data: bytes) -> None:
        self._send(wf.OP_BINARY, bytes(data))

    def send_ping(self, data: bytes = b"") -> None:
        self._send(wf.OP_PING, data)

    def _send(self, opcode: int, payload: bytes) -> None:
        if self.closed or self._sent_close:
            raise ConnectionClosed("the WebSocket is closing or closed")
        try:
            self._sock.sendall(wf.encode_frame(opcode, payload, mask=True))
        except OSError as exc:
            self._drop()
            raise ConnectionClosed(f"send failed: {exc}") from None

    def _frames(self, timeout: float | None) -> wf.Frame:
        while not self._pending:
            self._sock.settimeout(timeout)
            try:
                data = self._sock.recv(65536)
            except TimeoutError:
                raise TimeoutError("no WebSocket frame before the timeout") from None
            except OSError:
                data = b""
            if not data:
                self._drop()
                return wf.Frame(True, wf.OP_CLOSE, b"")  # sentinel handled by recv()
            try:
                self._pending.extend(self._parser.feed(data))
            except wf.ProtocolError as exc:
                self._fail(exc)
                raise
        return self._pending.pop(0)

    def recv(self, timeout: float | None = None) -> wf.Message:
        """Next text, binary or close message. Pings are answered; pongs are dropped.

        Returns a "close" message once, when the peer closes or the connection drops (code 1006);
        after that, raises ConnectionClosed.
        """
        while True:
            if self.closed:
                raise ConnectionClosed("the WebSocket is closed")
            frame = self._frames(timeout)
            if self.closed:  # dropped without a close frame
                self.close_code = self.close_code or wf.CLOSE_ABNORMAL
                return wf.Message("close", b"", self.close_code, self.close_reason)
            try:
                message = self._assembler.push(frame)
            except wf.ProtocolError as exc:
                self._fail(exc)
                raise
            if message is None or message.kind == "pong":
                continue
            if message.kind == "ping":
                if not self._sent_close:
                    try:
                        self._sock.sendall(wf.encode_frame(wf.OP_PONG, message.data, mask=True))
                    except OSError:
                        self._drop()
                        self.close_code = wf.CLOSE_ABNORMAL
                        return wf.Message("close", b"", self.close_code, self.close_reason)
                continue
            if message.kind == "close":
                self.close_code, self.close_reason = message.close_code, message.close_reason
                if not self._sent_close:
                    self._send_close(message.close_code if message.close_code != wf.CLOSE_NO_STATUS else None)
                self._drop()
            return message

    def _send_close(self, code: int | None, reason: str = "") -> None:
        self._sent_close = True
        try:
            self._sock.sendall(wf.encode_frame(wf.OP_CLOSE, wf.encode_close(code, reason), mask=True))
        except OSError:
            pass

    def close(self, code: int = wf.CLOSE_NORMAL, reason: str = "", timeout: float = 2.0) -> None:
        """Start (or finish) the close handshake and release the socket."""
        if self.closed:
            return
        if not self._sent_close:
            self._send_close(code, reason)
        try:
            while not self.closed:
                message = self.recv(timeout)
                if message.kind == "close":
                    break
        except (TimeoutError, ConnectionClosed, wf.ProtocolError, OSError):
            pass
        self._drop()

    def _fail(self, exc: wf.ProtocolError) -> None:
        if not self._sent_close:
            self._send_close(exc.code)
        self.close_code = exc.code
        self._drop()

    def _drop(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self._sock.close()
            except OSError:
                pass
