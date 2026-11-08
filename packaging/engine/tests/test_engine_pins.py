"""Hold every shipped Windows engine file to its pin. AI-Model: codex."""

import hashlib
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1]
TARGET = "-Dtarget=x86_64-windows-gnu"


def read_pins():
    return [
        line.split()
        for line in (ENGINE / "ENGINE-PINS").read_text().splitlines()
        if line and not line.startswith("#")
    ]


def test_every_shipped_file_is_pinned():
    rows = read_pins()
    names = [row[0] for row in rows]
    assert names
    assert len(names) == len(set(names)), "duplicate pin"
    actual = {
        path.relative_to(ENGINE).as_posix()
        for path in (ENGINE / "windows-x64").rglob("*")
        if path.is_file()
    }
    assert set(names) == actual
    assert not any(path.is_symlink() for path in (ENGINE / "windows-x64").rglob("*"))


@pytest.mark.parametrize("row", read_pins(), ids=lambda row: row[0])
def test_engine_pin(row):
    name, digest, size, target = row
    assert target == TARGET
    assert name.startswith("windows-x64/") and ".." not in Path(name).parts
    data = (ENGINE / name).read_bytes()
    assert len(data) == int(size)
    assert hashlib.sha256(data).hexdigest() == digest


def check_pe(path):
    return subprocess.run(
        [sys.executable, "-I", str(ENGINE / "check-pe.py"), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_shipped_dll_is_pe_x64():
    dlls = list((ENGINE / "windows-x64").glob("*.dll"))
    assert len(dlls) == 1
    result = check_pe(dlls[0])
    assert result.returncode == 0, result.stderr
    assert "machine 0x8664" in result.stdout


@pytest.mark.parametrize("damage", ["machine", "signature", "offset", "truncated", "not-dll"])
def test_pe_check_rejects_invalid_dll(tmp_path, damage):
    data = bytearray((ENGINE / "windows-x64/ghostty-vt.dll").read_bytes())
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if damage == "machine":
        struct.pack_into("<H", data, offset + 4, 0xAA64)
    elif damage == "signature":
        data[offset : offset + 4] = b"nope"
    elif damage == "offset":
        struct.pack_into("<I", data, 0x3C, len(data))
    elif damage == "truncated":
        data = data[:32]
    else:
        struct.pack_into("<H", data, offset + 22, 0)
    path = tmp_path / "invalid.dll"
    path.write_bytes(data)
    assert check_pe(path).returncode != 0
