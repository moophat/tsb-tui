"""Three Windows-guard fixes from PR #32's Qodo review (2026-11-09).

Each test is red before its fix and green after. Tests run on Linux by monkeypatching sys.platform
and the relevant OS calls; no Windows host is needed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fix 1: read_answer_pipe refuses non-regular files on Windows before select
# ---------------------------------------------------------------------------

def test_read_answer_pipe_refuses_non_regular_file_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """P4: on win32, read_answer_pipe refuses any non-regular-file path with a plain error
    before it touches select.select.

    Why (Rule 9): select.select on a pipe fd is not supported on Windows. The function must
    reject anything that is not a regular file or stdin (fd 0) before it can reach select, so
    the error is clean and no blocking open can occur.

    Approach: create a real FIFO on Linux (supported there), monkeypatch sys.platform to 'win32',
    and assert the refusal before select is called.
    """
    from tsb_tui.drive import commands

    monkeypatch.setattr(sys, "platform", "win32")

    # Create a real FIFO — valid on Linux, and its stat reports S_ISFIFO.
    fifo_path = tmp_path / "answers.fifo"
    os.mkfifo(str(fifo_path))

    # Track whether select.select is ever called.
    select_called: list[bool] = []

    import select as _select_mod

    def _guarded_select(*args, **kwargs):  # type: ignore[no-untyped-def]
        select_called.append(True)
        return ([], [], [])  # type: ignore[return-value]

    monkeypatch.setattr(_select_mod, "select", _guarded_select)

    with pytest.raises(commands.DriveError) as exc_info:
        commands.read_answer_pipe(str(fifo_path), 1)

    assert exc_info.value.code == "bad_request"
    # The error must mention that a regular file is required (Windows context).
    assert "regular file" in exc_info.value.why or "Windows" in exc_info.value.why
    # select must never have been called.
    assert not select_called, "select.select was called on the Windows path — it must not be"


# ---------------------------------------------------------------------------
# Fix 2: os.add_dll_directory OSError becomes LibraryError
# ---------------------------------------------------------------------------

def test_add_dll_directory_oserror_becomes_library_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """P2b: an OSError from os.add_dll_directory on Windows becomes LibraryError, not an
    unhandled exception.

    Why (Rule 9): the existing try/except around ctypes.CDLL catches load failures, but
    add_dll_directory sits outside it. An OSError from it (e.g. the directory does not exist
    after a corrupt install) crashes the process instead of going through load_engine's normal
    startup error path. Moving it inside the try block fixes that.
    """
    from tsb_tui.term import ghostty as G

    monkeypatch.setattr(sys, "platform", "win32")

    def _bad_add_dll_directory(path: str) -> None:
        raise OSError(f"fake: cannot add DLL directory {path!r}")

    monkeypatch.setattr(os, "add_dll_directory", _bad_add_dll_directory, raising=False)

    # Create a file so the is_file() check passes.
    fake_so = tmp_path / "ghostty-vt.dll"
    fake_so.write_bytes(b"\x00" * 4)

    with pytest.raises(G.LibraryError):
        G.load(fake_so)


# ---------------------------------------------------------------------------
# Fix 3: --config help text names the Windows path on Windows
# ---------------------------------------------------------------------------

def test_config_help_names_windows_path_on_win32(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P6: when sys.platform is 'win32', the --config help string in both __main__ and
    runner mentions the Windows default (%APPDATA%\\tsb-tui\\config.toml), not the POSIX one.

    Why (Rule 9): a user reading `--help` on Windows should see the platform-correct path.
    The fix exposes a module-level `_config_help()` function in each module that returns a
    platform-conditional string; this test calls both and asserts the Windows branch.
    """
    import importlib

    monkeypatch.setattr(sys, "platform", "win32")

    import tsb_tui.__main__ as main_mod
    import tsb_tui.drive.runner as runner_mod

    # Reload with the patched platform so the conditional help strings are rebuilt.
    importlib.reload(main_mod)
    importlib.reload(runner_mod)

    # _config_help() is added by the fix to both modules; it returns the right string per platform.
    main_help: str = main_mod._config_help()      # type: ignore[attr-defined]
    runner_help: str = runner_mod._config_help()  # type: ignore[attr-defined]

    win_marker = "APPDATA"
    assert win_marker in main_help, (
        f"__main__._config_help() does not mention {win_marker!r} on win32: {main_help!r}"
    )
    assert win_marker in runner_help, (
        f"runner._config_help() does not mention {win_marker!r} on win32: {runner_help!r}"
    )
