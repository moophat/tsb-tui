"""In-memory target-import behavior for the local fixture server."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import Counter
from datetime import datetime, timezone

from . import _import_sources
from ._serve_vault import selector_matches


def _b32(data: bytes) -> str:
    return base64.b32encode(data).decode("ascii").lower().rstrip("=")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


# Per-format option allowlists from the mapping tables: the documented fields carried as inert
# inventory options. Anything outside is reported as a loss, never copied, and never discards the row.
CARRIED_OPTIONS = {
    ("tabby", "ssh"): {"auth", "x11", "skipBanner", "agentForward", "algorithms", "forwardedPorts",
                       "keepaliveInterval", "keepaliveCountMax"},
    ("tabby", "telnet"): {"inputMode", "inputNewlines", "outputMode", "outputNewlines", "input"},
    ("securecrt", "ssh"): {"keepaliveInterval", "x11", "agentForward"},
    ("mobaxterm", "ssh"): {"x11", "agentForward"},
    ("yaml", "ssh"): {"auth", "x11", "skipBanner", "agentForward", "algorithms", "forwardedPorts",
                      "keepaliveInterval", "keepaliveCountMax", "connection_linger", "keepaliveProbeInterval",
                      "orphan_timeout", "idle", "unattended", "expiry_notice", "quiet_prompt", "open_timeout",
                      "login_timeout", "size_floor", "size_ceiling"},
    ("yaml", "telnet"): {"inputMode", "inputNewlines", "outputMode", "outputNewlines", "input", "connection_linger"},
}
# Tabby fields named in the mapping table that have no TSB field: reported by name, not carried.
TABBY_DROP = {"readyTimeout", "jumpHost"}
# Bounds for the mapped fields that the inventory schema constrains. Every carried field obeys its
# existing schema bound (protocol §Limits): the open option keys the schema does not type have no
# bound to break, so only the typed ones are checked here.
def _seconds(v, minimum=1):
    return type(v) is int and minimum <= v <= 9007199254740


def _size(v, *, ceiling=False):
    if not isinstance(v, dict):
        return False
    if ceiling and v.get("cols") == 0 and v.get("rows") == 0:
        return True  # {0, 0} turns the ceiling off; one side 0 alone is refused
    return all(type(v.get(k)) is int and 1 <= v[k] <= 65535 for k in ("cols", "rows"))


def _has_secret(val) -> bool:
    if isinstance(val, dict):
        for k, v in val.items():
            if k in ("password", "privateKey", "privateKeys", "credential_ref") or _has_secret(v):
                return True
    elif isinstance(val, list):
        for item in val:
            if _has_secret(item):
                return True
    return False


OPTION_BOUNDS = {
    "host": lambda v: isinstance(v, str) and 1 <= len(v) <= 253,
    "port": lambda v: type(v) is int and 1 <= v <= 65535,
    "user": lambda v: isinstance(v, str) and len(v) <= 256,
    "auth": lambda v: v is None or (isinstance(v, str) and 1 <= len(v) <= 64),
    "x11": lambda v: type(v) is bool,
    "skipBanner": lambda v: type(v) is bool,
    "agentForward": lambda v: type(v) is bool,
    "algorithms": lambda v: isinstance(v, dict) and all(
        isinstance(k, str) and isinstance(algs, list) and all(isinstance(a, str) for a in algs)
        for k, algs in v.items()
    ),
    "forwardedPorts": lambda v: isinstance(v, list),
    "inputMode": lambda v: isinstance(v, str),
    "inputNewlines": lambda v: isinstance(v, str),
    "outputMode": lambda v: isinstance(v, str),
    "outputNewlines": lambda v: isinstance(v, str),
    "input": lambda v: isinstance(v, dict),
    "keepaliveInterval": lambda v: type(v) is int and 0 <= v <= 9007199254740,
    "keepaliveCountMax": lambda v: type(v) is int and 1 <= v <= 9007199254740991,
    "connection_linger": lambda v: _seconds(v, 0),
    "keepaliveProbeInterval": _seconds,
    "orphan_timeout": _seconds,
    "idle": _seconds,
    "unattended": _seconds,
    "expiry_notice": _seconds,
    "quiet_prompt": _seconds,
    "open_timeout": _seconds,
    "login_timeout": lambda v: type(v) is int and 10 <= v <= 1800,
    "size_floor": _size,
    "size_ceiling": lambda v: _size(v, ceiling=True),
}


def _canonical(value):
    """JCS for this plan's strings, integers, booleans, arrays and objects."""
    if isinstance(value, dict):
        keys = sorted(value, key=lambda key: key.encode("utf-16be"))
        return "{" + ",".join(_canonical(key) + ":" + _canonical(value[key]) for key in keys) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    if value is None or isinstance(value, (str, bool)) or type(value) is int:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    raise ValueError("unsupported canonical value")


SHARE_ENTRY_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._@-]{0,127}|[a-z][a-z0-9_]{0,31}:[A-Za-z0-9][A-Za-z0-9._@-]{0,127})$"
)


class ImportMixin:
    """In-memory fixture vault; sealing and durable recovery are provider responsibilities."""

    def import_init(self):
        self.import_receipts = {}
        self.import_receipt_shares = {}
        self.import_refs = {}
        self.import_definitions = {}
        self.import_results = {}
        self.import_aborted = set()
        self.target_edit_filters = {}
        self.audit_events = []

    def _import_error(self, status, code):
        status, headers, body = self._err(status, code, "target import request refused")
        return status, headers + [("Cache-Control", "no-store")], body

    def _import_conflict(self, import_id, receipt):
        status, headers, body = self._import_error(409, "import_conflict")
        stored = self.import_results[(receipt["user"], import_id)]["credentials"]["stored"]
        body["error"]["import_progress"] = {"import_id": import_id, "credentials_stored": stored,
            "targets_written": [row["target_id"] for row in receipt["plan"]] if receipt["stage"] != "committed" else [], "mapping": "not_requested",
            "audit": "unknown", "index": "unknown", "effects_known": False}
        return status, headers, body

    def _import_token(self, payload):
        encoded = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        return encoded + self._import_token_mac(encoded)

    def _import_token_mac(self, encoded):
        label = b"tsb-import-token"
        message = len(label).to_bytes(4, "big") + label + encoded.encode()
        return _b64(hmac.digest(self.id_key, message, "sha256"))

    def _import_read_token(self, token, user):
        try:
            encoded, signature = token[:-43], token[-43:]
            expected = self._import_token_mac(encoded)
            if not hmac.compare_digest(signature, expected):
                return None
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if payload["user"] != user or payload["expires"] < time.time() or (user, payload["import_id"]) in self.import_aborted:
                return None
            return payload
        except (AttributeError, ValueError, KeyError, TypeError, binascii.Error):
            return None

    def _import_id_for(self, import_id, source_ref, n):
        parts = (b"tsb-import-target-id", import_id.encode(), source_ref.encode(), n.to_bytes(8, "big"))
        message = b"".join(len(part).to_bytes(4, "big") + part for part in parts)
        return "g-" + _b32(hmac.digest(self.id_key, message, "sha256"))[:26]

    def _import_shared_allowed(self, user, targets):
        filters = self.target_edit_filters.get(user, ["*"])
        for row in targets:
            target = row["target"]
            if not any(selector_matches(selector, target) for selector in filters):
                return False
            if row["change"] == "update":
                existing = self.targets.get(target["id"])
                if (existing is None or not self._target_visible(user, target["id"])
                        or not any(selector_matches(selector, existing) for selector in filters)):
                    return False
        return True

    def _map_options(self, format_name, kind, row, locator, issues, options_report):
        """Map the format's documented options into inventory fields; report every other option as a
        loss without discarding the row. Returns the mapped options, or None if host/port/user is outside
        its bounds (the whole entry is then invalid)."""
        options = row.get("options")
        if not isinstance(options, dict):
            issues.append({"locator": locator, "kind": "row", "reason": "invalid_entry"})
            return None
        carried = CARRIED_OPTIONS.get((format_name, kind), set())
        mapped = {}
        for position, (key, value) in enumerate(options.items(), 1):
            if key in ("host", "port", "user"):
                ok = key == "user" and kind == "ssh" and OPTION_BOUNDS["user"](value)
                ok = ok or (key != "user" and OPTION_BOUNDS.get(key, lambda v: True)(value))
                if not ok:
                    issues.append({"locator": locator, "kind": "row", "reason": "invalid_entry"})
                    return None
                mapped[key] = value
            elif key in carried:
                if format_name == "tabby" and key == "keepaliveInterval":
                    if type(value) is not int or value < 0:
                        issues.append({"locator": locator, "kind": "row", "reason": "invalid_entry"})
                        return None
                    value = (value + 999) // 1000  # ceil(milliseconds / 1000), 0 -> 0
                if _has_secret(value) or (key in OPTION_BOUNDS and not OPTION_BOUNDS[key](value)):
                    issues.append({"locator": locator, "kind": "row", "reason": "invalid_entry"})
                    return None
                mapped[key] = value
            elif format_name == "tabby" and key in TABBY_DROP:
                issues.append({"locator": locator, "kind": "option", "reason": "unsupported_option",
                               "fields": [key], "position": position})
            else:
                issues.append({"locator": locator, "kind": "option", "reason": "unsupported_option",
                               "position": position})
        for name, pos in options_report:
            entry = {"locator": locator, "kind": "option", "reason": "unsupported_option", "position": pos}
            if name:
                entry["fields"] = [name]
            issues.append(entry)
        return mapped

    def _import_plan(self, user, share, import_id, rows, format_name):
        if len(rows) > 5000:
            raise ValueError("too many entries")
        ref_counts = Counter(
            row["id"] for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        )
        planned, issues, assigned, credentials, credential_keys, secrets_by_ref = [], [], set(), [], {}, {}
        for index, row in enumerate(rows, 1):
            locator = {"index": index}
            key_file = isinstance(row, dict) and bool(row.get("_key_file"))
            unreadable_key = isinstance(row, dict) and bool(row.get("_unreadable_key"))
            invalid_row = isinstance(row, dict) and bool(row.get("_invalid_row"))
            secret = row.get("_password") if isinstance(row, dict) else None
            options_report = row.get("_options_report", []) if isinstance(row, dict) else []
            if isinstance(row, dict):
                row = {key: value for key, value in row.items() if key not in ("_key_file", "_unreadable_key", "_password", "_options_report", "_invalid_row")}
            if key_file:
                issues.append({"locator": locator, "kind": "credential", "reason": "key_file_reference"})
            if unreadable_key:
                issues.append({"locator": locator, "kind": "credential", "reason": "unreadable_credential"})
            if invalid_row:
                # The reader marked a malformed numeric property; it is this row's failure and the
                # rest of the export proceeds (design §3).
                issues.append({"locator": locator, "kind": "row", "reason": "invalid_entry"})
                continue
            if (isinstance(row, dict) and isinstance(row.get("name"), str) and 1 <= len(row["name"]) <= 256
                    and not re.search(r"[\x00-\x1f\x7f-\x9f]", row["name"])):
                locator["name"] = row["name"]
            ref = row.get("id") if isinstance(row, dict) else None
            if not isinstance(ref, str) or not 1 <= len(ref) <= 512 or re.search(r"[\x00-\x1f\x7f-\x9f]", ref):
                issues.append({"locator": locator, "kind": "row", "reason": "missing_source_ref"})
                continue
            if ref_counts[ref] != 1:
                issues.append({"locator": locator, "kind": "row", "reason": "ambiguous_source_ref"})
                continue
            kind = row.get("type")
            if kind not in ("ssh", "telnet"):
                issues.append({"locator": locator, "kind": "row", "reason": "local_serial" if kind == "serial" else "unsupported_type"})
                continue
            # Top-level keys outside the target shape are a whole-row loss; options are mapped per format.
            allowed = {"id", "type", "name", "group", "tags", "vendor", "prompt_pattern", "options"}
            if set(row) - allowed:
                issues.append({"locator": locator, "kind": "row", "reason": "invalid_entry"})
                continue
            options = self._map_options(format_name, kind, row, locator, issues, options_report)
            if options is None:
                continue
            key = (format_name, "private" if share is not None else "shared", user if share is not None else "", ref)
            existing = self.import_refs.get(key)
            if existing not in self.targets:
                existing = None
            n = 0
            target_id = existing
            if target_id is None:
                while True:
                    target_id = self._import_id_for(import_id, ref, n)
                    if target_id not in self.targets and target_id not in assigned:
                        break
                    n += 1
            assigned.add(target_id)
            # Only host/port/user go into the public definition body; the carried inert options belong
            # in the nested options, which _valid_definition does not check.
            core = {k: v for k, v in options.items() if k in ("host", "port", "user")}
            flat = {"id": target_id, "type": kind, **core}
            flat.update({k: row[k] for k in ("name", "group", "tags", "vendor", "prompt_pattern") if k in row})
            if share is not None:
                flat["share"] = list(share)
            if existing is not None:
                # Carry tags, vendor and own prompt from the target's current state, not the last
                # import's snapshot: a PUT edit updates target_definitions, and a re-import must keep it.
                current = self.target_definitions[existing]
                for field in ("tags", "vendor"):
                    flat.pop(field, None)
                    if field in current:
                        flat[field] = current[field]
                if "prompt_pattern" not in row and "prompt_pattern" in current:
                    flat["prompt_pattern"] = current["prompt_pattern"]
            if not self._valid_definition(flat):
                issues.append({"locator": locator, "kind": "row", "reason": "invalid_entry"})
                continue
            reference = None
            if secret is not None:
                login = options.get("user")
                if (kind != "ssh" or not isinstance(secret, str) or not 1 <= len(secret) <= 1024
                        or not isinstance(login, str) or not 1 <= len(login) <= 256
                        or re.search(r"[\x00-\x1f\x7f-\x9f]", login)):
                    issues.append({"locator": locator, "kind": "credential", "reason": "unreadable_credential"})
                else:
                    identity = ("password", login, secret)
                    reference = credential_keys.get(identity)
                    if reference is None:
                        reference = "import-" + import_id[4:12] + "-" + str(len(credentials) + 1)
                        credential_keys[identity] = reference
                        secrets_by_ref[reference] = secret
                        credentials.append({"reference": reference, "kind": "password", "login_name": login, "target_ids": []})
                    next(item for item in credentials if item["reference"] == reference)["target_ids"].append(target_id)
            retained_options = {}
            if existing is not None:
                # Unmapped options and import_ref are import-only keys: read them from the snapshot.
                # Format-owned mapped options take export values; omission removes them (E3B-CONTRACT-DESIGN line 216).
                format_mapped = {"host", "port", "user"} | CARRIED_OPTIONS.get((format_name, kind), set())
                retained_options = {key: value for key, value in self.import_definitions[existing].get("options", {}).items()
                                    if key not in format_mapped and not _has_secret(value)}
            definition = {"id": target_id, "type": kind, "discovered_by": "tsb", "options": {**retained_options, **options},
                          "import_ref": {"format": format_name, "source_ref": ref}}
            definition.update({k: flat[k] for k in ("name", "group", "tags", "vendor", "prompt_pattern") if k in flat})
            if share is not None:
                definition["share"] = list(share)
            target = {k: v for k, v in flat.items() if k != "user"}
            target.update({"discovered_by": "tsb"})
            target.setdefault("tags", [])
            target.setdefault("port", 22 if kind == "ssh" else 23)
            if existing is not None and "last_seen" in self.targets[existing]:
                target["last_seen"] = self.targets[existing]["last_seen"]
                definition["last_seen"] = self.targets[existing]["last_seen"]
            if share is not None:
                source = "private-" + hashlib.sha256(user.encode()).hexdigest()[:32]
                definition["discovered_by"] = source
                target["discovered_by"] = source
                target["share"] = list(share)
                target["owner"] = user
            mapping = None if reference is None else {"reference": reference,
                "state": "keep" if target_id in self.profiles.get(user, {}) else "map"}
            planned.append({"entry": locator, "source_ref": ref, "target": target,
                            "change": "update" if existing else "create", "n": None if existing else n,
                            "has_credentials": reference is not None, "key_file_referenced": any(
                                item["locator"]["index"] == index and item["reason"] == "key_file_reference" for item in issues),
                            "definition": definition, "mapping": mapping})
        return planned, issues, credentials, secrets_by_ref

    def _import_publish(self, receipt):
        """Publish the sanitized plan; safe to repeat after an interrupted call."""
        user, format_name = receipt["user"], receipt["format"]
        share = receipt.get("share")
        result = self.import_results[(user, receipt["import_id"])]
        for row, target in zip(receipt["plan"], result["targets"], strict=True):
            tid = row["target_id"]
            definition = {k: v for k, v in row["definition"].items() if k != "private_owner"}
            old_target = self.targets.get(tid)
            self.targets[tid] = target
            self.target_definitions[tid] = {
                **{k: v for k, v in definition.items() if k in ("id", "type", "name", "group", "tags", "vendor", "prompt_pattern", "share")},
                **{k: v for k, v in definition["options"].items() if k in ("host", "port", "user")},
            }
            self.import_definitions[tid] = definition
            self.import_refs[(format_name, "private" if share is not None else "shared", user if share is not None else "", row["source_ref"])] = tid
            if share is not None:
                self.private_owners[tid] = {"owner": user, "share": list(share)}
            if row["change"] == "update":
                fields = []
                if old_target and old_target.get("share") != target.get("share"):
                    fields.append("share")
                self.audit_events.append({"action": "target.update", "target_id": tid, "detail": {"fields": fields, "source": target.get("discovered_by") if isinstance(target, dict) else "tsb"}})
            elif row["change"] == "create":
                self.audit_events.append({"action": "target.create", "target_id": tid, "detail": {"source": target.get("discovered_by") if isinstance(target, dict) else "tsb"}})

    def _import_resume(self, receipt):
        result = self.import_results[(receipt["user"], receipt["import_id"])]
        if receipt["stage"] == "committed":
            try:
                self._import_publish(receipt)
            except OSError:
                status, headers, response = self._import_error(500, "internal_error")
                response["error"]["import_progress"] = {
                    "import_id": receipt["import_id"], "credentials_stored": result["credentials"]["stored"], "targets_written": [],
                    "mapping": "pending" if receipt["choice"].get("destination") == "personal" else "not_requested",
                    "audit": "unknown", "index": "pending", "effects_known": False,
                }
                return status, headers, response
            receipt["stage"] = "published"
        if receipt["stage"] == "published":
            if receipt["choice"].get("destination") == "personal":
                profiles = self.profiles.setdefault(receipt["user"], {})
                mappings = []
                for row in receipt["plan"]:
                    mapping = row["mapping"]
                    if mapping is None:
                        continue
                    tid, reference = row["target_id"], mapping["reference"]
                    state = "kept" if tid in profiles and profiles[tid] != reference else "mapped"
                    if state == "mapped":
                        profiles[tid] = reference
                    mappings.append({"target_id": tid, "reference": reference, "state": state})
                result["credentials"]["mappings"] = mappings
            receipt["stage"] = "complete"
            receipt["index"] = "applied"
            receipt["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return 200, [("Cache-Control", "no-store")], result

    def import_http(self, user, body, cookie):
        if not isinstance(body, dict):
            return self._import_error(422, "invalid_request")
        if "scope" in body:
            return self._import_error(422, "invalid_request")
        action = body.get("action")
        if action == "resume":
            if set(body) != {"action", "import_id"} or not isinstance(body.get("import_id"), str) or not re.fullmatch(r"imp-[a-z2-7]{25}[aeimquy4]", body["import_id"]):
                return self._import_error(422, "invalid_request")
            receipt = self.import_receipts.get((user, body["import_id"]))
            if receipt is None:
                return self._import_error(409, "import_preview_stale")
            share = self.import_receipt_shares.get((user, body["import_id"]))
            if ("target.private" if share is not None else "target.edit") not in self._permissions(user):
                return self._import_error(403, "forbidden")
            if receipt["choice"].get("destination") == "org" and "vault.admin" not in self._permissions(user):
                return self._import_error(403, "forbidden")
            if receipt.get("share") is None:
                result = self.import_results[(user, body["import_id"])]
                saved = [{"target": target, "change": row["change"]}
                         for target, row in zip(result["targets"], receipt["plan"], strict=True)]
                if not self._import_shared_allowed(user, saved):
                    return self._import_error(403, "forbidden")
            return self._import_resume(receipt)
        if action == "abort":
            if set(body) != {"action", "preview_token"}:
                return self._import_error(422, "invalid_request")
            payload = self._import_read_token(body["preview_token"], user)
            if payload is None:
                return self._import_error(409, "import_preview_stale")
            share = payload.get("share")
            if ("target.private" if share is not None else "target.edit") not in self._permissions(user):
                return self._import_error(403, "forbidden")
            receipt = self.import_receipts.get((user, payload["import_id"]))
            if receipt is not None:
                return self._import_conflict(payload["import_id"], receipt)
            self.import_aborted.add((user, payload["import_id"]))
            return 200, [("Cache-Control", "no-store")], {"outcome": "aborted", "import_id": payload["import_id"], "targets": []}
        required = {"action", "format", "export_bytes"}
        allowed = required | {"share"} | ({"preview_token", "credential_choice", "credential_destination", "unlock_class"} if action == "commit" else set())
        if action not in ("preview", "commit") or not required <= body.keys() <= allowed or (action == "commit" and "preview_token" not in body):
            return self._import_error(422, "invalid_request")
        if body["format"] not in ("yaml", "tabby", "securecrt", "mobaxterm") or not isinstance(body["export_bytes"], str):
            return self._import_error(422, "invalid_request")
        if "share" in body:
            share = body["share"]
            if not isinstance(share, list) or not (1 <= len(share) <= 256):
                return self._import_error(422, "invalid_request")
            if any(not isinstance(s, str) or not SHARE_ENTRY_RE.fullmatch(s) or ":" in s for s in share):
                return self._import_error(422, "invalid_request")
            if user not in share:
                return self._import_error(422, "invalid_request")
            if len(share) != len(set(share)):
                return self._import_error(422, "invalid_request")
            if any(s not in self.users for s in share):
                return self._import_error(422, "invalid_request")
            share_list = sorted(share)
        else:
            share_list = None
        if action == "commit":
            token = body["preview_token"]
            if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,4096}", token):
                return self._import_error(422, "invalid_request")
            choice, destination, unlock = body.get("credential_choice"), body.get("credential_destination"), body.get("unlock_class")
            if (any(key in body and body[key] is None for key in ("credential_choice", "credential_destination", "unlock_class"))
                    or choice not in (None, "save", "discard") or destination not in (None, "personal", "org")
                    or unlock not in (None, "user_unlocked", "broker_unlocked")
                    or (choice != "save" and (destination is not None or unlock is not None))
                    or (destination == "org" and unlock is None)
                    or (destination != "org" and unlock is not None)):
                return self._import_error(422, "invalid_request")
        permission = "target.private" if share_list is not None else "target.edit"
        if permission not in self._permissions(user):
            return self._import_error(403, "forbidden")
        if body.get("credential_destination") == "org" and "vault.admin" not in self._permissions(user):
            return self._import_error(403, "forbidden")
        try:
            raw = base64.b64decode(body["export_bytes"], validate=True)
        except (ValueError, binascii.Error):
            return self._import_error(422, "invalid_request")
        if len(raw) > 4 * 1024 * 1024:
            return self._import_error(422, "invalid_request")
        try:
            rows = _import_sources.read(raw, body["format"])
        except _import_sources.UnsupportedFormat:
            return self._import_error(422, "import_format_unsupported")
        except (ValueError, UnicodeError, RecursionError, TypeError):
            return self._import_error(422, "invalid_request")
        payload = self._import_read_token(body.get("preview_token"), user) if action == "commit" else None
        if action == "commit" and payload is None:
            return self._import_error(409, "import_preview_stale")
        import_id = payload["import_id"] if payload else "imp-" + _b32(secrets.token_bytes(16))
        commitment = hmac.new(self.id_key, len(b"tsb-import-export-bytes").to_bytes(4, "big") + b"tsb-import-export-bytes" + raw, hashlib.sha256).hexdigest()
        if payload and (user, import_id) in self.import_receipts:
            if (payload.get("share"), payload["format"], payload["commitment"]) != (share_list, body["format"], commitment):
                return self._import_error(409, "import_preview_stale")
            receipt = self.import_receipts[(user, import_id)]
            if receipt.get("share") is None:
                result = self.import_results[(user, import_id)]
                saved = [{"target": target, "change": row["change"]}
                         for target, row in zip(result["targets"], receipt["plan"], strict=True)]
                if not self._import_shared_allowed(user, saved):
                    return self._import_error(403, "forbidden")
            choice = receipt["choice"]
            requested = body.get("credential_choice")
            destination = body.get("credential_destination", "personal") if requested == "save" else None
            if (choice["choice"] != "none" and
                    (requested not in (None, choice["choice"])
                     or (requested == "save" and (destination != choice.get("destination")
                                                  or body.get("unlock_class") != choice.get("class"))))):
                return self._import_conflict(import_id, receipt)
            return self._import_resume(receipt)
        try:
            targets, issues, credentials, secrets_by_ref = self._import_plan(user, share_list, import_id, rows, body["format"])
        except (ValueError, UnicodeError, RecursionError):
            return self._import_error(422, "invalid_request")
        if share_list is None and not self._import_shared_allowed(user, targets):
            return self._import_error(403, "forbidden")
        plan = []
        for row in targets:
            definition = dict(row["definition"])
            if share_list is not None:
                definition["private_owner"] = user
            plan.append({"locator": row["entry"], "source_ref": row["source_ref"], "change": row["change"],
                         "target_id": row["target"]["id"], "n": row["n"], "definition": definition,
                         "credentials": [{"reference": item["reference"], "kind": item["kind"]}
                                         for item in credentials if row["target"]["id"] in item["target_ids"]],
                         "mapping": row["mapping"]})
        try:
            digest_dict = {"format": body["format"], "plan": plan, "not_carried": issues}
            if share_list is not None:
                digest_dict["share"] = share_list
            digest = hashlib.sha256(_canonical(digest_dict).encode()).hexdigest()
        except (UnicodeError, ValueError):
            return self._import_error(422, "invalid_request")
        if action == "preview":
            token_dict = {"user": user, "format": body["format"], "import_id": import_id,
                          "expires": int(time.time()) + 600, "commitment": commitment, "digest": digest}
            if share_list is not None:
                token_dict["share"] = share_list
            token = self._import_token(token_dict)
            preview_resp = {"import_id": import_id, "preview_token": token,
                            "format": body["format"], "targets": targets, "not_carried": issues, "credentials": credentials}
            if share_list is not None:
                preview_resp["share"] = share_list
            return 200, [("Cache-Control", "no-store")], preview_resp
        if (payload.get("share"), payload["format"], payload["commitment"], payload["digest"]) != (share_list, body["format"], commitment, digest):
            return self._import_error(409, "import_preview_stale")
        if credentials and body.get("credential_choice") not in ("discard", "save"):
            return self._import_error(422, "invalid_request")
        choice = {"choice": "discard" if credentials else "none"}
        stored = []
        if body.get("credential_choice") == "save" and credentials:
            if user not in self.vault_users:
                return self._import_error(409, "vault_not_initialized")
            if cookie not in self.unlocked:
                return self._import_error(409, "vault_locked")
            destination = body.get("credential_destination", "personal")
            entries = self.org_creds if destination == "org" else self.creds.get(user, {})
            if any(item["reference"] in entries for item in credentials):
                return self._import_error(409, "credential_exists")
            choice = {"choice": "save", "destination": destination}
            if destination == "org":
                choice["class"] = body["unlock_class"]
            for item in credentials:
                reference = item["reference"]
                entry = {"reference": reference, "kind": item["kind"], "login_name": item["login_name"],
                         "secret": secrets_by_ref[reference]}
                if destination == "org":
                    entry.update({"unlock_class": body["unlock_class"], "stored_by": user})
                entries[reference] = entry
                stored.append({"reference": reference, "layer": destination, "kind": item["kind"],
                               "target_ids": item["target_ids"]})
            if destination == "personal":
                self.creds[user] = entries
        result = {"outcome": "written", "import_id": import_id, "targets": [row["target"] for row in targets],
                  "not_carried": issues, "credentials": {"choice": choice["choice"], "stored": stored, "mappings": []}}
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        receipt = {"version": 1, "import_id": import_id, "user": user,
                   "format": body["format"], "plan_digest": digest, "plan": plan, "not_carried": issues,
                   "choice": choice, "stage": "committed",
                   "audit": "unknown", "index": "pending", "created_at": now, "updated_at": now}
        if share_list is not None:
            receipt["share"] = share_list
        self.import_results[(user, import_id)] = result
        self.import_receipts[(user, import_id)] = receipt
        self.import_receipt_shares[(user, import_id)] = share_list
        return self._import_resume(receipt)
