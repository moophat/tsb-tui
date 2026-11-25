"""The client against the contract: the guard that makes Rule 8 hold for it.

The client is hand-written, so nothing but this test keeps it on the contract. Every request it
makes must be an operation of openapi/tsb.openapi.json with a body its request schema accepts,
and every frame it sends must validate against schemas/ws-frames.schema.json. It runs end to end
against tests/stub_core.py through both transports.
"""

import asyncio
import time
import unittest

import tsb_contract_client as tcc
from tsbc import _aws, _exchange, _http, _ws
from tsbc import _wsframe as wf
from tsbc.fixtures import contract_version
from tsbc.schema import Registry, validate

from . import CONTRACT
from .stub_core import NOCRED, PASSWORD, PEER, TARGET, USER, StubCore
from .stub_server import StubServer

REGISTRY = Registry.from_tree(CONTRACT)
TICKET = "FIXTURE-TICKET-000000000000000000"
# One of every server frame v0.2 adds, as the core would send them.
NEW_FRAMES = (
    {"t": "notice", "v": 1, "session_id": "sess-1", "ends_at": "2026-09-29T01:06:00Z", "ends_by": "link_timeout", "ending": True},
    {"t": "quiet_prompt", "v": 1, "prompt_id": TICKET, "code": "quiet_prompt.kill", "text": "Kill the session?"},
    {"t": "token", "v": 1, "kind": "control", "holder": None, "reason": "detach"},
    {"t": "x-later-frame", "v": 1},  # a frame this client does not know: skipped
    {"t": "input_refused", "v": 1, "reason": "replaying"},
    {"t": "data_path", "v": 1, "session_id": "sess-1", "state": "lost", "at": "2026-09-29T01:00:01Z"},
)


def exercise(url: str) -> None:
    """Every public call the client offers, once."""
    c = tcc.Client(url)
    c.login(USER, PASSWORD)
    assert c.me()["username"] == USER
    account = c.create_account("exercise-account", PASSWORD)
    assert account["account"]["username"] == "exercise-account"
    assert any(a["username"] == "exercise-account" for a in c.accounts())
    c.disable_account("exercise-account")
    c.enable_account("exercise-account", PASSWORD)
    c.set_account_password("exercise-account", PASSWORD)
    c.change_own_password(PASSWORD, PASSWORD)
    created = c.create_session(TARGET, "new", "xterm-256color", 120, 40)
    sid = created["session"]["id"]
    c.create_session(TARGET, "reuse", "xterm-256color", 120, 40)
    c.create_session(TARGET, "reuse", "xterm-256color", 120, 40, connection_id=created["session"]["connection_id"])
    assert any(s["id"] == sid for s in c.sessions())
    assert c.session(sid)["id"] == sid
    assert c.get_target(TARGET)["id"] == TARGET
    definition = {"id": "client-target", "type": "ssh", "host": "192.0.2.60", "user": "fixture-account"}
    assert c.create_target(definition)["id"] == definition["id"]
    assert c.get_target_definition(definition["id"]) == definition
    assert c.replace_target(definition["id"], definition)["id"] == definition["id"]
    c.delete_target(definition["id"])
    assert c.list_targets()["targets"]
    assert any(r["id"] == TARGET for r in c.iter_targets(limit=2))
    assert c.ws_ticket(sid)["v"] == 1
    att = c.attach(sid)
    att.resize(90, 30)
    att.answer_quiet_prompt(TICKET, False)
    att.close()
    with c.alerts(read_timeout=5) as stream:
        assert len(list(stream)) == 2
    c.close(sid)
    c.logout()


class MatchesContract(unittest.TestCase):
    def test_every_request_is_an_operation_with_a_valid_body(self):
        core = StubCore()
        with StubServer(core) as url:
            exercise(url)
        self.assertTrue(core.requests)
        for method, path, body in core.requests:
            op = _exchange.find_operation(REGISTRY, method, path)
            self.assertIsNotNone(op, f"{method} {path} is not in the OpenAPI document")
            schema = _exchange.request_schema(REGISTRY, op)
            if body is not None:
                self.assertIsNotNone(schema, f"{method} {op.template} takes no body")
                self.assertEqual(validate(body, schema, REGISTRY, _exchange.OPENAPI), [], f"{method} {op.template}")
            else:
                self.assertFalse(schema and op.op["requestBody"]["required"], f"{method} {op.template} needs a body")

    def test_every_frame_sent_validates(self):
        core = StubCore()
        with StubServer(core) as url:
            exercise(url)
        self.assertEqual({f["t"] for f in core.frames}, {"attach", "resize", "quiet_prompt_answer"})
        for frame in core.frames:
            self.assertEqual(REGISTRY.validate(frame, "schemas/ws-frames.schema.json#/$defs/client"), [], frame)

    def test_attach_path_is_the_websocket_operation(self):
        self.assertEqual(_exchange.websocket_operation(REGISTRY).template, tcc.client.ATTACH_PATH)

    def test_client_is_built_for_this_tree(self):
        self.assertEqual(tcc.CONTRACT_VERSION, contract_version(CONTRACT).removesuffix("-dev"))

    def test_a_server_frame_that_is_not_json_is_ignored(self):
        # NaN is Python-json but not JSON; such a frame is not one the client knows.
        self.assertIsNone(tcc.frames.parse_server('{"t": "attached", "v": NaN}'))
        self.assertEqual(tcc.frames.parse_server('{"t": "attached", "v": 1}'), {"t": "attached", "v": 1})


class PublicErrors(unittest.TestCase):
    def test_the_transport_errors_are_the_ones_the_transports_raise(self):
        # A surface that catches tsb_contract_client.ConnectionClosed catches what either transport
        # raises; were they two classes, one `except` would let the other escape and crash a UI.
        self.assertIs(tcc.ConnectionClosed, _ws.ConnectionClosed)
        self.assertIs(tcc.ConnectionClosed, _aws.ConnectionClosed)
        self.assertIs(tcc.HandshakeError, wf.HandshakeError)
        self.assertIs(tcc.ProtocolError, wf.ProtocolError)
        self.assertIs(tcc.HttpError, _http.HttpError)
        for name in ("ConnectionClosed", "HandshakeError", "ProtocolError", "HttpError", "AlertStream"):
            self.assertIn(name, tcc.__all__)

    def test_a_closed_socket_raises_the_public_class(self):
        core = StubCore()
        with StubServer(core) as url:
            client = tcc.Client(url, timeout=5)
            client.login(USER, PASSWORD)
            sid = client.create_session(TARGET, "new", "xterm-256color", 120, 40)["session"]["id"]
            att = client.attach(sid)
            att.close()
            with self.assertRaises(tcc.ConnectionClosed):
                att.send(b"x")


class NewFrames(unittest.TestCase):
    def test_every_new_server_frame_is_received_and_valid(self):
        core = StubCore(after_attach=NEW_FRAMES)
        with StubServer(core) as url:
            client = tcc.Client(url, timeout=5)
            client.login(USER, PASSWORD)
            sid = client.create_session(TARGET, "new", "xterm-256color", 120, 40)["session"]["id"]
            att = client.attach(sid)
            got = [att.recv_message(5) for _ in range(5)]
            att.recv(5)  # the attach prefix dump: recv() skips the prefix frame, returns the bytes
            self.assertEqual(att.recv_message(5)["t"], "clients")  # v0.3 knows the clients frame
            att.send(b"hi")
            self.assertEqual(att.recv_message(5), b"hi")
            att.close()
        self.assertEqual([g["t"] for g in got], ["notice", "quiet_prompt", "token", "input_refused", "data_path"])
        for frame in got:
            self.assertEqual(REGISTRY.validate(frame, "schemas/ws-frames.schema.json#/$defs/server"), [], frame)

    def test_recv_keeps_its_v01_meaning(self):
        # recv() is output only: a v0.1 caller never sees a frame it did not ask for.
        core = StubCore(after_attach=NEW_FRAMES)
        with StubServer(core) as url:
            client = tcc.Client(url, timeout=5)
            client.login(USER, PASSWORD)
            sid = client.create_session(TARGET, "new", "xterm-256color", 120, 40)["session"]["id"]
            att = client.attach(sid)
            att.recv(5)  # the attach prefix dump
            att.send(b"hi")
            self.assertEqual(att.recv(5), b"hi")
            att.close()

    def test_the_answer_validates(self):
        frame = tcc.frames.quiet_prompt_answer_frame(TICKET, True)
        self.assertEqual(REGISTRY.validate(frame, "schemas/ws-frames.schema.json#/$defs/client"), [])


class AlertStream(unittest.TestCase):
    def setUp(self):
        self.core = StubCore()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)

    def test_the_stream_yields_each_alert_and_skips_the_rest(self):
        # A comment (the keepalive) and an event type this client does not know are skipped; a
        # data field split over two lines is one event; each event is an AlertEvent.
        client = tcc.Client(self.url, timeout=5)
        client.login(USER, PASSWORD)
        with client.alerts(read_timeout=5) as stream:
            events = list(stream)
        self.assertEqual([(e["alert_id"], e["state"]) for e in events],
                         [("fixture-alert-1", "raised"), ("fixture-alert-1", "cleared")])
        schema = "openapi/tsb.openapi.json#/components/schemas/AlertEvent"
        for event in events:
            self.assertEqual(REGISTRY.validate(event, schema), [], event)
        self.assertIn(("/alerts", "text/event-stream"), self.core.accepts)

    def test_a_line_or_an_event_past_its_limit_fails_the_stream(self):
        # A peer that never ends a line, or never ends an event, would grow the reader's memory
        # without bound; past either documented maximum the stream fails, as a transport error.
        client = tcc.Client(self.url, timeout=5)
        client.login(USER, PASSWORD)
        line = b"event: alert\ndata: " + b"x" * _http.MAX_STREAM_LINE + b"\n\n"
        lines = b"event: alert\n" + (b"data: " + b"x" * 1023 + b"\n") * (_http.MAX_EVENT_DATA // 1024 + 1) + b"\n"
        for body, needle in ((line, f"a line longer than {_http.MAX_STREAM_LINE} bytes"),
                             (lines, f"an event's data is longer than {_http.MAX_EVENT_DATA} bytes")):
            with self.subTest(needle):
                self.core.alerts_body = body
                with client.alerts(read_timeout=5) as stream, self.assertRaises(tcc.HttpError) as ctx:
                    next(stream)
                self.assertIn(needle, str(ctx.exception))
        # A line of exactly the maximum, its end included, is within it.
        head = b'data: {"alert_id": "fixture-alert-1", "pad": "'
        tail = b'"}\n'
        self.core.alerts_body = (b"event: alert\n" + head + b"x" * (_http.MAX_STREAM_LINE - len(head) - len(tail))
                                 + tail + b"\n")
        with client.alerts(read_timeout=5) as stream:
            self.assertEqual(next(stream)["alert_id"], "fixture-alert-1")

    def test_a_deadline_bounds_every_read_not_only_the_gaps_between_them(self):
        # The runner reads a live alert stream until a deadline. Checked only between lines, a
        # silent stream would hold the read for the stream's own timeout past it; so each read is
        # bounded by what remains of the deadline, and none starts once it has passed.
        class Silent:
            def __init__(self, lines: list[bytes]) -> None:
                self.lines, self.timeouts = lines, []

            def settimeout(self, timeout: float | None) -> None:
                self.timeouts.append(timeout)

            def readline(self) -> bytes:
                if self.lines:
                    return self.lines.pop(0)
                raise _http.StreamTimeout("stream: no data within the read timeout")

        stream = Silent([b": keepalive\n", b"event: alert\n", b"data: {}\n", b"\n"])
        self.assertEqual(_http.next_event(stream, time.monotonic() + 5), ("alert", "{}"))
        self.assertEqual(len(stream.timeouts), 4)
        self.assertTrue(all(0 < t <= 5 for t in stream.timeouts), stream.timeouts)
        late = Silent([b"event: alert\n", b"data: {}\n", b"\n"])
        with self.assertRaises(_http.StreamTimeout):
            _http.next_event(late, time.monotonic() - 1)
        self.assertEqual(late.lines, [b"event: alert\n", b"data: {}\n", b"\n"])  # nothing read

    def test_a_refusal_is_an_api_error(self):
        peer = tcc.Client(self.url, timeout=5)
        peer.login(PEER, PASSWORD)
        with self.assertRaises(tcc.ApiError) as ctx:
            peer.alerts()
        self.assertEqual((ctx.exception.status, ctx.exception.code), (403, "forbidden"))
        with self.assertRaises(tcc.ApiError) as ctx:
            tcc.Client(self.url, timeout=5).alerts()
        self.assertEqual((ctx.exception.status, ctx.exception.code), (401, "unauthenticated"))


class Behaviour(unittest.TestCase):
    def setUp(self):
        self.core = StubCore()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)

    def new_session(self) -> str:
        return self.client.create_session(TARGET, "new", "xterm-256color", 120, 40)["session"]["id"]

    def test_blocking_attach_round_trip(self):
        sid = self.new_session()
        att = self.client.attach(sid)
        self.assertEqual(att.session["id"], sid)
        self.assertEqual(att.session["control_holder"]["client_id"], att.client_id)
        att.recv(5)  # the attach prefix dump (C2 decision 3): a v0.2 client renders it as bytes
        att.send(b"echo hi\r")
        self.assertEqual(att.recv(5), b"echo hi\r")
        att.close()
        # The server records the detach asynchronously when it handles the close, so the REST read
        # may land first: wait for the state the close promises (up to 2 s), then assert.
        deadline = time.monotonic() + 2
        state = self.client.session(sid)["state"]
        while state != "detached" and time.monotonic() < deadline:
            time.sleep(0.01)
            state = self.client.session(sid)["state"]
        self.assertEqual(state, "detached")

    def test_server_close_ends_recv_with_the_code(self):
        sid = self.new_session()
        att = self.client.attach(sid)
        att.recv(5)  # the attach prefix dump, before the close the session triggers
        self.client.close(sid)
        self.assertIsNone(att.recv(5))
        self.assertEqual(att.close_code, 4010)

    def test_attach_refused_carries_the_close_code(self):
        sid = self.new_session()
        self.client.close(sid)
        with self.assertRaises(tcc.AttachRefused) as ctx:
            self.client.attach(sid)
        self.assertEqual(ctx.exception.code, 4004)

    def test_errors_carry_the_contract_code(self):
        with self.assertRaises(tcc.ApiError) as ctx:
            self.client.create_session(NOCRED, "new", "xterm-256color", 120, 40)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "no_resolving_credential"))

    def test_the_connection_choice_has_no_default(self):
        with self.assertRaises(ValueError):
            self.client.create_session(TARGET, "", "xterm-256color", 120, 40)
        with self.assertRaises(ValueError):  # a connection to reuse only makes sense with reuse
            self.client.create_session(TARGET, "new", "xterm-256color", 120, 40, connection_id="conn-1")

    def test_ambiguous_reuse_is_the_operators_choice(self):
        a = self.client.create_session(TARGET, "new", "xterm-256color", 120, 40)["session"]
        self.client.create_session(TARGET, "new", "xterm-256color", 120, 40)
        with self.assertRaises(tcc.ApiError) as ctx:
            self.client.create_session(TARGET, "reuse", "xterm-256color", 120, 40)
        self.assertEqual(ctx.exception.code, "connection_choice_required")
        picked = self.client.create_session(TARGET, "reuse", "xterm-256color", 120, 40,
                                            connection_id=a["connection_id"])["session"]
        self.assertEqual(picked["connection_id"], a["connection_id"])

    def test_ids_cannot_add_path_segments(self):
        with self.assertRaises(tcc.ApiError):
            self.client.session("../auth/me")
        self.assertIn(("GET", "/sessions/..%2Fauth%2Fme", None), self.core.requests)

    def test_logout_forgets_the_cookie(self):
        self.client.logout()
        with self.assertRaises(tcc.ApiError) as ctx:
            self.client.me()
        self.assertEqual(ctx.exception.code, "unauthenticated")


class TargetCalls(unittest.TestCase):
    """The client's inventory calls. The TUI imports only this client, so these are the calls a
    target picker is built on: a page, the filters, paging to the end, and the two 404s that read
    alike."""

    def setUp(self):
        self.core = StubCore()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)

    def test_create_sends_exactly_the_definition_without_adding_credentials_or_defaults(self):
        definition = {"id": "client-exact-target", "type": "ssh", "host": "192.0.2.60",
                      "user": "fixture-account", "tags": ["lab"]}
        self.client.create_target(definition)
        method, path, body = self.core.requests[-1]
        self.assertEqual((method, path, body), ("POST", "/targets", definition))
        self.assertEqual(set(body), {"id", "type", "host", "user", "tags"})
        self.assertNotIn("port", definition)
        self.assertEqual(self.client.get_target_definition(definition["id"]), definition)
        self.client.delete_target(definition["id"])

    def test_a_page_follows_its_cursor_to_the_end_of_the_fleet(self):
        page = self.client.list_targets(limit=2)
        self.assertEqual(len(page["targets"]), 2)
        self.assertEqual(page["next_cursor"], page["targets"][-1]["id"])
        self.assertEqual([r["id"] for r in self.client.iter_targets(limit=2)], sorted(self.core.targets))

    def test_the_filters_narrow_the_page(self):
        self.assertEqual([r["id"] for r in self.client.list_targets(type="telnet")["targets"]], ["telnet-scenario"])
        self.assertEqual([r["id"] for r in self.client.list_targets(tags=["scenario", "shell"])["targets"]], [TARGET])
        # Every tag named must be on the target, so two tags that no record carries give nothing.
        self.assertEqual(self.client.list_targets(tags=["scenario", "nocred"])["targets"], [])

    def test_one_target_by_id_carries_the_port_in_force(self):
        self.assertEqual(self.client.get_target(TARGET)["id"], TARGET)
        self.assertEqual(self.client.get_target(TARGET)["port"], 22)

    def test_an_absent_target_and_an_invisible_one_answer_alike(self):
        for tid in ("fixture-no-such-target", "fixture-secret-target"):
            with self.subTest(tid=tid), self.assertRaises(tcc.ApiError) as ctx:
                self.client.get_target(tid)
            self.assertEqual((ctx.exception.status, ctx.exception.code), (404, "target_not_found"))

    def test_the_inventory_needs_the_login_cookie(self):
        fresh = tcc.Client(self.url, timeout=5)
        with self.assertRaises(tcc.ApiError) as ctx:
            fresh.list_targets()
        self.assertEqual((ctx.exception.status, ctx.exception.code), (401, "unauthenticated"))

    def test_a_parameter_outside_the_list_is_refused(self):
        for path in ("/targets?colour=red", "/targets?limit=0"):
            with self.subTest(path=path):
                resp = self.client._http.request("GET", path)
                self.assertEqual((resp.status, resp.json()["error"]["code"]), (422, "invalid_request"))


class AsyncBehaviour(unittest.IsolatedAsyncioTestCase):
    def setUp(self):  # the stub server starts and stops outside the event loop
        self.core = StubCore()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()

    def tearDown(self):
        self.server.__exit__(None, None, None)

    async def test_async_attach_round_trip_without_blocking_the_loop(self):
        client = tcc.Client(self.url, timeout=5)
        await asyncio.to_thread(client.login, USER, PASSWORD)
        created = await asyncio.to_thread(client.create_session, TARGET, "new", "xterm-256color", 120, 40)
        att = await client.async_attach(created["session"]["id"])
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(3):
                await asyncio.sleep(0.02)
                ticks += 1

        task = asyncio.create_task(ticker())
        await att.recv(5)  # the attach prefix dump
        with self.assertRaises(TimeoutError):
            await att.recv(0.2)  # nothing more to read: the loop keeps running meanwhile
        await task
        self.assertEqual(ticks, 3)
        await att.send(b"x")
        self.assertEqual(await att.recv(5), b"x")
        await att.resize(100, 30)
        await att.answer_quiet_prompt(TICKET, True)
        await att.close()
        self.assertIn({"t": "resize", "v": 1, "cols": 100, "rows": 30}, self.core.frames)
        self.assertIn({"t": "quiet_prompt_answer", "v": 1, "prompt_id": TICKET, "kill": True}, self.core.frames)

    async def test_async_recv_message_returns_the_new_frames(self):
        self.core.after_attach = NEW_FRAMES
        client = tcc.Client(self.url, timeout=5)
        await asyncio.to_thread(client.login, USER, PASSWORD)
        created = await asyncio.to_thread(client.create_session, TARGET, "new", "xterm-256color", 120, 40)
        att = await client.async_attach(created["session"]["id"])
        got = [await att.recv_message(5) for _ in range(5)]
        await att.close()
        self.assertEqual([g["t"] for g in got], ["notice", "quiet_prompt", "token", "input_refused", "data_path"])


if __name__ == "__main__":
    unittest.main()
