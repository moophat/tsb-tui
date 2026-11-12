#!/usr/bin/env python3
"""Check a thin 64-bit Mach-O dylib without loading it. AI-Model: codex."""

import struct
import sys
from pathlib import Path


def check(path, expected, executable=False):
    d = Path(path).read_bytes()
    if len(d) < 32 or d[:4] != b"\xcf\xfa\xed\xfe":
        raise ValueError("expected 64-bit Mach-O (little-endian)")
    _magic, cpu, sub, filetype, ncmds, sz, _flags, _res = struct.unpack_from(
        "<IiiIIIII", d, 0
    )
    if filetype != (2 if executable else 6):
        raise ValueError("file is not MH_EXECUTE" if executable else "file is not MH_DYLIB")
    want = {"arm64": 0x100000C, "x86_64": 0x1000007}[expected]
    if cpu != want:
        raise ValueError(f"expected cputype {expected}, got {cpu:#x}")
    subtype_base = sub & 0x00FFFFFF
    subtype_want = {"arm64": 0, "x86_64": 3}[expected]
    if subtype_base != subtype_want:
        raise ValueError(f"unexpected CPU subtype {sub:#x} for {expected}")
    if executable:
        print(f"Mach-O executable {expected}: cputype {cpu:#010x}")
        return
    if 32 + sz > len(d):
        raise ValueError("truncated load command region")
    # All command kinds that introduce a dylib dependency.
    # LC_LOAD_DYLIB 0xC is the only one allowed by policy; the rest must be
    # detected and rejected so they cannot smuggle in an extra dependency.
    _DYLIB_DEP_CMDS = frozenset({
        0xC,          # LC_LOAD_DYLIB
        0x80000018,   # LC_LOAD_WEAK_DYLIB
        0x8000001F,   # LC_REEXPORT_DYLIB
        0x20,         # LC_LAZY_LOAD_DYLIB
        0x80000023,   # LC_LOAD_UPWARD_DYLIB
    })
    # Human-readable names for error messages.
    _DYLIB_CMD_NAMES = {
        0xC:          "LC_LOAD_DYLIB",
        0x80000018:   "LC_LOAD_WEAK_DYLIB",
        0x8000001F:   "LC_REEXPORT_DYLIB",
        0x20:         "LC_LAZY_LOAD_DYLIB",
        0x80000023:   "LC_LOAD_UPWARD_DYLIB",
    }
    # macOS deployment floor: engine is built for 13.0 (Ventura).
    _MINOS_FLOOR = 13 << 16  # major=13, minor=0, patch=0

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
        if cmd in frozenset({0xD}) | _DYLIB_DEP_CMDS:
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
        if cmd in _DYLIB_DEP_CMDS:
            loads.append((cmd, name))
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
    minos_raw = build[1]
    minos = f"{minos_raw >> 16}.{(minos_raw >> 8) & 255}.{minos_raw & 255}"
    if minos_raw > _MINOS_FLOOR:
        raise ValueError(
            f"minos {minos} exceeds the macOS 13.0 floor; rebuild against the correct SDK"
        )
    if expected == "arm64":
        if not sig:
            raise ValueError("arm64 dylib signature is missing or empty")
    if ident != ["@rpath/libghostty-vt.dylib"]:
        raise ValueError(f"wrong install name {ident!r}; expected @rpath/libghostty-vt.dylib")
    # Only LC_LOAD_DYLIB 0xC is permitted; any other load kind is an unexpected dependency.
    bad = [(cmd, name) for cmd, name in loads if cmd != 0xC]
    good = [name for cmd, name in loads if cmd == 0xC]
    if bad:
        descriptions = ", ".join(
            f"{_DYLIB_CMD_NAMES.get(cmd, f'cmd 0x{cmd:08X}')} {name!r}"
            for cmd, name in bad
        )
        raise ValueError(f"extra or wrong dependency (unexpected load kind): {descriptions}")
    if good != ["/usr/lib/libSystem.B.dylib"]:
        raise ValueError(f"extra or wrong dependency: {good!r}")
    print(
        f"Mach-O {expected}: cputype {cpu:#010x}, minos {minos}, signature {'present' if sig else 'absent'}"
    )
    for x in ident:
        print(f"LC_ID_DYLIB {x}")
    for _, x in loads:
        print(f"LC_LOAD_DYLIB {x}")


if __name__ == "__main__":
    if (len(sys.argv) not in (3, 4) or sys.argv[2] not in ("arm64", "x86_64")
            or (len(sys.argv) == 4 and sys.argv[3] != "--executable")):
        sys.exit("usage: check-macho.py PATH arm64|x86_64 [--executable]")
    try:
        check(sys.argv[1], sys.argv[2], executable=len(sys.argv) == 4)
    except (OSError, ValueError) as e:
        sys.exit(str(e))
