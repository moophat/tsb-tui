"""Terminal formats reject false finality and preserve original recording bytes."""

import copy
import json
import unittest
from typing import ClassVar
from unittest.mock import patch

from tsbc import _binfmt as bf
from tsbc import _ipc, artifacts, selfcheck
from tsbc.schema import Registry

from . import CONTRACT


class TerminalFormats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.from_tree(CONTRACT)

    def event(self):
        path = CONTRACT / "fixtures/C6/recording-integrity-unrecorded.json"
        return json.loads(path.read_text())["case"]["instances"][0]["doc"]

    def test_unrecorded_never_fabricates_recording_or_finality(self):
        original = self.event()
        ref = "schemas/audit-event.schema.json"
        self.assertFalse(self.registry.validate(original, ref))
        for field, value in (("baseline_seq", 0), ("baseline_bytes", 0), ("final_out_bytes", 0),
                             ("read_outcome", "eof"), ("recording_enabled", True), ("unknown_suffix", False)):
            changed = copy.deepcopy(original)
            changed["detail"][field] = value
            with self.subTest(field=field):
                self.assertTrue(self.registry.validate(changed, ref))
        original["recording_path"] = "recordings/fabricated.ttyrec"
        self.assertTrue(self.registry.validate(original, ref))

        event = {"ts": "2026-11-15T02:15:00Z", "action": "recording.output_incomplete",
                 "session_id": "s1", "session_type": "shell", "recording_path": "recordings/s1.ttyrec"}
        details = [
            {"version": 1, "kind": "continuation", "key": "ri1:s1:c:1", "generation": 1,
             "previous_path": "recordings/s1.ttyrec", "previous_seq_out": 40, "previous_bytes": 8000,
             "previous_offset": 9000, "continuation_path": "recordings/s1.chain/continuation-1.ttyrec"},
            {"version": 1, "kind": "continuation_unconfirmed", "key": "ri1:s1:q:1", "generation": 1,
             "baseline_seq": 40, "baseline_bytes": 8000, "baseline_offset": 0,
             "reason": "recording_unsynced", "observed_at_ns": 1, "unknown_suffix": True},
        ]
        for detail in details:
            instance = {**event, "detail": detail}
            with self.subTest(kind=detail["kind"]):
                self.assertFalse(self.registry.validate(instance, ref))
                self.assertFalse(selfcheck.credential_problems(instance, placeholders=False))
                malformed = copy.deepcopy(instance)
                malformed["detail"]["key"] += ":secret"
                self.assertTrue(selfcheck.credential_problems(malformed, placeholders=False))
                malformed = copy.deepcopy(instance)
                malformed["detail"]["generation"] = 0
                self.assertTrue(self.registry.validate(malformed, ref))
                malformed = copy.deepcopy(instance)
                malformed["detail"]["unexpected"] = True
                self.assertTrue(self.registry.validate(malformed, ref))

    def test_only_exact_integrity_identity_is_exempt_from_credential_key_scan(self):
        original = self.event()
        self.assertFalse(selfcheck.credential_problems(original, placeholders=False))
        for changed in ({"key": original["detail"]["key"]},
                        {**original, "action": "session.disconnect"}):
            self.assertTrue(selfcheck.credential_problems(changed, placeholders=False))
        malformed = self.event()
        malformed["detail"]["kind"] = []
        self.assertTrue(selfcheck.credential_problems(malformed, placeholders=False))
        original["detail"]["key"] += ":secret"
        self.assertTrue(selfcheck.credential_problems(original, placeholders=False))
        original = self.event()
        original["detail"]["password"] = "not-an-approved-synthetic-value"
        self.assertTrue(selfcheck.credential_problems(original, placeholders=False))

    def test_unrecorded_key_is_reason_free_across_restarts(self):
        def event(reason, key):
            return {"action": "recording.output_incomplete", "session_id": "s1", "session_type": "shell",
                    "detail": {"version": 1, "kind": "unrecorded", "key": key, "recording_enabled": False,
                               "baseline_seq": None, "baseline_bytes": None, "reason": reason,
                               "observed_at_ns": 1794708000123456789, "unknown_suffix": True}}
        first = event("worker_absent", "ri1:s1:u:null:null")
        later = event("core_stop", "ri1:s1:u:null:null")
        self.assertEqual(selfcheck._integrity_identity(first), "ri1:s1:u:null:null")
        self.assertEqual(selfcheck._integrity_identity(later), "ri1:s1:u:null:null")
        self.assertEqual(selfcheck._integrity_identity(first), selfcheck._integrity_identity(later))
        not_recorded = {"action": "recording.output_incomplete", "session_id": "s1", "session_type": "shell",
                        "detail": {"version": 1, "kind": "not_recorded", "key": "ri1:s1:n",
                                   "recording_enabled": False, "read_outcome": "eof", "final_last_seq": 0,
                                   "final_out_bytes": 0, "final_last_out_ts": 0,
                                   "observed_at_ns": 1794708000123456789, "unknown_suffix": False}}
        self.assertEqual(selfcheck._integrity_identity(not_recorded), "ri1:s1:n")

    def test_marker_exact_bytes_header_and_invalid_values(self):
        expected = (b"\r\n[TSB: output incomplete after seq 42 bytes 8192; reason core_wait_timeout; "
                    b"observed_ns 1794708000123456789; remaining unknown]\r\n")
        self.assertEqual(bf.terminal_incomplete_line(42, 8192, "core_wait_timeout", 1794708000123456789), expected)
        self.assertEqual(bf.parse_terminal_incomplete_line(expected),
                         (42, 8192, "core_wait_timeout", 1794708000123456789))
        self.assertFalse(bf.record_problems(bf.RecFrame(1794708000, 123456, bf.TERMINAL_INCOMPLETE, expected)))
        self.assertTrue(bf.record_problems(bf.RecFrame(1794708000, 0, bf.TERMINAL_INCOMPLETE, expected)))
        for changed in (expected.replace(b"seq 42", b"seq 042"),
                        expected.replace(b"core_wait_timeout", b"unknown"),
                        expected.replace(b"seq 42", b"seq 18446744073709551616")):
            self.assertIsNone(bf.parse_terminal_incomplete_line(changed))

    def test_marker_bounds_and_clock_exemption_do_not_mask_data_regression(self):
        minimum = bf.terminal_incomplete_line(0, 0, "core_stop", 1)
        maximum = bf.terminal_incomplete_line(2**64-1, 2**64-1, "worker_drain_timeout", 4294967295999999999)
        self.assertEqual((len(minimum), len(maximum)), (100, 167))
        marker = bf.RecFrame(0, 0, bf.TERMINAL_INCOMPLETE, minimum)
        normal = [bf.RecFrame(10, 0, bf.OUT, b"a"), marker, bf.RecFrame(11, 0, bf.OUT, b"b")]
        self.assertFalse(bf.recording_stamp_problems(normal))
        normal[-1] = bf.RecFrame(9, 0, bf.OUT, b"b")
        self.assertTrue(bf.recording_stamp_problems(normal))

    def test_marker_changes_only_out_comparison_and_cannot_follow_end(self):
        marker = bf.RecFrame(0, 0, bf.TERMINAL_INCOMPLETE,
                             bf.terminal_incomplete_line(0, 0, "core_stop", 1))
        payload = bf.RecFrame(1, 0, bf.OUT, b"a")
        self.assertTrue(bf.compare_recordings([payload, marker], [payload, marker])["payload_identical"]["out"])
        comparison = bf.compare_recordings([payload, marker], [payload])["payload_identical"]
        self.assertFalse(comparison["out"])
        self.assertTrue(comparison["in"])
        self.assertTrue(bf.recording_problems([bf.RecFrame(1, 0, bf.END_KILLED, b""), marker]))

    def test_marker_counters_must_match_the_recording_walk(self):
        # §6 line 222 / §4: L/B use the recording baseline. One output byte makes the walk (seq 1, bytes 1);
        # a marker claiming seq 0 bytes 0 must be flagged as a recording problem.
        out = bf.RecFrame(1, 0, bf.OUT, b"a")
        end = bf.RecFrame(2, 0, bf.END_KILLED, b"")
        header = (1794708000, 123456)  # matches observed_ns 1794708000123456789's sec/usec
        low = bf.RecFrame(*header, bf.TERMINAL_INCOMPLETE,
                          bf.terminal_incomplete_line(0, 0, "core_stop", 1794708000123456789))
        problems = bf.recording_problems([out, low, end])
        self.assertTrue(any("seq 0 bytes 0" in text and "walk" in text for text in problems), problems)
        # The matching marker (the walk's own seq/bytes) is not flagged: a guard.
        ok = bf.RecFrame(*header, bf.TERMINAL_INCOMPLETE,
                         bf.terminal_incomplete_line(1, 1, "core_stop", 1794708000123456789))
        self.assertFalse(bf.recording_problems([out, ok, end]), bf.recording_problems([out, ok, end]))

    def test_terminal_release_needs_seal_and_completed_replay(self):
        path = CONTRACT / "fixtures/C3/reattach-ended-audited-sealed.json"
        messages = json.loads(path.read_text())["case"]["messages"]
        pairs = [(m["from"], m["msg"]) for m in messages]
        self.assertFalse(_ipc.conn_order(pairs))
        no_seal = [(sender, {k: v for k, v in msg.items() if k != "output_seal"})
                   for sender, msg in pairs
                   if msg.get("cmd") != "seal_output" and msg.get("event") != "output_sealed"]
        self.assertTrue(_ipc.conn_order(no_seal))
        self.assertTrue(_ipc.conn_order([(sender, msg) for sender, msg in pairs
                                        if msg.get("event") != "replay_done"]))

    def test_terminal_vocabulary_is_refused_for_known_older_peer(self):
        path = CONTRACT / "fixtures/C3/reattach-ended-audited-sealed.json"
        trace = json.loads(path.read_text())["case"]["messages"]
        pairs = [(m["from"], m["msg"]) for m in trace]
        self.assertTrue(any("pre-0.8" in text for _, text in _ipc.conn_order(pairs, version="0.7")))

    def test_published_marker_pattern_cannot_drift_from_codec(self):
        annotation = copy.deepcopy(self.registry.docs["schemas/ttyrec.schema.json"]["x-terminal-incomplete-line"])
        self.assertFalse(bf._terminal_line_problems(annotation))
        annotation["pattern"] = annotation["pattern"].replace("core_stop", "wrong_reason")
        self.assertTrue(bf._terminal_line_problems(annotation))

    def test_seal_can_discover_the_retained_session_is_now_absent(self):
        path = CONTRACT / "fixtures/C3/reattach-ended-audited-sealed.json"
        messages = json.loads(path.read_text())["case"]["messages"]
        pairs = [(m["from"], m["msg"]) for m in messages]
        stop = next(i for i, (_, m) in enumerate(pairs) if m.get("cmd") == "seal_output")
        raced = pairs[:stop+1] + [("worker", {"event": "error", "session_id": "fixture-session-a",
                                            "code": "no_such_session", "message": "session absent"})]
        self.assertFalse(_ipc.conn_order(raced))


class RoundTwo(unittest.TestCase):
    def trace(self):
        path = CONTRACT / "fixtures/C3/reattach-ended-audited-sealed.json"
        return [(m["from"], m["msg"]) for m in json.loads(path.read_text())["case"]["messages"]]

    def test_pending_seal_is_accepted_after_a_fresh_08_hello_and_flagged_before_one(self):
        pairs = self.trace()
        seal = next(i for i, (_, m) in enumerate(pairs) if m.get("event") == "output_sealed")
        prefix = pairs[:seal]
        sid = pairs[seal - 1][1]["session_id"]
        new = [("worker", {"event": "link_state", "state": "up"}),
               ("core", {"cmd": "ping", "id": 1})]
        hello = copy.deepcopy(pairs[5])  # the connection's 0.8 hello, replayed on the reattached peer
        sealed_ack = ("worker", {"event": "hello_ack", "session_id": sid, "proto_version": "0.8",
                                 "first_buffered_seq": 1, "first_buffered_offset": 0, "current_seq": 0,
                                 "ended": True, "output_seal": pairs[seal][1]["output_seal"]})
        # A late barrier reaching a reattached peer that has sent no hello stating 0.8 is a worker fault (§3.1).
        stale = prefix + new + [pairs[seal]]
        self.assertTrue(_ipc.conn_order(stale, ["a"] * len(prefix) + ["b"] * 3))
        # A fresh 0.8 hello on the new connection makes its peer a 0.8 peer; the carried barrier is then accepted.
        accepted = prefix + new + [hello, pairs[seal], sealed_ack]
        self.assertFalse(_ipc.conn_order(accepted, ["a"] * len(prefix) + ["b"] * 5))
        # A fresh seal request on the new connection, answered by the barrier after a 0.8 hello, is accepted.
        fresh = prefix + new + [hello, sealed_ack, pairs[seal - 1], pairs[seal]]
        self.assertFalse(_ipc.conn_order(fresh, ["a"] * len(prefix) + ["b"] * 6))

    def test_duplicate_seal_commands_each_receive_identical_result(self):
        pairs = self.trace()
        i = next(i for i, (_, m) in enumerate(pairs) if m.get("cmd") == "seal_output")
        doubled = pairs[:i] + [pairs[i], pairs[i], pairs[i+1], pairs[i+1]] + pairs[i+2:]
        self.assertFalse(_ipc.conn_order(doubled))

    def test_close_abandons_paused_handshake_in_future_version(self):
        pairs = self.trace()
        i = next(i for i, (_, m) in enumerate(pairs) if m.get("cmd") == "seal_output")
        prefix = copy.deepcopy(pairs[:i])
        prefix[-1][1]["ended"] = False
        sid = prefix[-1][1]["session_id"]
        close = [("core", {"cmd": "close_channel", "session_id": sid}),
                 ("worker", {"event": "channel_closed", "session_id": sid, "by": "core"})]
        # A fresh hello after close must not inherit the abandoned paused handshake.
        trace = prefix + close + copy.deepcopy(pairs[5:7]) + pairs[i:]
        for _, msg in trace:
            if "proto_version" in msg:
                msg["proto_version"] = "0.9"
        self.assertFalse(_ipc.conn_order(trace, version="0.9"))

    def test_code9_is_unique_and_only_original_end_may_follow(self):
        # The marker's counters must match the recording walk before it (§6), so the one output byte
        # (seq 1, bytes 1) is what the marker claims; its header carries observed_ns 1's sec/usec.
        marker = bf.RecFrame(0, 0, bf.TERMINAL_INCOMPLETE,
                             bf.terminal_incomplete_line(1, 1, "core_stop", 1))
        out = bf.RecFrame(1, 0, bf.OUT, b"a")
        end = bf.RecFrame(1, 0, bf.END_KILLED, b"")
        self.assertTrue(bf.recording_problems([marker, out]))
        self.assertTrue(bf.recording_problems([marker, marker, end]))
        self.assertFalse(bf.recording_problems([out, marker, end]))
        self.assertFalse(bf.recording_problems([out, marker]))


class RoundThree(unittest.TestCase):
    def preamble(self, sid):
        return [("core", {"cmd": "connect", "target_id": "t", "protocol": "echo"}),
                ("worker", {"event": "connected", "conn_id": "c"}),
                ("core", {"cmd": "open_shell", "session_id": sid, "term": "xterm", "cols": 80, "rows": 24}),
                ("worker", {"event": "channel_opened", "session_id": sid, "session_type": "shell",
                             "socket": f"{sid}.sock"})]

    def ack(self, sid, ended):
        return ("worker", {"event": "hello_ack", "session_id": sid, "proto_version": "0.8",
                           "first_buffered_seq": 1, "first_buffered_offset": 0, "current_seq": 0,
                           "ended": ended})

    def test_close_keeps_a_handshake_awaiting_its_hello_ack(self):
        sid = "a"
        trace = self.preamble(sid) + [
            ("core", {"cmd": "hello", "session_id": sid, "proto_version": "0.8", "last_committed_seq": 0}),
            ("core", {"cmd": "close_channel", "session_id": sid}),
            self.ack(sid, False),
            ("worker", {"event": "channel_closed", "session_id": sid, "by": "core"})]
        reattach = [text for rule, text in _ipc.conn_order(trace, version="0.8") if rule == _ipc.REATTACH]
        self.assertFalse(reattach, reattach)

    def test_close_keeps_a_handshake_mid_replay(self):
        sid = "a"
        trace = self.preamble(sid) + [
            ("core", {"cmd": "hello", "session_id": sid, "proto_version": "0.8", "last_committed_seq": 0}),
            self.ack(sid, False),
            ("core", {"cmd": "replay", "session_id": sid, "from_seq": 1}),
            ("core", {"cmd": "close_channel", "session_id": sid}),
            ("worker", {"event": "replay_done", "session_id": sid, "through_seq": 0}),
            ("worker", {"event": "channel_closed", "session_id": sid, "by": "core"})]
        reattach = [text for rule, text in _ipc.conn_order(trace, version="0.8") if rule == _ipc.REATTACH]
        self.assertFalse(reattach, reattach)


class RoundFive(unittest.TestCase):
    SID = "fixture-session-a"

    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.from_tree(CONTRACT)

    def trace(self):
        path = CONTRACT / "fixtures/C3/reattach-ended-audited-sealed.json"
        return [(m["from"], m["msg"]) for m in json.loads(path.read_text())["case"]["messages"]]

    def last_ack(self, trace):
        return max(i for i, (_, m) in enumerate(trace) if m.get("event") == "hello_ack")

    def test_seal_reply_is_a_successful_seal(self):
        reply = {"event": "output_sealed", "session_id": self.SID,
                 "output_seal": {"sealed": True, "read_outcome": "eof", "final_last_seq": 0,
                                  "final_out_bytes": 0, "final_last_out_ts": 0,
                                  "observed_at_ns": 1794708000123456789, "unknown_suffix": False}}
        self.assertFalse(self.registry.validate(reply, "schemas/ipc-control.schema.json"))
        preliminary = {"event": "output_sealed", "session_id": self.SID, "output_seal": {"sealed": False}}
        problems = self.registry.validate(preliminary, "schemas/ipc-control.schema.json")
        self.assertTrue(problems, problems)
        summary = {"event": "hello_ack", "session_id": self.SID, "proto_version": "0.8",
                   "first_buffered_seq": 1, "first_buffered_offset": 0, "current_seq": 0,
                   "ended": True, "output_seal": {"sealed": False}}
        self.assertFalse(self.registry.validate(summary, "schemas/ipc-control.schema.json"))

    def test_disposal_needs_the_seal_summary_on_the_replay(self):
        pairs = self.trace()
        self.assertFalse(_ipc.conn_order(pairs))
        for seal in ({}, {"sealed": False}):
            trace = copy.deepcopy(pairs)
            trace[self.last_ack(trace)][1].pop("output_seal", None)
            if seal:
                trace[self.last_ack(trace)][1]["output_seal"] = seal
            problems = [text for _, text in _ipc.conn_order(trace)]
            self.assertTrue(any("no sealed summary" in text for text in problems), problems)

    def test_hello_answered_while_sealing_is_a_problem(self):
        pairs = self.trace()
        seal = next(i for i, (_, m) in enumerate(pairs) if m.get("cmd") == "seal_output")
        prefix, hello, ack = copy.deepcopy(pairs[:seal + 1]), copy.deepcopy(pairs[5]), copy.deepcopy(pairs[6])
        busy = ("worker", {"event": "error", "session_id": self.SID, "code": "terminal_busy",
                           "message": "terminal operation in progress"})
        # terminal_busy answers the hello sent during sealing, so no handshake fault; the capture ends
        # with the seal still in flight, which ruling 2 reports as an ORDER problem — that is the only line.
        busy_only = _ipc.conn_order(prefix + [hello, busy])
        self.assertTrue(busy_only)
        self.assertTrue(all(rule == _ipc.ORDER and "seal_output for session" in text
                            for rule, text in busy_only), busy_only)
        acked = prefix + [hello, ack]
        problems = [text for rule, text in _ipc.conn_order(acked) if rule == _ipc.ORDER]
        self.assertTrue(any("while it is sealing" in text for text in problems), problems)
        replay = ("core", {"cmd": "replay", "session_id": self.SID, "from_seq": 1})
        later = [text for rule, text in _ipc.conn_order(acked + [replay]) if rule == _ipc.REATTACH]
        self.assertTrue(any("before its hello_ack" in text for text in later), later)

    def test_fixture_notes_name_the_peer_the_capture_negotiates(self):
        fixture = json.loads((CONTRACT / "fixtures/C3/reattach-ended-audited-sealed.json").read_text())
        messages = fixture["case"]["messages"]
        notes = {m["msg"]["cmd"]: m.get("note", "") for m in messages if m["from"] == "core" and "note" in m}
        hostkey = next(m["note"] for m in messages if m["msg"].get("event") == "hostkey")
        self.assertIn("0.8", notes["connect"])
        self.assertNotIn("0.6", notes["connect"])
        self.assertIn("0.8", hostkey)
        self.assertNotIn("0.6", hostkey)


class RoundSix(unittest.TestCase):
    SID = "fixture-session-a"
    SEAL: ClassVar[dict] = {"sealed": True, "read_outcome": "eof", "final_last_seq": 0, "final_out_bytes": 0,
            "final_last_out_ts": 0, "observed_at_ns": 1794708000123456789, "unknown_suffix": False}

    def ssh_trace(self):
        path = CONTRACT / "fixtures/C3/reattach-ended-audited-sealed.json"
        return [(m["from"], m["msg"]) for m in json.loads(path.read_text())["case"]["messages"]]

    def telnet_trace(self):
        sid = self.SID
        return [
            ("core", {"cmd": "connect", "target_id": "fixture-target", "protocol": "telnet"}),
            ("worker", {"event": "connected", "conn_id": "fixture-conn-1"}),
            ("core", {"cmd": "open_shell", "session_id": sid, "term": "xterm-256color", "cols": 120, "rows": 40}),
            ("worker", {"event": "channel_opened", "session_id": sid, "session_type": "shell", "socket": "shell.sock"}),
            ("core", {"cmd": "hello", "session_id": sid, "proto_version": "0.8", "last_committed_seq": 0}),
            ("worker", {"event": "hello_ack", "session_id": sid, "proto_version": "0.8", "first_buffered_seq": 1,
                        "first_buffered_offset": 0, "current_seq": 0, "ended": True}),
            ("core", {"cmd": "seal_output", "session_id": sid}),
            ("worker", {"event": "output_sealed", "session_id": sid, "output_seal": self.SEAL}),
            ("core", {"cmd": "hello", "session_id": sid, "proto_version": "0.8", "last_committed_seq": 0}),
            ("worker", {"event": "hello_ack", "session_id": sid, "proto_version": "0.8", "first_buffered_seq": 1,
                        "first_buffered_offset": 0, "current_seq": 0, "ended": True, "output_seal": self.SEAL}),
            ("core", {"cmd": "replay", "session_id": sid, "from_seq": 1}),
            ("worker", {"event": "replay_done", "session_id": sid, "through_seq": 0}),
            ("core", {"cmd": "release_accounted", "session_id": sid}),
        ]

    def single_session_flags(self, problems):
        return [text for _, text in problems if "single_session" in text]

    def test_accounted_release_owes_the_single_session_report(self):
        base = self.telnet_trace()
        # The one session disposed through the accounted path ends the telnet connection: its report is owed.
        self.assertTrue(self.single_session_flags(_ipc.conn_order(base)), _ipc.conn_order(base))
        reported = base + [("worker", {"event": "connection_lost", "conn_id": "fixture-conn-1",
                                       "cause": "single_session", "session_ids": []})]
        self.assertFalse(_ipc.conn_order(reported), _ipc.conn_order(reported))

    def test_the_report_rule_is_telnet_only_and_leaves_ssh_unaffected(self):
        ssh = self.ssh_trace()
        # SSH carries many sessions and its accounted disposal owes no single_session report.
        self.assertFalse(self.single_session_flags(_ipc.conn_order(ssh)), _ipc.conn_order(ssh))
        self.assertTrue(self.single_session_flags(_ipc.conn_order(self.telnet_trace())),
                        _ipc.conn_order(self.telnet_trace()))


class RoundSeven(unittest.TestCase):
    SID = "fixture-session-a"

    def preamble(self, sid):
        return [("core", {"cmd": "connect", "target_id": "t", "protocol": "echo"}),
                ("worker", {"event": "connected", "conn_id": "c"}),
                ("core", {"cmd": "open_shell", "session_id": sid, "term": "xterm", "cols": 80, "rows": 24}),
                ("worker", {"event": "channel_opened", "session_id": sid, "session_type": "shell",
                             "socket": f"{sid}.sock"})]

    def hello(self, sid):
        return ("core", {"cmd": "hello", "session_id": sid, "proto_version": "0.8", "last_committed_seq": 0})

    def ack(self, sid, ended):
        return ("worker", {"event": "hello_ack", "session_id": sid, "proto_version": "0.8",
                           "first_buffered_seq": 1, "first_buffered_offset": 0, "current_seq": 0,
                           "ended": ended})

    def busy(self, sid):
        return ("worker", {"event": "error", "session_id": sid, "code": "terminal_busy",
                           "message": "terminal operation in progress"})

    def busy_flags(self, trace, **kwargs):
        return [text for rule, text in _ipc.conn_order(trace, **kwargs)
                if rule == _ipc.ORDER and "terminal_busy" in text]

    def test_terminal_busy_for_an_ordinary_live_hello_is_flagged(self):
        sid = "a"
        trace = self.preamble(sid) + [self.hello(sid), self.busy(sid)]
        self.assertTrue(self.busy_flags(trace, version="0.8"), _ipc.conn_order(trace, version="0.8"))

    def test_terminal_busy_for_a_sealing_session(self):
        path = CONTRACT / "fixtures/C3/reattach-ended-audited-sealed.json"
        pairs = [(m["from"], m["msg"]) for m in json.loads(path.read_text())["case"]["messages"]]
        seal = next(i for i, (_, m) in enumerate(pairs) if m.get("cmd") == "seal_output")
        hello = copy.deepcopy(pairs[5])
        # While the seal is in flight the refusal is the permitted answer; the barrier completes the flow.
        during = copy.deepcopy(pairs[:seal + 1]) + [hello, self.busy(self.SID)] + pairs[seal + 1:]
        self.assertFalse(_ipc.conn_order(during), _ipc.conn_order(during))
        # Once the seal completed there is no terminal operation left to be busy with.
        after = copy.deepcopy(pairs[:seal + 2]) + [hello, self.busy(self.SID)]
        self.assertTrue(self.busy_flags(after), _ipc.conn_order(after))

    def test_terminal_busy_for_a_session_with_a_core_requested_end_in_flight(self):
        sid = "a"
        close = ("core", {"cmd": "close_channel", "session_id": sid})
        closed = ("worker", {"event": "channel_closed", "session_id": sid, "by": "core"})
        in_flight = (self.preamble(sid) + [self.hello(sid), self.ack(sid, False)]
                     + [close, self.hello(sid), self.busy(sid), closed, self.hello(sid), self.ack(sid, True)])
        self.assertFalse(_ipc.conn_order(in_flight, version="0.8"), _ipc.conn_order(in_flight, version="0.8"))
        live = (self.preamble(sid) + [self.hello(sid), self.ack(sid, False),
                                      ("core", {"cmd": "replay", "session_id": sid, "from_seq": 1}),
                                      ("worker", {"event": "replay_done", "session_id": sid, "through_seq": 0}),
                                      ("core", {"cmd": "live", "session_id": sid})])
        # A live session with no core-requested end in flight may not be refused terminal_busy.
        self.assertTrue(self.busy_flags(live + [self.hello(sid), self.busy(sid)], version="0.8"))


class RoundNineB(unittest.TestCase):
    """Round 9b: Qodo's two checker corrections, ruling 2 amended to option 1 (§3 line 70)."""

    SID = "a"

    def preamble(self, sid):
        return [("core", {"cmd": "connect", "target_id": "t", "protocol": "echo"}),
                ("worker", {"event": "connected", "conn_id": "c"}),
                ("core", {"cmd": "open_shell", "session_id": sid, "term": "xterm", "cols": 80, "rows": 24}),
                ("worker", {"event": "channel_opened", "session_id": sid, "session_type": "shell",
                             "socket": f"{sid}.sock"})]

    def hello(self, sid):
        return ("core", {"cmd": "hello", "session_id": sid, "proto_version": "0.8", "last_committed_seq": 0})

    def ack(self, sid):
        return ("worker", {"event": "hello_ack", "session_id": sid, "proto_version": "0.8",
                           "first_buffered_seq": 1, "first_buffered_offset": 0, "current_seq": 0, "ended": True})

    def seal(self, sid):
        return ("core", {"cmd": "seal_output", "session_id": sid})

    def error(self, sid, code):
        return ("worker", {"event": "error", "session_id": sid, "code": code, "message": "refused"})

    def seal_base(self):
        return self.preamble(self.SID) + [self.hello(self.SID), self.ack(self.SID), self.seal(self.SID)]

    def order_flags(self, trace, needle):
        return [text for rule, text in _ipc.conn_order(trace, version="0.8")
                if rule == _ipc.ORDER and needle in text]

    def test_capture_ending_with_a_pending_seal_is_flagged(self):
        # §3 / §9A: seal_output ends the handshake and owes its answer; a capture that ends with the
        # seal pending is an ORDER problem, as an unanswered hello or replay is.
        problems = self.order_flags(self.seal_base(), "seal_output for session")
        self.assertTrue(problems, _ipc.conn_order(self.seal_base(), version="0.8"))

    def test_seal_answered_with_an_unlisted_code_is_flagged(self):
        # §3 line 70 lists only bad_request and no_such_session as seal_output refusals; no_data_peer
        # answering a seal is flagged as an ORDER problem for the seal, not merely as answering no hello.
        problems = self.order_flags(self.seal_base() + [self.error(self.SID, "no_data_peer")], "seal_output")
        self.assertTrue(problems, _ipc.conn_order(self.seal_base() + [self.error(self.SID, "no_data_peer")],
                                                  version="0.8"))

    def test_seal_answered_with_bad_request_is_not_flagged(self):
        # §3 line 70 / §9 B: a target close racing the seal answers bad_request; it is rediscovery, not a fault.
        trace = self.seal_base() + [self.error(self.SID, "bad_request")]
        self.assertFalse(_ipc.conn_order(trace, version="0.8"), _ipc.conn_order(trace, version="0.8"))

    def test_seal_answered_with_no_such_session_is_not_flagged(self):
        # §3 line 70: on an absent session seal_output refuses no_such_session; the retained record is dropped.
        trace = self.seal_base() + [self.error(self.SID, "no_such_session")]
        self.assertFalse(_ipc.conn_order(trace, version="0.8"), _ipc.conn_order(trace, version="0.8"))


class RoundTenB(unittest.TestCase):
    """Round 10b: the committed gap's sequence is not in the recording, so once the walk passes a code-2
    marker L is unknown; a code-9 marker's bytes are always compared and its seq only while L is known.
    No sequence is ever invented (terminal-output-accounting §4)."""

    OBSERVED = 1794708000123456789
    HEADER = (1794708000, 123456)  # OBSERVED's sec/usec

    def frames(self, seq: int, count: int):
        # output frames 1-4 (16 bytes), a code-2 gap marker (10 bytes lost), one output frame (1 byte):
        # the walk through them is B=27 with L unknown past the gap; the code-9 marker claims seq/count.
        outs = [bf.RecFrame(i, 0, bf.OUT, b"x" * 4) for i in range(1, 5)]
        gap = bf.RecFrame(5, 0, bf.GAP, bf.gap_line(10, (4, 0), (5, 0)))
        last = bf.RecFrame(6, 0, bf.OUT, b"y")
        marker = bf.RecFrame(*self.HEADER, bf.TERMINAL_INCOMPLETE,
                             bf.terminal_incomplete_line(seq, count, "core_stop", self.OBSERVED))
        return outs + [gap, last, marker]

    def test_right_bytes_and_seq_11_after_a_gap_is_not_flagged(self):
        # seq 11 cannot be established past the code-2 gap (the committed gap's seq is not in the recording),
        # so bytes-only match is enough: B=27, L unknown. On ca073db the walk computed L=5 and flagged seq 11.
        problems = bf.recording_problems(self.frames(11, 27))
        self.assertFalse(problems, problems)

    def test_wrong_bytes_after_a_gap_is_flagged_with_L_unknown(self):
        # bytes are always compared: a marker claiming 26 against the walk's 27 is flagged even past a gap.
        problems = bf.recording_problems(self.frames(11, 26))
        self.assertTrue(any("walk" in text for text in problems), problems)


class RoundElevenB(unittest.TestCase):
    """Round 11b carried worker state, corrected by round 12: `sealing` and `closing` stay per-run,
    but terminal barriers may finish their carried drains on the restarted control connection
    (§3.1, §9 B). Accepted hello and session absence also settle the carried state."""

    SID = "a"
    SEAL: ClassVar[dict] = {"sealed": True, "read_outcome": "eof", "final_last_seq": 0, "final_out_bytes": 0,
            "final_last_out_ts": 0, "observed_at_ns": 1794708000123456789, "unknown_suffix": False}

    def preamble(self, sid):
        return [("core", {"cmd": "connect", "target_id": "t", "protocol": "echo"}),
                ("worker", {"event": "connected", "conn_id": "c"}),
                ("core", {"cmd": "open_shell", "session_id": sid, "term": "xterm", "cols": 80, "rows": 24}),
                ("worker", {"event": "channel_opened", "session_id": sid, "session_type": "shell",
                             "socket": f"{sid}.sock"})]

    def hello(self, sid):
        return ("core", {"cmd": "hello", "session_id": sid, "proto_version": "0.8", "last_committed_seq": 0})

    def ack(self, sid, ended):
        return ("worker", {"event": "hello_ack", "session_id": sid, "proto_version": "0.8",
                           "first_buffered_seq": 1, "first_buffered_offset": 0, "current_seq": 0,
                           "ended": ended})

    def busy(self, sid):
        return ("worker", {"event": "error", "session_id": sid, "code": "terminal_busy",
                           "message": "terminal operation in progress"})

    def ack_seal(self, sid):
        return ("worker", {"event": "hello_ack", "session_id": sid, "proto_version": "0.8",
                           "first_buffered_seq": 1, "first_buffered_offset": 0, "current_seq": 0,
                           "ended": True, "output_seal": self.SEAL})

    def reattach(self):
        # The restarted core's own first act on a reattached connection: link_state from the worker,
        # then the core's first ping before its hello (core-restart step 22).
        return [("worker", {"event": "link_state", "state": "up"}), ("core", {"cmd": "ping", "n": 1})]

    def seal_prefix(self):
        # The fixture's capture up to and including its seal_output, and the output_sealed pair that
        # answers it; both for the fixture's own session id.
        path = CONTRACT / "fixtures/C3/reattach-ended-audited-sealed.json"
        pairs = [(m["from"], m["msg"]) for m in json.loads(path.read_text())["case"]["messages"]]
        seal = next(i for i, (_, m) in enumerate(pairs) if m.get("cmd") == "seal_output")
        return copy.deepcopy(pairs[:seal + 1]), copy.deepcopy(pairs[seal + 1])

    def busy_flags(self, trace, runs):
        problems = _ipc.conn_order(trace, runs, version="0.8")
        return [text for rule, text in problems if rule == _ipc.ORDER and "terminal_busy" in text], problems

    def test_carried_seal_makes_the_restarted_hello_busy_accepted(self):
        # Run 1 sends seal_output; the core restarts; run 2's hello is answered terminal_busy, and that
        # refusal has no fault. On a2a4c10 this fails with ORDER "terminal_busy for session ... only while
        # the session is sealing ...", because the boundary cleared sealing.
        run1, _ = self.seal_prefix()
        sid = run1[-1][1]["session_id"]
        run2 = self.reattach() + [self.hello(sid), self.busy(sid)]
        runs = ["r1"] * len(run1) + ["r2"] * len(run2)
        _flags, problems = self.busy_flags(run1 + run2, runs)
        self.assertFalse(problems, problems)

    def test_fresh_seal_answers_the_drain_and_a_later_busy_is_flagged(self):
        # After the carried busy, run 2 sends its own seal_output and output_sealed answers it on this
        # connection; the session leaves the carried set, so a further terminal_busy on a hello for it —
        # nothing pending, nothing carried — is flagged. On a2a4c10 the first busy is flagged too.
        run1, sealed = self.seal_prefix()
        sid = run1[-1][1]["session_id"]
        run2 = (self.reattach() + [self.hello(sid), self.busy(sid),
                                   ("core", {"cmd": "seal_output", "session_id": sid}), sealed,
                                   self.hello(sid), self.busy(sid)])
        runs = ["r1"] * len(run1) + ["r2"] * len(run2)
        flags, problems = self.busy_flags(run1 + run2, runs)
        self.assertEqual(len(flags), 1, problems)

    def test_carried_close_is_cleared_by_channel_closed_and_a_later_busy_is_flagged(self):
        # The same for a pending close_channel: run 2's hello answered terminal_busy has no fault; run 2's
        # own close_channel answered by channel_closed by: core has no fault and clears the carried drain,
        # so a further busy on a hello is flagged. On a2a4c10 the first busy is flagged too.
        run1 = self.preamble(self.SID) + [self.hello(self.SID), self.ack(self.SID, False),
                                           ("core", {"cmd": "close_channel", "session_id": self.SID})]
        closed = ("worker", {"event": "channel_closed", "session_id": self.SID, "by": "core"})
        run2 = (self.reattach() + [self.hello(self.SID), self.busy(self.SID),
                                   ("core", {"cmd": "close_channel", "session_id": self.SID}), closed,
                                   self.hello(self.SID), self.busy(self.SID)])
        runs = ["r1"] * len(run1) + ["r2"] * len(run2)
        flags, problems = self.busy_flags(run1 + run2, runs)
        self.assertEqual(len(flags), 1, problems)

    def test_release_clears_the_carried_drain(self):
        # The release clause: a session leaves the carried set when it is released, so the busy after the
        # release is flagged. On a2a4c10 the carried busy before the release is flagged too.
        run1, _ = self.seal_prefix()
        sid = run1[-1][1]["session_id"]
        run2 = (self.reattach() + [self.hello(sid), self.busy(sid), ("core", {"cmd": "release", "session_id": sid}),
                                   self.hello(sid), self.busy(sid)])
        runs = ["r1"] * len(run1) + ["r2"] * len(run2)
        flags, problems = self.busy_flags(run1 + run2, runs)
        self.assertEqual(len(flags), 1, problems)

    def test_busy_with_nothing_pending_or_carried_is_flagged(self):
        # A session that was never sealing or closing in an earlier run is not carried: a hello in the
        # later run of a live session is refused terminal_busy, and that refusal stays an ORDER fault.
        run1 = self.preamble(self.SID) + [self.hello(self.SID), self.ack(self.SID, False)]
        run2 = self.reattach() + [self.hello(self.SID), self.busy(self.SID)]
        runs = ["r1"] * len(run1) + ["r2"] * len(run2)
        flags, problems = self.busy_flags(run1 + run2, runs)
        self.assertEqual(len(flags), 1, problems)


class RoundTwelve(RoundElevenB):
    """Restart barriers and worker evidence settle carried drains (§3.1, §9 B)."""

    def test_carried_seal_reply_after_a_fresh_08_hello(self):
        # A carried seal is settled by its barrier once the reattached peer has stated 0.8 in a hello; the same
        # barrier reaching a peer that sent no 0.8 hello is flagged (§3.1).
        run1, sealed = self.seal_prefix()
        sid = run1[-1][1]["session_id"]
        run2 = self.reattach() + [self.hello(sid), sealed, self.ack_seal(sid)]
        self.assertFalse(_ipc.conn_order(run1 + run2, ["r1"] * len(run1) + ["r2"] * len(run2)))

    def test_carried_close_reply_without_fresh_request(self):
        run1 = self.preamble(self.SID) + [self.hello(self.SID), self.ack(self.SID, False),
                                        ("core", {"cmd": "close_channel", "session_id": self.SID})]
        run2 = self.reattach() + [("worker", {"event": "channel_closed", "session_id": self.SID, "by": "core"})]
        self.assertFalse(_ipc.conn_order(run1 + run2, ["r1"] * len(run1) + ["r2"] * len(run2)))

    def test_worker_evidence_ends_carried_drain(self):
        # Round 14 (the orchestrator, 2026-11-15 21:5x): a carried seal is settled only with proof
        # (output_seal with sealed: true); replacing round 12's assertion that any hello_ack settles it.
        sealed_ack = ("worker", {"event": "hello_ack", "session_id": "fixture-session-a", "proto_version": "0.8",
                                 "first_buffered_seq": 1, "first_buffered_offset": 0, "current_seq": 0,
                                 "ended": True, "output_seal": self.SEAL})
        for evidence in (sealed_ack,
                         ("worker", {"event": "error", "session_id": "fixture-session-a",
                                     "code": "no_such_session", "message": "absent"})):
            with self.subTest(evidence=evidence):
                run1, _ = self.seal_prefix()
                sid = run1[-1][1]["session_id"]
                run2 = self.reattach() + [self.hello(sid), evidence]
                # A further restart drops the paused handshake, but must not resurrect the drain.
                run3 = self.reattach() + [self.hello(sid), self.busy(sid)]
                flags, problems = self.busy_flags(run1 + run2 + run3,
                                                  ["r1"] * len(run1) + ["r2"] * len(run2) + ["r3"] * len(run3))
                self.assertEqual(len(flags), 1, problems)

    def test_unsolicited_barriers_stay_faults(self):
        for barrier in (("worker", {"event": "output_sealed", "session_id": self.SID, "output_seal": self.SEAL}),
                        ("worker", {"event": "channel_closed", "session_id": self.SID, "by": "core"})):
            with self.subTest(barrier=barrier):
                self.assertTrue(_ipc.conn_order(self.preamble(self.SID) + [barrier], version="0.8"))

    def test_code9_artifact_scope(self):
        marker = bf.RecFrame(0, 0, bf.TERMINAL_INCOMPLETE,
                             bf.terminal_incomplete_line(0, 0, "core_stop", 1))
        frames = [marker, bf.RecFrame(1, 0, bf.END_KILLED, b"")]
        self.assertFalse(bf.recording_problems(frames))
        for kind in ("sftp", "shell"):
            with self.subTest(kind=kind), patch.object(artifacts, "_recordings", return_value=({"a": frames}, [])), \
                    patch.object(artifacts, "_session_types", return_value={"a": kind}):
                ok, detail = artifacts.recordings_parse(None, None)
                self.assertEqual(ok, kind == "shell", detail)


class RoundThirteen(RoundElevenB):
    """Carried seal and close barriers are tracked apart (the orchestrator, 2026-11-15 21:0x)."""

    def test_interrupted_close_refuses_output_sealed(self):
        # Qodo finding on 7104fe0: after a restart interrupting only close_channel, an output_sealed
        # with no seal ever requested is refused as the wrong barrier.
        run1 = self.preamble(self.SID) + [self.hello(self.SID), self.ack(self.SID, False),
                                          ("core", {"cmd": "close_channel", "session_id": self.SID})]
        seal = ("worker", {"event": "output_sealed", "session_id": self.SID, "output_seal": self.SEAL})
        run2 = self.reattach() + [seal]
        problems = _ipc.conn_order(run1 + run2, ["r1"] * len(run1) + ["r2"] * len(run2), version="0.8")
        self.assertTrue(any(r == _ipc.REATTACH and "no pending seal" in text for r, text in problems), problems)

    def test_interrupted_seal_accepts_output_sealed_after_a_fresh_08_hello(self):
        # A restart interrupting a seal_output accepts output_sealed on the reattached control connection once its
        # peer has stated 0.8 in a hello; before any such hello the barrier is flagged (§3.1).
        run1, sealed = self.seal_prefix()
        sid = run1[-1][1]["session_id"]
        run2 = self.reattach() + [self.hello(sid), sealed, self.ack_seal(sid)]
        self.assertFalse(_ipc.conn_order(run1 + run2, ["r1"] * len(run1) + ["r2"] * len(run2), version="0.8"))

    def test_carried_close_cleared_by_channel_closed_by_core(self):
        # A carried close is cleared by channel_closed by: core; a subsequent hello answered terminal_busy
        # is flagged because the carried close was cleared.
        run1 = self.preamble(self.SID) + [self.hello(self.SID), self.ack(self.SID, False),
                                          ("core", {"cmd": "close_channel", "session_id": self.SID})]
        closed = ("worker", {"event": "channel_closed", "session_id": self.SID, "by": "core"})
        run2 = self.reattach() + [self.hello(self.SID), self.busy(self.SID),
                                  closed,
                                  self.hello(self.SID), self.busy(self.SID)]
        runs = ["r1"] * len(run1) + ["r2"] * len(run2)
        flags, problems = self.busy_flags(run1 + run2, runs)
        self.assertEqual(len(flags), 1, problems)


class RoundFourteen(RoundElevenB):
    """Round 14: Qodo's two findings on a6533d9 (the orchestrator, 2026-11-15 21:5x).
    1. hello_ack settles a carried drain only with proof: output_seal with sealed: true
       for a carried seal, ended: true for a carried close; without proof it is flagged
       (worker should have answered terminal_busy).
    2. An error no_such_session answers a carried seal (session gone), removes it from
       the set and is not reported as 'answers no hello'.
    """

    def test_carried_seal_hello_ack_without_sealed_summary_is_flagged(self):
        # A carried seal followed by a hello_ack without a sealed summary is a fault;
        # the worker should have answered terminal_busy (§3.1).
        run1, _ = self.seal_prefix()
        sid = run1[-1][1]["session_id"]
        run2 = self.reattach() + [self.hello(sid), self.ack(sid, True)]
        problems = _ipc.conn_order(run1 + run2, ["r1"] * len(run1) + ["r2"] * len(run2), version="0.8")
        self.assertTrue(any(r == _ipc.ORDER and "terminal_busy" in text for r, text in problems), problems)

    def test_carried_seal_hello_ack_with_sealed_summary_is_accepted(self):
        # A carried seal followed by a hello_ack carrying output_seal with sealed: true is accepted
        # and settles the carried drain.
        run1, _ = self.seal_prefix()
        sid = run1[-1][1]["session_id"]
        sealed_ack = ("worker", {"event": "hello_ack", "session_id": sid, "proto_version": "0.8",
                                 "first_buffered_seq": 1, "first_buffered_offset": 0, "current_seq": 0,
                                 "ended": True, "output_seal": self.SEAL})
        run2 = self.reattach() + [self.hello(sid), sealed_ack]
        problems = _ipc.conn_order(run1 + run2, ["r1"] * len(run1) + ["r2"] * len(run2), version="0.8")
        self.assertFalse(problems, problems)

    def test_carried_close_hello_ack_without_ended_true_is_flagged(self):
        # A carried close followed by a hello_ack without ended: true is a fault;
        # the worker should have answered terminal_busy (§3.1).
        run1 = self.preamble(self.SID) + [self.hello(self.SID), self.ack(self.SID, False),
                                          ("core", {"cmd": "close_channel", "session_id": self.SID})]
        run2 = self.reattach() + [self.hello(self.SID), self.ack(self.SID, False)]
        problems = _ipc.conn_order(run1 + run2, ["r1"] * len(run1) + ["r2"] * len(run2), version="0.8")
        self.assertTrue(any(r == _ipc.ORDER and "terminal_busy" in text for r, text in problems), problems)

    def test_carried_close_hello_ack_with_ended_true_is_accepted(self):
        # A carried close followed by a hello_ack with ended: true is accepted
        # and settles the carried drain.
        run1 = self.preamble(self.SID) + [self.hello(self.SID), self.ack(self.SID, False),
                                          ("core", {"cmd": "close_channel", "session_id": self.SID})]
        run2 = self.reattach() + [self.hello(self.SID), self.ack(self.SID, True)]
        problems = _ipc.conn_order(run1 + run2, ["r1"] * len(run1) + ["r2"] * len(run2), version="0.8")
        self.assertFalse(problems, problems)

    def test_carried_seal_answered_no_such_session_in_next_run_is_not_flagged(self):
        # An error no_such_session answers a carried seal_output directly and is not reported
        # as answering no hello (ruling 2).
        run1, _ = self.seal_prefix()
        sid = run1[-1][1]["session_id"]
        run2 = self.reattach() + [("worker", {"event": "error", "session_id": sid,
                                              "code": "no_such_session", "message": "absent"})]
        problems = _ipc.conn_order(run1 + run2, ["r1"] * len(run1) + ["r2"] * len(run2), version="0.8")
        self.assertFalse(problems, problems)




class LegacyOfferMidSeal(RoundElevenB):
    """terminal-output-accounting §3.1: a seal carried across a restart whose new core
    offers a version below 0.8. The worker answers that hello unsupported_proto while the seal is carried,
    sends the 0.8-only events only to a peer that stated 0.8, and once sealed presents the session by the
    ordinary legacy handshake (a hello_ack with no output_seal) and legacy release."""

    def legacy_hello(self, sid):
        return ("core", {"cmd": "hello", "session_id": sid, "proto_version": "0.7", "last_committed_seq": 0})

    def legacy_ack(self, sid, ended):
        return ("worker", {"event": "hello_ack", "session_id": sid, "proto_version": "0.7",
                           "first_buffered_seq": 1, "first_buffered_offset": 0, "current_seq": 0,
                           "ended": ended})

    def refused(self, sid):
        return ("worker", {"event": "error", "session_id": sid, "code": "unsupported_proto",
                           "message": "unsupported protocol version"})

    def check(self, run2, run3=None):
        run1, _ = self.seal_prefix()
        runs = ["r1"] * len(run1) + ["r2"] * len(run2)
        trace = run1 + run2
        if run3 is not None:
            trace += run3
            runs += ["r3"] * len(run3)
        return _ipc.conn_order(trace, runs, version="0.8")

    def test_legacy_handshake_after_sealing_is_accepted(self):
        # While carried: unsupported_proto naming the session. Once sealed: the legacy hello_ack, replay and
        # legacy release. The retry comes at the legacy core's next start (run 3), not in the same run: a running
        # legacy core sends no second hello (§3.1). Before this fix the legacy hello_ack was flagged "should have
        # answered terminal_busy", which B forbids toward a peer below 0.8.
        sid = self.seal_prefix()[0][-1][1]["session_id"]
        run2 = self.reattach() + [self.legacy_hello(sid), self.refused(sid)]
        run3 = self.reattach() + [self.legacy_hello(sid), self.legacy_ack(sid, True),
                                  ("core", {"cmd": "replay", "session_id": sid, "from_seq": 1}),
                                  ("worker", {"event": "replay_done", "session_id": sid, "through_seq": 0}),
                                  ("core", {"cmd": "release", "session_id": sid})]
        problems = self.check(run2, run3)
        self.assertFalse(problems, problems)

    def test_legacy_ack_saying_live_for_a_carried_seal_is_flagged(self):
        # A sealed session is ended: a legacy hello_ack for a carried seal that says ended: false is a fault.
        sid = self.seal_prefix()[0][-1][1]["session_id"]
        problems = self.check(self.reattach() + [self.legacy_hello(sid), self.legacy_ack(sid, False)])
        self.assertTrue(any(r == _ipc.ORDER and "ended: false" in text for r, text in problems), problems)

    def test_terminal_busy_to_a_legacy_offer_is_flagged(self):
        # Never terminal_busy toward a hello offering below 0.8.
        sid = self.seal_prefix()[0][-1][1]["session_id"]
        problems = self.check(self.reattach() + [self.legacy_hello(sid), self.busy(sid)])
        self.assertTrue(any("pre-0.8 peer" in text for _, text in problems), problems)

    def test_output_sealed_to_a_legacy_offer_is_flagged(self):
        # output_sealed only to a peer that stated 0.8: never after a hello offering below 0.8.
        run1, sealed = self.seal_prefix()
        sid = run1[-1][1]["session_id"]
        problems = self.check(self.reattach() + [self.legacy_hello(sid), self.refused(sid), sealed])
        self.assertTrue(any("output_sealed sent to a peer that has not stated 0.8" in text for _, text in problems),
                        problems)

    def test_a_second_hello_in_the_same_run_after_the_refusal_is_flagged(self):
        # A running legacy core sends no further hello for a session whose below-0.8 hello was refused during a
        # carried seal: it sets no retry timer, so the legacy handshake comes at its next start (§3.1,
        # core-restart step 23). Before the fix the checker accepted that second hello in the same run.
        sid = self.seal_prefix()[0][-1][1]["session_id"]
        problems = self.check(self.reattach() + [self.legacy_hello(sid), self.refused(sid), self.legacy_hello(sid)])
        self.assertTrue(any("no further hello" in text for _, text in problems), problems)


class SealRulePeerState(unittest.TestCase):
    """terminal-output-accounting §3.1: output_sealed and an error terminal_busy reach only a peer that
    stated 0.8 in a parsed hello (refused hellos included) or by sending seal_output on this connection.
    Either statement applies to every session on that control connection, and a replacement peer
    starts with no statement."""

    SID = "fixture-session-a"
    SEAL: ClassVar[dict] = {"sealed": True, "read_outcome": "eof", "final_last_seq": 0, "final_out_bytes": 0,
            "final_last_out_ts": 0, "observed_at_ns": 1794708000123456789, "unknown_suffix": False}

    def preamble(self, sid):
        return [("core", {"cmd": "connect", "target_id": "t", "protocol": "echo"}),
                ("worker", {"event": "connected", "conn_id": "c"}),
                ("core", {"cmd": "open_shell", "session_id": sid, "term": "xterm", "cols": 80, "rows": 24}),
                ("worker", {"event": "channel_opened", "session_id": sid, "session_type": "shell",
                             "socket": f"{sid}.sock"})]

    def hello(self, sid):
        return ("core", {"cmd": "hello", "session_id": sid, "proto_version": "0.8", "last_committed_seq": 0})

    def ack(self, sid, ended):
        return ("worker", {"event": "hello_ack", "session_id": sid, "proto_version": "0.8",
                           "first_buffered_seq": 1, "first_buffered_offset": 0, "current_seq": 0,
                           "ended": ended})

    def ack_seal(self, sid):
        return ("worker", {"event": "hello_ack", "session_id": sid, "proto_version": "0.8",
                           "first_buffered_seq": 1, "first_buffered_offset": 0, "current_seq": 0,
                           "ended": True, "output_seal": self.SEAL})

    def busy(self, sid):
        return ("worker", {"event": "error", "session_id": sid, "code": "terminal_busy",
                           "message": "terminal operation in progress"})

    def seal_reply(self, sid):
        return ("worker", {"event": "output_sealed", "session_id": sid, "output_seal": self.SEAL})

    def reattach(self):
        return [("worker", {"event": "link_state", "state": "up"}), ("core", {"cmd": "ping", "n": 1})]

    def seal_prefix(self):
        path = CONTRACT / "fixtures/C3/reattach-ended-audited-sealed.json"
        pairs = [(m["from"], m["msg"]) for m in json.loads(path.read_text())["case"]["messages"]]
        seal = next(i for i, (_, m) in enumerate(pairs) if m.get("cmd") == "seal_output")
        return copy.deepcopy(pairs[:seal + 1]), copy.deepcopy(pairs[seal + 1])

    def peer_flags(self, trace, runs):
        return [text for _, text in _ipc.conn_order(trace, runs, version="0.8") if "has not stated 0.8" in text]

    def no_hello_trace(self):
        path = CONTRACT / "fixtures/C3/terminal-seal-output-states-version.json"
        messages = json.loads(path.read_text())["case"]["messages"]
        return [(m["from"], m["msg"]) for m in messages], [m["run"] for m in messages]

    def test_seal_output_without_hello_states_08_for_its_control_peer(self):
        trace, runs = self.no_hello_trace()
        self.assertFalse(any(m.get("cmd") == "hello" for _, m in trace))
        self.assertFalse(_ipc.conn_order(trace, runs, version="0.8"))

    def test_replacement_does_not_inherit_the_seal_senders_statement(self):
        trace, runs = self.no_hello_trace()
        reply = next(i for i, (_, m) in enumerate(trace) if m.get("event") == "output_sealed")
        # Keep the seal pending across replacement, so a carried result is otherwise allowed.
        pending = trace[:reply] + trace[reply + 1:]
        pending_runs = runs[:reply] + runs[reply + 1:]
        for event in (trace[reply], self.busy(self.SID)):
            with self.subTest(event=event[1]["event"]):
                changed = pending + [event]
                changed_runs = pending_runs + [runs[-1]]
                self.assertTrue(self.peer_flags(changed, changed_runs),
                                _ipc.conn_order(changed, changed_runs, version="0.8"))

    def test_replacement_can_state_08_by_sending_its_own_seal_without_hello(self):
        trace, runs = self.no_hello_trace()
        reply = next(i for i, (_, m) in enumerate(trace) if m.get("event") == "output_sealed")
        changed = trace + [("core", {"cmd": "seal_output", "session_id": self.SID}), trace[reply]]
        self.assertFalse(_ipc.conn_order(changed, runs + [runs[-1]] * 2, version="0.8"))

    def test_output_sealed_before_a_08_hello_is_flagged(self):
        # A carried seal's barrier reaching a reattached peer that has sent no hello stating 0.8 is a worker fault.
        run1, _ = self.seal_prefix()
        run2 = self.reattach() + [self.seal_reply(self.SID)]
        runs = ["r1"] * len(run1) + ["r2"] * len(run2)
        self.assertTrue(self.peer_flags(run1 + run2, runs), _ipc.conn_order(run1 + run2, runs, version="0.8"))

    def test_terminal_busy_before_a_08_hello_is_flagged(self):
        # terminal_busy reaching a peer that has sent no hello stating 0.8 on this connection is a worker fault.
        run1, _ = self.seal_prefix()
        run2 = self.reattach() + [self.busy(self.SID)]
        runs = ["r1"] * len(run1) + ["r2"] * len(run2)
        self.assertTrue(self.peer_flags(run1 + run2, runs), _ipc.conn_order(run1 + run2, runs, version="0.8"))

    def test_output_sealed_after_a_08_hello_is_not_flagged(self):
        # After a parsed hello stating 0.8 on this connection, the carried barrier is accepted.
        run1, sealed = self.seal_prefix()
        run2 = self.reattach() + [self.hello(self.SID), sealed, self.ack_seal(self.SID)]
        runs = ["r1"] * len(run1) + ["r2"] * len(run2)
        self.assertFalse(self.peer_flags(run1 + run2, runs), _ipc.conn_order(run1 + run2, runs, version="0.8"))

    def test_terminal_busy_after_a_refused_08_hello_is_not_flagged(self):
        # A hello offering 0.8 answered terminal_busy is a parsed 0.8 hello; the barrier after it is accepted.
        run1, sealed = self.seal_prefix()
        run2 = self.reattach() + [self.hello(self.SID), self.busy(self.SID), sealed]
        runs = ["r1"] * len(run1) + ["r2"] * len(run2)
        self.assertFalse(self.peer_flags(run1 + run2, runs), _ipc.conn_order(run1 + run2, runs, version="0.8"))

    def test_a_08_hello_for_one_session_makes_the_peer_08_for_another(self):
        # A hello stating 0.8 for one session makes the connection's peer a 0.8 peer for every session on it.
        run1, sealed = self.seal_prefix()
        other = "fixture-session-b"
        run2 = self.reattach() + [self.hello(self.SID), self.hello(other), sealed]
        runs = ["r1"] * len(run1) + ["r2"] * len(run2)
        self.assertFalse(self.peer_flags(run1 + run2, runs), _ipc.conn_order(run1 + run2, runs, version="0.8"))

    def legacy_hello(self, sid):
        return ("core", {"cmd": "hello", "session_id": sid, "proto_version": "0.7", "last_committed_seq": 0})

    def version_flags(self, trace, runs):
        problems = _ipc.conn_order(trace, runs, version="0.8")
        return [text for _, text in problems if "pre-0.8" in text or "has not stated 0.8" in text], problems

    def test_a_later_hello_below_08_does_not_revoke_the_peer_08(self):
        # The peer stated 0.8 in a parsed hello for the sealing session; a later hello on the same peer offering 0.7
        # for another session leaves it a 0.8 peer, so the terminal_busy answering the 0.8 hello and the barrier after
        # it are not faults.
        run1, sealed = self.seal_prefix()
        run2 = self.reattach() + [self.hello(self.SID), self.legacy_hello("fixture-session-b"), self.busy(self.SID),
                                  sealed]
        flags, problems = self.version_flags(run1 + run2, ["r1"] * len(run1) + ["r2"] * len(run2))
        self.assertFalse(flags, problems)

    def test_a_later_hello_below_08_does_not_strip_the_summary_of_an_08_handshake(self):
        # The sealed hello_ack answers the hello that offered 0.8; a later 0.7 hello for another session does not
        # make that ack's output_seal terminal vocabulary sent to a pre-0.8 peer.
        run1, sealed = self.seal_prefix()
        run2 = self.reattach() + [self.hello(self.SID), self.legacy_hello("fixture-session-b"), sealed,
                                  self.ack_seal(self.SID)]
        flags, problems = self.version_flags(run1 + run2, ["r1"] * len(run1) + ["r2"] * len(run2))
        self.assertFalse(flags, problems)

    def test_a_core_that_stated_08_may_seal_after_a_hello_below_08(self):
        # The core's own terminal commands follow the same sticky statement: once it stated 0.8 on the connection,
        # a later 0.7 hello for another session does not make its seal_output vocabulary for a pre-0.8 peer.
        run1, _ = self.seal_prefix()
        sid = run1[-1][1]["session_id"]
        run1 = run1[:-1]  # up to the 0.8 hello_ack that says the session has ended
        trace = run1 + [self.legacy_hello("fixture-session-b"), ("core", {"cmd": "seal_output", "session_id": sid})]
        flags, problems = self.version_flags(trace, ["r1"] * len(trace))
        self.assertFalse(flags, problems)

    def test_terminal_busy_answering_a_hello_below_08_stays_flagged(self):
        # §3.1: toward a hello offering below 0.8 the answer is unsupported_proto, never terminal_busy, even from a
        # worker whose peer stated 0.8 in an earlier hello on the connection.
        run1, _ = self.seal_prefix()
        run2 = self.reattach() + [self.hello("fixture-session-b"), self.legacy_hello(self.SID), self.busy(self.SID)]
        flags, problems = self.version_flags(run1 + run2, ["r1"] * len(run1) + ["r2"] * len(run2))
        self.assertTrue(any("pre-0.8" in text for text in flags), problems)


class RecordingChainTests(unittest.TestCase):
    def test_chain_preserves_unconfirmed_suffix_and_rejects_missing_notice(self):
        first = bf.RecFrame(1, 0, bf.OUT, b"A")
        old = bf.RecFrame(2, 0, bf.OUT, b"B")
        replay = bf.RecFrame(2, 0, bf.OUT, b"C")
        raw, codes = bf.encode_recording([first, old])
        tail, tail_codes = bf.encode_recording([replay])
        prefix = bf.encode_recording([first])[0]
        path = "recordings/s1.ttyrec"
        next_path = "recordings/s1.chain/continuation-1.ttyrec"
        notice = {"ts": "2026-11-15T02:15:00Z", "action": "recording.output_incomplete", "session_id": "s1", "session_type": "shell", "recording_path": path,
                  "detail": {"version": 1, "kind": "unconfirmed", "key": "ri1:s1:u:1:1",
                             "baseline_seq": 1, "baseline_bytes": 1, "reason": "recording_unsynced", "observed_at_ns": 1,
                             "unknown_suffix": True}}
        link = {"ts": "2026-11-15T02:15:00Z", "action": "recording.output_incomplete", "session_id": "s1", "session_type": "shell", "recording_path": path,
                "detail": {"version": 1, "kind": "continuation", "key": "ri1:s1:c:1", "generation": 1,
                           "previous_path": path, "previous_seq_out": 1, "previous_bytes": 1,
                           "previous_offset": len(prefix), "continuation_path": next_path}}
        chain = bf.RecordingChain("s1", (bf.RecordingPair(path, raw, codes),
                                         bf.RecordingPair(next_path, tail, tail_codes)), (notice, link), True)
        result = bf.recording_chain_baseline(chain)
        self.assertTrue(result.ok)
        self.assertEqual(result.value, (2, 2))
        self.assertEqual([f.data for f in result.frames], [b"A", b"C"])
        self.assertEqual(result.evidence[0].frames[0].data, b"B")
        with self.assertRaises(bf.FormatError):
            bf.recording_baseline(chain)
        with self.assertRaises(bf.FormatError):
            bf.recording_stamp_problems(chain)
        with self.assertRaises(bf.FormatError):
            bf.compare_recordings(chain, [])
        self.assertEqual(bf.recording_chain_baseline(bf.RecordingChain("s1", chain.pairs, chain.audit, False)).error, "incomplete_audit")
        self.assertEqual(bf.recording_chain_stamp_problems(chain).value["status"], "unconfirmed")
        held0 = bf.RecordingChain("s1", (bf.RecordingPair(path, raw, codes, len(prefix)),), (notice,), True)
        self.assertEqual(bf.recording_chain_baseline(held0).value, (1, 1))
        held_notice = {"ts": "2026-11-15T02:15:00Z", "action": "recording.output_incomplete",
                       "session_id": "s1", "session_type": "shell", "recording_path": next_path,
                       "detail": {"version": 1, "kind": "continuation_unconfirmed", "key": "ri1:s1:q:1",
                                  "generation": 1, "baseline_seq": 2, "baseline_bytes": 2,
                                  "baseline_offset": len(tail), "reason": "recording_unsynced",
                                  "observed_at_ns": 1, "unknown_suffix": True}}
        held1 = bf.RecordingChain("s1", chain.pairs, (*chain.audit, held_notice), True)
        self.assertEqual(bf.recording_chain_baseline(held1).value, (2, 2))
        unused = copy.deepcopy(held_notice)
        unused["detail"]["generation"] = 2
        unused["detail"]["key"] = "ri1:s1:q:2"
        surplus = bf.RecordingChain("s1", chain.pairs, (*chain.audit, unused), True)
        self.assertFalse(bf.recording_chain_baseline(surplus).ok)
        incomplete = bf.RecordingChain("s1", chain.pairs[:1], chain.audit, True)
        self.assertEqual(bf.recording_chain_baseline(incomplete).error, "unavailable_pair")
        conflict = copy.deepcopy(link)
        conflict["detail"]["previous_bytes"] = 2
        conflicting = bf.RecordingChain("s1", chain.pairs, (*chain.audit, conflict), True)
        self.assertEqual(bf.recording_chain_baseline(conflicting).error, "invalid_chain")
        torn = bf.RecordingChain("s1", (chain.pairs[0], bf.RecordingPair(next_path, tail + b"\x01", tail_codes)),
                                 chain.audit, True)
        self.assertEqual(bf.recording_chain_baseline(torn).value, (2, 2))
        missing = bf.RecordingChain("s1", chain.pairs, (link,), True)
        self.assertEqual(bf.recording_chain_baseline(missing).error, "missing_notice")
        self.assertEqual(bf.compare_recording_chains(chain, chain).value["input_status"], "unconfirmed")
        corrupted = bf.RecordingChain("s1", (bf.RecordingPair(path, prefix + bytes(24), b"\x00"),
                                              chain.pairs[1]), chain.audit, True)
        compared = bf.compare_recording_chains(corrupted, corrupted)
        self.assertTrue(compared.ok)
        self.assertEqual(compared.value["input_status"], "unconfirmed")
        self.assertIsNone(compared.value["payload_identical"]["in"])
        self.assertEqual(compared.evidence[0].status, "unparseable")
        partially_coded = bf.RecordingChain("s1", (bf.RecordingPair(path, raw + bytes(24), codes), chain.pairs[1]),
                                             chain.audit, True)
        observed = bf.recording_chain_baseline(partially_coded)
        self.assertEqual(observed.evidence[0].frames[0].data, b"B")
        self.assertEqual(observed.evidence[0].status, "unparseable")
        self.assertEqual(observed.evidence[0].unparseable_ttyrec_start, len(raw))
        missing_sidecar = bf.RecordingChain("s1", (bf.RecordingPair(path, raw, None), chain.pairs[1]),
                                            chain.audit, True)
        unavailable = bf.recording_chain_baseline(missing_sidecar)
        self.assertFalse(unavailable.ok)
        self.assertEqual(unavailable.evidence[0].status, "sidecar_missing")
        self.assertEqual(unavailable.evidence[0].ttyrec_bytes, raw[len(prefix):])
        input_tail = bf.RecFrame(3, 0, bf.IN, b"typed")
        input_raw, input_codes = bf.encode_recording([first, input_tail])
        uncertain_input = bf.RecordingChain("s1", (bf.RecordingPair(path, input_raw, input_codes), chain.pairs[1]),
                                            chain.audit, True)
        same = bf.compare_recording_chains(uncertain_input, uncertain_input)
        self.assertEqual(same.value["input_status"], "unconfirmed")
        different_raw, different_codes = bf.encode_recording([replay, bf.RecFrame(4, 0, bf.IN, b"other")])
        different = bf.RecordingChain("s1", (uncertain_input.pairs[0],
                                             bf.RecordingPair(next_path, different_raw, different_codes)),
                                      uncertain_input.audit, True)
        # both sides have gaps: the first segments agree as prefixes (both empty) and the last agree as
        # suffixes (the empty one is a suffix of "other"), so a common input exists: unconfirmed, not different.
        self.assertEqual(bf.compare_recording_chains(uncertain_input, different).value["input_status"], "unconfirmed")

    def test_committed_gap_uses_next_link_as_sequence_anchor(self):
        first = bf.RecFrame(1, 0, bf.OUT, b"A")
        gap = bf.RecFrame(2, 0, bf.GAP, bf.gap_line(3, (1, 0), (2, 0)))
        following = bf.RecFrame(3, 0, bf.OUT, b"C")
        pairs_data = [bf.encode_recording([first]), bf.encode_recording([gap]), bf.encode_recording([following])]
        paths = ["recordings/s1.ttyrec", "recordings/s1.chain/continuation-1.ttyrec",
                 "recordings/s1.chain/continuation-2.ttyrec"]
        def event(path, detail):
            return {"ts": "2026-11-15T02:15:00Z", "action": "recording.output_incomplete",
                    "session_id": "s1", "session_type": "shell", "recording_path": path, "detail": detail}
        notice = event(paths[0], {"version": 1, "kind": "unconfirmed", "key": "ri1:s1:u:1:1",
                                  "baseline_seq": 1, "baseline_bytes": 1, "reason": "recording_unsynced",
                                  "observed_at_ns": 1, "unknown_suffix": True})
        link1 = event(paths[0], {"version": 1, "kind": "continuation", "key": "ri1:s1:c:1", "generation": 1,
                                 "previous_path": paths[0], "previous_seq_out": 1, "previous_bytes": 1,
                                 "previous_offset": len(pairs_data[0][0]), "continuation_path": paths[1]})
        notice1 = event(paths[1], {"version": 1, "kind": "continuation_unconfirmed", "key": "ri1:s1:q:1",
                                   "generation": 1, "baseline_seq": 4, "baseline_bytes": 4,
                                   "baseline_offset": len(pairs_data[1][0]), "reason": "recording_unsynced",
                                   "observed_at_ns": 2, "unknown_suffix": True})
        link2 = event(paths[1], {"version": 1, "kind": "continuation", "key": "ri1:s1:c:2", "generation": 2,
                                 "previous_path": paths[1], "previous_seq_out": 4, "previous_bytes": 4,
                                 "previous_offset": len(pairs_data[1][0]), "continuation_path": paths[2]})
        pairs = tuple(bf.RecordingPair(path, raw, sidecar) for path, (raw, sidecar) in zip(paths, pairs_data))
        chain = bf.RecordingChain("s1", pairs, (notice, link1, notice1, link2), True)
        result = bf.recording_chain_baseline(chain)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.value, (5, 5))
        backwards_link = copy.deepcopy(link2)
        backwards_notice = copy.deepcopy(notice1)
        backwards_link["detail"]["previous_seq_out"] = 1
        backwards_notice["detail"]["baseline_seq"] = 1
        backwards = bf.RecordingChain("s1", pairs, (notice, link1, backwards_notice, backwards_link), True)
        self.assertEqual(bf.recording_chain_baseline(backwards).error, "invalid_link")

    def test_chain_compare_loads_schema_once(self):
        cases = json.loads((CONTRACT / "fixtures/C8/terminal-continuation-chain.json").read_text())
        doc = cases["case"]["instances"][0]["doc"]
        pairs = tuple(bf.RecordingPair(p["path"], bytes.fromhex(p["ttyrec_hex"]), bytes.fromhex(p["sidecar_hex"]))
                      for p in doc["pairs"])
        chain = bf.RecordingChain(doc["session_id"], pairs, tuple(doc["audit"]), True)
        with patch.object(bf.selfcheck.Registry, "from_tree", wraps=bf.selfcheck.Registry.from_tree) as load:
            self.assertTrue(bf.compare_recording_chains(chain, chain).ok)
            self.assertEqual(load.call_count, 1)

    def test_chain_input_segments_decide_unconfirmed_or_different(self):
        first = bf.RecFrame(1, 0, bf.OUT, b"A")
        typed = bf.RecFrame(2, 0, bf.IN, b"typed")
        replay = bf.RecFrame(3, 0, bf.OUT, b"C")
        path = "recordings/s1.ttyrec"
        next_path = "recordings/s1.chain/continuation-1.ttyrec"
        raw, codes = bf.encode_recording([first, typed])
        prefix = bf.encode_recording([first])[0]
        tail, tail_codes = bf.encode_recording([replay])
        notice = {"ts": "2026-11-15T02:15:00Z", "action": "recording.output_incomplete", "session_id": "s1",
                  "session_type": "shell", "recording_path": path,
                  "detail": {"version": 1, "kind": "unconfirmed", "key": "ri1:s1:u:1:1", "baseline_seq": 1,
                             "baseline_bytes": 1, "reason": "recording_unsynced", "observed_at_ns": 1,
                             "unknown_suffix": True}}
        link = {"ts": "2026-11-15T02:15:00Z", "action": "recording.output_incomplete", "session_id": "s1",
                "session_type": "shell", "recording_path": path,
                "detail": {"version": 1, "kind": "continuation", "key": "ri1:s1:c:1", "generation": 1,
                            "previous_path": path, "previous_seq_out": 1, "previous_bytes": 1,
                            "previous_offset": len(prefix), "continuation_path": next_path}}
        chain = bf.RecordingChain("s1", (bf.RecordingPair(path, raw, codes), bf.RecordingPair(next_path, tail, tail_codes)),
                                  (notice, link), True)
        # reference: the uninterrupted recording, with the input that the suffix lost
        ref_raw, ref_codes = bf.encode_recording([first, typed, replay])
        reference = bf.RecordingChain("s1", (bf.RecordingPair(path, ref_raw, ref_codes),), (), True)
        # input lost only in the unconfirmed suffix: unconfirmed, never equal, never different
        self.assertEqual(bf.compare_recording_chains(chain, reference).value["input_status"], "unconfirmed")
        # a divergent trusted byte before P: the prefix segment is not a prefix of the reference
        pre_raw, pre_codes = bf.encode_recording([first, bf.RecFrame(2, 0, bf.IN, b"x")])
        pre_notice = copy.deepcopy(notice)
        pre_link = copy.deepcopy(link)
        pre_link["detail"]["previous_offset"] = len(pre_raw)
        bad_prefix = bf.RecordingChain("s1", (bf.RecordingPair(path, pre_raw + tail, pre_codes + tail_codes),
                                              chain.pairs[1]), (pre_notice, pre_link), True)
        self.assertEqual(bf.compare_recording_chains(bad_prefix, reference).value["input_status"], "different")
        # a divergent trusted byte after the switch: the last segment is not a suffix of the reference
        bad_suffix_raw, bad_suffix_codes = bf.encode_recording([replay, bf.RecFrame(4, 0, bf.IN, b"z")])
        bad_suffix = bf.RecordingChain("s1", (chain.pairs[0], bf.RecordingPair(next_path, bad_suffix_raw, bad_suffix_codes)),
                                       chain.audit, True)
        self.assertEqual(bf.compare_recording_chains(bad_suffix, reference).value["input_status"], "different")
        path = CONTRACT / "fixtures/C8/terminal-continuation-chain.json"
        cases = json.loads(path.read_text())["case"]["instances"]
        for case in cases:
            doc = case["doc"]
            pairs = tuple(bf.RecordingPair(pair["path"], bytes.fromhex(pair["ttyrec_hex"]),
                                           None if pair["sidecar_hex"] is None else bytes.fromhex(pair["sidecar_hex"]))
                          for pair in doc["pairs"])
            chain = bf.RecordingChain(doc["session_id"], pairs, tuple(doc["audit"]), doc["audit_complete"])
            result = bf.recording_chain_baseline(chain)
            with self.subTest(note=case["note"]):
                self.assertEqual(result.error, doc["expected_error"])
                if result.ok:
                    self.assertEqual(result.value, (doc["expected_seq"], doc["expected_bytes"]))
                    if "expected_output_hex" in doc:
                        self.assertEqual(b"".join(frame.data for frame in result.frames if frame.code == bf.OUT).hex(),
                                         doc["expected_output_hex"])
                    self.assertEqual(result.evidence[0].status, doc["expected_evidence_status"])
                    self.assertEqual(bf.compare_recording_chains(chain, chain).value["input_status"],
                                     doc["expected_input_status"])

    def _quarantined_chain(self, seg0: bytes, seg1: bytes) -> bf.RecordingChain:
        """A two-pair chain quarantined at P (the end of pair 0): pair 0's trusted input segment is seg0,
        pair 1's (the continuation, taken whole) is seg1."""
        path = "recordings/s1.ttyrec"
        next_path = "recordings/s1.chain/continuation-1.ttyrec"
        raw, codes = bf.encode_recording([bf.RecFrame(1, 0, bf.OUT, b"A"), bf.RecFrame(2, 0, bf.IN, seg0)])
        tail, tail_codes = bf.encode_recording([bf.RecFrame(3, 0, bf.IN, seg1)])
        notice = {"ts": "2026-11-24T01:00:00Z", "action": "recording.output_incomplete", "session_id": "s1",
                  "session_type": "shell", "recording_path": path,
                  "detail": {"version": 1, "kind": "unconfirmed", "key": "ri1:s1:u:1:1", "baseline_seq": 1,
                             "baseline_bytes": 1, "reason": "recording_unsynced", "observed_at_ns": 1,
                             "unknown_suffix": True}}
        link = {"ts": "2026-11-24T01:00:00Z", "action": "recording.output_incomplete", "session_id": "s1",
                "session_type": "shell", "recording_path": path,
                "detail": {"version": 1, "kind": "continuation", "key": "ri1:s1:c:1", "generation": 1,
                           "previous_path": path, "previous_seq_out": 1, "previous_bytes": 1,
                           "previous_offset": len(raw), "continuation_path": next_path}}
        return bf.RecordingChain("s1", (bf.RecordingPair(path, raw, codes),
                                        bf.RecordingPair(next_path, tail, tail_codes)), (notice, link), True)

    def test_both_sides_gaps_decide_by_first_prefix_and_last_suffix(self):
        # Qodo's case: first segments agree as prefixes (a is a prefix of ab), last agree as suffixes (c == c),
        # so a common input exists and the result is unconfirmed, never different.
        left = self._quarantined_chain(b"a", b"c")
        right = self._quarantined_chain(b"ab", b"c")
        self.assertEqual(bf.compare_recording_chains(left, right).value["input_status"], "unconfirmed")
        # a conflicting first segment: neither is a prefix of the other.
        left = self._quarantined_chain(b"ax", b"c")
        right = self._quarantined_chain(b"ay", b"c")
        self.assertEqual(bf.compare_recording_chains(left, right).value["input_status"], "different")
        # a conflicting last segment: neither is a suffix of the other.
        left = self._quarantined_chain(b"a", b"cx")
        right = self._quarantined_chain(b"ab", b"cy")
        self.assertEqual(bf.compare_recording_chains(left, right).value["input_status"], "different")

    def _held_single(self, trusted_input: bytes) -> bf.RecordingChain:
        """A single-pair recording whose last pair is quarantined with no continuation (a held session): its
        trusted input at/below P is trusted_input; the input after P is unknown, so the chain ends in a gap."""
        path = "recordings/s1.ttyrec"
        prefix, prefix_codes = bf.encode_recording([bf.RecFrame(1, 0, bf.OUT, b"A"),
                                                    bf.RecFrame(2, 0, bf.IN, trusted_input)])
        suffix, suffix_codes = bf.encode_recording([bf.RecFrame(3, 0, bf.OUT, b"B")])
        notice = {"ts": "2026-11-24T02:00:00Z", "action": "recording.output_incomplete", "session_id": "s1",
                  "session_type": "shell", "recording_path": path,
                  "detail": {"version": 1, "kind": "unconfirmed", "key": "ri1:s1:u:1:1", "baseline_seq": 1,
                             "baseline_bytes": 1, "reason": "recording_unsynced", "observed_at_ns": 1,
                             "unknown_suffix": True}}
        return bf.RecordingChain("s1", (bf.RecordingPair(path, prefix + suffix, prefix_codes + suffix_codes,
                                                         len(prefix)),), (notice,), True)

    def test_held_recording_trailing_gap_never_anchors_suffix(self):
        # a held single-pair recording against an identical complete reference: the trusted prefix is a prefix
        # of the reference and the unknown suffix is a trailing gap, so the last segment is never anchored as a
        # suffix — the result is unconfirmed, never different.
        held = self._held_single(b"a")
        ref_raw, ref_codes = bf.encode_recording([bf.RecFrame(1, 0, bf.OUT, b"A"), bf.RecFrame(2, 0, bf.IN, b"a")])
        reference = bf.RecordingChain("s1", (bf.RecordingPair("recordings/s1.ttyrec", ref_raw, ref_codes),), (), True)
        self.assertEqual(bf.compare_recording_chains(held, reference).value["input_status"], "unconfirmed")
        # two held recordings with compatible trusted prefixes (a is a prefix of ab): both end in trailing gaps,
        # so neither last segment is anchored as a suffix — unconfirmed.
        self.assertEqual(bf.compare_recording_chains(self._held_single(b"a"),
                                                     self._held_single(b"ab")).value["input_status"], "unconfirmed")
        # a held recording whose trusted prefix conflicts with the reference: the first segment is not a prefix → different.
        conflict_raw, conflict_codes = bf.encode_recording([bf.RecFrame(1, 0, bf.OUT, b"A"),
                                                            bf.RecFrame(2, 0, bf.IN, b"q")])
        conflict_ref = bf.RecordingChain("s1", (bf.RecordingPair("recordings/s1.ttyrec", conflict_raw, conflict_codes),),
                                         (), True)
        self.assertEqual(bf.compare_recording_chains(self._held_single(b"a"), conflict_ref).value["input_status"],
                         "different")


class ContinuationRecoveryFixturePaths(unittest.TestCase):
    def test_pending_path_uses_session_chain_directory(self):
        doc = json.loads((CONTRACT / "fixtures/C6/terminal-continuation-recovery.json").read_text())
        for case in doc["case"]["instances"]:
            pending = case["doc"].get("recording_pending_continuation")
            if pending:
                self.assertEqual(pending["continuation_path"], "recordings/fixture-session-a.chain/continuation-1.ttyrec")
