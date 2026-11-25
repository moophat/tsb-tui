"""contract-v0.3 phase 2 part A (northbound C1/C2): the schemas and OpenAPI this job adds.

These tests hold the rules the fixtures encode, from the schema side, so a drift that a fixture's
own `with:` grading might miss is caught here:

- C2: the versioning rule (every frame keeps v 1), the ordering rule in the top description, the
  attach viewport, the prefix and its one binary dump, the clients frame, handoff /
  request_refused, the size answer and its six reasons, and the stale flow with cause gap |
  grid_lost and gap_seq only for gap (R4: oneOf + const).
- C1: the record's clients / resizable / pinned, the two new error codes, and the pages endpoint's
  octet-stream 200 with its headers, admitted by self-check on exactly that operation.
"""

import unittest

from tsbc._exchange import OPENAPI, WS_FRAMES, frame_types
from tsbc.schema import Registry

from . import CONTRACT


class C2Frames(unittest.TestCase):
    def setUp(self):
        self.reg = Registry.from_tree(CONTRACT)
        self.frames = self.reg.docs[WS_FRAMES]["$defs"]

    def defn(self, name):
        return self.frames[name]

    def test_every_new_frame_keeps_v_1(self):
        for name in ("attach", "resize", "attached", "prefix", "clients", "handoff",
                     "request_refused", "size", "stale", "stale_prompt", "stale_answer",
                     "token", "input_refused"):
            with self.subTest(frame=name):
                self.assertEqual(self.defn(name)["properties"]["v"], {"const": 1})

    def test_the_ordering_rule_is_in_the_top_description(self):
        # terminal-size-and-control.md §6, quoted: one client's frames are applied in the order it
        # sent them, and a take-over is applied before that client's next frame.
        top = self.reg.docs[WS_FRAMES]["description"]
        self.assertIn("one client's frames are applied in the order it sent them, and a take-over "
                      "is applied before that client's next frame", top)
        # The versioning rule: a known t with an unknown v is ignored like an unknown t.
        self.assertIn("a receiver ignores a known `t` with an unknown `v` exactly as it ignores "
                      "an unknown `t`", top)

    def test_attach_viewport_is_optional(self):
        props = self.defn("attach")["properties"]
        self.assertIn("cols", props)
        self.assertIn("rows", props)
        self.assertNotIn("cols", self.defn("attach")["required"])
        self.assertNotIn("rows", self.defn("attach")["required"])

    def test_the_new_frame_types_are_registered_on_the_right_side(self):
        client = frame_types(self.reg, "client")
        server = frame_types(self.reg, "server")
        self.assertLessEqual({"handoff", "stale_answer"}, client)
        self.assertLessEqual({"prefix", "clients", "request_refused", "size", "stale",
                              "stale_prompt"}, server)

    def test_token_reasons_gained_the_takeover_set(self):
        self.assertEqual(self.defn("token")["properties"]["reason"]["enum"],
                         ["attach", "detach", "handoff", "owner", "takeover", "release"])

    def test_input_refused_reasons_gained_stale_and_not_typist(self):
        self.assertEqual(self.defn("input_refused")["properties"]["reason"]["enum"],
                         ["unreachable", "replaying", "stale", "not_typist", "recording_unavailable"])

    def test_size_has_the_six_reasons(self):
        self.assertEqual(self.defn("size")["properties"]["reason"]["enum"],
                         ["applied", "clamped_floor", "clamped_ceiling", "pinned",
                          "not_resizable", "not_typist"])

    def test_gap_seq_is_required_only_for_cause_gap(self):
        # R4: a oneOf of const conditions, graded by fixtures.
        reg = self.reg
        ref = f"{WS_FRAMES}#/$defs/stale"
        gap = {"t": "stale", "v": 1, "session_id": "s", "state": "pending", "cause": "gap",
               "at": "2026-10-06T01:00:00Z"}
        self.assertTrue(reg.validate(gap, ref), "cause gap names where the loss began")
        self.assertEqual(reg.validate({**gap, "gap_seq": 1}, ref), [])
        lost = {**gap, "cause": "grid_lost"}
        self.assertEqual(reg.validate(lost, ref), [])
        self.assertTrue(reg.validate({**lost, "gap_seq": 1}, ref),
                        "grid_lost has no lost seq to name")

    def test_chosen_stale_carries_the_answer(self):
        ref = f"{WS_FRAMES}#/$defs/stale"
        chosen = {"t": "stale", "v": 1, "session_id": "s", "state": "chosen", "cause": "gap",
                  "gap_seq": 1, "at": "2026-10-06T01:00:00Z"}
        self.assertTrue(self.reg.validate(chosen, ref))
        self.assertEqual(self.reg.validate(
            {**chosen, "choice": "wiggle", "by": {"client_id": "c", "user": "u"}}, ref), [])

    def test_the_prompt_and_notice_texts_are_the_fixed_ones(self):
        prompt = self.defn("stale_prompt")["properties"]
        self.assertEqual(prompt["text"]["const"],
                         "TSB: lost output - the screen may be wrong. Choose:")
        self.assertEqual(prompt["code"]["const"], "stale.choose")

    def test_close_code_4014_is_declared_and_4013_retired(self):
        codes = self.reg.docs[WS_FRAMES]["x-close-codes"]
        self.assertIn("4014", codes)
        self.assertEqual(codes["4014"]["name"], "removed")
        self.assertNotIn("4013", codes)


class C1RecordAndErrors(unittest.TestCase):
    def setUp(self):
        self.reg = Registry.from_tree(CONTRACT)

    def test_the_record_gains_clients_resizable_and_pinned(self):
        props = self.reg.docs["schemas/session-record.schema.json"]["properties"]
        for name in ("clients", "resizable", "pinned"):
            self.assertIn(name, props)
        # Optional in the schema so a v0.2 record stays valid (R1).
        required = self.reg.docs["schemas/session-record.schema.json"]["required"]
        for name in ("clients", "resizable", "pinned"):
            self.assertNotIn(name, required)
        client = props["clients"]["items"]
        self.assertEqual(set(client["required"]), {"client_id", "user", "may_control"})

    def test_an_enumerated_v02_record_stays_valid(self):
        record = {"id": "s", "target_id": "t", "connection_id": "c", "session_type": "shell",
                  "state": "live", "tier": 1, "owner": "u",
                  "control_holder": {"client_id": "c1", "user": "u"},
                  "created_at": "2026-10-06T12:00:00Z", "term": "xterm-256color",
                  "cols": 120, "rows": 40, "recording": {"active": False}}
        self.assertEqual(self.reg.validate(record, "schemas/session-record.schema.json"), [])
        with_new = {**record, "resizable": True, "pinned": False,
                    "clients": [{"client_id": "c1", "user": "u", "may_control": True}]}
        self.assertEqual(self.reg.validate(with_new, "schemas/session-record.schema.json"), [])

    def test_the_two_new_error_codes_and_their_statuses(self):
        doc = self.reg.docs["schemas/error.schema.json"]
        codes = doc["$defs"]["code"]["enum"]
        self.assertIn("client_not_found", codes)
        self.assertIn("not_implemented", codes)
        self.assertEqual(doc["x-codes"]["client_not_found"]["status"], 404)
        self.assertEqual(doc["x-codes"]["not_implemented"]["status"], 501)


class C1OpenAPI(unittest.TestCase):
    def setUp(self):
        self.doc = Registry.from_tree(CONTRACT).docs[OPENAPI]

    def test_the_removal_operation_exists(self):
        op = self.doc["paths"]["/sessions/{id}/clients/{client_id}"]["delete"]
        self.assertEqual(op["operationId"], "removeClient")
        self.assertIn("204", op["responses"])
        self.assertIn("403", op["responses"])
        self.assertIn("404", op["responses"])

    def test_the_pages_operation_is_octet_stream_200_with_headers(self):
        op = self.doc["paths"]["/sessions/{id}/output"]["get"]
        self.assertEqual(op["operationId"], "outputPage")
        body = op["responses"]["200"]["content"]
        self.assertIn("application/octet-stream", body)
        headers = op["responses"]["200"]["headers"]
        for name in ("TSB-Page-First-Seq", "TSB-Page-Last-Seq", "TSB-Page-More",
                     "TSB-Page-Gap", "TSB-Page-No-Ground", "TSB-Page-Next-Before-Seq"):
            self.assertIn(name, headers)
        for status in ("401", "403", "404", "422", "501"):
            self.assertIn(status, op["responses"])

    def test_octet_stream_is_admitted_on_exactly_one_response(self):
        found = []
        for template, item in self.doc["paths"].items():
            for method in ("get", "post", "delete"):
                op = item.get(method)
                if not op:
                    continue
                for status, resp in op["responses"].items():
                    if "application/octet-stream" in resp.get("content", {}):
                        found.append((method.upper(), template, status))
        self.assertEqual(found, [("GET", "/sessions/{id}/output", "200")])


if __name__ == "__main__":
    unittest.main()