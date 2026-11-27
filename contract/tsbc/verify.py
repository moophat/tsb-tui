"""`python -m tsbc verify` — grade a provider against the contract, or the contract itself.

    python -m tsbc verify --self-check [--profile eN]        the tree, offline
    python -m tsbc verify URL --profile eN [--artifacts DIR]  a running core (+ collected artefacts)
    python -m tsbc verify --artifacts DIR --profile eN        collected artefacts only
    python -m tsbc verify --list [--profile eN]               every fixture with its mark

Per fixture the result is one of:
  PASS / FAIL   graded by this run (live, from artefacts, or with the fixture its grade names);
  SKIP          published, not yet required green in the profile — nothing else is ever SKIP;
  PROVIDER      a reference fixture every entry in the index grades as passed in this run's
                results (--provider-results), so a cited test that ran and failed is FAIL, one
                that did not run is UNGRADED, and an owed entry is never graded;
  UNGRADED      required green, but the reference fixture's cited tests are not part of this run.
                --provider-tests alone grades no reference fixture: it stays accepted and is no
                longer sufficient, and the line under such a result points at --provider-results.
A required-green fixture whose input is missing (no URL, no --artifacts, an unset credential) is
FAIL "input missing", never SKIP.

Exit status: 0 — every required-green fixture of the profile was graded and passed; 1 — any FAIL;
3 — nothing failed, but some required-green fixtures were not graded by this run (UNGRADED, or
filtered out by --seams): a partial run never reads as green; 2 — usage error or unreadable tree.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from . import _serve, profiles, selfcheck
from ._http import HttpClient
from ._run import INPUTS, InputMissing, Run
from .fixtures import (
    SEAMS,
    Fixture,
    TreeError,
    contract_root,
    contract_version,
    load_fixtures,
)
from .schema import Registry, SchemaError

DEFAULT_PROFILE = "e3"  # the epoch this tag pins

PASS, FAIL, SKIP, PROVIDER, UNGRADED = "PASS", "FAIL", "SKIP", "PROVIDER", "UNGRADED"
OUTCOMES = (PASS, FAIL, SKIP, PROVIDER, UNGRADED)
EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_NOT_ALL_GRADED = 0, 1, 2, 3


@dataclass
class Result:
    fixture: Fixture
    outcome: str
    detail: str = ""

    @property
    def input_missing(self) -> bool:
        return self.outcome == FAIL and "input missing" in self.detail


def _graded_by(fx: Fixture) -> str:
    return "; ".join(f"{g['unit']} {g['test']}" for g in fx.doc.get("graded_by", [])) or "no provider test named"


def _units(units) -> str:
    """U2,U5,U11 — in unit order, not string order."""
    return ",".join(sorted(units, key=lambda u: (len(u), u)))


def _grade(fx: Fixture, run: Run) -> Result:
    """The fixture's result, its detail stripped of every secret this run read: a provider that
    echoes a submitted password in an error message must not get it printed."""
    result = _grade_one(fx, run)
    return Result(fx, result.outcome, run.redact(result.detail))


def _indexed(fx: Fixture, run: Run) -> list[dict]:
    """The index entries for a reference fixture, read once per run. A tree with no index gives
    an empty list, which the rule below treats as UNGRADED: a fixture whose cited tests cannot be
    found is not a fixture that passes."""
    if "index" not in run.state:
        run.state["index"] = selfcheck.load_index(run.root, run.registry)
    return run.state["index"].get(f"{fx.seam}/{fx.name}", [])


def _reference(fx: Fixture, run: Run) -> Result:
    """A reference fixture is graded by the provider's own test, and the run checks it rather than
    taking the caller's word: every index entry must appear as passed in this run's results.

    A cited test that ran and failed fails the fixture — the provider ran the very test that
    proves the clause and it did not hold. One absent from the results is UNGRADED: nothing proves
    the claim, and nothing failed either, so it is never green. An owed entry never grades.
    """
    entries = _indexed(fx, run)
    if not entries:
        return Result(fx, UNGRADED, f"graded only by {_graded_by(fx)}, and no --provider-results cite "
                                    f"that test; give --provider-results for the run's own results")
    failed, missing, owed = [], [], []
    for entry in entries:
        unit = entry.get("unit")
        test_id, file = entry.get("test_id"), entry.get("file", "")
        if "owed" in entry:
            owed.append(f"{unit} {test_id or entry['owed']}")
            continue
        outcome = run.provider_results.outcome(unit, file, test_id) if run.provider_results else None
        if outcome == "pass":
            continue
        (failed if outcome in ("fail", "skip") else missing).append(f"{unit} {file} {test_id}")
    if failed:
        return Result(fx, FAIL, "the provider test that grades it did not pass: " + "; ".join(failed))
    if missing:
        return Result(fx, UNGRADED, "the cited test did not run: " + "; ".join(missing)
                                    + " — give --provider-results for the run's own results")
    if owed:
        return Result(fx, UNGRADED, "the test that grades it is owed (" + "; ".join(owed) + ")")
    return Result(fx, PROVIDER, "graded by " + "; ".join(f"{e.get('unit')} {e.get('test_id')}"
                                                          for e in entries if "test_id" in e))


def _grade_one(fx: Fixture, run: Run) -> Result:
    if fx.grade == "reference":
        return _reference(fx, run)
    if fx.grade == "live":
        if not run.url:
            return Result(fx, FAIL, "input missing: no URL to grade against")
        from . import _exchange
        try:
            _exchange.execute(fx, run)
        except InputMissing as exc:
            return Result(fx, FAIL, str(exc))
        except _exchange.Failed as exc:
            return Result(fx, FAIL, str(exc))
        return Result(fx, PASS)
    if fx.grade.startswith("artifacts:"):
        if run.artifacts is None:
            return Result(fx, FAIL, "input missing: no --artifacts directory")
        from . import artifacts
        ok, detail = artifacts.grade(fx.grade.split(":", 1)[1], run)
        return Result(fx, PASS if ok else FAIL, detail)
    # A fixture of an unknown grade cannot pass self-check, so this is unreachable on a valid tree.
    return Result(fx, FAIL, f"unknown grade {fx.grade!r}")


def _grade_all(fixtures: list[Fixture], run: Run, seams: set[str]) -> tuple[list[Result], list[Fixture]]:
    """(results in fixture order, required fixtures filtered out by --seams).

    Results are keyed by the fixture's file, never by its name: were two fixtures to share a name,
    one result must not silently replace the other. (main refuses such a tree before grading.)"""
    results: dict[str, Result] = {}
    filtered, deferred = [], []
    for fx in fixtures:
        state = profiles.status(fx.mark, run.profile)
        if state == profiles.FUTURE:
            continue
        if fx.seam not in seams:
            if state == profiles.REQUIRED:
                filtered.append(fx)
            continue
        if state == profiles.PUBLISHED:
            results[fx.rel] = Result(fx, SKIP, f"{profiles.describe(fx.mark)}; not required green in "
                                               f"profile {run.profile}")
        elif fx.grade.startswith("with:"):
            deferred.append(fx)
        else:
            results[fx.rel] = _grade(fx, run)
    by_name = {fx.name: fx.rel for fx in fixtures}
    for fx in deferred:
        name = fx.grade.split(":", 1)[1]
        other = results.get(by_name.get(name, ""))
        if other is None or other.outcome not in (PASS, FAIL):
            why = "filtered out by --seams" if other is None else other.outcome
            results[fx.rel] = Result(fx, UNGRADED, f"graded with {name}, which this run did not grade ({why})")
        else:
            detail = f"graded with {name}" + (f": {other.detail}" if other.detail else "")
            results[fx.rel] = Result(fx, other.outcome, detail)
    order = {fx.rel: i for i, fx in enumerate(fixtures)}
    return sorted(results.values(), key=lambda r: order[r.fixture.rel]), filtered


def _print_results(results: list[Result], filtered: list[Fixture], run: Run, out) -> int:
    width = max((len(r.fixture.label) for r in results), default=0)
    for r in results:
        tail = f"  {r.detail}" if r.detail else ""
        print(f"{r.outcome:<8}  {r.fixture.label:<{width}}{tail}", file=out)
    counts = {k: sum(1 for r in results if r.outcome == k) for k in OUTCOMES}
    missing = sum(1 for r in results if r.input_missing)
    print(f"summary: {counts[PASS]} PASS, {counts[FAIL]} FAIL ({missing} of them input missing), "
          f"{counts[SKIP]} SKIP, {counts[PROVIDER]} PROVIDER, {counts[UNGRADED]} UNGRADED (profile {run.profile})",
          file=out)
    not_graded = counts[UNGRADED] + len(filtered)
    if not_graded:
        print(f"{not_graded} required-green fixtures not graded by this run: {counts[UNGRADED]} graded only by "
              f"the provider results this run did not include, {len(filtered)} filtered out by --seams", file=out)

    graded, input_missing, ungraded = [], [], []
    for seam in SEAMS:
        mine = [r for r in results if r.fixture.seam == seam and r.outcome != SKIP]
        cut = [f for f in filtered if f.seam == seam]
        if cut:
            ungraded.append(f"{seam} (filtered out by --seams)")
        elif any(r.input_missing for r in mine):
            input_missing.append(f"{seam} ({sum(r.input_missing for r in mine)} fixtures)")
        elif any(r.outcome == UNGRADED for r in mine):
            ungraded.append(f"{seam} ({sum(r.outcome == UNGRADED for r in mine)} graded only by the "
                            "provider results)")
        elif mine:
            graded.append(seam)
    print(f"seams graded: {', '.join(graded) or 'none'}", file=out)
    print(f"seams with input missing: {', '.join(input_missing) or 'none'}", file=out)
    print(f"seams not graded by this run: {', '.join(ungraded) or 'none'}", file=out)
    for warning in run.warnings:
        print(f"warning: {run.redact(warning)}", file=out)
    if counts[FAIL]:
        return EXIT_FAIL
    return EXIT_NOT_ALL_GRADED if not_graded else EXIT_OK


def _list(fixtures: list[Fixture], profile: str, out) -> None:
    width = max((len(f.label) for f in fixtures), default=0)
    grade_width = max((len(f.grade) for f in fixtures), default=0)
    for fx in fixtures:
        state = profiles.status(fx.mark, profile) if not profiles.check_mark(fx.mark) else "bad-mark"
        print(f"{fx.label:<{width}}  {fx.kind:<9} {fx.grade:<{grade_width}}  {profiles.describe(fx.mark):<36} "
              f"[{state} in {profile}]", file=out)
    print(f"{len(fixtures)} fixtures", file=out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tsbc", description="TSB contract runner (stdlib only)")
    sub = parser.add_subparsers(dest="command", required=True)
    _serve.add_serve_parser(sub)
    v = sub.add_parser("verify", help="grade a provider, collected artefacts, or the contract tree")
    v.add_argument("url", nargs="?", help="base URL of the core, e.g. https://tsb.example")
    v.add_argument("--profile", default=DEFAULT_PROFILE, choices=profiles.EPOCHS,
                   help=f"epoch whose required-green fixtures are asserted (default {DEFAULT_PROFILE})")
    v.add_argument("--self-check", action="store_true", help="validate schemas and fixtures offline")
    v.add_argument("--artifacts", type=Path, metavar="DIR", help="collected artefacts (README 'Artefacts')")
    v.add_argument("--list", action="store_true", help="list fixtures with their marks and exit")
    v.add_argument("--seams", help="comma-separated seams to grade, e.g. C1,C2 (default: all)")
    v.add_argument("--provider-tests", metavar="UNITS",
                   help="comma-separated units (e.g. U2,U5) whose own tests ran and passed with this run; "
                        "the caller vouches for it. Reference fixtures those tests grade count as graded")
    v.add_argument("--provider-results", type=Path, metavar="FILE", action="append", default=[],
                   help="the run's own test results, as the runners wrote them: `go test -json` output, "
                        "pytest's --junitxml, or a JSON list [{unit, file, test_id, outcome}]. A reference "
                        "fixture is graded only when every test its index entry cites appears there as passed")
    v.add_argument("--ca-file", help="PEM bundle to verify the core's TLS certificate against")
    v.add_argument("--target", help=f"target id for session fixtures (default ${INPUTS['target_id'][1]})")
    v.add_argument("--timeout", type=float, default=10.0, help="seconds per network wait (default 10)")
    v.add_argument("--contract-dir", help="contract tree to use (default: the one next to this package)")
    return parser


def main(argv: list[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return _serve.serve(args.bind, args.port, args.static, out)
    try:
        root = contract_root(args.contract_dir)
    except TreeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    seams = set(SEAMS)
    if args.seams:
        seams = {s.strip().upper() for s in args.seams.split(",") if s.strip()}
        unknown = seams - set(SEAMS)
        if unknown:
            print(f"error: unknown seam(s) {', '.join(sorted(unknown))}", file=sys.stderr)
            return EXIT_USAGE
    provider_tests = frozenset(u.strip().upper() for u in (args.provider_tests or "").split(",") if u.strip())
    if args.list:
        fixtures, errors = load_fixtures(root)
        _list(fixtures, args.profile, out)
        for err in errors:
            print(f"error: {err}", file=out)
        return EXIT_FAIL if errors else EXIT_OK

    if not (args.self_check or args.url or args.artifacts):
        print("error: nothing to verify: give a URL, --artifacts DIR, or --self-check", file=sys.stderr)
        return EXIT_USAGE

    status = EXIT_OK
    if args.self_check:
        report = selfcheck.run(root, args.profile)
        for line in report.lines:
            print(line, file=out)
        for failure in report.failures:
            print(f"  FAIL {failure}", file=out)
        print(f"self-check: {'PASS' if report.ok else 'FAIL'} ({len(report.failures)} problems)", file=out)
        if not report.ok:
            return EXIT_FAIL

    if args.url or args.artifacts:
        if args.artifacts is not None and not args.artifacts.is_dir():
            print(f"error: --artifacts {args.artifacts} is not a directory", file=sys.stderr)
            return EXIT_USAGE
        if args.url:
            try:
                HttpClient(args.url)  # the scheme, host and cleartext rules, before anything is sent
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return EXIT_USAGE
        try:
            registry = Registry.from_tree(root)
        except SchemaError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        problems, _ = selfcheck.schema_problems(registry)
        if problems:  # never grade against a schema the validator cannot fully enforce
            for problem in problems:
                print(f"error: {problem}", file=sys.stderr)
            return EXIT_USAGE
        fixtures, errors = load_fixtures(root)
        if errors:
            for err in errors:
                print(f"error: {err}", file=sys.stderr)
            return EXIT_USAGE
        # The run's own test results, read before anything is graded: a reference fixture is graded
        # by what they say, and a file that cannot be read stops the run rather than being read as
        # "no tests ran" — which would grade a fixture on nothing.
        from . import _results
        provider_results = _results.read(args.provider_results)
        for problem in _results.PROBLEMS:
            print(f"error: {problem}", file=sys.stderr)
        if _results.PROBLEMS:
            return EXIT_USAGE
        # Grade only a tree that is itself valid: every fixture inside its schema with a well-formed
        # mark, names unique, grades coherent. A malformed fixture must stop the run with a usage
        # error, never crash it, and never collide with another fixture's result.
        report = selfcheck.run(root, args.profile)
        if not report.ok:
            for failure in report.failures:
                print(f"error: {failure}", file=sys.stderr)
            print("error: the contract tree fails self-check; nothing is graded against it", file=sys.stderr)
            return EXIT_USAGE
        overrides = {"target_id": args.target} if args.target else {}
        run = Run(root, registry, args.profile, args.url.rstrip("/") if args.url else None,
                  args.artifacts, args.ca_file, args.timeout, overrides, provider_tests,
                  provider_results)
        print(f"tsbc verify: {contract_version(root)}, profile {args.profile}, "
              f"url {'given' if args.url else 'none'}, artifacts {'given' if args.artifacts else 'none'}, "
              f"provider tests {_units(provider_tests) or 'none'}, provider results "
              f"{len(args.provider_results)} file(s)", file=out)
        results, filtered = _grade_all(fixtures, run, seams)
        from . import _exchange
        deleted = _exchange.sweep(run) if run.url else 0
        status = _print_results(results, filtered, run, out)
        if deleted:
            print(f"cleanup: deleted {deleted} sessions this run created and left open", file=out)
    return status
