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
    assert "LC_ID_DYLIB " in result.stdout
    assert "LC_LOAD_DYLIB " in result.stdout


def minimal(cpu=0x100000C, signature=True, build=True, filetype=6):
    commands = b""
    count = 0
    if build:
        commands += struct.pack("<IIIIII", 0x32, 24, 1, 13 << 16, 13 << 16, 0)
        count += 1
    if signature:
        commands += struct.pack("<IIII", 0x1D, 16, 0, 0)
        count += 1
    return (
        struct.pack(
            "<IiiIIIII", 0xFEEDFACF, cpu, 0, filetype, count, len(commands), 0, 0
        )
        + commands
    )


@pytest.mark.parametrize(
    "damage,expected",
    [
        ("truncated", "Mach-O"),
        ("wrong-architecture", "cputype"),
        ("fat", "Mach-O"),
        ("unsigned", "LC_CODE_SIGNATURE"),
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
