"""The state one `tsbc verify` run hands to its graders, and its run-time inputs."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schema import Registry

# Run-time inputs: name -> (source, variable). "file" means the variable names a file whose
# contents (without the trailing newline) are the value, so a password never sits in argv or in
# the environment. The same file is the one the core's seed reads (TSB_SUPERADMIN_PASSWORD_FILE).
INPUTS = {
    "user": ("env", "TSB_SUPERADMIN_USER"),
    "password": ("file", "TSB_SUPERADMIN_PASSWORD_FILE"),
    "target_id": ("env", "TSB_TARGET_ID"),
    "target_no_cred_id": ("env", "TSB_TARGET_NO_CRED_ID"),
    "peer_user": ("env", "TSB_PEER_USER"),
    "peer_password": ("file", "TSB_PEER_PASSWORD_FILE"),
    "viewer_user": ("env", "TSB_VIEWER_USER"),
    "viewer_password": ("file", "TSB_VIEWER_PASSWORD_FILE"),
}


class InputMissing(Exception):
    """A value a required fixture needs was not provided: FAIL `input missing`, never SKIP."""


@dataclass
class Run:
    root: Path
    registry: Registry
    profile: str
    url: str | None
    artifacts: Path | None
    ca_file: str | None
    timeout: float
    overrides: dict[str, str] = field(default_factory=dict)
    provider_tests: frozenset[str] = frozenset()  # units whose own tests the caller ran with this run
    provider_results: Any = None  # tsbc._results.Results: what those tests did, as the runners wrote it
    warnings: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)  # shared by graders across fixtures
    _cache: dict[str, str] = field(default_factory=dict)

    def input(self, name: str) -> str:
        if name in self._cache:
            return self._cache[name]
        if name in self.overrides:
            value = self.overrides[name]
        else:
            source, var = INPUTS[name]
            raw = os.environ.get(var)
            if not raw:
                raise InputMissing(f"input missing: {name} (set {var})")
            value = raw if source == "env" else self._read_secret(name, var, Path(raw))
        self._cache[name] = value
        return value

    def redact(self, text: str) -> str:
        """text with every secret input this run has read (a password from its file) replaced by
        <redacted>, so a provider that echoes one back in a message never gets it printed."""
        for name, value in self._cache.items():
            if INPUTS.get(name, ("env", ""))[0] == "file" and value:
                text = text.replace(value, "<redacted>")
        return text

    def _read_secret(self, name: str, var: str, path: Path) -> str:
        try:
            mode = path.stat().st_mode
            value = path.read_text(encoding="utf-8").rstrip("\r\n")
        except (OSError, UnicodeDecodeError) as exc:
            raise InputMissing(f"input missing: {name} ({var} names a file that cannot be read: "
                               f"{getattr(exc, 'strerror', None) or exc})") from None
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            warning = f"{var} names a file that group or others can read; it should be owner-only"
            if warning not in self.warnings:
                self.warnings.append(warning)
        if not value:
            raise InputMissing(f"input missing: {name} ({var} names an empty file)")
        return value
