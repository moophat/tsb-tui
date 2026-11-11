#!/usr/bin/env python3
"""import-lint — keeps tsb-tui/ independently deployable (Contract §3).

`tsb_tui`, tests and benchmarks must import nothing of TSB's except the published contract client, so what ships is a
genuine API consumer and it can leave the repo by `git subtree split`. This script is the only
thing enforcing that — review and memory do not (development-plan.md §1). `contract/` is no longer
linted here: its stdlib-only rule is enforced in tsb-program, where it is published from. The name
`contract` stays reserved below, so no unit may define a top-level `contract` of its own.

Usage: python3 tools/import_lint.py [--root REPO_ROOT]
Exit:  0 clean · 1 violations or an unparseable file · 2 a unit directory is missing (wrong root)

Checked, in .py and .pyi files:
- `import`/`from` statements anywhere (functions and TYPE_CHECKING blocks included);
- dynamic imports: `importlib.import_module(name, package)` and the builtin `__import__(name, …,
  level)`, reached directly or through a module-wide alias (`import importlib as il`, `from
  importlib import import_module as load`, `builtins.__import__`, `b = __import__`, `getattr(il,
  "import_module")`). A relative name is resolved against `package=` (or `__package__`, meaning
  this module's package). A name, package or level that is not a constant cannot be checked and
  is itself a violation, in both units. `x.import_module(…)` on anything that is not importlib is
  not an import;
- relative imports that climb above the unit's top-level packages;
- unit-defined top-level names that shadow a TSB unit or an allowlisted package, and names defined
  only under tests imported from shipped code;
- symlinks: ANY symlink under a checked unit is a violation, wherever it points. A link inside the
  unit is harmless today and an alias for anything tomorrow; one rule with no exceptions is the
  only version of this check that cannot be argued around.

Known gaps: `asyncio.open_connection` / `loop.create_connection` open sockets without importing a
network module, and tsb-tui/ must import asyncio (Textual needs it). Aliases are tracked module-wide by
name, not by scope or control flow, and only through the forms listed above. The lint guards
against accidental coupling; it is not a sandbox — review covers the rest.

Stdlib only, so it runs on a bare CI image.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from dataclasses import dataclass
from pathlib import Path

STDLIB = frozenset(sys.stdlib_module_names)

# Third-party and first-party top-level modules each unit may import besides the stdlib and its
# own packages. One line of rationale per entry; the TUI plan cites this table, so change it here.
ALLOWED = {
    ".": {
        "textual": "the TUI framework",
        "rich": "Textual's own rendering library; the TUI canvas builds its Strip segments from it",
        "tsb_contract_client": "the published contract client — the one TSB import tsb-tui/ may make",
        "tsbc": "the contract runner the client is built on; part of the same published contract",
        "argon2": "argon2id for the vault's untrusted-mode unlock; the contract client is stdlib-only (E4 DEFAULT 19)",
        "precis_i18n": "RFC 8265's OpaqueString profile for the passphrase's bytes: a correctness-proven library, never hand-written (E4 OPEN-12.1)",
    },
}

# Stdlib modules that open network connections. tsb-tui/ may not use them outside its tests: every
# network call goes through tsb_contract_client (the TUI plan's "no direct urllib/socket" rule —
# this lint is the test it names). asyncio cannot be listed; see the known gaps above.
NETWORK_STDLIB = {
    ".": frozenset({
        "socket", "_socket", "ssl", "_ssl", "http", "urllib", "ftplib", "smtplib", "imaplib",
        "poplib", "nntplib", "telnetlib", "xmlrpc", "socketserver",
    }),
}

# Extra imports allowed in test files only (tests/ dirs, test_*.py, *_test.py, conftest.py).
# Tests do not ship, so they may use the test runner and stub a server over sockets; they still
# may not import core/, worker/ or any other TSB code.
TEST_ALLOWED = {"pytest": "the test runner `make test` uses"}

# This module reads one static nginx release manifest, not a broker API. Every broker API call
# still goes through tsb_contract_client. Match the file and imported modules exactly.
UPDATE_CHECK_ALLOWED = {
    "urllib.request": "one GET of the static update manifest, without redirects",
    "ssl": "verify the manifest's TLS with the app's configured CA file",
}

# Top-level names a unit may not define for itself: a local `core` package would make `import
# core` legal, and a local `tsbc` would shadow the real contract runner.
REPO_UNITS = frozenset({
    "core", "worker", "vt", "schema", "discovery", "vault", "harness", "deploy", "tui", "contract",
})

SKIP_DIRS = {"__pycache__", "venv", "node_modules"}  # plus every dot-directory (.venv, .git, …)
SUFFIXES = (".py", ".pyi")

# What a name in a module can be bound to, for dynamic-import detection.
IMPORTLIB, IMPORT_MODULE, BUILTINS, DUNDER_IMPORT = "importlib", "import_module", "builtins", "__import__"


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


@dataclass(frozen=True)
class Imported:
    """One import site: an absolute module (level 0), a relative remainder (level > 0), or an
    error when a dynamic import cannot be resolved statically."""
    line: int
    module: str = ""
    level: int = 0
    error: str | None = None


def walk_unit(unit_dir: Path) -> tuple[list[Path], list[Path]]:
    """(python files, symlinks). Symlinks are never followed and every one is reported."""
    files, links = [], []
    for dirpath, dirnames, filenames in os.walk(unit_dir, followlinks=False):
        here = Path(dirpath)
        kept = []
        for name in sorted(dirnames):
            if (here / name).is_symlink():
                links.append(here / name)
            elif not (name.startswith(".") or name in SKIP_DIRS):
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = here / name
            if path.is_symlink():
                links.append(path)
            elif name.endswith(SUFFIXES):
                files.append(path)
    return sorted(files), sorted(links)


def is_package(directory: Path) -> bool:
    return (directory / "__init__.py").is_file() or (directory / "__init__.pyi").is_file()


def is_test_file(path: Path, unit_dir: Path) -> bool:
    name = path.name
    if name == "conftest.py" or name.startswith("test_") or name.endswith(("_test.py", "_test.pyi")):
        return True
    return "tests" in path.relative_to(unit_dir).parts[:-1]


def top_level_name(path: Path, unit_dir: Path) -> tuple[str, Path] | None:
    """The top-level import name a file defines and the path that defines it, or None for a
    package's inner module. Package roots are bounded at the unit dir."""
    directory = path.parent
    if not is_package(directory):
        return (None if path.stem == "__init__" else (path.stem, path))
    while directory != unit_dir and directory.parent != unit_dir and is_package(directory.parent):
        directory = directory.parent
    init = directory / ("__init__.py" if (directory / "__init__.py").is_file() else "__init__.pyi")
    return directory.name, init


def local_names(files: list[Path], unit_dir: Path) -> tuple[dict[str, bool], dict[str, Path]]:
    """({name: shipped?}, {name: defining path}). A name defined only under test paths is not
    shipped, so only test files may import it."""
    shipped: dict[str, bool] = {}
    where: dict[str, Path] = {}
    for path in files:
        found = top_level_name(path, unit_dir)
        if found is None:
            continue
        name, definer = found
        shipped[name] = shipped.get(name, False) or not is_test_file(definer, unit_dir)
        where.setdefault(name, definer)
    return shipped, where


def package_depth(path: Path, unit_dir: Path) -> int:
    """Enclosing packages strictly inside the unit — the deepest legal relative-import level."""
    depth, directory = 0, path.parent
    while directory != unit_dir and is_package(directory):
        depth += 1
        directory = directory.parent
    return depth


def binding(expr: ast.expr, names: dict[str, str]) -> str | None:
    """What an expression refers to, if it is importlib, import_module, builtins or __import__."""
    if isinstance(expr, ast.Name):
        return names.get(expr.id)
    attr = None
    if isinstance(expr, ast.Attribute):
        base, attr = expr.value, expr.attr
    elif (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "getattr"
          and len(expr.args) == 2 and isinstance(expr.args[1], ast.Constant)):
        base, attr = expr.args[0], expr.args[1].value
    if attr is None:
        return None
    kind = binding(base, names)
    if kind == IMPORTLIB and attr == "import_module":
        return IMPORT_MODULE
    if kind == BUILTINS and attr == "__import__":
        return DUNDER_IMPORT
    return None


def import_bindings(tree: ast.AST) -> dict[str, str]:
    """Module-wide names bound to importlib, import_module, builtins or __import__."""
    names = {"__import__": DUNDER_IMPORT, "__builtins__": BUILTINS}
    assigns: list[tuple[list[str], ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if alias.asname is None and top in (IMPORTLIB, BUILTINS):
                    names[top] = top  # `import importlib.util` binds `importlib`
                elif alias.name in (IMPORTLIB, BUILTINS):
                    names[alias.asname] = alias.name  # `import importlib.util as u` does not
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            wanted = {IMPORTLIB: "import_module", BUILTINS: "__import__"}.get(node.module)
            for alias in node.names:
                if alias.name == wanted:
                    names[alias.asname or alias.name] = IMPORT_MODULE if wanted == "import_module" else DUNDER_IMPORT
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            assigns.append(([t.id for t in targets if isinstance(t, ast.Name)], node.value))
    changed = True
    while changed:  # alias chains: a = importlib; load = a.import_module
        changed = False
        for targets, value in assigns:
            kind = binding(value, names)
            for target in targets:
                if kind and names.get(target) != kind:
                    names[target], changed = kind, True
    return names


def argument(call: ast.Call, position: int, keyword: str) -> ast.expr | None:
    if len(call.args) > position:
        return call.args[position]
    return next((k.value for k in call.keywords if k.arg == keyword), None)


def constant(expr: ast.expr | None, kind: type) -> bool:
    return isinstance(expr, ast.Constant) and type(expr.value) is kind


def resolve_import_module(call: ast.Call) -> Imported:
    """importlib.import_module(name, package=None), resolved as importlib does."""
    line, name, package = call.lineno, argument(call, 0, "name"), argument(call, 1, "package")
    if not constant(name, str):
        return Imported(line, error="dynamic import with a non-constant name cannot be checked")
    is_this_package = isinstance(package, ast.Name) and package.id == "__package__"
    if package is not None and not is_this_package and not constant(package, str) \
            and not (isinstance(package, ast.Constant) and package.value is None):
        return Imported(line, error="dynamic import with a non-constant package= cannot be checked")
    rest = name.value.lstrip(".")
    level = len(name.value) - len(rest)
    if level == 0:
        return Imported(line, rest)  # importlib ignores package= for an absolute name
    if is_this_package:
        return Imported(line, rest, level)
    if not constant(package, str) or not package.value:
        return Imported(line, error="relative import_module name without package= cannot be resolved")
    bits = package.value.rsplit(".", level - 1)
    if len(bits) < level:
        return Imported(line, error=f"import_module('{name.value}', '{package.value}') climbs beyond the top-level package")
    return Imported(line, f"{bits[0]}.{rest}" if rest else bits[0])


def resolve_dunder_import(call: ast.Call) -> Imported:
    """__import__(name, globals, locals, fromlist, level=0)."""
    line, name, level = call.lineno, argument(call, 0, "name"), argument(call, 4, "level")
    if not constant(name, str):
        return Imported(line, error="dynamic import with a non-constant name cannot be checked")
    if level is not None and not constant(level, int):
        return Imported(line, error="__import__ with a non-constant level cannot be checked")
    return Imported(line, name.value, level.value if level is not None else 0)


def imported_modules(tree: ast.AST) -> list[Imported]:
    """Every import site in a module: statements and dynamic imports through tracked names."""
    names = import_bindings(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(Imported(node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append(Imported(node.lineno, node.module or "", node.level))
        elif isinstance(node, ast.Call):
            kind = binding(node.func, names)
            if kind == IMPORT_MODULE:
                found.append(resolve_import_module(node))
            elif kind == DUNDER_IMPORT:
                found.append(resolve_dunder_import(node))
    return found


def check_unit(root: Path, unit: str) -> tuple[list[Violation], int]:
    """Lint one unit directory. Returns (violations, files checked)."""
    unit_dir = root / unit
    files, links = [], []
    for directory in ("tsb_tui", "tests", "bench"):
        found, symlinks = walk_unit(unit_dir / directory)
        files.extend(found)
        links.extend(symlinks)
    shipped, where = local_names(files, unit_dir)
    violations = [Violation(p.relative_to(root), 1,
                            f"symlink under {unit}/ — not allowed: a link can alias code this lint does not check")
                  for p in links]

    reserved = REPO_UNITS | set(ALLOWED[unit])
    for name in sorted(set(shipped) & reserved):
        violations.append(Violation(where[name].relative_to(root), 1,
                                    f"{unit}/ defines top-level '{name}', which shadows a TSB unit or an allowlisted package"))
    own = {n: s for n, s in shipped.items() if n not in reserved}

    for path in files:
        shown = path.relative_to(root)
        try:
            tree = ast.parse(path.read_bytes(), filename=str(path))
        except (SyntaxError, ValueError) as exc:
            line = getattr(exc, "lineno", None) or 1
            violations.append(Violation(shown, line, f"cannot parse, so cannot check: {exc}"))
            continue
        test = is_test_file(path, unit_dir)
        for site in imported_modules(tree):
            line, module = site.line, site.module
            if site.error:
                violations.append(Violation(shown, line, site.error))
                continue
            if site.level:
                if site.level > package_depth(path, unit_dir):
                    violations.append(Violation(
                        shown, line, f"relative import (level {site.level}) climbs out of {unit}/'s packages"))
                continue
            top = module.split(".", 1)[0]
            update_allowed = (shown.as_posix() == "tsb_tui/update_check.py"
                              and module in UPDATE_CHECK_ALLOWED)
            if top in NETWORK_STDLIB[unit] and not test and not update_allowed:
                violations.append(Violation(
                    shown, line, f"{unit}/ may not import '{module}' — network calls go through tsb_contract_client"))
                continue
            if top in STDLIB or top in ALLOWED[unit] or (test and top in TEST_ALLOWED):
                continue
            if top in own and (own[top] or test):
                continue
            hint = " (defined only in tests)" if top in own else ""
            allowed = ", ".join(["stdlib", *ALLOWED[unit], *(TEST_ALLOWED if test else ())])
            violations.append(Violation(
                shown, line,
                f"{unit}/ may not import '{module}'{hint} (allowed: {allowed}, and {unit}/'s own packages)"))
    return violations, len(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="repository root (default: this checkout)")
    args = parser.parse_args(argv)

    missing = [unit for unit in ("tsb_tui", "tests", "bench") if not (args.root / unit).is_dir()]
    if missing:
        print(f"import-lint: ERROR: {', '.join(u + '/' for u in missing)} not found under {args.root}"
              " — wrong --root, or a unit was split out and this lint must be updated", file=sys.stderr)
        return 2

    violations, counts = [], []
    for unit in ALLOWED:
        found, checked = check_unit(args.root, unit)
        violations.extend(found)
        counts.append(f"{unit}/ {checked} file{'s' * (checked != 1)}")

    for violation in violations:
        print(violation, file=sys.stderr)
    summary = ", ".join(counts)
    if violations:
        print(f"import-lint: FAIL — {len(violations)} violation(s) ({summary})", file=sys.stderr)
        return 1
    print(f"import-lint: clean ({summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
