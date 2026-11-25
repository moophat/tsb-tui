"""Locate the contract tree and load its fixtures."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import loads

SEAMS = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8")
ROOT_ENV = "TSB_CONTRACT_DIR"
# The side index of the reference fixtures' grading tests, beside the seam directories: it is one
# file naming them all, not a fixture, so it is not a seam and never loaded as one.
GRADED_BY_INDEX = "graded-by.json"


class TreeError(Exception):
    """The contract tree is missing or unreadable."""


def is_tree(path: Path) -> bool:
    return (path / "CONTRACT_VERSION").is_file() and (path / "schemas").is_dir()


def contract_root(explicit: str | None = None) -> Path:
    """--contract-dir, else $TSB_CONTRACT_DIR, else the tree this package was loaded from."""
    for candidate, source in ((explicit, "--contract-dir"), (os.environ.get(ROOT_ENV), ROOT_ENV)):
        if candidate:
            path = Path(candidate).resolve()
            if not is_tree(path):
                raise TreeError(f"{source} {path} is not a contract tree (no CONTRACT_VERSION or schemas/)")
            return path
    here = Path(__file__).resolve().parent.parent
    if is_tree(here):
        return here
    raise TreeError("contract tree not found next to the tsbc package; pass --contract-dir or set "
                    f"{ROOT_ENV} (an installed tsbc carries no schemas or fixtures)")


def contract_version(root: Path) -> str:
    return (root / "CONTRACT_VERSION").read_text(encoding="utf-8").strip()


@dataclass
class Fixture:
    path: Path
    rel: str
    doc: dict[str, Any]

    @property
    def seam(self) -> str:
        return self.doc.get("seam", "?")

    @property
    def name(self) -> str:
        return self.doc.get("name", self.path.stem)

    @property
    def kind(self) -> str:
        return self.doc.get("kind", "?")

    @property
    def grade(self) -> str:
        return self.doc.get("grade", "?")

    @property
    def mark(self) -> dict[str, str]:
        mark = self.doc.get("mark")
        return mark if isinstance(mark, dict) else {}

    @property
    def case(self) -> dict[str, Any]:
        case = self.doc.get("case")
        return case if isinstance(case, dict) else {}

    @property
    def label(self) -> str:
        return f"{self.seam} {self.name}"


def load_fixtures(root: Path) -> tuple[list[Fixture], list[str]]:
    """Every fixtures/C*/*.json, sorted by seam then name; plus load errors (bad JSON, stray dirs)."""
    fixtures, errors = [], []
    base = root / "fixtures"
    if not base.is_dir():
        return fixtures, errors
    for entry in sorted(base.iterdir()):
        if entry.name.startswith(".") or entry.name == GRADED_BY_INDEX:
            continue
        if not entry.is_dir() or entry.name not in SEAMS:
            errors.append(f"fixtures/{entry.name}: only the seam directories {', '.join(SEAMS)} belong here")
            continue
        for path in sorted(entry.iterdir()):
            rel = path.relative_to(root).as_posix()
            if path.name.startswith(".") or path.suffix == ".bin":
                continue
            if path.is_dir() or path.suffix != ".json":
                errors.append(f"{rel}: fixtures are <name>.json plus optional <name>*.bin files")
                continue
            try:
                doc = loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:  # JSONDecodeError, UnicodeDecodeError, a non-finite number
                errors.append(f"{rel}: not valid JSON: {exc}")
                continue
            if not isinstance(doc, dict):
                errors.append(f"{rel}: a fixture is a JSON object")
                continue
            fixtures.append(Fixture(path, rel, doc))
    return fixtures, errors
