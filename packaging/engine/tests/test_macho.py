"""Structural Mach-O checks without executing a foreign library. AI-Model: codex."""

import struct
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1]


def run_check(path, arch):
    return subprocess.run(
        [sys.executable, "-I", str(ENGINE / "check-macho.py"), str(path), arch],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "folder,arch", [("macos-arm64", "arm64"), ("macos-x64", "x86_64")]
)
def test_real_dylib(folder, arch):
    result = run_check(ENGINE / folder / "libghostty-vt.dylib", arch)
    assert result.returncode == 0, result.stderr
    assert "minos " in result.stdout
    assert [x for x in result.stdout.splitlines() if x.startswith("LC_ID_DYLIB ")] == ["LC_ID_DYLIB @rpath/libghostty-vt.dylib"]
    assert [x for x in result.stdout.splitlines() if x.startswith("LC_LOAD_DYLIB ")] == ["LC_LOAD_DYLIB /usr/lib/libSystem.B.dylib"]


def minimal(cpu=0x100000C, subtype=0, signature=True, build=True, filetype=6,
            signature_range=(0, 0), ident="@rpath/libghostty-vt.dylib",
            loads=("/usr/lib/libSystem.B.dylib",),
            extra_load_cmds=()):
    """Build a minimal Mach-O dylib in memory.

    extra_load_cmds: sequence of (cmd_int, name_str) pairs emitted with that
    command kind instead of the default 0xC (LC_LOAD_DYLIB).
    """
    commands = b""
    count = 0
    if build:
        commands += struct.pack("<IIIIII", 0x32, 24, 1, 13 << 16, 13 << 16, 0)
        count += 1
    if signature:
        commands += struct.pack("<IIII", 0x1D, 16, *signature_range)
        count += 1
    ident_b = ident.encode() + b"\0"
    dylib = struct.pack("<IIIIII", 0xD, 24 + len(ident_b), 24, 0, 0, 0) + ident_b
    commands += dylib
    count += 1
    for load in loads:
        load_b = load.encode() + b"\0"
        commands += struct.pack("<IIIIII", 0xC, 24 + len(load_b), 24, 0, 0, 0) + load_b
        count += 1
    for cmd_kind, name in extra_load_cmds:
        name_b = name.encode() + b"\0"
        commands += struct.pack("<IIIIII", cmd_kind, 24 + len(name_b), 24, 0, 0, 0) + name_b
        count += 1
    return (
        struct.pack(
            "<IiiIIIII", 0xFEEDFACF, cpu, subtype, filetype, count, len(commands), 0, 0
        )
        + commands
    )


def minimal_with_minos(minos_packed, cpu=0x100000C, subtype=0, signature_range=(64, 1),
                       ident="@rpath/libghostty-vt.dylib",
                       loads=("/usr/lib/libSystem.B.dylib",)):
    """Like minimal() but with a custom minos value packed into LC_BUILD_VERSION."""
    commands = b""
    count = 0
    # LC_BUILD_VERSION with custom minos
    commands += struct.pack("<IIIIII", 0x32, 24, 1, minos_packed, minos_packed, 0)
    count += 1
    commands += struct.pack("<IIII", 0x1D, 16, *signature_range)
    count += 1
    ident_b = ident.encode() + b"\0"
    commands += struct.pack("<IIIIII", 0xD, 24 + len(ident_b), 24, 0, 0, 0) + ident_b
    count += 1
    for load in loads:
        load_b = load.encode() + b"\0"
        commands += struct.pack("<IIIIII", 0xC, 24 + len(load_b), 24, 0, 0, 0) + load_b
        count += 1
    return (
        struct.pack(
            "<IiiIIIII", 0xFEEDFACF, cpu, subtype, 6, count, len(commands), 0, 0
        )
        + commands
    )


@pytest.mark.parametrize(
    "damage,expected",
    [
        ("truncated", "Mach-O"),
        ("wrong-architecture", "cputype"),
        ("fat", "Mach-O"),
        ("unsigned", "missing or empty"),
        ("missing-build", "LC_BUILD_VERSION"),
        ("not-dylib", "MH_DYLIB"),
    ],
)
def test_invalid_macho(tmp_path, damage, expected):
    data = minimal()
    if damage == "truncated":
        data = data[:20]
    elif damage == "wrong-architecture":
        data = minimal(cpu=0x1000007)
    elif damage == "fat":
        data = struct.pack(">II", 0xCAFEBABE, 0)
    elif damage == "unsigned":
        data = minimal(signature=False)
    elif damage == "missing-build":
        data = minimal(build=False)
    elif damage == "not-dylib":
        data = minimal(filetype=2)
    path = tmp_path / "invalid.dylib"
    path.write_bytes(data)
    result = run_check(path, "arm64")
    assert result.returncode != 0
    assert expected in result.stderr


@pytest.mark.parametrize("signature_range", [(0, 0), (0, 1), (64, 0), (99999, 1), (64, 99999)])
def test_signature_must_have_nonempty_in_file_range(tmp_path, signature_range):
    path = tmp_path / "empty-signature.dylib"
    path.write_bytes(minimal(signature_range=signature_range))
    result = run_check(path, "arm64")
    assert result.returncode != 0
    assert "missing or empty" in result.stderr


@pytest.mark.parametrize("arch,cpu,subtype", [("arm64", 0x100000C, 2), ("x86_64", 0x1000007, 8)])
def test_rejects_newer_cpu_subtype(tmp_path, arch, cpu, subtype):
    path = tmp_path / "newer-subtype.dylib"
    path.write_bytes(minimal(cpu=cpu, subtype=subtype, signature_range=(64, 1)))
    result = run_check(path, arch)
    assert result.returncode != 0
    assert "subtype" in result.stderr


@pytest.mark.parametrize("ident,loads,needle", [
    ("wrong", ("/usr/lib/libSystem.B.dylib",), "install name"),
    ("@rpath/libghostty-vt.dylib", ("/usr/lib/libSystem.B.dylib", "/tmp/extra.dylib"), "extra"),
])
def test_rejects_wrong_dylib_dependencies(tmp_path, ident, loads, needle):
    path = tmp_path / "bad-deps.dylib"
    path.write_bytes(minimal(signature_range=(64, 1), ident=ident, loads=loads))
    result = run_check(path, "arm64")
    assert result.returncode != 0
    assert needle in result.stderr


@pytest.mark.parametrize("arch,cpu,subtype", [("arm64", 0x100000C, -2147483648), ("x86_64", 0x1000007, -2147483645)])
def test_accepts_generic_subtype_with_capability_bits(tmp_path, arch, cpu, subtype):
    path = tmp_path / "generic.dylib"
    path.write_bytes(minimal(cpu=cpu, subtype=subtype, signature_range=(64, 1)))
    result = run_check(path, arch)
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Finding 1: every dylib load command kind must be counted (red-first)
# ---------------------------------------------------------------------------

# Map of all dylib-dependency command kinds the checker must recognise.
_DYLIB_LOAD_CMDS = {
    "LC_LOAD_WEAK_DYLIB":   0x80000018,
    "LC_REEXPORT_DYLIB":    0x8000001F,
    "LC_LAZY_LOAD_DYLIB":   0x20,
    "LC_LOAD_UPWARD_DYLIB": 0x80000023,
}


@pytest.mark.parametrize("cmd_name,cmd_kind", list(_DYLIB_LOAD_CMDS.items()))
def test_rejects_extra_weak_or_reexport_load(tmp_path, cmd_name, cmd_kind):
    """A dylib that pulls in an extra dependency via any non-LC_LOAD_DYLIB kind
    must fail the checker.  On abe266c it passes (red gate)."""
    path = tmp_path / "extra-dep.dylib"
    path.write_bytes(
        minimal(
            signature_range=(64, 1),
            extra_load_cmds=[(cmd_kind, "/usr/lib/libz.1.dylib")],
        )
    )
    result = run_check(path, "arm64")
    assert result.returncode != 0, (
        f"{cmd_name} (0x{cmd_kind:08X}) extra dep was not rejected"
    )
    assert "extra" in result.stderr or "dependency" in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# Finding 2: macOS floor (minos <= 13.0) enforced (red-first)
# ---------------------------------------------------------------------------

def _pack_minos(major, minor=0, patch=0):
    """Pack a macOS version into the 32-bit LC_BUILD_VERSION minos field."""
    return (major << 16) | (minor << 8) | patch


@pytest.mark.parametrize("folder,arch", [("macos-arm64", "arm64"), ("macos-x64", "x86_64")])
def test_real_dylib_minos_exact(folder, arch):
    """Real dylibs must report exactly minos 13.0.0."""
    result = run_check(ENGINE / folder / "libghostty-vt.dylib", arch)
    assert result.returncode == 0, result.stderr
    assert "minos 13.0.0" in result.stdout, (
        f"expected 'minos 13.0.0' in output; got: {result.stdout!r}"
    )


def test_rejects_minos_above_floor(tmp_path):
    """A dylib built for macOS 14.0 (above the 13.0 floor) must be rejected."""
    path = tmp_path / "minos-14.dylib"
    path.write_bytes(minimal_with_minos(_pack_minos(14, 0, 0)))
    result = run_check(path, "arm64")
    assert result.returncode != 0, "minos 14.0.0 was not rejected (red gate)"
    assert "14" in result.stderr, result.stderr
