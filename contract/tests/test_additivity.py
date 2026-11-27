"""This tag is additive on contract-v0.1 (immortality §6.4, LOCKED: additive-only evolution, unknown
fields ignored, the core speaks N and N-1).

Two things must hold, or a v0.2 core cannot read a v0.1 worker and a consumer pinned to v0.1 reads
a contract that quietly changed under it:

- every instance a v0.1 fixture calls valid, and every message a v0.1 sequence fixture shows, is
  valid against this tag's schemas;
- the v0.1 fixtures themselves are unchanged, but for text that would be false in this tag's
  tarball (a description that says what "a later tag" will do, now that this tag does it) and the
  one refused case that asserted code 2 was held for this tag — which this tag defines.

`v01-fixture-digests.json` is the record of the v0.1 tree, taken from the contract-v0.1 tag: a
sha256 per fixture file (two parallel lists), with exactly the parts listed in TEXT_ONLY left out.

This tag is additive on contract-v0.2.1 too (R1, R2): every v0.2.1 valid instance and message stays
valid, and the v0.2.1 fixtures change only in the text the plan's § v0.2 fixtures changed table
names — a `graded_by` path re-pointed at the test that grades it, or a `description` that "a later
tag" made false. `v02-fixture-digests.json` is the record of the v0.2.1 tree, taken from the
contract-v0.2.1 tag in the same shape, with `V02_TEXT_ONLY` left out.

This tag is additive on contract-v0.5.0 (R1, R2): every v0.5.0 valid instance and message stays valid,
and the v0.5.0 fixtures change only in the two texts this release makes false — serial being deferred, so
a telnet peer that refused NAWS is the first case to grade `not_resizable`, and what a telnet connect
carries. `v05-fixture-digests.json` is the record of the v0.5.0 tree, taken from the v0.5.0 tag in the same
shape, with `V05_TEXT_ONLY` left out. The next fix release corrects two v0.5.0 fixtures that contradicted the
contract — the page header that must be absent, and the detach event's user — and nothing else in them.
"""

import hashlib
import io
import json
import subprocess
import tarfile
import unittest
from typing import ClassVar

from tsbc import _ipc
from tsbc.schema import Registry

from . import CONTRACT

FIXTURES = CONTRACT / "fixtures"
_RAW = json.loads((CONTRACT / "tests" / "v01-fixture-digests.json").read_text(encoding="utf-8"))
RECORD = dict(zip(_RAW["files"], _RAW["sha256"], strict=True))
# What this tag may change in a v0.1 fixture, and nothing else.
TEXT_ONLY = {
    "C3/data-frame-shape.json": ("description", "notes"),   # "bit 1 is reserved at this tag"
    "C5/manifest-closed-v0.1.json": ("description",),       # "the version marker the next tag introduces"
    # code 2 "held for contract-v0.2's gap marker"; and the refused case that used code 7 as a code no tag had
    # defined, which the login answer now is: it used 8 until TSB's own prompt became code 8, and uses 10 (invalid-3's sidecar_hex and problem, nothing else of it)
    "C8/end-marker-killed.json": ("description", "invalid-4", "invalid-3.sidecar_hex", "invalid-3.problem"),
    "C2/unknown-frame-ignored.json": ("description",),      # "the attach prefix at v0.3": now past tense
    # the telnet instance's note: a connect carries no user or credential, and needs term, cols and rows;
    # and the file surface is a later release's, not contract-v0.6's
    "C3/control-message-shape.json": ("notes",),
    "C3/open-sftp.json": ("description",),
}

_V02_RAW = json.loads((CONTRACT / "tests" / "v02-fixture-digests.json").read_text(encoding="utf-8"))
V02_RECORD = dict(zip(_V02_RAW["files"], _V02_RAW["sha256"], strict=True))
# What this tag may change in a v0.2.1 fixture, and nothing else (plan.md § v0.2 fixtures changed).
V02_TEXT_ONLY = {
    "C1/auth-cross-origin-403.json": ("graded_by.test",),        # #51: the grading test moved
    "C2/data-path-marker.json": ("graded_by.test",),             # the grading test moved
    "C2/token-event-on-detach.json": ("description",),           # "the explicit hand-off is contract-v0.3's": done
    "C2/unknown-frame-ignored.json": ("description",),           # "the attach prefix at v0.3": past tense
    "C3/control-socket-stays-open.json": ("graded_by.test",),    # the grading test moved
    "C3/input-refused-too-large.json": ("graded_by.test",),      # the grading test moved
    "C6/event-spooled-end.json": ("description",),               # an orphan end a present core is told of on channel_closed by: worker is stamped after its start
    "C6/no-claim-token-field.json": ("description",),            # snapshot* "lives in the grid_snapshots bucket"
    # the grading test moved; the restart's write of the seq and the count is now the realign before
    # hello: with a grid snapshot, or record-only for a session with none
    "C6/out-bytes-committed-after-reopen.json": ("graded_by.test", "description"),
    "C8/input-recorded-before-sent.json": ("graded_by.test",),   # the grading test moved
    # "no recorder hold rule": the core now appends nothing after a handshake's gap marker until its commit
    "C8/gap-after-overflow.json": ("description",),
    # "last_committed_seq is 6" holds when the marker's commit landed: said so
    "C8/gap-twice-across-restart.json": ("description",),
    # the telnet instance's note: a connect carries no user or credential, and needs term, cols and rows;
    # and the file surface is a later release's, not contract-v0.6's
    "C3/control-message-shape.json": ("notes",),
    "C3/open-sftp.json": ("description",),
    "C3/reattach-non-pty.json": ("description",),
    # the refused case that used code 7 as a code no tag had defined: the login answer is code 7 and TSB's own prompt code 8 now, it uses 10
    # (invalid-3's sidecar_hex and problem, nothing else of it)
    "C8/end-marker-killed.json": ("invalid-3.sidecar_hex", "invalid-3.problem"),
}


def _comparable(rel: str, raw: bytes, text_only: dict) -> bytes:
    """The bytes of a fixture a record compares, with exactly the parts in `text_only` removed, so
    a record catches a moved doc, verdict or instance while allowing text this tag made false."""
    if not rel.endswith(".json"):
        return raw
    doc = json.loads(raw)
    for part in text_only.get(rel, ()):
        if part == "description":
            doc.pop("description")
        elif part == "notes":
            for inst in doc["case"]["instances"]:
                inst.pop("note", None)
        elif part == "invalid-4":
            doc["case"]["invalid"][4] = None
        elif part.startswith("invalid-3."):
            # named fields of that one refused case, never the case: the rest of it stays in the record
            doc["case"]["invalid"][3].pop(part.removeprefix("invalid-3."))
        elif part == "page-more-header":
            doc["case"]["steps"][2]["expect"]["headers"].pop("TSB-Page-More", None)
        elif part == "detach-user":
            doc["case"]["instances"][1]["doc"].pop("user", None)
        elif part == "graded_by.test":
            for entry in doc.get("graded_by", ()):
                entry.pop("test", None)
    return json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()


def comparable(rel: str, raw: bytes) -> bytes:
    return _comparable(rel, raw, TEXT_ONLY)


def comparable_v02(rel: str, raw: bytes) -> bytes:
    return _comparable(rel, raw, V02_TEXT_ONLY)


class V01TreeUnchanged(unittest.TestCase):
    def test_every_v01_fixture_is_still_there_and_unchanged(self):
        changed = []
        for rel, digest in sorted(RECORD.items()):
            path = FIXTURES / rel
            self.assertTrue(path.is_file(), f"{rel} is gone: a consumer pinned to v0.1 grades against it")
            if hashlib.sha256(comparable(rel, path.read_bytes())).hexdigest() != digest:
                changed.append(rel)
        self.assertEqual(changed, [])
        self.assertEqual(len([r for r in RECORD if r.endswith(".json")]), 70)

    def test_the_record_catches_a_change(self):
        # A v0.1 instance whose doc or verdict moved, or a note on a fixture not listed, is caught.
        rel = "C3/data-frame-shape.json"
        doc = json.loads((FIXTURES / rel).read_bytes())
        doc["case"]["instances"][2]["valid"] = True
        raw = json.dumps(doc).encode()
        self.assertNotEqual(hashlib.sha256(comparable(rel, raw)).hexdigest(), RECORD[rel])
        rel = "C8/roundtrip.json"
        doc = json.loads((FIXTURES / rel).read_bytes())
        doc["description"] += " "
        self.assertNotEqual(hashlib.sha256(comparable(rel, json.dumps(doc).encode())).hexdigest(), RECORD[rel])


class V01InstancesValidAtV02(unittest.TestCase):
    def test_every_v01_valid_instance_and_message_validates(self):
        reg = Registry.from_tree(CONTRACT)
        instances = messages = 0
        for rel in sorted(r for r in RECORD if r.endswith(".json")):
            fx = json.loads((FIXTURES / rel).read_text(encoding="utf-8"))
            case = fx["case"]
            if fx["kind"] == "instance":
                for i, inst in enumerate(case["instances"]):
                    if inst["valid"]:
                        instances += 1
                        self.assertEqual(reg.validate(inst["doc"], case["schema"]), [], f"{rel} instance {i}")
            elif fx["kind"] == "sequence":
                for i, m in enumerate(case["messages"]):
                    if not m.get("unknown"):
                        messages += 1
                        self.assertEqual(_ipc.message_errors(reg, case["socket"], m["from"], m["msg"]), [],
                                         f"{rel} message {i}")
        self.assertGreaterEqual(instances, 49)  # the v0.1 record: 49 valid instances, 66 messages
        self.assertGreaterEqual(messages, 66)

    def test_a_field_added_to_a_v01_message_is_optional(self):
        # A v0.1 connect, create_worker, worker_created, manifest and session record carry none of
        # this tag's additions and stay valid; the same message with an addition is valid too.
        reg = Registry.from_tree(CONTRACT)
        connect = {"cmd": "connect", "target_id": "t", "protocol": "echo"}
        self.assertEqual(reg.validate(connect, "schemas/ipc-control.schema.json#/$defs/command"), [])
        created = {"event": "worker_created", "conn_id": "c", "worker_id": "w", "conn_dir": "/tmp/tsb/conn-c"}
        self.assertEqual(reg.validate(created, "schemas/supervisor.schema.json#/$defs/reply"), [])
        self.assertEqual(reg.validate({**created, "proto_version": "0.2"}, "schemas/supervisor.schema.json#/$defs/reply"), [])


class V02TreeUnchanged(unittest.TestCase):
    def test_every_v021_fixture_is_still_there_and_unchanged(self):
        changed = []
        for rel, digest in sorted(V02_RECORD.items()):
            path = FIXTURES / rel
            self.assertTrue(path.is_file(), f"{rel} is gone: a consumer pinned to v0.2.1 grades against it")
            if hashlib.sha256(comparable_v02(rel, path.read_bytes())).hexdigest() != digest:
                changed.append(rel)
        self.assertEqual(changed, [])
        self.assertEqual(len([r for r in V02_RECORD if r.endswith(".json")]), 139)
        # Only the plan's table may differ, and each entry must name a real fixture.
        for rel in V02_TEXT_ONLY:
            self.assertIn(rel, V02_RECORD, f"{rel} is not a v0.2.1 fixture")

    def test_the_record_catches_a_change(self):
        # A v0.2.1 instance whose verdict moved, or a non-table fixture's text, is caught.
        rel = "C2/token-event-on-detach.json"
        doc = json.loads((FIXTURES / rel).read_bytes())
        doc["case"]["steps"][3]["ws_expect"] = None
        self.assertNotEqual(hashlib.sha256(comparable_v02(rel, json.dumps(doc).encode())).hexdigest(),
                            V02_RECORD[rel])
        rel = "C1/session-delete.json"
        doc = json.loads((FIXTURES / rel).read_bytes())
        doc["description"] += " "
        self.assertNotEqual(hashlib.sha256(comparable_v02(rel, json.dumps(doc).encode())).hexdigest(),
                            V02_RECORD[rel])


class V02InstancesValidAtV03(unittest.TestCase):
    def test_every_v021_valid_instance_and_message_validates(self):
        reg = Registry.from_tree(CONTRACT)
        instances = messages = 0
        for rel in sorted(r for r in V02_RECORD if r.endswith(".json")):
            fx = json.loads((FIXTURES / rel).read_text(encoding="utf-8"))
            case = fx["case"]
            if fx["kind"] == "instance":
                for i, inst in enumerate(case["instances"]):
                    if inst["valid"]:
                        instances += 1
                        self.assertEqual(reg.validate(inst["doc"], case["schema"]), [], f"{rel} instance {i}")
            elif fx["kind"] == "sequence":
                for i, m in enumerate(case["messages"]):
                    if not m.get("unknown") and not m.get("invalid"):
                        messages += 1
                        self.assertEqual(_ipc.message_errors(reg, case["socket"], m["from"], m["msg"]), [],
                                         f"{rel} message {i}")
        self.assertGreaterEqual(instances, 49)
        self.assertGreaterEqual(messages, 66)

    def test_a_field_added_to_a_v021_message_is_optional(self):
        # The v0.2 additions (proto_version, timers) still validate as they did at v0.2.1: a field
        # this tag added to a v0.2 message is optional, and its absence is what an old sender sends.
        reg = Registry.from_tree(CONTRACT)
        connect = {"cmd": "connect", "target_id": "t", "protocol": "echo",
                   "timers": {"heartbeat_interval": 1000, "heartbeat_missed": 3,
                              "keepalive_interval": 0, "keepalive_count_max": 3,
                              "connection_linger": 0, "orphan_timeout": 60000,
                              "idle": 60000, "unattended": 60000},
                   "buffer_bytes": 65536, "input_frame_max": 65536}
        self.assertEqual(reg.validate(connect, "schemas/ipc-control.schema.json#/$defs/command"), [])
        # keepalive_probe_interval is this tag's addition and is optional.
        with_probe = {**connect, "timers": {**connect["timers"], "keepalive_probe_interval": 500}}
        self.assertEqual(reg.validate(with_probe, "schemas/ipc-control.schema.json#/$defs/command"), [])


_V05_RAW = json.loads((CONTRACT / "tests" / "v05-fixture-digests.json").read_text(encoding="utf-8"))
V05_RECORD = dict(zip(_V05_RAW["files"], _V05_RAW["sha256"], strict=True))
# What this tag may change in a v0.5.0 fixture, and nothing else. Two texts: serial is deferred, so the
# first case to grade `not_resizable` is a telnet peer that refused NAWS; and a telnet connect carries
# no user or credential and needs term, cols and rows, which is what this tag defines. The next fix release
# corrects two v0.5.0 fixtures that contradicted the contract — the page header that must be absent, and the
# detach event's user — and nothing else in them.
V05_TEXT_ONLY = {
    "C2/size-not-resizable.json": ("description",),
    "C3/control-message-shape.json": ("notes",),
    # the file surface is a later release's, not this tag's named successor: the stage name v0.6 was E3's number
    "C3/open-sftp.json": ("description",),
    "C3/reattach-non-pty.json": ("description",),
    # TSB-Page-More is required absent: an explicit false end-of-history is never sent
    "C1/output-page-ground-start.json": ("page-more-header",),
    # the detach event's user is the departing holder
    "C6/event-vocabulary-v0.3.json": ("detach-user",),
    # the refused case that used code 7 as a code no tag had defined: the login answer is code 7 and TSB's own prompt code 8 now, it uses 10
    # (invalid-3's sidecar_hex and problem, nothing else of it)
    "C8/end-marker-killed.json": ("invalid-3.sidecar_hex", "invalid-3.problem"),
}


def comparable_v05(rel: str, raw: bytes) -> bytes:
    return _comparable(rel, raw, V05_TEXT_ONLY)


_V010_RAW = json.loads((CONTRACT / "tests" / "v010-fixture-digests.json").read_text(encoding="utf-8"))
V010_RECORD = dict(zip(_V010_RAW["files"], _V010_RAW["sha256"], strict=True))
# What a later release may change in a v0.10.0 fixture, and nothing else: the refused case that used code 7 as a code no
# tag had defined uses code 9 now that TSB's own prompt is code 8 (invalid-3's sidecar_hex and problem, nothing else of it).
V010_TEXT_ONLY = {
    "C8/end-marker-killed.json": ("invalid-3.sidecar_hex", "invalid-3.problem"),
}


def comparable_v010(rel: str, raw: bytes) -> bytes:
    return _comparable(rel, raw, V010_TEXT_ONLY)


class V05TreeUnchanged(unittest.TestCase):
    def test_every_v050_fixture_is_still_there_and_unchanged(self):
        changed = []
        for rel, digest in sorted(V05_RECORD.items()):
            path = FIXTURES / rel
            self.assertTrue(path.is_file(), f"{rel} is gone: a consumer pinned to v0.5.0 grades against it")
            if hashlib.sha256(comparable_v05(rel, path.read_bytes())).hexdigest() != digest:
                changed.append(rel)
        self.assertEqual(changed, [])
        self.assertEqual(len([r for r in V05_RECORD if r.endswith(".json")]), 206)
        # Only the two texts above may differ, and each entry must name a real v0.5.0 fixture.
        for rel in V05_TEXT_ONLY:
            self.assertIn(rel, V05_RECORD, f"{rel} is not a v0.5.0 fixture")

    def test_the_record_catches_a_change(self):
        # The telnet instance of C3/control-message-shape stays invalid: its verdict moving is caught,
        # even though its note is one of the two texts this tag rewrote.
        rel = "C3/control-message-shape.json"
        doc = json.loads((FIXTURES / rel).read_bytes())
        telnet = [i for i, ins in enumerate(doc["case"]["instances"])
                  if ins["doc"].get("protocol") == "telnet"]
        self.assertEqual(len(telnet), 1)
        doc["case"]["instances"][telnet[0]]["valid"] = True
        self.assertNotEqual(hashlib.sha256(comparable_v05(rel, json.dumps(doc).encode())).hexdigest(),
                            V05_RECORD[rel])
        rel = "C1/session-record-shape.json"
        doc = json.loads((FIXTURES / rel).read_bytes())
        doc["description"] += " "
        self.assertNotEqual(hashlib.sha256(comparable_v05(rel, json.dumps(doc).encode())).hexdigest(),
                            V05_RECORD[rel])


class TheCodeSevenExemptionNamesFieldsNotACase(unittest.TestCase):
    """C8/end-marker-killed's refused sidecar code moved from 7 to 8 when 7 became the login answer. The
    exemption names the two fields of that one invalid case the change touched, never the case, so every other
    part of the case (and of the file) is still held by the four digest records."""

    REL = "C8/end-marker-killed.json"
    FIELDS = ("sidecar_hex", "problem")
    RECORDS = (("v0.1", comparable, RECORD), ("v0.2.1", comparable_v02, V02_RECORD),
               ("v0.5.0", comparable_v05, V05_RECORD), ("v0.10.0", comparable_v010, V010_RECORD))
    ALLOWANCES = (TEXT_ONLY, V02_TEXT_ONLY, V05_TEXT_ONLY, V010_TEXT_ONLY)

    def digest(self, fn, doc) -> str:
        return hashlib.sha256(fn(self.REL, json.dumps(doc).encode())).hexdigest()

    def test_each_allowance_names_those_two_fields_and_no_whole_case(self):
        for allowance in self.ALLOWANCES:
            parts = allowance[self.REL]
            self.assertEqual(sorted(p for p in parts if p.startswith("invalid-3")),
                             ["invalid-3.problem", "invalid-3.sidecar_hex"])
            self.assertNotIn("invalid-3", parts)

    def test_the_two_fields_may_differ_and_nothing_else_in_the_case_may(self):
        for tag, fn, record in self.RECORDS:
            doc = json.loads((FIXTURES / self.REL).read_bytes())
            self.assertEqual(self.digest(fn, doc), record[self.REL], tag)
            for field in self.FIELDS:
                moved = json.loads((FIXTURES / self.REL).read_bytes())
                moved["case"]["invalid"][3][field] = "7"
                self.assertEqual(self.digest(fn, moved), record[self.REL], f"{tag}: {field} is the change itself")
            for field in ("ttyrec_hex", "note"):
                moved = json.loads((FIXTURES / self.REL).read_bytes())
                moved["case"]["invalid"][3][field] += "00"
                self.assertNotEqual(self.digest(fn, moved), record[self.REL], f"{tag}: {field} moved unseen")
            moved = json.loads((FIXTURES / self.REL).read_bytes())
            moved["case"]["invalid"][3]["added"] = "x"
            self.assertNotEqual(self.digest(fn, moved), record[self.REL], f"{tag}: a field added to the case")


class V05InstancesValidAtV06(unittest.TestCase):
    def test_every_v050_valid_instance_and_message_validates(self):
        reg = Registry.from_tree(CONTRACT)
        instances = messages = 0
        for rel in sorted(r for r in V05_RECORD if r.endswith(".json")):
            fx = json.loads((FIXTURES / rel).read_text(encoding="utf-8"))
            case = fx["case"]
            if fx["kind"] == "instance":
                for i, inst in enumerate(case["instances"]):
                    if inst["valid"]:
                        instances += 1
                        self.assertEqual(reg.validate(inst["doc"], case["schema"]), [], f"{rel} instance {i}")
            elif fx["kind"] == "sequence":
                for i, m in enumerate(case["messages"]):
                    if not m.get("unknown") and not m.get("invalid"):
                        messages += 1
                        self.assertEqual(_ipc.message_errors(reg, case["socket"], m["from"], m["msg"]), [],
                                         f"{rel} message {i}")
        self.assertGreaterEqual(instances, 213)
        self.assertGreaterEqual(messages, 415)

    def test_a_field_added_to_a_v050_message_is_optional(self):
        # `resizable` is this tag's addition to channel_opened: a v0.5.0 worker sends none of it and stays
        # valid, and absent means true (conventions V1: what a newer sender must send is graded by fixtures).
        reg = Registry.from_tree(CONTRACT)
        opened = {"event": "channel_opened", "session_id": "fixture-session-a",
                  "session_type": "shell", "socket": "shell.sock"}
        self.assertEqual(reg.validate(opened, "schemas/ipc-control.schema.json#/$defs/event"), [])
        for value in (True, False):
            self.assertEqual(reg.validate({**opened, "resizable": value},
                                          "schemas/ipc-control.schema.json#/$defs/event"), [])


if __name__ == "__main__":
    unittest.main()


U64, SAFE = 18446744073709551615, 9007199254740991


def effective_range(reg: Registry, ref: str) -> tuple:
    """(minimum, maximum) a field enforces, through its $ref chain: every constraint on the chain
    applies, so the tightest of each wins."""
    schema, doc = reg.split(ref)
    lo, hi = None, None
    while True:
        if "minimum" in schema:
            lo = schema["minimum"] if lo is None else max(lo, schema["minimum"])
        if "maximum" in schema:
            hi = schema["maximum"] if hi is None else min(hi, schema["maximum"])
        if "$ref" not in schema:
            return lo, hi
        schema, doc = reg.resolve(schema["$ref"], doc)


class Ranges(unittest.TestCase):
    """Every numeric field this tag adds carries a range, and never a default (decision 5.36:
    contract is not config): durations integer milliseconds from 1 — or 0, where 0 means off — to
    2^53-1; counts from 1; every seq and byte count a u64, as the data frame's seq is."""

    CTL, SUP = "schemas/ipc-control.schema.json#/$defs", "schemas/supervisor.schema.json#/$defs"
    FIELDS: ClassVar[dict[str, tuple[int, int]]] = {
        f"{CTL}/hello/properties/last_committed_seq": (0, U64),
        f"{CTL}/hello_ack/properties/first_buffered_seq": (1, U64),
        f"{CTL}/hello_ack/properties/first_buffered_offset": (0, U64),
        f"{CTL}/hello_ack/properties/last_dropped_ts": (1, U64),
        f"{CTL}/hello_ack/properties/current_seq": (0, U64),
        f"{CTL}/replay/properties/from_seq": (1, U64),
        f"{CTL}/replay_done/properties/through_seq": (0, U64),
        f"{CTL}/input_refused/properties/seq": (1, U64),
        f"{CTL}/ping/properties/n": (1, SAFE),
        f"{CTL}/timer_values/properties/heartbeat_interval": (1, SAFE),
        f"{CTL}/timer_values/properties/heartbeat_missed": (1, SAFE),
        f"{CTL}/timer_values/properties/keepalive_interval": (0, SAFE),
        f"{CTL}/timer_values/properties/keepalive_count_max": (1, SAFE),
        f"{CTL}/timer_values/properties/connection_linger": (0, SAFE),
        f"{CTL}/timer_values/properties/orphan_timeout": (1, SAFE),
        f"{CTL}/timer_values/properties/idle": (1, SAFE),
        f"{CTL}/timer_values/properties/unattended": (1, SAFE),
        f"{CTL}/open_shell/properties/open_timeout": (1, SAFE),
        f"{CTL}/buffer_bytes": (1, SAFE),
        f"{CTL}/input_frame_max": (1, 4294967295),
        f"{SUP}/create_worker/properties/undialled_timeout": (1, SAFE),
        f"{SUP}/kill_worker/properties/reap_timeout": (1, SAFE),
        f"{SUP}/ping/properties/n": (1, SAFE),
        "schemas/spool-entry.schema.json#/properties/last_seq": (0, U64),
        "schemas/spool-entry.schema.json#/properties/out_bytes": (0, U64),
        "schemas/spool-entry.schema.json#/properties/last_out_ts": (0, U64),
        "schemas/continuity-state.schema.json#/$defs/session_record/properties/out_bytes_committed": (0, U64),
        "schemas/continuity-state.schema.json#/$defs/session_record/properties/recording_last_seq_out": (0, U64),
        "schemas/ipc-data-frame.schema.json#/properties/length": (0, 4294967295),
        "schemas/ipc-data-frame.schema.json#/properties/seq": (1, U64),
        "schemas/ttyrec.schema.json#/$defs/gap_marker/properties/length": (1, 4294967295),
        # GET /targets' page bound, added by this release: a sanity bound, and no default (5.36).
        "openapi/tsb.openapi.json#/components/parameters/TargetLimit/schema": (1, 500),
        # Release B (the device login): ids and counts from 1, the login clock and the cap in their ranges.
        "schemas/common.schema.json#/$defs/login_prompt_id": (1, SAFE),
        "schemas/common.schema.json#/$defs/login_attempt": (1, SAFE),
        f"{CTL}/connect/oneOf/0/properties/login_timeout": (10000, 1800000),
        "openapi/tsb.openapi.json#/components/schemas/DeviceLoginPending/properties/expires_in": (1, 60),
        "schemas/config.schema.json#/properties/worker/properties/device_logins_max": (1, 64),
        "schemas/inventory-target.schema.json#/properties/options/properties/login_timeout": (10, 1800),
    }

    def test_every_new_numeric_field_has_its_range(self):
        reg = Registry.from_tree(CONTRACT)
        for ref, want in self.FIELDS.items():
            with self.subTest(ref=ref.split("schemas/")[-1]):
                self.assertEqual(effective_range(reg, ref), want)


class V019ValidShapesRemainValid(unittest.TestCase):
    def test_all_v0191_valid_instances_and_control_messages_still_validate(self):
        registry = Registry.from_tree(CONTRACT)
        archive = subprocess.check_output(
            ["git", "archive", "v0.19.1", "contract/fixtures"], cwd=CONTRACT.parent)
        checked = 0
        with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
            for member in tree:
                if not member.isfile() or not member.name.endswith(".json") or member.name.endswith("graded-by.json"):
                    continue
                fixture = json.load(tree.extractfile(member))
                case = fixture.get("case", {})
                if fixture["kind"] == "instance":
                    for item in case["instances"]:
                        if item["valid"]:
                            self.assertEqual(registry.validate(item["doc"], case["schema"]), [], member.name)
                            checked += 1
                elif fixture["kind"] == "sequence":
                    for item in case["messages"]:
                        if not item.get("invalid") and not item.get("unknown"):
                            self.assertEqual(_ipc.message_errors(
                                registry, case["socket"], item["from"], item["msg"]), [], member.name)
                            checked += 1
        self.assertGreater(checked, 100)
