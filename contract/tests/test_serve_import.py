"""The fixture server's import is request-bound and stores no uploaded bytes."""

import base64
import hashlib
import hmac
import json
import secrets
import unittest
from unittest.mock import patch

from tsbc._serve import PASSWORD, PEER, USER, ServeApp
from tsbc.schema import Registry

from . import CONTRACT


class ServeImport(unittest.TestCase):
    def setUp(self):
        self.app = ServeApp()
        _, headers, _ = self.app.http("POST", "/auth/login", {}, {"username": USER, "password": PASSWORD})
        self.headers = {"cookie": headers[0][1].split(";", 1)[0]}
        self.export = base64.b64encode(b'id: foreign\ntype: ssh\nname: router\noptions:\n  host: fixture.invalid\n').decode()
        self.preview = {"action": "preview", "format": "yaml", "export_bytes": self.export}

    def request(self, body):
        return self.app.http("POST", "/target-imports", self.headers, body)

    def test_preview_token_mac_domain_and_framing(self):
        token = self.request(self.preview)[2]["preview_token"]
        descriptor, signature = token[:-43], token[-43:]
        label = b"tsb-import-token"
        expected = hmac.new(self.app.id_key, len(label).to_bytes(4, "big") + label + descriptor.encode(), hashlib.sha256).digest()
        self.assertEqual(signature, base64.urlsafe_b64encode(expected).rstrip(b"=").decode())
        old_mac = hmac.new(self.app.id_key, descriptor.encode(), hashlib.sha256).digest()
        old_token = descriptor + base64.urlsafe_b64encode(old_mac).rstrip(b"=").decode()
        status, _, error = self.request({"action": "abort", "preview_token": old_token})
        self.assertEqual((status, error["error"]["code"]), (409, "import_preview_stale"))

    def test_preview_commit_replay_and_resume(self):
        status, headers, preview = self.request(self.preview)
        self.assertEqual(status, 200)
        self.assertIn(("Cache-Control", "no-store"), headers)
        registry = Registry.from_tree(CONTRACT)
        self.assertEqual(registry.validate(preview, "openapi/tsb.openapi.json#/components/schemas/TargetImportPreview"), [])
        self.assertNotIn(preview["targets"][0]["target"]["id"], self.app.targets)
        commit = {**self.preview, "action": "commit", "preview_token": preview["preview_token"]}
        status, _, written = self.request(commit)
        self.assertEqual(status, 200)
        self.assertEqual(registry.validate(written, "openapi/tsb.openapi.json#/components/schemas/TargetImportResult"), [])
        self.assertEqual(written["targets"][0]["id"], preview["targets"][0]["target"]["id"])
        self.assertEqual(self.request({"action": "resume", "import_id": preview["import_id"]})[2], written)
        self.assertEqual(self.request(commit)[2], written)
        status, _, conflict = self.request({"action": "abort", "preview_token": preview["preview_token"]})
        self.assertEqual((status, conflict["error"]["code"]), (409, "import_conflict"))
        self.assertEqual(registry.validate(conflict, "schemas/error.schema.json"), [])
        self.assertNotIn(self.export, json.dumps(list(self.app.import_receipts.values())))

    def test_abort_spends_token_and_changed_bytes_are_stale(self):
        preview = self.request(self.preview)[2]
        abort = {"action": "abort", "preview_token": preview["preview_token"]}
        self.assertEqual(self.request(abort)[0], 200)
        self.assertEqual(self.request(abort)[2]["error"]["code"], "import_preview_stale")
        same = {**self.preview, "action": "commit", "preview_token": preview["preview_token"]}
        self.assertEqual(self.request(same)[2]["error"]["code"], "import_preview_stale")
        preview = self.request(self.preview)[2]
        changed = base64.b64encode(base64.b64decode(self.export) + b"\n").decode()
        commit = {**self.preview, "action": "commit", "preview_token": preview["preview_token"], "export_bytes": changed}
        status, _, response = self.request(commit)
        self.assertEqual((status, response["error"]["code"]), (409, "import_preview_stale"))
        self.assertEqual(self.app.import_receipts, {})

    def test_shared_filter_refuses_entire_create_or_hidden_match(self):
        self.app.target_edit_filters[USER] = ["tag:lab"]
        status, _, error = self.request(self.preview)
        self.assertEqual((status, error["error"]["code"]), (403, "forbidden"))
        create_error = error
        self.app.target_edit_filters[USER] = ["*"]
        preview = self.request(self.preview)[2]
        self.assertEqual(self.request({**self.preview, "action": "commit", "preview_token": preview["preview_token"]})[0], 200)
        self.app.target_edit_filters[USER] = ["tag:lab"]
        lab = base64.b64encode(base64.b64decode(self.export) + b'tags: [lab]\n').decode()
        status, _, error = self.request({**self.preview, "export_bytes": lab})
        self.assertEqual(status, 403)
        self.assertEqual(error, create_error)

    def test_shared_filters_check_matched_target_and_recheck_commit(self):
        sample = base64.b64encode(base64.b64decode(self.export) + b'tags: [lab]\n').decode()
        request = {**self.preview, "export_bytes": sample}
        self.app.target_edit_filters[USER] = ["tag:lab"]
        preview = self.request(request)[2]
        self.app.target_edit_filters[USER] = ["tag:other"]
        status, _, error = self.request({**request, "action": "commit", "preview_token": preview["preview_token"]})
        self.assertEqual((status, error["error"]["code"]), (403, "forbidden"))
        self.assertEqual(self.app.import_receipts, {})
        self.app.target_edit_filters[USER] = ["tag:lab"]
        self.assertEqual(self.request({**request, "action": "commit", "preview_token": preview["preview_token"]})[0], 200)
        self.app.target_edit_filters[USER] = ["tag:other"]
        self.assertEqual(self.request({**request, "action": "commit", "preview_token": preview["preview_token"]})[0], 403)
        self.app.target_edit_filters[USER] = ["tag:lab"]
        tid = preview["targets"][0]["target"]["id"]
        self.app.targets[tid]["tags"] = ["other"]
        status, _, error = self.request(request)
        self.assertEqual((status, error["error"]["code"]), (403, "forbidden"))

    def test_no_secret_save_choice_replays_as_none(self):
        preview = self.request(self.preview)[2]
        commit = {**self.preview, "action": "commit", "preview_token": preview["preview_token"], "credential_choice": "save"}
        status, _, result = self.request(commit)
        self.assertEqual(status, 200)
        self.assertEqual(result["credentials"]["choice"], "none")
        self.assertEqual(self.request(commit)[2], result)

    def test_interrupted_publication_resumes_from_secret_free_receipt(self):
        preview = self.request(self.preview)[2]
        commit = {**self.preview, "action": "commit", "preview_token": preview["preview_token"]}
        with patch.object(self.app, "_import_publish", side_effect=OSError("synthetic failure")):
            status, _, error = self.request(commit)
        self.assertEqual((status, error["error"]["code"]), (500, "internal_error"))
        receipt = self.app.import_receipts[(USER, preview["import_id"])]
        self.assertEqual(receipt["stage"], "committed")
        self.assertEqual(Registry.from_tree(CONTRACT).validate(receipt, "schemas/import-receipt.schema.json"), [])
        self.assertNotIn(self.export, json.dumps(receipt))
        self.app.id_key = secrets.token_bytes(32)
        status, _, result = self.request({"action": "resume", "import_id": preview["import_id"]})
        self.assertEqual(status, 200)
        self.assertEqual(result["targets"][0]["id"], preview["targets"][0]["target"]["id"])
        self.assertEqual(receipt["stage"], "complete")
        self.assertEqual(self.request({"action": "resume", "import_id": preview["import_id"]})[2], result)
        self.assertEqual(self.request(commit)[2]["error"]["code"], "import_preview_stale")

    def test_foreign_formats_map_basic_credential_free_rows(self):
        samples = {
            "tabby": (b'version: 1\nprofiles:\n  - id: native\n    type: ssh\n    name: router\n    options:\n      host: fixture.invalid\n', "native"),
            "securecrt": (b'<VanDyke version="3.0"><key name="Sessions"><key name="lab"><key name="router"><dword name="Is Session">1</dword><string name="Protocol Name">SSH2</string><string name="Hostname">fixture.invalid</string></key></key></key></VanDyke>', "lab/router"),
            "mobaxterm": (b'[Bookmarks]\nSubRep=lab\nImgNum=0\nrouter=#109#0%fixture.invalid%22%fixture-user\n', "lab/router"),
        }
        for format_name, (sample, source) in samples.items():
            with self.subTest(format=format_name):
                body = {"action": "preview", "format": format_name,
                        "export_bytes": base64.b64encode(sample).decode()}
                status, _, preview = self.request(body)
                self.assertEqual(status, 200)
                self.assertEqual(preview["targets"][0]["source_ref"], source)
                self.assertEqual(preview["targets"][0]["definition"]["options"]["host"], "fixture.invalid")

    def test_clear_tabby_password_can_be_discarded_without_storage(self):
        sample = (b'version: 1\nprofiles:\n  - id: first\n    type: ssh\n    name: router\n'
                  b'    options:\n      host: fixture.invalid\n      user: fixture-user\n'
                  b'      password: FIXTURE-NOT-A-PASSWORD\n')
        request = {"action": "preview", "format": "tabby",
                   "export_bytes": base64.b64encode(sample).decode()}
        status, _, preview = self.request(request)
        self.assertEqual(status, 200)
        self.assertTrue(preview["targets"][0]["has_credentials"])
        self.assertEqual(preview["credentials"][0]["kind"], "password")
        self.assertNotIn("FIXTURE-NOT-A-PASSWORD", json.dumps(preview))
        commit = {**request, "action": "commit", "preview_token": preview["preview_token"],
                  "credential_choice": "discard"}
        status, _, result = self.request(commit)
        self.assertEqual(status, 200)
        self.assertEqual(result["credentials"], {"choice": "discard", "stored": [], "mappings": []})
        self.assertEqual(self.app.creds, {})
        self.assertNotIn("FIXTURE-NOT-A-PASSWORD", json.dumps(list(self.app.import_receipts.values())))
        self.assertEqual(self.request(commit)[2], result)

    def test_duplicate_native_references_are_skipped_and_not_first_wins(self):
        sample = (b'- id: repeated\n  type: ssh\n  options:\n    host: first.invalid\n'
                  b'- id: repeated\n  type: ssh\n  options:\n    host: second.invalid\n')
        request = {**self.preview, "export_bytes": base64.b64encode(sample).decode()}
        status, _, preview = self.request(request)
        self.assertEqual(status, 200)
        self.assertEqual(preview["targets"], [])
        self.assertEqual([issue["reason"] for issue in preview["not_carried"]],
                         ["ambiguous_source_ref", "ambiguous_source_ref"])

    def test_personal_save_refusals_replay_and_resume(self):
        sample = (b'version: 1\nprofiles:\n  - id: first\n    type: ssh\n    name: router\n'
                  b'    options:\n      host: fixture.invalid\n      user: fixture-user\n'
                  b'      password: FIXTURE-NOT-A-PASSWORD\n')
        request = {"action": "preview", "format": "tabby",
                   "export_bytes": base64.b64encode(sample).decode()}
        preview = self.request(request)[2]
        commit = {**request, "action": "commit", "preview_token": preview["preview_token"]}
        self.assertEqual(self.request(commit)[0], 422)
        status, _, error = self.request({**commit, "credential_choice": "save"})
        self.assertEqual((status, error["error"]["code"]), (409, "vault_locked"))
        self.assertEqual(self.app.creds, {})
        cookie = self.app._cookie(self.headers)
        self.app.unlocked.add(cookie)
        save = {**commit, "credential_choice": "save"}
        status, _, result = self.request(save)
        self.assertEqual(status, 200)
        self.assertEqual(Registry.from_tree(CONTRACT).validate(result, "openapi/tsb.openapi.json#/components/schemas/TargetImportResult"), [])
        stored = result["credentials"]["stored"][0]
        reference, target = stored["reference"], stored["target_ids"][0]
        self.assertEqual(stored["layer"], "personal")
        self.assertEqual(self.app.creds[USER][reference]["secret"], "FIXTURE-NOT-A-PASSWORD")
        self.assertEqual(self.app.profiles[USER][target], reference)
        self.assertEqual(result["credentials"]["mappings"], [{"target_id": target, "reference": reference, "state": "mapped"}])
        self.assertNotIn("FIXTURE-NOT-A-PASSWORD", json.dumps(result))
        self.assertNotIn("FIXTURE-NOT-A-PASSWORD", json.dumps(self.app.import_receipts[(USER, preview["import_id"])]))
        self.assertEqual(self.request(save)[2], result)
        self.assertEqual(self.request({**save, "credential_choice": "discard"})[2]["error"]["code"], "import_conflict")
        self.app.unlocked.remove(cookie)
        self.assertEqual(self.request({"action": "resume", "import_id": preview["import_id"]})[2], result)
        self.assertEqual(len(self.app.creds[USER]), 1)

    def test_org_save_and_occupied_reference_refusal(self):
        sample = (b'version: 1\nprofiles:\n  - id: native\n    type: ssh\n    options:\n'
                  b'      host: fixture.invalid\n      user: fixture-user\n      password: FIXTURE-NOT-A-PASSWORD\n')
        request = {"action": "preview", "format": "tabby", "export_bytes": base64.b64encode(sample).decode()}
        preview = self.request(request)[2]
        commit = {**request, "action": "commit", "preview_token": preview["preview_token"], "credential_choice": "save",
                  "credential_destination": "org", "unlock_class": "user_unlocked"}
        self.app.unlocked.add(self.app._cookie(self.headers))
        with patch.object(self.app, "_permissions", return_value=["target.edit"]):
            self.assertEqual(self.request(commit)[0], 403)
        reference = preview["credentials"][0]["reference"]
        self.app.org_creds[reference] = {"reference": reference}
        status, _, error = self.request(commit)
        self.assertEqual((status, error["error"]["code"]), (409, "credential_exists"))
        self.assertEqual(self.app.import_receipts, {})
        del self.app.org_creds[reference]
        status, _, result = self.request(commit)
        self.assertEqual(status, 200)
        self.assertEqual(result["credentials"]["stored"][0]["layer"], "org")
        self.assertEqual(result["credentials"]["mappings"], [])
        self.assertEqual(self.app.org_creds[reference]["unlock_class"], "user_unlocked")
        self.assertEqual(self.request(commit)[2], result)
        self.assertEqual(self.app.profiles, {})
        self.app.unlocked.clear()
        refusal = self.app.vault_lock_refusal(USER, self.app._cookie(self.headers), (reference, "org"))
        self.assertEqual((refusal[0], refusal[2]["error"]["code"]), (409, "org_credential_locked"))
        self.assertEqual(self.request({"action": "resume", "import_id": preview["import_id"]})[2], result)
        with patch.object(self.app, "_permissions", return_value=["target.edit"]):
            self.assertEqual(self.request({"action": "resume", "import_id": preview["import_id"]})[0], 403)

    def test_personal_reimport_keeps_existing_mapping(self):
        sample = (b'version: 1\nprofiles:\n  - id: one\n    type: ssh\n    options:\n'
                  b'      host: one.invalid\n      user: fixture-user\n      password: FIXTURE-NOT-A-PASSWORD\n')
        request = {"action": "preview", "format": "tabby", "export_bytes": base64.b64encode(sample).decode()}
        self.app.unlocked.add(self.app._cookie(self.headers))
        first = self.request(request)[2]
        result = self.request({**request, "action": "commit", "preview_token": first["preview_token"], "credential_choice": "save"})[2]
        target, old_ref = result["targets"][0]["id"], result["credentials"]["stored"][0]["reference"]
        second = self.request(request)[2]
        result = self.request({**request, "action": "commit", "preview_token": second["preview_token"], "credential_choice": "save"})[2]
        new_ref = result["credentials"]["stored"][0]["reference"]
        self.assertNotEqual(old_ref, new_ref)
        self.assertEqual(self.app.profiles[USER][target], old_ref)
        self.assertEqual(result["credentials"]["mappings"], [{"target_id": target, "reference": new_ref, "state": "kept"}])

    def test_save_publication_failure_resumes_without_second_store(self):
        sample = (b'version: 1\nprofiles:\n  - id: one\n    type: ssh\n    options:\n'
                  b'      host: one.invalid\n      user: fixture-user\n      password: FIXTURE-NOT-A-PASSWORD\n'
                  b'  - id: two\n    type: ssh\n    options:\n'
                  b'      host: two.invalid\n      user: fixture-user\n      password: FIXTURE-NOT-A-PASSWORD\n')
        request = {"action": "preview", "format": "tabby", "export_bytes": base64.b64encode(sample).decode()}
        preview = self.request(request)[2]
        self.assertEqual(len(preview["credentials"]), 1)
        commit = {**request, "action": "commit", "preview_token": preview["preview_token"], "credential_choice": "save"}
        cookie = self.app._cookie(self.headers)
        self.app.unlocked.add(cookie)
        with patch.object(self.app, "_import_publish", side_effect=OSError("synthetic failure")):
            status, _, error = self.request(commit)
        self.assertEqual(status, 500)
        self.assertEqual(len(error["error"]["import_progress"]["credentials_stored"]), 1)
        self.assertEqual(len(self.app.creds[USER]), 1)
        self.app.unlocked.remove(cookie)
        status, _, result = self.request({"action": "resume", "import_id": preview["import_id"]})
        self.assertEqual(status, 200)
        self.assertEqual(len(result["credentials"]["stored"]), 1)
        self.assertEqual(len(result["credentials"]["mappings"]), 2)
        self.assertEqual(len(self.app.creds[USER]), 1)

    def test_yaml_text_fields_preserve_boolean_and_null_looking_scalars(self):
        sample = b'id: true\ntype: ssh\nname: null\nprompt_pattern: false\noptions:\n  host: on\n'
        request = {**self.preview, "export_bytes": base64.b64encode(sample).decode()}
        status, _, preview = self.request(request)
        self.assertEqual(status, 200)
        row = preview["targets"][0]
        self.assertEqual(row["source_ref"], "true")
        self.assertEqual(row["definition"]["name"], "null")
        self.assertEqual(row["definition"]["prompt_pattern"], "false")
        self.assertEqual(row["definition"]["options"]["host"], "on")

    def test_reimport_preserves_identity_tags_vendor_and_absent_prompt(self):
        sample = (b'id: native\ntype: ssh\nname: old\ntags: [lab]\nvendor: cisco\n'
                  b'prompt_pattern: ""\noptions:\n  host: fixture.invalid\n')
        request = {**self.preview, "export_bytes": base64.b64encode(sample).decode()}
        first = self.request(request)[2]
        self.request({**request, "action": "commit", "preview_token": first["preview_token"]})
        changed = (b'id: native\ntype: ssh\nname: new\ntags: [other]\nvendor: juniper\n'
                   b'options:\n  host: fixture.invalid\n')
        request["export_bytes"] = base64.b64encode(changed).decode()
        second = self.request(request)[2]
        row = second["targets"][0]
        self.assertEqual(row["change"], "update")
        self.assertEqual(row["target"]["id"], first["targets"][0]["target"]["id"])
        self.assertEqual(row["definition"]["tags"], ["lab"])
        self.assertEqual(row["definition"]["vendor"], "cisco")
        self.assertEqual(row["definition"]["prompt_pattern"], "")

    def test_yaml_quoted_scalars_and_flow_sequences_handle_comments_and_quoted_commas(self):
        # A double-quoted name with a trailing comment parses to the name; a quoted comma is one
        # item (so it fails the tag schema as a single value), not two tags. The valid row survives.
        sample = (b'- id: good\n  type: ssh\n  name: "router" # exported\n  tags: ["a", "b"]\n'
                  b'  options:\n    host: fixture.invalid\n'
                  b'- id: comma\n  type: ssh\n  name: other\n  tags: ["lab,prod"]\n'
                  b'  options:\n    host: other.invalid\n')
        request = {**self.preview, "export_bytes": base64.b64encode(sample).decode()}
        status, _, preview = self.request(request)
        self.assertEqual(status, 200)
        good = next(row for row in preview["targets"] if row["source_ref"] == "good")
        self.assertEqual(good["definition"]["name"], "router")
        self.assertEqual(good["definition"]["tags"], ["a", "b"])
        self.assertNotIn("comma", [row["source_ref"] for row in preview["targets"]])
        self.assertIn("invalid_entry", [issue["reason"] for issue in preview["not_carried"]])

    def test_yaml_options_outside_the_inventory_bounds_are_invalid_rows(self):
        # Qodo round 2 finding 1: every carried field obeys its existing schema bound (protocol
        # §Limits), so a YAML entry with an out-of-range timer or size is skipped and reported,
        # never published. The valid row survives.
        sample = (b'- id: bad-linger\n  type: ssh\n  options:\n    host: a.invalid\n    connection_linger: -1\n'
                  b'- id: short-login\n  type: ssh\n  options:\n    host: b.invalid\n    login_timeout: 1\n'
                  b'- id: half-ceiling\n  type: ssh\n  options:\n    host: c.invalid\n    size_ceiling:\n'
                  b'      cols: 0\n      rows: 5\n'
                  b'- id: good\n  type: ssh\n  options:\n    host: d.invalid\n    connection_linger: 30\n'
                  b'    login_timeout: 10\n    size_floor:\n      cols: 80\n      rows: 24\n')
        request = {**self.preview, "export_bytes": base64.b64encode(sample).decode()}
        status, _, preview = self.request(request)
        self.assertEqual(status, 200)
        self.assertEqual([row["source_ref"] for row in preview["targets"]], ["good"])
        self.assertEqual(preview["targets"][0]["definition"]["options"]["connection_linger"], 30)
        bad = [issue for issue in preview["not_carried"] if issue["kind"] == "row"]
        self.assertEqual([issue["locator"]["index"] for issue in bad], [1, 2, 3])
        self.assertEqual([issue["reason"] for issue in bad], ["invalid_entry", "invalid_entry", "invalid_entry"])

    def test_securecrt_conflicting_port_aliases_are_reported_not_resolved(self):
        # Qodo round 2 finding 3: alias properties that coexist inconsistently have no winner; the
        # row is reported as invalid, never silently resolved by document order (SecureCRT mapping
        # table). The other session keeps its preview.
        securecrt = ('<VanDyke version="3.0"><key name="Sessions">'
                     '<key name="conflict"><dword name="Is Session">1</dword>'
                     '<string name="Protocol Name">SSH2</string><string name="Hostname">a.invalid</string>'
                     '<dword name="[SSH2] Port">22</dword><dword name="Port">2222</dword></key>'
                     '<key name="agreed"><dword name="Is Session">1</dword>'
                     '<string name="Protocol Name">SSH2</string><string name="Hostname">b.invalid</string>'
                     '<dword name="[SSH2] Port">44</dword><dword name="Port">44</dword></key>'
                     '</key></VanDyke>')
        status, _, preview = self.request({"action": "preview", "format": "securecrt",
                                           "export_bytes": base64.b64encode(securecrt.encode()).decode()})
        self.assertEqual(status, 200)
        self.assertEqual([row["source_ref"] for row in preview["targets"]], ["agreed"])
        self.assertEqual(preview["targets"][0]["definition"]["options"]["port"], 44)
        bad = [issue for issue in preview["not_carried"] if issue["kind"] == "row"]
        self.assertEqual([(issue["locator"]["index"], issue["reason"]) for issue in bad], [(1, "invalid_entry")])

    def test_securecrt_nonnumeric_port_skips_only_its_own_row(self):
        # Qodo round 2 finding 4: a malformed port is an invalid row, skipped and reported; the rest
        # of the export keeps its preview (design §3: invalid rows are skips, malformed whole
        # documents are 422).
        securecrt = ('<VanDyke version="3.0"><key name="Sessions">'
                     '<key name="broken"><dword name="Is Session">1</dword>'
                     '<string name="Protocol Name">SSH2</string><string name="Hostname">a.invalid</string>'
                     '<string name="Port">abc</string></key>'
                     '<key name="ok"><dword name="Is Session">1</dword>'
                     '<string name="Protocol Name">SSH2</string><string name="Hostname">b.invalid</string>'
                     '<dword name="[SSH2] Port">33</dword></key>'
                     '</key></VanDyke>')
        status, _, preview = self.request({"action": "preview", "format": "securecrt",
                                           "export_bytes": base64.b64encode(securecrt.encode()).decode()})
        self.assertEqual(status, 200)
        self.assertEqual([row["source_ref"] for row in preview["targets"]], ["ok"])
        self.assertEqual(preview["targets"][0]["definition"]["options"]["port"], 33)
        bad = [issue for issue in preview["not_carried"] if issue["kind"] == "row"]
        self.assertEqual([(issue["locator"]["index"], issue["reason"]) for issue in bad], [(1, "invalid_entry")])
        # The same per-row rule holds the MobaXterm reader: one malformed bookmark port is its own
        # invalid row, and the other bookmarks keep their preview.
        moba = "[Bookmarks]\nSubRep=lab\nImgNum=0\nbroken=#109#0%a.invalid%xx%u\nok=#109#0%b.invalid%22%u\n"
        status, _, preview = self.request({"action": "preview", "format": "mobaxterm",
                                           "export_bytes": base64.b64encode(moba.encode()).decode()})
        self.assertEqual(status, 200)
        self.assertEqual([row["source_ref"] for row in preview["targets"]], ["lab/ok"])
        self.assertEqual(preview["targets"][0]["definition"]["options"]["port"], 22)
        bad = [issue for issue in preview["not_carried"] if issue["kind"] == "row"]
        self.assertEqual([(issue["locator"]["index"], issue["reason"]) for issue in bad], [(1, "invalid_entry")])

    def test_duplicate_reference_detection_is_linear_in_entries(self):
        # refs.count(ref) per row scans the whole list, so duplicate detection is quadratic in the
        # entries. A frequency map built once makes each lookup constant, so the number of reference
        # comparisons grows linearly, not as the square of the rows.
        class CountingRef(str):
            calls = 0
            __hash__ = str.__hash__
            def __eq__(self, other):
                CountingRef.calls += 1
                return str.__eq__(self, other)
        n = 2000
        rows = [{"id": CountingRef(f"ref{i}"), "type": "ssh", "name": f"name{i}",
                 "options": {"host": "fixture.invalid"}} for i in range(n)]
        targets, issues, _, _ = self.app._import_plan(USER, "shared", "imp-aaaaaaaaaaaaaaaaaaaaaaa", rows, "yaml")
        self.assertEqual(len(targets), n)
        self.assertEqual(issues, [])
        self.assertLess(CountingRef.calls, 10 * n)

    def test_utf16_securecrt_export_is_refused_before_parsing(self):
        # The ASCII DOCTYPE/ENTITY byte search misses a UTF-16 file, so expat decodes it and runs its
        # internal DTD. Only a UTF-8 export is supported, so this one is refused before any parse.
        xml = ('<?xml version="1.0"?>\n<!DOCTYPE VanDyke [ <!ENTITY x "y" > ]>\n'
               '<VanDyke version="3.0"><key name="Sessions"><key name="router">'
               '<dword name="Is Session">1</dword><string name="Protocol Name">SSH2</string>'
               '<string name="Hostname">fixture.invalid</string></key></key></VanDyke>')
        body = {"action": "preview", "format": "securecrt",
                "export_bytes": base64.b64encode(xml.encode("utf-16")).decode()}
        status, _, error = self.request(body)
        self.assertEqual((status, error["error"]["code"]), (422, "import_format_unsupported"))

    def test_credential_bearing_securecrt_and_mobaxterm_exports_keep_their_targets(self):
        securecrt = ('<VanDyke version="3.0"><key name="Sessions">'
                     '<key name="clean"><dword name="Is Session">1</dword>'
                     '<string name="Protocol Name">SSH2</string><string name="Hostname">a.invalid</string></key>'
                     '<key name="secret"><dword name="Is Session">1</dword>'
                     '<string name="Protocol Name">SSH2</string><string name="Hostname">b.invalid</string>'
                     '<string name="Password">u00ff</string></key></key></VanDyke>')
        status, _, preview = self.request({"action": "preview", "format": "securecrt",
                                           "export_bytes": base64.b64encode(securecrt.encode()).decode()})
        self.assertEqual(status, 200)
        refs = sorted(row["source_ref"] for row in preview["targets"])
        self.assertEqual(refs, ["clean", "secret"])
        self.assertIn("unreadable_credential", [issue["reason"] for issue in preview["not_carried"]])
        moba = "[Bookmarks]\nSubRep=lab\nImgNum=0\nrouter=#109#0%fixture.invalid%22%fixture-user\n[Passwords]\n0=FIXTURE-NOT-A-PASSWORD\n"
        status, _, preview = self.request({"action": "preview", "format": "mobaxterm",
                                           "export_bytes": base64.b64encode(moba.encode()).decode()})
        self.assertEqual(status, 200)
        self.assertEqual([row["source_ref"] for row in preview["targets"]], ["lab/router"])
        self.assertIn("unreadable_credential", [issue["reason"] for issue in preview["not_carried"]])

    def test_reimport_after_a_put_edit_preserves_the_current_target_state(self):
        sample = (b'id: native\ntype: ssh\nname: router\ntags: [lab]\nvendor: cisco\n'
                  b'options:\n  host: fixture.invalid\n')
        request = {**self.preview, "export_bytes": base64.b64encode(sample).decode()}
        first = self.request(request)[2]
        self.request({**request, "action": "commit", "preview_token": first["preview_token"]})
        tid = first["targets"][0]["target"]["id"]
        status, _, _ = self.app.http("PUT", f"/targets/{tid}", self.headers,
                                     {"id": tid, "type": "ssh", "host": "fixture.invalid",
                                      "tags": ["other"], "vendor": "juniper", "prompt_pattern": "router#"})
        self.assertEqual(status, 200)
        second = self.request(request)[2]
        row = second["targets"][0]["definition"]
        self.assertEqual(row["tags"], ["other"])
        self.assertEqual(row["vendor"], "juniper")
        self.assertEqual(row["prompt_pattern"], "router#")

    def test_tabby_supported_options_are_carried_and_only_unknowns_are_losses(self):
        # A Tabby profile carrying documented SSH options must import; keepaliveInterval is converted
        # from milliseconds to whole seconds, readyTimeout has no TSB field so it is reported as a loss,
        # and the target is not discarded for the other valid options.
        sample = (b'version: 1\nprofiles:\n  - id: native\n    type: ssh\n    name: router\n'
                  b'    options:\n      host: fixture.invalid\n      user: fixture-user\n'
                  b'      keepaliveInterval: 1500\n      x11: true\n      auth: password\n'
                  b'      readyTimeout: 5000\n')
        request = {"action": "preview", "format": "tabby",
                   "export_bytes": base64.b64encode(sample).decode()}
        status, _, preview = self.request(request)
        self.assertEqual(status, 200)
        row = preview["targets"][0]["definition"]["options"]
        self.assertEqual(row["keepaliveInterval"], 2)
        self.assertEqual(row["x11"], True)
        self.assertEqual(row["auth"], "password")
        losses = [issue for issue in preview["not_carried"] if issue["kind"] == "option"]
        self.assertEqual([issue["reason"] for issue in losses], ["unsupported_option"])
        self.assertEqual(losses[0]["fields"], ["readyTimeout"])
        self.assertEqual(Registry.from_tree(CONTRACT).validate(preview, "openapi/tsb.openapi.json#/components/schemas/TargetImportPreview"), [])

    def test_securecrt_mapped_options_are_carried_and_unmapped_ones_reported(self):
        securecrt = ('<VanDyke version="3.0"><key name="Sessions"><key name="router">'
                     '<dword name="Is Session">1</dword><string name="Protocol Name">SSH2</string>'
                     '<string name="Hostname">fixture.invalid</string><string name="Username">fixture-user</string>'
                     '<dword name="Forward X11">1</dword><string name="Firewall Name">default</string>'
                     '</key></key></VanDyke>')
        status, _, preview = self.request({"action": "preview", "format": "securecrt",
                                           "export_bytes": base64.b64encode(securecrt.encode()).decode()})
        self.assertEqual(status, 200)
        options = preview["targets"][0]["definition"]["options"]
        self.assertEqual(options["x11"], True)
        losses = [issue for issue in preview["not_carried"] if issue["kind"] == "option"]
        self.assertEqual([issue["reason"] for issue in losses], ["unsupported_option"])

    def test_unpaired_surrogate_is_refused_without_a_server_exception(self):
        sample = b'id: native\ntype: ssh\nname: "\\ud800"\noptions:\n  host: fixture.invalid\n'
        request = {**self.preview, "export_bytes": base64.b64encode(sample).decode()}
        status, _, error = self.request(request)
        self.assertEqual((status, error["error"]["code"]), (422, "invalid_request"))

    def test_multiple_yaml_documents_are_not_merged(self):
        sample = b'id: first\ntype: ssh\noptions:\n  host: fixture.invalid\n---\nname: other\n'
        request = {**self.preview, "export_bytes": base64.b64encode(sample).decode()}
        status, _, error = self.request(request)
        self.assertEqual((status, error["error"]["code"]), (422, "invalid_request"))

    def test_import_with_share_preview_commit_and_stale_on_changed_share(self):
        preview_body = {"action": "preview", "format": "yaml", "share": [USER, PEER], "export_bytes": self.export}
        status, _, preview = self.request(preview_body)
        self.assertEqual(status, 200)
        self.assertEqual(preview["share"], sorted([USER, PEER]))
        self.assertEqual(preview["targets"][0]["definition"]["share"], sorted([USER, PEER]))
        self.assertEqual(preview["targets"][0]["target"]["share"], sorted([USER, PEER]))
        self.assertEqual(preview["targets"][0]["target"]["owner"], USER)

        # Commit with changed share list -> 409 import_preview_stale
        changed_commit = {**preview_body, "action": "commit", "preview_token": preview["preview_token"], "share": [USER]}
        status, _, error = self.request(changed_commit)
        self.assertEqual((status, error["error"]["code"]), (409, "import_preview_stale"))

        # Commit with previewed share list -> 200, targets written with share and owner
        valid_commit = {**preview_body, "action": "commit", "preview_token": preview["preview_token"]}
        status, _, written = self.request(valid_commit)
        self.assertEqual(status, 200)
        tid = written["targets"][0]["id"]
        self.assertEqual(written["targets"][0]["share"], sorted([USER, PEER]))
        self.assertEqual(written["targets"][0]["owner"], USER)
        self.assertEqual(self.app.targets[tid]["share"], sorted([USER, PEER]))
        self.assertEqual(self.app.targets[tid]["owner"], USER)

    def test_reimport_with_changed_share_list_updates_targets_and_audits_share(self):
        preview_body = {"action": "preview", "format": "yaml", "share": [USER], "export_bytes": self.export}
        status, _, preview = self.request(preview_body)
        self.assertEqual(status, 200)
        status, _, written = self.request({**preview_body, "action": "commit", "preview_token": preview["preview_token"]})
        self.assertEqual(status, 200)
        tid = written["targets"][0]["id"]

        # Re-import with changed share list
        update_preview = {"action": "preview", "format": "yaml", "share": [USER, PEER], "export_bytes": self.export}
        status, _, prev2 = self.request(update_preview)
        self.assertEqual(status, 200)
        status, _, written2 = self.request({**update_preview, "action": "commit", "preview_token": prev2["preview_token"]})
        self.assertEqual(status, 200)
        self.assertEqual(written2["outcome"], "written")
        self.assertEqual(self.app.targets[tid]["share"], sorted([USER, PEER]))
        updates = [e for e in self.app.audit_events if e.get("action") == "target.update" and e.get("target_id") == tid]
        self.assertTrue(any("share" in e.get("detail", {}).get("fields", []) for e in updates))

    def test_nested_secret_in_options_is_rejected_and_not_stored_in_definition_or_receipt(self):
        sample = (
            b"- id: leak-target\n"
            b"  type: ssh\n"
            b"  name: bad-row\n"
            b"  options:\n"
            b"    host: fixture.invalid\n"
            b"    auth:\n"
            b"      password: nested-plaintext-secret\n"
            b"- id: safe-target\n"
            b"  type: ssh\n"
            b"  name: good-row\n"
            b"  options:\n"
            b"    host: fixture.invalid\n"
        )
        request = {"action": "preview", "format": "yaml", "export_bytes": base64.b64encode(sample).decode()}
        status, _, preview = self.request(request)
        self.assertEqual(status, 200)
        self.assertEqual(len(preview["targets"]), 1)
        self.assertEqual(preview["targets"][0]["source_ref"], "safe-target")
        self.assertEqual(preview["not_carried"][0]["reason"], "invalid_entry")
        self.assertNotIn("nested-plaintext-secret", json.dumps(preview))

        commit = {**request, "action": "commit", "preview_token": preview["preview_token"]}
        status, _, written = self.request(commit)
        self.assertEqual(status, 200)
        self.assertNotIn("nested-plaintext-secret", json.dumps(written))
        self.assertNotIn("nested-plaintext-secret", json.dumps(list(self.app.import_receipts.values())))
        self.assertNotIn("nested-plaintext-secret", json.dumps(list(self.app.target_definitions.values())))
        self.assertNotIn("nested-plaintext-secret", json.dumps(list(self.app.import_definitions.values())))

    def test_resume_committed_shared_receipt_checks_current_edit_filters(self):
        sample = base64.b64encode(base64.b64decode(self.export) + b'tags: [lab]\n').decode()
        request = {**self.preview, "export_bytes": sample}
        self.app.target_edit_filters[USER] = ["tag:lab"]
        preview = self.request(request)[2]
        import_id = preview["import_id"]
        with patch.object(self.app, "_import_publish", side_effect=OSError("simulated storage failure")):
            commit = {**request, "action": "commit", "preview_token": preview["preview_token"]}
            status, _, _ = self.request(commit)
            self.assertEqual(status, 500)
        receipt = self.app.import_receipts[(USER, import_id)]
        self.assertEqual(receipt["stage"], "committed")
        tid = preview["targets"][0]["target"]["id"]
        self.assertNotIn(tid, self.app.targets)

        self.app.target_edit_filters[USER] = ["tag:other"]
        resume = {"action": "resume", "import_id": import_id}
        status, _, resp = self.request(resume)
        self.assertEqual((status, resp["error"]["code"]), (403, "forbidden"))
        self.assertNotIn(tid, self.app.targets)

    def test_non_string_source_ref_does_not_crash_plan_and_reports_missing_ref(self):
        sample = (
            b"- id: [flow, sequence]\n"
            b"  type: ssh\n"
            b"  name: bad-ref\n"
            b"  options:\n"
            b"    host: fixture.invalid\n"
            b"- id: valid-id\n"
            b"  type: ssh\n"
            b"  name: good-row\n"
            b"  options:\n"
            b"    host: fixture.invalid\n"
        )
        request = {"action": "preview", "format": "yaml", "export_bytes": base64.b64encode(sample).decode()}
        status, _, preview = self.request(request)
        self.assertEqual(status, 200)
        self.assertEqual(len(preview["targets"]), 1)
        self.assertEqual(preview["targets"][0]["source_ref"], "valid-id")
        reasons = [item["reason"] for item in preview["not_carried"]]
        self.assertEqual(reasons, ["missing_source_ref"])

        # Also cover list and mapping IDs directly in _import_plan
        rows = [
            {"id": ["flow", "sequence"], "type": "ssh", "name": "bad-list", "options": {"host": "fixture.invalid"}},
            {"id": {"nested": "map"}, "type": "ssh", "name": "bad-map", "options": {"host": "fixture.invalid"}},
            {"id": "valid-direct", "type": "ssh", "name": "good-direct", "options": {"host": "fixture.invalid"}},
        ]
        targets, issues, _, _ = self.app._import_plan(USER, None, "imp-aaaaaaaaaaaaaaaaaaaaaaaaaa", rows, "yaml")
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["source_ref"], "valid-direct")
        self.assertEqual([item["reason"] for item in issues], ["missing_source_ref", "missing_source_ref"])

    def test_yaml_mismatched_flow_delimiters_refused_as_invalid_request(self):
        sample = (
            b"- id: target-1\n"
            b"  type: ssh\n"
            b"  name: t1\n"
            b"  tags: [a}]\n"
            b"  options:\n"
            b"    host: fixture.invalid\n"
        )
        status, _, body = self.request({
            "action": "preview",
            "format": "yaml",
            "export_bytes": base64.b64encode(sample).decode("ascii"),
        })
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["code"], "invalid_request")

    def test_reimport_removes_omitted_mapped_options_while_preserving_unmapped_options(self):
        sample1 = (
            b"- id: target-reimport-opts\n"
            b"  type: ssh\n"
            b"  name: host1\n"
            b"  options:\n"
            b"    host: 10.0.0.1\n"
            b"    x11: true\n"
            b"    keepaliveInterval: 30\n"
        )
        req1 = {"action": "preview", "format": "yaml", "export_bytes": base64.b64encode(sample1).decode("ascii")}
        status, _, prev1 = self.request(req1)
        self.assertEqual(status, 200)
        status, _, written1 = self.request({**req1, "action": "commit", "preview_token": prev1["preview_token"]})
        self.assertEqual(status, 200)
        tid = written1["targets"][0]["id"]
        self.app.import_definitions[tid]["options"]["custom_unmapped_knob"] = "preserved"

        sample2 = (
            b"- id: target-reimport-opts\n"
            b"  type: ssh\n"
            b"  name: host1\n"
            b"  options:\n"
            b"    host: 10.0.0.1\n"
            b"    connection_linger: 5\n"
        )
        req2 = {"action": "preview", "format": "yaml", "export_bytes": base64.b64encode(sample2).decode("ascii")}
        status, _, prev2 = self.request(req2)
        self.assertEqual(status, 200)
        status, _, written2 = self.request({**req2, "action": "commit", "preview_token": prev2["preview_token"]})
        self.assertEqual(status, 200)
        self.assertEqual(written2["outcome"], "written")

        opts = self.app.import_definitions[tid]["options"]
        self.assertNotIn("x11", opts)
        self.assertNotIn("keepaliveInterval", opts)
        self.assertEqual(opts.get("connection_linger"), 5)
        self.assertEqual(opts.get("custom_unmapped_knob"), "preserved")


if __name__ == "__main__":
    unittest.main()
