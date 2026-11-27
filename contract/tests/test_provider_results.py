"""`--provider-results`: a reference fixture is graded by what a run's own results say.

The unit a fixture names is who owes the test; the results are the evidence it ran and passed. A
cited test that ran and failed must fail the fixture, one that did not run must never be green, and
a result in another file must never stand in for it: those are the three ways a fixture could read
PROVIDER while the clause it encodes was never checked.

Every reader shape is exercised here against its own runner's output, written by that runner's own
rules (the Go event's `Package`, pytest's `file` and `[param]` names, the harness's small JSON list)
rather than by a hand-fed key, so a reader that keyed its own store would match its own typo.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tsbc import _results, verify
from tsbc._run import Run
from tsbc.fixtures import load_fixtures
from tsbc.schema import Registry

from . import CONTRACT


def go_json(*rows) -> str:
    """A `go test -json` stream: one event per line, `Package` the full import path.

    A package-level `pass` carries no `Test` and is not a result: a "the package passed" line can
    never be read as a cited test passing."""
    lines = []
    for package, test in rows:
        pkg = f"github.com/moophat/tsb-core/{package}"
        if test is None:
            lines.append(json.dumps({"Action": "pass", "Package": pkg, "Elapsed": 0.1}))
            continue
        lines.append(json.dumps({"Action": "run", "Package": pkg, "Test": test}))
        lines.append(json.dumps({"Action": "pass", "Package": pkg, "Test": test, "Elapsed": 0.1}))
    return "\n".join(lines) + "\n"


def go_json_failed(package: str, test: str) -> str:
    pkg = f"github.com/moophat/tsb-core/{package}"
    return "\n".join([json.dumps({"Action": "run", "Package": pkg, "Test": test}),
                      json.dumps({"Action": "fail", "Package": pkg, "Test": test, "Elapsed": 0.1})]) + "\n"


def junit_xml(*cases) -> str:
    """pytest's `--junitxml` shape: `file` is the module path, `name` the pytest node id, and a
    parametrized case carries its `[param]` suffix. A failure is a child <failure> element."""
    body = "\n".join(
        f'    <testcase classname="{classname}" file="{file}" name="{name}" time="0.1">'
        + ("</testcase>" if outcome == "pass" else f'<{"failure" if outcome == "fail" else "skipped"} '
           f'message="x"/></testcase>')
        for file, classname, name, outcome in cases)
    return f'<?xml version="1.0" encoding="utf-8"?>\n<testsuites name="pytest tests">\n' \
           f'  <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="{len(cases)}">\n' \
           f"{body}\n  </testsuite>\n</testsuites>\n"


def junit_xml_module_from_classname(name: str) -> str:
    """A writer that omits `file`: pytest's own classname (`test_sample.Class::test_a`'s
    `test_sample.Class`) is what a reader can place the case by."""
    return f'<?xml version="1.0"?><testsuites><testsuite name="pytest">' \
           f'<testcase classname="test_sample" name="{name}"/></testsuite></testsuites>'


def junit_xunit2(*cases) -> str:
    """pytest's default `--junitxml` (`junit_family=xunit2`): no `file` attribute, so the classname
    is the whole dotted module path (`worker.tests.test_keepalive`), with `.Class` for a method."""
    body = "\n".join(
        f'    <testcase classname="{classname}" name="{name}" time="0.1">'
        + ("</testcase>" if outcome == "pass" else f'<{"failure" if outcome == "fail" else "skipped"} '
           f'message="x"/></testcase>')
        for classname, name, outcome in cases)
    return f'<?xml version="1.0" encoding="utf-8"?>\n<testsuites name="pytest tests">\n' \
           f'  <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="{len(cases)}">\n' \
           f"{body}\n  </testsuite>\n</testsuites>\n"


def results_for(*paths) -> _results.Results:
    return _results.read([Path(p) for p in paths])


def provider_results(dest: Path) -> list[Path]:
    """A results file that vouches for every cited test in the tree, for a test that grades the
    runner rather than a provider. It is built from the index itself, so it always matches the
    citations a run will look up; a fixture's real provider runs its own tests and writes their
    own output, which is the whole point of reading it instead of trusting a list."""
    index = json.loads((CONTRACT / "fixtures" / "graded-by.json").read_text(encoding="utf-8"))
    rows = [{"unit": e["unit"], "file": e["file"], "test_id": e["test_id"], "outcome": "pass"}
            for entries in index.values() for e in entries if "test_id" in e]
    path = dest / "provider-results.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return [path]


class Readers(unittest.TestCase):
    """Each runner's output read into one index, and each refusal of a malformed one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def read(self, *names: str) -> tuple[_results.Results, list]:
        results = results_for(*[self.dir / n for n in names])
        return results, list(_results.PROBLEMS)

    # ---- go test -json -------------------------------------------------------------------------

    def test_a_go_event_names_its_package_and_test(self):
        # The index cites a file; a Go event cites the package that directory is. The two are the
        # same place, and nothing else in the results file says which file a test was in.
        results, problems = self.read(self.write("go.jsonl", go_json(
            ("core/internal/alert", "TestAlertStreamEndsOnLogout"))).name)
        self.assertEqual(problems, [])
        self.assertEqual(results.outcome("U2", "core/internal/alert/stream_test.go",
                                         "TestAlertStreamEndsOnLogout"), "pass")
        # The same test name in another directory is not a match for a citation naming this one.
        self.assertIsNone(results.outcome("U2", "core/internal/session/stream_test.go",
                                          "TestAlertStreamEndsOnLogout"))

    def test_a_go_package_verdict_is_not_a_test_result(self):
        # `ok <pkg>` with no Test: the package passed, which says nothing about the cited test —
        # a reader that took it for a result would grade every fixture of that package green.
        results, _ = self.read(self.write("go.jsonl", go_json(("core/internal/alert", None))).name)
        self.assertIsNone(results.outcome("U2", "core/internal/alert/stream_test.go",
                                          "TestAlertStreamEndsOnLogout"))

    def test_a_failing_go_test_fails_the_citation(self):
        results, _ = self.read(self.write(
            "go.jsonl", go_json_failed("core/internal/record",
                                       "TestReopenCountsPastTheCommittedOffset")).name)
        self.assertEqual(results.outcome("U2", "core/internal/record/gap_test.go",
                                         "TestReopenCountsPastTheCommittedOffset"), "fail")

    def test_a_go_subtest_is_not_graded_by_its_parent(self):
        # A `Parent/sub` citation needs a result for that exact ID: the parent's result is not the
        # subtest's, and a reader that fell back to the parent graded a subtest that never ran.
        results, _ = self.read(self.write("go.jsonl", go_json(
            ("core/internal/session", "TestAttach"))).name)
        self.assertIsNone(results.outcome("U2", "core/internal/session/attach_test.go",
                                          "TestAttach/replay_then_live"))
        # Go replaces spaces in subtest names with underscores, so the citation's space matches it.
        spaced, _ = self.read(self.write("spaced.jsonl", go_json(
            ("core/internal/session", "TestAttach/replay_then_live"))).name)
        self.assertEqual(spaced.outcome("U2", "core/internal/session/attach_test.go",
                                        "TestAttach/replay then live"), "pass")

    # ---- pytest JUnit -------------------------------------------------------------------------

    def test_a_junit_case_is_matched_by_file_and_test_name(self):
        results, _ = self.read(self.write("py.xml", junit_xml(
            ("worker/tests/test_keepalive.py", "test_keepalive",
             "test_missing_is_reported_after_the_last_ack_and_before_the_close", "pass"))).name)
        self.assertEqual(results.outcome("U3", "worker/tests/test_keepalive.py",
                                         "test_missing_is_reported_after_the_last_ack_and_before_the_close"),
                         "pass")

    def test_a_parametrized_case_is_cited_by_its_prefix_and_every_case_must_pass(self):
        # The index cites `test_a_silent_peer...`, never its `[1]`/`[2]`/`[4]` suffixes: the
        # reader keeps the cases and grades on all of them, so one failing parameter fails.
        name = "test_a_silent_peer_goes_missing_then_times_out_at_exactly_interval_times_count_max"
        passing = junit_xml(*[( "worker/tests/test_keepalive.py", "test_keepalive", f"{name}[{n}]", "pass")
                              for n in (1, 2, 4)])
        path = self.write("py.xml", passing)
        self.assertEqual(results_for(path).outcome("U3", "worker/tests/test_keepalive.py", name), "pass")
        mixed = junit_xml(*[( "worker/tests/test_keepalive.py", "test_keepalive", f"{name}[{n}]", "pass")
                            for n in (1, 2)]
                         + [("worker/tests/test_keepalive.py", "test_keepalive", f"{name}[4]", "fail")])
        path.write_text(mixed, encoding="utf-8")
        self.assertEqual(results_for(path).outcome("U3", "worker/tests/test_keepalive.py", name), "fail")

    def test_a_junit_case_from_another_file_is_not_a_match(self):
        results, _ = self.read(self.write("py.xml", junit_xml(
            ("worker/tests/test_supervisor.py", "test_supervisor",
             "test_kill_worker_sigkills_and_reaps_answering_only_after_the_reap", "pass"))).name)
        self.assertIsNone(results.outcome("U3", "worker/tests/test_spool.py",
                                          "test_kill_worker_sigkills_and_reaps_answering_only_after_the_reap"))

    def test_a_parametrized_case_in_a_sibling_file_is_not_a_match(self):
        # `_same_file` treated any two files in one directory as the same, so a same-named
        # parametrized case in a sibling graded the citation from a file the run never touched.
        name = "test_a_silent_peer_goes_missing_then_times_out_at_exactly_interval_times_count_max"
        sibling = self.write("py.xml", junit_xml(
            ("worker/tests/test_supervisor.py", "test_supervisor", f"{name}[1]", "pass")))
        self.assertIsNone(results_for(sibling).outcome("U3", "worker/tests/test_keepalive.py", name))
        same = self.write("same.xml", junit_xml(
            ("worker/tests/test_keepalive.py", "test_keepalive", f"{name}[1]", "pass")))
        self.assertEqual(results_for(same).outcome("U3", "worker/tests/test_keepalive.py", name), "pass")

    def test_a_parametrized_class_case_is_matched_only_to_its_own_class(self):
        # A parametrized case was placed by its file alone, so a sibling class in the same file graded
        # the citation: a JUnit case names the method alone, so its class must be the cited one.
        def cases(*rows) -> str:  # rows: (class, param, outcome), the xunit1 form with a `file`
            return junit_xml(*[("pkg/tests/test_x.py", f"pkg.tests.test_x.{cls}", f"test_z[{p}]", o)
                               for cls, p, o in rows])
        only_b = self.write("only_b.xml", cases(("TestB", "a", "pass")))
        self.assertIsNone(results_for(only_b).outcome("U3", "pkg/tests/test_x.py", "TestA::test_z"))
        b_fails = self.write("b_fails.xml", cases(("TestA", "a", "pass"), ("TestB", "a", "fail")))
        self.assertEqual(results_for(b_fails).outcome("U3", "pkg/tests/test_x.py", "TestA::test_z"), "pass")
        a_fails = self.write("a_fails.xml", cases(("TestA", "a", "fail"), ("TestB", "a", "pass")))
        self.assertEqual(results_for(a_fails).outcome("U3", "pkg/tests/test_x.py", "TestA::test_z"), "fail")

    def test_an_xunit2_parametrized_class_case_is_matched_only_to_its_own_class(self):
        # The same two-class check in pytest's default output, where no `file` is written: the class
        # rides in the classname, so TestB's cases must never grade a citation of TestA.
        only_b = self.write("only_b.xml", junit_xunit2(("pkg.tests.test_x.TestB", "test_z[a]", "pass")))
        self.assertIsNone(results_for(only_b).outcome("U3", "pkg/tests/test_x.py", "TestA::test_z"))

    def test_a_results_list_record_keeps_the_whole_node_id_of_a_parametrized_case(self):
        # The harness writes a rig's full node id with its `[param]`, while the citation is the prefix:
        # matching on the method alone left a record that keeps `TestThing::test_z[a]` ungraded.
        path = self.write("rigs.json", json.dumps([{"unit": "U3", "file": "pkg/tests/test_x.py",
                                                    "test_id": "TestThing::test_z[a]", "outcome": "pass"}]))
        self.assertEqual(results_for(path).outcome("U3", "pkg/tests/test_x.py",
                                                   "TestThing::test_z"), "pass")

    def test_an_xunit2_parametrized_case_is_placed_by_its_classname(self):
        # A parametrized test in pytest's default output was left UNGRADED although it ran and passed:
        # xunit2 writes no `file`, so the prefix branch demanded a file the case never carries, and
        # only its dotted classname places it at the citation's file.
        results, problems = self.read(self.write("py.xml", junit_xunit2(
            ("pkg.tests.test_x", "test_y[1]", "pass"),
            ("pkg.tests.test_x", "test_y[2]", "pass"))))
        self.assertEqual(problems, [])
        self.assertEqual(results.outcome("U3", "pkg/tests/test_x.py", "test_y"), "pass")

    def test_an_xunit2_parametrized_case_that_failed_fails_the_citation(self):
        # A parametrized test in pytest's default output was left UNGRADED although it ran and passed:
        # once the cases are placed, one failing parameter must fail the citation like any other run.
        path = self.write("py.xml", junit_xunit2(
            ("pkg.tests.test_x", "test_y[1]", "pass"),
            ("pkg.tests.test_x", "test_y[2]", "fail")))
        self.assertEqual(results_for(path).outcome("U3", "pkg/tests/test_x.py", "test_y"), "fail")

    def test_an_xunit2_parametrized_case_in_another_module_is_not_a_match(self):
        # A parametrized test in pytest's default output was left UNGRADED although it ran and passed:
        # placing a case by classname must not let a same-named case in a sibling module stand in for
        # the cited file, which is what `_same_file` refuses for a case that does carry a file.
        other = self.write("py.xml", junit_xunit2(
            ("pkg.tests.test_other", "test_y[1]", "pass"),
            ("pkg.tests.test_other", "test_y[2]", "pass")))
        self.assertIsNone(results_for(other).outcome("U3", "pkg/tests/test_x.py", "test_y"))

    def test_an_xunit2_class_parametrized_case_is_placed_by_its_classname_and_class(self):
        # A parametrized test in pytest's default output was left UNGRADED although it ran and passed:
        # a `Class::method` citation names the class in its classname, and the case's own name is the
        # method with its `[param]`, never the whole citation.
        cls = self.write("py.xml", junit_xunit2(
            ("pkg.tests.test_x.TestThing", "test_z[a]", "pass"),
            ("pkg.tests.test_x.TestThing", "test_z[b]", "pass")))
        self.assertEqual(results_for(cls).outcome("U3", "pkg/tests/test_x.py",
                                                  "TestThing::test_z"), "pass")

    def test_a_junit_case_without_a_file_is_placed_by_its_classname(self):
        results, problems = self.read(
            self.write("py.xml", junit_xml_module_from_classname("test_q")).name)
        self.assertEqual(problems, [])
        self.assertEqual(results.outcome("U3", "test_sample.py", "test_q"), "pass")

    def test_a_junit_case_without_a_file_is_placed_by_its_dotted_classname(self):
        # pytest's default output (xunit2) writes no `file`: classname is the whole dotted module
        # path, or that path plus `.Class`. `_module` kept only the text before the first dot, so a
        # case collected from `worker.tests.test_keepalive` was placed as `worker` and never matched
        # the citation's file. Both a function case and a class case are read here.
        results, problems = self.read(self.write("py.xml", junit_xunit2(
            ("worker.tests.test_keepalive", "test_missing_is_reported_after_the_last_ack", "pass"),
            ("worker.tests.test_keepalive.TestKeepalive", "test_a_link_answers_again", "pass"))))
        self.assertEqual(problems, [])
        self.assertEqual(results.outcome("U3", "worker/tests/test_keepalive.py",
                                         "test_missing_is_reported_after_the_last_ack"), "pass")

    def test_a_cited_class_test_is_graded_in_both_junit_forms(self):
        # A `Class::test` citation: with no `file` (pytest's default) the classname must end with
        # `.Class`; with a `file` the classname's last segment is `Class`. Both must grade, and a
        # method is the case's `name`, never the whole citation.
        no_file = self.write("py.xml", junit_xunit2(
            ("worker.tests.test_keepalive.TestKeepalive", "test_a_link_answers_again", "pass")))
        self.assertEqual(results_for(no_file).outcome(
            "U3", "worker/tests/test_keepalive.py", "TestKeepalive::test_a_link_answers_again"), "pass")
        with_file = self.write("with_file.xml", junit_xml(
            ("worker/tests/test_keepalive.py", "test_keepalive.TestKeepalive",
             "test_a_link_answers_again", "pass")))
        self.assertEqual(results_for(with_file).outcome(
            "U3", "worker/tests/test_keepalive.py", "TestKeepalive::test_a_link_answers_again"), "pass")

    def test_a_junit_case_that_neither_names_a_file_nor_a_test_is_reported(self):
        _results, problems = self.read(self.write(
            "py.xml", '<?xml version="1.0"?><testsuites><testsuite><testcase/></testsuite></testsuites>').name)
        self.assertTrue(any("names neither a file nor a test" in p for p in problems), problems)

    # ---- the harness's own JSON list ---------------------------------------------------------

    def test_a_small_json_list_is_read_as_written(self):
        rows = [{"unit": "U11", "file": "harness/rigs/kill-core.sh", "test_id": "rig-kill-core",
                 "outcome": "pass"}]
        path = self.write("rigs.json", json.dumps(rows))
        self.assertEqual(results_for(path).outcome("U11", "harness/rigs/kill-core.sh",
                                                   "rig-kill-core"), "pass")

    def test_a_list_entry_with_an_unknown_outcome_is_reported(self):
        # The problem must come from this read: the module-level PROBLEMS can still hold entries
        # from an earlier test, so asserting on it alone would pass without the reader reporting
        # anything. The value a read returns carries its own findings for exactly this.
        path = self.write("rigs.json", json.dumps([{"unit": "U11", "file": "f", "test_id": "rig-x",
                                                    "outcome": "maybe"}]))
        results = results_for(path)
        self.assertTrue(any("no outcome of pass/fail/skip" in p for p in results.problems),
                        results.problems)

    # ---- refusals -----------------------------------------------------------------------------

    def test_an_unreadable_results_file_stops_the_run(self):
        # Read as "no tests ran", a truncated or a missing file would leave the citation it names
        # UNGRADED for the runner's own reason, or let a caller pass a file and count it.
        missing = self.dir / "nope.jsonl"
        results = results_for(missing)
        self.assertTrue(any("not a file" in p for p in _results.PROBLEMS), _results.PROBLEMS)
        self.assertIsNone(results.outcome("U2", "a/b_test.go", "TestX"))

    def test_malformed_json_stops_the_run(self):
        self.write("go.jsonl", "this is not json\n")
        results_for(self.dir / "go.jsonl")
        self.assertTrue(any("not valid JSON" in p for p in _results.PROBLEMS), _results.PROBLEMS)

    def test_a_stray_absent_result_never_hides_a_real_one(self):
        # Several results for one citation are one verdict, and a missing one (a result a reader
        # could not place) says nothing: with the pass last, a naive `all(...)` over the tuple
        # returns the leading None and loses the fixture; it must still read pass. A failure
        # among the known outcomes is the verdict, and no known outcome at all is "did not run".
        self.assertEqual(_results._verdict(None, "pass"), "pass")
        self.assertEqual(_results._verdict("pass", None), "pass")
        self.assertEqual(_results._verdict(None, "fail", "pass"), "fail")
        self.assertIsNone(_results._verdict(None, None))

    def test_a_partly_skipped_test_skips_whatever_the_order(self):
        # A pass needs every result that was written to have passed: a mix of a skip and a pass is
        # a skip, not the first outcome written, and the order the results came in changes nothing.
        self.assertEqual(_results._verdict("skip", "pass"), "skip")
        self.assertEqual(_results._verdict("pass", "skip"), "skip")

    def test_a_later_pass_never_hides_an_earlier_failure(self):
        # Every outcome for a key is kept, across records and across files: a retried test that
        # failed then passed is fail, not the last outcome written. Two files read together carry
        # both outcomes, and a single file retried carries them too.
        both = self.write("both.json", json.dumps([
            {"unit": "U11", "file": "harness/rigs/kill-core.sh", "test_id": "rig-kill-core",
             "outcome": "fail"},
            {"unit": "U11", "file": "harness/rigs/kill-core.sh", "test_id": "rig-kill-core",
             "outcome": "pass"}]))
        self.assertEqual(results_for(both).outcome("U11", "harness/rigs/kill-core.sh",
                                                   "rig-kill-core"), "fail")
        f1 = self.write("f1.json", json.dumps([
            {"unit": "U11", "file": "harness/rigs/kill-core.sh", "test_id": "rig-kill-core",
             "outcome": "fail"}]))
        f2 = self.write("f2.json", json.dumps([
            {"unit": "U11", "file": "harness/rigs/kill-core.sh", "test_id": "rig-kill-core",
             "outcome": "pass"}]))
        self.assertEqual(results_for(f1, f2).outcome("U11", "harness/rigs/kill-core.sh",
                                                     "rig-kill-core"), "fail")


class GradingRule(unittest.TestCase):
    """The four verdicts the phase asks for, each proved against the tree."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.fixtures, _ = load_fixtures(CONTRACT)
        self.by_name = {f.name: f for f in self.fixtures}

    def grade(self, name: str, results: _results.Results) -> verify.Result:
        run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e2", None, None, None, 1.0, {},
                  frozenset(), results)
        return verify._grade(self.by_name[name], run)

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_provider_on_a_passing_cited_test(self):
        # C1/alerts-stream is graded only by six U2 tests in one file plus a heartbeat one: all
        # six must have passed, and one is enough to fail it (proved below).
        results = results_for(self.write("go.jsonl", go_json(
            ("core/internal/alert", "TestAlertStreamSendsTheActiveSetThenEachRaiseAndClear"),
            ("core/internal/alert", "TestAlertStreamKeepaliveAfterHeartbeatIntervalOfSilence"),
            ("core/internal/alert", "TestAlertStreamEndsOnLogout"),
            ("core/internal/alert", "TestAlertStreamEndsWhenItsLoginExpires"),
            ("core/internal/alert", "TestAPolicyReloadEndsTheStreamsItRevokesAndNoOther"),
            ("core/internal/session", "TestAGoneSupervisorsAlertsClearAndNothingIsRaisedForIt"))))
        self.assertEqual(self.grade("alerts-stream", results).outcome, verify.PROVIDER,
                         results.outcome("U2", "core/internal/alert/stream_test.go",
                                         "TestAlertStreamEndsOnLogout"))

    def test_fail_on_a_cited_test_that_ran_and_failed(self):
        results = results_for(self.write("go.jsonl",
            go_json(("core/internal/alert", "TestAlertStreamSendsTheActiveSetThenEachRaiseAndClear"),
                    ("core/internal/alert", "TestAlertStreamKeepaliveAfterHeartbeatIntervalOfSilence"),
                    ("core/internal/alert", "TestAlertStreamEndsOnLogout"),
                    ("core/internal/alert", "TestAlertStreamEndsWhenItsLoginExpires"),
                    ("core/internal/alert", "TestAPolicyReloadEndsTheStreamsItRevokesAndNoOther"))
            + go_json_failed("core/internal/session",
                             "TestAGoneSupervisorsAlertsClearAndNothingIsRaisedForIt")))
        result = self.grade("alerts-stream", results)
        self.assertEqual(result.outcome, verify.FAIL, result.detail)
        self.assertIn("the provider test that grades it did not pass", result.detail)

    def test_ungraded_when_the_cited_test_absent(self):
        results = results_for(self.write("go.jsonl", go_json(("core/internal/alert",
                                                               "TestAlertStreamEndsOnLogout"))))
        result = self.grade("alerts-stream", results)
        self.assertEqual(result.outcome, verify.UNGRADED, result.detail)
        self.assertIn("the cited test did not run", result.detail)
        self.assertIn("give --provider-results", result.detail)

    def test_ungraded_when_only_a_same_named_test_in_another_file_passed(self):
        # The cited test's name is held by a test in another package: a reader keyed on the name
        # alone would grade this fixture on a run that never touched the file it cites.
        results = results_for(self.write("go.jsonl",
            go_json(("core/internal/alert", "TestASpooledEndOfAnUnlistedSessionIsRecordedAfterItsGap"))))
        result = self.grade("gap-before-end", results)
        self.assertEqual(result.outcome, verify.UNGRADED, result.detail)
        self.assertIn("the cited test did not run", result.detail)

    def test_ungraded_for_an_owed_entry(self):
        # C1/output-page-gap's only entry is owed by the E5 core plan: nothing runs it, and nothing
        # fails either, so the fixture is never green and never red. (It named C2/size-not-resizable
        # until the E3 graders were named.)
        result = self.grade("output-page-gap", _results.Results())
        self.assertEqual(result.outcome, verify.UNGRADED, result.detail)
        self.assertIn("owed", result.detail)

    def test_provider_tests_alone_grades_no_reference_fixture(self):
        # The flag is still accepted — no input valid before is refused — and it grades nothing:
        # without the results, every reference fixture of every grade is UNGRADED.
        run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e2", None, None, None, 1.0, {},
                  frozenset({"U2", "U3", "U5", "U11"}))
        reference = [f for f in self.fixtures if f.grade == "reference"
                     and profiles_status_required(f)]
        for fx in reference:
            with self.subTest(fx=fx.label):
                self.assertEqual(verify._grade(fx, run).outcome, verify.UNGRADED)


def profiles_status_required(fx) -> bool:
    from tsbc import profiles
    return profiles.status(fx.mark, "e2") == profiles.REQUIRED


class SelfCheckIndex(unittest.TestCase):
    """Self-check holds the index to the fixtures it speaks for: an entry that cannot be graded,
    or a fixture that must be but is not, fails the tree rather than a run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "contract"
        self.root.mkdir()
        for name in ("CONTRACT_VERSION", "schemas", "openapi", "fixtures"):
            src = CONTRACT / name
            (shutil.copytree if src.is_dir() else shutil.copy)(src, self.root / name)
        self.index = json.loads((CONTRACT / "fixtures" / "graded-by.json").read_text(encoding="utf-8"))

    def write(self, index) -> None:
        (self.root / "fixtures" / "graded-by.json").write_text(json.dumps(index), encoding="utf-8")

    def assertFailure(self, needle: str) -> None:
        from tsbc import selfcheck
        failures = selfcheck.run(self.root).failures
        self.assertTrue(any(needle in f for f in failures), f"{needle!r} not in:\n" + "\n".join(failures))

    def test_a_clean_index_passes(self):
        from tsbc import selfcheck
        self.write(self.index)
        self.assertTrue(selfcheck.run(self.root).ok,
                        "\n".join(selfcheck.run(self.root).failures))

    def test_an_entry_for_a_fixture_that_does_not_exist(self):
        self.index["C1/no-such-fixture"] = [{"unit": "U2", "file": "a/b_test.go", "test_id": "TestX"}]
        self.write(self.index)
        self.assertFailure("names no fixture")

    def test_an_entry_for_a_fixture_that_is_not_reference(self):
        self.index["C1/healthz"] = [{"unit": "U2", "file": "a/b_test.go", "test_id": "TestX"}]
        self.write(self.index)
        self.assertFailure("is not a reference fixture")

    def test_an_entry_naming_a_unit_the_fixture_does_not(self):
        self.index["C1/alerts-stream"] = [{"unit": "U3", "file": "a/b_test.go", "test_id": "TestX"}]
        self.write(self.index)
        self.assertFailure("which the fixture's own graded_by does not")

    def test_an_entry_with_both_owed_and_a_file(self):
        self.index["C1/alerts-stream"] = [{"unit": "U2", "file": "a/b_test.go", "test_id": "TestX",
                                           "owed": "the E2 plan"}]
        self.write(self.index)
        self.assertFailure("matches none of oneOf")

    def test_an_absolute_file_is_refused(self):
        self.index["C1/alerts-stream"] = [{"unit": "U2", "file": "/etc/passwd", "test_id": "TestX"}]
        self.write(self.index)
        self.assertFailure("does not match pattern")

    def test_a_file_with_a_dotdot_segment_is_refused(self):
        # The schema pattern allows a `.` in a name, so a `..` segment passes it; the self-check
        # rule is what refuses a path that could climb out of the provider's repo.
        self.index["C1/alerts-stream"] = [{"unit": "U2", "file": "core/../etc/passwd", "test_id": "TestX"}]
        self.write(self.index)
        self.assertFailure("may not begin with '/' or hold a '..' segment")

    def test_an_owed_entry_on_a_closed_epoch_is_refused(self):
        # C1/alerts-stream is required green at e1: its claim is already due, so it cannot be owed.
        self.index["C1/alerts-stream"] = [{"unit": "U2", "owed": "a later plan"}]
        self.write(self.index)
        self.assertFailure("is owed, but the fixture is required green at e1")

    def test_an_owed_entry_on_a_later_epoch_is_allowed(self):
        # C2/frame-versioning is required green at e2, which is not a closed epoch: owed is how the
        # index records that its test does not exist yet, and the tree stays valid.
        self.index["C2/frame-versioning"] = [{"unit": "U2", "owed": "the E2 core plan"}]
        self.write(self.index)
        from tsbc import selfcheck
        self.assertTrue(selfcheck.run(self.root).ok, "\n".join(selfcheck.run(self.root).failures))

    def test_a_required_reference_fixture_with_no_entry(self):
        del self.index["C1/alerts-stream"]
        self.write(self.index)
        self.assertFailure("a required-green reference fixture has no entry")

    def test_a_published_only_reference_fixture_may_have_no_entry(self):
        # C3/reattach-non-pty is published at e1, not required, and no v0.2 worker opens a non-PTY
        # session: requiring an entry would name a test that cannot exist.
        self.assertNotIn("C3/reattach-non-pty", self.index)
        self.write(self.index)
        from tsbc import selfcheck
        self.assertTrue(selfcheck.run(self.root).ok, "\n".join(selfcheck.run(self.root).failures))


if __name__ == "__main__":
    unittest.main()