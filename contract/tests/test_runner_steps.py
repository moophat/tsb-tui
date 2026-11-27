"""Phase 2 part B1: the runner's new step forms and the era rule (F5, plan.md § Runner 1, 3).

Held here:
- the era rule: a live fixture reads server frames as its own mark epoch knew them, so a frame
  type introduced at a later tag is skipped like an unknown `t` — v0.2.1's `token-event-on-detach`
  stays green at e1/e2 without being edited (R2);
- the `prefix` expectation step: the text frame, then one binary message that contains or lacks
  given strings;
- the HTTP expectation on a binary body and named response headers (GET /sessions/{id}/output).
"""

import json
import unittest

from tsbc import _exchange
from tsbc import _wsframe as wf
from tsbc._http import Response
from tsbc._run import Run
from tsbc.fixtures import Fixture
from tsbc.schema import Registry

from . import CONTRACT

REG = Registry.from_tree(CONTRACT)
PREFIX = {"t": "prefix", "v": 1, "through_seq": 42, "cols": 120, "rows": 40}


class FakeSock:
    """A WebSocket that yields a canned message sequence to the runner's ws_step."""

    def __init__(self, messages):
        self.messages = list(messages)
        self.close_code = None

    def recv(self, timeout=None):
        if not self.messages:
            return wf.Message("close", b"", 4000, "canned stream exhausted")
        return self.messages.pop(0)

    def close(self, code=None, reason="", timeout=0.0):
        self.close_code = code


def live(fixture_doc, timeout=2.0):
    fx = Fixture(CONTRACT / "fixtures/C2/x.json", "fixtures/C2/x.json", fixture_doc)
    run = Run(CONTRACT, REG, "e2", "http://127.0.0.1:0", None, None, timeout, {})
    return _exchange.Live(fx, run)


def ws_fixture(mark):
    return {"seam": "C2", "name": "x", "mark": mark, "kind": "ws", "grade": "live",
            "corpus": ["x"], "description": "x",
            "case": {"inputs": [], "steps": [{"ws_open": "a", "path": "/ws/attach"}]}}


class EraRule(unittest.TestCase):
    """F5: a fixture reads server frames as its own era knew them."""

    def test_e1_does_not_know_the_v0_3_frames(self):
        known = _exchange.era_server_frames(REG, {"required-green": "e1"})
        for t in ("prefix", "clients", "size", "request_refused", "stale", "stale_prompt"):
            with self.subTest(frame=t):
                self.assertNotIn(t, known)
        for t in ("attached", "token", "notice", "quiet_prompt", "input_refused", "data_path"):
            with self.subTest(frame=t):
                self.assertIn(t, known)

    def test_e2_knows_every_current_frame(self):
        known = _exchange.era_server_frames(REG, {"required-green": "e2"})
        self.assertEqual(known, _exchange.frame_types(REG, "server"))

    def test_the_v0_2_1_detach_fixture_reads_attached_then_token(self):
        # token-event-on-detach is marked e1: a prefix and clients frame sent between attached and
        # token at e2 must be skipped, so the token match still lands (R2: it is not edited).
        doc = ws_fixture({"required-green": "e1"})
        doc["case"]["steps"] = [
            {"ws_open": "a", "path": "/ws/attach"},
            {"ws_expect": "a", "frame": {"match": [{"pointer": "/t", "eq": "token"},
                                                    {"pointer": "/reason", "eq": "detach"}]}},
        ]
        sock = FakeSock([
            wf.Message("text", json.dumps(PREFIX)),                              # later-tag frame
            wf.Message("text", json.dumps({"t": "clients", "v": 1,
                                           "session_id": "s", "clients": []})),  # later-tag frame
            wf.Message("text", json.dumps({"t": "token", "v": 1, "kind": "control",
                                           "holder": {"client_id": "c1", "user": "u"},
                                           "reason": "detach"})),
        ])
        lx = live(doc)
        lx.sockets["a"] = (sock, bytearray())
        lx.ws_step(doc["case"]["steps"][1], "step 1")  # should not raise
        self.assertEqual(sock.messages, [], "the token frame should have been consumed")


class PrefixStep(unittest.TestCase):
    """The prefix expectation: the text frame, then one binary message."""

    def run_prefix(self, sock_messages, prefix, timeout=2.0):
        doc = ws_fixture({"required-green": "e2"})
        doc["case"]["steps"] = [{"ws_open": "a", "path": "/ws/attach"},
                                {"ws_expect": "a", "prefix": prefix}]
        lx = live(doc, timeout)
        lx.sockets["a"] = (FakeSock(sock_messages), bytearray())
        lx.ws_step(doc["case"]["steps"][1], "step 1")

    def test_a_matching_prefix_and_dump_pass(self):
        self.run_prefix(
            [wf.Message("text", json.dumps(PREFIX)),
             wf.Message("binary", b"golden screen \x1b[32mOK\x1b[0m")],
            {"match": [{"pointer": "/t", "eq": "prefix"},
                       {"pointer": "/through_seq", "eq": 42}],
             "binary_contains": ["golden screen"], "binary_lacks": ["SECRET"]})

    def test_a_dump_missing_a_needle_fails(self):
        with self.assertRaises(_exchange.Failed) as caught:
            self.run_prefix([wf.Message("text", json.dumps(PREFIX)),
                             wf.Message("binary", b"short")],
                            {"binary_contains": ["golden"]})
        self.assertIn("does not contain", str(caught.exception))

    def test_a_dump_carrying_a_lack_fails(self):
        with self.assertRaises(_exchange.Failed):
            self.run_prefix([wf.Message("text", json.dumps(PREFIX)),
                             wf.Message("binary", b"has SECRET in it")],
                            {"binary_lacks": ["SECRET"]})

    def test_the_text_frame_is_matched_before_the_dump(self):
        # A prefix match that does not hold fails before the dump is ever read.
        with self.assertRaises(_exchange.Failed) as caught:
            self.run_prefix([wf.Message("text", json.dumps(PREFIX))],
                            {"match": [{"pointer": "/through_seq", "eq": 7}]})
        self.assertIn("through_seq", str(caught.exception))

    def test_a_frame_between_attached_and_the_prefix_is_read_past(self):
        self.run_prefix(
            [wf.Message("text", json.dumps({"t": "clients", "v": 1, "session_id": "s", "clients": []})),
             wf.Message("text", json.dumps(PREFIX)),
             wf.Message("binary", b"dump")],
            {"match": [{"pointer": "/t", "eq": "prefix"}]})


class BinaryHttpStep(unittest.TestCase):
    """GET /sessions/{id}/output: an application/octet-stream body and named headers."""

    def check(self, resp, expect, where="step"):
        lx = live(ws_fixture({"required-green": "e5"}))
        lx._check_binary_body(resp, expect.get("binary_body", {}), where)
        lx._check_headers(resp, expect.get("headers", {}), where)

    def test_a_body_and_headers_that_match_pass(self):
        resp = Response(200, [("TSB-Page-More", "false"), ("TSB-Page-First-Seq", "3")],
                        b"\x1b[32moutput\x1b[0m")
        self.check(resp, {"binary_body": {"min_bytes": 1, "contains": ["output"], "lacks": ["SECRET"]},
                          "headers": {"TSB-Page-More": "false", "TSB-Page-First-Seq": "3"}})

    def test_an_empty_body_is_accepted_when_expected(self):
        resp = Response(200, [("TSB-Page-No-Ground", "true")], b"")
        self.check(resp, {"binary_body": {"empty": True},
                          "headers": {"TSB-Page-No-Ground": "true"}})

    def test_a_body_that_is_not_empty_but_expected_empty_fails(self):
        resp = Response(200, [], b"data")
        with self.assertRaises(_exchange.Failed):
            self.check(resp, {"binary_body": {"empty": True}})

    def test_a_body_below_min_bytes_fails(self):
        with self.assertRaises(_exchange.Failed):
            self.check(Response(200, [], b"xy"), {"binary_body": {"min_bytes": 10}})

    def test_a_needle_the_body_lacks_fails(self):
        with self.assertRaises(_exchange.Failed):
            self.check(Response(200, [], b"abc"), {"binary_body": {"contains": ["zzz"]}})

    def test_a_needle_the_body_cannot_carry_fails(self):
        with self.assertRaises(_exchange.Failed):
            self.check(Response(200, [], b"abc"), {"binary_body": {"lacks": ["abc"]}})

    def test_a_header_absent_fails(self):
        with self.assertRaises(_exchange.Failed) as caught:
            self.check(Response(200, [], b""), {"headers": {"TSB-Page-Gap": "true"}})
        self.assertIn("TSB-Page-Gap", str(caught.exception))

    def test_a_header_with_the_wrong_value_fails(self):
        with self.assertRaises(_exchange.Failed):
            self.check(Response(200, [("TSB-Page-More", "true")], b""),
                       {"headers": {"TSB-Page-More": "false"}})

    def test_a_null_expectation_passes_when_header_is_absent(self):
        # null means the response MUST NOT carry that header
        resp = Response(200, [("TSB-Page-No-Ground", "true")], b"")
        self.check(resp, {"headers": {"TSB-Page-No-Ground": "true", "TSB-Page-More": None}})

    def test_a_null_expectation_fails_when_header_is_present(self):
        # null means the response MUST NOT carry that header
        resp = Response(200, [("TSB-Page-No-Ground", "true"), ("TSB-Page-More", "false")], b"")
        with self.assertRaises(_exchange.Failed) as caught:
            self.check(resp, {"headers": {"TSB-Page-No-Ground": "true", "TSB-Page-More": None}})
        self.assertIn("TSB-Page-More", str(caught.exception))
        self.assertIn("expected none", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
