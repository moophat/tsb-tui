"""Release A of login-at-connect: a login supplied on session create, and the recording knob.

What this release adds is checked here against expectations written out by hand, never from the code
under test: the request shape (and that every request that was valid before still is), the escape
rule of the login line byte by byte, the head-of-recording rule, the knob, the audit events, the
client, and the served fixture app. The fixtures are graded by self-check; these tests hold the
rules the fixtures only illustrate.
"""

import io
import json
import logging
import unittest
from contextlib import redirect_stderr, redirect_stdout
from typing import ClassVar

from tsbc import _binfmt as bf
from tsbc import _serve, selfcheck
from tsbc.schema import Registry

from . import CONTRACT
from .stub_core import NOCRED, PASSWORD, TARGET, USER, StubCore
from .stub_server import StubServer

REG = Registry.from_tree(CONTRACT)
REQUEST = "openapi/tsb.openapi.json#/components/schemas/SessionCreateRequest"
ME = "openapi/tsb.openapi.json#/components/schemas/Me"
SYNTHETIC = "FIXTURE-NOT-A-PASSWORD"
RELEASE_A_FIXTURES = {"session-create-supplied-login", "session-create-login-on-reuse-422",
                      "session-create-login-not-ssh-422", "session-create-login-never-echoed",
                      "control-connect-supplied-login", "event-session-connect-login", "event-target-auth-failed",
                      "recording-login-frames", "login-line-escape", "policy-login-input"}


def body(connection="new", **extra):
    base = {"target_id": "t1", "connection": connection, "term": "xterm-256color", "cols": 120, "rows": 40}
    return {**base, **extra}


def login(user="root", password=SYNTHETIC, kind="password"):
    return {"user": user, "auth": {"kind": kind, "password": password}}


class RequestShape(unittest.TestCase):
    def valid(self, doc):
        return REG.validate(doc, REQUEST) == []

    def test_every_request_that_was_valid_before_still_is(self):
        self.assertTrue(self.valid(body("new")))
        self.assertTrue(self.valid(body("reuse")))
        self.assertTrue(self.valid(body("reuse", connection_id="conn-1")))

    def test_a_login_is_allowed_on_a_new_connection(self):
        self.assertTrue(self.valid(body("new", login=login())))

    def test_a_login_on_a_reuse_is_refused_by_shape(self):
        self.assertFalse(self.valid(body("reuse", login=login())))
        self.assertFalse(self.valid(body("reuse", connection_id="conn-1", login=login())))

    def test_the_login_is_closed_and_needs_user_and_a_password_auth(self):
        for name, bad in {
            "no user": {"auth": {"kind": "password", "password": SYNTHETIC}},
            "no auth": {"user": "root"},
            "extra key": {**login(), "target": "t1"},
            "empty user": login(user=""),
            "user past 256": login(user="u" * 257),
            "empty password": login(password=""),
            "password past 1024 characters": login(password="p" * 1025),
            "a key is not a branch of this release": {"user": "root", "auth": {"kind": "key", "key": SYNTHETIC}},
            # Release B adds the prompt branch ({"kind": "prompt"}, device login); test_device_login_release_b holds it
            "flat password": {"user": "root", "password": SYNTHETIC},
            "extra auth key": {"user": "root", "auth": {"kind": "password", "password": SYNTHETIC, "x": 1}},
        }.items():
            with self.subTest(name):
                self.assertFalse(self.valid(body("new", login=bad)))

    def test_the_bounds_are_the_published_ones(self):
        self.assertTrue(self.valid(body("new", login=login(user="u" * 256, password="p" * 1024))))

    def test_me_carries_the_knob_and_stays_valid_without_it(self):
        self.assertEqual(REG.validate({"username": "alice"}, ME), [])
        self.assertEqual(REG.validate({"username": "alice", "recording": {"login_input": True}}, ME), [])
        for bad in ({"username": "alice", "recording": {"login_input": "yes"}},
                    {"username": "alice", "recording": {}},
                    {"username": "alice", "recording": {"login_input": True, "enabled": True}},
                    {"username": "alice", "x": 1}):
            with self.subTest(bad=bad):
                self.assertNotEqual(REG.validate(bad, ME), [])

    def test_login_still_answers_the_principal_alone(self):
        principal = "openapi/tsb.openapi.json#/components/schemas/Principal"
        self.assertEqual(REG.validate({"username": "alice"}, principal), [])
        self.assertNotEqual(REG.validate({"username": "alice", "recording": {"login_input": True}}, principal), [])


class Knob(unittest.TestCase):
    def valid(self, recording):
        doc = {"users": {"alice": {"permissions": ["session.create"], "targets": ["*"]}}, "recording": recording}
        return REG.validate(doc, "schemas/policy.schema.json") == []

    def test_a_boolean_beside_enabled_and_absent_is_off(self):
        self.assertTrue(self.valid({"enabled": True, "login_input": True}))
        self.assertTrue(self.valid({"enabled": False, "login_input": False}))
        self.assertTrue(self.valid({"enabled": True}))

    def test_nothing_else_is_a_knob(self):
        self.assertFalse(self.valid({"enabled": True, "login_input": 1}))
        self.assertFalse(self.valid({"login_input": True}))
        self.assertFalse(self.valid({"enabled": True, "login_input": True, "per_target": True}))


class Audit(unittest.TestCase):
    BASE: ClassVar[dict] = {"ts": "2026-10-29T12:00:00Z", "user": "alice", "target": "core-rtr-01"}

    def valid(self, doc):
        return REG.validate(doc, "schemas/audit-event.schema.json") == []

    def test_auth_failed_is_for_a_supplied_login_and_carries_no_reason(self):
        ok = {**self.BASE, "action": "target.auth_failed", "detail": {"login": "supplied", "conn_id": "c1"}}
        self.assertTrue(self.valid(ok))
        self.assertTrue(self.valid({**ok, "detail": {"login": "supplied"}}))
        for name, bad in {
            "stored": {**ok, "detail": {"login": "stored"}},
            "no source": {**ok, "detail": {"conn_id": "c1"}},
            "the device's reason": {**ok, "detail": {"login": "supplied", "reason": "Permission denied"}},
            "a password": {**ok, "detail": {"login": "supplied", "password": SYNTHETIC}},
            "a session": {**ok, "session_id": "s1"},
            "no detail": {k: v for k, v in ok.items() if k != "detail"},
        }.items():
            with self.subTest(name):
                self.assertFalse(self.valid(bad))

    def test_session_connect_names_the_source_and_whether_it_was_recorded(self):
        ok = {**self.BASE, "action": "session.connect", "session_id": "s1", "session_type": "shell"}
        self.assertTrue(self.valid({**ok, "detail": {"login": "supplied", "login_recorded": True}}))
        self.assertTrue(self.valid({**ok, "detail": {"login": "stored"}}))
        self.assertTrue(self.valid({**ok, "detail": {"anything": "else"}}), "the object stays open as before")
        self.assertFalse(self.valid({**ok, "detail": {"login": "typed"}}))
        self.assertFalse(self.valid({**ok, "detail": {"login_recorded": "yes"}}))


class LoginLine(unittest.TestCase):
    """The escape rule, written out by hand from ttyrec.schema.json x-login-line."""

    ANSWER = b"\r\n[TSB: login answer] "
    PROMPT = b"\r\n[TSB: login prompt] "

    def test_plain_text_is_the_tag_the_text_and_crlf(self):
        self.assertEqual(bf.login_line(bf.LOGIN_ANSWER, "operator"), self.ANSWER + b"operator\r\n")
        self.assertEqual(bf.login_line(bf.LOGIN_PROMPT, "Password:"), self.PROMPT + b"Password:\r\n")
        self.assertEqual(bf.login_line(bf.LOGIN_ANSWER, ""), self.ANSWER + b"\r\n")

    def test_a_backslash_is_two_and_a_control_is_backslash_x_and_lowercase_hex(self):
        self.assertEqual(bf.login_line(bf.LOGIN_ANSWER, "corp\\op"), self.ANSWER + b"corp\\\\op\r\n")
        self.assertEqual(bf.login_line(bf.LOGIN_ANSWER, "a\x1bb"), self.ANSWER + b"a\\x1bb\r\n")
        self.assertEqual(bf.login_line(bf.LOGIN_ANSWER, "a\r\nb"), self.ANSWER + b"a\\x0d\\x0ab\r\n")
        self.assertEqual(bf.login_line(bf.LOGIN_ANSWER, "\x00\x1f\x7f"), self.ANSWER + b"\\x00\\x1f\\x7f\r\n")
        self.assertEqual(bf.login_line(bf.LOGIN_ANSWER, "\x80\x85\x9f"), self.ANSWER + b"\\x80\\x85\\x9f\r\n")
        # the first characters on each side of every range stay as they are
        self.assertEqual(bf.login_line(bf.LOGIN_ANSWER, " ~\u00a0"), self.ANSWER + b" ~\xc2\xa0\r\n")

    def test_other_text_stays_utf8(self):
        self.assertEqual(bf.login_line(bf.LOGIN_ANSWER, "\u00e9\u4e2d"), self.ANSWER + b"\xc3\xa9\xe4\xb8\xad\r\n")

    def test_a_line_is_one_row_whatever_the_value(self):
        hostile = "x\x1b[2J\r\n\x85\x9b\x07\x00\\\\\\x41"
        line = bf.login_line(bf.LOGIN_PROMPT, hostile)
        inner = line[len(self.PROMPT):-2]
        for byte in (b"\x1b", b"\r", b"\n", b"\x07", b"\x00"):
            self.assertNotIn(byte, inner)
        self.assertNotIn("\u0085".encode(), inner)
        self.assertNotIn("\u009b".encode(), inner)
        self.assertEqual(bf.parse_login_line(bf.LOGIN_PROMPT, line), hostile)

    def test_the_escape_undoes_exactly(self):
        for value in ("", "a", "\\", "\\x41", "\\\\", "\x1b", "\x7f", "\x85", "a\x00b", "\u00e9\u4e2d", "x\\x1b"):
            with self.subTest(value=value):
                line = bf.login_line(bf.LOGIN_ANSWER, value)
                self.assertEqual(bf.parse_login_line(bf.LOGIN_ANSWER, line), value)

    def test_the_escape_is_the_only_encoding(self):
        for name, data in {
            "an escape of a character that needs none": self.ANSWER + b"\\x41\r\n",
            "uppercase hex": self.ANSWER + b"\\x1B\r\n",
            "a short escape": self.ANSWER + b"\\x1\r\n",
            "another backslash sequence": self.ANSWER + b"\\n\r\n",
            "a lone backslash": self.ANSWER + b"a\\b\r\n",
            "a trailing backslash": self.ANSWER + b"a\\\r\n",
            "a raw ESC": self.ANSWER + b"a\x1bb\r\n",
            "a raw CR": self.ANSWER + b"a\rb\r\n",
            "a raw LF": self.ANSWER + b"a\nb\r\n",
            "a raw DEL": self.ANSWER + b"a\x7fb\r\n",
            "a raw C1 character": self.ANSWER + "a\u0085b\r\n".encode(),
            "bytes that are not UTF-8": self.ANSWER + b"a\xffb\r\n",
            "no closing CR LF": self.ANSWER + b"a",
            "an LF alone to close": self.ANSWER + b"a\n",
            "no leading CR LF": b"[TSB: login answer] a\r\n",
            "no space after the tag": b"\r\n[TSB: login answer]a\r\n",
            "the prompt tag on code 7": self.PROMPT + b"a\r\n",
            "empty data": b"",
        }.items():
            with self.subTest(name):
                self.assertIsNone(bf.parse_login_line(bf.LOGIN_ANSWER, data))

    def test_a_code_reads_only_its_own_tag(self):
        self.assertIsNone(bf.parse_login_line(bf.LOGIN_PROMPT, self.ANSWER + b"a\r\n"))
        self.assertIsNone(bf.parse_login_line(bf.OUT, self.ANSWER + b"a\r\n"))
        with self.assertRaises(bf.FormatError):
            bf.login_line(bf.OUT, "a")

    def test_text_that_is_not_unicode_has_no_line(self):
        with self.assertRaises(bf.FormatError):
            bf.login_line(bf.LOGIN_ANSWER, "a\ud800b")

    def test_the_published_pattern_agrees_with_the_codec(self):
        published = REG.docs["schemas/ttyrec.schema.json"]["x-login-line"]["pattern"]
        pattern = selfcheck.compile_published(published)
        for value in ("operator", "corp\\op", "\x1b[0m", "\r\n", "\x85", "\u00e9"):
            line = bf.login_line(bf.LOGIN_ANSWER, value).decode("utf-8")
            self.assertTrue(pattern.search(line), value)
        for bad in ("\r\n[TSB: login answer] a\x1bb\r\n", "\r\n[TSB: login answer] \\x41\r\n",
                    "\r\n[TSB: login answer] a\u0085b\r\n", "\r\n[TSB: login answer] a\\b\r\n",
                    "\r\n[TSB: login typed] a\r\n"):
            self.assertFalse(pattern.search(bad), bad)


def frame(code, data, sec=1789000000, usec=0):
    return bf.RecFrame(sec, usec, code, data)


class Recording(unittest.TestCase):
    A = staticmethod(lambda t: bf.login_line(bf.LOGIN_ANSWER, t))

    def test_the_codes_are_6_and_7_and_a_reader_of_an_earlier_tag_would_refuse_them(self):
        self.assertEqual((bf.LOGIN_PROMPT, bf.LOGIN_ANSWER), (6, 7))
        self.assertEqual(bf.SIDECAR_VALUES[6], "login_prompt")
        self.assertEqual(bf.SIDECAR_VALUES[7], "login_answer")
        ttyrec, sidecar = bf.encode_recording([frame(7, self.A("u"))])
        self.assertEqual(sidecar, b"\x07")
        self.assertEqual(bf.decode_recording(ttyrec, sidecar)[0].login, "u")
        with self.assertRaises(bf.FormatError):
            bf.decode_recording(ttyrec, b"\x0a")

    def test_a_supplied_login_is_two_answers_at_the_head_before_output(self):
        frames = [frame(7, self.A("root")), frame(7, self.A(SYNTHETIC), usec=1), frame(0, b"$ ", sec=1789000001)]
        self.assertEqual(bf.recording_problems(frames) + bf.recording_stamp_problems(frames), [])
        self.assertEqual([f.login for f in frames], ["root", SYNTHETIC, None])

    def test_login_frames_stand_before_every_other_frame(self):
        for late in (frame(0, b"$ "), frame(1, b"x"), frame(5, bf.broker_line("wiggle"))):
            frames = [late, frame(7, self.A("root"), sec=1789000002)]
            with self.subTest(code=late.code):
                self.assertTrue(any("the head of the recording" in p for p in bf.recording_problems(frames)))

    def test_a_login_frame_carries_its_line(self):
        self.assertTrue(bf.recording_problems([frame(7, b"")]))
        self.assertTrue(bf.recording_problems([frame(7, b"operator")]))
        self.assertTrue(bf.recording_problems([frame(6, self.A("a"))]), "an answer line on code 6")

    def test_login_frames_count_for_neither_direction_and_are_compared_line_for_line(self):
        plain = [frame(0, b"$ "), frame(1, b"id\r", sec=1789000001)]
        with_login = [frame(7, self.A("root")), frame(7, self.A("pw"), usec=1), *plain]
        same = bf.compare_recordings(plain, with_login)
        self.assertFalse(same["payload_identical"]["in"], "a login is compared line for line, so a login is a difference")
        self.assertTrue(same["payload_identical"]["out"])
        a = [frame(7, self.A("root")), frame(7, self.A("pw"), usec=1), *plain]
        b = [frame(7, self.A("root")), frame(7, self.A("pw"), usec=2), *plain]
        self.assertTrue(bf.compare_recordings(a, b)["payload_identical"]["in"])
        c = [frame(7, self.A("root")), frame(7, self.A("other"), usec=1), *plain]
        self.assertFalse(bf.compare_recordings(a, c)["payload_identical"]["in"])
        d = [frame(6, bf.login_line(bf.LOGIN_PROMPT, "root")), *plain]
        e = [frame(7, self.A("root")), *plain]
        self.assertFalse(bf.compare_recordings(d, e)["payload_identical"]["in"], "prompts with prompts")

    def test_login_frames_belong_to_the_input_clock(self):
        # stamped by the core: an output frame's earlier worker stamp does not make them go back
        ok = [frame(7, self.A("u"), sec=1789000005), frame(0, b"$ ", sec=1789000001)]
        self.assertEqual(bf.recording_stamp_problems(ok), [])
        back = [frame(7, self.A("u"), sec=1789000005), frame(7, self.A("p"), sec=1789000004)]
        self.assertTrue(bf.recording_stamp_problems(back))

    def test_the_json_form_names_the_marker(self):
        f = frame(7, self.A("u"))
        d = bf.record_json(f)
        self.assertEqual(d["marker"], "login_answer")
        self.assertEqual(bf.record_from_json(d), f)
        self.assertEqual(REG.validate(d, "schemas/ttyrec.schema.json"), [])
        p = bf.record_json(frame(6, bf.login_line(bf.LOGIN_PROMPT, "Password:")))
        self.assertEqual(p["marker"], "login_prompt")
        self.assertEqual(REG.validate(p, "schemas/ttyrec.schema.json"), [])
        # the schema holds the tag and the line end, so the wrong tag for the marker is refused
        wrong = {**d, "marker": "login_prompt"}
        self.assertNotEqual(REG.validate(wrong, "schemas/ttyrec.schema.json"), [])


class NoCredentialInFixtures(unittest.TestCase):
    def test_a_non_synthetic_login_password_is_refused_wherever_it_hides(self):
        doc = {"body": {"login": {"user": "root", "auth": {"kind": "password", "password": "hunter2"}}}}
        problems = selfcheck.credential_problems(doc, placeholders=False)
        self.assertEqual(len(problems), 1)
        self.assertNotIn("hunter2", problems[0])
        ok = {"body": {"login": {"user": "root", "auth": {"kind": "password", "password": SYNTHETIC}}}}
        self.assertEqual(selfcheck.credential_problems(ok, placeholders=False), [])

    def test_every_release_a_fixture_uses_the_synthetic_constant(self):
        paths = [p for p in sorted((CONTRACT / "fixtures").glob("C*/*.json")) if p.stem in RELEASE_A_FIXTURES]
        self.assertEqual(len(paths), len(RELEASE_A_FIXTURES))
        for path in paths:
            text = path.read_text(encoding="utf-8")
            doc = json.loads(text)
            with self.subTest(path.name):
                self.assertEqual(selfcheck.credential_problems(doc, placeholders=True), [])
                if '"password"' in text:
                    self.assertIn(SYNTHETIC, text)


class Client(unittest.TestCase):
    def test_a_login_reaches_the_request_body_and_nowhere_else(self):
        import tsb_contract_client as tcc

        secret = "CLIENT-TEST-SECRET-NOT-REAL"
        core = StubCore()
        records = io.StringIO()
        handler = logging.StreamHandler(records)
        root = logging.getLogger()
        root.addHandler(handler)
        old = root.level
        root.setLevel(logging.DEBUG)
        out, err = io.StringIO(), io.StringIO()
        try:
            with StubServer(core) as url, redirect_stdout(out), redirect_stderr(err):
                c = tcc.Client(url)
                c.login(USER, PASSWORD)
                created = c.create_session(NOCRED, "new", "xterm-256color", 120, 40,
                                           login={"user": "root", "auth": {"kind": "password", "password": secret}})
                self.assertEqual(created["session"]["target_id"], NOCRED)
                with self.assertRaises(tcc.ApiError) as raised:
                    c.create_session(TARGET, "reuse", "xterm-256color", 120, 40,
                                     login={"user": "root", "auth": {"kind": "password", "password": secret}})
                self.assertEqual(raised.exception.status, 422)
                self.assertNotIn(secret, str(raised.exception))
        finally:
            root.removeHandler(handler)
            root.setLevel(old)
        sent = [b for m, p, b in core.requests if m == "POST" and p == "/sessions"]
        self.assertEqual(sent[0]["login"], {"user": "root", "auth": {"kind": "password", "password": secret}})
        self.assertEqual(sent[0]["connection"], "new")
        for stream in (records.getvalue(), out.getvalue(), err.getvalue()):
            self.assertNotIn(secret, stream)

    def test_without_a_login_the_body_is_what_it_was(self):
        import tsb_contract_client as tcc

        core = StubCore()
        with StubServer(core) as url:
            c = tcc.Client(url)
            c.login(USER, PASSWORD)
            c.create_session(TARGET, "new", "xterm-256color", 120, 40)
        [sent] = [b for m, p, b in core.requests if m == "POST" and p == "/sessions"]
        self.assertNotIn("login", sent)


class ServedApp(unittest.TestCase):
    """What the fixture server (and so the stub) does with a login, as the contract says."""

    def create(self, core, target, connection="new", **extra):
        import tsb_contract_client as tcc

        with StubServer(core) as url:
            c = tcc.Client(url)
            c.login(USER, PASSWORD)
            return c.create_session(target, connection, "xterm-256color", 120, 40, **extra)

    def test_a_login_is_not_refused_for_want_of_a_stored_credential(self):
        got = self.create(StubCore(), NOCRED, login=login())
        self.assertEqual(got["session"]["target_id"], NOCRED)

    def test_without_a_login_the_same_target_is_still_409(self):
        import tsb_contract_client as tcc

        with self.assertRaises(tcc.ApiError) as raised:
            self.create(StubCore(), NOCRED)
        self.assertEqual((raised.exception.status, raised.exception.code), (409, "no_resolving_credential"))

    def test_a_login_on_a_reuse_is_422_and_a_bad_login_is_422(self):
        import tsb_contract_client as tcc

        for kwargs in ({"connection": "reuse", "login": login()},
                       {"login": {"user": "root", "auth": {"kind": "key", "key": SYNTHETIC}}},
                       {"login": login(password="p" * 1025)},
                       {"login": login(password="\u00e9" * 600)}):  # 600 characters, 1200 bytes
            with self.subTest(kwargs=kwargs), self.assertRaises(tcc.ApiError) as raised:
                self.create(StubCore(), TARGET, **kwargs)
            self.assertEqual((raised.exception.status, raised.exception.code), (422, "invalid_request"))

    def test_a_login_on_a_telnet_target_is_422_before_anything_else(self):
        import tsb_contract_client as tcc

        app = _serve.ServeApp()
        with StubServer(app) as url:
            c = tcc.Client(url)
            c.login(USER, PASSWORD)
            with self.assertRaises(tcc.ApiError) as raised:
                c.create_session("telnet-scenario", "new", "xterm-256color", 120, 40, login=login())
            self.assertEqual((raised.exception.status, raised.exception.code), (422, "invalid_request"))
            self.assertNotIn(SYNTHETIC, str(raised.exception))
            ok = c.create_session("telnet-scenario", "new", "xterm-256color", 120, 40)
            self.assertEqual(ok["session"]["target_id"], "telnet-scenario")

    def test_me_says_what_the_deployment_records(self):
        import tsb_contract_client as tcc

        with StubServer(StubCore()) as url:
            c = tcc.Client(url)
            c.login(USER, PASSWORD)
            self.assertEqual(c.me()["recording"], {"login_input": False})
            self.assertEqual(REG.validate(c.me(), ME), [])

    def test_the_live_reuse_fixture_fails_a_core_that_accepts_it(self):
        from tsbc import verify
        from tsbc._run import Run
        from tsbc.fixtures import load_fixtures

        from .test_exchange import INPUTS

        fixtures, _ = load_fixtures(CONTRACT)
        [fx] = [f for f in fixtures if f.name == "session-create-login-on-reuse-422"]

        def grade(core):
            with StubServer(core) as url:
                run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e4", url, None, None, 2.0, dict(INPUTS))
                return verify._grade(fx, run)

        self.assertEqual(grade(StubCore()).outcome, verify.PASS)
        bad = grade(StubCore(bugs=frozenset({"login-on-reuse-accepted"})))
        self.assertEqual(bad.outcome, verify.FAIL)
        self.assertIn("HTTP 201", bad.detail)


class Marks(unittest.TestCase):
    def test_the_release_a_fixtures_are_published_at_e3_and_required_green_at_e4(self):
        names = RELEASE_A_FIXTURES
        found = {}
        for path in (CONTRACT / "fixtures").glob("C*/*.json"):
            if path.stem in names:
                found[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(found), names)
        for name, fx in found.items():
            with self.subTest(name):
                self.assertEqual(fx["mark"], {"published": "e3", "required-green": "e4"})

    def test_the_not_ssh_fixture_is_graded_by_the_core_like_the_telnet_ones(self):
        fx = json.loads((CONTRACT / "fixtures/C1/session-create-login-not-ssh-422.json").read_text(encoding="utf-8"))
        self.assertEqual(fx["grade"], "reference")

    def test_the_knob_input_adds_no_runner_input(self):
        from tsbc._run import INPUTS
        self.assertEqual(sorted(INPUTS), ["password", "peer_password", "peer_user", "target_id",
                                          "target_no_cred_id", "user", "viewer_password", "viewer_user"])


if __name__ == "__main__":
    unittest.main()
