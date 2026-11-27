"""The fixture server's vault rules, round 2 (Qodo round 1 on #107, findings 3, 5, 6, 7, 8, 11 and 13, and the
unredeemed-ticket half of device-trust.md step 12). Each test holds one rule the first version got wrong:

- a vault wipe of a user who stored a user-unlocked org credential is 409 `vault_wipe_blocked`, naming the
  references, and changes nothing (3);
- a device challenge answered after the `expires_in` it told is refused 409 `challenge_failed` (5);
- a grant to a name that is no principal is 422 `invalid_request`, whatever the type of the name (6);
- the OpenAPI answers another principal's device for a caller without `vault.admin` as an absent one, 404
  `device_not_found`, never 403, and no fixture asserts the 403 (7);
- only POST and DELETE act on `/vault/grants/{id}`: any other method deletes nothing (8);
- a mapping whose `target_id` or `credential_ref` is not a string is 422 `invalid_request`, never a dropped
  connection (11);
- the activity signal is counted at most once per login per `locks.activity_interval`, in memory bounded by the
  logins (13);
- a revocation voids the revoked logins' unredeemed tickets and no other login's (device-trust.md step 12).

Finding 4 (the credential choice) is in `test_vault_candidates.py`. The grant rules of the third round are here, in
`GrantsNameOnlyPrincipals`: a `credential_ref` that is not a string is 422 before any lookup, and so is a selector
whose `host:` pattern does not compile under Go's `path.Match` (the core's policy load refuses one).
"""

import http.client
import json
import unittest
import urllib.parse

import tsb_contract_client as tcc
from tsbc import _exchange
from tsbc._serve import PEER, TARGET, USER, VIEWER
from tsbc.schema import validate

from . import CONTRACT
from . import test_e4_vault as e4
from . import test_vault_revocation as base
from .test_vault_revocation import DEV, PART1, PART2, SIG


class Rules(base.Harness):
    """The Harness's server, plus raw requests for the answers the client turns into exceptions."""

    def headers(self, cookie: str) -> dict:
        return {"cookie": f"tsb_session={cookie}"}

    def call(self, cookie: str, method: str, path: str, body=None) -> tuple[int, dict | None]:
        """One request through the real server (a dropped connection is an exception here, as to a surface)."""
        parts = urllib.parse.urlsplit(self.url)
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
        try:
            raw = None if body is None else json.dumps(body)
            headers = {"Cookie": f"tsb_session={cookie}"}
            if raw is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=raw, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, (json.loads(data) if data else None)
        finally:
            conn.close()

    def admins(self, *names: str) -> None:
        self.app.vault_admins = frozenset(names)


class ClockedHarness(Rules):
    """A server whose vault clock the test moves, so no test waits out a lifetime."""

    def setUp(self):
        super().setUp()
        self.now = 1000.0
        self.app.clock = lambda: self.now


# -- 3 --------------------------------------------------------------------------------------------------------

class WipeIsBlockedByUserUnlockedOrgCredentials(Rules):
    def store(self, user: str, reference: str, unlock_class: str) -> str:
        """`user` (an administrator, holding an unlock) stores an org credential; returns its cookie."""
        _, cookie = self.login(user)
        self.app.unlocked.add(cookie)
        status, _ = self.call(cookie, "POST", "/vault/org-credentials",
                              {"reference": reference, "unlock_class": unlock_class, "kind": "none"})
        self.assertEqual(status, 201)
        return cookie

    def wipe(self, user: str) -> tuple[int, dict | None]:
        _, admin = self.login(USER)
        return self.call(admin, "DELETE", f"/vault/users/{user}")

    def test_a_wipe_of_the_storing_administrator_is_409_naming_the_credential(self):
        self.admins(USER, PEER)
        peer = self.store(PEER, "fleet-key", "user_unlocked")
        self.app.creds[PEER] = {"lab-switch": {"reference": "lab-switch", "kind": "none"}}
        self.app.profiles[PEER] = {TARGET: "lab-switch"}
        tied, tied_cookie = self.tied_login(PEER)
        status, body = self.wipe(PEER)
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "vault_wipe_blocked")
        self.assertEqual(body["error"]["credential_refs"], ["fleet-key"])
        op = _exchange.find_operation(e4.REGISTRY, "DELETE", "/vault/users/{username}")
        self.assertEqual(validate(body, e4._response_schema(op, 409), e4.REGISTRY, _exchange.OPENAPI), [])
        # nothing changed: "Nothing changed" is the error's own meaning (error.schema.json x-codes)
        self.assertIn(PEER, self.app.vault_users)
        self.assertEqual(self.app.creds[PEER], {"lab-switch": {"reference": "lab-switch", "kind": "none"}})
        self.assertEqual(self.app.profiles[PEER], {TARGET: "lab-switch"})
        self.assertNotIn("revoked_at", self.app.devices[(PEER, DEV)])
        self.assertIn(peer, self.app.unlocked)
        self.assertEqual(self.app.tied[tied_cookie], DEV)
        self.assertEqual(tied.me()["username"], PEER)
        self.assertIn("fleet-key", self.app.org_creds)

    def test_every_blocking_reference_is_named_in_order(self):
        self.admins(USER, PEER)
        self.store(PEER, "fleet-b", "user_unlocked")
        self.store(PEER, "fleet-a", "user_unlocked")
        self.store(PEER, "fleet-broker", "broker_unlocked")
        status, body = self.wipe(PEER)
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["credential_refs"], ["fleet-a", "fleet-b"])

    def test_a_broker_unlocked_credential_does_not_block_a_wipe(self):
        self.admins(USER, PEER)
        self.store(PEER, "fleet-broker", "broker_unlocked")
        self.assertEqual(self.wipe(PEER), (204, None))
        self.assertNotIn(PEER, self.app.vault_users)

    def test_a_credential_another_administrator_stored_does_not_block_a_wipe(self):
        self.admins(USER, PEER)
        self.store(USER, "fleet-key", "user_unlocked")
        self.assertEqual(self.wipe(PEER), (204, None))

    def test_the_wipe_goes_through_once_the_credential_is_deleted(self):
        self.admins(USER, PEER)
        peer = self.store(PEER, "fleet-key", "user_unlocked")
        self.assertEqual(self.wipe(PEER)[0], 409)
        self.assertEqual(self.call(peer, "DELETE", "/vault/org-credentials/fleet-key")[0], 204)
        self.assertEqual(self.wipe(PEER), (204, None))
        self.assertNotIn(PEER, self.app.vault_users)

    def test_the_refusals_before_it_keep_their_order(self):
        self.admins(USER, PEER)
        self.store(PEER, "fleet-key", "user_unlocked")
        _, viewer = self.login(VIEWER)
        self.assertEqual(self.call(viewer, "DELETE", f"/vault/users/{PEER}")[1]["error"]["code"], "forbidden")
        self.assertEqual(self.wipe("nobody")[1]["error"]["code"], "user_not_found")


# -- 5 --------------------------------------------------------------------------------------------------------

class ChallengesExpire(ClockedHarness):
    def test_a_bind_answered_after_its_expires_in_is_refused(self):
        client, cookie = self.login(USER)
        issued = client.device_challenge("login.bind", DEV)
        self.now += issued["expires_in"] + 1
        self.assertEqual(self.refused(lambda: client.bind_login(issued["challenge_id"], SIG)), "challenge_failed")
        self.assertNotIn(cookie, self.app.tied)

    def test_the_same_answer_inside_the_lifetime_is_accepted(self):
        client, cookie = self.login(USER)
        issued = client.device_challenge("login.bind", DEV)
        self.now += issued["expires_in"] - 1
        client.bind_login(issued["challenge_id"], SIG)
        self.assertEqual(self.app.tied[cookie], DEV)

    def test_a_trusted_unlock_answered_late_is_refused_and_installs_nothing(self):
        client, cookie = self.tied_login(USER)
        issued = client.device_challenge("vault.unlock", DEV)
        self.now += issued["expires_in"] + 1
        self.assertEqual(self.refused(lambda: client.vault_unlock_trusted(issued["challenge_id"], PART1, SIG)),
                         "challenge_failed")
        self.assertNotIn(cookie, self.app.unlocked)

    def test_a_part2_regeneration_answered_late_is_refused_and_installs_nothing(self):
        client, cookie = self.tied_login(USER)
        issued = client.device_challenge("part2.regenerate", DEV)
        self.now += issued["expires_in"] + 1
        self.assertEqual(self.refused(lambda: client.regenerate_part2(issued["challenge_id"], PART1, PART2, SIG)),
                         "challenge_failed")
        self.assertNotIn(cookie, self.app.unlocked)

    def test_a_registration_answered_late_is_refused_and_stores_nothing(self):
        other = "FIXTURE-DEVICE-ID-" + "B" * 24 + "A"
        client, _ = self.login(USER)
        issued = client.device_challenge("device.register", other)
        self.now += issued["expires_in"] + 1
        self.assertEqual(self.refused(lambda: client.register_device(
            issued["challenge_id"], PART1, PART2, e4.ServeAnswersTheVault.PUB, SIG, "Second laptop")),
            "challenge_failed")
        self.assertNotIn((USER, other), self.app.devices)

    def test_the_lifetime_is_the_one_the_answer_told(self):
        client, _ = self.login(USER)
        issued = client.device_challenge("login.bind", DEV)
        self.assertEqual(issued["expires_in"], 30)
        self.now += 30
        client.bind_login(issued["challenge_id"], SIG)       # answered at expires_in: not after it


# -- 6 --------------------------------------------------------------------------------------------------------

class GrantsNameOnlyPrincipals(Rules):
    def setUp(self):
        super().setUp()
        _, self.admin = self.login(USER)
        status, _ = self.call(self.admin, "POST", "/vault/org-credentials",
                              {"reference": "fleet-telnet", "unlock_class": "broker_unlocked", "kind": "none"})
        self.assertEqual(status, 201)

    def grant(self, user) -> tuple[int, dict | None]:
        return self.call(self.admin, "POST", "/vault/grants",
                         {"user": user, "selectors": ["*"], "credential_ref": "fleet-telnet"})

    def grant_to(self, user, selectors) -> tuple[int, dict | None]:
        return self.call(self.admin, "POST", "/vault/grants",
                         {"user": user, "selectors": selectors, "credential_ref": "fleet-telnet"})

    def test_a_grant_to_a_name_that_is_no_principal_is_422_and_stores_nothing(self):
        status, body = self.grant("nobody")
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["code"], "invalid_request")
        self.assertEqual(self.app.grants, {})

    def test_a_user_that_is_not_a_string_is_422_never_an_exception(self):
        for user in (["fixture-peer"], {"a": 1}, 7, None, True, ""):
            status, body = self.grant(user)
            self.assertEqual(status, 422, repr(user))
            self.assertEqual(body["error"]["code"], "invalid_request", repr(user))
        self.assertEqual(self.app.grants, {})

    def test_a_credential_ref_that_is_not_a_string_is_422_before_any_lookup(self):
        for ref in (["fleet-telnet"], {"a": 1}, 7, None, True):
            status, body = self.call(self.admin, "POST", "/vault/grants",
                                     {"user": PEER, "selectors": ["*"], "credential_ref": ref})
            self.assertEqual(status, 422, repr(ref))
            self.assertEqual(body["error"]["code"], "invalid_request", repr(ref))
        self.assertEqual(self.app.grants, {})

    def test_a_host_pattern_that_does_not_compile_is_422_and_stores_nothing(self):
        for selector in ("host:[", "host:a[", "host:[]a]", "host:[a-b-c]", "host:web\\", "host:[-]", "host:*[a"):
            for selectors in ([selector], ["*", selector]):       # one bad selector refuses the whole grant
                status, body = self.grant_to(PEER, selectors)
                self.assertEqual(status, 422, repr(selectors))
                self.assertEqual(body["error"]["code"], "invalid_request", repr(selectors))
        self.assertEqual(self.app.grants, {})

    def test_host_patterns_that_compile_are_created(self):
        for selector in ("host:staging-*", "host:192.0.2.[1-5]0", "host:192.0.2.[^1-5]0", "host:lab/*", "host:?",
                         "host:a\\*b", "host:*", "host:[a-z]*"):
            status, body = self.grant_to(PEER, [selector])
            self.assertEqual((status, body["selectors"]), (201, [selector]), selector)

    def test_a_scope_change_to_a_pattern_that_does_not_compile_is_422_and_changes_nothing(self):
        _, grant = self.grant_to(PEER, ["tag:core"])
        status, body = self.call(self.admin, "POST", f"/vault/grants/{grant['grant_id']}", {"selectors": ["host:["]})
        self.assertEqual((status, body["error"]["code"]), (422, "invalid_request"))
        self.assertEqual(self.app.grants[grant["grant_id"]]["selectors"], ["tag:core"])

    def test_a_grant_to_every_principal_the_server_knows_is_created(self):
        for user in (USER, PEER, VIEWER):
            status, body = self.grant(user)
            self.assertEqual((status, body["user"]), (201, user))
        self.assertEqual(len(self.app.grants), 3)


# -- 7 --------------------------------------------------------------------------------------------------------

class RevocationDoesNotRevealOwnership(Rules):
    def revoke_op(self) -> dict:
        document = json.loads((CONTRACT / "openapi" / "tsb.openapi.json").read_text(encoding="utf-8"))
        return document["paths"]["/devices/{device_id}"]["delete"]

    def test_the_openapi_declares_no_403_for_a_revocation(self):
        self.assertNotIn("403", self.revoke_op()["responses"])

    def test_the_openapis_404_covers_another_principals_device_as_an_absent_one(self):
        description = self.revoke_op()["responses"]["404"]["description"].lower()
        self.assertIn("device_not_found", description)
        self.assertIn("another principal", description)
        self.assertIn("absent", description)

    def test_the_server_answers_both_alike(self):
        _, viewer = self.login(VIEWER)     # no vault.admin, and no device of its own
        absent = self.call(viewer, "DELETE", "/devices/FIXTURE-DEVICE-ID-" + "C" * 24 + "A")
        theirs = self.call(viewer, "DELETE", f"/devices/{DEV}?user={USER}")
        own_name = self.call(viewer, "DELETE", f"/devices/{DEV}")
        self.assertEqual(absent[0], 404)
        self.assertEqual(theirs, absent)
        self.assertEqual(own_name, absent)
        self.assertNotIn("revoked_at", self.app.devices[(USER, DEV)])
        op = _exchange.find_operation(e4.REGISTRY, "DELETE", "/devices/{device_id}")
        self.assertEqual(validate(theirs[1], e4._response_schema(op, 404), e4.REGISTRY, _exchange.OPENAPI), [])

    def test_no_fixture_asserts_a_403_for_a_revocation(self):
        for path in sorted((CONTRACT / "fixtures").glob("*/*.json")):
            case = json.loads(path.read_text(encoding="utf-8")).get("case")
            for step in case.get("steps", []) if isinstance(case, dict) else []:
                request = step.get("http") or {}
                if request.get("method") == "DELETE" and str(request.get("path", "")).startswith("/devices/"):
                    self.assertNotEqual((step.get("expect") or {}).get("status"), 403, path.name)


# -- 8 --------------------------------------------------------------------------------------------------------

class OnlyPostAndDeleteActOnAGrant(Rules):
    def setUp(self):
        super().setUp()
        _, self.admin = self.login(USER)
        self.call(self.admin, "POST", "/vault/org-credentials",
                  {"reference": "fleet-telnet", "unlock_class": "broker_unlocked", "kind": "none"})
        status, grant = self.call(self.admin, "POST", "/vault/grants",
                                  {"user": PEER, "selectors": ["tag:core"], "credential_ref": "fleet-telnet"})
        self.assertEqual(status, 201)
        self.gid = grant["grant_id"]

    def test_a_get_answers_as_the_server_answers_an_undeclared_method_and_deletes_nothing(self):
        status, body = self.call(self.admin, "GET", f"/vault/grants/{self.gid}")
        elsewhere = self.call(self.admin, "GET", "/vault/org-credentials/fleet-telnet")
        self.assertEqual(elsewhere[0], 404)
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "grant_not_found")
        self.assertEqual(body["error"]["message"], elsewhere[1]["error"]["message"])   # "no route" in both
        self.assertIn(self.gid, self.app.grants)

    def test_a_method_the_server_does_not_route_deletes_nothing(self):
        for method in ("PUT", "PATCH", "HEAD", "OPTIONS"):
            parts = urllib.parse.urlsplit(self.url)
            conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
            conn.request(method, f"/vault/grants/{self.gid}", headers={"Cookie": f"tsb_session={self.admin}"})
            resp = conn.getresponse()
            resp.read()
            conn.close()
            # Target writes add a PUT handler, but never a PUT operation on grants. Other
            # unsupported verbs still fail at the HTTP handler before reaching the app.
            self.assertEqual(resp.status, 404 if method == "PUT" else 501, method)
            self.assertIn(self.gid, self.app.grants, method)

    def test_post_still_changes_the_scope_and_delete_still_revokes(self):
        status, body = self.call(self.admin, "POST", f"/vault/grants/{self.gid}", {"selectors": ["*"]})
        self.assertEqual((status, body["selectors"]), (200, ["*"]))
        self.assertEqual(self.call(self.admin, "DELETE", f"/vault/grants/{self.gid}"), (204, None))
        self.assertNotIn(self.gid, self.app.grants)
        self.assertEqual(self.call(self.admin, "DELETE", f"/vault/grants/{self.gid}")[1]["error"]["code"],
                         "grant_not_found")

    def test_a_get_on_the_collection_deletes_nothing(self):
        self.assertEqual(self.call(self.admin, "GET", "/vault/grants")[0], 404)
        self.assertIn(self.gid, self.app.grants)


# -- 11 -------------------------------------------------------------------------------------------------------

class MalformedMappingsAre422(Rules):
    def setUp(self):
        super().setUp()
        _, self.cookie = self.login(USER)
        self.app.creds[USER] = {"lab-switch": {"reference": "lab-switch", "kind": "none"}}

    def test_a_target_id_or_credential_ref_that_is_not_a_string_is_422(self):
        bad = [{"target_id": [TARGET], "credential_ref": "lab-switch"},
               {"target_id": {"a": 1}, "credential_ref": "lab-switch"},
               {"target_id": 5, "credential_ref": "lab-switch"},
               {"target_id": None, "credential_ref": "lab-switch"},
               {"target_id": TARGET, "credential_ref": ["lab-switch"]},
               {"target_id": TARGET, "credential_ref": {"a": 1}},
               {"target_id": TARGET, "credential_ref": 5},
               {"target_id": TARGET, "credential_ref": True}]
        for mapping in bad:
            status, body = self.call(self.cookie, "POST", "/profiles/batch", {"mappings": [mapping]})
            self.assertEqual(status, 422, repr(mapping))
            self.assertEqual(body["error"]["code"], "invalid_request", repr(mapping))
        self.assertEqual(self.app.profiles, {})

    def test_one_bad_mapping_in_a_batch_applies_none_of_it(self):
        status, body = self.call(self.cookie, "POST", "/profiles/batch", {"mappings": [
            {"target_id": TARGET, "credential_ref": "lab-switch"},
            {"target_id": ["x"], "credential_ref": None}]})
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["code"], "invalid_request")
        self.assertEqual(self.app.profiles, {})

    def test_a_valid_batch_is_still_applied_and_a_null_still_removes(self):
        ok = {"mappings": [{"target_id": TARGET, "credential_ref": "lab-switch"}]}
        self.assertEqual(self.call(self.cookie, "POST", "/profiles/batch", ok), (204, None))
        self.assertEqual(self.app.profiles[USER], {TARGET: "lab-switch"})
        gone = {"mappings": [{"target_id": TARGET, "credential_ref": None}]}
        self.assertEqual(self.call(self.cookie, "POST", "/profiles/batch", gone), (204, None))
        self.assertEqual(self.app.profiles[USER], {})


# -- 13 -------------------------------------------------------------------------------------------------------

class ActivityIsCountedOncePerInterval(ClockedHarness):
    def test_a_login_that_signals_often_is_counted_once_per_interval(self):
        client, cookie = self.login(USER)
        interval = client.me()["locks"]["activity_interval"]
        for _ in range(50):
            client.activity()                                   # every one answered 204, never refused
        self.assertEqual(len(self.app.activity), 1)
        self.assertEqual(self.app.activity, {cookie: 1000.0})
        self.now += interval - 1
        for _ in range(50):
            client.activity()
        self.assertEqual(self.app.activity, {cookie: 1000.0})   # inside the interval: ignored
        self.now += 1
        client.activity()
        self.assertEqual(self.app.activity, {cookie: 1000.0 + interval})   # at the interval: counted again

    def test_each_login_has_its_own_interval_and_memory_is_one_entry_per_login(self):
        first, first_cookie = self.login(USER)
        second, second_cookie = self.login(USER)
        first.activity()
        self.now += 10
        second.activity()
        for _ in range(100):
            first.activity()
            second.activity()
        self.assertEqual(self.app.activity, {first_cookie: 1000.0, second_cookie: 1010.0})

    def test_a_login_that_ends_leaves_no_entry_behind(self):
        gone, _ = self.login(USER)
        stays, stays_cookie = self.login(USER)
        gone.activity()
        stays.activity()
        gone.logout()
        self.assertEqual(self.app.activity, {stays_cookie: 1000.0})


# -- device-trust.md step 12: unredeemed tickets ---------------------------------------------------------------

class RevocationVoidsUnredeemedTickets(Rules):
    def attach_with(self, client: tcc.Client, ticket: str, sid: str) -> tcc.Attachment:
        return tcc.Attachment.open(client._attach_url(), ticket, sid, ssl_context=None, timeout=5)

    def test_a_revoked_logins_unredeemed_ticket_no_longer_attaches_and_no_other_does(self):
        tied, _ = self.tied_login(USER)
        peer, _ = self.tied_login(PEER)           # the same device id, another principal
        revoker, _ = self.login(USER)             # an untied login of the revoking principal
        sid = self.session_id(revoker)
        revoked_ticket = tied.ws_ticket(sid)["ticket"]
        peer_ticket = peer.ws_ticket(sid)["ticket"]
        revoker_ticket = revoker.ws_ticket(sid)["ticket"]
        revoker.revoke_device(DEV)
        self.assertNotIn(revoked_ticket, self.app.tickets)
        with self.assertRaises(tcc.AttachRefused) as refused:
            self.attach_with(tied, revoked_ticket, sid)
        self.assertEqual(refused.exception.code, 4001)
        for client, ticket in ((peer, peer_ticket), (revoker, revoker_ticket)):
            attached = self.attach_with(client, ticket, sid)
            self.addCleanup(attached.close)
            self.assertFalse(attached.closed)

    def test_a_wipe_voids_the_wiped_principals_tied_logins_tickets(self):
        tied, _ = self.tied_login(PEER)
        admin, _ = self.login(USER)
        sid = self.session_id(admin)
        ticket = tied.ws_ticket(sid)["ticket"]
        admin.wipe_vault(PEER)
        self.assertNotIn(ticket, self.app.tickets)


if __name__ == "__main__":
    unittest.main()
