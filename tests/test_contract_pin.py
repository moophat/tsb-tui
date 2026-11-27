"""A consumer that regressed to importing the contract from anywhere but `contract/` —
site-packages, the sibling `../tsb-program`, or a stale copy of an older tag — must fail a
test, not pass silently."""

from __future__ import annotations

from pathlib import Path

import tsb_contract_client
import tsbc
from conftest import CONTRACT_DIR


def test_tsbc_and_client_resolve_under_contract_not_elsewhere():
    for mod in (tsbc, tsb_contract_client):
        resolved = Path(mod.__file__).resolve()
        assert resolved.is_relative_to(CONTRACT_DIR), f"{mod.__name__} imported from {resolved}, not {CONTRACT_DIR}"