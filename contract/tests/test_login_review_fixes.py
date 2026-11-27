"""Review fixes on the supplied login (release A of login-at-connect), ruled by the orchestrator 2026-10-29 15:36.

Each class holds one finding's rule, with expectations written by hand: the warning's signal is the effective
value (2); a collected recording holds no login answer (4); a supplied-login connection is never reused (5); a
login that would be recorded needs the request's explicit consent (6). Finding 3 (the additivity exemption)
is in test_additivity.py.
"""

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import tsb_contract_client as tcc
from tsbc import _binfmt as bf
from tsbc import _serve, artifacts
from tsbc._run import Run
from tsbc.schema import Registry

from . import CONTRACT
from .stub_core import NOCRED, PASSWORD, PEER, TARGET, USER, StubCore
from .stub_server import StubServer

REG = Registry.from_tree(CONTRACT)
OPENAPI = "openapi/tsb.openapi.json#/components/schemas/"
ME = OPENAPI + "Me"
SESSION_LOGIN = OPENAPI + "SessionLogin"
ERROR = "schemas/error.schema.json"
SYNTHETIC = "FIXTURE-NOT-A-PASSWORD"
UNACKNOWLEDGED = "login_recording_not_acknowledged"
APPS = {"the served app": _serve.ServeApp, "the stub core": StubCore}


def login(**extra):
    return {"user": "root", "auth": {"kind": "password", "password": SYNTHETIC}, **extra}


class Refused(Exception):
    """A non-2xx answer to the create, with its decoded body (the client's ApiError keeps no body)."""

    def __init__(self, status: int, body: dict) -> None:
        super().__init__(f"HTTP {status}")
        self.status, self.body = status, body
        self.code = body.get("error", {}).get("code")


def _post(url: str, path: str, body: dict, cookie: str | None = None):
    headers = {"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})}
    request = urllib.request.Request(url + path, data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request) as reply:
            return reply.status, reply.headers, json.loads(reply.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, err.headers, json.loads(err.read() or b"null")


def create(url, target=TARGET, connection="new", user=USER, **extra):
    _, headers, _ = _post(url, "/auth/login", {"username": user, "password": PASSWORD})
    cookie = headers["Set-Cookie"].split(";")[0]
    body = {"target_id": target, "connection": connection, "term": "xterm-256color", "cols": 120, "rows": 40, **extra}
    status, _, doc = _post(url, "/sessions", body, cookie)
    if status != 201:
        raise Refused(status, doc)
    return doc["session"]


class TheWarningsSignalIsTheEffectiveValue(unittest.TestCase):
    """`Me.recording.login_input` is true only while recording.enabled and recording.login_input are both on."""

    def me(self, app) -> dict:
        with StubServer(app) as url:
            c = tcc.Client(url)
            c.login(USER, PASSWORD)
            return c.me()

    def test_it_is_true_only_while_recording_is_enabled_and_the_knob_is_on(self):
        for name, make in APPS.items():
            for recording, knob, want in ((True, True, True), (True, False, False),
                                          (False, True, False), (False, False, False)):
                with self.subTest(app=name, recording_enabled=recording, knob=knob):
                    me = self.me(make(recording=recording, login_input=knob))
                    self.assertEqual(me["recording"], {"login_input": want})
                    self.assertEqual(REG.validate(me, ME), [])

    def test_the_knob_is_off_unless_it_is_turned_on(self):
        for name, make in APPS.items():
            with self.subTest(app=name):
                self.assertEqual(self.me(make())["recording"], {"login_input": False})

    def test_the_contract_says_so_in_me_and_in_step_22(self):
        openapi = json.loads((CONTRACT / "openapi/tsb.openapi.json").read_text(encoding="utf-8"))
        me = openapi["components"]["schemas"]["Me"]["properties"]["recording"]["properties"]["login_input"]
        self.assertIn("only while", me["description"])
        self.assertIn("recording.enabled", me["description"])
        step22 = next(row for row in (CONTRACT / "protocols/session-open.md").read_text(encoding="utf-8").splitlines()
                      if row.startswith("| 22 |"))
        self.assertIn("recording.enabled", step22)
        self.assertIn("effective", step22)


class ACollectedRecordingHoldsNoLoginAnswer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def collect(self, frames, sid="sess-1") -> Run:
        ttyrec, sidecar = bf.encode_recording(frames)
        (self.dir / "recordings").mkdir()
        (self.dir / f"recordings/{sid}.ttyrec").write_bytes(ttyrec)
        (self.dir / f"recordings/{sid}.dir").write_bytes(sidecar)
        (self.dir / "collected.json").write_text(json.dumps({
            "collected_at": "2026-10-29T12:05:00Z", "node_role": "lab",
            "items": [{"seam": "C8", "kind": "recording", "path": f"recordings/{sid}.ttyrec"},
                      {"seam": "C8", "kind": "recording-direction", "path": f"recordings/{sid}.dir"}]}))
        return Run(CONTRACT, REG, "e4", None, self.dir, None, 1.0)

    def frame(self, code, text=None, data=None, usec=0):
        return bf.RecFrame(1789000000, usec, code, data if data is not None else bf.login_line(code, text))

    def test_a_collection_holding_a_code_7_frame_is_refused_and_names_the_file(self):
        run = self.collect([self.frame(7, "root"), self.frame(7, "hunter2", usec=1), self.frame(0, data=b"$ ", usec=2)])
        got = artifacts.load_collected(run)
        self.assertIsInstance(got, str, "a collection holding a login answer must not load")
        self.assertIn("recordings/sess-1.dir", got)
        self.assertIn("login answer", got)
        self.assertNotIn("hunter2", got)

    def test_the_refusal_fails_every_artefact_fixture_not_one(self):
        run = self.collect([self.frame(7, "root"), self.frame(0, data=b"$ ", usec=1)])
        ok, detail = artifacts.grade("recordings-parse", run)
        self.assertFalse(ok)
        self.assertIn("recordings/sess-1.dir", detail)

    def test_a_recording_that_is_not_listed_but_sits_in_the_directory_is_refused_too(self):
        run = self.collect([self.frame(0, data=b"$ ")])
        (self.dir / "recordings/sess-2.ttyrec").write_bytes(bf.encode_recording([self.frame(7, "root")])[0])
        (self.dir / "recordings/sess-2.dir").write_bytes(b"\x07")
        got = artifacts.load_collected(run)
        self.assertIsInstance(got, str)
        self.assertIn("recordings/sess-2.dir", got)
        self.assertNotIn("sess-1", got)

    def test_a_code_6_prompt_is_not_refused(self):
        run = self.collect([self.frame(6, "Password:"), self.frame(0, data=b"$ ", usec=1)])
        self.assertNotIsInstance(artifacts.load_collected(run), str)

    def test_a_recording_with_no_login_frame_loads(self):
        run = self.collect([self.frame(0, data=b"$ "), self.frame(1, data=b"ls", usec=1)])
        self.assertNotIsInstance(artifacts.load_collected(run), str)

    def test_the_schema_and_the_readme_state_the_rule(self):
        schema = json.loads((CONTRACT / "schemas/artifacts-collected.schema.json").read_text(encoding="utf-8"))
        for text in (schema["description"], (CONTRACT / "README.md").read_text(encoding="utf-8")):
            text = " ".join(text.split())  # the README wraps its lines
            self.assertIn("a collected recording must not hold a code-7 login answer frame", text)
            self.assertIn("leaves such a recording out", text)


class ASuppliedLoginConnectionIsNeverReused(unittest.TestCase):
    """session-open step 4: not implicitly, not by connection_id, not in a 409's connection_ids."""

    def test_the_connection_keeps_a_marker_that_it_was_opened_with_a_supplied_login(self):
        for name, make in APPS.items():
            with self.subTest(app=name):
                app = make()
                with StubServer(app) as url:
                    supplied = create(url, connection="new", login=login())
                    stored = create(url, connection="new")
                self.assertIs(app.connections[supplied["connection_id"]]["supplied_login"], True)
                self.assertIs(app.connections[stored["connection_id"]]["supplied_login"], False)

    def test_a_reuse_with_no_connection_id_does_not_take_it(self):
        for name, make in APPS.items():
            with self.subTest(app=name), StubServer(make()) as url:
                supplied = create(url, connection="new", login=login())
                again = create(url, connection="reuse")
                self.assertNotEqual(again["connection_id"], supplied["connection_id"])

    def test_a_reuse_naming_it_by_connection_id_is_refused(self):
        for name, make in APPS.items():
            with self.subTest(app=name), StubServer(make()) as url:
                supplied = create(url, connection="new", login=login())
                with self.assertRaises(Refused) as raised:
                    create(url, connection="reuse", connection_id=supplied["connection_id"])
                self.assertEqual((raised.exception.status, raised.exception.code), (422, "invalid_request"))

    def test_a_409_choice_never_lists_it(self):
        for name, make in APPS.items():
            with self.subTest(app=name), StubServer(make()) as url:
                supplied = create(url, connection="new", login=login())
                first = create(url, connection="new")
                second = create(url, connection="new")
                with self.assertRaises(Refused) as raised:
                    create(url, connection="reuse")
                self.assertEqual(raised.exception.code, "connection_choice_required")
                listed = raised.exception.body["error"]["connection_ids"]
                self.assertEqual(sorted(listed), sorted([first["connection_id"], second["connection_id"]]))
                self.assertNotIn(supplied["connection_id"], listed)

    def test_a_supplied_connection_beside_one_stored_one_is_no_choice(self):
        # Counting it as a candidate would make two, and the core would ask the operator to pick.
        for name, make in APPS.items():
            with self.subTest(app=name), StubServer(make()) as url:
                supplied = create(url, connection="new", login=login())
                stored = create(url, connection="new")
                again = create(url, connection="reuse")
                self.assertEqual(again["connection_id"], stored["connection_id"])
                self.assertNotEqual(again["connection_id"], supplied["connection_id"])

    def test_the_marker_is_the_servers_own_and_a_peers_connections_are_untouched(self):
        with StubServer(_serve.ServeApp()) as url:
            create(url, connection="new", login=login())
            peer_conn = create(url, connection="new", user=PEER)
            self.assertEqual(create(url, connection="reuse", user=PEER)["connection_id"], peer_conn["connection_id"])


class ALoginThatWouldBeRecordedNeedsConsent(unittest.TestCase):
    """SessionLogin.recording_acknowledged; the 409 `login_recording_not_acknowledged`."""

    def refused(self, app, **extra):
        with StubServer(app) as url, self.assertRaises(Refused) as raised:
            create(url, **extra)
        return raised.exception

    def test_the_field_is_an_optional_boolean_of_the_login(self):
        self.assertEqual(REG.validate(login(), SESSION_LOGIN), [])
        self.assertEqual(REG.validate(login(recording_acknowledged=True), SESSION_LOGIN), [])
        self.assertEqual(REG.validate(login(recording_acknowledged=False), SESSION_LOGIN), [])
        for bad in ("yes", 1, None, {}):
            with self.subTest(bad=bad):
                self.assertNotEqual(REG.validate(login(recording_acknowledged=bad), SESSION_LOGIN), [])
        request = OPENAPI + "SessionCreateRequest"
        body = {"target_id": "t1", "connection": "new", "term": "xterm", "cols": 80, "rows": 24}
        self.assertEqual(REG.validate({**body, "login": login(recording_acknowledged=True)}, request), [])
        self.assertNotEqual(REG.validate({**body, "recording_acknowledged": True}, request), [],
                            "the acknowledgement belongs to the login, not to the request")

    def test_the_code_is_in_the_registry_with_its_status(self):
        schema = json.loads((CONTRACT / ERROR).read_text(encoding="utf-8"))
        self.assertIn(UNACKNOWLEDGED, schema["$defs"]["code"]["enum"])
        self.assertEqual(schema["x-codes"][UNACKNOWLEDGED]["status"], 409)
        doc = {"error": {"code": UNACKNOWLEDGED, "message": "the login would be recorded; acknowledge it"}}
        self.assertEqual(REG.validate(doc, ERROR), [])
        self.assertEqual(set(schema["x-codes"]), set(schema["$defs"]["code"]["enum"]))

    def test_without_the_field_a_login_that_would_be_recorded_is_409_and_nothing_happens(self):
        for name, make in APPS.items():
            for extra in ({"login": login()}, {"login": login(recording_acknowledged=False)}):
                with self.subTest(app=name, login=sorted(extra["login"])):
                    app = make(login_input=True)
                    err = self.refused(app, **extra)
                    self.assertEqual((err.status, err.code), (409, UNACKNOWLEDGED))
                    self.assertEqual(REG.validate(err.body, ERROR), [])
                    self.assertNotIn(SYNTHETIC, json.dumps(err.body))
                    self.assertNotIn("connection_ids", err.body["error"])
                    # nothing was opened, so nothing could reach a worker or a recording
                    self.assertEqual((app.connections, app.sessions), ({}, {}))

    def test_with_the_field_set_the_same_request_is_accepted(self):
        for name, make in APPS.items():
            with self.subTest(app=name), StubServer(make(login_input=True)) as url:
                got = create(url, login=login(recording_acknowledged=True))
                self.assertEqual(got["target_id"], TARGET)

    def test_an_acknowledged_request_with_the_knob_off_is_accepted(self):
        for name, make in APPS.items():
            with self.subTest(app=name), StubServer(make(login_input=False)) as url:
                got = create(url, login=login(recording_acknowledged=True))
                self.assertEqual(got["target_id"], TARGET)

    def test_with_the_knob_off_or_nothing_recorded_no_acknowledgement_is_asked(self):
        for name, make in APPS.items():
            for kwargs in ({"login_input": False}, {}, {"login_input": True, "recording": False}):
                with self.subTest(app=name, kwargs=kwargs), StubServer(make(**kwargs)) as url:
                    self.assertEqual(create(url, login=login())["target_id"], TARGET)

    def test_the_effective_value_is_what_decides_and_a_surface_can_read_it(self):
        # What /auth/me says is exactly when the 409 comes: a surface that warned on it never meets a
        # surprise, and one that did not warn is refused until it does.
        for name, make in APPS.items():
            for kwargs in ({"login_input": True}, {"login_input": True, "recording": False}, {"login_input": False}):
                with self.subTest(app=name, kwargs=kwargs), StubServer(make(**kwargs)) as url:
                    c = tcc.Client(url)
                    c.login(USER, PASSWORD)
                    asked = c.me()["recording"]["login_input"]
                    try:
                        create(url, login=login())
                        refused = False
                    except Refused as err:
                        self.assertEqual(err.code, UNACKNOWLEDGED)
                        refused = True
                    self.assertEqual(refused, asked)

    def test_a_request_without_a_login_is_never_asked_for_one(self):
        for name, make in APPS.items():
            with self.subTest(app=name), StubServer(make(login_input=True)) as url:
                self.assertEqual(create(url)["target_id"], TARGET)

    def test_the_other_refusals_come_first(self):
        # The request is known well formed, its target visible and ssh, before consent is asked.
        app = _serve.ServeApp(login_input=True)
        bad_login = {"user": "", "auth": {"kind": "password", "password": SYNTHETIC}}
        with StubServer(app) as url:
            for kwargs, want in (({"login": bad_login}, (422, "invalid_request")),
                                 ({"target": "no-such-target", "login": login()}, (404, "target_not_found")),
                                 ({"target": "telnet-scenario", "login": login()}, (422, "invalid_request"))):
                with self.subTest(kwargs=kwargs), self.assertRaises(Refused) as raised:
                    create(url, **kwargs)
                self.assertEqual((raised.exception.status, raised.exception.code), want)

    def test_a_target_with_no_stored_credential_is_asked_too(self):
        app = StubCore(login_input=True)
        err = self.refused(app, target=NOCRED, login=login())
        self.assertEqual((err.status, err.code), (409, UNACKNOWLEDGED))

    def test_the_refusal_has_its_fixture_and_the_protocol_names_it(self):
        path = CONTRACT / "fixtures/C1/session-create-login-unacknowledged-409.json"
        fx = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(fx["mark"], {"published": "e3", "required-green": "e4"})
        self.assertEqual((fx["kind"], fx["grade"]), ("rest", "reference"))
        step = fx["case"]["steps"][0]
        self.assertEqual(step["expect"]["status"], 409)
        self.assertIn({"pointer": "/error/code", "eq": UNACKNOWLEDGED}, step["expect"]["match"])
        self.assertEqual(REG.validate(step["expect"]["example"], ERROR), [])
        self.assertNotIn("recording_acknowledged", json.dumps(step["http"]["body"]))
        protocol = (CONTRACT / "protocols/session-open.md").read_text(encoding="utf-8")
        self.assertIn(UNACKNOWLEDGED, protocol)
        self.assertIn("session-create-login-unacknowledged-409", protocol)
        self.assertIn(UNACKNOWLEDGED, (CONTRACT / "CHANGELOG.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
