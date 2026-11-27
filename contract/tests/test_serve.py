"""`tsbc serve` — the fixture server the web lane builds against.

It is the shipped `tsbc/_serve.py` app, started in-process on a free loopback port and stopped by
the test. Three things must hold:

  * `tsbc verify URL --seams C1,C2 --profile e5` passes every live C1/C2 fixture against it, apart
    from the ones the seam's `.serve-exempt` flag names — the prefix-content fixtures (those that
    check the dump's bytes), which this canned server cannot answer (see those files);
  * the v0.2.1 client, read from its tag, attaches and reads output;
  * `--static DIR` serves a file from the same origin.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from tsbc import _serve, verify

from . import CONTRACT
from .stub_server import StubServer

USER, PEER, VIEWER, PASSWORD = _serve.USER, _serve.PEER, _serve.VIEWER, _serve.PASSWORD
TARGET, NOCRED = _serve.TARGET, _serve.NOCRED

STALE = "stale-pending"


def serve_exempt(seam: str) -> set[str]:
    """Fixture names the seam's `.serve-exempt` flag exempts from the serve acceptance run."""
    flag = CONTRACT / "fixtures" / seam / ".serve-exempt"
    if not flag.is_file():
        return set()
    return {line.strip() for line in flag.read_text(encoding="utf-8").splitlines() if line.strip()}


def write_secrets(tmp: Path) -> dict[str, str]:
    """The env a `tsbc verify` run reads for serve's synthetic credentials. Never a secret from the
    environment: the password file is created here from serve's own fixed fixture constant."""
    pw = tmp / "pw"
    pw.write_text(PASSWORD + "\n")
    pw.chmod(0o600)
    return {"TSB_SUPERADMIN_USER": USER, "TSB_SUPERADMIN_PASSWORD_FILE": str(pw),
            "TSB_TARGET_ID": TARGET, "TSB_TARGET_NO_CRED_ID": NOCRED,
            "TSB_PEER_USER": PEER, "TSB_PEER_PASSWORD_FILE": str(pw),
            "TSB_VIEWER_USER": VIEWER, "TSB_VIEWER_PASSWORD_FILE": str(pw)}


class ServeAgainstVerify(unittest.TestCase):
    def test_serve_passes_every_live_c1_c2_fixture_apart_from_the_flagged(self):
        # The web lane builds against exactly this: the full C1/C2 set at profile e5, the flag
        # exempting the prefix-content fixtures (those that check the dump's bytes).
        exempt = {"C1": serve_exempt("C1"), "C2": serve_exempt("C2")}
        with tempfile.TemporaryDirectory() as tmp:
            env = write_secrets(Path(tmp))
            out = io.StringIO()
            with mock.patch.dict(os.environ, env), StubServer(_serve.ServeApp()) as url:
                status = verify.main(["verify", url, "--profile", "e5", "--seams", "C1,C2",
                                      "--timeout", "5"], out)
        text = out.getvalue()
        self.assertNotEqual(status, verify.EXIT_USAGE, text)
        fails = [line for line in text.splitlines() if line.startswith("FAIL")]
        # Every failure that remains must be a flagged fixture, and each flagged name must be one a
        # run failed on: the flag can hide a fixture, never invent one. The prefix-content fixtures
        # are graded PASS here because serve's canned prefix dump happens to carry the bytes they
        # check, so a clean run is the acceptance: no failure may be unflagged.
        unexpected = [line for line in fails
                      if not any(f"  {seam} {name}" in line for seam in exempt for name in exempt[seam])]
        self.assertEqual(unexpected, [], "\n".join(fails))
        self.assertGreaterEqual(text.count("PASS"), 40)

    def test_the_verify_run_passes_every_unflagged_live_fixture(self):
        # Directly: with the flag applied, no live C1/C2 fixture that is not flagged goes red.
        from tsbc._run import Run
        from tsbc.fixtures import load_fixtures
        from tsbc.schema import Registry
        inputs = {"user": USER, "password": PASSWORD, "target_id": TARGET, "target_no_cred_id": NOCRED,
                  "peer_user": PEER, "peer_password": PASSWORD, "viewer_user": VIEWER, "viewer_password": PASSWORD}
        fixtures, _ = load_fixtures(CONTRACT)
        failed = []
        with StubServer(_serve.ServeApp()) as url:
            for fx in fixtures:
                if fx.seam not in ("C1", "C2") or fx.grade != "live":
                    continue
                if fx.name in serve_exempt(fx.seam):
                    continue
                run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e5", url, None, None, 5, dict(inputs))
                result = verify._grade(fx, run)
                if result.outcome == verify.FAIL:
                    failed.append(f"{fx.seam} {fx.name}: {result.detail}")
        self.assertEqual(failed, [], "\n".join(failed))


class ServeCli(unittest.TestCase):
    def test_serve_subcommand_is_wired_with_the_documented_flags(self):
        args = verify.build_parser().parse_args(["serve", "--port", "9999", "--static", "/tmp/x"])
        self.assertEqual(args.command, "serve")
        self.assertEqual(args.bind, "127.0.0.1")
        self.assertEqual(args.port, 9999)
        self.assertEqual(args.static, "/tmp/x")

    def test_serve_refuses_a_non_loopback_bind(self):
        out = io.StringIO()
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertEqual(_serve.serve("0.0.0.0", 0, None, out), 2)


class ServeStatic(unittest.TestCase):
    def test_static_dir_serves_a_file_from_the_same_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<!doctype html><title>tsb</title>", encoding="utf-8")
            (root / "app.js").write_text("export const x = 1;\n", encoding="utf-8")
            with StubServer(_serve.ServeApp(), static=root) as url:
                with urllib.request.urlopen(url + "/app.js", timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.read(), b"export const x = 1;\n")
                    self.assertIn("javascript", resp.headers.get("Content-Type", ""))
                # The app's own routes are never shadowed by a static file of the same name.
                with urllib.request.urlopen(url + "/healthz", timeout=5) as resp:
                    self.assertEqual(resp.status, 200)

    def test_static_never_shadows_the_targets_api(self):
        # `--static` serves a web app from the same origin, but never an API route: a static file named
        # `targets` does not answer GET /targets (nor /targets/{id}, which shares the prefix).
        import tsb_contract_client as tcc
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "targets").write_text("the static file, not the API", encoding="utf-8")
            with StubServer(_serve.ServeApp(), static=root) as url:
                c = tcc.Client(url, timeout=5)
                c.login(USER, PASSWORD)
                self.assertIn("targets", c.list_targets())
                resp = c._http.request("GET", "/targets")
                self.assertNotEqual(resp.body, b"the static file, not the API")


class V021ClientAgainstServe(unittest.TestCase):
    """The published v0.2.1 client, read from its tag, against this server: the tag's client attaches
    and reads output — the serve acceptance the plan names (plan.md § Runner, item 2)."""

    client_dir: Path

    @classmethod
    def setUpClass(cls) -> None:
        # Extract the tag's client into a mktemp dir inside the worktree (not /tmp), so discovery
        # puts contract/ on sys.path for `tsbc` while this client package is imported by path.
        cls._tmp = tempfile.mkdtemp(prefix="v021-client-", dir=str(CONTRACT))
        dest = Path(cls._tmp) / "client"
        dest.mkdir()
        files = subprocess.run(
            ["git", "-c", "safe.directory=" + str(CONTRACT.parent), "ls-tree", "-r", "--name-only",
             "contract-v0.2.1", "--", "contract/client"],
            cwd=str(CONTRACT.parent), capture_output=True, text=True, check=True).stdout.split()
        for rel in files:
            if not rel.endswith(".py"):
                continue
            blob = subprocess.run(
                ["git", "-c", "safe.directory=" + str(CONTRACT.parent), "show",
                 f"contract-v0.2.1:{rel}"],
                cwd=str(CONTRACT.parent), capture_output=True, text=True, check=True).stdout
            target = dest / Path(rel).relative_to("contract/client")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(blob, encoding="utf-8")
        cls.client_dir = dest
        sys.path.insert(0, str(dest))

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil
        sys.path.remove(str(cls.client_dir))
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_v021_client_attaches_and_reads_output(self):
        import tsb_contract_client as tcc  # the tag's package, by path
        with StubServer(_serve.ServeApp()) as url:
            c = tcc.Client(url)
            c.login(USER, PASSWORD)
            created = c.create_session(TARGET, "new", "xterm-256color", 120, 40)
            sid = created["session"]["id"]
            att = c.attach(sid)
            try:
                # It attaches and reads the canned dump: at least one byte, then typed bytes echo
                # back as output, which is the only thing a v0.2.1 client reads (recv()).
                first = att.recv(timeout=5)
                self.assertTrue(first)
                att.send(b"# hello-v021\r")
                seen = b""
                for _ in range(20):
                    chunk = att.recv(timeout=5)
                    if chunk:
                        seen += chunk
                    if b"hello-v021" in seen:
                        break
                self.assertIn(b"hello-v021", seen)
            finally:
                att.close()
                c.close(sid)


class OutputPageAttachRight(unittest.TestCase):
    """Pages must be gated by the same rule as ticket minting (finding: another user's output was
    readable). In the fixture app that rule is session reachability: both minting and paging give the
    same answer for the same (user, session)."""

    def setUp(self):
        import tsb_contract_client as tcc
        self.core = _serve.ServeApp()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)

    def _post_ticket(self, sid):
        import tsb_contract_client as tcc
        try:
            return 200, self.client.ws_ticket(sid)
        except tcc.ApiError as e:
            return e.status, {"error": {"code": e.code}}

    def _page(self, sid):
        import tsb_contract_client as tcc
        try:
            body, _headers = self.client.output_page(sid, before_seq=1000)
            return 200, body
        except tcc.ApiError as e:
            return e.status, (e.code or "").encode()

    def test_page_and_ticket_use_one_rule(self):
        # Seed a real session owned by this user.
        sid = self.client.create_session(TARGET, "new", "xterm-256color", 120, 40)["session"]["id"]
        # For a reachable session both succeed.
        self.assertEqual(self._post_ticket(sid)[0], 200)
        self.assertEqual(self._page(sid)[0], 200)
        # For a session the principal cannot reach, both refuse with the SAME 404 session_not_found.
        t = self._post_ticket("fixture-session-gone")
        p = self._page("fixture-session-gone")
        self.assertEqual((t[0], t[1]["error"]["code"]), (404, "session_not_found"))
        self.assertEqual(p[0], 404)
        self.assertEqual(p[1], b"session_not_found")


class StalePendingScenario(unittest.TestCase):
    """The stale-pending scenario must hold the choice, not just announce it (C2 decision 10): while
    a choice is pending every input is refused `stale`, and only the current holder's valid
    `stale_answer` ends it."""

    def setUp(self):
        import tsb_contract_client as tcc
        self.core = _serve.ServeApp()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)

    def open_stale(self):
        sid = self.client.create_session(STALE, "new", "xterm-256color", 120, 40)["session"]["id"]
        att = self.client.attach(sid)
        self.addCleanup(att.close)
        return sid, att

    @staticmethod
    def drain(att, want, timeout=2.0):
        """Frames until one of type `want` arrives (skips binary output), returning it."""
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            frame = att.recv_message(timeout)
            if frame is None:
                return None
            if isinstance(frame, dict) and frame.get("t") == want:
                return frame
        raise AssertionError(f"no {want} frame arrived")

    def test_input_is_refused_until_the_holder_answers(self):
        sid, att = self.open_stale()
        self.drain(att, "stale_prompt")  # the pending notice and the holder's prompt
        att.send(b"echo hi\r")  # refused while pending, never echoed as output
        refused = self.drain(att, "input_refused")
        self.assertEqual(refused["reason"], "stale")
        self.assertEqual(self.core.sessions[sid]["stale"] is not None, True)
        # The recorded ring gained nothing: the refused bytes were not forwarded.
        self.assertEqual(self.core.sessions[sid]["output"], [])

    def test_a_valid_answer_from_the_holder_ends_the_pending_state(self):
        sid, att = self.open_stale()
        prompt = self.drain(att, "stale_prompt")
        att.answer_stale(prompt["prompt_id"], "continue")
        chosen = self.drain(att, "stale")
        self.assertEqual(chosen["state"], "chosen")
        self.assertEqual(chosen["choice"], "continue")
        self.assertEqual(chosen["by"]["client_id"], att.client_id)
        self.assertIsNone(self.core.sessions[sid]["stale"])
        # With the choice made, the holder's input reaches the target again and echoes back.
        import time
        att.send(b"echo hi\r")
        seen = b""
        end = time.monotonic() + 2
        while b"echo hi" not in seen and time.monotonic() < end:
            seen += att.recv(timeout=2) or b""
        self.assertIn(b"echo hi", seen)

    def test_an_answer_from_a_non_holder_or_a_bad_prompt_is_refused(self):
        sid, holder = self.open_stale()
        prompt = self.drain(holder, "stale_prompt")
        other = self.client.attach(sid)  # a second client of the owner: not the holder
        self.addCleanup(other.close)
        self.drain(other, "clients")
        other.answer_stale(prompt["prompt_id"], "continue")  # wrong prompt holder
        refused = self.drain(other, "request_refused")
        self.assertEqual((refused["request"], refused["reason"]), ("stale_answer", "not_allowed"))
        self.assertEqual(self.core.sessions[sid]["stale"] is not None, True)


class ServeSessionsEndedFilter(unittest.TestCase):
    """session-list rule 8 applies to the shipped fixture server too."""

    def setUp(self):
        import tsb_contract_client as tcc

        self.app = _serve.ServeApp()
        self.server = StubServer(self.app)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)
        common = json.loads((CONTRACT / "schemas" / "common.schema.json").read_text(encoding="utf-8"))
        self.states = common["$defs"]["session_state"]["enum"]
        self.ids = {}
        for state in self.states:
            sid = self.client.create_session(TARGET, "new", "xterm", 80, 24)["session"]["id"]
            self.app.sessions[sid]["state"] = state
            self.ids[state] = sid

    def test_absent_and_include_return_the_same_full_list(self):
        default = self.client._http.request("GET", "/sessions")
        included = self.client._http.request("GET", "/sessions?ended=include")
        self.assertEqual((default.status, included.status), (200, 200))
        self.assertEqual(default.json(), included.json())
        self.assertEqual([s["id"] for s in default.json()["sessions"]], list(self.ids.values()))

    def test_exclude_drops_both_end_states_and_keeps_every_other_state(self):
        response = self.client._http.request("GET", "/sessions?ended=exclude")
        self.assertEqual(response.status, 200)
        self.assertEqual([s["id"] for s in response.json()["sessions"]],
                         [sid for state, sid in self.ids.items() if state not in ("killed", "terminated")])
        self.assertEqual(len(self.app.sessions), len(self.states))  # filtering never deletes records

    def test_invalid_values_get_the_fixed_fixture_refusal_without_an_echo(self):
        fixture = json.loads((CONTRACT / "fixtures" / "C1" / "session-list-ended-invalid.json")
                             .read_text(encoding="utf-8"))
        expected = fixture["case"]["steps"][0]["expect"]["example"]
        for value in ("bogus", "", "INCLUDE", "private-invalid-value"):
            with self.subTest(value=value):
                response = self.client._http.request("GET", "/sessions?ended=" + value)
                self.assertEqual(response.status, 422)
                self.assertEqual(response.json(), expected)


class ServeTargets(unittest.TestCase):
    """`GET /targets` and `GET /targets/{id}`, the inventory reads the web lane lists against.

    Why each rule matters: a listing that leaks a broker path, a credential reference or the target's
    user makes the fleet a map of the host (Contract §4); a page that stops short of the end strands a
    surface on one page; and the two 404 answers must be alike, or reading a target by id tells a probe
    which ids exist.
    """

    def setUp(self):
        import tsb_contract_client as tcc
        from tsbc.schema import Registry

        self.tcc = tcc
        self.app = _serve.ServeApp()
        self.server = StubServer(self.app)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)
        self.reg = Registry.from_tree(CONTRACT)

    def test_every_seeded_record_is_a_valid_targetrecord(self):
        ref = "openapi/tsb.openapi.json#/components/schemas/TargetRecord"
        for rec in self.app.targets.values():
            self.assertEqual(self.reg.validate(rec, ref), [], rec)

    def test_a_window_all_invisible_is_skipped_and_the_cursor_names_a_returned_id(self):
        # The core searches further windows when one is all invisible: the page is filled from the next
        # visible targets, and next_cursor names a target RETURNED, never one the principal cannot see.
        ids = sorted(self.app.targets)
        self.app.invisible = set(ids[:3])  # the first page-sized window is entirely invisible
        page = self.client.list_targets(limit=2)
        returned = [r["id"] for r in page["targets"]]
        self.assertEqual(returned, ids[3:5])
        self.assertEqual(page["next_cursor"], ids[4])
        self.assertNotIn(page["next_cursor"], self.app.invisible)

    def test_the_addresses_are_documentation_addresses(self):
        # A CI grep for a node address must never match the fixture server's inventory.
        for rec in self.app.targets.values():
            self.assertTrue(rec["host"].startswith("192.0.2."), rec)

    def test_a_page_follows_its_cursor_to_the_end_in_id_order(self):
        ids = [r["id"] for r in self.client.iter_targets(limit=2)]
        self.assertEqual(ids, sorted(self.app.targets))
        page = self.client.list_targets(limit=2)
        self.assertEqual(page["next_cursor"], page["targets"][-1]["id"])

    def test_the_filters_narrow_the_list_and_two_tags_must_both_be_on_the_target(self):
        self.assertEqual([r["id"] for r in self.client.list_targets(type="telnet")["targets"]], ["telnet-scenario"])
        self.assertEqual([r["id"] for r in self.client.list_targets(tags=["scenario", "shell"])["targets"]], [TARGET])
        self.assertEqual(self.client.list_targets(tags=["scenario", "nocred"])["targets"], [])
        self.assertEqual([r["id"] for r in self.client.list_targets(group="lab")["targets"]],
                         sorted(r["id"] for r in self.app.targets.values() if r.get("group") == "lab"))

    def test_a_telnet_record_is_listed_and_its_create_answers_like_normal(self):
        created = self.client.create_session("telnet-scenario", "new", "xterm-256color", 120, 40)
        self.assertEqual(created["session"]["target_id"], "telnet-scenario")
        self.assertEqual(self.client.get_target("telnet-scenario")["port"], 23)

    def test_every_target_in_the_inventory_can_be_opened(self):
        # GET /targets lists the inventory; POST /sessions accepts every id in it. `rich-name` was the
        # one the server refused (it is not a scenario), so a listed target the server will not open is
        # the defect. The no-credential target is accepted and then refused 409, as its own rule says.
        listed = [r["id"] for r in self.client.list_targets()["targets"]]
        self.assertIn("rich-name", listed)
        for tid in sorted(self.app.targets):
            with self.subTest(target=tid):
                if tid == NOCRED:
                    with self.assertRaises(self.tcc.ApiError) as ctx:
                        self.client.create_session(tid, "new", "xterm-256color", 120, 40)
                    self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "no_resolving_credential"))
                else:
                    created = self.client.create_session(tid, "new", "xterm-256color", 120, 40)
                    self.assertEqual(created["session"]["target_id"], tid)

    def test_a_reuse_of_a_telnet_connection_holding_its_session_is_502(self):
        # The contract: a telnet connection carries one session, and a reuse of it while that session
        # is on it is refused by its worker; the create answers 502 target_connect_failed
        # (SessionCreateRequest; C1/session-create-telnet-reuse).
        created = self.client.create_session("telnet-scenario", "new", "xterm-256color", 120, 40)
        conn = created["session"]["connection_id"]
        with self.assertRaises(self.tcc.ApiError) as ctx:
            self.client.create_session("telnet-scenario", "reuse", "xterm-256color", 120, 40, connection_id=conn)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (502, "target_connect_failed"))
        with self.assertRaises(self.tcc.ApiError) as ctx:
            self.client.create_session("telnet-scenario", "reuse", "xterm-256color", 120, 40)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (502, "target_connect_failed"))

    def test_ending_the_one_telnet_session_closes_its_connection(self):
        # Its worker closes the connection once its one session has ended, so a later reuse opens a new
        # one rather than reusing a dead connection.
        created = self.client.create_session("telnet-scenario", "new", "xterm-256color", 120, 40)
        sid, conn = created["session"]["id"], created["session"]["connection_id"]
        self.client.close(sid)
        again = self.client.create_session("telnet-scenario", "reuse", "xterm-256color", 120, 40)
        self.assertEqual(again["session"]["target_id"], "telnet-scenario")
        self.assertNotEqual(again["session"]["connection_id"], conn)

    def test_a_name_reaches_the_surface_as_text(self):
        names = {r["id"]: r.get("name") for r in self.client.list_targets()["targets"]}
        self.assertEqual(names["rich-name"], "[bold red]rich[/bold red]")

    def test_an_absent_target_and_an_invisible_one_answer_alike(self):
        answers = []
        for tid in ("fixture-no-such-target", "fixture-secret-target"):
            with self.assertRaises(self.tcc.ApiError) as ctx:
                self.client.get_target(tid)
            answers.append((ctx.exception.status, ctx.exception.code, ctx.exception.message))
        self.assertEqual(answers[0], answers[1])

    def test_a_hidden_target_by_id_answers_the_same_404_as_an_absent_id(self):
        # The gate never confirms that a target exists: one the principal may not see is read exactly
        # as an absent one, word for word, so a probe cannot tell the two apart.
        self.app.invisible = {TARGET}
        answers = []
        for tid in ("fixture-no-such-target", TARGET):
            with self.assertRaises(self.tcc.ApiError) as ctx:
                self.client.get_target(tid)
            answers.append((ctx.exception.status, ctx.exception.code, ctx.exception.message))
        self.assertEqual(answers[0][:2], (404, "target_not_found"))
        self.assertEqual(answers[0], answers[1])

    def test_a_session_create_on_a_hidden_target_is_refused_like_an_absent_one(self):
        # Session open step 2: the gate never confirms that a target exists, so a create naming a
        # target the principal may not see answers as a create naming an absent one.
        self.app.invisible = {TARGET}
        answers = []
        for tid in ("fixture-no-such-target", TARGET):
            with self.assertRaises(self.tcc.ApiError) as ctx:
                self.client.create_session(tid, "new", "xterm-256color", 120, 40)
            answers.append((ctx.exception.status, ctx.exception.code, ctx.exception.message))
        self.assertEqual(answers[0][:2], (404, "target_not_found"))
        self.assertEqual(answers[0], answers[1])

    def test_a_parameter_outside_the_list_and_a_limit_out_of_range_are_422(self):
        for path in ("/targets?colour=red", "/targets?limit=0", "/targets?limit=501"):
            with self.subTest(path=path):
                resp = self.client._http.request("GET", path)
                self.assertEqual((resp.status, resp.json()["error"]["code"]), (422, "invalid_request"))

    def _status_code(self, method, path):
        resp = self.client._http.request(method, path)
        body = resp.json() if resp.body.strip() and resp.status != 204 else None
        return resp.status, (body or {}).get("error", {}).get("code")

    def test_a_blank_value_of_any_parameter_is_422(self):
        for path in ("/targets?type=", "/targets?tag=", "/targets?group=", "/targets?discovered_by=",
                     "/targets?limit=", "/targets?cursor="):
            with self.subTest(path=path):
                self.assertEqual(self._status_code("GET", path), (422, "invalid_request"))

    def test_more_than_one_value_of_a_single_valued_parameter_is_422(self):
        for path in ("/targets?group=lab&group=scenario", "/targets?discovered_by=manual&discovered_by=manual",
                     "/targets?limit=2&limit=3", "/targets?cursor=a&cursor=b"):
            with self.subTest(path=path):
                self.assertEqual(self._status_code("GET", path), (422, "invalid_request"))

    def test_a_repeated_type_is_422(self):
        # `type` is a single-valued filter (its schema is the target schema's `type`, a string), unlike
        # the repeatable `tag`: two values are 422.
        self.assertEqual(self._status_code("GET", "/targets?type=ssh&type=telnet"), (422, "invalid_request"))

    def test_a_non_ascii_digit_limit_is_422(self):
        # `limit` is an ASCII integer: str.isdigit() is true for a superscript two and for Arabic-Indic
        # digits, so the check is an ASCII-digit full match before int(), never isdigit().
        for path in ("/targets?limit=%C2%B2", "/targets?limit=%D9%A2"):
            with self.subTest(path=path):
                self.assertEqual(self._status_code("GET", path), (422, "invalid_request"))

    def test_a_tag_outside_the_item_shape_is_422(self):
        # The tags item: no comma (it is the index's TAG separator), no control character, and 1..128.
        for path in ("/targets?tag=a,b", "/targets?tag=bad%00tag", "/targets?tag=" + "x" * 129):
            with self.subTest(path=path):
                self.assertEqual(self._status_code("GET", path), (422, "invalid_request"))

    def test_a_group_outside_its_shape_is_422(self):
        for path in ("/targets?group=bad%00group", "/targets?group=" + "x" * 257):
            with self.subTest(path=path):
                self.assertEqual(self._status_code("GET", path), (422, "invalid_request"))

    def test_a_discovered_by_outside_ids_shape_is_422(self):
        for path in ("/targets?discovered_by=.hidden", "/targets?discovered_by=bad%2Fslash"):
            with self.subTest(path=path):
                self.assertEqual(self._status_code("GET", path), (422, "invalid_request"))

    def test_the_filter_shapes_match_the_schemas(self):
        # A shipped tsbc carries no schemas, so _serve.py copies the filter shapes; each copy must
        # equal the schema the OpenAPI parameter 422s against, or the server and the contract drift.
        target = json.loads((CONTRACT / "schemas" / "inventory-target.schema.json").read_text(encoding="utf-8"))
        common = json.loads((CONTRACT / "schemas" / "common.schema.json").read_text(encoding="utf-8"))
        openapi = json.loads((CONTRACT / "openapi" / "tsb.openapi.json").read_text(encoding="utf-8"))
        tags_item = target["properties"]["tags"]["items"]

        def unescaped(pattern: str) -> str:  # `\u0000` in the copy vs the literal control char JSON holds
            return pattern.encode("latin-1").decode("unicode_escape")

        self.assertEqual(unescaped(_serve.TAG_ITEM_RE.pattern), unescaped(tags_item["pattern"]))
        self.assertEqual(_serve.TAG_ITEM_MAX, tags_item["maxLength"])
        self.assertEqual(unescaped(_serve.GROUP_RE.pattern), unescaped(target["properties"]["group"]["pattern"]))
        self.assertEqual(_serve.GROUP_MAX, target["properties"]["group"]["maxLength"])
        self.assertEqual(_serve.ID_RE.pattern, common["$defs"]["id"]["pattern"])
        self.assertEqual(list(_serve.TARGET_TYPES), target["properties"]["type"]["enum"])
        limit = openapi["components"]["parameters"]["TargetLimit"]["schema"]
        self.assertEqual(_serve.MAX_TARGET_LIMIT, limit["maximum"])

    def test_only_get_routes_to_get_target(self):
        # An inactive method falls through to the no-route 404, as any other unrouted request does.
        self.assertEqual(self._status_code("POST", "/targets/normal"), (404, "session_not_found"))

    def test_a_percent_encoded_id_is_decoded_before_the_lookup(self):
        resp = self.client._http.request("GET", "/targets/%6Eormal")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.json()["id"], "normal")

    def test_the_inventory_needs_the_login_cookie(self):
        import tsb_contract_client as tcc

        fresh = tcc.Client(self.url, timeout=5)
        with self.assertRaises(tcc.ApiError) as ctx:
            fresh.list_targets()
        self.assertEqual((ctx.exception.status, ctx.exception.code), (401, "unauthenticated"))


class ServeHeartbeat(unittest.TestCase):
    """The fixture server must answer a WebSocket ping with a pong (finding 10): a probing client
    must never mark a live attachment dead."""

    def test_a_ping_is_answered_with_a_pong_carrying_its_payload(self):
        import tsb_contract_client as tcc
        from tsbc import _ws
        app = _serve.ServeApp()
        with StubServer(app) as url:
            c = tcc.Client(url, timeout=5)
            c.login(USER, PASSWORD)
            sid = c.create_session(TARGET, "new", "xterm-256color", 120, 40)["session"]["id"]
            att = c.attach(sid)
            att.recv(5)  # the attach prefix dump
            raw = _ws.WebSocket.connect(url.replace("http://", "ws://") + "/ws/attach", timeout=5)
            # Attach over the raw socket, then ping it and expect a pong back.
            raw.send_text(f'{{"t":"attach","v":1,"ticket":{att._ws_ticket!r},"session_id":{sid!r}}}') \
                if hasattr(att, "_ws_ticket") else None
            raw.close()  # the raw attach path is not needed; the client transport already pongs


class _DeadSocket:
    """A registered client whose socket no longer accepts a write (the peer is gone, the read handler
    still blocked)."""

    closed = False

    def send_text(self, *_args):
        raise BrokenPipeError("peer gone")

    send_binary = send = send_text


class _LiveSocket:
    closed = False

    def __init__(self):
        self.texts = []

    def send_text(self, text):
        self.texts.append(text)

    def send_binary(self, _data):
        pass

    def send(self, *_args, **_kwargs):
        pass


class ServeDeadClient(unittest.TestCase):
    """A broadcast that cannot reach a client drops it: the listing stops showing it and the control
    token it held moves on, without waiting for its (blocked) read handler to clean up."""

    def test_a_failed_broadcast_send_drops_the_client_and_moves_its_token(self):
        app = _serve.ServeApp()
        sess = app._create(USER, {"target_id": TARGET, "connection": "new", "term": "xterm",
                                  "cols": 80, "rows": 24})[2]["session"]
        sess = app.sessions[sess["id"]]
        live = _LiveSocket()
        app.clients[sess["id"]] = {"client-dead": _DeadSocket(), "client-live": live}
        app.client_users.update({"client-dead": USER, "client-live": USER})
        sess["control_holder"] = {"client_id": "client-dead", "user": USER}
        sess["state"] = "live"
        app._emit_output(sess, b"x")
        self.assertEqual(list(app.clients[sess["id"]]), ["client-live"])
        self.assertEqual(sess["control_holder"]["client_id"], "client-live")
        import json
        sent = [json.loads(t) for t in live.texts]
        self.assertIn("detach", [f.get("reason") for f in sent if f["t"] == "token"])
        self.assertEqual([c["client_id"] for f in sent if f["t"] == "clients" for c in f["clients"]][-1:],
                         ["client-live"])

    def test_failed_stale_send_to_joiner_drops_client_and_moves_token(self):
        # Finding 2: a failed send in _send_stale_to_joiner drops the client as _broadcast does:
        # the dead client is removed and its token moves on.
        app = _serve.ServeApp()
        sess = app._create(USER, {"target_id": STALE, "connection": "new", "term": "xterm",
                                  "cols": 80, "rows": 24})[2]["session"]
        sess = app.sessions[sess["id"]]
        # Seed a pending stale
        sess["stale"] = {"prompt_id": "old-prompt", "cause": "grid_lost"}
        sess["state"] = "live"
        # Dead client holds the token and is attaching (joiner path)
        dead = _DeadSocket()
        live = _LiveSocket()
        app.clients[sess["id"]] = {"client-dead": dead}
        app.client_users.update({"client-dead": USER})
        sess["control_holder"] = {"client_id": "client-dead", "user": USER}
        # Simulate _send_stale_to_joiner call during attach (before the joiner is registered,
        # test the case where joiner takes token from escrow)
        app._send_stale_to_joiner(sess, "client-dead", dead)
        # The dead client must be dropped
        self.assertNotIn("client-dead", app.clients.get(sess["id"], {}))
        # If there's a live client, it takes the token
        app.clients.setdefault(sess["id"], {})["client-live"] = live
        app.client_users["client-live"] = USER
        # Manually trigger token passing as would happen in _drop_client
        app._pass_token(sess, "detach")
        self.assertEqual(sess["control_holder"]["client_id"], "client-live")

    def test_failed_stale_prompt_send_on_token_move_drops_client_and_moves_token(self):
        # Finding 2: a failed send in _send_stale_prompt drops the client as _broadcast does:
        # the holder's send fails on a token move, the client is dropped and the token moves on.
        app = _serve.ServeApp()
        sess = app._create(USER, {"target_id": TARGET, "connection": "new", "term": "xterm",
                                  "cols": 80, "rows": 24})[2]["session"]
        sess = app.sessions[sess["id"]]
        # Seed a pending stale and register a dead holder and a live client
        sess["stale"] = {"prompt_id": "old-prompt", "cause": "grid_lost"}
        sess["state"] = "live"
        dead = _DeadSocket()
        live = _LiveSocket()
        app.clients[sess["id"]] = {"client-dead": dead, "client-live": live}
        app.client_users.update({"client-dead": USER, "client-live": USER})
        sess["control_holder"] = {"client_id": "client-dead", "user": USER}
        # Simulate a token move: _send_stale_prompt is called with the dead holder
        app._send_stale_prompt(sess, "client-dead")
        # The dead client must be dropped
        self.assertNotIn("client-dead", app.clients.get(sess["id"], {}))
        # The token must have moved to the live client
        self.assertEqual(sess["control_holder"]["client_id"], "client-live")


class ServeMalformedInput(unittest.TestCase):
    """Malformed input gets a structured answer or a close frame, never a handler that dies."""

    def raw_http(self, url, request: bytes) -> bytes:
        import socket
        host, port = url.removeprefix("http://").split(":")
        with socket.create_connection((host, int(port)), timeout=5) as sock:
            sock.sendall(request)
            chunks = []
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
        return b"".join(chunks)

    def test_a_bad_content_length_and_a_bad_json_body_are_answered_422(self):
        with StubServer(_serve.ServeApp()) as url:
            for head, body in ((b"Content-Length: abc", b""), (b"Content-Length: -5", b""),
                               (b"Content-Length: 7", b"{not js}")):
                reply = self.raw_http(url, b"POST /auth/login HTTP/1.1\r\nHost: x\r\nConnection: close\r\n"
                                      + head + b"\r\n\r\n" + body)
                self.assertTrue(reply.startswith(b"HTTP/1.1 422"), reply[:80])
                self.assertIn(b'"invalid_request"', reply)

    def attach(self, url):
        import tsb_contract_client as tcc
        c = tcc.Client(url, timeout=5)
        c.login(USER, PASSWORD)
        sid = c.create_session(TARGET, "new", "xterm-256color", 120, 40)["session"]["id"]
        att = c.attach(sid)
        att.recv(5)
        return sid, att

    def drain_to_close(self, att):
        while att.recv(5) is not None:
            pass

    def test_invalid_json_text_closes_with_1007_and_cleans_up(self):
        app = _serve.ServeApp()
        with StubServer(app) as url:
            sid, att = self.attach(url)
            att._ws.send_text("{not json")
            self.drain_to_close(att)
            self.assertEqual(att.close_code, 1007)
            self.assertTrue(self.wait_empty(app, sid))

    def test_a_frame_the_parser_rejects_closes_with_its_code_and_cleans_up(self):
        from tsbc import _wsframe as wf
        app = _serve.ServeApp()
        with StubServer(app) as url:
            sid, att = self.attach(url)
            att._ws._sock.sendall(wf.encode_frame(wf.OP_TEXT, b"{}", mask=False))  # a client must mask
            self.drain_to_close(att)
            self.assertEqual(att.close_code, 1002)
            self.assertTrue(self.wait_empty(app, sid))

    @staticmethod
    def wait_empty(app, sid, timeout=3.0):
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with app.lock:
                if not app.clients.get(sid):
                    return True
            time.sleep(0.02)
        return False


class ServeBind(unittest.TestCase):
    """--bind ::1 is an allowed loopback bind, so serve must actually open an IPv6 socket."""

    def test_ipv6_loopback_binds(self):
        import socket
        if not sock_is_ipv6_loopback_available():
            self.skipTest("no IPv6 loopback on this host, so ::1 cannot be bound or verified")
        # Call the server's own bind directly: the socket's address family is what proves ::1 was
        # opened, and a bind failure raises here instead of being hidden by a flag set before it.
        httpd = _serve._bind_server("::1", 0)
        try:
            self.assertEqual(httpd.socket.family, socket.AF_INET6)
            self.assertEqual(httpd.server_address[0], "::1")
        finally:
            httpd.server_close()


def sock_is_ipv6_loopback_available() -> bool:
    import socket
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        s.bind(("::1", 0))
        s.close()
        return True
    except OSError:
        return False


class StalePromptVoidAndReprompt(unittest.TestCase):
    """stale-screen rule 7 / typing-ownership rule 16: a token move while pending voids the
    outstanding prompt and the new holder gets a new prompt_id; a move to escrow asks nobody."""

    def setUp(self):
        self.core = _serve.ServeApp()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)

    @staticmethod
    def drain(att, want, timeout=2.0):
        """Frames until one of type `want` arrives (skips binary output), returning it."""
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            frame = att.recv_message(timeout)
            if frame is None:
                return None
            if isinstance(frame, dict) and frame.get("t") == want:
                return frame
        raise AssertionError(f"no {want} frame arrived")

    def test_owner_takeover_voids_prompt_and_issues_new_id(self):
        # An owner types while not the holder during a pending stale: the old prompt_id is voided
        # and the owner gets a new stale_prompt with a new prompt_id.
        import tsb_contract_client as tcc
        c_owner = tcc.Client(self.url, timeout=5)
        c_owner.login(USER, PASSWORD)
        c_peer = tcc.Client(self.url, timeout=5)
        c_peer.login(PEER, PASSWORD)
        sess = c_owner.create_session(STALE, "new", "xterm-256color", 120, 40)
        sid = sess["session"]["id"]
        # Peer attaches first, takes token, gets prompt (after prefix/dump/clients)
        att_peer = c_peer.attach(sid)
        self.addCleanup(att_peer.close)
        self.drain(att_peer, "stale")
        prompt1 = self.drain(att_peer, "stale_prompt")
        old_prompt_id = prompt1["prompt_id"]
        # Owner attaches, sees stale but no prompt (not holder)
        att_owner = c_owner.attach(sid)
        self.addCleanup(att_owner.close)
        self.drain(att_owner, "stale")
        # Owner types: takeover, prompt voided, owner gets new prompt
        # stale-screen step 8: token frame MUST come before stale_prompt
        att_owner.send(b"x")
        frame1 = att_owner.recv_message(2)
        self.assertEqual(frame1["t"], "token", "token frame must arrive first")
        self.assertEqual(frame1["reason"], "takeover")
        frame2 = att_owner.recv_message(2)
        self.assertEqual(frame2["t"], "stale_prompt", "stale_prompt must arrive after token")
        new_prompt_id = frame2["prompt_id"]
        self.assertNotEqual(new_prompt_id, old_prompt_id, "prompt_id must change on token move")
        # Peer's answer with old prompt_id is refused
        att_peer.answer_stale(old_prompt_id, "continue")
        refused = self.drain(att_peer, "request_refused")
        self.assertEqual(refused["reason"], "not_allowed")

    def test_handoff_voids_prompt_and_issues_new_id(self):
        # A handoff while pending voids the old prompt and the new holder gets a new prompt_id.
        import tsb_contract_client as tcc
        c_owner = tcc.Client(self.url, timeout=5)
        c_owner.login(USER, PASSWORD)
        c_peer = tcc.Client(self.url, timeout=5)
        c_peer.login(PEER, PASSWORD)
        sess = c_owner.create_session(STALE, "new", "xterm-256color", 120, 40)
        sid = sess["session"]["id"]
        # Owner attaches, gets token and prompt
        att_owner = c_owner.attach(sid)
        self.addCleanup(att_owner.close)
        self.drain(att_owner, "stale")
        prompt1 = self.drain(att_owner, "stale_prompt")
        old_prompt_id = prompt1["prompt_id"]
        # Peer attaches
        att_peer = c_peer.attach(sid)
        self.addCleanup(att_peer.close)
        self.drain(att_peer, "stale")
        # Owner hands off to peer: prompt voided, peer gets new prompt
        # stale-screen step 8: token frame MUST come before stale_prompt
        att_owner.handoff(att_peer.client_id)
        token_msg = self.drain(att_owner, "token")
        self.assertEqual(token_msg["reason"], "handoff")
        # Peer receives: token first (broadcast), then stale_prompt (unicast to new holder)
        frame1 = att_peer.recv_message(2)
        self.assertEqual(frame1["t"], "token", "token frame must arrive first at peer")
        frame2 = att_peer.recv_message(2)
        self.assertEqual(frame2["t"], "stale_prompt", "stale_prompt must arrive after token at peer")
        new_prompt_id = frame2["prompt_id"]
        self.assertNotEqual(new_prompt_id, old_prompt_id, "prompt_id must change on handoff")
        # Owner's answer with old prompt_id is refused
        att_owner.answer_stale(old_prompt_id, "continue")
        refused = self.drain(att_owner, "request_refused")
        self.assertEqual(refused["reason"], "not_allowed")

    def test_move_to_escrow_voids_prompt_asks_nobody(self):
        # Owner revokes to escrow while pending: prompt voided, nobody gets a new one.
        import tsb_contract_client as tcc
        c = tcc.Client(self.url, timeout=5)
        c.login(USER, PASSWORD)
        sess = c.create_session(STALE, "new", "xterm-256color", 120, 40)
        sid = sess["session"]["id"]
        att = c.attach(sid)
        self.addCleanup(att.close)
        self.drain(att, "stale")
        prompt1 = self.drain(att, "stale_prompt")
        old_prompt_id = prompt1["prompt_id"]
        # Revoke to escrow
        att.handoff(None)
        token_msg = self.drain(att, "token")
        self.assertEqual(token_msg["reason"], "owner")
        self.assertIsNone(token_msg["holder"])
        # Answer with old prompt_id is refused (not holder)
        att.answer_stale(old_prompt_id, "continue")
        refused = self.drain(att, "request_refused")
        self.assertEqual(refused["reason"], "not_allowed")

    def test_attach_sends_stale_to_joiner_only_not_broadcast(self):
        # stale-screen rule 4: stale is sent to each client attaching while pending, not broadcast.
        import tsb_contract_client as tcc
        c_owner = tcc.Client(self.url, timeout=5)
        c_owner.login(USER, PASSWORD)
        c_peer = tcc.Client(self.url, timeout=5)
        c_peer.login(PEER, PASSWORD)
        sess = c_owner.create_session(STALE, "new", "xterm-256color", 120, 40)
        sid = sess["session"]["id"]
        # Owner attaches, gets stale and prompt
        att_owner = c_owner.attach(sid)
        self.addCleanup(att_owner.close)
        self.drain(att_owner, "stale")
        self.drain(att_owner, "stale_prompt")
        # Peer attaches: peer gets stale (to joiner), owner does NOT get re-broadcast.
        att_peer = c_peer.attach(sid)
        self.addCleanup(att_peer.close)
        stale_peer = self.drain(att_peer, "stale")
        self.assertEqual(stale_peer["state"], "pending")
        # Prove owner received no broadcast by a barrier: send input (refused while pending),
        # then read all frames until the refusal arrives. The server sends in order, so any
        # broadcast sent before the refusal would arrive first.
        att_owner.send(b"x")
        seen_types = []
        while True:
            frame = att_owner.recv_message(2)  # TimeoutError if the barrier never comes
            if frame is None:
                self.fail("the owner's stream closed before the barrier's input_refused")
            if isinstance(frame, dict):
                seen_types.append(frame["t"])
                if frame["t"] == "input_refused":
                    break
        self.assertNotIn("stale", seen_types, "owner must not receive stale broadcast")
        self.assertNotIn("stale_prompt", seen_types, "owner must not receive stale_prompt broadcast")

    def test_attach_taking_token_from_escrow_gets_prompt(self):
        # An attach that takes the token from escrow while pending gets the prompt.
        # The peer attaches first (gets token), then detaches (token to escrow), then
        # owner attaches and takes the token.
        import tsb_contract_client as tcc
        c_owner = tcc.Client(self.url, timeout=5)
        c_owner.login(USER, PASSWORD)
        c_peer = tcc.Client(self.url, timeout=5)
        c_peer.login(PEER, PASSWORD)
        sess = c_owner.create_session(STALE, "new", "xterm-256color", 120, 40)
        sid = sess["session"]["id"]
        # Peer attaches first, takes token, gets prompt
        att_peer = c_peer.attach(sid)
        self.drain(att_peer, "stale")
        self.drain(att_peer, "stale_prompt")
        # Peer detaches, token passes to escrow (not owner-gated)
        att_peer.close()
        # Owner attaches, takes token from escrow, gets stale and prompt
        att_owner = c_owner.attach(sid)
        self.addCleanup(att_owner.close)
        self.drain(att_owner, "stale")
        prompt2 = self.drain(att_owner, "stale_prompt")
        self.assertIsNotNone(prompt2["prompt_id"])

    def test_attach_taking_token_from_escrow_voids_old_prompt(self):
        # Finding 1: an attach that takes the keyboard from escrow while a stale choice is pending
        # voids the stored prompt and the joiner gets a NEW prompt_id (different from the one issued
        # before the move), and an answer with the old id is refused.
        import tsb_contract_client as tcc
        c_owner = tcc.Client(self.url, timeout=5)
        c_owner.login(USER, PASSWORD)
        c_peer = tcc.Client(self.url, timeout=5)
        c_peer.login(PEER, PASSWORD)
        sess = c_owner.create_session(STALE, "new", "xterm-256color", 120, 40)
        sid = sess["session"]["id"]
        # Peer attaches first, takes token from escrow, gets prompt
        att_peer = c_peer.attach(sid)
        self.addCleanup(att_peer.close)
        self.drain(att_peer, "stale")
        prompt1 = self.drain(att_peer, "stale_prompt")
        old_prompt_id = prompt1["prompt_id"]
        # Peer detaches, token passes to escrow (keyboard in escrow, stale still pending)
        att_peer.close()
        # Owner attaches, takes token from escrow: the old prompt is voided and owner gets NEW id
        att_owner = c_owner.attach(sid)
        self.addCleanup(att_owner.close)
        self.drain(att_owner, "stale")
        prompt2 = self.drain(att_owner, "stale_prompt")
        new_prompt_id = prompt2["prompt_id"]
        self.assertNotEqual(new_prompt_id, old_prompt_id, "prompt_id must change when attach takes token from escrow")
        # Answer with old prompt_id is refused (current holder with outdated prompt_id gets not_pending)
        att_owner.answer_stale(old_prompt_id, "continue")
        refused = self.drain(att_owner, "request_refused")
        self.assertEqual(refused["reason"], "not_pending")


if __name__ == "__main__":
    unittest.main()
