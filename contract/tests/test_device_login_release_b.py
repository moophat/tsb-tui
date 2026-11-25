"""Release B of login-at-connect: the device login (C1 202 and 429, C2 device_login_* frames, C3 0.7).

What this release adds is checked here against expectations written out by hand, never from the code
under test. The fixtures are graded by self-check; these tests hold the rules the fixtures only
illustrate: the C3 order rules a capture can show, the shapes, the additivity of every change and the
exact set of descriptions rewritten, the redaction of answers, the C8 codec, the client and `tsbc serve`.
"""

import contextlib
import io
import json
import logging
import tempfile
import unittest
from pathlib import Path

import tsb_contract_client as tcc
from tsbc import _exchange, _ipc, artifacts, selfcheck
from tsbc._serve import TARGET as SERVE_TARGET
from tsbc._serve import USER as SERVE_USER
from tsbc._serve import ServeApp
from tsbc.schema import Registry, validate

from . import CONTRACT
from .stub_core import PASSWORD, TARGET, USER
from .stub_server import StubServer

REG = Registry.from_tree(CONTRACT)

SYNTH = "FIXTURE-NOT-A-PASSWORD"
FP = "SHA256:FIXTURE000000000000000000000000000000000000"
C = "c"


def connect(**over):
    msg = {"cmd": "connect", "target_id": "t", "protocol": "ssh", "host": "192.0.2.10", "port": 22, "user": "u",
           "auth": {"kind": "prompt"}, "login_timeout": 120000}
    msg.update(over)
    return ("core", msg)


def ask(kind="first_use"):
    return ("worker", {"event": "hostkey_ask", "conn_id": C, "kind": kind, "fingerprint": FP})


def hk_answer(accept=True):
    return ("core", {"cmd": "hostkey_answer", "accept": accept})


def password(prompt_id=1, attempt=1):
    return ("worker", {"event": "auth_prompt", "conn_id": C, "method": "password", "attempt": attempt,
                       "prompt_id": prompt_id})


def kbd(prompt_id=1, attempt=1, n=2, method="keyboard-interactive"):
    prompts = [{"text": f"q{i}: ", "echo": False} for i in range(n)]
    msg = {"event": "auth_prompt", "conn_id": C, "method": method, "attempt": attempt, "name": "", "instruction": "",
           "prompts": prompts}
    if n:
        msg["prompt_id"] = prompt_id
    return ("worker", msg)


def banner():
    return ("worker", {"event": "auth_prompt", "conn_id": C, "method": "banner", "text": "hello"})


def answer(prompt_id=1, n=1):
    return ("core", {"cmd": "auth_answer", "prompt_id": prompt_id, "answers": [SYNTH] * n})


CONNECTED = ("worker", {"event": "connected", "conn_id": C})
FAILED = ("worker", {"event": "auth_failed", "conn_id": C, "reason": "x", "code": "refused"})
BAD_REQUEST = ("worker", {"event": "error", "code": "bad_request", "message": "x"})
DISCONNECT = ("core", {"cmd": "disconnect"})
LOST = ("worker", {"event": "connection_lost", "conn_id": C, "session_ids": [], "cause": "disconnect", "reason": "x"})


def problems(messages):
    return [text for _, text in _ipc.conn_order(messages)]


def joined(messages):
    return " | ".join(problems(messages))


class DeviceLoginOrder(unittest.TestCase):
    """C3 rules 2, 3, 6, 7 and 8 as a capture shows them (ipc-control top). Rules 1, 4 and 5 are the providers'."""

    def test_a_whole_device_login_is_clean(self):
        self.assertEqual(problems([connect(), ask(), hk_answer(), banner(), kbd(1, 1), answer(1, 2),
                                   kbd(2, 1, n=0), password(3, 1), answer(3, 1), CONNECTED]), [])

    def test_a_login_with_no_key_ask_and_no_prompt_is_clean(self):
        self.assertEqual(problems([connect(), CONNECTED]), [])

    def test_one_ask_before_any_prompt_and_answered_before_the_next_event(self):
        self.assertIn("second hostkey_ask", joined([connect(), ask(), hk_answer(), ask()]))
        self.assertIn("before any auth_prompt", joined([connect(), banner(), ask(), hk_answer()]))
        self.assertIn("unanswered", joined([connect(), ask(), password()]))

    def test_never_a_hostkey_event_in_prompt_mode(self):
        hostkey = ("worker", {"event": "hostkey", "conn_id": C, "fingerprint": FP, "first_use": True})
        self.assertIn("never a hostkey event", joined([connect(), hostkey, CONNECTED]))

    def test_one_prompt_with_an_id_outstanding_at_a_time(self):
        self.assertIn("outstanding", joined([connect(), password(1), password(2)]))
        # display-only rounds and banners come at any time
        self.assertEqual(problems([connect(), password(1), banner(), kbd(2, 1, n=0), answer(1, 1), CONNECTED]), [])

    def test_the_core_sends_only_four_commands_before_the_terminal_answer(self):
        timers = ("core", {"cmd": "timers", "timers": {}})
        self.assertIn("before the terminal answer", joined([connect(), timers, CONNECTED]))
        ping = ("core", {"cmd": "ping", "n": 1})
        pong = ("worker", {"event": "pong", "n": 1})
        self.assertEqual(problems([connect(), ping, pong, CONNECTED]), [])

    def test_connected_never_comes_with_a_question_outstanding(self):
        self.assertIn("outstanding", joined([connect(), password(1), CONNECTED]))
        self.assertIn("outstanding", joined([connect(), ask(), CONNECTED]))

    def test_auth_failed_may_end_a_login_with_a_prompt_outstanding(self):
        self.assertEqual(problems([connect(), password(1), FAILED]), [])

    def test_an_answer_is_checked_against_its_prompt_and_a_bad_one_is_answered_bad_request(self):
        for name, wrong in {"another id": answer(9, 1), "another count": answer(1, 2)}.items():
            with self.subTest(name):
                self.assertEqual(problems([connect(), password(1), wrong, BAD_REQUEST]), [])
                self.assertIn("bad_request", joined([connect(), password(1), wrong, CONNECTED]))
        self.assertEqual(problems([connect(), hk_answer(), BAD_REQUEST]), [])       # no ask outstanding
        self.assertEqual(problems([connect(), ask(), hk_answer(), hk_answer(), BAD_REQUEST]), [])  # a second one
        self.assertEqual(problems([connect(), answer(1, 1), BAD_REQUEST]), [])      # no prompt outstanding

    def test_after_the_terminal_answer_the_worker_ignores_answers_and_says_nothing(self):
        self.assertEqual(problems([connect(), password(1), answer(2, 1), BAD_REQUEST, answer(1, 1)]), [])
        self.assertEqual(problems([connect(), CONNECTED, answer(1, 1)]), [])
        self.assertIn("ignores", joined([connect(), CONNECTED, answer(1, 1), BAD_REQUEST]))
        self.assertIn("ignores", joined([connect(), password(1), answer(2, 1), BAD_REQUEST, answer(1, 1), BAD_REQUEST]))

    def test_no_prompt_or_ask_after_the_terminal_answer(self):
        self.assertIn("terminal answer", joined([connect(), CONNECTED, password(1)]))
        self.assertIn("terminal answer", joined([connect(), CONNECTED, ask()]))

    def test_every_error_before_the_terminal_answer_is_the_terminal_answer(self):
        error = ("worker", {"event": "error", "message": "no code"})
        self.assertEqual(problems([connect(), password(1), error]), [])
        self.assertIn("after the connection ended", joined([connect(), error, password(1)]))

    def test_a_disconnect_before_the_terminal_answer_ends_the_attempt(self):
        self.assertEqual(problems([connect(), ask(), DISCONNECT, LOST]), [])
        self.assertIn("never answered", joined([connect(), ask(), DISCONNECT]))
        listed = ("worker", {"event": "connection_lost", "conn_id": C, "session_ids": ["s"], "cause": "disconnect",
                             "reason": "x"})
        self.assertTrue(problems([connect(), ask(), DISCONNECT, listed]))
        # an event that crossed the disconnect is tolerated, and the answer still follows
        self.assertEqual(problems([connect(), ask(), DISCONNECT, password(1), LOST]), [])

    def test_a_disconnect_before_connected_is_still_a_problem_without_prompt_mode(self):
        plain = ("core", {"cmd": "connect", "target_id": "t", "protocol": "echo"})
        self.assertIn("disconnect before connected", joined([plain, DISCONNECT]))

    def test_an_unknown_method_or_kind_is_answered_disconnect(self):
        self.assertEqual(problems([connect(), kbd(method="voice"), DISCONNECT, LOST]), [])
        self.assertIn("disconnect", joined([connect(), kbd(method="voice"), answer(1, 2), BAD_REQUEST]))
        self.assertEqual(problems([connect(), ask("changed"), DISCONNECT, LOST]), [])
        self.assertIn("never disconnected", joined([connect(), ask("changed")]))

    def test_the_attempt_rises_only_after_an_answer_and_by_one(self):
        ok = [connect(), password(1, 1), answer(1, 1), password(2, 2), answer(2, 1), CONNECTED]
        self.assertEqual(problems(ok), [])
        self.assertIn("attempt", joined([connect(), password(1, 2)]))                       # starts at 1
        self.assertIn("attempt", joined([connect(), password(1, 1), answer(1, 1), password(2, 3)]))  # by one
        self.assertIn("attempt", joined([connect(), kbd(1, 1, n=0), password(1, 2)]))      # no answer was given
        # a partial success keeps the attempt: the same number twice
        self.assertEqual(problems([connect(), password(1, 1), answer(1, 1), kbd(2, 1, n=1), answer(2, 1), CONNECTED]), [])
        self.assertIn("attempt", joined([connect(), password(1, 2), answer(1, 1), password(2, 1)]))  # never down

    def test_a_code_is_sent_only_on_the_answer_to_a_prompt_mode_connect(self):
        plain = ("core", {"cmd": "connect", "target_id": "t", "protocol": "ssh", "host": "h", "port": 22, "user": "u",
                          "auth": {"kind": "password", "password": SYNTH}})
        self.assertIn("prompt-mode", joined([plain, FAILED]))
        self.assertEqual(problems([plain, ("worker", {"event": "auth_failed", "conn_id": C, "reason": "x"})]), [])

    def test_device_login_events_are_a_fault_on_a_connection_not_in_prompt_mode(self):
        plain = ("core", {"cmd": "connect", "target_id": "t", "protocol": "ssh", "host": "h", "port": 22, "user": "u",
                          "auth": {"kind": "password", "password": SYNTH}})
        self.assertIn("not in prompt mode", joined([plain, ask()]))
        self.assertIn("not in prompt mode", joined([plain, password()]))

    def test_the_c3_version_includes_terminal_sealing(self):
        self.assertEqual(_ipc.C3_VERSION, "0.8")


class AnswersAreCredentials(unittest.TestCase):
    """`answers` joins the credential-named keys: each string of the list is a credential (artifacts-collected top)."""

    def test_a_list_under_answers_must_be_all_the_synthetic_constant(self):
        self.assertIn("answers", selfcheck.CREDENTIAL_KEYS)
        ok = {"cmd": "auth_answer", "prompt_id": 1, "answers": [SYNTH, SYNTH]}
        self.assertEqual(selfcheck.credential_problems(ok, placeholders=False), [])
        bad = {"cmd": "auth_answer", "prompt_id": 1, "answers": [SYNTH, "hunter2"]}
        found = selfcheck.credential_problems(bad, placeholders=False)
        self.assertEqual(len(found), 1)
        self.assertIn("answers[1]", found[0])
        self.assertNotIn("hunter2", found[0])           # a finding never quotes the value

    def test_an_empty_string_answer_is_not_synthetic_and_a_fixture_holds_none(self):
        self.assertTrue(selfcheck.credential_problems({"answers": [""]}, placeholders=False))

    def test_the_c2_frame_is_caught_the_same_way(self):
        frame = {"t": "device_login_answer", "v": 1, "prompt_id": 1, "answers": ["a-real-one"]}
        self.assertTrue(selfcheck.credential_problems(frame, placeholders=True))
        self.assertEqual(selfcheck.credential_problems({**frame, "answers": ["${a1}"]}, placeholders=True), [])

    def test_a_collection_with_a_real_answer_in_a_control_capture_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "control").mkdir()
            line = {"from": "core", "msg": {"cmd": "auth_answer", "prompt_id": 1, "answers": ["a-real-one"]}}
            (root / "control" / "c.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
            findings = artifacts._credential_findings(root, [root / "control" / "c.jsonl"])
            self.assertEqual(len(findings), 1)
            self.assertNotIn("a-real-one", findings[0])
            line["msg"]["answers"] = [SYNTH]
            (root / "control" / "c.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
            self.assertEqual(artifacts._credential_findings(root, [root / "control" / "c.jsonl"]), [])

    def test_the_collection_schema_says_so(self):
        text = json.loads((CONTRACT / "schemas" / "artifacts-collected.schema.json").read_text(encoding="utf-8"))
        self.assertIn("every string of an answers list", text["description"])


CANARY = "ANSWER-CANARY-7f3a"


def ws_url(client):
    return client._attach_url()


class ClientDeviceLogin(unittest.IsolatedAsyncioTestCase):
    """The client drives one device login through `tsbc serve`: the 202, the socket, the host key, one
    keyboard-interactive round, `connected`, and the same socket as an attach."""

    def setUp(self):
        self.core = ServeApp()      # the shipped app: the same code `tsbc serve` runs
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)

    def tearDown(self):
        self.server.__exit__(None, None, None)

    async def open(self, target=TARGET):
        pending = self.client.create_device_login(target, "fixture-account", "xterm-256color", 120, 40)
        return pending, await tcc.AsyncDeviceLogin.open(ws_url(self.client), pending, ssl_context=None)

    async def test_one_whole_device_login_and_the_attach_on_the_same_socket(self):
        pending, login = await self.open()
        self.assertEqual(REG.validate(pending, "openapi/tsb.openapi.json#/components/schemas/DeviceLoginPending"), [])
        key = await login.recv(5)
        self.assertEqual(key["t"], "device_login_hostkey")
        await login.answer_hostkey(True)
        prompt = await login.recv(5)
        self.assertEqual((prompt["t"], prompt["method"], prompt["prompt_id"]), ("device_login_prompt", "keyboard-interactive", 1))
        self.assertEqual([p["echo"] for p in prompt["prompts"]], [False, True])
        for frame in (key, prompt):
            self.assertEqual(REG.validate(frame, "schemas/ws-frames.schema.json#/$defs/server"), [])
        await login.answer(1, [SYNTH, SYNTH])
        result = await login.recv(5)
        self.assertEqual(result, {"t": "device_login_result", "v": 1, "outcome": "connected"})
        self.assertIsNone(await login.recv(0.1))            # after the result, the next frame is attached
        att = await login.attachment()
        self.assertEqual(att.session["target_id"], TARGET)
        self.assertEqual(att.session["owner"], USER)
        await att.close()
        sent = [f for f in self.core.device_login_frames]
        for frame in sent:
            self.assertEqual(REG.validate(frame, "schemas/ws-frames.schema.json#/$defs/client"), [], frame)
        self.assertEqual([f["t"] for f in sent], ["device_login_attach", "device_login_hostkey_answer", "device_login_answer"])

    async def test_an_answer_is_accepted_and_kept_nowhere_in_the_server_or_the_clients_log(self):
        logs, out, err = io.StringIO(), io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(logs)
        root = logging.getLogger()
        old = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                _pending, login = await self.open()
                await login.recv(5)
                await login.answer_hostkey(True)
                await login.recv(5)
                await login.answer(1, [CANARY, CANARY + "-2"])
                self.assertEqual((await login.recv(5))["outcome"], "connected")      # the server took it
                text = repr(login) + repr(login.__dict__)
        finally:
            root.removeHandler(handler)
            root.setLevel(old)
        seen = [f for f in self.core.device_login_frames if f["t"] == "device_login_answer"]
        self.assertEqual(seen[0]["answers"], [SYNTH, SYNTH])               # the server keeps the constant, never an answer
        self.assertNotIn(CANARY, repr(self.core.__dict__))
        for where, held in {"log": logs.getvalue(), "stdout": out.getvalue(), "stderr": err.getvalue(), "repr": text}.items():
            self.assertNotIn(CANARY, held, where)
        att = await login.attachment()
        await att.close()

    async def test_attachment_before_connected_raises(self):
        _pending, login = await self.open()
        with self.assertRaises(tcc.DeviceLoginError):
            await login.attachment()
        await login.recv(5)                                  # the host key; still no session
        with self.assertRaises(tcc.DeviceLoginError):
            await login.attachment()
        await login.cancel()
        await login.close()

    async def test_an_empty_answer_string_is_sent_as_it_is(self):
        _pending, login = await self.open()
        await login.recv(5)
        await login.answer_hostkey(True)
        await login.recv(5)
        await login.answer(1, ["", ""])
        self.assertEqual((await login.recv(5))["outcome"], "connected")
        sent = [f for f in self.core.device_login_frames if f["t"] == "device_login_answer"]
        self.assertEqual(sent[0]["answers"], [SYNTH, SYNTH])         # accepted as two strings; the server keeps none
        self.assertEqual(REG.validate(sent[0], "schemas/ws-frames.schema.json#/$defs/client"), [])
        await (await login.attachment()).close()

    async def test_a_refused_answer_is_answered_and_the_prompt_stays_outstanding(self):
        _pending, login = await self.open()
        await login.recv(5)
        await login.answer(1, [SYNTH, SYNTH])                # no key answer yet: not pending, nothing crosses
        self.assertEqual((await login.recv(5))["reason"], "not_pending")
        await login.answer_hostkey(True)
        await login.recv(5)
        await login.answer(1, [SYNTH])                       # one string for two prompts
        self.assertEqual(await login.recv(5), {"t": "request_refused", "v": 1, "request": "device_login_answer",
                                              "reason": "invalid"})
        await login.answer(7, [SYNTH, SYNTH])                # another prompt_id
        self.assertEqual((await login.recv(5))["reason"], "not_pending")
        await login.answer(1, [SYNTH, SYNTH])                # the positive control: a good answer goes through
        self.assertEqual((await login.recv(5))["outcome"], "connected")
        await (await login.attachment()).close()

    async def test_a_cancel_and_a_refused_key_end_the_login_4016(self):
        _pending, login = await self.open()
        await login.recv(5)
        await login.cancel()
        self.assertEqual((await login.recv(5))["outcome"], "cancelled")
        self.assertIsNone(await login.recv(5))
        self.assertEqual(login.close_code, 4016)
        _pending, login = await self.open()
        await login.recv(5)
        await login.answer_hostkey(False)
        self.assertEqual((await login.recv(5))["outcome"], "hostkey_refused")
        self.assertIsNone(await login.recv(5))
        self.assertEqual(login.close_code, 4016)
        self.assertEqual(self.core.sessions, {})            # no session was ever made


class ServedDeviceLogin(unittest.IsolatedAsyncioTestCase):
    """`tsbc serve` holds the contract's refusals and ticket rules for a device login."""

    def setUp(self):
        self.core = ServeApp()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)

    def tearDown(self):
        self.server.__exit__(None, None, None)

    def prompt_body(self, **over):
        body = {"target_id": TARGET, "connection": "new", "term": "xterm-256color", "cols": 120, "rows": 40,
                "login": {"user": "u", "auth": {"kind": "prompt"}}}
        body.update(over)
        return body

    def status(self, body):
        try:
            self.client._call("POST", "/sessions", body, expect=202)
            return 202, None
        except tcc.ApiError as exc:
            return exc.status, exc.code

    def test_the_create_body_the_client_sends_is_a_valid_request_and_the_202_a_valid_answer(self):
        pending = self.client.create_device_login(TARGET, "u", "xterm-256color", 120, 40, recording_acknowledged=True)
        sent = [b for m, path, b in self.core.requests if (m, path) == ("POST", "/sessions")][-1]
        op = _exchange.find_operation(REG, "POST", "/sessions")
        self.assertEqual(validate(sent, _exchange.request_schema(REG, op), REG, _exchange.OPENAPI), [])
        self.assertEqual(sent["login"], {"user": "u", "auth": {"kind": "prompt"}, "recording_acknowledged": True})
        self.assertEqual(set(pending), {"device_login_id", "ticket", "expires_in"})
        self.assertEqual(self.core.sessions, {})            # nothing is listed or placed by the 202

    def test_a_prompt_login_is_allowed_on_a_new_ssh_connection_only(self):
        self.assertEqual(self.status(self.prompt_body()), (202, None))
        self.assertEqual(self.status(self.prompt_body(connection="reuse"))[0], 422)
        self.assertEqual(self.status(self.prompt_body(target_id="telnet-scenario"))[0], 422)
        self.assertEqual(self.status(self.prompt_body(login={"user": "u", "auth": {"kind": "prompt", "x": 1}}))[0], 422)
        self.assertEqual(self.status(self.prompt_body(login={"auth": {"kind": "prompt"}}))[0], 422)
        self.assertEqual(self.status(self.prompt_body(target_id="no-such-target"))[0], 404)

    def test_a_prompt_login_that_would_be_recorded_needs_consent(self):
        core = ServeApp(login_input=True)
        with StubServer(core) as url:
            client = tcc.Client(url, timeout=5)
            client.login(USER, PASSWORD)
            with self.assertRaises(tcc.ApiError) as raised:
                client.create_device_login(TARGET, "u", "xterm-256color", 120, 40)
            self.assertEqual((raised.exception.status, raised.exception.code), (409, "login_recording_not_acknowledged"))
            self.assertEqual(core.device_logins, {})
            client.create_device_login(TARGET, "u", "xterm-256color", 120, 40, recording_acknowledged=True)

    async def ws_close_code(self, first_frame):
        from tsbc import _aws
        ws = await _aws.AsyncWebSocket.connect(ws_url(self.client), ssl_context=None, timeout=5)
        await ws.send_text(json.dumps(first_frame))
        while True:
            message = await ws.recv(5)
            if message.kind == "close":
                return message.close_code

    async def test_the_ticket_is_one_time_and_of_one_kind(self):
        pending = self.client.create_device_login(TARGET, "u", "xterm-256color", 120, 40)
        attach = {"t": "device_login_attach", "v": 1, "ticket": pending["ticket"],
                  "device_login_id": pending["device_login_id"]}
        first = await tcc.AsyncDeviceLogin.open(ws_url(self.client), pending, ssl_context=None)
        self.assertEqual((await first.recv(5))["t"], "device_login_hostkey")
        self.assertEqual(await self.ws_close_code(attach), 4001)            # burned on first presentation
        await first.cancel()
        await first.close()
        # a session ticket does not open a device login, and a device-login ticket does not open an attach
        sid = self.client.create_session(TARGET, "new", "xterm-256color", 120, 40)["session"]["id"]
        session_ticket = self.client.ws_ticket(sid)["ticket"]
        other = self.client.create_device_login(TARGET, "u", "xterm-256color", 120, 40)
        self.assertEqual(await self.ws_close_code({**attach, "ticket": session_ticket,
                                                   "device_login_id": other["device_login_id"]}), 4003)
        self.assertEqual(await self.ws_close_code({"t": "attach", "v": 1, "ticket": other["ticket"],
                                                   "session_id": sid}), 4003)
        wrong = self.client.create_device_login(TARGET, "u", "xterm-256color", 120, 40)
        self.assertEqual(await self.ws_close_code({**attach, "ticket": wrong["ticket"],
                                                   "device_login_id": "some-other-login"}), 4003)

    async def test_a_prompted_connection_is_reusable_by_id_and_offered_to_its_principal(self):
        pending = self.client.create_device_login(TARGET, "u", "xterm-256color", 120, 40)
        login = await tcc.AsyncDeviceLogin.open(ws_url(self.client), pending, ssl_context=None)
        await login.recv(5)
        await login.answer_hostkey(True)
        await login.recv(5)
        await login.answer(1, [SYNTH, SYNTH])
        await login.recv(5)
        att = await login.attachment()
        prompted = att.session["connection_id"]
        await att.close()
        self.assertTrue(self.core.connections[prompted]["prompted"])
        # offered back: a reuse with no id, no login and no credential choice takes its principal's prompted connection
        implicit = self.client.create_session(TARGET, "reuse", "xterm-256color", 120, 40)["session"]
        self.assertEqual(implicit["connection_id"], prompted)
        # by id, for the principal who opened it, with no credential
        by_id = self.client.create_session(TARGET, "reuse", "xterm-256color", 120, 40, connection_id=prompted)
        self.assertEqual(by_id["session"]["connection_id"], prompted)
        # with a credential choice, no stored credential opened it
        with self.assertRaises(tcc.ApiError) as raised:
            self.client.create_session(TARGET, "reuse", "xterm-256color", 120, 40, connection_id=prompted,
                                       credential=("fixture-credential", "personal"))
        self.assertEqual(raised.exception.status, 422)


WS = "schemas/ws-frames.schema.json"
CTL = "schemas/ipc-control.schema.json"
OPENAPI_SCHEMAS = "openapi/tsb.openapi.json#/components/schemas/"
RELEASE_B_FIXTURES = {
    "C1": ["session-create-device-login-202", "session-create-device-login-needs-v0.7-503",
           "session-create-device-login-limit-429", "session-create-device-login-unacknowledged-409",
           "session-create-device-login-not-ssh-422", "session-create-device-login-no-join-403",
           "reuse-prompted-connection-by-id", "reuse-prompted-connection-offered-to-its-principal"],
    "C2": ["ws-frames-shape-device-login", "device-login-relay", "device-login-order", "device-login-hostkey-first-use",
           "device-login-ticket-once", "device-login-ticket-kinds", "device-login-cancel", "device-login-after-connected",
           "device-login-permission-lost", "device-login-answer-refused", "device-login-unattached-expires",
           "device-login-hostkey-audit-unwritable"],
    "C3": ["device-login-sequence", "hostkey-ask-before-any-auth", "hostkey-refused", "hostkey-revoked-refused-before-ask",
           "auth-failed-codes", "device-login-bad-answer-ends", "device-login-partial-success",
           "device-login-connect-without-login-timeout-refused", "device-login-disconnect-cancels",
           "device-login-peer-gone-drops", "device-login-version-gate", "hostkey-audit-unwritable-disconnects",
           "control-message-shape-v0.7",
           "control-message-shape-v0.6", "n1-v0.6-worker-positive"],
    "C5": ["config-login-timeout", "config-device-logins-max", "inventory-target-login-timeout"],
    "C6": ["event-target-auth-failed-prompted", "event-target-hostkey-refused", "event-session-connect-prompted",
           "connection-record-login"],
    "C8": ["recording-device-login-frames", "recording-device-login-own-prompt"],
}


def fixture(seam, name):
    return json.loads((CONTRACT / "fixtures" / seam / f"{name}.json").read_text(encoding="utf-8"))


class FixtureMarks(unittest.TestCase):
    def test_every_release_b_fixture_is_published_e3_and_required_green_e5(self):
        self.assertEqual(sum(len(v) for v in RELEASE_B_FIXTURES.values()), 44)
        for seam, names in RELEASE_B_FIXTURES.items():
            for name in names:
                with self.subTest(f"{seam}/{name}"):
                    self.assertEqual(fixture(seam, name)["mark"], {"published": "e3", "required-green": "e5"})

    def test_the_shape_fixture_is_an_instance_fixture_and_holds_each_frame_valid_and_invalid(self):
        fx = fixture("C2", "ws-frames-shape-device-login")
        self.assertEqual(fx["kind"], "instance")
        for t in ("device_login_attach", "device_login_hostkey_answer", "device_login_answer", "device_login_cancel",
                  "device_login_hostkey", "device_login_prompt", "device_login_result"):
            with self.subTest(t):
                verdicts = {i["valid"] for i in fx["case"]["instances"] if i["doc"].get("t") == t}
                self.assertEqual(verdicts, {True, False})

    def test_the_frozen_06_shape_sits_beside_the_new_07_one(self):
        self.assertEqual(fixture("C3", "control-message-shape-v0.6")["kind"], "instance")
        self.assertEqual(fixture("C3", "control-message-shape-v0.7")["kind"], "instance")

    def test_every_reference_fixture_has_an_owed_entry_naming_its_units(self):
        index = json.loads((CONTRACT / "fixtures" / "graded-by.json").read_text(encoding="utf-8"))
        for seam, names in RELEASE_B_FIXTURES.items():
            for name in names:
                fx = fixture(seam, name)
                if fx["grade"] != "reference":
                    self.assertNotIn(f"{seam}/{name}", index)      # live and artefact-graded ones have none
                    continue
                entries = index[f"{seam}/{name}"]
                self.assertEqual({e["unit"] for e in entries}, {g["unit"] for g in fx["graded_by"]})
                for e in entries:
                    self.assertIn("login-at-connect phase B", e["owed"])
                    self.assertNotIn("test_id", e)

    def test_every_credential_shaped_value_in_these_fixtures_is_the_synthetic_constant(self):
        for seam, names in RELEASE_B_FIXTURES.items():
            for name in names:
                found = selfcheck.credential_problems(fixture(seam, name)["case"], placeholders=True)
                self.assertEqual(found, [], f"{seam}/{name}")


class HostKeyAuditUnwritable(unittest.TestCase):
    """Derived 23, ruled 2026-11-01 15:00: an acceptance the core cannot audit ends the login with disconnect, never with
    hostkey_answer {accept: false}; the pane is told connect_failed, never hostkey_refused."""

    def test_the_worker_is_sent_disconnect_and_never_a_refusal_it_was_not_given(self):
        msgs = fixture("C3", "hostkey-audit-unwritable-disconnects")["case"]["messages"]
        sent = [m["msg"].get("cmd") for m in msgs if m["from"] == "core"]
        self.assertEqual(sent, ["connect", "disconnect"])
        pairs = [(m["from"], m["msg"]) for m in msgs]
        self.assertEqual(problems(pairs), [])
        # the same capture with a relayed refusal is also clean order-wise: the contract's rule is who sends what,
        # which the fixture's description fixes and the core's own test grades
        self.assertIn("never `hostkey_answer` with `accept` false", fixture("C3", "hostkey-audit-unwritable-disconnects")["description"])

    def test_the_pane_is_told_connect_failed(self):
        instances = fixture("C2", "device-login-hostkey-audit-unwritable")["case"]["instances"]
        outcomes = [i["doc"]["outcome"] for i in instances if i["doc"]["t"] == "device_login_result"]
        self.assertEqual(outcomes, ["connect_failed"])
        text = (CONTRACT / "protocols" / "device-login.md").read_text(encoding="utf-8")
        self.assertIn("never `hostkey_answer {accept: false}`", text)
        self.assertIn("`outcome: connect_failed`", text)
        self.assertIn("writes no `target.hostkey_refused`", text)


class Shapes(unittest.TestCase):
    """Hand-written verdicts for what the fixtures only illustrate."""

    def v(self, doc, ref):
        return REG.validate(doc, ref) == []

    def test_an_answer_may_hold_empty_strings_and_up_to_sixteen_of_them(self):
        frame = {"t": "device_login_answer", "v": 1, "prompt_id": 1}
        self.assertTrue(self.v({**frame, "answers": [""]}, WS))
        self.assertTrue(self.v({**frame, "answers": [""] * 16}, WS))
        self.assertTrue(self.v({**frame, "answers": ["x" * 1024]}, WS))
        self.assertFalse(self.v({**frame, "answers": ["x" * 1025]}, WS))
        self.assertFalse(self.v({**frame, "answers": [""] * 17}, WS))
        self.assertFalse(self.v({**frame, "answers": []}, WS))
        self.assertFalse(self.v({**frame, "answers": [1]}, WS))

    def test_the_text_bounds_are_in_characters(self):
        kbd = {"t": "device_login_prompt", "v": 1, "method": "keyboard-interactive", "attempt": 1, "prompt_id": 1,
               "name": "n" * 1024, "instruction": "i" * 4096, "prompts": [{"text": "t" * 1024, "echo": True}] * 16}
        self.assertTrue(self.v(kbd, WS))
        for field, value in (("name", "n" * 1025), ("instruction", "i" * 4097),
                             ("prompts", [{"text": "t" * 1025, "echo": True}]),
                             ("prompts", [{"text": "t", "echo": True}] * 17)):
            with self.subTest(field):
                self.assertFalse(self.v({**kbd, field: value}, WS))
        banner = {"t": "device_login_prompt", "v": 1, "method": "banner", "text": "b" * 16384}
        self.assertTrue(self.v(banner, WS))
        self.assertFalse(self.v({**banner, "text": "b" * 16385}, WS))
        # the same bounds on C3
        event = {"event": "auth_prompt", "conn_id": C, **{k: v for k, v in kbd.items() if k not in ("t", "v")}}
        self.assertTrue(self.v(event, CTL))
        self.assertFalse(self.v({**event, "name": "n" * 1025}, CTL))

    def test_prompt_id_is_present_exactly_when_prompts_is_not_empty(self):
        base = {"t": "device_login_prompt", "v": 1, "method": "keyboard-interactive", "attempt": 1, "name": "",
                "instruction": ""}
        one = [{"text": "x", "echo": False}]
        self.assertTrue(self.v({**base, "prompts": one, "prompt_id": 1}, WS))
        self.assertFalse(self.v({**base, "prompts": one}, WS))
        self.assertTrue(self.v({**base, "prompts": []}, WS))
        self.assertFalse(self.v({**base, "prompts": [], "prompt_id": 1}, WS))

    def test_the_eleven_outcomes_and_nothing_else(self):
        outcomes = ["connected", "refused", "timeout", "password_change_requested", "hostkey_refused",
                    "hostkey_changed", "cancelled", "connect_failed", "worker_unavailable", "too_long", "forbidden"]
        for outcome in outcomes:
            self.assertTrue(self.v({"t": "device_login_result", "v": 1, "outcome": outcome}, WS), outcome)
        self.assertFalse(self.v({"t": "device_login_result", "v": 1, "outcome": "failed"}, WS))

    def test_request_refused_and_the_close_code(self):
        for request in ("device_login_answer", "device_login_hostkey_answer"):
            for reason in ("not_pending", "invalid"):
                self.assertTrue(self.v({"t": "request_refused", "v": 1, "request": request, "reason": reason}, WS))
        self.assertTrue(self.v({"t": "request_refused", "v": 1, "request": "handoff", "reason": "not_allowed"}, WS))
        codes = REG.docs[WS]["x-close-codes"]
        self.assertEqual(codes["4016"]["name"], "device_login_ended")
        order = " ".join(REG.docs[WS]["x-attach-order"])
        for needle in ("device_login_attach", "4016", "4003", "4001", "4000", "4015"):
            self.assertIn(needle, order)

    def test_attempts_goes_with_a_prompted_login_and_never_with_a_supplied_one(self):
        def event(**detail):
            return {"ts": "2026-11-01T12:00:00Z", "action": "target.auth_failed", "user": "alice",
                    "target": "core-rtr-01", "detail": detail}
        audit = "schemas/audit-event.schema.json"
        self.assertTrue(self.v(event(login="prompted", attempts=1), audit))
        self.assertTrue(self.v(event(login="prompted", attempts=2, conn_id="c"), audit))
        self.assertTrue(self.v(event(login="supplied"), audit))
        self.assertTrue(self.v(event(login="supplied", conn_id="c"), audit))
        self.assertFalse(self.v(event(login="prompted"), audit))
        self.assertFalse(self.v(event(login="prompted", conn_id="c"), audit))
        self.assertFalse(self.v(event(login="supplied", attempts=1), audit))

    def test_the_c3_messages(self):
        connect = {"cmd": "connect", "target_id": "t", "protocol": "ssh", "host": "h", "port": 22, "user": "u",
                   "auth": {"kind": "prompt"}}
        self.assertTrue(self.v(connect, CTL))
        for ms, ok in ((10000, True), (1800000, True), (9999, False), (1800001, False)):
            self.assertEqual(self.v({**connect, "login_timeout": ms}, CTL), ok, ms)
        self.assertFalse(self.v({**connect, "auth": {"kind": "prompt", "password": SYNTH}}, CTL))
        # a telnet connect carries neither a prompt login nor a login_timeout
        telnet = {"cmd": "connect", "target_id": "t", "protocol": "telnet", "host": "h", "port": 23, "term": "xterm",
                  "cols": 80, "rows": 24, "timers": {}, "buffer_bytes": 1, "input_frame_max": 1}
        self.assertFalse(self.v({**telnet, "login_timeout": 10000}, CTL))
        self.assertTrue(self.v({"cmd": "hostkey_answer", "accept": True}, CTL))
        self.assertTrue(self.v({"event": "hostkey_ask", "conn_id": C, "kind": "first_use", "fingerprint": FP}, CTL))
        for code in ("refused", "login_timeout", "password_change_requested"):
            self.assertTrue(self.v({"event": "auth_failed", "conn_id": C, "reason": "x", "code": code}, CTL))
        self.assertTrue(self.v({"event": "error", "code": "hostkey_refused", "message": "x"}, CTL))

    def test_the_openapi_changes(self):
        auth = OPENAPI_SCHEMAS + "SessionLoginAuth"
        self.assertTrue(self.v({"kind": "prompt"}, auth))
        self.assertFalse(self.v({"kind": "prompt", "password": SYNTH}, auth))
        self.assertTrue(self.v({"kind": "password", "password": SYNTH}, auth))
        pending = OPENAPI_SCHEMAS + "DeviceLoginPending"
        good = {"device_login_id": "dl-1", "ticket": "FIXTURE-TICKET-000000000000000000", "expires_in": 30}
        self.assertTrue(self.v(good, pending))
        self.assertFalse(self.v({**good, "expires_in": 61}, pending))
        self.assertFalse(self.v({**good, "v": 1}, pending))
        op = _exchange.find_operation(REG, "POST", "/sessions")
        self.assertEqual(sorted(op.op["responses"]), ["201", "202", "401", "403", "404", "409", "422", "429", "502", "503"])
        self.assertTrue(self.v({"error": {"code": "rate_limited", "message": "x"}}, "openapi/tsb.openapi.json#/components/schemas/Error"))

    def test_the_knobs_and_the_records(self):
        cfg = fixture("C5", "config-valid")["case"]["instances"][0]["doc"]
        for name, value, ok in (("device_logins_max", 64, True), ("device_logins_max", 65, False),
                                ("device_logins_max", 0, False), ("login_timeout", "120s", True),
                                ("login_timeout", "soon", False)):
            doc = {**cfg, "worker": {**cfg["worker"], name: value}}
            self.assertEqual(self.v(doc, "schemas/config.schema.json"), ok, (name, value))
        required = REG.docs["schemas/config.schema.json"]["properties"]["worker"]["required"]
        self.assertNotIn("login_timeout", required)
        self.assertNotIn("device_logins_max", required)
        record = {"conn_id": "c", "worker_id": "w", "target_id": "t", "credential_ref": "supplied", "session_ids": []}
        ref = "schemas/continuity-state.schema.json#/$defs/connection_record"
        self.assertTrue(self.v(record, ref))                                    # an older core's record
        for login, cred, ok in (("prompted", "supplied", True), ("supplied", "supplied", True), ("stored", "supplied", False),
                                ("stored", "mine", True), ("prompted", "mine", False), ("supplied", "mine", False)):
            self.assertEqual(self.v({**record, "login": login, "credential_ref": cred}, ref), ok, (login, cred))


class CodecKeepsCodes6And7(unittest.TestCase):
    """The lines of codes 6 and 7 are unchanged; a device login's frames round-trip through the codec."""

    def test_a_device_logins_head_round_trips_with_an_empty_prompt_and_stamps_that_never_decrease(self):
        from tsbc import _binfmt as bf
        fx = fixture("C8", "recording-device-login-frames")
        raw = (CONTRACT / "fixtures" / "C8" / fx["case"]["file"]).read_bytes()
        side = (CONTRACT / "fixtures" / "C8" / fx["case"]["sidecar"]).read_bytes()
        frames = bf.decode_recording(raw, side)
        self.assertEqual(bf.encode_recording(frames), (raw, side))
        self.assertEqual(bf.recording_problems(frames) + bf.recording_stamp_problems(frames), [])
        self.assertEqual(frames[0].code, bf.OWN_PROMPT)                         # `login as:` comes first
        self.assertEqual(frames[0].own_prompt, "login as:")
        texts = [(f.code, f.login) for f in frames if f.code in bf.LOGIN_CODES]
        self.assertEqual(texts[0], (bf.LOGIN_ANSWER, "fixture-account"))
        self.assertIn((bf.LOGIN_PROMPT, ""), texts)                              # the empty prompt: the tag alone
        empty = next(f for f in frames if f.login == "" and f.code == bf.LOGIN_PROMPT)
        self.assertEqual(empty.data, b"\r\n[TSB: login prompt] \r\n")
        # a prompt after an earlier answer of its round takes that answer's stamp: equal stamps are allowed
        i = frames.index(empty)
        self.assertEqual((frames[i - 1].sec, frames[i - 1].usec), (empty.sec, empty.usec))
        self.assertEqual(bf.LOGIN_TAGS[bf.LOGIN_PROMPT], b"\r\n[TSB: login prompt] ")   # the line is unchanged
        self.assertEqual(sorted(bf.LOGIN_TAGS), [6, 7])


class OwnPromptCode8(unittest.TestCase):
    """Sidecar code 8, TSB's own prompt text, written by hand from ttyrec.schema.json x-own-prompt-line."""

    LOGIN_AS = b"\r\n[TSB: own prompt] login as:\r\n"
    PASSWORD = b"\r\n[TSB: own prompt] Password:\r\n"

    def frame(self, code, data, sec=1789000000, usec=0):
        from tsbc import _binfmt as bf
        return bf.RecFrame(sec, usec, code, data)

    def test_the_code_its_name_and_its_two_fixed_lines(self):
        from tsbc import _binfmt as bf
        self.assertEqual(bf.OWN_PROMPT, 8)
        self.assertEqual(bf.SIDECAR_VALUES[8], "own_prompt")
        self.assertEqual(bf.own_prompt_line("login as:"), self.LOGIN_AS)
        self.assertEqual(bf.own_prompt_line("Password:"), self.PASSWORD)
        self.assertEqual(bf.parse_own_prompt_line(self.LOGIN_AS), "login as:")
        self.assertEqual(bf.parse_own_prompt_line(self.PASSWORD), "Password:")
        self.assertEqual(bf.LOGIN_CODES, (6, 7))                                 # codes 6 and 7 keep their own lines

    def test_any_other_text_or_tag_is_no_line(self):
        from tsbc import _binfmt as bf
        for text in ("Passphrase:", "password:", "login as: ", "", "login as:\r\n"):
            with self.subTest(text=text), self.assertRaises(bf.FormatError):
                bf.own_prompt_line(text)
            self.assertIsNone(bf.parse_own_prompt_line(b"\r\n[TSB: own prompt] " + text.encode() + b"\r\n"))
        self.assertIsNone(bf.parse_own_prompt_line(b"\r\n[TSB: login answer] login as:\r\n"))
        self.assertIsNone(bf.parse_own_prompt_line(b"\r\n[TSB: login prompt] Password:\r\n"))
        self.assertIsNone(bf.parse_own_prompt_line(self.LOGIN_AS + b"\r\n"))
        self.assertIsNone(bf.parse_own_prompt_line(b"x" + self.LOGIN_AS))
        # codes 6 and 7 do not read code 8's tag, and 8 is no login code
        self.assertIsNone(bf.parse_login_line(bf.LOGIN_PROMPT, self.LOGIN_AS))
        self.assertIsNone(bf.parse_login_line(bf.LOGIN_ANSWER, self.PASSWORD))
        with self.assertRaises(bf.FormatError):
            bf.login_line(bf.OWN_PROMPT, "login as:")

    def test_an_older_reader_refuses_8_and_this_tag_refuses_unknown_codes(self):
        from tsbc import _binfmt as bf
        ttyrec, sidecar = bf.encode_recording([self.frame(8, self.LOGIN_AS)])
        self.assertEqual(sidecar, b"\x08")
        self.assertEqual(bf.decode_recording(ttyrec, sidecar)[0].own_prompt, "login as:")
        with self.assertRaises(bf.FormatError):
            bf.decode_recording(ttyrec, b"\x0a")

    def test_a_device_logins_head_with_both_prompts_is_a_valid_recording(self):
        from tsbc import _binfmt as bf
        user = bf.login_line(bf.LOGIN_ANSWER, "u")
        frames = [self.frame(8, self.LOGIN_AS), self.frame(7, user), self.frame(8, self.PASSWORD, usec=1),
                  self.frame(7, user, usec=2), self.frame(0, b"$ ", sec=1789000001)]
        self.assertEqual(bf.recording_problems(frames) + bf.recording_stamp_problems(frames), [])
        self.assertEqual([f.own_prompt for f in frames], ["login as:", None, "Password:", None, None])
        self.assertEqual([f.login for f in frames], [None, "u", None, "u", None])

    def test_code_8_stands_in_the_head_carries_its_line_and_is_not_session_output(self):
        from tsbc import _binfmt as bf
        for first in (self.frame(0, b"$ "), self.frame(1, b"x"), self.frame(5, bf.broker_line("wiggle"))):
            with self.subTest(code=first.code):
                frames = [first, self.frame(8, self.PASSWORD, sec=1789000002)]
                self.assertTrue(any("stand after frame" in p for p in bf.recording_problems(frames)))
        self.assertTrue(any("not the own-prompt line" in p for p in bf.recording_problems([self.frame(8, b"")]) + bf.recording_problems([self.frame(8, b"Password:")])))
        self.assertTrue(bf.recording_problems([self.frame(8, b"\r\n[TSB: own prompt] Other:\r\n")]))
        self.assertTrue(bf.recording_problems([self.frame(7, self.LOGIN_AS)]), "an own-prompt line on code 7")

    def test_code_8_is_on_the_input_clock_and_compared_line_for_line(self):
        from tsbc import _binfmt as bf
        ok = [self.frame(8, self.LOGIN_AS, sec=1789000005), self.frame(0, b"$ ", sec=1789000001)]
        self.assertEqual(bf.recording_stamp_problems(ok), [])
        back = [self.frame(7, bf.login_line(bf.LOGIN_ANSWER, "u"), sec=1789000005), self.frame(8, self.PASSWORD, sec=1789000004)]
        self.assertTrue(bf.recording_stamp_problems(back))
        plain = [self.frame(0, b"$ "), self.frame(1, b"id\r", sec=1789000001)]
        a = [self.frame(8, self.LOGIN_AS), *plain]
        self.assertFalse(bf.compare_recordings(plain, a)["payload_identical"]["in"])
        self.assertTrue(bf.compare_recordings(a, [self.frame(8, self.LOGIN_AS, usec=5), *plain])["payload_identical"]["in"])
        b = [self.frame(8, self.PASSWORD), *plain]
        self.assertFalse(bf.compare_recordings(a, b)["payload_identical"]["in"])

    def test_the_json_form_names_the_marker_and_the_schema_holds_the_two_lines(self):
        from tsbc import _binfmt as bf
        for data in (self.LOGIN_AS, self.PASSWORD):
            doc = bf.record_json(self.frame(8, data))
            self.assertEqual(doc["marker"], "own_prompt")
            self.assertEqual(bf.record_from_json(doc), self.frame(8, data))
            self.assertEqual(REG.validate(doc, "schemas/ttyrec.schema.json"), [])
        doc = bf.record_json(self.frame(8, self.LOGIN_AS))
        for bad in ({**doc, "marker": "login_prompt"},
                    {**doc, "data_hex": b"\r\n[TSB: own prompt] Passphrase:\r\n".hex(), "length": 33},
                    {**doc, "data_hex": b"\r\n[TSB: login answer] login as:\r\n".hex(), "length": 34}):
            self.assertNotEqual(REG.validate(bad, "schemas/ttyrec.schema.json"), [])
        for d in (bf.record_json(self.frame(7, bf.login_line(bf.LOGIN_ANSWER, "u"))),):
            self.assertNotEqual(REG.validate({**d, "marker": "own_prompt"}, "schemas/ttyrec.schema.json"), [])

    def test_the_published_line_and_sidecar_agree_with_the_codec(self):
        from tsbc import _binfmt as bf
        doc = REG.docs["schemas/ttyrec.schema.json"]
        self.assertEqual(doc["x-sidecar"]["values"]["8"], "own_prompt")
        line = doc["x-own-prompt-line"]
        pattern = selfcheck.compile_published(line["pattern"])
        for text in ("login as:", "Password:"):
            self.assertTrue(pattern.search(bf.own_prompt_line(text).decode("utf-8")), text)
        for bad in ("\r\n[TSB: own prompt] Passphrase:\r\n", "\r\n[TSB: own prompt] login as: \r\n",
                    "\r\n[TSB: login answer] login as:\r\n", "\r\n[TSB: own prompt] login as:"):
            self.assertFalse(pattern.search(bad), bad)
        self.assertEqual(line["texts"], ["login as:", "Password:"])

    def test_code_8_is_written_with_the_login_frames_and_a_supplied_login_has_none(self):
        text = (CONTRACT / "schemas" / "ttyrec.schema.json").read_text(encoding="utf-8")
        doc = json.loads(text)
        rule = next(r for r in doc["x-sidecar"]["rules"] if r.startswith("6 marks a login prompt"))
        self.assertIn("A supplied login is two code-7 frames, the username then the password, and no code-6 frame", rule)
        self.assertIn("a supplied login has none either", rule)
        self.assertIn("8 marks TSB's own prompt", rule)

    def test_the_collection_gate_keeps_code_8_and_still_refuses_code_7(self):
        from tsbc import _binfmt as bf
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "recordings").mkdir()
            kept, refused = root / "recordings" / "s1.dir", root / "recordings" / "s2.dir"
            kept.write_bytes(bytes([bf.OWN_PROMPT, bf.LOGIN_PROMPT, bf.OUT]))
            refused.write_bytes(bytes([bf.OWN_PROMPT, bf.LOGIN_ANSWER, bf.OUT]))
            findings = artifacts._login_answer_findings(root, [kept, refused])
        self.assertEqual(len(findings), 1)
        self.assertIn("s2.dir", findings[0])

    def test_the_new_fixture_is_the_codec_s_bytes_and_holds_the_orders_it_names(self):
        from tsbc import _binfmt as bf
        fx = fixture("C8", "recording-device-login-own-prompt")
        base = CONTRACT / "fixtures" / "C8"
        frames = bf.decode_recording((base / fx["case"]["file"]).read_bytes(), (base / fx["case"]["sidecar"]).read_bytes())
        self.assertEqual([f.code for f in frames], [8, 7, 6, 7, 8, 7, 0, 1])
        self.assertEqual([f.own_prompt for f in frames if f.code == 8], ["login as:", "Password:"])
        self.assertEqual(len(fx["case"]["invalid"]), 3)


if __name__ == "__main__":
    unittest.main()


class CreateShapes(unittest.TestCase):
    def test_a_connection_id_that_is_no_string_is_refused_422_before_any_lookup(self):
        core = ServeApp()
        for bad in ([], {}, ["a"], 1, None, True):
            with self.subTest(connection_id=repr(bad)):
                body = {"target_id": SERVE_TARGET, "connection": "reuse", "term": "xterm-256color", "cols": 120, "rows": 40,
                        "connection_id": bad}
                status, _, reply = core._create(SERVE_USER, body)
                self.assertEqual((status, reply["error"]["code"]), (422, "invalid_request"))
