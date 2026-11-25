"""C1/C2 fixtures: the published tree is coherent, self-check catches drift from the OpenAPI
document, and the live runner fails a provider that breaks the clause a fixture encodes.

The live half runs against tests/stub_core.py. A provider that behaves as the contract says must
pass every live fixture — proof the fixtures are satisfiable and do not contradict each other —
and each planted provider bug must turn its fixture red, or the fixture tests nothing.
"""

import io
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tsbc import _exchange, selfcheck, verify
from tsbc import _wsframe as wf
from tsbc._run import Run
from tsbc.fixtures import load_fixtures
from tsbc.schema import Registry, SchemaError, check_schema

from . import CONTRACT
from .stub_core import (
    ALERTS,
    ENDED,
    NOCRED,
    PASSWORD,
    PEER,
    TARGET,
    USER,
    VIEWER,
    StubCore,
)
from .stub_server import StubServer

INPUTS = {"user": USER, "password": PASSWORD, "target_id": TARGET, "target_no_cred_id": NOCRED,
          "peer_user": PEER, "peer_password": PASSWORD, "viewer_user": VIEWER, "viewer_password": PASSWORD}


def copy_tree(dest: Path) -> Path:
    root = dest / "contract"
    root.mkdir()
    for name in ("CONTRACT_VERSION", "schemas", "openapi", "fixtures"):
        src = CONTRACT / name
        (shutil.copytree if src.is_dir() else shutil.copy)(src, root / name)
    return root


class PublishedTree(unittest.TestCase):
    def test_published_tree_passes_self_check(self):
        report = selfcheck.run(CONTRACT, "e0")
        self.assertTrue(report.ok, "\n".join(report.failures))

    def test_no_client_side_ack_or_resume_frame(self):
        # Decision 5.15 defers the SSP whole, and an Ack frame is one of its named components: a
        # C2 frame that carried a delivered position would ship a slice of it under another name.
        # What a reattaching client is owed is contract-v0.3's attach prefix.
        reg = Registry.from_tree(CONTRACT)
        types = _exchange.frame_types(reg, "client") | _exchange.frame_types(reg, "server")
        self.assertEqual({t for t in types if "ack" in t or "resume" in t}, set())
        positions = {"seq", "from_seq", "offset", "position", "delivered", "last_seq", "through_seq"}
        frames = reg.docs[_exchange.WS_FRAMES]["$defs"]
        for side in ("client", "server"):
            for branch in frames[side]["oneOf"]:
                target, _ = reg.resolve(branch["$ref"], _exchange.WS_FRAMES)
                # contract-v0.3's attach prefix is what a reattaching client is owed (a server-side
                # snapshot point), so its through_seq is the one position the contract carries; no
                # client frame and no other server frame may carry one (decision 5.15).
                if target["properties"]["t"]["const"] == "prefix":
                    self.assertEqual(set(target["properties"]) & positions, {"through_seq"})
                else:
                    self.assertEqual(set(target["properties"]) & positions, set(), target["properties"]["t"])

    def test_no_schema_holds_a_default(self):
        # Contract is not config (decision 5.36): a schema carries a field and its range, and the
        # value lives in config.yaml. The validator subset refuses the keyword outright, so no
        # schema can slip one in; and none sits in an annotation either.
        with self.assertRaises(SchemaError):
            check_schema({"type": "integer", "default": 10000})
        for path in sorted((CONTRACT / "schemas").glob("*.json")) + [CONTRACT / "openapi" / "tsb.openapi.json"]:
            self.assertNotIn('"default"', path.read_text(encoding="utf-8"), path.name)

    def test_every_live_fixture_cites_the_corpus_and_says_why(self):
        fixtures, _ = load_fixtures(CONTRACT)
        for fx in fixtures:
            self.assertGreater(len(fx.doc["description"]), 60, fx.label)
            self.assertTrue(fx.doc["corpus"], fx.label)


class OfflineChecks(unittest.TestCase):
    """Mutate a copy of the tree; self-check must name the drift."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = copy_tree(Path(self.tmp.name))

    def mutate(self, rel: str, change) -> None:
        path = self.root / rel
        doc = json.loads(path.read_text())
        change(doc)
        path.write_text(json.dumps(doc))

    def assertFailure(self, needle: str) -> None:
        failures = selfcheck.run(self.root).failures
        self.assertTrue(any(needle in f for f in failures), f"{needle!r} not in:\n" + "\n".join(failures))

    def test_path_the_openapi_document_lacks(self):
        self.mutate("fixtures/C1/healthz.json", lambda d: d["case"]["steps"][0]["http"].update(path="/health"))
        self.assertFailure("is not an operation")

    def test_status_the_operation_does_not_declare(self):
        self.mutate("fixtures/C1/healthz.json", lambda d: d["case"]["steps"][0]["expect"].update(status=204))
        self.assertFailure("declares no 204 response")

    def test_invalid_body_that_is_actually_valid(self):
        def change(d):
            d["case"]["steps"][0]["http"]["body"]["connection"] = "new"
        self.mutate("fixtures/C1/session-create-missing-connection-422.json", change)
        self.assertFailure("marked invalid_body, but the body is valid")

    def test_example_that_breaks_the_response_schema(self):
        def change(d):
            d["case"]["steps"][0]["expect"]["example"]["session"]["recording_path"] = "x"
        self.mutate("fixtures/C1/session-create-new.json", change)
        self.assertFailure("example:")

    def test_placeholder_nobody_defines(self):
        self.mutate("fixtures/C1/session-list-get.json",
                    lambda d: d["case"]["steps"][2]["http"].update(path="/sessions/${nobody}"))
        self.assertFailure("placeholder ${nobody}")

    def test_example_error_message_naming_a_host_path(self):
        def change(d):
            d["case"]["steps"][0]["expect"]["example"]["error"]["message"] = "no file /var/lib/tsb/inventory/x.yaml"
        self.mutate("fixtures/C1/session-create-unknown-target-404.json", change)
        self.assertFailure("names a host path")

    def test_an_event_stream_is_admitted_only_for_the_alert_stream(self):
        # Every body the runner grades live is JSON it validates; only GET /alerts' 200 is not, and a
        # second non-JSON response would be one no run can hold to its schema.
        def change(d):
            content = d["paths"]["/sessions"]["get"]["responses"]["200"]["content"]
            media = content.pop("application/json")
            content["text/event-stream"] = {**media, "x-itemSchema": media["schema"]}  # well formed, wrongly placed
        self.mutate("openapi/tsb.openapi.json", change)
        self.assertFailure("text/event-stream is admitted only for GET /alerts' 200 response")

    def test_the_event_stream_names_its_per_event_schema(self):
        # Under OpenAPI 3.1 a media type's schema reads as the whole body; x-itemSchema says the
        # schema is one event's data, so a code generator never types the stream as one object.
        def change(d):
            del d["paths"]["/alerts"]["get"]["responses"]["200"]["content"]["text/event-stream"]["x-itemSchema"]
        self.mutate("openapi/tsb.openapi.json", change)
        self.assertFailure("missing required property 'x-itemSchema'")

    def test_the_per_event_schema_is_held_to_the_validator_subset(self):
        # x-itemSchema is what a code generator reads for each event, so it is a schema like any
        # other: a keyword the runner cannot evaluate is refused there too.
        def change(d):
            d["paths"]["/alerts"]["get"]["responses"]["200"]["content"]["text/event-stream"]["x-itemSchema"] = {"not": {}}
        self.mutate("openapi/tsb.openapi.json", change)
        self.assertFailure("200/x-itemSchema: keyword 'not' is outside the tsbc validator subset")

    def test_no_other_media_type(self):
        def change(d):
            d["paths"]["/alerts"]["get"]["responses"]["200"]["content"]["text/plain"] = {"schema": {"type": "string"}}
        self.mutate("openapi/tsb.openapi.json", change)
        self.assertFailure("property 'text/plain' is not allowed")

    def test_a_frame_a_live_run_can_produce_needs_a_live_fixture(self):
        # Only the v0.2 frames no live run can produce (a timer value, a v0.1 worker or a mocked
        # fault) may be covered by an instance a provider's test grades. resize is live-producible:
        # its one live fixture gone, coverage fails as it did at v0.1, even with a provider-graded
        # instance showing it.
        (self.root / "fixtures/C2/resize-control.json").unlink()
        for name in ("size-request-applied", "size-request-clamped", "size-request-not-typist"):
            (self.root / f"fixtures/C2/{name}.json").unlink()
        self.mutate("fixtures/C2/notice-changes.json", lambda d: d["case"]["instances"].append(
            {"valid": True, "doc": {"t": "resize", "v": 1, "cols": 80, "rows": 24}}))
        self.assertFailure("client frame 'resize' is sent by no fixture")

    def test_a_reference_fixture_covers_only_the_frames_no_live_run_can_produce(self):
        # token-escrow-after-restart is graded by provider tests and shows `token`; a live run can
        # produce `token`, so with every live fixture that expects it gone, coverage fails whatever
        # that reference shows. v0.3 added several live token producers (hand-off, take-over,
        # revoke/reassign), so dropping the v0.2 one alone no longer removes all coverage: drop them
        # all, and token must fail coverage while the reference still shows it.
        for name in ("token-event-on-detach", "token-handoff", "token-owner-revoke-reassign",
                     "token-move-no-resize", "owner-takeover-by-typing", "owner-takeover-then-ctrl-c"):
            (self.root / f"fixtures/C2/{name}.json").unlink()
        self.assertFailure("server frame 'token' is expected by no fixture")

    def test_a_reference_frame_needs_a_reference_graded_instance(self):
        self.mutate("fixtures/C2/notice-changes.json", lambda d: (d.update(grade="with:session-countdown"),
                                                                  d.pop("graded_by")))
        self.assertFailure("server frame 'notice' is expected by no fixture")

    def test_operation_without_a_fixture(self):
        (self.root / "fixtures/C1/healthz.json").unlink()
        self.assertFailure("GET /healthz has no fixture")

    def test_frame_that_breaks_the_frame_schema(self):
        def change(d):
            d["case"]["steps"][5]["frame"]["cols"] = 0
        self.mutate("fixtures/C2/resize-control.json", change)
        self.assertFailure("frame invalid")

    def test_close_code_the_contract_does_not_define(self):
        self.mutate("fixtures/C2/attach-bad-ticket-close-4001.json",
                    lambda d: d["case"]["steps"][3].update(close=4999))
        self.assertFailure("close code 4999")

    def test_invalid_instance_that_validates(self):
        def change(d):
            [inst] = [i for i in d["case"]["instances"] if i.get("note") == "not a state name in this tag"]
            inst["doc"]["state"] = "live"
        self.mutate("fixtures/C1/session-record-shape.json", change)
        self.assertFailure("tests nothing")

    def test_a_non_finite_number_is_not_json(self):
        # Python's json reads NaN; JSON has none. Here it would pass as a correctly refused instance.
        path = self.root / "fixtures/C1/session-record-shape.json"
        text = path.read_text().replace('"instances": [', '"instances": [{"valid": false, "doc": NaN}, ', 1)
        path.write_text(text)
        self.assertFailure("NaN is not a JSON number")

    def run_against_tree(self) -> tuple[int, str]:
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as empty, mock.patch("sys.stderr", err):
            status = verify.main(["verify", "--artifacts", empty, "--contract-dir", str(self.root)], io.StringIO())
        return status, err.getvalue()

    def test_a_graded_run_refuses_a_tree_with_two_fixtures_of_one_name(self):
        # Keyed by name, a later fixture's result would replace an earlier one: a FAIL could vanish.
        doc = json.loads((self.root / "fixtures/C1/healthz.json").read_text())
        (self.root / "fixtures/C1/healthz-again.json").write_text(json.dumps(doc))
        status, err = self.run_against_tree()
        self.assertEqual(status, verify.EXIT_USAGE)
        self.assertIn("fixture name 'healthz' is used more than once", err)
        self.assertIn("nothing is graded against it", err)

    def test_a_graded_run_refuses_a_malformed_mark_instead_of_crashing(self):
        self.mutate("fixtures/C1/healthz.json", lambda d: d.update(mark={}))
        status, err = self.run_against_tree()
        self.assertEqual(status, verify.EXIT_USAGE)
        self.assertIn("fixtures/C1/healthz.json", err)

    def test_results_are_kept_per_fixture_file_not_per_name(self):
        fixtures, _ = load_fixtures(CONTRACT)
        [healthz] = [f for f in fixtures if f.name == "healthz"]
        twin = type(healthz)(healthz.path, "fixtures/C1/healthz-again.json", healthz.doc)
        run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e0", None, None, None, 1.0)
        results, _ = verify._grade_all([healthz, twin], run, {"C1"})
        self.assertEqual([r.fixture.rel for r in results], [healthz.rel, twin.rel])

    # ---- how each fixture is graded is itself checked -----------------------------------------

    def test_required_reference_fixture_must_name_its_provider_test(self):
        self.mutate("fixtures/C1/policy-storage-example.json", lambda d: d.pop("graded_by"))
        self.assertFailure("must name the provider test")

    def test_with_grade_must_lean_on_a_fixture_a_run_grades(self):
        self.mutate("fixtures/C1/session-record-shape.json", lambda d: d.update(grade="with:no-such-fixture"))
        self.assertFailure("with:no-such-fixture names no fixture")
        self.mutate("fixtures/C1/session-record-shape.json", lambda d: d.update(grade="with:policy-storage-example"))
        self.assertFailure("that fixture is not graded live or from artefacts")

    # ---- C8: the recording and its end marker ------------------------------------------------

    def test_invalid_recording_that_is_valid(self):
        def change(d):
            valid = d["case"]["invalid"][0]
            valid["ttyrec_hex"] = (self.root / "fixtures/C8/end-marker-killed.ttyrec.bin").read_bytes().hex()
            valid["sidecar_hex"] = (self.root / "fixtures/C8/end-marker-killed.dir.bin").read_bytes().hex()
        self.mutate("fixtures/C8/end-marker-killed.json", change)
        self.assertFailure("is a valid recording: it tests nothing")

    def test_invalid_recording_refused_for_another_reason(self):
        self.mutate("fixtures/C8/end-marker-killed.json",
                    lambda d: d["case"]["invalid"][1].update(problem="must be the last frame"))
        self.assertFailure("is refused, but not for 'must be the last frame'")

    def test_recording_bytes_that_drop_the_end_marker(self):
        sidecar = self.root / "fixtures/C8/end-marker-killed.dir.bin"
        sidecar.write_bytes(sidecar.read_bytes()[:-1] + bytes([0]))  # the marker relabelled as output
        self.assertFailure("does not decode to the fixture's frames")

    def test_tap_derivation_without_the_end_marker(self):
        self.mutate("fixtures/C8/worker-death-recorded.json", lambda d: d["case"]["output"]["frames"].pop())
        self.assertFailure("4 frames derived, the fixture lists 3")

    def test_rediscovery_that_dials_a_dead_session(self):
        def change(d):
            d["case"]["output"]["dead_sessions"].remove("fixture-session-b")
        self.mutate("fixtures/C5/manifest-rediscovery-case.json", change)
        self.assertFailure("the rediscovery rule gives")

    # ---- contract-v0.2: each fixture form is held to what it claims ---------------------------

    def test_a_request_body_is_never_an_event_stream(self):
        # The runner sends every request body as JSON; an operation declaring another would be one
        # no run can exercise.
        def change(d):
            content = d["paths"]["/sessions"]["post"]["requestBody"]["content"]
            media = content.pop("application/json")
            content["text/event-stream"] = {**media, "x-itemSchema": media["schema"]}
        self.mutate("openapi/tsb.openapi.json", change)
        self.assertFailure("POST /sessions: a request body is application/json")

    def test_a_message_marked_invalid_that_validates(self):
        # The marked-invalid supervisor line is the one a bad_request answers; were it valid, the
        # fixture would show a supervisor refusing a well-formed request.
        def change(d):
            d["case"]["messages"][4]["msg"].update(conn_id="fixture-conn-2", n_max=4)
        self.mutate("fixtures/C3/supervisor-bad-request.json", change)
        self.assertFailure("message 5 is marked invalid, but it validates: the case tests nothing")

    RUNS = "fixtures/C3/seq-continuity-across-restart.json"

    def test_a_run_tap_that_is_not_its_frames(self):
        # Each run's .bin is the harness's ground truth for that run: it must be the listed frames.
        path = self.root / "fixtures/C3/seq-continuity-across-restart.run1.bin"
        data = path.read_bytes()
        path.write_bytes(data[:-1] + bytes([data[-1] ^ 0x01]))
        self.assertFailure("seq-continuity-across-restart.run1.bin is not the fixture's run 01789000000000000000 "
                           "frames byte for byte")

    def test_run_taps_are_held_to_the_seq_rules(self):
        # A repeat inside one run is a repeat, whatever a later run replays; runs order by the core's
        # start; and a replayed frame repeats what the earlier run tapped, byte for byte.
        cases = {
            "run 01789000000000000000: frame 2: out seq 1 after 1": lambda d: d["case"]["runs"][0]["frames"][2].update(seq=1),
            "runs are listed in run order": lambda d: [r.update(run=run) for r, run in
                                                       zip(d["case"]["runs"], reversed([r["run"] for r in d["case"]["runs"]]))],
            "merged: run 01789000100000000000: output seq 4 replayed with other bytes":
                lambda d: d["case"]["runs"][1]["frames"][0].update(payload_hex="78" * d["case"]["runs"][1]["frames"][0]["length"]),
        }
        original = (self.root / self.RUNS).read_text()
        for needle, change in cases.items():
            with self.subTest(needle):
                (self.root / self.RUNS).write_text(original)
                self.mutate(self.RUNS, change)
                self.assertFailure(needle)

    def test_a_comparison_whose_verdict_or_inputs_are_wrong(self):
        compare = "fixtures/C8/payload-identical-across-restart.json"
        self.mutate(compare, lambda d: d["case"]["output"]["payload_identical"].update(out=False))
        self.assertFailure("the comparator gives")
        # An input that is not a recording of this tag proves nothing about the comparator.
        self.mutate(compare, lambda d: d["case"]["input"]["b"]["frames"].append(
            {"sec": 1789000012, "usec": 0, "length": 1, "direction": "out", "data_hex": "78"}))
        self.assertFailure("input b the end marker is frame 4 of 6: it must be the last frame")

    def test_a_derivation_whose_input_is_malformed(self):
        spooled = "fixtures/C8/gap-before-end.json"
        original = (self.root / spooled).read_text()
        self.mutate(spooled, lambda d: d["case"]["input"]["end"].update(end_state="orphan"))
        self.assertFailure("input end is {end_state: killed|terminated, timestamp: ns, spool?}")
        (self.root / spooled).write_text(original)
        self.mutate(spooled, lambda d: d["case"]["input"]["end"]["spool"].update(out_bytes=0))
        self.assertFailure("input end spool:")
        # A gap frame stamped other than its handshake's last_dropped_ts has no marker to derive.
        self.mutate("fixtures/C8/gap-after-overflow.json",
                    lambda d: d["case"]["input"]["handshakes"][0].update(last_dropped_ts=1789000009123456790))
        self.assertFailure("input: gap frame 6 is stamped")


class HostPathHeuristic(unittest.TestCase):
    """An error message must not name a path on the broker host (Contract §4); an API path is fine."""

    def test_host_paths_are_caught(self):
        for message in ("no file /var/lib/tsb/inventory/x.yaml", "cannot open /tmp/tsb/conn-1/control.sock",
                        "see ~/.tsb/policy.yaml", "at /home/tsb/.tsb", "open(/etc/tsb/config.yaml)"):
            with self.subTest(message=message):
                self.assertTrue(_exchange.host_detail(message))

    def test_ordinary_messages_and_api_paths_pass(self):
        for message in ("log in first", "see GET /sessions/fixture-session-a", "POST /auth/ws-ticket needs session_id",
                        "unknown user or wrong password"):
            with self.subTest(message=message):
                self.assertFalse(_exchange.host_detail(message))


class ProviderTests(unittest.TestCase):
    def test_a_reference_fixture_needs_every_cited_test_to_have_run(self):
        # The unit is no longer what grades a reference fixture: the run's own results are. A
        # fixture two units grade is graded only when both units' cited tests passed in them.
        from .test_provider_results import results_for
        fixtures, _ = load_fixtures(CONTRACT)
        by_name = {f.name: f for f in fixtures}
        reg = Registry.from_tree(CONTRACT)

        def grade(name, paths):
            run = Run(CONTRACT, reg, "e0", None, None, None, 1.0, {}, frozenset(),
                      results_for(*paths))
            return verify._grade(by_name[name], run)

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            # C1/session-ended-unexpectedly: U2's SIGKILL-the-worker test, one entry.
            one = self.write(Path(tmp) / "one.json", [("U2", "core/internal/worker/manager_test.go",
                                                      "TestSIGKILLTheEchoWorkerMidStream")])
            self.assertEqual(grade("session-ended-unexpectedly", [one]).outcome, verify.PROVIDER)
            # C5/manifest-rediscovery-case also needs U11's rig; U2 alone leaves it UNGRADED.
            u2 = self.write(Path(tmp) / "u2.json", [
                ("U2", "core/internal/worker/rediscover_test.go", "TestRediscoveryDialsExactlyWhatTheFixtureSays"),
                ("U2", "core/internal/worker/rediscover_test.go", "TestRestartedCoreEndsWhatTheFixtureSaysIsDead")])
            self.assertEqual(grade("manifest-rediscovery-case", [u2]).outcome, verify.UNGRADED)
            rig = self.write(Path(tmp) / "rig.json", [("U11", "harness/rigs/kill-core.sh",
                                                       "rig-kill-core")])
            self.assertEqual(grade("manifest-rediscovery-case", [u2, rig]).outcome, verify.PROVIDER)
            # A failing cited test fails the fixture; an absent one never grades it.
            failed = self.write(Path(tmp) / "fail.json", [("U2", "core/internal/worker/manager_test.go",
                                                           "TestSIGKILLTheEchoWorkerMidStream")],
                                outcome="fail")
            self.assertEqual(grade("session-ended-unexpectedly", [failed]).outcome, verify.FAIL)
            self.assertEqual(grade("session-ended-unexpectedly", []).outcome, verify.UNGRADED)

    def write(self, path: Path, rows, outcome: str = "pass") -> Path:
        """A results file in the small JSON list form a provider writes for its own tests."""
        path.write_text(json.dumps([{"unit": u, "file": f, "test_id": t, "outcome": outcome}
                                    for u, f, t in rows]), encoding="utf-8")
        return path


class LiveAgainstStub(unittest.TestCase):
    def grade(self, core: StubCore, name: str, timeout: float = 2.0) -> verify.Result:
        fixtures, _ = load_fixtures(CONTRACT)
        [fx] = [f for f in fixtures if f.name == name]
        with StubServer(core) as url:
            run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e0", url, None, None, timeout, dict(INPUTS))
            return verify._grade(fx, run)

    def run_main(self, *args: str, core: StubCore | None = None) -> tuple[int, str]:
        core = core or StubCore()
        with tempfile.TemporaryDirectory() as tmp:
            password_file = Path(tmp) / "pw"
            password_file.write_text(PASSWORD + "\n")
            password_file.chmod(0o600)
            env = {"TSB_SUPERADMIN_USER": USER, "TSB_SUPERADMIN_PASSWORD_FILE": str(password_file),
                   "TSB_TARGET_ID": TARGET, "TSB_TARGET_NO_CRED_ID": NOCRED,
                   "TSB_PEER_USER": PEER, "TSB_PEER_PASSWORD_FILE": str(password_file),
                   "TSB_VIEWER_USER": VIEWER, "TSB_VIEWER_PASSWORD_FILE": str(password_file)}
            out = io.StringIO()
            with mock.patch.dict(os.environ, env), StubServer(core) as url:
                status = verify.main(["verify", url, "--profile", "e0", "--timeout", "5", *args], out)
        return status, out.getvalue()

    def open_sessions(self, core: StubCore) -> list[str]:
        return [s["id"] for s in core.sessions.values() if s["state"] not in ENDED]

    def assertNoFail(self, text: str) -> None:
        self.assertEqual([line for line in text.splitlines() if line.startswith("FAIL")], [], text)

    def open_stream(self, first: bytes, keepalives: int = 100):
        """An alert stream as a core serves it: `first`, then a keepalive comment every 0.1 s,
        never ending by itself (the stub gives up after 10 s). `state['ended']` flips only when
        the generator is exhausted, so a step that read the body to its end is distinguishable
        from one that returned at the run's deadline, with no timing."""
        state = {"ended": False}

        def stream():
            yield first
            for _ in range(keepalives):
                time.sleep(0.1)
                yield b": keepalive\n\n"
            state["ended"] = True

        return stream(), state

    def grade_live_alerts(self, first: bytes) -> tuple[verify.Result, dict]:
        """A live step that expects GET /alerts' 200, graded against a stream that never ends."""
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_tree(Path(tmp))
            step = {"http": {"method": "GET", "path": "/alerts"},
                    "expect": {"status": 200, "match": [{"pointer": "/0/state", "eq": "raised"},
                                                        {"pointer": "/1/state", "eq": "cleared"}]}}
            doc = {"seam": "C1", "name": "alerts-live", "mark": {"required-green": "e1"}, "kind": "rest",
                   "grade": "live", "corpus": ["session model §11"],
                   "description": "A broker.health holder reads the alert stream: its events, as they arrive.",
                   "case": {"inputs": ["user", "password"], "steps": [step]}}
            (root / "fixtures/C1/alerts-live.json").write_text(json.dumps(doc))
            fixtures, _ = load_fixtures(root)
            [fx] = [f for f in fixtures if f.name == "alerts-live"]
            core = StubCore()
            body, state = self.open_stream(first)
            core.alerts_body = body
            with StubServer(core) as url:
                run = Run(root, Registry.from_tree(root), "e1", url, None, None, 1.0, dict(INPUTS))
                result = verify._grade(fx, run)
            return result, state

    def test_a_successful_alert_stream_is_read_as_it_arrives(self):
        # The alert stream never ends by itself: a step that read its body to the end would wait
        # for as long as the core kept it open, then parse it as JSON. The runner reads its events
        # as they arrive, for at most the run's timeout, and holds each alert event to its schema.
        result, state = self.grade_live_alerts(ALERTS)
        self.assertEqual(result.outcome, verify.PASS, result.detail)
        # The run returned at its own deadline: a step that read the body to its end would have
        # consumed the whole stream, flipping `ended`. No wall-clock margin is read.
        self.assertFalse(state["ended"],
                         "the runner read the alert stream to its end instead of returning at the run deadline")
        result, _ = self.grade_live_alerts(b'event: alert\ndata: {"alert_id": "fixture-alert-1"}\n\n')
        self.assertEqual(result.outcome, verify.FAIL, result.detail)
        self.assertIn("event 0 (alert) breaks its schema", result.detail)

    def test_a_correct_provider_passes_every_live_fixture(self):
        from .test_provider_results import provider_results
        with tempfile.TemporaryDirectory() as res:
            status, text = self.run_main("--seams", "C1,C2", "--provider-results",
                                         *[str(p) for p in provider_results(Path(res))])
        self.assertNoFail(text)
        self.assertIn("seams graded: C1, C2", text)
        self.assertNotIn(PASSWORD, text)
        # Published at e0, required green at e1: the lab seeds their inputs with the E1 harness.
        self.assertIn("SKIP      C1 session-delete-not-owner-403", text)
        self.assertIn("SKIP      C1 no-resolving-credential-409", text)
        # A shape fixture takes the grade of the live fixture whose run validates real instances.
        self.assertIn("PASS      C1 session-record-shape", text)
        self.assertIn("graded with session-create-new", text)
        self.assertIn("PROVIDER  C1 policy-storage-example", text)
        # C3–C8 were filtered out, so this run is not green, however clean it is.
        self.assertEqual(status, verify.EXIT_NOT_ALL_GRADED, text)
        self.assertIn("filtered out by --seams", text)

    def test_reference_fixtures_are_ungraded_without_their_provider_results(self):
        # --provider-tests alone grades no reference fixture: the caller's word that the units ran
        # is not the run's own evidence, and the line under such a result points at the flag that
        # does supply it.
        status, text = self.run_main("--seams", "C1,C2")
        self.assertNoFail(text)
        self.assertIn("UNGRADED  C1 session-ended-unexpectedly", text)
        self.assertIn("--provider-results", text)
        self.assertIn("C1 (3 graded only by the provider results)", text)
        self.assertEqual(status, verify.EXIT_NOT_ALL_GRADED, text)

    def test_the_whole_contract_graded_is_the_only_green(self):
        # URL, artefacts and the providers' own results together grade every required-green
        # fixture of the profile, and only then does the run exit 0.
        from .test_artifacts import build, files
        from .test_provider_results import provider_results
        with tempfile.TemporaryDirectory() as art, tempfile.TemporaryDirectory() as res:
            build(Path(art), files())
            files_res = provider_results(Path(res))
            status, text = self.run_main("--artifacts", art, "--provider-results",
                                         *[str(p) for p in files_res])
        self.assertNoFail(text)
        self.assertIn(" 0 UNGRADED ", text)
        self.assertIn("seams graded: C1, C2, C3, C5, C6, C8", text)
        self.assertEqual(status, verify.EXIT_OK, text)

    def test_a_full_run_without_provider_results_is_not_green(self):
        # URL and artefacts, every seam, nothing filtered: the four reference fixtures only a
        # provider's own results can grade are UNGRADED, and that alone keeps the exit off 0.
        from .test_artifacts import build, files
        with tempfile.TemporaryDirectory() as art:
            build(Path(art), files())
            status, text = self.run_main("--artifacts", art)
        self.assertNoFail(text)
        self.assertIn(" 4 UNGRADED ", text)
        self.assertIn("4 required-green fixtures not graded by this run: 4 graded only by the provider "
                      "results this run did not include, 0 filtered out by --seams", text)
        self.assertEqual(status, verify.EXIT_NOT_ALL_GRADED, text)

    def test_a_run_deletes_the_sessions_it_created(self):
        core = StubCore()
        from .test_provider_results import provider_results
        with tempfile.TemporaryDirectory() as res:
            _status, text = self.run_main("--seams", "C1,C2", "--provider-results",
                                          *[str(p) for p in provider_results(Path(res))], core=core)
        self.assertNoFail(text)
        self.assertTrue(core.sessions)
        self.assertEqual(self.open_sessions(core), [])

    def test_sessions_a_failing_fixture_leaves_open_are_swept(self):
        # Every create below returns a record that breaks the schema, so each fixture fails before
        # it saves the id its own cleanup would delete. Without the run's sweep they stay open, and
        # repeated runs against a standing core pile up connections until capacity runs out.
        core = StubCore(bugs=frozenset({"leak-recording-path"}))
        status, text = self.run_main("--seams", "C1", core=core)
        self.assertEqual(status, verify.EXIT_FAIL)
        self.assertEqual(self.open_sessions(core), [])
        self.assertIn("cleanup: deleted", text)

    def test_missing_credentials_fail_as_input_missing_not_skip(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), StubServer(StubCore()) as url:
            status = verify.main(["verify", url, "--seams", "C1", "--timeout", "2"], out)
        self.assertEqual(status, verify.EXIT_FAIL)
        self.assertIn("FAIL      C1 auth-login-ok", out.getvalue())
        self.assertIn("seams with input missing: C1", out.getvalue())
        self.assertIn("input missing: user (set TSB_SUPERADMIN_USER)", out.getvalue())
        # SKIP is only ever a published fixture not yet required; a missing input never becomes one.
        skips = [line for line in out.getvalue().splitlines() if line.startswith("SKIP")]
        self.assertTrue(all(f"not required green in profile {verify.DEFAULT_PROFILE}" in line for line in skips), skips)
        self.assertFalse([line for line in skips if "input missing" in line])

    def test_each_provider_bug_fails_its_fixture(self):
        cases = {
            "cookie-not-httponly": ("auth-login-ok", "lacks HttpOnly"),
            "logout-client-only": ("auth-logout", "HTTP 200"),
            "ticket-reusable": ("attach-ticket-reused-close-4001", "close 4001"),
            "ticket-any-session": ("attach-ticket-wrong-session-close-4003", "close 4003"),
            "leak-recording-path": ("session-create-new", "recording_path"),
            "no-cred-403": ("no-resolving-credential-409", "HTTP 403"),
            "reuse-opens-new": ("session-create-reuse", "connection_id"),
            "delete-kills-siblings": ("session-delete", "killed"),
            "peer-may-delete": ("session-delete-not-owner-403", "HTTP 204"),
            "unknown-frame-closes": ("unknown-frame-ignored", "closed with 1003"),
            "healthz-leaks-version": ("healthz", "'version' is not allowed"),
            "ticket-never-expires": ("attach-ticket-expired-close-4001", "close 4001"),
            "reuse-picks-silently": ("session-create-reuse-ambiguous-409", "HTTP 201"),
            "error-leaks-path": ("session-create-unknown-target-404", "names a path on the broker host"),
        }
        for bug, (fixture, needle) in cases.items():
            with self.subTest(bug=bug):
                result = self.grade(StubCore(bugs=frozenset({bug}), ticket_ttl=1), fixture)
                self.assertEqual(result.outcome, verify.FAIL, f"{fixture} passed a provider with {bug}")
                self.assertIn(needle, result.detail)
                self.assertEqual(self.grade(StubCore(ticket_ttl=1), fixture).outcome, verify.PASS)

    def test_a_correct_e1_provider_passes_every_e1_live_fixture(self):
        # From E1 connection_linger keeps a connection up after its last session, which the e1
        # fixtures expect; the v0.1 fixtures name their connections, so lingering ones do no harm.
        with tempfile.TemporaryDirectory() as tmp:
            password_file = Path(tmp) / "pw"
            password_file.write_text(PASSWORD + "\n")
            password_file.chmod(0o600)
            env = {"TSB_SUPERADMIN_USER": USER, "TSB_SUPERADMIN_PASSWORD_FILE": str(password_file),
                   "TSB_TARGET_ID": TARGET, "TSB_TARGET_NO_CRED_ID": NOCRED,
                   "TSB_PEER_USER": PEER, "TSB_PEER_PASSWORD_FILE": str(password_file),
                   "TSB_VIEWER_USER": VIEWER, "TSB_VIEWER_PASSWORD_FILE": str(password_file)}
            out = io.StringIO()
            core = StubCore(linger=True)
            with mock.patch.dict(os.environ, env), StubServer(core) as url:
                status = verify.main(["verify", url, "--profile", "e1", "--timeout", "5", "--seams", "C1,C2"], out)
        text = out.getvalue()
        self.assertNoFail(text)
        for name in ("session-countdown", "session-record-shape-v0.2", "alerts-unauthenticated-401",
                     "alerts-without-broker-health-403", "reuse-409-lists-candidates", "token-event-on-detach",
                     "ws-frames-shape-v0.2", "session-create-reuse-empty-connection"):
            self.assertRegex(text, rf"PASS +C[12] {name} ", name)
        self.assertEqual(status, verify.EXIT_NOT_ALL_GRADED, text)  # C3–C8 were filtered out
        self.assertIn(("/alerts", "text/event-stream"), core.accepts)  # the operation's own media type
        self.assertIn(("/sessions", "application/json"), core.accepts)

    def test_each_v02_provider_bug_fails_its_fixture(self):
        cases = {
            "countdown-missing": ("session-countdown", "/session/ends_at is missing"),
            "reuse-409-lists-all": ("reuse-409-lists-candidates", "which none may"),
            "reuse-409-no-ids": ("reuse-409-lists-candidates", "/error/connection_ids is missing"),
            "alerts-open": ("alerts-without-broker-health-403", "HTTP 200"),
            "token-silent": ("token-event-on-detach", "did not arrive"),
        }
        for bug, (fixture, needle) in cases.items():
            with self.subTest(bug=bug):
                result = self.grade(StubCore(bugs=frozenset({bug}), linger=True), fixture, timeout=1.0)
                self.assertEqual(result.outcome, verify.FAIL, f"{fixture} passed a provider with {bug}")
                self.assertIn(needle, result.detail)
                self.assertEqual(self.grade(StubCore(linger=True), fixture).outcome, verify.PASS)

    def test_a_connection_with_no_session_is_reused_while_its_transport_is_up(self):
        # connection_linger exists so a quick reopen reuses the login (session model §5, §11; a
        # TACACS+ round trip saved): a connection whose last session ended is still a candidate
        # while it is up. The old rule — a candidate must carry a live or detached session — throws
        # that login away. An E0 core, whose connection_linger is 0, closes the connection with its
        # last session, and a closed connection is never a candidate: hence required green from e1.
        name = "session-create-reuse-empty-connection"
        self.assertEqual(self.grade(StubCore(linger=True), name).outcome, verify.PASS)
        for core in (StubCore(linger=True, bugs=frozenset({"reuse-needs-a-session"})), StubCore(linger=False)):
            with self.subTest(linger=core.linger, bugs=sorted(core.bugs)):
                result = self.grade(core, name)
                self.assertEqual(result.outcome, verify.FAIL)
                self.assertIn("HTTP 422", result.detail)

    def test_the_published_reuse_rule_counts_a_connection_with_no_session(self):
        rule = json.loads((CONTRACT / "openapi" / "tsb.openapi.json").read_text(encoding="utf-8"))
        text = rule["components"]["schemas"]["SessionCreateRequest"]["description"]
        self.assertIn("whose transport is up — whether or not it has a session now", text)
        self.assertIn("connection_linger", text)
        self.assertIn("never a candidate", text)
        self.assertNotIn("at least one of its sessions is live or detached", text)

    def test_a_provider_that_echoes_the_password_never_gets_it_printed(self):
        # The login's error message is the provider's; the runner redacts every secret it read.
        result = self.grade(StubCore(bugs=frozenset({"login-echoes-password"})), "session-list-get")
        self.assertEqual(result.outcome, verify.FAIL)
        self.assertIn("rejected password <redacted>", result.detail)
        self.assertNotIn(PASSWORD, result.detail)

    def test_a_fresh_login_s_sessions_are_deleted_even_when_its_fixture_fails(self):
        # The fresh login lives only as long as its fixture, so the fixture sweeps what it created.
        fixtures, _ = load_fixtures(CONTRACT)
        [base] = [f for f in fixtures if f.name == "auth-login-ok"]
        doc = {**base.doc, "case": {"inputs": ["user", "password", "target_id"], "steps": [
            {"http": {"method": "POST", "path": "/auth/login", "as": "fresh",
                      "body": {"username": "${user}", "password": "${password}"}}, "expect": {"status": 200}},
            {"http": {"method": "POST", "path": "/sessions", "as": "fresh",
                      "body": {"target_id": "${target_id}", "connection": "new", "term": "xterm-256color",
                               "cols": 80, "rows": 24}},
             "expect": {"status": 201, "match": [{"pointer": "/session/tier", "eq": 99}]},
             "save": {"sid": "/session/id"}}]}}
        fx = type(base)(base.path, base.rel, doc)
        core = StubCore()
        with StubServer(core) as url:
            run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e0", url, None, None, 2.0, dict(INPUTS))
            result = verify._grade(fx, run)
        self.assertEqual(result.outcome, verify.FAIL)       # the match failed after the create
        self.assertTrue(core.sessions)
        self.assertEqual([s["id"] for s in core.sessions.values() if s["state"] not in ENDED], [])

    def test_a_streaming_provider_cannot_exhaust_the_runner(self):
        # A socket that streams while the runner waits for a close keeps only a bounded tail.
        class Streaming:
            def __init__(self, chunks: int) -> None:
                self.left = chunks

            def recv(self, timeout: float):
                if self.left:
                    self.left -= 1
                    return wf.Message("binary", b"y" * 65536)
                return wf.Message("close", b"", 4010, "killed")

        fixtures, _ = load_fixtures(CONTRACT)
        [fx] = [f for f in fixtures if f.name == "session-closed-close-4010"]
        live = _exchange.Live(fx, Run(CONTRACT, Registry.from_tree(CONTRACT), "e0", "https://127.0.0.1", None,
                                      None, 5.0))
        live.sockets["w"] = (Streaming(48), bytearray())  # 3 MiB
        live.ws_step({"ws_expect": "w", "close": 4010}, "step 1")
        self.assertLessEqual(len(live.sockets["w"][1]), 1 << 20)

    def test_a_with_fixture_fails_with_the_fixture_it_leans_on(self):
        with StubServer(StubCore(bugs=frozenset({"leak-recording-path"}))) as url:
            fixtures, _ = load_fixtures(CONTRACT)
            run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e0", url, None, None, 2.0, dict(INPUTS))
            wanted = [f for f in fixtures if f.name in ("session-create-new", "session-record-shape")]
            results, _ = verify._grade_all(wanted, run, {"C1"})
        self.assertEqual([(r.fixture.name, r.outcome) for r in results],
                         [("session-create-new", verify.FAIL), ("session-record-shape", verify.FAIL)])
        self.assertIn("graded with session-create-new: ", results[1].detail)

    def test_recording_off_is_a_valid_provider(self):
        # A single-user deployment may disable recording (open_decisions §5); the record says so.
        self.assertEqual(self.grade(StubCore(recording=False), "session-create-new").outcome, verify.PASS)


if __name__ == "__main__":
    unittest.main()
