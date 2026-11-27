"""`--artifacts` at contract-v0.2: an e1 collection that spans a core restart passes every e1
artefact fixture, and each way a core, a worker or a harness can get a v0.2 clause wrong fails the
fixture that encodes it.

The collection is the v0.1 one (test_artifacts.files) plus one connection whose control capture
and taps span two runs of the core: run 1 opens two sessions and runs their first handshakes; the
core is killed; while it is away one session's target closes it and the other's ring overflows;
run 2 reattaches — link_state first, the timer values again, a gap frame for what the ring dropped,
an ended session replayed and released — then a lone data-socket re-dial, a close, and a linger
close. Each mutation then breaks one thing.
"""

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from tsbc import _binfmt as bf
from tsbc import verify
from tsbc._run import Run
from tsbc.fixtures import load_fixtures
from tsbc.schema import Registry

from . import CONTRACT
from .test_artifacts import (
    SID,
    SUP,
    T,
    build,
    core,
    files,
    frames,
    jsonl,
    manual_recording,
    recording,
    worker,
)

RUN1, RUN2 = "01789000000000000000", "01789000100000000000"
RCONN, RS, RQ, DEAD = "conn-r", "sess-r", "sess-q", "sess-dead"
RWORKER = "worker-r"
TIMERS = {"heartbeat_interval": 10000, "heartbeat_missed": 3, "keepalive_interval": 30000, "keepalive_count_max": 12,
          "connection_linger": 300000, "orphan_timeout": 172800000, "idle": 604800000, "unattended": 2592000000}


def frame(flags, seq, ts, payload):
    return bf.DataFrame(flags, seq, ts, payload)


def run1_frames():
    return [frame(0, 1, T, b"$ "), frame(1, 1, T + 1_000_000_000, b"tail -f log\r"),
            frame(0, 2, T + 1_000_100_000, b"tail -f log\r\n"), frame(0, 3, T + 2_000_000_000, b"line 3\r\n")]


def run2_frames():
    # Lines 4 and 5 were dropped by the ring while the core was away: the gap frame is seq 5, the
    # last lost seq, stamped with frame 5's stamp; the replay then writes 6 to 8 with their stamps.
    return [frame(2, 5, T + 5_000_000_000, b""), frame(0, 6, T + 6_000_000_000, b"line 6\r\n"),
            frame(0, 7, T + 7_000_000_000, b"line 7\r\n"), frame(0, 8, T + 8_000_000_000, b"line 8\r\n"),
            frame(1, 2, T + 101_000_000_000, b"\x03"), frame(0, 9, T + 101_000_100_000, b"^C\r\n$ "),
            frame(0, 10, T + 102_000_000_000, b"$ ")]


# Output 1 to 3 carry 23 payload bytes and the lost 4 and 5 carry 16: the ring's oldest frame, 6,
# starts at byte 39 of the session's output.
LOST_FROM = 39
ORPHAN_TS = "2026-09-10T00:28:00.5Z"  # the worker's stamp of RQ's orphan end, before run 2 began


def hs(sid, lcs, fbs, fbo, current, ended=False, dropped=None, run=None, live=True):
    ack = {"event": "hello_ack", "session_id": sid, "proto_version": "0.2", "first_buffered_seq": fbs,
           "first_buffered_offset": fbo, "current_seq": current, "ended": ended}
    if dropped is not None:
        ack["last_dropped_ts"] = dropped
    lines = [core({"cmd": "hello", "session_id": sid, "proto_version": "0.2", "last_committed_seq": lcs}),
             worker(ack), core({"cmd": "replay", "session_id": sid, "from_seq": lcs + 1}),
             worker({"event": "replay_done", "session_id": sid, "through_seq": current}),
             core({"cmd": "live" if live else "release", "session_id": sid})]
    return [dict(line, run=run) for line in lines]


def tagged(lines, run):
    return [dict(line, run=run) for line in lines]


def reattach_lines():
    connect = {"cmd": "connect", "target_id": "t1", "protocol": "ssh", "host": "192.0.2.10", "port": 22, "user": "u",
               "auth": {"kind": "key", "key": "FIXTURE-NOT-A-KEY"}, "timers": TIMERS, "buffer_bytes": 8388608,
               "input_frame_max": 1048576}
    shell = lambda sid, sock: [core({"cmd": "open_shell", "session_id": sid, "term": "xterm", "cols": 80, "rows": 24}),
                               worker({"event": "channel_opened", "session_id": sid, "session_type": "shell",
                                       "socket": sock})]
    one = tagged([core(connect), worker({"event": "connected", "conn_id": RCONN})] + shell(RS, "shell.sock"), RUN1)
    one += hs(RS, 0, 1, 0, 0, run=RUN1)
    one += tagged(shell(RQ, "shell-2.sock"), RUN1) + hs(RQ, 0, 1, 0, 0, run=RUN1)
    one += tagged([core({"cmd": "ping", "n": 1}), worker({"event": "pong", "n": 1}), core({"cmd": "ping", "n": 2})], RUN1)
    two = tagged([worker({"event": "link_state", "conn_id": RCONN, "state": "answering",
                          "last_ack_at": "2026-09-29T01:00:00Z"}),
                  core({"cmd": "timers", "timers": TIMERS})], RUN2)
    two += hs(RS, 3, 6, LOST_FROM, 8, dropped=T + 5_000_000_000, run=RUN2)
    two += hs(RQ, 0, 1, 0, 0, ended=True, run=RUN2, live=False)  # ended while the core was away
    two += tagged([core({"cmd": "ping", "n": 1}), worker({"event": "pong", "n": 1})], RUN2)
    two += hs(RS, 9, 6, LOST_FROM, 10, dropped=T + 5_000_000_000, run=RUN2)  # a lone re-dial: the same ring
    two += tagged([core({"cmd": "close_channel", "session_id": RS}),
                   worker({"event": "channel_closed", "session_id": RS, "by": "core", "reason": "closed by the core"}),
                   core({"cmd": "release", "session_id": RS}),
                   worker({"event": "connection_lost", "conn_id": RCONN, "session_ids": [], "cause": "linger",
                           "reason": "connection_linger ran out"})], RUN2)
    return one + two


def supervisor_run_lines():
    return (tagged([core({"cmd": "create_worker", "conn_id": RCONN, "placement_key": "u", "n_max": 2}),
                    {"from": "supervisor", "msg": {"event": "worker_created", "conn_id": RCONN, "worker_id": RWORKER,
                                                   "conn_dir": f"/tmp/tsb/conn-{RCONN}", "proto_version": "0.2"}},
                    core({"cmd": "ping", "n": 1}), {"from": "supervisor", "msg": {"event": "pong", "n": 1}}], RUN1)
            + tagged([core({"cmd": "ping", "n": 1}), {"from": "supervisor", "msg": {"event": "pong", "n": 1}},
                      core({"cmd": "kill_worker", "worker_id": "worker-hung", "reap_timeout": 5000}),
                      {"from": "supervisor", "msg": {"event": "worker_killed", "worker_id": "worker-hung"}}], RUN2))


def e1_files() -> dict[str, bytes]:
    content = files()
    manifest = {"worker_id": RWORKER, "pid": 4301, "supervisor_id": SUP, "started_at": "2026-09-29T00:00:00Z",
                "proto_version": "0.2",
                "connections": [{"conn_id": RCONN, "target_id": "t1", "conn_dir": f"/tmp/tsb/conn-{RCONN}",
                                 "sessions": [{"session_id": RS, "session_type": "shell", "socket": "shell.sock"}]}]}
    content[f"manifests/workers/{RWORKER}.json"] = json.dumps(manifest).encode()
    content[f"control/{RCONN}.jsonl"] = jsonl(reattach_lines())
    content[f"control/supervisors/{SUP}.jsonl"] = content[f"control/supervisors/{SUP}.jsonl"] + jsonl(supervisor_run_lines())
    content[f"frames/{RUN1}/{RS}.bin"] = bf.encode_frames(run1_frames())
    content[f"frames/{RUN2}/{RS}.bin"] = bf.encode_frames(run2_frames())
    # A worker killed while collecting (the E1 worker-death rig): terminated / worker_lost.
    ts = "2026-09-29T00:10:00Z"
    audit = [{"ts": ts, "user": "u", "action": "session.connect", "target": "t1", "session_id": DEAD,
              "session_type": "shell"},
             {"ts": ts, "action": "session.disconnect", "session_id": DEAD, "session_type": "shell",
              "end_state": "terminated", "end_reason": "worker_lost"}]
    # The reattached session, closed by its owner in run 2; and the session that orphan_timeout
    # ended while the core was away, whose end the worker spooled and the returning core wrote.
    audit += [{"ts": ts, "user": "u", "action": "session.connect", "target": "t1", "session_id": RS, "session_type": "shell"},
              {"ts": ts, "user": "u", "action": "session.connect", "target": "t1", "session_id": RQ, "session_type": "shell"},
              {"ts": "2026-09-10T00:30:00Z", "user": "u", "action": "session.disconnect", "session_id": RS,
               "session_type": "shell", "end_state": "killed", "end_reason": "owner"},
              {"ts": ORPHAN_TS, "action": "session.disconnect", "session_id": RQ, "session_type": "shell",
               "end_state": "killed", "end_reason": "orphan"}]
    content["audit.jsonl"] = content["audit.jsonl"] + jsonl(audit)
    content[f"recordings/{DEAD}.ttyrec"], content[f"recordings/{DEAD}.dir"] = recording(
        [frame(0, 1, T, b"$ ")], end="terminated")
    content[f"recordings/{RS}.ttyrec"], content[f"recordings/{RS}.dir"] = manual_recording(rs_recording())
    content[f"recordings/{RQ}.ttyrec"], content[f"recordings/{RQ}.dir"] = bf.encode_recording(
        [bf.to_recording(frame(0, 1, T, b"$ ")), bf.end_marker(bf.rfc3339_ns(ORPHAN_TS), "killed")])
    return content


def rs_recording(frames=None) -> list[bf.RecFrame]:
    """What the core records for RS: run 1's output, the gap marker for 4 and 5 from run 2's
    handshake, the replay and what followed, then the owner's end. The passing case is written out
    from the published ttyrec layout and sidecar codes, not derived by the runner, so a bug in the
    runner's own derivation cannot make its expected recording agree with itself. `frames`, when
    given, is a mutation's own tap and is derived as the runner would."""
    if frames is not None:
        return bf.derive_recording(frames, {"end_state": "killed", "timestamp": bf.rfc3339_ns("2026-09-10T00:30:00Z")},
                                   [{"first_buffered_seq": 6, "first_buffered_offset": LOST_FROM,
                                     "last_dropped_ts": T + 5_000_000_000}])
    return [
        bf.RecFrame(1789000000, 123456, bf.OUT, b"$ "),
        bf.RecFrame(1789000001, 123456, bf.IN, b"tail -f log\r"),
        bf.RecFrame(1789000001, 123556, bf.OUT, b"tail -f log\r\n"),
        bf.RecFrame(1789000002, 123456, bf.OUT, b"line 3\r\n"),
        bf.RecFrame(1789000005, 123456, bf.GAP,
                    b"\r\n[TSB gap: 16 bytes lost from 2026-09-10T00:26:42.123456Z "
                    b"to 2026-09-10T00:26:45.123456Z]\r\n"),
        bf.RecFrame(1789000006, 123456, bf.OUT, b"line 6\r\n"),
        bf.RecFrame(1789000007, 123456, bf.OUT, b"line 7\r\n"),
        bf.RecFrame(1789000008, 123456, bf.OUT, b"line 8\r\n"),
        bf.RecFrame(1789000101, 123456, bf.IN, b"\x03"),
        bf.RecFrame(1789000101, 123556, bf.OUT, b"^C\r\n$ "),
        bf.RecFrame(1789000102, 123456, bf.OUT, b"$ "),
        bf.RecFrame(1789000200, 0, bf.END_KILLED, b""),
    ]


def kind_of_run(rel: str) -> tuple[str, str] | None:
    return ("C3", "run-frames") if rel.startswith("frames/") and rel.count("/") == 2 else None


def build_e1(root: Path, content: dict[str, bytes]) -> Path:
    """test_artifacts.build, with run-bounded taps listed as run-frames."""
    plain = {k: v for k, v in content.items() if not kind_of_run(k)}
    build(root, plain)
    doc = json.loads((root / "collected.json").read_text())
    for rel, data in content.items():
        if kind_of_run(rel):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
            doc["items"].append({"seam": "C3", "kind": "run-frames", "path": rel})
    (root / "collected.json").write_text(json.dumps(doc))
    return root


class E1Artefacts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.n = 0

    def grade(self, content: dict, name: str) -> verify.Result:
        self.n += 1
        root = build_e1(Path(self.tmp.name) / str(self.n), content)
        fixtures, _ = load_fixtures(CONTRACT)
        [fx] = [f for f in fixtures if f.name == name]
        return verify._grade(fx, Run(CONTRACT, Registry.from_tree(CONTRACT), "e1", None, root, None, 1.0))

    def assertFails(self, content: dict, fixture: str, needle: str) -> None:
        result = self.grade(content, fixture)
        self.assertEqual(result.outcome, verify.FAIL, f"{fixture} passed: {result.detail}")
        self.assertIn(needle, result.detail)

    def assertPasses(self, content: dict, fixture: str) -> None:
        result = self.grade(content, fixture)
        self.assertEqual(result.outcome, verify.PASS, f"{fixture}: {result.detail}")

    def with_lines(self, change) -> dict:
        lines = reattach_lines()
        change(lines)
        content = e1_files()
        content[f"control/{RCONN}.jsonl"] = jsonl(lines)
        return content

    @staticmethod
    def find(lines, kind, sid=None, run=None, nth=0):
        hits = [i for i, line in enumerate(lines) if (line["msg"].get("cmd") or line["msg"].get("event")) == kind
                and (sid is None or line["msg"].get("session_id") == sid) and (run is None or line.get("run") == run)]
        return hits[nth]

    def test_the_e1_collection_passes_every_c3_and_c5_artefact_fixture(self):
        root = build_e1(Path(self.tmp.name) / "all", e1_files())
        out = io.StringIO()
        status = verify.main(["verify", "--artifacts", str(root), "--seams", "C3,C5", "--profile", "e1"], out)
        text = out.getvalue()
        self.assertEqual([line for line in text.splitlines() if line.startswith("FAIL")], [], text)
        for name in ("reattach-sequence", "reattach-empty", "redial-replay", "gap-frame", "seq-continuity-across-restart",
                     "connect-carries-timers", "heartbeat-ping-pong", "supervisor-heartbeat", "linger-close",
                     "output-stamps-monotonic", "seq-monotonic-per-session", "manifest-proto-version"):
            self.assertRegex(text, rf"PASS +C[35] {name} ", name)
        self.assertEqual(status, verify.EXIT_NOT_ALL_GRADED, text)  # reference fixtures and other seams remain

    def test_the_e1_collection_passes_every_artefact_fixture_of_the_profile(self):
        root = build_e1(Path(self.tmp.name) / "every", e1_files())
        out = io.StringIO()
        status = verify.main(["verify", "--artifacts", str(root), "--seams", "C3,C5,C6,C8", "--profile", "e1"], out)
        text = out.getvalue()
        self.assertEqual([line for line in text.splitlines() if line.startswith("FAIL")], [], text)
        for name in ("gap-marker", "gap-after-overflow", "timestamps-monotonic", "event-spooled-end",
                     "worker-death-recorded", "end-marker-terminated", "ns-derivation-out", "roundtrip"):
            self.assertRegex(text, rf"PASS +C[68] {name} ", name)
        self.assertEqual(status, verify.EXIT_NOT_ALL_GRADED, text)  # reference fixtures remain the providers'

    # ---- the recording -------------------------------------------------------------------------

    def with_rs(self, frames) -> dict:
        content = e1_files()
        content[f"recordings/{RS}.ttyrec"], content[f"recordings/{RS}.dir"] = bf.encode_recording(frames)
        return content

    def test_a_gap_frame_with_no_gap_marker_is_a_silent_splice(self):
        frames = [f for f in rs_recording() if f.code != bf.GAP]
        self.assertFails(self.with_rs(frames), "gap-marker", "has no gap marker in the recording: the loss is "
                                                             "spliced silently")

    def test_a_gap_marker_with_the_wrong_count(self):
        frames = rs_recording()
        i = next(i for i, f in enumerate(frames) if f.code == bf.GAP)
        lost, start, end = bf.parse_gap_line(frames[i].data)
        frames[i] = bf.RecFrame(frames[i].sec, frames[i].usec, bf.GAP, bf.gap_line(lost + 1, start, end))
        self.assertFails(self.with_rs(frames), "gap-after-overflow", "the handshake 16")

    def test_a_gap_marker_no_gap_frame_declared(self):
        frames = rs_recording()
        stray = bf.RecFrame(frames[0].sec, frames[0].usec, bf.GAP,
                            bf.gap_line(3, (frames[0].sec, frames[0].usec), (frames[0].sec, frames[0].usec)))
        self.assertFails(self.with_rs([frames[0], stray] + frames[1:]), "gap-marker", "that no gap frame declared")

    def test_a_replayed_frame_recorded_twice(self):
        # After a reattach no output payload is recorded twice (x-sidecar reopen): the replay starts
        # after last_committed_seq, and a core that recorded frame 3 again breaks the match.
        frames = rs_recording()
        i = next(i for i, f in enumerate(frames) if f.code == bf.GAP)
        self.assertFails(self.with_rs(frames[:i] + [frames[i - 1]] + frames[i:]), "ns-derivation-out",
                         "are not the captured frames")

    def test_recording_stamps_that_go_back(self):
        frames = rs_recording()
        frames[-2] = bf.RecFrame(frames[0].sec, frames[0].usec, frames[-2].code, frames[-2].data)
        self.assertFails(self.with_rs(frames), "timestamps-monotonic", "the stamps go back")

    def test_a_gap_marker_whose_span_starts_elsewhere(self):
        # The span starts at the last output frame or gap marker the recording held: a marker that
        # starts it earlier overlaps output the recording kept, and one that starts it later hides
        # part of the loss.
        frames = rs_recording()
        i = next(i for i, f in enumerate(frames) if f.code == bf.GAP)
        lost, start, end = bf.parse_gap_line(frames[i].data)
        frames[i] = bf.RecFrame(frames[i].sec, frames[i].usec, bf.GAP, bf.gap_line(lost, (start[0] - 1, start[1]), end))
        self.assertFails(self.with_rs(frames), "gap-marker", "the gap marker's span starts")

    def two_markers(self, second_dropped: int) -> tuple[dict, list[bf.RecFrame]]:
        """The core wrote the first marker (gap frame 5) and lost its data socket before frame 6
        arrived; the ring then dropped 6 to 9, the last stamped `second_dropped`, and the lone
        re-dial declares them (gap frame 9). The collection, and the recording a core derives."""
        tap = [frame(2, 5, T + 5_000_000_000, b""), frame(2, 9, second_dropped, b""),
               frame(0, 10, T + 10_000_000_000, b"line 10\r\n")]
        acks = [{"first_buffered_seq": 6, "first_buffered_offset": LOST_FROM, "last_dropped_ts": T + 5_000_000_000},
                {"first_buffered_seq": 10, "first_buffered_offset": LOST_FROM + 32, "last_dropped_ts": second_dropped}]

        def redial_after_the_marker(lines):
            i = self.find(lines, "hello", RS, RUN2, nth=1)
            lines[i:i + 5] = hs(RS, 5, 10, LOST_FROM + 32, 10, dropped=second_dropped, run=RUN2)
        content = self.with_lines(redial_after_the_marker)
        content[f"frames/{RUN2}/{RS}.bin"] = bf.encode_frames(tap)
        merged, _ = bf.merge_runs([(RUN1, run1_frames()), (RUN2, tap)], {RUN2: 3})
        rec = bf.derive_recording(sorted(merged, key=lambda f: (f.timestamp, f.flags & bf.FLAG_IN)),
                                  {"end_state": "killed", "timestamp": bf.rfc3339_ns("2026-09-10T00:30:00Z")}, acks)
        content[f"recordings/{RS}.ttyrec"], content[f"recordings/{RS}.dir"] = bf.encode_recording(rec)
        return content, rec

    def test_two_markers_with_nothing_recorded_between(self):
        # A marker is a commit point, so the second span starts at the first marker — started at
        # frame 3, the two spans would overlap.
        content, rec = self.two_markers(T + 9_500_000_000)
        self.assertPasses(content, "gap-marker")
        first, second = (i for i, f in enumerate(rec) if f.code == bf.GAP)
        lost, _start, end = bf.parse_gap_line(rec[second].data)
        rec[second] = bf.RecFrame(rec[second].sec, rec[second].usec, bf.GAP,
                                  bf.gap_line(lost, (rec[first - 1].sec, rec[first - 1].usec), end))
        content[f"recordings/{RS}.ttyrec"], content[f"recordings/{RS}.dir"] = bf.encode_recording(rec)
        self.assertFails(content, "gap-marker", "the gap marker's span starts")

    def test_two_gaps_in_one_microsecond(self):
        # Frames 6 to 9 came in one burst, so the second gap frame's stamp is 100 ns after the
        # first's: both markers carry one microsecond. They are two losses, matched in order —
        # keyed by the microsecond, the second would read as undeclared and the first as unmatched.
        content, rec = self.two_markers(T + 5_000_000_100)
        self.assertEqual(len({(f.sec, f.usec) for f in rec if f.code == bf.GAP}), 1)
        self.assertPasses(content, "gap-marker")

    def died_before_the_markers_commit(self, run2_gap: bool = True) -> dict:
        """A third session, ST. Run 2's handshake declared 4 and 5 lost (gap frame 5), the core wrote
        that marker and died before the write that commits it. The recording is never cut, so the
        marker stays; run 3 realigns to the commit point before it (3), and its handshake finds 6 and 7
        dropped since: it writes a second marker for those 16 bytes alone, its span starting at the
        first marker. Run 2's gap frame is superseded by run 3's replay, yet it is what the first
        marker declares. Written out from the published layout, not derived by the runner."""
        st, run3 = "sess-t", "01789000200000000000"
        open_st = [core({"cmd": "open_shell", "session_id": st, "term": "xterm", "cols": 80, "rows": 24}),
                   worker({"event": "channel_opened", "session_id": st, "session_type": "shell", "socket": "shell-3.sock"})]
        died = hs(st, 3, 6, LOST_FROM, 5, dropped=T + 5_000_000_000, run=RUN2)[:3]  # hello, hello_ack, replay
        lines = reattach_lines()
        first_run2 = next(i for i, line in enumerate(lines) if line.get("run") == RUN2)
        lines = (lines[:first_run2] + tagged(open_st, RUN1) + hs(st, 0, 1, 0, 0, run=RUN1) + lines[first_run2:]
                 + died + hs(st, 3, 8, LOST_FROM + 16, 8, dropped=T + 7_000_000_000, run=run3))
        content = e1_files()
        content[f"control/{RCONN}.jsonl"] = jsonl(lines)
        content[f"frames/{RUN1}/{st}.bin"] = bf.encode_frames(run1_frames())
        content[f"frames/{RUN2}/{st}.bin"] = bf.encode_frames([frame(2, 5, T + 5_000_000_000, b"")] if run2_gap else [])
        content[f"frames/{run3}/{st}.bin"] = bf.encode_frames(
            [frame(2, 7, T + 7_000_000_000, b""), frame(0, 8, T + 8_000_000_000, b"line 8\r\n")])
        content[f"recordings/{st}.ttyrec"], content[f"recordings/{st}.dir"] = manual_recording([
            bf.RecFrame(1789000000, 123456, bf.OUT, b"$ "),
            bf.RecFrame(1789000001, 123456, bf.IN, b"tail -f log\r"),
            bf.RecFrame(1789000001, 123556, bf.OUT, b"tail -f log\r\n"),
            bf.RecFrame(1789000002, 123456, bf.OUT, b"line 3\r\n"),
            bf.RecFrame(1789000005, 123456, bf.GAP, b"\r\n[TSB gap: 16 bytes lost from 2026-09-10T00:26:42.123456Z "
                                                     b"to 2026-09-10T00:26:45.123456Z]\r\n"),
            bf.RecFrame(1789000007, 123456, bf.GAP, b"\r\n[TSB gap: 16 bytes lost from 2026-09-10T00:26:45.123456Z "
                                                     b"to 2026-09-10T00:26:47.123456Z]\r\n"),
            bf.RecFrame(1789000008, 123456, bf.OUT, b"line 8\r\n"),
        ])
        return content

    def test_a_marker_kept_past_a_core_that_died_before_its_commit(self):
        # Cutting the first marker would drop a declared loss when no handshake follows (an E10 end);
        # keeping it, the later marker counts only bytes lost since, and the two never overlap.
        self.assertPasses(self.died_before_the_markers_commit(), "gap-marker")
        self.assertPasses(self.died_before_the_markers_commit(), "gap-after-overflow")

    def test_a_kept_marker_still_needs_the_gap_frame_that_declared_it(self):
        # The superseded gap frame is what makes the first marker declared: without it in run 2's tap,
        # the marker declares a loss no handshake reported.
        self.assertFails(self.died_before_the_markers_commit(run2_gap=False), "gap-marker",
                         "that no gap frame declared")

    def nothing_dropped_since(self, declare_again: bool) -> dict:
        """ST again, but run 3's ring dropped nothing more: its handshake finds the same ring (gap
        frame 5 again, then 6 to 8). The loss counted against what the recording holds is zero, so
        the core writes no second marker: the first already declares it. `declare_again` writes the
        second marker an E1 core wrote, the same 16 bytes declared twice."""
        st, run3 = "sess-t", "01789000200000000000"
        content = self.died_before_the_markers_commit()
        lines = [json.loads(line) for line in content[f"control/{RCONN}.jsonl"].splitlines()]
        lines = [line for line in lines if not (line.get("run") == run3 and line["msg"].get("session_id") == st)]
        content[f"control/{RCONN}.jsonl"] = jsonl(lines + hs(st, 3, 6, LOST_FROM, 8, dropped=T + 5_000_000_000, run=run3))
        content[f"frames/{run3}/{st}.bin"] = bf.encode_frames(
            [frame(2, 5, T + 5_000_000_000, b""), frame(0, 6, T + 6_000_000_000, b"line 6\r\n"),
             frame(0, 7, T + 7_000_000_000, b"line 7\r\n"), frame(0, 8, T + 8_000_000_000, b"line 8\r\n")])
        marker = bf.RecFrame(1789000005, 123456, bf.GAP, b"\r\n[TSB gap: 16 bytes lost from 2026-09-10T00:26:42.123456Z "
                                                          b"to 2026-09-10T00:26:45.123456Z]\r\n")
        again = bf.RecFrame(1789000005, 123456, bf.GAP, b"\r\n[TSB gap: 16 bytes lost from 2026-09-10T00:26:45.123456Z "
                                                         b"to 2026-09-10T00:26:45.123456Z]\r\n")
        content[f"recordings/{st}.ttyrec"], content[f"recordings/{st}.dir"] = manual_recording([
            bf.RecFrame(1789000000, 123456, bf.OUT, b"$ "),
            bf.RecFrame(1789000001, 123456, bf.IN, b"tail -f log\r"),
            bf.RecFrame(1789000001, 123556, bf.OUT, b"tail -f log\r\n"),
            bf.RecFrame(1789000002, 123456, bf.OUT, b"line 3\r\n"),
            marker, *([again] if declare_again else []),
            bf.RecFrame(1789000006, 123456, bf.OUT, b"line 6\r\n"),
            bf.RecFrame(1789000007, 123456, bf.OUT, b"line 7\r\n"),
            bf.RecFrame(1789000008, 123456, bf.OUT, b"line 8\r\n"),
        ])
        return content

    def test_a_loss_already_declared_gets_no_second_marker(self):
        # The narrow reading of immortality §4.3: the marker already in the recording is the explicit
        # marker for this drop, so the re-sent gap frame matches it and nothing more is owed.
        self.assertPasses(self.nothing_dropped_since(declare_again=False), "gap-marker")

    def test_declaring_a_loss_twice_fails(self):
        # An E1 core counted the kept marker nowhere and declared the same loss again: two markers for
        # one gap frame, the second undeclared.
        self.assertFails(self.nothing_dropped_since(declare_again=True), "gap-marker", "that no gap frame declared")

    def first_marker_from(self, start: tuple[int, int]) -> dict:
        """RS's recording with no output before its first marker: run 1's output is left out, and the
        marker declares all 39 bytes before frame 6 lost, over a span that starts at `start`."""
        rec = rs_recording()
        i = next(i for i, f in enumerate(rec) if f.code == bf.GAP)
        _lost, _start, end = bf.parse_gap_line(rec[i].data)
        marker = bf.RecFrame(rec[i].sec, rec[i].usec, bf.GAP, bf.gap_line(LOST_FROM, start, end))
        return self.with_rs([f for f in rec[:i] if f.code != bf.OUT] + [marker] + rec[i + 1:])

    def test_a_first_span_starts_at_the_session_s_creation(self):
        # With no output before it, a marker's span starts at created_at, which no collected file
        # holds. The collection still bounds it: RS was opened in run 1, so after run 1 began, and
        # its record was persisted before its session.connect. A start outside them overstates, or
        # understates, the loss.
        created = bf._sec_usec(T)
        self.assertPasses(self.first_marker_from(created), "gap-marker")
        self.assertFails(self.first_marker_from((int(RUN1) // bf.NS - 1, 0)), "gap-marker",
                         "before the core run that opened the session began")
        content = self.first_marker_from(created)
        connected = "2026-09-10T00:26:40.1Z"  # after run 1 began, before the claimed creation
        content["audit.jsonl"] = content["audit.jsonl"].replace(
            b'{"ts": "2026-09-29T00:10:00Z", "user": "u", "action": "session.connect", "target": "t1", "session_id": "sess-r"',
            f'{{"ts": "{connected}", "user": "u", "action": "session.connect", "target": "t1", "session_id": "sess-r"'.encode())
        self.assertFails(content, "gap-marker", "after the session's session.connect")

    def test_a_gap_frame_stamped_other_than_the_last_frame_dropped(self):
        # The gap frame's stamp is hello_ack's last_dropped_ts; a worker that stamps it anything else
        # — even monotonically — misstates when the loss ended.
        def change(lines):
            lines[self.find(lines, "hello_ack", RS, RUN2)]["msg"]["last_dropped_ts"] = T + 4_000_000_000
        self.assertFails(self.with_lines(change), "gap-marker", "last_dropped_ts")

    UNSENT = bf.RecFrame(1789000003, 0, bf.IN, b"q")

    def test_a_keystroke_recorded_and_never_sent_across_a_kill(self):
        # The core records an input frame before it sends it, so a kill between the two leaves a
        # frame in the recording that no tap holds — one at the end of the killed run. Run 2 also
        # holds one lone re-dial, which may leave one more; a third is a keystroke the core invented.
        frames = rs_recording()
        after_run1 = next(i for i, f in enumerate(frames) if f.code == bf.GAP)
        for unsent in (1, 2, 3):
            with self.subTest(unsent=unsent):
                content = self.with_rs(frames[:after_run1] + [self.UNSENT] * unsent + frames[after_run1:])
                if unsent < 3:
                    self.assertPasses(content, "ns-derivation-in")
                else:
                    self.assertFails(content, "ns-derivation-in", "in frames are not the captured frames")

    def run2_with_two_inputs(self, unsent: int, redial: bool = True) -> dict:
        """Run 2 taps two input frames; the recording holds `unsent` recorded-but-unsent frames
        between them, and the capture keeps or drops run 2's lone re-dial of RS."""
        two = run2_frames() + [frame(1, 3, T + 101_500_000_000, b"\r")]

        def change(lines):
            if not redial:
                i = self.find(lines, "hello", RS, RUN2, nth=1)
                del lines[i:i + 5]
        content = self.with_lines(change)
        content[f"frames/{RUN2}/{RS}.bin"] = bf.encode_frames(two)
        merged, _ = bf.merge_runs([(RUN1, run1_frames()), (RUN2, two)], {RUN2: 3})
        rec = rs_recording(sorted(merged, key=lambda f: (f.timestamp, f.flags & bf.FLAG_IN)))
        last_in = max(i for i, f in enumerate(rec) if f.code == bf.IN)
        rec[last_in:last_in] = [self.UNSENT] * unsent
        content[f"recordings/{RS}.ttyrec"], content[f"recordings/{RS}.dir"] = bf.encode_recording(rec)
        return content

    def test_a_keystroke_recorded_and_never_sent_across_a_lone_redial(self):
        # A lone data-socket loss does inside a run what a kill does at a run's end: the keystroke
        # being written when the socket died is recorded and never tapped. One per re-dial; with no
        # re-dial in the run, the run's tapped input is recorded contiguously, as at v0.1.
        self.assertPasses(self.run2_with_two_inputs(0), "ns-derivation-in")
        self.assertPasses(self.run2_with_two_inputs(1), "ns-derivation-in")
        self.assertFails(self.run2_with_two_inputs(2), "ns-derivation-in", "in frames are not the captured frames")
        self.assertFails(self.run2_with_two_inputs(1, redial=False), "ns-derivation-in",
                         "in frames are not the captured frames")

    def non_pty(self, command: dict, kind: str, payloads: tuple[bytes, bytes]) -> dict:
        """RQ opened as `kind`: run 1 taps its seq 1, the run-2 handshake says the recording kept it,
        and run 2 taps seq 4 — output 2 and 3 died in flight with the killed core."""
        def change(lines):
            i = self.find(lines, "open_shell", RQ)
            lines[i]["msg"] = {**command, "session_id": RQ}
            lines[i + 1]["msg"]["session_type"] = kind
            h = self.find(lines, "hello", RQ, RUN2)
            lines[h]["msg"]["last_committed_seq"] = 1
            lines[h + 2]["msg"]["from_seq"] = 2
        content = self.with_lines(change)
        content[f"frames/{RUN1}/{RQ}.bin"] = bf.encode_frames([frame(0, 1, T, payloads[0])])
        content[f"frames/{RUN2}/{RQ}.bin"] = bf.encode_frames([frame(0, 4, T + 100_000_000_000, payloads[1])])
        return content

    def test_a_file_transfer_loses_in_flight_output_to_its_transfer_layer(self):
        # No ring (Contract §15.1): an sftp session's seqs may jump across a restart, with no gap
        # frame, because a file transfer resumes by offset; but a gap frame on one is a worker
        # declaring a loss from a ring it has none of.
        content = self.non_pty({"cmd": "open_sftp"}, "sftp", (b"\x00\x00\x00\x05", b"\x00\x00\x00\x09"))
        self.assertPasses(content, "gap-frame")
        content[f"frames/{RUN2}/{RQ}.bin"] = bf.encode_frames([frame(2, 3, T + 99_000_000_000, b""),
                                                             frame(0, 4, T + 100_000_000_000, b"\x00\x00\x00\x09")])
        self.assertFails(content, "gap-frame", "a gap frame on a sftp session")

    def test_only_a_file_transfer_resumes_by_offset(self):
        # Contract §15.1 lets a file transfer resume by offset; it says nothing of exec, netconf or
        # forward, whose loss rule waits for the tag that opens them. Until then a seq jump on one of
        # them is a silent splice (immortality §4.3), as on a shell.
        content = self.non_pty({"cmd": "open_subsystem", "name": "netconf"}, "netconf", (b"<hello/>", b"<rpc-reply/>"))
        self.assertFails(content, "gap-frame", "out seq jumps from 1 to 4 with no gap frame")

    def test_a_gap_frame_on_an_input_frame_in_a_tap(self):
        content = self.with_run2(run2_frames() + [frame(3, 3, T + 103_000_000_000, b"")])
        self.assertFails(content, "data-frame-envelope", "the gap bit on an input frame")

    def test_a_gap_frame_whose_numbers_cannot_be_checked(self):
        # The marker's numbers are the handshake's: with the recording or the declaring hello_ack
        # missing, or the tap it is read from torn, nothing proves the loss was declared truly.
        content = e1_files()
        del content[f"recordings/{RS}.ttyrec"], content[f"recordings/{RS}.dir"]
        self.assertFails(content, "gap-marker", f"recordings/{RS}: its tap holds a gap frame, and its recording was not "
                                                "collected")

        def undeclared(lines):  # no run-2 handshake reports the ring starting at 6
            for nth in (0, 1):
                lines[self.find(lines, "hello_ack", RS, RUN2, nth=nth)]["msg"]["first_buffered_seq"] = 4
        self.assertFails(self.with_lines(undeclared), "gap-marker", "gap frame 5 has no hello_ack with first_buffered_seq 6")
        content = e1_files()
        content[f"frames/{RUN2}/{RS}.bin"] += b"\x01\x00\x00"
        self.assertFails(content, "gap-marker", "torn envelope")

    def without_loss(self) -> dict:
        """The same restart with nothing lost: run 2 replays from 4 on, and no gap frame or marker."""
        replayed = [frame(f.flags, f.seq - 2, f.timestamp, f.payload) if f.direction == "out" else f
                    for f in run2_frames()[1:]]
        content = self.with_run2(replayed)
        merged, _ = bf.merge_runs([(RUN1, run1_frames()), (RUN2, replayed)], {RUN2: 3})
        ordered = sorted(merged, key=lambda f: (f.timestamp, f.flags & bf.FLAG_IN))
        content[f"recordings/{RS}.ttyrec"], content[f"recordings/{RS}.dir"] = bf.encode_recording(rs_recording(ordered))
        return content

    def test_a_restart_that_lost_nothing_proves_no_gap(self):
        # E1 gates on a forced overflow producing a gap marker (spine v0.2 row): a collection where
        # nothing was lost is correct, and proves nothing about the gap path.
        content = self.without_loss()
        self.assertFails(content, "gap-frame", "input missing: no gap frame was tapped")
        self.assertFails(content, "gap-marker", "input missing: no gap frame with its session's recording was collected")

    def test_one_frame_of_a_direction_proves_no_order(self):
        # Stamps "never go back" needs two stamps of a direction to compare.
        content = self.with_run2([f for f in run2_frames() if f.direction == "out"])
        del content[f"frames/{SID}.bin"]
        self.assertFails(content, "output-stamps-monotonic", "no capture holds two or more in frames")
        content = e1_files()
        content[f"recordings/{SID}.ttyrec"], content[f"recordings/{SID}.dir"] = recording(frames()[:2])
        content[f"recordings/{RS}.ttyrec"], content[f"recordings/{RS}.dir"] = bf.encode_recording(
            [f for f in rs_recording() if f.code != bf.IN])
        self.assertFails(content, "timestamps-monotonic",
                         "input missing: no recording holds two or more frames of each direction")

    # ---- the orphan end ---------------------------------------------------------------------------

    def test_a_gap_marker_after_the_orphan_s_end(self):
        content = e1_files()
        end = bf.rfc3339_ns(ORPHAN_TS)
        late = bf.RecFrame(*bf._sec_usec(end + 1_000_000), bf.GAP,
                           bf.gap_line(9, bf._sec_usec(T), bf._sec_usec(end + 1_000_000)))
        content[f"recordings/{RQ}.ttyrec"], content[f"recordings/{RQ}.dir"] = bf.encode_recording(
            [bf.to_recording(frame(0, 1, T, b"$ ")), late, bf.end_marker(end, "killed")])
        self.assertFails(content, "event-spooled-end", "after the end")

    def test_accounted_release_counts_as_ended_restart_recovery(self):
        content = e1_files()
        lines = reattach_lines()
        for line in lines:
            msg = line["msg"]
            if "proto_version" in msg:
                msg["proto_version"] = "0.8"
            if msg.get("event") == "hello_ack" and msg.get("ended"):
                msg["output_seal"] = {"sealed": True, "read_outcome": "eof", "final_last_seq": 0,
                                      "final_out_bytes": 0, "final_last_out_ts": 0,
                                      "observed_at_ns": T, "unknown_suffix": False}
            if msg.get("cmd") == "release" and msg.get("session_id") == RQ:
                msg["cmd"] = "release_accounted"
        # 0.8 requires the restarted core's first ping before any hello.
        start = next(i for i, line in enumerate(lines) if line.get("run") == RUN2)
        lines[start+1:start+1] = tagged([core({"cmd": "ping", "n": 99}),
                                       worker({"event": "pong", "n": 99})], RUN2)
        content[f"control/{RCONN}.jsonl"] = jsonl(lines)
        self.assertPasses(content, "reattach-sequence")

    def test_spooled_gap_before_code9_still_checks_recording_and_orphan_stamp(self):
        content = e1_files()
        end = bf.rfc3339_ns(ORPHAN_TS)
        out = bf.to_recording(frame(0, 1, T, b"$ "))
        gap = bf.RecFrame(*bf._sec_usec(T + 1_000_000), bf.GAP,
                          bf.gap_line(9, bf._sec_usec(T), bf._sec_usec(T + 1_000_000)))
        marker = bf.RecFrame(*bf._sec_usec(end), bf.TERMINAL_INCOMPLETE,
                             bf.terminal_incomplete_line(1, 11, "core_stop", end))
        rec = [out, gap, marker, bf.end_marker(end, "killed")]
        content[f"recordings/{RQ}.ttyrec"], content[f"recordings/{RQ}.dir"] = bf.encode_recording(rec)
        for fixture in ("gap-marker", "ns-derivation-out", "event-spooled-end"):
            self.assertPasses(content, fixture)
        late = end + 1_000_000
        rec[1] = bf.RecFrame(*bf._sec_usec(late), bf.GAP,
                            bf.gap_line(9, bf._sec_usec(T), bf._sec_usec(late)))
        content[f"recordings/{RQ}.ttyrec"], content[f"recordings/{RQ}.dir"] = bf.encode_recording(rec)
        self.assertFails(content, "event-spooled-end", "after the end")

    def test_an_orphan_end_carries_the_worker_s_stamp(self):
        content = e1_files()
        late = "2026-09-10T00:29:00Z"  # after run 2 began: the returning core's time, not the worker's
        content["audit.jsonl"] = content["audit.jsonl"].replace(ORPHAN_TS.encode(), late.encode())
        content[f"recordings/{RQ}.ttyrec"], content[f"recordings/{RQ}.dir"] = bf.encode_recording(
            [bf.to_recording(frame(0, 1, T, b"$ ")), bf.end_marker(bf.rfc3339_ns(late), "killed")])
        self.assertFails(content, "event-spooled-end", "after the latest core started")
        content = e1_files()
        content[f"recordings/{RQ}.ttyrec"], content[f"recordings/{RQ}.dir"] = bf.encode_recording(
            [bf.to_recording(frame(0, 1, T, b"$ ")), bf.end_marker(bf.rfc3339_ns(ORPHAN_TS) + 1_000_000_000, "killed")])
        self.assertFails(content, "event-spooled-end", "both are the worker's stamp of the end")

    def test_an_orphan_end_whose_recording_proves_nothing(self):
        # The orphan end is proved by its recording: without one, ended any other way, or beside a
        # recording that does not parse, the end marker the returning core owed is unshown.
        content = e1_files()
        del content[f"recordings/{RQ}.ttyrec"], content[f"recordings/{RQ}.dir"]
        self.assertFails(content, "event-spooled-end", f"session {RQ}: ended killed / orphan, and its recording was not "
                                                       "collected")
        content = e1_files()
        content[f"recordings/{RQ}.ttyrec"], content[f"recordings/{RQ}.dir"] = bf.encode_recording(
            [bf.to_recording(frame(0, 1, T, b"$ ")), bf.end_marker(bf.rfc3339_ns(ORPHAN_TS), "terminated")])
        self.assertFails(content, "event-spooled-end", "an orphan end, and the recording does not end in a killed marker")
        content = e1_files()
        content[f"recordings/{SID}.ttyrec"] = content[f"recordings/{SID}.ttyrec"][:-3]
        self.assertFails(content, "event-spooled-end", "torn header")

    def test_no_orphan_end_collected(self):
        content = e1_files()
        content["audit.jsonl"] = content["audit.jsonl"].replace(b'"end_reason": "orphan"', b'"end_reason": "idle"')
        self.assertFails(content, "event-spooled-end", "input missing: no orphan end was collected")

    # ---- the handshake ----------------------------------------------------------------------------

    def test_live_before_the_replay_is_complete(self):
        def change(lines):
            i = self.find(lines, "replay_done", RS, RUN2)
            lines[i], lines[i + 1] = lines[i + 1], lines[i]
        self.assertFails(self.with_lines(change), "reattach-sequence", "before replay_done")

    def test_input_before_live_is_a_core_defect(self):
        def change(lines):
            lines.insert(self.find(lines, "live", RS, RUN2), dict(worker(
                {"event": "input_refused", "session_id": RS, "seq": 2, "reason": "replaying"}), run=RUN2))
        self.assertFails(self.with_lines(change), "reattach-empty", "the core sent input before live")

    def test_a_replay_that_skips_what_the_core_lacks(self):
        def change(lines):
            lines[self.find(lines, "replay", RS, RUN2)]["msg"]["from_seq"] = 6
        self.assertFails(self.with_lines(change), "redial-replay", "a replay starts at the next seq")

    def test_each_kind_of_handshake_must_be_shown(self):
        # The e1 collection must show a new session's handshake, a reattach after a restart, a lone
        # re-dial and an ended session replayed and released (README "Artefacts").
        def no_ended(lines):
            i = self.find(lines, "hello", RQ, RUN2)
            del lines[i:i + 5]
        self.assertFails(self.with_lines(no_ended), "reattach-sequence",
                         "input missing: no capture shows an ended session replayed after a restart and released")

        def no_runs(lines):
            for line in lines:
                line.pop("run", None)
        # Without run boundaries the same lines are one core's: no restart is shown at all.
        self.assertFails(self.with_lines(no_runs), "reattach-sequence",
                         "input missing: no capture shows a reattach after a core restart")

    def test_a_new_session_whose_banner_arrived_first_is_still_new(self):
        # A real target prints its banner before the core's hello arrives, so a brand-new session's
        # hello_ack reports current_seq >= 1 and the replay carries those bytes. The core offered
        # last_committed_seq 0, so the handshake is the new session's all the same (C3 handshake).
        # Both in-run handshakes are given a banner: without the fix no "new" handshake is seen and
        # the capture fails as input missing.
        def change(lines):
            for sid in (RS, RQ):
                i = self.find(lines, "hello_ack", sid, RUN1)
                lines[i]["msg"]["current_seq"] = 1
                lines[self.find(lines, "replay_done", sid, RUN1)]["msg"]["through_seq"] = 1
        self.assertPasses(self.with_lines(change), "reattach-sequence")

    # ---- the taps across runs ---------------------------------------------------------------------

    def with_run2(self, frames) -> dict:
        content = e1_files()
        content[f"frames/{RUN2}/{RS}.bin"] = bf.encode_frames(frames)
        return content

    def test_a_loss_without_a_gap_frame_is_spliced_silently(self):
        self.assertFails(self.with_run2(run2_frames()[1:]), "gap-frame", "with no gap frame")

    def test_a_replay_with_other_bytes(self):
        # The hello says the recording keeps 1 to 3; a run-2 tap that "replays" 3 with other bytes
        # means the worker sent something other than what it sent before.
        def change(lines):
            i = self.find(lines, "hello", RS, RUN2)
            lines[i]["msg"]["last_committed_seq"] = 2
            lines[i + 2]["msg"]["from_seq"] = 3
        content = self.with_lines(change)
        content[f"frames/{RUN2}/{RS}.bin"] = bf.encode_frames(
            [frame(0, 3, T + 2_000_000_000, b"LINE 3\r\n")] + run2_frames())
        self.assertFails(content, "seq-continuity-across-restart", "replayed with other bytes or another stamp")

    def test_the_replay_overlap_is_taken_once(self):
        # A tap ahead of its recording at the kill: run 1 tapped 3, the hello says 2 is the last
        # committed, and run 2 replays 3 again, identically. frames-seq, a v0.1 check, takes it once.
        def change(lines):
            i = self.find(lines, "hello", RS, RUN2)
            lines[i]["msg"]["last_committed_seq"] = 2
            lines[i + 2]["msg"]["from_seq"] = 3
        content = self.with_lines(change)
        content[f"frames/{RUN2}/{RS}.bin"] = bf.encode_frames([run1_frames()[3]] + run2_frames())
        self.assertPasses(content, "seq-continuity-across-restart")
        self.assertPasses(content, "seq-monotonic-per-session")

    def test_a_repeat_inside_one_run_is_still_a_repeat(self):
        frames = run2_frames()
        self.assertFails(self.with_run2(frames + [frames[-1]]), "seq-monotonic-per-session", "repeat or decrease")
        self.assertFails(self.with_run2(frames + [frames[-1]]), "gap-frame", "repeat or decrease")

    def test_no_restart_in_the_taps_proves_no_continuity(self):
        content = e1_files()
        del content[f"frames/{RUN2}/{RS}.bin"]
        self.assertFails(content, "gap-frame", "no session's output was tapped across a core restart")

    def test_a_torn_tail_ends_a_killed_run_only(self):
        content = e1_files()
        content[f"frames/{RUN1}/{RS}.bin"] = bf.encode_frames(run1_frames()) + b"\x01\x00\x00"
        self.assertPasses(content, "gap-frame")  # run 1's core was killed mid-write: cut
        content = e1_files()
        content[f"frames/{RUN2}/{RS}.bin"] = bf.encode_frames(run2_frames()) + b"\x01\x00\x00"
        self.assertFails(content, "gap-frame", "torn envelope")  # the latest run: still a torn tap

    def test_a_session_tapped_with_and_without_runs(self):
        content = e1_files()
        content[f"frames/{RS}.bin"] = bf.encode_frames(run1_frames())
        self.assertFails(content, "data-frame-envelope", "tapped both with and without a run boundary")

    def test_stamps_that_go_back(self):
        frames = run2_frames()
        frames[2] = frame(0, 7, T + 5_500_000_000, b"line 7\r\n")  # before line 6's stamp
        self.assertFails(self.with_run2(frames), "output-stamps-monotonic", "the stamps go back")
        # Across directions nothing is assumed: input stamped before output is no problem.
        frames = run2_frames()
        frames[4] = frame(1, 2, T + 1_500_000_000, b"\x03")
        self.assertPasses(self.with_run2(frames), "output-stamps-monotonic")

    # ---- timers, heartbeats, linger -------------------------------------------------------------

    def test_a_connect_to_a_v02_worker_without_its_values(self):
        def change(lines):
            del lines[0]["msg"]["timers"]
        self.assertFails(self.with_lines(change), "connect-carries-timers", "without timers")

    def test_the_worker_version_comes_from_worker_created_too(self):
        # With no manifest collected, the supervisor's worker_created names the version.
        def change(lines):
            del lines[0]["msg"]["buffer_bytes"]
        content = self.with_lines(change)
        del content[f"manifests/workers/{RWORKER}.json"]
        self.assertFails(content, "connect-carries-timers", "without buffer_bytes")

    def test_a_skipped_ping(self):
        def change(lines):
            del lines[self.find(lines, "pong", run=RUN1)]
            lines.insert(self.find(lines, "ping", run=RUN1, nth=1) + 1, dict(worker({"event": "pong", "n": 2}), run=RUN1))
        self.assertFails(self.with_lines(change), "heartbeat-ping-pong", "ping 1 was never answered")

    def test_an_unanswered_kill(self):
        content = e1_files()
        lines = supervisor_run_lines()[:-1]
        content[f"control/supervisors/{SUP}.jsonl"] = jsonl(copy.deepcopy(lines))
        self.assertFails(content, "supervisor-heartbeat", "kill_worker worker-hung was never answered")

    def test_a_linger_close_that_lists_a_session(self):
        def change(lines):
            lines[-1]["msg"]["session_ids"] = [RQ]
        self.assertFails(self.with_lines(change), "linger-close", "$.session_ids: more than 0 items")

    def test_no_linger_close_captured(self):
        def change(lines):
            lines[-1]["msg"]["cause"] = "transport"
        self.assertFails(self.with_lines(change), "linger-close", "input missing: no linger close was captured")

    def test_a_linger_close_while_a_session_is_open(self):
        # connection_linger arms only once no live session is left: a close with RS still open
        # ends a session no timer reached.
        def change(lines):
            del lines[-4:-1]  # RS is never closed or released
        self.assertFails(self.with_lines(change), "linger-close", f"a linger close while sessions ['{RS}'] are open")

    def test_nothing_captured_proves_nothing(self):
        # Each of these checks grades what a collection shows; with none of it shown, it fails as
        # input missing rather than passing on nothing.
        def no_heartbeat(lines):
            lines[:] = [line for line in lines if line["msg"].get("cmd") != "ping" and line["msg"].get("event") != "pong"]
        self.assertFails(self.with_lines(no_heartbeat), "heartbeat-ping-pong",
                         "input missing: no ping answered by pong was captured on a connection's control socket")
        content = e1_files()
        quiet = [line for line in supervisor_run_lines() if line["msg"].get("cmd") != "ping"
                 and line["msg"].get("event") != "pong"]
        content[f"control/supervisors/{SUP}.jsonl"] = files()[f"control/supervisors/{SUP}.jsonl"] + jsonl(quiet)
        self.assertFails(content, "supervisor-heartbeat",
                         "input missing: no ping answered by pong was captured on a supervisor's socket")
        content = e1_files()
        del content[f"manifests/workers/{RWORKER}.json"]
        unversioned = supervisor_run_lines()
        del unversioned[1]["msg"]["proto_version"]
        content[f"control/supervisors/{SUP}.jsonl"] = files()[f"control/supervisors/{SUP}.jsonl"] + jsonl(unversioned)
        self.assertFails(content, "connect-carries-timers",
                         "input missing: no connect to a worker whose version is known")


if __name__ == "__main__":
    unittest.main()
