"""The fixture server's device login: its cap, its answers, its records and its tie to the TSB login.

`tsbc serve` holds a prompt-mode create in memory from its 202 to its end. These tests hold what that state must keep,
each one a case the first version got wrong:

- the cap (device-login step 5): a principal's device logins in progress are counted, under the request lock, before a
  new one is registered, and past `worker.device_logins_max` the create is 429 `rate_limited`; every end gives the
  place back, once;
- no answer is kept: the frames the server read hold the synthetic constant where an answer was, on every path;
- one end function drops the record on every end (cancel, refusal, failure, success, expiry, a lost socket);
- a device login belongs to the TSB login that created it (V10; login steps 9 to 14): when that login logs out, its
  pending device logins end `cancelled` with 4016; when a device revocation ends it, they close 4015 with no result, and
  the session a device login opened is a client of that login, closed 4015 with it.
"""

import json
import unittest
from concurrent.futures import ThreadPoolExecutor

import tsb_contract_client as tcc
from tsbc import _aws
from tsbc._serve import PASSWORD, PEER, TARGET, USER, ServeApp

from .stub_server import StubServer

SYNTH = "FIXTURE-NOT-A-PASSWORD"
CANARY = "CANARY-ANSWER-0001"
DEV = "FIXTURE-DEVICE-ID-" + "A" * 25
SIG = "RklYVFVSRS1TSUdOQVRVUkUtMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMA=="
DEVICE_RECORD = {"device_id": DEV, "device_name": "Work laptop", "registered_at": "2026-10-29T08:00:00Z",
                 "last_used_at": "2026-10-29T08:00:00Z"}
TOO_MANY = "too many device logins in progress; finish or cancel one"


class Pane:
    """One raw socket on `GET /ws/attach`, the way a surface's pane holds one."""

    def __init__(self, ws: _aws.AsyncWebSocket) -> None:
        self.ws = ws

    @classmethod
    async def open(cls, client: tcc.Client, pending: dict) -> "Pane":
        ws = await _aws.AsyncWebSocket.connect(client._attach_url(), ssl_context=None, timeout=5)
        pane = cls(ws)
        await pane.send({"t": "device_login_attach", "v": 1, "ticket": pending["ticket"],
                         "device_login_id": pending["device_login_id"]})
        return pane

    async def send(self, frame) -> None:
        await self.ws.send_text(json.dumps(frame))

    async def next(self, timeout: float = 5):
        """The next text frame as a dict, or ("close", code, reason)."""
        while True:
            message = await self.ws.recv(timeout)
            if message.kind == "close":
                return ("close", message.close_code, message.close_reason)
            if message.kind == "text":
                return json.loads(message.data)

    async def through_hostkey(self) -> None:
        self_key = await self.next()
        assert self_key["t"] == "device_login_hostkey", self_key
        await self.send({"t": "device_login_hostkey_answer", "v": 1, "accept": True})
        prompt = await self.next()
        assert prompt["t"] == "device_login_prompt", prompt

    async def close(self) -> None:
        await self.ws.close()


class Served(unittest.IsolatedAsyncioTestCase):
    def start(self, **knobs) -> None:
        self.core = ServeApp(**knobs)
        server = StubServer(self.core)
        self.url = server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        self.client = self.login(USER)

    def login(self, user: str) -> tcc.Client:
        client = tcc.Client(self.url, timeout=5)
        client.login(user, PASSWORD)
        return client

    @staticmethod
    def create(client: tcc.Client) -> dict:
        return client.create_device_login(TARGET, "fixture-account", "xterm-256color", 120, 40)

    @staticmethod
    def refusal(client: tcc.Client):
        """(status, code, message) of a create, or the 202's body when it is accepted."""
        try:
            return client.create_device_login(TARGET, "fixture-account", "xterm-256color", 120, 40)
        except tcc.ApiError as exc:
            return exc.status, exc.code, exc.message

    def assert_place_free(self, client: tcc.Client, what: str) -> dict:
        """The principal's one place is free again: a create is accepted (and holds it until the caller ends it)."""
        got = self.refusal(client)
        self.assertIsInstance(got, dict, f"{what}: the create was refused {got}")
        return got

    def assert_cap_full(self, client: tcc.Client) -> None:
        self.assertEqual(self.refusal(client), (429, "rate_limited", TOO_MANY))

    def expire(self, pending: dict) -> None:
        """Move an unredeemed device login's ticket past its lifetime."""
        with self.core.lock:
            user, dlid, _ = self.core.dl_tickets[pending["ticket"]]
            self.core.dl_tickets[pending["ticket"]] = (user, dlid, 0.0)

    def tied_login(self, user: str = USER) -> tcc.Client:
        """A logged-in client tied to the device DEV, whose record the principal holds."""
        self.core.devices[(user, DEV)] = dict(DEVICE_RECORD)
        client = self.login(user)
        client.bind_login(client.device_challenge("login.bind", DEV)["challenge_id"], SIG)
        return client


class TheCap(Served):
    """The cap: device-login step 5 and C1/session-create-device-login-limit-429."""

    def test_a_create_past_the_cap_is_429_rate_limited_and_the_ones_in_progress_stand(self):
        self.start(device_logins_max=2)
        first, second = self.create(self.client), self.create(self.client)
        self.assert_cap_full(self.client)
        self.assertEqual(set(self.core.device_logins), {first["device_login_id"], second["device_login_id"]})
        self.assertEqual(len(self.core.dl_tickets), 2)       # the refused create registered nothing

    def test_the_cap_is_per_principal(self):
        self.start(device_logins_max=1)
        self.create(self.client)
        self.assert_cap_full(self.client)
        self.assertIsInstance(self.refusal(self.login(PEER)), dict)

    def test_the_cap_is_per_principal_not_per_login(self):
        self.start(device_logins_max=1)
        self.create(self.client)
        self.assert_cap_full(self.login(USER))                # another cookie of the same principal

    def test_the_served_limit_defaults_to_the_configs_template_value(self):
        self.start()
        for _ in range(8):
            self.create(self.client)
        self.assert_cap_full(self.client)

    def test_two_creates_at_once_never_both_take_the_last_place(self):
        self.start(device_logins_max=3)
        clients = [self.login(USER) for _ in range(12)]
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(self.refusal, clients))
        accepted = [r for r in results if isinstance(r, dict)]
        self.assertEqual(len(accepted), 3)
        self.assertEqual({r[:2] for r in results if isinstance(r, tuple)}, {(429, "rate_limited")})
        self.assertEqual(len(self.core.device_logins), 3)

    async def test_a_cancel_gives_the_place_back_once(self):
        self.start(device_logins_max=2)
        pane = await Pane.open(self.client, self.create(self.client))
        self.create(self.client)
        self.assert_cap_full(self.client)
        await pane.next()                                    # the host key
        await pane.send({"t": "device_login_cancel", "v": 1})
        self.assertEqual((await pane.next())["outcome"], "cancelled")
        self.assertEqual((await pane.next())[:2], ("close", 4016))
        await pane.close()                                   # the socket's close after the end: no second give-back
        self.assert_place_free(self.client, "after a cancel")
        self.assert_cap_full(self.client)

    async def test_a_refused_key_gives_the_place_back(self):
        self.start(device_logins_max=1)
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.next()
        await pane.send({"t": "device_login_hostkey_answer", "v": 1, "accept": False})
        self.assertEqual((await pane.next())["outcome"], "hostkey_refused")
        await pane.close()
        self.assert_place_free(self.client, "after a refused key")

    async def test_a_lost_socket_gives_the_place_back(self):
        self.start(device_logins_max=1)
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.next()
        await pane.close()
        self.assert_place_free(self.client, "after the pane closed its socket")

    async def test_a_login_that_became_an_attach_gives_the_place_back(self):
        self.start(device_logins_max=1)
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.through_hostkey()
        await pane.send({"t": "device_login_answer", "v": 1, "prompt_id": 1, "answers": [SYNTH, SYNTH]})
        self.assertEqual((await pane.next())["outcome"], "connected")
        self.assert_place_free(self.client, "after connected")
        await pane.close()

    def test_an_unattached_login_gives_the_place_back_when_its_ticket_expires(self):
        self.start(device_logins_max=1)
        pending = self.create(self.client)
        self.assert_cap_full(self.client)
        self.expire(pending)
        self.assert_place_free(self.client, "after the ticket's lifetime")

    async def test_a_logout_of_the_creating_login_gives_the_place_back(self):
        self.start(device_logins_max=1)
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.next()
        self.client.logout()
        self.assertEqual((await pane.next())["outcome"], "cancelled")
        self.assert_place_free(self.login(USER), "after the login ended")
        await pane.close()
