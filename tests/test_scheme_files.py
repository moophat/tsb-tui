# fails today: the schemes folder does not exist
"""Shipped TUI colour scheme files, their shape, provenance and packaging.

Why these matter (Rule 9): the TUI ships third-party palettes, so a file that is
malformed, unlicensed, unattributed or left out of the wheel breaks both the
operator's theme list and the project's legal position — a scheme with no licence
in the manifest cannot be shipped, and a scheme file not in package-data silently
disappears from an installed wheel.

Phase 3: the shape check is the real loader (`theme_files.parse_scheme`), not a copy of it, so a
scheme the loader would refuse fails here too; provenance, the manifest and package-data are kept.
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

import tomllib
from tsb_tui.theme_files import parse_scheme

TUI_DIR = Path(__file__).resolve().parents[1]
SCHEMES = TUI_DIR / "tsb_tui" / "schemes"
THEMES = TUI_DIR / "tsb_tui" / "themes"
PYPROJECT = TUI_DIR / "pyproject.toml"
MANIFEST = SCHEMES / "THIRD_PARTY_SCHEMES.md"

SOURCE_KEYS = {"origin", "author", "license", "url"}
LICENSES = {"MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "Unlicense", "CC0-1.0"}


def _scheme_files() -> list[Path]:
    return sorted(SCHEMES.glob("*.json"))


def _third_party_files() -> list[Path]:
    return [p for p in _scheme_files() if p.stem != "tsb-default"]


def test_shipped_scheme_count_and_named_schemes() -> None:
    """The ruling ships tsb-default plus the 18 verified schemes (range 19 to 26 so
    a later ruling can add a few); a count outside it means files were added or
    dropped without a ruling. The named four are the baseline set the operator
    uses to verify the theme list renders dark and light palettes."""
    files = _scheme_files()
    assert 19 <= len(files) <= 26, [p.name for p in files]
    slugs = {p.stem for p in files}
    assert {"tsb-default", "dracula", "nord", "solarized-light"} <= slugs


def test_each_file_is_a_well_formed_scheme() -> None:
    """A malformed scheme would make the TUI's theme list fail to load. The check is the loader
    itself: a file `parse_scheme` refuses (an unknown key, a short `ansi`, a bad hex) fails here,
    so this test can never drift from what start-up accepts."""
    # fails today: `theme_files.parse_scheme` does not exist.
    for path in _scheme_files():
        scheme = parse_scheme(path)
        assert scheme.slug == path.stem
        assert len(scheme.ansi) == 16
        assert scheme.variant in ("dark", "light")


def test_third_party_provenance_and_licence() -> None:
    """Every third-party scheme is shipped under its author's licence, so each file
    must name the repository, the pinned commit, the author and an SPDX id from the
    allowed set; without this the project ships palette data with no legal basis."""
    for path in _third_party_files():
        source = json.loads(path.read_text(encoding="utf-8"))["source"]
        assert set(source) == SOURCE_KEYS, (path.name, sorted(source))
        m = re.search(r"@([0-9a-f]{40}):", source["origin"])
        assert m, (path.name, source["origin"])
        assert source["license"] in LICENSES, (path.name, source["license"])


def test_manifest_attributes_each_scheme_at_its_commit() -> None:
    """The manifest is the legal record: each third-party slug must appear with the
    same commit its file pins, so a re-pin that updates a JSON without updating the
    licence record fails here."""
    text = MANIFEST.read_text(encoding="utf-8")
    for path in _third_party_files():
        source = json.loads(path.read_text(encoding="utf-8"))["source"]
        commit = re.search(r"@([0-9a-f]{40}):", source["origin"]).group(1)
        rows = [line for line in text.splitlines() if f"| {path.stem} |" in line]
        assert rows, (path.stem, "not in manifest")
        assert any(commit in line for line in rows), (path.stem, commit)


def test_every_scheme_and_theme_file_is_packaged() -> None:
    # fails today: no file under tsb_tui/themes/ is covered by a package-data glob.
    """A scheme not covered by a package-data glob is silently missing from an
    installed wheel — the operator's theme list would ship 18 entries and load 17. Phase 5 adds
    the chrome theme files, which vanish from the wheel the same way."""
    globs = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))[
        "tool"]["setuptools"]["package-data"]["tsb_tui"]
    for folder in (SCHEMES, THEMES):
        for path in sorted(folder.iterdir()):
            rel = path.relative_to(TUI_DIR / "tsb_tui").as_posix()
            assert any(fnmatch.fnmatch(rel, glob) for glob in globs), (rel, globs)
