"""E4b contract invariants: rollback safety, redaction and epoch compatibility.

These are contract/tooling checks. Account lifecycle behavior is owed by the core,
not simulated by these tests.
"""

import copy
import json
import subprocess
import unittest

from tsbc import _exchange, profiles, selfcheck
from tsbc.schema import Registry

from . import CONTRACT


class AccountsContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.from_tree(CONTRACT)
        cls.openapi = json.loads((CONTRACT / "openapi/tsb.openapi.json").read_text())

    def test_disabled_records_cannot_retain_an_enabled_accounts_hash(self):
        fixture = json.loads((CONTRACT / "fixtures/C6/users-old-record-loads-enabled.json").read_text())
        old = fixture["case"]["instances"][0]["doc"]
        schema = "schemas/users.schema.json"
        self.assertEqual(self.registry.validate(old, schema), [])
        disabled = {**old, "disabled": True}
        self.assertTrue(self.registry.validate(disabled, schema))
        disabled["password_hash"] = ""
        self.assertEqual(self.registry.validate(disabled, schema), [])
        self.assertTrue(self.registry.validate({**disabled, "disabled": False}, schema))
        self.assertEqual(self.registry.validate({**old, "disabled": False}, schema), [])

    def test_own_change_password_fields_are_hygiene_checked_and_redacted(self):
        for field in ("current_password", "new_password"):
            with self.subTest(field=field):
                value = "not-a-synthetic-value"
                problems = selfcheck.credential_problems({field: value}, placeholders=True)
                self.assertEqual(len(problems), 1)
                self.assertNotIn(value, problems[0])
                self.assertEqual(selfcheck.credential_problems(
                    {field: "FIXTURE-NOT-A-PASSWORD"}, placeholders=True), [])
                self.assertEqual(_exchange._safe("/" + field, value), "<redacted>")

    def test_account_responses_and_audit_reject_passwords_hashes_and_unlisted_rights(self):
        item = {"username": "fixture-account", "created_at": "2026-11-13T09:00:00Z", "disabled": False}
        base = "openapi/tsb.openapi.json#/components/schemas/"
        self.assertEqual(self.registry.validate(item, base + "AccountSummary"), [])
        for key in ("password", "password_hash", "permissions"):
            self.assertTrue(self.registry.validate({**item, key: "FIXTURE-NOT-A-PASSWORD"}, base + "AccountSummary"))
        created = {"account": item, "permissions": ["account.admin"]}
        self.assertEqual(self.registry.validate(created, base + "AccountCreated"), [])
        self.assertTrue(self.registry.validate({**created, "permissions": ["account.root"]}, base + "AccountCreated"))
        for action in ("create", "disable", "enable", "password_set", "password_changed"):
            event = {"ts": item["created_at"], "user": item["username"], "actor": "fixture-admin",
                     "action": "account." + action}
            schema = "schemas/audit-event.schema.json"
            self.assertEqual(self.registry.validate(event, schema), [])
            for key in ("password", "password_hash", "detail"):
                self.assertTrue(self.registry.validate({**event, key: "FIXTURE-NOT-A-PASSWORD"}, schema))
            no_actor = copy.deepcopy(event)
            del no_actor["actor"]
            self.assertTrue(self.registry.validate(no_actor, schema))

    def test_login_failure_reason_registry_preserves_other_auth_details(self):
        schema = "schemas/audit-event.schema.json"
        event = {"ts": "2026-11-13T09:00:00Z", "user": "fixture-user",
                 "action": "auth.login_failed"}
        for reason in ("invalid_credentials", "disabled"):
            self.assertEqual(self.registry.validate({**event, "detail": {"reason": reason}}, schema), [])
        self.assertTrue(self.registry.validate({**event, "detail": {"reason": "unregistered"}}, schema))
        # M5 registers login failure only; other auth details stay extensible.
        for action in ("auth.login", "auth.screen_unlock_failed", "auth.password_change_failed"):
            self.assertEqual(self.registry.validate(
                {**event, "action": action, "detail": {"reason": "other-detail"}}, schema), [])

    def test_password_schema_keeps_existing_short_proofs_and_character_maximum(self):
        schema = "openapi/tsb.openapi.json#/components/schemas/OwnPasswordChangeRequest"
        proof = {"current_password": "x", "new_password": "é" * 1024}
        # Runtime minimum is a knob; schema cannot reject a short legacy proof.
        self.assertEqual(self.registry.validate(proof, schema), [])
        self.assertTrue(self.registry.validate({**proof, "new_password": "é" * 1025}, schema))
        self.assertTrue(self.registry.validate({**proof, "current_password": ""}, schema))

    def test_inserting_e4b_preserves_every_old_fixture_profile_classification(self):
        old_epochs = ("e0", "e1", "e2", "e3", "e4", "e5", "e6")
        self.assertEqual(profiles.EPOCHS, (*old_epochs[:5], "e4b", "e3b", *old_epochs[5:]))
        names = subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", "v0.16.0", "--", "contract/fixtures"],
            cwd=CONTRACT.parent, text=True).splitlines()
        for name in names:
            if not name.endswith(".json") or name.endswith("graded-by.json"):
                continue
            old = json.loads(subprocess.check_output(
                ["git", "show", "v0.16.0:" + name], cwd=CONTRACT.parent, text=True))
            now = json.loads((CONTRACT.parent / name).read_text())
            if name == "contract/fixtures/C8/end-marker-killed.json":
                # Terminal incompleteness allocates 9; this case still rejects the next unknown code.
                old["case"]["invalid"][3]["sidecar_hex"] = "000a"
                old["case"]["invalid"][3]["problem"] = "codes [10] this tag does not define"
            if name == "contract/fixtures/C1/target-create.json":
                # Inventory read-back alone opts out of comparing its response-only hint.
                old["case"]["steps"][1]["expect"]["match"][0]["ignore"] = ["login_hint"]
            self.assertEqual(now, old, name)
            mark = old["mark"]
            for epoch in old_epochs:
                here = old_epochs.index(epoch)
                required = mark.get("required-green")
                if required is not None and old_epochs.index(required) <= here:
                    want = profiles.REQUIRED
                elif min(old_epochs.index(e) for e in mark.values()) <= here:
                    want = profiles.PUBLISHED
                else:
                    want = profiles.FUTURE
                self.assertEqual(profiles.status(mark, epoch), want, (name, epoch))
            self.assertEqual(profiles.status(mark, "e4b"), profiles.status(mark, "e4"), name)

    def test_e4b_fixtures_have_graders_and_are_required_only_from_e4b_on(self):
        index = json.loads((CONTRACT / "fixtures/graded-by.json").read_text())
        found = []
        for path in (CONTRACT / "fixtures").glob("C*/*.json"):
            fixture = json.loads(path.read_text())
            if "e4b" not in fixture["mark"].values():
                continue
            found.append(path)
            self.assertEqual(fixture["mark"], {"published": "e4b", "required-green": "e4b"})
            self.assertEqual(fixture["grade"], "reference")
            label = fixture["seam"] + "/" + fixture["name"]
            self.assertTrue(index[label], label)
            for entry in index[label]:
                self.assertIn("unit", entry)
                self.assertTrue("owed" in entry or ("file" in entry and "test_id" in entry), label)
            for epoch in profiles.EPOCHS:
                want = profiles.FUTURE if profiles.epoch_index(epoch) < profiles.epoch_index("e4b") else profiles.REQUIRED
                self.assertEqual(profiles.status(fixture["mark"], epoch), want, label)
        self.assertTrue(found)
