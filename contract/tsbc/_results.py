"""`--provider-results`: what a provider's own tests did, as the runners wrote it.

A reference fixture is graded by the provider's own test that checks it, and the caller's word that
those tests ran is not evidence: the contract reads the runners' own output. Three shapes, with no
converter in the provider's repo between a run and its grade:

- `go test -json` events, one JSON object per line (`Action` pass/fail/skip, `Package`, `Test`).
  An event names its package and its test, never a file, so the package's directory is what an
  index entry's `file` is matched on: `core/internal/alert/stream_test.go` is in the package
  directory `core/internal/alert`, and a result for the same test name in another directory is no
  match for it. A package-level event carries no `Test`, so a bare "the package passed" is never a
  citation passing.
- pytest's JUnit XML (`--junitxml`), where the test case is its pytest node id and the case is
  placed by its `file` attribute when it carries one. A writer that omits `file` (pytest's default
  `junit_family` is xunit2, which does) is placed by its `classname` instead: the dotted module path,
  optionally with `.Class`, that the citation's own file builds. A parametrized case is cited by the
  prefix before `[param]`, so the reader keeps every case that ran and requires all of them to have
  passed, from the same file only.
- a small JSON list `[{unit, file, test_id, outcome}]`, what the harness writes from a rig's own
  stamps: U11's "tests" are rigs, and a rig stamps what it did in the run it is evidence for.

A JUnit file names no unit, so its cases are held under `pytest` and identified by (file, test id);
an index entry names the unit its own fixture does, and the entry that matches is the one whose
file and test are the cited ones.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .schema import loads

PYTEST = "pytest"  # the unit a JUnit file's cases are held under: it names none of its own

# Every result a run read, keyed (unit, directory or file, test id, classname) -> every outcome.
# The value keeps each outcome recorded, not just the last: a test can be retried or re-run across
# files, and a later pass must not hide an earlier failure.
RESULTS: dict[tuple[str, str, str, str], list[str]] = {}
# Parse problems, each naming its source. A results file that cannot be read stops the run: read as
# "no tests ran" it would leave a fixture UNGRADED for a reason of the runner's own, or — worse —
# let a caller pass a truncated file and have the citation it holds count.
PROBLEMS: list[str] = []


def _directory(file: str) -> str:
    """The directory a file lives in, as the index wrote it: `core/internal/alert/stream_test.go`
    is in `core/internal/alert`. A Go package's import path ends with this same directory."""
    return file.rsplit("/", 1)[0] if "/" in file else ""


def _in_package(package: str, directory: str) -> bool:
    """Whether a `go test -json` Package names the package a file's directory is: the import path
    ends with the directory, so a file in `core/internal/alert` is in the package whose path ends
    `.../core/internal/alert`, and a same-named test in another package is not a match for it."""
    return bool(directory) and (package == directory or package.endswith("/" + directory))


@dataclass
class Results:
    """One run's test evidence, as the runners wrote it."""

    units: set[str] = field(default_factory=set)
    # What this read found wrong, kept on the value as well as in PROBLEMS (which the reader clears
    # per read): a caller that keeps several reads — a test grading each one — must be able to see
    # what its own read found, not what an earlier one left behind.
    problems: list[str] = field(default_factory=list)

    def outcome(self, unit: str, file: str, test_id: str) -> str | None:
        """pass/fail/skip for a cited test, or None when this run holds no result for it.

        In order: a rig by its own name; an exact result under the cited unit; the same test in
        the package directory the file is in (how a Go event names it, whether or not pytest wrote
        the file itself); the pytest key its own classname builds (pytest's default JUnit output
        names no file); a parametrized pytest case by the node id's prefix, placed by the cited
        file, or by its classname when the JUnit case carries no file, where the class is checked
        for a JUnit case of a class citation, every case that ran having passed; and a Go subtest
        by the exact ID
        `go test -json` reports. Every result recorded for one citation is one verdict: any
        failure among them fails.
        """
        if test_id.startswith("rig-"):
            return _verdict(*_all(unit, file, test_id), *_all(unit, "", test_id))
        # A `go test -json` event names a subtest the way Go wrote it: spaces in the citation's
        # subtest name become underscores, so a `Parent/sub test` citation matches `Parent/sub_test`.
        name = test_id.replace(" ", "_")
        got = _verdict(*_all(unit, file, name))
        if got is None and _directory(file):
            # A `go test -json` event names no unit (its Package is the package, the key's own
            # unit is ""), so the cited unit cannot be required of it: the (package, test) pair
            # is what identifies the result, and the index entry named both. A subtest needs a
            # result for its exact ID: its parent's result is not its own.
            got = _verdict(*[o for (u, where, test, _class), outcomes in RESULTS.items()
                             if test == name and _in_package(where, _directory(file))
                             and u in (unit, PYTEST, "") for o in outcomes])
        if got is None:
            # pytest's own key: its classname names the module (`test_sample`), its `file`
            # attribute the path (`test_sample.py`), so a citation's file is either.
            for candidate in (file, file[:-3] if file.endswith(".py") else file + ".py"):
                got = _verdict(*_all(PYTEST, candidate, name))
                if got is not None:
                    break
        if got is None:
            # pytest's default JUnit output (xunit2) carries no `file`: the case is placed by the
            # classname the citation's file and class build, and compared the other way round.
            got = _verdict(*_all_classname(file, name))
        if got is not None:
            return got
        expected, method, klass = _expected_classname(file, name)
        cases: list[str] = []
        for (_u, where, test, classname), outcomes in RESULTS.items():
            if test.startswith(name + "[") and _same_file(where, file):
                # The whole citation as the index wrote it, in the cited file: a results-list record
                # keeps the full node id with its `[param]`, so this is the rule that places it.
                cases.extend(outcomes)
            elif klass and _u == PYTEST and test.startswith(method + "[") and (
                    (where and _same_file(where, file) and classname.rsplit(".", 1)[-1] == klass)
                    or (not where and classname == expected)):
                # A JUnit case names the method alone, so its class must be the cited one: the last
                # segment of the classname for a case with a `file`, the whole classname for xunit2.
                cases.extend(outcomes)
            elif not klass and _u == PYTEST and not where and test.startswith(method + "[") \
                    and classname == expected:
                # xunit2, no class cited: the module classname places the case at the cited file.
                cases.extend(outcomes)
        if cases:
            return _verdict(*cases)
        return None


def _all(unit: str, where: str, test_id: str) -> list[str]:
    """Every outcome recorded for one exact key, whatever classname it came with."""
    return [o for (u, w, t, _class), outcomes in RESULTS.items()
            if u == unit and w == where and t == test_id for o in outcomes]


def _expected_classname(file: str, test_id: str) -> tuple[str, str, str]:
    """The `classname` pytest would write for a citation's file and class, the case's own `name`,
    and the class part: the module is the file without `.py` and with `/` as `.`; a `Class::method`
    citation makes the classname `<module>.<Class>` and the name the method alone."""
    module = file.removesuffix(".py").replace("/", ".")
    klass, name = ("", test_id)
    if "::" in test_id:
        klass, name = test_id.split("::", 1)
    return (f"{module}.{klass}" if klass else module), name, klass


def _all_classname(file: str, test_id: str) -> list[str]:
    """Every outcome for a citation whose JUnit case carried no `file` (pytest's default output):
    the citation's file and class build the `classname` pytest would have written, and a `::method`
    tail is the case's `name`. A case that did carry a `file` is matched too, by its classname's
    last segment, since a class test's `name` is the method alone."""
    expected, name, klass = _expected_classname(file, test_id)
    found: list[str] = []
    for (unit, where, test, classname), outcomes in RESULTS.items():
        if unit != PYTEST or test != name:
            continue
        if where:
            if klass and _same_file(where, file) and classname.rsplit(".", 1)[-1] == klass:
                found.extend(outcomes)
        elif classname == expected:
            found.extend(outcomes)
    return found


def _verdict(*outcomes: str | None) -> str | None:
    """One verdict for every result a citation matched. A missing result (None) says nothing:
    several results for one citation are one verdict, so a stray absence never hides a failure,
    and a pass needs every result that was written to have passed. Any fail is fail; every pass is
    pass; anything else (a skip, with no fail) is skip, whatever order the results came in."""
    known = [o for o in outcomes if o is not None]
    if not known:
        return None
    if any(o == "fail" for o in known):
        return "fail"
    return "pass" if all(o == "pass" for o in known) else "skip"


def _same_file(of: str, file: str) -> bool:
    """Whether a result's own file names the cited one: the same path, or the same path with a
    `.py` added or dropped (pytest's classname names a module, the `file` attribute its path).
    Two files in one directory are not the same file: a same-named case in a sibling is no match."""
    if not of or not file:
        return False
    return of == file or of == file[:-3] or of + ".py" == file


def _read(path: Path, source: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        PROBLEMS.append(f"{source}: cannot be read ({exc})")
        return
    if path.suffix in (".xml", ".junit", ".junitxml"):
        _read_junit(text, source)
    else:
        _read_lines(text, source)


def _read_lines(text: str, source: str) -> None:
    """Newline-delimited JSON (what `go test -json` writes), or a JSON list of result records."""
    if text.lstrip().startswith("["):
        try:
            records = loads(text)
        except ValueError as exc:
            PROBLEMS.append(f"{source}: not valid JSON: {exc}")
            return
        if not isinstance(records, list):
            PROBLEMS.append(f"{source}: a results list must be a JSON array")
            return
        for i, record in enumerate(records):
            key = _key(record, f"{source}[{i}]")
            if key is not None:
                _record(key, record["outcome"])
        return
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = loads(line)
        except ValueError as exc:
            PROBLEMS.append(f"{source}: not valid JSON: {exc}")
            return
        _event(event, source)


def _record(key: tuple[str, str, str, str], outcome: str) -> None:
    """Keep one more outcome for a key. A test can be retried or re-run across files, so the key
    holds every outcome recorded, never only the last: a later pass must not hide an earlier fail."""
    RESULTS.setdefault(key, []).append(outcome)


def _key(record, source: str) -> tuple[str, str, str, str] | None:
    """(unit, file, test_id, classname) from one result record, or None when it is not one. A
    result list names no class, so its classname is empty."""
    if not isinstance(record, dict):
        PROBLEMS.append(f"{source}: a result record is not an object")
        return None
    unit, test_id, file = record.get("unit"), record.get("test_id"), record.get("file", "")
    if not isinstance(unit, str) or not isinstance(test_id, str) or not unit or not test_id:
        PROBLEMS.append(f"{source}: a result record needs a non-empty 'unit' and 'test_id'")
        return None
    if not isinstance(file, str):
        PROBLEMS.append(f"{source}: {unit} {test_id} names a 'file' that is not a string")
        return None
    if record.get("outcome") not in ("pass", "fail", "skip"):
        PROBLEMS.append(f"{source}: {unit} {test_id} has no outcome of pass/fail/skip")
        return None
    return unit, file, test_id, ""


def _event(event, source: str) -> None:
    if not isinstance(event, dict):
        PROBLEMS.append(f"{source}: a `go test -json` line is not a JSON object")
        return
    action, test, package = event.get("Action"), event.get("Test"), event.get("Package")
    if action not in ("pass", "fail", "skip"):
        return
    if not isinstance(test, str) or not test:
        return  # a package-level event: the package's verdict, never a test's
    if not isinstance(package, str) or not package:
        PROBLEMS.append(f"{source}: the test {test} came with no package")
        return
    _record(("", package.rstrip("/"), test, ""), action)


def _read_junit(text: str, source: str) -> None:
    """pytest's JUnit XML: each case wherever it sits in the file. A case's `file` attribute names
    the module it was collected from. pytest's default `junit_family` (xunit2) writes none, and then
    the case's `classname` — the dotted module path, with `.Class` for a method — is what the
    citation's own file builds, matched the other way round in `_all_classname`. A case that names neither
    a test nor a file or classname cannot be placed and is reported."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        PROBLEMS.append(f"{source}: not valid XML: {exc}")
        return
    for case in root.iter("testcase"):
        name, file = case.get("name") or "", case.get("file") or ""
        classname = case.get("classname") or ""
        if not name or not (file or classname):
            PROBLEMS.append(f"{source}: a test case names neither a file nor a test")
            continue
        outcome = "fail" if case.find("failure") is not None or case.find("error") is not None \
            else "skip" if case.find("skipped") is not None else "pass"
        _record((PYTEST, file, name, classname), outcome)


def read(paths: list[Path]) -> Results:
    """Read every results file this run was given, in the order given, into one index."""
    RESULTS.clear()
    PROBLEMS.clear()
    for path in paths:
        if not path.is_file():
            PROBLEMS.append(f"{path.name}: not a file")
            continue
        _read(path, path.name)
    return Results(units={unit for unit, _where, _test, _class in RESULTS}, problems=list(PROBLEMS))
