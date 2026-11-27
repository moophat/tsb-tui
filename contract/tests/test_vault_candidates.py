"""The credential choice on `POST /sessions` in the fixture server (Qodo round 1 on #107, finding 4; rulings
2026-10-29 23:36). session-open step 3: the candidates for a principal and a target are

- the layer `personal`: the reference the principal's own Layer 2a mapping gives for the target, when the principal's
  vault holds it;
- the layer `org`: an org credential named by a grant to the principal whose selectors match the target.

A choice, `credential_ref` with `credential_layer`, that is a candidate is accepted and carried into the connection
the create opens. Anything else, stale, forged, absent, another principal's, a grant whose selectors do not match the
target, is 422 `invalid_request`, and nothing is opened. A selector is Storage §9's vocabulary and matches as the core's
RBAC filter does: `*` every target; `tag:X` a tag the target carries; `id:X` exactly that id; `host:pattern` Go's
`path.Match` over the target's id and then, when it has one, its host.

A choice on a `reuse` narrows the reuse candidates to the principal's connections to the target opened with that
credential (session-open.md, "Candidate": "with the same credential"): a `connection_id` of another credential is
422, the 409 `connection_choice_required` lists only the matching ones, and with none a new connection opens with the
choice (ruling 2026-10-29 23:52). A valid choice on the no-credential target is a candidate, so it resolves and is not
refused 409.
"""

import unittest

from tsbc._serve import NOCRED, PEER, TARGET, USER

from . import test_vault_serve_rules as rules

ORG = "fleet-backup"


def entry(reference: str, **more) -> dict:
    return {"reference": reference, "kind": "none", **more}


class Choice(rules.Rules):
    def setUp(self):
        super().setUp()
        _, self.cookie = self.login(USER)
        self.app.creds[USER] = {"netops": entry("netops"), "other": entry("other")}
        self.app.profiles[USER] = {TARGET: "netops"}
        self.app.org_creds[ORG] = entry(ORG, unlock_class="broker_unlocked", stored_by=USER)
        self.grant_id = self.grant(USER, ["tag:shell"], ORG)

    def grant(self, user: str, selectors: list[str], ref: str) -> str:
        gid = f"g-{len(self.app.grants) + 1}"
        self.app.grants[gid] = {"grant_id": gid, "user": user, "selectors": selectors, "credential_ref": ref,
                                "granted_by": USER, "granted_at": "2026-10-29T08:00:00Z"}
        return gid

    def create(self, target: str = TARGET, choice: tuple | None = None, connection: str = "new", **more):
        body = {"target_id": target, "connection": connection, "term": "xterm-256color", "cols": 120, "rows": 40}
        if choice is not None:
            body["credential_ref"], body["credential_layer"] = choice
        return self.call(self.cookie, "POST", "/sessions", {**body, **more})

    def assert_accepted(self, answer, choice: tuple | None):
        status, body = answer
        self.assertEqual(status, 201, body)
        conn = self.app.connections[self.app.sessions[body["session"]["id"]]["connection_id"]]
        self.assertEqual((conn.get("credential_ref"), conn.get("credential_layer")), choice or (None, None))

    def assert_refused(self, answer, what: str = ""):
        before = (len(self.app.sessions), len(self.app.connections))
        status, body = answer
        self.assertEqual(status, 422, f"{what}: {body}")
        self.assertEqual(body["error"]["code"], "invalid_request", what)
        self.assertEqual((len(self.app.sessions), len(self.app.connections)), before, what)   # nothing was opened


class AcceptedChoices(Choice):
    def test_a_mapped_personal_credential_is_accepted_and_enters_the_connection(self):
        self.assert_accepted(self.create(choice=("netops", "personal")), ("netops", "personal"))

    def test_a_granted_org_credential_is_accepted_and_enters_the_connection(self):
        self.assert_accepted(self.create(choice=(ORG, "org")), (ORG, "org"))

    def test_a_personal_and_an_org_credential_of_one_name_are_two_candidates(self):
        self.app.org_creds["netops"] = entry("netops", unlock_class="broker_unlocked", stored_by=USER)
        self.grant(USER, ["*"], "netops")
        self.assert_accepted(self.create(choice=("netops", "org")), ("netops", "org"))
        self.assert_accepted(self.create(choice=("netops", "personal")), ("netops", "personal"))

    def test_no_choice_opens_a_target_that_resolves_to_one_credential_and_records_it(self):
        del self.app.grants[self.grant_id]                # one candidate is left: netops
        self.assert_accepted(self.create(), ("netops", "personal"))


class ReuseWithAChoice(Choice):
    """session-open.md: a reuse candidate is the principal's own connection, to this target, "with the same
    credential". A choice therefore narrows the candidates to the connections opened with the chosen credential
    (Contract §12: a choice accepted and then ignored would be a silent precedence)."""

    def open(self, choice: tuple | None) -> str:
        """A connection of USER to TARGET opened with `choice` (None: with none recorded); its id."""
        status, body = self.create(choice=choice)
        self.assertEqual(status, 201, body)
        return body["session"]["connection_id"]

    def setUp(self):
        super().setUp()
        self.netops = self.open(("netops", "personal"))
        self.org = self.open((ORG, "org"))
        # a connection with no credential recorded: one opened while nothing resolved for the target
        grant, mapping = self.app.grants.pop(self.grant_id), self.app.profiles[USER].pop(TARGET)
        self.plain = self.open(None)
        self.app.grants[self.grant_id], self.app.profiles[USER][TARGET] = grant, mapping

    def test_the_only_connection_with_the_chosen_credential_is_the_one_reused(self):
        status, body = self.create(choice=("netops", "personal"), connection="reuse")
        self.assertEqual((status, body["session"]["connection_id"]), (201, self.netops))
        status, body = self.create(choice=(ORG, "org"), connection="reuse")
        self.assertEqual((status, body["session"]["connection_id"]), (201, self.org))

    def test_a_connection_id_opened_with_another_credential_is_not_a_candidate(self):
        for conn in (self.org, self.plain):
            self.assert_refused(self.create(choice=("netops", "personal"), connection="reuse", connection_id=conn),
                                f"connection_id {conn}")
        status, body = self.create(choice=("netops", "personal"), connection="reuse", connection_id=self.netops)
        self.assertEqual((status, body["session"]["connection_id"]), (201, self.netops))

    def test_the_409_lists_only_the_connections_opened_with_the_chosen_credential(self):
        second = self.open(("netops", "personal"))
        status, body = self.create(choice=("netops", "personal"), connection="reuse")
        self.assertEqual((status, body["error"]["code"]), (409, "connection_choice_required"))
        self.assertEqual(body["error"]["connection_ids"], sorted([self.netops, second]))
        status, body = self.create(connection="reuse")            # no choice, two credentials resolve: step 3 asks first
        self.assertEqual((status, body["error"]["code"]), (409, "credential_choice_required"))

    def test_with_no_connection_of_that_credential_a_new_one_opens_with_it(self):
        self.app.creds[USER]["third"] = entry("third")
        self.app.profiles[USER][TARGET] = "third"
        before = set(self.app.connections)
        status, body = self.create(choice=("third", "personal"), connection="reuse")
        self.assertEqual(status, 201, body)
        opened = body["session"]["connection_id"]
        self.assertEqual(set(self.app.connections) - before, {opened})
        self.assertEqual((self.app.connections[opened]["credential_ref"], self.app.connections[opened]["credential_layer"]),
                         ("third", "personal"))
        status, again = self.create(choice=("third", "personal"), connection="reuse")      # and that one is now reused
        self.assertEqual((status, again["session"]["connection_id"]), (201, opened))

    def test_a_choice_that_is_no_candidate_is_still_422_on_a_reuse(self):
        self.assert_refused(self.create(choice=("other", "personal"), connection="reuse"))
        self.assert_refused(self.create(choice=("ghost", "org"), connection="reuse", connection_id=self.org))


class RefusedChoices(Choice):
    def test_a_personal_credential_that_is_stored_but_not_mapped_to_this_target_is_refused(self):
        self.assert_refused(self.create(choice=("other", "personal")))

    def test_a_personal_credential_mapped_for_another_target_is_refused(self):
        self.app.profiles[USER]["normal"] = "other"
        self.assert_refused(self.create(choice=("other", "personal")))

    def test_a_personal_credential_that_is_not_stored_is_refused(self):
        self.assert_refused(self.create(choice=("ghost", "personal")))
        self.assert_refused(self.create(choice=("supplied", "personal")))

    def test_another_principals_personal_credential_is_refused(self):
        self.app.creds[PEER] = {"peers": entry("peers")}
        self.app.profiles[PEER] = {TARGET: "peers"}
        self.assert_refused(self.create(choice=("peers", "personal")))

    def test_an_org_credential_with_no_grant_is_refused(self):
        self.app.org_creds["ungranted"] = entry("ungranted", unlock_class="broker_unlocked", stored_by=USER)
        self.assert_refused(self.create(choice=("ungranted", "org")))

    def test_an_org_credential_granted_to_another_principal_is_refused(self):
        self.app.org_creds["peers-only"] = entry("peers-only", unlock_class="broker_unlocked", stored_by=USER)
        self.grant(PEER, ["*"], "peers-only")
        self.assert_refused(self.create(choice=("peers-only", "org")))

    def test_a_grant_whose_selectors_do_not_match_the_target_is_refused(self):
        self.assert_refused(self.create(target="normal", choice=(ORG, "org")))     # tags: scenario, not shell

    def test_a_revoked_grant_is_stale_and_refused(self):
        gid = next(iter(self.app.grants))
        self.assert_accepted(self.create(choice=(ORG, "org")), (ORG, "org"))
        del self.app.grants[gid]
        self.assert_refused(self.create(choice=(ORG, "org")))

    def test_a_deleted_credential_is_stale_and_refused(self):
        del self.app.creds[USER]["netops"]
        del self.app.profiles[USER][TARGET]
        self.assert_refused(self.create(choice=("netops", "personal")))

    def test_the_wrong_layer_is_refused(self):
        self.assert_refused(self.create(choice=("netops", "org")))
        self.assert_refused(self.create(choice=(ORG, "personal")))

    def test_a_choice_beside_a_login_is_still_422_by_shape(self):
        login = {"user": "root", "auth": {"kind": "password", "password": "FIXTURE-NOT-A-PASSWORD"}}
        self.assert_refused(self.create(choice=("netops", "personal"), login=login))

    def test_a_dangling_mapping_is_still_409_whatever_the_choice(self):
        self.app.profiles[USER][TARGET] = "gone"
        status, body = self.create(choice=(ORG, "org"))
        self.assertEqual((status, body["error"]["code"], body["error"]["credential_ref"]),
                         (409, "mapped_credential_missing", "gone"))

    def test_an_unknown_target_is_still_404_first(self):
        status, body = self.create(target="nowhere", choice=("ghost", "personal"))
        self.assertEqual((status, body["error"]["code"]), (404, "target_not_found"))


class TheNoCredentialTarget(Choice):
    def test_it_is_still_refused_409_with_no_choice(self):
        status, body = self.create(target=NOCRED)
        self.assertEqual((status, body["error"]["code"]), (409, "no_resolving_credential"))

    def test_a_mapped_personal_choice_resolves_and_is_not_refused(self):
        self.app.profiles[USER][NOCRED] = "netops"
        self.assert_accepted(self.create(target=NOCRED, choice=("netops", "personal")), ("netops", "personal"))

    def test_a_matching_grant_resolves_and_is_not_refused(self):
        self.grant(USER, ["tag:nocred"], "fleet-nocred")
        self.app.org_creds["fleet-nocred"] = entry("fleet-nocred", unlock_class="broker_unlocked", stored_by=USER)
        self.assert_accepted(self.create(target=NOCRED, choice=("fleet-nocred", "org")), ("fleet-nocred", "org"))

    def test_a_choice_that_is_no_candidate_is_422_not_409(self):
        self.assert_refused(self.create(target=NOCRED, choice=("netops", "personal")))
        self.assert_refused(self.create(target=NOCRED, choice=(ORG, "org")))     # shell, not nocred


# (selector, the target's id, its host or None, its tags, whether it is a candidate)
SELECTORS = (
    ("*", "any-id", None, [], True),
    ("tag:shell", "any-id", None, ["lab", "shell"], True),
    ("tag:shel", "any-id", None, ["shell"], False),
    ("tag:Shell", "any-id", None, ["shell"], False),
    ("tag:shell", "any-id", None, [], False),
    ("id:staging-web", "staging-web", "192.0.2.50", [], True),
    ("id:staging-*", "staging-web", "192.0.2.50", [], False),               # id: is exact equality, not a glob
    ("id:staging-web", "staging-web2", "192.0.2.50", [], False),
    ("id:192.0.2.50", "staging-web", "192.0.2.50", [], False),              # id: never reads the host
    ("host:staging-*", "staging-web", "192.0.2.50", [], True),             # a pattern over the id
    ("host:staging-*", "web-staging", "192.0.2.50", [], False),
    ("host:staging-*", "staging-web", None, [], True),                      # the id answers when there is no host
    ("host:192.0.2.5*", "web", "192.0.2.50", [], True),                     # a pattern over the host
    ("host:192.0.2.5?", "web", "192.0.2.50", [], True),
    ("host:192.0.2.5?", "web", "192.0.2.500", [], False),
    ("host:192.0.2.5*", "web", None, [], False),                            # no host, and the id does not match
    ("host:192.0.2.5*", "web", "", [], False),
    ("host:lab*", "lab/web-1", "192.0.2.50", [], False),                    # a * never crosses a /
    ("host:lab/*", "lab/web-1", "192.0.2.50", [], True),
    ("host:lab/*", "lab/a/b", "192.0.2.50", [], False),
    ("host:?eb-1", "lab/web-1", None, [], False),
    ("host:lab/?eb-1", "lab/web-1", None, [], True),
    ("host:*", "lab/web-1", None, [], False),                               # id has a /, and there is no host
    ("host:*", "lab/web-1", "192.0.2.50", [], True),                        # the host has none
    ("host:192.0.2.[1-5]0", "web", "192.0.2.30", [], True),                 # a class
    ("host:192.0.2.[1-5]0", "web", "192.0.2.60", [], False),
    ("host:192.0.2.[^1-5]0", "web", "192.0.2.60", [], True),                # a negated class
    ("host:192.0.2.[^1-5]0", "web", "192.0.2.30", [], False),
    ("host:[", "web", "192.0.2.50", [], False),                             # an invalid pattern matches nothing
    ("host:192.0.2.5[", "192.0.2.5", "192.0.2.5", [], False),
    ("host:[]a]", "]", "]", [], False),
    ("host:[a-b-c]", "a", "a", [], False),
    ("host:web\\", "web", "web", [], False),
)


class SelectorsMatchAsTheCoresFilterDoes(Choice):
    def test_each_selector_against_each_target(self):
        for selector, tid, host, tags, expected in SELECTORS:
            with self.subTest(selector=selector, id=tid, host=host):
                record = {"id": tid, "type": "ssh", "name": tid, "group": "lab", "tags": tags,
                          "discovered_by": "manual", "last_seen": "2026-10-17T08:30:00Z", "port": 22}
                if host is not None:
                    record["host"] = host
                self.app.targets[tid] = record
                self.app.grants.clear()
                self.grant(USER, [selector], ORG)
                answer = self.create(target=tid, choice=(ORG, "org"))
                if expected:
                    self.assert_accepted(answer, (ORG, "org"))
                else:
                    self.assert_refused(answer, selector)

    def test_a_grant_is_a_candidate_when_any_one_selector_matches(self):
        self.app.grants.clear()
        self.grant(USER, ["host:[", "id:nowhere", "tag:shell"], ORG)       # a bad pattern costs the grant nothing else
        self.assert_accepted(self.create(choice=(ORG, "org")), (ORG, "org"))

    def test_two_grants_of_one_reference_are_one_candidate(self):
        self.grant(USER, ["*"], ORG)
        self.assert_accepted(self.create(choice=(ORG, "org")), (ORG, "org"))


if __name__ == "__main__":
    unittest.main()
