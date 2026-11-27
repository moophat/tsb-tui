"""Self-check on synthetic trees: the empty tree, and the credential-hygiene scan.

The release tarball goes to strangers. The hygiene scan is the only thing standing between a
pasted real key in a fixture and publishing it, so it is proven to fail on planted material in
every place a secret could hide: a string, a JSON escape, a hex payload, a byte file.
"""

import base64
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tsbc import selfcheck, verify

from . import CONTRACT

# Assembled at run time so this file itself never holds a key-shaped header.
PEM = "-----BEGIN " + "OPENSSH PRIVATE KEY" + "-----"


def make_tree(tmp: Path) -> Path:
    root = tmp / "contract"
    (root / "schemas").mkdir(parents=True)
    (root / "fixtures" / "C6").mkdir(parents=True)
    (root / "CONTRACT_VERSION").write_text("contract-v0.1-dev\n")
    shutil.copy(CONTRACT / "schemas" / "fixture.schema.json", root / "schemas")
    return root


def plant(root: Path, name: str, value) -> None:
    (root / "fixtures" / "C6" / name).write_text(json.dumps({"note": value}))


class EmptyTree(unittest.TestCase):
    def test_empty_tree_passes_with_zero_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(Path(tmp))
            out = io.StringIO()
            status = verify.main(["verify", "--self-check", "--contract-dir", str(root)], out)
            self.assertEqual(status, 0, out.getvalue())
            self.assertIn("fixtures: 0 fixtures", out.getvalue())

    def test_a_patch_version_is_a_tag(self):
        # A grader-only patch (contract-vX.Y.Z) is a release the self-check accepts, alongside the
        # minor and their -dev forms; anything else is refused.
        for version in ("contract-v0.2", "contract-v0.2.1", "contract-v0.1-dev", "contract-v0.2.1-dev"):
            with self.subTest(version=version):
                self.assertIsNotNone(selfcheck.VERSION_RE.match(version))
        for version in ("contract-v0.2.1.4", "contract-v0.2."):
            with self.subTest(version=version):
                self.assertIsNone(selfcheck.VERSION_RE.match(version))

    def test_a_plain_release_tag_is_a_tag(self):
        # tsb-program cuts the contract's releases under their own version (vX.Y.Z), so the
        # self-check accepts a plain release tag alongside the contract-vX.Y forms; a two-part or
        # bare-numeric version is still refused.
        self.assertIsNotNone(selfcheck.VERSION_RE.match("v0.3.1"))
        for version in ("v0.3", "0.3.1"):
            with self.subTest(version=version):
                self.assertIsNone(selfcheck.VERSION_RE.match(version))

    def test_missing_tree_is_a_usage_error_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()) as err:
            status = verify.main(["verify", "--self-check", "--contract-dir", tmp], io.StringIO())
        self.assertEqual(status, 2)
        self.assertIn("not a contract tree", err.getvalue())


class OctetStreamRegistration(unittest.TestCase):
    def test_repeated_runs_register_the_policy_once(self):
        # run() is called by every self-check in a process (the suite, an embedding caller): a
        # second registration would report each octet-stream violation once per earlier run.
        from tsbc import _exchange
        selfcheck.admit_octet_stream()
        selfcheck.admit_octet_stream()
        registered = _exchange.selfcheck.OPENAPI_CHECKS
        self.assertEqual(registered.count(selfcheck._octet_stream_policy), 1)


class PatternSyntax(unittest.TestCase):
    """A schema pattern stays inside the syntax Python re, ECMA-262 and Go RE2 share."""

    def problems(self, pattern: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(Path(tmp))
            (root / "schemas" / "probe.schema.json").write_text(json.dumps(
                {"type": "object", "properties": {"p": {"type": "string", "pattern": pattern}}}))
            registry = selfcheck.Registry.from_tree(root)
            return selfcheck.schema_problems(registry)[0]

    def test_a_negative_lookahead_fails_the_self_check(self):
        problems = self.problems("^(?!x$)")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("schemas/probe.schema.json#/properties/p/pattern", problems[0])
        self.assertIn("(?!", problems[0])

    def test_each_unshared_construct_fails(self):
        for pattern in ("^(?=x)", "(?<=x)y", "(?<!x)y", r"(a)\1", r"(?<n>a)\k<n>"):
            with self.subTest(pattern=pattern):
                self.assertTrue(any("outside the syntax" in p for p in self.problems(pattern)))

    def test_a_python_named_group_and_backreference_fail(self):
        for pattern, construct in (("(?P<a>x)(?P=a)", "(?P<"), ("(?P<a>x)", "(?P<"), ("(?P=a)", "(?P=")):
            with self.subTest(pattern=pattern):
                problems = self.problems(pattern)
                self.assertTrue(any("outside the syntax" in p and construct in p for p in problems), problems)

    def test_an_escaped_or_in_class_python_named_construct_does_not_fail(self):
        for pattern in (r"^\(?P=a", r"^\(?P<a", "^[(?P=]+$", "^[(?P<]+$"):
            with self.subTest(pattern=pattern):
                self.assertEqual(self.problems(pattern), [])

    def test_escaped_and_in_class_tokens_do_not_fail(self):
        for pattern in (r"^\(?!x", "^[(?!]+$", r"^[\1]$", r"\\1"):
            with self.subTest(pattern=pattern):
                self.assertEqual(self.problems(pattern), [])

    def test_a_published_annotation_pattern_with_lookahead_is_refused(self):
        with self.assertRaises(ValueError):
            selfcheck.compile_published("^(?!x$)")

    def test_the_stored_login_credential_ref_pattern_means_anything_but_supplied(self):
        from tsbc.schema import compile_pattern
        doc = json.loads((CONTRACT / "schemas" / "continuity-state.schema.json").read_text())
        pattern = doc["$defs"]["connection_record"]["oneOf"][2]["properties"]["credential_ref"]["pattern"]
        compiled = compile_pattern(pattern)
        self.assertIsNone(compiled.search("supplied"))
        for ok in ("stored-ref", "s", "sup", "suppliedx", "x"):
            self.assertIsNotNone(compiled.search(ok), ok)


class Hygiene(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = make_tree(Path(self.tmp.name))

    def findings(self):
        return selfcheck.scan_tree(self.root)[1]

    def test_planted_pem_header_fails_self_check(self):
        plant(self.root, "leak.json", PEM)
        self.assertTrue(any("PEM header" in f for f in self.findings()))
        report = selfcheck.run(self.root)
        self.assertFalse(report.ok)
        self.assertTrue(any(f.startswith("hygiene: fixtures/C6/leak.json") for f in report.failures))

    def test_pem_header_hidden_behind_json_escapes(self):
        escaped = "".join(f"\\u{ord(c):04x}" for c in PEM)
        (self.root / "fixtures" / "C6" / "leak.json").write_text('{"note": "' + escaped + '"}')
        self.assertTrue(any("PEM header" in f for f in self.findings()))

    def test_pem_header_hidden_in_a_hex_payload(self):
        plant(self.root, "leak.json", PEM.encode().hex())
        self.assertTrue(any("PEM header" in f for f in self.findings()))

    def test_pem_header_inside_a_byte_file(self):
        (self.root / "fixtures" / "C6" / "leak.bin").write_bytes(b"\x00\x01" + PEM.encode() + b"\xff")
        self.assertTrue(any("leak.bin: PEM header" in f for f in self.findings()))

    def test_openssh_key_marker_raw_and_base64(self):
        body = base64.b64encode(b"openssh-key-v1\x00\x00\x00\x00\x04none").decode()
        for value in ("prefix openssh-key-v1 suffix", body):
            with self.subTest(value=value):
                plant(self.root, "leak.json", value)
                self.assertTrue(any("OpenSSH" in f for f in self.findings()))

    def test_high_entropy_base64_run(self):
        plant(self.root, "leak.json", base64.b64encode(os.urandom(48)).decode())
        self.assertTrue(any("bits/char" in f for f in self.findings()))

    def graded_by(self, *test_ids: str) -> None:
        """Write the graded-by index the scan reads, citing one test id per entry."""
        (self.root / "fixtures" / selfcheck.GRADED_BY_INDEX).write_text(json.dumps(
            {f"C2/entry{i}": [{"unit": "U2", "file": "a/b_test.go", "test_id": t}]
             for i, t in enumerate(test_ids)}), encoding="utf-8")

    def test_a_whole_test_name_cited_in_the_graded_by_index_is_not_a_finding(self):
        # A parametrized Go test name is a citation the index must hold verbatim: the hygiene scan
        # read long mixed-case test names as base64-like runs, so a clean tree failed self-check.
        name = "TestTheCommitPointMovesOnlyWithASnapshotOrRecordOnlyAndNeverBackwards"
        self.assertTrue(selfcheck.shannon_entropy(name) > selfcheck.ENTROPY_LIMIT)
        self.graded_by(name)
        self.assertEqual([f for f in self.findings() if "bits/char" in f], [])

    def test_a_cited_test_name_in_another_file_is_still_flagged(self):
        # The allowance is the index's own citation, not a whitelist of the string: the same
        # high-entropy name pasted into any other file is still credential-shaped and must be reported.
        name = "TestTheCommitPointMovesOnlyWithASnapshotOrRecordOnlyAndNeverBackwards"
        self.graded_by(name)
        plant(self.root, "leak.json", name)
        self.assertTrue(any("bits/char" in f for f in self.findings()), self.findings())

    def test_a_high_entropy_run_in_the_index_that_is_not_test_shaped_is_still_flagged(self):
        # Only whole test names are allowed: a base64 run sitting in a test_id is a credential and
        # must be reported, so the allowance cannot be "anything the index cites".
        secret = "Zq" + base64.b64encode(bytes(range(46))).decode()
        self.assertTrue(selfcheck.shannon_entropy(secret) > selfcheck.ENTROPY_LIMIT)
        self.assertIsNone(selfcheck.TEST_NAME.fullmatch(secret))
        self.graded_by(secret)
        self.assertTrue(any("fixtures/graded-by.json" in f and "bits/char" in f
                            for f in self.findings()), self.findings())

    def test_a_high_entropy_subtest_name_cited_in_the_index_is_allowed(self):
        # A Go citation includes its subtest path, so the high-entropy part is the tail, not the
        # head: allowing only `TestFoo` would leave the cited subtest's name flagged.
        name = "TestFoo/ZkQxVbNmJhGfDsAeIrTuLpCyWqXsBvNmKtRjH"
        self.assertTrue(len(name) >= 40 and
                        selfcheck.shannon_entropy(name) > selfcheck.ENTROPY_LIMIT)
        self.graded_by(name)
        self.assertEqual([f for f in self.findings() if "bits/char" in f], [])

    def test_a_test_name_in_the_index_outside_a_test_id_is_still_flagged(self):
        # The exemption is a citation's own `test_id`, not the string anywhere in the file: the same
        # high-entropy name as an entry's `file` value is credential-shaped and must be reported.
        name = "TestTheCommitPointMovesOnlyWithASnapshotOrRecordOnlyAndNeverBackwards"
        (self.root / "fixtures" / selfcheck.GRADED_BY_INDEX).write_text(json.dumps(
            {"C2/entry0": [{"unit": "U2", "file": name, "test_id": name}]}), encoding="utf-8")
        self.assertTrue(any("fixtures/graded-by.json" in f and "bits/char" in f
                            for f in self.findings()), self.findings())

    def test_a_high_entropy_run_in_a_test_id_that_is_not_the_test_name_is_still_flagged(self):
        # Only the test-name part of a citation is exempt: a pytest citation carries its module path,
        # and a base64 run sitting in that part of a `test_id` must still be reported.
        secret = "Zq" + base64.b64encode(bytes(range(46))).decode()
        self.assertTrue(selfcheck.shannon_entropy(secret) > selfcheck.ENTROPY_LIMIT)
        self.graded_by(f"{secret}::test_x")
        self.assertTrue(any("fixtures/graded-by.json" in f and "bits/char" in f
                            for f in self.findings()), self.findings())

    def test_credential_named_values_must_be_synthetic(self):
        # A low-entropy password slips past the text scan; the structural check catches it.
        for value in ("hunter2", "a1" * 32):
            with self.subTest(value=value):
                problems = selfcheck.credential_problems(
                    {"auth": {"kind": "key", "key": value}, "password": value}, placeholders=True)
                self.assertEqual(len(problems), 2)
                self.assertTrue(all(value not in p for p in problems), "a finding must not quote the value")
        doc = {"password": "${password}", "ticket": "FIXTURE-TICKET-000000000000000000",
               "auth": {"kind": "key", "key": "FIXTURE-NOT-A-KEY"}}
        self.assertEqual(selfcheck.credential_problems(doc, placeholders=True), [])
        self.assertTrue(selfcheck.credential_problems({"password": "${password}"}, placeholders=False))

    def test_a_credential_cannot_hide_in_a_list_or_an_object(self):
        for doc in ({"password": ["hunter2"]}, {"secret": {"value": "hunter2"}}, {"key": [["a1" * 32]]}):
            with self.subTest(doc=list(doc)):
                problems = selfcheck.credential_problems(doc, placeholders=True)
                self.assertEqual(len(problems), 1, problems)
                self.assertNotIn("hunter2", problems[0])
        self.assertEqual(selfcheck.credential_problems({"password": ["${password}", "FIXTURE-NOT-A-PASSWORD"]},
                                                       placeholders=True), [])

    def test_plain_password_in_a_fixture_fails_self_check(self):
        (self.root / "fixtures" / "C6" / "leak.json").write_text(json.dumps(
            {"case": {"doc": {"password": "hunter2"}}}))
        report = selfcheck.run(self.root)
        self.assertIn("fixtures/C6/leak.json: $.case.doc.password: a credential-named value must be "
                      "a documented synthetic constant or a ${placeholder}", report.failures)
        self.assertEqual(selfcheck.scan_tree(self.root)[1], [])  # the text scan alone misses it

    def test_synthetic_constants_and_ordinary_text_pass(self):
        plant(self.root, "ok.json", sorted(selfcheck.SYNTHETIC_CONSTANTS)
              + ["attach-ticket-wrong-session-close-4003-and-some-more-words",
                 "68656c6c6f" * 20, "0" * 64])
        self.assertEqual(self.findings(), [])


if __name__ == "__main__":
    unittest.main()
