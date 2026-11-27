"""`tsbc verify --artifacts DIR`: grade what a harness collected from a running node.

C3, C5, C6 and C8 live on the broker's filesystem and its local sockets, which a stranger's
machine cannot reach over the network, so a harness copies them into DIR in the layout of
schemas/artifacts-collected.schema.json and lists them in DIR/collected.json. Each check below
grades one clause over everything collected. A required check with nothing to grade is FAIL
"input missing", never a vacuous pass.

Before any check runs, the whole collection must be clean: every value under a credential-named
key is a synthetic constant (the harness replaces real ones before it collects), no file in
the directory holds key material, and no recording holds a login answer frame (sidecar code 7). A dirty collection fails every artefact fixture, because the
directory itself is what must not travel.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import _binfmt, _ipc, selfcheck
from ._run import Run
from .schema import loads

COLLECTED = "collected.json"
COLLECTED_SCHEMA = "schemas/artifacts-collected.schema.json"
CONTROL_LINE = f"{COLLECTED_SCHEMA}#/$defs/control_line"
AUDIT_SCHEMA = "schemas/audit-event.schema.json"
_ID = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
LAYOUT = {
    "worker-manifest": re.compile(rf"^manifests/workers/({_ID})\.json$"),
    "supervisor-manifest": re.compile(rf"^manifests/supervisors/({_ID})/manifest\.json$"),
    "recording": re.compile(rf"^recordings/({_ID}(?:\.chain/continuation-[1-9][0-9]*)?)\.ttyrec$"),
    "recording-direction": re.compile(rf"^recordings/({_ID}(?:\.chain/continuation-[1-9][0-9]*)?)\.dir$"),
    "audit": re.compile(r"^(audit)\.jsonl$"),
    "frames": re.compile(rf"^frames/({_ID})\.bin$"),
    "run-frames": re.compile(rf"^frames/([0-9]{{20}}/{_ID})\.bin$"),
    "control": re.compile(rf"^control/({_ID})\.jsonl$"),
    "supervisor-control": re.compile(rf"^control/supervisors/({_ID})\.jsonl$"),
}
SEAM_OF = {"worker-manifest": "C5", "supervisor-manifest": "C5", "recording": "C8", "recording-direction": "C8",
           "audit": "C6", "frames": "C3", "run-frames": "C3", "control": "C3", "supervisor-control": "C3"}
MAX_PROBLEMS = 5
# The lowest worker proto_version that may carry a telnet connect (telnet was added at 0.5; ipc-control
# top). Fixed here, where telnet was added, not this tag's own C3_VERSION: a later core that speaks a
# higher version still sends telnet to a 0.5 peer.
TELNET_MIN_VERSION = "0.5"
# The lowest worker proto_version that may receive open_shell's open_timeout (added at 0.6; ipc-control
# open_shell). Fixed here, where the field was added, as TELNET_MIN_VERSION is.
OPEN_TIMEOUT_MIN_VERSION = "0.6"


class Missing(Exception):
    pass


@dataclass
class Collected:
    root: Path
    chains: dict[str, _binfmt.RecordingChainResult] = field(default_factory=dict)
    items: dict[str, list[tuple[str, Path]]] = field(default_factory=dict)  # kind -> [(id, path)]
    orphans: list[str] = field(default_factory=list)  # continuation pairs present with no link to them

    def need(self, kind: str) -> list[tuple[str, Path]]:
        found = self.items.get(kind, [])
        if not found:
            raise Missing(f"input missing: collected.json lists no {kind}")
        return found


def _jsonl(path: Path) -> list[tuple[int, Any, str | None]]:
    """(line number, document, error) per non-empty line. A file that is not UTF-8 is one error
    row, never an exception: a malformed artefact fails its fixture, it does not stop the run."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [(0, None, f"{path.name} is not UTF-8 text ({exc.reason} at byte {exc.start})")]
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append((n, loads(line), None))
        except ValueError as exc:
            rows.append((n, None, f"line {n} is not JSON: {getattr(exc, 'msg', exc)}"))
    return rows


def _walk(root: Path) -> tuple[list[Path], list[str]]:
    """(every regular file under root, every symbolic link under root). Links are never followed:
    a collection is the files in it, and a link could make the runner grade, or scan, a file
    outside the directory instead of the evidence collected.json names."""
    files, links = [], []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in sorted(dirnames + filenames):
            path = Path(dirpath) / name
            if path.is_symlink():
                links.append(path.relative_to(root).as_posix())
            elif name in filenames:
                files.append(path)
    return sorted(files), sorted(links)


def _credential_findings(root: Path, files: list[Path]) -> list[str]:
    """Credential-named values that are not synthetic, in every JSON and JSONL file."""
    findings = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            if path.suffix == ".json":
                docs = [(None, loads(path.read_text(encoding="utf-8")))]
            elif path.suffix == ".jsonl":
                docs = [(n, row) for n, row, err in _jsonl(path) if err is None]
            else:
                continue
        except (ValueError, UnicodeDecodeError):
            continue  # the check that reads the file reports it
        for n, doc in docs:
            where = f"{rel}" + (f" line {n}" if n else "")
            findings += [f"{where}: {p}" for p in selfcheck.credential_problems(doc, placeholders=False)]
    return findings


def _login_answer_findings(root: Path, files: list[Path]) -> list[str]:
    """Every recording sidecar that holds a code-7 login answer frame, named by file. A collected recording
    must not hold one: a collector leaves such a recording out (artifacts-collected.schema.json). The sidecar
    is one code byte per frame, so a 7 in it is a login answer frame whether or not the .ttyrec beside it
    decodes. A code-6 frame (the prompt) is not refused: it carries the device's text, never an answer. A code-8 frame
    (TSB's own prompt line, two fixed texts) is not refused either: it carries no secret."""
    findings = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        if LAYOUT["recording-direction"].match(rel) and _binfmt.LOGIN_ANSWER in path.read_bytes():
            findings.append(f"{rel} holds a login answer frame (sidecar code {_binfmt.LOGIN_ANSWER}): "
                            "a collected recording must not; leave the recording out")
    return findings


def load_collected(run: Run) -> Collected | str:
    root = run.artifacts
    every_file, links = _walk(root)
    if links:
        return (f"{', '.join(links[:MAX_PROBLEMS])}: symbolic links; a collection holds regular files only, never "
                "a link that could point outside it")
    path = root / COLLECTED
    if not path.is_file():
        return f"input missing: {path.name} not found in the artefacts directory"
    try:
        doc = loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:  # JSONDecodeError, UnicodeDecodeError, a non-finite number
        return f"{COLLECTED} is not JSON: {exc}"
    errors = run.registry.validate(doc, COLLECTED_SCHEMA)
    if errors:
        return f"{COLLECTED} breaks its schema: {errors[0]}"
    col = Collected(root)
    problems = []
    for item in doc["items"]:
        match = LAYOUT[item["kind"]].match(item["path"])
        if not match:
            problems.append(f"{item['path']} is not where a {item['kind']} belongs")
        elif SEAM_OF[item["kind"]] != item["seam"]:
            problems.append(f"{item['path']}: a {item['kind']} is {SEAM_OF[item['kind']]}, not {item['seam']}")
        elif not (root / item["path"]).is_file():
            problems.append(f"{item['path']} is listed but missing")
        else:
            col.items.setdefault(item["kind"], []).append((match.group(1), root / item["path"]))
    if problems:
        return f"{COLLECTED}: " + "; ".join(problems[:MAX_PROBLEMS])
    dirty = (_credential_findings(root, every_file) + selfcheck.scan_files(root, every_file)
             + _login_answer_findings(root, every_file))
    if dirty:
        more = f" (+{len(dirty) - MAX_PROBLEMS} more)" if len(dirty) > MAX_PROBLEMS else ""
        return ("the collection carries credential material and must not be graded or shared: "
                + "; ".join(dirty[:MAX_PROBLEMS]) + more)
    referenced = set()
    chain_specs = doc.get("recording_chains", [])
    audit_events = []
    if chain_specs:
        audit_files = col.items.get("audit", [])
        if len(audit_files) != 1:
            return "recording chains require exactly one collected audit log"
        audit_path = audit_files[0][1]
        try:
            raw_audit = audit_path.read_bytes()
        except OSError:
            return "recording chains require a readable audit log"
        if raw_audit and not raw_audit.endswith(b"\n"):
            return "recording chains require a complete audit log"
        rows = _jsonl(audit_path)
        if any(error is not None or not isinstance(event, dict) for _, event, error in rows):
            return "recording chains require a parseable complete audit log"
        audit_events = [event for _, event, _ in rows]
    for spec in chain_specs:
        state = spec["session_record"]
        sid = state["session"]["id"]
        next_generation = state.get("recording_next_generation", 1)
        if sid in col.chains:
            return f"recording chain {sid}: duplicate session"
        if not state.get("recording_path"):
            return f"recording chain {sid}: missing original pair reference"
        pairs = []
        prior_generation = -1
        for idx, pair in enumerate(spec["pairs"]):
            tty = pair["ttyrec"]
            side = pair["sidecar"]
            if side != tty.removesuffix(".ttyrec") + ".dir":
                return f"recording chain {sid}: invalid pair order or path"
            if tty == f"recordings/{sid}.ttyrec":
                generation = 0
            else:
                match = re.fullmatch(rf"recordings/{re.escape(sid)}\.chain/continuation-(\d+)\.ttyrec", tty)
                if not match:
                    return f"recording chain {sid}: pair not at its chain path"
                generation = int(match.group(1))
            if (idx == 0 and generation != 0) or not prior_generation < generation < next_generation:
                return f"recording chain {sid}: generations not ordered below next generation"
            prior_generation = generation
            if not any(path == root / tty for _, path in col.items.get("recording", [])) or not any(
                    path == root / side for _, path in col.items.get("recording-direction", [])):
                return f"recording chain {sid}: incomplete pair collection"
            referenced.update((tty, side))
            opened = _binfmt.open_recording_pair(root / tty, state["recording_path"] if generation == 0 else tty)
            if not opened.ok or opened.value.sidecar is None:
                return f"recording chain {sid}: unavailable pair"
            snapshot = opened.value
            pairs.append(_binfmt.RecordingPair(snapshot.path, snapshot.ttyrec, snapshot.sidecar,
                                                state.get("recording_committed_offset") if generation == 0 else None))
        chain = _binfmt.RecordingChain(sid, tuple(pairs), tuple(spec["audit"]), True)
        result = _binfmt._chain_walk(chain, [run.registry])
        if not result.ok:
            return f"recording chain {sid}: {result.error}"
        actual = [event for event in audit_events if event.get("action") == "recording.output_incomplete"
                  and event.get("session_id") == sid]
        canonical = lambda event: json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if sorted(map(canonical, actual)) != sorted(map(canonical, spec["audit"])):
            return f"recording chain {sid}: audit facts differ from collected audit log"
        if state.get("recording_held") is True and (
                not result.evidence or result.evidence[-1].path != pairs[-1].path):
            return f"recording chain {sid}: held pair lacks a quarantine notice"
        col.chains[sid] = result
    for path in every_file:
        rel = path.relative_to(root).as_posix()
        if (LAYOUT["recording"].match(rel) or LAYOUT["recording-direction"].match(rel)) and (
                ".chain/continuation-" in rel and rel not in referenced):
            if not chain_specs:
                return f"{rel}: continuation pair lacks complete chain metadata"
            if rel.endswith(".ttyrec") and rel not in col.orphans:
                col.orphans.append(rel)  # a pair present with no link to it: reported, never used, never adopted
    for _, audit_path in col.items.get("audit", []):
        for _, event, error in _jsonl(audit_path):
            if error is not None or not isinstance(event, dict) or event.get("action") != "recording.output_incomplete":
                continue
            detail = event.get("detail")
            if not isinstance(detail, dict):
                continue
            kind = detail.get("kind")
            if kind not in ("continuation", "continuation_unconfirmed") and not (
                    kind == "unconfirmed" and detail.get("reason") == "recording_unsynced"):
                continue
            sid = event.get("session_id")
            if sid not in col.chains:
                if kind in ("continuation", "continuation_unconfirmed") or any(
                        ident == sid for ident, _ in col.items.get("recording", [])):
                    return "audit contains quarantine or continuation facts without complete chain metadata"
                continue
            if not any(other.get("detail") == detail and other.get("recording_path") == event.get("recording_path")
                       and other.get("session_type") == event.get("session_type") for other in col.chains[sid].audit):
                return f"recording chain {sid}: audit fact omitted or conflicting"
    return col


def _result(problems: list[str], summary: str) -> tuple[bool, str]:
    if problems:
        more = f" (+{len(problems) - MAX_PROBLEMS} more)" if len(problems) > MAX_PROBLEMS else ""
        return False, "; ".join(problems[:MAX_PROBLEMS]) + more
    return True, summary


Check = Callable[[Run, Collected], tuple[bool, str]]
CHECKS: dict[str, Check] = {}


def check(name: str):
    def register(fn: Check) -> Check:
        CHECKS[name] = fn
        return fn
    return register


def grade(name: str, run: Run) -> tuple[bool, str]:
    cache = run.state.setdefault("artifacts", {})
    if "collected" not in cache:
        cache["collected"] = load_collected(run)
    col = cache["collected"]
    if isinstance(col, str):
        return False, col
    if col.chains and (name.startswith("recordings-") or name in ("audit-session-connect", "audit-orphan-end")):
        return False, "legacy artefact grader refuses a chain collection"
    if name not in CHECKS:
        return False, f"no artefact check named {name!r}"
    if name not in cache:
        try:
            cache[name] = CHECKS[name](run, col)
        except Missing as exc:
            cache[name] = (False, str(exc))
        except (OSError, ValueError) as exc:  # an unreadable or undecodable artefact fails, never crashes
            cache[name] = (False, f"a collected file cannot be read: {exc}")
    return cache[name]


# ---- C5 -----------------------------------------------------------------------------------

def _manifests(run: Run, col: Collected, kind: str, schema: str, id_key: str) -> tuple[bool, str]:
    problems = []
    items = col.need(kind)
    for ident, path in items:
        try:
            doc = loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:  # JSONDecodeError, UnicodeDecodeError, a non-finite number
            problems.append(f"{path.name}: not JSON: {exc}")
            continue
        problems += [f"{ident}: {e}" for e in run.registry.validate(doc, schema)[:2]]
        if isinstance(doc, dict) and doc.get(id_key) != ident:
            problems.append(f"{ident}: {id_key} {doc.get(id_key)!r} does not match its file location")
    return _result(problems, f"{len(items)} {kind}s valid")


@check("worker-manifests")
def worker_manifests(run: Run, col: Collected) -> tuple[bool, str]:
    return _manifests(run, col, "worker-manifest", "schemas/worker-manifest.schema.json", "worker_id")


@check("supervisor-manifests")
def supervisor_manifests(run: Run, col: Collected) -> tuple[bool, str]:
    return _manifests(run, col, "supervisor-manifest", "schemas/supervisor-manifest.schema.json", "supervisor_id")


# ---- C3 data frames ------------------------------------------------------------------------

Taps = dict[str, list[tuple[str | None, list[_binfmt.DataFrame]]]]


def _taps(col: Collected) -> tuple[Taps, list[str]]:
    """session_id → its taps in run order, (run, frames); a collection with no run boundary holds one
    tap per session, run None, graded exactly as at v0.1. A tap torn at its end is a killed core's
    last write when a later run follows it, and is cut as the recording's reopen rule cuts a torn
    tail; anywhere else a torn tap fails, as at v0.1."""
    plain, per_run = col.items.get("frames", []), col.items.get("run-frames", [])
    if not plain and not per_run:
        raise Missing("input missing: collected.json lists no frames")
    taps: Taps = {}
    problems: list[str] = []
    for ident, path in plain:
        try:
            taps[ident] = [(None, _binfmt.load_frames(path))]
        except _binfmt.FormatError as exc:
            problems.append(f"frames/{ident}: {exc}")
            taps[ident] = []
    runs = sorted({ident.split("/", 1)[0] for ident, _ in per_run})
    by_session: dict[str, list[tuple[str, Path]]] = {}
    for ident, path in per_run:
        core_run, sid = ident.split("/", 1)
        by_session.setdefault(sid, []).append((core_run, path))
    for sid, items in sorted(by_session.items()):
        if sid in taps:
            problems.append(f"frames/{sid}: tapped both with and without a run boundary")
            continue
        taps[sid] = []
        for core_run, path in sorted(items):
            data = path.read_bytes()
            try:
                frames = _binfmt.decode_frames(data)
            except _binfmt.FormatError as exc:
                if core_run == runs[-1]:
                    problems.append(f"frames/{core_run}/{sid}: {exc}")
                    continue
                frames = _binfmt.decode_frames(_binfmt.cut_torn_frames(data))
            taps[sid].append((core_run, frames))
    return taps, problems


def _frames(col: Collected) -> tuple[dict[str, list[_binfmt.DataFrame]], list[str]]:
    """Every tap by its collected name — frames/<session_id> or frames/<run>/<session_id> — for the
    checks that hold each frame to its own rules."""
    taps, problems = _taps(col)
    return {(f"{r}/{sid}" if r else sid): frames for sid, runs in taps.items() for r, frames in runs}, problems


def _session_types(run: Run, col: Collected) -> dict[str, str]:
    """session_id → its session_type, from the channel_opened events the captures hold. A session
    no capture opened is read as a PTY session: the stricter rule."""
    try:
        captures, _ = _conn_captures(run, col)
    except Missing:
        return {}
    return {m.get("session_id"): m.get("session_type") for c in captures.values() for s, m, _ in c
            if s == "worker" and _ipc.kind(m) == "channel_opened"}


def _hello_commits(run: Run, col: Collected) -> dict[tuple[str | None, str], int]:
    """(run, session_id) → the last_committed_seq of that run's first hello for the session: where
    the replay of a restarted core starts, and so where an earlier run's tap stops counting."""
    try:
        captures, _ = _conn_captures(run, col)
    except Missing:
        return {}
    commits: dict[tuple[str | None, str], int] = {}
    for messages in captures.values():
        for (sender, msg, _), core_run in zip(messages, messages.runs):
            if sender == "core" and _ipc.kind(msg) == "hello":
                commits.setdefault((core_run, msg.get("session_id")), msg.get("last_committed_seq"))
    return commits


def _merged(run: Run, col: Collected) -> tuple[dict[str, list[_binfmt.DataFrame]], list[str], int]:
    """session_id → its frames as its recording must hold them, each seq once (merge_runs); and the
    number of sessions tapped across a run boundary."""
    taps, problems = _taps(col)
    commits = _hello_commits(run, col) if col.items.get("run-frames") else {}
    merged, crossed = {}, 0
    for sid, runs in taps.items():
        if len(runs) == 1:
            merged[sid] = runs[0][1]
            continue
        crossed += len([r for r, frames in runs if any(f.direction == "out" for f in frames)]) >= 2
        mine = {r: commits[(r, sid)] for r, _ in runs if (r, sid) in commits}
        merged[sid], more = _binfmt.merge_runs(runs, mine)
        problems += [f"frames/{sid}: {p}" for p in more]
    return merged, problems, crossed


@check("frames-envelope")
def frames_envelope(run: Run, col: Collected) -> tuple[bool, str]:
    decoded, problems = _frames(col)
    for ident, frames in decoded.items():
        for i, f in enumerate(frames):
            problems += [f"frames/{ident} frame {i}: {p}" for p in _binfmt.frame_problems(f)]
    total = sum(len(f) for f in decoded.values())
    if not problems and total == 0:
        problems.append("input missing: the frame captures hold no frames")
    return _result(problems, f"{len(decoded)} captures, {total} frames, every envelope v1")


@check("frames-seq")
def frames_seq(run: Run, col: Collected) -> tuple[bool, str]:
    merged, problems, _ = _merged(run, col)
    for ident, frames in merged.items():
        problems += [f"frames/{ident}: {p}" for p in _binfmt.seq_problems(frames, contiguous=False)]
    # Order is only proved by two frames of one direction in one capture; one frame proves nothing.
    ordered = {d for frames in merged.values() for d in ("out", "in")
               if sum(f.direction == d for f in frames) >= 2}
    for d in ("out", "in"):
        if not problems and d not in ordered:
            problems.append(f"input missing: no capture holds two or more {d} frames, so their order is unproven")
    return _result(problems, f"{len(merged)} captures, seq strictly increasing per session and direction")


@check("frames-continuity")
def frames_continuity(run: Run, col: Collected) -> tuple[bool, str]:
    """Output across a core restart and a lone re-dial: each seq once, no jump that is not a gap
    frame, the frame after a gap its seq + 1, and a replayed frame the same bytes and stamp as the
    earlier run tapped (immortality §4.1, §4.3: gap closed, or declared — never spliced silently).
    Only a PTY session has a ring (Contract §15.1), so no other session carries a gap frame. An sftp
    session's in-flight output is its transfer layer's to resume by offset, so its seqs may jump; exec,
    netconf and forward have no loss rule at this tag, so for them a jump is a silent splice, as for a
    shell."""
    merged, problems, crossed = _merged(run, col)
    types = _session_types(run, col)
    gaps = 0
    for sid, frames in merged.items():
        out = [f for f in frames if f.direction == "out"]
        kind = types.get(sid, "shell")
        if kind != "shell":
            problems += [f"frames/{sid}: a gap frame on a {kind} session, which has no ring to lose from"
                         for f in out if f.gap]
        if kind == "sftp":
            continue
        gaps += sum(f.gap for f in out)
        problems += [f"frames/{sid}: {p}" for p in _binfmt.seq_problems(out, contiguous=True, from_one=False)]
    if not problems and not crossed:
        problems.append("input missing: no session's output was tapped across a core restart (run-bounded captures)")
    if not problems and not gaps:
        problems.append("input missing: no gap frame was tapped (force a ring overflow while the core is away)")
    return _result(problems, f"{len(merged)} sessions, {crossed} across a restart, {gaps} gap frames: every seq "
                             "once, every jump declared")


@check("frames-monotonic")
def frames_monotonic(run: Run, col: Collected) -> tuple[bool, str]:
    """Each direction's stamps never decrease, across a restart included: the worker keeps its output
    stamps non-decreasing at the source and the core never alters one; the core keeps its own input
    stamps non-decreasing (immortality §4.2; decision 5.22). Across directions nothing is assumed."""
    merged, problems, _ = _merged(run, col)
    for sid, frames in merged.items():
        problems += [f"frames/{sid}: {p}" for p in _binfmt.stamp_problems(frames)]
    counted = {d for frames in merged.values() for d in ("out", "in") if sum(f.direction == d for f in frames) >= 2}
    for d in ("out", "in"):
        if not problems and d not in counted:
            problems.append(f"input missing: no capture holds two or more {d} frames, so their stamps' order is unproven")
    return _result(problems, f"{len(merged)} sessions, each direction's stamps non-decreasing")


# ---- C8 recordings -------------------------------------------------------------------------

def _recordings(col: Collected) -> tuple[dict[str, list[_binfmt.RecFrame]], list[str]]:
    sidecars = dict(col.items.get("recording-direction", []))
    decoded, problems = {}, []
    for ident, path in col.need("recording"):
        if ident not in sidecars:
            problems.append(f"recordings/{ident}.dir, the sidecar of recordings/{ident}.ttyrec, "
                            "is not collected")
            continue
        try:
            decoded[ident] = _binfmt.load_recording(path, sidecars[ident])
        except _binfmt.FormatError as exc:
            problems.append(f"recordings/{ident}: {exc}")
    return decoded, problems


@check("recording-chains-complete")
def recording_chains_complete(run: Run, col: Collected) -> tuple[bool, str]:
    if not col.chains:
        return False, "input missing: no recording chain"
    summary = f"{len(col.chains)} complete recording chains validated"
    if col.orphans:
        more = f" (+{len(col.orphans) - MAX_PROBLEMS} more)" if len(col.orphans) > MAX_PROBLEMS else ""
        summary += f"; {len(col.orphans)} orphan pair(s) present with no link, not used: " + \
                   ", ".join(col.orphans[:MAX_PROBLEMS]) + more
    return True, summary


@check("recordings-parse")
def recordings_parse(run: Run, col: Collected) -> tuple[bool, str]:
    decoded, problems = _recordings(col)
    types = _session_types(run, col)
    for ident, frames in decoded.items():
        problems += [f"recordings/{ident}: {p}" for p in _binfmt.recording_problems(frames)]
        if types.get(ident, "shell") != "shell" and any(f.code == _binfmt.TERMINAL_INCOMPLETE for f in frames):
            problems.append(f"recordings/{ident}: code-9 terminal incompleteness marker is PTY-only (§1)")
    if not problems and not any(decoded.values()):
        problems.append("input missing: the collected recordings hold no frames")
    return _result(problems, f"{len(decoded)} recordings and their sidecars parse to the last byte, "
                             "at most one end marker each, last")


@check("recordings-both-directions")
def recordings_both_directions(run: Run, col: Collected) -> tuple[bool, str]:
    decoded, problems = _recordings(col)
    both = [i for i, frames in decoded.items() if {"out", "in"} <= {f.direction for f in frames}]
    if not problems and not both:
        problems.append("no recording holds both directions: one recording per session carries input and output")
    return _result(problems, f"{len(both)} of {len(decoded)} recordings hold both directions")


def _expected(frames: list[_binfmt.DataFrame]) -> list[_binfmt.RecFrame]:
    """The recording frames the tap derives (ttyrec.schema.json x-derivation). A gap frame derives
    the gap marker (code 2) at its stamp; what the marker's line says is recordings-gap's."""
    return [_binfmt.RecFrame(*_binfmt._sec_usec(f.timestamp), _binfmt.GAP, b"") if f.gap else _binfmt.to_recording(f)
            for f in frames]


def _same(recorded: _binfmt.RecFrame, expected: _binfmt.RecFrame) -> bool:
    if expected.code == _binfmt.GAP:
        return recorded.code == _binfmt.GAP and (recorded.sec, recorded.usec) == (expected.sec, expected.usec)
    return recorded == expected


def _runs_in_order(recorded: list, expected: list) -> bool:
    start = next((i for i, f in enumerate(recorded) if _same(f, expected[0])), None)
    return (start is not None and len(recorded) - start >= len(expected)
            and all(_same(r, e) for r, e in zip(recorded[start:], expected)))


def _input_in_order(recorded: list, runs: list[list], redials: list[int] | None = None) -> bool:
    """The recording's input frames against each run's tapped input, in run order. The core records
    an input frame before it sends it (ttyrec.schema.json x-sidecar reopen), so a lost data path
    between the two leaves a recorded frame no tap holds. A kill leaves one at most as the last
    input of a run a later run follows — one per run boundary; a lone data-socket loss leaves one
    inside its run — one for each of that run's re-dials (`redials`, aligned with `runs`). Otherwise
    the tapped frames are recorded contiguously, and with no run boundary and no re-dial this is
    v0.1's strict rule."""
    redials = redials or [0] * len(runs)
    first = next((k for k, expected in enumerate(runs) if expected), None)
    if first is None:
        return True
    pos = next((i for i, f in enumerate(recorded) if _same(f, runs[first][0])), None)
    if pos is None:
        return False
    boundary = 0
    for k in range(first, len(runs)):
        boundary += k > first
        spare = redials[k]
        for j, expected in enumerate(runs[k]):
            while pos < len(recorded) and not _same(recorded[pos], expected):
                if j == 0 and boundary:
                    boundary -= 1
                elif spare:
                    spare -= 1
                else:
                    return False
                pos += 1
            if pos == len(recorded):
                return False
            pos += 1
        if runs[k]:
            boundary = 0
    return True


def _redials(run: Run, col: Collected) -> dict[tuple[str | None, str], int]:
    """(run, session_id) → that run's lone re-dials of the session: every hello after the run's first,
    which is the session's new-session or reattach handshake."""
    try:
        captures, _ = _conn_captures(run, col)
    except Missing:
        return {}
    hellos: dict[tuple[str | None, str], int] = {}
    for messages in captures.values():
        for (sender, msg, _), core_run in zip(messages, messages.runs):
            if sender == "core" and _ipc.kind(msg) == "hello":
                key = (core_run, msg.get("session_id"))
                hellos[key] = hellos.get(key, 0) + 1
    return {key: count - 1 for key, count in hellos.items()}


def _match_frames(run: Run, col: Collected, direction: str) -> tuple[bool, str]:
    recordings, problems = _recordings(col)
    captures, more, _ = _merged(run, col)
    problems += more
    taps = _taps(col)[0] if direction == "in" else {}
    redials = _redials(run, col) if direction == "in" else {}
    pairs = sorted(set(recordings) & set(captures))
    if not pairs:
        raise Missing("input missing: no session has both a recording and a frames capture")
    compared = 0
    codes = (_binfmt.OUT, _binfmt.GAP) if direction == "out" else (_binfmt.IN,)
    for ident in pairs:
        tapped = [f for f in captures[ident] if f.direction == direction]
        if not tapped:
            continue
        try:
            expected = _expected(tapped)
        except _binfmt.FormatError as exc:
            problems.append(f"{ident}: {exc}")
            continue
        frames = recordings[ident]
        recorded = [f for f in frames if f.code in codes]
        # A gap marker just before the end marker may come from the spool, with no gap frame in
        # any tap (spool-entry.schema.json): the loss happened after the last replay.
        spooled = (len(frames) >= 2 and frames[-1].end and frames[_gap_before_end(frames)].code == _binfmt.GAP
                   and not (tapped and tapped[-1].gap))
        # The trailing gap marker is in `recorded` for the out direction (codes include GAP); for
        # in it never is, so trimming blindly would drop the last real input frame (#57). Trim only
        # when the last recorded item is actually that gap marker.
        if spooled and recorded and recorded[-1].code == _binfmt.GAP:
            recorded = recorded[:-1]
        if direction == "in":
            per_run = [_expected([f for f in frames_ if f.direction == "in"]) for _, frames_ in taps.get(ident, [])]
            matched = _input_in_order(recorded, per_run,
                                      [redials.get((core_run, ident), 0) for core_run, _ in taps.get(ident, [])])
        else:
            matched = _runs_in_order(recorded, expected)
        if not matched:
            problems.append(f"{ident}: the recording's {direction} frames are not the captured frames with "
                            "their envelope timestamps derived to sec/usec (worker-owned for output, the "
                            "core's send stamp for input), in order")
        compared += len(expected)
    if not problems and compared == 0:
        problems.append(f"input missing: no capture holds {direction} frames")
    return _result(problems, f"{compared} {direction} frames match their recordings exactly")


@check("recordings-match-frames-out")
def recordings_match_frames_out(run: Run, col: Collected) -> tuple[bool, str]:
    return _match_frames(run, col, "out")


@check("recordings-match-frames-in")
def recordings_match_frames_in(run: Run, col: Collected) -> tuple[bool, str]:
    return _match_frames(run, col, "in")


def _gap_acks(run: Run, col: Collected) -> dict[tuple[str, int], dict]:
    """(session_id, first_buffered_seq) → the first hello_ack that reported it: the handshake that
    found the ring there first, and so declared the gap whose marker is computed from its numbers. A
    later handshake that finds the same ring declares nothing and does not replace it."""
    try:
        captures, _ = _conn_captures(run, col)
    except Missing:
        return {}
    acks: dict[tuple[str, int], dict] = {}
    for capture in captures.values():
        for sender, msg, _ in capture:
            if sender == "worker" and _ipc.kind(msg) == "hello_ack":
                acks.setdefault((msg.get("session_id"), msg.get("first_buffered_seq")), msg)
    return acks


def _creation_bounds(run: Run, col: Collected) -> dict[str, tuple[int | None, int | None]]:
    """session_id → (no earlier than, no later than) its created_at, in nanoseconds. No collected file
    holds created_at itself (it lives in the C1 record and C6), so these are the bounds a collection
    gives: a session the core opened in a run a control capture shows was created after that run
    began, a run id being the core's start; and the core persists a session's record, created_at in
    it, before it writes the session's session.connect, so that event's ts bounds it from above
    (audit-event.schema.json)."""
    low: dict[str, int] = {}
    try:
        captures, _ = _conn_captures(run, col)
    except Missing:
        captures = {}
    for messages in captures.values():
        for (sender, msg, _), core_run in zip(messages, messages.runs):
            if sender == "core" and _ipc.kind(msg) in _ipc.OPENS and core_run is not None:
                low.setdefault(msg.get("session_id"), int(core_run))
    high: dict[str, int] = {}
    if col.items.get("audit"):
        for e in _audit(run, col)[0]:
            if e["action"] == "session.connect":
                high.setdefault(e["session_id"], _binfmt.rfc3339_ns(e["ts"]))
    return {sid: (low.get(sid), high.get(sid)) for sid in set(low) | set(high)}


def _gap_before_end(frames: list[_binfmt.RecFrame]) -> int:
    """Return the index immediately before the end, skipping a trailing code-9 marker."""
    end = len(frames) - 1
    if end >= 0 and frames[end].end:
        end -= 1
    if end >= 0 and frames[end].code == _binfmt.TERMINAL_INCOMPLETE:
        end -= 1
    return end


@check("recordings-gap")
def recordings_gap(run: Run, col: Collected) -> tuple[bool, str]:
    """Every gap frame a tap holds has its gap marker in the recording, with the handshake's numbers
    — lost bytes are first_buffered_offset minus the output bytes the recording held before it, over
    the span from the stamp of its last output frame or gap marker to the gap frame's, which is its
    hello_ack's last_dropped_ts — and every gap marker is declared,
    by a gap frame or, just before the end marker, by the spool (immortality §4.3: an explicit gap
    marker, wall-clock span and byte count lost; never splice silently). Gap frames are matched to
    markers in order, so two whose stamps fall in one microsecond are two losses. A session's first
    marker, with no output before it, starts at the session's created_at, which no collected file
    holds: its start is held to the bounds the collection gives (_creation_bounds). A gap frame an
    earlier run tapped that a later run's handshake superseded (the core died before that marker's
    commit, and the later run asked from the commit point before it) may still be what a marker in
    the recording declares: the recording is never cut, so the marker stays and the later run's marker
    covers only what was lost since (ttyrec.schema.json x-sidecar reopen). Such a frame matches a
    marker if one is there, and is not owed one."""
    recordings, problems = _recordings(col)
    merged, more, _ = _merged(run, col)
    problems += more
    taps, _ = _taps(col)
    acks = _gap_acks(run, col)
    bounds = _creation_bounds(run, col)
    checked = bounded = 0
    for sid, tapped in sorted(merged.items()):
        if any(f.gap for f in tapped) and sid not in recordings:
            problems.append(f"recordings/{sid}: its tap holds a gap frame, and its recording was not collected")
    for sid, frames in sorted(recordings.items()):
        gaps = [f for f in merged.get(sid, []) if f.gap]
        kept = {(g.seq, g.timestamp) for g in gaps}
        superseded = [f for _r, tap in taps.get(sid, []) for f in tap if f.gap and (f.seq, f.timestamp) not in kept]
        held, last_out = 0, None
        for i, f in enumerate(frames):
            if f.code == _binfmt.OUT:
                held, last_out = held + len(f.data), (f.sec, f.usec)
                continue
            parsed = _binfmt.parse_gap_line(f.data) if f.code == _binfmt.GAP else None
            if parsed is None:
                continue  # not a gap marker, or not a valid one: recordings-parse says which
            lost, start, _end = parsed
            at = next((k for k, g in enumerate(gaps) if _binfmt._sec_usec(g.timestamp) == (f.sec, f.usec)), None)
            gap = gaps.pop(at) if at is not None else None
            if gap is None:
                at = next((k for k, g in enumerate(superseded) if _binfmt._sec_usec(g.timestamp) == (f.sec, f.usec)), None)
                gap = superseded.pop(at) if at is not None else None
            spooled = i == _gap_before_end(frames) and frames[-1].end is not None
            if gap is not None:
                ack = acks.get((sid, gap.seq + 1))
                if ack is None:
                    problems.append(f"recordings/{sid}: gap frame {gap.seq} has no hello_ack with first_buffered_seq "
                                    f"{gap.seq + 1} in the control captures")
                elif ack.get("last_dropped_ts") != gap.timestamp:
                    problems.append(f"recordings/{sid}: gap frame {gap.seq} is stamped {gap.timestamp}, but its hello_ack's "
                                    f"last_dropped_ts is {ack.get('last_dropped_ts')}: the span's end is the last frame "
                                    "the ring dropped")
                elif lost != ack.get("first_buffered_offset", 0) - held:
                    problems.append(f"recordings/{sid}: the gap marker says {lost} bytes lost, the handshake "
                                    f"{ack.get('first_buffered_offset', 0) - held} (first_buffered_offset "
                                    f"{ack.get('first_buffered_offset')} less the {held} bytes the recording held)")
                checked += 1
            elif not spooled:
                problems.append(f"recordings/{sid}: a gap marker at {_binfmt.usec_stamp(f.sec, f.usec)} that no gap "
                                "frame declared")
            if last_out is not None and start != last_out:
                problems.append(f"recordings/{sid}: the gap marker's span starts {_binfmt.usec_stamp(*start)}, the last "
                                f"output frame or gap marker before it is stamped {_binfmt.usec_stamp(*last_out)}")
            elif last_out is None:
                earliest, latest = bounds.get(sid, (None, None))
                if earliest is not None and start < _binfmt._sec_usec(earliest):
                    problems.append(f"recordings/{sid}: the first gap marker's span starts {_binfmt.usec_stamp(*start)}, "
                                    "before the core run that opened the session began "
                                    f"({_binfmt.usec_stamp(*_binfmt._sec_usec(earliest))}): it starts at created_at")
                if latest is not None and start > _binfmt._sec_usec(latest):
                    problems.append(f"recordings/{sid}: the first gap marker's span starts {_binfmt.usec_stamp(*start)}, "
                                    "after the session's session.connect "
                                    f"({_binfmt.usec_stamp(*_binfmt._sec_usec(latest))}): it starts at created_at")
                bounded += earliest is not None or latest is not None
            held, last_out = held + lost, (f.sec, f.usec)  # a marker is a commit point: the next span starts here
        problems += [f"recordings/{sid}: gap frame {g.seq} has no gap marker in the recording: the loss is spliced "
                     "silently" for g in gaps]
    if not problems and not checked:
        problems.append("input missing: no gap frame with its session's recording was collected (force a ring "
                        "overflow while the core is away)")
    return _result(problems, f"{checked} gap frames, each with its gap marker and the handshake's numbers; "
                             f"{bounded} first spans held to their session's creation bounds")


@check("recordings-broker-marker")
def recordings_broker_marker(run: Run, col: Collected) -> tuple[bool, str]:
    """The broker marker (sidecar code 5): when a stale-grid choice makes the core act on the target,
    the recording marks it broker-originated (5.46). For ctrl_l the marker stands before the code-1
    input frame carrying 0x0C — the core records an input frame before it sends it — and the choice is
    on the session.stale_choice audit event. A recorded broker send with neither the input frame nor
    the audit event hides who chose and what reached the target."""
    recordings, problems = _recordings(col)
    events, more = _audit(run, col)
    problems += more
    choices = {e.get("client_id"): e for e in events if e.get("action") == "session.stale_choice"}
    marked = 0
    for sid, frames in sorted(recordings.items()):
        for i, f in enumerate(frames):
            if f.code != _binfmt.BROKER:
                continue
            marked += 1
            choice = _binfmt.parse_broker_line(f.data)
            if choice is None:
                problems.append(f"recordings/{sid}: a broker marker's data is not the x-broker-line line")
                continue
            if choice == "ctrl_l":
                nxt = frames[i + 1] if i + 1 < len(frames) else None
                if nxt is None or nxt.code != _binfmt.IN or nxt.data != b"\x0c":
                    problems.append(f"recordings/{sid}: a ctrl_l broker marker with no 0x0C input frame after it: "
                                    "the core records an input frame before it sends it")
            chosen = [e for e in choices.values() if e.get("session_id") == sid and e.get("choice") == choice]
            if not chosen:
                problems.append(f"recordings/{sid}: a broker marker for choice {choice!r} with no "
                                "session.stale_choice audit event for it: who chose lives in the audit")
    if not problems and not marked:
        problems.append("input missing: no recording holds a broker marker (force a gap and answer ctrl_l while "
                        "collecting)")
    return _result(problems, f"{marked} broker markers, each with its choice on the audit and, for ctrl_l, its "
                             "0x0C input frame after it")


@check("recordings-monotonic")
def recordings_monotonic(run: Run, col: Collected) -> tuple[bool, str]:
    """In every recording each direction's stamps never decrease, across a core restart included
    (ttyrec.schema.json x-compare: the worker's output stamps, the core's input stamps)."""
    recordings, problems = _recordings(col)
    for sid, frames in sorted(recordings.items()):
        problems += [f"recordings/{sid}: {p}" for p in _binfmt.recording_stamp_problems(frames)]
    both = [sid for sid, frames in recordings.items()
            if all(sum(f.code == c for f in frames) >= 2 for c in (_binfmt.OUT, _binfmt.IN))]
    if not problems and not both:
        problems.append("input missing: no recording holds two or more frames of each direction")
    return _result(problems, f"{len(recordings)} recordings, each direction's stamps non-decreasing")


# ---- C3 control captures ---------------------------------------------------------------------

class Capture(list):
    """(from, msg, probe) per line, in order; `runs` holds each line's core run (None without one)."""

    def __init__(self) -> None:
        super().__init__()
        self.runs: list[str | None] = []


def _captures(run: Run, col: Collected, supervisor: bool) -> tuple[dict[str, Capture], list[str]]:
    # The kind in collected.json, and so the directory, says which socket a capture is from —
    # never a prefix of its id, which a connection id may share.
    socket = "supervisor-control" if supervisor else "conn-control"
    found = col.items.get("supervisor-control" if supervisor else "control", [])
    if not found:
        raise Missing(f"input missing: no {'supervisor' if supervisor else 'connection'} control capture")
    captures, problems = {}, []
    for ident, path in found:
        messages = Capture()
        where = path.relative_to(col.root).with_suffix("").as_posix()
        rows = _jsonl(path)
        for k, (n, row, error) in enumerate(rows):
            if error:
                if _torn_run_end(rows, k):
                    continue  # the last line of a run whose core was killed mid-write: cut, as a torn tail
                problems.append(f"{where}: {error}")
                continue
            errs = run.registry.validate(row, CONTROL_LINE)
            probe = isinstance(row, dict) and row.get("probe") is True
            if not errs and not probe:
                errs = _ipc.message_errors(run.registry, socket, row["from"], row["msg"])
            if not errs and probe and row["from"] != "core":
                errs = ["only the harness, speaking as the core, sends a probe"]
            if errs:
                problems.append(f"{where} line {n}: {errs[0]}")
                continue
            messages.append((row["from"], row["msg"], probe))
            messages.runs.append(row.get("run"))
        captures[ident] = messages
    return captures, problems


def _torn_run_end(rows: list, k: int) -> bool:
    """Row k failed to parse, and the rows around it say it ended a run: the parsed line before it
    and the one after it carry different runs, the later one after the earlier."""
    def run_of(row):
        return row.get("run") if isinstance(row, dict) else None
    before = next((row for _, row, err in reversed(rows[:k]) if err is None), None)
    after = next((row for _, row, err in rows[k + 1:] if err is None), None)
    b, a = run_of(before), run_of(after)
    return b is not None and a is not None and a > b


def _pairs(capture: Capture) -> list[tuple[str, dict]]:
    return [(sender, msg) for sender, msg, _ in capture]


def _has_subsequence(messages: Capture, wanted: list[str]) -> bool:
    it = iter(_ipc.kind(m) for _, m, _ in messages)
    return all(any(k == w for k in it) for w in wanted)


def _conn_captures(run: Run, col: Collected) -> tuple[dict[str, Capture], list[str]]:
    return _captures(run, col, supervisor=False)


@check("control-sequence")
def control_sequence(run: Run, col: Collected) -> tuple[bool, str]:
    captures, problems = _conn_captures(run, col)
    versions = _worker_versions(run, col)
    for ident, messages in captures.items():
        problems += [f"control/{ident}: {p}" for rule, p in _ipc.conn_order(_pairs(messages), messages.runs, versions.get(ident))
                     if rule == _ipc.ORDER]
    # A capture whose peer is at proto_version 0.4 or later (the supervisor's worker_created, or the worker's
    # manifest, carries it) must be spoken to in its own version, or in this tag's own where the peer's is later:
    # the version expected on the core's hello and the worker's hello_ack is the lower of the peer's version and
    # this tag's C3_VERSION, because a peer whose version the core does not know is offered the core's own (V1;
    # immortality §6.4). So a 0.4 peer is spoken to in 0.4, a 0.6 peer in 0.6, and a 0.7 peer in 0.6. The
    # comparison is numeric. A capture with no peer at 0.4 or later grades as today, so a 0.4 core toward 0.3
    # workers, or an all-0.3 capture, is unaffected.
    for ident, messages in captures.items():
        peer = versions.get(ident)
        if peer is None or not _version_at_least(peer, "0.4"):
            continue
        expected = peer if _version_at_least(_ipc.C3_VERSION, peer) else _ipc.C3_VERSION
        for sender, msg, _ in messages:
            k = _ipc.kind(msg)
            if sender == "core" and k == "hello" and msg.get("proto_version") != expected:
                problems.append(f"control/{ident}: hello offers {msg.get('proto_version')!r} to a peer at proto_version "
                                f"{peer}: it must offer {expected}, the lower of the peer's version and this tag's "
                                f"{_ipc.C3_VERSION}")
            elif sender == "worker" and k == "hello_ack" and msg.get("proto_version") != expected:
                problems.append(f"control/{ident}: hello_ack answers {msg.get('proto_version')!r} for a peer at proto_version "
                                f"{peer}: it must answer {expected}, the lower of the peer's version and this tag's "
                                f"{_ipc.C3_VERSION}")
    wanted = ["connect", "connected", "open_shell", "channel_opened"]
    if not problems and not any(_has_subsequence(m, wanted) for m in captures.values()):
        problems.append("no capture shows connect → connected → open_shell → channel_opened")
    return _result(problems, f"{len(captures)} connection captures follow the control protocol, "
                             "every message inside its closed shape")


@check("control-connection-lost")
def control_connection_lost(run: Run, col: Collected) -> tuple[bool, str]:
    captures, problems = _conn_captures(run, col)
    lost = sum(1 for m in captures.values() for _, msg, _ in m if _ipc.kind(msg) == "connection_lost")
    for ident, messages in captures.items():
        problems += [f"control/{ident}: {p}" for rule, p in _ipc.conn_order(_pairs(messages), messages.runs)
                     if rule == _ipc.LOST_SET]
    if not problems and lost == 0:
        problems.append("input missing: no connection_lost was captured (tear a connection down while collecting)")
    return _result(problems, f"{lost} connection_lost events each list exactly the open sessions")


def _during_connect(messages: Capture, event_kind: str, code: str | None = None) -> tuple[int, list[str]]:
    """How many `event_kind` events a capture holds, and any that arrived outside the window
    between connect and connected."""
    count, problems, connecting = 0, [], False
    for sender, msg, _ in messages:
        k = _ipc.kind(msg)
        if sender == "core" and k == "connect":
            connecting = True
        elif k == "connected":
            connecting = False
        if sender == "worker" and k == event_kind and (code is None or msg.get("code") == code):
            count += 1
            if not connecting:
                problems.append(f"{event_kind}{' ' + code if code else ''} arrived outside connect → connected")
    return count, problems


def _instances_check(run: Run, col: Collected, event_kind: str, what: str, code: str | None = None,
                     ends: bool = False) -> tuple[bool, str]:
    captures, problems = _conn_captures(run, col)
    total = 0
    for ident, messages in captures.items():
        count, found = _during_connect(messages, event_kind, code)
        total += count
        problems += [f"control/{ident}: {p}" for p in found]
        if ends and count:
            first = next(i for i, (s, m, _) in enumerate(messages)
                         if s == "worker" and _ipc.kind(m) == event_kind and (code is None or m.get("code") == code))
            if any(_ipc.kind(m) == "connected" for _, m, _ in messages[first:]):
                problems.append(f"control/{ident}: connected after {event_kind}: the connection was not refused")
    if not problems and total == 0:
        problems.append(f"input missing: no {what} was captured")
    return _result(problems, f"{total} {what} captured, each between connect and connected")


@check("control-hostkey-first-use")
def control_hostkey_first_use(run: Run, col: Collected) -> tuple[bool, str]:
    return _instances_check(run, col, "hostkey", "first-use host key report")


@check("control-hostkey-changed")
def control_hostkey_changed(run: Run, col: Collected) -> tuple[bool, str]:
    return _instances_check(run, col, "error", "refusal of a changed host key", code="hostkey_changed", ends=True)


@check("control-auth-failed")
def control_auth_failed(run: Run, col: Collected) -> tuple[bool, str]:
    return _instances_check(run, col, "auth_failed", "authentication failure", ends=True)


@check("control-unknown-cmd")
def control_unknown_cmd(run: Run, col: Collected) -> tuple[bool, str]:
    captures, problems = _conn_captures(run, col)
    known = _ipc.defined_kinds(run.registry, f"{_ipc.CONN}#/$defs/command")
    probes = 0
    for ident, messages in captures.items():
        for i, (sender, msg, _) in enumerate(messages):
            if sender != "core" or _ipc.kind(msg) in known:
                continue
            probes += 1
            answer = next((m for s, m, _ in messages[i + 1:] if s == "worker"), None)
            if answer is None or _ipc.kind(answer) != "error" or answer.get("code") != "unknown_cmd":
                problems.append(f"control/{ident}: unknown command {_ipc.kind(msg)!r} was not answered by "
                                "error unknown_cmd")
    if not problems and probes == 0:
        problems.append("input missing: no unknown-command probe was captured (inject one, marked probe)")
    return _result(problems, f"{probes} unknown commands answered by error unknown_cmd, the connection kept")


@check("control-connect-echo")
def control_connect_echo(run: Run, col: Collected) -> tuple[bool, str]:
    captures, problems = _conn_captures(run, col)
    echo = [i for i, m in captures.items()
            if any(_ipc.kind(msg) == "connect" and msg.get("protocol") == "echo" for _, msg, _ in m)
            and _has_subsequence(m, ["connect", "connected", "open_shell", "channel_opened"])]
    if not problems and not echo:
        problems.append("input missing: no capture shows the echo driver connect → connected → open_shell → "
                        "channel_opened")
    return _result(problems, f"{len(echo)} connections through the echo driver behind the same connect message")


@check("control-connect-telnet")
def control_connect_telnet(run: Run, col: Collected) -> tuple[bool, str]:
    """The telnet driver behind the same connect message: the branch names term, cols and rows and carries
    no credential, and there is no host key between connect and connected. A collection from e3 must show at
    least one telnet connection in this order. A telnet connect is legal only toward a peer at
    proto_version 0.5 or later (ipc-control top; edge CO-PW "Versioning"): the peer's version is the one
    the existing checks resolve — the worker manifest's or the supervisor's worker_created — and a telnet
    connect toward a known peer below 0.5 is a problem. A connection whose version the capture does not
    carry is not graded here, as control-sequence and control-timers do not grade one."""
    captures, problems = _conn_captures(run, col)
    versions = _worker_versions(run, col)
    telnet = [i for i, m in captures.items()
              if any(_ipc.kind(msg) == "connect" and msg.get("protocol") == "telnet" for _, msg, _ in m)
              and _has_subsequence(m, ["connect", "connected", "open_shell", "channel_opened"])]
    for ident in telnet:
        version = versions.get(ident)
        if version is not None and not _version_at_least(version, TELNET_MIN_VERSION):
            problems.append(f"control/{ident}: a telnet connect to a peer at proto_version {version}: the core "
                            f"sends a telnet connect only to a peer at {TELNET_MIN_VERSION} or later (ipc-control top)")
    if not problems and not telnet:
        problems.append("input missing: no capture shows the telnet driver connect → connected → open_shell → "
                        "channel_opened (nothing found is input missing)")
    return _result(problems, f"{len(telnet)} connections through the telnet driver behind the same connect message")


@check("control-channels")
def control_channels(run: Run, col: Collected) -> tuple[bool, str]:
    captures, problems = _conn_captures(run, col)
    seen = {"resize": 0, "close_channel": 0, "disconnect": 0}
    for ident, messages in captures.items():
        for i, (sender, msg, _) in enumerate(messages):
            k = _ipc.kind(msg)
            if sender != "core" or k not in seen:
                continue
            seen[k] += 1
            later = [(s, m) for s, m, _ in messages[i + 1:] if s == "worker"]
            if k == "close_channel" and not any(_ipc.kind(m) == "channel_closed"
                                                and m.get("session_id") == msg.get("session_id") for _, m in later):
                problems.append(f"control/{ident}: close_channel {msg.get('session_id')} never answered by channel_closed")
            if k == "disconnect" and not any(_ipc.kind(m) == "connection_lost" for _, m in later):
                problems.append(f"control/{ident}: disconnect never answered by connection_lost")
    missing = [k for k, n in seen.items() if n == 0]
    if not problems and missing:
        problems.append(f"input missing: no {', '.join(missing)} was captured")
    return _result(problems, f"{seen['resize']} resize, {seen['close_channel']} close_channel, "
                             f"{seen['disconnect']} disconnect, each on its own session or connection")


def _rule_problems(ident: str, messages: Capture, rule: str) -> list[str]:
    return [f"control/{ident}: {p}" for r, p in _ipc.conn_order(_pairs(messages), messages.runs) if r == rule]


def _reattach_evidence(messages: Capture) -> dict[str, int]:
    """How many of each handshake a capture shows reaching its end: a new session's (opened in the
    run that opened it, its core hello carrying last_committed_seq 0), a reattach after a core
    restart, a lone data-socket re-dial within one run, and an ended session replayed after a
    restart and released (legacy release or accounted disposal)."""
    seen = {"new": 0, "restart": 0, "redial": 0, "ended": 0}
    first_core = next((_ipc.kind(m) for s, m, _ in messages if s == "core"), None)
    later = messages.runs[:1] != [None] and first_core not in (None, "connect")
    run, opened, live, acks, lcs = (messages.runs[0] if messages.runs else None), set(), set(), {}, {}
    for (sender, msg, _), core_run in zip(messages, messages.runs):
        if core_run != run:
            run, later, opened, live, acks, lcs = core_run, True, set(), set(), {}, {}
        k, sid = _ipc.kind(msg), msg.get("session_id")
        if sender == "worker" and k == "channel_opened":
            opened.add(sid)
        elif sender == "core" and k == "hello":
            lcs[sid] = msg.get("last_committed_seq")
        elif sender == "worker" and k == "hello_ack":
            acks[sid] = msg
        elif sender == "core" and k == "live" and sid in acks:
            if sid in live:
                seen["redial"] += 1
            elif sid in opened and lcs.get(sid) == 0:
                # A brand-new session whose target printed its banner before the core's hello
                # arrives answers current_seq >= 1, and the replay carries those bytes; the core
                # offered last_committed_seq 0, so the handshake is still the new session's (C3
                # reattach-empty/reattach-sequence/redial-replay).
                seen["new"] += 1
            elif later:
                seen["restart"] += 1
            live.add(sid)
        elif sender == "core" and k in ("release", "release_accounted") and later and acks.get(sid, {}).get("ended"):
            seen["ended"] += 1
    return seen


@check("control-reattach")
def control_reattach(run: Run, col: Collected) -> tuple[bool, str]:
    """The local reattach handshake on every connection's control socket (ipc-control.schema.json;
    immortality §6.1, §6.3), and no input before live: a worker's input_refused replaying means the
    core sent input while the screen was still replaying, which is how you type into the wrong
    prompt."""
    captures, problems = _conn_captures(run, col)
    seen = {"new": 0, "restart": 0, "redial": 0, "ended": 0}
    for ident, messages in captures.items():
        problems += _rule_problems(ident, messages, _ipc.REATTACH)
        problems += [f"control/{ident}: input to session {m.get('session_id')} arrived before replay_done "
                     "(input_refused replaying): the core sent input before live"
                     for s, m, _ in messages if s == "worker" and _ipc.kind(m) == "input_refused"
                     and m.get("reason") == "replaying"]
        for key, n in _reattach_evidence(messages).items():
            seen[key] += n
    wanted = {"new": "a new session's handshake", "restart": "a reattach after a core restart (run-bounded captures)",
              "redial": "a lone data-socket re-dial replayed", "ended": "an ended session replayed after a restart "
              "and released (legacy release or accounted disposal)"}
    missing = [what for key, what in wanted.items() if not seen[key]]
    if not problems and missing:
        problems.append("input missing: no capture shows " + "; ".join(missing))
    return _result(problems, f"{seen['new']} new, {seen['restart']} restart, {seen['redial']} re-dial and "
                             f"{seen['ended']} ended handshakes, each in order, no input before live")


def _worker_versions(run: Run, col: Collected) -> dict[str, str | None]:
    """conn_id → the proto_version of the worker holding it, from the worker manifests and from the
    supervisors' worker_created (which names it before a manifest need exist)."""
    versions: dict[str, str | None] = {}
    for _ident, path in col.items.get("worker-manifest", []):
        try:
            doc = loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(doc, dict):
            for conn in doc.get("connections", []) if isinstance(doc.get("connections"), list) else []:
                if isinstance(conn, dict):
                    versions[conn.get("conn_id")] = doc.get("proto_version")
    if col.items.get("supervisor-control"):
        captures, _ = _captures(run, col, supervisor=True)
        for messages in captures.values():
            for sender, msg, _ in messages:
                if sender == "supervisor" and _ipc.kind(msg) == "worker_created" and "proto_version" in msg:
                    versions.setdefault(msg.get("conn_id"), msg["proto_version"])
    return versions


@check("control-timers")
def control_timers(run: Run, col: Collected) -> tuple[bool, str]:
    """Every connect to a v0.2 (or later) worker carries the values its sessions run by — timers,
    buffer_bytes, input_frame_max — because such a worker has no value of its own and refuses a
    connect without them (decision 5.36: a default in its code is what the knobs rule forbids).
    From contract-v0.3, a connect or timers toward a 0.3 worker also carries keepalive_probe_interval
    in range (greater than 0 and less than a positive keepalive_interval), and toward a 0.2 worker it
    is absent: the frozen v0.2 worker refuses a timers whose key set is not exactly the eight it knows
    (D29, #114)."""
    captures, problems = _conn_captures(run, col)
    versions = _worker_versions(run, col)
    graded = 0
    probe_sent = 0
    for ident, messages in captures.items():
        version = versions.get(ident)
        if version is None:
            continue
        for sender, msg, _ in messages:
            if sender != "core" or _ipc.kind(msg) not in ("connect", "timers"):
                continue
            graded += 1
            lacked = [key for key in ("timers", "buffer_bytes", "input_frame_max")
                      if _ipc.kind(msg) == "connect" and key not in msg]
            if lacked:
                problems.append(f"control/{ident}: connect to a v{version} worker without {', '.join(lacked)}")
            timers = msg.get("timers") if isinstance(msg.get("timers"), dict) else {}
            probe = timers.get("keepalive_probe_interval")
            at_least_03 = _version_at_least(version, "0.3")
            if at_least_03 and probe is None:
                problems.append(f"control/{ident}: {_ipc.kind(msg)} to a v{version} worker without "
                                "keepalive_probe_interval")
            elif not at_least_03 and probe is not None:
                problems.append(f"control/{ident}: {_ipc.kind(msg)} to a v{version} worker carries "
                                "keepalive_probe_interval, which the frozen v0.2 worker refuses (D29)")
            elif probe is not None:
                probe_sent += 1
                interval = timers.get("keepalive_interval", 0)
                if probe < 1 or (interval > 0 and probe >= interval):
                    problems.append(f"control/{ident}: keepalive_probe_interval {probe} is out of range "
                                    f"(> 0 and < keepalive_interval {interval})")
    if not problems and not graded:
        problems.append("input missing: no connect to a worker whose version is known (a v0.2 manifest or "
                        "worker_created) was captured")
    return _result(problems, f"{graded} connects and timers to versioned workers, each carrying its timer values "
                             f"and knobs; {probe_sent} probe intervals toward ≥ 0.3 peers and none toward 0.2")


@check("control-open-timeout")
def control_open_timeout(run: Run, col: Collected) -> tuple[bool, str]:
    """The bound on a shell's open (ipc-control open_shell; session-open step 12): every open_shell toward a
    worker at proto_version 0.6 or later carries open_timeout, and none toward an older worker, which does not
    know the field (V1: the core sends a peer only the keys its version knows). The value's range is the
    schema's, which control-sequence grades. A connection whose version the capture does not carry is not
    graded here, as control-timers does not grade one."""
    captures, problems = _conn_captures(run, col)
    versions = _worker_versions(run, col)
    graded = sent = 0
    for ident, messages in captures.items():
        version = versions.get(ident)
        if version is None:
            continue
        at_least = _version_at_least(version, OPEN_TIMEOUT_MIN_VERSION)
        for sender, msg, _ in messages:
            if sender != "core" or _ipc.kind(msg) != "open_shell":
                continue
            graded += 1
            has = "open_timeout" in msg
            if at_least and not has:
                problems.append(f"control/{ident}: open_shell {msg.get('session_id')} to a v{version} worker without "
                                "open_timeout")
            elif not at_least and has:
                problems.append(f"control/{ident}: open_shell {msg.get('session_id')} to a v{version} worker carries "
                                f"open_timeout, which only a peer at {OPEN_TIMEOUT_MIN_VERSION} or later is sent (V1)")
            elif has:
                sent += 1
    if not problems and not graded:
        problems.append("input missing: no open_shell to a worker whose version is known (a manifest or "
                        "worker_created) was captured")
    return _result(problems, f"{graded} open_shell to versioned workers; {sent} carry open_timeout toward "
                             f"≥ {OPEN_TIMEOUT_MIN_VERSION} peers and none toward older ones")


def _version_at_least(version: str, floor: str) -> bool:
    """Whether a proto_version is at or above a floor version, comparing the numeric parts."""
    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split(".") if x.isdigit())
    try:
        return parts(version) >= parts(floor)
    except ValueError:
        return False


@check("control-heartbeat")
def control_heartbeat(run: Run, col: Collected) -> tuple[bool, str]:
    """Heartbeats both ways on every connection (session model §9 E6, E7): the core's ping, and the
    worker's pong with the same n from its own loop."""
    captures, problems = _conn_captures(run, col)
    pongs = 0
    for ident, messages in captures.items():
        problems += _rule_problems(ident, messages, _ipc.HEARTBEAT)
        pongs += sum(1 for s, m, _ in messages if s == "worker" and _ipc.kind(m) == "pong")
    if not problems and not pongs:
        problems.append("input missing: no ping answered by pong was captured on a connection's control socket")
    return _result(problems, f"{pongs} pings answered by pong with the same n, none skipped")


@check("supervisor-heartbeat")
def supervisor_heartbeat(run: Run, col: Collected) -> tuple[bool, str]:
    """The core pings every supervisor and each answers pong (session model §9 E11), and every
    request line gets exactly one answer — so supervisor_unresponsive is never raised against a
    supervisor that is merely refusing."""
    captures, problems = _supervisor_captures(run, col)
    pongs = sum(1 for m in captures.values() for s, msg, _ in m if s == "supervisor" and _ipc.kind(msg) == "pong")
    if not problems and not pongs:
        problems.append("input missing: no ping answered by pong was captured on a supervisor's socket")
    return _result(problems, f"{pongs} supervisor pings answered, every request answered exactly once")


@check("control-linger")
def control_linger(run: Run, col: Collected) -> tuple[bool, str]:
    """connection_linger closes a connection only once no session is left on it, and the close
    lists none (session model §11): a retained ended session never holds a connection open."""
    captures, problems = _conn_captures(run, col)
    closes = 0
    for ident, messages in captures.items():
        problems += _rule_problems(ident, messages, _ipc.LINGER)
        closes += sum(1 for s, m, _ in messages if s == "worker" and _ipc.kind(m) == "connection_lost"
                      and m.get("cause") == "linger")
    if not problems and not closes:
        problems.append("input missing: no linger close was captured (let a connection's last session end and "
                        "connection_linger run out)")
    return _result(problems, f"{closes} linger closes, each of a connection with no session left")


def _outside_ends(messages: Capture) -> tuple[dict[str, set[tuple[str, str]]], set[str]]:
    """session_id → the set of (end_state, end_reason) tuples an end from outside TSB in one capture
    gives it (session model §13): a channel_closed by anything but core or worker → {(terminated,
    target_closed)}; every session a connection_lost lists, unless it answers the core's disconnect →
    {(terminated, target_closed), (terminated, connection_lost), (killed, orphan), (killed, idle),
    (killed, unattended)}, because the worker may have begun its own timer's end, or seen the target
    close, before the loss, and the core records that entry's end from the spool. A target_disconnect
    connection_lost also allows the three timer tuples, beside (terminated, target_closed), because the
    core reads the spool first (session-end-from-outside step 5). by: worker adds no expectation. The
    worker's reason text is never read: no exit status, no prompt.

    Also returns the set of session_ids that ended by the target (for the input-missing guard)."""
    ends: dict[str, set[tuple[str, str]]] = {}
    target_ended: set[str] = set()
    target_closed: set[str] = set()  # sessions the target explicitly closed on channel_closed
    for sender, msg, _ in messages:
        k = _ipc.kind(msg)
        if sender != "worker":
            continue
        if k == "channel_closed":
            by = msg.get("by")
            if by not in ("core", "worker"):
                sid = msg.get("session_id")
                ends[sid] = {("terminated", "target_closed")}
                target_ended.add(sid)
                target_closed.add(sid)
        elif k == "connection_lost" and msg.get("cause") != "disconnect":
            if msg.get("cause") == "target_disconnect":
                # target_disconnect: the target closed, but may also have timer ends
                allowed = {("terminated", "target_closed"), ("killed", "orphan"),
                          ("killed", "idle"), ("killed", "unattended")}
                for sid in msg.get("session_ids", []):
                    # A session the target already closed is ended by that close: a session ends once, so
                    # a later connection_lost listing it does not widen its expected end.
                    if sid in target_closed:
                        continue
                    ends[sid] = allowed
                    target_ended.add(sid)
            else:
                # Any timer end, or target_closed, or connection_lost, is allowed for a listed session
                allowed = {("terminated", "target_closed"), ("terminated", "connection_lost"),
                          ("killed", "orphan"), ("killed", "idle"), ("killed", "unattended")}
                for sid in msg.get("session_ids", []):
                    if sid in target_closed:
                        continue
                    ends[sid] = allowed
    return ends, target_ended


@check("outside-ends")
def outside_ends(run: Run, col: Collected) -> tuple[bool, str]:
    captures, problems = _conn_captures(run, col)
    expected: dict[str, set[tuple[str, str]]] = {}
    target_ended_sessions: set[str] = set()
    for messages in captures.values():
        session_ends, target_ended = _outside_ends(messages)
        expected.update(session_ends)
        target_ended_sessions.update(target_ended)
    if not problems and not target_ended_sessions:
        problems.append("input missing: no end by the target was captured (exit a shell on the target while "
                        "collecting)")
    ends, more = _audit_ends(run, col)
    problems += more
    for sid, allowed_tuples in sorted(expected.items()):
        end = ends.get(sid)
        got = f"{end['end_state']} / {end['end_reason']}" if end else "no end event"
        # Check if the actual (end_state, end_reason) pair is in the allowed set
        if end:
            end_tuple = (end['end_state'], end['end_reason'])
            if end_tuple in allowed_tuples:
                continue
        # If we get here, the end doesn't match
        allowed_str = ", ".join(sorted(f"{state} / {reason}" for state, reason in allowed_tuples))
        problems.append(f"session {sid}: ended from outside TSB, allowed: {allowed_str} (session model §13); "
                        f"the audit says {got}")
    return _result(problems, f"{len(expected)} sessions ended from outside TSB, each audited with an allowed end")


def _supervisor_captures(run: Run, col: Collected) -> tuple[dict[str, Capture], list[str]]:
    captures, problems = _captures(run, col, supervisor=True)
    for ident, messages in captures.items():
        problems += [f"control/supervisors/{ident}: {p}" for p in _ipc.supervisor_order(_pairs(messages), messages.runs)]
    return captures, problems


@check("supervisor-sequence")
def supervisor_sequence(run: Run, col: Collected) -> tuple[bool, str]:
    captures, problems = _supervisor_captures(run, col)
    created = sum(1 for m in captures.values() for _, msg, _ in m if _ipc.kind(msg) == "worker_created")
    if not problems and created == 0:
        problems.append("input missing: no create_worker → worker_created exchange was captured")
    return _result(problems, f"{created} workers created through the supervisor clause")


@check("supervisor-error")
def supervisor_error(run: Run, col: Collected) -> tuple[bool, str]:
    captures, problems = _supervisor_captures(run, col)
    errors = sum(1 for m in captures.values() for s, msg, _ in m if s == "supervisor" and _ipc.kind(msg) == "error")
    if not problems and errors == 0:
        problems.append("input missing: no supervisor error reply was captured (provoke a failed spawn)")
    return _result(problems, f"{errors} failed create_worker requests answered with a bounded error")


# ---- C6 audit ----------------------------------------------------------------------------

def _audit(run: Run, col: Collected) -> tuple[list[dict], list[str]]:
    [(_ident, path)] = col.need("audit")[:1]
    events, problems = [], []
    for n, row, error in _jsonl(path):
        if error:
            problems.append(error)
            continue
        errs = run.registry.validate(row, AUDIT_SCHEMA)
        if errs:
            problems.append(f"line {n}: {errs[0]}")
        else:
            events.append(row)
    return events, problems


@check("audit-valid")
def audit_valid(run: Run, col: Collected) -> tuple[bool, str]:
    events, problems = _audit(run, col)
    if not problems and not events:
        problems.append("input missing: the audit log is empty")
    return _result(problems, f"{len(events)} audit events valid")


@check("audit-recording-state")
def audit_recording_state(run: Run, col: Collected) -> tuple[bool, str]:
    events, _ = _audit(run, col)
    states = [e for e in events if e["action"] == "config.recording_state"]
    if not states:
        return False, "no config.recording_state event: the recorder's state at core start is not on the record"
    return True, f"{len(states)} config.recording_state events"


@check("audit-session-connect")
def audit_session_connect(run: Run, col: Collected) -> tuple[bool, str]:
    events, _ = _audit(run, col)
    connected = {e["session_id"] for e in events if e["action"] == "session.connect"}
    if not connected:
        return False, "no session.connect event in the audit log"
    recorded = {ident for ident, _ in col.items.get("recording", [])}
    unaudited = sorted(recorded - connected)
    if unaudited:
        return False, f"recorded sessions with no session.connect event: {', '.join(unaudited[:MAX_PROBLEMS])}"
    return True, f"{len(connected)} sessions audited as connected, every collected recording among them"


@check("audit-hostkey-accepted")
def audit_hostkey_accepted(run: Run, col: Collected) -> tuple[bool, str]:
    captures, problems = _conn_captures(run, col)
    reported = {msg["fingerprint"] for m in captures.values() for s, msg, _ in m
                if s == "worker" and _ipc.kind(msg) == "hostkey"}
    if not reported:
        raise Missing("input missing: no first-use host key was captured, so none can be traced to the audit")
    events, _ = _audit(run, col)
    audited = {e["detail"]["fingerprint"] for e in events if e["action"] == "target.hostkey_accepted"}
    unaudited = sorted(reported - audited)
    if unaudited:
        problems.append(f"host keys accepted on first use with no target.hostkey_accepted event: {', '.join(unaudited)}")
    return _result(problems, f"{len(reported)} first-use host keys, each on the record")


def _audit_ends(run: Run, col: Collected) -> tuple[dict[str, dict], list[str]]:
    """session_id → its valid session.disconnect event (the audit-valid check reports the rest),
    and a problem for every session the log ends more than once: the end event closes a
    session's record, and a session ends once (decision 5.44)."""
    events, _ = _audit(run, col)
    ends: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for e in events:
        if e["action"] == "session.disconnect":
            ends.setdefault(e["session_id"], e)
            counts[e["session_id"]] = counts.get(e["session_id"], 0) + 1
    problems = [f"audit: session {sid} has {n} session.disconnect events; a session ends once"
                for sid, n in sorted(counts.items()) if n > 1]
    return ends, problems


@check("audit-end-states")
def audit_end_states(run: Run, col: Collected) -> tuple[bool, str]:
    ends, problems = _audit_ends(run, col)
    if not problems and not ends:
        problems.append("input missing: no session ended in the audit log (end one before collecting)")
    reasons = sorted({f"{e['end_state']}/{e['end_reason']}" for e in ends.values()})
    return _result(problems, f"{len(ends)} session ends, each once, saying how and why: {', '.join(reasons)}")


# ---- C8 × C6: the recording says how the session ended ---------------------------------------

def _end_markers(run: Run, col: Collected, kind: str) -> tuple[bool, str]:
    """Every collected recording against the audit, both ways — an audit-ended session's recording
    ends in the marker of its end_state, a recording that ends in a marker has that end event, a
    live session's recording has none — and at least one `kind` marker among them, so neither
    kind passes on the other's evidence."""
    decoded, problems = _recordings(col)
    ends, more = _audit_ends(run, col)
    problems += more
    marked = {"killed": 0, "terminated": 0}
    for ident, frames in decoded.items():
        problems += [f"recordings/{ident}: {p}" for p in _binfmt.recording_problems(frames)]
        if frames and frames[-1].code == _binfmt.TERMINAL_INCOMPLETE:
            # A crash-cut recording ends in the code-9 marker, not an end marker: it is an unfinished
            # end, never a live one. A finalized collection needs the original end marker and its
            # matching session.disconnect, so the check names it and fails.
            problems.append(f"recordings/{ident}: ends in a terminal incompleteness marker with no end "
                            "marker: a finalized collection needs the original end marker and its "
                            "matching session.disconnect")
            continue
        marker = frames[-1].end if frames else None
        audited = ends.get(ident, {}).get("end_state")
        if audited and marker != audited:
            problems.append(f"recordings/{ident}: the audit ended the session {audited}, the recording ends "
                            + (f"in a {marker} marker" if marker else "with no end marker"))
        elif marker and not audited:
            problems.append(f"recordings/{ident}: ends in a {marker} marker, but the audit has no end event for it")
        if marker:
            marked[marker] += 1
    if not problems and not marked[kind]:
        problems.append(f"input missing: no recording of a session that ended {kind} was collected")
    return _result(problems, f"{marked[kind]} recordings end in a {kind} marker; every recording's marker, or "
                             "its absence for a live session, matches the audit")


@check("recordings-end-marker-killed")
def recordings_end_marker_killed(run: Run, col: Collected) -> tuple[bool, str]:
    return _end_markers(run, col, "killed")


@check("recordings-end-marker-terminated")
def recordings_end_marker_terminated(run: Run, col: Collected) -> tuple[bool, str]:
    return _end_markers(run, col, "terminated")


@check("recordings-worker-death")
def recordings_worker_death(run: Run, col: Collected) -> tuple[bool, str]:
    decoded, problems = _recordings(col)
    ends, more = _audit_ends(run, col)
    problems += more
    died = sorted(i for i, e in ends.items() if (e["end_state"], e["end_reason"]) == ("terminated", "worker_lost"))
    proved = []
    for ident in died:
        frames = decoded.get(ident)
        if frames is None:
            problems.append(f"recordings/{ident}: its worker died, and its recording was not collected")
        elif frames and frames[-1].end == "terminated" and not _binfmt.recording_problems(frames):
            proved.append(ident)
        else:
            problems.append(f"recordings/{ident}: its worker died, and the recording is not finalized with one "
                            "terminated marker as its last frame")
    if not problems and not proved:
        problems.append("input missing: no recording of a session whose worker died was collected "
                        "(kill a worker while collecting)")
    return _result(problems, f"{len(proved)} sessions whose worker died, each recording finalized with a "
                             "terminated marker")


def _latest_run(col: Collected) -> str | None:
    runs = {ident.split("/", 1)[0] for ident, _ in col.items.get("run-frames", [])}
    for kind in ("control", "supervisor-control"):
        for _ident, path in col.items.get(kind, []):
            runs |= {row.get("run") for _, row, err in _jsonl(path) if err is None and isinstance(row, dict)}
    runs.discard(None)
    return max(runs) if runs else None


@check("audit-orphan-end")
def audit_orphan_end(run: Run, col: Collected) -> tuple[bool, str]:
    """An orphan end (phase-01: orphan_timeout fires when the core does not return): audited killed /
    orphan with the worker's stamp, its recording ending in the killed marker with that stamp. The
    worker spooled the end and the returning core wrote it, so the stamp precedes that core's start
    (session model §15), unless that core was told of it on channel_closed by: worker. A gap marker
    standing before the end marker is the spool's (its span is recordings-gap's), and it ends no
    later than the end. Whether one was due — the spool's last_seq past what the recording held —
    this check cannot say: the core deletes the spool entry once it has written the end, so no
    collection holds it, and the core's own test grades that case (C8 gap-before-end)."""
    ends, problems = _audit_ends(run, col)
    recordings, more = _recordings(col) if col.items.get("recording") else ({}, [])
    problems += more
    latest = _latest_run(col)
    # Get control captures to check for channel_closed by: worker (catch Missing like other helpers)
    try:
        captures, more = _conn_captures(run, col)
        problems += more
    except Missing:
        captures = {}
    # Keep the worker-end event per session: the ended_at it names is the stamp the audit event and the
    # recording's end marker must both carry (session-end-by-tsb step 23b). The restart-time exemption
    # holds only when a by: worker in the latest run names that same stamp for the session.
    by_worker_end: dict[str, str] = {}
    if latest is not None:
        for messages in captures.values():
            for (sender, msg, _), core_run in zip(messages, messages.runs):
                if (core_run == latest and sender == "worker" and _ipc.kind(msg) == "channel_closed"
                        and msg.get("by") == "worker"):
                    by_worker_end[msg.get("session_id")] = msg.get("ended_at")
    proved = 0
    for sid, end in sorted(ends.items()):
        if (end["end_state"], end["end_reason"]) != ("killed", "orphan"):
            continue
        ts = _binfmt.rfc3339_ns(end["ts"])
        frames = recordings.get(sid)
        if frames is None:
            problems.append(f"session {sid}: ended killed / orphan, and its recording was not collected")
            continue
        marker = frames[-1] if frames else None
        if marker is None or marker.end != "killed":
            problems.append(f"recordings/{sid}: an orphan end, and the recording does not end in a killed marker")
            continue
        if (marker.sec, marker.usec) != _binfmt._sec_usec(ts):
            problems.append(f"recordings/{sid}: the end marker is stamped {_binfmt.usec_stamp(marker.sec, marker.usec)}, "
                            f"the audit event {end['ts']}: both are the worker's stamp of the end")
        # The session is exempt from the restart-time check only when its worker-end event names a stamp
        # that both the audit event and the recording carry: they must equal that event's ended_at.
        worker_end = by_worker_end.get(sid)
        told = worker_end is not None and ts == _binfmt.rfc3339_ns(worker_end) \
            and (marker.sec, marker.usec) == _binfmt._sec_usec(_binfmt.rfc3339_ns(worker_end))
        if latest is not None and ts >= int(latest) and not told:
            problems.append(f"session {sid}: its orphan end is stamped {end['ts']}, after the latest core started: the "
                            "end happened while the core was away and carries the worker's stamp")
        before = frames[_gap_before_end(frames)] if _gap_before_end(frames) >= 0 else None
        if before is not None and before.code == _binfmt.GAP and (before.sec, before.usec) > (marker.sec, marker.usec):
            problems.append(f"recordings/{sid}: the gap marker before the end is stamped "
                            f"{_binfmt.usec_stamp(before.sec, before.usec)}, after the end: the output was lost before "
                            "the session ended")
        proved += 1
    if not problems and not proved:
        problems.append("input missing: no orphan end was collected (let orphan_timeout fire while the core is away)")
    return _result(problems, f"{proved} orphan ends, each spooled by its worker and written with its stamp")


# ---- self-check ----------------------------------------------------------------------------

def check_grades(ctx: selfcheck.Context) -> None:
    """Every artefacts:<check> a fixture names exists, and every check is named by a fixture."""
    named = set()
    for fx in ctx.fixtures:
        if fx.grade.startswith("artifacts:"):
            name = fx.grade.split(":", 1)[1]
            named.add(name)
            if name not in CHECKS:
                ctx.report.fail(fx.rel, f"grade names no artefact check: {name!r} (known: {', '.join(sorted(CHECKS))})")
            if fx.kind in ("rest", "ws"):
                ctx.report.fail(fx.rel, "rest and ws fixtures are graded live")
    for name in sorted(set(CHECKS) - named):
        ctx.report.fail("coverage", f"artefact check {name!r} is named by no fixture")


selfcheck.COVERAGE_CHECKS.append(check_grades)
