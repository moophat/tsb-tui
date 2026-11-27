"""JSON over HTTP(S) with a cookie jar, stdlib only (http.client + ssl).

Shared by the runner and by `tsb_contract_client`. TLS verifies against the system CA store, or
against `ca_file` when given; there is no switch that turns verification off. Plain http:// is
refused except to a loopback host, so a login password and its cookie never cross a network in
the clear.
"""

from __future__ import annotations

import http.client
import json
import ssl
import time
import urllib.parse
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any

from .schema import loads

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_NO_BODY = object()


def require_tls_off_loopback(scheme: str, host: str) -> None:
    """Refuse a cleartext scheme (http, ws) to anything but a loopback host."""
    if scheme in ("http", "ws") and host not in LOCAL_HOSTS:
        raise ValueError(f"{scheme}:// is allowed only to a loopback host ({', '.join(sorted(LOCAL_HOSTS))}); "
                         f"use {'https' if scheme == 'http' else 'wss'}://")


def ssl_context(ca_file: str | None = None) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=ca_file)


@dataclass
class Cookie:
    name: str
    value: str
    attributes: dict[str, str] = field(default_factory=dict)  # lower-cased names

    def flag(self, name: str) -> bool:
        return name.lower() in self.attributes

    def attr(self, name: str) -> str | None:
        return self.attributes.get(name.lower())


def parse_set_cookie(header: str) -> Cookie | None:
    parts = [p.strip() for p in header.split(";")]
    name, sep, value = parts[0].partition("=")
    if not sep or not name.strip():
        return None
    attrs: dict[str, str] = {}
    for part in parts[1:]:
        key, _, val = part.partition("=")
        if key:
            attrs[key.strip().lower()] = val.strip()
    return Cookie(name.strip(), value.strip(), attrs)


def _expired(cookie: Cookie) -> bool:
    max_age = cookie.attr("max-age")
    if max_age is not None:
        try:
            return int(max_age) <= 0
        except ValueError:
            return False
    expires = cookie.attr("expires")
    if expires:
        try:
            return parsedate_to_datetime(expires).timestamp() <= time.time()
        except (TypeError, ValueError):
            return False
    return False


class CookieJar:
    """Name → cookie for one origin. Held in memory only; never written to disk."""

    def __init__(self) -> None:
        self.cookies: dict[str, Cookie] = {}

    def update(self, set_cookie_headers: list[str]) -> list[Cookie]:
        seen = []
        for header in set_cookie_headers:
            cookie = parse_set_cookie(header)
            if cookie is None:
                continue
            seen.append(cookie)
            if _expired(cookie):
                self.cookies.pop(cookie.name, None)
            else:
                self.cookies[cookie.name] = cookie
        return seen

    def header(self, secure_channel: bool) -> str:
        # A Secure cookie travels only over TLS, or to a loopback host, as a browser does.
        return "; ".join(f"{c.name}={c.value}" for c in self.cookies.values()
                         if secure_channel or not c.flag("secure"))

    def clear(self) -> None:
        self.cookies.clear()


@dataclass
class Response:
    status: int
    headers: list[tuple[str, str]]
    body: bytes
    set_cookies: list[Cookie] = field(default_factory=list)

    def header(self, name: str) -> str | None:
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None

    def json(self) -> Any:
        return loads(self.body.decode("utf-8")) if self.body else None


class HttpError(Exception):
    """Transport failure: connection refused, TLS failure, timeout. Not an HTTP error status."""


class StreamTimeout(HttpError):
    """A stream read waited past its timeout."""


class StreamLimitError(HttpError):
    """A stream broke a framing limit: a line past MAX_STREAM_LINE, or one event's data past
    MAX_EVENT_DATA. The stream is not read further."""


# The limits a reader of an event stream (the alert stream) holds a peer to. An AlertEvent is a few
# hundred bytes, so both leave wide room; without them a peer that never ends a line, or never ends
# an event, would grow the reader's memory without bound.
MAX_STREAM_LINE = 64 * 1024     # bytes in one line, its end included
MAX_EVENT_DATA = 1024 * 1024    # bytes of one event's data lines, joined


class StreamResponse:
    """A response read as it arrives (the alert stream): its status and headers, then its body a
    line at a time. The connection stays open until close()."""

    def __init__(self, conn: http.client.HTTPConnection, raw: http.client.HTTPResponse) -> None:
        self._conn, self._raw = conn, raw
        self.status = raw.status
        self.headers = raw.getheaders()

    def header(self, name: str) -> str | None:
        return next((v for k, v in self.headers if k.lower() == name.lower()), None)

    def settimeout(self, timeout: float | None) -> None:
        """Bound each later read (None: block)."""
        if self._conn.sock is not None:
            self._conn.sock.settimeout(timeout)

    def readline(self) -> bytes:
        """The next line, its end included; b"" at the end of the stream. A line longer than
        MAX_STREAM_LINE is StreamLimitError, a read timeout StreamTimeout, and any other transport
        failure HttpError."""
        try:
            line = self._raw.readline(MAX_STREAM_LINE + 1)
        except TimeoutError as exc:
            raise StreamTimeout(f"stream: no data within the read timeout ({exc})") from None
        except (OSError, http.client.HTTPException) as exc:
            raise HttpError(f"stream: {type(exc).__name__}: {exc}") from None
        if len(line) > MAX_STREAM_LINE:
            raise StreamLimitError(f"stream: a line longer than {MAX_STREAM_LINE} bytes")
        return line

    def read(self) -> bytes:
        try:
            return self._raw.read()
        except (OSError, http.client.HTTPException) as exc:
            raise HttpError(f"stream: {type(exc).__name__}: {exc}") from None

    def close(self) -> None:
        self._conn.close()


def next_event(stream: StreamResponse, deadline: float | None = None) -> tuple[str, str] | None:
    """The next server-sent event of a text/event-stream body (the WHATWG EventSource format), as
    (its type, its data lines joined by newlines); None at the end of the stream. A comment line
    (":" first, the keepalive) is skipped, an event with no data line is dropped, and so is one the
    stream ends before its blank line. One event's data past MAX_EVENT_DATA bytes is
    StreamLimitError; a line past MAX_STREAM_LINE, a timeout or a transport failure are readline's.
    `deadline` (time.monotonic()) bounds the whole wait, keepalives included: past it,
    StreamTimeout."""
    kind, data, size = "message", [], 0
    while True:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StreamTimeout("stream: no event before the deadline")
            stream.settimeout(remaining)
        raw = stream.readline()
        if not raw:
            return None
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "":
            if data:
                return kind, "\n".join(data)
            kind, size = "message", 0
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")  # one leading space, as EventSource strips it
        if name == "event":
            kind = value
        elif name == "data":
            size += len(value.encode("utf-8")) + 1
            if size > MAX_EVENT_DATA:
                raise StreamLimitError(f"stream: an event's data is longer than {MAX_EVENT_DATA} bytes")
            data.append(value)


class HttpClient:
    """One base URL (http:// or https://, optionally with a path prefix) and its cookie jar."""

    def __init__(self, base_url: str, *, ca_file: str | None = None, timeout: float = 10.0,
                 jar: CookieJar | None = None) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"base URL must be http:// or https:// with a host, got {base_url!r}")
        if parsed.query or parsed.fragment:
            raise ValueError("base URL must not carry a query or a fragment")
        require_tls_off_loopback(parsed.scheme, parsed.hostname)
        self.base_url = base_url
        self.tls = parsed.scheme == "https"
        self.host = parsed.hostname
        self.port = parsed.port or (443 if self.tls else 80)
        self.prefix = parsed.path.rstrip("/")
        self.ca_file = ca_file
        self.timeout = timeout
        self.jar = jar if jar is not None else CookieJar()
        self._context = ssl_context(ca_file) if self.tls else None

    @property
    def secure_channel(self) -> bool:
        return self.tls or self.host in LOCAL_HOSTS

    def request(self, method: str, path: str, *, body: Any = _NO_BODY,
                headers: dict[str, str] | None = None, use_cookies: bool = True,
                accept: str = "application/json") -> Response:
        """One request, read to its end. `accept` is the media type the request asks for — the
        operation's own (the alert stream is text/event-stream), application/json otherwise."""
        if not path.startswith("/"):
            raise ValueError(f"request path must start with '/': {path!r}")
        send_headers = {"Accept": accept}
        payload = None
        if body is not _NO_BODY:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            send_headers["Content-Type"] = "application/json"
        if use_cookies:
            cookie = self.jar.header(self.secure_channel)
            if cookie:
                send_headers["Cookie"] = cookie
        send_headers.update(headers or {})
        if self.tls:
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                self.host, self.port, timeout=self.timeout, context=self._context)
        else:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request(method, self.prefix + path, body=payload, headers=send_headers)
            raw = conn.getresponse()
            data = raw.read()
            header_list = raw.getheaders()
        except (OSError, http.client.HTTPException) as exc:
            raise HttpError(f"{method} {path}: {type(exc).__name__}: {exc}") from None
        finally:
            conn.close()
        set_cookie = [v for k, v in header_list if k.lower() == "set-cookie"]
        cookies = self.jar.update(set_cookie) if use_cookies else [
            c for c in (parse_set_cookie(h) for h in set_cookie) if c]
        return Response(raw.status, header_list, data, cookies)

    def stream(self, path: str, *, accept: str, timeout: float | None = None) -> StreamResponse:
        """GET a response to read as it arrives, with this client's cookie, asking for `accept`.
        `timeout` bounds each read (None: block), so a stream that sends a keepalive comment every
        heartbeat_interval needs one above it."""
        if not path.startswith("/"):
            raise ValueError(f"request path must start with '/': {path!r}")
        send_headers = {"Accept": accept}
        cookie = self.jar.header(self.secure_channel)
        if cookie:
            send_headers["Cookie"] = cookie
        if self.tls:
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                self.host, self.port, timeout=self.timeout, context=self._context)
        else:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request("GET", self.prefix + path, headers=send_headers)
            raw = conn.getresponse()
            if conn.sock is not None:
                conn.sock.settimeout(timeout)
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise HttpError(f"GET {path}: {type(exc).__name__}: {exc}") from None
        return StreamResponse(conn, raw)
