"""Puts the pinned contract tree at `program/contract/` on sys.path before any test module
imports `tsbc` or `tsb_contract_client`. The pin is the program submodule at its release
(v0.3.1); `program/contract/` and `program/contract/client/` go first on sys.path so an import
that would otherwise resolve from site-packages, the sibling `../tsb-program`, or a stale
copy of an older tag fails loudly instead. Nothing is copied, cached or resolved with git: the
path is valid because it is the program release (`make contract-check` proves it). CI no longer
installs `contract/` into this venv (`-e ./contract` dropped, .github/workflows/test.yml) —
without this, collecting any test module that imports tsb_tui (which imports tsbc via
_transport.py) fails outright. pytest imports every conftest.py before collecting the test modules
in its directory, so this runs first.

It also yields the vendored libghostty-vt shared object's path to the engine tests (`term_so`).
That file is untracked engine input (phase 1's H1 tarball), so the fixture fails loudly when it is
absent rather than letting the engine tests error with a confusing import — exactly as this file
already does for `program/contract/`.

Every test runs in a pinned environment (`_pinned_environment`): `HOME` and `XDG_CONFIG_HOME` point at
an empty directory, no `TSB_TUI_*` variable leaks in from the workstation or CI, and the app is given
what a start-up needs: the engine's path (`term.library`) and an explicit `size.auto_request = false`
(OPEN-1 was ruled on 2026-10-12, so the default profile ships `true`; most tests want the window's
size to speak for nothing, so they start it off). A test that cares sets it itself with
`monkeypatch.setenv`.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

TUI_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TUI_DIR.parent
CONTRACT_DIR = REPO_ROOT / "program" / "contract"
TERM_SO = REPO_ROOT / "vt" / "third_party" / "libghostty-vt" / "lib" / "libghostty-vt.so"

if not CONTRACT_DIR.is_dir() or not any(CONTRACT_DIR.iterdir()):
    raise RuntimeError(
        f"{CONTRACT_DIR} is empty or absent — run make contract-init (it populates the program "
        f"submodule at its pinned release)")

for _entry in (CONTRACT_DIR, CONTRACT_DIR / "client"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))


@pytest.fixture(scope="session")
def term_so() -> Path:
    """The path to the vendored libghostty-vt `.so` (phase 1's engine input).

    The engine is built on the integration node at the pin and unpacked into the worktree,
    untracked; it is never committed (phase 1 "Input"). Failing here, loudly, is the point: without
    it there is no engine to test, and an import error would hide that.
    """
    if not TERM_SO.is_file():
        raise RuntimeError(
            f"{TERM_SO} is absent — unpack the H1 node-step tarball into the worktree (it is "
            f"untracked engine input; phase 1 does not start without it)")
    return TERM_SO


@pytest.fixture(autouse=True)
def _pinned_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, term_so: Path) -> None:
    """No test reads the workstation's or CI's configuration, and every app has an engine to load."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for name in [name for name in os.environ if name.startswith("TSB_TUI_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("TSB_TUI_TERM_LIBRARY", str(term_so))
    monkeypatch.setenv("TSB_TUI_SIZE_AUTO_REQUEST", "false")
    # A reattach in a test should not wait half a second per step.
    monkeypatch.setenv("TSB_TUI_REATTACH_BACKOFF_MS", "20,20,20,20,20,20")
    # `^\ <letter>` must never race the Menu's timer on a loaded machine: a test that wants the Menu from
    # the leader alone sets a short delay itself.
    monkeypatch.setenv("TSB_TUI_MENU_OPEN_ON_LEADER_AFTER_MS", "10000")


def _screen_text(app) -> str:
    """The text the operator sees, row by row, as Textual composed it: the same entry point for any
    widget that paints the terminal, so a test reads the screen and not an implementation."""
    return "\n".join(strip.text.rstrip() for strip in app.screen._compositor.render_strips())


@pytest.fixture
def screen_text() -> Callable:
    return _screen_text


@pytest.fixture
def serve() -> Iterator[Callable]:
    """`start(app=None) -> (app, url)`: the contract's in-process fixture server (DT7), stopped when
    the test ends. Pass a `ServeApp` (or a subclass) to shape what it sends."""
    from tsbc._serve import ServeApp, StubServer

    stack = contextlib.ExitStack()

    def start(app=None):
        app = app if app is not None else ServeApp()
        return app, stack.enter_context(StubServer(app))

    yield start
    stack.close()


@pytest.fixture
def wait_until() -> Callable:
    """`await wait_until(pilot, predicate, "what")`: wait on a condition, bounded, never on time."""

    async def wait(pilot, predicate: Callable[[], bool], what: str, timeout_s: float = 10.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate():
                return
            await pilot.pause(0.01)
        raise AssertionError(f"timed out after {timeout_s:g}s waiting for {what}")

    return wait


@pytest.fixture(scope="session")
def server_frame_validator() -> Callable[[dict], None]:
    """Validates a frame a test builds against the contract's own schema (DT6), so a hand-built case
    is never a shape the contract does not allow."""
    from tsbc.schema import Registry

    registry = Registry.from_tree(CONTRACT_DIR)

    def validate(frame: dict) -> None:
        errors = registry.validate(frame, "schemas/ws-frames.schema.json#/$defs/server")
        assert not errors, f"frame {frame.get('t')} breaks the contract schema: {errors[0]}"

    return validate
