"""Only schema-shaped device ids are exempt from the collection scan. A failed pong returns
one abnormal close message, then receives raise the closed-stream error.
"""

import io
import json
import socket
import tempfile
import unittest
from pathlib import Path

from tsbc import _ws, selfcheck, verify
from tsbc import _wsframe as wf
from tsbc._run import Run
from tsbc.fixtures import load_fixtures
from tsbc.schema import Registry

from . import CONTRACT
from .test_artifacts import audit_lines, build, echo_end, files, jsonl

# A real-looking device_id: 43 unpadded base64url characters, high entropy, and the last character
# in the $defs/device_id pattern's final set. Assembled so this file holds no long run at top level.
DEV = "k7GqXz9Qm2VbNpLr" + "TfHjWdYsUcEaOiKl" + "BxMnZvQtR8w"
PEM_HEADER = "-----BEGIN PRIVATE KEY-----"


def device_register(ts: str = "2026-09-23T12:00:00Z") -> dict:
    return {"ts": ts, "user": "u", "action": "device.register", "device_id": DEV, "actor": "u"}


class DeviceIdScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def grade(self, name: str) -> verify.Result:
        fixtures, _ = load_fixtures(CONTRACT)
        [fx] = [f for f in fixtures if f.name == name]
        run = Run(CONTRACT, Registry.from_tree(CONTRACT), "e0", None, self.dir, None, 1.0)
        return verify._grade(fx, run)

    def test_a_real_looking_device_id_in_the_audit_passes_the_collection_scan(self):
        content = files()
        content["audit.jsonl"] = jsonl(audit_lines() + echo_end() + [device_register()])
        build(self.dir, content)
        out = io.StringIO()
        verify.main(["verify", "--artifacts", str(self.dir), "--seams", "C6", "--profile", "e0"], out)
        text = out.getvalue()
        self.assertEqual([line for line in text.splitlines() if line.startswith("FAIL")], [], text)
        self.assertNotIn("credential material", text)

    def test_the_same_value_under_a_credential_key_on_the_same_line_still_fails(self):
        # The exemption is the field, not the string: `"device_id"` and `"secret"` carry the same
        # value on one line, and the line must fail for the second.
        content = files()
        line = {"ts": "2026-09-23T12:00:00Z", "user": "u", "action": "auth.login",
                "detail": {"device_id": DEV, "secret": DEV}}
        content["audit.jsonl"] = jsonl(audit_lines() + echo_end() + [line])
        build(self.dir, content)
        result = self.grade("event-vocabulary")
        self.assertEqual(result.outcome, verify.FAIL, result.detail)
        self.assertIn("credential material", result.detail)
        self.assertNotIn(DEV, result.detail)

    def assert_device_id_credential_refused(self, value: str, detector: str):
        for suffix in ("json", "jsonl"):
            with self.subTest(suffix=suffix):
                build(self.dir, files())
                path = self.dir / f"device.{suffix}"
                path.write_text(json.dumps({"device_id": value}), encoding="utf-8")
                result = self.grade("event-vocabulary")
                self.assertEqual(result.outcome, verify.FAIL, result.detail)
                self.assertIn("credential material", result.detail)
                self.assertIn(detector, result.detail)
                path.unlink()

    def test_a_pem_header_under_device_id_is_refused(self):
        self.assert_device_id_credential_refused(PEM_HEADER, "PEM header")

    def test_an_openssh_marker_under_device_id_is_refused(self):
        self.assert_device_id_credential_refused("openssh-key-v1", "OpenSSH private-key marker")

    def test_non_device_id_shapes_stay_visible_in_both_scan_paths(self):
        for value in (PEM_HEADER, "openssh-key-v1", DEV[:-1], DEV + "A",
                      DEV[:-1] + "B"):
            doc = {"nested": [{"device_id": value}]}
            raw = json.dumps(doc)
            with self.subTest(value=value, path="raw"):
                self.assertEqual(selfcheck._strip_raw_device_ids(raw), raw)
            with self.subTest(value=value, path="parsed"):
                self.assertEqual(selfcheck._doc_without_device_ids(doc), doc)


class PingToADeadPeer(unittest.TestCase):
    def test_a_failed_pong_returns_one_abnormal_close_then_raises(self):
        # The peer sends one ping and closes. The pong write fails on the dead socket and `_drop()`
        # closes ours; this receive returns close 1006, and later calls raise ConnectionClosed.
        # A unix socketpair answers deterministically: a write to
        # a closed peer raises, where a TCP write may sit in the send buffer.
        client, peer = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(peer.close)
        ws = _ws.WebSocket(client)
        peer.sendall(wf.encode_frame(wf.OP_PING, b"bye", mask=False))
        peer.close()
        message = ws.recv(2)
        self.assertEqual(message, wf.Message("close", b"", 1006, ""))
        self.assertEqual(ws.close_code, 1006)
        self.assertTrue(ws.closed)
        for _ in range(2):
            with self.assertRaises(_ws.ConnectionClosed):
                ws.recv(2)


if __name__ == "__main__":
    unittest.main()
