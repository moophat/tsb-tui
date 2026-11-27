"""C2 text frames the client sends and understands (schemas/ws-frames.schema.json)."""

from __future__ import annotations

import json
from typing import Any

from tsbc.schema import loads

FRAME_VERSION = 1
# Every server frame this client knows; any other `t` is ignored (a receiver's rule).
SERVER_TYPES = frozenset({"attached", "prefix", "clients", "notice", "quiet_prompt", "token",
                          "input_refused", "data_path", "size", "request_refused", "stale",
                          "stale_prompt"})


def attach_frame(ticket: str, session_id: str, cols: int | None = None,
                 rows: int | None = None) -> dict[str, Any]:
    """The first frame on the socket. cols/rows are the client's viewport at attach, a rendering
    fact, never a target size request: omit them (None) and the client declares no viewport."""
    frame: dict[str, Any] = {"t": "attach", "v": FRAME_VERSION, "ticket": ticket, "session_id": session_id}
    if cols is not None:
        frame["cols"] = cols
    if rows is not None:
        frame["rows"] = rows
    return frame


def resize_frame(cols: int, rows: int) -> dict[str, Any]:
    return {"t": "resize", "v": FRAME_VERSION, "cols": cols, "rows": rows}


def quiet_prompt_answer_frame(prompt_id: str, kill: bool) -> dict[str, Any]:
    """The owner's answer to a quiet_prompt: kill true ends the session killed / owner."""
    return {"t": "quiet_prompt_answer", "v": FRAME_VERSION, "prompt_id": prompt_id, "kill": bool(kill)}


def handoff_frame(to: str | None) -> dict[str, Any]:
    """A handoff moves the control token (PTY §7.1). `to` names the destination client_id, or is
    None to revoke it to escrow. `to` equal to the sender's own client_id is the owner's explicit
    take-over (5.53)."""
    return {"t": "handoff", "v": FRAME_VERSION, "to": to}


def stale_answer_frame(prompt_id: str, choice: str) -> dict[str, Any]:
    """The holder's answer to a stale_prompt (choice: schemas/ws-frames.schema.json#/$defs/stale_choice)."""
    return {"t": "stale_answer", "v": FRAME_VERSION, "prompt_id": prompt_id, "choice": choice}


# The frames a device-login socket carries from the server (Release B). request_refused answers a refused
# device_login_answer or device_login_hostkey_answer; any other `t` is ignored, as ever.
DEVICE_LOGIN_SERVER_TYPES = frozenset({"device_login_hostkey", "device_login_prompt", "device_login_result",
                                       "request_refused"})


def device_login_attach_frame(ticket: str, device_login_id: str) -> dict[str, Any]:
    """The first frame of a device-login socket: the 202's ticket, and the device login it is bound to."""
    return {"t": "device_login_attach", "v": FRAME_VERSION, "ticket": ticket, "device_login_id": device_login_id}


def device_login_hostkey_answer_frame(accept: bool) -> dict[str, Any]:
    return {"t": "device_login_hostkey_answer", "v": FRAME_VERSION, "accept": bool(accept)}


def device_login_answer_frame(prompt_id: int, answers: list[str]) -> dict[str, Any]:
    """The answers to one round, one string per prompt. An empty string is an answer, sent as it is."""
    return {"t": "device_login_answer", "v": FRAME_VERSION, "prompt_id": prompt_id, "answers": list(answers)}


def device_login_cancel_frame() -> dict[str, Any]:
    return {"t": "device_login_cancel", "v": FRAME_VERSION}


def encode(frame: dict[str, Any]) -> str:
    return json.dumps(frame, separators=(",", ":"))


def parse_device_login(text: str) -> dict[str, Any] | None:
    """A frame of a device-login socket this client knows, or None: a receiver ignores a `t` it does not know, and a
    known `t` with a `v` it does not speak is ignored as an unknown `t` (conventions V1, consumers). Only `v` 1, a JSON
    integer, is known: `true` and `1.0` are not 1 here."""
    try:
        frame = loads(text)
    except ValueError:
        return None
    if isinstance(frame, dict) and frame.get("t") in DEVICE_LOGIN_SERVER_TYPES:
        version = frame.get("v")
        if type(version) is int and version == FRAME_VERSION:
            return frame
    return None


def parse_server(text: str) -> dict[str, Any] | None:
    """A server frame this client knows, or None: a receiver ignores a `t` it does not know, and a
    known `t` at a version it does not speak is ignored as an unknown `t` (V1 consumer rule)."""
    try:
        frame = loads(text)  # RFC 8259: a frame carrying NaN or Infinity is not JSON
    except ValueError:
        return None
    if isinstance(frame, dict) and frame.get("t") in SERVER_TYPES:
        version = frame.get("v")
        if type(version) is int and version == FRAME_VERSION:
            return frame
    return None
