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
"""

from __future__ import annotations

import sys
from pathlib import Path

TUI_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TUI_DIR.parent
CONTRACT_DIR = REPO_ROOT / "program" / "contract"

if not CONTRACT_DIR.is_dir() or not any(CONTRACT_DIR.iterdir()):
    raise RuntimeError(
        f"{CONTRACT_DIR} is empty or absent — run make contract-init (it populates the program "
        f"submodule at its pinned release)")

for _entry in (CONTRACT_DIR, CONTRACT_DIR / "client"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))