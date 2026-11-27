"""The northbound client: TSB's own login, sessions, one-time tickets, the target inventory, attach.

Written against openapi/tsb.openapi.json and schemas/ws-frames.schema.json at this tag; the
contract tests check every call it makes and every frame it sends against them. The login cookie
lives in this object's memory only and is never written anywhere (decision 5.34).
"""

from __future__ import annotations

import asyncio
import urllib.parse
from collections.abc import Iterator
from typing import Any

from tsbc import _http
from tsbc.schema import loads

from .attachment import AsyncAttachment, Attachment
from .events import AlertStream

ATTACH_PATH = "/ws/attach"
ALERTS_PATH = "/alerts"
TARGETS_PATH = "/targets"
CONNECTION_CHOICES = ("reuse", "new")  # Contract §15.3: the surface chooses; there is no default


def _target_record(record: dict) -> dict:
    """V5: unknown/absent login hints make no availability claim; retain the target."""
    out = dict(record)
    if out.get("type") != "ssh" or out.get("login_hint") not in ("saved", "choice_required", "none", "unavailable"):
        out.pop("login_hint", None)
    return out


class ApiError(Exception):
    """A non-2xx answer. `code` is the contract's error code (schemas/error.schema.json).
    `detail` holds the error body's members beside `code` and `message` — `candidates`, `attempts_remaining`,
    `connection_ids`, `credential_refs`, `credential_ref` — as the JSON decoded them; {} when the body carries
    none, is not JSON, or has no `error` object."""

    def __init__(self, status: int, code: str | None, message: str, detail: dict | None = None) -> None:
        super().__init__(f"HTTP {status} {code or ''}: {message}".strip())
        self.status = status
        self.code = code
        self.message = message
        self.detail = {} if detail is None else detail


class Client:
    def __init__(self, base_url: str, ca_file: str | None = None, *, timeout: float = 10.0) -> None:
        self._http = _http.HttpClient(base_url, ca_file=ca_file, timeout=timeout)
        self.timeout = timeout

    def _call(self, method: str, path: str, body: Any = None, *, expect: int) -> Any:
        if body is None:
            resp = self._http.request(method, path)
        else:
            resp = self._http.request(method, path, body=body)
        data = None
        if resp.body.strip():
            try:
                data = resp.json()
            except ValueError:
                raise ApiError(resp.status, None, "response body is not JSON") from None
        if resp.status != expect:
            err = data.get("error") if isinstance(data, dict) else None
            err = err if isinstance(err, dict) else {}
            detail = {k: v for k, v in err.items() if k not in ("code", "message")}
            raise ApiError(resp.status, err.get("code"), err.get("message", "unexpected response"), detail=detail)
        return data

    # -- auth --

    def login(self, user: str, password: str) -> dict:
        return self._call("POST", "/auth/login", {"username": user, "password": password}, expect=200)

    def logout(self) -> None:
        try:
            self._call("POST", "/auth/logout", expect=204)
        finally:
            self._http.jar.clear()

    def me(self) -> dict:
        return self._call("GET", "/auth/me", expect=200)

    def ws_ticket(self, session_id: str) -> dict:
        return self._call("POST", "/auth/ws-ticket", {"session_id": session_id}, expect=200)

    # -- accounts (E4b, account-administration) --
    #
    # The five administrator operations need account.admin; own change is every valid login's. A
    # password travels in its request body and nowhere else: this client never logs one, never keeps
    # one and never puts one in an exception.

    def accounts(self) -> list[dict]:
        """GET /accounts: every account, enabled and disabled, ordered by username ascending —
        username, created_at and disabled only, never a hash or a right."""
        return self._call("GET", "/accounts", expect=200)["accounts"]

    def create_account(self, username: str, password: str) -> dict:
        """POST /accounts: a never-before-used name with a starting password, created enabled and
        usable at once, granting no rights. The 201 answer is the AccountCreated body: {"account":
        summary, "permissions"} — the names policy already grants this username. A reused name is
        ApiError user_exists (409), disabled included."""
        return self._call("POST", "/accounts", {"username": username, "password": password}, expect=201)

    def disable_account(self, username: str) -> None:
        """POST /accounts/{username}/disable: void the account's hash and end every login of that
        user before the answer. Idempotent: an already-disabled account answers 204 with nothing
        touched. The caller cannot disable itself (409 account_self_disable)."""
        self._call("POST", f"/accounts/{_segment(username)}/disable", expect=204)

    def enable_account(self, username: str, password: str) -> None:
        """POST /accounts/{username}/enable: set this new password on a disabled account and clear
        the flag — the old hash is never restored. Not idempotent: an enabled account answers 409
        account_already_enabled without setting the password."""
        self._call("POST", f"/accounts/{_segment(username)}/enable", {"password": password}, expect=204)

    def set_account_password(self, username: str, password: str) -> None:
        """POST /accounts/{username}/password: a new password on an enabled account, ending its
        every login (the caller's too, when acting on itself); a disabled account answers 409
        account_disabled — enable alone gives it a usable password."""
        self._call("POST", f"/accounts/{_segment(username)}/password", {"password": password}, expect=204)

    def change_own_password(self, current_password: str, new_password: str) -> None:
        """POST /auth/password: prove the current password and set a new one. This login and
        everything it holds stay; every other login of the user ends. A wrong proof is ApiError 403
        password_change_failed — never 401, never a lost login — and the current-password proof is
        not subject to the new minimum."""
        self._call("POST", "/auth/password",
                   {"current_password": current_password, "new_password": new_password}, expect=204)

    # -- the operator alert stream --

    def alerts(self, read_timeout: float | None = None) -> AlertStream:
        """GET /alerts: TSB's own alerts, for a principal holding broker.health — the active set,
        then each raise and clear. read_timeout bounds each read (None: block); the core sends a
        keepalive comment every heartbeat_interval, so a bound must be above that. A refusal (401,
        403) is ApiError, as for every other call."""
        stream = self._http.stream(ALERTS_PATH, accept="text/event-stream", timeout=read_timeout)
        if stream.status != 200:
            try:
                raw = stream.read()
            finally:
                stream.close()
            try:
                err = loads(raw.decode("utf-8")).get("error", {}) if raw.strip() else {}
            except (ValueError, AttributeError):
                err = {}
            raise ApiError(stream.status, err.get("code"), err.get("message", "unexpected response"))
        return AlertStream(stream)

    # -- the vault and the two locks (from the vault release: C7, and C1's E4 operations) --
    #
    # These carry byte values the caller derived (padded base64; device_id base64url): this client computes no
    # argon2id, opens nothing and signs nothing. It sends part1, part2, a signature or a secret as given, in the
    # request body and nowhere else, and never logs or keeps one.

    def vault_salt(self) -> dict:
        """{"initialized": bool, "user_salt"?: base64}. Writes nothing."""
        return self._call("GET", "/vault/salt", expect=200)

    def vault_mint_salt(self) -> dict:
        """Initialisation's first call: {"initialized": false, "user_salt": base64}. A repeat while the vault is not
        sealed answers the same salt; on a sealed vault, ApiError vault_exists."""
        return self._call("POST", "/vault/salt", expect=200)

    def vault_init(self, part1: str, part2: str) -> None:
        """Initialisation's second call: both halves, derived from the minted salt. The vault is sealed and this login
        holds an unlock. ApiError vault_not_minted means mint first."""
        self._call("POST", "/vault/init", {"part1": part1, "part2": part2}, expect=204)

    def vault_unlock_untrusted(self, part1: str, part2: str) -> None:
        """Both halves (credential-protection §5.1); this login then holds an unlock under the vault lock."""
        self._call("POST", "/vault/unlock", {"mode": "untrusted", "part1": part1, "part2": part2}, expect=204)

    def vault_unlock_trusted(self, challenge_id: str, part1: str, signature: str) -> None:
        """Part 1 and a signed vault.unlock challenge for this login's tied device (§5.4). On ApiError
        part2_missing, regenerate (regenerate_part2) with the passphrase already in hand."""
        self._call("POST", "/vault/unlock", {"mode": "trusted", "challenge_id": challenge_id, "part1": part1,
                                             "signature": signature}, expect=204)

    def regenerate_part2(self, challenge_id: str, part1: str, part2: str, signature: str) -> None:
        self._call("POST", "/vault/part2", {"challenge_id": challenge_id, "part1": part1, "part2": part2,
                                            "signature": signature}, expect=204)

    def device_challenge(self, operation: str, device_id: str) -> dict:
        """{"challenge_id", "challenge", "login_handle", "expires_in"}. Derive everything first, then ask,
        then sign and send at once. Drop a stored device key on ApiError device_not_trusted only."""
        return self._call("POST", "/devices/challenges", {"operation": operation, "device_id": device_id}, expect=200)

    def register_device(self, challenge_id: str, part1: str, part2: str, public_key: str, signature: str,
                        device_name: str) -> dict:
        return self._call("POST", "/devices", {"challenge_id": challenge_id, "part1": part1, "part2": part2,
                                               "public_key": public_key, "signature": signature,
                                               "device_name": device_name}, expect=201)

    def devices(self, user: str | None = None) -> list[dict]:
        path = "/devices" if user is None else "/devices?" + urllib.parse.urlencode({"user": user})
        return self._call("GET", path, expect=200)["devices"]

    def revoke_device(self, device_id: str, user: str | None = None) -> None:
        path = f"/devices/{_segment(device_id)}"
        if user is not None:
            path += "?" + urllib.parse.urlencode({"user": user})
        self._call("DELETE", path, expect=204)

    def wipe_vault(self, user: str) -> None:
        """A vault administrator's wipe of a user's vault (irreversible)."""
        self._call("DELETE", f"/vault/users/{_segment(user)}", expect=204)

    def store_credential(self, reference: str, kind: str, login_name: str | None = None,
                         secret: str | None = None, label: str | None = None) -> dict:
        return self._call("POST", "/vault/credentials",
                          _drop_none({"reference": reference, "kind": kind, "login_name": login_name,
                                      "secret": secret, "label": label}), expect=201)

    def credentials(self) -> list[dict]:
        return self._call("GET", "/vault/credentials", expect=200)["credentials"]

    def delete_credential(self, reference: str) -> None:
        self._call("DELETE", f"/vault/credentials/{_segment(reference)}", expect=204)

    def store_org_credential(self, reference: str, unlock_class: str, kind: str, login_name: str | None = None,
                             secret: str | None = None, label: str | None = None) -> dict:
        return self._call("POST", "/vault/org-credentials",
                          _drop_none({"reference": reference, "unlock_class": unlock_class, "kind": kind,
                                      "login_name": login_name, "secret": secret, "label": label}), expect=201)

    def org_credentials(self) -> list[dict]:
        return self._call("GET", "/vault/org-credentials", expect=200)["credentials"]

    def delete_org_credential(self, reference: str) -> None:
        self._call("DELETE", f"/vault/org-credentials/{_segment(reference)}", expect=204)

    def create_grant(self, user: str, selectors: list[str], credential_ref: str) -> dict:
        return self._call("POST", "/vault/grants", {"user": user, "selectors": list(selectors),
                                                    "credential_ref": credential_ref}, expect=201)

    def change_grant_scope(self, grant_id: str, selectors: list[str]) -> dict:
        return self._call("POST", f"/vault/grants/{_segment(grant_id)}", {"selectors": list(selectors)}, expect=200)

    def revoke_grant(self, grant_id: str) -> None:
        self._call("DELETE", f"/vault/grants/{_segment(grant_id)}", expect=204)

    def map_profiles(self, mappings: dict[str, str | None]) -> None:
        """Layer 2a: {target_id: own credential_ref, or None to remove the mapping}, all in one call."""
        self._call("POST", "/profiles/batch",
                   {"mappings": [{"target_id": t, "credential_ref": r} for t, r in mappings.items()]}, expect=204)

    def bind_login(self, challenge_id: str, signature: str) -> None:
        self._call("POST", "/auth/bind", {"challenge_id": challenge_id, "signature": signature}, expect=204)

    def activity(self) -> None:
        """The user-activity signal; send it at most every me()["locks"]["activity_interval"] seconds."""
        self._call("POST", "/auth/activity", expect=204)

    def lock_now(self) -> None:
        """Purge every vault unlock the user holds; the caller then locks its own screen."""
        self._call("POST", "/auth/lock", expect=204)

    def screen_unlock(self, password: str) -> None:
        """The login password, re-checked by the core. ApiError screen_unlock_failed (403) leaves the login as it
        was: never treat it as a lost login."""
        self._call("POST", "/auth/screen-unlock", {"password": password}, expect=204)

    # -- sessions --

    def create_session(self, target_id: str, connection: str, term: str, cols: int, rows: int,
                       connection_id: str | None = None, login: dict | None = None,
                       credential: tuple[str, str] | None = None) -> dict:
        """The response: {"session": record, "hostkey"?: {...}}. Show session.recording.warning_text,
        and hostkey.fingerprint when present, before attaching.

        connection_id picks the connection to reuse; an ApiError with code
        connection_choice_required means the operator must pick one (GET /sessions lists them).

        login is SessionLogin, {"user": ..., "auth": {"kind": "password", "password": ...}}: a username
        and password for this one new connection, used only to authenticate it and never stored. It is
        sent as given, in the request body and nowhere else: this client never logs it, never keeps it
        and never puts it in an exception. A core that records supplied logins says so in
        `me()["recording"]["login_input"]` (true only while recording is enabled and the knob is on); warn the
        operator before sending a login when it is true, and set `"recording_acknowledged": True` in the
        login once they have seen the warning: without it the core answers 409
        `login_recording_not_acknowledged` and sends nothing.
        A login with connection "reuse" is 422, for the core to refuse.

        credential is the operator's choice of stored credential, (credential_ref, credential_layer), after an
        ApiError with code credential_choice_required listed the candidates in `ApiError.detail["candidates"]`: put
        the choice to the operator and repeat the create with it; never pick one for them (Contract §12). A choice
        and a login are exclusive in one request.
        """
        if connection not in CONNECTION_CHOICES:
            raise ValueError(f"connection must be one of {CONNECTION_CHOICES}, got {connection!r}")
        if connection_id is not None and connection != "reuse":
            raise ValueError("connection_id names a connection to reuse, so it needs connection='reuse'")
        body = {"target_id": target_id, "connection": connection, "term": term, "cols": cols, "rows": rows}
        if connection_id is not None:
            body["connection_id"] = connection_id
        if login is not None and credential is not None:
            raise ValueError("a login and a credential choice are exclusive in one request")
        if login is not None:
            body["login"] = login
        if credential is not None:
            body["credential_ref"], body["credential_layer"] = credential
        return self._call("POST", "/sessions", body, expect=201)

    def create_device_login(self, target_id: str, user: str, term: str, cols: int, rows: int, *,
                            recording_acknowledged: bool = False) -> dict:
        """A create in prompt mode (Release B): `login {user, auth: {kind: prompt}}` on a new connection. No secret
        is sent, and no session exists yet. The answer is the 202's body, `{"device_login_id", "ticket",
        "expires_in"}`: open the attach socket within `expires_in` seconds with AsyncDeviceLogin.open, and answer
        the device's own questions there. `user` is the target's account (what the surface asked at `login as:`).
        Warn the operator before asking it when `me()["recording"]["login_input"]` is true, and pass
        recording_acknowledged=True once they have seen the warning: without it the core answers 409
        `login_recording_not_acknowledged`. A 503 or a 422 means the way is not open on this core."""
        body = {"target_id": target_id, "connection": "new", "term": term, "cols": cols, "rows": rows,
                "login": {"user": user, "auth": {"kind": "prompt"},
                          "recording_acknowledged": bool(recording_acknowledged)}}
        return self._call("POST", "/sessions", body, expect=202)

    def sessions(self) -> list[dict]:
        return self._call("GET", "/sessions", expect=200)["sessions"]

    def list_sessions(self, *, ended: str | None = None, limit: int,
                      cursor: str | None = None) -> dict:
        """GET /sessions: one SessionList page, newest creation first, ties by id descending.
        limit is 1..500 and required on every call. ended is include, exclude or only.
        Copy next_cursor unchanged to continue; this call never drains pages or falls back
        to an unbounded legacy request. A 422 is ApiError for the surface to handle once.
        """
        pairs = [("limit", str(limit))]
        if ended is not None:
            pairs.append(("ended", ended))
        if cursor is not None:
            pairs.append(("cursor", cursor))
        return self._call("GET", "/sessions?" + urllib.parse.urlencode(pairs), expect=200)

    def session(self, session_id: str) -> dict:
        return self._call("GET", f"/sessions/{_segment(session_id)}", expect=200)

    # Resizing is the attached control-token holder's: Attachment.resize sends the C2 frame.

    def close(self, session_id: str) -> None:
        self._call("DELETE", f"/sessions/{_segment(session_id)}", expect=204)

    def remove_client(self, session_id: str, client_id: str) -> None:
        """DELETE /sessions/{id}/clients/{client_id}: remove one attached client. The client's
        socket is closed 4014 `removed`, the session lives on. Owner or session.terminate only."""
        self._call("DELETE", f"/sessions/{_segment(session_id)}/clients/{_segment(client_id)}", expect=204)

    def output_page(self, session_id: str, before_seq: int | None = None,
                    max_bytes: int | None = None) -> tuple[bytes, dict[str, str]]:
        """GET /sessions/{id}/output: a page of recorded output. Returns (body, headers), body the
        raw octet-stream bytes and headers the TSB-Page-* response headers (first/last seq, more,
        gap, no-ground, next-before-seq), lower-cased names, those present only. A core without a
        ring answers ApiError code not_implemented."""
        query: dict[str, str] = {}
        if before_seq is not None:
            query["before_seq"] = str(before_seq)
        if max_bytes is not None:
            query["max_bytes"] = str(max_bytes)
        path = f"/sessions/{_segment(session_id)}/output"
        if query:
            path += "?" + urllib.parse.urlencode(query)
        resp = self._http.request("GET", path, accept="application/octet-stream")
        if resp.status != 200:
            try:
                data = resp.json() if resp.body.strip() else {}
            except ValueError:
                raise ApiError(resp.status, None, "response body is not JSON") from None
            err = data.get("error", {}) if isinstance(data, dict) else {}
            raise ApiError(resp.status, err.get("code"), err.get("message", "unexpected response"))
        headers = {k.lower(): v for k, v in resp.headers if k.lower().startswith("tsb-page-")}
        return resp.body, headers

    # -- targets --

    def list_targets(self, *, type: str | None = None, tags: tuple[str, ...] = (), group: str | None = None,
                     discovered_by: str | None = None, limit: int | None = None,
                     cursor: str | None = None) -> dict:
        """GET /targets: one page of the targets this principal may see, as a TargetList dict
        (`{"targets": [...], "next_cursor"?: ...}`). Every filter is an exact match on the index,
        and every tag named must be on the target. `limit` is 1..500. A parameter outside this list,
        or a value outside its range, is ApiError code `invalid_request`, as for every other call.
        The answer carries no credential, no user and no path on the broker host: a visible target
        is not a connectable one. Optional SSH login_hint describes metadata candidates only;
        missing/unknown hints are suppressed, never inferred as none. Saved does not prove material."""
        pairs: list[tuple[str, str]] = []
        if type is not None:
            pairs.append(("type", type))
        for tag in tags:
            pairs.append(("tag", tag))
        if group is not None:
            pairs.append(("group", group))
        if discovered_by is not None:
            pairs.append(("discovered_by", discovered_by))
        if limit is not None:
            pairs.append(("limit", str(limit)))
        if cursor is not None:
            pairs.append(("cursor", cursor))
        path = TARGETS_PATH
        if pairs:
            path += "?" + urllib.parse.urlencode(pairs)
        page = self._call("GET", path, expect=200)
        return {**page, "targets": [_target_record(record) for record in page["targets"]]}

    def iter_targets(self, *, type: str | None = None, tags: tuple[str, ...] = (), group: str | None = None,
                     discovered_by: str | None = None, limit: int | None = None) -> Iterator[dict]:
        """Every target the principal may see, one `TargetRecord` at a time, following `next_cursor`
        to the end. The filters are the same as `list_targets`; `cursor` is not a caller's argument,
        because the paging is this call's. Pages are not a snapshot: a target added or removed
        between two pages may be missed, or seen once. A `next_cursor` that repeats the previous one
        is a defective server — the walk stops with a ValueError rather than loop forever."""
        page = self.list_targets(type=type, tags=tags, group=group, discovered_by=discovered_by, limit=limit)
        previous: str | None = None
        while True:
            yield from page["targets"]
            nxt = page.get("next_cursor")
            if not nxt:
                return
            if nxt == previous:
                raise ValueError(f"GET /targets answered next_cursor {nxt!r} twice: a server that does not "
                                 "advance its cursor would loop forever")
            previous = nxt
            page = self.list_targets(type=type, tags=tags, group=group, discovered_by=discovered_by,
                                     limit=limit, cursor=nxt)

    def get_target(self, target_id: str) -> dict:
        """GET /targets/{id}: one TargetRecord. An absent target and one the principal may not see
        are answered alike, so ApiError code `target_not_found` never confirms that a target exists."""
        return _target_record(self._call("GET", f"/targets/{_segment(target_id)}", expect=200))

    def create_target(self, definition: dict) -> dict:
        """Create a target in the tsb source, sending exactly the editable definition."""
        return self._call("POST", TARGETS_PATH, definition, expect=201)

    def get_target_definition(self, target_id: str) -> dict:
        """Read the eight editable fields of a visible tsb target (requires target.edit)."""
        return self._call("GET", f"/targets/{_segment(target_id)}/definition", expect=200)

    def replace_target(self, target_id: str, definition: dict) -> dict:
        """Replace the editable fields; the provider preserves all other file keys."""
        return self._call("PUT", f"/targets/{_segment(target_id)}", definition, expect=200)

    def delete_target(self, target_id: str) -> None:
        """Delete a tsb target without ending its open sessions."""
        self._call("DELETE", f"/targets/{_segment(target_id)}", expect=204)

    # -- attach --

    def _attach_url(self) -> str:
        parts = urllib.parse.urlsplit(self._http.base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return urllib.parse.urlunsplit((scheme, parts.netloc, parts.path.rstrip("/") + ATTACH_PATH, "", ""))

    def _ssl(self):
        return _http.ssl_context(self._http.ca_file) if self._http.tls else None

    def attach(self, session_id: str, cols: int | None = None, rows: int | None = None) -> Attachment:
        """Mint a ticket and attach, blocking. cols/rows are the client's attach viewport (a
        rendering fact, never a target size request); omit them to declare none. The ticket goes in
        the first frame, never the URL."""
        ticket = self.ws_ticket(session_id)["ticket"]
        return Attachment.open(self._attach_url(), ticket, session_id, ssl_context=self._ssl(),
                               timeout=self.timeout, cols=cols, rows=rows)

    async def async_attach(self, session_id: str, cols: int | None = None,
                           rows: int | None = None) -> AsyncAttachment:
        """The same inside an event loop; the ticket request runs off the loop, too."""
        ticket = (await asyncio.to_thread(self.ws_ticket, session_id))["ticket"]
        return await AsyncAttachment.open(self._attach_url(), ticket, session_id, ssl_context=self._ssl(),
                                          timeout=self.timeout, cols=cols, rows=rows)


def _drop_none(body: dict) -> dict:
    return {k: v for k, v in body.items() if v is not None}


def _segment(value: str) -> str:
    """A path segment, percent-encoded, so an id can never add a segment of its own."""
    return urllib.parse.quote(value, safe="")
