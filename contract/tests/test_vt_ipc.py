"""The C4 core ↔ VT seam codec: byte by byte, and the schemas' valid/invalid instances.

The fixture's .bin was produced by this codec, so a fixture round trip alone would pass a codec
that is wrong everywhere in the same way. These expectations are built by hand from the published
layout instead: [u32 BE length][u8 kind][body], big-endian, and kind 2 embedding a C3 frame
byte-identical.
"""

import json
import unittest

from tsbc import _binfmt as bf
from tsbc import _ipc
from tsbc.fixtures import Fixture, load_fixtures
from tsbc.schema import Registry

from . import CONTRACT


def _ipc_fixture(messages):
    """A bare vt-control sequence fixture, for the ordering rules' own negative cases."""
    return Fixture(CONTRACT / "fixtures" / "C4" / "example.json", "C4/example.json",
                   {"seam": "C4", "name": "example", "kind": "sequence",
                    "case": {"socket": "vt-control",
                             "messages": [{"from": s, "msg": m} for s, m in messages]}})

TS = 1789000000123456789
C4_FIXTURES = CONTRACT / "fixtures" / "C4"


class C4Codec(unittest.TestCase):
    def test_control_round_trips(self):
        msg = {"cmd": "ping"}
        wire = bf.encode_control(msg)
        length, kind = bf.VT_HEADER.unpack_from(wire, 0)
        self.assertEqual(kind, bf.VT_KIND_CONTROL)
        self.assertEqual(length, len(wire) - bf.VT_HEADER.size)
        [frame], trailing = bf.decode_frames_vt(wire)
        self.assertEqual(trailing, b"")
        self.assertEqual(bf.decode_control(frame), msg)

    def test_frame_embeds_c3_byte_identical(self):
        c3 = bf.DataFrame(0, 1, TS, b"login: ")
        wire = bf.encode_output_frame("sess-1", c3)
        length, kind = bf.VT_HEADER.unpack_from(wire, 0)
        self.assertEqual(kind, bf.VT_KIND_FRAME)
        self.assertEqual(length, len(wire) - bf.VT_HEADER.size)
        # The body is [u8 id_len][session_id][C3 envelope + payload], the C3 frame's own bytes.
        body = wire[bf.VT_HEADER.size:]
        n = body[0]
        self.assertEqual(body[1:1 + n], b"sess-1")
        self.assertEqual(body[1 + n:], bf.encode_frames([c3]))
        [frame], _ = bf.decode_frames_vt(wire)
        out = bf.decode_output_frame(frame)
        self.assertEqual(out.session_id, "sess-1")
        self.assertEqual(out.frame, c3)

    def test_gap_frame_travels_verbatim(self):
        gap = bf.DataFrame(2, 1, TS, b"")
        out = bf.decode_output_frame(bf.decode_frames_vt(bf.encode_output_frame("s", gap))[0][0])
        self.assertEqual(out.frame, gap)
        self.assertTrue(out.frame.gap)

    def test_blob_chunks_and_reassembles(self):
        blob = b"x" * 64
        maxb = 1 + 6 + 8   # id_len + "req-1" + room; max_message_bytes counts the body
        chunks = bf.encode_blob("req-1", blob, max_message_bytes=maxb)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) - bf.VT_HEADER.size <= maxb for c in chunks))
        messages, trailing = bf.decode_frames_vt(b"".join(chunks))
        self.assertEqual(trailing, b"")
        self.assertEqual(bf.reassemble_blobs(messages), {"req-1": blob})

    def test_blob_that_fits_is_one_message(self):
        blob = b"short"
        [chunk] = bf.encode_blob("r", blob)
        [message], _ = bf.decode_frames_vt(chunk)
        self.assertEqual(bf.decode_blob(message), bf.VTBlob("r", blob))

    def test_max_size_is_refused_from_its_length_alone(self):
        # A receiver must refuse a length above max_message_bytes before buffering the body (D16).
        wire = bf.VT_HEADER.pack(64, bf.VT_KIND_BLOB) + b"x" * 64
        with self.assertRaises(bf.FormatError):
            bf.decode_frames_vt(wire, max_message_bytes=16)

    def test_a_kind_outside_the_three_is_refused(self):
        for kind in (0, 4, 255):
            with self.assertRaises(bf.FormatError):
                bf.decode_frames_vt(bf.VT_HEADER.pack(0, kind))

    def test_encode_refuses_a_body_above_max(self):
        with self.assertRaises(bf.FormatError):
            bf.encode_control({"msg": "x" * 100}, max_message_bytes=16)

    def test_torn_stream_keeps_trailing_bytes(self):
        wire = bf.encode_control({"cmd": "ping"}) + b"\x00\x00"
        messages, trailing = bf.decode_frames_vt(wire)
        self.assertEqual(len(messages), 1)
        self.assertEqual(trailing, b"\x00\x00")

    def test_stream_of_kinds(self):
        wire = (bf.encode_control({"cmd": "hello", "proto_version": "0.3"})
                + bf.encode_output_frame("s", bf.DataFrame(0, 1, TS, b"out"))
                + bf.encode_blob("r1", b"blob")[0])
        messages, _ = bf.decode_frames_vt(wire)
        self.assertEqual([m.kind for m in messages],
                         [bf.VT_KIND_CONTROL, bf.VT_KIND_FRAME, bf.VT_KIND_BLOB])


class C4Schema(unittest.TestCase):
    """Valid and invalid instances of vt-ipc and vt-screen (R4: oneOf + const)."""

    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.from_tree(CONTRACT)

    def errors(self, ref, doc):
        return self.registry.validate(doc, ref)

    def test_hello_valid(self):
        ref = "schemas/vt-ipc.schema.json"
        self.assertEqual(self.errors(ref, {"cmd": "hello", "proto_version": "0.3"}), [])

    def test_hello_bad_proto_invalid(self):
        ref = "schemas/vt-ipc.schema.json"
        self.assertTrue(self.errors(ref, {"cmd": "hello", "proto_version": "0.2"}))

    def test_error_codes(self):
        ref = "schemas/vt-ipc.schema.json"
        for code in ("unknown_session", "seq_jump", "snapshot_incompatible", "bad_request",
                     "unknown_cmd", "unsupported_proto"):
            self.assertEqual(self.errors(ref, {"event": "error", "code": code, "message": "m"}), [])
        self.assertTrue(self.errors(ref, {"event": "error", "code": "vt_unavailable", "message": "m"}))

    def test_hello_ack_requires_grids_and_engine(self):
        ref = "schemas/vt-ipc.schema.json"
        good = {"event": "hello_ack", "proto_version": "0.3",
                "engine": {"name": "libghostty-vt", "version": "1"}, "grids": []}
        self.assertEqual(self.errors(ref, good), [])
        self.assertTrue(self.errors(ref, {"event": "hello_ack", "proto_version": "0.3"}))

    def test_unknown_command_invalid(self):
        ref = "schemas/vt-ipc.schema.json"
        self.assertTrue(self.errors(ref, {"cmd": "frobnicate"}))

    def test_fresh_grid_at_seq_zero_validates_everywhere(self):
        # A grid opened but with no output yet is at seq 0; every position that names a grid's
        # output point must accept it, so a fresh grid's hello_ack, prefix, snapshot and restore
        # all validate. (held_grid, prefix_ready, snapshot_ready already allowed 0; restore's
        # snapshot_seq and restored's through_seq now do too.)
        ref = "schemas/vt-ipc.schema.json"
        engine = {"name": "libghostty-vt", "version": "1"}
        self.assertEqual(self.errors(ref, {"event": "hello_ack", "proto_version": "0.3",
                                           "engine": engine,
                                           "grids": [{"session_id": "s", "through_seq": 0,
                                                      "cols": 80, "rows": 24}]}), [])
        self.assertEqual(self.errors(ref, {"event": "prefix_ready", "request_id": "r1",
                                           "session_id": "s", "through_seq": 0,
                                           "cols": 80, "rows": 24}), [])
        self.assertEqual(self.errors(ref, {"event": "snapshot_ready", "request_id": "r1",
                                           "session_id": "s", "through_seq": 0,
                                           "cols": 80, "rows": 24, "engine": engine}), [])
        self.assertEqual(self.errors(ref, {"cmd": "restore", "session_id": "s", "request_id": "r1",
                                           "snapshot_seq": 0, "cols": 80, "rows": 24}), [])
        self.assertEqual(self.errors(ref, {"event": "restored", "request_id": "r1",
                                           "session_id": "s", "through_seq": 0}), [])

    def test_vt_screen_valid_and_invalid(self):
        ref = "schemas/vt-screen.schema.json"
        good = {"cols": 80, "rows": 24, "active_screen": "primary",
                "screens": {"primary": {"lines": ["hi"]}, "alt": {"lines": []}},
                "cursor": {"row": 0, "col": 2, "visible": True},
                "modes": {"decckm": False, "keypad": "numeric", "bracketed_paste": False,
                          "mouse": {"mode": "off"}, "focus": False, "origin": False,
                          "autowrap": True, "insert": False}}
        self.assertEqual(self.errors(ref, good), [])
        bad = dict(good, alt_screen=True)
        self.assertTrue(self.errors(ref, bad))
        self.assertTrue(self.errors(ref, {k: v for k, v in good.items() if k != "modes"}))


class C4Fixtures(unittest.TestCase):
    """Every C4 fixture on disk: its messages validate against vt-ipc, and every golden .bin is
    exactly what make_c4_fixtures.py produces from the case's own chunk list — so a case edited
    without regenerating fails here, not silently at a customer."""

    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.from_tree(CONTRACT)
        from . import make_c4_fixtures as mc
        cls.golden_data = mc._golden_data

    def _c4_docs(self):
        for path in sorted(C4_FIXTURES.glob("*.json")):
            yield path.name, json.loads(path.read_text(encoding="utf-8"))

    def test_every_c4_message_validates_against_vt_ipc(self):
        ref = "schemas/vt-ipc.schema.json"
        checked = 0
        for name, fx in self._c4_docs():
            case = fx["case"]
            for i, entry in enumerate(case.get("messages", [])):
                if entry.get("kind") != bf.VT_KIND_CONTROL:
                    continue
                checked += 1
                self.assertEqual(self.registry.validate(entry["msg"], ref), [],
                                 f"{name} message {i}: {entry['msg']!r}")
        # vt-frame-layout carries hello and hello_ack; the golden cases carry the resize events.
        # The C4 sequence fixtures (whose messages are control objects too) are a later job.
        self.assertGreaterEqual(checked, 4)

    def test_golden_bins_regenerate_byte_identical(self):
        from . import make_c4_fixtures as mc
        for name, _chunks, messages, _asserts in self.golden_data:
            encoded = b"".join(mc.encode_message(m, mc.MAX_MESSAGE_BYTES) for m in messages)
            on_disk = (C4_FIXTURES / f"{name}.bin").read_bytes()
            self.assertEqual(encoded, on_disk, f"{name}.bin has drifted from make_c4_fixtures.py")
            decoded, trailing = bf.decode_frames_vt(on_disk, max_message_bytes=mc.MAX_MESSAGE_BYTES)
            self.assertEqual(trailing, b"", name)
            self.assertEqual(len(decoded), len(messages), name)

    def test_golden_input_carries_resize_events(self):
        # F14d: the cases that resize mid-stream carry a kind-1 resize between output frames, so the
        # fresh parser sees the size change and not a frame boundary pretend.
        for name, chunks, messages, _asserts in self.golden_data:
            resizes = [m for m in messages if m["kind"] == bf.VT_KIND_CONTROL
                       and m["msg"].get("cmd") == "resize"]
            self.assertEqual(len(resizes), sum(1 for c in chunks if isinstance(c, tuple)),
                             f"{name}: one resize event per (cols, rows) chunk")


class C4Sequences(unittest.TestCase):
    """Every vt-control sequence fixture passes `check_sequence`, and every message it shows
    validates against vt-ipc — the runner's half of C4 part B2."""

    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.from_tree(CONTRACT)
        cls.fixtures, _ = load_fixtures(CONTRACT)
        cls.seqs = [fx for fx in cls.fixtures
                    if fx.kind == "sequence" and fx.case.get("socket") == "vt-control"]

    def test_a_seq_fixture_exists(self):
        self.assertEqual(len(self.seqs), 13)

    def test_every_sequence_passes_check_sequence(self):
        from tsbc import selfcheck
        for fx in self.seqs:
            with self.subTest(fx.name):
                report = selfcheck.Report()
                ctx = selfcheck.Context(CONTRACT, self.registry, report, self.fixtures)
                _ipc.check_sequence(fx, ctx)
                self.assertEqual(report.failures, [])

    def test_every_control_message_validates(self):
        for fx in self.seqs:
            for i, item in enumerate(fx.case["messages"]):
                if item.get("unknown"):
                    continue
                with self.subTest(f"{fx.name}#{i}"):
                    self.assertEqual(_ipc.message_errors(self.registry, "vt-control",
                                                         item["from"], item["msg"]), [])

    def test_all_sixteen_kinds_are_covered(self):
        # Coverage counts the C4 messages; the four rules are graded on the same socket.
        seen = set()
        for fx in self.seqs:
            for item in fx.case["messages"]:
                if not item.get("unknown") and not item.get("invalid"):
                    seen.add(_ipc.kind(item["msg"]))
        self.assertEqual(seen, {k for ref in _ipc.SENDERS["vt-control"].values()
                                for k in _ipc.defined_kinds(self.registry, ref)})

    def test_each_rule_is_refused_by_an_out_of_order_instance(self):
        # One instance per rule: a sequence that breaks it must not pass check_sequence.
        from tsbc import selfcheck
        cases = {
            "must be hello": [("core", {"cmd": "open", "session_id": "s", "cols": 80,
                                        "rows": 24, "scrollback_bytes": 1})],
            "is not open": [("core", {"cmd": "hello", "proto_version": "0.3"}),
                            ("vt", {"event": "hello_ack", "proto_version": "0.3",
                                    "engine": {"name": "libghostty-vt", "version": "1"},
                                    "grids": []}),
                            ("core", {"cmd": "resize", "session_id": "s",
                                      "cols": 80, "rows": 24})],
            "after its close": [("core", {"cmd": "hello", "proto_version": "0.3"}),
                                ("vt", {"event": "hello_ack", "proto_version": "0.3",
                                        "engine": {"name": "libghostty-vt", "version": "1"},
                                        "grids": []}),
                                ("core", {"cmd": "open", "session_id": "s", "cols": 80,
                                          "rows": 24, "scrollback_bytes": 1}),
                                ("core", {"cmd": "close", "session_id": "s"}),
                                ("core", {"cmd": "resize", "session_id": "s",
                                          "cols": 80, "rows": 24})],
            "answers no in-flight request": [
                ("core", {"cmd": "hello", "proto_version": "0.3"}),
                ("vt", {"event": "hello_ack", "proto_version": "0.3",
                        "engine": {"name": "libghostty-vt", "version": "1"}, "grids": []}),
                ("core", {"cmd": "open", "session_id": "s", "cols": 80, "rows": 24,
                          "scrollback_bytes": 1}),
                ("core", {"cmd": "prefix", "session_id": "s", "request_id": "r1"}),
                ("vt", {"event": "prefix_ready", "request_id": "r2", "session_id": "s",
                        "through_seq": 1, "cols": 80, "rows": 24})],
            "answers no ping": [("core", {"cmd": "hello", "proto_version": "0.3"}),
                                ("vt", {"event": "hello_ack", "proto_version": "0.3",
                                        "engine": {"name": "libghostty-vt", "version": "1"},
                                        "grids": []}),
                                ("vt", {"event": "pong"})],
        }
        for needle, messages in cases.items():
            with self.subTest(needle):
                report = selfcheck.Report()
                ctx = selfcheck.Context(CONTRACT, self.registry, report, self.fixtures)
                fx = _ipc_fixture(messages)
                _ipc.check_sequence(fx, ctx)
                self.assertTrue(any(needle in f for f in report.failures), report.failures)


if __name__ == "__main__":
    unittest.main()