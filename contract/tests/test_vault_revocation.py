"""The fixture server's revocation and challenge rules (Qodo round 1 on #107, findings 1, 2, 9, 10 and 12).

`tsbc serve` answers the vault release from in-memory state. These tests hold the rules that state must keep, each
one a case the first version got wrong:

- a revocation ends only the logins of the principal whose device record it revokes, never another principal's login
  tied to a device with the same id (the devices bucket is keyed by user and device);
- under the default policy (`end_logins`) the revoked logins' attached clients close with 4015 `device_revoked`;
- a vault wipe ends the logins tied to the wiped principal's devices, as one revocation does;
- a challenge issued before a revocation (or a wipe) and answered after it is refused 409 `challenge_failed`: bind,
  trusted unlock and Part 2 regeneration each re-check the device is still trusted;
- the `login_handle` a challenge returns comes from fresh randomness, never from the session cookie.
"""

import unittest

import tsb_contract_client as tcc
from tsbc._serve import PASSWORD, PEER, TARGET, USER, ServeApp

from .stub_server import StubServer

PART1 = "RklYVFVSRS1QQVJUMS0wMA=="
PART2 = "RklYVFVSRS1QQVJUMi0wMA=="
SIG = "RklYVFVSRS1TSUdOQVRVUkUtMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMA=="
DEV = "FIXTURE-DEVICE-ID-" + "A" * 25
RECORD = {"device_id": DEV, "device_name": "Work laptop", "registered_at": "2026-10-29T08:00:00Z",
          "last_used_at": "2026-10-29T08:00:00Z"}


class Harness(unittest.TestCase):
    def setUp(self):
        self.app = ServeApp()
        self.app.vault_users.add(PEER)
        self.app.devices[(USER, DEV)] = dict(RECORD)
        self.app.devices[(PEER, DEV)] = dict(RECORD)   # one browser profile, registered by both principals
        server = StubServer(self.app)
        self.url = server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)

    def login(self, user: str) -> tuple[tcc.Client, str]:
        """A logged-in client and its session cookie's value (read from the server's own table)."""
        before = set(self.app.logins)
        client = tcc.Client(self.url, timeout=5)
        client.login(user, PASSWORD)
        (cookie,) = set(self.app.logins) - before
        return client, cookie

    def tied_login(self, user: str) -> tuple[tcc.Client, str]:
        client, cookie = self.login(user)
        client.bind_login(client.device_challenge("login.bind", DEV)["challenge_id"], SIG)
        self.assertEqual(self.app.tied[cookie], DEV)
        return client, cookie

    def session_id(self, client: tcc.Client) -> str:
        return client.create_session(TARGET, "new", "xterm-256color", 120, 40)["session"]["id"]

    @staticmethod
    def closed_by_server(attachment: tcc.Attachment) -> bool:
        """Read past the attach's canned output: True when the server's close arrives, False when it stays quiet."""
        try:
            while attachment.recv(1) is not None:
                pass
        except TimeoutError:
            return False
        return True

    def refused(self, call) -> str | None:
        with self.assertRaises(tcc.ApiError) as raised:
            call()
        return raised.exception.code


class RevocationEndsOnlyItsPrincipalsLogins(Harness):
    def test_a_revocation_leaves_another_principals_login_on_the_same_device_id(self):
        mine, _ = self.tied_login(USER)
        theirs, _ = self.tied_login(PEER)
        other, _ = self.login(USER)          # an untied login of the revoking principal makes the call
        other.revoke_device(DEV)
        self.assertEqual(self.refused(mine.me), "unauthenticated")                # the revoked device's own login ends
        self.assertEqual(theirs.me()["username"], PEER)                           # the peer's login does not
        self.assertEqual(theirs.device_challenge("vault.unlock", DEV)["expires_in"], 30)   # and is still tied
        self.assertNotIn("revoked_at", self.app.devices[(PEER, DEV)])

    def test_a_revocation_ends_every_login_of_that_principal_tied_to_the_device(self):
        first, _ = self.tied_login(USER)
        second, _ = self.tied_login(USER)
        untied, _ = self.login(USER)
        untied.revoke_device(DEV)
        self.assertEqual(self.refused(first.me), "unauthenticated")
        self.assertEqual(self.refused(second.me), "unauthenticated")
        self.assertEqual(untied.me()["username"], USER)
        self.assertEqual(self.app.tied, {})                                       # nothing left tied to a dead login


class RevocationClosesAttachedClients(Harness):
    def test_the_revoked_logins_attached_client_closes_with_4015(self):
        tied, _ = self.tied_login(USER)
        untied, _ = self.login(USER)
        sid = self.session_id(untied)
        revoked_client = tied.attach(sid)
        surviving_client = untied.attach(sid)
        self.addCleanup(surviving_client.close)
        untied.revoke_device(DEV)
        self.assertTrue(self.closed_by_server(revoked_client))
        self.assertEqual((revoked_client.close_code, revoked_client.close_reason), (4015, "device_revoked"))
        self.assertFalse(self.closed_by_server(surviving_client))     # a login the revocation did not end keeps it
        self.assertFalse(surviving_client.closed)
        self.assertEqual(list(self.app.clients[sid]), [surviving_client.client_id])

    def test_another_principals_attached_client_on_the_same_device_id_stays(self):
        mine, _ = self.tied_login(USER)
        theirs, _ = self.tied_login(PEER)
        other, _ = self.login(USER)
        sid = self.session_id(other)
        gone = mine.attach(sid)
        stays = theirs.attach(sid)
        self.addCleanup(stays.close)
        other.revoke_device(DEV)
        self.assertTrue(self.closed_by_server(gone))
        self.assertEqual(gone.close_code, 4015)
        self.assertFalse(self.closed_by_server(stays))
        self.assertFalse(stays.closed)


class WipeEndsTheLoginsOfTheDevicesItRevokes(Harness):
    def test_a_wipe_ends_the_wiped_principals_tied_logins_and_their_clients(self):
        tied, _ = self.tied_login(PEER)
        untied, _ = self.login(PEER)
        admin, _ = self.login(USER)
        sid = self.session_id(admin)
        attached = tied.attach(sid)
        admin.wipe_vault(PEER)
        self.assertEqual(self.refused(tied.me), "unauthenticated")
        self.assertEqual(untied.me()["username"], PEER)    # no device tie, so a wipe leaves the login (it only locks)
        self.assertTrue(self.closed_by_server(attached))
        self.assertEqual(attached.close_code, 4015)
        self.assertNotIn(PEER, {u for (u, _d), dev in self.app.devices.items() if "revoked_at" not in dev})
        self.assertEqual(self.app.tied, {})

    def test_a_wipe_leaves_every_other_principals_login(self):
        mine, _ = self.tied_login(USER)
        self.tied_login(PEER)
        admin, _ = self.login(USER)
        admin.wipe_vault(PEER)
        self.assertEqual(mine.me()["username"], USER)


class RedeemingAChallengeRechecksTheDevice(Harness):
    def revoke_quietly(self, user: str = USER) -> None:
        """The device leaves the allowlist while its logins stay: the window between an issue and a redeem."""
        self.app.devices[(user, DEV)]["revoked_at"] = "2026-10-29T08:00:01Z"

    def test_bind_after_a_revocation_is_refused_and_ties_nothing(self):
        client, cookie = self.login(USER)
        challenge = client.device_challenge("login.bind", DEV)["challenge_id"]
        self.revoke_quietly()
        self.assertEqual(self.refused(lambda: client.bind_login(challenge, SIG)), "challenge_failed")
        self.assertNotIn(cookie, self.app.tied)

    def test_a_trusted_unlock_after_a_revocation_is_refused_and_installs_nothing(self):
        client, cookie = self.tied_login(USER)
        challenge = client.device_challenge("vault.unlock", DEV)["challenge_id"]
        self.revoke_quietly()
        self.assertEqual(self.refused(lambda: client.vault_unlock_trusted(challenge, PART1, SIG)), "challenge_failed")
        self.assertNotIn(cookie, self.app.unlocked)

    def test_part2_regeneration_after_a_revocation_is_refused_and_installs_nothing(self):
        client, cookie = self.tied_login(USER)
        challenge = client.device_challenge("part2.regenerate", DEV)["challenge_id"]
        self.revoke_quietly()
        self.assertEqual(self.refused(lambda: client.regenerate_part2(challenge, PART1, PART2, SIG)),
                         "challenge_failed")
        self.assertNotIn(cookie, self.app.unlocked)

    def test_a_challenge_issued_before_a_wipe_is_refused_after_it(self):
        client, cookie = self.login(PEER)
        challenge = client.device_challenge("login.bind", DEV)["challenge_id"]
        admin, _ = self.login(USER)
        admin.wipe_vault(PEER)
        self.assertEqual(self.refused(lambda: client.bind_login(challenge, SIG)), "challenge_failed")
        self.assertNotIn(cookie, self.app.tied)

    def test_a_trusted_device_still_completes_each_act(self):
        client, cookie = self.login(USER)
        client.bind_login(client.device_challenge("login.bind", DEV)["challenge_id"], SIG)
        client.vault_unlock_trusted(client.device_challenge("vault.unlock", DEV)["challenge_id"], PART1, SIG)
        self.assertIn(cookie, self.app.unlocked)
        self.app.unlocked.discard(cookie)
        client.regenerate_part2(client.device_challenge("part2.regenerate", DEV)["challenge_id"], PART1, PART2, SIG)
        self.assertIn(cookie, self.app.unlocked)


class TheLoginHandleIsNotTheCookie(Harness):
    def test_the_handle_shares_nothing_with_the_cookie(self):
        client, cookie = self.login(USER)
        handle = client.device_challenge("login.bind", DEV)["login_handle"]
        self.assertTrue(handle.startswith("lh-") and len(handle) > 3)
        self.assertNotIn(cookie[:16], handle)
        self.assertNotIn(cookie[:16].replace("_", "x"), handle)
        self.assertNotEqual(handle, "lh-" + cookie[:16].replace("_", "x"))

    def test_the_handle_is_the_same_for_every_challenge_of_a_login_and_differs_between_logins(self):
        first, _ = self.login(USER)
        second, _ = self.login(USER)
        handles = {first.device_challenge(op, DEV)["login_handle"] for op in ("login.bind", "device.register")}
        self.assertEqual(len(handles), 1)
        self.assertNotEqual(handles, {second.device_challenge("login.bind", DEV)["login_handle"]})


if __name__ == "__main__":
    unittest.main()
