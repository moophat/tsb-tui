"""Target writes: closed definitions, ruled refusal order, preservation and audit shapes."""

import copy
import json
import subprocess
import unittest
from unittest.mock import patch

from tsbc import _exchange
from tsbc._serve import (
    PASSWORD,
    PEER,
    TARGET,
    USER,
    VENDOR_PROMPT_TEMPLATES,
    VIEWER,
    ServeApp,
)
from tsbc.fixtures import load_fixtures
from tsbc.schema import Registry

from . import CONTRACT
from .test_release_b_additive import additive

DEFINITION = "openapi/tsb.openapi.json#/components/schemas/TargetDefinition"
BASE = {"id": "fixture-written", "type": "ssh", "host": "192.0.2.60"}


class TargetWrites(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.from_tree(CONTRACT)

    def setUp(self):
        self.app = ServeApp()
        self.cookies = {}
        for user in (USER, PEER, VIEWER):
            status, headers, _ = self.app.http("POST", "/auth/login", {},
                                               {"username": user, "password": PASSWORD})
            self.assertEqual(status, 200)
            self.cookies[user] = {"cookie": headers[0][1].split(";", 1)[0]}

    def request(self, method, path="/targets", body=None, user=USER):
        status, _, response = self.app.http(method, path, self.cookies[user], body)
        return status, response

    def test_definition_bounds_match_the_shipped_server_validator(self):
        cases = [BASE, {**BASE, "type": "telnet"},
                 {**BASE, "name": "on", "group": "yes", "tags": ["null"], "user": "", "port": 65535}]
        for field in ("password", "privateKeys", "credential_ref", "options", "discovered_by",
                      "last_seen", "prompt_pattern", "extra"):
            cases.append({**BASE, field: "FIXTURE-NOT-A-PASSWORD"})
        for key, values in {
            "id": [None, True, "", "x\n", "../x", "x" * 129],
            "type": [None, [], "serial", "echo", "SSH"],
            "host": [None, 1, "", "h" * 253, "h" * 254],
            "port": [True, 0, 1, 65535, 65536, 22.5, "22"],
            "name": ["", "n" * 256, "n" * 257, "line\n", "\x7f"],
            "group": ["", "g" * 256, "g" * 257, "group\x80"],
            "tags": [[], ["t" * 128], ["t" * 129], ["a,b"], [""], [True], "lab"],
            "user": [None, "", "u" * 256, "u" * 257],
        }.items():
            cases.extend({**BASE, key: value} for value in values)
        cases.extend([{**BASE, "type": "telnet", "user": "fixture-account"}, {}, None, [],
                      {k: v for k, v in BASE.items() if k != "host"}])
        for definition in cases:
            with self.subTest(definition=definition):
                expected = not self.registry.validate(definition, DEFINITION)
                self.assertEqual(self.app._valid_definition(definition), expected)

    def test_destination_permission_precedes_shape_and_visibility(self):
        status, body = self.request("POST", body={**BASE, "options": {}}, user=PEER)
        self.assertEqual((status, body["error"]["code"]), (403, "forbidden"))
        status, body = self.request("POST", body={**BASE, "options": {}, "share": [PEER]}, user=PEER)
        self.assertEqual((status, body["error"]["code"]), (403, "forbidden"))
        for user in (PEER, VIEWER):
            for method, path, definition in [
                ("POST", "/targets", BASE),
                ("POST", "/targets", {**BASE, "share": [user]}),
                ("PUT", f"/targets/{TARGET}", {**BASE, "id": TARGET}),
                ("DELETE", f"/targets/{TARGET}", None),
                ("GET", f"/targets/{TARGET}/definition", None),
            ]:
                status, body = self.request(method, path, definition, user)
                self.assertEqual((status, body["error"]["code"]), (403, "forbidden"))
        self.app.invisible.add(TARGET)
        for method, path, definition in [
            ("PUT", f"/targets/{TARGET}", {**BASE, "id": TARGET}),
            ("DELETE", f"/targets/{TARGET}", None),
            ("GET", f"/targets/{TARGET}/definition", None),
        ]:
            status, body = self.request(method, path, definition, PEER)
            self.assertEqual((status, body["error"]["code"]), (403, "forbidden"))
        self.assertNotIn(BASE["id"], self.app.targets)

    def test_own_target_share_and_generated_id(self):
        definition = {"type": "ssh", "host": "fixture.invalid", "name": "private", "share": [USER]}
        status, record = self.request("POST", "/targets", definition)
        self.assertEqual(status, 201)
        tid = record["id"]
        self.assertRegex(tid, r"^g-[a-z2-7]{26}$")
        self.assertEqual(record["share"], [USER])
        self.assertEqual(record["owner"], USER)
        self.assertNotIn("scope", record)
        self.assertEqual(self.request("GET", f"/targets/{tid}", user=PEER)[0], 404)
        self.assertEqual(self.request("GET", f"/targets/{tid}/definition")[0], 200)
        self.assertEqual(self.request("POST", "/targets", {**definition, "id": "supplied"})[0], 422)
        self.assertEqual(self.request("POST", "/targets", {**definition, "share": [PEER]}, user=PEER)[0], 403)
        self.assertEqual(self.request("DELETE", f"/targets/{tid}")[0], 204)
        self.assertNotIn(tid, self.app.private_owners)

    def test_target_writes_refuse_foreign_origin_before_destination_or_shape(self):
        headers = {**self.cookies[USER], "origin": "https://foreign.invalid", "host": "localhost"}
        for method, path, body in (("POST", "/targets", {"share": [USER]}), ("PUT", "/targets/missing", {}),
                                   ("DELETE", "/targets/missing", None)):
            self.assertEqual(self.app.http(method, path, headers, body)[0], 403)

    def test_own_inventory_visibility_and_disable(self):
        definition = {"type": "ssh", "host": "fixture.invalid", "name": "own", "share": [USER, PEER]}
        status, record = self.request("POST", "/targets", definition)
        self.assertEqual(status, 201)
        tid = record["id"]
        self.assertEqual(self.request("GET", f"/targets/{tid}", user=PEER)[0], 200)
        with patch.object(self.app, "_permissions", return_value=[]):
            self.assertEqual(self.request("GET", f"/targets/{tid}", user=PEER)[0], 200)
            self.assertIn(tid, [t["id"] for t in self.request("GET", "/targets", user=PEER)[1]["targets"]])
        status, body = self.request("GET", f"/targets/{tid}", user=VIEWER)
        self.assertEqual((status, body["error"]["code"]), (404, "target_not_found"))
        status, body = self.request("GET", "/targets", user=VIEWER)
        self.assertEqual(status, 200)
        self.assertNotIn(tid, [target["id"] for target in body["targets"]])
        self.app.users[USER]["disabled"] = True
        self.assertFalse(self.app._target_visible(USER, tid))
        self.assertFalse(self.app._target_visible(PEER, tid))
        self.app.users[USER]["disabled"] = False
        self.assertTrue(self.app._target_visible(USER, tid))
        self.assertTrue(self.app._target_visible(PEER, tid))

    def test_target_writes_refuse_query_parameters(self):
        for path, method, body in [
            ("/targets?scope=private", "POST", BASE),
            (f"/targets/{TARGET}?x=1", "PUT", {**BASE, "id": TARGET}),
            (f"/targets/{TARGET}?x=1", "DELETE", None),
            (f"/targets/{TARGET}/definition?x=1", "GET", None),
        ]:
            status, resp = self.request(method, path, body)
            self.assertEqual((status, resp["error"]["code"]), (422, "invalid_request"))

    def test_own_target_put_semantics(self):
        definition = {"type": "ssh", "host": "fixture.invalid", "name": "own", "share": [USER, PEER]}
        status, record = self.request("POST", "/targets", definition)
        self.assertEqual(status, 201)
        tid = record["id"]
        put_body = {"id": tid, "type": "ssh", "host": "fixture.invalid", "name": "updated"}
        status, resp = self.request("PUT", f"/targets/{tid}", put_body)
        self.assertEqual(status, 200)
        self.assertEqual(resp["share"], sorted([USER, PEER]))
        def_status, def_body = self.request("GET", f"/targets/{tid}/definition")
        self.assertEqual(def_status, 200)
        self.assertEqual(def_body["share"], sorted([USER, PEER]))
        status, resp = self.request("PUT", f"/targets/{tid}", {**put_body, "share": None})
        self.assertEqual(status, 422)
        status, resp = self.request("PUT", f"/targets/{tid}", {**put_body, "share": [PEER]})
        self.assertEqual(status, 422)
        status, resp = self.request("PUT", f"/targets/{TARGET}", {**BASE, "id": TARGET, "share": [USER]})
        self.assertEqual(status, 422)
        status, resp = self.request("PUT", f"/targets/{tid}", {**put_body, "share": [PEER, USER]})
        self.assertEqual(status, 200)
        self.assertEqual(resp["share"], sorted([USER, PEER]))

    def test_own_target_writes_are_owner_only(self):
        definition = {"type": "ssh", "host": "fixture.invalid", "name": "own", "share": [USER, PEER]}
        status, record = self.request("POST", "/targets", definition)
        self.assertEqual(status, 201)
        tid = record["id"]
        put_body = {"id": tid, "type": "ssh", "host": "fixture.invalid", "name": "peer-edit"}
        status, resp = self.request("PUT", f"/targets/{tid}", put_body, user=PEER)
        self.assertEqual((status, resp["error"]["code"]), (403, "forbidden"))
        status, resp = self.request("DELETE", f"/targets/{tid}", user=PEER)
        self.assertEqual((status, resp["error"]["code"]), (403, "forbidden"))
        status, resp = self.request("GET", f"/targets/{tid}/definition", user=PEER)
        self.assertEqual((status, resp["error"]["code"]), (403, "forbidden"))

    def test_own_target_create_refusals(self):
        base = {"type": "ssh", "host": "fixture.invalid", "name": "own"}
        status, resp = self.request("POST", "/targets", {**base, "share": [PEER]})
        self.assertEqual((status, resp["error"]["code"]), (422, "invalid_request"))
        status, resp = self.request("POST", "/targets", {**base, "share": [USER, "role:ops"]})
        self.assertEqual((status, resp["error"]["code"]), (422, "invalid_request"))
        status, resp = self.request("POST", "/targets", {**base, "share": [USER, "unknownuser"]})
        self.assertEqual((status, resp["error"]["code"]), (422, "invalid_request"))
        self.assertNotIn("unknownuser", resp["error"]["message"])
        status, resp = self.request("POST", "/targets", {**base, "share": []})
        self.assertEqual((status, resp["error"]["code"]), (422, "invalid_request"))

    def test_manual_targets_are_immutable_and_duplicate_create_is_not_a_replace(self):
        before = copy.deepcopy(self.app.targets)
        for method in ("PUT", "DELETE"):
            status, body = self.request(method, f"/targets/{TARGET}", {**BASE, "id": TARGET})
            self.assertEqual((status, body["error"]["code"]), (403, "forbidden"))
            self.assertEqual(body["error"]["message"],
                             "This target comes from manual and is not edited through TSB.")
        status, body = self.request("POST", body={**BASE, "id": TARGET})
        self.assertEqual((status, body["error"]["code"]), (409, "target_id_taken"))
        self.assertEqual(self.app.targets, before)

    def test_whole_replace_preserves_prompt_pattern_and_removes_omitted_editable_fields(self):
        definition = {**BASE, "name": "old", "group": "lab", "tags": ["lab"],
                      "port": 2222, "user": "fixture-account"}
        self.assertEqual(self.request("POST", body=definition)[0], 201)
        tid = BASE["id"]
        self.app.targets[tid]["prompt_pattern"] = "router#"
        self.app.targets[tid]["last_seen"] = "2026-11-07T02:00:00Z"
        replacement = {**BASE, "type": "telnet"}
        status, record = self.request("PUT", f"/targets/{tid}", replacement)
        self.assertEqual(status, 200)
        # The write answer carries the read-only effective prompt (design §7, Qodo round 2 finding 2).
        self.assertEqual(record, {**replacement, "port": 23, "tags": [], "discovered_by": "tsb",
                                  "prompt_pattern": "router#", "last_seen": "2026-11-07T02:00:00Z",
                                  "effective_prompt_pattern": "router#"})
        self.assertEqual(self.request("GET", f"/targets/{tid}/definition"), (200, replacement))
        # The read answer carries the effective pattern derived from the present own pattern.
        answer = {**record, "effective_prompt_pattern": "router#"}
        self.assertEqual(self.request("GET", f"/targets/{tid}"), (200, answer))
        status, body = self.request("PUT", f"/targets/{tid}", {**replacement, "id": "another-id"})
        self.assertEqual((status, body["error"]["code"]), (422, "invalid_request"))
        self.assertEqual(self.request("GET", f"/targets/{tid}"), (200, answer))
        self.assertEqual(self.request("PUT", f"/targets/{tid}", replacement), (200, record))
        self.assertEqual(self.request("DELETE", f"/targets/{tid}"), (204, None))
        self.assertEqual(self.request("GET", f"/targets/{tid}")[0], 404)
        self.assertNotIn(tid, self.app.target_definitions)

    def test_target_reads_derive_effective_prompt_pattern_from_vendor_or_own(self):
        template = VENDOR_PROMPT_TEMPLATES["cisco-ios"]
        definition = {**BASE, "id": "vendor-only", "vendor": "cisco-ios"}
        status, record = self.request("POST", "/targets", definition)
        self.assertEqual(status, 201)
        tid = record["id"]
        got = self.request("GET", f"/targets/{tid}")[1]
        self.assertEqual(got.get("effective_prompt_pattern"), template)
        listed = [t for t in self.request("GET", "/targets")[1]["targets"] if t["id"] == tid]
        self.assertEqual(listed[0].get("effective_prompt_pattern"), template)
        # An own pattern, empty included, is verbatim and detaches from the vendor template.
        self.request("PUT", f"/targets/{tid}", {**definition, "prompt_pattern": ""})
        self.assertEqual(self.request("GET", f"/targets/{tid}")[1].get("effective_prompt_pattern"), "")
        # A vendor absent from the catalogue with no own pattern yields no effective pattern.
        status, other = self.request("POST", "/targets", {**BASE, "id": "unknown-vendor", "vendor": "not-a-template"})
        self.assertEqual(status, 201)
        self.assertNotIn("effective_prompt_pattern", self.request("GET", f"/targets/{other['id']}")[1])

    def test_write_responses_carry_the_effective_prompt_that_later_reads_give(self):
        # Qodo round 2 finding 2: `effective_prompt_pattern` is read-only on TargetWriteRecord too
        # (design §7), so POST and PUT answers match the later GET apart from login_hint, and the
        # derived field is never persisted.
        template = VENDOR_PROMPT_TEMPLATES["cisco-ios"]
        status, created = self.request("POST", "/targets", {**BASE, "id": "vendor-write", "vendor": "cisco-ios"})
        self.assertEqual(status, 201)
        tid = created["id"]
        self.assertEqual(created.get("effective_prompt_pattern"), template)
        read = self.request("GET", f"/targets/{tid}")[1]
        self.assertEqual({k: v for k, v in read.items() if k != "login_hint"}, created)
        status, replaced = self.request("PUT", f"/targets/{tid}",
                                        {**BASE, "id": tid, "vendor": "cisco-ios", "prompt_pattern": "own#"})
        self.assertEqual(status, 200)
        self.assertEqual(replaced.get("effective_prompt_pattern"), "own#")
        read = self.request("GET", f"/targets/{tid}")[1]
        self.assertEqual({k: v for k, v in read.items() if k != "login_hint"}, replaced)
        self.assertNotIn("effective_prompt_pattern", self.app.targets[tid])

    def test_me_lists_the_granted_permissions_and_leaves_login_principal_unchanged(self):
        for user in (USER, PEER, VIEWER):
            status, me = self.request("GET", "/auth/me", user=user)
            self.assertEqual(status, 200)
            self.assertEqual("target.edit" in me["permissions"], user == USER)
            self.assertEqual(self.registry.validate(me, "openapi/tsb.openapi.json#/components/schemas/Me"), [])
        status, _, login = self.app.http("POST", "/auth/login", {}, {"username": USER, "password": PASSWORD})
        self.assertEqual(status, 200)
        self.assertNotIn("permissions", login)

    def test_the_target_write_500_describes_a_landed_change_and_never_uses_the_vault_response(self):
        for method, path in [("POST", "/targets"), ("PUT", "/targets/fixture-written"),
                             ("DELETE", "/targets/fixture-written")]:
            op = _exchange.find_operation(self.registry, method, path)
            response = _exchange.response(self.registry, op, 500)
            self.assertIn("change took effect", response["description"])
            self.assertIn("audit line is missing", response["description"])
            self.assertIn("re-reads before it retries", response["description"])
            self.assertNotIn("nothing was committed", response["description"])

    def test_index_timeout_requires_an_audit_line_before_the_wait_and_audit_failure_wins(self):
        protocol = (CONTRACT / "protocols/target-write.md").read_text()
        self.assertLess(protocol.index("| 6 | CO-DK"), protocol.index("| 7 | CO-RD"))
        self.assertIn("before the index wait", protocol)
        self.assertIn("takes precedence whatever the index does", protocol)
        for method, path in [("POST", "/targets"), ("PUT", "/targets/fixture-written"),
                             ("DELETE", "/targets/fixture-written")]:
            op = _exchange.find_operation(self.registry, method, path)
            self.assertIn("taking precedence over an index timeout", _exchange.response(self.registry, op, 500)["description"])
            self.assertIn("change took effect and is audited but is not yet listed",
                          _exchange.response(self.registry, op, 503)["description"])
        fixture = json.loads((CONTRACT / "fixtures/C1/target-write-index-timeout-503.json").read_text())
        self.assertEqual(fixture["grade"], "reference")
        self.assertEqual(fixture["case"]["steps"][0]["expect"]["status"], 503)
        self.assertIn("audit line is already present", fixture["description"])
        self.assertIn("matching target.create audit line exist before the 503", fixture["graded_by"][0]["test"])

    def test_discovery_writer_configured_with_either_reserved_name_is_refused(self):
        layout = self.registry.docs["schemas/inventory-target.schema.json"]["x-layout"]
        self.assertIn("configured as manual or tsb refuses to run", layout["tsb-source"])
        fleet = (CONTRACT / "protocols/fleet-discovery.md").read_text()
        self.assertIn("refuses to run as source `manual` or `tsb`", fleet)
        self.assertIn("names itself `manual` or `tsb` refuses to run", fleet)
        edge = (CONTRACT / "protocols/edges/DW-FS.md").read_text()
        self.assertIn("never `manual` or `tsb`", edge)

    def test_each_operation_description_applies_only_to_its_own_route(self):
        paths = self.registry.docs[_exchange.OPENAPI]["paths"]
        self.assertNotIn("404", paths["/targets"]["post"]["description"])
        self.assertNotIn("shape", paths["/targets/{id}"]["delete"]["description"])
        read = paths["/targets/{id}/definition"]["get"]
        self.assertNotIn("shape", read["description"])
        self.assertNotIn("sessions", read["description"])
        self.assertNotIn("audit", read["description"])
        self.assertEqual(read["responses"]["503"], {"$ref": "#/components/responses/InventoryUnavailable"})
        protocol = (CONTRACT / "protocols/target-write.md").read_text()
        row = next(line for line in protocol.splitlines() if line.startswith("| 3 |"))
        self.assertIn("CO-FS for definition read", row)
        self.assertIn("file → TargetDefinition for reads", row)
        self.assertIn("create and replace only: 422", protocol)
        edge = (CONTRACT / "protocols/edges/SF-CO.md").read_text()
        self.assertIn("target-write.1–2 (CO-RD visibility), .3 (CO-FS read), .8", edge)

    def test_target_write_event_accepts_only_the_core_owned_source(self):
        for action in ("create", "update", "delete"):
            fixture = json.loads((CONTRACT / f"fixtures/C6/event-target-{action}.json").read_text())
            doc = copy.deepcopy(fixture["case"]["instances"][0]["doc"])
            self.assertEqual(self.registry.validate(doc, "schemas/audit-event.schema.json"), [])
            doc["detail"]["source"] = "manual"
            self.assertTrue(self.registry.validate(doc, "schemas/audit-event.schema.json"))

    def test_new_fixtures_have_exact_marks_and_reference_graders(self):
        names = {"C1/" + n for n in ("target-create", "target-create-id-taken-409",
                 "target-create-secret-field-422", "target-create-outside-filter-403",
                 "target-edit-other-source-403", "target-replace", "target-delete", "target-definition-read",
                 "target-write-forbidden-without-permission", "target-write-index-timeout-503", "me-permissions")}
        names |= {"C5/policy-target-edit", "C6/event-target-create", "C6/event-target-update", "C6/event-target-delete"}
        fixtures, _ = load_fixtures(CONTRACT)
        new = [fx for fx in fixtures if f"{fx.seam}/{fx.name}" in names]
        self.assertEqual(len(new), 15)
        graders = json.loads((CONTRACT / "fixtures/graded-by.json").read_text())
        reference = {"C1/target-create-outside-filter-403", "C1/target-write-index-timeout-503", "C5/policy-target-edit",
                     "C6/event-target-create", "C6/event-target-update", "C6/event-target-delete"}
        for fx in new:
            name = f"{fx.seam}/{fx.name}"
            self.assertEqual(fx.mark, {"published": "e3", "required-green": "e4"})
            self.assertEqual(fx.grade, "reference" if name in reference else "live")
            if name in reference:
                self.assertTrue(graders[name])
                for grader in graders[name]:
                    self.assertNotIn("owed", grader)
                    self.assertIn("file", grader)
                    self.assertIn("test_id", grader)

    def test_target_write_changes_remain_additive_against_v0_13_0(self):
        for rel in ("openapi/tsb.openapi.json", "schemas/inventory-target.schema.json",
                    "schemas/policy.schema.json", "schemas/audit-event.schema.json",
                    "schemas/error.schema.json", "schemas/fixture.schema.json"):
            previous = subprocess.check_output(["git", "show", f"v0.13.0:contract/{rel}"],
                                               cwd=CONTRACT, text=True)
            problems, rewritten = [], set()
            additive(json.loads(previous), json.loads((CONTRACT / rel).read_text()), "", rel, rewritten, problems)
            expected_problems = []
            if rel == "schemas/inventory-target.schema.json":
                # These exact changes widen the prompt and rule mixed-provenance duplicates.
                expected_problems = [
                    f"{rel}:properties/prompt_pattern/minLength: 1 became 0",
                    f"{rel}:properties/prompt_pattern/pattern: a value that is not prose changed",
                    f"{rel}:x-layout/identity-and-refusals: a value that is not prose changed",
                ]
                old_prompt = json.loads(previous)["properties"]["prompt_pattern"]
                new_prompt = json.loads((CONTRACT / rel).read_text())["properties"]["prompt_pattern"]
                self.assertEqual(new_prompt["pattern"], old_prompt["pattern"].replace("]+$", "]*$"))
            self.assertEqual(problems, expected_problems, rel)
            expected = {"components/schemas/TargetRecord/description", "paths//targets/get/description"} \
                if rel == "openapi/tsb.openapi.json" else (
                    {"properties/prompt_pattern/description"} if rel == "schemas/inventory-target.schema.json" else set()
                )
            self.assertEqual(rewritten, expected, rel)
