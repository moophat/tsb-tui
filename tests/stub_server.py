"""A minimal C1/C2 stub for the pilot tests: replays the shapes contract-v0.1's C1/C2 fixtures
assert (`program/contract/fixtures/C1/session-create-new.json`,
`program/contract/fixtures/C2/attach-ok-bytes-roundtrip.json`), on purpose with delay and fragmentation
between output chunks, so a pilot test can prove the app stays responsive while a slow target is
still writing, and with real UTF-8/escape-sequence fragmentation across frames, so a test proving
that boundary matters has something to prove it against. Test scaffolding, not a second fixture
server: it does not run tsbc's own fixtures and proves nothing on its own — the real proof is a
manual run against a live core, done separately from this test suite.

Built on `tsbc._wsframe` (the same codec `tsb_contract_client` uses), so the wire bytes this stub
sends are exactly what a real WebSocket peer would send, never a hand-rolled approximation.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
import time
from typing import Any

from tsbc import _wsframe as wf

FIXTURE_TICKET = "FIXTURE-TICKET-0000000000000000000000000"
RECORDING = {
    "active": True,
    "warning_code": "recording.active",
    "warning_text": "This session is recorded byte-perfect. Anything typed will be stored as-is.",
}
NOT_RECORDING = {"active": False}
HOSTKEY = {
    "fingerprint": "SHA256:FIXTURESTUBHOSTKEY0000000000000000000000000",
    "first_use": True,
}


class ServerWS:
    """Server side of one WebSocket: reads masked client frames, writes unmasked frames — the
    same split program/contract/tests/stub_server.py uses, kept as its own copy here (tui/ and
    contract/ each ship independently; a sub writes only its own unit's tree)."""

    def __init__(self, rfile, wfile, connection: socket.socket | None = None) -> None:
        self.rfile, self.wfile = rfile, wfile
        self._connection = connection
        self.parser = wf.FrameParser(expect_masked=True)
        self.assembler = wf.MessageAssembler()
        self.pending: list[wf.Frame] = []
        self.closed = False

    def recv(self) -> wf.Message:
        while True:
            while not self.pending:
                data = self.rfile.read1(65536)
                if not data:
                    self.closed = True
                    return wf.Message("close", b"", wf.CLOSE_ABNORMAL)
                self.pending.extend(self.parser.feed(data))
            message = self.assembler.push(self.pending.pop(0))
            if message is not None:
                return message

    def send(self, opcode: int, payload: bytes) -> None:
        self.wfile.write(wf.encode_frame(opcode, payload, mask=False))
        self.wfile.flush()

    def send_text(self, text: str) -> None:
        self.send(wf.OP_TEXT, text.encode("utf-8"))

    def send_binary(self, data: bytes) -> None:
        self.send(wf.OP_BINARY, data)

    def send_binary_fragmented(self, data: bytes, split_at: int) -> None:
        """The same message as two frames — a continuation frame in between — splitting it
        wherever the caller chooses, including mid-UTF-8-sequence or mid-escape-sequence: a real
        target's TCP segmentation has no notion of "do not split a character here", and a client
        that only ever sees whole lines in one frame could hide a bug that only a real, unlucky
        segment boundary would expose."""
        if not 0 < split_at < len(data):
            raise ValueError("split_at must land strictly inside the payload")
        head = wf.encode_frame(wf.OP_BINARY, data[:split_at], fin=False, mask=False)
        tail = wf.encode_frame(wf.OP_CONT, data[split_at:], fin=True, mask=False)
        self.wfile.write(head + tail)
        self.wfile.flush()

    def send_close(self, code: int, reason: str = "") -> None:
        self.send(wf.OP_CLOSE, wf.encode_close(code, reason))

    def send_invalid_frame(self) -> None:
        """A frame RFC 6455 has no opcode for (reserved 0x3): the receiver must fail the
        connection (§5.2). Used to prove a client's receive loop treats a broken frame arriving
        after attach as connection-lost, never a crash."""
        self.wfile.write(bytes([0x83, 0x00]))  # FIN=1, reserved opcode 0x3, empty payload
        self.wfile.flush()

    def drop(self) -> None:
        """An abrupt TCP drop: torn down at the socket, not just this one file wrapper — a
        client's `read()` on the same fd would otherwise never see the EOF a real dead link
        produces, since `rfile`/`wfile` are separate wrappers over one socket."""
        if self._connection is not None:
            try:
                self._connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            self.wfile.close()
        except OSError:
            pass
        self.closed = True


class SlowChunkedApp:
    """One target session: `connection: new` always succeeds. Attach streams binary output every
    `delay_s` until either a client frame arrives or `max_chunks` is reached, then `tail_chunks`
    more so the stream visibly continues afterward — deterministic proof that a keystroke was
    forwarded while output was still arriving, never a race against wall-clock timing (a working
    app forwards it well within `max_chunks`; the hard cap is a failure signal, not a clock)."""

    def __init__(self, delay_s: float = 0.02, max_chunks: int = 400, tail_chunks: int = 5,
                 hostkey_on_create: bool = False, recording_on_create: bool = True,
                 create_error: tuple[int, str, str] | None = None,
                 ticket_status: int = 200, refuse_upgrade: bool = False,
                 attach_close_code: int | None = None, drop_after_chunks: int | None = None,
                 fragment_every: int | None = None, echo_client_frames: bool = False,
                 invalid_frame_after_attach: bool = False,
                 connection_choice_candidates: list[str] | None = None,
                 connection_choice_target: str = "stub-target",
                 sessions_call_delays: tuple[float, ...] = (),
                 sessions_first_call_gate: threading.Event | None = None) -> None:
        self.delay_s = delay_s
        self.max_chunks = max_chunks
        self.tail_chunks = tail_chunks
        self.hostkey_on_create = hostkey_on_create
        self.recording_on_create = recording_on_create
        self.create_error = create_error
        self.ticket_status = ticket_status
        self.refuse_upgrade = refuse_upgrade
        self.attach_close_code = attach_close_code
        self.drop_after_chunks = drop_after_chunks
        self.fragment_every = fragment_every
        # Echoes every client binary frame back over the wire — a real echoing shell or network
        # CLI's own behavior, and what tsb_tui.smoke's probe round trip needs to see to pass.
        self.echo_client_frames = echo_client_frames
        # Sent right after `attached`, before any burst chunk: proves a client's receive loop
        # survives a broken frame arriving once it is already attached, not just during upgrade.
        self.invalid_frame_after_attach = invalid_frame_after_attach
        # Session create refuses reuse without connection_id (409 connection_choice_required)
        # until one of these is named; GET /sessions is seeded with one existing session per
        # candidate so a client's own "fetch and offer" recovery has something real to fetch.
        self.connection_choice_candidates = connection_choice_candidates
        self.sessions_call_delays = sessions_call_delays
        # When set, the first `GET /sessions` is held on this event instead of the delay above,
        # and `sessions_first_call_started` is set once its request is the one being held: a test
        # releases the gate to land an older reply strictly after a newer one, with no wall clock.
        # If the release never comes the held request fails rather than answering with its stale
        # snapshot, so a broken test cannot pass by letting the older reply through early.
        self.sessions_first_call_gate = sessions_first_call_gate
        self.sessions_first_call_started = threading.Event()
        self._sessions_calls = 0

        self.sessions: dict[str, dict] = {}
        self._next_id = 1
        self.chunks_sent = 0  # read by the test thread; plain int, GIL-safe for this scaffolding
        self.chunk_at_first_client_frame: int | None = None
        self.client_frames: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self._client_frame_seen = threading.Event()

        if connection_choice_candidates:
            for connection_id in connection_choice_candidates:
                session_id = f"stub-existing-{connection_id}"
                self.sessions[session_id] = {
                    "id": session_id, "target_id": connection_choice_target,
                    "connection_id": connection_id, "session_type": "shell", "state": "detached",
                    "tier": 1, "owner": "fixture-user", "control_holder": None,
                    "created_at": "2026-09-23T12:00:00Z", "term": "xterm-256color",
                    "cols": 80, "rows": 24, "recording": NOT_RECORDING,
                }

    # -- HTTP (C1) --

    def http(self, method: str, path: str, headers: dict[str, str], body: Any) -> tuple[int, list, Any]:
        if method == "POST" and path == "/auth/login":
            return 200, [("Set-Cookie", "tsb_session=stub; HttpOnly; Secure; SameSite=Strict")], \
                {"username": body["username"]}
        if method == "POST" and path == "/sessions":
            if self.create_error is not None:
                status, code, message = self.create_error
                return status, [], {"error": {"code": code, "message": message}}
            if self.connection_choice_candidates and body["connection"] == "reuse":
                connection_id = body.get("connection_id")
                if connection_id is None:
                    return 409, [], {"error": {"code": "connection_choice_required",
                                               "message": "several connections could be reused; name one"}}
                if connection_id not in self.connection_choice_candidates:
                    return 422, [], {"error": {"code": "invalid_request",
                                               "message": "not a candidate connection"}}
            session_id = f"stub-session-{self._next_id}"
            self._next_id += 1
            session = {
                "id": session_id,
                "target_id": body["target_id"],
                "connection_id": body.get("connection_id") or f"stub-conn-{session_id}",
                "session_type": "shell",
                "state": "detached",
                "tier": 1,
                "owner": "fixture-user",
                "control_holder": None,
                "created_at": "2026-09-23T12:00:00Z",
                "term": body["term"],
                "cols": body["cols"],
                "rows": body["rows"],
                "recording": RECORDING if self.recording_on_create else NOT_RECORDING,
            }
            self.sessions[session_id] = session
            reply: dict[str, Any] = {"session": session}
            if self.hostkey_on_create:
                reply["hostkey"] = HOSTKEY
            return 201, [], reply
        if method == "POST" and path == "/auth/ws-ticket":
            if self.ticket_status != 200:
                code = {401: "unauthenticated", 404: "session_not_found"}.get(self.ticket_status, "error")
                return self.ticket_status, [], {"error": {"code": code, "message": "stub ticket failure"}}
            return 200, [], {"v": 1, "ticket": FIXTURE_TICKET, "expires_in": 30}
        if method == "GET" and path == "/sessions":
            # Snapshot before the delay, not after: this is what the list looked like when the
            # request arrived, so a slow reply proves stale — not accidentally fresh because the
            # world changed while it sat sleeping.
            snapshot = list(self.sessions.values())
            call_index = self._sessions_calls
            self._sessions_calls += 1
            if self.sessions_first_call_gate is not None and call_index == 0:
                self.sessions_first_call_started.set()
                if not self.sessions_first_call_gate.wait(timeout=10):
                    # The test releases the gate only after the newer reply is applied; a timeout
                    # is a broken test, never a reason to answer with the stale snapshot below.
                    return 503, [], {"error": {"code": "gate_timeout",
                                               "message": "sessions gate was never released"}}
            elif call_index < len(self.sessions_call_delays):
                time.sleep(self.sessions_call_delays[call_index])
            return 200, [], {"sessions": snapshot}
        if method == "GET" and path.startswith("/sessions/"):
            session = self.sessions.get(path.rsplit("/", 1)[-1])
            if session is None:
                return 404, [], {"error": {"code": "session_not_found", "message": "no such session"}}
            return 200, [], session
        if method == "DELETE" and path.startswith("/sessions/"):
            session = self.sessions.get(path.rsplit("/", 1)[-1])
            if session is None:
                return 404, [], {"error": {"code": "session_not_found", "message": "no such session"}}
            session["state"] = "killed"
            session["control_holder"] = None
            return 204, [], None
        return 404, [], {"error": {"code": "session_not_found", "message": "no route in stub"}}

    ws_paths = ("/ws/attach",)

    # -- WebSocket (C2) --

    def websocket(self, path: str, ws: ServerWS) -> None:
        message = ws.recv()
        frame = json.loads(message.data)
        if self.attach_close_code is not None:
            ws.send_close(self.attach_close_code, "stub refusal")
            return
        session = self.sessions[frame["session_id"]]
        session["state"] = "live"
        session["control_holder"] = {"client_id": "stub-client-1", "user": "fixture-user"}
        ws.send_text(json.dumps({
            "t": "attached", "v": 1, "session": session, "client_id": "stub-client-1",
        }))

        if self.invalid_frame_after_attach:
            # A short delay first: sent back-to-back, the `attached` text frame and this one can
            # land in the same read from the socket, so the client's own attach loop — which
            # parses every frame in whatever it reads in one call — would be the one to see the
            # broken frame, rather than the receive loop this mode means to exercise.
            time.sleep(0.1)
            ws.send_invalid_frame()
            return

        # Read concurrently with the burst below: a stub that only reads once the burst is over
        # could never show a keystroke being handled while output is still arriving.
        reader = threading.Thread(target=self._read_client_frames, args=(ws,), daemon=True)
        reader.start()

        chunks_after_seen = 0
        for i in range(self.max_chunks):
            self._send_chunk(ws, i)
            self.chunks_sent = i + 1
            if self.drop_after_chunks is not None and self.chunks_sent >= self.drop_after_chunks:
                # No close frame, so the client sees an abnormal close (1006) rather than any of
                # the contract's own end-state codes.
                ws.drop()
                return
            if self._client_frame_seen.is_set():
                chunks_after_seen += 1
                if chunks_after_seen >= self.tail_chunks:
                    break
            time.sleep(self.delay_s)
        reader.join()
        session["state"] = "detached"
        session["control_holder"] = None

    def _send_chunk(self, ws: ServerWS, i: int) -> None:
        data = f"line {i:03d} of slow output\r\n".encode()
        if self.fragment_every and i % self.fragment_every == 0 and len(data) > 1:
            ws.send_binary_fragmented(data, split_at=len(data) // 2)
        else:
            ws.send_binary(data)

    def _read_client_frames(self, ws: ServerWS) -> None:
        while not ws.closed:
            message = ws.recv()
            if message.kind == "close":
                break
            if message.kind == "binary":
                if self.chunk_at_first_client_frame is None:
                    self.chunk_at_first_client_frame = self.chunks_sent
                self.client_frames.append(message.data)
                self._client_frame_seen.set()
                if self.echo_client_frames:
                    ws.send_binary(message.data)
            elif message.kind == "text":
                text_frame = json.loads(message.data)
                if text_frame.get("t") == "resize":
                    self.resizes.append((text_frame["cols"], text_frame["rows"]))


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:  # keep test output quiet
        pass

    def _dispatch(self, method: str) -> None:
        app: SlowChunkedApp = self.server.app  # type: ignore[attr-defined]
        headers = {k.lower(): v for k, v in self.headers.items()}
        if method == "GET" and headers.get("upgrade", "").lower() == "websocket" and self.path in app.ws_paths:
            if app.refuse_upgrade:
                self.send_response(503, "Service Unavailable")
                self.send_header("Content-Length", "0")
                self.end_headers()
                self.close_connection = True
                return
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", wf.accept_for(headers.get("sec-websocket-key", "")))
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            app.websocket(self.path, ServerWS(self.rfile, self.wfile, self.connection))
            return
        length = int(headers.get("content-length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        status, extra, reply = app.http(method, self.path, headers, body)
        data = b"" if reply is None else json.dumps(reply).encode("utf-8")
        self.send_response(status)
        for key, value in extra:
            self.send_header(key, value)
        if data:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")


class StubServer:
    """`with StubServer(app) as url:` — serves on 127.0.0.1 on a free loopback port."""

    def __init__(self, app: SlowChunkedApp) -> None:
        self.app = app
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.app = app  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
