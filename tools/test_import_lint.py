"""The extracted unit's boundary, including aliases, relative imports and fail-closed inputs."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

import import_lint
import pytest


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for directory in ("tsb_tui", "tests", "bench"):
        (tmp_path / directory).mkdir()
    (tmp_path / "tsb_tui/__init__.py").write_text("")
    return tmp_path


def run(root: Path) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = import_lint.main(["--root", str(root)])
    return code, out.getvalue()


def test_allowed_imports(root: Path) -> None:
    (root / "tsb_tui/allowed.py").write_text(
        "import os, textual, rich, argon2, precis_i18n, tsb_contract_client, tsbc\n"
        "from . import local\nimport importlib as il\nil.import_module('tsbc')\n")
    (root / "tsb_tui/local.py").write_text("")
    (root / "tests/test_example.py").write_text("import pytest, socket\n")
    (root / "bench/probe.py").write_text("import tsb_tui, tsbc\n")
    code, out = run(root)
    assert code == 0, out


@pytest.mark.parametrize("module", ["core", "worker", "vt", "schema", "vault", "harness"])
@pytest.mark.parametrize("directory", ["tsb_tui", "tests", "bench"])
def test_forbidden_core_units(root: Path, module: str, directory: str) -> None:
    (root / directory / "forbidden.py").write_text(f"import {module}\n")
    code, out = run(root)
    assert code == 1 and module in out, out


@pytest.mark.parametrize("body", [
    "import requests\n", "import pytest\n", "import socket\n",
    "from .. import outside\n", "def bad(:\n",
    "import importlib as il\nil.import_module('core')\n",
    "from importlib import import_module as load\nload('worker')\n",
    "import builtins\nbuiltins.__import__('vault')\n",
    "load = __import__\nload('schema')\n",
    "import importlib\ngetattr(importlib, 'import_module')('harness')\n",
    "import importlib\nimportlib.import_module(name)\n",
    "import importlib\nimportlib.import_module('.x', package='core')\n",
])
def test_forbidden_shapes_fail(root: Path, body: str) -> None:
    (root / "tsb_tui/bad.py").write_text(body)
    code, out = run(root)
    assert code == 1, out


def test_stub_imports_are_checked(root: Path) -> None:
    (root / "tsb_tui/bad.pyi").write_text("import core\n")
    assert run(root)[0] == 1


def test_symlinks_fail(root: Path) -> None:
    (root / "tsb_tui/link.py").symlink_to(root / "tsb_tui/__init__.py")
    assert run(root)[0] == 1


def test_local_core_cannot_launder_an_import(root: Path) -> None:
    (root / "tsb_tui/core.py").write_text("")
    (root / "tsb_tui/bad.py").write_text("import core\n")
    assert run(root)[0] == 1


def test_test_only_helper_cannot_be_imported_by_shipped_code(root: Path) -> None:
    (root / "tests/helper.py").write_text("")
    (root / "tsb_tui/bad.py").write_text("import helper\n")
    assert run(root)[0] == 1


def test_missing_input_fails(root: Path) -> None:
    (root / "bench").rmdir()
    assert run(root)[0] == 2
