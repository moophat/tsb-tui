"""Tests for the contract runner and client. Run from the repo root:

    python -m unittest discover contract/

Discovery puts contract/ on sys.path (so `tsbc` imports); the client package lives one level down
in contract/client/, which is added here so the tests import it exactly as a consumer does.
"""

import sys
from pathlib import Path

CONTRACT = Path(__file__).resolve().parent.parent
for entry in (CONTRACT, CONTRACT / "client"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
