"""The vault tests' shared pieces: C7's vectors as the contract publishes them, and the contract's fixture server taught the
shapes `tsbc serve` does not simulate (a wrong passphrase, the passphrase limiter, a short screen-lock time, an encrypted
key refused, text no field would normally carry).

`tsbc serve` answers an untrusted unlock with 204 whatever the halves are, and serves one constant salt; a test that must
prove "the unlock was sent with the halves of C7's vector for this passphrase and salt" therefore serves the vector's own
salt and refuses every other pair, as the core's GCM tag does. Every value here is a synthetic constant or a published
vector.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from tsbc import _serve
from tsbc._serve import ServeApp

CONTRACT_C7 = Path(__file__).resolve().parents[1] / "contract" / "fixtures" / "C7"

RECORDING_LINE = "Typed keys may be recorded when this TUI runs inside a TSB session."


def vector(name: str) -> dict:
    """The one instance of a C7 vector file: its `doc`."""
    return json.loads((CONTRACT_C7 / f"{name}.json").read_text())["case"]["instances"][0]["doc"]


def flat(text: str) -> str:
    """Screen text as one run of words: the panel borders (the heavy ones of the pane that has the keys too) and the line
    breaks of wrapped text taken out."""
    return " ".join(text.replace("│", " ").replace("┃", " ").split())


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class Chain:
    """C7's master-hash chain: the passphrase the vectors use, the salt, and the two halves in hex and on the wire."""

    def __init__(self) -> None:
        self.passphrase = vector("chain-passphrase-bytes")["passphrase"]
        hashed = vector("chain-master-hash")
        split = vector("chain-split")
        self.salt = bytes.fromhex(hashed["user_salt"])
        self.master_hash = bytes.fromhex(hashed["master_hash"])
        self.part1 = bytes.fromhex(split["part1"])
        self.part2 = bytes.fromhex(split["part2"])

    @property
    def wire(self) -> tuple[str, str]:
        return b64(self.part1), b64(self.part2)


CHAIN = Chain()


class VaultServe(ServeApp):
    """`tsbc serve`, plus: the salt it serves and mints, which pair of halves opens the vault, the passphrase limiter,
    the lock settings it answers, and an encrypted private key refused."""

    def __init__(self, *, salt: bytes | None = None, accept: tuple[str, str] | None = None, attempts: int = 5,
                 locks: dict | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.salt_wire = b64(salt) if salt is not None else None
        self.accept = accept            # the (part1, part2) that opens the vault; None: any pair does (serve's own)
        self.attempts = attempts        # failures before 429
        self.failures = 0
        self.unlock_bodies: list[dict] = []
        self.init_bodies: list[dict] = []
        self.locks = dict(_serve.VaultMixin.vault_locks(), **(locks or {}))
        self.names: dict[str, str] = {}   # device_id -> a name to serve in place of the stored one

    def vault_locks(self) -> dict:   # type: ignore[override]
        return dict(self.locks)

    def vault_http(self, method: str, path: str, headers: dict, body: Any, query: dict, user: str):
        if self.salt_wire is not None:
            if (method, path) == ("GET", "/vault/salt") and user in self.vault_users:
                return 200, [], {"initialized": True, "user_salt": self.salt_wire}
            if (method, path) == ("GET", "/vault/salt") and user in self.minted:
                return 200, [], {"initialized": False, "user_salt": self.salt_wire}
            if (method, path) == ("POST", "/vault/salt") and user not in self.vault_users:
                self.minted[user] = self.salt_wire
        if (method, path) == ("POST", "/vault/init"):
            self.init_bodies.append(body)
        if (method, path) == ("POST", "/vault/credentials") and isinstance(body, dict) \
                and "ENCRYPTED" in str(body.get("secret", "")):
            return self._err(422, "invalid_request", "an encrypted private key is not accepted: decrypt it first")
        answer = super().vault_http(method, path, headers, body, query, user)
        if (method, path) == ("GET", "/devices") and answer is not None and answer[0] == 200:
            for device in answer[2]["devices"]:
                device["device_name"] = self.names.get(device["device_id"], device["device_name"])
        return answer

    def _unlock(self, cookie: str, user: str, body: Any):
        if isinstance(body, dict) and body.get("mode") == "untrusted":
            self.unlock_bodies.append(dict(body))
            if self.failures >= self.attempts:
                return self._err(429, "rate_limited", "too many passphrase attempts; try again later")
            if self.accept is not None and (body.get("part1"), body.get("part2")) != self.accept:
                self.failures += 1
                status, headers, reply = self._err(409, "unlock_failed", "the passphrase did not open the vault")
                reply["error"]["attempts_remaining"] = max(self.attempts - self.failures, 0)
                return status, headers, reply
        return super()._unlock(cookie, user, body)

    # -- seeding, as a test's "the vault already holds this" --

    def hold_credential(self, user: str, reference: str, kind: str = "password", login_name: str = "oper",
                        secret: str = "FIXTURE-SECRET", label: str | None = None) -> None:
        entry = {"reference": reference, "kind": kind, "login_name": login_name, "secret": secret}
        if label is not None:
            entry["label"] = label
        self.creds.setdefault(user, {})[reference] = entry

    def map_target(self, user: str, target: str, reference: str) -> None:
        self.profiles.setdefault(user, {})[target] = reference

    def register_device(self, user: str, device_id: str, name: str) -> None:
        self.devices[(user, device_id)] = {"device_id": device_id, "device_name": name,
                                           "registered_at": _serve_now(), "last_used_at": _serve_now()}

    def calls(self, method: str, path: str) -> list[Any]:
        """The bodies of every request of this shape the server has seen, in order."""
        return [body for m, p, body in list(self.requests) if (m, p) == (method, path)]


def _serve_now() -> str:
    from tsbc._serve_vault import NOW

    return NOW


DEVICE_A = "A" * 42 + "A"          # a device_id: 43 base64url characters (common.schema.json device_id)
DEVICE_B = "B" * 42 + "A"