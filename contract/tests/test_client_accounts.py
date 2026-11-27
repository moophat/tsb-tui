"""The client's six account methods against the fixture server (E4b, account-administration).

Each test runs the served `tsbc serve` app through StubServer with the thin client over it, as
test_client_error_detail.py does: the methods are the client's, the answers and refusals are the
served contract's. The last test holds every refusal's ApiError against the passwords sent.
"""

import json
import unittest

import tsb_contract_client as tcc
from tsbc._serve import PASSWORD, PEER, USER, VIEWER, ServeApp

from .stub_server import StubServer

FIRST = "client-account-first-pw"    # every new password meets the configured minimum (15)
SECOND = "client-account-second-pw"
OWN_NEW = "client-account-own-new-pw"


class ClientAccounts(unittest.TestCase):
    def setUp(self):
        self.app = ServeApp()
        server = StubServer(self.app)
        self.url = server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        self.admin = tcc.Client(self.url, timeout=5)
        self.admin.login(USER, PASSWORD)
        self.peer = tcc.Client(self.url, timeout=5)
        self.peer.login(PEER, PASSWORD)

    # -- the six methods --

    def test_accounts_lists_every_account_ordered_by_username(self):
        accounts = self.admin.accounts()
        names = [a["username"] for a in accounts]
        self.assertEqual(names, sorted(names))
        self.assertTrue({USER, PEER, VIEWER} <= set(names))
        for account in accounts:
            self.assertEqual(set(account), {"username", "created_at", "disabled"})

    def test_create_answers_the_record_and_grants_no_rights(self):
        answer = self.admin.create_account("client-made", FIRST)
        self.assertEqual(answer["account"]["username"], "client-made")
        self.assertFalse(answer["account"]["disabled"])
        self.assertEqual(answer["permissions"], [])
        self.assertNotIn(FIRST, json.dumps(answer))
        self.assertIn("client-made", [a["username"] for a in self.admin.accounts()])
        fresh = tcc.Client(self.url, timeout=5)
        fresh.login("client-made", FIRST)  # created enabled, usable at once
        self.assertEqual(fresh.me()["username"], "client-made")

    def test_disable_ends_the_logins_and_is_idempotent(self):
        self.admin.create_account("client-cycle", FIRST)
        user = tcc.Client(self.url, timeout=5)
        user.login("client-cycle", FIRST)
        self.assertIsNone(self.admin.disable_account("client-cycle"))
        with self.assertRaises(tcc.ApiError) as ctx:
            user.me()  # every login of the account ended before the 204
        self.assertEqual((ctx.exception.status, ctx.exception.code), (401, "unauthenticated"))
        self.assertIsNone(self.admin.disable_account("client-cycle"))  # already disabled: 204 again

    def test_enable_sets_a_new_password_never_the_old_one(self):
        self.admin.create_account("client-cycle", FIRST)
        self.admin.disable_account("client-cycle")
        self.assertIsNone(self.admin.enable_account("client-cycle", SECOND))
        tcc.Client(self.url, timeout=5).login("client-cycle", SECOND)
        with self.assertRaises(tcc.ApiError) as ctx:  # the old hash was voided, not restored
            tcc.Client(self.url, timeout=5).login("client-cycle", FIRST)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (401, "invalid_credentials"))

    def test_set_account_password_ends_logins_and_moves_the_login(self):
        self.admin.create_account("client-pw", FIRST)
        user = tcc.Client(self.url, timeout=5)
        user.login("client-pw", FIRST)
        self.assertIsNone(self.admin.set_account_password("client-pw", SECOND))
        with self.assertRaises(tcc.ApiError):
            user.me()
        with self.assertRaises(tcc.ApiError):
            tcc.Client(self.url, timeout=5).login("client-pw", FIRST)
        tcc.Client(self.url, timeout=5).login("client-pw", SECOND)

    def test_change_own_password_keeps_the_caller_and_ends_the_others(self):
        self.admin.create_account("client-own", FIRST)
        first = tcc.Client(self.url, timeout=5)
        first.login("client-own", FIRST)
        second = tcc.Client(self.url, timeout=5)
        second.login("client-own", FIRST)
        self.assertIsNone(first.change_own_password(FIRST, SECOND))
        self.assertEqual(first.me()["username"], "client-own")  # the caller's login stays
        with self.assertRaises(tcc.ApiError):
            second.me()  # every other login of the user ends
        tcc.Client(self.url, timeout=5).login("client-own", SECOND)

    # -- refusals keep the contract's status and code --

    def test_every_method_without_a_login_is_unauthenticated(self):
        fresh = tcc.Client(self.url, timeout=5)
        calls = {
            "accounts": fresh.accounts,
            "create": lambda: fresh.create_account("client-x", FIRST),
            "disable": lambda: fresh.disable_account("client-x"),
            "enable": lambda: fresh.enable_account("client-x", FIRST),
            "set": lambda: fresh.set_account_password("client-x", FIRST),
            "own": lambda: fresh.change_own_password(FIRST, SECOND),
        }
        for name, call in calls.items():
            with self.subTest(call=name), self.assertRaises(tcc.ApiError) as ctx:
                call()
            self.assertEqual((ctx.exception.status, ctx.exception.code), (401, "unauthenticated"))

    def test_the_five_administrator_operations_need_account_admin(self):
        calls = {
            "list": self.peer.accounts,
            "create": lambda: self.peer.create_account("client-x", FIRST),
            "disable": lambda: self.peer.disable_account("client-x"),
            "enable": lambda: self.peer.enable_account("client-x", FIRST),
            "set": lambda: self.peer.set_account_password("client-x", FIRST),
        }
        for name, call in calls.items():
            with self.subTest(call=name), self.assertRaises(tcc.ApiError) as ctx:
                call()
            self.assertEqual((ctx.exception.status, ctx.exception.code), (403, "forbidden"))
        # Own change is every valid login's, with no permission: the peer proves its password and
        # keeps its login.
        self.assertIsNone(self.peer.change_own_password(PASSWORD, OWN_NEW))
        self.assertEqual(self.peer.me()["username"], PEER)

    def test_state_refusals_keep_status_and_code(self):
        self.admin.create_account("client-state", FIRST)
        with self.assertRaises(tcc.ApiError) as ctx:
            self.admin.create_account("client-state", FIRST)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "user_exists"))
        with self.assertRaises(tcc.ApiError) as ctx:
            self.admin.disable_account(USER)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "account_self_disable"))
        with self.assertRaises(tcc.ApiError) as ctx:
            self.admin.enable_account("client-state", FIRST)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "account_already_enabled"))
        self.admin.disable_account("client-state")
        with self.assertRaises(tcc.ApiError) as ctx:
            self.admin.set_account_password("client-state", SECOND)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "account_disabled"))
        for name in ("client-absent", "client.state"):
            for call in (lambda n=name: self.admin.disable_account(n),
                         lambda n=name: self.admin.enable_account(n, FIRST),
                         lambda n=name: self.admin.set_account_password(n, FIRST)):
                with self.subTest(name=name), self.assertRaises(tcc.ApiError) as ctx:
                    call()
                self.assertEqual((ctx.exception.status, ctx.exception.code), (404, "user_not_found"))

    def test_input_outside_the_rules_is_invalid_request(self):
        calls = (
            lambda: self.admin.create_account("client-shape", "short"),
            lambda: self.admin.create_account("client-shape", "x" * 1025),
            lambda: self.admin.create_account("bad/name", FIRST),
            lambda: self.admin.enable_account("client-shape", "short"),
            lambda: self.admin.set_account_password("client-shape", "short"),
            lambda: self.peer.change_own_password(PASSWORD, "short"),
        )
        for call in calls:
            with self.assertRaises(tcc.ApiError) as ctx:
                call()
            self.assertEqual((ctx.exception.status, ctx.exception.code), (422, "invalid_request"))

    def test_a_wrong_current_password_is_password_change_failed_never_a_lost_login(self):
        with self.assertRaises(tcc.ApiError) as ctx:
            self.peer.change_own_password("client-wrong-current-pw", OWN_NEW)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (403, "password_change_failed"))
        self.assertEqual(self.peer.me()["username"], PEER)  # the login stands; do not treat it as 401

    def test_no_password_appears_in_any_apierror_text_or_repr(self):
        first = "client-echo-first-pw"
        self.admin.create_account("client-echo", first)
        self.admin.disable_account("client-echo")
        dup = "client-echo-dup-pw"
        enable = "client-echo-enable-pw"
        setpw = "client-echo-set-pw"
        wrong = "client-echo-wrong-pw"
        fresh = "client-echo-fresh-pw"
        cases = (
            ("duplicate create", (dup,), (409, "user_exists"),
             lambda: self.admin.create_account("client-echo", dup)),
            ("enable an enabled account", (enable,), (409, "account_already_enabled"),
             lambda: self.admin.enable_account(USER, enable)),
            ("set on a disabled account", (setpw,), (409, "account_disabled"),
             lambda: self.admin.set_account_password("client-echo", setpw)),
            ("wrong current password", (wrong, OWN_NEW), (403, "password_change_failed"),
             lambda: self.peer.change_own_password(wrong, OWN_NEW)),
            ("password below the minimum", ("echo-short",), (422, "invalid_request"),
             lambda: self.admin.create_account("client-echo", "echo-short")),
            ("no login at all", (fresh,), (401, "unauthenticated"),
             lambda: tcc.Client(self.url, timeout=5).create_account("client-echo", fresh)),
        )
        for name, values, expected, call in cases:
            with self.subTest(refusal=name), self.assertRaises(tcc.ApiError) as ctx:
                call()
            err = ctx.exception
            self.assertEqual((err.status, err.code), expected)
            for value in values:
                self.assertNotIn(value, str(err))
                self.assertNotIn(value, repr(err))
                self.assertNotIn(value, json.dumps(err.detail))


if __name__ == "__main__":
    unittest.main()
