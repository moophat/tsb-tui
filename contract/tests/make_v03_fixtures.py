#!/usr/bin/env python3
"""Generate the contract-v0.3 C8 `.bin` files (kept in tests/, not shipped).

Run from the contract directory with the test venv's Python:

    python3 tests/make_v03_fixtures.py

It writes the byte files under fixtures/C8/ that the sibling .json fixtures name. The fixtures
themselves stay hand-written; this script only produces the bytes they are the ground truth for.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTRACT = HERE.parent
sys.path.insert(0, str(CONTRACT))

from tsbc import _binfmt as bf

FIXTURES = CONTRACT / "fixtures" / "C8"


def write(name: str, frames: list[bf.RecFrame]) -> None:
    ttyrec, sidecar = bf.encode_recording(frames)
    (FIXTURES / f"{name}.ttyrec.bin").write_bytes(ttyrec)
    (FIXTURES / f"{name}.dir.bin").write_bytes(sidecar)
    print(f"{name}: {len(frames)} frames, {len(ttyrec)} ttyrec bytes, {len(sidecar)} sidecar bytes")


def main() -> None:
    # broker-marker: a live session; a broker marker for each choice, pure ASCII, a hyphen.
    write("broker-marker", [
        bf.RecFrame(1789000000, 123456, bf.OUT, b"$ "),
        bf.RecFrame(1789000001, 0, bf.BROKER, bf.broker_line("wiggle")),
        bf.RecFrame(1789000002, 0, bf.BROKER, bf.broker_line("continue")),
        bf.RecFrame(1789000003, 0, bf.BROKER, bf.broker_line("fresh_session")),
    ])
    # broker-marker-ctrl-l: a declared gap, the ctrl_l marker, then the 0x0C input frame the core
    # recorded before it sent it (the marker precedes the input).
    write("broker-marker-ctrl-l", [
        bf.RecFrame(1789000000, 123456, bf.OUT, b"$ "),
        bf.RecFrame(1789000001, 0, bf.BROKER, bf.broker_line("ctrl_l")),
        bf.RecFrame(1789000001, 1000, bf.IN, b"\x0c"),
    ])
    # recordings-broker-marker: a collected forced gap plus the ctrl_l answer — the gap marker, the
    # broker marker, then the 0x0C input frame; the artifacts check grades it against a real run.
    write("recordings-broker-marker", [
        bf.RecFrame(1789000000, 123456, bf.OUT, b"$ "),
        bf.RecFrame(1789000001, 500000, bf.GAP, bf.gap_line(8, (1789000000, 123456), (1789000001, 500000))),
        bf.RecFrame(1789000001, 500000, bf.BROKER, bf.broker_line("ctrl_l")),
        bf.RecFrame(1789000001, 501000, bf.IN, b"\x0c"),
    ])


if __name__ == "__main__":
    main()