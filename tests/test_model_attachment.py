"""The attachment model: every C2 frame into state, and the pump (DT6, DT13, DT17).

Why these matter (Rule 9): the TUI renders what the core says and computes nothing itself (design
§2.2 principle 6). Each test names the clause it guards — a receiver's rule (V1, V2, V3, V5), a
consumer MUST (session-attach CR1–CR5, typing-ownership CR1–CR3, size-control surface rules,
session-detach 9–12, client-remove 8, session-countdown 9–11, session-list 7) — and replays the
contract's own fixtures, never a hand-written shape (DT6).

Two kinds of test, per DT6:

* `instance` fixtures (`C2/stale-flow`, `notice-changes`, `size-pinned`, `size-not-resizable`,
  `size-coalesced`, `prefix-after-restart`, `data-path-marker`, `quiet-prompt-owner-only`,
  `stream-input-refused-*`, and every server `$def` in `C2/ws-frames-shape-v0.3`): replay only
  their `valid: true` documents through `AttachmentState.apply_frame`.
* `ws` exchange fixtures (`token-handoff`, `token-owner-revoke-reassign`, `owner-takeover-by-typing`,
  `token-event-on-detach`, `clients-frame`, `handoff-refused`, `size-request-applied`, `-clamped`,
  `-not-typist`, `attach-prefix-mid-session`, `attach-prefix-modes`, `attach-ok-bytes-roundtrip`,
  `unknown-frame-ignored`): run the exchange against the in-process fixture server, capture the
  frames it sends, validate each with `tsbc`'s validator, then assert the state.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tsbc import _http, _serve, _ws
from tsbc._serve import ServeApp, StubServer
from tsbc.fixtures import load_fixtures
from tsbc.schema import Registry

from tsb_tui.model.attachment import (
    AttachmentState,
)

CONTRACT = Path(__file__).resolve().parents[1] / "program" / "contract"
WS_FRAMES = "schemas/ws-frames.schema.json"
REGISTRY = Registry.from_tree(CONTRACT)

# The inputs the fixtures declare; every value is a fixed synthetic constant the fixture server
# seeds (tsbc._serve), never a secret.
INPUTS = {
    "user": _serve.USER, "password": _serve.PASSWORD, "target_id": _serve.TARGET,
    "peer_user": _serve.PEER, "peer_password": _serve.PASSWORD,
    "viewer_user": _serve.VIEWER, "viewer_password": _serve.PASSWORD,
}


def _fixture_docs() -> dict[str, dict]:
    loaded, errors = load_fixtures(CONTRACT)
    assert not errors, f"the contract tree does not load: {errors}"
    return {f"{fx.seam}/{fx.name}": fx.doc for fx in loaded}


FIXTURE_DOCS = _fixture_docs()


def _server_types() -> set[str]:
    types = set()
    for branch in REGISTRY.docs[WS_FRAMES]["$defs"]["server"]["oneOf"]:
        target, _ = REGISTRY.resolve(branch["$ref"], WS_FRAMES)
        types.add(target["properties"]["t"]["const"])
    return types


SERVER_TYPES = _server_types()


def _instance_docs(name: str, *, server_only: bool = False) -> list[dict]:
    """The `valid: true` documents of one instance fixture, in order (DT6). An instance fixture may
    also carry client frames (`attach`, `handoff`, `stale_answer`); `server_only` drops those,
    because only server frames are applied to the state."""
    doc = FIXTURE_DOCS[f"C2/{name}"]
    assert doc["kind"] == "instance", f"C2/{name} is a {doc['kind']} fixture"
    docs = [i["doc"] for i in doc["case"]["instances"]
            if i["valid"] and isinstance(i.get("doc"), dict)]
    if server_only:
        docs = [d for d in docs if d.get("t") in SERVER_TYPES]
    return docs


def _validate(frame: dict) -> None:
    """A frame the fixture server sends must be valid against the schema (DT6)."""
    errors = REGISTRY.validate(frame, f"{WS_FRAMES}#/$defs/server")
    assert not errors, f"server frame {frame.get('t')} breaks the schema: {errors[0]}"


def _state(client_id: str = "fixture-client-1", user: str = "fixture-user") -> AttachmentState:
    return AttachmentState(client_id, user=user)


def _apply(state: AttachmentState, frame: dict) -> None:
    _validate(frame)
    state.apply_frame(frame)


# ---- every server $def in the C2 instance fixtures is applied ------------------------------------


def _all_server_instances() -> list[dict]:
    """Every `valid: true` server frame from every C2 `instance` fixture, each tagged with its
    fixture so a failure names it. `C2/ws-frames-shape-v0.3` carries most; the older shape fixtures
    and the frame-specific ones carry the rest (DT6)."""
    out: list[dict] = []
    for key, fx in FIXTURE_DOCS.items():
        if fx.get("seam") != "C2" or fx.get("kind") != "instance":
            continue
        for instance in fx["case"]["instances"]:
            if not instance.get("valid") or not isinstance(instance.get("doc"), dict):
                continue
            if instance["doc"].get("t") in SERVER_TYPES:
                out.append(instance["doc"])
    # De-duplicate by (t, json) so a type repeated across fixtures is applied once.
    seen: set[tuple] = set()
    unique: list[dict] = []
    for doc in out:
        key = (doc.get("t"), json.dumps(doc, sort_keys=True))
        if key not in seen:
            seen.add(key)
            unique.append(doc)
    return unique


@pytest.mark.parametrize("frame", _all_server_instances(), ids=lambda f: f["t"])
def test_every_server_frame_type_in_an_instance_fixture_is_applied(frame: dict) -> None:
    """Every server `$def` in `ws-frames.schema.json` reaches the state without error, replayed
    from the C2 instance fixtures' own `valid: true` documents (DT6; the phase's acceptance).

    Why: a frame type the model does not know is ignored (V2), but a type it knows and mishandles
    would corrupt the state the status row, the rail and the dialogs all render. The contract's own
    instances are the wire shapes, so applying them proves none is dropped.
    """
    state = _state()
    _apply(state, frame)
    # The apply must not raise; a frame that changes nothing (an unknown field, a no-op) is fine.
    assert isinstance(state.snapshot(), dict)


def test_every_server_frame_type_has_an_applied_instance() -> None:
    """Every server `$def` in `ws-frames.schema.json` has a `valid: true` instance in some C2
    instance fixture, and the parametrized test above applies each (DT6; acceptance).

    Why: the phase's acceptance is "every server `$def` in ws-frames.schema.json has a test that
    applies it". `C2/ws-frames-shape-v0.3` carries most; the older shape fixtures and the
    frame-specific fixtures (`notice-changes`, `quiet-prompt-owner-only`, `data-path-marker`,
    `stale-flow`, `stream-input-refused-*`, `token-auto-release`, `token-escrow-after-restart`)
    carry the rest. This test proves the union covers every defined type.
    """
    defined = SERVER_TYPES
    shown: set[str] = set()
    for fx in FIXTURE_DOCS.values():
        if fx.get("seam") != "C2" or fx.get("kind") != "instance":
            continue
        for instance in fx["case"]["instances"]:
            doc = instance.get("doc")
            if instance.get("valid") and isinstance(doc, dict) and doc.get("t") in defined:
                shown.add(doc["t"])
    assert defined == shown, f"no valid instance for: {defined - shown}"


# ---- the instance fixtures, replayed --------------------------------------------------------------


def test_stale_flow_shapes_reach_the_state() -> None:
    """`stale` pending/chosen, `stale_prompt`, `stale_answer`, `request_refused`, `input_refused`
    `stale` all apply (stale-screen steps 3, 4, 11, 22; C2/stale-flow).

    Why: the holder must see the prompt and the choice; every client must see pending and chosen. A
    surface that dropped `stale` `pending` would let the operator type into a screen that may be
    wrong.
    """
    state = _state()
    for doc in _instance_docs("stale-flow", server_only=True):
        _apply(state, doc)
    # The last stale is `chosen`, so the pending prompt has been resolved.
    assert state.stale_state == "chosen"
    assert state.stale_cause == "gap"
    assert state.stale_choice == "wiggle"
    assert state.last_request_refused == {"request": "stale_answer", "reason": "not_allowed"}
    assert state.last_input_refused == "stale"


def test_a_stale_pending_keeps_the_prompt_and_the_gap() -> None:
    """A `stale` `pending` with cause `gap` keeps `gap_seq`; `grid_lost` carries none
    (stale-screen step 2; C2/stale-flow).

    Why: `gap_seq` names where the loss began, and `grid_lost` has no lost seq to name. A surface
    that required `gap_seq` on `grid_lost` would fail on the core's own frame.
    """
    docs = _instance_docs("stale-flow", server_only=True)
    gap = next(d for d in docs if d["t"] == "stale" and d["cause"] == "gap")
    state = _state()
    _apply(state, gap)
    assert state.stale_state == "pending" and state.stale_gap_seq == 41

    grid_lost = next(d for d in docs if d["t"] == "stale" and d["cause"] == "grid_lost")
    state2 = _state()
    _apply(state2, grid_lost)
    assert state2.stale_state == "pending" and state2.stale_gap_seq is None


def test_a_token_move_voids_the_outstanding_stale_prompt() -> None:
    """Stale-screen step 8: a token move voids the prompt and the client that receives the token is asked
    anew. A prompt id kept across a move would be answered `not_allowed` or `not_pending`, so it is dropped
    with the move and only the frame that follows can bring a new one (C2/stale-flow's own frames)."""
    docs = _instance_docs("stale-flow", server_only=True)
    state = _state()
    for doc in (next(d for d in docs if d["t"] == "stale" and d["state"] == "pending"),
                next(d for d in docs if d["t"] == "stale_prompt")):
        _apply(state, doc)
    assert state.stale_prompt is not None
    _apply(state, {"t": "token", "v": 1, "kind": "control", "reason": "handoff",
                   "holder": {"client_id": "other", "user": "someone"}})
    assert state.stale_prompt is None and state.stale_state == "pending"


def test_a_new_attachment_starts_with_no_stale_state_from_the_dropped_one() -> None:
    """Session-attach step 16, stale-screen step 5: a (re)attach is a new attachment, and the core tells it
    again only a choice that is still pending (`stale`, then `stale_prompt`, after `clients`). A choice made
    while this client was detached therefore never reaches it as a frame, so a `pending` kept from the
    dropped attachment would stay for good and DT4 (b) would silence every reply, focus report and mouse
    report of a client whose screen is fine. `attached` resets the stale state, and the frames that follow
    set it again when it is still true.

    Frames: `C2/stale-flow`'s own valid `stale` and `stale_prompt`, then an `attached` built in the test
    from the contract's shape and validated (DT6)."""
    docs = _instance_docs("stale-flow", server_only=True)
    pending = next(d for d in docs if d["t"] == "stale" and d["state"] == "pending")
    chosen = next(d for d in docs if d["t"] == "stale" and d["state"] == "chosen")
    prompt = next(d for d in docs if d["t"] == "stale_prompt")

    state = _state()
    _apply(state, pending)
    _apply(state, prompt)
    assert state.stale_state == "pending" and state.stale_prompt is not None

    _apply(state, validated_attached("fixture-client-2"))
    assert (state.stale_state, state.stale_cause, state.stale_gap_seq, state.stale_prompt) == (
        None, None, None, None), "a stale choice made while detached must not survive the reattach"

    _apply(state, pending)  # still pending: the core says so again after `clients`
    assert state.stale_state == "pending"

    _apply(state, chosen)
    assert state.stale_choice == "wiggle"
    _apply(state, validated_attached("fixture-client-3"))
    assert state.stale_choice is None and state.stale_state is None


def test_a_new_attachment_starts_with_no_data_path_from_the_dropped_one() -> None:
    """`attached` resets the data-path state, so a recovered session does not still show "restoring".

    Why (Qodo #229, ruling 2; data-socket-redial steps 6, 8): `data_path` is attachment-local — a
    `lost`/`restored` marker belongs to the connection the previous attachment used. A reattach
    starts a fresh data path, and only a new marker (not carried over) says otherwise. Keeping the
    old value left the surface telling the operator input was not reaching the target when it was.

    Frames: `C2/data-path-marker`'s own markers, then an `attached` built in the test from the
    contract's shape and validated (DT6).
    """
    state = _state()
    for doc in _instance_docs("data-path-marker", server_only=True):
        _apply(state, doc)
    assert state.data_path == "restored" and state.data_path_at is not None

    _apply(state, validated_attached("fixture-client-2"))
    assert (state.data_path, state.data_path_at) == (None, None), (
        "a data-path marker from the dropped attachment must not survive the reattach"
    )


def test_a_stale_prompt_carries_the_four_fixed_choices() -> None:
    """The `stale_prompt` text and its four choices are the contract's, shown as sent
    (stale-screen rule 18; C2/stale-flow).

    Why: no surface may soften the warning; the choice text is fixed by the contract, so a surface
    cannot relabel a choice.
    """
    state = _state()
    for doc in _instance_docs("stale-flow", server_only=True):
        if doc["t"] == "stale_prompt":
            _apply(state, doc)
    assert state.stale_prompt is not None
    assert state.stale_prompt["text"] == "TSB: lost output - the screen may be wrong. Choose:"
    assert [c["choice"] for c in state.stale_prompt["choices"]] == [
        "wiggle", "ctrl_l", "continue", "fresh_session"]
    assert state.stale_prompt["choices"][0]["text"] == "Redraw the screen (no guarantee)"


def test_notice_changes_update_the_countdown() -> None:
    """A `notice` updates the countdown's three fields, and `ending` false withdraws it
    (session-countdown 9–11; C2/notice-changes).

    Why: the countdown comes only from the core (rule 9); a surface that kept a stale `ending` true
    would show a warning that has cleared.
    """
    state = _state()
    for doc in _instance_docs("notice-changes", server_only=True):
        _apply(state, doc)
    assert state.ends_at == "2026-09-30T12:00:00Z"
    assert state.ends_by == "idle"
    assert state.ending is False


def test_size_answers_carry_the_size_in_effect() -> None:
    """`size` `pinned`, `not_resizable`, `applied`, `clamped_floor`, `clamped_ceiling` and
    `not_typist` each set the size in effect and the reason (size-control steps 2, 3, 11; C2/size-*).

    Why: every answer carries the size that stands, a refusal included; the surface shows the real
    size and the reason, and must not re-ask after a clamp.
    """
    for name in ("size-pinned", "size-not-resizable", "size-coalesced"):
        state = _state()
        for doc in _instance_docs(name):
            _apply(state, doc)
        assert state.size_cols and state.size_rows
        assert state.size_reason in {
            "applied", "clamped_floor", "clamped_ceiling", "pinned", "not_resizable", "not_typist"}


def test_prefix_carries_the_dumps_geometry() -> None:
    """`prefix` sets the dump's geometry and through_seq (session-attach R7, R8;
    C2/prefix-after-restart).

    Why: after `prefix` the next binary is the dump, rendered at `prefix.cols` × `prefix.rows`
    (CR3). A surface that kept the attach viewport would show the wrong screen.
    """
    state = _state()
    docs = _instance_docs("prefix-after-restart", server_only=True)
    for doc in docs:
        _apply(state, doc)
    # The last prefix in the fixture is the dump's geometry that stands.
    assert state.prefix == docs[-1]
    assert state.prefix["cols"] == 80 and state.prefix["rows"] == 24
    # The middle one carries through_seq and scrollback.
    assert docs[1]["through_seq"] == 42
    assert docs[1]["scrollback"] == {"first_seq": 11, "bytes": 2048}


def test_data_path_markers_are_remembered() -> None:
    """`data_path` `lost` and `restored` update the data path (data-socket-redial steps 6, 8;
    C2/data-path-marker).

    Why: the surface shows that input may not reach the target; a surface that dropped `restored`
    would leave the operator believing the path is still lost.
    """
    state = _state()
    for doc in _instance_docs("data-path-marker", server_only=True):
        _apply(state, doc)
    assert state.data_path == "restored"


def test_quiet_prompt_is_remembered_with_its_prompt_id() -> None:
    """`quiet_prompt` sets the outstanding prompt with its unguessable id (quiet-prompt steps 6, 7;
    C2/quiet-prompt-owner-only).

    Why: the answer must carry the `prompt_id` it received, in exactly the closed shape, so no
    answer can be forged (quiet-prompt 9).
    """
    state = _state()
    for doc in _instance_docs("quiet-prompt-owner-only", server_only=True):
        if doc["t"] == "quiet_prompt":
            _apply(state, doc)
    assert state.quiet_prompt is not None
    assert state.quiet_prompt["code"] == "quiet_prompt.kill"
    assert state.quiet_prompt["text"] == "Kill the session?"
    assert len(state.quiet_prompt["prompt_id"]) >= 22  # the ticket's shape (common `ticket`)


def test_input_refused_reasons_are_remembered() -> None:
    """`input_refused` `replaying` and `unreachable` are remembered (byte-relay's table;
    C2/stream-input-refused-*).

    Why: no keystroke vanishes without a signal; the surface shows why its input was refused.
    """
    for name, reason in (("stream-input-refused-replaying", "replaying"),
                         ("stream-input-refused-unreachable", "unreachable")):
        state = _state()
        for doc in _instance_docs(name):
            _apply(state, doc)
        assert state.last_input_refused == reason


# ---- receiver rules (V1, V2, V5) ------------------------------------------------------------------


def test_an_unknown_frame_type_changes_nothing() -> None:
    """A `t` the model does not know is ignored (V2; C2/unknown-frame-ignored).

    Why: a later tag adds frame types without breaking an earlier peer (V1); a surface that raised
    on an unknown `t` would crash on the next contract bump.
    """
    state = _state()
    before = state.snapshot()
    state.apply_frame({"t": "x-future-frame", "v": 1, "anything": True})
    assert state.snapshot() == before


def test_a_known_type_with_an_unknown_version_changes_nothing() -> None:
    """A known `t` with an unknown `v` is ignored exactly as an unknown `t` (V1).

    Why: the attach first-frame rule wins (4000); a surface must not apply a frame whose version it
    does not speak.
    """
    state = _state()
    before = state.snapshot()
    state.apply_frame({"t": "size", "v": 2, "session_id": "fixture-session-a",
                       "cols": 1, "rows": 1, "reason": "applied"})
    assert state.snapshot() == before


def test_an_unknown_field_on_a_known_frame_is_ignored() -> None:
    """A field the model does not know on a known frame is ignored (V2).

    Why: a sender emits exactly the shape its tag defines and a receiver ignores fields it does not
    know, so a later tag's optional field never breaks an earlier peer.
    """
    state = _state()
    state.apply_frame({"t": "size", "v": 1, "session_id": "fixture-session-a",
                       "cols": 120, "rows": 40, "reason": "applied", "future_field": True})
    assert state.size_cols == 120 and state.size_rows == 40


def test_an_unknown_size_reason_still_reads_cols_rows() -> None:
    """A `size` reason the model does not know reads `cols`/`rows` as the size in effect (V5).

    Why: the registry's rule for the size answer; a surface that refused to render on an unknown
    reason would leave the operator with no size at all.
    """
    state = _state()
    state.apply_frame({"t": "size", "v": 1, "session_id": "fixture-session-a",
                       "cols": 132, "rows": 43, "reason": "below_threshold"})
    assert state.size_cols == 132 and state.size_rows == 43
    assert state.size_reason == "below_threshold"


def test_an_unknown_token_reason_still_reads_the_holder() -> None:
    """A `token` reason the model does not know reads `holder` (V5; typing-ownership CR1).

    Why: the holder is the fact, the reason is the cause; a surface that ignored a token frame with
    a new reason would leave the keyboard pill wrong.
    """
    state = _state()  # client_id fixture-client-1, not the holder the frame names
    state.apply_frame({"t": "token", "v": 1, "kind": "control",
                       "holder": {"client_id": "fixture-client-9", "user": "fixture-peer"},
                       "reason": "some_future_reason"})
    assert state.holder_client_id == "fixture-client-9"
    assert state.holds_keyboard is False
    assert state.may_type is False  # not the holder, and owner is unknown so not an owner's client


def test_an_unknown_ends_by_still_shows_ends_at() -> None:
    """A `notice` `ends_by` the model does not know shows `ends_at` (V5; session-countdown 10).

    Why: the countdown's time is the fact; which timer it is can grow.
    """
    state = _state()
    state.apply_frame({"t": "notice", "v": 1, "session_id": "fixture-session-a",
                       "ends_at": "2026-09-29T01:06:00Z", "ends_by": "some_future_timer",
                       "ending": True})
    assert state.ends_at == "2026-09-29T01:06:00Z"
    assert state.ending is True


# ---- the ws exchange fixtures, against the in-process fixture server ------------------------------


def _sub(node: Any, value: dict[str, Any]) -> Any:
    """Replace `${name}` from the saved vars and the fixture's inputs. A string that is exactly one
    placeholder takes the value's own type (as tsbc's own runner does)."""
    if isinstance(node, str):
        if node.startswith("${") and node.endswith("}"):
            return value[node[2:-1]]
        out, i = "", 0
        while i < len(node):
            if node.startswith("${", i):
                end = node.index("}", i)
                out += str(value[node[i + 2:end]])
                i = end + 1
            else:
                out += node[i]
                i += 1
        return out
    if isinstance(node, dict):
        return {k: _sub(v, value) for k, v in node.items()}
    if isinstance(node, list):
        return [_sub(v, value) for v in node]
    return node


def _pointer_get(data: Any, pointer: str) -> Any:
    node = data
    for token in pointer.lstrip("/").split("/"):
        node = node[int(token)] if isinstance(node, list) and token.isdigit() else node[token]
    return node


def _match(frame: dict, m: dict, value: dict[str, Any]) -> bool:
    try:
        actual = _pointer_get(frame, m["pointer"])
    except (KeyError, IndexError, TypeError):
        return "absent" in m
    if "absent" in m:
        return False
    if "present" in m:
        return True
    if "eq" in m:
        return actual == _sub(m["eq"], value)
    if "ne" in m:
        return actual != _sub(m["ne"], value)
    if "some" in m:
        want = _sub(m["some"], value)
        return isinstance(actual, list) and any(
            all(_pointer_get(el, p) == v for p, v in want.items()) for el in actual)
    if "none" in m:
        want = _sub(m["none"], value)
        return not (isinstance(actual, list) and any(
            all(_pointer_get(el, p) == v for p, v in want.items()) for el in actual))
    return False


class _Exchange:
    """Plays one ws fixture's own steps against the in-process fixture server, over the blocking
    `tsbc._ws` transport, and captures the server frames each socket saw — validated with tsbc's
    validator. The fixture's steps decide what is sent, so the frames are the core's, never
    hand-written (DT6).

    A reader thread per socket drains frames continuously, because a fixture's steps interleave
    (`ws_expect` on one socket while another sends); a single synchronous reader would drop a frame
    that arrives before its `ws_expect` and block on one it has already seen."""

    def __init__(self, name: str) -> None:
        self.doc = FIXTURE_DOCS[f"C2/{name}"]
        self.vars: dict[str, Any] = {"nonce": "fixturenonce", **INPUTS}
        self._frames: dict[str, list[dict]] = {}
        self._output: dict[str, bytearray] = {}
        self._cursors: dict[str, int] = {}
        self._socks: dict[str, _ws.WebSocket] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._clients: dict[str, _http.HttpClient] = {}
        self._url = ""

    def frames(self, sid: str) -> list[dict]:
        with self._lock:
            return list(self._frames[sid])

    def output(self, sid: str) -> bytes:
        with self._lock:
            return bytes(self._output[sid])

    def all_frames(self) -> dict[str, list[dict]]:
        with self._lock:
            return {sid: list(frames) for sid, frames in self._frames.items()}

    def run(self) -> _Exchange:
        with StubServer(ServeApp()) as url:
            self._url = url
            for step in self.doc["case"]["steps"]:
                self._step(step)
            for sid, sock in list(self._socks.items()):
                self._stop_reader(sid)
                sock.close()
        return self

    # -- reader threads --

    def _start_reader(self, sid: str) -> None:
        self._frames[sid] = []
        self._output[sid] = bytearray()
        self._cursors[sid] = 0
        thread = threading.Thread(target=self._read, args=(sid,), daemon=True)
        self._threads[sid] = thread
        thread.start()

    def _stop_reader(self, sid: str) -> None:
        thread = self._threads.pop(sid, None)
        if thread is not None:
            thread.join(timeout=2.0)

    def _read(self, sid: str) -> None:
        sock = self._socks[sid]
        while True:
            try:
                message = sock.recv(0.5)
            except TimeoutError:
                continue
            except Exception:  # noqa: BLE001 — the socket closed under the reader
                return
            if message.kind == "close":
                return
            if message.kind == "binary":
                with self._lock:
                    self._output[sid] += message.data
                continue
            try:
                frame = json.loads(message.data)
            except ValueError:
                continue
            _validate(frame)
            with self._lock:
                self._frames[sid].append(frame)

    def _wait_for(self, sid: str, predicate: Callable[[dict], bool], deadline: float) -> dict:
        """The next frame at or after this socket's cursor that matches `predicate`; the cursor
        advances past it, so two expectations of the same frame type consume two frames."""
        while time.monotonic() < deadline:
            with self._lock:
                frames = list(self._frames[sid])
                cursor = self._cursors[sid]
            for index in range(cursor, len(frames)):
                if predicate(frames[index]):
                    with self._lock:
                        self._cursors[sid] = index + 1
                    return frames[index]
            time.sleep(0.01)
        raise AssertionError(f"{sid}: the expected frame never arrived before the deadline")

    def _wait_output(self, sid: str, needle: bytes, deadline: float) -> None:
        while time.monotonic() < deadline:
            if needle in self.output(sid):
                return
            time.sleep(0.01)
        raise AssertionError(f"{sid}: output {needle!r} never arrived")

    def _client(self, who: str) -> _http.HttpClient:
        if who not in self._clients:
            client = _http.HttpClient(self._url, timeout=5.0)
            user = {"main": _serve.USER, "peer": _serve.PEER, "fresh": _serve.VIEWER}[who]
            resp = client.request("POST", "/auth/login",
                                  body={"username": user, "password": _serve.PASSWORD})
            assert resp.status == 200, f"login as {who} failed: {resp.status}"
            self._clients[who] = client
        return self._clients[who]

    def _step(self, step: dict) -> None:
        if "http" in step:
            self._http(step)
        elif "ws_open" in step:
            parts = self._url.split("://", 1)
            self._socks[step["ws_open"]] = _ws.WebSocket.connect(
                "ws://" + parts[1] + step["path"], timeout=5.0)
            self._start_reader(step["ws_open"])
        elif "ws_send" in step:
            self._ws_send(step["ws_send"], step)
        elif "ws_expect" in step:
            self._ws_expect(step["ws_expect"], step)
        elif "ws_close" in step:
            self._stop_reader(step["ws_close"])
            self._socks[step["ws_close"]].close()
        elif "wait" in step:
            time.sleep(float(step["wait"]))

    def _http(self, step: dict) -> None:
        req = step["http"]
        client = self._client(req.get("as", "main"))
        method, path = req["method"], _sub(req["path"], self.vars)
        body = _sub(req["body"], self.vars) if "body" in req else None
        resp = client.request(method, path, body=body)
        data = resp.json() if resp.body else None
        for name, pointer in step.get("save", {}).items():
            self.vars[name] = _pointer_get(data, pointer)

    def _ws_send(self, sid: str, step: dict) -> None:
        sock = self._socks[sid]
        if "frame" in step:
            sock.send_text(json.dumps(_sub(step["frame"], self.vars)))
        elif "bytes" in step:
            sock.send_binary(str(_sub(step["bytes"], self.vars)).encode("utf-8"))

    def _ws_expect(self, sid: str, step: dict) -> None:
        deadline = time.monotonic() + 5.0
        if "output_contains" in step:
            needle = str(_sub(step["output_contains"], self.vars)).encode("utf-8")
            self._wait_output(sid, needle, deadline)
            return
        if "prefix" in step:
            self._wait_for(sid, lambda f: f.get("t") == "prefix", deadline)
            spec = step["prefix"]
            for needle in spec.get("binary_contains", []):
                assert str(_sub(needle, self.vars)).encode("utf-8") in self.output(sid), \
                    f"{sid}: the dump lacks {needle!r}"
            for needle in spec.get("binary_lacks", []):
                assert str(_sub(needle, self.vars)).encode("utf-8") not in self.output(sid), \
                    f"{sid}: the dump has {needle!r}"
            return
        spec = step["frame"]
        matches = spec.get("match", [])
        # Save before matching, as tsbc's own runner does (_exchange.py ws_step): a fixture's match
        # may reference a var the same frame's `save` names (for example an `attached` frame whose
        # `control_holder/client_id` equals the `client_id` it carries).
        frame = self._wait_for(sid, lambda f: f.get("t") == matches[0]["eq"] if matches else True,
                               deadline)
        for name, pointer in spec.get("save", {}).items():
            self.vars[name] = _pointer_get(frame, pointer)
        assert all(_match(frame, m, self.vars) for m in matches), \
            f"{sid}: frame {frame.get('t')} did not match {matches}"


# Not attach-prefix-modes: since contract v0.4.3 the target prints the modes through `printf`, which the
# fixture server's echo cannot run (the contract lists it in fixtures/C2/.serve-exempt); the post-merge loop
# grades it live against a lab target.
WS_FIXTURES = (
    "token-handoff", "token-owner-revoke-reassign", "owner-takeover-by-typing",
    "token-event-on-detach", "clients-frame", "handoff-refused", "size-request-applied",
    "size-request-clamped", "size-request-not-typist", "attach-prefix-mid-session",
    "attach-ok-bytes-roundtrip", "unknown-frame-ignored",
)


@pytest.mark.parametrize("name", WS_FIXTURES)
def test_the_ws_exchange_fixtures_apply_against_the_state(name: str) -> None:
    """Each ws exchange fixture's server frames apply to the state without error (DT6).

    Why: the fixture server is the contract's own provider; a frame it sends that the model drops
    or corrupts is a real defect, not a test artifact. The exchange is driven by the fixture's own
    steps, so the frames are the core's, never hand-written.
    """
    exchange = _Exchange(name).run()
    all_frames = exchange.all_frames()
    assert all_frames, f"{name}: the fixture server sent no frames"
    for frames in all_frames.values():
        state = _state()
        for frame in frames:
            state.apply_frame(frame)
        assert state.client_id is not None


def test_token_handoff_sets_the_new_holder_and_view_only() -> None:
    """After `handoff`, the token frame's holder is the new holder; a non-holder's `may_type` is
    false and an owner's client's is true (typing-ownership CR1; C2/token-handoff).

    Why: the holder is the fact every move reports; a surface that kept the old holder would let
    the wrong client believe it may type.
    """
    exchange = _Exchange("token-handoff").run()
    frames = exchange.all_frames()["a"]
    state = _state(client_id=exchange.vars["client_a"], user="fixture-user")
    for frame in frames:
        state.apply_frame(frame)
    # The fixture's handoff moved the token to p1; a later detach passed it back to a (the last
    # holder). The history records both, so the handoff is provable.
    handoffs = [t for t in state.token_history if t["reason"] == "handoff"]
    assert handoffs and handoffs[0]["holder"]["client_id"] == exchange.vars["client_p1"]
    assert state.holds_keyboard is True  # a, the owner's client, holds it at the end
    assert state.may_type is True


def test_owner_takeover_by_typing_sets_the_owner_as_holder() -> None:
    """An owner's input while not the holder moves the token to it, reason `takeover`
    (typing-ownership steps 8–9; C2/owner-takeover-by-typing).

    Why: the owner's brake; the owner must learn it holds the keyboard again.
    """
    exchange = _Exchange("owner-takeover-by-typing").run()
    state = _state(client_id=exchange.vars["client_o"], user="fixture-user")
    for frame in exchange.all_frames()["o"]:
        state.apply_frame(frame)
    assert state.holder_client_id == exchange.vars["client_o"]
    assert state.holds_keyboard is True
    reasons = [t["reason"] for t in state.token_history]
    assert "takeover" in reasons


def test_clients_frame_replaces_the_whole_set() -> None:
    """Each `clients` frame replaces the set the model held (session-detach rule 11;
    C2/clients-frame).

    Why: a surface that merged instead of replaced would keep showing a client that has left.
    """
    exchange = _Exchange("clients-frame").run()
    state = _state(client_id=exchange.vars["client_a"], user="fixture-user")
    for frame in exchange.all_frames()["a"]:
        state.apply_frame(frame)
    ids = {c["client_id"] for c in state.clients}
    assert exchange.vars["client_b"] not in ids  # b closed; the last clients frame drops it
    assert exchange.vars["client_a"] in ids


def test_handoff_refused_is_remembered() -> None:
    """`request_refused` request `handoff` records the reason (typing-ownership rule 8;
    C2/handoff-refused).

    Why: a refused hand-off is answered explicitly, never silently (D7); the dialog shows why.
    """
    exchange = _Exchange("handoff-refused").run()
    state = _state(client_id=exchange.vars["client_a"], user="fixture-user")
    for frame in exchange.all_frames()["a"]:
        state.apply_frame(frame)
    assert state.last_request_refused is not None
    assert state.last_request_refused["request"] == "handoff"


def test_size_request_not_typist_leaves_the_size() -> None:
    """A non-holder's `resize` is answered `size` `not_typist` with the size in effect
    (size-control rule 2; C2/size-request-not-typist).

    Why: the one refusal path for a size request, never `request_refused`; the size that stands is
    what the surface shows.
    """
    exchange = _Exchange("size-request-not-typist").run()
    state = _state(client_id=exchange.vars["client_v"], user="fixture-viewer")
    for frame in exchange.all_frames()["v"]:
        state.apply_frame(frame)
    assert state.size_reason == "not_typist"
    assert (state.size_cols, state.size_rows) == (120, 40)


def test_size_request_applied_sets_the_new_size() -> None:
    """The holder's in-bounds request is answered `applied` with the new size
    (size-control steps 11–12; C2/size-request-applied).

    Why: the size in effect must follow the applied request, so the grid renders at the target's
    size.
    """
    exchange = _Exchange("size-request-applied").run()
    state = _state(client_id=exchange.vars["client_a"], user="fixture-user")
    for frame in exchange.all_frames()["a"]:
        state.apply_frame(frame)
    assert state.size_reason == "applied"
    assert (state.size_cols, state.size_rows) == (132, 43)


def test_unknown_frame_ignored_keeps_the_state() -> None:
    """An unknown frame the client sends does not disturb the stream (V2;
    C2/unknown-frame-ignored).

    Why: the exchange's own unknown-frame step proves the server keeps streaming; this test proves
    the client's state is unaffected by the server's ordinary frames around it.
    """
    exchange = _Exchange("unknown-frame-ignored").run()
    state = _state(client_id=exchange.vars.get("client_id"), user="fixture-user")
    for frame in exchange.all_frames()["w"]:
        state.apply_frame(frame)
    assert state.client_id is not None


# ---- the pump: the sink's roles, handoff-in-flight, and R13's frame order -------------------------


class _FakePumpAttachment:
    """A fake `AsyncAttachment` for the pump tests: it yields a fixed message list, then closes. Like
    the real one it does not yield `attached` (`open` consumed it); it carries it as attributes."""

    def __init__(self, messages: list[Any]) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        for message in messages:
            self._queue.put_nowait(message)
        # 4010 (killed): never reattaches, so an exhausted fake stops the pump instead of looping.
        self.close_code: int | None = 4010
        self.close_reason = "fake end"
        # What `AsyncAttachment.open` leaves behind: it consumes `attached`, so the pump reads
        # these two attributes instead of a frame.
        attached = validated_attached("c1")
        self.client_id: str = attached["client_id"]
        self.session: dict = attached["session"]
        self.closed = False
        self.sent: list[bytes] = []
        self.handoffs: list[str | None] = []

    async def recv_message(self) -> Any:
        # Blocks (with a timeout) rather than returning None once exhausted, so a test can inject
        # a frame mid-stream and have the read loop apply it.
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=0.5)
        except TimeoutError:
            return None

    def push(self, message: Any) -> None:
        self._queue.put_nowait(message)

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def handoff(self, to: str | None) -> None:
        self.handoffs.append(to)

    async def resize(self, cols: int, rows: int, timeout: float | None = None) -> None:
        pass

    async def answer_stale(self, prompt_id: str, choice: str) -> None:
        pass

    async def answer_quiet_prompt(self, prompt_id: str, kill: bool) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class _FakePumpClient:
    def __init__(self, att: _FakePumpAttachment) -> None:
        self._att = att

    async def async_attach(self, session_id: str, cols: int | None = None,
                           rows: int | None = None) -> _FakePumpAttachment:
        return self._att

    def session(self, session_id: str) -> dict:
        return {"state": "detached"}


def _run_pump(messages: list[Any]) -> tuple[AttachmentState, list[tuple[str, bytes]], Any]:
    """Run the pump over a fake attachment and return (state, sink calls, pump)."""
    from tsb_tui.model.attachment import AttachmentPump

    att = _FakePumpAttachment(messages)
    sink_calls: list[tuple[str, bytes]] = []
    pump = AttachmentPump(_FakePumpClient(att), "sess-1",
                          sink=lambda role, data: sink_calls.append((role, data)))

    async def go() -> None:
        await pump.start()
        await pump.wait_closed()

    asyncio.run(go())
    return pump.state, sink_calls, pump


def validated_attached(client_id: str = "c1") -> dict:
    """A valid `attached` frame built in the test and validated before use (DT6)."""
    frame = {
        "t": "attached", "v": 1, "client_id": client_id,
        "session": {
            "id": "sess-1", "target_id": "fixture-target", "connection_id": "fixture-conn-1",
            "session_type": "shell", "state": "live", "tier": 1, "owner": "fixture-user",
            "control_holder": {"client_id": client_id, "user": "fixture-user"},
            "created_at": "2026-10-06T12:00:00Z", "term": "xterm-256color", "cols": 120, "rows": 40,
            "recording": {"active": False}, "clients": [], "resizable": True, "pinned": False,
        },
    }
    errs = REGISTRY.validate(frame, f"{WS_FRAMES}#/$defs/server")
    assert not errs, errs
    return frame


def _prefix_frame(cols: int = 120, rows: int = 40, through_seq: int = 0) -> dict:
    frame = {"t": "prefix", "v": 1, "through_seq": through_seq, "cols": cols, "rows": rows}
    errs = REGISTRY.validate(frame, f"{WS_FRAMES}#/$defs/server")
    assert not errs, errs
    return frame


def test_the_sink_sees_the_dump_once_then_output() -> None:
    """After `prefix` the next binary is the dump (role `dump`); later binaries are `output`
    (session-attach CR3; DT17), coalesced into one run by DT20.

    Why: the dump rebuilds the screen at the prefix's geometry and live bytes follow it; a surface
    that treated the dump as ordinary output would corrupt the grid on every attach. Since DT20 the
    live chunks arrive as one run — consecutive output is coalesced before it reaches the sink — so
    the sink sees the dump once and then a single output run holding both live chunks, in order.
    """
    state, sink, _ = _run_pump([
        _prefix_frame(),
        b"DUMP-BYTES",
        b"live-1",
        b"live-2",
    ])
    assert [role for role, _ in sink] == ["dump", "output"]
    assert sink[0][1] == b"DUMP-BYTES"
    # The two live chunks are coalesced into the one run, still in stream order (DT20 §3.2).
    assert sink[1][1] == b"live-1live-2"
    assert state.dump_seen is True


def test_with_no_prefix_output_follows_attached() -> None:
    """With no `prefix`, output follows `attached` directly (session-attach CR3; an E1 core).

    Why: a non-shell session, or an E1 core, sends no prefix; every binary is output, never a dump.
    Since DT20 the consecutive output chunks are coalesced into one run before the sink, so it sees
    a single output run holding both, in order.
    """
    state, sink, _ = _run_pump([
        b"output-1",
        b"output-2",
    ])
    assert [role for role, _ in sink] == ["output"]
    assert sink[0][1] == b"output-1output-2"
    assert state.dump_seen is False


@pytest.mark.parametrize("reason", ["handoff", "release", "takeover", "some_future_reason"])
def test_handoff_in_flight_is_set_by_a_sent_handoff_and_cleared_by_the_next_token(reason: str) -> None:
    """DT4 (b): sending a `handoff` sets `handoff_in_flight`; the next `token` frame clears it,
    whatever its reason.

    Why: derived input must not go out between a handoff and the token's return; the flag is what
    the input path checks. A flag cleared too early would let a stale reply take the keyboard.
    """
    from tsb_tui.model.attachment import AttachmentPump

    att = _FakePumpAttachment([])
    sink: list[tuple[str, bytes]] = []
    pump = AttachmentPump(_FakePumpClient(att), "sess-1", sink=lambda r, d: sink.append((r, d)))

    async def go() -> None:
        await pump.start()
        # Wait for `attached` to be applied.
        for _ in range(200):
            if pump.state.client_id is not None:
                break
            await asyncio.sleep(0.001)
        assert pump.state.handoff_in_flight is False
        await pump.handoff("c2")
        assert pump.state.handoff_in_flight is True
        # A token frame arrives through the pump: inject it and let the loop apply it.
        att.push({
            "t": "token", "v": 1, "kind": "control",
            "holder": {"client_id": "c1", "user": "fixture-user"}, "reason": reason})
        for _ in range(200):
            if pump.state.handoff_in_flight is False:
                break
            await asyncio.sleep(0.001)
        assert pump.state.handoff_in_flight is False
        await pump.stop()

    asyncio.run(go())


def test_token_size_and_notice_between_attached_and_clients_all_apply_and_the_latest_size_wins() -> None:
    """R13 (session-attach, G-6): where a `token`, `size` or `notice` falls between `attached` and
    `clients` is unspecified; the model applies each whatever its position, and the latest size is
    the one kept.

    Why: U2's frame order inside the attach window differs from the fixture server's (plan R11), so
    a model that required the exact order would drop a size or a token frame on the real core.
    """
    state, _, _ = _run_pump([
        _prefix_frame(cols=120, rows=40),
        {"t": "size", "v": 1, "session_id": "sess-1", "cols": 132, "rows": 43,
         "reason": "applied"},
        {"t": "token", "v": 1, "kind": "control",
         "holder": {"client_id": "c2", "user": "peer"}, "reason": "attach"},
        {"t": "notice", "v": 1, "session_id": "sess-1", "ends_at": "2026-09-29T01:06:00Z",
         "ends_by": "link_timeout", "ending": True},
        b"DUMP",
        {"t": "clients", "v": 1, "session_id": "sess-1",
         "clients": [{"client_id": "c1", "user": "fixture-user", "may_control": True}]},
    ])
    # The size frame applied even though it arrived before `clients`; the latest size is kept.
    assert (state.size_cols, state.size_rows) == (132, 43)
    assert state.holder_client_id == "c2"
    assert state.ending is True
    # The dump came after the size; the size in effect is still 132x43 for the render.
    assert state.dump_seen is True
    assert state.prefix["cols"] == 120  # the dump's own geometry is the prefix's


def test_a_size_before_the_prefix_is_kept_for_after_the_dump() -> None:
    """The latest size in effect is kept for after the dump (phase 2, order rule; R13).

    Why: a size frame may arrive between `attached` and the prefix; the grid renders the dump at the
    prefix's geometry, then takes the latest size. Losing the earlier size would leave the grid at
    the stale attach size.
    """
    state, _, _ = _run_pump([
        {"t": "size", "v": 1, "session_id": "sess-1", "cols": 132, "rows": 43,
         "reason": "applied"},
        _prefix_frame(cols=120, rows=40),
        b"DUMP",
    ])
    assert state.dump_seen is True
    assert (state.size_cols, state.size_rows) == (132, 43)


def test_the_event_log_is_bounded_by_the_knob() -> None:
    """D22, DT19: the per-attachment event log is bounded by `event_log.lines`.

    Why: a session left attached for days must not grow the log without limit; the oldest line
    drops.
    """
    from tsb_tui import config
    from tsb_tui.model.events import EventLog

    cfg = config.load(config_file=Path("/nonexistent"), environ={},
                      overrides={"size.auto_request": "true", "event_log.lines": "3"})
    log = EventLog.from_config(cfg)
    assert log.limit == 3
    for i in range(10):
        log.add("output", float(i), bytes=i)
    assert len(log) == 3
    assert [e.detail["bytes"] for e in log.all()] == [7, 8, 9]


def test_the_flags_follow_every_token_frame_whatever_its_reason() -> None:
    """After each `token` the holder, the owner's-client and may-type flags are re-derived from the
    frame alone (typing-ownership CR1; DT4 a).

    Why: a surface that kept the flags from `attached` would let a viewer type after losing the
    keyboard, or lock the owner out of the keyboard they just took back. The owner's client may
    type even when it is not the holder (the owner's typing takes the keyboard).
    """
    state = AttachmentState("me", user="fixture-user")
    attached = validated_attached("me")
    attached["session"]["owner"] = "fixture-user"
    _apply(state, attached)
    assert (state.holds_keyboard, state.is_owner_client, state.may_type) == (True, True, True)

    other = {"t": "token", "v": 1, "kind": "control", "reason": "handoff",
             "holder": {"client_id": "c9", "user": "fixture-peer"}}
    _apply(state, other)
    assert state.holder_client_id == "c9"
    assert (state.holds_keyboard, state.is_owner_client, state.may_type) == (False, True, True)

    nobody = {"t": "token", "v": 1, "kind": "control", "reason": "release", "holder": None}
    _apply(state, nobody)
    assert state.holder is None and state.holder_client_id is None
    assert state.holds_keyboard is False

    viewer = AttachmentState("v1", user="fixture-viewer")
    _apply(viewer, validated_attached("v1") | {"client_id": "v1"})
    viewer.owner = "fixture-user"
    _apply(viewer, other)
    assert (viewer.holds_keyboard, viewer.is_owner_client, viewer.may_type) == (False, False, False)


def test_each_prefix_is_followed_by_exactly_one_dump() -> None:
    """A second `prefix` (a mid-session re-sync, session-attach CR3) makes the next binary a dump
    again, and only that one.

    Why: a surface that treated only the first binary of the attachment as a dump would paint the
    second dump as live output on top of the old screen.
    """
    _, sink, _ = _run_pump([
        _prefix_frame(), b"D1", b"o1",
        _prefix_frame(through_seq=9), b"D2", b"o2",
    ])
    assert [role for role, _ in sink] == ["dump", "output", "dump", "output"]
    assert [data for _, data in sink] == [b"D1", b"o1", b"D2", b"o2"]


def test_a_real_attachment_teaches_the_state_its_client_and_holder() -> None:
    """`async_attach` consumes `attached`, so the pump must apply it from the open attachment
    (client id, holder, size); `recv_message` never yields it again.

    Why: without it the state never learns which client is this one, and the keyboard pill and the
    typing guard read a holder they cannot compare.
    """
    from stub_server import SlowChunkedApp
    from stub_server import StubServer as StubHttpServer
    from tsb_contract_client import Client

    from tsb_tui.model.attachment import AttachmentPump

    async def go(url: str) -> AttachmentPump:
        client = Client(url)
        client.login("fixture-user", "fixture-password")
        sid = client.create_session("stub-target", "new", "xterm-256color", 80, 24)["session"]["id"]
        pump = AttachmentPump(client, sid, sink=lambda *_: None)
        await pump.start()
        await pump.stop()
        return pump

    with StubHttpServer(SlowChunkedApp(delay_s=0.0, max_chunks=20)) as url:
        pump = asyncio.run(go(url))
    assert pump.state.client_id == "stub-client-1"
    assert pump.state.holder_client_id == "stub-client-1"
    assert pump.state.holds_keyboard is True
    assert (pump.state.size_cols, pump.state.size_rows) == (80, 24)
