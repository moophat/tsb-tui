"""The C3 envelope and the C8 recording, byte by byte, with expectations built by hand.

The fixtures' .bin files were produced by this codec, so a fixture round trip alone would pass a
codec that is wrong everywhere in the same way. These expectations are written out from the
published layouts instead: network order for the envelope, little-endian for the recording.
"""

import copy
import unittest
from typing import ClassVar

from tsbc import _binfmt as bf
from tsbc import selfcheck
from tsbc.schema import Registry

from . import CONTRACT

TS = 1789000000123456789


class Envelope(unittest.TestCase):
    def test_output_frame_bytes(self):
        frame = bf.DataFrame(flags=0, seq=1, timestamp=TS, payload=b"hello")
        expected = (bytes([1, 0]) + (0).to_bytes(2, "big") + (5).to_bytes(4, "big")
                    + (1).to_bytes(8, "big") + TS.to_bytes(8, "big") + b"hello")
        self.assertEqual(len(expected) - 5, 24)
        self.assertEqual(bf.encode_frames([frame]), expected)
        self.assertEqual(bf.decode_frames(expected), [frame])

    def test_input_frame_sets_direction_bit(self):
        [frame] = bf.decode_frames(bf.encode_frames([bf.DataFrame(1, 9, TS, b"x")]))
        self.assertEqual(frame.direction, "in")
        self.assertEqual(bf.encode_frames([frame])[1], 0x01)

    def test_torn_tail_is_an_error_not_a_short_read(self):
        data = bf.encode_frames([bf.DataFrame(0, 1, TS, b"hello")])
        for cut in (3, 24, len(data) - 1):
            with self.subTest(cut=cut), self.assertRaises(bf.FormatError):
                bf.decode_frames(data[:cut])

    def test_v01_rules(self):
        self.assertEqual(bf.frame_problems(bf.DataFrame(0, 1, TS, b"x")), [])
        self.assertTrue(bf.frame_problems(bf.DataFrame(0x02, 1, TS, b"x")))       # gap bit with a payload
        self.assertTrue(bf.frame_problems(bf.DataFrame(0, 1, TS, b"")))           # empty and not a gap
        self.assertTrue(bf.frame_problems(bf.DataFrame(0, 1, TS, b"x", version=2)))

    def test_seq_is_per_direction(self):
        frames = [bf.DataFrame(0, 1, TS, b"a"), bf.DataFrame(1, 1, TS, b"b"), bf.DataFrame(0, 2, TS, b"c")]
        self.assertEqual(bf.seq_problems(frames, contiguous=True), [])
        jump = frames + [bf.DataFrame(0, 9, TS, b"d")]
        self.assertTrue(bf.seq_problems(jump, contiguous=True))
        self.assertEqual(bf.seq_problems(jump, contiguous=False), [])  # dropped frames: allowed in a capture
        repeat = frames + [bf.DataFrame(1, 1, TS, b"e")]
        self.assertTrue(bf.seq_problems(repeat, contiguous=False))


def header(sec, usec, length):
    return sec.to_bytes(4, "little") + usec.to_bytes(4, "little") + length.to_bytes(4, "little")


class Recording(unittest.TestCase):
    def test_frame_bytes_are_classic_ttyrec_and_the_direction_is_beside_them(self):
        # The .ttyrec must stay readable by any ttyrec player: a 12-byte header and the data,
        # nothing else. The direction lang-arch §8.3 asks for lives in the sidecar.
        out = bf.RecFrame(sec=1789000000, usec=123456, code=bf.OUT, data=b"$ ")
        inp = bf.RecFrame(sec=1789000001, usec=7, code=bf.IN, data=b"ls\r")
        ttyrec = header(1789000000, 123456, 2) + b"$ " + header(1789000001, 7, 3) + b"ls\r"
        self.assertEqual(bf.encode_recording([out, inp]), (ttyrec, bytes([0, 1])))
        self.assertEqual(bf.decode_recording(ttyrec, bytes([0, 1])), [out, inp])
        self.assertEqual([f.direction for f in (out, inp)], ["out", "in"])

    def test_the_end_marker_is_a_bare_header_with_no_data_bytes(self):
        # No data bytes means nothing for a player to write to the terminal; what a given player
        # prints elsewhere (upstream ttyplay 1.0.8 warns on stderr) is outside the format.
        marker = bf.end_marker(1789000005123456789, "terminated")
        ttyrec, sidecar = bf.encode_recording([marker])
        self.assertEqual(ttyrec, header(1789000005, 123456, 0))  # no data bytes at all
        self.assertEqual(sidecar, bytes([4]))
        self.assertEqual((marker.end, marker.direction), ("terminated", None))
        self.assertEqual(bf.end_marker(1789000005123456789, "killed").code, 3)

    def test_the_sidecar_must_count_the_frames(self):
        ttyrec = header(1, 0, 1) + b"x"
        for sidecar in (b"", bytes([0, 0])):
            with self.subTest(sidecar=sidecar), self.assertRaises(bf.FormatError):
                bf.decode_recording(ttyrec, sidecar)

    def test_a_code_this_tag_does_not_define_is_refused(self):
        with self.assertRaises(bf.FormatError) as caught:
            bf.decode_recording(header(1, 0, 1) + b"x", bytes([10]))  # code 9 is terminal incompleteness
        self.assertIn("[10]", str(caught.exception))

    def test_marker_rules(self):
        data = bf.RecFrame(1, 0, bf.OUT, b"x")
        killed, terminated = bf.end_marker(2 * bf.NS, "killed"), bf.end_marker(2 * bf.NS, "terminated")
        self.assertEqual(bf.recording_problems([data]), [])            # a live session: no marker
        self.assertEqual(bf.recording_problems([data, killed]), [])    # an ended session: one, last
        cases = {
            "must be the last frame": [data, killed, data],
            "a session ends once": [data, killed, terminated],
            "an end marker carries no data": [data, bf.RecFrame(2, 0, bf.END_KILLED, b"x")],
            "an empty data frame": [bf.RecFrame(1, 0, bf.IN, b""), killed],
            "a gap marker carries its readable line": [data, bf.RecFrame(2, 0, bf.GAP, b"")],
        }
        for needle, frames in cases.items():
            with self.subTest(needle):
                problems = bf.recording_problems(frames)
                self.assertTrue(any(needle in p for p in problems), problems)

    def test_reopen_cuts_both_files_to_the_last_frame_both_hold(self):
        # The writer appends the frame, then its code; kill -9 can land between the two, or
        # inside the frame. Reopening must leave a recording that decodes and appends cleanly —
        # never pad a code, which would shift every later frame's direction.
        frames = [bf.RecFrame(1, 0, bf.OUT, b"$ "), bf.RecFrame(2, 0, bf.IN, b"ls\r"), bf.RecFrame(3, 0, bf.OUT, b"x")]
        ttyrec, sidecar = bf.encode_recording(frames)
        two_ttyrec, two_sidecar = bf.encode_recording(frames[:2])
        crashes = {
            "the last code never written": (ttyrec, sidecar[:2]),
            "the last frame torn inside its data": (ttyrec[:-1], sidecar[:2]),
            "the last frame torn inside its header": (two_ttyrec + ttyrec[len(two_ttyrec):len(two_ttyrec) + 5], sidecar[:2]),
            "a code with no frame (a writer out of order)": (two_ttyrec, sidecar),
        }
        for name, (t, s) in crashes.items():
            with self.subTest(name):
                self.assertEqual(bf.reopen(t, s), (two_ttyrec, two_sidecar))
                cut_t, cut_s = bf.reopen(t, s)
                more_t, more_s = bf.encode_recording([bf.RecFrame(4, 0, bf.IN, b"q")])
                self.assertEqual(bf.decode_recording(cut_t + more_t, cut_s + more_s)[-1].direction, "in")
        self.assertEqual(bf.reopen(ttyrec, sidecar), (ttyrec, sidecar))   # an intact recording is untouched
        self.assertEqual(bf.reopen(b"", b""), (b"", b""))

    def test_the_end_marker_s_time_is_the_observer_s_not_the_core_s(self):
        # From contract-v0.2 an end that happens while the core is away is stamped by the worker
        # and written by the core when it returns (session model §15, the spool). A v0.1 text that
        # tied the marker to the core's clock would need a semantic amendment then; naming the
        # component that observed the end needs none.
        doc = Registry.from_tree(CONTRACT).docs[bf.RECORDING_SCHEMA]
        for text in (doc["description"], doc["x-derivation"]["end marker"]):
            self.assertIn("the component that observed the end", text)
            self.assertNotIn("the core's clock", text)

    def test_the_gap_code_is_held_not_reused(self):
        # v0.2 defines the gap marker as the code v0.1 held for it, 2, renumbering nothing: 3 and
        # 4 stay the end markers, so every v0.1 recording reads the same at v0.2.
        self.assertEqual((bf.OUT, bf.IN, bf.GAP, bf.END_KILLED, bf.END_TERMINATED), (0, 1, 2, 3, 4))
        self.assertEqual(bf.SIDECAR_VALUES[bf.GAP], "gap")
        self.assertEqual(bf.RESERVED_CODES, ())

    def test_derivation_truncates_and_keeps_the_frames_own_clock(self):
        # Worker-owned timestamps (immortality §4.2): the recording carries the envelope's
        # time, and 999999999 ns never rounds up into the next second.
        rec = bf.to_recording(bf.DataFrame(0, 1, 1789000000999999999, b"x"))
        self.assertEqual((rec.sec, rec.usec, rec.code, rec.data), (1789000000, 999999, bf.OUT, b"x"))
        rec = bf.to_recording(bf.DataFrame(1, 1, 1789000001000000999, b"y"))
        self.assertEqual((rec.sec, rec.usec, rec.direction), (1789000001, 0, "in"))

    def test_past_2106_cannot_be_written(self):
        with self.assertRaises(bf.FormatError):
            bf.to_recording(bf.DataFrame(0, 1, (bf.U32_MAX + 1) * bf.NS, b"x"))
        with self.assertRaises(bf.FormatError):
            bf.end_marker((bf.U32_MAX + 1) * bf.NS, "killed")


class PublishedLayout(unittest.TestCase):
    """The codec and the schemas' x-binary-layout cannot drift apart unnoticed."""

    def failures_with(self, schema: str, change, key: str = "x-binary-layout") -> list[str]:
        reg = Registry.from_tree(CONTRACT)
        doc = copy.deepcopy(reg.docs[schema])
        change(doc[key])
        reg.add(schema, doc)
        report = selfcheck.Report()
        bf.check_layouts(selfcheck.Context(CONTRACT, reg, report, []))
        return report.failures

    def test_published_layouts_match(self):
        self.assertEqual(self.failures_with(bf.DATA_FRAME_SCHEMA, lambda layout: None), [])
        self.assertEqual(self.failures_with(bf.RECORDING_SCHEMA, lambda layout: None), [])

    def test_a_changed_field_width_is_caught(self):
        def widen(layout):
            layout["fields"][4]["type"] = "u32"
        self.assertTrue(self.failures_with(bf.DATA_FRAME_SCHEMA, widen))

    def test_a_changed_byte_order_is_caught(self):
        self.assertTrue(self.failures_with(bf.RECORDING_SCHEMA, lambda layout: layout.update(byte_order="big-endian")))

    def test_a_ttyrec_header_grown_back_to_13_bytes_is_caught(self):
        def flags_byte(layout):
            layout["fields"].append({"name": "flags", "offset": 12, "type": "u8"})
            layout["header_bytes"] = 13
        self.assertTrue(self.failures_with(bf.RECORDING_SCHEMA, flags_byte))

    def test_a_renumbered_sidecar_code_is_caught(self):
        def swap(sidecar):
            sidecar["values"]["3"], sidecar["values"]["4"] = sidecar["values"]["4"], sidecar["values"]["3"]
        self.assertTrue(self.failures_with(bf.RECORDING_SCHEMA, swap, "x-sidecar"))
        # Re-holding the gap marker's code in the published list, while the codec defines it, is drift too.
        self.assertTrue(self.failures_with(bf.RECORDING_SCHEMA, lambda s: s.update(reserved=[2]), "x-sidecar"))

    def test_the_published_gap_line_is_the_one_the_codec_writes(self):
        # A TSB reader parses the marker with the published pattern: a pattern the codec's own line
        # fails would make every TSB recording with a gap unreadable, and one that admits a broken
        # line (0 bytes lost, say) would let a recording that lies about its loss pass.
        def rename(line):
            line["pattern"] = line["pattern"].replace("TSB gap", "TSB loss")

        def loosen(line):
            line["pattern"] = line["pattern"].replace("([1-9][0-9]{0,19})", "([0-9]{1,20})")
        self.assertEqual(self.failures_with(bf.RECORDING_SCHEMA, lambda line: None, "x-gap-line"), [])
        self.assertIn("x-gap-line: the codec's own line does not match the published pattern",
                      " ".join(self.failures_with(bf.RECORDING_SCHEMA, rename, "x-gap-line")))
        self.assertIn("is accepted, but it breaks the format",
                      " ".join(self.failures_with(bf.RECORDING_SCHEMA, loosen, "x-gap-line")))


class Rediscovery(unittest.TestCase):
    STATE: ClassVar[dict] = {
        "supervisors": [{"supervisor_id": "s1", "pid": 1, "control_sock": "/x/supervisors/s1/control.sock"},
                        {"supervisor_id": "s2", "pid": 1, "control_sock": "/x/supervisors/s2/control.sock"}],
        "workers": [{"worker_id": "w1", "pid": 1, "connections": [
            {"conn_id": "c1", "conn_dir": "/x/conn-c1", "sessions": [{"session_id": "a", "socket": "shell.sock"},
                                                                     {"session_id": "b", "socket": "shell-2.sock"}]},
            {"conn_id": "c2", "conn_dir": "/x/conn-c2", "sessions": [{"session_id": "c", "socket": "shell.sock"}]}]}],
        "sockets": {"/x/supervisors/s1/control.sock": "connectable", "/x/supervisors/s2/control.sock": "refused",
                    "/x/conn-c1/control.sock": "connectable", "/x/conn-c1/shell.sock": "connectable",
                    "/x/conn-c2/control.sock": "refused", "/x/conn-c2/shell.sock": "connectable"},
    }

    def test_liveness_is_the_dial_not_the_pid(self):
        # Every pid here is 1 — alive in any namespace — so a pid test would dial everything.
        # The worker runs in another container; only its sockets answer for it.
        out = bf.rediscover(self.STATE)
        self.assertEqual(out["supervisors"], ["/x/supervisors/s1/control.sock"])
        self.assertEqual(out["dead_supervisors"], ["s2"])
        self.assertEqual(out["dial"], [{"worker_id": "w1", "conn_id": "c1", "control": "/x/conn-c1/control.sock",
                                        "sessions": [{"session_id": "a", "socket": "/x/conn-c1/shell.sock"}]}])

    def test_the_dead_are_ended_not_retried(self):
        # A missing session socket kills that session; a refused control socket kills its whole
        # connection, even when a session socket still answers.
        out = bf.rediscover(self.STATE)
        self.assertEqual(out["dead_connections"], ["c2"])
        self.assertEqual(sorted(out["dead_sessions"]), ["b", "c"])


class GapCommitPoint(unittest.TestCase):
    """A gap marker is a commit point. Were last_committed_seq to count output frames only, a core
    killed after writing a marker and before the next frame would ask again from the old seq, the
    worker would declare the same gap again, and the core would owe a marker for 0 bytes — which no
    recording can hold — or one whose span overlaps the first."""

    FIRST = bf.DataFrame(0x02, 6, TS + 9_000_000_000, b"")
    SECOND = bf.DataFrame(0x02, 9, TS + 12_000_000_000, b"")

    def test_after_a_marker_the_next_loss_counts_from_it(self):
        committed = {"out_bytes": 47, "last_out_ts": self.FIRST.timestamp, "last_seq": 6}
        handshake = {"first_buffered_seq": 10, "first_buffered_offset": 71, "last_dropped_ts": self.SECOND.timestamp}
        [marker] = bf.derive_recording([self.SECOND], handshakes=[handshake], committed=committed)
        lost, start, end = bf.parse_gap_line(marker.data)
        self.assertEqual((lost, start, end), (24, bf._sec_usec(self.FIRST.timestamp), bf._sec_usec(self.SECOND.timestamp)))

    def test_a_marker_moves_the_committed_point(self):
        # Two losses in one run: the second marker's span starts at the first marker, not at the
        # output frame before it, and its count excludes the first loss.
        head = bf.DataFrame(0, 2, TS, b"xy")
        handshakes = [{"first_buffered_seq": 7, "first_buffered_offset": 40, "last_dropped_ts": self.FIRST.timestamp},
                      {"first_buffered_seq": 10, "first_buffered_offset": 64, "last_dropped_ts": self.SECOND.timestamp}]
        rec = bf.derive_recording([head, self.FIRST, self.SECOND], handshakes=handshakes,
                                  committed={"out_bytes": 10, "last_out_ts": TS - 1, "last_seq": 1})
        first, second = (bf.parse_gap_line(f.data) for f in rec if f.code == bf.GAP)
        self.assertEqual(first[0], 40 - 12)
        self.assertEqual(second, (64 - 40, bf._sec_usec(self.FIRST.timestamp), bf._sec_usec(self.SECOND.timestamp)))

    def test_a_first_loss_with_no_output_starts_at_creation(self):
        created = TS - 5_000_000_000
        handshake = {"first_buffered_seq": 7, "first_buffered_offset": 30, "last_dropped_ts": self.FIRST.timestamp}
        [marker] = bf.derive_recording([self.FIRST], handshakes=[handshake], committed={"created_at": created})
        self.assertEqual(bf.parse_gap_line(marker.data)[1], bf._sec_usec(created))
        with self.assertRaises(bf.FormatError):
            bf.derive_recording([self.FIRST], handshakes=[handshake])  # neither a stamp nor created_at


class GapBitOnInput(unittest.TestCase):
    def test_only_output_is_ever_declared_lost(self):
        # The JSON form refuses flags 3 by schema; a collected binary tap reaches this rule only.
        problems = bf.frame_problems(bf.DataFrame(0x03, 1, TS, b""))
        self.assertTrue(any("the gap bit on an input frame" in p for p in problems), problems)
        self.assertEqual(bf.frame_problems(bf.DataFrame(0x02, 1, TS, b"")), [])


class RediscoveryV02(unittest.TestCase):
    def test_a_spooled_end_is_never_read_as_worker_lost(self):
        # A session whose socket refuses inside a live connection ended while the core was away:
        # the spool holds its end, so it is written from there, never ended E10 worker_lost.
        state = {"supervisors": [], "spool": [{"session_id": "b"}], "audited": [],
                 "workers": [{"worker_id": "w", "proto_version": "0.2", "connections": [
                     {"conn_id": "c", "conn_dir": "/x/conn-c", "sessions": [{"session_id": "a", "socket": "shell.sock"},
                                                                            {"session_id": "b", "socket": "shell-2.sock"}]}]}],
                 "sockets": {"/x/conn-c/control.sock": "connectable", "/x/conn-c/shell.sock": "connectable",
                             "/x/conn-c/shell-2.sock": "refused"}}
        out = bf.rediscover_v02(state)
        self.assertEqual(out["dead_sessions"], [])
        self.assertEqual(out["spooled_ends"], [{"session_id": "b", "after_replay": False}])
        del state["spool"][0]
        self.assertEqual(bf.rediscover_v02(state)["dead_sessions"], ["b"])


class MergeAcrossRuns(unittest.TestCase):
    ONE = bf.DataFrame(0, 1, TS, b"$ ")
    GAP = bf.DataFrame(0x02, 5, TS + 5_000_000_000, b"")
    AFTER = bf.DataFrame(0, 6, TS + 6_000_000_000, b"line 6")

    def test_a_gap_frame_both_runs_tapped_is_one_frame(self):
        # Two handshakes that find the ring at one seq report one loss, so one gap frame, one
        # stamp: a later copy stamped otherwise is a worker misreporting when the loss ended.
        self.assertEqual(bf.merge_runs([("r1", [self.ONE, self.GAP]), ("r2", [self.GAP, self.AFTER])], {"r2": 1})[1], [])
        moved = bf.DataFrame(0x02, 5, TS + 5_000_000_001, b"")
        problems = bf.merge_runs([("r1", [self.ONE, self.GAP]), ("r2", [moved, self.AFTER])], {"r2": 1})[1]
        self.assertIn("output seq 5 replayed with other bytes or another stamp", " ".join(problems))

    def test_only_a_frame_the_later_run_declares_lost_may_differ(self):
        # The earlier run tapped frame 5 and never committed it; the ring dropped it before the next
        # handshake, which declares it lost: the gap frame stands where the real frame stood.
        real = bf.DataFrame(0, 5, TS + 4_000_000_000, b"line 5")
        self.assertEqual(bf.merge_runs([("r1", [self.ONE, real]), ("r2", [self.GAP, self.AFTER])], {"r2": 1})[1], [])
        # The other way round cannot happen: a frame declared lost never comes back.
        problems = bf.merge_runs([("r1", [self.ONE, self.GAP]), ("r2", [real, self.AFTER])], {"r2": 1})[1]
        self.assertIn("output seq 5 replayed", " ".join(problems))


class Comparator(unittest.TestCase):
    def test_a_declared_loss_is_never_identical(self):
        # The same output payload, and a gap marker on one side only: the loss is declared, not
        # closed, so the output is not identical however the payload reads (x-compare).
        a = [bf.RecFrame(1789000000, 0, bf.OUT, b"$ "), bf.RecFrame(1789000001, 0, bf.OUT, b"ls")]
        marker = bf.RecFrame(1789000000, 500, bf.GAP, bf.gap_line(3, (1789000000, 0), (1789000000, 500)))
        b = [a[0], marker, a[1]]
        self.assertEqual(bf.compare_recordings(a, a)["payload_identical"], {"out": True, "in": True})
        self.assertEqual(bf.compare_recordings(a, b)["payload_identical"], {"out": False, "in": True})
        self.assertTrue(bf.compare_recordings(b, list(b))["payload_identical"]["out"])


if __name__ == "__main__":
    unittest.main()
