"""An in-memory stand-in for the core's C1/C2 behaviour, for testing the runner and the client.

Test scaffolding, not a fixture server: it subclasses the shipped `tsbc/_serve.py` app (the same
code `python -m tsbc serve` runs) and adds the `BUGS` fault toggles that let the tests show each
provider bug turns its fixture red. The shipped module never carries a fault toggle.
"""

from __future__ import annotations

import json
import secrets
import time

from tsbc import _wsframe as wf
from tsbc._serve import (
    ALERTS,
    BROKER_HEALTH,
    ENDED,
    NOCRED,
    PASSWORD,
    PEER,
    TARGET,
    UP,
    USER,
    VIEWER,
    WARNING,
    App,
    ServeApp,
    ServerWS,
)

BUGS = frozenset({
    "cookie-not-httponly",      # login cookie readable by page script
    "logout-client-only",       # logout clears the cookie but the server session stays valid
    "ticket-reusable",          # a ticket opens more than one attach
    "ticket-any-session",       # a ticket opens a session it was not minted for
    "leak-recording-path",      # the session record carries the broker's recording path
    "no-cred-403",              # visible target without credential answered 403
    "reuse-opens-new",          # connection: reuse ignored
    "delete-kills-siblings",    # closing one session closes its connection's other sessions
    "peer-may-delete",          # a non-owner without session.terminate may end a session
    "unknown-frame-closes",     # an unknown frame type closes the socket
    "healthz-leaks-version",    # /healthz reveals a version string
    "ticket-never-expires",     # an expired ticket still opens an attach
    "reuse-picks-silently",     # reuse with two candidate connections picks one instead of asking
    "reuse-needs-a-session",    # reuse skips a connection that is up but has no session on it
    "error-leaks-path",         # an error message names a path on the broker host
    "login-echoes-password",    # a refused login echoes the submitted password in its message
    "countdown-missing",        # a live session's record carries no ends_at / ends_by / ending
    "reuse-409-lists-all",      # the 409 lists every connection to the target, another principal's too
    "reuse-409-no-ids",         # the 409 lists no candidate connection at all
    "alerts-open",              # the alert stream is served to a principal without broker.health
    "token-silent",             # the control token passes on without a token frame
    "login-on-reuse-accepted",  # a create with connection reuse and a login is accepted, the login ignored
})

__all__ = [
    "ALERTS",
    "BROKER_HEALTH",
    "BUGS",
    "ENDED",
    "NOCRED",
    "PASSWORD",
    "PEER",
    "TARGET",
    "UP",
    "USER",
    "VIEWER",
    "WARNING",
    "App",
    "ServerWS",
    "StubCore",
]


class StubCore(ServeApp):
    """The served app plus the test-only fault toggles. Every bug branch lives only here."""

    def __init__(self, bugs: frozenset[str] = frozenset(), recording: bool = True, ticket_ttl: int = 3,
                 linger: bool = True, after_attach: tuple[dict, ...] = (), login_input: bool = False) -> None:
        unknown = set(bugs) - BUGS
        if unknown:
            raise ValueError(f"unknown bug switch {unknown}")
        super().__init__(recording=recording, ticket_ttl=ticket_ttl, linger=linger, after_attach=after_attach,
                         login_input=login_input)
        self.bugs = frozenset(bugs)

    # -- helpers that consult bugs --

    def _record(self, sess: dict) -> dict:
        rec = super()._record(sess)
        if sess["state"] not in ENDED and "countdown-missing" in self.bugs:
            for key in ("ends_at", "ends_by", "ending"):
                rec.pop(key, None)
        if "leak-recording-path" in self.bugs:
            rec["recording_path"] = f"/tmp/recordings/{sess['id']}.bin"
        return rec

    def _send_token(self, sess: dict, reason: str) -> None:
        if "token-silent" in self.bugs:
            return
        super()._send_token(sess, reason)

    # -- HTTP --

    def _http(self, method, path, headers, body, query=None):
        if (method, path) == ("GET", "/healthz"):
            reply = {"status": "ok"}
            if "healthz-leaks-version" in self.bugs:
                reply["version"] = "0.0.0-stub"
            return 200, [], reply
        if (method, path) == ("POST", "/auth/login"):
            if not isinstance(body, dict) or not body.get("username") or not body.get("password"):
                return self._err(422, "invalid_request", "username and password are required")
            if "login-echoes-password" in self.bugs:
                return self._err(401, "invalid_credentials", f"rejected password {body['password']}")
            if body["username"] not in (USER, PEER, VIEWER) or body["password"] != PASSWORD:
                return self._err(401, "invalid_credentials", "unknown user or wrong password")
            cookie = secrets.token_urlsafe(24)
            self.logins[cookie] = body["username"]
            flags = "; Secure; SameSite=Strict; Path=/"
            if "cookie-not-httponly" not in self.bugs:
                flags = "; HttpOnly" + flags
            return 200, [("Set-Cookie", f"tsb_session={cookie}{flags}")], {"username": body["username"]}

        user = self._user(headers)
        if user is None:
            return self._err(401, "unauthenticated", "log in first")
        if (method, path) == ("POST", "/auth/logout"):
            if "logout-client-only" not in self.bugs:
                for cookie, who in list(self.logins.items()):
                    if who == user and f"tsb_session={cookie}" in headers.get("cookie", ""):
                        del self.logins[cookie]
            return 204, [("Set-Cookie", "tsb_session=; Max-Age=0; Path=/")], None
        if (method, path) == ("GET", "/alerts"):
            if user not in BROKER_HEALTH and "alerts-open" not in self.bugs:
                return self._err(403, "forbidden", "the alert stream needs broker.health")
            return 200, [("Content-Type", "text/event-stream")], self.alerts_body
        if "peer-may-delete" in self.bugs:
            parts = path.strip("/").split("/")
            if len(parts) == 2 and parts[0] == "sessions" and method == "DELETE":
                sess = self.sessions.get(parts[1])
                if sess is not None and user != sess["owner"]:
                    self._kill(sess)
                    return 204, [], None
        return super()._http(method, path, headers, body, query)

    def _create(self, user: str, body, cookie: str = ""):
        required = {"target_id", "connection", "term", "cols", "rows"}
        login_on_reuse = "login-on-reuse-accepted" in self.bugs
        if not isinstance(body, dict) or not required <= set(body) <= required | {"connection_id", "login"} \
                or body["connection"] not in ("reuse", "new") or not self._valid_size(body) \
                or ("connection_id" in body and body["connection"] != "reuse") \
                or ("login" in body and (not self._valid_login(body["login"])
                                         or (body["connection"] != "new" and not login_on_reuse))):
            return self._err(422, "invalid_request", "target_id, connection (reuse|new), term, cols, rows")
        target = body["target_id"]
        if target == NOCRED and "login" not in body:
            if "no-cred-403" in self.bugs:
                return self._err(403, "forbidden", "no")
            return self._err(409, "no_resolving_credential", "no credential resolves for this target")
        if target not in (TARGET,) and not (target == NOCRED and "login" in body):
            message = "no such target"
            if "error-leaks-path" in self.bugs:
                message = f"no file /var/lib/tsb/inventory/manual/{target}.yaml"
            return self._err(404, "target_not_found", message)
        refusal = self._unacknowledged(body)
        if refusal is not None:
            return refusal
        candidates = self._candidates(user, target)
        if body["connection"] == "new" or "reuse-opens-new" in self.bugs:
            conn = self._open_connection(user, target, supplied_login="login" in body)
        elif "connection_id" in body:
            if body["connection_id"] not in candidates:
                return self._err(422, "invalid_request", "connection_id is not a connection reuse may pick")
            conn = body["connection_id"]
        elif len(candidates) > 1 and "reuse-picks-silently" not in self.bugs:
            status, headers, reply = self._err(409, "connection_choice_required", "several connections could be reused; name one")
            if "reuse-409-lists-all" in self.bugs:
                candidates = sorted(c for c, conn in self.connections.items() if conn["target_id"] == target and conn["up"])
            if "reuse-409-no-ids" not in self.bugs:
                reply["error"]["connection_ids"] = candidates
            return status, headers, reply
        else:
            conn = candidates[0] if candidates else self._open_connection(user, target)
        sid = self._next("sess")
        self.sessions[sid] = {
            "id": sid, "target_id": target, "connection_id": conn, "session_type": "shell",
            "state": "detached", "tier": 1, "owner": user, "control_holder": None, "owner_revoked": False,
            "created_at": "2026-09-23T12:00:00Z", "term": body["term"], "cols": body["cols"], "rows": body["rows"],
            "output": [], "next_seq": 0, "stale": None, "stale_wanted": False,
        }
        return 201, [], {"session": self._record(self.sessions[sid])}

    def _candidates(self, user: str, target: str) -> list[str]:
        if "reuse-needs-a-session" in self.bugs:
            conns = {s["connection_id"] for s in self.sessions.values()
                     if s["owner"] == user and s["target_id"] == target and s["state"] in UP
                     and not self.connections[s["connection_id"]]["supplied_login"]}
            return sorted(conns)
        return super()._candidates(user, target)

    def _remove_client(self, user: str, sess: dict, client_id: str):
        if user != sess["owner"] and "peer-may-delete" not in self.bugs:
            return self._err(403, "forbidden", "only the owner or session.terminate may remove this client")
        ws = self.clients.get(sess["id"], {}).get(client_id)
        if ws is None:
            return self._err(404, "client_not_found", "no such client")
        self._drop_client(sess, client_id, code=4014, reason="removed")
        return 204, [], None

    def _kill(self, sess: dict) -> None:
        if "delete-kills-siblings" not in self.bugs:
            return super()._kill(sess)
        victims = [s for s in self.sessions.values() if s["connection_id"] == sess["connection_id"]]
        for victim in victims:
            victim["state"], victim["control_holder"] = "killed", None
            for ws in list(self.clients.pop(victim["id"], {}).values()):
                try:
                    ws.send(wf.OP_CLOSE, wf.encode_close(4010, "session killed"))
                except OSError:
                    pass
                ws.closed = True
        for conn in {victim["connection_id"] for victim in victims}:
            left = [s for s in self.sessions.values() if s["connection_id"] == conn and s["state"] in UP]
            if not left and not self.linger:
                self.connections[conn]["up"] = False

    # -- WebSocket --

    def websocket(self, path: str, ws: ServerWS) -> None:
        first = ws.recv()
        frame = None
        if first.kind == "text":
            try:
                frame = json.loads(first.data)
            except ValueError:
                frame = None
        if not (isinstance(frame, dict) and frame.get("t") == "attach" and frame.get("v") == 1
                and isinstance(frame.get("ticket"), str) and isinstance(frame.get("session_id"), str)):
            ws.close(4000, "the first frame must be attach")
            return
        self.frames.append(frame)
        with self.lock:
            entry = self.tickets.get(frame["ticket"])
            expired = entry is not None and entry[2] < time.monotonic() and "ticket-never-expires" not in self.bugs
            if entry is None or expired:
                ws.close(4001, "ticket invalid")
                return
            if "ticket-reusable" not in self.bugs:
                del self.tickets[frame["ticket"]]
            user, sid, _ = entry
            if sid != frame["session_id"] and "ticket-any-session" not in self.bugs:
                ws.close(4003, "ticket not valid for this session")
                return
            sess = self.sessions.get(frame["session_id"])
            if sess is None or sess["state"] in ENDED:
                ws.close(4004, "no such session")
                return
            client_id = self._next("client")
            self.clients.setdefault(sess["id"], {})[client_id] = ws
            self.client_users[client_id] = user
            if sess["control_holder"] is None and not sess.get("owner_revoked") and self._may_control(user):
                sess["control_holder"] = {"client_id": client_id, "user": user}
            sess["state"] = "live"
            ws.send_text(json.dumps({"t": "attached", "v": 1, "session": self._record(sess), "client_id": client_id}))
            for extra in self.after_attach:
                ws.send_text(json.dumps(extra))
            prefix_frame, dump = self._prefix_step(sess)
            ws.send_text(json.dumps(prefix_frame))
            ws.send_binary(dump)
            self._send_clients(sess)
        try:
            self._stream(ws, sess, client_id)
        finally:
            with self.lock:
                if self.clients.get(sess["id"], {}).get(client_id) is not None:
                    self._drop_client(sess, client_id, code=1000, reason="")
                else:
                    if not self.clients.get(sess["id"]) and sess["state"] not in ENDED:
                        sess["state"] = "detached"

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
                        sess["control_holder"] = {"client_id": client_id, "user": self.client_users[client_id]}
                        sess["owner_revoked"] = False
                        self._send_token(sess, "takeover")
                        holds_control = True
                    if sess["stale"] is not None:
                        ws.send_text(json.dumps({"t": "input_refused", "v": 1, "reason": "stale"}))
                    elif holds_control:
                        self._emit_output(sess, message.data)
                    else:
                        ws.send_text(json.dumps({"t": "input_refused", "v": 1, "reason": "not_typist"}))
                elif message.kind == "text":
                    frame = json.loads(message.data)
                    self.frames.append(frame)
                    t = frame.get("t")
                    if t == "resize":
                        self._size_answer(ws, sess, client_id, frame)
                    elif t == "handoff":
                        self._handoff(ws, sess, client_id, frame)
                    elif t == "stale_answer":
                        self._stale_answer(ws, sess, client_id, frame)
                    elif "unknown-frame-closes" in self.bugs:
                        ws.close(1003, "unknown frame")
                        return