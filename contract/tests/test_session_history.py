"""Session-history contract and thin-client compatibility; U2 retention/paging remains owed."""

import copy
import json
import unittest
import urllib.parse
from unittest.mock import patch

from tsb_contract_client import ApiError, Client
from tsbc import _exchange, _serve, profiles
from tsbc.schema import Registry

from . import CONTRACT
from .stub_server import StubServer


def fixture(name):
    return json.loads((CONTRACT / "fixtures" / name).read_text())


class PageAnswers:
    """Serve published response examples to test transport, not provider pagination logic."""

    def __init__(self):
        self.requests = []
        steps = fixture("C1/session-list-paged.json")["case"]["steps"]
        self.first = copy.deepcopy(steps[0]["expect"]["example"])
        self.last = steps[1]["expect"]["example"]
        self.cursor = "opaque+/=&?position"
        self.first["next_cursor"] = self.cursor
        self.refusal = None

    def http(self, method, path, headers, body):
        self.requests.append((method, path, body))
        if self.refusal is not None:
            return self.refusal, [], {"error": {"code": "invalid_request", "message": "cursor is invalid"}}
        if path == "/sessions":
            return 200, [], {"sessions": self.first["sessions"] + self.last["sessions"]}
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        answer = self.last if query.get("cursor") == [self.cursor] else self.first
        return 200, [], answer


class SessionHistoryClient(unittest.TestCase):
    def setUp(self):
        self.app = PageAnswers()
        server = StubServer(self.app)
        self.client = Client(server.__enter__(), timeout=5)
        self.addCleanup(server.__exit__, None, None, None)

    def test_legacy_call_is_unbounded_list_without_new_parameters(self):
        records = self.client.sessions()
        self.assertIsInstance(records, list)
        self.assertEqual(len(records), 4)
        self.assertEqual({r["state"] for r in records}, {"live", "detached", "killed", "terminated"})
        self.assertEqual(self.app.requests, [("GET", "/sessions", None)])

    def test_one_page_then_explicit_continuation_preserves_opaque_cursor(self):
        first = self.client.list_sessions(ended="only", limit=2)
        self.assertEqual(first, self.app.first)
        self.assertEqual(len(self.app.requests), 1)  # never auto-drain
        last = self.client.list_sessions(ended="only", limit=2, cursor=first["next_cursor"])
        self.assertEqual(last, self.app.last)
        self.assertNotIn("next_cursor", last)
        for request in self.app.requests:
            method, path, body = request
            self.assertEqual((method, body), ("GET", None))
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            self.assertEqual(query["limit"], ["2"])
            self.assertEqual(query["ended"], ["only"])
        self.assertEqual(query["cursor"], [self.app.cursor])

    def test_omitted_filter_and_cursor_are_not_serialized(self):
        self.client.list_sessions(limit=500)
        self.assertEqual(self.app.requests, [("GET", "/sessions?limit=500", None)])

    def test_all_filter_values_are_forwarded(self):
        for ended in ("include", "exclude", "only"):
            self.client.list_sessions(ended=ended, limit=1)
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.app.requests[-1][1]).query)
            self.assertEqual(query["ended"], [ended])

    def test_limit_is_a_required_keyword_argument(self):
        with self.assertRaises(TypeError):
            self.client.list_sessions()
        with self.assertRaises(TypeError):
            self.client.list_sessions(2)
        self.assertEqual(self.app.requests, [])

    def test_older_core_refusal_is_exposed_without_unbounded_retry(self):
        self.app.refusal = 422
        with self.assertRaises(ApiError) as raised:
            self.client.list_sessions(limit=2)
        self.assertEqual((raised.exception.status, raised.exception.code), (422, "invalid_request"))
        self.assertEqual(self.app.requests, [("GET", "/sessions?limit=2", None)])

    def test_empty_cursor_is_sent_for_provider_validation(self):
        # Do not silently start a fresh walk by dropping an invalid supplied position.
        with patch.object(self.client, "_call", return_value={"sessions": []}) as call:
            self.client.list_sessions(limit=1, cursor="")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(call.call_args.args[1]).query,
                                    keep_blank_values=True)
        self.assertEqual(query["cursor"], [""])


class SessionHistoryServe(unittest.TestCase):
    def setUp(self):
        self.app = _serve.ServeApp()
        self.app.logins["history-login"] = _serve.USER
        self.headers = {"cookie": "tsb_session=history-login"}
        # Seed real served sessions, deliberately inserting them out of page order.
        self.ids = []
        for state, created_at in (("live", "2026-11-14T12:00:00Z"),
                                  ("detached", "2026-11-15T12:00:00Z"),
                                  ("terminated", "2026-11-15T12:00:00Z"),
                                  ("killed", "2026-11-16T12:00:00Z")):
            sess = self.app._new_session(_serve.USER, _serve.TARGET, "fixture-conn", "xterm", 80, 24)
            sess.update(state=state, created_at=created_at)
            self.ids.append(sess["id"])

    def get(self, query=""):
        return self.app.http("GET", "/sessions" + query, self.headers, None)

    def test_pages_order_creation_instants_then_ids_and_end_with_empty_page(self):
        seen, query = [], "?limit=2"
        for _ in range(3):
            status, _, page = self.get(query)
            self.assertEqual(status, 200)
            self.assertLessEqual(len(page["sessions"]), 2)
            seen.extend(r["id"] for r in page["sessions"])
            if "next_cursor" not in page:
                break
            query = "?" + urllib.parse.urlencode({"limit": 2, "cursor": page["next_cursor"]})
        else:
            self.fail("page walk did not finish")
        self.assertEqual(seen, self.ids[::-1])
        self.assertNotIn("next_cursor", page)
        status, _, short = self.get("?limit=500")
        self.assertEqual(status, 200)
        self.assertEqual(len(short["sessions"]), 4)
        self.assertNotIn("next_cursor", short)

    def test_order_and_continuation_preserve_nanosecond_precision(self):
        self.app.sessions[self.ids[0]]["created_at"] = "2026-11-16T12:00:00.000000002Z"
        self.app.sessions[self.ids[-1]]["created_at"] = "2026-11-16T12:00:00.000000001Z"
        status, _, first = self.get("?limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(first["sessions"][0]["id"], self.ids[0])
        query = "?" + urllib.parse.urlencode({"limit": 1, "cursor": first["next_cursor"]})
        status, _, second = self.get(query)
        self.assertEqual(status, 200)
        self.assertEqual(second["sessions"][0]["id"], self.ids[-1])

    def test_each_filter_is_applied_before_filling_and_unpaged_is_complete(self):
        for ended, expected in (("include", self.ids), ("only", self.ids[2:]), ("exclude", self.ids[:2])):
            with self.subTest(ended=ended):
                status, _, page = self.get("?ended=" + ended + "&limit=2")
                self.assertEqual(status, 200)
                self.assertEqual([r["id"] for r in page["sessions"]], expected[::-1][:2])
                status, _, unpaged = self.get("?ended=" + ended)
                self.assertEqual(status, 200)
                self.assertEqual({r["id"] for r in unpaged["sessions"]}, set(expected))
                self.assertNotIn("next_cursor", unpaged)
        self.assertEqual({r["id"] for r in self.get()[2]["sessions"]}, set(self.ids))

    def test_cursor_survives_boundary_removal_and_filter_change(self):
        first = self.get("?limit=2")[2]
        del self.app.sessions[first["sessions"][-1]["id"]]
        query = "?" + urllib.parse.urlencode({"limit": 2, "cursor": first["next_cursor"], "ended": "exclude"})
        status, _, page = self.get(query)
        self.assertEqual(status, 200)
        self.assertEqual([r["id"] for r in page["sessions"]], self.ids[1::-1])

    def test_visibility_is_rechecked_and_invisible_records_do_not_fill_pages(self):
        original = self.app._may_attach
        hidden = self.ids[-1]
        with patch.object(self.app, "_may_attach", side_effect=lambda user, sid: None if sid == hidden else original(user, sid)):
            status, _, page = self.get("?limit=2")
        self.assertEqual(status, 200)
        self.assertEqual([r["id"] for r in page["sessions"]], self.ids[-2:0:-1])
        query = "?" + urllib.parse.urlencode({"limit": 2, "cursor": page["next_cursor"]})
        with patch.object(self.app, "_may_attach", return_value=None):
            status, _, page = self.get(query)
        self.assertEqual((status, page), (200, {"sessions": []}))

    def test_history_page_over_http_uses_the_shipped_client(self):
        with StubServer(self.app) as url:
            client = Client(url, timeout=5)
            client.login(_serve.USER, _serve.PASSWORD)
            first = client.list_sessions(ended="only", limit=1)
            last = client.list_sessions(ended="only", limit=1, cursor=first["next_cursor"])
        self.assertEqual([first["sessions"][0]["id"], last["sessions"][0]["id"]], self.ids[:1:-1])

    def test_cursor_without_limit_is_refused(self):
        status, _, body = self.get("?cursor=private-supplied-value")
        self.assertEqual((status, body["error"]["code"]), (422, "invalid_request"))
        self.assertNotIn("private-supplied-value", json.dumps(body))

    def test_malformed_cursors_have_fixed_message_without_echo(self):
        for cursor in ("", "private-supplied-value", "x" * 257, "W10=", "eyJpZCI6IngifQ=="):
            with self.subTest(cursor=cursor):
                status, _, body = self.get("?" + urllib.parse.urlencode({"limit": 2, "cursor": cursor}))
                self.assertEqual(status, 422)
                self.assertEqual(body, {"error": {"code": "invalid_request", "message": "cursor is invalid"}})

    def test_query_refusals_are_strict_and_do_not_echo_values(self):
        for query in ("limit=0", "limit=501", "limit=-1", "limit=1.5", "limit=", "limit=%D9%A2",
                      "limit=" + "9" * 5000, "limit=1&limit=2", "ended=", "ended=secret-filter",
                      "ended=only&ended=exclude", "cursor=a&cursor=b&limit=1", "secret-name=value"):
            with self.subTest(query=query):
                status, _, body = self.get("?" + query)
                self.assertEqual((status, body["error"]["code"]), (422, "invalid_request"))
                self.assertNotIn("secret", json.dumps(body))

    def test_authentication_precedes_query_validation(self):
        status, _, body = self.app.http("GET", "/sessions?limit=0", {}, None)
        self.assertEqual((status, body["error"]["code"]), (401, "unauthenticated"))


class SessionHistoryContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.from_tree(CONTRACT)
        cls.api = json.loads((CONTRACT / "openapi/tsb.openapi.json").read_text())

    def test_paging_shape_is_additive_and_closed(self):
        schema = "openapi/tsb.openapi.json#/components/schemas/SessionList"
        for page in ({"sessions": []}, {"sessions": [], "next_cursor": "opaque"}):
            self.assertEqual(self.registry.validate(page, schema), [])
        for page in ({"sessions": [], "next_cursor": ""},
                     {"sessions": [], "next_cursor": "x" * 257},
                     {"sessions": [], "next_cursor": 1},
                     {"sessions": [], "host_path": "hidden"}):
            self.assertTrue(self.registry.validate(page, schema))
        self.assertEqual(self.api["components"]["schemas"]["SessionList"]["required"], ["sessions"])

    def test_query_limits_and_ended_enum(self):
        params = {p["name"]: p for p in self.api["paths"]["/sessions"]["get"]["parameters"]}
        self.assertEqual(set(params), {"ended", "limit", "cursor"})
        self.assertEqual(params["ended"]["schema"]["enum"], ["include", "exclude", "only"])
        self.assertEqual(params["limit"]["schema"], {"type": "integer", "minimum": 1, "maximum": 500})
        self.assertFalse(any(p["required"] for p in params.values()))

    def test_retention_has_no_default_and_preserves_older_configs(self):
        config = json.loads((CONTRACT / "schemas/config.schema.json").read_text())
        self.assertNotIn("sessions", config["required"])
        section = config["properties"]["sessions"]
        self.assertNotIn("required", section)
        self.assertNotIn("default", section["properties"]["ended_retention"])
        old = fixture("C5/config-valid.json")["case"]["instances"][0]["doc"]
        self.assertEqual(self.registry.validate(old, "schemas/config.schema.json"), [])
        for instance in fixture("C5/config-sessions-ended-retention.json")["case"]["instances"]:
            errors = self.registry.validate(instance["doc"], "schemas/config.schema.json")
            self.assertEqual(not errors, instance["valid"], instance["note"])

    def test_reference_marks_and_provider_obligations_are_explicit(self):
        index = json.loads((CONTRACT / "fixtures/graded-by.json").read_text())
        names = ["C1/" + n for n in ("session-list-paged", "session-list-ended-only",
                 "session-list-cursor-without-limit", "session-list-cursor-invalid",
                 "session-list-legacy-complete", "session-get-removed-404", "session-retention-safety")]
        names.append("C5/config-sessions-ended-retention")
        for name in names:
            with self.subTest(name=name):
                doc = fixture(name + ".json")
                self.assertEqual(doc["grade"], "reference")
                self.assertEqual(profiles.status(doc["mark"], "e4"), profiles.PUBLISHED)
                self.assertEqual(profiles.status(doc["mark"], "e5"), profiles.REQUIRED)
                self.assertEqual(index[name][0]["unit"], "U2")
                self.assertIn("owed", index[name][0])

    def test_order_fixture_exercises_creation_order_and_id_tie_break_across_pages(self):
        steps = fixture("C1/session-list-paged.json")["case"]["steps"]
        records = [r for step in steps for r in step["expect"]["example"]["sessions"]]
        keys = [(r["created_at"], r["id"]) for r in records]
        self.assertEqual(keys, sorted(keys, reverse=True))
        self.assertEqual(records[1]["created_at"], records[2]["created_at"])
        self.assertGreater(records[1]["id"], records[2]["id"])
        self.assertNotIn("next_cursor", steps[-1]["expect"]["example"])  # example chooses omission


    def test_unpaged_fixture_matches_are_set_based_and_reject_extra_records(self):
        for name in ("session-list-ended-only", "session-list-legacy-complete"):
            for step in fixture("C1/" + name + ".json")["case"]["steps"]:
                if "limit=" in step["http"]["path"]:
                    continue
                expect = step["expect"]
                answer = copy.deepcopy(expect["example"])
                answer["sessions"].reverse()
                matches = expect["match"]
                self.assertTrue(all(_exchange._match_holds(answer, m, lambda _: None) for m in matches))
                answer["sessions"].append(dict(answer["sessions"][0], id="invisible-extra"))
                self.assertFalse(all(_exchange._match_holds(answer, m, lambda _: None) for m in matches))

    def test_full_final_fixture_page_allows_optional_cursor(self):
        for name in ("session-list-paged", "session-list-ended-only"):
            steps = fixture("C1/" + name + ".json")["case"]["steps"]
            last = [s for s in steps if "cursor=" in s["http"]["path"]][-1]["expect"]
            for cursor in (None, "opaque-next-empty"):
                answer = copy.deepcopy(last["example"])
                if cursor is not None:
                    answer["next_cursor"] = cursor
                self.assertTrue(all(_exchange._match_holds(answer, m, lambda _: None) for m in last["match"]))


if __name__ == "__main__":
    unittest.main()
