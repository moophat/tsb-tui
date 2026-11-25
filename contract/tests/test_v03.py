"""contract-v0.3 phase 1 (C6, C8, C3): what this tag adds, and what it must not break.

Three things are held here:

- additive-only (immortality §6.4): every instance a v0.2.1 fixture calls valid still validates at
  v0.3, and every one it calls invalid still fails. R2 forbids a v0.2.1 fixture changing except the
  text this tag makes false; a verdict is never that text.
- the C8 broker marker: the x-broker-line codec, the pure-ASCII line (a hyphen, never an em dash),
  the marker-before-input rule, and the x-compare rules updated for code 5.
- the C3 timer key: keepalive_probe_interval is optional, so a v0.2.1 set stays valid, and the
  artifacts control-timers check grades it toward ≥ 0.3 peers and its absence toward 0.2.
"""

import copy
import json
import unittest

from tsbc import _binfmt as bf
from tsbc import _ipc
from tsbc.schema import Registry

from . import CONTRACT

FIXTURES = CONTRACT / "fixtures"
EPOCHS = {"e0", "e1"}


def is_v021(fx: dict) -> bool:
    """A fixture this tag must not change (R2): one published or required green at or before e1."""
    mark = fx.get("mark", {})
    return mark.get("required-green", mark.get("published")) in EPOCHS


def load_all() -> list[dict]:
    docs = []
    for path in sorted(FIXTURES.rglob("*.json")):
        docs.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return docs


class V021Verdicts(unittest.TestCase):
    """R1, immortality §6.4: additive only. A v0.2.1 fixture's valid instances stay valid and its
    invalid ones stay invalid, whatever this tag adds — or a consumer pinned to v0.2.1 would read a
    contract that quietly changed under it."""

    def test_every_v021_valid_instance_and_message_still_validates(self):
        reg = Registry.from_tree(CONTRACT)
        instances = messages = 0
        for rel, fx in [(p, d) for p, d in load_all() if is_v021(d)]:
            case = fx["case"]
            if fx["kind"] == "instance":
                for i, inst in enumerate(case["instances"]):
                    if inst["valid"]:
                        instances += 1
                        self.assertEqual(reg.validate(inst["doc"], case["schema"]), [],
                                         f"{rel.name} instance {i}")
            elif fx["kind"] == "sequence":
                for i, m in enumerate(case["messages"]):
                    if not m.get("unknown") and not m.get("invalid"):
                        messages += 1
                        self.assertEqual(_ipc.message_errors(reg, case["socket"], m["from"], m["msg"]), [],
                                         f"{rel.name} message {i}")
        self.assertGreaterEqual(instances, 40)
        self.assertGreaterEqual(messages, 40)

    def test_every_v021_invalid_instance_stays_invalid(self):
        # An instance a v0.2.1 fixture calls invalid must still fail: this tag only adds optional
        # fields, so nothing it loosens may let a refused record or message through.
        reg = Registry.from_tree(CONTRACT)
        refused = 0
        for rel, fx in [(p, d) for p, d in load_all() if is_v021(d) and d["kind"] == "instance"]:
            case = fx["case"]
            for i, inst in enumerate(case["instances"]):
                if not inst["valid"]:
                    refused += 1
                    self.assertTrue(reg.validate(inst["doc"], case["schema"]),
                                    f"{rel.name} instance {i} is marked invalid but validates")
        self.assertGreaterEqual(refused, 20)


class BrokerMarker(unittest.TestCase):
    """C8 code 5: the fixed pure-ASCII line (5.46)."""

    def test_the_line_is_the_published_one(self):
        for choice in bf.BROKER_CHOICES:
            line = bf.broker_line(choice)
            self.assertEqual(line, f"\r\n[TSB: stale screen after lost output - {choice} chosen]\r\n".encode("ascii"))
            self.assertEqual(bf.parse_broker_line(line), choice)

    def test_the_line_is_pure_ascii_and_never_an_em_dash(self):
        line = bf.broker_line("ctrl_l")
        self.assertTrue(all(b < 0x80 for b in line))
        self.assertIn(b" - ", line)
        self.assertNotIn(b"\xe2\x80\x94", line)  # an em dash would be UTF-8 e2 80 94
        # The hyphen between the prompt and the choice is a plain ASCII hyphen-minus.
        self.assertIn(b"lost output - ctrl_l chosen", line)

    def test_an_unknown_choice_is_refused(self):
        for bad in ("ctrl-L", "fresh session", "Ctrl_L", ""):
            with self.subTest(bad=bad), self.assertRaises(bf.FormatError):
                bf.broker_line(bad)

    def test_a_broken_line_does_not_parse(self):
        good = bf.broker_line("ctrl_l")
        for bad in (good.replace(b"ctrl_l", b"ctrl-L"), good.replace(b"-", b"\xe2\x80\x94"),
                    good.rstrip(b"\r\n"), b"x" + good, good.replace(b"stale screen", b"stale screens")):
            with self.subTest(bad=bad):
                self.assertIsNone(bf.parse_broker_line(bad))

    def test_the_recording_frame_rules(self):
        marker = bf.RecFrame(1789000001, 0, bf.BROKER, bf.broker_line("wiggle"))
        empty = bf.RecFrame(1789000001, 0, bf.BROKER, b"")
        junk = bf.RecFrame(1789000001, 0, bf.BROKER, b"free text")
        self.assertEqual(bf.recording_problems([marker]), [])
        self.assertTrue(any("a broker marker carries its readable line" in p
                            for p in bf.recording_problems([empty])))
        self.assertTrue(any("not the broker marker's line" in p for p in bf.recording_problems([junk])))

    def test_the_published_pattern_is_the_codec_s(self):
        # self-check proves codec ≡ layout; here the schema's own pattern accepts the codec's line
        # and refuses an em-dash one, which no fixture's self-check would catch.
        doc = Registry.from_tree(CONTRACT).docs[bf.RECORDING_SCHEMA]
        import re as _re
        pattern = doc["x-broker-line"]["pattern"]
        for choice in bf.BROKER_CHOICES:
            self.assertTrue(_re.match(pattern, bf.broker_line(choice).decode("ascii")))
        em = b"\r\n[TSB: stale screen after lost output \xe2\x80\x94 ctrl_l chosen]\r\n".decode("latin-1")
        self.assertIsNone(_re.match(pattern, em))


class ReviewSchemaTightening(unittest.TestCase):
    """Shapes new at v0.3, tightened in the release review: a bad instance is refused and the good
    one (and every fixture, via the self-check) still passes."""

    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.from_tree(CONTRACT)

    def check(self, instance, ref):
        return self.registry.validate(instance, ref)

    def test_a_chosen_stale_event_names_who_answered(self):
        ref = "schemas/ws-frames.schema.json#/$defs/stale"
        base = {"t": "stale", "v": 1, "session_id": "s-1", "state": "chosen", "cause": "grid_lost",
                "at": "2026-10-06T02:00:02Z", "choice": "continue"}
        holder = {"client_id": "c-1", "user": "u"}
        self.assertEqual(self.check({**base, "by": holder}, ref), [])
        self.assertNotEqual(self.check({**base, "by": None}, ref), [])
        gap = {**base, "cause": "gap", "gap_seq": 5}
        self.assertEqual(self.check({**gap, "by": holder}, ref), [])
        self.assertNotEqual(self.check({**gap, "by": None}, ref), [])
        pending = {"t": "stale", "v": 1, "session_id": "s-1", "state": "pending", "cause": "grid_lost",
                   "at": "2026-10-06T02:00:01Z"}
        self.assertEqual(self.check(pending, ref), [])

    def test_a_stale_choice_carries_only_its_own_text(self):
        ref = "schemas/ws-frames.schema.json#/$defs/stale_choice_text"
        good = {"wiggle": "Redraw the screen (no guarantee)",
                "ctrl_l": "Send Ctrl-L (clears the screen, no guarantee)",
                "continue": "Keep going without a redraw (no guarantee the screen is right)",
                "fresh_session": "Start a fresh session"}
        for choice, text in good.items():
            self.assertEqual(self.check({"choice": choice, "text": text}, ref), [], choice)
        self.assertNotEqual(self.check({"choice": "continue", "text": good["fresh_session"]}, ref), [])
        self.assertNotEqual(self.check({"choice": "wiggle", "text": "Delete everything"}, ref), [])

    def test_a_grid_snapshot_is_whole_base64(self):
        ref = "schemas/continuity-state.schema.json#/$defs/grid_snapshot_record"
        base = {"session_id": "s-1", "snapshot_seq": 1, "cols": 80, "rows": 24,
                "engine": {"name": "libghostty-vt", "version": "1"}, "written_at": "2026-10-06T02:00:02Z"}
        for ok in ("QQ==", "QQ", "QUI=", "QUI", "QUJD", "QUJDRA==", "QUJDRA"):
            self.assertEqual(self.check({**base, "snapshot": ok}, ref), [], ok)
        for bad in ("A", "QUJDR", "QUJDRAA=A", "=", "", "QQ=", "Q==="):
            self.assertNotEqual(self.check({**base, "snapshot": bad}, ref), [], bad)

    def test_a_broker_marker_is_one_of_the_four_lines(self):
        ref = "schemas/ttyrec.schema.json#/$defs/broker_marker"
        for choice in bf.BROKER_CHOICES:
            line = bf.broker_line(choice)
            marker = {"sec": 1789000001, "usec": 0, "length": len(line), "marker": "broker",
                      "data_hex": line.hex()}
            self.assertEqual(self.check(marker, ref), [], choice)
            self.assertNotEqual(self.check({**marker, "length": len(line) + 1}, ref), [], choice)
        line = bf.broker_line("wiggle")
        base = {"sec": 1789000001, "usec": 0, "marker": "broker"}
        self.assertNotEqual(self.check({**base, "length": 1, "data_hex": "00"}, ref), [])
        self.assertNotEqual(self.check({**base, "length": len(line), "data_hex": line.replace(b"wiggle", b"wigglx").hex()},
                                       ref), [])


class CompareCode5(unittest.TestCase):
    """x-compare updated for code 5: left out of the payload, compared line for line."""

    def test_code_5_is_left_out_of_the_payload(self):
        a = [bf.RecFrame(1789000000, 0, bf.OUT, b"$ "), bf.RecFrame(1789000001, 0, bf.BROKER, bf.broker_line("ctrl_l")),
             bf.RecFrame(1789000001, 1000, bf.IN, b"\x0c")]
        b = [a[0], bf.RecFrame(1789000002, 0, bf.BROKER, bf.broker_line("ctrl_l")), a[2]]
        # Same payload, a broker marker moved: the payloads still match (code 5 is not payload).
        self.assertEqual(bf.compare_recordings(a, b)["payload_identical"], {"out": True, "in": True})

    def test_a_different_broker_line_makes_the_input_not_identical(self):
        a = [bf.RecFrame(1789000000, 0, bf.OUT, b"$ "), bf.RecFrame(1789000001, 0, bf.BROKER, bf.broker_line("ctrl_l")),
             bf.RecFrame(1789000001, 1000, bf.IN, b"\x0c")]
        b = [a[0], bf.RecFrame(1789000001, 0, bf.BROKER, bf.broker_line("wiggle")), a[2]]
        # A different choice means the broker acted differently: the recordings differ, line for line.
        self.assertFalse(bf.compare_recordings(a, b)["payload_identical"]["in"])

    def test_the_broker_marker_bears_the_input_clock(self):
        # x-compare: a broker marker is stamped on the core's input clock, so it goes back only when
        # the input stamps do.
        in_frame = bf.RecFrame(1789000001, 1000, bf.IN, b"\x0c")
        before = bf.RecFrame(1789000001, 0, bf.BROKER, bf.broker_line("ctrl_l"))
        problems = bf.recording_stamp_problems([in_frame, before])
        self.assertTrue(any("the stamps go back" in p for p in problems), problems)
        self.assertEqual(bf.recording_stamp_problems([before, in_frame]), [])


class TimerKeyRule(unittest.TestCase):
    """C3: keepalive_probe_interval is optional (R1), and the control-timers check grades it only
    toward peers at ≥ 0.3 (D29)."""

    CTL = "schemas/ipc-control.schema.json#/$defs/command"

    def test_the_probe_key_is_optional_so_a_v021_set_stays_valid(self):
        reg = Registry.from_tree(CONTRACT)
        base = {"heartbeat_interval": 10000, "heartbeat_missed": 3, "keepalive_interval": 30000,
                "keepalive_count_max": 12, "connection_linger": 300000, "orphan_timeout": 172800000,
                "idle": 604800000, "unattended": 2592000000}
        without = {"cmd": "timers", "timers": copy.deepcopy(base)}
        self.assertEqual(reg.validate(without, self.CTL), [])
        with_probe = {"cmd": "timers", "timers": {**base, "keepalive_probe_interval": 15000}}
        self.assertEqual(reg.validate(with_probe, self.CTL), [])
        # A 0.1 connect, no timers at all, stays valid too (immortality §6.4).
        self.assertEqual(reg.validate({"cmd": "connect", "target_id": "t", "protocol": "echo"}, self.CTL), [])

    def test_the_probe_key_has_the_duration_range(self):
        reg = Registry.from_tree(CONTRACT)
        schema, doc = reg.split("schemas/ipc-control.schema.json#/$defs/timer_values")
        prop = schema["properties"]["keepalive_probe_interval"]
        self.assertEqual(reg.resolve(prop["$ref"], doc)[0]["minimum"], 1)  # a duration_ms: 1 … 2^53-1
        self.assertNotIn("default", prop)  # the default is config.yaml's, never the schema's (5.36)

    def test_the_core_sends_the_key_only_to_peers_at_0_3(self):
        # artifacts._version_at_least is the rule D29 leans on: a 0.2 worker must never see the key.
        from tsbc.artifacts import _version_at_least
        self.assertTrue(_version_at_least("0.3", "0.3"))
        self.assertTrue(_version_at_least("0.3", "0.2"))
        self.assertTrue(_version_at_least("0.2", "0.2"))
        self.assertFalse(_version_at_least("0.2", "0.3"))
        self.assertFalse(_version_at_least("0.1", "0.2"))


if __name__ == "__main__":
    unittest.main()