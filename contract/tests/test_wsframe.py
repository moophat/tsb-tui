"""The RFC 6455 codec against the RFC's own worked examples (§1.3, §5.7).

Both transports and the client sit on this codec, and the test server does too, so an echo test
alone could pass with a codec that is wrong in the same way at both ends. These known answers
anchor it to the RFC instead.
"""

import unittest

from tsbc import _wsframe as wf

HELLO = b"Hello"
RFC_MASK = bytes([0x37, 0xFA, 0x21, 0x3D])


def parse_all(data: bytes, *, masked: bool) -> list:
    return wf.FrameParser(expect_masked=masked).feed(data)


class KnownAnswers(unittest.TestCase):
    def test_accept_value_from_rfc_section_1_3(self):
        self.assertEqual(wf.accept_for("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_unmasked_text(self):
        encoded = bytes([0x81, 0x05]) + HELLO
        self.assertEqual(wf.encode_frame(wf.OP_TEXT, HELLO, mask=False), encoded)
        [frame] = parse_all(encoded, masked=False)
        self.assertEqual((frame.fin, frame.opcode, frame.payload), (True, wf.OP_TEXT, HELLO))

    def test_masked_text(self):
        encoded = bytes([0x81, 0x85, 0x37, 0xFA, 0x21, 0x3D, 0x7F, 0x9F, 0x4D, 0x51, 0x58])
        self.assertEqual(wf.encode_frame(wf.OP_TEXT, HELLO, mask=True, mask_key=RFC_MASK), encoded)
        [frame] = parse_all(encoded, masked=True)
        self.assertEqual(frame.payload, HELLO)

    def test_fragmented_text_reassembles(self):
        data = bytes([0x01, 0x03]) + b"Hel" + bytes([0x80, 0x02]) + b"lo"
        assembler = wf.MessageAssembler()
        messages = [assembler.push(f) for f in parse_all(data, masked=False)]
        self.assertEqual(messages[0], None)
        self.assertEqual(messages[1], wf.Message("text", "Hello"))

    def test_ping_and_masked_pong(self):
        ping = bytes([0x89, 0x05]) + HELLO
        pong = bytes([0x8A, 0x85, 0x37, 0xFA, 0x21, 0x3D, 0x7F, 0x9F, 0x4D, 0x51, 0x58])
        self.assertEqual(wf.encode_frame(wf.OP_PING, HELLO, mask=False), ping)
        self.assertEqual(wf.encode_frame(wf.OP_PONG, HELLO, mask=True, mask_key=RFC_MASK), pong)

    def test_16_and_64_bit_lengths(self):
        for size, head in ((256, bytes([0x82, 0x7E, 0x01, 0x00])),
                           (65536, bytes([0x82, 0x7F, 0, 0, 0, 0, 0, 1, 0, 0]))):
            payload = bytes(size)
            encoded = wf.encode_frame(wf.OP_BINARY, payload, mask=False)
            self.assertEqual(encoded[:len(head)], head)
            [frame] = parse_all(encoded, masked=False)
            self.assertEqual(len(frame.payload), size)

    def test_parser_is_incremental(self):
        encoded = wf.encode_frame(wf.OP_BINARY, bytes(range(200)), mask=False)
        parser = wf.FrameParser(expect_masked=False)
        frames = []
        for i in range(len(encoded)):  # one byte at a time
            frames += parser.feed(encoded[i:i + 1])
        self.assertEqual([f.payload for f in frames], [bytes(range(200))])


class Violations(unittest.TestCase):
    """What a client must refuse, because accepting it would hide a broken server."""

    def test_masked_frame_from_server_is_rejected(self):
        with self.assertRaises(wf.ProtocolError):
            parse_all(wf.encode_frame(wf.OP_TEXT, HELLO, mask=True), masked=False)

    def test_rsv_bits_are_rejected(self):
        with self.assertRaises(wf.ProtocolError):
            parse_all(bytes([0xC1, 0x00]), masked=False)

    def test_fragmented_control_frame_is_rejected(self):
        with self.assertRaises(wf.ProtocolError):
            parse_all(bytes([0x09, 0x00]), masked=False)

    def test_continuation_without_start_is_rejected(self):
        with self.assertRaises(wf.ProtocolError):
            wf.MessageAssembler().push(wf.Frame(True, wf.OP_CONT, b"x"))

    def test_invalid_utf8_text_closes_with_1007(self):
        with self.assertRaises(wf.ProtocolError) as ctx:
            wf.MessageAssembler().push(wf.Frame(True, wf.OP_TEXT, b"\xff"))
        self.assertEqual(ctx.exception.code, wf.CLOSE_INVALID_DATA)

    def test_close_codes(self):
        self.assertEqual(wf.decode_close(wf.encode_close(4001, "bad ticket")), (4001, "bad ticket"))
        self.assertEqual(wf.decode_close(b""), (wf.CLOSE_NO_STATUS, ""))
        for reserved in (1005, 1006, 1015, 999, 5000):
            with self.assertRaises(wf.ProtocolError):
                wf.decode_close(reserved.to_bytes(2, "big"))

    def test_handshake_accept_mismatch_is_refused(self):
        head = (b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: wrong")
        with self.assertRaises(wf.HandshakeError):
            wf.parse_handshake_response(head, wf.new_nonce())

    def test_handshake_non_101_carries_status(self):
        with self.assertRaises(wf.HandshakeError) as ctx:
            wf.parse_handshake_response(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0", wf.new_nonce())
        self.assertEqual(ctx.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
