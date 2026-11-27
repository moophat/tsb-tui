"""The two binary formats — the C3 data-frame envelope and the C8 recording (a classic ttyrec
plus its sidecar, which says of every frame whether it is output, input or the end marker) — and
the transforms between contract shapes: the tap derivation (C3 frames and the session's end →
C8 frames) and the C5 rediscovery rules (manifests, socket liveness and, from v0.2, the spool →
what a restarted core dials, hands off to the handshake, and ends).

The layouts are normative in the schemas' x-binary-layout and x-sidecar; self-check proves these
codecs agree with them, so the code and the published description cannot drift apart.
"""

from __future__ import annotations

import calendar
import datetime
import os
import posixpath
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from . import _inventory, selfcheck
from .fixtures import Fixture

DATA_FRAME_SCHEMA = "schemas/ipc-data-frame.schema.json"
RECORDING_SCHEMA = "schemas/ttyrec.schema.json"

ENVELOPE = struct.Struct(">BBHIQQ")  # version, flags, stream, length, seq, timestamp: 24 bytes
RECORD = struct.Struct("<III")       # classic ttyrec: sec, usec, length: 12 bytes
ENVELOPE_FIELDS = ("version", "flags", "stream", "length", "seq", "timestamp")
RECORD_FIELDS = ("sec", "usec", "length")
# Sidecar codes, one byte per ttyrec frame. 2 is the gap marker, held at v0.1 and defined from v0.2,
# so adding it renumbered nothing. 5 is the broker marker, defined at v0.3. 6 and 7 are the login prompt
# and the login answer, defined with SessionCreateRequest.login (ttyrec.schema.json x-login-line). 8 is TSB's
# own prompt text in a device login (x-own-prompt-line), defined with the device login (Release B).
OUT, IN, GAP, END_KILLED, END_TERMINATED, BROKER, LOGIN_PROMPT, LOGIN_ANSWER, OWN_PROMPT = 0, 1, 2, 3, 4, 5, 6, 7, 8
TERMINAL_INCOMPLETE = 9
SIDECAR_VALUES = {OUT: "out", IN: "in", GAP: "gap", END_KILLED: "end:killed", END_TERMINATED: "end:terminated",
                  BROKER: "broker", LOGIN_PROMPT: "login_prompt", LOGIN_ANSWER: "login_answer",
                  OWN_PROMPT: "own_prompt", TERMINAL_INCOMPLETE: "terminal_incomplete"}
LOGIN_CODES = (LOGIN_PROMPT, LOGIN_ANSWER)
# The frames that stand at the head of a recording, before its first frame of any other code, on the input clock.
HEAD_CODES = (*LOGIN_CODES, OWN_PROMPT)
MARKER_CODES = {"gap": GAP, "broker": BROKER, "login_prompt": LOGIN_PROMPT, "login_answer": LOGIN_ANSWER,
                "own_prompt": OWN_PROMPT, "terminal_incomplete": TERMINAL_INCOMPLETE}
RESERVED_CODES: tuple[int, ...] = ()
# The gap marker's data (ttyrec.schema.json x-gap-line): one fixed-format line a classic player
# prints, so the recording tells the truth on its own. Span ends are UTC with microseconds.
_STAMP = rb"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z"
GAP_LINE = re.compile(rb"\r\n\[TSB gap: ([1-9][0-9]{0,19}) bytes lost from (" + _STAMP + rb") to (" + _STAMP + rb")\]\r\n\Z")
# The broker marker's data (ttyrec.schema.json x-broker-line): one fixed pure-ASCII line a classic
# player prints, carrying the stale-grid choice. A plain hyphen-minus, never an em dash; the choices
# keep their C2/audit spelling, underscores and all.
BROKER_CHOICES = ("wiggle", "ctrl_l", "continue", "fresh_session")
BROKER_LINE = re.compile(rb"\r\n\[TSB: stale screen after lost output - ("
                         + b"|".join(c.encode("ascii") for c in BROKER_CHOICES) + rb") chosen\]\r\n\Z")
# The login frames' data (ttyrec.schema.json x-login-line): a fixed ASCII tag, then the value's text with
# every backslash and every C0, C1 and DEL character escaped, then CR LF: one printable row. Code 6 carries
# the prompt tag and code 7 the answer tag.
LOGIN_TAGS = {LOGIN_PROMPT: b"\r\n[TSB: login prompt] ", LOGIN_ANSWER: b"\r\n[TSB: login answer] "}
# TSB's own prompt (x-own-prompt-line): the same line form with its own fixed tag, and a text that is one of two
# fixed lines, never a value, so it has no escape.
OWN_PROMPT_TAG = b"\r\n[TSB: own prompt] "
OWN_PROMPT_TEXTS = ("login as:", "Password:")
LOGIN_TEXT = re.compile(r"(?:[^\x00-\x1f\x7f-\x9f\\]|\\x(?:[01][0-9a-f]|7f|[89][0-9a-f])|\\\\)*\Z")
_LOGIN_UNESCAPE = re.compile(r"\\(?:x([0-9a-f]{2})|(\\))")
END_STATES = {END_KILLED: "killed", END_TERMINATED: "terminated"}
END_CODES = {state: code for code, state in END_STATES.items()}
VERSION = 1
FLAG_IN = 0x01            # bit 0: 1 = core → target
FLAG_GAP = 0x02           # bit 1: the gap frame — output declared lost, no payload
KNOWN_FLAGS = FLAG_IN | FLAG_GAP  # every other bit is 0 at this tag
NS, US = 1_000_000_000, 1_000
U32_MAX = 0xFFFFFFFF
# common.schema.json $defs.id: the identifier grammar every session/request id must satisfy, on the
# wire and in a control object alike (so the two forms can never disagree).
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
LIVE = "connectable"
CORE_PROTO = "0.2"        # frozen c5-rediscovery-v0.2 transform; current C3 is _ipc.C3_VERSION

# ---- C4 core ↔ VT seam framing (vt-ipc.schema.json x-binary-layout) -----------------------
VT_IPC_SCHEMA = "schemas/vt-ipc.schema.json"
VT_SCREEN_SCHEMA = "schemas/vt-screen.schema.json"
VT_HEADER = struct.Struct(">IB")  # length (body bytes, after the kind), kind: 5 bytes
VT_HEADER_FIELDS = ("length", "kind")
VT_KIND_CONTROL, VT_KIND_FRAME, VT_KIND_BLOB = 1, 2, 3
VT_PROTO = "0.3"          # the C4 version this tag speaks, on hello/hello_ack
# The stated maximum message size is a contract range (R7), a config.yaml knob (5.36): the codec
# takes it from the caller, and this value is only the range's published ceiling, never a default.
# The VT header encodes the body length in a 32-bit unsigned field, so the ceiling is the largest
# length that field can represent (2^32-1); a larger configured limit cannot be encoded on the wire.
VT_MAX_MESSAGE_BYTES = U32_MAX  # 4294967295, the 32-bit wire length ceiling


class FormatError(Exception):
    pass


def direction(flags: int) -> str:
    return "in" if flags & FLAG_IN else "out"


@dataclass(frozen=True)
class DataFrame:
    flags: int
    seq: int
    timestamp: int
    payload: bytes
    version: int = VERSION
    stream: int = 0

    @property
    def direction(self) -> str:
        return direction(self.flags)

    @property
    def gap(self) -> bool:
        return bool(self.flags & FLAG_GAP)


@dataclass(frozen=True)
class RecFrame:
    """One ttyrec frame and its sidecar code (SIDECAR_VALUES)."""
    sec: int
    usec: int
    code: int
    data: bytes

    @property
    def direction(self) -> str | None:
        """out or in for a data frame; None for a marker."""
        return {OUT: "out", IN: "in"}.get(self.code)

    @property
    def end(self) -> str | None:
        """killed or terminated for an end marker; None otherwise."""
        return END_STATES.get(self.code)

    @property
    def broker(self) -> str | None:
        """The stale-grid choice for a broker marker; None otherwise."""
        return parse_broker_line(self.data) if self.code == BROKER else None

    @property
    def login(self) -> str | None:
        """The text of a login prompt or login answer frame, its escape undone; None otherwise."""
        return parse_login_line(self.code, self.data) if self.code in LOGIN_CODES else None

    @property
    def own_prompt(self) -> str | None:
        """The fixed line of an own-prompt frame (login as: or Password:); None otherwise."""
        return parse_own_prompt_line(self.data) if self.code == OWN_PROMPT else None


def encode_frames(frames: list[DataFrame]) -> bytes:
    return b"".join(ENVELOPE.pack(f.version, f.flags, f.stream, len(f.payload), f.seq, f.timestamp) + f.payload
                    for f in frames)


def decode_frames(data: bytes) -> list[DataFrame]:
    frames, offset = [], 0
    while offset < len(data):
        if len(data) - offset < ENVELOPE.size:
            raise FormatError(f"{len(data) - offset} trailing bytes at offset {offset}: a torn envelope")
        version, flags, stream, length, seq, timestamp = ENVELOPE.unpack_from(data, offset)
        start = offset + ENVELOPE.size
        if start + length > len(data):
            raise FormatError(f"frame at offset {offset} declares {length} payload bytes, {len(data) - start} remain")
        frames.append(DataFrame(flags, seq, timestamp, data[start:start + length], version, stream))
        offset = start + length
    return frames


def encode_recording(frames: list[RecFrame]) -> tuple[bytes, bytes]:
    """(the .ttyrec bytes, the .dir sidecar bytes)."""
    ttyrec = b"".join(RECORD.pack(f.sec, f.usec, len(f.data)) + f.data for f in frames)
    return ttyrec, bytes(f.code for f in frames)


def decode_recording(ttyrec: bytes, sidecar: bytes) -> list[RecFrame]:
    headers, offset = [], 0
    while offset < len(ttyrec):
        if len(ttyrec) - offset < RECORD.size:
            raise FormatError(f"{len(ttyrec) - offset} trailing bytes at offset {offset}: a torn header")
        sec, usec, length = RECORD.unpack_from(ttyrec, offset)
        start = offset + RECORD.size
        if start + length > len(ttyrec):
            raise FormatError(f"frame at offset {offset} declares {length} data bytes, {len(ttyrec) - start} remain")
        headers.append((sec, usec, ttyrec[start:start + length]))
        offset = start + length
    if len(sidecar) != len(headers):
        raise FormatError(f"the sidecar holds {len(sidecar)} bytes for {len(headers)} ttyrec frames")
    bad = sorted({b for b in sidecar if b not in SIDECAR_VALUES})
    if bad:
        raise FormatError(f"the sidecar holds codes {bad} this tag does not define "
                          f"({', '.join(f'{k} {v}' for k, v in SIDECAR_VALUES.items())})")
    return [RecFrame(sec, usec, code, data) for (sec, usec, data), code in zip(headers, sidecar)]


def cut_torn_frames(data: bytes) -> bytes:
    """A tap cut to its last complete frame: the torn tail a killed core leaves at the end of a run,
    cut as the recording's reopen rule cuts one."""
    offset = 0
    while len(data) - offset >= ENVELOPE.size:
        end = offset + ENVELOPE.size + ENVELOPE.unpack_from(data, offset)[3]
        if end > len(data):
            break
        offset = end
    return data[:offset]


def reopen(ttyrec: bytes, sidecar: bytes) -> tuple[bytes, bytes]:
    """The reopen rule (ttyrec.schema.json x-sidecar): cut both files to the last frame both hold.

    The writer appends a frame to the .ttyrec, then its code to the .dir, so a crash can leave a
    torn frame at the .ttyrec's tail, or a whole frame whose code was never written. Before it
    appends again it keeps only the frames that are complete in the .ttyrec and have a code in
    the .dir, and truncates both files there — never pads, which would shift every later code.
    """
    ends, offset = [], 0
    while len(ttyrec) - offset >= RECORD.size:
        end = offset + RECORD.size + RECORD.unpack_from(ttyrec, offset)[2]
        if end > len(ttyrec):
            break
        ends.append(end)
        offset = end
    keep = min(len(ends), len(sidecar))
    return ttyrec[:ends[keep - 1] if keep else 0], sidecar[:keep]


def frame_problems(f: DataFrame) -> list[str]:
    """What an envelope may not be. The gap frame (bit 1) is output with no payload, and only it is
    empty; every other v0.1 refusal stands."""
    problems = []
    if f.version != VERSION:
        problems.append(f"version {f.version}, this tag speaks {VERSION}")
    if f.flags & ~KNOWN_FLAGS:
        problems.append(f"flags {f.flags:#04x} set bits this tag reserves")
    if f.stream != 0:
        problems.append(f"stream {f.stream}, reserved as 0")
    if f.gap and f.direction == "in":
        problems.append("the gap bit on an input frame: only output is ever declared lost")
    elif f.gap and f.payload:
        problems.append(f"a gap frame carries {len(f.payload)} payload bytes: it declares a loss and carries none")
    elif not f.gap and not f.payload:
        problems.append("empty payload: only the gap frame (bit 1) is empty")
    if f.seq < 1:
        problems.append("seq 0; sequences start at 1")
    if f.timestamp < 1:
        problems.append("timestamp 0")
    return problems


def rfc3339_ns(text: str) -> int:
    """Nanoseconds since the Unix epoch for a common.schema.json timestamp (UTC, Z, up to nine
    fractional digits), without the rounding a float would add."""
    whole, _, frac = text.rstrip("Z").partition(".")
    moment = datetime.datetime.strptime(whole, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.UTC)
    return calendar.timegm(moment.timetuple()) * NS + int((frac or "0").ljust(9, "0")[:9])


def usec_stamp(sec: int, usec: int) -> str:
    """A (sec, usec) pair as the gap line writes it: RFC 3339, UTC, six fractional digits."""
    moment = datetime.datetime.fromtimestamp(sec, datetime.UTC).replace(microsecond=usec)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def gap_line(lost: int, start: tuple[int, int], end: tuple[int, int]) -> bytes:
    """The gap marker's data: lost bytes over the span (start, end], each end a (sec, usec)."""
    return f"\r\n[TSB gap: {lost} bytes lost from {usec_stamp(*start)} to {usec_stamp(*end)}]\r\n".encode("ascii")


def parse_gap_line(data: bytes) -> tuple[int, tuple[int, int], tuple[int, int]] | None:
    """(lost bytes, span start, span end) from a gap marker's data, each end a (sec, usec); None
    when the data is not the line (a TSB reader parses the marker by its code, never by its text)."""
    match = GAP_LINE.match(data)
    if not match:
        return None
    ends = []
    for raw in match.group(2, 3):
        try:
            moment = datetime.datetime.strptime(raw.decode("ascii"), "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=datetime.UTC)
        except ValueError:  # a field out of range: month 13, say
            return None
        ends.append((calendar.timegm(moment.timetuple()), moment.microsecond))
    return int(match.group(1)), ends[0], ends[1]


def broker_line(choice: str) -> bytes:
    """The broker marker's data: the fixed pure-ASCII line carrying the stale-grid choice. The
    separator is a plain hyphen-minus, never an em dash."""
    if choice not in BROKER_CHOICES:
        raise FormatError(f"{choice!r} is not a stale-grid choice ({', '.join(BROKER_CHOICES)})")
    return f"\r\n[TSB: stale screen after lost output - {choice} chosen]\r\n".encode("ascii")


def parse_broker_line(data: bytes) -> str | None:
    """The choice from a broker marker's data, or None when the data is not the line (a TSB reader
    parses the marker by its code, never by its text)."""
    match = BROKER_LINE.match(data)
    return match.group(1).decode("ascii") if match else None


def login_line(code: int, text: str) -> bytes:
    """A login frame's data: the code's tag, the text with its backslashes and its C0, C1 and DEL characters
    escaped (a backslash as two, a control as \\xNN, lowercase hex of the code point), then CR LF."""
    if code not in LOGIN_TAGS:
        raise FormatError(f"{code} is not a login code ({LOGIN_PROMPT} prompt, {LOGIN_ANSWER} answer)")
    escaped = []
    for char in text:
        point = ord(char)
        if char == "\\":
            escaped.append("\\\\")
        elif point < 0x20 or 0x7F <= point <= 0x9F:
            escaped.append(f"\\x{point:02x}")
        else:
            escaped.append(char)
    try:
        body = "".join(escaped).encode("utf-8")
    except UnicodeEncodeError:
        raise FormatError("a login value that is not valid Unicode text (a lone surrogate) has no line") from None
    return LOGIN_TAGS[code] + body + b"\r\n"


def parse_login_line(code: int, data: bytes) -> str | None:
    """The text of a login frame's data, its escape undone, or None when the data is not the line of that
    code (a TSB reader parses the frame by its code, never by its text). The escape is the only encoding:
    \\x41, uppercase hex, any other backslash sequence, a raw control or C1 character, and bytes that are not
    UTF-8 are all refused."""
    tag = LOGIN_TAGS.get(code)
    if tag is None or not data.startswith(tag) or not data.endswith(b"\r\n") or len(data) < len(tag) + 2:
        return None
    try:
        body = data[len(tag):-2].decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not LOGIN_TEXT.match(body):
        return None
    return _LOGIN_UNESCAPE.sub(lambda m: chr(int(m.group(1), 16)) if m.group(1) else "\\", body)


def own_prompt_line(text: str) -> bytes:
    """An own-prompt frame's data: the tag, one of the two fixed lines, then CR LF."""
    if text not in OWN_PROMPT_TEXTS:
        raise FormatError(f"{text!r} is not one of TSB's own prompt lines ({', '.join(OWN_PROMPT_TEXTS)})")
    return OWN_PROMPT_TAG + text.encode("ascii") + b"\r\n"


def parse_own_prompt_line(data: bytes) -> str | None:
    """The fixed line of an own-prompt frame's data, or None when the data is not exactly the tag, one of the
    two fixed lines and CR LF (a TSB reader parses the frame by its code, never by its text)."""
    for text in OWN_PROMPT_TEXTS:
        if data == own_prompt_line(text):
            return text
    return None


TERMINAL_REASONS = ("core_wait_timeout", "core_stop", "worker_drain_timeout", "worker_read_error")
_UINT = rb"(0|[1-9][0-9]{0,19})"
TERMINAL_LINE = re.compile(
    rb"\r\n\[TSB: output incomplete after seq " + _UINT + rb" bytes " + _UINT
    + rb"; reason (" + b"|".join(r.encode("ascii") for r in TERMINAL_REASONS)
    + rb"); observed_ns ([1-9][0-9]{0,18}); remaining unknown\]\r\n\Z")


def parse_terminal_incomplete_line(data: bytes) -> tuple[int, int, str, int] | None:
    """An unknown terminal remainder is not a counted gap or a zero-length loss."""
    match = TERMINAL_LINE.fullmatch(data)
    if match is None or not 100 <= len(data) <= 167:
        return None
    seq, count, reason, observed = match.groups()
    seq, count, observed = int(seq), int(count), int(observed)
    if max(seq, count) > 0xFFFFFFFFFFFFFFFF or observed > 4294967295999999999:
        return None
    return seq, count, reason.decode("ascii"), observed


def terminal_incomplete_line(seq: int, count: int, reason: str, observed: int) -> bytes:
    """Encode the canonical code-9 observation without inventing a suffix count."""
    if any(type(value) is not int for value in (seq, count, observed)):
        raise FormatError("terminal observation counters and stamp must be integers")
    data = (f"\r\n[TSB: output incomplete after seq {seq} bytes {count}; reason {reason}; "
            f"observed_ns {observed}; remaining unknown]\r\n").encode("ascii")
    if parse_terminal_incomplete_line(data) is None:
        raise FormatError("invalid terminal incompleteness observation")
    return data


def record_problems(f: RecFrame) -> list[str]:
    """What one recording frame may not be."""
    problems = []
    if f.usec > 999_999:
        problems.append(f"usec {f.usec} is not below one second")
    if f.code == GAP:
        parsed = parse_gap_line(f.data)
        if not f.data:
            problems.append("a gap marker carries its readable line: this one has no data")
        elif parsed is None:
            problems.append("a gap marker's data is not the gap marker's line (x-gap-line)")
        elif parsed[2] != (f.sec, f.usec):
            problems.append(f"a gap marker is stamped with its span's end: its line ends {usec_stamp(*parsed[2])}, "
                            f"the frame is stamped {usec_stamp(f.sec, f.usec)}")
        elif parsed[1] > parsed[2]:
            problems.append("a gap marker's span starts after it ends")
    elif f.code == BROKER:
        if not f.data:
            problems.append("a broker marker carries its readable line: this one has no data")
        elif parse_broker_line(f.data) is None:
            problems.append("a broker marker's data is not the broker marker's line (x-broker-line)")
    elif f.code in LOGIN_CODES:
        if not f.data:
            problems.append("a login frame carries its readable line: this one has no data")
        elif parse_login_line(f.code, f.data) is None:
            problems.append("a login frame's data is not the login line of its code (x-login-line)")
    elif f.code == OWN_PROMPT:
        if not f.data:
            problems.append("an own-prompt frame carries its readable line: this one has no data")
        elif parse_own_prompt_line(f.data) is None:
            problems.append("an own-prompt frame's data is not the own-prompt line (x-own-prompt-line)")
    elif f.code == TERMINAL_INCOMPLETE:
        parsed = parse_terminal_incomplete_line(f.data)
        if parsed is None:
            problems.append("invalid terminal incompleteness line (x-terminal-incomplete-line)")
        elif (f.sec, f.usec) != _sec_usec(parsed[3]):
            problems.append("terminal incompleteness header differs from observed_ns")
    elif f.end and f.data:
        problems.append(f"an end marker carries no data, this one {len(f.data)} bytes a player would print")
    elif f.direction and not f.data:
        problems.append("an empty data frame: only an end marker has no data")
    return problems


def recording_problems(frames: list[RecFrame]) -> list[str]:
    """What a v0.1 recording may not be: every frame's rules, and at most one end marker, which
    is the last frame. Whether a marker must be there is the session's state, not the format's:
    an ended session's recording has one, a live session's has none."""
    problems = [f"frame {i}: {p}" for i, f in enumerate(frames) for p in record_problems(f)]
    first_other = next((i for i, f in enumerate(frames) if f.code not in HEAD_CODES), len(frames))
    late = [i for i, f in enumerate(frames) if f.code in HEAD_CODES and i > first_other]
    if late:
        problems.append(f"login frames at {', '.join(map(str, late))} stand after frame {first_other}: they are "
                        "the head of the recording, before every other frame (x-login-line, x-own-prompt-line)")
    incomplete = [i for i, f in enumerate(frames) if f.code == TERMINAL_INCOMPLETE]
    if len(incomplete) > 1:
        problems.append(f"{len(incomplete)} terminal incompleteness markers "
                        f"(frames {', '.join(map(str, incomplete))}): only one is allowed")
    elif incomplete:
        i = incomplete[0]
        if i + 1 < len(frames) and not frames[i + 1].end:
            problems.append(f"terminal incompleteness marker at frame {i} must be followed only "
                            "by the original end marker")
        parsed = parse_terminal_incomplete_line(frames[i].data)
        if parsed is not None:
            last_seq, out_bytes = recording_baseline(frames[:i])
            if parsed[1] != out_bytes or (last_seq is not None and parsed[0] != last_seq):
                walk_seq = last_seq if last_seq is not None else "unknown"
                problems.append(f"terminal incompleteness marker at frame {i} claims seq {parsed[0]} bytes "
                                f"{parsed[1]}, but the recording walk before it is seq {walk_seq} bytes "
                                f"{out_bytes}: a code-9 marker's counters must match the recording it sits in (§6)")
    markers = [i for i, f in enumerate(frames) if f.end]
    if len(markers) > 1:
        problems.append(f"{len(markers)} end markers (frames {', '.join(map(str, markers))}): a session ends once")
    elif markers and markers[0] != len(frames) - 1:
        problems.append(f"the end marker is frame {markers[0]} of {len(frames)}: it must be the last frame")
    return problems


def _sec_usec(timestamp: int) -> tuple[int, int]:
    sec, usec = timestamp // NS, (timestamp % NS) // US
    if sec > U32_MAX:
        raise FormatError(f"timestamp {timestamp} ns is past what a u32 sec can hold")
    return sec, usec


@dataclass(frozen=True)
class RecordingPair:
    """One fixed-size recording snapshot; None represents an unavailable sidecar."""
    path: str
    ttyrec: bytes
    sidecar: bytes | None
    committed_offset: int | None = None


@dataclass(frozen=True)
class RecordingChain:
    session_id: str
    pairs: tuple[RecordingPair, ...]
    audit: tuple[dict, ...]
    audit_complete: bool


@dataclass(frozen=True)
class RecordingEvidence:
    path: str
    ttyrec_start: int
    ttyrec_end: int
    sidecar_start: int
    sidecar_end: int
    status: str
    frames: tuple[RecFrame, ...]
    ttyrec_bytes: bytes
    sidecar_bytes: bytes | None
    unparseable_ttyrec_start: int | None = None
    unparseable_sidecar_start: int | None = None


@dataclass(frozen=True)
class RecordingChainResult:
    ok: bool
    error: str | None = None
    frames: tuple[RecFrame, ...] = ()
    evidence: tuple[RecordingEvidence, ...] = ()
    value: object = None
    audit: tuple[dict, ...] = ()
    segments: tuple[tuple[int, int], ...] = ()  # per-pair spans [start, end) into frames; the input segment boundaries
    trailing_gap: bool = False  # True when the last pair is quarantined with no continuation: input after P is unknown


def open_recording_pair(path: Path, recording_path: str) -> RecordingChainResult:
    """Snapshot both lengths at open; bytes appended afterwards are not part of this read."""
    try:
        with path.open("rb") as tty:
            tty_length = os.fstat(tty.fileno()).st_size
            try:
                side = path.with_suffix(".dir").open("rb")
            except FileNotFoundError:
                raw = tty.read(tty_length)
                if len(raw) != tty_length:
                    return _chain_error("unavailable_pair")
                return RecordingChainResult(True, value=RecordingPair(recording_path, raw, None))
            with side:
                side_length = os.fstat(side.fileno()).st_size
                raw, codes = tty.read(tty_length), side.read(side_length)
                if len(raw) != tty_length or len(codes) != side_length:
                    return _chain_error("unavailable_pair")
                return RecordingChainResult(True, value=RecordingPair(recording_path, raw, codes))
    except (OSError, TypeError, ValueError, AttributeError):
        return _chain_error("unavailable_pair")


def _chain_error(code: str) -> RecordingChainResult:
    return RecordingChainResult(False, error=code)


def _recording_evidence(pair: RecordingPair, offset: int, side_count: int) -> RecordingEvidence:
    """Keep every raw suffix byte; parse only the coded, complete prefix of that evidence."""
    assert pair.sidecar is not None
    raw, codes = pair.ttyrec[offset:], pair.sidecar[side_count:]
    frames: list[RecFrame] = []
    pos, code_pos = 0, 0
    while pos < len(raw) and code_pos < len(codes):
        if len(raw) - pos < RECORD.size:
            break
        end = pos + RECORD.size + RECORD.unpack_from(raw, pos)[2]
        if end > len(raw):
            break
        try:
            frame = decode_recording(raw[pos:end], codes[code_pos:code_pos + 1])[0]
        except FormatError:
            break
        if record_problems(frame):
            break
        frames.append(frame)
        pos, code_pos = end, code_pos + 1
    complete = pos == len(raw) and code_pos == len(codes)
    return RecordingEvidence(pair.path, offset, len(pair.ttyrec), side_count, len(pair.sidecar),
                             "parsed" if complete else "unparseable", tuple(frames), raw, codes,
                             None if complete else offset + pos, None if complete else side_count + code_pos)


def _chain_walk(chain: RecordingChain, registry_cache: list | None = None) -> RecordingChainResult:
    if not isinstance(chain, RecordingChain) or chain.audit_complete is not True:
        return _chain_error("incomplete_audit")
    if (not isinstance(chain.session_id, str) or not _ID_RE.fullmatch(chain.session_id)
            or not isinstance(chain.pairs, tuple) or not isinstance(chain.audit, tuple)
            or any(not isinstance(event, dict) for event in chain.audit) or not chain.pairs):
        return _chain_error("invalid_chain")
    paths = [p.path for p in chain.pairs if isinstance(p, RecordingPair)]
    if (len(paths) != len(chain.pairs) or any(not isinstance(path, str) for path in paths)
            or len(set(paths)) != len(paths)):
        return _chain_error("invalid_chain")
    records = [e for e in chain.audit if isinstance(e, dict)
               and e.get("action") == "recording.output_incomplete" and e.get("session_id") == chain.session_id]
    try:
        if registry_cache is None:
            registry = selfcheck.Registry.from_tree(Path(__file__).resolve().parent.parent)
        else:
            if not registry_cache:
                registry_cache.append(selfcheck.Registry.from_tree(Path(__file__).resolve().parent.parent))
            registry = registry_cache[0]
        for event in records:
            if registry.validate(event, "schemas/audit-event.schema.json"):
                kind = event.get("detail", {}).get("kind") if isinstance(event.get("detail"), dict) else None
                return _chain_error("invalid_link" if kind == "continuation" else "invalid_notice")
    except (OSError, ValueError, selfcheck.SchemaError):
        return _chain_error("schema_unavailable")
    identities: dict[str, tuple[dict, object]] = {}
    for event in records:
        detail = event["detail"]
        key = detail["key"]
        if selfcheck._integrity_identity(event) != key:
            return _chain_error("invalid_chain")
        semantic = (detail, event.get("recording_path"))
        if key in identities and identities[key] != semantic:
            return _chain_error("invalid_chain")
        identities[key] = semantic
        if detail.get("kind") == "continuation" and detail.get("previous_path") not in paths:
            return _chain_error("invalid_chain")
    effective, evidence, segments = [], [], []
    used_keys: set[str] = set()
    anchor_seq, anchor_bytes, anchor_index = 0, 0, 0
    prior_generation = 0
    trailing_gap = False
    for index, pair in enumerate(chain.pairs):
        if (not isinstance(pair.path, str) or not isinstance(pair.ttyrec, bytes)
                or (pair.sidecar is not None and not isinstance(pair.sidecar, bytes))):
            return _chain_error("unavailable_pair")
        seg_start = len(effective)
        if index == len(chain.pairs) - 1:
            if any(isinstance(e.get("detail"), dict) and e["detail"].get("kind") == "continuation"
                   and e["detail"].get("previous_path") == pair.path for e in records):
                return _chain_error("unavailable_pair")
            held = [e for e in records if isinstance(e.get("detail"), dict) and e.get("recording_path") == pair.path
                    and e["detail"].get("reason") == "recording_unsynced"
                    and e["detail"].get("kind") in ("unconfirmed", "continuation_unconfirmed")]
            if held:
                if any(e["detail"] != held[0]["detail"] for e in held[1:]):
                    return _chain_error("invalid_notice")
                detail = held[0]["detail"]
                used_keys.add(detail["key"])
                offset = detail.get("baseline_offset") if index else pair.committed_offset
                if (type(offset) is not int or not 0 <= offset <= len(pair.ttyrec)
                        or (index == 0 and detail.get("kind") != "unconfirmed")
                        or (index > 0 and (detail.get("kind") != "continuation_unconfirmed"
                                           or detail.get("generation") != prior_generation))
                        or selfcheck._integrity_identity(held[0]) != detail.get("key")
                        or detail.get("unknown_suffix") is not True):
                    return _chain_error("invalid_notice")
                if pair.sidecar is None:
                    missing = RecordingEvidence(pair.path, offset, len(pair.ttyrec), 0, 0, "sidecar_missing", (),
                                                pair.ttyrec[offset:], None)
                    return RecordingChainResult(False, error="unavailable_pair", evidence=(*evidence, missing))
                pos, side_count = 0, 0
                while pos < offset:
                    if offset - pos < RECORD.size:
                        return _chain_error("invalid_prefix")
                    pos += RECORD.size + RECORD.unpack_from(pair.ttyrec, pos)[2]
                    side_count += 1
                    if pos > offset:
                        return _chain_error("invalid_prefix")
                if side_count > len(pair.sidecar):
                    return _chain_error("invalid_prefix")
                try:
                    effective.extend(decode_recording(pair.ttyrec[:offset], pair.sidecar[:side_count]))
                except FormatError:
                    return _chain_error("invalid_prefix")
                segments.append((seg_start, len(effective)))
                seq, count = recording_baseline(effective[anchor_index:])
                minimum = anchor_seq + sum(f.code in (OUT, GAP) for f in effective[anchor_index:])
                if (anchor_bytes + count != detail.get("baseline_bytes")
                        or (seq is not None and anchor_seq + seq != detail.get("baseline_seq"))
                        or (seq is None and detail.get("baseline_seq") < minimum)):
                    return _chain_error("invalid_notice")
                anchor_seq, anchor_bytes, anchor_index = detail["baseline_seq"], detail["baseline_bytes"], len(effective)
                evidence.append(_recording_evidence(pair, offset, side_count))
                trailing_gap = True
                break
            if pair.sidecar is None:
                return _chain_error("unavailable_pair")
            try:
                complete_ttyrec, complete_sidecar = reopen(pair.ttyrec, pair.sidecar)
                effective.extend(decode_recording(complete_ttyrec, complete_sidecar))
            except FormatError:
                return _chain_error("invalid_prefix")
            segments.append((seg_start, len(effective)))
            break
        successor = chain.pairs[index + 1]
        links = [e for e in records if isinstance(e.get("detail"), dict)
                 and e["detail"].get("kind") == "continuation"
                 and e["detail"].get("previous_path") == pair.path]
        if not links:
            return _chain_error("missing_link")
        if any(e["detail"] != links[0]["detail"] or e.get("recording_path") != links[0].get("recording_path")
               for e in links[1:]):
            return _chain_error("invalid_link")
        event, link = links[0], links[0]["detail"]
        used_keys.add(link["key"])
        generation = link.get("generation")
        expected_fields = {"version", "kind", "key", "generation", "previous_path", "previous_seq_out",
                           "previous_bytes", "previous_offset", "continuation_path"}
        if (set(link) != expected_fields or event.get("session_type") != "shell"
                or type(generation) is not int or not prior_generation < generation <= 0xFFFFFFFFFFFFFFFF
                or link.get("version") != 1 or link.get("key") != f"ri1:{chain.session_id}:c:{generation}"
                or event.get("recording_path") != pair.path or link.get("continuation_path") != successor.path
                or successor.path != f"recordings/{chain.session_id}.chain/continuation-{generation}.ttyrec"):
            return _chain_error("invalid_link")
        offset = link.get("previous_offset")
        if type(offset) is not int or not 0 <= offset <= len(pair.ttyrec):
            return _chain_error("invalid_prefix")
        if pair.sidecar is None:
            missing = RecordingEvidence(pair.path, offset, len(pair.ttyrec), 0, 0, "sidecar_missing", (),
                                        pair.ttyrec[offset:], None)
            return RecordingChainResult(False, error="unavailable_pair", evidence=(*evidence, missing))
        prefix, side_count, pos = pair.ttyrec[:offset], 0, 0
        while pos < offset:
            if offset - pos < RECORD.size:
                return _chain_error("invalid_prefix")
            pos += RECORD.size + RECORD.unpack_from(prefix, pos)[2]
            side_count += 1
            if pos > offset:
                return _chain_error("invalid_prefix")
        if side_count > len(pair.sidecar):
            return _chain_error("invalid_prefix")
        try:
            effective.extend(decode_recording(prefix, pair.sidecar[:side_count]))
        except FormatError:
            return _chain_error("invalid_prefix")
        segments.append((seg_start, len(effective)))
        seq, count = recording_baseline(effective[anchor_index:])
        minimum = anchor_seq + sum(f.code in (OUT, GAP) for f in effective[anchor_index:])
        if (anchor_bytes + count != link.get("previous_bytes")
                or (seq is not None and anchor_seq + seq != link.get("previous_seq_out"))
                or (seq is None and link.get("previous_seq_out") < minimum)):
            return _chain_error("invalid_link")
        anchor_seq, anchor_bytes, anchor_index = link["previous_seq_out"], link["previous_bytes"], len(effective)
        if index == 0:
            notices = [e for e in records if isinstance(e.get("detail"), dict)
                       and e["detail"].get("kind") == "unconfirmed" and e.get("recording_path") == pair.path
                       and e["detail"].get("reason") == "recording_unsynced"
                       and e["detail"].get("baseline_seq") == link.get("previous_seq_out")
                       and e["detail"].get("baseline_bytes") == link.get("previous_bytes")]
        else:
            notices = [e for e in records if isinstance(e.get("detail"), dict)
                       and e["detail"].get("kind") == "continuation_unconfirmed"
                       and e.get("recording_path") == pair.path and e["detail"].get("generation") == prior_generation
                       and e["detail"].get("reason") == "recording_unsynced"
                       and e["detail"].get("baseline_seq") == link.get("previous_seq_out")
                       and e["detail"].get("baseline_bytes") == link.get("previous_bytes")
                       and e["detail"].get("baseline_offset") == offset]
        if not notices:
            return _chain_error("missing_notice")
        if (any(e["detail"] != notices[0]["detail"] for e in notices[1:])
                or notices[0].get("session_type") != "shell"
                or notices[0]["detail"].get("unknown_suffix") is not True
                or type(notices[0]["detail"].get("observed_at_ns")) is not int
                or notices[0]["detail"]["observed_at_ns"] < 1
                or selfcheck._integrity_identity(notices[0]) != notices[0]["detail"].get("key")):
            return _chain_error("invalid_notice")
        used_keys.add(notices[0]["detail"]["key"])
        evidence.append(_recording_evidence(pair, offset, side_count))
        prior_generation = generation
    if any(e["detail"].get("kind") in ("continuation", "continuation_unconfirmed")
           and e["detail"]["key"] not in used_keys for e in records):
        return _chain_error("invalid_chain")
    if recording_problems(effective):
        return _chain_error("invalid_prefix")
    seq, count = recording_baseline(effective[anchor_index:])
    baseline = (None if seq is None else anchor_seq + seq, anchor_bytes + count)
    return RecordingChainResult(True, frames=tuple(effective), evidence=tuple(evidence), value=baseline,
                                audit=chain.audit, segments=tuple(segments), trailing_gap=trailing_gap)


def recording_chain_baseline(chain: RecordingChain) -> RecordingChainResult:
    result = _chain_walk(chain)
    if not result.ok:
        return result
    return RecordingChainResult(True, frames=result.frames, evidence=result.evidence,
                                value=result.value, audit=result.audit)


def recording_chain_stamp_problems(chain: RecordingChain) -> RecordingChainResult:
    result = _chain_walk(chain)
    if not result.ok:
        return result
    return RecordingChainResult(True, frames=result.frames, evidence=result.evidence,
                                value={"problems": recording_stamp_problems(list(result.frames)),
                                       "status": "unconfirmed" if result.evidence else "confirmed"},
                                audit=result.audit)


def _input_segments(result: RecordingChainResult) -> list[bytes]:
    """The chain's trusted input as a list of segments in order: each pair contributes one segment, its
    input frames at or below P (a quarantined pair) or whole (the last pair), the continuation's from its switch."""
    return [b"".join(f.data for f in result.frames[start:end] if f.code == IN) for start, end in result.segments]


def _input_explainable(segments: list[bytes], other_input: bytes, trailing_gap: bool) -> bool:
    """The other side's input can be written as these segments in order with gaps between them, one gap at most
    per quarantine boundary: the first segment a prefix, the last a suffix only when there is no trailing gap
    (the last pair is not quarantined), the middle ones placed greedily leftmost. Input is never replayed, so a
    lost unconfirmed suffix is a gap; a divergent trusted byte is not. When the last pair is quarantined with no
    continuation the input after P is unknown, so the chain ends in a trailing gap and its last segment is placed
    greedily, never as a suffix."""
    if not other_input.startswith(segments[0]):
        return False
    pos = len(segments[0])
    if trailing_gap:
        # The chain ends in a gap: every segment after the prefix is placed greedily with gaps allowed, and the
        # last segment is never anchored as a suffix.
        for seg in segments[1:]:
            idx = other_input.find(seg, pos)
            if idx < 0:
                return False
            pos = idx + len(seg)
        return True
    last = segments[-1]
    tail_start = len(other_input) - len(last)
    if tail_start < pos:
        return False
    for seg in segments[1:-1]:
        idx = other_input.find(seg, pos, tail_start + 1)
        if idx < 0:
            return False
        pos = idx + len(seg)
        if pos > tail_start:
            return False
    return other_input.endswith(last)


def compare_recording_chains(a: RecordingChain, b: RecordingChain) -> RecordingChainResult:
    registry_cache: list = []
    left, right = _chain_walk(a, registry_cache), _chain_walk(b, registry_cache)
    if not left.ok:
        return left
    if not right.ok:
        return right
    comparison = compare_recordings(list(left.frames), list(right.frames))
    left_gap, right_gap = bool(left.evidence), bool(right.evidence)
    if not left_gap and not right_gap:
        comparison["input_status"] = "equal" if comparison["payload_identical"]["in"] else "different"
    elif left_gap != right_gap:
        # Exactly one side has a quarantine gap: its trusted segments placed in order in the other side's
        # complete input (the first a prefix, the last a suffix only when it has no trailing gap, the middle ones
        # greedy leftmost).
        gap_chain, other_in = ((left, b"".join(f.data for f in right.frames if f.code == IN)) if left_gap
                               else (right, b"".join(f.data for f in left.frames if f.code == IN)))
        comparison["input_status"] = "unconfirmed" if _input_explainable(_input_segments(gap_chain), other_in,
                                                                         gap_chain.trailing_gap) else "different"
    else:
        # Both sides have gaps. A common input exists whenever their first segments agree as prefixes (one is a
        # prefix of the other). The last segments impose nothing when at least one side ends in a trailing gap
        # (its last pair is quarantined with no continuation), because that segment sits in a gap; only when
        # neither side has a trailing gap must the last segments agree as suffixes. So `different` only when the
        # first segments disagree, or neither side has a trailing gap and the last segments disagree as suffixes.
        lseg, rseg = _input_segments(left), _input_segments(right)
        first_ok = lseg[0].startswith(rseg[0]) or rseg[0].startswith(lseg[0])
        last_ok = True
        if not left.trailing_gap and not right.trailing_gap:
            last_ok = lseg[-1].endswith(rseg[-1]) or rseg[-1].endswith(lseg[-1])
        comparison["input_status"] = "unconfirmed" if first_ok and last_ok else "different"
    if comparison["input_status"] == "unconfirmed":
        comparison["payload_identical"]["in"] = None
    return RecordingChainResult(True, evidence=left.evidence + right.evidence, value=comparison,
                                audit=left.audit + right.audit)


def recording_baseline(frames: list[RecFrame]) -> tuple[int | None, int]:
    """The immutable recording walk's baseline A=(L,B) (§4, §6): L is the last output sequence the
    recording establishes — each code-0 frame one seq — and B the output bytes through it, code-2 counted
    loss included. A gap marker adds its byte count and no seq (a recorded gap with no seq committed
    fabricates none, so L=0,B>0 is possible); a code-9 marker adds neither. The committed gap's sequence
    is not in the recording, so once the walk passes a code-2 marker L is unknown (returned as None):
    no sequence is ever invented past it, while B keeps counting exactly as before."""
    if isinstance(frames, RecordingChain):
        raise FormatError("chain input requires the chain-aware reader")
    last_seq, out_bytes = 0, 0
    for f in frames:
        if f.code == OUT:
            if last_seq is not None:
                last_seq += 1
            out_bytes += len(f.data)
        elif f.code == GAP:
            parsed = parse_gap_line(f.data)
            if parsed is not None:
                out_bytes += parsed[0]
            last_seq = None
    return last_seq, out_bytes


def to_recording(frame: DataFrame) -> RecFrame:
    """The tap's derivation (ttyrec.schema.json x-derivation): ns → (sec, usec), truncated."""
    return RecFrame(*_sec_usec(frame.timestamp), IN if frame.flags & FLAG_IN else OUT, frame.payload)


def recording_stamp_problems(frames: list[RecFrame]) -> list[str]:
    """Per direction a recording's stamps never decrease (ttyrec.schema.json x-compare): output
    frames, and the gap markers between them, carry the worker's clock; input frames, the broker
    markers, the login frames and the own-prompt frames carry the core's input clock. Across directions nothing is assumed, and the end marker,
    the observer's, is not compared."""
    if isinstance(frames, RecordingChain):
        raise FormatError("chain input requires the chain-aware reader")
    problems, last = [], {}
    for i, f in enumerate(frames):
        clock = "out" if f.code in (OUT, GAP) else "in" if f.code in (IN, BROKER, *HEAD_CODES) else None
        if clock is None:
            continue
        stamp, prev = (f.sec, f.usec), last.get(clock)
        if prev is not None and stamp < prev:
            problems.append(f"frame {i}: {clock} stamp {usec_stamp(*stamp)} before {usec_stamp(*prev)}: "
                            "the stamps go back")
        last[clock] = max(stamp, prev) if prev is not None else stamp
    return problems


def compare_recordings(a: list[RecFrame], b: list[RecFrame]) -> dict:
    """The payload-identical rule (ttyrec.schema.json x-compare): per direction the concatenated
    payloads of the data frames (codes 0 and 1) are byte-equal; frame boundaries may differ, and the
    end markers (3, 4) are left out. Output is identical only if the gap markers (2) are too, line
    for line: a recording that declares a loss is never identical to one that does not, however its
    payload reads. The broker markers (5) are left out of the payload and compared line for line, like
    a gap marker, and so are the login frames (6 and 7), prompts with prompts and answers with answers, and the own-prompt frames (8). With it, whether each recording's stamps are monotonic."""
    if isinstance(a, RecordingChain) or isinstance(b, RecordingChain):
        raise FormatError("chain input requires the chain-aware reader")

    def payload(frames: list[RecFrame], code: int) -> bytes:
        return b"".join(f.data for f in frames if f.code == code)

    def lines(frames: list[RecFrame], code: int) -> list[bytes]:
        return [f.data for f in frames if f.code == code]
    return {"payload_identical": {"out": payload(a, OUT) == payload(b, OUT) and lines(a, GAP) == lines(b, GAP)
                                  and lines(a, TERMINAL_INCOMPLETE) == lines(b, TERMINAL_INCOMPLETE),
                                  "in": payload(a, IN) == payload(b, IN) and lines(a, BROKER) == lines(b, BROKER)
                                  and all(lines(a, c) == lines(b, c) for c in HEAD_CODES)},
            "monotonic": {"a": not recording_stamp_problems(a), "b": not recording_stamp_problems(b)}}


def derive_recording(frames: list[DataFrame], end: dict | None = None, handshakes: list[dict] = (),
                     committed: dict | None = None) -> list[RecFrame]:
    """The recording a core writes from a tap (ttyrec.schema.json x-derivation), gap markers
    included. A gap frame becomes a code-2 line whose numbers come from the handshake that declared
    it — lost bytes are its first_buffered_offset minus the output bytes the recording holds so far
    (declared losses included), over the span from the stamp of the last output frame or gap marker
    to the gap frame's — and an ended session whose spooled last_seq is past the last one recorded
    gets one before its end marker, from the spool's out_bytes and last_out_ts. A gap marker is a
    commit point like an output frame: it moves last_committed_seq to its gap frame's seq and the
    committed bytes past the loss, so a core that dies right after writing one declares no loss twice.
    `committed` is what the recording held before these frames: {out_bytes, last_out_ts, last_seq},
    and created_at, where a span starts when it held no output yet (all nanoseconds)."""
    committed = committed or {}
    out_bytes, last_seq = committed.get("out_bytes", 0), committed.get("last_seq", 0)
    last_out = committed.get("last_out_ts", committed.get("created_at"))
    by_fbs = {h["first_buffered_seq"]: h for h in handshakes}

    def gap(lost: int, until: int) -> RecFrame:
        if last_out is None:
            raise FormatError("a gap marker's span starts at the last output frame or gap marker recorded, or at the "
                              "session's created_at, and neither is given")
        if lost < 1:
            raise FormatError(f"the numbers declare {lost} bytes lost: a gap loses at least one")
        return RecFrame(*_sec_usec(until), GAP, gap_line(lost, _sec_usec(last_out), _sec_usec(until)))

    derived = []
    for f in frames:
        if f.gap:
            h = by_fbs.get(f.seq + 1)
            if h is None:
                raise FormatError(f"gap frame {f.seq} has no handshake whose first_buffered_seq is {f.seq + 1}")
            if h.get("last_dropped_ts") != f.timestamp:
                raise FormatError(f"gap frame {f.seq} is stamped {f.timestamp}, its handshake's last_dropped_ts is "
                                  f"{h.get('last_dropped_ts')}")
            marker = gap(h["first_buffered_offset"] - out_bytes, f.timestamp)
            out_bytes, last_out, last_seq = h["first_buffered_offset"], f.timestamp, f.seq
            derived.append(marker)
            continue
        derived.append(to_recording(f))
        if f.direction == "out":
            out_bytes, last_out, last_seq = out_bytes + len(f.payload), f.timestamp, f.seq
    if end is not None:
        spool = end.get("spool")
        if spool and spool["last_seq"] > last_seq:
            derived.append(gap(spool["out_bytes"] - out_bytes, spool["last_out_ts"]))
        derived.append(end_marker(end["timestamp"], end["end_state"]))
    return derived


def end_marker(timestamp: int, end_state: str) -> RecFrame:
    """The frame that closes an ended session's recording: no data, the end time, and the end
    state the audit's end event carries (killed or terminated)."""
    return RecFrame(*_sec_usec(timestamp), END_CODES[end_state], b"")


def seq_problems(frames: list[DataFrame], *, contiguous: bool, from_one: bool = True) -> list[str]:
    """Per direction: strictly increasing from at least 1; with contiguous, exactly 1, 2, 3 …, except
    that a gap frame may jump — it declares the seqs it skips lost — so the frame after it is its seq
    + 1 (immortality §4.3: never splice silently). from_one=False drops the "starts at 1" rule, for a
    run that begins with a replay."""
    problems, last = [], {}
    for i, f in enumerate(frames):
        prev = last.get(f.direction)
        if prev is None and contiguous and from_one and f.seq != 1 and not f.gap:
            problems.append(f"frame {i}: first {f.direction} seq is {f.seq}, not 1")
        if prev is not None:
            if f.seq <= prev:
                problems.append(f"frame {i}: {f.direction} seq {f.seq} after {prev} (repeat or decrease)")
            elif contiguous and f.seq != prev + 1 and not f.gap:
                problems.append(f"frame {i}: {f.direction} seq jumps from {prev} to {f.seq} with no gap frame")
        last[f.direction] = f.seq
    return problems


def stamp_problems(frames: list[DataFrame]) -> list[str]:
    """Per direction, timestamps never decrease (immortality §4.2; decision 5.22): the worker keeps a
    session's output stamps non-decreasing at the source — a clock that steps back stamps the
    previous value, never a smaller one — and the core keeps its own input stamps non-decreasing.
    Across directions nothing is assumed: two clocks stamp them."""
    problems, last = [], {}
    for i, f in enumerate(frames):
        prev = last.get(f.direction)
        if prev is not None and f.timestamp < prev:
            problems.append(f"frame {i}: {f.direction} stamp {f.timestamp} before {prev}: the stamps go back")
        last[f.direction] = max(f.timestamp, prev or 0)
    return problems


def merge_runs(runs: list[tuple[str | None, list[DataFrame]]],
               committed: dict[str | None, int] | None = None) -> tuple[list[DataFrame], list[str]]:
    """One session's taps across core restarts, as its recording must hold them, each seq once.

    Output: a later run's handshake replays from last_committed_seq + 1, so everything an earlier
    run tapped after that point is superseded by the later run's frames — its replay of what the
    ring still held, and its gap frame for what it did not (frames an earlier core tapped but never
    recorded). `committed` gives each run's last_committed_seq from its hello; without one, the cut
    is just before the later run's first output frame. A replayed frame an earlier run also tapped
    must repeat it exactly — same flags, payload and stamp — or the worker replayed something other
    than what it sent; a gap frame both runs tapped repeats exactly too, and only an earlier real
    frame the later run declares lost differs. Input: the core's own counter, never replayed, so
    runs simply follow one another. Returns (the merged frames, output then input, and problems)."""
    committed = committed or {}
    out: list[DataFrame] = []
    inp: list[DataFrame] = []
    problems: list[str] = []
    for run, frames in runs:
        later_out = [f for f in frames if f.direction == "out"]
        inp += [f for f in frames if f.direction == "in"]
        if out and later_out:
            cut = committed.get(run)
            if cut is None:
                cut = later_out[0].seq - 1
            earlier = {f.seq: f for f in out}
            for f in later_out:
                seen = earlier.get(f.seq)
                # The one legitimate difference: a frame an earlier run tapped but never committed,
                # which the ring dropped before this run's handshake, comes back as the gap frame
                # that declares it lost. Two gap frames of one seq are one ring's, so they are equal.
                if seen is not None and seen != f and not (f.gap and not seen.gap):
                    problems.append(f"run {run}: output seq {f.seq} replayed with other bytes or another stamp "
                                    "than the earlier run tapped")
            out = [f for f in out if f.seq <= cut]
        out += later_out
    return out + inp, problems


# ---- JSON forms (the decoded shapes fixtures use) ---------------------------------------------

def frame_json(f: DataFrame) -> dict:
    return {"version": f.version, "flags": f.flags, "direction": f.direction, "stream": f.stream,
            "length": len(f.payload), "seq": f.seq, "timestamp": f.timestamp, "payload_hex": f.payload.hex()}


def frame_from_json(d: dict) -> DataFrame:
    return DataFrame(d["flags"], d["seq"], d["timestamp"], bytes.fromhex(d["payload_hex"]), d["version"], d["stream"])


def record_json(f: RecFrame) -> dict:
    """The decoded form (ttyrec.schema.json): a data frame names its direction, a marker its end.
    A frame with a code this tag reserves has no decoded form, so it keeps its raw code — which
    the schema refuses."""
    d = {"sec": f.sec, "usec": f.usec, "length": len(f.data)}
    if f.direction:
        d["direction"] = f.direction
    elif f.end:
        d["end"] = f.end
    elif f.code == GAP:
        d["marker"] = "gap"
    elif f.code in MARKER_CODES.values():
        d["marker"] = SIDECAR_VALUES[f.code]
    else:
        d["code"] = f.code
    return {**d, "data_hex": f.data.hex()}


def record_from_json(d: dict) -> RecFrame:
    if "marker" in d:
        code = MARKER_CODES[d["marker"]]
    else:
        code = END_CODES[d["end"]] if "end" in d else (IN if d["direction"] == "in" else OUT)
    return RecFrame(d["sec"], d["usec"], code, bytes.fromhex(d["data_hex"]))


def _json_consistency(d: dict, fmt: str) -> list[str]:
    problems = []
    payload = d["payload_hex"] if fmt == "c3" else d["data_hex"]
    if fmt == "c3" and d["direction"] != direction(d["flags"]):
        problems.append(f"direction {d['direction']!r} contradicts flags {d['flags']}")
    if d["length"] != len(payload) // 2:
        problems.append(f"length {d['length']} but {len(payload) // 2} payload bytes")
    return problems


# ---- C5: the rediscovery rule ---------------------------------------------------------------

def rediscover(state: dict) -> dict:
    """What a restarted core dials, given the manifests it finds and what each published socket
    does when dialled ("connectable", "refused", or absent from `sockets`: missing).

    Liveness is the dial, never a pid: the worker and its supervisor run in another container, so
    their pids mean nothing to the core. Every supervisor whose control socket connects is
    addressed (they are a set). For each connection whose {conn_dir}/control.sock connects, the
    core dials it and {conn_dir}/{socket} for each session; a session socket that does not connect
    is a dead session, and a connection whose control socket does not connect is dead with all its
    sessions. Every dead session goes down the dead-peer path — terminated, never a retry against
    a pid (immortality §3: a worker the core cannot reach is a dead worker).
    """
    sockets = state["sockets"]
    out = {"supervisors": [], "dial": [], "dead_supervisors": [], "dead_connections": [], "dead_sessions": []}
    for sup in state["supervisors"]:
        if sockets.get(sup["control_sock"]) == LIVE:
            out["supervisors"].append(sup["control_sock"])
        else:
            out["dead_supervisors"].append(sup["supervisor_id"])
    for worker in state["workers"]:
        for conn in worker["connections"]:
            control = posixpath.join(conn["conn_dir"], "control.sock")
            if sockets.get(control) != LIVE:
                out["dead_connections"].append(conn["conn_id"])
                out["dead_sessions"] += [s["session_id"] for s in conn["sessions"]]
                continue
            live_sessions = []
            for sess in conn["sessions"]:
                path = posixpath.join(conn["conn_dir"], sess["socket"])
                if sockets.get(path) == LIVE:
                    live_sessions.append({"session_id": sess["session_id"], "socket": path})
                else:
                    out["dead_sessions"].append(sess["session_id"])
            out["dial"].append({"worker_id": worker["worker_id"], "conn_id": conn["conn_id"],
                                "control": control, "sessions": live_sessions})
    return out


def rediscover_v02(state: dict) -> dict:
    """The v0.2 rule (worker-manifest.schema.json; transform c5-rediscovery-v0.2), in its order.

    1. The spool first: an end a worker spooled while the core was away is that session's end, even
       when the worker has since exited — so it is never read as E10 worker_lost. An entry for a
       session the core already audited is ignored: a session ends once.
    2. Dial as v0.1 does: a missing or refused socket is a dead worker (decision 5.38), terminated /
       worker_lost, whatever the manifest's version — unless the spool holds that session's end.
    3. For a live peer only, branch on the worker's proto_version: none is a v0.1 worker, whose
       sessions are unreachable / protocol (immortality §6.4: never silently killed); any version
       gets the handshake, a retained ended session included — it is replayed, then its spooled end
       is written.
    Supervisors: every live one is addressed; workers are created only through one whose manifest
    carries the version this core speaks (a v0.1 supervisor's sessions would get no heartbeat).
    """
    sockets = state["sockets"]
    audited = set(state.get("audited", []))
    spool = {e["session_id"]: e for e in state.get("spool", [])}
    ends = {sid for sid in spool if sid not in audited}
    out = {"spool_ignored": sorted(set(spool) & audited), "supervisors": [], "create_through": [],
           "dead_supervisors": [], "handshake": [], "unreachable": [], "dead_connections": [],
           "dead_sessions": [], "spooled_ends": []}
    replayed: set[str] = set()
    for sup in state["supervisors"]:
        if sockets.get(sup["control_sock"]) == LIVE:
            out["supervisors"].append(sup["control_sock"])
            if sup.get("proto_version") == CORE_PROTO:
                out["create_through"].append(sup["control_sock"])
        else:
            out["dead_supervisors"].append(sup["supervisor_id"])
    for worker in state["workers"]:
        version = worker.get("proto_version")
        for conn in worker["connections"]:
            control = posixpath.join(conn["conn_dir"], "control.sock")
            if sockets.get(control) != LIVE:
                out["dead_connections"].append(conn["conn_id"])
                out["dead_sessions"] += [s["session_id"] for s in conn["sessions"] if s["session_id"] not in ends]
                continue
            live_sessions = []
            for sess in conn["sessions"]:
                path = posixpath.join(conn["conn_dir"], sess["socket"])
                if sockets.get(path) == LIVE:
                    live_sessions.append({"session_id": sess["session_id"], "socket": path})
                elif sess["session_id"] not in ends:
                    out["dead_sessions"].append(sess["session_id"])
            entry = {"worker_id": worker["worker_id"], "conn_id": conn["conn_id"], "control": control,
                     "sessions": live_sessions}
            if version is None:
                out["unreachable"].append(entry)
            else:
                out["handshake"].append(entry)
                replayed |= {s["session_id"] for s in live_sessions}
    out["spooled_ends"] = [{"session_id": sid, "after_replay": sid in replayed} for sid in sorted(ends)]
    return out


# ---- C4: the core ↔ VT seam frame codec ---------------------------------------------------

@dataclass(frozen=True)
class VTFrame:
    """One C4 message, decoded from its [length][kind][body] frame."""
    kind: int
    body: bytes

    def json(self) -> dict:
        import json as _json
        if self.kind != VT_KIND_CONTROL:
            raise FormatError(f"kind {self.kind} is not a JSON control object")
        return _json.loads(self.body.decode("utf-8"))


@dataclass(frozen=True)
class VTBlob:
    """The decoded form of a kind-3 body: a request id and the blob's bytes."""
    request_id: str
    data: bytes


@dataclass(frozen=True)
class VTOutputFrame:
    """The decoded form of a kind-2 body: which grid and the tapped C3 frame, verbatim."""
    session_id: str
    frame: DataFrame


def _pack_id(ident: str) -> bytes:
    if not _ID_RE.match(ident):
        raise FormatError(f"id {ident!r} is not common.id: ASCII, first alphanumeric, "
                          f"[A-Za-z0-9._-] up to 128 chars")
    raw = ident.encode("ascii")
    return bytes([len(raw)]) + raw


def _unpack_id(body: bytes, where: int) -> tuple[str, int]:
    if where >= len(body):
        raise FormatError(f"{len(body)} trailing bytes at offset {where}: a torn id_len")
    n = body[where]
    start = where + 1
    if n == 0:
        raise FormatError(f"an empty id at offset {where}: id_len is at least 1")
    if start + n > len(body):
        raise FormatError(f"id at offset {where} declares {n} bytes, {len(body) - start} remain")
    try:
        ident = body[start:start + n].decode("ascii")
    except UnicodeDecodeError:
        raise FormatError(f"id at offset {where} is not ASCII (common.id)") from None
    if not _ID_RE.match(ident):
        raise FormatError(f"id {ident!r} at offset {where} is not common.id: ASCII, first "
                          f"alphanumeric, [A-Za-z0-9._-] up to 128 chars")
    return ident, start + n


def encode_control(msg: dict, *, max_message_bytes: int = VT_MAX_MESSAGE_BYTES) -> bytes:
    """Kind 1: a UTF-8 JSON control object."""
    import json as _json
    body = _json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _frame(VT_KIND_CONTROL, body, max_message_bytes)


def encode_output_frame(session_id: str, frame: DataFrame, *, max_message_bytes: int = VT_MAX_MESSAGE_BYTES) -> bytes:
    """Kind 2: [u8 id_len][session_id][C3 envelope + payload], the tapped frame byte-identical."""
    body = _pack_id(session_id) + ENVELOPE.pack(frame.version, frame.flags, frame.stream,
                                                len(frame.payload), frame.seq, frame.timestamp) + frame.payload
    return _frame(VT_KIND_FRAME, body, max_message_bytes)


def encode_blob(request_id: str, data: bytes, *, max_message_bytes: int = VT_MAX_MESSAGE_BYTES) -> list[bytes]:
    """Kind 3: a blob as one or more frames, each carrying the same request_id, none exceeding
    max_message_bytes. A blob that fits is one frame; a larger one is split in arrival order."""
    prefix = _pack_id(request_id)
    if len(prefix) + len(data) <= max_message_bytes:
        return [_frame(VT_KIND_BLOB, prefix + data, max_message_bytes)]
    room = max_message_bytes - len(prefix)
    if room <= 0:
        raise FormatError(f"max_message_bytes {max_message_bytes} leaves no room for a blob body")
    return [_frame(VT_KIND_BLOB, prefix + data[i:i + room], max_message_bytes)
            for i in range(0, len(data), room)]


def _frame(kind: int, body: bytes, max_message_bytes: int) -> bytes:
    if max_message_bytes > U32_MAX:
        raise FormatError(f"max_message_bytes {max_message_bytes} is above the 32-bit wire length "
                          f"ceiling {U32_MAX}")
    if len(body) > max_message_bytes:
        raise FormatError(f"body is {len(body)} bytes, max_message_bytes is {max_message_bytes}")
    if len(body) > U32_MAX:
        raise FormatError(f"body is {len(body)} bytes, above the 32-bit wire length ceiling {U32_MAX}")
    return VT_HEADER.pack(len(body), kind) + body


def decode_frames_vt(data: bytes, *, max_message_bytes: int = VT_MAX_MESSAGE_BYTES) -> tuple[list[VTFrame], bytes]:
    """Decode a stream of C4 messages: ([messages], the unconsumed trailing bytes).

    A message whose body exceeds max_message_bytes is refused from its length alone (D16), as a
    receiver must, so a peer cannot make the receiver buffer past the stated size. A kind other
    than 1, 2 or 3 is refused from its header (the kinds are fixed, vt-ipc D17)."""
    if max_message_bytes > U32_MAX:
        raise FormatError(f"max_message_bytes {max_message_bytes} is above the 32-bit wire length "
                          f"ceiling {U32_MAX}")
    messages, offset = [], 0
    while offset < len(data):
        if len(data) - offset < VT_HEADER.size:
            break
        length, kind = VT_HEADER.unpack_from(data, offset)
        if length > max_message_bytes:
            raise FormatError(f"length {length} at offset {offset} exceeds max_message_bytes {max_message_bytes}")
        if kind not in (VT_KIND_CONTROL, VT_KIND_FRAME, VT_KIND_BLOB):
            raise FormatError(f"kind {kind} at offset {offset} is not a defined message kind (1, 2 or 3)")
        start = offset + VT_HEADER.size
        if start + length > len(data):
            break
        messages.append(VTFrame(kind, data[start:start + length]))
        offset = start + length
    return messages, data[offset:]


def decode_control(msg: VTFrame) -> dict:
    return msg.json()


def decode_output_frame(msg: VTFrame) -> VTOutputFrame:
    """Kind 2 → (session_id, the C3 frame, verbatim)."""
    if msg.kind != VT_KIND_FRAME:
        raise FormatError(f"kind {msg.kind} is not an output frame")
    session_id, at = _unpack_id(msg.body, 0)
    if len(msg.body) - at < ENVELOPE.size:
        raise FormatError(f"kind 2 at offset 0 is short: {len(msg.body) - at} bytes for a {ENVELOPE.size}-byte envelope")
    version, flags, stream, length, seq, timestamp = ENVELOPE.unpack_from(msg.body, at)
    start = at + ENVELOPE.size
    if start + length > len(msg.body):
        raise FormatError(f"frame at offset {at} declares {length} payload bytes, {len(msg.body) - start} remain")
    return VTOutputFrame(session_id, DataFrame(flags, seq, timestamp, msg.body[start:start + length], version, stream))


def decode_blob(msg: VTFrame) -> VTBlob:
    """Kind 3 → (request_id, blob bytes)."""
    if msg.kind != VT_KIND_BLOB:
        raise FormatError(f"kind {msg.kind} is not a blob")
    request_id, at = _unpack_id(msg.body, 0)
    return VTBlob(request_id, msg.body[at:])


def reassemble_blobs(frames: list[VTFrame]) -> dict[str, bytes]:
    """The kind-3 chunks of one stream, reassembled by request_id in arrival order (D16)."""
    out: dict[str, bytes] = {}
    for msg in frames:
        if msg.kind != VT_KIND_BLOB:
            continue
        chunk = decode_blob(msg)
        out[chunk.request_id] = out.get(chunk.request_id, b"") + chunk.data
    return out


# ---- self-check: kinds bytes and transform, and the layouts -------------------------------

def _read_sibling(fx: Fixture, name: str, ctx: selfcheck.Context) -> bytes | None:
    path = fx.path.parent / name
    if not path.is_file():
        ctx.report.fail(fx.rel, f"byte file {name} is missing")
        return None
    return path.read_bytes()


def _json_frames(fx: Fixture, ctx: selfcheck.Context, frames: list, fmt: str, where: str) -> list | None:
    schema = DATA_FRAME_SCHEMA if fmt == "c3" else RECORDING_SCHEMA
    ok = True
    for i, d in enumerate(frames):
        errors = ctx.registry.validate(d, schema)
        if errors:
            ctx.report.fail(fx.rel, f"{where} frame {i}: {errors[0]}")
            ok = False
        else:
            for problem in _json_consistency(d, fmt):
                ctx.report.fail(fx.rel, f"{where} frame {i}: {problem}")
                ok = False
    if not ok:
        return None
    return [frame_from_json(d) for d in frames] if fmt == "c3" else [record_from_json(d) for d in frames]


def _decode_files(fx: Fixture, ctx: selfcheck.Context, side: dict, fmt: str) -> list | None:
    """Frames from a fixture's byte files: `file` (+ `sidecar` for a C8 recording)."""
    data = _read_sibling(fx, side["file"], ctx)
    if fmt == "c8" and "sidecar" not in side:
        ctx.report.fail(fx.rel, f"{side['file']}: a recording needs its direction `sidecar` file")
        return None
    sidecar = _read_sibling(fx, side["sidecar"], ctx) if fmt == "c8" else b""
    if data is None or sidecar is None:
        return None
    try:
        return decode_frames(data) if fmt == "c3" else decode_recording(data, sidecar)
    except FormatError as exc:
        ctx.report.fail(fx.rel, f"{side['file']}: {exc}")
        return None


def _check_run_bytes(fx: Fixture, ctx: selfcheck.Context, runs: list) -> None:
    """C3 taps across core restarts: each run's file is its frames byte for byte, and the runs merge
    as merge_runs says — the replay overlap taken once, and no jump that is not a gap frame. Each
    frame's envelope rules are its schema's, which every decoded frame here has passed."""
    tapped = []
    for k, item in enumerate(runs):
        where = f"run {item['run']}"
        frames = _json_frames(fx, ctx, item["frames"], "c3", where)
        decoded = _decode_files(fx, ctx, {"file": item["file"]}, "c3")
        if frames is None or decoded is None:
            return
        if decoded != frames or encode_frames(frames) != _read_sibling(fx, item["file"], ctx):
            ctx.report.fail(fx.rel, f"{item['file']} is not the fixture's {where} frames byte for byte")
        for problem in seq_problems(frames, contiguous=True, from_one=k == 0):
            ctx.report.fail(fx.rel, f"{where}: {problem}")
        tapped.append((item["run"], frames))
    if [r for r, _ in tapped] != sorted(r for r, _ in tapped):
        ctx.report.fail(fx.rel, "runs are listed in run order")
    merged, problems = merge_runs(tapped)
    for problem in problems + seq_problems(merged, contiguous=True):
        ctx.report.fail(fx.rel, f"merged: {problem}")


def _vt_frame_from_json(entry: dict) -> DataFrame:
    return DataFrame(entry["flags"], entry["seq"], entry["timestamp"], bytes.fromhex(entry["payload_hex"]))


def _encode_vt_message(entry: dict, max_message_bytes: int) -> list[bytes]:
    kind = entry["kind"]
    if kind == VT_KIND_CONTROL:
        return [encode_control(entry["msg"], max_message_bytes=max_message_bytes)]
    if kind == VT_KIND_FRAME:
        return [encode_output_frame(entry["session_id"], _vt_frame_from_json(entry["frame"]),
                                    max_message_bytes=max_message_bytes)]
    return encode_blob(entry["request_id"], bytes.fromhex(entry["blob_hex"]), max_message_bytes=max_message_bytes)


def _check_vt_bytes(fx: Fixture, ctx: selfcheck.Context, case: dict) -> None:
    """The C4 .bin is ground truth: the fixture's decoded messages encode to it byte for byte, the
    stream decodes back to them, kind 2 carries the C3 envelope verbatim and the blobs reassemble."""
    max_message_bytes = case["max_message_bytes"]
    try:
        expected = b"".join(b for entry in case["messages"]
                            for b in _encode_vt_message(entry, max_message_bytes))
    except (FormatError, KeyError) as exc:
        ctx.report.fail(fx.rel, f"the fixture's messages do not encode: {exc!r}")
        return
    actual = _read_sibling(fx, case["file"], ctx)
    if actual is None:
        return
    if expected != actual:
        ctx.report.fail(fx.rel, f"the fixture's messages do not encode to {case['file']} byte for byte")
        return
    try:
        messages, trailing = decode_frames_vt(actual, max_message_bytes=max_message_bytes)
    except FormatError as exc:
        ctx.report.fail(fx.rel, str(exc))
        return
    if trailing:
        ctx.report.fail(fx.rel, f"{len(trailing)} trailing bytes after the last message")
    decoded = []
    for msg in messages:
        if msg.kind == VT_KIND_CONTROL:
            decoded.append({"kind": msg.kind, "msg": msg.json()})
        elif msg.kind == VT_KIND_FRAME:
            out = decode_output_frame(msg)
            decoded.append({"kind": msg.kind, "session_id": out.session_id,
                            "frame": {"flags": out.frame.flags, "seq": out.frame.seq,
                                      "timestamp": out.frame.timestamp,
                                      "payload_hex": out.frame.payload.hex()}})
        else:
            blob = decode_blob(msg)
            decoded.append({"kind": msg.kind, "request_id": blob.request_id, "blob_hex": blob.data.hex()})
    if decoded != case["messages"]:
        ctx.report.fail(fx.rel, f"{case['file']} does not decode back to the fixture's messages")
    # kind 2 must embed the C3 envelope + payload byte-identical (DRY with ipc-data-frame).
    for entry in case["messages"]:
        if entry["kind"] != VT_KIND_FRAME:
            continue
        frame = _vt_frame_from_json(entry["frame"])
        embedded = ENVELOPE.pack(frame.version, frame.flags, frame.stream, len(frame.payload),
                                 frame.seq, frame.timestamp) + frame.payload
        if encode_output_frame(entry["session_id"], frame) != VT_HEADER.pack(len(_pack_id(entry["session_id"])) + len(embedded),
                                                                            VT_KIND_FRAME) + _pack_id(entry["session_id"]) + embedded:
            ctx.report.fail(fx.rel, "kind 2's body is not [id][C3 envelope + payload] verbatim")
    expected_blobs: dict[str, bytes] = {}
    for e in case["messages"]:
        if e["kind"] == VT_KIND_BLOB:
            expected_blobs[e["request_id"]] = expected_blobs.get(e["request_id"], b"") + bytes.fromhex(e["blob_hex"])
    if reassemble_blobs(messages) != expected_blobs:
        ctx.report.fail(fx.rel, "the kind-3 chunks do not reassemble by request_id")


def check_bytes(fx: Fixture, ctx: selfcheck.Context) -> None:
    case = fx.case
    if "runs" in case:
        _check_run_bytes(fx, ctx, case["runs"])
        return
    if case["format"] == "c4-vt-frames":
        _check_vt_bytes(fx, ctx, case)
        return
    fmt = "c3" if case["format"] == "c3-frames" else "c8"
    frames = _json_frames(fx, ctx, case["frames"], fmt, "decoded")
    decoded = _decode_files(fx, ctx, case, fmt)
    if frames is None or decoded is None:
        return
    if decoded != frames:
        ctx.report.fail(fx.rel, f"{case['file']} does not decode to the fixture's frames")
    if fmt == "c3":
        if encode_frames(frames) != _read_sibling(fx, case["file"], ctx):
            ctx.report.fail(fx.rel, f"the fixture's frames do not encode to {case['file']} byte for byte")
    elif encode_recording(frames) != (_read_sibling(fx, case["file"], ctx), _read_sibling(fx, case["sidecar"], ctx)):
        ctx.report.fail(fx.rel, f"the fixture's frames do not encode to {case['file']} and "
                                f"{case['sidecar']} byte for byte")
    if fmt == "c3":
        for i, f in enumerate(frames):
            for problem in frame_problems(f):
                ctx.report.fail(fx.rel, f"frame {i}: {problem}")
        for problem in seq_problems(frames, contiguous=True):
            ctx.report.fail(fx.rel, problem)
    else:
        for problem in recording_problems(frames) + recording_stamp_problems(frames):
            ctx.report.fail(fx.rel, problem)
    _check_invalid_recordings(fx, ctx, case.get("invalid", []), fmt)


def _check_invalid_recordings(fx: Fixture, ctx: selfcheck.Context, cases: list, fmt: str) -> None:
    """Each invalid recording must be refused, and for the reason the fixture names — a case the
    rules accept, or refuse for another reason, tests nothing."""
    if cases and fmt != "c8":
        ctx.report.fail(fx.rel, "invalid byte cases are for C8 recordings")
        return
    for i, bad in enumerate(cases):
        try:
            frames = decode_recording(bytes.fromhex(bad["ttyrec_hex"]), bytes.fromhex(bad["sidecar_hex"]))
            problems = recording_problems(frames) + recording_stamp_problems(frames)
        except FormatError as exc:
            problems = [str(exc)]
        if not problems:
            ctx.report.fail(fx.rel, f"invalid case {i} ({bad.get('note', bad['problem'])}) is a valid recording: "
                                    "it tests nothing")
        elif not any(bad["problem"] in p for p in problems):
            ctx.report.fail(fx.rel, f"invalid case {i} is refused, but not for {bad['problem']!r}: {problems[0]}")


def _check_compare(fx: Fixture, ctx: selfcheck.Context, case: dict) -> None:
    """c8-compare: two recordings of one session (an uninterrupted run and a restarted one, say),
    and what the comparator says of them."""
    a = _side(fx, ctx, case["input"].get("a", {}), "c8", "input a")
    b = _side(fx, ctx, case["input"].get("b", {}), "c8", "input b")
    if a is None or b is None:
        return
    for side, frames in (("a", a), ("b", b)):
        for problem in recording_problems(frames):
            ctx.report.fail(fx.rel, f"input {side} {problem}")
    derived = compare_recordings(a, b)
    if derived != case["output"]:
        ctx.report.fail(fx.rel, f"the comparator gives {derived}, the fixture says {case['output']}")


def _side(fx: Fixture, ctx: selfcheck.Context, side: dict, fmt: str, where: str) -> list | None:
    if "file" in side:
        return _decode_files(fx, ctx, side, fmt)
    if "frames" in side:
        return _json_frames(fx, ctx, side["frames"], fmt, where)
    ctx.report.fail(fx.rel, f"{where} needs `file` or `frames`")
    return None


def check_transform(fx: Fixture, ctx: selfcheck.Context) -> None:
    case = fx.case
    if case["transform"] in _inventory.INVENTORY_TRANSFORMS:
        _inventory.check_inventory_transform(fx, ctx, case)
        return
    if case["transform"] == "c8-compare":
        _check_compare(fx, ctx, case)
        return
    if case["transform"] == "c3-to-c8":
        frames = _side(fx, ctx, case["input"], "c3", "input")
        expected = _side(fx, ctx, case["output"], "c8", "output")
        end = case["input"].get("end")
        if end is not None and not (isinstance(end, dict) and {"end_state", "timestamp"} <= set(end)
                                    and set(end) <= {"end_state", "timestamp", "spool"}
                                    and end["end_state"] in END_CODES
                                    and isinstance(end["timestamp"], int) and end["timestamp"] >= 1):
            ctx.report.fail(fx.rel, "input end is {end_state: killed|terminated, timestamp: ns, spool?}")
            return
        if end is not None and "spool" in end:
            for err in ctx.registry.validate({**end["spool"], "session_id": "x", "conn_id": "x", "end_state": "killed",
                                              "end_reason": "orphan", "ended_at": "2026-01-01T00:00:00Z"},
                                             "schemas/spool-entry.schema.json")[:1]:
                ctx.report.fail(fx.rel, f"input end spool: {err}")
        if frames is None or expected is None:
            return
        for problem in recording_problems(expected):
            ctx.report.fail(fx.rel, f"output {problem}")
        try:
            derived = derive_recording(frames, end, case["input"].get("handshakes", []), case["input"].get("committed"))
        except (FormatError, KeyError, TypeError) as exc:
            ctx.report.fail(fx.rel, f"input: {exc}")
            return
        if derived != expected:
            for i, (got, want) in enumerate(zip(derived, expected)):
                if got != want:
                    ctx.report.fail(fx.rel, f"frame {i}: the derivation gives {record_json(got)}, "
                                            f"the fixture says {record_json(want)}")
                    break
            else:
                ctx.report.fail(fx.rel, f"{len(derived)} frames derived, the fixture lists {len(expected)}")
        return
    state = case["input"]
    v02 = case["transform"] == "c5-rediscovery-v0.2"
    inputs = [("supervisors", "schemas/supervisor-manifest.schema.json"),
              ("workers", "schemas/worker-manifest.schema.json")]
    if v02:
        inputs.append(("spool", "schemas/spool-entry.schema.json"))
    for key, schema in inputs:
        for i, doc in enumerate(state.get(key, [])):
            for err in ctx.registry.validate(doc, schema)[:1]:
                ctx.report.fail(fx.rel, f"input {key}[{i}]: {err}")
    if not isinstance(state.get("sockets"), dict) or set(state["sockets"].values()) - {LIVE, "refused"}:
        ctx.report.fail(fx.rel, "input sockets maps a path to 'connectable' or 'refused' (absent means missing)")
        return
    try:
        derived = rediscover_v02(state) if v02 else rediscover(state)
    except (KeyError, TypeError) as exc:
        ctx.report.fail(fx.rel, f"input is not a rediscovery state: {exc!r}")
        return
    if derived != case["output"]:
        ctx.report.fail(fx.rel, f"the rediscovery rule gives {derived}, the fixture says {case['output']}")


_WIDTH = {"u8": 1, "u16": 2, "u32": 4, "u64": 8}
_CODE = {"u8": "B", "u16": "H", "u32": "I", "u64": "Q"}


def check_layouts(ctx: selfcheck.Context) -> None:
    """The codecs' struct layouts equal the schemas' published x-binary-layout, and the sidecar's
    published values equal the codec's."""
    for schema, codec, names, order in ((DATA_FRAME_SCHEMA, ENVELOPE, ENVELOPE_FIELDS, ">"),
                                        (RECORDING_SCHEMA, RECORD, RECORD_FIELDS, "<"),
                                        (VT_IPC_SCHEMA, VT_HEADER, VT_HEADER_FIELDS, ">")):
        doc = ctx.registry.docs.get(schema)
        if doc is None:
            continue
        layout = doc.get("x-binary-layout", {})
        fields = layout.get("fields", [])
        published = order + "".join(_CODE.get(f.get("type"), "?") for f in fields)
        offsets, at = [], 0
        for f in fields:
            offsets.append(at)
            at += _WIDTH.get(f.get("type"), 0)
        problems = []
        if published != codec.format:
            problems.append(f"published layout {published} but the codec packs {codec.format}")
        if tuple(f.get("name") for f in fields) != names:
            problems.append(f"published fields {[f.get('name') for f in fields]}, the codec's are {list(names)}")
        if [f.get("offset") for f in fields] != offsets:
            problems.append("published offsets do not follow from the field widths")
        if layout.get("header_bytes") != codec.size:
            problems.append(f"header_bytes {layout.get('header_bytes')} but the codec's header is {codec.size}")
        expected_order = "big-endian" if order == ">" else "little-endian"
        if not str(layout.get("byte_order", "")).startswith(expected_order):
            problems.append(f"byte_order is not {expected_order}")
        if schema == RECORDING_SCHEMA:
            sidecar = doc.get("x-sidecar", {})
            if sidecar.get("values") != {str(k): v for k, v in SIDECAR_VALUES.items()}:
                problems.append(f"x-sidecar values {sidecar.get('values')} are not the codec's {SIDECAR_VALUES}")
            if sidecar.get("reserved") != list(RESERVED_CODES):
                problems.append(f"x-sidecar reserved {sidecar.get('reserved')} are not the codec's {list(RESERVED_CODES)}")
            problems += _terminal_line_problems(doc.get("x-terminal-incomplete-line", {}))
            problems += _gap_line_problems(doc.get("x-gap-line", {}))
            problems += _broker_line_problems(doc.get("x-broker-line", {}))
            problems += _login_line_problems(doc.get("x-login-line", {}))
            problems += _own_prompt_line_problems(doc.get("x-own-prompt-line", {}))
        for problem in problems:
            ctx.report.fail(schema, f"x-binary-layout: {problem}")


def _vt_layout_problems(published: dict, ctx: selfcheck.Context) -> list[str]:
    """The C4 codec embodies the published x-binary-layout: kinds 1/2/3 exist, the header is
    big-endian [u32 length][u8 kind], kind 2 embeds a C3 frame byte-identical, and a body above
    max_message_bytes is refused."""
    problems: list[str] = []
    kinds = {k.get("kind") for k in published.get("kinds", [])}
    if kinds != {VT_KIND_CONTROL, VT_KIND_FRAME, VT_KIND_BLOB}:
        problems.append(f"published kinds {sorted(kinds)} are not the codec's "
                        f"{[VT_KIND_CONTROL, VT_KIND_FRAME, VT_KIND_BLOB]}")
    if VT_HEADER.format != ">IB":
        problems.append(f"the codec's header packs {VT_HEADER.format}, not the published big-endian [u32][u8]")
    c3 = DataFrame(0, 7, 1789000000123456789, b"login: ")
    encoded = encode_output_frame("sess-1", c3)
    length, kind = VT_HEADER.unpack_from(encoded, 0)
    if length != len(encoded) - VT_HEADER.size or kind != VT_KIND_FRAME:
        problems.append("kind 2 does not carry [u32 length][u8 kind] with length counting the body")
    decoded = decode_output_frame(VTFrame(VT_KIND_FRAME, encoded[VT_HEADER.size:]))
    if decoded.session_id != "sess-1" or decoded.frame != c3:
        problems.append("kind 2 does not round-trip its session id and C3 frame")
    embedded = ENVELOPE.pack(c3.version, c3.flags, c3.stream, len(c3.payload), c3.seq, c3.timestamp) + c3.payload
    body = encoded[VT_HEADER.size:]
    if not body.endswith(embedded):
        problems.append("kind 2's body does not embed the C3 envelope + payload byte-identical")
    blob = b"x" * 16
    chunks = encode_blob("req-1", blob, max_message_bytes=1 + 6 + 4)
    if len(chunks) < 2 or reassemble_blobs(decode_frames_vt(b"".join(chunks))[0]) != {"req-1": blob}:
        problems.append("a blob above max_message_bytes is not chunked by request_id and reassembled")
    try:
        decode_frames_vt(VT_HEADER.pack(64, VT_KIND_BLOB) + b"x" * 64, max_message_bytes=16)
        problems.append("a body above max_message_bytes is accepted, not refused from its length alone")
    except FormatError:
        pass
    return problems


def check_c4_layout(ctx: selfcheck.Context) -> None:
    doc = ctx.registry.docs.get(VT_IPC_SCHEMA)
    if doc is None:
        return
    for problem in _vt_layout_problems(doc.get("x-binary-layout", {}), ctx):
        ctx.report.fail(VT_IPC_SCHEMA, f"x-binary-layout: {problem}")



def _terminal_line_problems(published: dict) -> list[str]:
    """Keep the published observation grammar coupled to the codec's exact bytes."""
    try:
        pattern = selfcheck.compile_published(published.get("pattern", ""))
    except (ValueError, TypeError) as exc:
        return [f"x-terminal-incomplete-line pattern does not compile: {exc}"]
    problems = []
    for reason in TERMINAL_REASONS:
        line = terminal_incomplete_line(42, 8192, reason, 1794708000123456789)
        if not pattern.search(line.decode("ascii")):
            problems.append("terminal marker codec line differs from published pattern")
        for bad in (line.replace(b"seq 42", b"seq 042"), line.replace(reason.encode(), b"unknown"),
                    line[:-1], b"x" + line):
            if pattern.search(bad.decode("ascii")) or parse_terminal_incomplete_line(bad) is not None:
                problems.append("terminal marker accepts a line outside the published grammar")
    return problems

def _gap_line_problems(published: dict) -> list[str]:
    """The codec writes and parses exactly the line x-gap-line publishes: its own output matches the
    published pattern and parses back, and lines that break the format match neither."""
    try:
        pattern = selfcheck.compile_published(published.get("pattern", ""))
    except (ValueError, TypeError) as exc:
        return [f"x-gap-line pattern does not compile: {exc}"]
    line = gap_line(4096, (1789000000, 123456), (1789000009, 5))
    problems = []
    if not pattern.search(line.decode("ascii")) or parse_gap_line(line) != (4096, (1789000000, 123456), (1789000009, 5)):
        problems.append("x-gap-line: the codec's own line does not match the published pattern, or does not parse back")
    for bad in (line.replace(b"4096", b"0"), line.rstrip(b"\r\n"), line.replace(b".123456", b".123"), b"x" + line):
        if pattern.search(bad.decode("ascii")) or parse_gap_line(bad) is not None:
            problems.append(f"x-gap-line: {bad!r} is accepted, but it breaks the format")
    return problems


def _broker_line_problems(published: dict) -> list[str]:
    """The codec writes and parses exactly the line x-broker-line publishes: its own output matches
    the published pattern and parses back, and lines that break the format match neither."""
    try:
        pattern = selfcheck.compile_published(published.get("pattern", ""))
    except (ValueError, TypeError) as exc:
        return [f"x-broker-line pattern does not compile: {exc}"]
    line = broker_line("ctrl_l")
    problems = []
    if not pattern.search(line.decode("ascii")) or parse_broker_line(line) != "ctrl_l":
        problems.append("x-broker-line: the codec's own line does not match the published pattern, or does not parse back")
    for bad in (line.replace(b"ctrl_l", b"ctrl-L"), line.replace(b"-", b"\xe2\x80\x94"), line.rstrip(b"\r\n"), b"x" + line):
        if pattern.search(bad.decode("latin-1")) or parse_broker_line(bad) is not None:
            problems.append(f"x-broker-line: {bad!r} is accepted, but it breaks the format")
    return problems


def _login_line_problems(published: dict) -> list[str]:
    """The codec writes and parses exactly the line x-login-line publishes: its own lines match the published
    pattern and parse back to the value, and lines that break the escape rule match neither."""
    try:
        pattern = selfcheck.compile_published(published.get("pattern", ""))
    except (ValueError, TypeError) as exc:
        return [f"x-login-line pattern does not compile: {exc}"]
    problems = []
    value = "pa\\ss\x1b[31m\r\nw\x85\x7f\u00e9\u4e2d"
    for code, name in ((LOGIN_PROMPT, "prompt"), (LOGIN_ANSWER, "answer")):
        line = login_line(code, value)
        if not pattern.search(line.decode("utf-8")) or parse_login_line(code, line) != value:
            problems.append(f"x-login-line: the codec's own {name} line does not match the published pattern, "
                            "or does not parse back")
        if any(b in line[len(LOGIN_TAGS[code]):-2] for b in (b"\x1b", b"\r", b"\n")):
            problems.append(f"x-login-line: the codec's {name} line holds a raw control: it must be one row")
    tag = LOGIN_TAGS[LOGIN_ANSWER]
    for body in (b"a\\b", b"\\x41", b"\\x1B", b"\\q", b"\\x1", b"\x1b", b"a\rb", b"\xc2\x85", b"\xff"):
        bad = tag + body + b"\r\n"
        try:
            matched = pattern.search(bad.decode("utf-8"))  # the pattern reads decoded text; not UTF-8 never gets there
        except UnicodeDecodeError:
            matched = None
        if parse_login_line(LOGIN_ANSWER, bad) is not None or matched:
            problems.append(f"x-login-line: {bad!r} is accepted, but it breaks the escape rule")
    for bad in (tag + b"a", tag + b"a\r\n\r\n", b"x" + tag + b"a\r\n", LOGIN_TAGS[LOGIN_PROMPT] + b"a\r\n"):
        if parse_login_line(LOGIN_ANSWER, bad) is not None:
            problems.append(f"x-login-line: {bad!r} is accepted as a code-7 line, but it breaks the line")
    return problems


def _own_prompt_line_problems(published: dict) -> list[str]:
    """The codec writes and parses exactly the line x-own-prompt-line publishes: its own two lines match the
    published pattern and parse back, the published texts are the codec's, and other lines match neither."""
    try:
        pattern = selfcheck.compile_published(published.get("pattern", ""))
    except (ValueError, TypeError) as exc:
        return [f"x-own-prompt-line pattern does not compile: {exc}"]
    problems = []
    if published.get("texts") != list(OWN_PROMPT_TEXTS):
        problems.append(f"x-own-prompt-line texts {published.get('texts')} are not the codec's {list(OWN_PROMPT_TEXTS)}")
    for text in OWN_PROMPT_TEXTS:
        line = own_prompt_line(text)
        if not pattern.search(line.decode("ascii")) or parse_own_prompt_line(line) != text:
            problems.append(f"x-own-prompt-line: the codec's own line for {text!r} does not match the published "
                            "pattern, or does not parse back")
    for bad in (OWN_PROMPT_TAG + b"Passphrase:\r\n", OWN_PROMPT_TAG + b"login as: \r\n", OWN_PROMPT_TAG + b"\r\n",
                LOGIN_TAGS[LOGIN_ANSWER] + b"login as:\r\n", OWN_PROMPT_TAG + b"login as:", b"x" + own_prompt_line("login as:")):
        if pattern.search(bad.decode("ascii")) or parse_own_prompt_line(bad) is not None:
            problems.append(f"x-own-prompt-line: {bad!r} is accepted, but it is not one of the two lines")
    return problems


selfcheck.KIND_CHECKS["bytes"] = check_bytes
selfcheck.KIND_CHECKS["transform"] = check_transform
selfcheck.TREE_CHECKS.append(check_layouts)
selfcheck.TREE_CHECKS.append(check_c4_layout)


def load_frames(path: Path) -> list[DataFrame]:
    return decode_frames(path.read_bytes())


def load_recording(ttyrec: Path, sidecar: Path) -> list[RecFrame]:
    return decode_recording(ttyrec.read_bytes(), sidecar.read_bytes())
