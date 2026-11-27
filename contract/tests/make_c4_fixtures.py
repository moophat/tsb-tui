#!/usr/bin/env python3
"""Generate the contract-v0.3 C4 fixtures and their `.bin` files (kept in tests/, not shipped).

Run from the contract directory with the test venv's Python:

    python3 tests/make_c4_fixtures.py

It writes two groups under fixtures/C4/:

- `vt-frame-layout` — the framing fixture: the decoded messages and the bytes cannot drift, because
  the .bin is the ground truth and the JSON carries the same messages.
- the seven golden `.bin` inputs (`golden-*`), each a bytes fixture whose .bin is the raw output
  the case feeds a fresh parser, with resize events (kind-1 `resize` control messages) between
  output chunks where the case needs them. No expected screen is stored: the U4 round trip derives it
  by feeding this input to a fresh parser and comparing it with the parser's view of the dump.

The C4 sequence fixtures live in their own job and are not written here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTRACT = HERE.parent
sys.path.insert(0, str(CONTRACT))

from tsbc import _binfmt as bf

FIXTURES = CONTRACT / "fixtures" / "C4"
MAX_MESSAGE_BYTES = 200
BLOB = bytes(range(256))
GAP_TS = 1789000000123456789
SESSION = "sess-1"

# ---- message builders ------------------------------------------------------------------------


def _control(msg: dict) -> dict:
    return {"kind": bf.VT_KIND_CONTROL, "msg": msg}


def _out(seq: int, payload: bytes) -> dict:
    return {"kind": bf.VT_KIND_FRAME, "session_id": SESSION,
            "frame": {"flags": 0, "seq": seq, "timestamp": GAP_TS, "payload_hex": payload.hex()}}


def _resize(cols: int, rows: int) -> dict:
    return _control({"cmd": "resize", "session_id": SESSION, "cols": cols, "rows": rows})


def _chunk_bodies(request_id: str, data: bytes, max_bytes: int) -> list[bytes]:
    return [chunk[bf.VT_HEADER.size:][len(request_id) + 1:]
            for chunk in bf.encode_blob(request_id, data, max_message_bytes=max_bytes)]


def build_messages() -> list[dict]:
    """The decoded form of every `vt-frame-layout` message, in stream order. Kind 2 embeds a C3
    frame; kind 3 is a blob above max_message_bytes, listed as the chunks it is split into."""
    messages: list[dict] = [
        _control({"cmd": "hello", "proto_version": "0.3"}),
        _out(1, b"login: "),
        {"kind": bf.VT_KIND_FRAME, "session_id": SESSION,
         "frame": {"flags": 2, "seq": 1, "timestamp": GAP_TS, "payload_hex": ""}},
    ]
    for chunk in _chunk_bodies("req-1", BLOB, MAX_MESSAGE_BYTES):
        messages.append({"kind": bf.VT_KIND_BLOB, "request_id": "req-1", "blob_hex": chunk.hex()})
    messages.append(_control({
        "event": "hello_ack", "proto_version": "0.3",
        "engine": {"name": "libghostty-vt", "version": "1.0"},
        "grids": [{"session_id": SESSION, "through_seq": 1, "cols": 80, "rows": 24}]}))
    return messages


def encode_message(m: dict, max_bytes: int) -> bytes:
    kind = m["kind"]
    if kind == bf.VT_KIND_CONTROL:
        return bf.encode_control(m["msg"], max_message_bytes=max_bytes)
    if kind == bf.VT_KIND_FRAME:
        f = m["frame"]
        frame = bf.DataFrame(f["flags"], f["seq"], f["timestamp"], bytes.fromhex(f["payload_hex"]),
                             f.get("version", 1), f.get("stream", 0))
        return bf.encode_output_frame(m["session_id"], frame, max_message_bytes=max_bytes)
    if kind == bf.VT_KIND_BLOB:
        return bf.encode_blob(m["request_id"], bytes.fromhex(m["blob_hex"]), max_message_bytes=max_bytes)[0]
    raise ValueError(f"unknown kind {kind}")


# ---- golden inputs ---------------------------------------------------------------------------

def _golden_messages(chunks: list[bytes | tuple[int, int]]) -> list[dict]:
    """Output chunks and resize events in stream order, as output frames seq 1..n with the resize
    events between them."""
    messages: list[dict] = []
    seq = 0
    for chunk in chunks:
        if isinstance(chunk, tuple):
            messages.append(_resize(*chunk))
        else:
            seq += 1
            messages.append(_out(seq, chunk))
    return messages


GOLDEN = [
     ("golden-alt-screen",
      [b"$ vim\r\n", b"\x1b[?1049h\x1b[H\x1b[2JALT SCREEN\r\n", b"\x1b[?1049l$ "],
      ("asserts the alternate screen while CSI ?1049h is in effect and the primary screen after "
       "CSI ?1049l, both screens' lines in the dump's emission order")),
    ("golden-wide-chars",
      [b"$ echo \xe4\xb8\x96\xe7\x95\x8c\r\n", b"\xe4\xb8\x96\xe7\x95\x8c\r\n"],
      ("asserts a wide glyph occupies its cell once — its continuation not repeated as a second glyph — "
       "at the cursor the output leaves")),
    ("golden-resize-mid-stream",
      [b"one two three four\r\n", (100, 30), b"five six seven eight\r\n"],
      "asserts the grid is 100x30 for the output after the resize event, the earlier lines from before it"),
    ("golden-reflow",
      [b"the quick brown fox jumps over the lazy dog and keeps on running past the edge\r\n", (40, 12)],
      "asserts the same logical text reflows at 40x12 after the resize event"),
    ("golden-sgr",
      [b"\x1b[1;32mgreen bold\x1b[0m plain\r\n"],
      "asserts the SGR run bold+green at row 0 columns 0..9, plain after CSI 0m"),
    ("golden-input-modes",
      [b"\x1b[?1h\x1b[?2004h\x1b[?1000h\x1b[?1006h\x1b[?1004h\x1b=\x1b[?6h\x1b[?7h\x1b[4h$ "],
      ("asserts decckm true, bracketed_paste true, mouse mode 1000 encoding sgr, focus true, keypad "
       "application, origin true, autowrap true and insert true")),
    ("golden-mode-reset",
      [b"\x1b[?1h\x1b[?2004h\x1b[?1000h\x1b[?1004h\x1b[?6h\x1b[4h$ ",
       b"\x1b[?1l\x1b[?2004l\x1b[?1000l\x1b[?1004l\x1b[?6l\x1b[4l$ "],
      ("asserts every carried mode back to its reset state after the second chunk, the full state the "
       "dump must emit")),
]

_golden_data = [(name, chunks, _golden_messages(chunks), asserts) for name, chunks, asserts in GOLDEN]


def _write_fixture(name: str, messages: list[dict], description: str, corpus: list[str],
                   graded_by: list[dict]) -> None:
    encoded = b"".join(encode_message(m, MAX_MESSAGE_BYTES) for m in messages)
    (FIXTURES / f"{name}.bin").write_bytes(encoded)
    fixture = {
        "seam": "C4",
        "name": name,
        "mark": {"required-green": "e2"},
        "kind": "bytes",
        "grade": "reference",
        "graded_by": graded_by,
        "description": description,
        "corpus": corpus,
        "case": {
            "format": "c4-vt-frames",
            "file": f"{name}.bin",
            "max_message_bytes": MAX_MESSAGE_BYTES,
            "messages": messages,
        },
    }
    (FIXTURES / f"{name}.json").write_text(json.dumps(fixture, indent=2) + "\n")
    print(f"{name}: {len(messages)} messages, {len(encoded)} bytes")


FRAME_LAYOUT_DESCRIPTION = (
    "The C4 core \u2194 VT seam's framing, byte by byte: [u32 BE length][u8 kind][body], big-endian "
    "throughout as the embedded C3 envelope. Kind 1 is a JSON control object; kind 2 is "
    "[u8 id_len][session_id][C3 data-frame envelope + payload], the tapped frame verbatim, gap bit "
    "included; kind 3 is [u8 id_len][request_id][bytes], a blob above max_message_bytes split across "
    "messages and reassembled by request_id. The .bin file is the ground truth: a decoder must "
    "reproduce these messages from it and an encoder these bytes from them."
)


def golden_description(asserts: str, has_resize: bool) -> str:
    resize = (" (resize events \u2014 kind-1 `resize` control messages \u2014 between output chunks, F14d)"
              if has_resize else "")
    return (
        "A golden C4 screen case, graded by the U4 libghostty-vt round trip: the sibling .bin is "
        "raw output fed to a fresh parser" + resize + "; the dump the worker then produces is fed to "
        "a second parser and must show the same screen and modes. **No expected screen is stored** "
        "(F14: expected screens are never hand-written, they come from the round trip at a pinned "
        "engine); the fixture " + asserts + "."
    )


def main() -> None:
    _write_fixture(
        "vt-frame-layout", build_messages(), FRAME_LAYOUT_DESCRIPTION,
        ["plan.md C4 decision 2", "plan.md C4 decision 3", "plan.md C4 decision 10",
         "ipc-data-frame.schema.json x-binary-layout"],
        [{"unit": "U4", "test": "owed by the E2 vt plan"},
         {"unit": "U2", "test": "owed by the E2 core plan"}],
    )
    for name, chunks, messages, asserts in _golden_data:
        has_resize = any(isinstance(c, tuple) for c in chunks)
        corpus = ["plan.md C4 decision 6", "plan.md C4 decision 9", "vt-screen.schema.json"]
        if has_resize:
            corpus.append("plan.md C4 6 (F14d)")
        _write_fixture(name, messages, golden_description(asserts, has_resize), corpus,
                       [{"unit": "U4", "test": "owed by the E2 vt plan"}])


if __name__ == "__main__":
    main()