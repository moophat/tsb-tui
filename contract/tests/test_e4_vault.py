"""The vault release (E4): its vectors, the self-check's vector exemption, the fixture server's vault operations,
and additivity on v0.10.0.

- Every C7 vector file parses as hex where the schema says bytes; the SHA-256 and PBKDF2 known answers and the chain
  steps the standard library can compute (the split, device_id, encryption_key, the associated-data and signed-bytes
  encoding) are recomputed here. argon2id, AES-GCM, ECDSA and OpaqueString need libraries tsbc does not carry, so
  their files name the implementations that grade them (DEFAULT 16, 55).
- The self-check: a shape fixture with a non-synthetic value under any of the five new credential keys fails; a hex
  vector file under the exempted path passes; the same file moved outside that path fails (DEFAULT 44).
- The authoring script is a tool, not a module: nothing in tsbc, the client or the tests imports it.
- `tsbc serve` answers every new operation, and the client's request bodies and the server's answers match the
  OpenAPI document.
- v0.10.0's fixtures are unchanged, and every new fixture is first published or required at e4, so an E3 provider
  graded at e3 meets exactly the set it met before.
"""

import base64
import hashlib
import json
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

import tsb_contract_client as tcc
from tsbc import _exchange, profiles, selfcheck
from tsbc.fixtures import load_fixtures
from tsbc.schema import Registry, validate

from . import CONTRACT
from .stub_core import PASSWORD, PEER, TARGET, USER
from .stub_server import StubServer
from .test_additivity import V010_TEXT_ONLY, _comparable

C7 = CONTRACT / "fixtures" / "C7"
REGISTRY = Registry.from_tree(CONTRACT)
HEX = re.compile(r"^(?:[0-9a-f]{2})*$")
TEXT_FIELDS = {"name", "source", "checked_by", "primitive", "step", "comment", "passphrase", "user_id", "login_handle",
               "operation", "key_name", "unit", "what", "device_id_b64url", "aad_fields"}


def vector(name: str) -> dict:
    return json.loads((C7 / f"{name}.json").read_text(encoding="utf-8"))["case"]["instances"][0]["doc"]


def fields(*items: bytes) -> bytes:
    return b"".join(struct.pack(">H", len(i)) + i for i in items)


class VectorFiles(unittest.TestCase):
    def test_vector_files_are_exactly_the_kat_and_chain_fixtures(self):
        names = sorted(p.name for p in C7.glob("*.json") if selfcheck.is_vector_file(f"fixtures/C7/{p.name}"))
        self.assertEqual(len([n for n in names if n.startswith("kat-")]), 7)
        self.assertEqual(len([n for n in names if n.startswith("chain-")]), 11)
        for name in names:
            fx = json.loads((C7 / name).read_text(encoding="utf-8"))
            self.assertEqual(fx["case"]["schema"].split("#")[0], "schemas/vault-vectors.schema.json", name)
            self.assertEqual(fx["mark"], {"published": "e4"}, name)

    def test_every_byte_value_is_hex(self):
        def walk(node, where):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in TEXT_FIELDS:
                        continue
                    yield from walk(v, f"{where}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    yield from walk(v, f"{where}[{i}]")
            elif isinstance(node, str):
                yield where, node
        checked = 0
        for path in sorted(C7.glob("*.json")):
            if not selfcheck.is_vector_file(f"fixtures/C7/{path.name}"):
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))["case"]["instances"][0]["doc"]
            for where, value in walk(doc, path.name):
                self.assertRegex(value, HEX, where)
                bytes.fromhex(value)
                checked += 1
        self.assertGreater(checked, 100)

    def test_sha256_known_answers(self):
        for case in vector("kat-sha256")["cases"]:
            self.assertEqual(hashlib.sha256(bytes.fromhex(case["message"])).hexdigest(), case["digest"])

    def test_pbkdf2_known_answers(self):
        for case in vector("kat-pbkdf2-sha256")["cases"]:
            got = hashlib.pbkdf2_hmac("sha256", bytes.fromhex(case["password"]), bytes.fromhex(case["salt"]),
                                      case["iterations"], case["dk_length"])
            self.assertEqual(got.hex(), case["dk"])

    def test_the_chain_steps_the_stdlib_can_compute(self):
        split, mh = vector("chain-split"), vector("chain-master-hash")
        self.assertEqual(split["master_hash"], mh["master_hash"])
        self.assertEqual(split["part1"] + split["part2"], split["master_hash"])
        self.assertEqual(vector("chain-passphrase-bytes")["bytes"], mh["passphrase_bytes"])
        self.assertEqual(mh["params"], {"version": 19, "time_cost": 3, "memory_kib": 65536, "parallelism": 4,
                                        "tag_length": 32})
        dev = vector("chain-device-id")
        digest = hashlib.sha256(bytes.fromhex(dev["facts_bytes"])).digest()
        self.assertEqual(digest.hex(), dev["device_id"])
        self.assertEqual(base64.urlsafe_b64encode(digest).rstrip(b"=").decode(), dev["device_id_b64url"])
        self.assertEqual(REGISTRY.validate(dev["device_id_b64url"], "schemas/common.schema.json#/$defs/device_id"), [])
        ek = vector("chain-encryption-key")
        self.assertEqual(ek["master_hash"], mh["master_hash"])
        got = hashlib.pbkdf2_hmac("sha256", bytes.fromhex(ek["master_hash"]), bytes.fromhex(ek["salt2"]),
                                  ek["iterations"], 32)
        self.assertEqual(got.hex(), ek["encryption_key"])
        sk = vector("chain-storage-key")
        self.assertEqual(sk["device_id"], dev["device_id"])

    def test_the_seals_carry_their_associated_data_and_iv(self):
        user, dev = b"fixture-user", bytes.fromhex(vector("chain-device-id")["device_id"])
        ek = vector("chain-encryption-key")["encryption_key"]
        want = {
            "chain-part1-seal": (fields(b"tsb.vault.part1.v1", user, dev), vector("chain-storage-key")["storage_key"]),
            "chain-set-seal": (fields(b"tsb.vault.set.v1", user), ek),
            "chain-org-seal-broker": (fields(b"tsb.vault.org.v1", b"fleet-backup", b"broker_unlocked"), None),
            "chain-org-seal-user": (fields(b"tsb.vault.org.v1", b"fleet-backup", b"user_unlocked"), ek),
        }
        for name, (aad, key) in want.items():
            doc = vector(name)
            self.assertEqual(doc["aad"], aad.hex(), name)
            if key is not None:
                self.assertEqual(doc["key"], key, name)
            self.assertTrue(doc["sealed"].startswith(doc["iv"]), name)
            # IV (12) + ciphertext (as long as the plaintext) + tag (16)
            self.assertEqual(len(doc["sealed"]) // 2, 12 + len(doc["plaintext"]) // 2 + 16, name)
        self.assertEqual(vector("chain-part1-seal")["plaintext"], vector("chain-split")["part1"])
        plain = json.loads(bytes.fromhex(vector("chain-set-seal")["plaintext"]))
        self.assertEqual(REGISTRY.validate(plain, "schemas/vault.schema.json#/$defs/credential_set"), [])
        org = json.loads(bytes.fromhex(vector("chain-org-seal-user")["plaintext"]))
        self.assertEqual(REGISTRY.validate(org, "schemas/vault.schema.json#/$defs/org_credential_secret"), [])

    def test_the_challenge_signed_bytes(self):
        doc = vector("chain-challenge-signed-bytes")
        dev = bytes.fromhex(doc["device_id"])
        parts = [bytes.fromhex(doc["challenge"]), doc["user_id"].encode(), dev, doc["login_handle"].encode()]
        self.assertEqual(doc["signed_bytes"], fields(b"tsb.device.challenge.v1", *parts, doc["operation"].encode()).hex())
        self.assertEqual(doc["wrong_operation_signed_bytes"],
                         fields(b"tsb.device.challenge.v1", *parts, b"part2.regenerate").hex())
        self.assertEqual(len(bytes.fromhex(doc["signature"])), 64)
        spki = base64.b64encode(bytes.fromhex(doc["public_key"])).decode()
        self.assertEqual(REGISTRY.validate(spki, "schemas/vault.schema.json#/$defs/public_key"), [])

    def test_argon2id_vectors_name_both_sources(self):
        rfc, ref = vector("kat-argon2id-rfc9106"), vector("kat-argon2id-reference")
        self.assertTrue(rfc["cases"][0]["secret"] and rfc["cases"][0]["ad"])
        self.assertTrue(all("secret" not in c and "ad" not in c for c in ref["cases"]))
        self.assertTrue(all(c["params"]["memory_kib"] <= 65536 for c in ref["cases"]))


class VectorExemption(unittest.TestCase):
    """DEFAULT 44: the vector files are exempt from the credential-key rule by path, and only by path."""

    def _tree(self, tmp: str) -> Path:
        root = Path(tmp) / "contract"
        shutil.copytree(CONTRACT, root, ignore=shutil.ignore_patterns("tests", "__pycache__", "client", "tools"))
        return root

    @staticmethod
    def _cred_failures(report) -> list[str]:
        return [f for f in report.failures if "credential-named value" in f]

    def test_the_tree_passes_with_its_vectors_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._cred_failures(selfcheck.run(self._tree(tmp))), [])

    def test_the_same_vector_file_outside_the_path_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            src = root / "fixtures" / "C7" / "kat-pbkdf2-sha256.json"
            fx = json.loads(src.read_text(encoding="utf-8"))
            fx["name"] = "pbkdf2-known-answers"
            src.unlink()
            (root / "fixtures" / "C7" / "pbkdf2-known-answers.json").write_text(json.dumps(fx), encoding="utf-8")
            failures = self._cred_failures(selfcheck.run(root))
            self.assertTrue(failures)
            self.assertTrue(all("pbkdf2-known-answers.json" in f for f in failures), failures)
            self.assertFalse(selfcheck.is_vector_file("fixtures/C6/kat-pbkdf2-sha256.json"))
            self.assertFalse(selfcheck.is_vector_file("fixtures/C7/sub/kat-x.json"))

    def test_a_shape_fixture_with_a_real_looking_part_fails(self):
        for key in ("part1", "part2", "master_hash", "storage_key", "encryption_key"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                root = self._tree(tmp)
                src = root / "fixtures" / "C7" / "unlock-untrusted.json"
                fx = json.loads(src.read_text(encoding="utf-8"))
                fx["case"]["steps"][0]["http"]["body"][key] = "c2VjcmV0LWxvb2tpbmctdmFsdWU="
                src.write_text(json.dumps(fx), encoding="utf-8")
                failures = self._cred_failures(selfcheck.run(root))
                self.assertTrue(any(f"body.{key}" in f for f in failures), failures)

    def test_the_new_keys_are_credential_keys_with_fitting_constants(self):
        for key in ("part1", "part2", "master_hash", "storage_key", "encryption_key"):
            self.assertIn(key, selfcheck.CREDENTIAL_KEYS)
        b16 = "schemas/vault.schema.json#/$defs/b64_16"
        b32 = "schemas/vault.schema.json#/$defs/b64_32"
        new = [c for c in selfcheck.SYNTHETIC_CONSTANTS if c.startswith("RklY")]
        self.assertEqual(len(new), 5)
        for c in new:
            self.assertTrue(base64.b64decode(c).startswith(b"FIXTURE-"))
            self.assertEqual(REGISTRY.validate(c, b16 if len(c) == 24 else b32), [], c)


class AuthoringScriptIsNotAModule(unittest.TestCase):
    def test_nothing_imports_the_authoring_script(self):
        # The script lives in tsb-program's tools/, outside contract/: no file under contract/ is it, and no Python
        # file the contract ships or tests imports it (tsbc, the client, the tests; a run from a split-out
        # contract tree still checks all of that).
        self.assertEqual([p for p in CONTRACT.rglob("author*c7*vectors*") if "__pycache__" not in p.parts], [])
        program_tool = CONTRACT.parent / "tools" / "author-c7-vectors.py"
        if (CONTRACT.parent / "tools" / "release.py").is_file():  # inside the tsb-program checkout
            self.assertTrue(program_tool.is_file())
        pattern = re.compile(r"author[-_]c7[-_]vectors")
        for base in ("tsbc", "client", "tests"):
            for path in (CONTRACT / base).rglob("*.py"):
                if path.name == Path(__file__).name:
                    continue
                self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), str(path))


def _response_schema(op, status: int):
    resp = _exchange.response(REGISTRY, op, status)
    return _exchange.json_schema_of(REGISTRY, resp) if resp else None


class ServeAnswersTheVault(unittest.TestCase):
    """`tsbc serve` answers every operation the vault release adds; the client's bodies and the answers validate."""

    NEW: ClassVar[frozenset] = frozenset({("GET", "/vault/salt"), ("POST", "/vault/salt"), ("POST", "/vault/init"), ("POST", "/vault/unlock"), ("POST", "/vault/part2"),
           ("POST", "/devices/challenges"), ("GET", "/devices"), ("POST", "/devices"),
           ("DELETE", "/devices/{device_id}"), ("DELETE", "/vault/users/{username}"),
           ("GET", "/vault/credentials"), ("POST", "/vault/credentials"), ("DELETE", "/vault/credentials/{reference}"),
           ("GET", "/vault/org-credentials"), ("POST", "/vault/org-credentials"),
           ("DELETE", "/vault/org-credentials/{reference}"), ("POST", "/vault/grants"),
           ("POST", "/vault/grants/{grant_id}"), ("DELETE", "/vault/grants/{grant_id}"), ("POST", "/auth/bind"),
           ("POST", "/auth/activity"), ("POST", "/auth/lock"), ("POST", "/auth/screen-unlock"),
           ("POST", "/profiles/batch")})

    PART1 = "RklYVFVSRS1QQVJUMS0wMA=="
    PART2 = "RklYVFVSRS1QQVJUMi0wMA=="
    SIG = "RklYVFVSRS1TSUdOQVRVUkUtMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMA=="
    PUB = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE" + "A" * 86 + "=="
    DEV = "FIXTURE-DEVICE-ID-" + "A" * 25

    def test_the_new_operations_are_the_ones_in_the_document(self):
        ops = {(o.method, o.template) for o in _exchange.operations(REGISTRY)}
        self.assertLessEqual(self.NEW, ops)

    def test_every_new_operation_answers_and_matches_the_contract(self):
        from tsbc._serve import ServeApp

        app = ServeApp()
        with StubServer(app) as url:
            c = tcc.Client(url, timeout=5)
            self.assertEqual(c.login(USER, PASSWORD)["locks"]["vault_hard_cap"], 28800)
            self.assertIn("locks", c.me())
            self.assertTrue(c.vault_salt()["initialized"])
            c.vault_unlock_untrusted(self.PART1, self.PART2)
            ch = c.device_challenge("device.register", self.DEV)
            self.assertEqual(c.register_device(ch["challenge_id"], self.PART1, self.PART2, self.PUB, self.SIG,
                                               "Work laptop")["device_id"], self.DEV)
            with self.assertRaises(tcc.ApiError) as bind_refused:  # the registration tied this login already
                c.device_challenge("login.bind", self.DEV)
            self.assertEqual(bind_refused.exception.code, "challenge_failed")
            ch = c.device_challenge("vault.unlock", self.DEV)
            c.vault_unlock_trusted(ch["challenge_id"], self.PART1, self.SIG)
            ch = c.device_challenge("part2.regenerate", self.DEV)
            c.regenerate_part2(ch["challenge_id"], self.PART1, self.PART2, self.SIG)
            self.assertEqual(c.store_credential("lab-switch", "password", "netops", PASSWORD, "Lab switches"),
                             {"reference": "lab-switch", "kind": "password"})
            self.assertEqual(c.credentials()[0]["label"], "Lab switches")
            c.map_profiles({TARGET: "lab-switch"})
            c.store_org_credential("fleet-backup", "broker_unlocked", "password", "svc", PASSWORD)
            self.assertEqual(c.org_credentials()[0]["unlock_class"], "broker_unlocked")
            grant = c.create_grant(PEER, ["tag:core"], "fleet-backup")
            self.assertEqual(c.change_grant_scope(grant["grant_id"], ["*"])["selectors"], ["*"])
            with self.assertRaises(tcc.ApiError) as has_grants:
                c.delete_org_credential("fleet-backup")
            self.assertEqual(has_grants.exception.code, "credential_has_grants")
            c.revoke_grant(grant["grant_id"])
            c.delete_org_credential("fleet-backup")
            c.activity()
            c.screen_unlock(PASSWORD)
            with self.assertRaises(tcc.ApiError) as wrong:
                c.screen_unlock("FIXTURE-WRONG-PASSWORD")
            self.assertEqual((wrong.exception.status, wrong.exception.code), (403, "screen_unlock_failed"))
            self.assertEqual(c.me()["username"], USER)  # the login stays
            c.delete_credential("lab-switch")
            c.store_credential("netops", "password", "netops", PASSWORD, "Switches")  # what the choice below names:
            c.map_profiles({TARGET: "netops"})                                        # stored, and mapped to TARGET
            self.assertEqual(c.devices()[0]["device_id"], self.DEV)
            self.assertEqual(c.devices(USER)[0]["device_id"], self.DEV)
            c.lock_now()
            c.wipe_vault(PEER)
            with self.assertRaises(tcc.ApiError) as locked:   # netops carries a secret and the lock purged this login's unlock
                c.create_session(TARGET, "new", "xterm-256color", 120, 40, credential=("netops", "personal"))
            self.assertEqual((locked.exception.status, locked.exception.code), (409, "vault_locked"))
            c.vault_unlock_untrusted(self.PART1, self.PART2)
            created = c.create_session(TARGET, "new", "xterm-256color", 120, 40, credential=("netops", "personal"))
            self.assertIn("session", created)
            c.revoke_device(self.DEV)
            with self.assertRaises(tcc.ApiError) as gone:  # this login was tied to the revoked device
                c.me()
            self.assertEqual(gone.exception.status, 401)
            p = tcc.Client(url, timeout=5)
            p.login(PEER, PASSWORD)
            self.assertEqual(p.vault_salt(), {"initialized": False})
            with self.assertRaises(tcc.ApiError) as forbidden:
                p.devices(USER)
            self.assertEqual(forbidden.exception.code, "forbidden")
            with self.assertRaises(tcc.ApiError) as not_found:
                p.revoke_device(self.DEV)
            self.assertEqual(not_found.exception.code, "device_not_found")
        seen = set()
        for method, path, body in app.requests:
            op = _exchange.find_operation(REGISTRY, method, path)
            self.assertIsNotNone(op, f"{method} {path}")
            seen.add((op.method, op.template))
            schema = _exchange.request_schema(REGISTRY, op)
            if body is not None:
                self.assertEqual(validate(body, schema, REGISTRY, _exchange.OPENAPI), [], f"{method} {op.template}")
        # bind, the mint and init are driven by their own tests below
        missing = self.NEW - seen - {("POST", "/auth/bind"), ("POST", "/vault/salt"), ("POST", "/vault/init")}
        self.assertEqual(missing, set())

    def test_initialisation_in_two_calls(self):
        from tsbc._serve import ServeApp

        app = ServeApp()
        with StubServer(app) as url:
            p = tcc.Client(url, timeout=5)
            p.login(PEER, PASSWORD)
            with self.assertRaises(tcc.ApiError) as before:
                p.vault_init(self.PART1, self.PART2)
            self.assertEqual((before.exception.status, before.exception.code), (409, "vault_not_minted"))
            minted = p.vault_mint_salt()
            self.assertFalse(minted["initialized"])
            self.assertEqual(p.vault_mint_salt(), minted)  # a repeat answers the same salt
            self.assertEqual(p.vault_salt(), minted)       # the read answers it too, still not initialised
            with self.assertRaises(tcc.ApiError) as unsealed:
                p.vault_unlock_untrusted(self.PART1, self.PART2)
            self.assertEqual(unsealed.exception.code, "vault_not_initialized")
            p.vault_init(self.PART1, self.PART2)
            self.assertEqual(p.vault_salt()["initialized"], True)
            p.store_credential("lab-telnet", "none")      # init holds an unlock (DEFAULT 35)
            for call in (p.vault_mint_salt, lambda: p.vault_init(self.PART1, self.PART2)):
                with self.assertRaises(tcc.ApiError) as sealed:
                    call()
                self.assertEqual(sealed.exception.code, "vault_exists")
        for method, path, body in app.requests:
            op = _exchange.find_operation(REGISTRY, method, path)
            if body is not None:
                self.assertEqual(validate(body, _exchange.request_schema(REGISTRY, op), REGISTRY, _exchange.OPENAPI), [])
        self.assertNotIn(PEER, app.minted)

    def test_a_mapping_to_a_missing_reference_is_409_naming_it(self):
        from tsbc._serve import ServeApp

        app = ServeApp()
        app.profiles[USER] = {TARGET: "lab-gone"}
        with StubServer(app) as url:
            c = tcc.Client(url, timeout=5)
            c.login(USER, PASSWORD)
            with self.assertRaises(tcc.ApiError) as missing:
                c.create_session(TARGET, "new", "xterm-256color", 120, 40)
            self.assertEqual((missing.exception.status, missing.exception.code), (409, "mapped_credential_missing"))
        status, _, body = app.http("POST", "/sessions", {"cookie": f"tsb_session={next(iter(app.logins))}"},
                                   {"target_id": TARGET, "connection": "new", "term": "xterm", "cols": 80, "rows": 24})
        self.assertEqual((status, body["error"]["credential_ref"]), (409, "lab-gone"))
        schema = _response_schema(_exchange.find_operation(REGISTRY, "POST", "/sessions"), 409)
        self.assertEqual(validate(body, schema, REGISTRY, _exchange.OPENAPI), [])

    def test_bind_and_its_answers_validate(self):
        from tsbc._serve import ServeApp

        app = ServeApp()
        app.devices[(USER, self.DEV)] = {"device_id": self.DEV, "device_name": "Work laptop",
                                         "registered_at": "2026-10-29T08:00:00Z", "last_used_at": "2026-10-29T08:00:00Z"}
        with StubServer(app) as url:
            c = tcc.Client(url, timeout=5)
            c.login(USER, PASSWORD)
            ch = c.device_challenge("login.bind", self.DEV)
            c.bind_login(ch["challenge_id"], self.SIG)
            with self.assertRaises(tcc.ApiError) as again:
                c.device_challenge("login.bind", self.DEV)
            self.assertEqual(again.exception.code, "challenge_failed")
            with self.assertRaises(tcc.ApiError) as untrusted:
                c.device_challenge("vault.unlock", "FIXTURE-DEVICE-ID-" + "B" * 24 + "A")
            self.assertEqual(untrusted.exception.code, "device_not_trusted")

    def test_each_answer_validates_against_its_response_schema(self):
        from tsbc._serve import ServeApp

        app = ServeApp()
        cookie = "c" * 32
        app.logins[cookie] = USER
        app.unlocked.add(cookie)
        headers = {"cookie": f"tsb_session={cookie}"}
        calls = [
            ("GET", "/vault/salt", None),
            ("POST", "/vault/salt", None),
            ("POST", "/vault/init", {"part1": self.PART1, "part2": self.PART2}),
            ("POST", "/devices/challenges", {"operation": "device.register", "device_id": self.DEV}),
            ("GET", "/devices", None),
            ("POST", "/vault/credentials", {"reference": "lab-telnet", "kind": "none"}),
            ("GET", "/vault/credentials", None),
            ("POST", "/vault/org-credentials", {"reference": "fleet-telnet", "unlock_class": "user_unlocked", "kind": "none"}),
            ("GET", "/vault/org-credentials", None),
            ("POST", "/vault/grants", {"user": PEER, "selectors": ["*"], "credential_ref": "fleet-telnet"}),
            ("POST", "/vault/unlock", {"mode": "untrusted", "part1": self.PART1}),
            ("POST", "/auth/screen-unlock", {"password": "FIXTURE-WRONG-PASSWORD"}),
            ("DELETE", "/vault/users/nobody", None),
            ("GET", "/auth/me", None),
        ]
        for method, path, body in calls:
            status, _, answer = app.http(method, path, headers, body)
            op = _exchange.find_operation(REGISTRY, method, path)
            schema = _response_schema(op, status)
            self.assertIsNotNone(schema, f"{method} {path} {status} is not a declared answer")
            self.assertEqual(validate(answer, schema, REGISTRY, _exchange.OPENAPI), [], f"{method} {path} {status}")


class AdditiveOnV010(unittest.TestCase):
    """v0.10.0's fixtures are unchanged, its valid instances stay valid, and every fixture this release adds is first
    marked at e4, so `tsbc verify --profile e3` grades an E3 core on exactly what it graded before."""

    RAW: ClassVar[dict] = json.loads((CONTRACT / "tests" / "v010-fixture-digests.json").read_text(encoding="utf-8"))
    RECORD: ClassVar[dict] = dict(zip(RAW["files"], RAW["sha256"], strict=True))

    def test_every_v010_fixture_is_unchanged(self):
        changed = [rel for rel, digest in sorted(self.RECORD.items())
                   if hashlib.sha256(_comparable(rel, (CONTRACT / "fixtures" / rel).read_bytes(), V010_TEXT_ONLY)).hexdigest() != digest]
        self.assertEqual(changed, [])
        self.assertEqual(len([r for r in self.RECORD if r.endswith(".json")]), 255)

    def test_every_v010_valid_instance_still_validates(self):
        count = 0
        for rel in sorted(r for r in self.RECORD if r.endswith(".json")):
            fx = json.loads((CONTRACT / "fixtures" / rel).read_text(encoding="utf-8"))
            if fx["kind"] != "instance":
                continue
            for i, inst in enumerate(fx["case"]["instances"]):
                if inst["valid"]:
                    count += 1
                    self.assertEqual(REGISTRY.validate(inst["doc"], fx["case"]["schema"]), [], f"{rel} {i}")
        self.assertGreater(count, 100)

    def test_every_new_fixture_is_marked_from_e4(self):
        fixtures, _ = load_fixtures(CONTRACT)
        # Judge the vault release's fixtures, not the marks of later releases (device login,
        # target writes). Their own tests hold those releases to their ruled publication epochs.
        released = set(subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", "v0.11.1", "--", "contract/fixtures"],
            cwd=CONTRACT.parent, text=True).splitlines())
        new = [fx for fx in fixtures if f"contract/{fx.rel}" in released
               and fx.rel.removeprefix("fixtures/") not in self.RECORD]
        self.assertGreater(len(new), 120)
        for fx in new:
            first = min(profiles.epoch_index(e) for e in fx.mark.values())
            self.assertGreaterEqual(first, profiles.epoch_index("e4"), fx.label)
