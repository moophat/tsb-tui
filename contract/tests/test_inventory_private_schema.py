"""Inventory additions preserve prompt presence and validate private import metadata."""

import http.client
import json
import unittest
import urllib.parse

from tsbc._serve import PASSWORD, USER, ServeApp, StubServer
from tsbc.schema import Registry

from . import CONTRACT


class InventoryPrivateSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.from_tree(CONTRACT)

    def test_prompt_presence_and_control_bounds(self):
        schema = "schemas/inventory-target.schema.json#/properties/prompt_pattern"
        for value in ("", "router#", "x" * 256):
            self.assertEqual(self.registry.validate(value, schema), [])
        for value in (None, "x" * 257, "bad\nline", "bad\x80"):
            self.assertTrue(self.registry.validate(value, schema))

    def test_import_reference_is_closed_and_bounded(self):
        schema = "schemas/inventory-target.schema.json#/properties/import_ref"
        valid = {"format": "tabby", "source_ref": "source-one"}
        self.assertEqual(self.registry.validate(valid, schema), [])
        for value in ({**valid, "extra": "x"}, {**valid, "source_ref": ""},
                      {**valid, "source_ref": "x" * 513}, {**valid, "format": "other"}):
            self.assertTrue(self.registry.validate(value, schema))

    def test_target_audit_accepts_only_shared_or_private_source(self):
        schema = "schemas/audit-event.schema.json#/$defs/target_write_event"
        event = {"ts": "2026-11-23T00:00:00Z", "user": "fixture-user", "action": "target.create",
                 "target": "fixture-target", "detail": {"source": "tsb", "fields": ["vendor"]}}
        self.assertEqual(self.registry.validate(event, schema), [])
        private = {**event, "detail": {"source": "private-" + "a" * 32, "fields": ["prompt_pattern"]}}
        self.assertEqual(self.registry.validate(private, schema), [])
        for source in ("manual", "private-ABC", "private-" + "g" * 32):
            self.assertTrue(self.registry.validate({**event, "detail": {"source": source, "fields": []}}, schema))

    def test_fixture_server_serves_proposed_catalogue(self):
        app = ServeApp()
        status, headers, _ = app.http("POST", "/auth/login", {}, {"username": USER, "password": PASSWORD})
        self.assertEqual(status, 200)
        cookie = {"cookie": headers[0][1].split(";", 1)[0]}
        fixture = json.loads((CONTRACT / "fixtures/C1/vendor-prompt-catalogue-proposals.json").read_text())
        expected = fixture["case"]["steps"][0]["expect"]["example"]
        self.assertEqual(app.http("GET", "/vendor-prompt-templates", cookie, None), (200, [], expected))
        for template in expected["templates"]:
            self.assertEqual(app.http("GET", "/vendor-prompt-templates/" + template["vendor"], cookie, None),
                             (200, [], template))
        status, _, body = app.http("GET", "/vendor-prompt-templates/fixture-missing", cookie, None)
        self.assertEqual((status, body["error"]["code"]), (404, "vendor_template_not_found"))
        self.assertEqual(app.http("GET", "/vendor-prompt-templates", {}, None)[0], 401)

    def test_import_request_envelope(self):
        schema = "openapi/tsb.openapi.json#/components/schemas/TargetImportRequest"
        base = {"action": "commit", "preview_token": "fixture_token", "format": "yaml",
                "share": ["fixture-user"], "export_bytes": "e30="}
        for value in (base, {**base, "share": ["fixture-user", "role:ops"]},
                      {**base, "credential_choice": "discard"},
                      {**base, "credential_choice": "save"},
                      {**base, "credential_choice": "save", "credential_destination": "org",
                       "unlock_class": "user_unlocked"},
                      {"action": "resume", "import_id": "imp-" + "a" * 26}):
            self.assertEqual(self.registry.validate(value, schema), [])
        for value in ({**base, "share": []},
                      {**base, "share": ["invalid:colon:extra"]},
                      {**base, "share": [":bad"]},
                      {**base, "credential_choice": "discard", "credential_destination": "org"},
                      {**base, "credential_choice": "save", "credential_destination": "org"},
                      {**base, "credential_choice": "save", "unlock_class": "user_unlocked"},
                      {"action": "resume", "import_id": "imp-" + "a" * 26, "export_bytes": "e30="}):
            self.assertTrue(self.registry.validate(value, schema))

    def test_import_receipt_and_progress_are_secret_free_and_choice_independent(self):
        schema = "schemas/import-receipt.schema.json#/$defs/ImportReceipt"
        target = {"id": "g-" + "a" * 26, "type": "ssh", "options": {"host": "fixture.invalid"},
                  "import_ref": {"format": "yaml", "source_ref": "fixture"}}
        receipt = {"version": 1, "import_id": "imp-" + "a" * 26, "user": "fixture-user",
                   "format": "yaml", "plan_digest": "a" * 64,
                   "plan": [{"locator": {"index": 1}, "source_ref": "fixture", "change": "create",
                             "target_id": target["id"], "n": 0, "definition": target,
                             "credentials": [{"reference": "import-aaaaaaaa-1", "kind": "password"}],
                             "mapping": {"reference": "import-aaaaaaaa-1", "state": "map"}}],
                   "not_carried": [], "choice": {"choice": "save", "destination": "personal"},
                   "stage": "committed", "audit": "unknown", "index": "pending",
                   "created_at": "2026-11-23T00:00:00Z", "updated_at": "2026-11-23T00:00:00Z"}
        self.assertEqual(self.registry.validate(receipt, schema), [])
        bad = dict(receipt, export_bytes="secret")
        self.assertTrue(self.registry.validate(bad, schema))
        bad = dict(receipt, plan=[{**receipt["plan"][0], "credentials": [
            {"reference": "import-aaaaaaaa-1", "kind": "password", "layer": "personal"}]}])
        self.assertTrue(self.registry.validate(bad, schema))
        bad = dict(receipt, plan=[{**receipt["plan"][0], "credentials": [
            {"reference": "import-aaaaaaaa-1", "kind": "key"}]}])
        self.assertTrue(self.registry.validate(bad, schema))

    def test_preview_never_exposes_private_owner(self):
        schema = "openapi/tsb.openapi.json#/components/schemas/TargetImportPreviewEntry"
        target = {"id": "g-" + "a" * 26, "type": "ssh", "tags": [], "discovered_by": "tsb",
                  "host": "fixture.invalid", "port": 22}
        definition = {"id": target["id"], "type": "ssh", "options": {"host": "fixture.invalid"}}
        entry = {"entry": {"index": 1}, "source_ref": "fixture", "target": target,
                 "change": "create", "n": 0, "has_credentials": False, "key_file_referenced": False,
                 "definition": definition, "mapping": None}
        self.assertEqual(self.registry.validate(entry, schema), [])
        self.assertTrue(self.registry.validate({**entry, "definition": {**definition, "private_owner": "fixture-user"}}, schema))
        # An own-import preview row definition with share and without private_owner validates
        own_def = {**definition, "share": ["fixture-user"]}
        self.assertEqual(self.registry.validate({**entry, "definition": own_def}, schema), [])

    def test_share_entry_grammar_and_record_rules(self):
        common = "schemas/common.schema.json#/$defs/share_entry"
        for valid in ("user", "fixture-user", "user.name", "role:ops", "kind:entry_name-1"):
            self.assertEqual(self.registry.validate(valid, common), [])
        for invalid in ("", ":", ":ops", "Role:ops", "role:", "role:ops:extra", "123:abc"):
            self.assertTrue(self.registry.validate(invalid, common))

        rec_schema = "openapi/tsb.openapi.json#/components/schemas/TargetRecord"
        write_schema = "openapi/tsb.openapi.json#/components/schemas/TargetWriteRecord"
        base_rec = {"id": "target-1", "type": "ssh", "host": "fixture.invalid", "port": 22, "tags": [], "discovered_by": "tsb"}
        # Both without share and owner -> valid on both
        self.assertEqual(self.registry.validate(base_rec, rec_schema), [])
        self.assertEqual(self.registry.validate(base_rec, write_schema), [])
        # Both with share and owner -> valid on both
        own_rec = {**base_rec, "share": ["fixture-user"], "owner": "fixture-user"}
        self.assertEqual(self.registry.validate(own_rec, rec_schema), [])
        self.assertEqual(self.registry.validate(own_rec, write_schema), [])
        # Owner alone (SL-5 c) -> valid on TargetRecord, invalid on TargetWriteRecord
        owner_only = {**base_rec, "owner": "fixture-user"}
        self.assertEqual(self.registry.validate(owner_only, rec_schema), [])
        self.assertTrue(self.registry.validate(owner_only, write_schema))
        # Share alone without owner -> invalid on both
        share_only = {**base_rec, "share": ["fixture-user"]}
        self.assertTrue(self.registry.validate(share_only, rec_schema))
        self.assertTrue(self.registry.validate(share_only, write_schema))

    def test_import_error_progress_is_scoped_to_permitted_codes(self):
        schema = "schemas/error.schema.json"
        progress = {"import_id": "imp-" + "a" * 26, "credentials_stored": [], "targets_written": [],
                    "mapping": "pending", "audit": "unknown", "index": "pending", "effects_known": True}
        for code in ("internal_error", "inventory_unavailable", "import_conflict"):
            self.assertEqual(self.registry.validate({"error": {"code": code, "message": "error",
                                                                 "import_progress": progress}}, schema), [])
        self.assertTrue(self.registry.validate({"error": {"code": "forbidden", "message": "error",
                                                           "import_progress": progress}}, schema))
        self.assertTrue(self.registry.validate({"error": {"code": "import_conflict", "message": "error"}}, schema))

    def test_fixture_server_does_not_retain_import_request_body(self):
        app = ServeApp()
        _, headers, _ = app.http("POST", "/auth/login", {}, {"username": USER, "password": PASSWORD})
        cookie = {"cookie": headers[0][1].split(";", 1)[0]}
        app.http("POST", "/target-imports", cookie, {"action": "preview", "export_bytes": "FIXTURE-NOT-A-PASSWORD"})
        self.assertEqual(app.requests[-1], ("POST", "/target-imports", None))

    def test_import_rejects_unauthenticated_and_foreign_origin_before_reading_body(self):
        app = ServeApp()
        _, headers, _ = app.http("POST", "/auth/login", {}, {"username": USER, "password": PASSWORD})
        cookie = headers[0][1].split(";", 1)[0]
        with StubServer(app) as url:
            addr = urllib.parse.urlsplit(url)
            for supplied, expected in (({}, 401), ({"Cookie": cookie, "Origin": "https://foreign.invalid"}, 403)):
                conn = http.client.HTTPConnection(addr.hostname, addr.port, timeout=2)
                conn.putrequest("POST", "/target-imports")
                conn.putheader("Content-Length", "100")
                for key, value in supplied.items():
                    conn.putheader(key, value)
                conn.endheaders()
                response = conn.getresponse()
                self.assertEqual(response.status, expected)
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
                response.read()
                conn.close()

    def test_resume_without_retained_receipt_is_stale_and_not_cached(self):
        app = ServeApp()
        _, headers, _ = app.http("POST", "/auth/login", {}, {"username": USER, "password": PASSWORD})
        cookie = {"cookie": headers[0][1].split(";", 1)[0]}
        status, headers, body = app.http("POST", "/target-imports", cookie,
                                         {"action": "resume", "import_id": "imp-" + "a" * 26})
        self.assertEqual((status, body["error"]["code"]), (409, "import_preview_stale"))
        self.assertIn(("Cache-Control", "no-store"), headers)
        self.assertEqual(app.requests[-1], ("POST", "/target-imports", None))

    def test_import_config_bounds(self):
        for key, good, bad in (("max_bytes", 67108864, 67108865), ("max_yaml_depth", 256, 257)):
            schema = f"schemas/config.schema.json#/properties/import/properties/{key}"
            self.assertEqual(self.registry.validate(good, schema), [])
            self.assertTrue(self.registry.validate(bad, schema))
            self.assertTrue(self.registry.validate(0, schema))
        for key in ("preview_ttl", "receipt_retention"):
            schema = f"schemas/config.schema.json#/properties/import/properties/{key}"
            self.assertEqual(self.registry.validate("5m", schema), [])
            self.assertTrue(self.registry.validate("0", schema))


if __name__ == "__main__":
    unittest.main()
