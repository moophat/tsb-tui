"""Session records for the fleet tests, from the contract's own fixture (DT6): `C1/session-record-shape-v0.3`'s
`valid: true` documents, and a record with fields changed is validated against the session-record schema before a
test uses it, so no test stands on a shape the contract does not allow."""

from __future__ import annotations

import json
from pathlib import Path

from tsbc.schema import Registry

CONTRACT = Path(__file__).resolve().parents[2] / "program" / "contract"
REGISTRY = Registry.from_tree(CONTRACT)
FIXTURE = json.loads((CONTRACT / "fixtures" / "C1" / "session-record-shape-v0.3.json")
                     .read_text(encoding="utf-8"))
ME = "fixture-user"
PEER = "fixture-peer"


def fixture_records() -> list[dict]:
    """The valid, not ended documents of the fixture: a live one with its clients, a detached one, a live one."""
    return [i["doc"] for i in FIXTURE["case"]["instances"]
            if i["valid"] and i["doc"]["state"] not in ("killed", "terminated")]


def derived(record: dict, **changes: object) -> dict:
    """A fixture record with fields changed, validated before use."""
    out = {**record, **changes}
    errors = REGISTRY.validate(out, "schemas/session-record.schema.json")
    assert not errors, errors
    return out


def holder(user: str | None, client_id: str | None = None) -> dict | None:
    return None if user is None else {"client_id": client_id or f"client-{user}", "user": user}
