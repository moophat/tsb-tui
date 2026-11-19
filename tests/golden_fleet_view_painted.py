"""Regenerates `fleet_view_painted_golden.json`, the painted full-view text the golden test compares against.

Not collected by the suite: no `test_` function, so a normal run never rewrites the golden (a test that
regenerated its own fixture would compare nothing). Run it by hand, exactly as the golden was first made:

    python tests/golden_fleet_view_painted.py [case,case,...]

with the test venv's python. It captures from the package on `sys.path`, so to capture the code *before* a
change, point it at a copy of the package from the commit before it (`BASE` is any scratch directory):

    mkdir -p "$BASE" && git archive <commit> tsb_tui | tar -x -C "$BASE"
    PYTHONPATH="$BASE" python tests/golden_fleet_view_painted.py

The cases are `CASES` in the test module: the contract's `C1/session-record-shape-v0.3` records, ~95% ended, all
owned by the login, `GET /sessions` answered from that list. The environment is the conftest's pinned one, set
here the same way, so the capture matches what the test sees.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))
for _entry in (REPO_ROOT / "program" / "contract", REPO_ROOT / "program" / "contract" / "client"):
    sys.path.insert(0, str(_entry))

from test_fleet_view_painted_rows import CASES, GOLDEN, paint_case


def _pin_environment(home: Path) -> None:
    """The conftest's `_pinned_environment`, for a run outside pytest."""
    os.environ["HOME"] = str(home)
    os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
    os.environ["XDG_STATE_HOME"] = str(home / ".local" / "state")
    library = os.environ.get("TSB_TUI_TERM_LIBRARY", str(REPO_ROOT / "vendor" / "libghostty-vt" / "libghostty-vt.so"))
    for name in [name for name in os.environ if name.startswith("TSB_TUI_")]:
        del os.environ[name]
    os.environ["TSB_TUI_TERM_LIBRARY"] = library
    os.environ["TSB_TUI_SIZE_AUTO_REQUEST"] = "false"
    os.environ["TSB_TUI_REATTACH_BACKOFF_MS"] = "20,20,20,20,20,20"
    os.environ["TSB_TUI_MENU_OPEN_ON_LEADER_AFTER_MS"] = "10000"


async def _capture(name: str, tmp_path: Path) -> str:
    from tsbc._serve import ServeApp, StubServer
    with StubServer(ServeApp()) as url:
        return await paint_case(url, tmp_path, CASES[name])


def main() -> None:
    names = sys.argv[1].split(",") if len(sys.argv) > 1 else list(CASES)
    tmp_path = Path(tempfile.mkdtemp(prefix="golden-fleet-view-"))
    (tmp_path / "home").mkdir(exist_ok=True)
    _pin_environment(tmp_path / "home")
    out = {name: asyncio.run(_capture(name, tmp_path)) for name in names}
    GOLDEN.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print({name: len(text.splitlines()) for name, text in out.items()}, flush=True)


if __name__ == "__main__":
    main()
