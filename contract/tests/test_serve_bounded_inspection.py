"""The fixture server's non-waiting inspection of a socket (`ServerWS._fill_pending`, behind `buffered_text` and
`peer_gone`) is bounded: it reads a fixed number of bytes per call and stops while a fixed payload is held unread, and the
rest stays for the ordinary `recv`, in order.
"""

import socket
import types
import unittest

from tsbc import _serve
from tsbc import _wsframe as wf


class Peer:
    """A read side holding bytes the peer already sent: `read1` returns what is left, up to `n`, and would block when
    nothing is. It counts what each call of the inspection took."""

    def __init__(self, data: bytes) -> None:
        self.data = bytearray(data)
        self.taken: list[int] = []

    def read1(self, n: int) -> bytes:
        if not self.data:
            raise BlockingIOError
        chunk = bytes(self.data[:n])
        del self.data[:n]
        self.taken.append(len(chunk))
        return chunk


class BoundedInspection(unittest.TestCase):
    FRAMES = 40
    SIZE = 65000

    def setUp(self):
        self.socks = socket.socketpair()
        self.addCleanup(lambda: [s.close() for s in self.socks])
        texts = [f"{i:04d}".encode() + b"x" * (self.SIZE - 4) for i in range(self.FRAMES)]
        self.texts = [t.decode() for t in texts]
        data = b"".join(wf.encode_frame(wf.OP_TEXT, t) for t in texts)
        self.assertGreater(len(data), 2 * _serve.INSPECT_READ_BUDGET)
        self.peer = Peer(data)
        self.ws = _serve.ServerWS(self.peer, types.SimpleNamespace(_sock=self.socks[0]))

    def held(self) -> int:
        return sum(len(f.payload) for f in self.ws.pending)

    def test_a_call_reads_no_more_than_the_budget_and_holds_no_more_than_the_cap(self):
        self.assertFalse(self.ws._fill_pending())
        self.assertLessEqual(sum(self.peer.taken), _serve.INSPECT_READ_BUDGET)
        self.assertLessEqual(self.held(), _serve.INSPECT_HELD_CAP)
        self.assertGreater(self.peer.data, b"")                  # the rest is still in the socket

    def test_nothing_is_read_while_the_cap_is_held(self):
        heldat = []
        read1 = self.peer.read1
        self.peer.read1 = lambda n: (heldat.append(self.held()), read1(n))[1]
        for _ in range(5):
            self.ws._fill_pending()
        self.assertTrue(heldat)
        self.assertTrue(all(h < _serve.INSPECT_HELD_CAP for h in heldat))   # every read began under the cap
        self.assertGreaterEqual(self.held(), _serve.INSPECT_HELD_CAP)      # and the cap was reached, so reading stopped
        taken = sum(self.peer.taken)
        self.assertFalse(self.ws._fill_pending())
        self.assertEqual(sum(self.peer.taken), taken)

    def test_every_frame_is_still_returned_in_order(self):
        for _ in range(5):
            self.ws.buffered_text()
            self.assertLessEqual(self.held(), _serve.INSPECT_HELD_CAP + self.SIZE)   # at most the frame that crossed it
        got = []
        while len(got) < self.FRAMES:
            message = self.ws.recv()
            self.assertEqual(message.kind, "text")
            got.append(message.data)
        self.assertEqual(got, self.texts)


if __name__ == "__main__":
    unittest.main()
