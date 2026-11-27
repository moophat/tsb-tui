"""States built from the contract's own frames, for tests that need one without a server (DT6).

`C2/ws-frames-shape` carries a valid `attached` document; a case the fixtures do not give (a state with a
chosen holder, owner and set of clients) is that document with its fields set, and each frame is
validated against the server `$def` before it is applied, so no test stands on a shape the contract
does not allow. The test that uses one says which of its frames it built.
"""

from __future__ import annotations

import copy
from pathlib import Path

from tsbc.fixtures import load_fixtures
from tsbc.schema import Registry

from tsb_tui.model.attachment import AttachmentState

CONTRACT = Path(__file__).resolve().parents[1] / "contract"
WS_FRAMES = "schemas/ws-frames.schema.json"
REGISTRY = Registry.from_tree(CONTRACT)


def _attached_template() -> dict:
    loaded, errors = load_fixtures(CONTRACT)
    assert not errors, f"the contract tree does not load: {errors}"
    for fixture in loaded:
        if (fixture.seam, fixture.name) == ("C2", "ws-frames-shape"):
            for instance in fixture.doc["case"]["instances"]:
                doc = instance.get("doc")
                if instance["valid"] and isinstance(doc, dict) and doc.get("t") == "attached":
                    return doc
    raise AssertionError("C2/ws-frames-shape has no valid attached document")


ATTACHED = _attached_template()


def validated(frame: dict) -> dict:
    errors = REGISTRY.validate(frame, f"{WS_FRAMES}#/$defs/server")
    assert not errors, f"frame {frame.get('t')} breaks the contract schema: {errors[0]}"
    return frame


def attached_frame(*, client_id: str, owner: str, holder: tuple[str, str] | None,
                   cols: int = 120, rows: int = 40) -> dict:
    frame = copy.deepcopy(ATTACHED)
    frame["client_id"] = client_id
    session = frame["session"]
    session["owner"] = owner
    session["cols"], session["rows"] = cols, rows
    session["control_holder"] = None if holder is None else {"client_id": holder[0], "user": holder[1]}
    return validated(frame)


def clients_frame(*clients: tuple[str, str, bool]) -> dict:
    return validated({"t": "clients", "v": 1, "session_id": ATTACHED["session"]["id"],
                      "clients": [{"client_id": cid, "user": user, "may_control": may}
                                  for cid, user, may in clients]})


def token_frame(holder: tuple[str, str] | None, reason: str) -> dict:
    return validated({"t": "token", "v": 1, "kind": "control", "reason": reason,
                      "holder": None if holder is None else {"client_id": holder[0], "user": holder[1]}})


def size_frame(cols: int, rows: int, reason: str) -> dict:
    return validated({"t": "size", "v": 1, "session_id": ATTACHED["session"]["id"],
                      "cols": cols, "rows": rows, "reason": reason})


def refused_frame(request: str, reason: str) -> dict:
    return validated({"t": "request_refused", "v": 1, "request": request, "reason": reason})


def state_of(*, me: tuple[str, str], owner: str, holder: tuple[str, str] | None,
             clients: tuple[tuple[str, str, bool], ...], cols: int = 120, rows: int = 40,
             ) -> AttachmentState:
    """An attached state: `me` is this client (id, user), `clients` the whole set (id, user, may_control)."""
    state = AttachmentState(session_id=ATTACHED["session"]["id"])
    state.apply_frame(attached_frame(client_id=me[0], owner=owner, holder=holder, cols=cols, rows=rows))
    state.apply_frame(clients_frame(*clients))
    return state
