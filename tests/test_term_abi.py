"""The ABI/drift guards for the ctypes binding.

Why these matter (plan R2, phase 1 "Tests"): a struct layout guessed wrong corrupts memory
silently, and a ctypes declaration that drifts from the pinned C API on a pin bump is exactly the
failure DT1 takes on and bounds with the manifest check. These tests prove the check is real:
every declared struct's size and field offsets, and every declared enum constant, match the
library's own manifest (`ghostty_type_json()`, types.h:410); every declared function exists in the
vendored headers and the loaded library; and the engine's identity is pinned.

**One deviation, reported.** The phase's ABI test asks that the build info name `PIN`'s
`upstream_commit`. The vendored shared object does **not**: `ghostty_build_info(VERSION_STRING)` is
`"0.1.0-dev"` and the manifest's `commit` is null, matching `PIN`'s `pc_version`, not
`upstream_version: 1.3.2-dev` + the commit. Engine identity is therefore proved by the `.so`'s exact
sha256 against `PIN`'s `so_sha256` and by the version the library does report; the commit-not-named
gap is surfaced in the phase report, not smoothed over (AGENTS Rule 12).
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import sys
from pathlib import Path

import pytest

from tsb_tui.term import ghostty as G

PIN_COMMIT = "34f39002c6e3974b54e6a6d400bd83777c5ea558"

# Read-only frozen inputs: the vendored headers this binding declares against, and the pin file.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_HEADERS = _REPO_ROOT / "vendor" / "libghostty-vt" / "include"
_PIN = _HEADERS.parent / "PIN"
_TERM_SO = Path(os.environ["TSB_TUI_TERM_LIBRARY"]) if "TSB_TUI_TERM_LIBRARY" in os.environ else (
    _HEADERS.parent / "libghostty-vt.so")


def _pin_value(key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(\S+)\s*$", _PIN.read_text(), re.MULTILINE)
    assert match is not None, f"PIN has no {key} line"
    return match.group(1)


@pytest.fixture(scope="module")
def lib(term_so: Path) -> G.GhosttyLib:
    return G.load(term_so)


@pytest.fixture(scope="module")
def library_and_pin_version(lib: G.GhosttyLib) -> tuple[str, str]:
    return lib.build_info.version_string, _pin_value("pc_version")


def test_manifest_matches_every_declared_struct(lib: G.GhosttyLib) -> None:
    """Every declared struct's size and field offsets match the library manifest.

    Why: a wrong offset is a silent memory corruption; the manifest is the library's own statement
    of its layout, so it is the authority (types.h:410). This is the check that refuses to load.
    """
    for name, cls in G._DECLARED_STRUCTS:
        G._check_struct(lib.manifest, name, cls)


def test_manifest_matches_every_declared_enum(lib: G.GhosttyLib) -> None:
    """Every declared enum constant equals the manifest's value.

    Why: an enum off by one sends the wrong mode query or the wrong effect, and the bug is invisible
    until a target misbehaves. The manifest carries the values (types.h example, "values").
    """
    for name, constants in G._DECLARED_ENUMS.items():
        G._check_enum(lib.manifest, name, constants)


def test_manifest_matches_packed_cell_bits(lib: G.GhosttyLib) -> None:
    """The packed GhosttyCell bit fields we decode by hand match the manifest.

    Why: the cell is packed into a u64 and the backend decodes content, style id and wide flag from
    it by bit position; if the layout moves, every cell is wrong at once (screen.h:32, "hardcoding
    bit positions is unsupported").
    """
    G._check_cell_bits(lib.manifest)


def test_check_refuses_a_drifted_struct(lib: G.GhosttyLib) -> None:
    """A manifest whose struct size differs is refused, naming the type.

    Why: the whole point of the check is that a pin bump that moves a field fails start-up rather
    than corrupting memory (plan R2). A test that only passed the good manifest would not prove the
    check can fail.
    """
    bad = copy.deepcopy(lib.manifest)
    bad["types"]["GhosttyStyle"]["size"] += 8
    with pytest.raises(G.AbiError, match="GhosttyStyle"):
        G._check_manifest(bad)


def test_check_refuses_a_drifted_offset(lib: G.GhosttyLib) -> None:
    """A manifest whose field offset differs is refused, naming the field.

    Why: size can match while a field moves; only the offset check catches that (plan R2).
    """
    bad = copy.deepcopy(lib.manifest)
    bad["types"]["GhosttyString"]["fields"]["len"]["offset"] = 12
    with pytest.raises(G.AbiError, match="GhosttyString.len"):
        G._check_manifest(bad)


def test_check_refuses_a_drifted_enum(lib: G.GhosttyLib) -> None:
    """A manifest whose enum value differs is refused, naming the constant.

    Why: a changed enum value changes a wire behaviour (which option, which mode) with no other
    symptom; the check must catch it.
    """
    bad = copy.deepcopy(lib.manifest)
    bad["types"]["GhosttyTerminalOption"]["values"]["WRITE_PTY"] = 9
    with pytest.raises(G.AbiError, match="WRITE_PTY"):
        G._check_manifest(bad)


def test_check_refuses_a_drifted_cell_bit(lib: G.GhosttyLib) -> None:
    """A manifest whose packed cell bit moved is refused, naming the field.

    Why: the backend decodes the cell by bit position; a moved bit silently mislabels content
    (screen.h:32).
    """
    bad = copy.deepcopy(lib.manifest)
    bad["types"]["GhosttyCell"]["bits"]["wide"]["lsb"] = 40
    with pytest.raises(G.AbiError, match="wide"):
        G._check_manifest(bad)


def test_check_refuses_an_unknown_schema(lib: G.GhosttyLib) -> None:
    """A manifest with an unknown schema version is refused.

    Why: the check is written against schema 1; a future schema could mean anything, so loading
    must stop rather than guess (types.h:364 "Consumers should reject unknown schema versions").
    """
    bad = copy.deepcopy(lib.manifest)
    bad["schema"] = 2
    with pytest.raises(G.AbiError, match="schema"):
        G._check_manifest(bad)


def test_every_declared_function_exists_in_the_headers() -> None:
    """Every function this binding declares is present in the vendored headers at the pin.

    Why (phase 1 "Tests", plan R2): a function name invented or renamed on a bump would fail only
    at the first call. Reading the headers at `PIN` proves the binding names only real API.
    """
    text = "\n".join(p.read_text() for p in _HEADERS.rglob("*.h"))
    missing = [name for name in G.declared_function_names() if name not in text]
    assert not missing, f"declared functions absent from the vendored headers: {missing}"


def test_every_declared_function_exists_in_the_library(lib: G.GhosttyLib) -> None:
    """Every function this binding declares is exported by the loaded `.so`.

    Why: the headers and the built library are the same pin, but a function removed from the build
    (for example a disabled protocol) must fail here, not at first use (plan R2).
    """
    for name in G.declared_function_names():
        assert getattr(lib.lib, name, None) is not None, f"{name} not exported by the library"


def test_build_info_matches_the_pin(library_and_pin_version: tuple[str, str]) -> None:
    """The engine's reported version equals `PIN`'s `pc_version`.

    Why: the TUI must render with the same engine the core checked (immortality §5.2). The shared
    object's `ghostty_build_info(VERSION_STRING)` must equal `PIN`'s `pc_version`; if a different
    build were loaded, this fails. It does **not** name the ghostty `upstream_commit` — the artifact
    reports `commit: null` and version `0.1.0-dev` — so engine identity is pinned by the exact
    sha256 below, and the gap is reported for the orchestrator rather than smoothed over.
    """
    version_string, pin_pc_version = library_and_pin_version
    assert version_string == pin_pc_version


def test_library_sha256_matches_pin(term_so: Path) -> None:
    """The loaded `.so` is byte-for-byte the artifact `PIN` records.

    Why: because the library's build info cannot name its ghostty commit, the only exact proof that
    this is the pinned engine is the artifact hash (`PIN` `so_sha256`). A rebuilt or drifted `.so`
    fails here, before any rendering happens (plan R2).
    """
    want = _pin_value("so_sha256")
    got = hashlib.sha256(term_so.read_bytes()).hexdigest()
    assert got == want


def test_pin_file_names_the_commit_and_floor() -> None:
    """`PIN`'s `upstream_commit` is the commit this suite records, and the glibc floor is recorded.

    Why: the constant above and the pin file must move together, so a bump that changes one and not
    the other fails rather than silently checking the old engine (plan R2). The glibc floor is
    phase 1's acceptance line (R3).
    """
    assert _pin_value("upstream_commit") == PIN_COMMIT
    assert _pin_value("upstream_version") == "1.3.2-dev"
    assert _pin_value("so_glibc_floor") == "GLIBC_2.29"


def test_load_refuses_a_missing_library(tmp_path: Path) -> None:
    """Loading a path with no `.so` raises LibraryError naming the path.

    Why (DT2): the library is found by an explicit path and a missing one must fail loudly, naming
    the knob and the path, never fall back to a search.
    """
    missing = tmp_path / "libghostty-vt.so"
    with pytest.raises(G.LibraryError, match="libghostty-vt.so"):
        G.load(missing)


def test_windows_default_engine_filename_and_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1: on Windows default engine is ghostty-vt.dll and error message names it."""
    from tsb_tui import app
    from tsb_tui.config import load

    monkeypatch.setenv("TSB_TUI_TERM_LIBRARY", "")
    cfg = load(overrides={"term.library": ""})

    monkeypatch.setattr(sys, "platform", "darwin")
    assert app.library_path(cfg).name == "libghostty-vt.dylib"

    monkeypatch.setattr(sys, "platform", "linux")
    assert app.library_path(cfg).name == "libghostty-vt.so"

    monkeypatch.setattr(sys, "platform", "win32")
    assert app.library_path(cfg).name == "ghostty-vt.dll"
    with pytest.raises(app.EngineError, match=r"ghostty-vt\.dll"):
        app.load_engine(cfg)


def test_load_engine_calls_add_dll_directory_on_windows(monkeypatch: pytest.MonkeyPatch, term_so: Path) -> None:
    """P2: on Windows, before loading the engine, os.add_dll_directory() its folder."""
    monkeypatch.setattr(sys, "platform", "win32")
    added: list[str] = []
    monkeypatch.setattr(os, "add_dll_directory", lambda p: added.append(p), raising=False)
    G.load(term_so)
    assert added == [str(term_so.resolve().parent)]
