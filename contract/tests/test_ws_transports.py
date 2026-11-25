"""The blocking and the asyncio transport against one stdlib echo server.

Why two transports: the TUI runs inside Textual's event loop, where a blocking recv() freezes the
UI, while the runner and the rigs are plain scripts. Both must behave identically on the wire,
so the same scenarios run through both.
"""

import asyncio
import re
import socket
import threading
import unittest

from tsbc import _aws, _ws
from tsbc import _wsframe as wf

from .stub_server import App, ServerWS, StubServer


class EchoApp(App):
    """Echoes text; echoes binary in three fragments with a ping between them; closes on request."""

    ws_paths = ("/echo",)

    def __init__(self) -> None:
        self.pongs: list[bytes] = []

    def websocket(self, path: str, ws: ServerWS) -> None:
        while True:
            message = ws.recv()
            if message.kind == "close":
                if not ws.closed:
                    ws.send(wf.OP_CLOSE, wf.encode_close(message.close_code))
                return
            if message.kind == "pong":
                self.pongs.append(message.data)
                ws.send_text("pong:" + message.data.decode())
            elif message.kind == "text" and message.data.startswith("close:"):
                _, code, reason = message.data.split(":", 2)
                ws.close(int(code), reason)
                return
            elif message.kind == "text" and message.data == "ping-me":
                ws.send(wf.OP_PING, b"srv")
            elif message.kind == "text" and message.data == "bad-frame":
                ws.send_raw(wf.encode_frame(wf.OP_TEXT, b"masked", mask=True))
            elif message.kind == "text":
                ws.send_text(message.data)
            elif message.kind == "binary":
                data, third = message.data, max(1, len(message.data) // 3)
                ws.send(wf.OP_BINARY, data[:third], fin=False)
                ws.send(wf.OP_PING, b"mid")
                ws.send(wf.OP_CONT, data[third:2 * third], fin=False)
                ws.send(wf.OP_CONT, data[2 * third:], fin=True)


class BlockingTransport(unittest.TestCase):
    def setUp(self):
        self.app = EchoApp()
        self.server = StubServer(self.app)
        self.url = self.server.__enter__().replace("http://", "ws://") + "/echo"
        self.addCleanup(self.server.__exit__, None, None, None)

    def test_text_and_fragmented_binary_round_trip(self):
        ws = _ws.WebSocket.connect(self.url, timeout=5)
        ws.send_text("héllo")
        self.assertEqual(ws.recv(5), wf.Message("text", "héllo"))
        payload = bytes(range(256)) * 50
        ws.send_binary(payload)
        self.assertEqual(ws.recv(5), wf.Message("binary", payload))  # ping mid-message answered
        ws.close()
        self.assertEqual(self.app.pongs, [b"mid"])

    def test_server_ping_is_answered(self):
        ws = _ws.WebSocket.connect(self.url, timeout=5)
        ws.send_text("ping-me")
        self.assertEqual(ws.recv(5).data, "pong:srv")
        ws.close()

    def test_server_close_code_and_reason_are_reported(self):
        ws = _ws.WebSocket.connect(self.url, timeout=5)
        ws.send_text("close:4001:ticket invalid")
        message = ws.recv(5)
        self.assertEqual((message.kind, message.close_code, message.close_reason),
                         ("close", 4001, "ticket invalid"))
        self.assertEqual((ws.close_code, ws.close_reason), (4001, "ticket invalid"))
        with self.assertRaises(_ws.ConnectionClosed):
            ws.recv(1)

    def test_masked_server_frame_is_a_protocol_error(self):
        ws = _ws.WebSocket.connect(self.url, timeout=5)
        ws.send_text("bad-frame")
        with self.assertRaises(wf.ProtocolError):
            ws.recv(5)
        self.assertEqual(ws.close_code, wf.CLOSE_PROTOCOL_ERROR)

    def test_recv_timeout(self):
        ws = _ws.WebSocket.connect(self.url, timeout=5)
        with self.assertRaises(TimeoutError):
            ws.recv(0.2)
        ws.close()

    def test_refused_upgrade_raises_handshake_error_with_status(self):
        with self.assertRaises(wf.HandshakeError) as ctx:
            _ws.WebSocket.connect(self.url.replace("/echo", "/nowhere"), timeout=5)
        self.assertEqual(ctx.exception.status, 404)


class AsyncTransport(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = EchoApp()
        self.server = StubServer(self.app)
        self.url = self.server.__enter__().replace("http://", "ws://") + "/echo"
        self.addCleanup(self.server.__exit__, None, None, None)

    async def test_text_and_fragmented_binary_round_trip(self):
        ws = await _aws.AsyncWebSocket.connect(self.url, timeout=5)
        await ws.send_text("héllo")
        self.assertEqual(await ws.recv(5), wf.Message("text", "héllo"))
        payload = bytes(range(256)) * 50
        await ws.send_binary(payload)
        self.assertEqual(await ws.recv(5), wf.Message("binary", payload))
        await ws.close()
        self.assertEqual(self.app.pongs, [b"mid"])

    async def test_server_close_code_and_reason_are_reported(self):
        ws = await _aws.AsyncWebSocket.connect(self.url, timeout=5)
        await ws.send_text("close:4010:session closed")
        message = await ws.recv(5)
        self.assertEqual((message.close_code, message.close_reason), (4010, "session closed"))
        with self.assertRaises(_aws.ConnectionClosed):
            await ws.recv(1)

    async def test_recv_does_not_block_the_loop(self):
        # The property the asyncio transport exists for: while recv() waits, other tasks run.
        ws = await _aws.AsyncWebSocket.connect(self.url, timeout=5)
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.02)
                ticks += 1

        task = asyncio.create_task(ticker())
        with self.assertRaises(TimeoutError):
            await ws.recv(0.3)
        await task
        self.assertEqual(ticks, 5)
        await ws.close()

    async def test_masked_server_frame_is_a_protocol_error(self):
        ws = await _aws.AsyncWebSocket.connect(self.url, timeout=5)
        await ws.send_text("bad-frame")
        with self.assertRaises(wf.ProtocolError):
            await ws.recv(5)


class HandshakeHeadLimit(unittest.TestCase):
    """The 64 KiB limit is on the upgrade response head alone. Bytes after the blank line belong to
    the first frame, and a server may send them in the same segment, so they never count."""

    FRAME = "x" * 8000
    LIMIT = 65536  # the published head limit (wf.MAX_HANDSHAKE_HEAD), written out

    def serve(self, head_len: int) -> str:
        server = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(server.close)

        def run() -> None:
            conn, _ = server.accept()
            with conn:
                request = b""
                while b"\r\n\r\n" not in request:
                    request += conn.recv(4096)
                key = re.search(rb"Sec-WebSocket-Key: (\S+)", request).group(1).decode()
                head = ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Accept: {wf.accept_for(key)}\r\nX-Pad: ").encode()
                head += b"a" * (head_len - len(head))
                frame = wf.encode_frame(wf.OP_TEXT, self.FRAME.encode(), mask=False)
                conn.sendall(head + b"\r\n\r\n" + frame)  # head and first frame in one write
                try:
                    conn.recv(1024)
                except OSError:
                    pass

        threading.Thread(target=run, daemon=True).start()
        return f"ws://127.0.0.1:{server.getsockname()[1]}/"

    def test_a_head_at_the_limit_with_a_frame_behind_it_is_accepted(self):
        ws = _ws.WebSocket.connect(self.serve(self.LIMIT), timeout=5)
        self.assertEqual(ws.recv(5).data, self.FRAME)
        ws.close(timeout=0.5)

        async def scenario() -> str:
            aws = await _aws.AsyncWebSocket.connect(self.serve(self.LIMIT), timeout=5)
            message = await aws.recv(5)
            await aws.close(timeout=0.5)
            return message.data
        self.assertEqual(asyncio.run(scenario()), self.FRAME)

    def test_a_head_past_the_limit_is_refused(self):
        with self.assertRaises(wf.HandshakeError):
            _ws.WebSocket.connect(self.serve(self.LIMIT + 1), timeout=5)


if __name__ == "__main__":
    unittest.main()
