"""The fixture server follows session-open steps 3 and 4 whole (Qodo round 2 on #107, four findings, all taken by the
orchestrator's ruling of 2026-10-30 00:20). Each test holds one rule the previous version got wrong:

1. Two or more credentials resolve for the principal and target and the request made no choice: 409
   `credential_choice_required` with `candidates`, each a reference and its layer, before any connection is taken.
   A request with a `login` resolves none and is never asked (step 3).
2. A new connection whose credential needs an unlock this login does not hold: 409 `vault_locked`, before any
   connection. A personal credential with no secret needs none; an org one behaves as its unlock class says; a reuse of
   an existing connection never needs one (step 3).
3. The login tracking is bounded by live sockets and live tickets: `client_logins` loses an entry when its socket
   leaves (a disconnect, a kill) and `ticket_logins` one when its ticket is redeemed, rejected or expired.
4. A reuse with no choice takes only the principal's connections to the target opened with the credential that
   request resolves to (step 4, "Candidate": "with the same credential"); another credential's connection is never
   reused, never named by `connection_id` (422) and never listed in `connection_choice_required`.

2b. A `user_unlocked` org credential needs its storing administrator's unlock, held by any login of that administrator,
   and the caller's own unlock never matters for it; without it, 409 `org_credential_locked` (credential-protection
   §8.2, §5.5; ruling 2026-10-30 00:58).
"""

import time
import unittest

import tsb_contract_client as tcc
from tsbc import _exchange
from tsbc._serve import NOCRED, PEER, TARGET, USER, VIEWER
from tsbc.schema import validate

from . import test_e4_vault as e4
from . import test_vault_serve_rules as rules

ORG = "fleet-backup"
SECRET = "FIXTURE-NOT-A-SECRET"


def entry(reference: str, kind: str = "none", **more) -> dict:
    more = {"login_name": "ops", "secret": SECRET, **more} if kind != "none" else more
    return {"reference": reference, "kind": kind, **more}


class Base(rules.Rules):
    """USER holds a personal credential `netops`, mapped to TARGET, and an org credential granted for tag `shell`."""

    NETOPS_KIND = "none"          # a personal entry with no secret needs no unlock; "password" carries one

    def setUp(self):
        super().setUp()
        _, self.cookie = self.login(USER)
        self.app.creds[USER] = {"netops": entry("netops", self.NETOPS_KIND), "other": entry("other")}
        self.app.profiles[USER] = {TARGET: "netops"}
        self.app.org_creds[ORG] = entry(ORG, unlock_class="broker_unlocked", stored_by=USER)
        self.grant_id = self.grant(USER, ["tag:shell"], ORG)

    def grant(self, user: str, selectors: list[str], ref: str) -> str:
        gid = f"g-{len(self.app.grants) + 1}"
        self.app.grants[gid] = {"grant_id": gid, "user": user, "selectors": selectors, "credential_ref": ref,
                                "granted_by": USER, "granted_at": "2026-10-29T08:00:00Z"}
        return gid

    def create(self, target: str = TARGET, choice: tuple | None = None, connection: str = "new", cookie=None, **more):
        body = {"target_id": target, "connection": connection, "term": "xterm-256color", "cols": 120, "rows": 40}
        if choice is not None:
            body["credential_ref"], body["credential_layer"] = choice
        return self.call(cookie or self.cookie, "POST", "/sessions", {**body, **more})

    def only_personal(self) -> None:
        """Revoke the org grant: exactly one candidate, `netops` (personal)."""
        del self.app.grants[self.grant_id]

    def only_org(self) -> None:
        del self.app.profiles[USER][TARGET]

    def counts(self) -> tuple[int, int]:
        return len(self.app.sessions), len(self.app.connections)

    def recorded(self, body: dict) -> tuple:
        conn = self.app.connections[body["session"]["connection_id"]]
        return conn.get("credential_ref"), conn.get("credential_layer")

    def open(self, choice: tuple | None = None) -> str:
        status, body = self.create(choice=choice)
        self.assertEqual(status, 201, body)
        return body["session"]["connection_id"]


# -- 1 --------------------------------------------------------------------------------------------------------

class SeveralCredentialsAndNoChoice(Base):
    def assert_choice_required(self, answer, expected: list[dict]):
        before = self.counts()
        status, body = answer
        self.assertEqual((status, body["error"]["code"]), (409, "credential_choice_required"), body)
        # the protocol fixes no order: compared sorted by layer and reference
        self.assertEqual(sorted(body["error"]["candidates"], key=lambda c: (c["credential_layer"], c["credential_ref"])),
                         expected)
        for candidate in body["error"]["candidates"]:
            self.assertEqual(set(candidate), {"credential_ref", "credential_layer"})   # never a kind, label or secret
        schema = e4._response_schema(_exchange.find_operation(e4.REGISTRY, "POST", "/sessions"), 409)
        self.assertEqual(validate(body, schema, e4.REGISTRY, _exchange.OPENAPI), [])
        self.assertEqual(self.counts(), before)                                         # nothing was opened

    def test_a_personal_and_an_org_candidate_with_no_choice_is_409_with_both(self):
        self.assert_choice_required(self.create(), [{"credential_ref": ORG, "credential_layer": "org"},
                                                    {"credential_ref": "netops", "credential_layer": "personal"}])

    def test_it_is_409_for_a_reuse_too_and_whatever_connections_exist(self):
        want = [{"credential_ref": ORG, "credential_layer": "org"},
                {"credential_ref": "netops", "credential_layer": "personal"}]
        conn = self.open(("netops", "personal"))
        self.assert_choice_required(self.create(connection="reuse"), want)
        self.assert_choice_required(self.create(connection="reuse", connection_id=conn), want)

    def test_a_personal_and_an_org_credential_of_one_name_are_two_candidates(self):
        self.app.org_creds["netops"] = entry("netops", unlock_class="broker_unlocked", stored_by=USER)
        self.grant(USER, ["*"], "netops")
        del self.app.grants[self.grant_id]       # leave only the "*" grant of `netops` beside the personal mapping
        self.assert_choice_required(self.create(), [{"credential_ref": "netops", "credential_layer": "org"},
                                                    {"credential_ref": "netops", "credential_layer": "personal"}])

    def test_two_grants_naming_one_org_reference_are_one_candidate(self):
        self.only_org()
        self.grant(USER, ["id:" + TARGET], ORG)
        status, body = self.create()
        self.assertEqual(status, 201, body)
        self.assertEqual(self.recorded(body), (ORG, "org"))

    def test_a_choice_resolves_and_a_login_is_never_asked(self):
        status, body = self.create(choice=(ORG, "org"))
        self.assertEqual((status, self.recorded(body)), (201, (ORG, "org")))
        login = {"user": "root", "auth": {"kind": "password", "password": "FIXTURE-NOT-A-PASSWORD"}}
        status, body = self.create(login=login)
        self.assertEqual(status, 201, body)
        self.assertEqual(self.recorded(body), (None, None))

    def test_one_candidate_and_no_choice_opens_with_that_credential_recorded(self):
        self.only_personal()
        status, body = self.create()
        self.assertEqual((status, self.recorded(body)), (201, ("netops", "personal")))
        self.grant(USER, ["tag:shell"], ORG)
        del self.app.profiles[USER][TARGET]
        status, body = self.create()
        self.assertEqual((status, self.recorded(body)), (201, (ORG, "org")))

    def test_a_credential_of_another_principal_is_not_a_candidate(self):
        self.only_personal()
        self.app.org_creds["peers"] = entry("peers", unlock_class="broker_unlocked", stored_by=USER)
        self.grant(PEER, ["*"], "peers")
        status, body = self.create()
        self.assertEqual(status, 201, body)

    def test_an_unknown_target_and_a_non_candidate_choice_are_answered_first(self):
        self.assertEqual(self.create(target="nowhere")[1]["error"]["code"], "target_not_found")
        self.assertEqual(self.create(choice=("other", "personal"))[1]["error"]["code"], "invalid_request")


# -- 2 --------------------------------------------------------------------------------------------------------

class ALockedCredentialDoesNotOpen(Base):
    NETOPS_KIND = "password"

    def assert_locked(self, answer):
        before = self.counts()
        status, body = answer
        self.assertEqual((status, body["error"]["code"]), (409, "vault_locked"), body)
        self.assertEqual(self.counts(), before)

    def unlock(self) -> None:
        self.app.unlocked.add(self.cookie)

    def test_a_personal_credential_with_a_secret_needs_this_logins_unlock(self):
        self.only_personal()
        self.assert_locked(self.create())
        self.assert_locked(self.create(choice=("netops", "personal")))
        self.unlock()
        status, body = self.create()
        self.assertEqual((status, self.recorded(body)), (201, ("netops", "personal")))

    def test_the_unlock_belongs_to_the_login_that_made_it(self):
        self.only_personal()
        self.unlock()
        _, other = self.login(USER)
        self.assert_locked(self.create(cookie=other))

    def test_a_personal_credential_with_no_secret_needs_none(self):
        self.only_personal()
        self.app.profiles[USER][TARGET] = "other"                # `other` is a key-less, secret-less entry
        status, body = self.create()
        self.assertEqual((status, self.recorded(body)), (201, ("other", "personal")))
        status, body = self.create(choice=("other", "personal"))
        self.assertEqual(status, 201, body)

    def test_a_broker_unlocked_org_credential_needs_none(self):
        self.only_org()
        self.app.org_creds[ORG] = entry(ORG, "password", unlock_class="broker_unlocked", stored_by=USER)
        status, body = self.create()
        self.assertEqual((status, self.recorded(body)), (201, (ORG, "org")))

    def test_a_choice_of_the_unlocked_kind_decides_not_the_other_candidate(self):
        # both candidates resolve; the choice names the one that needs no unlock, so nothing is locked
        self.app.org_creds[ORG] = entry(ORG, "password", unlock_class="broker_unlocked", stored_by=USER)
        status, body = self.create(choice=(ORG, "org"))
        self.assertEqual(status, 201, body)
        self.assert_locked(self.create(choice=("netops", "personal")))

    def test_a_reuse_of_an_existing_connection_never_needs_one(self):
        self.only_personal()
        self.unlock()
        conn = self.open()
        self.app.unlocked.discard(self.cookie)                   # the vault locks
        status, body = self.create(connection="reuse")
        self.assertEqual((status, body["session"]["connection_id"]), (201, conn))
        status, body = self.create(connection="reuse", connection_id=conn)
        self.assertEqual((status, body["session"]["connection_id"]), (201, conn))

    def test_a_reuse_that_must_open_a_new_connection_needs_one(self):
        self.only_personal()
        self.assert_locked(self.create(connection="reuse"))

    def test_a_connection_id_that_is_no_candidate_is_422_not_locked(self):
        self.only_personal()
        status, body = self.create(connection="reuse", connection_id="conn-99")
        self.assertEqual((status, body["error"]["code"]), (422, "invalid_request"))

    def test_the_choice_required_answer_comes_before_the_lock(self):
        status, body = self.create()
        self.assertEqual((status, body["error"]["code"]), (409, "credential_choice_required"))

    def test_a_supplied_login_touches_no_vault_and_is_never_locked(self):
        self.only_personal()
        login = {"user": "root", "auth": {"kind": "password", "password": "FIXTURE-NOT-A-PASSWORD"}}
        status, body = self.create(login=login)
        self.assertEqual(status, 201, body)

    def test_an_unlock_through_the_api_is_what_opens_it(self):
        self.only_personal()
        self.assert_locked(self.create())
        status, _ = self.call(self.cookie, "POST", "/vault/unlock", {
            "mode": "untrusted", "part1": rules.PART1, "part2": rules.PART2})
        self.assertEqual(status, 204)
        self.assertEqual(self.create()[0], 201)



class AUserUnlockedOrgCredentialNeedsItsAdministratorsUnlock(Base):
    """credential-protection §8.2 (LOCKED): a user-unlocked org credential is usable for the operators granted it while
    its storing administrator holds an unlock, and an unlock belongs to the login that made it (§5.5), so any login
    of that administrator. The caller's own unlock never matters for it. Without it: 409 `org_credential_locked`, its
    own code (ruling 2026-10-30 00:58, DEFAULT), since the operator's passphrase cannot help."""

    NETOPS_KIND = "password"

    def setUp(self):
        super().setUp()
        self.app.org_creds[ORG] = entry(ORG, "password", unlock_class="user_unlocked", stored_by=PEER)
        self.only_org()                                          # the request resolves to (ORG, org) alone

    def assert_org_locked(self, answer):
        before = self.counts()
        status, body = answer
        self.assertEqual((status, body["error"]["code"]), (409, "org_credential_locked"), body)
        self.assertEqual(body["error"]["message"], "the credential's administrator holds no unlock")
        self.assertEqual(set(body["error"]), {"code", "message"})
        schema = e4._response_schema(_exchange.find_operation(e4.REGISTRY, "POST", "/sessions"), 409)
        self.assertEqual(validate(body, schema, e4.REGISTRY, _exchange.OPENAPI), [])
        self.assertEqual(self.counts(), before)

    def admin_unlocks(self) -> str:
        _, cookie = self.login(PEER)
        self.app.unlocked.add(cookie)
        return cookie

    def assert_opens(self, answer):
        status, body = answer
        self.assertEqual(status, 201, body)
        self.assertEqual(self.recorded(body), (ORG, "org"))

    def test_with_no_login_of_the_administrator_unlocked_it_is_409_org_credential_locked(self):
        self.login(PEER)                                          # logged in, not unlocked
        self.assert_org_locked(self.create())
        self.assert_org_locked(self.create(choice=(ORG, "org")))

    def test_the_callers_own_unlock_does_not_open_it(self):
        self.app.unlocked.add(self.cookie)
        self.assert_org_locked(self.create())

    def test_the_callers_own_lock_does_not_close_it(self):
        self.admin_unlocks()
        self.assertNotIn(self.cookie, self.app.unlocked)
        self.assert_opens(self.create())

    def test_any_login_of_the_administrator_will_do(self):
        self.login(PEER)
        self.admin_unlocks()                                      # the second login holds the unlock
        self.assert_opens(self.create())

    def test_another_principals_unlock_does_not_open_it(self):
        _, viewer = self.login(VIEWER)
        self.app.unlocked.add(viewer)
        self.assert_org_locked(self.create())

    def test_it_stops_when_the_administrator_locks(self):
        admin = self.admin_unlocks()
        self.assert_opens(self.create())
        self.assertEqual(self.call(admin, "POST", "/auth/lock")[0], 204)
        self.assert_org_locked(self.create())

    def test_it_stops_when_the_administrator_logs_out(self):
        admin = self.admin_unlocks()
        self.assertEqual(self.call(admin, "POST", "/auth/logout")[0], 204)
        self.assert_org_locked(self.create())

    def test_an_administrator_who_is_the_caller_needs_an_unlock_of_their_own_logins(self):
        self.app.org_creds[ORG]["stored_by"] = USER
        self.assert_org_locked(self.create())
        _, second = self.login(USER)
        self.app.unlocked.add(second)                             # another login of the same administrator
        self.assert_opens(self.create())

    def test_an_entry_with_no_secret_behaves_as_its_class_says(self):
        self.app.org_creds[ORG] = entry(ORG, unlock_class="user_unlocked", stored_by=PEER)
        self.assert_org_locked(self.create())
        self.admin_unlocks()
        self.assert_opens(self.create())

    def test_a_broker_unlocked_credential_needs_no_unlock_at_all(self):
        self.app.org_creds[ORG]["unlock_class"] = "broker_unlocked"
        self.assert_opens(self.create())

    def test_a_reuse_of_its_connection_needs_none(self):
        admin = self.admin_unlocks()
        status, body = self.create()
        conn = body["session"]["connection_id"]
        self.app.unlocked.discard(admin)
        status, body = self.create(connection="reuse")
        self.assertEqual((status, body["session"]["connection_id"]), (201, conn))
        status, body = self.create(connection="reuse", connection_id=conn)
        self.assertEqual((status, body["session"]["connection_id"]), (201, conn))
        self.assert_org_locked(self.create())                     # a new one does

    def test_a_reuse_that_opens_a_new_connection_needs_it(self):
        self.assert_org_locked(self.create(connection="reuse"))

    def test_a_personal_credential_keeps_vault_locked(self):
        self.app.profiles[USER][TARGET] = "netops"               # two resolve now: the choice decides
        self.admin_unlocks()
        status, body = self.create(choice=("netops", "personal"))
        self.assertEqual((status, body["error"]["code"]), (409, "vault_locked"))
        self.assert_opens(self.create(choice=(ORG, "org")))

    def test_a_supplied_login_is_never_asked(self):
        login = {"user": "root", "auth": {"kind": "password", "password": "FIXTURE-NOT-A-PASSWORD"}}
        self.assertEqual(self.create(login=login)[0], 201)

# -- 3 --------------------------------------------------------------------------------------------------------

def wait_for(test: unittest.TestCase, condition, what: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    test.fail(f"never: {what}")


class LoginTrackingIsBounded(rules.Rules):
    def setUp(self):
        super().setUp()
        self.client, self.cookie = self.login(USER)
        self.sid = self.session_id(self.client)

    def attach(self) -> tcc.Attachment:
        attached = self.client.attach(self.sid, 120, 40)
        self.addCleanup(attached.close)
        return attached

    def test_a_client_that_leaves_drops_its_login_entry(self):
        attached = self.attach()
        wait_for(self, lambda: len(self.app.client_logins) == 1, "the attached client's login is tracked")
        self.assertEqual(set(self.app.client_logins.values()), {self.cookie})
        attached.close()
        wait_for(self, lambda: not self.app.clients.get(self.sid), "the server saw the socket leave")
        self.assertEqual(self.app.client_logins, {})

    def test_repeated_attaches_and_disconnects_leave_nothing_behind(self):
        for _ in range(5):
            attached = self.attach()
            wait_for(self, lambda: len(self.app.client_logins) == 1, "tracked")
            attached.close()
            wait_for(self, lambda: not self.app.clients.get(self.sid), "left")
        self.assertEqual(self.app.client_logins, {})
        self.assertEqual(self.app.ticket_logins, {})

    def test_a_removed_client_drops_its_login_entry(self):
        self.attach()
        wait_for(self, lambda: len(self.app.client_logins) == 1, "tracked")
        (client_id,) = self.app.clients[self.sid]
        self.client.remove_client(self.sid, client_id)
        self.assertEqual(self.app.client_logins, {})

    def test_a_session_kill_drops_every_attached_clients_login_entry(self):
        other, _ = self.login(USER)
        self.attach()
        second = other.attach(self.sid, 120, 40)
        self.addCleanup(second.close)
        wait_for(self, lambda: len(self.app.client_logins) == 2, "both tracked")
        self.client.close(self.sid)
        self.assertEqual(self.app.client_logins, {})
        for _ in range(3):                                         # and again, session after session
            sid = self.session_id(self.client)
            late = self.client.attach(sid, 120, 40)
            self.addCleanup(late.close)
            wait_for(self, lambda: len(self.app.client_logins) == 1, "tracked")
            self.client.close(sid)
            self.assertEqual(self.app.client_logins, {})

    def test_another_clients_entry_stays_while_its_socket_does(self):
        other, other_cookie = self.login(USER)
        first = self.attach()
        second = other.attach(self.sid, 120, 40)
        self.addCleanup(second.close)
        wait_for(self, lambda: len(self.app.client_logins) == 2, "both tracked")
        first.close()
        wait_for(self, lambda: len(self.app.client_logins) == 1, "the first left")
        self.assertEqual(set(self.app.client_logins.values()), {other_cookie})

    def test_a_redeemed_ticket_drops_its_entry(self):
        ticket = self.client.ws_ticket(self.sid)["ticket"]
        self.assertIn(ticket, self.app.ticket_logins)
        attached = tcc.Attachment.open(self.client._attach_url(), ticket, self.sid, ssl_context=None, timeout=5)
        self.addCleanup(attached.close)
        self.assertNotIn(ticket, self.app.ticket_logins)

    def test_an_expired_ticket_that_is_rejected_drops_its_entry(self):
        self.app.ticket_ttl = -1
        ticket = self.client.ws_ticket(self.sid)["ticket"]
        with self.assertRaises(tcc.AttachRefused) as refused:
            tcc.Attachment.open(self.client._attach_url(), ticket, self.sid, ssl_context=None, timeout=5)
        self.assertEqual(refused.exception.code, 4001)
        self.assertNotIn(ticket, self.app.ticket_logins)
        self.assertNotIn(ticket, self.app.tickets)

    def test_a_ticket_for_another_session_drops_its_entry(self):
        other_sid = self.session_id(self.client)
        ticket = self.client.ws_ticket(self.sid)["ticket"]
        with self.assertRaises(tcc.AttachRefused) as refused:
            tcc.Attachment.open(self.client._attach_url(), ticket, other_sid, ssl_context=None, timeout=5)
        self.assertEqual(refused.exception.code, 4003)
        self.assertNotIn(ticket, self.app.ticket_logins)

    def test_a_ticket_that_expires_unredeemed_does_not_stay(self):
        self.app.ticket_ttl = -1
        stale = [self.client.ws_ticket(self.sid)["ticket"] for _ in range(20)]
        self.app.ticket_ttl = 30
        live = self.client.ws_ticket(self.sid)["ticket"]           # the next mint sweeps what has expired
        self.assertEqual(set(self.app.ticket_logins), {live})
        self.assertEqual(set(self.app.tickets), {live})
        self.assertFalse(set(stale) & set(self.app.ticket_logins))

    def test_a_live_ticket_is_kept_until_it_is_used_or_expires(self):
        first = self.client.ws_ticket(self.sid)["ticket"]
        second = self.client.ws_ticket(self.sid)["ticket"]
        self.assertEqual(set(self.app.ticket_logins), {first, second})
        attached = tcc.Attachment.open(self.client._attach_url(), first, self.sid, ssl_context=None, timeout=5)
        self.addCleanup(attached.close)
        self.assertEqual(set(self.app.ticket_logins), {second})


# -- 4 --------------------------------------------------------------------------------------------------------

class AReuseTakesOnlyTheResolvedCredentialsConnections(Base):
    """A connection opened with an explicit org choice is another credential's, for a request that resolves to the
    principal's personal mapping."""

    def setUp(self):
        super().setUp()
        self.org = self.open((ORG, "org"))
        self.only_personal()                      # the request now resolves to (netops, personal) alone

    def test_a_reuse_with_no_choice_never_takes_another_credentials_connection(self):
        before = set(self.app.connections)
        status, body = self.create(connection="reuse")
        self.assertEqual(status, 201, body)
        opened = body["session"]["connection_id"]
        self.assertNotEqual(opened, self.org)
        self.assertEqual(set(self.app.connections) - before, {opened})
        self.assertEqual(self.recorded(body), ("netops", "personal"))
        status, again = self.create(connection="reuse")             # and that one is the next reuse's
        self.assertEqual((status, again["session"]["connection_id"]), (201, opened))

    def test_connection_id_of_another_credentials_connection_is_422(self):
        before = self.counts()
        status, body = self.create(connection="reuse", connection_id=self.org)
        self.assertEqual((status, body["error"]["code"]), (422, "invalid_request"))
        self.assertEqual(self.counts(), before)

    def test_the_409_never_lists_another_credentials_connection(self):
        first = self.open()                                          # both resolve to (netops, personal)
        second = self.open()
        status, body = self.create(connection="reuse")
        self.assertEqual((status, body["error"]["code"]), (409, "connection_choice_required"))
        self.assertEqual(body["error"]["connection_ids"], sorted([first, second]))
        self.assertNotIn(self.org, body["error"]["connection_ids"])

    def test_a_connection_of_the_resolved_credential_is_named_by_connection_id(self):
        mine = self.open()
        self.open()
        status, body = self.create(connection="reuse", connection_id=mine)
        self.assertEqual((status, body["session"]["connection_id"]), (201, mine))

    def test_a_connection_opened_under_an_earlier_resolution_is_not_taken_after_the_mapping_changes(self):
        old = self.open()                                            # (netops, personal)
        self.app.profiles[USER][TARGET] = "other"
        status, body = self.create(connection="reuse")
        self.assertEqual(status, 201, body)
        self.assertNotEqual(body["session"]["connection_id"], old)
        self.assertEqual(self.recorded(body), ("other", "personal"))

    def test_the_org_resolution_takes_the_org_connection_and_not_the_personal_one(self):
        personal = self.open()
        self.grant(USER, ["tag:shell"], ORG)
        del self.app.profiles[USER][TARGET]
        status, body = self.create(connection="reuse")
        self.assertEqual((status, body["session"]["connection_id"]), (201, self.org))
        self.assertNotEqual(body["session"]["connection_id"], personal)

    def test_a_target_nothing_resolves_for_reuses_only_connections_with_no_credential(self):
        # the server's canned convention: a target other than NOCRED with no vault candidate opens with none recorded
        del self.app.profiles[USER][TARGET]
        plain = self.open()
        self.assertEqual(self.app.connections[plain].get("credential_ref"), None)
        status, body = self.create(connection="reuse")
        self.assertEqual((status, body["session"]["connection_id"]), (201, plain))
        status, body = self.create(connection="reuse", connection_id=self.org)
        self.assertEqual((status, body["error"]["code"]), (422, "invalid_request"))

    def test_the_answers_of_step_3_come_first(self):
        self.grant(USER, ["tag:shell"], ORG)                         # two resolve again
        status, body = self.create(connection="reuse", connection_id=self.org)
        self.assertEqual((status, body["error"]["code"]), (409, "credential_choice_required"))
        status, body = self.create(target=NOCRED, connection="reuse")
        self.assertEqual((status, body["error"]["code"]), (409, "no_resolving_credential"))


if __name__ == "__main__":
    unittest.main()
