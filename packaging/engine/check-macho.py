#!/usr/bin/env python3
"""Check a thin 64-bit Mach-O dylib without loading it. AI-Model: codex."""

import struct
import sys
from pathlib import Path


def check(path, expected):
    d = Path(path).read_bytes()
    if len(d) < 32 or d[:4] != b"\xcf\xfa\xed\xfe":
        raise ValueError("expected 64-bit Mach-O (little-endian)")
    _magic, cpu, sub, filetype, ncmds, sz, _flags, _res = struct.unpack_from(
        "<IiiIIIII", d, 0
    )
    if filetype != 6:
        raise ValueError("file is not MH_DYLIB")
    want = {"arm64": 0x100000C, "x86_64": 0x1000007}[expected]
    if cpu != want:
        raise ValueError(f"expected cputype {expected}, got {cpu:#x}")
    subtype_base = sub & 0x00FFFFFF
    subtype_want = {"arm64": 0, "x86_64": 3}[expected]
    if subtype_base != subtype_want:
        raise ValueError(f"unexpected CPU subtype {sub:#x} for {expected}")
    if 32 + sz > len(d):
        raise ValueError("truncated load command region")
    off = 32
    build = None
    sig = False
    ident = []
    loads = []
    for _ in range(ncmds):
        if off + 8 > len(d):
            raise ValueError("truncated load command")
        cmd, cmdsz = struct.unpack_from("<II", d, off)
        if cmdsz < 8 or off + cmdsz > 32 + sz:
            raise ValueError("invalid load command")
        if cmd == 0x32 and cmdsz >= 24:
            platform, version = struct.unpack_from("<II", d, off + 8)[0:2]
            build = (platform, version)
        if cmd in (0xD, 0xC):
            if cmdsz < 24:
                raise ValueError("truncated dylib command")
            nameoff = struct.unpack_from("<I", d, off + 8)[0]
            if not 24 <= nameoff < cmdsz:
                raise ValueError("invalid dylib name offset")
            name = (
                d[off + nameoff : off + cmdsz]
                .split(b"\0", 1)[0]
                .decode(errors="replace")
            )
        if cmd == 0xD:
            ident.append(name)
        if cmd == 0xC:
            loads.append(name)
        if cmd == 0x1D:
            if cmdsz < 16:
                raise ValueError("truncated code signature command")
            dataoff, datasize = struct.unpack_from("<II", d, off + 8)
            sig = sig or bool(dataoff and datasize and dataoff + datasize <= len(d))
        off += cmdsz
    if off != 32 + sz:
        raise ValueError("load command size mismatch")
    if not build or build[0] != 1:
        raise ValueError("missing LC_BUILD_VERSION for macOS")
    minos = f"{build[1] >> 16}.{(build[1] >> 8) & 255}.{build[1] & 255}"
    if expected == "arm64":
        if not sig:
            raise ValueError("arm64 dylib signature is missing or empty")
    if ident != ["@rpath/libghostty-vt.dylib"]:
        raise ValueError(f"wrong install name {ident!r}; expected @rpath/libghostty-vt.dylib")
    if loads != ["/usr/lib/libSystem.B.dylib"]:
        extra = repr(loads)
        raise ValueError(f"extra or wrong dependency: {extra}")
    print(
        f"Mach-O {expected}: cputype {cpu:#010x}, minos {minos}, signature {'present' if sig else 'absent'}"
    )
    for x in ident:
        print(f"LC_ID_DYLIB {x}")
    for x in loads:
        print(f"LC_LOAD_DYLIB {x}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[2] not in ("arm64", "x86_64"):
        sys.exit("usage: check-macho.py PATH arm64|x86_64")
    try:
        check(sys.argv[1], sys.argv[2])
    except (OSError, ValueError) as e:
        sys.exit(str(e))
