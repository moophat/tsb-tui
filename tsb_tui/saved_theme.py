"""The saved theme choice per TSB login (TT11, TT16–TT19).

The picker's Save writes one table per login to a state file; the next login reads it and applies it.
drive never reads or writes this file (TT19).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path


class SavedThemeError(Exception):
    """The state file is unreadable or malformed."""


def state_path(environ: Mapping[str, str] | None = None) -> Path:
    """The state file's path: `$XDG_STATE_HOME/tsb-tui/theme.toml` or `~/.local/state/tsb-tui/theme.toml`."""
    env = os.environ if environ is None else environ
    xdg_state = env.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "tsb-tui" / "theme.toml"
    if sys.platform == "win32":
        local_appdata = env.get("LOCALAPPDATA") or str(Path(env.get("HOME", "~")).expanduser() / "AppData" / "Local")
        return Path(local_appdata) / "tsb-tui" / "theme.toml"
    return Path(env.get("HOME", "~")).expanduser() / ".local" / "state" / "tsb-tui" / "theme.toml"


def read(path: Path) -> dict[str, dict[str, str]]:
    """Read the state file. An absent file is `{}`. Each top-level table is one login with string
    `theme` and `scheme`. Anything else raises `SavedThemeError` naming the file and the field."""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        doc = tomllib.loads(text)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SavedThemeError(f"{path}: could not parse ({exc})") from None
    result: dict[str, dict[str, str]] = {}
    for login, entry in doc.items():
        if not isinstance(entry, dict):
            raise SavedThemeError(f"{path}: [{login!r}] must be a table")
        for field in ("theme", "scheme"):
            if field not in entry:
                raise SavedThemeError(f"{path}: [{login!r}].{field} is missing")
            if not isinstance(entry[field], str):
                raise SavedThemeError(f"{path}: [{login!r}].{field} must be a string")
        result[login] = {"theme": entry["theme"], "scheme": entry["scheme"]}
    return result


def write(path: Path, entries: Mapping[str, Mapping[str, str]]) -> None:
    """Write the state file atomically: a temporary file in the same directory, mode 0600, written,
    flushed, `fsync`ed, then `os.replace`d over the file. The directory is created with mode 0700.
    Every key and string is written with `json.dumps` so TOML accepts any login name.

    Each write gets its own temporary file, writes every byte, and removes it on any failure before the
    replace, so a corrupt or partial file is never installed. Two TUIs saving at once may still lose one
    login's entry: the last writer wins, an accepted risk."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Build the TOML text manually so every key and string is quoted with json.dumps.
    lines: list[str] = []
    for login, entry in entries.items():
        lines.append(f"[{json.dumps(login)}]")
        lines.append(f"theme = {json.dumps(entry['theme'])}")
        lines.append(f"scheme = {json.dumps(entry['scheme'])}")
        lines.append("")
    text = "\n".join(lines)
    data = text.encode("utf-8")

    # mkstemp gives a unique name; write in a loop; cleanup on exception.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        # Write all bytes in a loop (os.write may write fewer than requested).
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        os.fsync(fd)
        os.close(fd)
        fd = -1  # Mark as closed
        os.replace(tmp_path, path)
    except Exception:
        # Clean up: close fd if still open, remove temp file, re-raise.
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def save(path: Path, login: str, theme: str, scheme: str) -> None:
    """Save this login's entry: read first (a `SavedThemeError` propagates: it never overwrites what
    it could not read, TT17), set this login's entry, write."""
    entries = read(path)
    entries[login] = {"theme": theme, "scheme": scheme}
    write(path, entries)
