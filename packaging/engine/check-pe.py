#!/usr/bin/env python3
"""Check a Windows x64 DLL without loading or executing it. AI-Model: codex."""

import struct
import sys
from pathlib import Path


def check_pe(path: Path) -> None:
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b"MZ":
        raise ValueError("missing DOS header")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if offset < 64 or offset + 24 > len(data):
        raise ValueError("invalid PE header offset")
    if data[offset : offset + 4] != b"PE\0\0":
        raise ValueError("missing PE signature")
    machine = struct.unpack_from("<H", data, offset + 4)[0]
    if machine != 0x8664:
        raise ValueError(f"expected machine 0x8664, got {machine:#06x}")
    characteristics = struct.unpack_from("<H", data, offset + 22)[0]
    if not characteristics & 0x2000:
        raise ValueError("PE file is not a DLL")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 -I check-pe.py DLL")
    try:
        check_pe(Path(sys.argv[1]))
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
    print("PE x64 DLL: OK (machine 0x8664)")
