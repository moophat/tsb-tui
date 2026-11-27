"""`ApiError.detail`: the error body's members beside `code` and `message` (ruling 2026-10-31 13:32).

The published client dropped every member of a REST error body except `code` and `message`, while its own
`create_session` docstring sent the caller to the body's `candidates`. The contract's error bodies carry more:
`credential_choice_required` carries `candidates`, `connection_choice_required` carries `connection_ids`,
`unlock_failed` carries `attempts_remaining`, `vault_wipe_blocked` carries `credential_refs`, and
`mapped_credential_missing` carries `credential_ref` (`schemas/error.schema.json`). Each test compares the client's
`detail` with the body the served app answers, and asserts `str(err)` is what it was before.

A supplied login and a password never enter an exception: the error bodies the contract defines carry none, and no
path copies the request into `detail`.
"""

import json
import unittest

import tsb_contract_client as tcc
from tsbc._serve import PASSWORD, TARGET, USER, ServeApp

from .stub_server import StubServer

PART1 = "RklYVFVSRS1QQVJUMS0wMA=="
PART2 = "RklYVFVSRS1QQVJUMi0wMA=="
ORG = "fleet-backup"
ATTEMPTS = 2


def entry(reference: str, **more) -> dict:
    return {"reference": reference, "kind": "none", **more}


class UnlockFailedApp(ServeApp):
    """`tsbc serve` answers no `unlock_failed`: this serves one, with `attempts_remaining`."""

    def http(self, method, path, headers, body):
        if method == "POST" and path == "/vault/unlock":
            return 409, [], {"error": {"code": "unlock_failed", "message": "wrong passphrase",
                                       "attempts_remaining": ATTEMPTS}}
        return super().http(method, path, headers, body)


class NotJsonApp(ServeApp):
    """A gateway's answer: a non-JSON body with the status."""

    def http(self, method, path, headers, body):
        if method == "GET" and path == "/auth/me":
            return 502, [("Content-Type", "text/html")], b"<html>bad gateway</html>"
        return super().http(method, path, headers, body)


class DetailFromTheErrorBody(unittest.TestCase):
    def setUp(self):
        self.app = ServeApp()
        self.app.creds[USER] = {"netops": entry("netops")}
        self.app.profiles[USER] = {TARGET: "netops"}
        self.app.org_creds[ORG] = entry(ORG, unlock_class="broker_unlocked", stored_by=USER)
        self.app.grants["g-1"] = {"grant_id": "g-1", "user": USER, "selectors": ["tag:shell"],
                                   "credential_ref": ORG, "granted_by": USER, "granted_at": "2026-10-29T08:00:00Z"}
        server = StubServer(self.app)
        self.url = server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        before = set(self.app.logins)
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)
        (self.cookie,) = set(self.app.logins) - before

    def served(self, method: str, path: str, body=None, cookie: str | None = None) -> tuple[int, dict]:
        """The answer the app itself gives to this request, read from the server's own table."""
        headers = {} if cookie is None else {"cookie": f"tsb_session={cookie}"}
        status, _extra, reply = self.app.http(method, path, headers, body)
        return status, reply

    def test_the_409_candidates_arrive_in_detail_as_they_were_served(self):
        create = {"target_id": TARGET, "connection": "new", "term": "xterm-256color", "cols": 120, "rows": 40}
        status, served = self.served("POST", "/sessions", create, cookie=self.cookie)
        self.assertEqual((status, served["error"]["code"]), (409, "credential_choice_required"))
        with self.assertRaises(tcc.ApiError) as raised:
            self.client.create_session(TARGET, "new", "xterm-256color", 120, 40)
        err = raised.exception
        self.assertEqual((err.status, err.code), (409, "credential_choice_required"))
        self.assertEqual(err.detail["candidates"], served["error"]["candidates"])
        self.assertNotIn("code", err.detail)
        self.assertNotIn("message", err.detail)
        self.assertEqual(str(err), "HTTP 409 credential_choice_required: choose a credential")

    def test_the_unlocks_attempts_remaining_arrives_in_detail(self):
        app = UnlockFailedApp()
        server = StubServer(app)
        url = server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        client = tcc.Client(url, timeout=5)
        client.login(USER, PASSWORD)
        with self.assertRaises(tcc.ApiError) as raised:
            client.vault_unlock_untrusted(PART1, PART2)
        err = raised.exception
        self.assertEqual((err.status, err.code), (409, "unlock_failed"))
        self.assertEqual(err.detail["attempts_remaining"], ATTEMPTS)
        self.assertEqual(str(err), "HTTP 409 unlock_failed: wrong passphrase")

    def test_a_401_with_no_extra_members_has_an_empty_detail(self):
        status, served = self.served("GET", "/auth/me")
        self.assertEqual((status, served["error"]["code"]), (401, "unauthenticated"))
        self.assertEqual(sorted(served["error"]), ["code", "message"])
        client = tcc.Client(self.url, timeout=5)
        with self.assertRaises(tcc.ApiError) as raised:
            client.me()
        err = raised.exception
        self.assertEqual((err.status, err.code), (401, "unauthenticated"))
        self.assertEqual(err.detail, {})
        self.assertEqual(str(err), "HTTP 401 unauthenticated: log in first")

    def test_a_non_json_error_body_has_an_empty_detail(self):
        app = NotJsonApp()
        server = StubServer(app)
        url = server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        client = tcc.Client(url, timeout=5)
        client.login(USER, PASSWORD)
        with self.assertRaises(tcc.ApiError) as raised:
            client.me()
        err = raised.exception
        self.assertEqual((err.status, err.code), (502, None))
        self.assertEqual(err.detail, {})
        self.assertEqual(str(err), "HTTP 502 : response body is not JSON")

    def test_a_supplied_login_never_enters_an_exception(self):
        login = {"user": "root", "auth": {"kind": "password", "password": PASSWORD}}
        self.app.targets[TARGET]["type"] = "telnet"      # 422: a login is for ssh targets only
        with self.assertRaises(tcc.ApiError) as raised:
            self.client.create_session(TARGET, "new", "xterm-256color", 120, 40, login=login)
        err = raised.exception
        self.assertEqual((err.status, err.code), (422, "invalid_request"))
        self.assertEqual(err.detail, {})
        self.assertNotIn(PASSWORD, str(err))
        self.assertNotIn(PASSWORD, json.dumps(err.detail))


if __name__ == "__main__":
    unittest.main()
