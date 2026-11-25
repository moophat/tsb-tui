"""`--artifacts`: a consistent collection passes every artefact-graded fixture, and each way a
core, a worker or a harness can get a clause wrong fails the fixture that encodes the clause.

The collection is synthetic but internally consistent: the recordings are derived from the tapped
frames exactly as the contract says, the ended session's recording closes with the marker its
audit end event names, the manifests describe the captured connections, and the captures show
every exchange the C3 fixtures promise (host key first use, a changed key, a refused password,
the echo driver, an unknown-command probe, resize / close / disconnect, a failed spawn). Each
mutation then breaks one thing.
"""

import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tsbc import _binfmt as bf
from tsbc import _ipc, verify
from tsbc._run import Run
from tsbc.fixtures import load_fixtures
from tsbc.schema import Registry

from . import CONTRACT

T = 1789000000123456789
SID, LIVE_SID, ECHO_SID = "sess-1", "sess-2", "sess-3"
CONN, ECHO, CHANGED, REFUSED, FAILED_SPAWN = "conn-1", "conn-2", "conn-3", "conn-4", "conn-5"
WORKER, SUP = "worker-1", "sup-1"
FP, FP_OTHER = "SHA256:FIXTURE" + "0" * 36, "SHA256:FIXTURE" + "1" * 36
END_T = T + 9_000_000_000
PEM = "-----BEGIN " + "OPENSSH PRIVATE KEY" + "-----"  # assembled so this file holds no key header


def frames():
    # Two frames each way: one frame of a direction proves nothing about its order.
    return [bf.DataFrame(0, 1, T, b"$ "), bf.DataFrame(1, 1, T + 1_000_000_000, b"ls"),
            bf.DataFrame(1, 2, T + 1_100_000_000, b"\r"), bf.DataFrame(0, 2, T + 1_100_100_000, b"ls\r\nnotes.txt\r\n$ ")]


def recording(tapped, end="killed"):
    """The recording the core must write for tapped frames, closed by `end` (None: still live)."""
    rec = [bf.to_recording(f) for f in tapped]
    if end:
        rec.append(bf.end_marker(END_T, end))
    return bf.encode_recording(rec)


# The passing case's bytes are written here to the published layouts, never through the runner's own
# codec: a grader bug in that codec would otherwise round-trip to itself and pass the case it built.
NS, US = 1_000_000_000, 1_000
ENVELOPE = struct.Struct(">BBHIQQ")  # schemas/ipc-data-frame.schema.json x-binary-layout
TTYREC = struct.Struct("<III")       # classic ttyrec: sec, usec, length (little-endian)


def _sec_usec(timestamp: int) -> tuple[int, int]:
    return timestamp // NS, (timestamp % NS) // US


def tap_bytes(tapped) -> bytes:
    return b"".join(
        ENVELOPE.pack(f.version, f.flags, f.stream, len(f.payload), f.seq, f.timestamp) + f.payload
        for f in tapped)


def recording_bytes(tapped, end=None) -> tuple[bytes, bytes]:
    """(the .ttyrec, the .dir sidecar) for tapped frames, hand-written to the published layout. The
    sidecar code is 0 for output and 1 for input; `end` (killed/terminated) appends the marker last."""
    entries = [(*_sec_usec(f.timestamp), 0x01 if f.flags & 0x01 else 0, f.payload) for f in tapped]
    if end is not None:
        entries.append((*_sec_usec(END_T), 3 if end == "killed" else 4, b""))
    ttyrec = b"".join(TTYREC.pack(sec, usec, len(data)) + data for sec, usec, _code, data in entries)
    return ttyrec, bytes(code for _sec, _usec, code, _data in entries)


def manual_recording(frames) -> tuple[bytes, bytes]:
    """(the .ttyrec, the .dir sidecar) for recorder frames, hand-written to the published layout so
    the passing case never leans on the runner's own derivation or encoder."""
    ttyrec = b"".join(TTYREC.pack(f.sec, f.usec, len(f.data)) + f.data for f in frames)
    return ttyrec, bytes(f.code for f in frames)


def ssh_connect(auth=None):
    return {"cmd": "connect", "target_id": "t1", "protocol": "ssh", "host": "192.0.2.10", "port": 22, "user": "u",
            "auth": auth or {"kind": "key", "key": "FIXTURE-NOT-A-KEY"}}


def shell(sid):
    return {"cmd": "open_shell", "session_id": sid, "term": "xterm-256color", "cols": 80, "rows": 24}


def opened(sid, sock="shell.sock"):
    return {"event": "channel_opened", "session_id": sid, "session_type": "shell", "socket": sock}


def core(msg, **kw):
    return {"from": "core", "msg": msg, **kw}


def worker(msg):
    return {"from": "worker", "msg": msg}


PROBE = core({"cmd": "x-probe-later-command"}, probe=True)


def control_lines(lost_ids=(LIVE_SID,)):
    """The main connection: first-use host key, two shells, resize, close one, a probe, disconnect."""
    return [
        core(ssh_connect()),
        worker({"event": "hostkey", "conn_id": CONN, "fingerprint": FP, "first_use": True}),
        worker({"event": "connected", "conn_id": CONN}),
        core(shell(SID)),
        worker(opened(SID)),
        core(shell(LIVE_SID)),
        worker(opened(LIVE_SID, "shell-2.sock")),
        core({"cmd": "resize", "session_id": SID, "cols": 100, "rows": 30}),
        core({"cmd": "close_channel", "session_id": SID}),
        worker({"event": "channel_closed", "session_id": SID, "by": "core", "reason": "closed by the core"}),
        PROBE,
        worker({"event": "error", "code": "unknown_cmd", "message": "unknown cmd 'x-probe-later-command'"}),
        core({"cmd": "disconnect"}),
        worker({"event": "connection_lost", "conn_id": CONN, "session_ids": list(lost_ids), "cause": "disconnect",
                "reason": "disconnect"}),
    ]


def conn04_lines(hello_version="0.4", ack_version="0.4"):
    """The main connection spoken to in proto_version 0.4: the handshake hello → hello_ack, each carrying
    the version, before the core disconnects. hello_version / ack_version say what the two offer."""
    return [
        core(ssh_connect()),
        worker({"event": "hostkey", "conn_id": CONN, "fingerprint": FP, "first_use": True}),
        worker({"event": "connected", "conn_id": CONN}),
        core(shell(SID)),
        worker(opened(SID)),
        core({"cmd": "hello", "session_id": SID, "proto_version": hello_version, "last_committed_seq": 0}),
        worker({"event": "hello_ack", "session_id": SID, "proto_version": ack_version,
                "first_buffered_seq": 1, "first_buffered_offset": 0, "current_seq": 0, "ended": False}),
        core({"cmd": "disconnect"}),
        worker({"event": "connection_lost", "conn_id": CONN, "session_ids": [SID], "cause": "disconnect",
                "reason": "disconnect"}),
    ]


def echo_lines(target_exits: bool = True):
    """The echo driver's connection; the target closes its shell's channel unless told otherwise."""
    lines = [core({"cmd": "connect", "target_id": "t-echo", "protocol": "echo"}),
             worker({"event": "connected", "conn_id": ECHO}), core(shell(ECHO_SID)), worker(opened(ECHO_SID))]
    if target_exits:
        lines.append(worker({"event": "channel_closed", "session_id": ECHO_SID, "by": "target",
                             "reason": "the shell exited"}))
    return lines


def echo_end(state="terminated", reason="target_closed"):
    """The audit lines for the echo session: opened, then ended — by default as its target's close ends it."""
    ts = "2026-09-23T12:00:00Z"
    return [{"ts": ts, "user": "u", "action": "session.connect", "target": "t-echo", "session_id": ECHO_SID,
             "session_type": "shell"},
            {"ts": ts, "action": "session.disconnect", "session_id": ECHO_SID, "session_type": "shell",
             "end_state": state, "end_reason": reason}]


def changed_key_lines():
    return [core(ssh_connect()),
            worker({"event": "error", "code": "hostkey_changed",
                    "message": f"host key changed: recorded {FP_OTHER}, presented {FP}; refused"})]


def refused_lines(password="FIXTURE-NOT-A-PASSWORD"):
    return [core(ssh_connect({"kind": "password", "password": password})),
            worker({"event": "auth_failed", "conn_id": REFUSED, "reason": "keyboard-interactive: bad password"})]


def supervisor_lines():
    return [core({"cmd": "create_worker", "conn_id": CONN, "placement_key": "u", "n_max": 2}),
            {"from": "supervisor", "msg": {"event": "worker_created", "conn_id": CONN, "worker_id": WORKER,
                                           "conn_dir": f"/tmp/tsb/conn-{CONN}"}},
            core({"cmd": "create_worker", "conn_id": FAILED_SPAWN, "placement_key": "u", "n_max": 1}),
            {"from": "supervisor", "msg": {"event": "error", "reason": "spawn_failed", "conn_id": FAILED_SPAWN,
                                           "exit_code": 1}}]


def audit_lines():
    ts = "2026-09-23T12:00:00Z"
    return [
        {"ts": ts, "action": "config.recording_state", "detail": {"enabled": True}},
        {"ts": ts, "user": "u", "action": "auth.login"},
        {"ts": ts, "user": "u", "action": "target.hostkey_accepted", "target": "t1", "detail": {"fingerprint": FP}},
        {"ts": ts, "user": "u", "action": "session.connect", "target": "t1", "session_id": SID, "session_type": "shell"},
        {"ts": ts, "user": "u", "action": "session.connect", "target": "t1", "session_id": LIVE_SID,
         "session_type": "shell"},
        {"ts": ts, "user": "u", "action": "session.disconnect", "session_id": SID, "session_type": "shell",
         "end_state": "killed", "end_reason": "owner", "duration": 9,
         "recording_path": f"/var/lib/tsb/recordings/{SID}.ttyrec"},
    ]


def jsonl(rows) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in rows).encode()


def files() -> dict[str, bytes]:
    manifest = {"worker_id": WORKER, "pid": 4201, "supervisor_id": SUP, "started_at": "2026-09-23T12:00:00Z",
                "connections": [{"conn_id": CONN, "target_id": "t1", "conn_dir": f"/tmp/tsb/conn-{CONN}",
                                 "sessions": [{"session_id": SID, "session_type": "shell", "socket": "shell.sock"},
                                              {"session_id": LIVE_SID, "session_type": "shell",
                                               "socket": "shell-2.sock"}]}]}
    sup = {"supervisor_id": SUP, "pid": 1, "control_sock": f"/tmp/tsb/supervisors/{SUP}/control.sock",
           "started_at": "2026-09-23T11:59:00Z"}
    ttyrec, sidecar = recording_bytes(frames(), end="killed")
    live_ttyrec, live_sidecar = recording_bytes([bf.DataFrame(0, 1, T, b"$ ")])
    return {
        f"manifests/workers/{WORKER}.json": json.dumps(manifest).encode(),
        f"manifests/supervisors/{SUP}/manifest.json": json.dumps(sup).encode(),
        f"frames/{SID}.bin": tap_bytes(frames()),
        f"recordings/{SID}.ttyrec": ttyrec,
        f"recordings/{SID}.dir": sidecar,
        f"recordings/{LIVE_SID}.ttyrec": live_ttyrec,
        f"recordings/{LIVE_SID}.dir": live_sidecar,
        "audit.jsonl": jsonl(audit_lines() + echo_end()),
        f"control/{CONN}.jsonl": jsonl(control_lines()),
        f"control/{ECHO}.jsonl": jsonl(echo_lines()),
        f"control/{CHANGED}.jsonl": jsonl(changed_key_lines()),
        f"control/{REFUSED}.jsonl": jsonl(refused_lines()),
        f"control/supervisors/{SUP}.jsonl": jsonl(supervisor_lines()),
    }


KINDS = (("manifests/workers/", "C5", "worker-manifest"), ("manifests/supervisors/", "C5", "supervisor-manifest"),
         ("frames/", "C3", "frames"), ("audit.jsonl", "C6", "audit"),
         ("control/supervisors/", "C3", "supervisor-control"), ("control/", "C3", "control"))


def kind_of(rel: str) -> tuple[str, str]:
    if rel.startswith("recordings/"):
        return "C8", "recording-direction" if rel.endswith(".dir") else "recording"
    return next((seam, kind) for prefix, seam, kind in KINDS if rel.startswith(prefix))


def build(root: Path, content: dict[str, bytes], *, collected: bool = True) -> Path:
    items = []
    for rel, data in content.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        seam, kind = kind_of(rel)
        items.append({"seam": seam, "kind": kind, "path": rel})
    if collected:
        (root / "collected.json").write_text(json.dumps(
            {"collected_at": "2026-09-23T12:05:00Z", "node_role": "lab", "items": items}))
    return root


class Artefacts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def grade(self, name: str) -> verify.Result:
        fixtures, _ = load_fixtures(CONTRACT)
        [fx] = [f for f in fixtures if f.name == name]
        run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e0", None, self.dir, None, 1.0)
        return verify._grade(fx, run)

    def assertFails(self, content: dict, fixture: str, needle: str, **kw) -> None:
        build(self.dir, content, **kw)
        result = self.grade(fixture)
        self.assertEqual(result.outcome, verify.FAIL, f"{fixture} passed: {result.detail}")
        self.assertIn(needle, result.detail)

    def run_verify(self, *extra: str) -> tuple[int, str]:
        out = io.StringIO()
        # A v0.1 collection, graded at the epoch it was built for: exactly the v0.1 verdicts.
        status = verify.main(["verify", "--artifacts", str(self.dir), "--seams", "C3,C5,C6,C8", "--profile", "e0",
                              *extra], out)
        return status, out.getvalue()

    def test_a_consistent_collection_passes_every_artefact_fixture(self):
        build(self.dir, files())
        status, text = self.run_verify()
        self.assertEqual([line for line in text.splitlines() if line.startswith("FAIL")], [], text)
        self.assertIn(" 0 FAIL ", text)
        self.assertIn("SKIP      C3 open-sftp", text)                   # published, not required at e0
        self.assertIn("SKIP      C8 worker-death-recorded", text)       # required green from e1
        self.assertIn("SKIP      C8 end-marker-terminated", text)       # likewise: e0 kills no worker
        # The rediscovery rule is graded only by U2's and U11's own tests, which this run did not
        # include, and C1/C2 were filtered out: a partial run must never read as green.
        self.assertIn("UNGRADED  C5 manifest-rediscovery-case", text)
        self.assertEqual(status, verify.EXIT_NOT_ALL_GRADED, text)
        self.assertIn("seams graded: C3, C6, C8", text)

    def test_provider_results_grade_the_reference_fixtures_they_name(self):
        # The results are what grades a reference fixture, not the caller's word about units: the
        # collection plus the results that name C5's rediscovery tests grade the fixture.
        build(self.dir, files())
        from .test_provider_results import provider_results
        with tempfile.TemporaryDirectory() as res:
            status, text = self.run_verify("--provider-results",
                                           *[str(p) for p in provider_results(Path(res))])
        self.assertIn("PROVIDER  C5 manifest-rediscovery-case", text)
        self.assertIn("seams graded: C3, C5, C6, C8", text)
        self.assertIn("C1 (filtered out by --seams)", text)
        self.assertEqual(status, verify.EXIT_NOT_ALL_GRADED, text)  # still partial: C1 and C2 were not run

    def test_without_artefacts_the_seams_fail_as_input_missing(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as empty:
            status = verify.main(["verify", "--artifacts", empty, "--seams", "C5"], out)
        self.assertEqual(status, verify.EXIT_FAIL)
        self.assertIn("input missing: collected.json not found", out.getvalue())
        self.assertIn("seams with input missing: C5", out.getvalue())

    # ---- the collection is the files in it ----------------------------------------------------

    def test_a_symbolic_link_fails_the_whole_collection(self):
        # A link could make the runner grade, or scan, a file outside the directory instead of the
        # evidence collected.json names; the outside copy here is valid, so only the link is wrong.
        with tempfile.TemporaryDirectory() as elsewhere:
            outside = Path(elsewhere) / "audit.jsonl"
            outside.write_bytes(jsonl(audit_lines()))
            build(self.dir, files())
            (self.dir / "audit.jsonl").unlink()
            (self.dir / "audit.jsonl").symlink_to(outside)
            result = self.grade("event-vocabulary")
            self.assertEqual(result.outcome, verify.FAIL, result.detail)
            self.assertIn("audit.jsonl: symbolic links", result.detail)
            self.dir = self.dir / "linked-dir"  # a linked directory, even one collected.json never names
            build(self.dir, files())
            (self.dir / "extra").symlink_to(Path(elsewhere), target_is_directory=True)
            result = self.grade("worker-manifest-valid")
            self.assertEqual(result.outcome, verify.FAIL, result.detail)
            self.assertIn("extra: symbolic links", result.detail)

    def test_a_connection_id_that_starts_like_a_supervisor_is_still_a_connection(self):
        # Which socket a capture is from is its kind and directory, never a prefix of its id.
        content = files()
        content["control/supervisor-blue.jsonl"] = content.pop(f"control/{ECHO}.jsonl")
        build(self.dir, content)
        for fixture in ("control-connect-echo", "supervisor-create-worker-ready"):
            with self.subTest(fixture=fixture):
                result = self.grade(fixture)
                self.assertEqual(result.outcome, verify.PASS, result.detail)

    def test_a_file_that_is_not_utf8_fails_its_fixture_not_the_run(self):
        content = files()
        content["audit.jsonl"] = b"\xff\xfe not text\n"
        self.assertFails(content, "event-vocabulary", "audit.jsonl is not UTF-8 text")

    def test_a_non_finite_number_is_not_json(self):
        # Python's json reads NaN; JSON has no such number, and a check must not pass on one.
        content = files()
        lines = jsonl(audit_lines()).decode() + '{"ts": "2026-09-23T12:00:00Z", "user": "u", ' \
            '"action": "auth.login", "detail": {"attempts": NaN}}\n'
        content["audit.jsonl"] = lines.encode()
        self.assertFails(content, "event-vocabulary", "NaN is not a JSON number")

    # ---- the collection must not carry credentials ------------------------------------------

    def test_a_capture_with_a_real_credential_fails_the_whole_collection(self):
        content = files()
        content[f"control/{REFUSED}.jsonl"] = jsonl(refused_lines(password="hunter2"))
        build(self.dir, content)
        for fixture in ("control-auth-failed", "worker-manifest-valid", "roundtrip"):
            with self.subTest(fixture=fixture):
                result = self.grade(fixture)
                self.assertEqual(result.outcome, verify.FAIL)
                self.assertIn("credential material", result.detail)
                self.assertNotIn("hunter2", result.detail)

    def test_key_material_displayed_in_a_recording_fails_the_collection(self):
        content = files()
        leaked = frames() + [bf.DataFrame(0, 3, T + 2_000_000_000, PEM.encode() + b"\r\n")]
        content[f"frames/{SID}.bin"] = bf.encode_frames(leaked)
        content[f"recordings/{SID}.ttyrec"], content[f"recordings/{SID}.dir"] = recording(leaked)
        self.assertFails(content, "roundtrip", "credential material")

    # ---- C8 ------------------------------------------------------------------------------------

    def test_recording_stamped_at_arrival_not_by_the_worker(self):
        content = files()
        late = [bf.DataFrame(f.flags, f.seq, f.timestamp + 5_000_000, f.payload) if f.direction == "out" else f
                for f in frames()]
        content[f"recordings/{SID}.ttyrec"], content[f"recordings/{SID}.dir"] = recording(late)
        self.assertFails(content, "ns-derivation-out", "timestamps derived")

    def test_input_recorded_with_another_clock(self):
        content = files()
        late = [bf.DataFrame(f.flags, f.seq, f.timestamp + 7_000, f.payload) if f.direction == "in" else f
                for f in frames()]
        content[f"recordings/{SID}.ttyrec"], content[f"recordings/{SID}.dir"] = recording(late)
        self.assertFails(content, "ns-derivation-in", "in frames")

    def test_a_spooled_end_does_not_drop_the_last_input_frame(self):
        # A recording may end with a spool gap marker then the end marker (#57). For the in
        # direction the gap marker is never in `recorded` (codes excludes it), so the trailing-marker
        # trim must not delete the last real input frame: in "ls\r" + gap + end:killed must match the
        # single tapped input frame. Before the fix `recorded[:-1]` emptied the input list.
        tapped_in = bf.DataFrame(1, 3, T, b"ls\r")
        gap = bf.RecFrame(*bf._sec_usec(T + 1_000_000_000), bf.GAP,
                          bf.gap_line(7, bf._sec_usec(T), bf._sec_usec(T + 1_000_000_000)))
        rec = [bf.to_recording(tapped_in), gap, bf.end_marker(END_T, "killed")]
        content = files()
        content[f"frames/{SID}.bin"] = bf.encode_frames([tapped_in])
        content[f"recordings/{SID}.ttyrec"], content[f"recordings/{SID}.dir"] = bf.encode_recording(rec)
        build(self.dir, content)
        result = self.grade("ns-derivation-in")
        self.assertEqual(result.outcome, verify.PASS, result.detail)

    def test_old_manifest_rejects_new_connect_timer(self):
        content = files()
        path = f"manifests/workers/{WORKER}.json"
        manifest = json.loads(content[path])
        manifest["proto_version"] = "0.7"
        content[path] = json.dumps(manifest).encode()
        lines = control_lines()
        lines[0]["msg"]["timers"] = {"heartbeat_interval": 10000, "heartbeat_missed": 3,
                                      "keepalive_interval": 30000, "keepalive_count_max": 12,
                                      "connection_linger": 300000, "orphan_timeout": 60000,
                                      "idle": 900000, "unattended": 1800000, "output_drain_timeout": 5000}
        content[f"control/{CONN}.jsonl"] = jsonl(lines)
        self.assertFails(content, "control-message-shape", "terminal vocabulary sent to pre-0.8 peer")

    def test_torn_recording(self):
        content = files()
        content[f"recordings/{SID}.ttyrec"] = content[f"recordings/{SID}.ttyrec"][:-3]
        self.assertFails(content, "roundtrip", "torn header")

    def test_recording_without_its_sidecar(self):
        content = files()
        del content[f"recordings/{SID}.dir"]
        self.assertFails(content, "roundtrip", "is not collected")

    def test_sidecar_that_does_not_count_the_frames(self):
        content = files()
        content[f"recordings/{SID}.dir"] = content[f"recordings/{SID}.dir"][:-1]
        self.assertFails(content, "roundtrip", "ttyrec frames")

    def test_recordings_that_hold_no_frames_prove_nothing(self):
        content = files()
        for sid in (SID, LIVE_SID):
            content[f"recordings/{sid}.ttyrec"], content[f"recordings/{sid}.dir"] = b"", b""
        self.assertFails(content, "roundtrip", "input missing: the collected recordings hold no frames")

    def test_recording_without_input(self):
        content = files()
        content[f"recordings/{SID}.ttyrec"], content[f"recordings/{SID}.dir"] = recording(
            [f for f in frames() if f.direction == "out"])
        self.assertFails(content, "mixed-direction", "no recording holds both directions")

    def test_a_frame_after_the_end_marker(self):
        content = files()
        rec = [bf.to_recording(f) for f in frames()]
        ttyrec, sidecar = bf.encode_recording(rec[:2] + [bf.end_marker(END_T, "killed")] + rec[2:])
        content[f"recordings/{SID}.ttyrec"], content[f"recordings/{SID}.dir"] = ttyrec, sidecar
        self.assertFails(content, "roundtrip", "must be the last frame")

    def test_an_ended_session_whose_recording_has_no_end_marker(self):
        content = files()
        content[f"recordings/{SID}.ttyrec"], content[f"recordings/{SID}.dir"] = recording(frames(), end=None)
        self.assertFails(content, "end-marker-killed", "with no end marker")

    def test_an_end_marker_of_the_wrong_kind(self):
        content = files()
        content[f"recordings/{SID}.ttyrec"], content[f"recordings/{SID}.dir"] = recording(frames(), end="terminated")
        self.assertFails(content, "end-marker-terminated", "ends in a terminated marker")

    def test_an_end_marker_with_no_end_event(self):
        content = files()
        content["audit.jsonl"] = jsonl(r for r in audit_lines() if r["action"] != "session.disconnect")
        self.assertFails(content, "end-marker-killed", "the audit has no end event")

    def test_a_crash_cut_recording_is_never_live_in_the_end_state_checks(self):
        # A recording whose last frame is the code-9 marker is an unfinished end, never a live one:
        # a finalized collection needs the original end marker and its matching session.disconnect.
        crash = "sess-5"
        out = bf.RecFrame(1, 0, bf.OUT, b"ls")
        marker = bf.RecFrame(*bf._sec_usec(END_T), bf.TERMINAL_INCOMPLETE,
                             bf.terminal_incomplete_line(1, 2, "core_wait_timeout", END_T))
        content = files()
        content[f"recordings/{crash}.ttyrec"], content[f"recordings/{crash}.dir"] = manual_recording([out, marker])
        # Mixed collection: SID's killed recording plus the crash-cut one, no end marker and no disconnect.
        self.assertFails(content, "end-marker-killed", f"recordings/{crash}: ends in a terminal incompleteness marker")
        # Finalized: the original end marker and its matching session.disconnect make it pass. The live
        # recording (LIVE_SID, no code 9, no end) is still treated as live, so the check passes.
        content[f"recordings/{crash}.ttyrec"], content[f"recordings/{crash}.dir"] = manual_recording(
            [out, marker, bf.end_marker(END_T, "killed")])
        content["audit.jsonl"] = jsonl(audit_lines() + echo_end() + [
            {"ts": "2026-09-23T12:00:00Z", "user": "u", "action": "session.disconnect", "session_id": crash,
             "session_type": "shell", "end_state": "killed", "end_reason": "owner", "duration": 9,
             "recording_path": f"/var/lib/tsb/recordings/{crash}.ttyrec"}])
        build(self.dir, content)
        self.assertEqual(self.grade("end-marker-killed").outcome, verify.PASS)

    def test_a_session_the_audit_ends_twice(self):
        # The end event closes a session's record; two of them, whichever comes last, is a lie.
        content = files()
        lines = audit_lines()
        lines.insert(-1, dict(lines[-1], end_state="terminated", end_reason="connection_lost"))
        content["audit.jsonl"] = jsonl(lines)
        self.assertFails(content, "event-session-ended", "a session ends once")
        self.assertFails(content, "end-marker-killed", "a session ends once")

    def test_a_terminated_marker_is_not_proved_by_a_killed_one(self):
        # The collection ends one session killed only; the terminated fixture must not pass on it.
        self.assertFails(files(), "end-marker-terminated",
                         "input missing: no recording of a session that ended terminated")

    def test_a_worker_death_must_be_recorded_as_terminated(self):
        # Required green from e1, when the harness can kill a worker while collecting; graded here
        # directly, since the check itself is the same at every epoch.
        dead = "sess-4"
        content = files()
        self.assertFails(content, "worker-death-recorded", "input missing")
        lines = audit_lines() + [
            {"ts": "2026-09-23T12:00:00Z", "user": "u", "action": "session.connect", "target": "t1",
             "session_id": dead, "session_type": "shell"},
            {"ts": "2026-09-23T12:00:00Z", "action": "session.disconnect", "session_id": dead,
             "session_type": "shell", "end_state": "terminated", "end_reason": "worker_lost"}]
        content["audit.jsonl"] = jsonl(lines)
        self.dir = self.dir / "unrecorded"
        self.assertFails(content, "worker-death-recorded", "its recording was not collected")
        content[f"recordings/{dead}.ttyrec"], content[f"recordings/{dead}.dir"] = recording(
            [bf.DataFrame(0, 1, T, b"$ ")], end="terminated")
        self.dir = self.dir / "recorded"
        build(self.dir, content)
        self.assertEqual(self.grade("worker-death-recorded").outcome, verify.PASS)
        self.assertEqual(self.grade("end-marker-terminated").outcome, verify.PASS)
        content[f"recordings/{dead}.ttyrec"], content[f"recordings/{dead}.dir"] = recording(
            [bf.DataFrame(0, 1, T, b"$ ")], end="killed")
        self.dir = self.dir / "again"
        self.assertFails(content, "worker-death-recorded", "not finalized with one terminated marker")

    # ---- C5 ------------------------------------------------------------------------------------

    def test_manifest_with_a_field_this_tag_does_not_define(self):
        content = files()
        doc = json.loads(content[f"manifests/workers/{WORKER}.json"])
        doc["x_later_field"] = "0.2"
        content[f"manifests/workers/{WORKER}.json"] = json.dumps(doc).encode()
        self.assertFails(content, "manifest-closed-v0.1", "not allowed")

    def test_manifest_whose_id_is_not_its_file_name(self):
        content = files()
        content["manifests/workers/other.json"] = content.pop(f"manifests/workers/{WORKER}.json")
        self.assertFails(content, "worker-manifest-valid", "does not match its file location")

    # ---- C3 --------------------------------------------------------------------------------------

    def test_connection_lost_that_forgets_a_session(self):
        content = files()
        content[f"control/{CONN}.jsonl"] = jsonl(control_lines(lost_ids=()))
        self.assertFails(content, "connection-lost-lists-sessions", "open sessions are")

    def test_host_key_reported_after_connected(self):
        content = files()
        lines = control_lines()
        lines[1], lines[2] = lines[2], lines[1]
        content[f"control/{CONN}.jsonl"] = jsonl(lines)
        self.assertFails(content, "control-connect-openshell-sequence", "host key")
        self.assertFails(content, "hostkey-event", "outside connect → connected")

    def test_a_changed_host_key_that_still_connects(self):
        content = files()
        content[f"control/{CHANGED}.jsonl"] = jsonl(changed_key_lines()[:1] + [
            worker({"event": "connected", "conn_id": CHANGED})] + changed_key_lines()[1:])
        self.assertFails(content, "control-hostkey-changed", "hostkey_changed arrived outside")

    def test_no_refused_password_captured(self):
        content = files()
        del content[f"control/{REFUSED}.jsonl"]
        self.assertFails(content, "control-auth-failed", "input missing: no authentication failure")

    def test_the_target_closing_a_channel_ends_that_session_terminated_target_closed(self):
        # SSH_MSG_CHANNEL_CLOSE from the target is an end from outside TSB (session model §13):
        # killed would make it TSB's end, and a session left without an end event lingers.
        for state, reason, needle in (("killed", "idle", "the audit says killed / idle"),
                                      ("terminated", "connection_lost", "the audit says terminated / connection_lost")):
            with self.subTest(reason=reason):
                content = files()
                content["audit.jsonl"] = jsonl(audit_lines() + echo_end(state, reason))
                self.dir = Path(self.tmp.name) / reason
                self.assertFails(content, "ends-from-outside", needle)
        content = files()
        content["audit.jsonl"] = jsonl(audit_lines() + echo_end()[:1])
        self.dir = Path(self.tmp.name) / "no-end"
        self.assertFails(content, "ends-from-outside", "the audit says no end event")

    def test_a_lost_connection_ends_every_session_on_it_by_its_cause(self):
        # SSH_MSG_DISCONNECT from the target is target_closed; our end gone or link_timeout is
        # connection_lost — for every session the loss lists. From 0.4, a timer end or target_closed
        # are also allowed (the worker may have started its end before the loss).
        lost_sid = "sess-6"
        for cause, reason in (("target_disconnect", "target_closed"), ("transport", "connection_lost"),
                              ("keepalive_timeout", "connection_lost")):
            with self.subTest(cause=cause):
                capture = [core(ssh_connect()), worker({"event": "connected", "conn_id": "conn-6"}),
                           core(shell(lost_sid)), worker(opened(lost_sid)),
                           worker({"event": "connection_lost", "conn_id": "conn-6", "session_ids": [lost_sid],
                                   "cause": cause, "reason": "x"})]
                end = {"ts": "2026-09-23T12:00:00Z", "action": "session.disconnect", "session_id": lost_sid,
                       "session_type": "shell", "end_state": "terminated"}
                content = files()
                content["control/conn-6.jsonl"] = jsonl(capture)
                self.dir = Path(self.tmp.name) / f"{cause}-ok"
                content["audit.jsonl"] = jsonl(audit_lines() + echo_end() + [dict(end, end_reason=reason)])
                build(self.dir, content)
                self.assertEqual(self.grade("ends-from-outside").outcome, verify.PASS)
                # From 0.4, both terminated reasons and timer ends are allowed for connection_lost
                # A killed / owner end is not allowed (worker never decides owner)
                content["audit.jsonl"] = jsonl(audit_lines() + echo_end() + [dict(end, end_state="killed",
                                                                                   end_reason="owner")])
                self.dir = Path(self.tmp.name) / f"{cause}-wrong"
                self.assertFails(content, "ends-from-outside", "allowed:")
        from tsbc import artifacts
        # A cause or a by this tag does not know is an end from outside TSB it cannot place: the
        # capture's shape check refuses it, and the rule still never reads it as the core's own.
        ends, _ = artifacts._outside_ends([("worker", {"event": "connection_lost", "session_ids": ["x"],
                                                       "cause": "a-later-cause"}, False)])
        self.assertEqual(ends,
                         {"x": {("terminated", "target_closed"), ("terminated", "connection_lost"),
                               ("killed", "orphan"), ("killed", "idle"), ("killed", "unattended")}})
        ends2, _ = artifacts._outside_ends([("worker", {"event": "channel_closed", "session_id": "x",
                                                        "by": "a-later-party"}, False)])
        self.assertEqual(ends2, {"x": {("terminated", "target_closed")}})

    def test_a_target_close_stays_authoritative_after_a_lost_connection(self):
        # A session the target closed is ended by that close; a later connection_lost listing it does not
        # widen its expected end (a session ends once). So the audit must say terminated / target_closed,
        # not a worker-timer end.
        sid = "sess-7"
        capture = [core(ssh_connect()), worker({"event": "connected", "conn_id": "conn-7"}),
                   core(shell(sid)), worker(opened(sid)),
                   worker({"event": "channel_closed", "session_id": sid, "by": "target", "reason": "the shell exited"}),
                   worker({"event": "connection_lost", "conn_id": "conn-7", "session_ids": [sid],
                           "cause": "target_disconnect", "reason": "the target dropped"})]
        end = {"ts": "2026-09-23T12:00:00Z", "action": "session.disconnect", "session_id": sid, "session_type": "shell"}
        content = files()
        content["control/conn-7.jsonl"] = jsonl(capture)
        content["audit.jsonl"] = jsonl(audit_lines() + echo_end()
                                        + [dict(end, end_state="terminated", end_reason="target_closed")])
        self.dir = Path(self.tmp.name) / "target-close-authoritative"
        build(self.dir, content)
        self.assertEqual(self.grade("ends-from-outside").outcome, verify.PASS)
        # A worker-timer end is not the target's close, so it fails.
        content["audit.jsonl"] = jsonl(audit_lines() + echo_end() + [dict(end, end_state="killed", end_reason="idle")])
        self.dir = Path(self.tmp.name) / "target-close-timer-end"
        self.assertFails(content, "ends-from-outside", "the audit says killed / idle")

    def test_the_core_s_own_closes_are_not_ends_from_outside(self):
        # close_channel then channel_closed by core, and disconnect then connection_lost of cause
        # disconnect, are the core's own; without the echo shell's close nothing from outside remains.
        content = files()
        content[f"control/{ECHO}.jsonl"] = jsonl(echo_lines(target_exits=False))
        self.assertFails(content, "ends-from-outside", "input missing: no end by the target was captured")

    def test_no_echo_driver_captured(self):
        content = files()
        del content[f"control/{ECHO}.jsonl"]
        self.assertFails(content, "control-connect-echo", "input missing")

    def test_unknown_command_not_answered_by_unknown_cmd(self):
        content = files()
        content[f"control/{CONN}.jsonl"] = jsonl(line for line in control_lines()
                                                  if line["msg"].get("code") != "unknown_cmd")
        self.assertFails(content, "control-unknown-cmd", "was not answered by error unknown_cmd")

    def test_a_probe_claimed_by_the_worker(self):
        content = files()
        content[f"control/{CONN}.jsonl"] = jsonl(control_lines() + [
            {"from": "worker", "msg": {"event": "x-later"}, "probe": True}])
        self.assertFails(content, "control-message-shape", "only the harness")

    def test_close_channel_never_answered(self):
        content = files()
        content[f"control/{CONN}.jsonl"] = jsonl(line for line in control_lines()
                                                  if line["msg"].get("event") != "channel_closed")
        self.assertFails(content, "control-resize-close-disconnect", "never answered by channel_closed")

    def test_supervisor_that_never_answers(self):
        content = files()
        content[f"control/supervisors/{SUP}.jsonl"] = jsonl(supervisor_lines()[:1])
        self.assertFails(content, "supervisor-create-worker-ready", "never answered")

    def test_a_worker_given_another_user_s_connection_fails_the_capture(self):
        # A collected supervisor capture is held to placement too: one user's worker taking another
        # user's connection is the tenant breach per_user placement exists to prevent.
        content = files()
        content[f"control/supervisors/{SUP}.jsonl"] = jsonl(supervisor_lines() + [
            core({"cmd": "create_worker", "conn_id": "conn-6", "placement_key": "someone-else", "n_max": 2}),
            {"from": "supervisor", "msg": {"event": "worker_created", "conn_id": "conn-6", "worker_id": WORKER,
                                           "conn_dir": "/tmp/tsb/conn-conn-6"}}])
        self.assertFails(content, "supervisor-create-worker-ready", "a worker never holds two keys")

    def test_no_failed_spawn_captured(self):
        content = files()
        content[f"control/supervisors/{SUP}.jsonl"] = jsonl(supervisor_lines()[:2])
        self.assertFails(content, "supervisor-create-worker-error", "input missing")

    def test_frames_with_a_repeated_seq(self):
        content = files()
        content[f"frames/{SID}.bin"] = bf.encode_frames(frames() + [bf.DataFrame(0, 2, T + 3_000_000_000, b"x")])
        self.assertFails(content, "seq-monotonic-per-session", "repeat or decrease")

    def test_one_frame_of_a_direction_proves_no_order(self):
        content = files()
        single_in = [f for f in frames() if not (f.direction == "in" and f.seq == 2)]
        content[f"frames/{SID}.bin"] = bf.encode_frames(single_in)
        self.assertFails(content, "seq-monotonic-per-session", "no capture holds two or more in frames")

    def test_frames_with_the_reserved_gap_bit(self):
        # From v0.2 bit 1 is the gap frame (decision 5.14), which declares output lost and carries
        # none: set on a frame with a payload it would file real output as a loss.
        content = files()
        content[f"frames/{SID}.bin"] = bf.encode_frames([bf.DataFrame(0x02, 1, T, b"x")])
        self.assertFails(content, "data-frame-envelope", "a gap frame carries 1 payload bytes")

    # ---- C6 --------------------------------------------------------------------------------------

    def test_audit_without_the_recording_state(self):
        content = files()
        content["audit.jsonl"] = jsonl(audit_lines()[1:])
        self.assertFails(content, "event-recording-state", "not on the record")

    def test_recorded_session_missing_from_the_audit(self):
        content = files()
        content["audit.jsonl"] = jsonl(r for r in audit_lines()
                                       if not (r["action"] == "session.connect" and r["session_id"] == SID))
        self.assertFails(content, "event-session-connect", "no session.connect")

    def test_first_use_host_key_missing_from_the_audit(self):
        content = files()
        content["audit.jsonl"] = jsonl(r for r in audit_lines() if r["action"] != "target.hostkey_accepted")
        self.assertFails(content, "event-hostkey-accepted", "no target.hostkey_accepted")

    def test_an_end_event_that_does_not_say_how_the_session_ended(self):
        content = files()
        lines = audit_lines()
        del lines[-1]["end_state"]
        content["audit.jsonl"] = jsonl(lines)
        self.assertFails(content, "event-vocabulary", "line 6")
        self.assertFails(content, "event-session-ended", "no session ended")

    def test_a_reason_from_the_other_end_state(self):
        content = files()
        lines = audit_lines()
        lines[-1]["end_reason"] = "worker_lost"  # killed, yet the worker died: which was it?
        content["audit.jsonl"] = jsonl(lines)
        self.assertFails(content, "event-missing-session-type-invalid", "line 6")

    def test_listed_file_that_is_missing(self):
        build(self.dir, files())
        (self.dir / f"frames/{SID}.bin").unlink()
        result = self.grade("data-frame-envelope")
        self.assertEqual(result.outcome, verify.FAIL)
        self.assertIn("listed but missing", result.detail)

    def test_outside_end_rejects_mismatched_state_reason_pair(self):
        # fails today: HEAD logic accepts any (state, reason) where reason is in the allowed set
        # For channel_closed by: target, HEAD would accept both terminated/target_closed AND
        # killed/target_closed (if schema allowed it). My fix only accepts the tuple ("terminated", "target_closed").
        # Since schema blocks killed/target_closed, test this by mocking _audit_ends to inject the invalid pair.
        from unittest.mock import patch

        from tsbc import artifacts

        content = files()
        content[f"control/{ECHO}.jsonl"] = jsonl(echo_lines(target_exits=True))
        content["audit.jsonl"] = jsonl(audit_lines() + echo_end())  # Default terminated/target_closed
        build(self.dir, content)

        # Mock _audit_ends to return killed/target_closed for sess-3 (bypassing schema validation)
        original_audit_ends = artifacts._audit_ends
        def mock_audit_ends(run, col):
            ends, problems = original_audit_ends(run, col)
            # Inject schema-invalid but test-valid data: killed/target_closed for sess-3
            ends[ECHO_SID] = {"ts": "2026-09-23T12:00:00Z", "action": "session.disconnect",
                             "session_id": ECHO_SID, "session_type": "shell",
                             "end_state": "killed", "end_reason": "target_closed"}
            return ends, problems

        with patch.object(artifacts, '_audit_ends', side_effect=mock_audit_ends):
            result = self.grade("ends-from-outside")
            # HEAD: killed and target_closed in {"target_closed"} → pass (BUG)
            # My fix: ("killed", "target_closed") in {("terminated", "target_closed")} → fail (CORRECT)
            self.assertEqual(result.outcome, verify.FAIL)
            self.assertIn("allowed: terminated / target_closed", result.detail)
            self.assertIn("killed / target_closed", result.detail)

    def test_outside_end_rejects_terminated_with_timer_reason(self):
        # fails today: for transport connection_lost, HEAD's allowed_reasons includes timer reasons
        # HEAD accepts terminated/idle because: terminated and idle in {"target_closed", "connection_lost",
        # "orphan", "idle", "unattended"} → pass. But idle is a killed-only reason (timer).
        # My fix stores tuples and would only accept ("killed", "idle"), not ("terminated", "idle").
        from unittest.mock import patch

        from tsbc import artifacts

        sid = "sess-transport"
        content = files()
        content[f"control/{CONN}.jsonl"] = jsonl([
            core(ssh_connect()),
            worker({"event": "hostkey", "conn_id": CONN, "fingerprint": FP, "first_use": True}),
            worker({"event": "connected", "conn_id": CONN}),
            core(shell(sid)),
            worker(opened(sid)),
            worker({"event": "connection_lost", "conn_id": CONN, "session_ids": [sid],
                    "cause": "transport", "reason": "connection reset"})])
        lines = [r for r in audit_lines() if r.get("session_id") not in (SID, LIVE_SID)]
        lines.append({"ts": "2026-09-23T12:00:00Z", "user": "u", "action": "session.connect",
                      "target": "t1", "session_id": sid, "session_type": "shell"})
        lines.append({"ts": "2026-09-23T12:00:00Z", "action": "session.disconnect", "session_id": sid,
                      "session_type": "shell", "end_state": "terminated", "end_reason": "connection_lost"})
        lines.extend(echo_end())
        content["audit.jsonl"] = jsonl(lines)
        build(self.dir, content)

        # Mock _audit_ends to inject terminated/idle (bypassing schema validation)
        original_audit_ends = artifacts._audit_ends
        def mock_audit_ends(run, col):
            ends, problems = original_audit_ends(run, col)
            # Inject schema-invalid but test-valid data: terminated/idle for the transport session
            ends[sid] = {"ts": "2026-09-23T12:00:00Z", "action": "session.disconnect",
                        "session_id": sid, "session_type": "shell",
                        "end_state": "terminated", "end_reason": "idle"}
            return ends, problems

        with patch.object(artifacts, '_audit_ends', side_effect=mock_audit_ends):
            result = self.grade("ends-from-outside")
            # HEAD: terminated and idle in allowed_reasons → pass (BUG)
            # My fix: ("terminated", "idle") not in allowed_tuples → fail (CORRECT)
            self.assertEqual(result.outcome, verify.FAIL)
            self.assertIn("killed / idle", result.detail)
            self.assertIn("terminated / idle", result.detail)

    def test_target_disconnect_allows_timer_ends(self):
        # fails today: connection_lost cause: target_disconnect should allow timer ends too
        # A session listed in a target_disconnect connection_lost may also end with a timer tuple
        sid = "sess-disc"
        content = files()
        # Use only the main connection with the disconnect session
        content[f"control/{CONN}.jsonl"] = jsonl(control_lines(lost_ids=(LIVE_SID,)) + [
            core(shell(sid)),
            worker(opened(sid, "shell-disc.sock")),
            worker({"event": "connection_lost", "conn_id": "conn-disc", "session_ids": [sid],
                    "cause": "target_disconnect", "reason": "target disconnected"})])
        lines = audit_lines()
        # Add audit for the disconnect session
        lines.append({"ts": "2026-09-23T12:00:00Z", "user": "u", "action": "session.connect",
                      "target": "t1", "session_id": sid, "session_type": "shell"})
        lines.append({"ts": "2026-09-23T12:00:00Z", "action": "session.disconnect", "session_id": sid,
                      "session_type": "shell", "end_state": "killed", "end_reason": "idle"})
        # Also add the echo session end (from files())
        lines.extend(echo_end())
        content["audit.jsonl"] = jsonl(lines)
        # This should pass: target_disconnect allows timer ends
        build(self.dir, content)
        result = self.grade("ends-from-outside")
        self.assertEqual(result.outcome, verify.PASS, f"should pass: {result.detail}")

    def test_outside_ends_input_missing_on_only_transport_loss(self):
        # fails today: the input-missing guard checks allowed sets, not actual target ends
        # Remove all other control captures except one with only transport loss
        content = {
            f"manifests/workers/{WORKER}.json": files()[f"manifests/workers/{WORKER}.json"],
            f"manifests/supervisors/{SUP}/manifest.json": files()[f"manifests/supervisors/{SUP}/manifest.json"],
            f"recordings/{SID}.ttyrec": files()[f"recordings/{SID}.ttyrec"],
            f"recordings/{SID}.dir": files()[f"recordings/{SID}.dir"],
        }
        # Only a transport connection_lost (no by: target end)
        content[f"control/{CONN}.jsonl"] = jsonl([
            core(ssh_connect()),
            worker({"event": "hostkey", "conn_id": CONN, "fingerprint": FP, "first_use": True}),
            worker({"event": "connected", "conn_id": CONN}),
            core(shell(SID)),
            worker(opened(SID)),
            worker({"event": "connection_lost", "conn_id": CONN, "session_ids": [SID],
                    "cause": "transport", "reason": "connection reset"})])
        content["audit.jsonl"] = jsonl([r for r in audit_lines() if r.get("session_id") != LIVE_SID])
        self.assertFails(content, "ends-from-outside", "input missing")

    def test_audit_orphan_end_without_control_capture(self):
        # fails today: audit_orphan_end must catch Missing from _conn_captures like other helpers
        content = files()
        # Remove all control captures
        content = {k: v for k, v in content.items() if not k.startswith("control/")}
        # Change the audit to show an orphan end
        lines = audit_lines()
        lines[-1]["end_state"] = "killed"
        lines[-1]["end_reason"] = "orphan"
        content["audit.jsonl"] = jsonl(lines)
        # Add the recording with killed marker
        ttyrec, sidecar = recording_bytes(frames(), end="killed")
        content[f"recordings/{SID}.ttyrec"] = ttyrec
        content[f"recordings/{SID}.dir"] = sidecar
        # Should not fail with "input missing" at the orphan-end check
        build(self.dir, content)
        result = self.grade("event-spooled-end")
        # It should pass or fail for other reasons, but not "input missing" from _conn_captures
        if result.outcome == verify.FAIL:
            self.assertNotIn("input missing", result.detail, "should not fail input missing from _conn_captures")

    def test_orphan_end_stamp_must_equal_the_worker_event(self):
        # The restart-time exemption holds only when the worker-end event names a stamp that both the audit
        # event and the recording's end marker carry. When the audit and recording agree with each other but
        # differ from the event's ended_at, the exemption does not apply: the end is stamped after the core started.
        from .test_artifacts_v02 import (
            ORPHAN_TS,
            RCONN,
            RQ,
            RUN2,
            build_e1,
            e1_files,
            frame,
        )
        content = e1_files()
        late = "2026-09-10T00:29:00Z"  # after run 2 began
        lines = [json.loads(l) for l in content["audit.jsonl"].decode().splitlines()]
        for l in lines:
            if l.get("session_id") == RQ and l.get("action") == "session.disconnect":
                l["ts"] = late  # audit and recording agree, at a stamp after the latest core started
        content["audit.jsonl"] = jsonl(lines)
        content[f"recordings/{RQ}.ttyrec"], content[f"recordings/{RQ}.dir"] = bf.encode_recording(
            [bf.to_recording(frame(0, 1, T, b"$ ")), bf.end_marker(bf.rfc3339_ns(late), "killed")])
        # The worker-end event in the latest run names an ended_at that neither the audit nor the recording carries
        by_worker = {"from": "worker", "run": RUN2, "msg": {"event": "channel_closed", "session_id": RQ,
                        "by": "worker", "reason": "idle timer", "end_state": "killed", "end_reason": "idle",
                        "ended_at": ORPHAN_TS, "out_bytes": 10, "last_seq": 1, "last_out_ts": T}}
        content[f"control/{RCONN}.jsonl"] = content[f"control/{RCONN}.jsonl"] + jsonl([by_worker])
        root = build_e1(self.dir, content)
        fixtures, _ = load_fixtures(CONTRACT)
        [fx] = [f for f in fixtures if f.name == "event-spooled-end"]
        result = verify._grade(fx, Run(CONTRACT, Registry.from_tree(CONTRACT), "e1", None, root, None, 1.0))
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn("after the latest core started", result.detail)


class ControlVersionMatch(unittest.TestCase):
    """A capture whose peer is at proto_version 0.4 (the worker's manifest, or the
    supervisor's worker_created, names it) must be spoken to in 0.4 — the core's hello offers 0.4 and the
    worker's hello_ack answers 0.4. A capture with no 0.4 peer grades as today."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def grade(self, name: str) -> verify.Result:
        fixtures, _ = load_fixtures(CONTRACT)
        [fx] = [f for f in fixtures if f.name == name]
        run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e0", None, self.dir, None, 1.0)
        return verify._grade(fx, run)

    def content(self, hello_version, ack_version, peer_version):
        content = files()
        doc = json.loads(content[f"manifests/workers/{WORKER}.json"])
        doc["proto_version"] = peer_version  # the worker's manifest names its version for this connection
        content[f"manifests/workers/{WORKER}.json"] = json.dumps(doc).encode()
        content[f"control/{CONN}.jsonl"] = jsonl(conn04_lines(hello_version, ack_version))
        return content

    def test_hello_offers_03_to_a_04_peer_fails(self):
        # A core that speaks its peer's 0.4 must offer 0.4 in hello; offering 0.3 names the connection and both versions.
        content = self.content("0.3", "0.4", "0.4")
        build(self.dir, content)
        result = self.grade("control-message-shape-v0.4")
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn(f"control/{CONN}: hello offers '0.3' to a peer at proto_version 0.4", result.detail)

    def test_hello_and_ack_at_04_pass(self):
        content = self.content("0.4", "0.4", "0.4")
        build(self.dir, content)
        result = self.grade("control-message-shape-v0.4")
        self.assertEqual(result.outcome, verify.PASS, f"failed: {result.detail}")

    def test_all_03_peers_pass_unchanged(self):
        # No 0.4 peer (the manifest says 0.3): the capture grades as today, hello and ack in 0.3 pass.
        content = self.content("0.3", "0.3", "0.3")
        build(self.dir, content)
        result = self.grade("control-message-shape-v0.4")
        self.assertEqual(result.outcome, verify.PASS, f"failed: {result.detail}")

    def test_hello_ack_answers_03_to_a_04_peer_fails(self):
        content = self.content("0.4", "0.3", "0.4")
        build(self.dir, content)
        result = self.grade("control-message-shape-v0.4")
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn(f"control/{CONN}: hello_ack answers '0.3' for a peer at proto_version 0.4", result.detail)

    def test_hello_and_ack_at_05_to_a_05_peer_pass(self):
        # The rule is numeric and extends to 0.5: a peer at 0.5 is spoken to in 0.5.
        content = self.content("0.5", "0.5", "0.5")
        build(self.dir, content)
        result = self.grade("control-message-shape")
        self.assertEqual(result.outcome, verify.PASS, f"failed: {result.detail}")

    def test_hello_offers_04_to_a_05_peer_fails(self):
        content = self.content("0.4", "0.5", "0.5")
        build(self.dir, content)
        result = self.grade("control-message-shape")
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn(f"control/{CONN}: hello offers '0.4' to a peer at proto_version 0.5", result.detail)

    def test_hello_and_ack_at_08_to_a_09_peer_pass(self):
        # V1: a newer unknown peer is offered this core's 0.8.
        content = self.content("0.8", "0.8", "0.9")
        build(self.dir, content)
        result = self.grade("control-message-shape")
        self.assertEqual(result.outcome, verify.PASS, f"failed: {result.detail}")

    def test_a_07_peer_is_spoken_to_in_07_and_a_06_peer_still_in_06(self):
        for hello, peer in (("0.7", "0.7"), ("0.6", "0.6")):
            build(self.dir, self.content(hello, hello, peer))
            self.assertEqual(self.grade("control-message-shape").outcome, verify.PASS, peer)
        build(self.dir, self.content("0.6", "0.6", "0.7"))
        result = self.grade("control-message-shape")
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn(f"control/{CONN}: hello offers '0.6' to a peer at proto_version 0.7", result.detail)

    def test_a_06_peer_is_spoken_to_in_06(self):
        # This tag speaks 0.7 and 0.6, so a 0.6 peer is one it knows: offering it 0.5 (what the core offered a
        # 0.6 peer while the tag spoke 0.5) fails, naming the connection and both versions.
        content = self.content("0.5", "0.5", "0.6")
        build(self.dir, content)
        result = self.grade("control-message-shape")
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn(f"control/{CONN}: hello offers '0.5' to a peer at proto_version 0.6", result.detail)

    def test_a_04_peer_still_needs_04(self):
        # The expected version is the lower of the peer's and this tag's own: a 0.4 peer is spoken to
        # in 0.4, so a hello and hello_ack at 0.5 both fail.
        content = self.content("0.5", "0.5", "0.4")
        build(self.dir, content)
        result = self.grade("control-message-shape")
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn(f"control/{CONN}: hello offers '0.5' to a peer at proto_version 0.4", result.detail)


TELNET_CONN = "conn-6"
TELNET_SID = "sess-6"


def telnet_lines():
    """A telnet connection: the connect names term, cols and rows and carries no credential; no host key
    between connect and connected; one session; its end is single_session once the close_channel is answered."""
    return [
        core({"cmd": "connect", "target_id": "t-telnet", "protocol": "telnet", "host": "192.0.2.20", "port": 23,
              "term": "xterm-256color", "cols": 80, "rows": 24,
              "timers": {"heartbeat_interval": 10000, "heartbeat_missed": 3, "keepalive_interval": 4000,
                         "keepalive_count_max": 2, "connection_linger": 0, "orphan_timeout": 60000,
                         "idle": 900000, "unattended": 1800000},
              "buffer_bytes": 65536, "input_frame_max": 4096}),
        worker({"event": "connected", "conn_id": TELNET_CONN}),
        core({"cmd": "open_shell", "session_id": TELNET_SID, "term": "xterm-256color", "cols": 80, "rows": 24}),
        worker({"event": "channel_opened", "session_id": TELNET_SID, "session_type": "shell",
                "socket": "shell.sock", "resizable": True}),
        core({"cmd": "close_channel", "session_id": TELNET_SID}),
        worker({"event": "channel_closed", "session_id": TELNET_SID, "by": "core", "reason": "closed by the core"}),
        worker({"event": "connection_lost", "conn_id": TELNET_CONN, "session_ids": [], "cause": "single_session",
                "reason": "the connection's one session ended"}),
    ]


class TelnetConnect(unittest.TestCase):
    """control-connect-telnet: a collection from e3 must show a telnet connect, and the order rules hold
    the connection's one session."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def grade(self, name: str) -> verify.Result:
        fixtures, _ = load_fixtures(CONTRACT)
        [fx] = [f for f in fixtures if f.name == name]
        run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e3", None, self.dir, None, 1.0)
        return verify._grade(fx, run)

    def test_a_collection_with_a_telnet_connection_passes(self):
        content = files()
        content[f"control/{TELNET_CONN}.jsonl"] = jsonl(telnet_lines())
        build(self.dir, content)
        result = self.grade("control-connect-telnet")
        self.assertEqual(result.outcome, verify.PASS, f"failed: {result.detail}")

    def test_a_collection_with_no_telnet_connection_is_input_missing(self):
        build(self.dir, files())
        result = self.grade("control-connect-telnet")
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn("input missing", result.detail)

    def test_a_telnet_connect_to_a_peer_below_05_fails(self):
        # A telnet connect is legal only toward a peer at proto_version 0.5 or later (ipc-control top;
        # edge CO-PW "Versioning"). The worker's manifest names this connection's version: at 0.4 the
        # capture's telnet connect is a problem.
        content = files()
        content[f"control/{TELNET_CONN}.jsonl"] = jsonl(telnet_lines())
        doc = json.loads(content[f"manifests/workers/{WORKER}.json"])
        doc["proto_version"] = "0.4"
        doc["connections"].append({"conn_id": TELNET_CONN, "target_id": "t-telnet",
                                   "conn_dir": f"/tmp/tsb/conn-{TELNET_CONN}", "sessions": []})
        content[f"manifests/workers/{WORKER}.json"] = json.dumps(doc).encode()
        build(self.dir, content)
        result = self.grade("control-connect-telnet")
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn(f"control/{TELNET_CONN}", result.detail)
        self.assertIn("0.4", result.detail)

    def test_a_telnet_connect_to_a_05_peer_passes(self):
        content = files()
        content[f"control/{TELNET_CONN}.jsonl"] = jsonl(telnet_lines())
        doc = json.loads(content[f"manifests/workers/{WORKER}.json"])
        doc["proto_version"] = "0.5"
        doc["connections"].append({"conn_id": TELNET_CONN, "target_id": "t-telnet",
                                   "conn_dir": f"/tmp/tsb/conn-{TELNET_CONN}", "sessions": []})
        content[f"manifests/workers/{WORKER}.json"] = json.dumps(doc).encode()
        build(self.dir, content)
        self.assertEqual(self.grade("control-connect-telnet").outcome, verify.PASS)

    def test_telnet_still_needs_0_5_when_this_tags_version_moves(self):
        # The lowest worker version that may carry telnet is fixed at 0.5, where telnet was added, not
        # this tag's C3_VERSION: a core that later speaks 0.6 still sends a telnet connect to a 0.5 peer.
        content = files()
        content[f"control/{TELNET_CONN}.jsonl"] = jsonl(telnet_lines())
        doc = json.loads(content[f"manifests/workers/{WORKER}.json"])
        doc["proto_version"] = "0.5"
        doc["connections"].append({"conn_id": TELNET_CONN, "target_id": "t-telnet",
                                   "conn_dir": f"/tmp/tsb/conn-{TELNET_CONN}", "sessions": []})
        content[f"manifests/workers/{WORKER}.json"] = json.dumps(doc).encode()
        build(self.dir, content)
        with mock.patch.object(_ipc, "C3_VERSION", "0.6"):
            result = self.grade("control-connect-telnet")
        self.assertEqual(result.outcome, verify.PASS, f"failed: {result.detail}")

    def test_a_refused_reuse_beside_a_live_session_passes(self):
        # The grader's rule is the worker's rule: an error answering a later open_shell requires nothing
        # after it, so a refused reuse beside the live session passes.
        lines = telnet_lines()[:-3]
        lines.append(core({"cmd": "open_shell", "session_id": "sess-7", "term": "xterm-256color",
                           "cols": 80, "rows": 24}))
        lines.append(worker({"event": "error", "code": "not_supported", "session_id": "sess-7",
                             "message": "one session: sess-7 refused"}))
        content = files()
        content[f"control/{TELNET_CONN}.jsonl"] = jsonl(lines)
        build(self.dir, content)
        result = self.grade("control-message-shape")
        self.assertEqual(result.outcome, verify.PASS, f"failed: {result.detail}")

    def test_a_missing_single_session_after_the_one_session_fails(self):
        # After the one session's channel_closed, the connection's next event is connection_lost cause
        # single_session; a capture that ends without it fails.
        lines = telnet_lines()[:-1]
        content = files()
        content[f"control/{TELNET_CONN}.jsonl"] = jsonl(lines)
        build(self.dir, content)
        result = self.grade("control-message-shape")
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn(f"control/{TELNET_CONN}", result.detail)

    def test_a_hostkey_on_a_telnet_connection_fails(self):
        lines = telnet_lines()
        lines.insert(1, worker({"event": "hostkey", "conn_id": TELNET_CONN, "fingerprint": FP, "first_use": True}))
        content = files()
        content[f"control/{TELNET_CONN}.jsonl"] = jsonl(lines)
        build(self.dir, content)
        result = self.grade("control-message-shape")
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn("no host key (RFC 854)", result.detail)


OPEN_TIMEOUT_SID = "sess-8"


def open_timeout_lines(bound=20000, version="0.6"):
    """The main connection with one shell whose open_shell carries `bound` as open_timeout (None: no
    field), then that session's handshake in `version` and the core's disconnect."""
    open_shell = shell(OPEN_TIMEOUT_SID) if bound is None else {**shell(OPEN_TIMEOUT_SID), "open_timeout": bound}
    return [
        core(ssh_connect()),
        worker({"event": "connected", "conn_id": CONN}),
        core(open_shell),
        worker(opened(OPEN_TIMEOUT_SID)),
        core({"cmd": "hello", "session_id": OPEN_TIMEOUT_SID, "proto_version": version, "last_committed_seq": 0}),
        worker({"event": "hello_ack", "session_id": OPEN_TIMEOUT_SID, "proto_version": version,
                "first_buffered_seq": 1, "first_buffered_offset": 0, "current_seq": 0, "ended": False}),
        core({"cmd": "disconnect"}),
        worker({"event": "connection_lost", "conn_id": CONN, "session_ids": [OPEN_TIMEOUT_SID], "cause": "disconnect",
                "reason": "disconnect"}),
    ]


class OpenTimeoutGate(unittest.TestCase):
    """control-open-timeout: open_shell's open_timeout goes to every peer at proto_version 0.6 or later, and to
    no older one (V1; ipc-control open_shell; session-open step 12). The peer's version is its manifest's."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def grade(self, name="open-timeout-version-gate") -> verify.Result:
        fixtures, _ = load_fixtures(CONTRACT)
        [fx] = [f for f in fixtures if f.name == name]
        run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e4", None, self.dir, None, 1.0)
        return verify._grade(fx, run)

    def collect(self, peer_version, bound=20000):
        content = files()
        doc = json.loads(content[f"manifests/workers/{WORKER}.json"])
        if peer_version is not None:
            doc["proto_version"] = peer_version
        doc["connections"][0]["sessions"] = [{"session_id": OPEN_TIMEOUT_SID, "session_type": "shell",
                                              "socket": "shell.sock"}]
        content[f"manifests/workers/{WORKER}.json"] = json.dumps(doc).encode()
        hello = (min(peer_version, _ipc.C3_VERSION, key=lambda v: tuple(map(int, v.split("."))))
                 if peer_version is not None else _ipc.C3_VERSION)
        content[f"control/{CONN}.jsonl"] = jsonl(open_timeout_lines(bound, hello))
        build(self.dir, content)

    def test_the_bound_toward_a_06_peer_passes(self):
        self.collect("0.6")
        for name in ("open-timeout-version-gate", "open-timeout-shape"):
            result = self.grade(name)
            self.assertEqual(result.outcome, verify.PASS, f"{name} failed: {result.detail}")
        self.assertIn("1 carry open_timeout", self.grade().detail)

    def test_an_open_shell_without_the_bound_toward_a_06_peer_fails(self):
        self.collect("0.6", bound=None)
        result = self.grade()
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn(f"control/{CONN}: open_shell {OPEN_TIMEOUT_SID} to a v0.6 worker without open_timeout",
                      result.detail)

    def test_the_bound_toward_a_05_peer_fails(self):
        # A 0.5 worker does not know the field: the core sends a peer only the keys its version knows (V1).
        self.collect("0.5")
        result = self.grade()
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn(f"control/{CONN}: open_shell {OPEN_TIMEOUT_SID} to a v0.5 worker carries open_timeout",
                      result.detail)

    def test_no_bound_toward_a_05_peer_passes(self):
        self.collect("0.5", bound=None)
        result = self.grade()
        self.assertEqual(result.outcome, verify.PASS, f"failed: {result.detail}")
        self.assertIn("0 carry open_timeout", result.detail)

    def test_a_later_peer_gets_the_bound_too(self):
        # A peer beyond this tag's version is offered 0.8 and speaks it, so it is sent the field.
        self.collect("0.9", bound=None)
        self.assertEqual(self.grade().outcome, verify.FAIL)

    def test_the_gate_stays_at_06_when_this_tags_version_moves(self):
        # OPEN_TIMEOUT_MIN_VERSION is fixed where the field was added, not at C3_VERSION: a later core
        # still sends no bound to a 0.5 peer, and still sends it to a 0.6 one.
        self.collect("0.6")
        with mock.patch.object(_ipc, "C3_VERSION", "0.8"):
            self.assertEqual(self.grade().outcome, verify.PASS)

    def test_a_collection_with_no_versioned_peer_is_input_missing(self):
        # The base collection's manifest names no proto_version (a v0.1 worker): nothing is graded.
        self.collect(None)
        result = self.grade()
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn("input missing", result.detail)

    def test_the_bound_out_of_range_fails_the_shape(self):
        # 0 is no bound: control-sequence validates every captured open_shell against the schema.
        self.collect("0.6", bound=0)
        result = self.grade("control-message-shape")
        self.assertEqual(result.outcome, verify.FAIL, f"passed: {result.detail}")
        self.assertIn(f"control/{CONN}", result.detail)


if __name__ == "__main__":
    unittest.main()
