"""409 `org_credential_locked` in the contract (ruling 2026-10-30 00:58, option (b) of the round-2 stop, and 01:05).

credential-protection §8.2 (LOCKED): a user-unlocked org credential is usable for the operators granted it while its
storing administrator holds an unlock, and locks with that administrator's vault; an unlock belongs to the login that
made it (§5.5), so any login of that administrator will do, and the caller's own unlock never matters for it. A create
on a new connection whose credential is such a one, while no login of its administrator holds an unlock, is refused
409 `org_credential_locked`, with fixed text and no field: the operator's passphrase cannot help, so the surface says
who must act and never asks for it. A personal credential keeps 409 `vault_locked`.

These tests hold the registry, the OpenAPI, the fixture and its index entry, and the protocol texts that name the
refusal, to that rule.
"""

import json
import re
import unittest

from tsbc import _exchange
from tsbc.schema import validate

from . import CONTRACT
from . import test_e4_vault as e4

CODE = "org_credential_locked"
TEXT = "the credential's administrator holds no unlock"
FIXTURE = CONTRACT / "fixtures" / "C1" / "session-create-org-credential-locked-409.json"
PROTOCOLS = CONTRACT / "protocols"


def read(name: str) -> str:
    return (PROTOCOLS / name).read_text(encoding="utf-8")


class TheRegistry(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads((CONTRACT / "schemas" / "error.schema.json").read_text(encoding="utf-8"))

    def test_the_code_is_registered_at_409_and_in_the_enum(self):
        self.assertEqual(self.schema["x-codes"][CODE]["status"], 409)
        self.assertIn(CODE, self.schema["$defs"]["code"]["enum"])

    def test_its_meaning_names_the_storing_administrator_and_vault_locked_stays_personal(self):
        meaning = self.schema["x-codes"][CODE]["meaning"]
        self.assertIn("user_unlocked", meaning)
        self.assertIn("storing administrator", meaning)
        self.assertIn("§8.2", meaning)
        self.assertIn("personal", self.schema["x-codes"]["vault_locked"]["meaning"])

    def test_a_bare_body_validates_and_one_carrying_a_field_does_not(self):
        op = _exchange.find_operation(e4.REGISTRY, "POST", "/sessions")
        schema = e4._response_schema(op, 409)
        self.assertEqual(validate({"error": {"code": CODE, "message": TEXT}}, schema, e4.REGISTRY, _exchange.OPENAPI), [])
        for extra in ({"credential_ref": "fleet-backup"}, {"credential_refs": ["fleet-backup"]},
                      {"candidates": [{"credential_ref": "a", "credential_layer": "org"},
                                      {"credential_ref": "b", "credential_layer": "org"}]}):
            body = {"error": {"code": CODE, "message": TEXT, **extra}}
            self.assertNotEqual(validate(body, schema, e4.REGISTRY, _exchange.OPENAPI), [], extra)


class TheOpenAPI(unittest.TestCase):
    def test_create_session_names_it_among_its_409s(self):
        op = json.loads((CONTRACT / "openapi" / "tsb.openapi.json").read_text(encoding="utf-8"))
        text = op["paths"]["/sessions"]["post"]["responses"]["409"]["description"]
        self.assertIn(f"`{CODE}`", text)
        self.assertIn("storing administrator", text)


class TheFixture(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_it_is_marked_as_the_other_e4_create_fixtures_are(self):
        other = json.loads((FIXTURE.parent / "session-create-vault-locked-409.json").read_text(encoding="utf-8"))
        for key in ("seam", "mark", "kind", "grade"):
            self.assertEqual(self.doc[key], other[key], key)
        self.assertEqual({g["unit"] for g in self.doc["graded_by"]}, {g["unit"] for g in other["graded_by"]})

    def test_it_expects_the_code_and_its_example_validates(self):
        (step,) = self.doc["case"]["steps"]
        self.assertEqual((step["http"]["method"], step["http"]["path"]), ("POST", "/sessions"))
        self.assertEqual(step["http"]["body"]["connection"], "new")
        self.assertEqual(step["expect"]["status"], 409)
        self.assertIn({"pointer": "/error/code", "eq": CODE}, step["expect"]["match"])
        self.assertEqual(step["expect"]["example"], {"error": {"code": CODE, "message": TEXT}})
        schema = e4._response_schema(_exchange.find_operation(e4.REGISTRY, "POST", "/sessions"), 409)
        self.assertEqual(validate(step["expect"]["example"], schema, e4.REGISTRY, _exchange.OPENAPI), [])

    def test_it_cites_the_corpus_clauses_it_rests_on(self):
        self.assertIn("credential-protection §8.2", self.doc["corpus"])
        self.assertIn("credential-protection §5.5", self.doc["corpus"])

    def test_it_has_its_graded_by_index_entry(self):
        index = json.loads((CONTRACT / "fixtures" / "graded-by.json").read_text(encoding="utf-8"))
        self.assertEqual({e["unit"] for e in index["C1/session-create-org-credential-locked-409"]}, {"U2", "U6"})

    def test_the_vault_codes_fixture_carries_it(self):
        codes = json.loads((CONTRACT / "fixtures" / "C1" / "error-codes-vault.json").read_text(encoding="utf-8"))
        valid = [i["doc"]["error"]["code"] for i in codes["case"]["instances"] if i["valid"]]
        self.assertIn(CODE, valid)


class TheProtocols(unittest.TestCase):
    def test_session_open_step_3_says_whose_unlock_and_never_this_logins_for_the_org_class(self):
        text = read("session-open.md")
        (step3,) = [line for line in text.splitlines() if line.startswith("| 3 |")]
        self.assertIn(CODE, step3)
        self.assertIn("C1/session-create-org-credential-locked-409", step3)
        self.assertIn("storing administrator", step3)
        (vault,) = [b for b in text.split("\n- ") if b.startswith("**The vault (from the vault release).**")]
        self.assertIn("`user_unlocked` org credential", vault)          # not this login's unlock for every credential
        self.assertIn("storing administrator", vault)

    def test_every_page_that_names_the_create_refusals_names_it(self):
        for name in ("session-open.md", "vault-unlock.md", "vault-lock.md", "conventions.md"):
            self.assertIn(CODE, read(name), name)
        # SF-CO lists the create's statuses, not its codes, so it gains nothing: 409 is already there
        (row,) = [line for line in read("edges/SF-CO.md").splitlines() if line.startswith("| `POST /sessions`")]
        self.assertIn("409", row)
        self.assertNotIn("vault_locked", row)

    def test_conventions_lists_every_vault_code(self):
        schema = json.loads((CONTRACT / "schemas" / "error.schema.json").read_text(encoding="utf-8"))
        vault = {c for c, v in schema["x-codes"].items() if v["meaning"].startswith("From the vault release")}
        text = read("conventions.md")
        providers = re.search(r"the vault's codes \((.*?)\) are never 401", text, re.DOTALL).group(1)
        self.assertEqual({c for c in vault if f"`{c}`" not in providers}, set())

    def test_the_changelog_says_it(self):
        # The code arrived in v0.11.0, so the check reads that release's section, from its heading to the next one:
        # a release renames `## Unreleased` to the version, so an Unreleased check breaks on every release.
        text = (CONTRACT / "CHANGELOG.md").read_text(encoding="utf-8")
        section = text.split("## v0.11.0", 1)[1].split("\n## ", 1)[0]
        self.assertIn(CODE, section)


if __name__ == "__main__":
    unittest.main()
