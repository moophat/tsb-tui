"""RFC 6455 WebSocket framing with no I/O: handshake strings, frame codec, message reassembly.

Both transports use it: `_ws.py` (blocking sockets, for the runner and scripts) and `_aws.py`
(asyncio streams, for a surface running inside an event loop). Stdlib only.

Clients mask every frame they send (§5.3) and reject masked frames from a server (§5.1).
Extensions are never negotiated, so any RSV bit is a protocol error.
"""

from __future__ import annotations

import base64
import hashlib
import os
import struct
import urllib.parse
from dataclasses import dataclass

from ._http import require_tls_off_loopback

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # RFC 6455 §1.3

OP_CONT, OP_TEXT, OP_BINARY = 0x0, 0x1, 0x2
OP_CLOSE, OP_PING, OP_PONG = 0x8, 0x9, 0xA
DATA_OPCODES = frozenset({OP_CONT, OP_TEXT, OP_BINARY})
CONTROL_OPCODES = frozenset({OP_CLOSE, OP_PING, OP_PONG})

CLOSE_NORMAL = 1000
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_NO_STATUS = 1005   # never sent: "no code in the close frame"
CLOSE_ABNORMAL = 1006    # never sent: "the connection dropped without a close frame"
CLOSE_INVALID_DATA = 1007
CLOSE_TOO_BIG = 1009

DEFAULT_MAX_MESSAGE = 16 * 1024 * 1024


class ProtocolError(Exception):
    """The peer broke RFC 6455. `code` is the close code to answer with."""

    def __init__(self, message: str, code: int = CLOSE_PROTOCOL_ERROR) -> None:
        super().__init__(message)
        self.code = code


class ConnectionClosed(Exception):
    """recv() or send() after the close handshake finished or the connection dropped. One class for
    both transports (_ws, _aws), so a surface catches it the same way whichever it uses."""


class HandshakeError(Exception):
    """The HTTP upgrade did not produce a WebSocket (wrong status, bad accept key)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Frame:
    fin: bool
    opcode: int
    payload: bytes


@dataclass(frozen=True)
class Message:
    """A complete message: kind is "text", "binary", "ping", "pong" or "close"."""
    kind: str
    data: bytes | str = b""
    close_code: int | None = None
    close_reason: str = ""


def parse_ws_url(url: str) -> tuple[bool, str, int, str]:
    """(tls, host, port, resource) for a ws://, wss://, http:// or https:// URL."""
    parts = urllib.parse.urlsplit(url)
    tls = {"ws": False, "http": False, "wss": True, "https": True}.get(parts.scheme)
    if tls is None or not parts.hostname:
        raise ValueError(f"not a WebSocket URL: {url!r}")
    require_tls_off_loopback("ws" if not tls else "wss", parts.hostname)
    resource = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return tls, parts.hostname, parts.port or (443 if tls else 80), resource


def new_nonce() -> str:
    return base64.b64encode(os.urandom(16)).decode("ascii")


def accept_for(nonce: str) -> str:
    """Sec-WebSocket-Accept for a Sec-WebSocket-Key nonce (RFC 6455 §4.2.2)."""
    digest = hashlib.sha1((nonce + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def handshake_request(host: str, port: int, tls: bool, resource: str, nonce: str,
                      extra_headers: tuple[tuple[str, str], ...] = ()) -> bytes:
    name = f"[{host}]" if ":" in host else host  # an IPv6 literal is bracketed in Host
    host_header = name if port == (443 if tls else 80) else f"{name}:{port}"
    lines = [
        f"GET {resource} HTTP/1.1",
        f"Host: {host_header}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {nonce}",
        "Sec-WebSocket-Version: 13",
        *(f"{k}: {v}" for k, v in extra_headers),
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


MAX_HANDSHAKE_HEAD = 65536


def take_head(buf: bytes) -> tuple[bytes, bytes] | None:
    """(head, rest) once buf holds the whole upgrade response head; None while it does not.

    The size limit is on the head alone. Bytes after the blank line already belong to the first
    frame — a server may send them in the same segment — so they never count against it."""
    head, sep, rest = buf.partition(b"\r\n\r\n")
    if sep:
        if len(head) > MAX_HANDSHAKE_HEAD:
            raise HandshakeError("upgrade response head too large")
        return head, rest
    if len(buf) > MAX_HANDSHAKE_HEAD + 3:  # the head so far, and at most three bytes of its terminator
        raise HandshakeError("upgrade response head too large")
    return None


def parse_handshake_response(head: bytes, nonce: str) -> dict[str, str]:
    """Validate a server's upgrade response head (up to, excluding, the blank line)."""
    lines = head.decode("latin-1").split("\r\n")
    status_parts = lines[0].split(" ", 2)
    if len(status_parts) < 2 or not status_parts[0].startswith("HTTP/1.1"):
        raise HandshakeError(f"not an HTTP/1.1 response: {lines[0]!r}")
    try:
        status = int(status_parts[1])
    except ValueError:
        raise HandshakeError(f"bad status line: {lines[0]!r}") from None
    headers: dict[str, str] = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers[key.strip().lower()] = value.strip()
    if status != 101:
        raise HandshakeError(f"upgrade refused with HTTP {status}", status)
    if headers.get("upgrade", "").lower() != "websocket":
        raise HandshakeError("response lacks 'Upgrade: websocket'", status)
    if "upgrade" not in [t.strip().lower() for t in headers.get("connection", "").split(",")]:
        raise HandshakeError("response lacks 'Connection: Upgrade'", status)
    if headers.get("sec-websocket-accept") != accept_for(nonce):
        raise HandshakeError("Sec-WebSocket-Accept does not match the key sent", status)
    if headers.get("sec-websocket-extensions"):
        raise HandshakeError("server negotiated an extension that was never offered", status)
    return headers


def mask_bytes(data: bytes, key: bytes) -> bytes:
    if not data:
        return b""
    repeated = (key * (len(data) // 4 + 1))[:len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(repeated, "big")).to_bytes(len(data), "big")


def encode_frame(opcode: int, payload: bytes = b"", *, fin: bool = True, mask: bool = True,
                 mask_key: bytes | None = None) -> bytes:
    if opcode not in DATA_OPCODES | CONTROL_OPCODES:
        raise ValueError(f"unknown opcode {opcode:#x}")
    if opcode in CONTROL_OPCODES and (len(payload) > 125 or not fin):
        raise ValueError("control frames carry at most 125 bytes and are never fragmented")
    head = bytearray([(0x80 if fin else 0) | opcode])
    length = len(payload)
    mask_bit = 0x80 if mask else 0
    if length < 126:
        head.append(mask_bit | length)
    elif length < 1 << 16:
        head.append(mask_bit | 126)
        head += struct.pack(">H", length)
    else:
        head.append(mask_bit | 127)
        head += struct.pack(">Q", length)
    if not mask:
        return bytes(head) + payload
    key = mask_key if mask_key is not None else os.urandom(4)
    if len(key) != 4:
        raise ValueError("a mask key is 4 bytes")
    return bytes(head) + key + mask_bytes(payload, key)


def encode_close(code: int | None = CLOSE_NORMAL, reason: str = "") -> bytes:
    if code is None:
        return b""
    data = struct.pack(">H", code) + reason.encode("utf-8")
    if len(data) > 125:
        raise ValueError("close reason too long for a control frame")
    return data


def _valid_close_code(code: int) -> bool:
    return code in (1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011) or 3000 <= code <= 4999


def decode_close(payload: bytes) -> tuple[int, str]:
    """(code, reason) from a close frame payload; an empty payload means 1005."""
    if not payload:
        return CLOSE_NO_STATUS, ""
    if len(payload) == 1:
        raise ProtocolError("close frame payload of one byte")
    code = struct.unpack(">H", payload[:2])[0]
    if not _valid_close_code(code):
        raise ProtocolError(f"invalid close code {code}")
    try:
        return code, payload[2:].decode("utf-8")
    except UnicodeDecodeError:
        raise ProtocolError("close reason is not UTF-8", CLOSE_INVALID_DATA) from None


class FrameParser:
    """Incremental frame parser. `expect_masked` is False on a client, True on a server."""

    def __init__(self, *, expect_masked: bool, max_frame: int = DEFAULT_MAX_MESSAGE) -> None:
        self.expect_masked = expect_masked
        self.max_frame = max_frame
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self._buf += data
        frames = []
        while True:
            frame = self._next()
            if frame is None:
                return frames
            frames.append(frame)

    def _next(self) -> Frame | None:
        buf = self._buf
        if len(buf) < 2:
            return None
        b0, b1 = buf[0], buf[1]
        fin, rsv, opcode = bool(b0 & 0x80), b0 & 0x70, b0 & 0x0F
        masked, length = bool(b1 & 0x80), b1 & 0x7F
        if rsv:
            raise ProtocolError("RSV bits set without a negotiated extension")
        if opcode not in DATA_OPCODES | CONTROL_OPCODES:
            raise ProtocolError(f"unknown opcode {opcode:#x}")
        if masked != self.expect_masked:
            raise ProtocolError("masked frame from a server" if masked else "unmasked frame from a client")
        offset = 2
        if length == 126:
            if len(buf) < 4:
                return None
            length = struct.unpack(">H", buf[2:4])[0]
            offset = 4
        elif length == 127:
            if len(buf) < 10:
                return None
            length = struct.unpack(">Q", buf[2:10])[0]
            if length >> 63:
                raise ProtocolError("64-bit length with the most significant bit set")
            offset = 10
        if opcode in CONTROL_OPCODES and (length > 125 or not fin):
            raise ProtocolError("control frame too long or fragmented")
        if length > self.max_frame:
            raise ProtocolError(f"frame of {length} bytes exceeds the limit", CLOSE_TOO_BIG)
        key = b""
        if masked:
            if len(buf) < offset + 4:
                return None
            key = bytes(buf[offset:offset + 4])
            offset += 4
        if len(buf) < offset + length:
            return None
        payload = bytes(buf[offset:offset + length])
        del buf[:offset + length]
        return Frame(fin, opcode, mask_bytes(payload, key) if masked else payload)


class MessageAssembler:
    """Joins fragmented data frames into messages; control frames pass straight through."""

    def __init__(self, max_message: int = DEFAULT_MAX_MESSAGE) -> None:
        self.max_message = max_message
        self._opcode: int | None = None
        self._parts: list[bytes] = []
        self._size = 0

    def push(self, frame: Frame) -> Message | None:
        if frame.opcode == OP_CLOSE:
            code, reason = decode_close(frame.payload)
            return Message("close", frame.payload, code, reason)
        if frame.opcode == OP_PING:
            return Message("ping", frame.payload)
        if frame.opcode == OP_PONG:
            return Message("pong", frame.payload)
        if frame.opcode == OP_CONT:
            if self._opcode is None:
                raise ProtocolError("continuation frame with no message in progress")
        elif self._opcode is not None:
            raise ProtocolError("new data frame while a fragmented message is in progress")
        else:
            self._opcode = frame.opcode
        self._parts.append(frame.payload)
        self._size += len(frame.payload)
        if self._size > self.max_message:
            raise ProtocolError(f"message exceeds {self.max_message} bytes", CLOSE_TOO_BIG)
        if not frame.fin:
            return None
        data, opcode = b"".join(self._parts), self._opcode
        self._opcode, self._parts, self._size = None, [], 0
        if opcode == OP_TEXT:
            try:
                return Message("text", data.decode("utf-8"))
            except UnicodeDecodeError:
                raise ProtocolError("text message is not UTF-8", CLOSE_INVALID_DATA) from None
        return Message("binary", data)
