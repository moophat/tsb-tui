"""The device-login client, and the fixture server's reuse of a prompted connection.

1. A known `t` with an unknown `v` is ignored as an unknown `t` (conventions V1, consumers): the client never takes a
   `device_login_result` of `v` 2 as leave to hand the socket over.
2. A failed handoff closes its socket: a wait for `attached` that times out or fails does not leave the WebSocket open;
   ownership passes only with a valid `attached` frame.
3. A prompted connection is reused by the principal who opened it, by `connection_id` or, with none named, as a candidate, with no credential resolved, so no
   credential refusal (no resolving credential, a choice to make, a mapping to a missing entry) can answer it
   (session-open steps 3 and 4). A credential choice beside it is 422, ahead of every 409.
"""

import json
import unittest

import tsb_contract_client as tcc
from tsb_contract_client import frames
from tsbc import _wsframe as wf
from tsbc._serve import NOCRED, PASSWORD, TARGET, USER, ServeApp

from .stub_server import StubServer
from .test_device_login_release_b import SYNTH, ws_url

RESULT = {"t": "device_login_result", "outcome": "connected"}
ATTACHED = {"t": "attached", "v": 1, "session": {"id": "s-1"}, "client_id": "c-1"}


def text(frame: dict) -> wf.Message:
    return wf.Message("text", json.dumps(frame))


class FakeWS:
    """The socket under `AsyncDeviceLogin`: it hands out its items in order (a message, or an exception to raise)
    and records every close."""

    def __init__(self, *items) -> None:
        self.items = list(items)
        self.closed = False
        self.close_code = None
        self.close_reason = ""
        self.close_calls = 0

    async def recv(self, timeout=None):
        if not self.items:
            raise TimeoutError("nothing arrived")
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        if item.kind == "close":
            self.closed, self.close_code = True, item.close_code
        return item

    async def close(self, *args, **kwargs) -> None:
        self.close_calls += 1
        self.closed = True


class UnknownVersion(unittest.IsolatedAsyncioTestCase):
    def test_a_known_type_with_another_version_is_not_a_frame(self):
        known = frames.encode({**RESULT, "v": 1})
        self.assertEqual(frames.parse_device_login(known), {**RESULT, "v": 1})        # the positive control
        for v in (2, 0, "1", True, 1.5, None):
            with self.subTest(v=v):
                self.assertIsNone(frames.parse_device_login(json.dumps({**RESULT, "v": v})))
        self.assertIsNone(frames.parse_device_login(json.dumps(RESULT)))               # no `v`: not a valid server frame

    async def test_an_unknown_version_result_never_sets_the_connected_state(self):
        ws = FakeWS(text({**RESULT, "v": 2}), wf.Message("close", b"", 4016, "device_login_ended"))
        login = tcc.AsyncDeviceLogin(ws)
        self.assertIsNone(await login.recv(1))            # skipped as an unknown `t`; the close ends the wait
        with self.assertRaises(tcc.DeviceLoginError):     # still no session: the state was never set
            await login.attachment()

    async def test_the_frame_after_a_skipped_one_is_returned(self):
        ws = FakeWS(text({**RESULT, "v": 2}), text({"t": "device_login_hostkey", "v": 1, "kind": "first_use",
                                                    "fingerprint": "SHA256:" + "A" * 43}))
        login = tcc.AsyncDeviceLogin(ws)
        self.assertEqual((await login.recv(1))["t"], "device_login_hostkey")
        with self.assertRaises(tcc.DeviceLoginError):
            await login.attachment()

    async def test_a_version_1_result_still_leads_to_the_attachment(self):
        ws = FakeWS(text({**RESULT, "v": 1}), text(ATTACHED))
        login = tcc.AsyncDeviceLogin(ws)
        self.assertEqual((await login.recv(1))["outcome"], "connected")
        att = await login.attachment()
        self.assertEqual(att.client_id, "c-1")
        self.assertEqual(ws.close_calls, 0)               # ownership passed: the attachment holds the socket


class FailedHandoff(unittest.IsolatedAsyncioTestCase):
    async def connected(self, *after) -> tuple[tcc.AsyncDeviceLogin, FakeWS]:
        ws = FakeWS(text({**RESULT, "v": 1}), *after)
        login = tcc.AsyncDeviceLogin(ws)
        await login.recv(1)
        return login, ws

    async def test_a_timeout_waiting_for_attached_closes_the_socket(self):
        login, ws = await self.connected()                # nothing follows the result: the wait times out
        with self.assertRaises(TimeoutError):
            await login.attachment()
        self.assertTrue(ws.closed)
        self.assertEqual(ws.close_calls, 1)

    async def test_a_receive_error_closes_the_socket(self):
        login, ws = await self.connected(ConnectionResetError("reset"))
        with self.assertRaises(ConnectionResetError):
            await login.attachment()
        self.assertEqual(ws.close_calls, 1)

    async def test_a_cancelled_wait_closes_the_socket(self):
        import asyncio
        login, ws = await self.connected(asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await login.attachment()
        self.assertEqual(ws.close_calls, 1)

    async def test_a_close_before_attached_is_refused_and_leaves_nothing_open(self):
        login, ws = await self.connected(wf.Message("close", b"", 4016, "device_login_ended"))
        with self.assertRaises(tcc.AttachRefused):
            await login.attachment()
        self.assertTrue(ws.closed)

    async def test_an_attached_frame_without_its_session_is_no_handoff(self):
        login, ws = await self.connected(text({"t": "attached", "v": 1}))
        with self.assertRaises(KeyError):
            await login.attachment()
        self.assertEqual(ws.close_calls, 1)


class PromptedReuse(unittest.IsolatedAsyncioTestCase):
    """A device login on a target whose stored credentials would refuse a reuse (session-open step 3), then the
    principal's reuse of its connection by id."""

    def setUp(self):
        self.core = ServeApp()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)

    def tearDown(self):
        self.server.__exit__(None, None, None)

    async def prompted(self, target: str) -> str:
        pending = self.client.create_device_login(target, "fixture-account", "xterm-256color", 120, 40)
        login = await tcc.AsyncDeviceLogin.open(ws_url(self.client), pending, ssl_context=None)
        await login.recv(5)
        await login.answer_hostkey(True)
        await login.recv(5)
        await login.answer(1, [SYNTH, SYNTH])
        await login.recv(5)
        att = await login.attachment()
        conn = att.session["connection_id"]
        await att.close()
        self.assertTrue(self.core.connections[conn]["prompted"])
        return conn

    def reuse(self, target: str, conn: str, **more):
        return self.client.create_session(target, "reuse", "xterm-256color", 120, 40, connection_id=conn, **more)

    async def test_a_target_no_credential_resolves_for_does_not_refuse_the_reuse(self):
        conn = await self.prompted(NOCRED)
        self.assertEqual(self.reuse(NOCRED, conn)["session"]["connection_id"], conn)
        # the control: with the connection no longer the principal's, the same target refuses as ever
        self.core.connections[conn]["owner"] = "someone-else"
        with self.assertRaises(tcc.ApiError) as raised:
            self.client.create_session(NOCRED, "reuse", "xterm-256color", 120, 40)
        self.assertEqual((raised.exception.status, raised.exception.code), (409, "no_resolving_credential"))

    async def test_a_mapping_to_a_missing_credential_does_not_refuse_the_reuse(self):
        conn = await self.prompted(TARGET)
        self.core.profiles[USER] = {TARGET: "gone"}       # a Layer 2a mapping the vault no longer holds
        self.assertEqual(self.reuse(TARGET, conn)["session"]["connection_id"], conn)
        self.core.connections[conn]["owner"] = "someone-else"
        with self.assertRaises(tcc.ApiError) as raised:
            self.client.create_session(TARGET, "reuse", "xterm-256color", 120, 40)
        self.assertEqual((raised.exception.status, raised.exception.code), (409, "mapped_credential_missing"))

    async def test_two_candidate_credentials_do_not_make_the_reuse_a_choice(self):
        conn = await self.prompted(TARGET)
        self.core.creds[USER] = {"a": {"reference": "a", "kind": "none"}, "b": {"reference": "b", "kind": "none"}}
        self.core.org_creds["fleet"] = {"reference": "fleet", "kind": "none", "unlock_class": "broker_unlocked",
                                        "stored_by": USER}
        self.core.profiles[USER] = {TARGET: "a"}
        self.core.grants["g-1"] = {"grant_id": "g-1", "user": USER, "selectors": ["tag:shell"],
                                   "credential_ref": "fleet", "granted_by": USER, "granted_at": "2026-10-29T08:00:00Z"}
        self.assertGreater(len(self.core.vault_candidates(USER, TARGET)), 1)           # the setup holds two
        self.assertEqual(self.reuse(TARGET, conn)["session"]["connection_id"], conn)
        self.core.connections[conn]["owner"] = "someone-else"
        with self.assertRaises(tcc.ApiError) as raised:
            self.client.create_session(TARGET, "reuse", "xterm-256color", 120, 40)
        self.assertEqual((raised.exception.status, raised.exception.code), (409, "credential_choice_required"))

    async def test_a_credential_choice_beside_a_prompted_connection_is_422_before_any_409(self):
        conn = await self.prompted(TARGET)
        self.core.profiles[USER] = {TARGET: "gone"}       # a 409 `mapped_credential_missing` would answer a reuse
        with self.assertRaises(tcc.ApiError) as raised:
            self.reuse(TARGET, conn, credential=("fixture-credential", "personal"))
        self.assertEqual((raised.exception.status, raised.exception.code), (422, "invalid_request"))
        # the same choice on a connection that is not prompted is no shape error: the mapping's 409 answers it
        with self.assertRaises(tcc.ApiError) as raised:
            self.reuse(TARGET, "no-such-connection", credential=("fixture-credential", "personal"))
        self.assertEqual((raised.exception.status, raised.exception.code), (409, "mapped_credential_missing"))
        # and on no target a credential resolves for (the 409 `no_resolving_credential` does not come first either)
        other = await self.prompted(NOCRED)
        with self.assertRaises(tcc.ApiError) as raised:
            self.reuse(NOCRED, other, credential=("fixture-credential", "personal"))
        self.assertEqual(raised.exception.status, 422)

    async def test_the_exception_is_the_principals_own_prompted_connection_only(self):
        conn = await self.prompted(NOCRED)
        # beside a credential choice no stored credential opened it: 422
        with self.assertRaises(tcc.ApiError) as raised:
            self.reuse(NOCRED, conn, credential=("fixture-credential", "personal"))
        self.assertEqual(raised.exception.status, 422)
        # a connection that is not prompted, or does not exist, is no exception: the credential check answers
        with self.assertRaises(tcc.ApiError) as raised:
            self.reuse(NOCRED, "no-such-connection")
        self.assertEqual(raised.exception.status, 409)
        # another principal's prompted connection is never theirs
        self.core.connections[conn]["owner"] = "someone-else"
        with self.assertRaises(tcc.ApiError) as raised:
            self.reuse(NOCRED, conn)
        self.assertEqual(raised.exception.status, 409)

    def reuse_unnamed(self, target: str, **more):
        return self.client.create_session(target, "reuse", "xterm-256color", 120, 40, **more)

    def two_credentials(self):
        self.core.creds[USER] = {"a": {"reference": "a", "kind": "none"}, "b": {"reference": "b", "kind": "none"}}
        self.core.org_creds["fleet"] = {"reference": "fleet", "kind": "none", "unlock_class": "broker_unlocked",
                                        "stored_by": USER}
        self.core.profiles[USER] = {TARGET: "a"}
        self.core.grants["g-1"] = {"grant_id": "g-1", "user": USER, "selectors": ["tag:shell"],
                                   "credential_ref": "fleet", "granted_by": USER, "granted_at": "2026-10-29T08:00:00Z"}
        self.assertGreater(len(self.core.vault_candidates(USER, TARGET)), 1)

    async def test_an_unnamed_reuse_by_the_principal_reuses_its_only_prompted_connection(self):
        for target in (NOCRED, TARGET):                     # no stored credential resolves; one resolves
            conn = await self.prompted(target)
            self.assertEqual(self.reuse_unnamed(target)["session"]["connection_id"], conn)

    def one_credential(self):
        self.core.creds[USER] = {"a": {"reference": "a", "kind": "none"}}
        self.core.profiles[USER] = {TARGET: "a"}
        self.assertEqual(self.core.vault_candidates(USER, TARGET), {("a", "personal")})

    async def test_a_stored_credential_connection_and_a_prompted_one_are_both_listed(self):
        conn = await self.prompted(TARGET)
        self.one_credential()
        stored = self.client.create_session(TARGET, "new", "xterm-256color", 120, 40)["session"]["connection_id"]
        with self.assertRaises(tcc.ApiError) as raised:
            self.reuse_unnamed(TARGET)
        self.assertEqual((raised.exception.status, raised.exception.code), (409, "connection_choice_required"))
        self.assertEqual(sorted(raised.exception.detail["connection_ids"]), sorted([conn, stored]))

    async def test_two_stored_credentials_leave_only_the_prompted_connections_as_candidates(self):
        conn = await self.prompted(TARGET)
        self.two_credentials()
        self.assertEqual(self.reuse_unnamed(TARGET)["session"]["connection_id"], conn)
        second = await self.prompted(TARGET)
        with self.assertRaises(tcc.ApiError) as raised:
            self.reuse_unnamed(TARGET)
        self.assertEqual((raised.exception.status, raised.exception.code), (409, "connection_choice_required"))
        self.assertEqual(sorted(raised.exception.detail["connection_ids"]), sorted([conn, second]))
        # a new connection still meets the credential choice
        with self.assertRaises(tcc.ApiError) as raised:
            self.client.create_session(TARGET, "new", "xterm-256color", 120, 40)
        self.assertEqual((raised.exception.status, raised.exception.code), (409, "credential_choice_required"))

    async def test_another_principals_prompted_connection_is_neither_listed_nor_reused(self):
        conn = await self.prompted(NOCRED)
        self.core.connections[conn]["owner"] = "someone-else"
        with self.assertRaises(tcc.ApiError) as raised:
            self.reuse_unnamed(NOCRED)
        self.assertEqual((raised.exception.status, raised.exception.code), (409, "no_resolving_credential"))
        other = await self.prompted(TARGET)
        self.core.connections[other]["owner"] = "someone-else"
        self.assertNotIn(self.reuse_unnamed(TARGET)["session"]["connection_id"], (conn, other))
        with self.assertRaises(tcc.ApiError) as raised:
            self.reuse(TARGET, other)
        self.assertEqual(raised.exception.status, 422)

    async def test_a_credential_choice_makes_prompted_connections_no_candidates(self):
        conn = await self.prompted(TARGET)
        self.one_credential()
        got = self.reuse_unnamed(TARGET, credential=("a", "personal"))
        self.assertNotEqual(got["session"]["connection_id"], conn)


if __name__ == "__main__":
    unittest.main()
