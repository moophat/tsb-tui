"""`python -m tsbc serve` — a stdlib fixture server the web lane builds against.

It is the test stub (`tests/stub_core.py` + `tests/stub_server.py`) moved into the shipped
package so both the tests and the served app share one implementation (DRY). It speaks plain HTTP
on loopback, seeds only synthetic credentials, and answers every scenario with a canned golden
dump — there is no shell and no VT. The `BUGS` fault toggles that let the tests prove a wrong
provider goes red live only in `tests/`, never here.

Scenarios are selected by the session's `target_id`:

    normal       the default: a session whose dump is whatever output it has recorded
    alt-screen   a session whose dump already carries the alternate-screen modes a C4 golden
                 entry/exit leaves behind, so a reattaching client renders the alt screen at once
    stale-pending one whose first attach is told a `grid_lost` stale state awaits a choice
    two-client   a session that admits two clients and moves the token on hand-off (the stub
                 already does); the scenario just names it
    telnet-scenario a telnet record, whose session create answers exactly as `normal` does

`GET /targets` and `GET /targets/{id}` answer from `TARGETS` below: the scenario targets are `ssh`
records with documentation addresses (RFC 5737 `192.0.2.0/24`, which no CI grep for a node address
matches), plus one `telnet` record and one whose `name` carries Rich markup, so a surface's text
rendering can be tested. In this fixture app every logged-in principal may see every target; the
core's visibility gate is graded against the core by the C1 fixtures.

`--static DIR` serves a web app from the same origin, which the cross-origin refusal and the
`SameSite=Strict` cookie would otherwise lock a browser out of.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import http.server
import json
import re
import secrets
import socket
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

from . import _wsframe as wf
from ._binfmt import rfc3339_ns
from ._serve_import import ImportMixin
from ._serve_vault import VaultMixin

WARNING = "This session is recorded byte-perfect. Anything typed will be stored as-is."
# A per-recipient send bound: a broadcast holds the app lock, so a client that stops reading must
# not block other clients or the HTTP/session calls waiting on that lock (C2 broadcast reliability).
SEND_TIMEOUT = 5.0
INSPECT_READ_BUDGET = 1 << 20  # most bytes one non-waiting inspection of a socket reads
INSPECT_HELD_CAP = 1 << 20  # inspection reads no more while this much payload is held unread
PASSWORD = "FIXTURE-NOT-A-PASSWORD"
USER, PEER = "fixture-user", "fixture-peer"
TARGET, NOCRED = "fixture-target", "fixture-nocred"
# A principal that holds session.join.ro only: it may attach and watch, never hold the token
# (PTY §7.1). Synthetic, like every credential here.
VIEWER = "fixture-viewer"

# broker.health is granted to the superadmin only, as the deploy's policy template does.
BROKER_HEALTH = frozenset({USER})
ALERTS = (b": keepalive\n\n"
          b"event: alert\ndata: {\"alert_id\": \"fixture-alert-1\", \"kind\": \"supervisor_unresponsive\", "
          b"\"state\": \"raised\", \"at\": \"2026-10-01T02:00:30Z\", \"supervisor_id\": \"fixture-sup-1\"}\n\n"
          b"event: x-later\ndata: {}\n\n"
          b"event: alert\ndata: {\"alert_id\": \"fixture-alert-1\", \"kind\": \"supervisor_unresponsive\",\n"
          b"data:  \"state\": \"cleared\", \"at\": \"2026-10-01T02:07:00Z\", \"supervisor_id\": \"fixture-sup-1\"}\n\n")
ENDED = ("killed", "terminated")
UP = ("live", "detached")  # states of a session whose connection's transport is up
# The target's size bounds (C2 decision 8, 5.50): a request below the floor clamps up, above the
# ceiling clamps down.
FLOOR_COLS, FLOOR_ROWS = 40, 12
CEIL_COLS, CEIL_ROWS = 200, 60

# Scenario target ids. `normal` (and any other id) is the plain stub session; the others carry a
# canned preamble dump and, for stale-pending, a stale state announced on the first attach.
SCENARIOS = ("normal", "alt-screen", "stale-pending", "two-client", "telnet-scenario")
# The alternate-screen modes a C4 golden enter/exit leaves: the sequence a fresh surface must
# render to show the screen the target is on (C2 decision 3, F7).
ALT_SCREEN_DUMP = b"\x1b[?1049h\x1b[?1h\x1b[?2004h\x1b[?1000h\x1b[?1006h\x1b[?1004h\x1b=\x1b[H\x1b[2J"
# The largest request body the fixture server reads; a larger Content-Length is refused 422.
MAX_BODY = 1 << 20
MAX_IMPORT_BODY = ((4 * 1024 * 1024 + 2) // 3) * 4 + 8192
_UNPARSEABLE_BODY = object()


def _account_write(method: str, path: str) -> bool:
    path = urllib.parse.urlsplit(path).path
    parts = path.strip("/").split("/")
    return method == "POST" and (path in ("/accounts", "/auth/password") or (
        len(parts) == 3 and parts[0] == "accounts" and parts[2] in ("disable", "enable", "password")))

# SessionLogin's bounds (openapi/tsb.openapi.json): the user in characters, the password in UTF-8 bytes.
LOGIN_USER_MAX = 256
LOGIN_PASSWORD_MAX = 1024
STALE_TEXT = "TSB: lost output - the screen may be wrong. Choose:"

# Release prompt proposals, served as text and never compiled by the core.
VENDOR_PROMPT_TEMPLATES = {'cisco-ios': '[A-Za-z0-9._-]+(\\([A-Za-z0-9._-]+\\))?[>#]', 'cisco-ios-xr': '[A-Za-z0-9]+/[0-9A-Za-z]+/[0-9A-Za-z]+/[0-9A-Za-z]+:[A-Za-z0-9._-]+(\\([A-Za-z0-9._-]+\\))?#', 'cisco-nxos': '[A-Za-z0-9._-]+(\\([A-Za-z0-9._-]+\\))?[>#]', 'juniper-junos': '[A-Za-z0-9._-]+@[A-Za-z0-9._-]+[>#]', 'arista-eos': '[A-Za-z0-9._-]+(\\([A-Za-z0-9._,:+-]+\\))?[>#]', 'huawei-vrp': '(HRP_[MS])?(<[A-Za-z0-9._-]+>|\\[[~*]?[A-Za-z0-9._/-]+\\])', 'mikrotik-routeros': '\\[[A-Za-z0-9._-]+@[A-Za-z0-9._-]+\\]( /[A-Za-z0-9._/-]+)? >', 'fortinet-fortios': '[A-Za-z0-9._-]+( \\([A-Za-z0-9._-]+\\))? [#$]', 'paloalto-panos': '[A-Za-z0-9._-]+@[A-Za-z0-9._-]+(\\([A-Za-z0-9._-]+\\))?[>#]', 'posix-shell': '([^ \\t]+ )?[$#]'}

# The target types GET /targets' `type` filter accepts, and the largest `limit` it answers.
TARGET_TYPES = ("ssh", "telnet", "serial", "echo")
MAX_TARGET_LIMIT = 500
# The shapes GET /targets holds its filter values to, copied from the schemas: a shipped `tsbc`
# carries no schemas (README "Stdlib only"), so `_serve.py` cannot load them. `tests/test_serve.py`
# compares each copy against schemas/inventory-target.schema.json and schemas/common.schema.json.
# A `tags` item: no comma (the index's TAG separator), no control character, 1..128.
TAG_ITEM_RE = re.compile(r"^[^,\u0000-\u001f\u007f-\u009f]+$")
TAG_ITEM_MAX = 128
# group: text, no control character, 1..256 (the same shape as a target file's group).
GROUP_RE = re.compile(r"^[^\u0000-\u001f\u007f-\u009f]+$")
GROUP_MAX = 256
# discovered_by: common#/$defs/id (also the shape of the {id} path segment).
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHARE_ENTRY_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._@-]{0,127}|[a-z][a-z0-9_]{0,31}:[A-Za-z0-9][A-Za-z0-9._@-]{0,127})$")
CURSOR_MAX = 256
SEEN = "2026-10-17T08:30:00Z"
# The inventory the two read operations answer from, one record per target in the TargetRecord shape
# (openapi/tsb.openapi.json). Every address is in RFC 5737's documentation range 192.0.2.0/24, so no
# CI grep for a node address matches one. `rich-name` carries Rich markup in its name, so a surface
# can be tested rendering a target's strings as text and never as markup.
TARGETS: dict[str, dict] = {
    "alt-screen": {"id": "alt-screen", "type": "ssh", "name": "alt screen", "group": "scenario",
                   "tags": ["alt-screen", "scenario"], "discovered_by": "manual", "last_seen": SEEN,
                   "host": "192.0.2.21", "port": 22},
    "fixture-nocred": {"id": "fixture-nocred", "type": "ssh", "name": "no credential", "group": "lab",
                       "tags": ["nocred"], "discovered_by": "manual", "last_seen": SEEN,
                       "host": "192.0.2.11", "port": 22},
    "fixture-target": {"id": "fixture-target", "type": "ssh", "name": "fixture target", "group": "lab",
                       "tags": ["scenario", "shell"], "discovered_by": "manual", "last_seen": SEEN,
                       "host": "192.0.2.10", "port": 22},
    "normal": {"id": "normal", "type": "ssh", "name": "normal", "group": "scenario", "tags": ["scenario"],
               "discovered_by": "manual", "last_seen": SEEN, "host": "192.0.2.20", "port": 22},
    "rich-name": {"id": "rich-name", "type": "ssh", "name": "[bold red]rich[/bold red]", "group": "lab",
                  "tags": ["markup"], "discovered_by": "manual", "last_seen": SEEN,
                  "host": "192.0.2.40", "port": 22},
    "stale-pending": {"id": "stale-pending", "type": "ssh", "name": "stale pending", "group": "scenario",
                      "tags": ["scenario", "stale"], "discovered_by": "manual", "last_seen": SEEN,
                      "host": "192.0.2.22", "port": 22},
    "telnet-scenario": {"id": "telnet-scenario", "type": "telnet", "name": "telnet scenario",
                        "group": "scenario", "tags": ["scenario", "telnet"], "discovered_by": "manual",
                        "last_seen": SEEN, "host": "192.0.2.30", "port": 23},
    "two-client": {"id": "two-client", "type": "ssh", "name": "two client", "group": "scenario",
                   "tags": ["scenario"], "discovered_by": "manual", "last_seen": SEEN,
                   "host": "192.0.2.23", "port": 22},
}


class ServerWS:
    """Server side of one WebSocket: reads masked client frames, writes unmasked frames."""

    def __init__(self, rfile, wfile) -> None:
        self.rfile, self.wfile = rfile, wfile
        self.parser = wf.FrameParser(expect_masked=True)
        self.assembler = wf.MessageAssembler()
        self.pending: list[wf.Frame] = []
        self.closed = False

    def fail(self, code: int, reason: str = "") -> wf.Message:
        """End the connection after a malformed client frame: send one close frame carrying `code`
        (best effort) and mark the socket closed, so the handler runs its normal client cleanup."""
        if not self.closed:
            try:
                self.send(wf.OP_CLOSE, wf.encode_close(code, reason))
            except OSError:
                pass
            self.closed = True
        return wf.Message("close", b"", code, reason)

    def recv(self) -> wf.Message:
        while True:
            try:
                while not self.pending:
                    data = self.rfile.read1(65536)
                    if not data:
                        self.closed = True
                        return wf.Message("close", b"", wf.CLOSE_ABNORMAL)
                    self.pending.extend(self.parser.feed(data))
                message = self.assembler.push(self.pending.pop(0))
            except wf.ProtocolError as exc:  # a frame RFC 6455 forbids: close with its code
                return self.fail(exc.code, str(exc)[:100])
            if message is None:
                continue
            if message.kind == "ping":
                # Answer every ping with a pong carrying its payload, at any point the server reads
                # (attachment, the close handshake, live streaming), so a client or proxy probing
                # liveness never sees a dead attachment.
                self.send(wf.OP_PONG, message.data)
                continue
            return message

    def _fill_pending(self) -> bool:
        """Read what the socket has already received, without waiting, into `pending` (nothing is consumed). True when
        the peer's end of the stream has been seen. One call reads at most `INSPECT_READ_BUDGET` bytes and none while
        `INSPECT_HELD_CAP` of payload is held unread; past either bound it returns False and leaves the rest in the
        socket, where `recv` reads it later, in order."""
        sock = self._socket()
        if sock is None or self.closed:
            return False
        previous = sock.gettimeout()
        sock.settimeout(0.0)
        budget = INSPECT_READ_BUDGET
        try:
            while True:
                room = min(65536, budget, INSPECT_HELD_CAP - sum(len(f.payload) for f in self.pending))
                if room <= 0:
                    return False
                try:
                    data = self.rfile.read1(room)
                except (BlockingIOError, TimeoutError):
                    return False
                if not data:                          # EOF, or only nothing yet: a peek tells which
                    try:
                        return sock.recv(1, socket.MSG_PEEK) == b""
                    except (BlockingIOError, TimeoutError):
                        return False
                budget -= len(data)
                self.pending.extend(self.parser.feed(data))
        except wf.ProtocolError:
            return False                              # the next `recv` finds the same frame and fails the socket
        except OSError:
            return True                               # a reset is the peer gone
        finally:
            sock.settimeout(previous)

    def buffered_text(self) -> list[str]:
        """The text of every whole, unfragmented text frame the socket has already received but nobody has read,
        without waiting for more and without consuming any: a later `recv` still returns each of them."""
        self._fill_pending()
        texts = []
        for frame in self.pending:
            if frame.fin and frame.opcode == wf.OP_TEXT:
                try:
                    texts.append(frame.payload.decode("utf-8"))
                except UnicodeDecodeError:
                    pass
        return texts

    def peer_gone(self) -> bool:
        """The peer's close frame has been received, or its end of the stream seen, and nobody has read it yet. Waits
        for nothing and consumes nothing."""
        eof = self._fill_pending()
        return eof or any(frame.opcode == wf.OP_CLOSE for frame in self.pending)

    def _socket(self):
        """The underlying socket behind `wfile` (a `_SocketWriter` or a buffered wrapper)."""
        wf_ = self.wfile
        return getattr(wf_, "_sock", None) or getattr(getattr(wf_, "raw", None), "_sock", None)

    def send_raw(self, data: bytes) -> None:
        """Write one frame, but never block on a non-reading client for more than `SEND_TIMEOUT`
        (a broadcast runs while the app holds its lock, so one stalled recipient must not stall the
        others or the HTTP and session operations behind the same lock). The send timeout bounds the
        write only; it is restored immediately, so the same socket's blocking read is unaffected. On
        a timeout the client is marked closed and a `TimeoutError` (an `OSError`) is raised so the
        caller drops it."""
        if self.closed:
            raise TimeoutError("socket already closed")
        sock = self._socket()
        if sock is None:
            self.wfile.write(data)
            self.wfile.flush()
            return
        previous = sock.gettimeout()
        sock.settimeout(SEND_TIMEOUT)
        try:
            self.wfile.write(data)
            self.wfile.flush()
        except (TimeoutError, BlockingIOError):
            self.closed = True
            raise TimeoutError("send timed out: client is not reading") from None
        finally:
            sock.settimeout(previous)

    def send(self, opcode: int, payload: bytes, *, fin: bool = True) -> None:
        self.send_raw(wf.encode_frame(opcode, payload, fin=fin, mask=False))

    def send_text(self, text: str) -> None:
        self.send(wf.OP_TEXT, text.encode("utf-8"))

    def send_binary(self, data: bytes) -> None:
        self.send(wf.OP_BINARY, data)

    def close(self, code: int, reason: str = "") -> wf.Message:
        """Send a close frame and wait for the client's answer."""
        self.send(wf.OP_CLOSE, wf.encode_close(code, reason))
        while True:
            message = self.recv()
            if message.kind == "close":
                self.closed = True
                return message


class App:
    """Override what a test needs. Return (status, headers, body) from http()."""

    def http(self, method: str, path: str, headers: dict[str, str], body: Any) -> tuple[int, list, Any]:
        return 404, [], {"error": {"code": "session_not_found", "message": "no route in stub"}}

    def preflight(self, method: str, path: str, headers: dict[str, str]):
        return None

    ws_paths: tuple[str, ...] = ()

    def websocket(self, path: str, ws: ServerWS) -> None:
        ws.close(1011, "no websocket in stub")


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # keep output quiet
        pass

    def _dispatch(self, method: str) -> None:
        app: App = self.server.app  # type: ignore[attr-defined]
        static: Path | None = getattr(self.server, "static", None)
        headers = {k.lower(): v for k, v in self.headers.items()}
        upgrade = headers.get("upgrade", "").lower() == "websocket"
        if method == "GET" and upgrade and self.path in app.ws_paths:
            nonce = headers.get("sec-websocket-key", "")
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", wf.accept_for(nonce))
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            app.websocket(self.path, ServerWS(self.rfile, self.wfile))
            return
        preflight = getattr(app, "preflight", None)
        refusal = preflight(method, self.path, headers) if preflight is not None else None
        if refusal is not None:
            status, extra, reply = refusal
            data = json.dumps(reply).encode("utf-8")
            self.close_connection = True
            self.send_response(status)
            for key, value in extra:
                self.send_header(key, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        limit = MAX_IMPORT_BODY if urllib.parse.urlsplit(self.path).path == "/target-imports" else MAX_BODY
        try:
            length = int(headers.get("content-length", "0") or 0)
            if not 0 <= length <= limit:
                raise ValueError("out of range")
        except ValueError:
            self.close_connection = True  # the body's extent is unknown: the stream cannot be reused
            if not _account_write(method, self.path):
                self._reject(f"Content-Length must be a number of bytes, at most {limit}", self.path)
                return
            body = _UNPARSEABLE_BODY
        else:
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else None
            except ValueError:  # includes UnicodeDecodeError
                if not _account_write(method, self.path):
                    self._reject("the request body is not JSON", self.path)
                    return
                body = _UNPARSEABLE_BODY
        if static is not None and method == "GET" and not self.path.startswith(("/auth", "/sessions",
                                                                               "/targets", "/accounts", "/healthz",
                                                                               "/alerts", "/ws/")) \
                and self._serve_static(static):
            return
        status, extra, reply = app.http(method, self.path, headers, body)
        if hasattr(reply, "__next__"):  # a body sent as it is produced, with no length: a stream
            self._stream(status, extra, reply)
            return
        raw_body = isinstance(reply, bytes)  # an app's own body, its Content-Type among its headers
        data = reply if raw_body else b"" if reply is None else json.dumps(reply).encode("utf-8")
        self.send_response(status)
        for key, value in extra:
            self.send_header(key, value)
        if data and not raw_body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _reject(self, message: str, path: str = "") -> None:
        """A request the server cannot read at all: the contract's 422 invalid_request, structured."""
        data = json.dumps({"error": {"code": "invalid_request", "message": message}}).encode("utf-8")
        self.send_response(422)
        self.send_header("Content-Type", "application/json")
        if urllib.parse.urlsplit(path).path == "/target-imports":
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, root: Path) -> bool:
        """Serve one file below `root` for a GET the app has no route for. `..` never escapes root."""
        rel = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).lstrip("/")
        target = (root / rel).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return False
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            return False
        data = target.read_bytes()
        import mimetypes
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True

    def _stream(self, status: int, extra: list, chunks) -> None:
        """Send each chunk as the app yields it, and close the connection when it stops — or when
        the client goes away."""
        self.close_connection = True
        self.send_response(status)
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        try:
            for chunk in chunks:
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")


class StubServer:
    """`with StubServer(app) as url:` — serves on 127.0.0.1 on a free port."""

    def __init__(self, app: App, static: Path | None = None) -> None:
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.app = app  # type: ignore[attr-defined]
        self.httpd.static = static  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class ServeApp(ImportMixin, VaultMixin, App):
    """The served app: the stub core's C1/C2 behaviour, minus the test-only `BUGS` toggles, plus
    scenario preamble dumps chosen by `target_id`."""

    ws_paths = ("/ws/attach",)

    def __init__(self, recording: bool = True, ticket_ttl: int = 30,
                 linger: bool = True, after_attach: tuple[dict, ...] = (),
                 login_input: bool = False, device_logins_max: int = 8) -> None:
        self.recording = recording
        # policy.yaml `recording.login_input`: off unless a test turns it on. What a surface is told, and
        # what the create asks consent for, is the effective value (`login_recorded`).
        self.login_input = login_input
        # config.yaml `worker.device_logins_max` (1 to 64): the device logins one principal may have in progress.
        # 8 is the config template's value (C5/config-device-logins-max); `tsbc serve` takes no knobs and runs it.
        self.device_logins_max = device_logins_max
        self.ticket_ttl = ticket_ttl
        self.linger = linger
        self.after_attach = after_attach
        self.alerts_body = ALERTS
        self.lock = threading.Lock()
        self.logins: dict[str, str] = {}          # cookie value -> user
        # Synthetic account records for the account-management contract surface.
        self.users: dict[str, dict] = {u: {"username": u, "created_at": SEEN, "disabled": False, "password_hash": hashlib.sha256(PASSWORD.encode()).hexdigest()}
                                       for u in (USER, PEER, VIEWER)}
        self.tickets: dict[str, tuple[str, str, float]] = {}  # ticket -> (user, sid, expiry)
        self.sessions: dict[str, dict] = {}
        self.targets: dict[str, dict] = {tid: dict(rec) for tid, rec in TARGETS.items()}
        self.target_definitions: dict[str, dict] = {}
        # Ownership and share lists are fixture-server state.
        self.private_owners: dict[str, dict | str] = {}
        self.audit_events: list[dict] = []
        self.id_key = secrets.token_bytes(32)
        self.import_init()
        # The stub's stand-in for the core's visibility gate (the core's own gate is graded against the
        # core): ids here are invisible to every principal, so GET /targets never lists them nor names
        # one in a cursor. Empty by default, as every logged-in principal sees every target.
        self.invisible: set[str] = set()
        self.connections: dict[str, dict] = {}    # conn_id -> {owner, target_id, up, supplied_login}
        self.clients: dict[str, dict[str, ServerWS]] = {}  # sid -> client_id -> socket
        self.client_users: dict[str, str] = {}    # client_id -> the principal it attached as
        self.ticket_logins: dict[str, str] = {}   # ticket -> the login (cookie) that minted it
        self.client_logins: dict[str, str] = {}   # client_id -> the login (cookie) it attached through
        # Release B, the device login: a prompt-mode create's 202 holds one here until its ticket is redeemed
        # (dl_tickets: ticket -> (user, device_login_id, expiry)). device_logins holds the ones in progress and only
        # those: `_dl_end` drops a record at every end, which is also what gives the principal's cap place back.
        # device_login_frames keeps every device-login frame the server read, as the tests read it, with the
        # synthetic constant where an answer was (`_dl_kept`): never a real answer.
        self.dl_tickets: dict[str, tuple[str, str, float]] = {}
        self.device_logins: dict[str, dict] = {}
        self.device_login_frames: list[dict] = []
        self.requests: list[tuple[str, str, object]] = []
        self.frames: list[dict] = []
        self.accepts: list[tuple[str, str]] = []
        self.counter = 0
        # The vault release's operations (tsbc/_serve_vault.py): fixture-user has a vault and holds vault.admin.
        self.vault_init_state((USER,))
        self.screen_password = PASSWORD

    @property
    def login_recorded(self) -> bool:
        """Whether a login supplied now would be written to a recording: `recording.enabled` and
        `recording.login_input` both on. This is `Me.recording.login_input`, and the case the create asks the
        request's `recording_acknowledged` for."""
        return self.recording and self.login_input

    def has_client(self, client_id: str) -> bool:
        return any(client_id in clients for clients in self.clients.values())

    def sid_of(self, client_id: str) -> str | None:
        for sid, clients in self.clients.items():
            if client_id in clients:
                return sid
        return None

    # -- helpers --

    def _next(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"

    def _user(self, headers: dict) -> str | None:
        for part in headers.get("cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "tsb_session":
                return self.logins.get(value)
        return None

    @staticmethod
    def _err(status: int, code: str, message: str):
        return status, [], {"error": {"code": code, "message": message}}

    def _record(self, sess: dict) -> dict:
        rec = {k: sess[k] for k in ("id", "target_id", "connection_id", "session_type", "state", "tier",
                                    "owner", "control_holder", "created_at", "term", "cols", "rows")}
        rec["recording"] = ({"active": True, "warning_code": "recording.active", "warning_text": WARNING}
                            if self.recording else {"active": False})
        if sess["state"] not in ENDED:
            rec.update(ends_at="2026-09-30T12:00:00Z", ends_by="idle", ending=False)
        if sess["state"] not in ENDED:
            # C2 decision 5: a v0.3 core puts the attached-client set on every record not ended.
            rec["clients"] = self._client_list(sess["id"])
        return rec

    @staticmethod
    def _may_control(user: str) -> bool:
        """may_control: the client's principal holds session.join.rw (session-record.schema.json).
        The viewer login holds session.join.ro only, so it may never hold the token."""
        return "session.join.rw" in ServeApp._permissions(user)

    def _client_list(self, sid: str) -> list[dict]:
        return [{"client_id": cid, "user": self.client_users[cid], "may_control": self._may_control(self.client_users[cid])}
                for cid in self.clients.get(sid, {})]

    def _broadcast(self, sess: dict, send, to: list[str] | None = None) -> None:
        """Run `send(client_id, ws)` for every attached client (or those in `to`). A client whose
        send fails is dead: it is dropped at once, so a listing never shows it and its control
        token moves on, even while its read handler stays blocked and its own cleanup never runs."""
        dead = []
        for cid, ws in list(self.clients.get(sess["id"], {}).items()):
            if to is not None and cid not in to:
                continue
            try:
                send(cid, ws)
            except OSError:
                dead.append(cid)
        for cid in dead:
            if cid in self.clients.get(sess["id"], {}):
                self._drop_client(sess, cid, code=1001, reason="send failed")

    def _send_clients(self, sess: dict) -> None:
        """The `clients` frame to every attached client, on each attach, detach and removal."""
        frame = json.dumps({"t": "clients", "v": 1, "session_id": sess["id"],
                            "clients": self._client_list(sess["id"])})
        self._broadcast(sess, lambda _cid, ws: ws.send_text(frame))

    def _emit_output(self, sess: dict, data: bytes) -> None:
        """Record a target output frame and send it live to every attached client. Live bytes are
        exactly the frames a client's prefix did not cover (C2 decision 3)."""
        if not data:
            return
        sess["next_seq"] += 1
        sess["output"].append((sess["next_seq"], data))
        self._broadcast(sess, lambda _cid, ws: ws.send_binary(data))

    def _prefix_step(self, sess: dict) -> tuple[dict, bytes]:
        """The `prefix` text frame and its one binary dump (C2 decision 3)."""
        frames = sess["output"]
        through_seq = frames[-1][0] if frames else 0
        dump = b"".join(data for _seq, data in frames)
        if not dump:
            dump = b"\x1b[H\x1b[2J"  # a ground screen is still at least one byte
        if frames:
            first_seq = frames[0][0]
            scrollback = {"first_seq": first_seq,
                          "bytes": sum(len(data) for _seq, data in frames if _seq >= first_seq)}
        else:
            scrollback = {"first_seq": through_seq + 1, "bytes": 0}
        frame = {"t": "prefix", "v": 1, "through_seq": through_seq, "cols": sess["cols"],
                 "rows": sess["rows"], "scrollback": scrollback}
        return frame, dump

    def _send_token(self, sess: dict, reason: str) -> None:
        frame = json.dumps({"t": "token", "v": 1, "kind": "control",
                            "holder": sess["control_holder"], "reason": reason})
        self._broadcast(sess, lambda _cid, ws: ws.send_text(frame))

    @staticmethod
    def _build_stale_prompt_frame(prompt_id: str) -> dict:
        """Build a stale_prompt frame with the given prompt_id."""
        return {"t": "stale_prompt", "v": 1, "prompt_id": prompt_id,
                "code": "stale.choose", "text": STALE_TEXT,
                "choices": [{"choice": "wiggle", "text": "Redraw the screen (no guarantee)"},
                            {"choice": "ctrl_l", "text": "Send Ctrl-L (clears the screen, no guarantee)"},
                            {"choice": "continue", "text": "Keep going without a redraw (no guarantee the screen is right)"},
                            {"choice": "fresh_session", "text": "Start a fresh session"}]}

    @staticmethod
    def _valid_size(body: dict) -> bool:
        return all(isinstance(body.get(k), int) and not isinstance(body.get(k), bool)
                   and 1 <= body[k] <= 65535 for k in ("cols", "rows"))

    def _may_attach(self, user: str, sid: str) -> dict | None:
        """The one rule both the ws-ticket path and the output-page path use: a principal may reach
        a session exactly when it may attach to it. In the served fixture app any logged-in user may
        attach to any session that exists, so reachability is session existence; the contract's real
        rule (session.join.ro, and the owner, may page — the same right as attaching, 5.48) is graded
        against the core by the output-page fixtures. Returns the session, or None when the principal
        may not reach it — the caller refuses 404 session_not_found, the same answer the ticket path
        gives for a session that does not exist or the principal may not see."""
        if not set(self._permissions(user)) & {"session.join.ro", "session.join.rw"}:
            return None
        return self.sessions.get(sid)

    # -- HTTP --

    def preflight(self, method: str, path: str, headers: dict[str, str]):
        url = urllib.parse.urlsplit(path)
        path_only = url.path
        importing = method == "POST" and path_only == "/target-imports"
        writing_target = (
            (method in ("POST", "PUT", "DELETE") and (path_only == "/targets" or path_only.startswith("/targets/")))
            or (method == "GET" and path_only.startswith("/targets/") and path_only.endswith("/definition"))
        )
        if not importing and not writing_target:
            return None
        no_store = [("Cache-Control", "no-store")] if importing else []
        with self.lock:
            user = self._user(headers)
            if user is None:
                status, extra, body = self._err(401, "unauthenticated", "log in first")
                return status, extra + no_store, body
            origin = headers.get("origin")
            if origin and origin != "http://" + headers.get("host", ""):
                status, extra, body = self._err(403, "forbidden", "same origin is required")
                return status, extra + no_store, body
            if writing_target:
                user_perms = self._permissions(user)
                if "target.edit" not in user_perms and "target.private" not in user_perms:
                    status, extra, body = self._err(403, "forbidden", "target write permission is required")
                    return status, extra + no_store, body
                if url.query:
                    status, extra, body = self._err(422, "invalid_request", "query parameters are not accepted on target writes")
                    return status, extra + no_store, body
        return None

    def http(self, method, path, headers, body):
        with self.lock:
            clean_path = urllib.parse.urlsplit(path).path
            if ((method == "POST" and clean_path == "/target-imports")
                    or (method in ("POST", "PUT", "DELETE")
                        and (clean_path == "/targets" or clean_path.startswith("/targets/")))
                    or (method == "GET" and clean_path.startswith("/targets/") and clean_path.endswith("/definition"))):
                if self._user(headers) is None:
                    status, headers_out, response = self._err(401, "unauthenticated", "log in first")
                    return status, headers_out + ([('Cache-Control', 'no-store')] if clean_path == '/target-imports' else []), response
                origin = headers.get("origin")
                if origin and origin != "http://" + headers.get("host", ""):
                    status, headers_out, response = self._err(403, "forbidden", "same origin is required")
                    return status, headers_out + ([('Cache-Control', 'no-store')] if clean_path == '/target-imports' else []), response
            if method == "POST" and (path.startswith("/accounts") or _account_write(method, path)):
                if self._user(headers) is None:
                    return self._err(401, "unauthenticated", "log in first")
                origin = headers.get("origin")
                if origin and origin != "http://" + headers.get("host", ""):
                    return self._err(403, "forbidden", "same origin is required")
            self._sweep_tickets()      # an unattached device login ends with its ticket's lifetime, whoever asks next
            captured = body
            if urllib.parse.urlsplit(path).path == "/target-imports":
                # The request can contain a raw credential-bearing export; retain no body or token.
                captured = None
            elif path.startswith("/accounts") or path in ("/auth/password", "/auth/login", "/auth/screen-unlock"):
                captured = {k: (PASSWORD if k in ("password", "current_password", "new_password") else v)
                            for k, v in body.items()} if isinstance(body, dict) else None
            self.requests.append((method, path, captured))
            self.accepts.append((path, headers.get("accept", "")))
            url = urllib.parse.urlsplit(path)
            # keep_blank_values: `?tag=` is a blank value, which the filters refuse 422; dropped, it
            # would read as "no filter" and answer 200.
            return self._http(method, url.path, headers, body,
                              urllib.parse.parse_qs(url.query, keep_blank_values=True))

    def _http(self, method, path, headers, body, query=None):
        if (method, path) == ("GET", "/healthz"):
            return 200, [], {"status": "ok"}
        if (method, path) == ("POST", "/auth/login"):
            if not isinstance(body, dict) or not body.get("username") or not body.get("password"):
                return self._err(422, "invalid_request", "username and password are required")
            if not isinstance(body["username"], str) or not isinstance(body["password"], str):
                return self._err(422, "invalid_request", "username and password must be strings")
            rec = self.users.get(body["username"])
            supplied_hash = self._account_hash(body["password"])
            if rec is None or rec["disabled"] or supplied_hash != rec["password_hash"]:
                return self._err(401, "invalid_credentials", "unknown user or wrong password")
            cookie = secrets.token_urlsafe(24)
            self.logins[cookie] = body["username"]
            flags = "; Secure; SameSite=Strict; Path=/"
            flags = "; HttpOnly" + flags
            return 200, [("Set-Cookie", f"tsb_session={cookie}{flags}")], \
                {"username": body["username"], "locks": self.vault_locks()}

        user = self._user(headers)
        if user is None:
            return self._err(401, "unauthenticated", "log in first")
        if (method, path) == ("POST", "/auth/logout"):
            for cookie, who in list(self.logins.items()):
                if who == user and f"tsb_session={cookie}" in headers.get("cookie", ""):
                    del self.logins[cookie]
                    self.activity.pop(cookie, None)
                    self._dl_login_ended(cookie, revoked=False)
            return 204, [("Set-Cookie", "tsb_session=; Max-Age=0; Path=/")], None
        if (method, path) == ("GET", "/auth/me"):
            # recording.login_input is the effective value: true only while recording is enabled and the knob is on.
            return 200, [], {"username": user, "recording": {"login_input": self.login_recorded},
                             "locks": self.vault_locks(), "permissions": self._permissions(user)}
        # Account-management endpoints are an in-memory synthetic implementation for served surfaces.
        if path == "/accounts" and method in ("GET", "POST"):
            return self._accounts(user, method, body)
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "accounts" and parts[2] in ("disable", "enable", "password") and method == "POST":
            return self._account_action(user, urllib.parse.unquote(parts[1]), parts[2], body)
        if path == "/auth/password" and method == "POST":
            return self._account_action(user, user, "own-password", body, self._cookie(headers))

        if ((method, path) == ("POST", "/auth/screen-unlock")
                and self.users[user]["password_hash"] != self._account_hash(PASSWORD)
                and isinstance(body, dict) and isinstance(body.get("password"), str)):
            body = dict(body, password=(self.screen_password if self._account_hash(body["password"]) == self.users[user]["password_hash"] else "invalid synthetic password"))
        # Account-created logins have no policy grants. Keep the seeded fixture principals'
        # existing route behavior, while refusing session/target capabilities before lookup.
        if (not self._permissions(user)
                and (path in ("/sessions", "/targets", "/auth/ws-ticket")
                     or path.startswith(("/sessions/", "/targets/")))
                and not (method == "GET" and (path == "/targets"
                         or (path.startswith("/targets/") and path.count("/") == 2)))):
            return self._err(403, "forbidden", "a session or target permission is required")
        vault = self.vault_http(method, path, headers, body, query or {}, user)
        if vault is not None:
            return vault
        if (method, path) == ("GET", "/alerts"):
            if user not in BROKER_HEALTH:
                return self._err(403, "forbidden", "the alert stream needs broker.health")
            return 200, [("Content-Type", "text/event-stream")], self.alerts_body
        if (method, path) == ("POST", "/auth/ws-ticket"):
            if not isinstance(body, dict) or set(body) != {"session_id"}:
                return self._err(422, "invalid_request", "session_id is required")
            if self._may_attach(user, body["session_id"]) is None:
                return self._err(404, "session_not_found", "no such session")
            self._sweep_tickets()
            ticket = secrets.token_urlsafe(24)
            self.tickets[ticket] = (user, body["session_id"], time.monotonic() + self.ticket_ttl)
            self.ticket_logins[ticket] = self._cookie(headers) or ""
            return 200, [], {"v": 1, "ticket": ticket, "expires_in": self.ticket_ttl}
        if (method, path) == ("GET", "/sessions"):
            return self._list_sessions(user, query or {})
        if (method, path) == ("POST", "/target-imports"):
            return self.import_http(user, body, self._cookie(headers) or "")
        if (method, path) == ("POST", "/sessions"):
            return self._create(user, body, self._cookie(headers) or "")
        if (method, path) == ("GET", "/vendor-prompt-templates"):
            return 200, [], {"templates": [{"vendor": vendor, "prompt_pattern": pattern}
                                           for vendor, pattern in VENDOR_PROMPT_TEMPLATES.items()]}
        if method == "GET" and path.startswith("/vendor-prompt-templates/"):
            vendor = urllib.parse.unquote(path.removeprefix("/vendor-prompt-templates/"))
            if vendor not in VENDOR_PROMPT_TEMPLATES:
                return self._err(404, "vendor_template_not_found", "no such template")
            return 200, [], {"vendor": vendor, "prompt_pattern": VENDOR_PROMPT_TEMPLATES[vendor]}
        if (method, path) == ("GET", "/targets"):
            return self._list_targets(query or {}, user)
        if (method, path) == ("POST", "/targets"):
            return self._write_target(user, method, None, body, query or {})
        parts = path.strip("/").split("/")
        if parts[0] == "targets":
            tid = urllib.parse.unquote(parts[1]) if len(parts) > 1 else ""
            if len(parts) == 2 and method in ("PUT", "DELETE"):
                return self._write_target(user, method, tid, body, query or {})
            if len(parts) == 3 and parts[2] == "definition" and method == "GET":
                return self._write_target(user, method, tid, body, query or {})
        # The public record read shares the same percent-decoded id as the editor routes.
        if len(parts) == 2 and parts[0] == "targets" and method == "GET":
            return self._get_target(urllib.parse.unquote(parts[1]), user)
        if len(parts) >= 2 and parts[0] == "sessions":
            sess = self.sessions.get(parts[1])
            if sess is None:
                return self._err(404, "session_not_found", "no such session")
            if len(parts) == 2 and method == "GET":
                return 200, [], self._record(sess)
            if len(parts) == 2 and method == "DELETE":
                if user != sess["owner"]:
                    return self._err(403, "forbidden", "only the owner or session.terminate may end this session")
                self._kill(sess)
                return 204, [], None
            if len(parts) == 4 and parts[2] == "clients" and method == "DELETE":
                return self._remove_client(user, sess, parts[3])
            if len(parts) == 3 and parts[2] == "output" and method == "GET":
                if self._may_attach(user, sess["id"]) is None:
                    # The same rule and the same refusal as ticket minting: a principal that may not
                    # reach the session is told 404 session_not_found before any byte is read.
                    return self._err(404, "session_not_found", "no such session")
                return self._output_page(user, sess, query or {})
        return self._err(404, "session_not_found", "no route")

    @staticmethod
    def _account_hash(value) -> str:
        # Synthetic verifier only: this is not the core's argon2id implementation.
        try:
            return hashlib.sha256(value.encode("utf-8")).hexdigest() if isinstance(value, str) else ""
        except UnicodeEncodeError:
            return ""

    def _account_password_error(self, value, minimum: int = 15):
        if not isinstance(value, str) or not self._account_hash(value):
            return self._err(422, "invalid_request", "password must be UTF-8")
        if not value:
            return self._err(422, "invalid_request", "password must not be empty")
        if len(value) > 1024:
            return self._err(422, "invalid_request", "password must not exceed 1024 characters")
        if len(value) < minimum:
            return self._err(422, "invalid_request", "password is shorter than configured minimum")
        return None

    def _account_forbidden(self, user: str):
        if "account.admin" not in self._permissions(user):
            return self._err(403, "forbidden", "account.admin is required")
        return None

    def _accounts(self, user: str, method: str, body):
        refusal = self._account_forbidden(user)
        if refusal is not None:
            return refusal
        if method == "GET":
            return 200, [], {"accounts": [{"username": r["username"], "created_at": r["created_at"], "disabled": r["disabled"]}
                               for r in sorted(self.users.values(), key=lambda r: r["username"])]}
        if body is _UNPARSEABLE_BODY:
            return self._err(422, "invalid_request", "the request body cannot be parsed")
        if not isinstance(body, dict) or set(body) != {"username", "password"}:
            return self._err(422, "invalid_request", "username and password are required")
        name, password = body["username"], body["password"]
        if not isinstance(name, str) or not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$", name):
            return self._err(422, "invalid_request", "account name must match username shape")
        if name.lower() == "shared":
            return self._err(422, "invalid_request", "account name is reserved")
        refusal = self._account_password_error(password)
        if refusal is not None:
            return refusal
        if name in self.users:
            return self._err(409, "user_exists", "username already exists")
        self.users[name] = {"username": name, "created_at": SEEN, "disabled": False, "password_hash": self._account_hash(password)}
        account = {"username": name, "created_at": SEEN, "disabled": False}
        return 201, [], {"account": account, "permissions": self._permissions(name)}

    def _account_action(self, actor: str, name: str, action: str, body, keep: str | None = None):
        if action != "own-password":
            refusal = self._account_forbidden(actor)
            if refusal is not None:
                return refusal
        if body is _UNPARSEABLE_BODY:
            return self._err(422, "invalid_request", "the request body cannot be parsed")
        if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$", name):
            return self._err(422, "invalid_request", "account name must match username shape")
        if name.lower() == "shared":
            return self._err(422, "invalid_request", "account name is reserved")
        if action != "disable":
            expected = {"current_password", "new_password"} if action == "own-password" else {"password"}
            if not isinstance(body, dict) or set(body) != expected:
                return self._err(422, "invalid_request", "password request is outside its shape")
            if action == "own-password":
                refusal = self._account_password_error(body["current_password"], minimum=1)
                if refusal is not None:
                    return refusal
            pw = body["new_password"] if action == "own-password" else body["password"]
            refusal = self._account_password_error(pw)
            if refusal is not None:
                return refusal
        rec = self.users.get(name)
        if rec is None:
            return self._err(404, "user_not_found", "no such user")
        if action == "disable":
            if actor == name:
                return self._err(409, "account_self_disable", "cannot disable your own account")
            if rec["disabled"]:
                return 204, [], None
            rec["disabled"], rec["password_hash"] = True, ""
            self._account_end_logins(name)
            return 204, [], None
        if action == "enable":
            if rec["disabled"] is False:
                return self._err(409, "account_already_enabled", "account is already enabled")
            rec.update(disabled=False, password_hash=self._account_hash(pw))
            return 204, [], None
        if action == "password":
            if rec["disabled"]:
                return self._err(409, "account_disabled", "account is disabled")
        elif self._account_hash(body["current_password"]) != rec["password_hash"]:
            return self._err(403, "password_change_failed", "current password is incorrect")
        rec["password_hash"] = self._account_hash(pw)
        self._account_end_logins(name, keep)
        return 204, [], None

    def _account_end_logins(self, user: str, keep: str | None = None):
        ended = {c for c, u in self.logins.items() if u == user and c != keep}
        for c in ended:
            self.logins.pop(c, None)
            self.tied.pop(c, None)
            self.handles.pop(c, None)
            self.activity.pop(c, None)
            self.unlocked.discard(c)
            for cid, challenge in list(self.challenges.items()):
                if challenge[0] == c:
                    self.challenges.pop(cid, None)
            for dl in [d for d in self.device_logins.values() if d["login"] == c]:
                self._dl_end(dl, "cancelled", tell=False, close=4017, reason="account_login_ended")
        for ticket, c in list(self.ticket_logins.items()):
            if c in ended:
                self.ticket_logins.pop(ticket, None)
                self.tickets.pop(ticket, None)
        for client_id, c in list(self.client_logins.items()):
            if c in ended:
                self.client_logins.pop(client_id, None)
                sid = self.sid_of(client_id)
                if sid is not None:
                    self._drop_client(self.sessions[sid], client_id, code=4017, reason="account_login_ended")

    @staticmethod
    def _permissions(user: str) -> list[str]:
        if user == USER:
            return ["session.create", "session.join.ro", "session.join.rw", "session.terminate",
                    "target.discover", "broker.health", "vault.admin", "target.edit", "target.private", "account.admin"]
        if user == PEER:
            return ["session.create", "session.join.rw", "target.discover"]
        return ["session.join.ro", "target.discover"] if user == VIEWER else []

    @staticmethod
    def _valid_definition(body) -> bool:
        # The shipped package has no schema files: keep these bounds aligned by contract tests.
        if not isinstance(body, dict) or not {"id", "type", "host"} <= body.keys():
            return False
        if body.keys() - {"id", "type", "name", "group", "tags", "host", "port", "user", "vendor", "prompt_pattern", "share"}:
            return False
        if not isinstance(body["id"], str) or not ID_RE.fullmatch(body["id"]):
            return False
        if body["type"] not in ("ssh", "telnet"):
            return False
        if not isinstance(body["host"], str) or not 1 <= len(body["host"]) <= 253:
            return False
        if "port" in body and (type(body["port"]) is not int or not 1 <= body["port"] <= 65535):
            return False
        if "user" in body and (body["type"] != "ssh" or not isinstance(body["user"], str)
                               or len(body["user"]) > 256):
            return False
        if "vendor" in body and body["vendor"] is not None and (
            not isinstance(body["vendor"], str) or not ID_RE.fullmatch(body["vendor"])
        ):
            return False
        if "prompt_pattern" in body and (
            not isinstance(body["prompt_pattern"], str) or len(body["prompt_pattern"]) > 256
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in body["prompt_pattern"])
        ):
            return False
        for key in ("name", "group"):
            if key in body and (not isinstance(body[key], str) or not 1 <= len(body[key]) <= GROUP_MAX
                                or not GROUP_RE.fullmatch(body[key])):
                return False
        if "tags" in body and (not isinstance(body["tags"], list)
                               or any(not isinstance(t, str) or len(t) > TAG_ITEM_MAX
                                      or not TAG_ITEM_RE.fullmatch(t) for t in body["tags"])):
            return False
        if "share" in body:
            share = body["share"]
            if not isinstance(share, list) or not 1 <= len(share) <= 256:
                return False
            if any(not isinstance(s, str) or not SHARE_ENTRY_RE.fullmatch(s) for s in share):
                return False
        return True

    def _owner_info(self, tid: str) -> tuple[str, list[str]] | None:
        info = self.private_owners.get(tid)
        if info is None:
            return None
        if isinstance(info, str):
            return info, [info]
        return info["owner"], info["share"]

    def _generated_target_id(self):
        nonce = base64.b32encode(secrets.token_bytes(16)).decode().lower().rstrip("=")
        counter = 0
        while True:
            fields = (b"tsb-create-target-id", nonce.encode(), b"", counter.to_bytes(8, "big"))
            message = b"".join(len(field).to_bytes(4, "big") + field for field in fields)
            digest = hmac.digest(self.id_key, message, "sha256")
            tid = "g-" + base64.b32encode(digest).decode().lower()[:26]
            if tid not in self.targets:
                return tid
            counter += 1

    def _write_target(self, user: str, method: str, tid: str | None, body, query: dict):
        user_perms = self._permissions(user)
        if "target.edit" not in user_perms and "target.private" not in user_perms:
            return self._err(403, "forbidden", "target write permission is required")
        if query:
            return self._err(422, "invalid_request", "query parameters are not accepted on target writes")

        if method == "POST":
            if not isinstance(body, dict):
                return self._err(422, "invalid_request", "the target definition is outside its shape")
            is_own = "share" in body
            if is_own:
                if "target.private" not in user_perms:
                    return self._err(403, "forbidden", "target.private is required to create an own target")
            else:
                if "target.edit" not in user_perms:
                    return self._err(403, "forbidden", "target.edit is required to create a shared target")

            if is_own and "id" in body:
                return self._err(422, "invalid_request", "own target ids are generated and may not be supplied")
            if not is_own and "id" in body:
                if not isinstance(body["id"], str) or body["id"].startswith("g-") or not ID_RE.fullmatch(body["id"]):
                    return self._err(422, "invalid_request", "invalid target id")
                candidate_id = body["id"]
            else:
                candidate_id = self._generated_target_id()

            candidate = {**body, "id": candidate_id}
            if not self._valid_definition(candidate):
                return self._err(422, "invalid_request", "the target definition is outside its shape")

            if is_own:
                share = body["share"]
                if user not in share:
                    return self._err(422, "invalid_request", "the owner must be included in the share list")
                if len(share) != len(set(share)):
                    return self._err(422, "invalid_request", "duplicate names in share list")
                if any(":" in s for s in share):
                    return self._err(422, "invalid_request", "kind entries are not supported in share list")
                if any(s not in self.users for s in share):
                    return self._err(422, "invalid_request", "all named users must be existing accounts")
                share_to_store = sorted(share)

            tid = candidate_id
            if tid in self.targets:
                return self._err(409, "target_id_taken", "a source already holds that target id")

            record = {k: v for k, v in candidate.items() if k != "user" and not (k == "vendor" and v is None)}
            record.setdefault("tags", [])
            record.setdefault("port", 22 if candidate["type"] == "ssh" else 23)
            record.pop("scope", None)
            if is_own:
                record["discovered_by"] = "private-" + hashlib.sha256(user.encode()).hexdigest()[:32]
                record["share"] = share_to_store
                record["owner"] = user
                self.private_owners[tid] = {"owner": user, "share": share_to_store}
            else:
                record["discovered_by"] = "tsb"
                record.pop("share", None)
                record.pop("owner", None)
            self.targets[tid] = record

            definition = dict(candidate)
            if is_own:
                definition["share"] = share_to_store
            else:
                definition.pop("share", None)
            self.target_definitions[tid] = definition
            return 201, [], self._effective_prompt(record)

        # PUT, DELETE, GET (definition)
        if method == "PUT" and (not self._valid_definition(body) or body.get("id") != tid):
            return self._err(422, "invalid_request", "the target definition is outside its shape")

        record = self.targets.get(tid)
        if record is None or not self._target_visible(user, tid):
            return self._err(404, "target_not_found", "no such target")

        owner_info = self._owner_info(tid)
        if method == "PUT" and owner_info is None and "share" in body:
            return self._err(422, "invalid_request", "share list is not allowed on a shared target")

        if owner_info is not None:
            owner, current_share = owner_info
            if owner != user:
                return self._err(403, "forbidden", "only the owner may write an own target")
            if "target.private" not in user_perms:
                return self._err(403, "forbidden", "target.private is required to write an own target")
        else:
            if record["discovered_by"] != "tsb":
                return self._err(403, "forbidden", f"This target comes from {record['discovered_by']} and is not edited through TSB.")
            if "target.edit" not in user_perms:
                return self._err(403, "forbidden", "target.edit is required to write a shared target")

        if method == "GET":
            return 200, [], dict(self.target_definitions[tid])

        if method == "DELETE":
            del self.targets[tid]
            del self.target_definitions[tid]
            self.private_owners.pop(tid, None)
            return 204, [], None

        # PUT
        if owner_info is None:
            if "share" in body:
                return self._err(422, "invalid_request", "share list is not allowed on a shared target")
            share_to_store = None
        else:
            if "share" in body:
                share = body["share"]
                if share is None:
                    return self._err(422, "invalid_request", "share cannot be null")
                if not isinstance(share, list) or not (1 <= len(share) <= 256):
                    return self._err(422, "invalid_request", "share list must have 1 to 256 entries")
                if owner not in share:
                    return self._err(422, "invalid_request", "the owner must be included in the share list")
                if len(share) != len(set(share)):
                    return self._err(422, "invalid_request", "duplicate names in share list")
                if any(":" in s for s in share):
                    return self._err(422, "invalid_request", "kind entries are not supported in share list")
                if any(s not in self.users for s in share):
                    return self._err(422, "invalid_request", "all named users must be existing accounts")
                share_to_store = sorted(share)
            else:
                share_to_store = current_share

        old_record = dict(self.targets[tid])
        new_record = dict(old_record)
        for key in ("name", "group", "tags", "host", "port"):
            new_record.pop(key, None)
        if body.get("vendor", False) is None:
            new_record.pop("vendor", None)
        new_record.update({k: v for k, v in body.items() if k != "user" and not (k == "vendor" and v is None)})
        new_record.setdefault("tags", [])
        new_record.setdefault("port", 22 if body["type"] == "ssh" else 23)
        new_record.pop("scope", None)
        if owner_info is not None:
            new_record["discovered_by"] = "private-" + hashlib.sha256(owner.encode()).hexdigest()[:32]
            new_record["share"] = share_to_store
            new_record["owner"] = owner
            self.private_owners[tid] = {"owner": owner, "share": share_to_store}
        else:
            new_record["discovered_by"] = "tsb"
            new_record.pop("share", None)
            new_record.pop("owner", None)
        self.targets[tid] = new_record

        definition = dict(body)
        for key in ("vendor", "prompt_pattern"):
            if key not in definition and key in self.target_definitions.get(tid, {}):
                definition[key] = self.target_definitions[tid][key]
        if definition.get("vendor", False) is None:
            del definition["vendor"]
        if owner_info is not None:
            definition["share"] = share_to_store
        else:
            definition.pop("share", None)
        self.target_definitions[tid] = definition
        if owner_info is not None and old_record.get("share") != share_to_store:
            self.audit_events.append({"action": "target.update", "target_id": tid, "detail": {"fields": ["share"], "source": new_record["discovered_by"]}})
        return 200, [], self._effective_prompt(new_record)

    def _list_sessions(self, user: str, query: dict):
        """Serve history pages; cursor bytes are local to this fixture provider, never a wire format."""
        if set(query) - {"ended", "limit", "cursor"}:
            return self._err(422, "invalid_request", "unknown parameter of GET /sessions")
        if any(len(values) != 1 for values in query.values()):
            return self._err(422, "invalid_request", "each parameter may appear at most once")
        ended = query.get("ended", ["include"])[0]
        if ended not in ("include", "exclude", "only"):
            return self._err(422, "invalid_request", "ended must be include or exclude")
        limit = None
        if "limit" in query:
            raw = query["limit"][0]
            # Strip leading zeroes before parsing so even a huge supplied integer cannot raise.
            digits = raw.lstrip("0") or "0"
            if not re.fullmatch(r"[0-9]+", raw) or len(digits) > 3 or not 1 <= int(digits) <= 500:
                return self._err(422, "invalid_request", "limit is an integer from 1 to 500")
            limit = int(digits)
        if "cursor" in query and limit is None:
            return self._err(422, "invalid_request", "cursor requires limit")

        def position(created_at, sid):
            if (not isinstance(created_at, str) or not isinstance(sid, str)
                    or not ID_RE.fullmatch(sid)
                    or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z", created_at)):
                raise ValueError("invalid position")
            return rfc3339_ns(created_at), sid

        boundary = None
        if "cursor" in query:
            cursor = query["cursor"][0]
            try:
                if not 1 <= len(cursor) <= CURSOR_MAX:
                    raise ValueError("invalid cursor length")
                decoded = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                if not isinstance(decoded, list) or len(decoded) != 2:
                    raise ValueError("invalid cursor shape")
                boundary = position(*decoded)
            except (ValueError, TypeError, binascii.Error, UnicodeError):
                return self._err(422, "invalid_request", "cursor is invalid")
        records = [s for s in self.sessions.values()
                   if self._may_attach(user, s["id"]) is not None
                   and (ended == "include" or (s["state"] in ENDED) == (ended == "only"))]
        if limit is None:
            return 200, [], {"sessions": [self._record(s) for s in records]}
        records.sort(key=lambda s: position(s["created_at"], s["id"]), reverse=True)
        if boundary is not None:
            records = [s for s in records if position(s["created_at"], s["id"]) < boundary]
        page = records[:limit]
        out = {"sessions": [self._record(s) for s in page]}
        # A full page may continue to an empty page: no look-ahead is required.
        if len(page) == limit:
            last = page[-1]
            encoded = json.dumps([last["created_at"], last["id"]], separators=(",", ":")).encode()
            out["next_cursor"] = base64.urlsafe_b64encode(encoded).decode()
        return 200, [], out

    def _list_targets(self, query: dict, user: str):
        """GET /targets: the filters, the ordering by id ascending, and the cursor. The core fills a page
        by searching further windows of the index until it holds `limit` visible targets or the index is
        exhausted, so a page holds fewer than `limit` only when it is the last (and carries no cursor);
        `next_cursor` continues after the last target RETURNED, never after one the principal cannot see.
        In this fixture app every logged-in principal sees every target but for `self.invisible` (the
        core's own gate is graded against the core). A parameter outside the list, or a value outside its
        range, is 422 invalid_request; the index never goes down here, so 503 is graded against the core."""
        for name in query:
            if name not in ("type", "tag", "group", "discovered_by", "limit", "cursor"):
                return self._err(422, "invalid_request", f"{name} is not a parameter of GET /targets")
        for name, values in query.items():
            if any(value == "" for value in values):
                return self._err(422, "invalid_request", f"{name} must not be empty")
        for name in ("type", "group", "discovered_by", "limit", "cursor"):
            if len(query.get(name, [])) > 1:
                return self._err(422, "invalid_request", f"{name} may appear at most once")
        limit = None
        if "limit" in query:
            raw = query["limit"][0]
            # ASCII digits only: str.isdigit() is true for a superscript two and for Arabic-Indic
            # digits, and int() reads the latter, so neither is the contract's integer.
            if not re.fullmatch(r"[0-9]+", raw) or not 1 <= int(raw) <= MAX_TARGET_LIMIT:
                return self._err(422, "invalid_request", f"limit is an integer from 1 to {MAX_TARGET_LIMIT}")
            limit = int(raw)
        types = query.get("type", [])
        for value in types:
            if value not in TARGET_TYPES:
                return self._err(422, "invalid_request", f"type {value} is not a target type")
        tags, group, source = query.get("tag", []), query.get("group", []), query.get("discovered_by", [])
        cursor = query["cursor"][0] if query.get("cursor") else None
        if any(len(t) > TAG_ITEM_MAX or not TAG_ITEM_RE.match(t) for t in tags):
            return self._err(422, "invalid_request", "a tag is outside the item shape the target schema states")
        if group and (len(group[0]) > GROUP_MAX or not GROUP_RE.match(group[0])):
            return self._err(422, "invalid_request", "group is outside the shape the target schema states")
        if source and not ID_RE.match(source[0]):
            return self._err(422, "invalid_request", "discovered_by is not an id")
        if cursor is not None and len(cursor) > CURSOR_MAX:
            return self._err(422, "invalid_request", "cursor is outside the range its parameter states")
        def visible(r: dict) -> bool:
            return (self._target_visible(user, r["id"])
                    and (not types or r["type"] in types)
                    and all(tag in r["tags"] for tag in tags)
                    and (not group or r.get("group") == group[0])
                    and (not source or r["discovered_by"] == source[0]))
        # Search further windows of the index until the page holds `limit` visible targets or the index
        # is exhausted: an invisible id never reaches the page nor the cursor, and next_cursor continues
        # after the last target RETURNED. So a page holds fewer than `limit` only when it is the last,
        # and then has no `next_cursor`; an empty page is only the last.
        index = [r for r in sorted(self.targets.values(), key=lambda r: r["id"])
                 if cursor is None or r["id"] > cursor]
        window = limit if limit is not None else MAX_TARGET_LIMIT
        page: list[dict] = []
        for start in range(0, len(index), window):
            page += [r for r in index[start:start + window] if visible(r)]
            if limit is not None and len(page) >= limit:
                page = page[:limit]
                break
        after = bool(page) and any(r["id"] > page[-1]["id"] and visible(r) for r in index)
        context = (self.vault_prepare_resolution(user)
                   if "session.create" in self._permissions(user) and any(r["type"] == "ssh" for r in page)
                   else None)
        out: dict[str, Any] = {"targets": [self._target_answer(user, r, context=context) for r in page]}
        if after:
            out["next_cursor"] = page[-1]["id"]
        headers = [("Cache-Control", "no-store")] if any("login_hint" in r for r in out["targets"]) else []
        return 200, headers, out

    def _target_visible(self, user: str, target_id: str) -> bool:
        if target_id in self.invisible:
            return False
        owner_info = self._owner_info(target_id)
        if owner_info is not None:
            owner, share = owner_info
            if self.users.get(owner, {}).get("disabled", True):
                return False
            return user in share
        return "target.discover" in self._permissions(user)

    def _get_target(self, target_id: str, user: str):
        """GET /targets/{id}: an absent target and one the principal may not see are answered alike,
        so this answer never confirms that a target exists."""
        record = self.targets.get(target_id)
        if record is None or not self._target_visible(user, target_id):
            return self._err(404, "target_not_found", "no such target")
        context = (self.vault_prepare_resolution(user)
                   if record["type"] == "ssh" and "session.create" in self._permissions(user) else None)
        out = self._target_answer(user, record, context=context)
        return 200, [("Cache-Control", "no-store")] if "login_hint" in out else [], out

    def _effective_prompt(self, record: dict) -> dict:
        """Read-only effective pattern from an explicit own pattern (empty included, detached)
        or the current release template for a known vendor; absent stays absent (design §7)."""
        out = dict(record)
        if "prompt_pattern" in record:
            out["effective_prompt_pattern"] = record["prompt_pattern"]
        elif record.get("vendor") in VENDOR_PROMPT_TEMPLATES:
            out["effective_prompt_pattern"] = VENDOR_PROMPT_TEMPLATES[record["vendor"]]
        return out

    def _target_answer(self, user: str, record: dict, context: Any = None) -> dict:
        """Response-only hints over the fixture app's metadata; never mutate its shared inventory.

        This app uses its target dictionary as both index and runtime store. Independent runtime
        visibility and target-scoped policy are graded by the owed U2 provider tests.
        """
        out = self._effective_prompt(record)
        out.pop("login_hint", None)
        out.pop("scope", None)
        owner_info = self._owner_info(record.get("id", ""))
        if owner_info is not None:
            owner, share = owner_info
            out["owner"] = owner
            out["share"] = list(share)
        else:
            out.pop("owner", None)
            out.pop("share", None)
        if record["type"] == "ssh" and "session.create" in self._permissions(user):
            out["login_hint"] = self.vault_login_hint(user, record["id"], context=context)
        return out

    def _output_page(self, user: str, sess: dict, query: dict):
        """GET /sessions/{id}/output?before_seq=N&max_bytes=M (C1 decision 3, D13).

        The caller has already checked `_may_attach`, the one rule pages share with ticket minting.
        """
        if sess["state"] in ENDED:
            return 200, [("Content-Type", "application/octet-stream"),
                         ("TSB-Page-More", "false")], b""
        # An omitted cursor means the latest output: the exclusive bound sits above the newest seq
        # (next_seq + 1), so the newest frame is included (C1 decision 3).
        before = int(query.get("before_seq", [str(sess["next_seq"] + 1)])[0])
        max_bytes = int(query.get("max_bytes", ["4096"])[0])
        frames = [(seq, data) for seq, data in sess["output"] if seq < before]
        if not frames:
            return 200, [("Content-Type", "application/octet-stream"),
                         ("TSB-Page-More", "false")], b""
        # Whole output frames only (C1 decision 3). Select the frames immediately below the cursor,
        # walking backward from the latest, so a byte-limited page returns the newest output (not the
        # oldest); reverse for the response body so bytes stay in seq order.
        chosen, size = [], 0
        for seq, data in reversed(frames):
            if chosen and size + len(data) > max_bytes:
                break
            chosen.append((seq, data))
            size += len(data)
        chosen.reverse()
        more = "true" if len(chosen) < len(frames) else "false"
        body = b"".join(data for _seq, data in chosen)
        headers = [("Content-Type", "application/octet-stream"),
                   ("TSB-Page-First-Seq", str(chosen[0][0])),
                   ("TSB-Page-Last-Seq", str(chosen[-1][0])),
                   ("TSB-Page-More", more)]
        return 200, headers, body

    def _remove_client(self, user: str, sess: dict, client_id: str):
        """DELETE /sessions/{id}/clients/{client_id} (C1 decision 2, D9)."""
        if user != sess["owner"]:
            return self._err(403, "forbidden", "only the owner or session.terminate may remove this client")
        ws = self.clients.get(sess["id"], {}).get(client_id)
        if ws is None:
            return self._err(404, "client_not_found", "no such client")
        self._drop_client(sess, client_id, code=4014, reason="removed")
        return 204, [], None

    def _drop_client(self, sess: dict, client_id: str, code: int, reason: str) -> None:
        ws = self.clients.get(sess["id"], {}).pop(client_id, None)
        self.client_logins.pop(client_id, None)
        if ws is not None:
            try:
                ws.send(wf.OP_CLOSE, wf.encode_close(code, reason))
            except OSError:
                pass
            ws.closed = True
        if sess["state"] in ENDED:
            return
        if (sess["control_holder"] or {}).get("client_id") == client_id:
            self._pass_token(sess, "detach")
            new_holder_id = (sess["control_holder"] or {}).get("client_id")
            self._void_and_reprompt(sess, new_holder_id)
            self._send_token(sess, "detach")
            if new_holder_id is not None:
                self._send_stale_prompt(sess, new_holder_id)
        self._send_clients(sess)
        if not self.clients.get(sess["id"]):
            sess["state"] = "detached"

    def _pass_token(self, sess: dict, _reason: str) -> None:
        left = self.clients.get(sess["id"], {})
        nxt = next((cid for cid, ws in left.items() if self._may_control(self.client_users[cid])), None)
        sess["control_holder"] = {"client_id": nxt, "user": self.client_users[nxt]} if nxt else None
        if nxt is not None:
            sess["owner_revoked"] = False

    def _void_and_reprompt(self, sess: dict, new_holder_id: str | None) -> None:
        """Void the outstanding stale prompt by generating a new prompt_id.
        
        stale-screen rule 7 / typing-ownership rule 16: a token move while pending voids the
        outstanding prompt, and the client that receives the token gets a new prompt_id; a move to
        escrow asks nobody.
        
        This only voids the prompt. The caller must send the token frame first, then call
        _send_stale_prompt to send the new prompt to the new holder."""
        if sess["stale"] is None:
            return
        # Void the outstanding prompt by generating a new prompt_id
        sess["stale"]["prompt_id"] = secrets.token_hex(8)

    def _send_stale_prompt(self, sess: dict, holder_id: str) -> None:
        """Send a stale_prompt frame to the given client (stale-screen step 8: the client that
        receives the token gets the prompt, AFTER the token frame)."""
        if sess["stale"] is None:
            return
        ws = self.clients.get(sess["id"], {}).get(holder_id)
        if ws is not None:
            prompt = self._build_stale_prompt_frame(sess["stale"]["prompt_id"])
            try:
                ws.send_text(json.dumps(prompt))
            except OSError:
                # A failed send drops the client as _broadcast does: the dead client is removed and
                # its token moves on (finding 2).
                if holder_id in self.clients.get(sess["id"], {}):
                    self._drop_client(sess, holder_id, code=1001, reason="send failed")

    def _candidates(self, user: str, target: str, credential: tuple[str, str] | None = None) -> list[str]:
        # A connection opened with a supplied login is never a candidate (session-open step 4). A candidate has
        # "the same credential" (session-open, Candidate): the one this request resolves to, a choice or the only
        # candidate, and with none resolved only a connection opened with none recorded.
        return sorted(c for c, conn in self.connections.items()
                      if conn["owner"] == user and conn["target_id"] == target and conn["up"]
                      and not conn["supplied_login"] and not conn.get("prompted")
                      and (conn.get("credential_ref"), conn.get("credential_layer")) == (credential or (None, None)))

    def _connection_holds_session(self, conn_id: str) -> bool:
        """Whether any session on this connection has not ended. A telnet connection holds at most its
        one session; while it is on the connection its worker refuses a reuse."""
        return any(s["connection_id"] == conn_id and s["state"] not in ENDED for s in self.sessions.values())

    def _open_connection(self, user: str, target: str, supplied_login: bool = False,
                         credential: tuple[str, str] | None = None, prompted: bool = False) -> str:
        conn = self._next("conn")
        self.connections[conn] = {"owner": user, "target_id": target, "up": True, "supplied_login": supplied_login,
                                  "prompted": prompted}
        if credential is not None:
            self.connections[conn]["credential_ref"], self.connections[conn]["credential_layer"] = credential
        return conn

    @staticmethod
    def _valid_login(login) -> bool:
        """SessionLogin (openapi/tsb.openapi.json): {user, auth: {kind: password, password}, and optionally
        recording_acknowledged: a boolean}, closed; user 1..256 characters, password 1..1024 bytes of UTF-8
        (the schema's maxLength counts characters, the byte bound is the core's)."""
        if not isinstance(login, dict) or not {"user", "auth"} <= set(login) <= {"user", "auth", "recording_acknowledged"}:
            return False
        if "recording_acknowledged" in login and not isinstance(login["recording_acknowledged"], bool):
            return False
        user, auth = login["user"], login["auth"]
        if not isinstance(user, str) or not 1 <= len(user) <= LOGIN_USER_MAX:
            return False
        if auth == {"kind": "prompt"}:
            return True       # a device login: nothing secret is sent now (Release B)
        if not isinstance(auth, dict) or set(auth) != {"kind", "password"} or auth["kind"] != "password":
            return False
        password = auth["password"]
        try:
            return isinstance(password, str) and 1 <= len(password.encode("utf-8")) <= LOGIN_PASSWORD_MAX
        except UnicodeEncodeError:
            return False

    def _unacknowledged(self, body):
        """The consent refusal (session-open step 2b): a login that would be recorded, and a request that
        did not set `login.recording_acknowledged`, is 409 before anything is opened, so nothing reaches a
        worker and nothing is recorded. None when no consent is owed or it was given."""
        if "login" in body and self.login_recorded and body["login"].get("recording_acknowledged") is not True:
            return self._err(409, "login_recording_not_acknowledged",
                             "the login would be recorded; acknowledge the recording and repeat the request")
        return None

    def _create(self, user: str, body, cookie: str = ""):
        required = {"target_id", "connection", "term", "cols", "rows"}
        choice = {"credential_ref", "credential_layer"}
        if isinstance(body, dict) and (choice & set(body)) and (not choice <= set(body) or "login" in body
                                                                or body["credential_layer"] not in ("personal", "org")
                                                                or not isinstance(body["credential_ref"], str)):
            return self._err(422, "invalid_request", "credential_ref and credential_layer go together, never with a login")
        if not isinstance(body, dict) or not required <= set(body) <= required | {"connection_id", "login"} | choice \
                or body["connection"] not in ("reuse", "new") or not self._valid_size(body) \
                or ("connection_id" in body and (body["connection"] != "reuse" or not isinstance(body["connection_id"], str))) \
                or ("login" in body and (body["connection"] != "new" or not self._valid_login(body["login"]))):
            return self._err(422, "invalid_request", "target_id, connection (reuse|new), term, cols, rows")
        target = body["target_id"]
        if (target not in self.targets or target in self.invisible
                or (target in self.private_owners and not self._target_visible(user, target))):
            return self._err(404, "target_not_found", "no such target")
        # A login is for ssh targets: a telnet login is typed in the session (session-open step 2a).
        if "login" in body and self.targets[target]["type"] != "ssh":
            return self._err(422, "invalid_request", "a login is for ssh targets only")
        refusal = self._unacknowledged(body)
        if refusal is not None:
            return refusal
        if "login" in body and body["login"]["auth"] == {"kind": "prompt"}:
            # A device login (Release B): nothing is placed or opened; a ticket redeems it on GET /ws/attach.
            # The cap (step 5): the principal's device logins in progress are counted and the new one registered
            # in one step, under the request lock `http` holds, so two creates never both take the last place.
            self._sweep_tickets()
            if sum(1 for dl in self.device_logins.values() if dl["user"] == user) >= self.device_logins_max:
                return self._err(429, "rate_limited", "too many device logins in progress; finish or cancel one")
            dlid, ticket = self._next("device-login"), secrets.token_urlsafe(24)
            self.dl_tickets[ticket] = (user, dlid, time.monotonic() + self.ticket_ttl)
            # `login` is the TSB login (cookie) that made the create: the ticket is bound to (principal, device
            # login) and to it (V10), and the device login ends when that login does. `ws` is the socket that
            # redeemed the ticket, once one has.
            self.device_logins[dlid] = {"id": dlid, "user": user, "login": cookie, "ticket": ticket, "ws": None,
                                        "target_id": target, "login_user": body["login"]["user"],
                                        "term": body["term"], "cols": body["cols"], "rows": body["rows"],
                                        "ended": False, "outcome": None}
            return 202, [], {"device_login_id": dlid, "ticket": ticket, "expires_in": self.ticket_ttl}
        if self._names_own_prompted_connection(user, target, body):
            # Session-open steps 3 and 4: the principal's own prompted connection, named by id, is decided before
            # anything of step 3. Its shape first: no stored credential opened it, so a credential choice beside it is
            # 422, ahead of every 409 (V14 orders a login beside a choice so). Then no credential is resolved and no
            # vault is touched, so none of step 3's refusals (a mapping to a missing entry, no resolving credential, a
            # choice to make, a lock) can answer it.
            if "credential_ref" in body:
                return self._err(422, "invalid_request", "a prompted connection was opened with no stored credential")
            return self._session_on(user, target, body, body["connection_id"])
        if body["connection"] == "reuse" and "login" not in body and "credential_ref" not in body \
                and "connection_id" not in body:
            # Session-open step 3, at its head too: a reuse that names no connection and no credential first gathers the
            # principal's own prompted connections to this target. With one or more, no credential refusal of step 3
            # answers (a target no credential resolves for, a choice to make, a mapping to a missing entry). The
            # candidates are those plus, when exactly one stored credential resolves, the connections opened with it.
            candidates = self._own_prompted_connections(user, target)
            if candidates:
                resolving = self.vault_candidates(user, target)
                if len(resolving) == 1:
                    candidates = sorted(candidates + self._candidates(user, target, next(iter(resolving))))
                if len(candidates) > 1:
                    status, headers, reply = self._err(409, "connection_choice_required",
                                                       "several connections could be reused; name one")
                    reply["error"]["connection_ids"] = candidates
                    return status, headers, reply
                return self._session_on(user, target, body, candidates[0])
        mapped = self.vault_create_refusal(user, body)
        if mapped is not None:
            return mapped
        # A choice (both fields, by shape) is the operator's pick among the candidates (session-open step 3): one
        # that is no candidate is refused here, before anything is opened.
        choice = (body["credential_ref"], body["credential_layer"]) if "credential_ref" in body else None
        # A request with a login resolves no credential and touches no vault (step 3).
        resolving = set() if "login" in body else self.vault_candidates(user, target)
        if choice is not None and choice not in resolving:
            return self._err(422, "invalid_request", "that credential is not a candidate")
        if target == NOCRED and "login" not in body and choice is None:
            return self._err(409, "no_resolving_credential", "no credential resolves for this target")
        if choice is None and len(resolving) > 1:
            # Two or more and no choice: the core picks none (Contract §12).
            status, headers, reply = self._err(409, "credential_choice_required", "choose a credential")
            reply["error"]["candidates"] = [{"credential_ref": ref, "credential_layer": layer}
                                            for ref, layer in sorted(resolving, key=lambda c: (c[1], c[0]))]
            return status, headers, reply
        # The credential this request resolves to: the choice, or the only candidate; none when nothing resolves.
        credential = choice or next(iter(resolving), None)
        candidates = self._candidates(user, target, credential)
        if body["connection"] == "new" or ("connection_id" not in body and not candidates):
            locked = self.vault_lock_refusal(user, cookie, credential)    # a reuse of a connection never needs one
            if locked is not None:
                return locked
        if body["connection"] == "new":
            conn = self._open_connection(user, target, supplied_login="login" in body, credential=credential)
        elif "connection_id" in body:
            if body["connection_id"] not in candidates:       # a prompted one named by id was decided above
                return self._err(422, "invalid_request", "connection_id is not a connection reuse may pick")
            conn = body["connection_id"]
        elif len(candidates) > 1:
            status, headers, reply = self._err(409, "connection_choice_required", "several connections could be reused; name one")
            reply["error"]["connection_ids"] = candidates
            return status, headers, reply
        else:
            conn = candidates[0] if candidates else self._open_connection(user, target, credential=credential)
        return self._session_on(user, target, body, conn)

    def _own_prompted_connections(self, user: str, target: str) -> list[str]:
        """The connections a device login opened for this principal, to this target, still up."""
        return sorted(c for c, conn in self.connections.items()
                      if conn.get("prompted") and conn["owner"] == user and conn["target_id"] == target and conn["up"])

    def _names_own_prompted_connection(self, user: str, target: str, body: dict) -> bool:
        """A `reuse` that names by `connection_id` a connection a device login opened for this principal, to this
        target, and still up (session-open step 4; device-login, "Reusing a prompted connection"). A credential
        choice beside it is 422, decided by the caller."""
        named = self.connections.get(body.get("connection_id"))
        return (named is not None and bool(named.get("prompted")) and named["owner"] == user
                and named["target_id"] == target and named["up"])

    def _session_on(self, user: str, target: str, body: dict, conn: str):
        # A telnet connection carries one session, and its worker closes it once that session has ended.
        # While that session is on it, a reuse is refused by the worker: the create is answered 502, as
        # any open the worker refuses (SessionCreateRequest; C1/session-create-telnet-reuse).
        if body["connection"] == "reuse" and self.targets[target]["type"] == "telnet" \
                and self._connection_holds_session(conn):
            return self._err(502, "target_connect_failed", "the connection carries one session; its reuse is refused")
        sess = self._new_session(user, target, conn, body["term"], body["cols"], body["rows"])
        return 201, [], {"session": self._record(sess)}

    def _new_session(self, user: str, target: str, conn: str, term: str, cols: int, rows: int) -> dict:
        """A detached shell session on `conn`, with the scenario's canned dump when the target names one."""
        sid = self._next("sess")
        sess = {
            "id": sid, "target_id": target, "connection_id": conn, "session_type": "shell",
            "state": "detached", "tier": 1, "owner": user, "control_holder": None, "owner_revoked": False,
            "created_at": "2026-09-23T12:00:00Z", "term": term, "cols": cols, "rows": rows,
            # The core's raw output ring: (seq, payload) in order. Serve has no VT, so the dump is
            # this output so far (C2 decision 3), seeded from the scenario's canned golden bytes.
            "output": [], "next_seq": 0, "stale": None, "stale_wanted": False}
        if target == "alt-screen":
            self._seed(sess, ALT_SCREEN_DUMP)
        if target == "stale-pending":
            sess["stale_wanted"] = True
        self.sessions[sid] = sess
        return sess

    def _seed(self, sess: dict, data: bytes) -> None:
        """Pre-load the raw ring so an attach's canned dump already carries these bytes."""
        if data:
            sess["next_seq"] += 1
            sess["output"].append((sess["next_seq"], data))

    def _sweep_tickets(self) -> None:
        """Drop every ticket past its expiry, with the login it carries: both tables hold live tickets only."""
        now = time.monotonic()
        for ticket in [t for t, (_, _, expiry) in self.tickets.items() if expiry < now]:
            del self.tickets[ticket]
            self.ticket_logins.pop(ticket, None)
        for ticket, (_, dlid, expiry) in list(self.dl_tickets.items()):
            if expiry < now:                  # an unattached device login ends with its ticket's lifetime
                self._dl_end(self.device_logins[dlid], "expired", tell=False, close=None)

    def _kill(self, sess: dict) -> None:
        sess["state"], sess["control_holder"] = "killed", None
        for client_id, ws in list(self.clients.pop(sess["id"], {}).items()):
            self.client_logins.pop(client_id, None)
            try:
                ws.send(wf.OP_CLOSE, wf.encode_close(4010, "session killed"))
            except OSError:
                pass
            ws.closed = True
        conn = self.connections.get(sess["connection_id"])
        if conn is not None and self.targets.get(conn["target_id"], {}).get("type") == "telnet":
            # A telnet connection carries one session and closes with it, whether or not it would linger.
            conn["up"] = False
            return
        left = [s for s in self.sessions.values()
                if s["connection_id"] == sess["connection_id"] and s["state"] in UP]
        if not left and not self.linger:
            self.connections[sess["connection_id"]]["up"] = False

    # -- WebSocket --

    def websocket(self, path: str, ws: ServerWS) -> None:
        first = ws.recv()
        if first.kind == "close" and ws.closed:  # dropped, or a malformed frame already got its close
            return
        frame = None
        if first.kind == "text":
            try:
                frame = json.loads(first.data)
            except ValueError:
                frame = None
        if (isinstance(frame, dict) and frame.get("t") == "device_login_attach" and frame.get("v") == 1
                and isinstance(frame.get("ticket"), str) and isinstance(frame.get("device_login_id"), str)):
            self._device_login(ws, frame)
            return
        if not (isinstance(frame, dict) and frame.get("t") == "attach" and frame.get("v") == 1
                and isinstance(frame.get("ticket"), str) and isinstance(frame.get("session_id"), str)):
            ws.close(4000, "the first frame must be attach, or device_login_attach")
            return
        self.frames.append(frame)
        with self.lock:
            self._sweep_tickets()      # an expired ticket is gone from both tables, and so is refused as an unknown one
            if frame["ticket"] in self.dl_tickets:
                # A device-login ticket opens no session: burned and refused, and its device login ends with it.
                self._dl_end(self.device_logins[self.dl_tickets[frame["ticket"]][1]], "cancelled", tell=False, close=None)
                ws.close(4003, "ticket not valid for this session")
                return
            entry = self.tickets.get(frame["ticket"])
            if entry is None:
                ws.close(4001, "ticket invalid")
                return
            del self.tickets[frame["ticket"]]
            login = self.ticket_logins.pop(frame["ticket"], None)
            user, sid, _ = entry
            if sid != frame["session_id"]:
                ws.close(4003, "ticket not valid for this session")
                return
            sess = self.sessions.get(frame["session_id"])
            if sess is None or sess["state"] in ENDED:
                ws.close(4004, "no such session")
                return
            client_id = self._attach_locked(ws, user, sess, login)
        self._stream_then_drop(ws, sess, client_id)

    def _attach_locked(self, ws: ServerWS, user: str, sess: dict, login: str | None) -> str:
        """Attach `ws` to `sess` as `user` (the lock is held): attached, prefix and dump, clients, stale."""
        client_id = self._next("client")
        self.clients.setdefault(sess["id"], {})[client_id] = ws
        self.client_users[client_id] = user
        if login is not None:
            self.client_logins[client_id] = login
        # Attach takes the token only from a plain escrow: a view-only client never takes it,
        # and an owner-revoked (owner-gated) token is not taken by an attach (C2 decision 6).
        if sess["control_holder"] is None and not sess.get("owner_revoked") and self._may_control(user):
            sess["control_holder"] = {"client_id": client_id, "user": user}
        sess["state"] = "live"
        ws.send_text(json.dumps({"t": "attached", "v": 1, "session": self._record(sess), "client_id": client_id}))
        for extra in self.after_attach:
            ws.send_text(json.dumps(extra))
        # The attach order (C2 decision 9): attached → prefix + dump → clients → live.
        prefix_frame, dump = self._prefix_step(sess)
        ws.send_text(json.dumps(prefix_frame))
        ws.send_binary(dump)
        self._send_clients(sess)
        if sess["stale"] is not None or sess["stale_wanted"]:
            # The stale-pending scenario announces a grid_lost with no gap_seq (5.52): the grid
            # could not be rebuilt though no byte was lost. Sent after `clients` to the joining
            # client only (stale-screen rule 4: send to each client attaching while pending).
            self._send_stale_to_joiner(sess, client_id, ws)
        return client_id

    def _stream_then_drop(self, ws: ServerWS, sess: dict, client_id: str) -> None:
        try:
            self._stream(ws, sess, client_id)
        finally:
            with self.lock:
                if self.clients.get(sess["id"], {}).get(client_id) is not None:
                    self._drop_client(sess, client_id, code=1000, reason="")
                else:
                    if not self.clients.get(sess["id"]) and sess["state"] not in ENDED:
                        sess["state"] = "detached"

    # -- the device login (Release B): one scripted login on a fixture target --
    #
    # A first-use host key, one keyboard-interactive round with two prompts (the second echoed), accepted for any
    # answers, then an ordinary session on a connection marked prompted. It is a fixture server: no device, no
    # probe, no recording, no permission to lose.

    DL_FP = "SHA256:FIXTURE000000000000000000000000000000000000"

    def _dl_end(self, dl: dict, outcome: str, *, tell: bool = True, close: int | None = 4016,
                reason: str = "device login ended") -> None:
        """The device login's one end function (device-login, how a device login ends); the lock is held. It sets
        the outcome, drops the record, which gives the principal's cap place back, voids the ticket if unredeemed,
        and tells the pane: `device_login_result` when `tell`, then a close with `close` when it is not None.
        Idempotent: every end runs it, and the second one finds nothing to do. `tell=False, close=None` is an end
        with nobody to tell (an expiry, a lost socket); `close=None` alone leaves the socket open for the attach
        that follows `connected`."""
        if dl["ended"]:
            return
        dl["ended"], dl["outcome"] = True, outcome
        self.device_logins.pop(dl["id"], None)
        self.dl_tickets.pop(dl["ticket"], None)
        ws = dl["ws"]
        if ws is None or ws.closed:
            return
        try:
            if tell:
                ws.send_text(json.dumps({"t": "device_login_result", "v": 1, "outcome": outcome}))
            if close is not None:
                # Not `ws.close`, which waits for the peer's answer: the lock is held, and the handler thread
                # blocked in `recv` is the one that reads it.
                ws.send(wf.OP_CLOSE, wf.encode_close(close, reason))
                ws.closed = True
        except OSError:
            ws.closed = True

    def _dl_login_ended(self, login: str, revoked: bool) -> None:
        """A TSB login ended (logout, or a device revocation; the lock is held): the device logins it created end
        (login steps 9 and 13). Logout: `cancelled`, close 4016. A revocation: close 4015 `device_revoked` and no
        result, since no session exists for 4015's "the session lives on" to apply to. Unredeemed ones lose their
        ticket and are dropped without a socket. A session a device login already opened is an attached client of
        the login (`client_logins`) and is closed with it as any attached client is."""
        for dl in [d for d in self.device_logins.values() if d["login"] == login]:
            if revoked:
                self._dl_end(dl, "cancelled", tell=False, close=4015, reason="device_revoked")
            else:
                self._dl_end(dl, "cancelled")

    @staticmethod
    def _dl_kept(frame: dict) -> dict:
        """The frame as the server keeps it: an answer is a credential, so the synthetic constant stands in for
        each string of `answers`, as a collection holds it; a value that is no list of strings is one constant."""
        if "answers" not in frame:
            return frame
        answers = frame["answers"]
        return {**frame, "answers": [PASSWORD] * len(answers) if isinstance(answers, list) else PASSWORD}

    def _dl_refuse(self, ws: ServerWS, request: str, reason: str) -> None:
        ws.send_text(json.dumps({"t": "request_refused", "v": 1, "request": request, "reason": reason}))

    @staticmethod
    def _dl_answers_ok(frame: dict, count: int) -> bool:
        answers = frame.get("answers")
        return (isinstance(answers, list) and len(answers) == count
                and all(isinstance(a, str) and len(a.encode("utf-8", "replace")) <= LOGIN_PASSWORD_MAX for a in answers))

    def _device_login(self, ws: ServerWS, frame: dict) -> None:
        with self.lock:
            self._sweep_tickets()
            if frame["ticket"] in self.tickets:
                del self.tickets[frame["ticket"]]    # a session ticket opens no device login: burned, and refused
                ws.close(4003, "ticket not valid for this device login")
                return
            entry = self.dl_tickets.pop(frame["ticket"], None)
            if entry is None:
                ws.close(4001, "ticket invalid")
                return
            user, dlid, _ = entry
            dl = self.device_logins[dlid]
            if dlid != frame["device_login_id"]:
                # Burned, and a refusal ends the device login (step 8): nothing was placed or dialled.
                self._dl_end(dl, "cancelled", tell=False, close=None)
                ws.close(4003, "ticket not valid for this device login")
                return
            # The ticket is bound to (principal, device login) and to the TSB login that created it. A login that
            # ended took its device logins and their tickets with it (`_dl_login_ended`), so a ticket that is
            # still here belongs to a login that is still live.
            dl["ws"] = ws
            self.device_login_frames.append(self._dl_kept(frame))
        try:
            opened = self._dl_dialogue(ws, dl, user)
        finally:
            with self.lock:
                self._dl_end(dl, "connect_failed", tell=False, close=None)    # a path that ended nothing; else a no-op
        if opened is not None:
            self._stream_then_drop(ws, *opened)

    @staticmethod
    def _dl_speaks(frame: dict) -> bool:
        """A frame at the version the server speaks. Any other `v`, or none, makes a known `t` an unknown one, which is
        ignored (V1, consumers); `True` and `1.0` are no version 1."""
        version = frame.get("v")
        return type(version) is int and version == 1

    @classmethod
    def _dl_cancel_read(cls, ws: ServerWS) -> bool:
        """A `device_login_cancel` the socket has already received and the dialogue has not yet read: the cancel
        point just before the session is created (device-login, "The two cancel points")."""
        for text in ws.buffered_text():
            try:
                got = json.loads(text)
            except ValueError:
                continue
            if isinstance(got, dict) and got.get("t") == "device_login_cancel" and cls._dl_speaks(got):
                return True
        return False

    @staticmethod
    def _dl_drain(ws: ServerWS) -> None:
        """After the server's close frame (`_dl_end`), read to the peer's answer, as `ServerWS.close` does."""
        while ws.recv().kind != "close":
            pass

    def _dl_dialogue(self, ws: ServerWS, dl: dict, user: str) -> tuple[dict, str] | None:
        """The scripted login on the redeemed socket. (session, client_id) when the device accepted and the same
        socket became an attach; None when the device login ended first, whatever the cause. Every exit but the
        attach has run `_dl_end` or finds it already run (a logout, a revocation)."""
        try:
            ws.send_text(json.dumps({"t": "device_login_hostkey", "v": 1, "kind": "first_use",
                                     "fingerprint": self.DL_FP}))
        except OSError:
            return None                               # the login ended and closed the socket first
        try:
            answered = self._dl_answers(ws, dl)
        except OSError:
            return None                               # a send met the socket the login's end closed: nothing more is sent
        if not answered:
            return None
        cancelled = gone = False
        with self.lock:
            if dl["ended"]:
                return None
            if self._dl_cancel_read(ws):
                cancelled = True                      # the cancel point before the session: nothing is placed or opened
                self._dl_end(dl, "cancelled")
            elif ws.peer_gone():
                # A socket closed before the core acts on `connected` ends the login `cancelled`, with nobody to tell.
                self._dl_end(dl, "cancelled", tell=False, close=None)
                gone = True
        if gone:
            if not ws.closed:
                try:
                    ws.send(wf.OP_CLOSE, wf.encode_close(None))     # the answer to a close frame, as the read loop gives it
                except OSError:
                    pass
            return None
        if cancelled:
            self._dl_drain(ws)
            return None
        with self.lock:
            if dl["ended"]:
                return None
            conn = self._open_connection(user, dl["target_id"], prompted=True)
            sess = self._new_session(user, dl["target_id"], conn, dl["term"], dl["cols"], dl["rows"])
            self._dl_end(dl, "connected", close=None)   # the result; the place is given back as it becomes an attach
            # A client of the TSB login that created it, so that login's end closes it as it closes any client.
            client_id = self._attach_locked(ws, user, sess, dl["login"])
        return sess, client_id

    def _dl_answers(self, ws: ServerWS, dl: dict) -> bool:
        """Read the pane's frames until the terminal answer: True when a valid answer came, False when the device login
        ended first. Only frames at the version the server speaks are acted on."""
        step = "hostkey"
        while True:
            message = ws.recv()
            if message.kind == "close":
                with self.lock:
                    self._dl_end(dl, "cancelled", tell=False, close=None)     # the pane's socket closed
                if not ws.closed:
                    try:
                        ws.send(wf.OP_CLOSE, wf.encode_close(message.close_code if message.close_code != 1005 else None))
                    except OSError:
                        pass
                return False
            if message.kind != "text":
                continue                              # nothing but device-login frames is read before `attached`
            try:
                got = json.loads(message.data)
            except ValueError:
                got = None
            if not isinstance(got, dict):
                with self.lock:
                    self._dl_end(dl, "cancelled", tell=False, close=None)
                ws.fail(1007, "text frame is not a JSON object")
                return False
            with self.lock:
                if dl["ended"]:
                    return False                      # ended elsewhere and the socket closed: read nothing more
                self.device_login_frames.append(self._dl_kept(got))
            if not self._dl_speaks(got):
                continue                              # a known `t` at another `v` is an unknown `t`: ignored
            t = got.get("t")
            if t == "device_login_cancel":
                with self.lock:
                    self._dl_end(dl, "cancelled")
                self._dl_drain(ws)
                return False
            if t == "device_login_hostkey_answer":
                if step != "hostkey" or not isinstance(got.get("accept"), bool):
                    self._dl_refuse(ws, "device_login_hostkey_answer", "not_pending")
                elif not got["accept"]:
                    with self.lock:
                        self._dl_end(dl, "hostkey_refused")
                    self._dl_drain(ws)
                    return False
                else:
                    step = "prompt"
                    ws.send_text(json.dumps({
                        "t": "device_login_prompt", "v": 1, "method": "keyboard-interactive", "attempt": 1,
                        "name": "", "instruction": "",
                        "prompts": [{"text": "Password: ", "echo": False}, {"text": "Verification code: ", "echo": True}],
                        "prompt_id": 1}))
            elif t == "device_login_answer":
                if step != "prompt" or got.get("prompt_id") != 1:
                    self._dl_refuse(ws, "device_login_answer", "not_pending")
                elif not self._dl_answers_ok(got, 2):
                    self._dl_refuse(ws, "device_login_answer", "invalid")
                else:
                    return True

    def _send_stale_to_joiner(self, sess: dict, client_id: str, ws: ServerWS) -> None:
        """Send stale to the joining client only (stale-screen rule 4: to each client attaching
        while pending, after `clients`). If the joiner takes the token, also send the prompt."""
        if sess["stale"] is None:
            sess["stale"] = {"prompt_id": secrets.token_hex(8), "cause": "grid_lost"}
        cause = sess["stale"]["cause"]
        stale = {"t": "stale", "v": 1, "session_id": sess["id"], "state": "pending",
                 "cause": cause, "at": "2026-10-06T02:00:01Z"}
        try:
            ws.send_text(json.dumps(stale))
            # If the joiner holds the token (took it from escrow), void the outstanding prompt and
            # issue a new one (stale-screen rule 7 / typing-ownership rule 16: a token move while
            # pending voids the prompt and the new holder gets a new prompt_id).
            holder = (sess["control_holder"] or {}).get("client_id")
            if holder == client_id:
                self._void_and_reprompt(sess, client_id)
                prompt = self._build_stale_prompt_frame(sess["stale"]["prompt_id"])
                ws.send_text(json.dumps(prompt))
        except OSError:
            # A failed send drops the client as _broadcast does: the dead client is removed and its
            # token moves on (finding 2).
            if client_id in self.clients.get(sess["id"], {}):
                self._drop_client(sess, client_id, code=1001, reason="send failed")

    def _stale_answer(self, ws: ServerWS, sess: dict, client_id: str, frame: dict) -> None:
        """A stale choice: valid only from the current holder's client for the outstanding prompt
        (C2 decision 10). Any answer ends the pending state (D12); otherwise `request_refused`."""
        pending = sess["stale"]
        holder_id = (sess["control_holder"] or {}).get("client_id")
        valid = (pending is not None
                 and frame.get("prompt_id") == pending["prompt_id"]
                 and client_id == holder_id
                 and frame.get("choice") in ("wiggle", "ctrl_l", "continue", "fresh_session"))
        if not valid:
            # stale-screen refusals: not_allowed when the answer is not from the current holder's
            # client for the outstanding prompt (covers view-only, non-holder, former holder with
            # voided prompt); not_pending when no choice is outstanding or current holder answers
            # with outdated prompt_id (open item 6: unspecified; kept as shipped).
            if pending is None:
                reason = "not_pending"
            elif client_id != holder_id:
                # Not from the current holder (covers former holder answering voided prompt)
                reason = "not_allowed"
            elif frame.get("prompt_id") != pending["prompt_id"]:
                # Current holder, wrong prompt_id: stale-screen open item 6: unspecified; kept as shipped
                reason = "not_pending"
            else:
                # Current holder, correct prompt_id, but invalid choice
                reason = "not_allowed"
            ws.send_text(json.dumps({"t": "request_refused", "v": 1,
                                     "request": "stale_answer", "reason": reason}))
            return
        sess["stale"] = None
        sess["stale_wanted"] = False
        chosen = {"t": "stale", "v": 1, "session_id": sess["id"], "state": "chosen",
                  "cause": pending["cause"], "at": "2026-10-06T02:00:02Z",
                  "choice": frame["choice"], "by": sess["control_holder"]}
        self._broadcast(sess, lambda _cid, sock: sock.send_text(json.dumps(chosen)))

    def _stream(self, ws: ServerWS, sess: dict, client_id: str) -> None:
        while True:
            message = ws.recv()
            if message.kind == "close":
                if not ws.closed:
                    try:
                        ws.send(wf.OP_CLOSE, wf.encode_close(message.close_code if message.close_code != 1005 else None))
                    except OSError:
                        pass
                return
            with self.lock:
                if self.clients.get(sess["id"], {}).get(client_id) is None:
                    return
                holds_control = (sess["control_holder"] or {}).get("client_id") == client_id
                if message.kind == "binary":
                    if not holds_control and self.client_users[client_id] == sess["owner"]:
                        # The owner types while not the holder: the token moves to it in the same
                        # trip, before this input is forwarded (C2 decision 6, 5.53). A stale
                        # choice still moves the token; the keystroke that caused it is refused
                        # `stale` with the rest (input is refused while a choice is pending).
                        sess["control_holder"] = {"client_id": client_id, "user": self.client_users[client_id]}
                        sess["owner_revoked"] = False
                        self._void_and_reprompt(sess, client_id)
                        self._send_token(sess, "takeover")
                        self._send_stale_prompt(sess, client_id)
                        holds_control = True
                    if sess["stale"] is not None:
                        # Until answered, every client input is refused (decision 10).
                        ws.send_text(json.dumps({"t": "input_refused", "v": 1, "reason": "stale"}))
                    elif holds_control:
                        self._emit_output(sess, message.data)  # a terminal echoes what is typed
                    else:
                        ws.send_text(json.dumps({"t": "input_refused", "v": 1, "reason": "not_typist"}))
                elif message.kind == "text":
                    try:
                        frame = json.loads(message.data)
                    except ValueError:
                        frame = None
                    if not isinstance(frame, dict):
                        ws.fail(1007, "text frame is not a JSON object")
                        return
                    self.frames.append(frame)
                    t = frame.get("t")
                    if t == "resize":
                        self._size_answer(ws, sess, client_id, frame)
                    elif t == "handoff":
                        self._handoff(ws, sess, client_id, frame)
                    elif t == "stale_answer":
                        self._stale_answer(ws, sess, client_id, frame)

    def _size_answer(self, ws: ServerWS, sess: dict, client_id: str, frame: dict) -> None:
        """A target-size request and its `size` answer (C2 decision 8, D33–D37)."""
        def answer(cols: int, rows: int, reason: str, to_all: bool) -> None:
            body = json.dumps({"t": "size", "v": 1, "session_id": sess["id"],
                               "cols": cols, "rows": rows, "reason": reason})
            if to_all:
                self._broadcast(sess, lambda _cid, sock: sock.send_text(body))
                return
            try:
                ws.send_text(body)
            except OSError:
                pass  # the requester's own socket: its read loop ends the attachment

        if (sess["control_holder"] or {}).get("client_id") != client_id:
            answer(sess["cols"], sess["rows"], "not_typist", to_all=False)
            return
        cols, rows = frame["cols"], frame["rows"]
        # Each dimension is clamped independently against both its floor and its ceiling (C2 decision
        # 8, 5.50 §3), so a request that is below one bound and above another still lands inside the
        # served target's bounds on both axes.
        clamped_cols = min(max(cols, FLOOR_COLS), CEIL_COLS)
        clamped_rows = min(max(rows, FLOOR_ROWS), CEIL_ROWS)
        sess["cols"], sess["rows"] = clamped_cols, clamped_rows
        if clamped_cols != cols or clamped_rows != rows:
            # One reason names the size that applied; a floor clamp takes the reason, else the ceiling.
            reason = "clamped_floor" if clamped_cols > cols or clamped_rows > rows else "clamped_ceiling"
            answer(clamped_cols, clamped_rows, reason, to_all=True)
        else:
            answer(cols, rows, "applied", to_all=True)

    def _handoff(self, ws: ServerWS, sess: dict, client_id: str, frame: dict) -> None:
        """A handoff moves the control token (C2 decision 6)."""
        to = frame.get("to")
        holder_id = (sess["control_holder"] or {}).get("client_id")
        sender_is_owner = self.client_users[client_id] == sess["owner"]
        if client_id == holder_id or sender_is_owner:
            reason = None
        else:
            reason = "not_allowed"
        if reason is None and to is not None:
            dest = self.clients.get(sess["id"], {}).get(to)
            if dest is None:
                reason = "no_such_client"
            elif not self._may_control(self.client_users[to]):
                reason = "view_only"
        if reason is not None:
            ws.send_text(json.dumps({"t": "request_refused", "v": 1, "request": "handoff", "reason": reason}))
            return
        if to is None:
            # An owner client revoked the token to escrow (to null): reason owner.
            sess["control_holder"] = None
            sess["owner_revoked"] = True
            self._void_and_reprompt(sess, None)
            token_reason = "owner"
            self._send_token(sess, token_reason)
            # No prompt sent (moved to escrow)
        else:
            # handoff: the holder moved it to another client (even an owner that is the holder).
            # takeover: the owner took it for itself, by handoff to its own client_id, when it was
            # not the holder. owner: the owner reassigned it to a different client while not the
            # holder doing a plain hand-off.
            if client_id == holder_id:
                token_reason = "handoff"
            elif sender_is_owner and to == client_id:
                token_reason = "takeover"
            elif sender_is_owner:
                token_reason = "owner"
            else:
                token_reason = "handoff"
            sess["control_holder"] = {"client_id": to, "user": self.client_users[to]}
            sess["owner_revoked"] = False
            self._void_and_reprompt(sess, to)
            self._send_token(sess, token_reason)
            self._send_stale_prompt(sess, to)


def _bind_server(bind: str, port: int) -> http.server.ThreadingHTTPServer:
    """A loopback server that actually opens `bind`. ThreadingHTTPServer defaults to AF_INET
    (IPv4-only), so ::1 needs its own IPv6 address family or the bind fails at socket creation after
    the loopback check passes."""
    if bind == "::1":
        import socket as _socket

        class _V6Server(http.server.ThreadingHTTPServer):
            address_family = _socket.AF_INET6

        return _V6Server((bind, port), _Handler)
    return http.server.ThreadingHTTPServer((bind, port), _Handler)


def serve(bind: str = "127.0.0.1", port: int = 8787, static: str | None = None, out=None) -> int:
    """Run the fixture server until interrupted. Returns a process exit status."""
    import sys
    out = out or sys.stdout
    if bind not in ("127.0.0.1", "localhost", "::1"):
        print(f"error: serve listens on loopback only; --bind {bind} is refused", file=sys.stderr)
        return 2
    static_dir = Path(static).resolve() if static else None
    if static_dir is not None and not static_dir.is_dir():
        print(f"error: --static {static} is not a directory", file=sys.stderr)
        return 2
    app = ServeApp()
    httpd = _bind_server(bind, port)
    httpd.daemon_threads = True
    httpd.app = app  # type: ignore[attr-defined]
    httpd.static = static_dir  # type: ignore[attr-defined]
    host, real_port = httpd.server_address[:2]
    # Synthetic credentials only: usernames and target ids are printed; the password is a fixed
    # fixture constant (tests/README name it), never read from the environment.
    print(f"tsbc serve: http://{host}:{real_port} (loopback, plain HTTP)", file=out)
    print(f"  users: {USER}, {PEER}, {VIEWER}", file=out)
    print(f"  targets: {', '.join(sorted(app.targets))}", file=out)
    print(f"  static: {static_dir if static_dir else 'none'}", file=out)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


def add_serve_parser(sub) -> None:
    """`serve` on `python -m tsbc`'s subparsers."""
    s = sub.add_parser("serve", help="run the fixture server the web lane builds against")
    s.add_argument("--bind", default="127.0.0.1", help="loopback address to bind (default 127.0.0.1)")
    s.add_argument("--port", type=int, default=8787, help="port to listen on (default 8787)")
    s.add_argument("--static", metavar="DIR", help="serve a web app from this directory on the same origin")


__all__ = [
    "ALERTS",
    "CEIL_COLS",
    "CEIL_ROWS",
    "ENDED",
    "FLOOR_COLS",
    "FLOOR_ROWS",
    "MAX_TARGET_LIMIT",
    "NOCRED",
    "PASSWORD",
    "PEER",
    "SCENARIOS",
    "TARGET",
    "TARGETS",
    "TARGET_TYPES",
    "UP",
    "USER",
    "VIEWER",
    "WARNING",
    "App",
    "ServeApp",
    "ServerWS",
    "StubServer",
    "add_serve_parser",
    "serve",
]
