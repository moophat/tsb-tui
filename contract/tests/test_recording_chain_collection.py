"""Chain collections validate linked evidence and fail legacy checks closed."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from tsbc import artifacts
from tsbc._run import Run
from tsbc.schema import Registry

from . import CONTRACT
from .test_artifacts import build


class ChainCollection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        cases = json.loads((CONTRACT / "fixtures/C8/terminal-continuation-chain.json").read_text())
        doc = cases["case"]["instances"][0]["doc"]
        state_fixture = json.loads((CONTRACT / "fixtures/C6/continuity-record-valid.json").read_text())
        state = copy.deepcopy(state_fixture["case"]["instances"][0]["doc"])
        state["session"]["id"] = doc["session_id"]
        state["recording_path"] = doc["pairs"][0]["path"]
        state["recording_next_generation"] = len(doc["pairs"])
        files, pairs = {}, []
        for pair in doc["pairs"]:
            tty = pair["path"]
            side = tty.removesuffix(".ttyrec") + ".dir"
            files[tty] = bytes.fromhex(pair["ttyrec_hex"])
            files[side] = bytes.fromhex(pair["sidecar_hex"])
            pairs.append({"ttyrec": tty, "sidecar": side})
        build(self.root, files)
        self.manifest = json.loads((self.root / "collected.json").read_text())
        self.manifest["recording_chains"] = [{"session_record": state, "pairs": pairs, "audit": doc["audit"]}]
        (self.root / "audit.jsonl").write_text("".join(json.dumps(event) + "\n" for event in doc["audit"]))
        self.manifest["items"].append({"seam": "C6", "kind": "audit", "path": "audit.jsonl"})

    def load(self):
        (self.root / "collected.json").write_text(json.dumps(self.manifest))
        return artifacts.load_collected(Run(CONTRACT, Registry.from_tree(CONTRACT), "e4", None, self.root, None, 1))

    def test_chain_requires_audit_log_and_matching_link(self):
        self.manifest["items"] = [item for item in self.manifest["items"] if item["kind"] != "audit"]
        self.assertIn("audit", self.load())
        audit = self.root / "audit.jsonl"
        audit.write_text(json.dumps(self.manifest["recording_chains"][0]["audit"][0]) + "\n")
        self.manifest["items"].append({"seam": "C6", "kind": "audit", "path": "audit.jsonl"})
        self.assertIn("audit", self.load())

    def test_chain_rejects_altered_audit_timestamp(self):
        events = copy.deepcopy(self.manifest["recording_chains"][0]["audit"])
        events[1]["ts"] = "2026-11-15T02:15:01Z"
        (self.root / "audit.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
        self.manifest["items"].append({"seam": "C6", "kind": "audit", "path": "audit.jsonl"})
        self.assertIn("audit", self.load())

    def test_omitted_extent_and_sealed_facts_fail(self):
        source = json.loads((CONTRACT / "fixtures/C6/recording-integrity-event-shape.json").read_text())
        examples = [case["doc"] for case in source["case"]["instances"]
                    if case["valid"] and case["doc"].get("detail", {}).get("kind") in ("extent", "sealed")]
        self.assertEqual(len(examples), 2)
        existing = self.manifest["recording_chains"][0]["audit"]
        for event in examples:
            with self.subTest(kind=event["detail"]["kind"]):
                (self.root / "audit.jsonl").write_text("".join(json.dumps(e) + "\n" for e in [*existing, event]))
                self.assertIn("audit facts differ", self.load())

    def test_fabricated_manifest_fact_and_torn_log_fail(self):
        audit = self.root / "audit.jsonl"
        old = audit.read_text()
        audit.write_text(json.dumps(self.manifest["recording_chains"][0]["audit"][0]) + "\n")
        self.assertIn("audit facts differ", self.load())
        audit.write_text(old.rstrip("\n"))
        self.assertIn("complete audit log", self.load())

    def test_unrelated_manifest_grader_is_not_refused(self):
        self.load()
        run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e4", None, self.root, None, 1)
        ok, message = artifacts.grade("audit-valid", run)
        self.assertTrue(ok, message)

    def test_complete_chain_and_legacy_refusal(self):
        result = self.load()
        self.assertIsInstance(result, artifacts.Collected, result)
        self.assertEqual(result.chains["s1"].value, (2, 2))
        run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e4", None, self.root, None, 1)
        self.assertTrue(artifacts.grade("recording-chains-complete", run)[0])
        self.assertFalse(artifacts.grade("recordings-parse", run)[0])

    def test_original_reference_differs_from_collected_path(self):
        spec = self.manifest["recording_chains"][0]
        old = spec["session_record"]["recording_path"]
        original = "store/recordings/s1.ttyrec"
        spec["session_record"]["recording_path"] = original
        for event in spec["audit"]:
            if event["recording_path"] == old:
                event["recording_path"] = original
            if event["detail"].get("previous_path") == old:
                event["detail"]["previous_path"] = original
        (self.root / "audit.jsonl").write_text("".join(json.dumps(event) + "\n" for event in spec["audit"]))
        result = self.load()
        self.assertIsInstance(result, artifacts.Collected, result)
        self.assertEqual(result.chains["s1"].value, (2, 2))

    def test_spent_generation_is_complete_and_missing_notice_fails(self):
        # generation 1 spent, chain 0 -> 2, next generation 3: the gap is not incompleteness
        spec = self.manifest["recording_chains"][0]
        gen2_path = "recordings/s1.chain/continuation-2.ttyrec"
        gen2_side = gen2_path.removesuffix(".ttyrec") + ".dir"
        src = spec["pairs"][1]
        (self.root / gen2_path).write_bytes((self.root / src["ttyrec"]).read_bytes())
        (self.root / gen2_side).write_bytes((self.root / src["sidecar"]).read_bytes())
        self.manifest["items"].append({"seam": "C8", "kind": "recording", "path": gen2_path})
        self.manifest["items"].append({"seam": "C8", "kind": "recording-direction", "path": gen2_side})
        spec["pairs"] = [spec["pairs"][0], {"ttyrec": gen2_path, "sidecar": gen2_side}]
        link = spec["audit"][1]
        link["detail"]["generation"] = 2
        link["detail"]["key"] = "ri1:s1:c:2"
        link["detail"]["continuation_path"] = gen2_path
        spec["session_record"]["recording_next_generation"] = 3
        (self.root / "audit.jsonl").write_text("".join(json.dumps(e) + "\n" for e in spec["audit"]))
        result = self.load()
        self.assertIsInstance(result, artifacts.Collected, result)
        run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e4", None, self.root, None, 1)
        self.assertTrue(artifacts.grade("recording-chains-complete", run)[0])

    def test_missing_linked_pair_fails_closed(self):
        # the link names generation 2, but its pair is not collected: fails closed
        spec = self.manifest["recording_chains"][0]
        gen2_path = "recordings/s1.chain/continuation-2.ttyrec"
        link = spec["audit"][1]
        link["detail"]["generation"] = 2
        link["detail"]["key"] = "ri1:s1:c:2"
        link["detail"]["continuation_path"] = gen2_path
        spec["pairs"] = [spec["pairs"][0]]
        spec["session_record"]["recording_next_generation"] = 3
        (self.root / "audit.jsonl").write_text("".join(json.dumps(e) + "\n" for e in spec["audit"]))
        result = self.load()
        self.assertIsInstance(result, str, result)  # the walk names generation 2; it is missing, so fails closed
        self.assertIn("s1", result)

    def test_orphan_continuation_is_reported_and_unused(self):
        # generation 1 present on disk with no link to it: reported as an orphan, never used
        spec = self.manifest["recording_chains"][0]
        gen2_path = "recordings/s1.chain/continuation-2.ttyrec"
        gen2_side = gen2_path.removesuffix(".ttyrec") + ".dir"
        src = spec["pairs"][1]
        (self.root / gen2_path).write_bytes((self.root / src["ttyrec"]).read_bytes())
        (self.root / gen2_side).write_bytes((self.root / src["sidecar"]).read_bytes())
        self.manifest["items"].append({"seam": "C8", "kind": "recording", "path": gen2_path})
        self.manifest["items"].append({"seam": "C8", "kind": "recording-direction", "path": gen2_side})
        spec["pairs"] = [spec["pairs"][0], {"ttyrec": gen2_path, "sidecar": gen2_side}]
        link = spec["audit"][1]
        link["detail"]["generation"] = 2
        link["detail"]["key"] = "ri1:s1:c:2"
        link["detail"]["continuation_path"] = gen2_path
        spec["session_record"]["recording_next_generation"] = 3
        (self.root / "audit.jsonl").write_text("".join(json.dumps(e) + "\n" for e in spec["audit"]))
        result = self.load()
        self.assertIsInstance(result, artifacts.Collected, result)
        self.assertIn("recordings/s1.chain/continuation-1.ttyrec", result.orphans)
        walked = [e.path for e in result.chains["s1"].evidence]
        self.assertNotIn("recordings/s1.chain/continuation-1.ttyrec", walked)
        run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e4", None, self.root, None, 1)
        self.assertTrue(artifacts.grade("recording-chains-complete", run)[0])

    def test_missing_metadata_and_unlisted_pair_fail(self):
        del self.manifest["recording_chains"]
        self.assertIn("lacks complete chain metadata", self.load())
        self.manifest["recording_chains"] = [{"session_record": {}, "pairs": [], "audit": []}]
        self.assertIn("breaks its schema", self.load())

    def test_unlisted_continuation_and_audit_facts_do_not_become_legacy(self):
        del self.manifest["recording_chains"]
        self.manifest["items"] = [item for item in self.manifest["items"] if ".chain/" not in item["path"]]
        self.assertIn("lacks complete chain metadata", self.load())
        for path in (self.root / "recordings/s1.chain").iterdir():
            path.unlink()
        audit = self.root / "audit.jsonl"
        event = json.loads((CONTRACT / "fixtures/C8/terminal-continuation-chain.json").read_text())["case"]["instances"][0]["doc"]["audit"][1]
        audit.write_text(json.dumps(event) + "\n")
        self.manifest["items"].append({"seam": "C6", "kind": "audit", "path": "audit.jsonl"})
        self.assertIn("without complete chain metadata", self.load())

    def test_held_final_pair_and_omitted_audit_fact_fail(self):
        spec = self.manifest["recording_chains"][0]
        spec["session_record"]["recording_held"] = True
        self.assertIn("held pair lacks a quarantine notice", self.load())
        spec["session_record"]["recording_held"] = False
        event = copy.deepcopy(spec["audit"][0])
        event["detail"]["baseline_seq"] = 99
        event["detail"]["key"] = "ri1:s1:u:99:1"
        audit = self.root / "audit.jsonl"
        audit.write_text("".join(json.dumps(e) + "\n" for e in [*spec["audit"], event]))
        self.assertIn("audit", self.load())

    def test_quarantined_original_cannot_be_graded_as_legacy(self):
        event = self.manifest["recording_chains"][0]["audit"][0]
        del self.manifest["recording_chains"]
        self.manifest["items"] = [item for item in self.manifest["items"] if ".chain/" not in item["path"]]
        for path in (self.root / "recordings/s1.chain").iterdir():
            path.unlink()
        (self.root / "audit.jsonl").write_text(json.dumps(event) + "\n")
        self.manifest["items"].append({"seam": "C6", "kind": "audit", "path": "audit.jsonl"})
        self.assertIn("without complete chain metadata", self.load())

    def test_login_answer_in_continuation_sidecar_is_detected(self):
        path = self.root / self.manifest["recording_chains"][0]["pairs"][1]["sidecar"]
        path.write_bytes(b"\x07")
        self.assertIn("login answer", self.load())
