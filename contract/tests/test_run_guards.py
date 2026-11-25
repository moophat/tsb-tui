"""Guards every run applies before it grades anything.

Cleartext only to loopback: the runner and the client send a login password and hold its cookie,
so plain http:// or ws:// to any other host would put both on the network in the clear.
Schemas checked on every run, not only in self-check: a live run against a tree whose schema uses
a keyword the validator does not implement would grade against a constraint nobody enforces.
"""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import tsb_contract_client as tcc
from tsbc import _http, verify
from tsbc import _wsframe as wf

from . import CONTRACT


class CleartextOnlyToLoopback(unittest.TestCase):
    def test_http_to_a_network_host_is_refused(self):
        for url in ("http://tsb.example", "http://192.0.2.10:8080"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _http.HttpClient(url)
        with self.assertRaises(ValueError):
            tcc.Client("http://tsb.example")

    def test_ws_to_a_network_host_is_refused(self):
        with self.assertRaises(ValueError):
            wf.parse_ws_url("ws://tsb.example/ws/attach")

    def test_tls_anywhere_and_cleartext_on_loopback_are_allowed(self):
        _http.HttpClient("https://tsb.example")
        _http.HttpClient("http://127.0.0.1:8080")
        _http.HttpClient("http://localhost")
        self.assertEqual(wf.parse_ws_url("wss://tsb.example/ws/attach")[0], True)
        self.assertEqual(wf.parse_ws_url("ws://127.0.0.1:9/ws/attach")[0], False)

    def test_the_runner_refuses_before_sending_anything(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status = verify.main(["verify", "http://tsb.example", "--seams", "C1"], io.StringIO())
        self.assertEqual(status, 2)
        self.assertIn("allowed only to a loopback host", err.getvalue())


class SchemasCheckedOnEveryRun(unittest.TestCase):
    def test_artifacts_run_refuses_a_schema_outside_the_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "contract"
            tree.mkdir()
            for name in ("CONTRACT_VERSION", "schemas", "openapi", "fixtures"):
                src = CONTRACT / name
                (shutil.copytree if src.is_dir() else shutil.copy)(src, tree / name)
            path = tree / "schemas" / "audit-event.schema.json"
            doc = json.loads(path.read_text())
            doc["$defs"]["ts"]["format"] = "date-time"
            path.write_text(json.dumps(doc))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                status = verify.main(["verify", "--artifacts", tmp, "--contract-dir", str(tree)], io.StringIO())
        self.assertEqual(status, 2)
        self.assertIn("keyword 'format' is outside the tsbc validator subset", err.getvalue())


if __name__ == "__main__":
    unittest.main()
