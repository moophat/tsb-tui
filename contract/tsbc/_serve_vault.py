"""The vault release's operations in `python -m tsbc serve`: C7 (the vault and device operations) and C1's E4 auth
and lock operations, answered with canned data from in-memory state (E4 DEFAULT 16).

A surface builds its vault screens against this, never against a real vault: nothing here derives a key, opens a
blob or verifies a signature. A challenge is honoured if it was issued to this login, for this operation, and not
spent; part1, part2 and the signature are only checked for shape. The fixture principal `fixture-user` has an
sealed vault and holds `vault.admin`; `fixture-peer` and `fixture-viewer` have none until they mint one
(`POST /vault/salt`) and seal it (`POST /vault/init`). Every value is a synthetic constant.
"""

from __future__ import annotations

import re
import secrets
import time
from typing import Any

from ._gopath import BadPattern, match

SALT = "RklYVFVSRS1TQUxULTAwMA=="
MINTED_SALT = "RklYVFVSRS1NSU5URUQtMA=="   # "FIXTURE-MINTED-0": what a mint answers here
CHALLENGE = "RklYVFVSRS1DSEFMTEVOR0UtMDAwMDAwMDAwMDAwMDA="
NOW = "2026-10-29T08:00:00Z"
LOCKS = {"screen_lock_after": 900, "vault_lock_on_idle": True, "vault_idle_after": 1800, "vault_hard_cap": 28800,
         "activity_interval": 60}
OPERATIONS = ("device.register", "login.bind", "vault.unlock", "part2.regenerate")
CHALLENGE_TTL = 30                                      # seconds: what a challenge tells (`expires_in`) and honours
PRINCIPALS = ("fixture-user", "fixture-peer", "fixture-viewer")   # the names `POST /auth/login` accepts
B64_16 = re.compile(r"^[A-Za-z0-9+/]{21}[AQgw]==$")
SIGNATURE = re.compile(r"^[A-Za-z0-9+/]{85}[AQgw]==$")
PUBLIC_KEY = re.compile(r"^MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE[A-Za-z0-9+/]{85}[AQgw]==$")
DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$")
REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
KINDS = ("key", "password", "none")
CLASSES = ("user_unlocked", "broker_unlocked")


def _text(value: Any, longest: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= longest and not CONTROL.search(value)


def _reference(value: Any) -> bool:
    return isinstance(value, str) and REFERENCE.match(value) is not None and value != "supplied"


def _glob(pattern: str, name: str) -> bool:
    """Go's `ok, _ := path.Match(pattern, name)`: a malformed pattern matches nothing."""
    try:
        return match(pattern, name)
    except BadPattern:
        return False


def _compiles(pattern: str) -> bool:
    """Whether a `host:` pattern is well formed: the core's policy load refuses one for which `path.Match(pattern, "")`
    errs (`validate` in `rbac.go`), and a grant's selectors are held to the same."""
    try:
        match(pattern, "")
    except BadPattern:
        return False
    return True


def selector_matches(selector: str, target: dict) -> bool:
    """Whether a grant's selector (Storage §9's vocabulary) selects a target, as the core's RBAC filter reads it:
    `*` every target; `tag:X` a tag the target carries; `id:X` exactly that id; `host:pattern` Go's `path.Match`
    over the target's id and then, when it has one, its host."""
    if selector == "*":
        return True
    kind, _, value = selector.partition(":")
    if kind == "tag":
        return value in target.get("tags", ())
    if kind == "id":
        return target["id"] == value
    if kind == "host":
        return _glob(value, target["id"]) or (bool(target.get("host")) and _glob(value, target["host"]))
    return False


def _entry_ok(body: dict, *, org: bool) -> bool:
    allowed = {"reference", "kind", "login_name", "secret", "label"} | ({"unlock_class"} if org else set())
    if not set(body) <= allowed or not _reference(body.get("reference")) or body.get("kind") not in KINDS:
        return False
    if org and body.get("unlock_class") not in CLASSES:
        return False
    if "label" in body and not _text(body["label"], 128):
        return False
    if body["kind"] == "none":
        return "secret" not in body and ("login_name" not in body or _text(body["login_name"], 256))
    limit = 1024 if body["kind"] == "password" else 16384
    return _text(body.get("login_name"), 256) and isinstance(body.get("secret"), str) and \
        0 < len(body["secret"]) <= limit


class _ResolutionContext:
    def __init__(self, app: Any, profiles: dict, vault_sealed: bool, user_creds: dict,
                 grant_fault: bool, caller_grants: list[tuple[str | None, list[str]]]):
        self.app = app
        self.profiles = profiles
        self.vault_sealed = vault_sealed
        self.user_creds = user_creds
        self.grant_fault = grant_fault
        self.caller_grants = caller_grants

    def hint_for_target(self, target_id: str) -> str:
        try:
            target = self.app.targets[target_id]
            candidates: set[tuple[str, str]] = set()

            # Layer 2a: personal mapping
            # Item 1: A broken mapping is never "no saved login". Treat present mapping by is not None
            # (empty-string included): mapping with no credential entry or unsealed vault is unavailable.
            ref = self.profiles.get(target_id)
            if ref is not None:
                if ref not in self.user_creds or not self.vault_sealed:
                    return "unavailable"
                entry = self.user_creds[ref]
                if (not isinstance(entry, dict) or entry.get("kind") not in KINDS
                        or not _reference(entry.get("reference")) or entry.get("reference") != ref):
                    return "unavailable"
                candidates.add((ref, "personal"))

            # Layer 2b: org grants
            if self.grant_fault:
                return "unavailable"

            for ref, selectors in self.caller_grants:
                if any(selector_matches(sel, target) for sel in selectors):
                    if ref is None:
                        return "unavailable"
                    candidates.add((ref, "org"))

            count = len(candidates)
            return "none" if count == 0 else "saved" if count == 1 else "choice_required"
        except (AttributeError, KeyError, TypeError, ValueError):
            return "unavailable"


class VaultMixin:
    """Mixed into ServeApp. `vault_http` answers a vault-release route, or returns None for any other."""

    vault_admins = frozenset({"fixture-user"})

    def vault_init_state(self, users: tuple[str, ...]) -> None:
        self.vault_users = set(users)                      # principals with a sealed vault
        self.minted: dict[str, str] = {}                   # principal -> user_salt, minted and not sealed
        self.unlocked: set[str] = set()                    # cookies (logins) holding an unlock
        self.tied: dict[str, str] = {}                     # cookie -> the device_id its login is tied to
        self.handles: dict[str, str] = {}                  # cookie -> its login_handle (fresh randomness, never the cookie)
        self.clock = time.monotonic                        # seconds; a test replaces it to move time
        self.challenges: dict[str, tuple[str, str, str, float]] = {}   # id -> (cookie, operation, device_id, expiry)
        self.devices: dict[tuple[str, str], dict] = {}     # (user, device_id) -> Device
        self.creds: dict[str, dict[str, dict]] = {}        # user -> reference -> stored entry
        self.org_creds: dict[str, dict] = {}
        self.grants: dict[str, dict] = {}
        self.profiles: dict[str, dict[str, str]] = {}      # user -> target -> reference
        self.activity: dict[str, float] = {}               # cookie -> when its last counted signal came

    @staticmethod
    def vault_locks() -> dict:
        return dict(LOCKS)

    # -- helpers --

    def _cookie(self, headers: dict) -> str | None:
        for part in headers.get("cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "tsb_session":
                return value
        return None

    def _challenge(self, cookie: str, user: str, cid: Any, operation: str) -> str | None:
        """The device a challenge named, when it was issued to this login for this operation, is answered within its
        `expires_in` and that device is still trusted (a revocation or a wipe since the issue refuses it); consumed
        either way. A registration names a device not yet on the allowlist, so it is the one operation that skips
        the trust check."""
        found = self.challenges.pop(cid, None) if isinstance(cid, str) else None
        if found is None or found[0] != cookie or found[1] != operation or self.clock() > found[3]:
            return None
        if operation != "device.register" and not self._trusted(user, found[2]):
            return None
        return found[2]

    def _end_logins(self, user: str, device_id: str) -> None:
        """The login-ending function of a revocation under the default policy: every login of `user` tied to
        `device_id` ends (cookie void, unlock purged, tie dropped), its unredeemed tickets are void and its attached
        clients close with 4015, as do the device logins it created that are still in progress (`_dl_login_ended`;
        a session a device login opened is one of its clients). A login of another principal tied to a device with
        the same id is not touched: the devices bucket is keyed by user and device. (The stub holds no alert stream open: an answer to
        `GET /alerts` is one finite body, so a revoked login has none to end.)"""
        ended = {c for c, d in self.tied.items() if d == device_id and self.logins.get(c) == user}
        for c in ended:
            self.logins.pop(c, None)
            self.tied.pop(c, None)
            self.handles.pop(c, None)
            self.activity.pop(c, None)
            self.unlocked.discard(c)
            self._dl_login_ended(c, revoked=True)     # its device logins in progress close 4015, with no result
        for ticket, c in list(self.ticket_logins.items()):
            if c in ended:
                del self.ticket_logins[ticket]
                self.tickets.pop(ticket, None)
        for client_id, c in list(self.client_logins.items()):
            if c in ended:
                del self.client_logins[client_id]
                sid = self.sid_of(client_id)
                if sid is not None:
                    self._drop_client(self.sessions[sid], client_id, code=4015, reason="device_revoked")

    def _trusted(self, user: str, device_id: str) -> bool:
        dev = self.devices.get((user, device_id))
        return dev is not None and "revoked_at" not in dev

    def _admin_target(self, user: str, query: dict) -> tuple[str, bool]:
        """(whose devices, allowed)."""
        who = (query.get("user") or [user])[0]
        return who, who == user or user in self.vault_admins

    # -- routes --

    def vault_http(self, method: str, path: str, headers: dict, body: Any, query: dict, user: str):
        cookie = self._cookie(headers) or ""
        parts = path.strip("/").split("/")
        route = (method, path)
        if route == ("GET", "/vault/salt"):
            if user in self.vault_users:
                return 200, [], {"initialized": True, "user_salt": SALT}
            if user in self.minted:
                return 200, [], {"initialized": False, "user_salt": self.minted[user]}
            return 200, [], {"initialized": False}
        if route == ("POST", "/vault/salt"):
            if user in self.vault_users:
                return self._err(409, "vault_exists", "the vault is set up already")
            return 200, [], {"initialized": False, "user_salt": self.minted.setdefault(user, MINTED_SALT)}
        if route == ("POST", "/vault/init"):
            if not isinstance(body, dict) or set(body) != {"part1", "part2"} or \
                    not (B64_16.match(str(body["part1"])) and B64_16.match(str(body["part2"]))):
                return self._err(422, "invalid_request", "the body is not an init request")
            if user in self.vault_users:
                return self._err(409, "vault_exists", "the vault is set up already")
            if user not in self.minted:
                return self._err(409, "vault_not_minted", "mint the vault's salt first")
            del self.minted[user]
            self.vault_users.add(user)
            self.unlocked.add(cookie)
            return 204, [], None
        if route == ("POST", "/vault/unlock"):
            return self._unlock(cookie, user, body)
        if route == ("POST", "/vault/part2"):
            if not isinstance(body, dict) or set(body) != {"challenge_id", "part1", "part2", "signature"} or \
                    not (B64_16.match(str(body["part1"])) and B64_16.match(str(body["part2"]))
                         and SIGNATURE.match(str(body["signature"]))):
                return self._err(422, "invalid_request", "the body is not a regeneration request")
            device = self._challenge(cookie, user, body["challenge_id"], "part2.regenerate")
            if device is None:
                return self._err(409, "challenge_failed", "the challenge was refused")
            self.unlocked.add(cookie)
            return 204, [], None
        if route == ("POST", "/devices/challenges"):
            return self._issue(cookie, user, body)
        if route == ("GET", "/devices"):
            who, allowed = self._admin_target(user, query)
            if not allowed:
                return self._err(403, "forbidden", "vault administration only")
            return 200, [], {"devices": [d for (u, _), d in sorted(self.devices.items()) if u == who]}
        if route == ("POST", "/devices"):
            return self._register(cookie, user, body)
        if len(parts) == 2 and parts[0] == "devices" and method == "DELETE":
            who, allowed = self._admin_target(user, query)
            dev = self.devices.get((who, parts[1])) if allowed else None
            if dev is None:
                return self._err(404, "device_not_found", "no such device")
            dev["revoked_at"], dev["revoke_reason"] = NOW, "user" if who == user else "admin"
            self._end_logins(who, parts[1])
            return 204, [], None
        if len(parts) == 3 and parts[:2] == ["vault", "users"] and method == "DELETE":
            if user not in self.vault_admins:
                return self._err(403, "forbidden", "vault administration only")
            if parts[2] not in PRINCIPALS:
                return self._err(404, "user_not_found", "no such user")
            blocking = sorted(ref for ref, e in self.org_creds.items()
                              if e.get("unlock_class") == "user_unlocked" and e.get("stored_by") == parts[2])
            if blocking:
                status, headers, reply = self._err(409, "vault_wipe_blocked", "the user stored user-unlocked org credentials")
                reply["error"]["credential_refs"] = blocking
                return status, headers, reply
            self.vault_users.discard(parts[2])
            self.minted.pop(parts[2], None)
            self.creds.pop(parts[2], None)
            self.profiles.pop(parts[2], None)
            for (u, device_id), dev in list(self.devices.items()):
                if u == parts[2] and "revoked_at" not in dev:
                    dev["revoked_at"], dev["revoke_reason"] = NOW, "admin"
                    self._end_logins(u, device_id)
            for c, who in list(self.logins.items()):
                if who == parts[2]:
                    self.unlocked.discard(c)
            return 204, [], None
        if path == "/vault/credentials" or (len(parts) == 3 and parts[:2] == ["vault", "credentials"]):
            return self._personal(method, parts, cookie, user, body)
        if path == "/vault/org-credentials" or (len(parts) == 3 and parts[:2] == ["vault", "org-credentials"]):
            if user not in self.vault_admins:
                return self._err(403, "forbidden", "vault administration only")
            return self._org(method, parts, cookie, user, body)
        if path == "/vault/grants" or (len(parts) == 3 and parts[:2] == ["vault", "grants"]):
            if user not in self.vault_admins:
                return self._err(403, "forbidden", "vault administration only")
            return self._grant(method, parts, user, body)
        if route == ("POST", "/profiles/batch"):
            maps = body.get("mappings") if isinstance(body, dict) and set(body) == {"mappings"} else None
            if not isinstance(maps, list) or not maps or any(
                    not isinstance(m, dict) or set(m) != {"target_id", "credential_ref"}
                    or not isinstance(m["target_id"], str)
                    or not (m["credential_ref"] is None or isinstance(m["credential_ref"], str)) for m in maps):
                return self._err(422, "invalid_request", "the body is not a mapping request")
            own = self.creds.get(user, {})
            targets = [m["target_id"] for m in maps]
            if len(set(targets)) != len(targets) or any(t not in self.targets for t in targets) or any(
                    m["credential_ref"] is not None and m["credential_ref"] not in own for m in maps):
                return self._err(422, "invalid_request", "a target or a reference is not yours to map")
            mine = self.profiles.setdefault(user, {})
            for m in maps:
                if m["credential_ref"] is None:
                    mine.pop(m["target_id"], None)
                else:
                    mine[m["target_id"]] = m["credential_ref"]
            return 204, [], None
        if route == ("POST", "/auth/bind"):
            if not isinstance(body, dict) or set(body) != {"challenge_id", "signature"} or \
                    not SIGNATURE.match(str(body["signature"])):
                return self._err(422, "invalid_request", "the body is not a bind request")
            device = self._challenge(cookie, user, body["challenge_id"], "login.bind")
            if device is None or cookie in self.tied:
                return self._err(409, "challenge_failed", "the challenge was refused")
            self.tied[cookie] = device
            return 204, [], None
        if route == ("POST", "/auth/activity"):
            now, last = self.clock(), self.activity.get(cookie)
            if last is None or now - last >= self.vault_locks()["activity_interval"]:
                self.activity[cookie] = now      # one entry per login: the rest of the interval's signals are ignored
            return 204, [], None
        if route == ("POST", "/auth/lock"):
            for c, who in list(self.logins.items()):
                if who == user:
                    self.unlocked.discard(c)
            return 204, [], None
        if route == ("POST", "/auth/screen-unlock"):
            if not isinstance(body, dict) or set(body) != {"password"} or not isinstance(body["password"], str) \
                    or not body["password"]:
                return self._err(422, "invalid_request", "a password is required")
            if body["password"] != self.screen_password:
                return self._err(403, "screen_unlock_failed", "that password is not right")
            return 204, [], None
        return None

    def vault_candidates(self, user: str, target_id: str) -> set[tuple[str, str]]:
        """The (reference, layer) candidates for a principal and a target (session-open step 3): the principal's own
        Layer 2a mapping for the target when their vault holds it (`personal`), and each org credential a grant to
        the principal names whose selectors match the target (`org`; two grants of one reference are one)."""
        target = self.targets[target_id]
        found = set()
        ref = self.profiles.get(user, {}).get(target_id)
        if ref is not None and ref in self.creds.get(user, {}):
            found.add((ref, "personal"))
        for grant in self.grants.values():
            if not isinstance(grant, dict) or grant.get("user") != user:
                continue
            if grant.get("credential_ref") in self.org_creds and isinstance(grant.get("selectors"), list) and \
                    any(selector_matches(sel, target) for sel in grant["selectors"] if isinstance(sel, str)):
                found.add((grant["credential_ref"], "org"))
        return found

    def vault_prepare_resolution(self, user: str) -> _ResolutionContext:
        """Prepare metadata resolution context once per inventory request (inventory-list One request context)."""
        profiles = dict(self.profiles.get(user, {}))
        vault_sealed = user in self.vault_users
        user_creds = dict(self.creds.get(user, {}))

        # Scan grants once, retaining only the caller's (another user's broken grant changes nothing)
        grant_fault = False
        caller_grants: list[tuple[str | None, list[str]]] = []
        for grant in self.grants.values():
            if not isinstance(grant, dict) or grant.get("user") != user:
                continue
            selectors = grant.get("selectors")
            if not isinstance(selectors, list) or not selectors or any(
                    not isinstance(sel, str) or not re.fullmatch(r"(\*|tag:.+|host:.+|id:.+)", sel)
                    or (sel.startswith("host:") and not _compiles(sel[5:])) for sel in selectors):
                grant_fault = True
                continue
            ref = grant.get("credential_ref")
            if not isinstance(ref, str) or ref not in self.org_creds:
                caller_grants.append((None, selectors))
                continue
            org_entry = self.org_creds[ref]
            # Item 2: An org candidate missing its required metadata (its unlock class, or any field
            # the vault schema requires) makes the hint unavailable.
            if not isinstance(org_entry, dict) or org_entry.get("kind") not in KINDS or \
                    org_entry.get("unlock_class") not in CLASSES or not _reference(org_entry.get("reference")) or \
                    org_entry.get("reference") != ref:
                caller_grants.append((None, selectors))
                continue
            caller_grants.append((ref, selectors))

        return _ResolutionContext(self, profiles, vault_sealed, user_creds, grant_fault, caller_grants)

    def vault_login_hint(self, user: str, target_id: str, context: _ResolutionContext | None = None) -> str:
        """Metadata only, sharing create's candidates; no unlock/material checks.

        The fixture app's vault_users is its structural sealed-record marker. Corrupt metadata
        produces the fixed unavailable value, without changing create's existing refusals.
        """
        if context is None:
            context = self.vault_prepare_resolution(user)
        return context.hint_for_target(target_id)

    def vault_lock_refusal(self, user: str, cookie: str, credential: tuple[str, str] | None):
        """session-open step 3, before any worker: the unlock a new connection's credential needs. A personal
        credential with a secret needs this login's: without it, 409 `vault_locked`; one with no secret needs none,
        as does a request no stored credential resolves for. An org credential behaves as its class says (DEFAULT 53):
        `broker_unlocked` needs none; `user_unlocked` needs its storing administrator's, held by any live login of that
        administrator, and the caller's own unlock never matters for it (credential-protection §8.2, §5.5): without
        it, 409 `org_credential_locked` (ruling 2026-10-30 00:58)."""
        if credential is None:
            return None
        ref, layer = credential
        if layer == "personal":
            if cookie not in self.unlocked and self.creds.get(user, {}).get(ref, {}).get("kind") != "none":
                return self._err(409, "vault_locked", "unlock the vault, then try again")
            return None
        org = self.org_creds.get(ref, {})
        if org.get("unlock_class") == "user_unlocked" and \
                not any(self.logins.get(c) == org.get("stored_by") for c in self.unlocked):
            return self._err(409, "org_credential_locked", "the credential's administrator holds no unlock")
        return None

    def vault_create_refusal(self, user: str, body: Any):
        """session-open step 3's Layer 2a check: a mapping whose reference is not in the index is 409, naming it."""
        if not isinstance(body, dict) or "login" in body:
            return None
        ref = self.profiles.get(user, {}).get(body.get("target_id"))
        if ref is not None and ref not in self.creds.get(user, {}):
            status, headers, reply = self._err(409, "mapped_credential_missing",
                                               "a mapping names a credential that is not in your vault")
            reply["error"]["credential_ref"] = ref
            return status, headers, reply
        return None

    def _unlock(self, cookie: str, user: str, body: Any):
        if not isinstance(body, dict):
            return self._err(422, "invalid_request", "the body is not an unlock request")
        if body.get("mode") == "untrusted" and set(body) == {"mode", "part1", "part2"} and \
                B64_16.match(str(body["part1"])) and B64_16.match(str(body["part2"])):
            if user not in self.vault_users:
                return self._err(409, "vault_not_initialized", "set up the vault first")
            self.unlocked.add(cookie)
            return 204, [], None
        if body.get("mode") == "trusted" and set(body) == {"mode", "challenge_id", "part1", "signature"} and \
                B64_16.match(str(body["part1"])) and SIGNATURE.match(str(body["signature"])):
            device = self._challenge(cookie, user, body["challenge_id"], "vault.unlock")
            if device is None:
                return self._err(409, "challenge_failed", "the challenge was refused")
            self.unlocked.add(cookie)
            return 204, [], None
        return self._err(422, "invalid_request", "the body is not an unlock request")

    def _issue(self, cookie: str, user: str, body: Any):
        if not isinstance(body, dict) or set(body) != {"operation", "device_id"} or \
                body["operation"] not in OPERATIONS or not DEVICE_ID.match(str(body["device_id"])):
            return self._err(422, "invalid_request", "the body is not a challenge request")
        op, device = body["operation"], body["device_id"]
        if op != "device.register" and not self._trusted(user, device):
            return self._err(403, "device_not_trusted", "this device is not trusted")
        tied = self.tied.get(cookie)
        if (op in ("vault.unlock", "part2.regenerate") and tied != device) or (op == "login.bind" and tied) or \
                (op == "device.register" and tied and tied != device):
            return self._err(409, "challenge_failed", "the challenge was refused")
        mine = [c for c, v in self.challenges.items() if v[0] == cookie]
        if len(mine) >= 4:
            self.challenges.pop(mine[0])   # a fifth voids the oldest (E4 DEFAULT 5)
        cid = "ch-" + secrets.token_hex(6)
        self.challenges[cid] = (cookie, op, device, self.clock() + CHALLENGE_TTL)
        return 200, [], {"challenge_id": cid, "challenge": CHALLENGE, "login_handle": self.handles.setdefault(cookie, "lh-" + secrets.token_hex(8)),
                         "expires_in": CHALLENGE_TTL}

    def _register(self, cookie: str, user: str, body: Any):
        keys = {"challenge_id", "part1", "part2", "public_key", "signature", "device_name"}
        if not isinstance(body, dict) or set(body) != keys or not B64_16.match(str(body["part1"])) or \
                not B64_16.match(str(body["part2"])) or not PUBLIC_KEY.match(str(body["public_key"])) or \
                not SIGNATURE.match(str(body["signature"])) or not _text(body["device_name"], 64):
            return self._err(422, "invalid_request", "the body is not a registration request")
        device = self._challenge(cookie, user, body["challenge_id"], "device.register")
        if device is None:
            return self._err(409, "challenge_failed", "the challenge was refused")
        if user not in self.vault_users:
            return self._err(409, "vault_not_initialized", "set up the vault first")
        record = {"device_id": device, "device_name": body["device_name"], "registered_at": NOW, "last_used_at": NOW}
        self.devices[(user, device)] = record
        self.tied.setdefault(cookie, device)
        self.unlocked.add(cookie)
        return 201, [], dict(record)

    def _personal(self, method: str, parts: list[str], cookie: str, user: str, body: Any):
        mine = self.creds.setdefault(user, {})
        if method == "GET" and len(parts) == 2:
            unlocked = cookie in self.unlocked
            return 200, [], {"credentials": [
                {k: v for k, v in e.items() if k in ("reference", "kind") or (k == "label" and unlocked)}
                for _, e in sorted(mine.items())]}
        if method == "POST" and len(parts) == 2:
            if not isinstance(body, dict) or not _entry_ok(body, org=False):
                return self._err(422, "invalid_request", "the body is not a store request")
            if user not in self.vault_users:
                return self._err(409, "vault_not_initialized", "set up the vault first")
            if cookie not in self.unlocked:
                return self._err(409, "vault_locked", "unlock the vault first")
            if body["reference"] in mine:
                return self._err(409, "credential_exists", "that reference is in use")
            mine[body["reference"]] = dict(body)
            return 201, [], {"reference": body["reference"], "kind": body["kind"]}
        if method == "DELETE" and len(parts) == 3:
            if parts[2] not in mine:
                return self._err(404, "credential_not_found", "no such credential")
            if cookie not in self.unlocked:
                return self._err(409, "vault_locked", "unlock the vault first")
            del mine[parts[2]]
            for target, ref in list(self.profiles.get(user, {}).items()):
                if ref == parts[2]:
                    del self.profiles[user][target]
            return 204, [], None
        return self._err(404, "credential_not_found", "no route")

    def _org(self, method: str, parts: list[str], cookie: str, user: str, body: Any):
        if method == "GET" and len(parts) == 2:
            return 200, [], {"credentials": [
                {k: e[k] for k in ("reference", "unlock_class", "kind", "label", "stored_by") if k in e}
                for _, e in sorted(self.org_creds.items())]}
        if method == "POST" and len(parts) == 2:
            if not isinstance(body, dict) or not _entry_ok(body, org=True):
                return self._err(422, "invalid_request", "the body is not an org store request")
            if body["unlock_class"] == "user_unlocked" and cookie not in self.unlocked:
                return self._err(409, "vault_locked", "unlock the vault first")
            if body["reference"] in self.org_creds:
                return self._err(409, "credential_exists", "that reference is in use")
            self.org_creds[body["reference"]] = dict(body, stored_by=user)
            return 201, [], {k: body[k] for k in ("reference", "unlock_class", "kind")}
        if method == "DELETE" and len(parts) == 3:
            if parts[2] not in self.org_creds:
                return self._err(404, "credential_not_found", "no such credential")
            if any(g["credential_ref"] == parts[2] for g in self.grants.values()):
                return self._err(409, "credential_has_grants", "revoke its grants first")
            del self.org_creds[parts[2]]
            return 204, [], None
        return self._err(404, "credential_not_found", "no route")

    def _grant(self, method: str, parts: list[str], user: str, body: Any):
        def selectors_ok(sel):
            return isinstance(sel, list) and 0 < len(sel) <= 64 and all(
                isinstance(s, str) and re.match(r"^(\*|tag:.+|host:.+|id:.+)$", s)
                and (not s.startswith("host:") or _compiles(s[len("host:"):])) for s in sel)
        if method == "POST" and len(parts) == 2:
            if not isinstance(body, dict) or set(body) != {"user", "selectors", "credential_ref"} or \
                    not selectors_ok(body["selectors"]) or not isinstance(body["credential_ref"], str) or \
                    body["credential_ref"] not in self.org_creds or \
                    not isinstance(body["user"], str) or body["user"] not in PRINCIPALS:
                return self._err(422, "invalid_request", "the body is not a grant request")
            gid = "g-" + secrets.token_hex(4)
            self.grants[gid] = {"grant_id": gid, "user": body["user"], "selectors": list(body["selectors"]),
                                "credential_ref": body["credential_ref"], "granted_by": user, "granted_at": NOW}
            return 201, [], dict(self.grants[gid])
        if method not in ("POST", "DELETE"):
            return self._err(404, "grant_not_found", "no route")   # as /vault/credentials answers an undeclared method
        grant = self.grants.get(parts[2]) if len(parts) == 3 else None
        if grant is None:
            return self._err(404, "grant_not_found", "no such grant")
        if method == "POST":
            if not isinstance(body, dict) or set(body) != {"selectors"} or not selectors_ok(body["selectors"]):
                return self._err(422, "invalid_request", "the body is not a scope request")
            grant["selectors"] = list(body["selectors"])
            return 200, [], dict(grant)
        del self.grants[parts[2]]
        return 204, [], None
