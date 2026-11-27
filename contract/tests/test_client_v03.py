"""The v0.3 client calls against the contract, and the v0.2.1 surface kept whole.

Phase 2 part C2: `attach(..., cols, rows)`, `Attachment.handoff(to)`, `resize` returning the
`size` answer, `answer_stale`, `Client.remove_client`, `Client.output_page` (bytes + TSB-Page-*
headers), and `recv_message` returning the new server frames. Every new frame sent validates
against schemas/ws-frames.schema.json, and every request is an operation of the OpenAPI document,
exactly as the v0.2.1 guard already checks for the older calls. The v0.2.1 method signatures are
asserted unchanged here so a later edit cannot quietly drop them.
"""

import asyncio
import inspect
import time
import unittest
from unittest import mock

import tsb_contract_client as tcc
from tsbc._exchange import find_operation, request_schema
from tsbc.schema import Registry

from . import CONTRACT
from .stub_core import PASSWORD, TARGET, USER, StubCore
from .stub_server import StubServer

REGISTRY = Registry.from_tree(CONTRACT)
TICKET = "FIXTURE-TICKET-000000000000000000"


def open_session(client: tcc.Client) -> str:
    return client.create_session(TARGET, "new", "xterm-256color", 120, 40)["session"]["id"]


def sent_frame(core: StubCore, t: str, deadline: float = 2.0) -> dict:
    """The first frame of type `t` the core recorded, waiting for the server to process the send."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        found = next((f for f in core.frames if f["t"] == t), None)
        if found is not None:
            return found
        time.sleep(0.01)
    raise AssertionError(f"the core never received a {t!r} frame (got {[f['t'] for f in core.frames]})")


class AttachViewport(unittest.TestCase):
    def setUp(self):
        self.core = StubCore()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)

    def test_attach_without_a_viewport_sends_no_cols_or_rows(self):
        sid = open_session(self.client)
        att = self.client.attach(sid)
        att.close()
        sent = next(f for f in self.core.frames if f["t"] == "attach")
        self.assertNotIn("cols", sent)
        self.assertNotIn("rows", sent)

    def test_attach_with_a_viewport_sends_them_and_they_validate(self):
        sid = open_session(self.client)
        att = self.client.attach(sid, cols=100, rows=30)
        att.close()
        sent = next(f for f in self.core.frames if f["t"] == "attach")
        self.assertEqual((sent["cols"], sent["rows"]), (100, 30))
        self.assertEqual(REGISTRY.validate(sent, "schemas/ws-frames.schema.json#/$defs/client"), [])


class HandoffAndStale(unittest.TestCase):
    def setUp(self):
        self.core = StubCore()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)

    def test_a_handoff_to_its_own_client_id_is_a_take_over_and_validates(self):
        sid = open_session(self.client)
        att = self.client.attach(sid)
        att.recv(5)  # the attach prefix dump
        att.handoff(att.client_id)  # the owner's explicit take-over (5.53)
        frame = sent_frame(self.core, "handoff")
        self.assertEqual(frame, {"t": "handoff", "v": 1, "to": att.client_id})
        self.assertEqual(REGISTRY.validate(frame, "schemas/ws-frames.schema.json#/$defs/client"), [])
        att.close()

    def test_a_handoff_to_escrow_names_null(self):
        sid = open_session(self.client)
        att = self.client.attach(sid)
        att.recv(5)
        att.handoff(None)
        frame = sent_frame(self.core, "handoff")
        self.assertIsNone(frame["to"])
        self.assertEqual(REGISTRY.validate(frame, "schemas/ws-frames.schema.json#/$defs/client"), [])
        att.close()

    def test_an_answer_stale_validates(self):
        sid = open_session(self.client)
        att = self.client.attach(sid)
        att.recv(5)
        att.answer_stale(TICKET, "ctrl_l")
        frame = sent_frame(self.core, "stale_answer")
        self.assertEqual(frame, {"t": "stale_answer", "v": 1, "prompt_id": TICKET, "choice": "ctrl_l"})
        self.assertEqual(REGISTRY.validate(frame, "schemas/ws-frames.schema.json#/$defs/client"), [])
        att.close()


class ResizeAnswer(unittest.TestCase):
    def setUp(self):
        self.core = StubCore()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)

    def test_resize_returns_the_size_answer(self):
        sid = open_session(self.client)
        att = self.client.attach(sid)
        att.recv(5)  # the attach prefix dump, before live frames
        answer = att.resize(90, 30)
        self.assertIsNotNone(answer)
        self.assertEqual([answer["t"], answer["cols"], answer["rows"], answer["reason"]],
                         ["size", 90, 30, "applied"])
        self.assertEqual(REGISTRY.validate(answer, "schemas/ws-frames.schema.json#/$defs/server"), [])
        att.close()

    def test_resize_from_a_non_holder_is_answered_not_typist(self):
        # A second client on the same owner does not hold the token; the size answer carries the
        # size that stands and the reason (terminal-size-and-control.md §3).
        sid = open_session(self.client)
        holder = self.client.attach(sid)
        holder.recv(5)
        other = self.client.attach(sid)
        other.recv(5)
        answer = other.resize(50, 20)
        self.assertEqual(answer["reason"], "not_typist")
        self.assertEqual((answer["cols"], answer["rows"]), (120, 40))
        holder.close()
        other.close()


class OutputPage(unittest.TestCase):
    def setUp(self):
        self.core = StubCore()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)

    def test_a_page_returns_bytes_and_the_tsb_page_headers(self):
        sid = open_session(self.client)
        att = self.client.attach(sid)
        att.recv(5)
        att.send(b"echo hi\r")
        att.recv(5)  # the echoed output, recorded in the ring
        body, headers = self.client.output_page(sid, before_seq=99)
        att.close()
        self.assertEqual(body, b"echo hi\r")  # the one output frame the ring holds
        self.assertTrue(all(name.startswith("tsb-page-") for name in headers))
        self.assertEqual(headers["tsb-page-first-seq"], "1")
        self.assertEqual(headers["tsb-page-last-seq"], "1")
        self.assertEqual(headers["tsb-page-more"], "false")

    def test_the_page_request_is_the_openapi_operation(self):
        sid = open_session(self.client)
        self.client.output_page(sid, before_seq=5, max_bytes=100)
        method, path, _body = next(r for r in self.core.requests if r[0] == "GET" and "/output" in r[1])
        self.assertIsNotNone(find_operation(REGISTRY, method, path))
        self.assertIn("before_seq=5", path)
        self.assertIn("max_bytes=100", path)

    def test_an_ended_session_page_is_empty(self):
        sid = open_session(self.client)
        self.client.close(sid)
        body, headers = self.client.output_page(sid)
        self.assertEqual(body, b"")
        self.assertEqual(headers["tsb-page-more"], "false")


class RemoveClient(unittest.TestCase):
    def setUp(self):
        self.core = StubCore()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = tcc.Client(self.url, timeout=5)
        self.client.login(USER, PASSWORD)

    def test_remove_client_closes_it_and_leaves_the_session(self):
        sid = open_session(self.client)
        att = self.client.attach(sid)
        att.recv(5)
        cid = att.client_id
        self.client.remove_client(sid, cid)
        self.assertIsNone(att.recv(5))
        self.assertEqual(att.close_code, 4014)
        self.assertEqual(self.client.session(sid)["state"] not in ("killed",), True)
        method, path, _body = next(r for r in self.core.requests if r[0] == "DELETE" and "clients" in r[1])
        self.assertIsNotNone(find_operation(REGISTRY, method, path))
        self.assertEqual(request_schema(REGISTRY, find_operation(REGISTRY, method, path)), None)


class V021Surface(unittest.TestCase):
    """The v0.2.1 signatures the new epoch must not change (plan §5 keeps v0.2.1 whole)."""

    def test_every_v021_signature_is_unchanged(self):
        # The parameter lists, by name and default, pinned so a later edit cannot silently drop one.
        # Release A of login-at-connect adds `login`, a trailing optional keyword, and the vault release adds
        # `credential` after it (the operator's choice of stored credential): nothing before either moved.
        params = inspect.signature(tcc.Client.create_session).parameters
        self.assertEqual(list(params),
                         ["self", "target_id", "connection", "term", "cols", "rows", "connection_id", "login",
                          "credential"])
        self.assertIsNone(params["login"].default)
        self.assertIsNone(params["credential"].default)
        self.assertIsNone(params["connection_id"].default)
        self.assertEqual(list(inspect.signature(tcc.Client.ws_ticket).parameters), ["self", "session_id"])
        self.assertEqual(list(inspect.signature(tcc.Client.close).parameters), ["self", "session_id"])
        self.assertEqual(list(inspect.signature(tcc.Client.sessions).parameters), ["self"])
        self.assertEqual(list(inspect.signature(tcc.Client.session).parameters), ["self", "session_id"])
        self.assertEqual(list(inspect.signature(tcc.Client.me).parameters), ["self"])
        self.assertEqual(list(inspect.signature(tcc.Client.logout).parameters), ["self"])
        self.assertEqual(list(inspect.signature(tcc.Client.alerts).parameters), ["self", "read_timeout"])
        self.assertEqual(list(inspect.signature(tcc.Attachment.send).parameters), ["self", "data"])
        self.assertEqual(list(inspect.signature(tcc.Attachment.recv).parameters), ["self", "timeout"])
        self.assertEqual(list(inspect.signature(tcc.Attachment.recv_message).parameters), ["self", "timeout"])
        self.assertEqual(list(inspect.signature(tcc.Attachment.answer_quiet_prompt).parameters),
                         ["self", "prompt_id", "kill"])
        self.assertEqual(list(inspect.signature(tcc.Attachment.close).parameters), ["self"])


class AsyncV03(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.core = StubCore()
        self.server = StubServer(self.core)
        self.url = self.server.__enter__()

    def tearDown(self):
        self.server.__exit__(None, None, None)

    async def test_async_attach_viewport_handoff_stale_and_resize(self):
        client = tcc.Client(self.url, timeout=5)
        await asyncio.to_thread(client.login, USER, PASSWORD)
        sid = (await asyncio.to_thread(client.create_session, TARGET, "new", "xterm-256color", 120, 40))["session"]["id"]
        att = await client.async_attach(sid, cols=100, rows=30)
        await att.recv(5)  # the attach prefix dump
        await att.handoff(att.client_id)
        await att.answer_stale(TICKET, "continue")
        answer = await att.resize(90, 30)
        await att.close()
        sent = next(f for f in self.core.frames if f["t"] == "attach")
        self.assertEqual((sent["cols"], sent["rows"]), (100, 30))
        self.assertIn({"t": "handoff", "v": 1, "to": att.client_id}, self.core.frames)
        self.assertIn({"t": "stale_answer", "v": 1, "prompt_id": TICKET, "choice": "continue"}, self.core.frames)
        self.assertEqual([answer["t"], answer["reason"]], ["size", "applied"])

    async def test_async_recv_message_returns_prefix_and_clients(self):
        client = tcc.Client(self.url, timeout=5)
        await asyncio.to_thread(client.login, USER, PASSWORD)
        sid = (await asyncio.to_thread(client.create_session, TARGET, "new", "xterm-256color", 120, 40))["session"]["id"]
        att = await client.async_attach(sid)
        first = await att.recv_message(5)   # prefix (text)
        dump = await att.recv_message(5)    # the attach prefix dump (binary)
        second = await att.recv_message(5)  # clients (text)
        await att.close()
        self.assertEqual(first["t"], "prefix")
        self.assertIsInstance(dump, bytes)
        self.assertEqual(second["t"], "clients")


class _Clock:
    """A monotonic clock the test advances by the time each read would take, so a deadline's
    behaviour is deterministic and independent of the host's wall clock. Injected as the client
    module's `time`, the seam where the code under test reads one."""

    def __init__(self, start=1000.0):
        self.now = start

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _ChattyWS:
    """A WebSocket double that never carries a `size` frame: every recv returns one output chunk
    after a short pause, then raises TimeoutError once its script runs out. With `clock` set, a
    read advances that clock instead of sleeping, so the test needs no wall-clock margin."""

    closed = False

    def __init__(self, messages=(), chunks=60, pause=0.02, clock=None):
        from tsbc import _wsframe as wf
        self._wf = wf
        self.script = list(messages)
        self.chunks = chunks
        self.pause = pause
        self.clock = clock
        self.sent = []
        self.reads = 0

    def send_text(self, text):
        self.sent.append(text)

    def recv(self, timeout=None):
        self.reads += 1
        if self.clock is not None:
            self.clock.advance(self.pause)
        else:
            time.sleep(self.pause)
        if self.script:
            return self.script.pop(0)
        if self.chunks <= 0:
            raise TimeoutError("nothing more")
        self.chunks -= 1
        return self._wf.Message("binary", b"out")


def _attached(ws):
    return tcc.Attachment(ws, {"session": {}, "client_id": "c1"}, 5)


class ResizeWait(unittest.TestCase):
    def test_unrelated_frames_do_not_extend_the_wait(self):
        # One deadline for the answer: output arriving every 20 ms must not push the wait past it
        # (a per-read bound would run for chunks x bound, never reaching the documented None).
        clock = _Clock()
        att = _attached(_ChattyWS(clock=clock))
        start = clock.now
        with mock.patch("tsb_contract_client.attachment.time", clock):
            self.assertIsNone(att.resize(90, 30, timeout=0.3))
        # The wait ends at the one deadline (the timeout), whatever the host's real clock does: a
        # per-read bound would instead keep reading past it until the fake runs out of chunks.
        self.assertAlmostEqual(clock.now - start, 0.3, delta=0.02)

    def test_timeout_zero_returns_at_once_without_reading(self):
        # A v0.2.x core never answers `size`; timeout=0 keeps the v0.2.1 fire-and-forget call.
        clock = _Clock()
        ws = _ChattyWS(clock=clock)
        att = _attached(ws)
        start = clock.now
        with mock.patch("tsb_contract_client.attachment.time", clock):
            self.assertIsNone(att.resize(90, 30, timeout=0))
        self.assertEqual(ws.reads, 0)        # returns at once, without reading
        self.assertEqual(clock.now, start)   # and without advancing any clock
        self.assertEqual(len(ws.sent), 1)

    def test_recv_after_a_resize_leaves_queued_frames_for_recv_message(self):
        wf_ = __import__("tsbc._wsframe", fromlist=["Message"])
        token = '{"t": "token", "v": 1, "kind": "control", "holder": null, "reason": "release"}'
        ws = _ChattyWS([wf_.Message("text", token), wf_.Message("binary", b"first")], chunks=0)
        att = _attached(ws)
        self.assertIsNone(att.resize(90, 30, timeout=0.2))
        self.assertEqual(att.recv(0.1), b"first")
        frame = att.recv_message(0.1)
        self.assertEqual(frame["t"], "token")


class AsyncResizeWait(unittest.IsolatedAsyncioTestCase):
    async def test_unrelated_frames_do_not_extend_the_wait(self):
        class AsyncChatty(_ChattyWS):
            async def send_text(self, text):
                self.sent.append(text)

            async def recv(self, timeout=None):
                self.reads += 1
                if self.clock is not None:
                    self.clock.advance(self.pause)
                    await asyncio.sleep(0)
                else:
                    await asyncio.sleep(self.pause)
                return self._next()

            def _next(self):
                if self.chunks <= 0:
                    raise TimeoutError("nothing more")
                self.chunks -= 1
                return self._wf.Message("binary", b"out")

        clock = _Clock()
        att = tcc.AsyncAttachment(AsyncChatty(clock=clock), {"session": {}, "client_id": "c1"}, 5)
        start = clock.now
        with mock.patch("tsb_contract_client.attachment.time", clock):
            self.assertIsNone(await att.resize(90, 30, timeout=0.3))
        self.assertAlmostEqual(clock.now - start, 0.3, delta=0.02)


class IterTargetsGuard(unittest.TestCase):
    """`iter_targets` follows `next_cursor`; a server that repeats one never advances."""

    def test_a_repeating_cursor_stops_with_an_error(self):
        # The pages are the client's own call, mocked: page 2 answers the same next_cursor as page 1, so
        # a walk without a guard would ask again forever. It stops with a ValueError instead.
        client = tcc.Client("http://127.0.0.1:1", timeout=1)
        pages = [{"targets": [{"id": "a"}], "next_cursor": "a"},
                 {"targets": [{"id": "b"}], "next_cursor": "a"}]
        with (mock.patch.object(client, "list_targets", side_effect=pages),
              self.assertRaises(ValueError)):
            list(client.iter_targets(limit=1))


if __name__ == "__main__":
    unittest.main()