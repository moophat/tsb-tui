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
            loads=("/usr/lib/libSystem.B.dylib",)):
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
    return (
        struct.pack(
            "<IiiIIIII", 0xFEEDFACF, cpu, subtype, filetype, count, len(commands), 0, 0
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
