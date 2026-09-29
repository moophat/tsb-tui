"""A consumer that regressed to importing the contract from the working tree instead of its
pinned tag — the exact defect conftest.py's materializer exists to prevent — must fail a test, not
pass silently."""

from __future__ import annotations

from pathlib import Path

import tsb_contract_client
import tsbc
from conftest import CONTRACT_DIR


def test_tsbc_and_client_resolve_under_the_pinned_cache_not_the_working_tree():
    for mod in (tsbc, tsb_contract_client):
        resolved = Path(mod.__file__).resolve()
        assert ".contract-pins" in resolved.parts, f"{mod.__name__} imported from {resolved}, not the pinned cache"
        assert resolved.is_relative_to(CONTRACT_DIR), f"{mod.__name__} imported from {resolved}, not {CONTRACT_DIR}"
