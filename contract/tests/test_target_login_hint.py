"""Additive SSH hint shape, consumer fallback and synthetic fixture-app responses.

Production storage/runtime/material behavior is owed to U2/U6, not verified by this app.
"""

import copy
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tsb_contract_client as tcc
from tsbc._serve import PASSWORD, PEER, TARGET, USER, VIEWER, ServeApp
from tsbc.schema import Registry

from . import CONTRACT
from .stub_server import StubServer

RECORD = "openapi/tsb.openapi.json#/components/schemas/TargetRecord"
VALUES = ("saved", "choice_required", "none", "unavailable")


class Schema(unittest.TestCase):
    def test_hint_is_optional_and_only_ssh_accepts_it(self):
        registry = Registry.from_tree(CONTRACT)
        for kind in ("ssh", "telnet", "serial", "echo"):
            record = {"id": "hint-target", "type": kind, "tags": [], "discovered_by": "manual"}
            if kind in ("ssh", "telnet"):
                record.update(host="192.0.2.10", port=22 if kind == "ssh" else 23)
            self.assertEqual(registry.validate(record, RECORD), [])
            for value in (*VALUES, "future", None, False):
                errors = registry.validate({**record, "login_hint": value}, RECORD)
                self.assertEqual(not errors, kind == "ssh" and value in VALUES, (kind, value, errors))
            if kind in ("ssh", "telnet"):
                for field in ("host", "port"):
                    invalid = dict(record)
                    del invalid[field]
                    self.assertTrue(registry.validate(invalid, RECORD))

    def test_older_records_and_old_consumer_remain_compatible(self):
        registry = Registry.from_tree(CONTRACT)
        for name in ("target-get", "targets-list"):
            fixture = json.loads((CONTRACT / "fixtures/C1" / f"{name}.json").read_text())
            example = fixture["case"]["steps"][0]["expect"]["example"]
            records = example["targets"] if name == "targets-list" else [example]
            for record in records:
                self.assertNotIn("login_hint", record)
                self.assertEqual(registry.validate(record, RECORD), [])
        source = subprocess.check_output([
            "git", "show", "v0.20.0:contract/client/tsb_contract_client/client.py"], cwd=CONTRACT.parent).decode()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        module_path = Path(temporary.name) / "older_client.py"
        module_path.write_text(source)
        spec = importlib.util.spec_from_file_location("tsb_contract_client._older_hint_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        old = module.Client("http://127.0.0.1")
        hinted = {**records[0], "login_hint": "saved"}
        with mock.patch.object(old, "_call", return_value=hinted):
            self.assertEqual(old.get_target(hinted["id"])["id"], hinted["id"])
        with mock.patch.object(old, "_call", return_value={"targets": [hinted]}):
            self.assertEqual(next(iter(old.iter_targets()))["id"], hinted["id"])

    def test_fixture_marks_headers_and_explicit_owed_provider_obligations(self):
        fixtures = sorted((CONTRACT / "fixtures/C1").glob("target-login-hint-*.json"))
        self.assertEqual(len(fixtures), 16)
        index = json.loads((CONTRACT / "fixtures/graded-by.json").read_text())
        for path in fixtures:
            fixture = json.loads(path.read_text())
            self.assertEqual(fixture["mark"], {"published": "e4", "required-green": "e5"})
            self.assertEqual((fixture["kind"], fixture["grade"]), ("rest", "reference"))
            entries = index["C1/" + fixture["name"]]
            if fixture["name"] != "target-login-hint-older-core-and-unknown":
                self.assertTrue(any(e["unit"] == "U2" and e.get("owed") for e in entries))
            for step in fixture["case"]["steps"]:
                expect = step["expect"]
                example = expect.get("example", {})
                records = example.get("targets", [example])
                if any("login_hint" in record for record in records):
                    self.assertEqual(expect["headers"], {"Cache-Control": "no-store"})


class ClientDecoding(unittest.TestCase):
    def test_absent_and_unknown_suppress_hint_on_all_target_methods(self):
        client = tcc.Client("http://127.0.0.1")
        for kind in ("ssh", "telnet", "serial", "echo", "future-type"):
            for value in (*VALUES, "future", None, {}, False):
                raw = {"id": "hint-target", "type": kind, "login_hint": value, "future-field": "kept"}
                expected = dict(raw)
                if kind != "ssh" or value not in VALUES:
                    del expected["login_hint"]
                with mock.patch.object(client, "_call", return_value=raw):
                    self.assertEqual(client.get_target("hint-target"), expected)
                with mock.patch.object(client, "_call", return_value={"targets": [raw]}):
                    self.assertEqual(client.list_targets(), {"targets": [expected]})
                    self.assertEqual(list(client.iter_targets()), [expected])
                self.assertIn("login_hint", raw)
        raw = {"id": "old", "type": "ssh"}
        with mock.patch.object(client, "_call", return_value=raw):
            self.assertNotIn("login_hint", client.get_target("old"))
        with mock.patch.object(client, "_call", return_value={"targets": [raw]}):
            self.assertNotIn("login_hint", client.list_targets()["targets"][0])
            self.assertNotIn("login_hint", next(iter(client.iter_targets())))


class ServedHints(unittest.TestCase):
    def setUp(self):
        self.app = ServeApp()
        server = StubServer(self.app)
        self.url = server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        self.client = tcc.Client(self.url)
        self.client.login(USER, PASSWORD)
        self.app.creds[USER] = {"hint-canary-reference": {"reference": "hint-canary-reference", "kind": "password",
                                                     "label": "hint-canary-label", "login_name": "hint-canary-user",
                                                     "secret": "FIXTURE-NOT-A-PASSWORD"}}

    def assert_hint(self, value, tid=TARGET, client=None):
        client = client or self.client
        for path in ("/targets", "/targets/" + tid):
            response = client._http.request("GET", path)
            self.assertEqual(response.status, 200)
            body = json.loads(response.body)
            record = next(r for r in body["targets"] if r["id"] == tid) if path == "/targets" else body
            if value is None:
                self.assertNotIn("login_hint", record)
            else:
                self.assertEqual(record["login_hint"], value)
                self.assertEqual({name.lower(): value for name, value in response.headers}.get("cache-control"), "no-store")
            for forbidden in ("hint-canary-reference", "hint-canary-label", "hint-canary-user", "FIXTURE-NOT-A-PASSWORD"):
                self.assertNotIn(forbidden, response.body.decode())
        self.assertNotIn("login_hint", self.app.targets[tid])

    def grant(self, reference="hint-org", **more):
        gid = str(len(self.app.grants))
        self.app.org_creds[reference] = {"reference": reference, "kind": "none", "unlock_class": "broker_unlocked"}
        self.app.grants[gid] = {"user": USER, "selectors": ["*"], "credential_ref": reference, **more}

    def test_zero_one_many_dedup_and_caller_scope(self):
        self.assert_hint("none")
        self.app.profiles[USER] = {TARGET: "hint-canary-reference"}
        self.assert_hint("saved")
        self.grant()
        self.grant()
        self.assert_hint("choice_required")
        del self.app.profiles[USER][TARGET]
        self.assert_hint("saved")
        self.app.profiles[USER][TARGET] = "hint-canary-reference"
        self.grant("hint-canary-reference")
        self.assert_hint("choice_required")
        peer = tcc.Client(self.url)
        peer.login(PEER, PASSWORD)
        self.assert_hint("none", client=peer)

    def test_faults_are_fixed_and_preserve_other_rows(self):
        self.app.profiles[USER] = {TARGET: "missing-canary"}
        self.grant()
        self.assert_hint("unavailable")
        self.assert_hint("saved", tid="normal")
        self.app.profiles[USER].clear()
        del self.app.org_creds["hint-org"]
        self.assert_hint("unavailable")
        self.app.org_creds["hint-org"] = {"reference": "hint-org", "kind": "none"}
        self.app.grants["0"]["selectors"] = ["host:["]
        self.assert_hint("unavailable")
        self.app.grants["0"]["selectors"] = ["*"]
        self.app.org_creds["hint-org"] = {"reference": "hint-org"}  # malformed metadata
        self.assert_hint("unavailable")

    def test_locked_and_unsealed_are_different_and_hints_do_not_touch_unlocks(self):
        self.app.profiles[USER] = {TARGET: "hint-canary-reference"}
        before = (set(self.app.unlocked), dict(self.app.activity))
        self.assert_hint("saved")
        self.assertEqual((self.app.unlocked, self.app.activity), before)
        self.app.unlocked.update(self.app.logins)
        self.assert_hint("saved")
        self.app.vault_users.remove(USER)
        self.assert_hint("unavailable")
        self.app.profiles[USER].clear()
        self.grant()
        self.assert_hint("saved")

    def test_discover_only_and_non_ssh_omit_and_recheck_is_fresh(self):
        viewer = tcc.Client(self.url)
        viewer.login(VIEWER, PASSWORD)
        self.assert_hint(None, client=viewer)
        self.assert_hint(None, tid="telnet-scenario")
        self.app.profiles[USER] = {TARGET: "hint-canary-reference"}
        self.assert_hint("saved")
        self.app.profiles[USER].clear()
        self.assert_hint("none")
        before = copy.deepcopy(self.app.targets)
        list(self.client.iter_targets(limit=2))
        self.assertEqual(self.app.targets, before)

    def test_broken_mapping_is_unavailable_never_none(self):
        self.app.profiles[USER] = {TARGET: ""}
        self.assert_hint("unavailable")
        self.app.profiles[USER] = {TARGET: "does-not-exist"}
        self.assert_hint("unavailable")

    def test_broken_org_record_is_unavailable(self):
        self.app.profiles.pop(USER, None)
        self.app.org_creds["broken-org"] = {"reference": "broken-org", "kind": "none"}
        self.app.grants["0"] = {"user": USER, "selectors": ["*"], "credential_ref": "broken-org"}
        self.assert_hint("unavailable")
        self.app.org_creds["broken-org"]["unlock_class"] = "invalid_class"
        self.assert_hint("unavailable")

    def test_other_users_broken_grant_does_not_affect_caller(self):
        self.app.profiles[USER] = {TARGET: "hint-canary-reference"}
        self.assert_hint("saved")
        self.app.grants["bad-1"] = {"user": PEER, "selectors": ["host:["], "credential_ref": "nonexistent"}
        self.app.grants["bad-2"] = {"user": PEER, "selectors": None}
        self.app.grants["bad-3"] = "not-a-dict"
        self.assert_hint("saved")

    def test_one_preparation_per_inventory_request(self):
        with mock.patch.object(self.app, "vault_prepare_resolution", wraps=self.app.vault_prepare_resolution) as prep:
            resp = self.client._http.request("GET", "/targets")
            self.assertEqual(resp.status, 200)
            self.assertEqual(prep.call_count, 1)

        with mock.patch.object(self.app, "vault_prepare_resolution", wraps=self.app.vault_prepare_resolution) as prep:
            resp = self.client._http.request("GET", f"/targets/{TARGET}")
            self.assertEqual(resp.status, 200)
            self.assertEqual(prep.call_count, 1)

    def test_missing_personal_reference_is_unavailable(self):
        self.app.profiles[USER] = {TARGET: "hint-canary-reference"}
        del self.app.creds[USER]["hint-canary-reference"]["reference"]
        self.assert_hint("unavailable")

    def test_mismatched_personal_reference_is_unavailable(self):
        self.app.profiles[USER] = {TARGET: "hint-canary-reference"}
        self.app.creds[USER]["hint-canary-reference"]["reference"] = "another-reference"
        self.assert_hint("unavailable")

    def test_no_preparation_without_eligible_rows(self):
        for path in ("/targets?tag=absent", "/targets?type=telnet", "/targets/telnet-scenario"):
            with self.subTest(path=path), mock.patch.object(
                    self.app, "vault_prepare_resolution", wraps=self.app.vault_prepare_resolution) as prep:
                self.assertEqual(self.client._http.request("GET", path).status, 200)
                prep.assert_not_called()

    def test_dangling_grant_fault_is_scoped_to_matching_target(self):
        self.app.grants["bad"] = {"user": USER, "selectors": ["id:other"], "credential_ref": "missing"}
        self.assert_hint("none")
        self.app.profiles[USER] = {TARGET: "hint-canary-reference"}
        self.assert_hint("saved")
        self.app.grants["bad"]["selectors"] = ["id:" + TARGET]
        self.assert_hint("unavailable")

    def test_broken_org_entry_fault_is_scoped_to_matching_target(self):
        self.grant(selectors=["id:other"])
        del self.app.org_creds["hint-org"]["unlock_class"]
        self.assert_hint("none")
        self.app.profiles[USER] = {TARGET: "hint-canary-reference"}
        self.assert_hint("saved")
        self.app.grants["0"]["selectors"] = ["id:" + TARGET]
        self.assert_hint("unavailable")

    def test_malformed_selector_faults_all_eligible_targets(self):
        self.app.grants["bad"] = {"user": USER, "selectors": ["id:other", "host:["],
                                  "credential_ref": "missing"}
        self.assert_hint("unavailable")
        self.assert_hint("unavailable", tid="normal")

    def test_write_answers_carry_no_hint(self):
        created = self.client.create_target({
            "id": "write-ssh-1", "type": "ssh", "host": "192.0.2.77"
        })
        self.assertNotIn("login_hint", created)
        registry = Registry.from_tree(CONTRACT)
        self.assertEqual(registry.validate(created, "openapi/tsb.openapi.json#/components/schemas/TargetWriteRecord"), [])

        replaced = self.client.replace_target("write-ssh-1", {
            "id": "write-ssh-1", "type": "ssh", "host": "192.0.2.78"
        })
        self.assertNotIn("login_hint", replaced)
        self.assertEqual(registry.validate(replaced, "openapi/tsb.openapi.json#/components/schemas/TargetWriteRecord"), [])
        self.client.delete_target("write-ssh-1")


class Round2ReviewFindings(unittest.TestCase):
    def test_target_write_record_schema_forbids_login_hint(self):
        registry = Registry.from_tree(CONTRACT)
        schema_uri = "openapi/tsb.openapi.json#/components/schemas/TargetWriteRecord"
        doc = {
            "id": "write-router-01",
            "type": "ssh",
            "tags": ["router"],
            "discovered_by": "tsb",
            "host": "192.0.2.10",
            "port": 22
        }
        self.assertEqual(registry.validate(doc, schema_uri), [])
        doc_with_hint = dict(doc, login_hint="none")
        self.assertTrue(registry.validate(doc_with_hint, schema_uri))

    def test_split_branches_describe_only_their_own_type(self):
        openapi = json.loads((CONTRACT / "openapi/tsb.openapi.json").read_text())
        branches = openapi["components"]["schemas"]["TargetRecord"]["oneOf"]
        ssh_desc = branches[0]["description"].lower()
        telnet_desc = branches[1]["description"].lower()
        self.assertIn("ssh", ssh_desc)
        self.assertNotIn("telnet", ssh_desc)
        self.assertIn("telnet", telnet_desc)
        self.assertNotIn("ssh", telnet_desc)

    def test_cache_control_schema_is_string_without_const(self):
        openapi = json.loads((CONTRACT / "openapi/tsb.openapi.json").read_text())
        for path in ("/targets", "/targets/{id}"):
            cc = openapi["paths"][path]["get"]["responses"]["200"]["headers"]["Cache-Control"]
            self.assertEqual(cc["schema"], {"type": "string"})
            self.assertNotIn("const", cc["schema"])
            self.assertIn("no-store", cc["description"])

    def test_match_holds_allows_only_login_hint_as_extra_field_on_create_readback(self):
        from tsbc._exchange import _match_holds
        post_record = {
            "id": "target-1",
            "type": "ssh",
            "host": "192.0.2.1",
            "port": 22,
            "tags": ["lab"],
            "discovered_by": "tsb"
        }
        matcher = {"pointer": "", "eq": "${created_record}", "ignore": ["login_hint"]}
        sub = lambda key: post_record if key == "created_record" else key

        # Exact match (no hint)
        self.assertTrue(_match_holds(post_record, matcher, sub))
        # GET returns login_hint extra
        get_with_hint = dict(post_record, login_hint="none")
        self.assertTrue(_match_holds(get_with_hint, matcher, sub))
        # GET returns any other extra field -> rejected
        get_with_other_extra = dict(post_record, login_hint="none", unexpected="extra")
        self.assertFalse(_match_holds(get_with_other_extra, matcher, sub))
        # GET missing an original field -> rejected
        get_missing_field = {k: v for k, v in get_with_hint.items() if k != "tags"}
        self.assertFalse(_match_holds(get_missing_field, matcher, sub))
        # GET modified value -> rejected
        get_modified_field = dict(get_with_hint, port=2222)
        self.assertFalse(_match_holds(get_modified_field, matcher, sub))


class MatcherConfidentiality(unittest.TestCase):
    CANARIES = ("hint-private-ref", "hint-private-label", "hint-private-user",
                "FIXTURE-NOT-A-PASSWORD", "/fixture-private/credential")

    def test_exact_match_rejects_stray_hint_without_opt_in(self):
        from tsbc._exchange import _match_holds
        self.assertFalse(_match_holds({"id": "target", "login_hint": "none"},
                                     {"pointer": "", "eq": {"id": "target"}}, lambda _: None))

    def test_whole_answer_matcher_checks_nested_values_and_keys(self):
        from tsbc._exchange import _match_holds
        matcher = {"pointer": "", "not_contains": list(self.CANARIES)}
        self.assertTrue(_match_holds({"nested": [{"public": "value"}]}, matcher, lambda _: None))
        for canary in self.CANARIES:
            for answer in ({"nested": [{"value": canary}]}, {"nested": [{canary: "value"}]}):
                with self.subTest(canary=canary, answer=answer):
                    self.assertFalse(_match_holds(answer, matcher, lambda _: None))

    def test_confidentiality_fixture_rejects_canaries_anywhere_in_answer(self):
        from tsbc import _exchange
        from tsbc._run import Run
        from tsbc.fixtures import load_fixtures

        fixtures, _ = load_fixtures(CONTRACT)
        fx = next(f for f in fixtures if f.doc["name"] == "target-login-hint-never-revealed")
        live = _exchange.Live(fx, Run(CONTRACT, Registry.from_tree(CONTRACT), "e5", None, None, None, 1.0))
        for step in fx.case["steps"]:
            expect = step["expect"]
            live.check_matches(expect["example"], expect["match"], "clean")
            for canary in self.CANARIES:
                for location in ("name", "group", "tags", "key"):
                    with self.subTest(path=step["http"]["path"], canary=canary, location=location):
                        answer = copy.deepcopy(expect["example"])
                        record = answer["targets"][0] if "targets" in answer else answer
                        if location == "key":
                            # A string-valued public field still has a schema-valid JSON representation.
                            record["name"] = json.dumps({canary: "public"})
                        else:
                            record[location] = [canary] if location == "tags" else canary
                        schema = ("openapi/tsb.openapi.json#/components/schemas/TargetList"
                                  if "targets" in answer else RECORD)
                        self.assertEqual(live.reg.validate(answer, schema), [])
                        with self.assertRaises(_exchange.Failed) as failure:
                            live.check_matches(answer, expect["match"], "leak")
                        self.assertNotIn(canary, str(failure.exception))


class AdditivityGuard(unittest.TestCase):
    def test_split_branches_still_detect_old_field_tightening(self):
        from .test_release_b_additive import OPENAPI, additive
        original = json.loads(subprocess.check_output([
            "git", "show", "v0.20.0:contract/openapi/tsb.openapi.json"], cwd=CONTRACT.parent))
        old = original["components"]["schemas"]["TargetRecord"]["oneOf"]
        new = json.loads((CONTRACT / "openapi/tsb.openapi.json").read_text())["components"]["schemas"]["TargetRecord"]["oneOf"]
        problems = []
        additive(old, new, "components/schemas/TargetRecord/oneOf", OPENAPI, set(), problems)
        self.assertEqual(problems, [])
        for index in (0, 1):
            broken = copy.deepcopy(new)
            broken[index]["required"].append("name")
            problems = []
            additive(old, broken, "components/schemas/TargetRecord/oneOf", OPENAPI, set(), problems)
            self.assertTrue(any("required grew" in problem for problem in problems))
