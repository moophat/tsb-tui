"""The non-interactive smoke path's own exit codes.

Why these matter (Rule 9): this is the post-deploy evidence path — a broken deploy that still
prints "close: ok" and exits 0 leaves a leaked session sitting for up to 7 days (session model
§11's `idle` timer) with nobody the wiser, and a `--broker http://` typo against a non-loopback
host would otherwise send the superadmin password across the network in clear. And a round trip
that only ever checked "some bytes came back" would pass against a target that only ever prints a
banner and never actually echoes what it was sent — never proving the target is alive at all.
"""

from __future__ import annotations

import io
import socket
import sys

import pytest
from stub_server import SlowChunkedApp, StubServer
from tsb_tui import smoke

TARGET_ID = "stub-target"


def _set_creds(monkeypatch, tmp_path, password: str = "fixture-password") -> None:
    password_file = tmp_path / "password"
    password_file.write_text(password, encoding="utf-8")
    password_file.chmod(0o600)
    monkeypatch.setenv("TSB_SUPERADMIN_USER", "fixture-user")
    monkeypatch.setenv("TSB_SUPERADMIN_PASSWORD_FILE", str(password_file))


def test_smoke_run_succeeds_end_to_end(monkeypatch, tmp_path) -> None:
    _set_creds(monkeypatch, tmp_path)
    stub = SlowChunkedApp(max_chunks=0, echo_client_frames=True)
    with StubServer(stub) as url:
        assert smoke.run(url, TARGET_ID, None) == 0


def test_smoke_run_fails_and_reports_a_close_failure(monkeypatch, tmp_path, capsys) -> None:
    """A close that itself fails must not be reported as `close: ok` — the whole point of a
    close-on-every-path smoke test is to catch exactly a broken close."""
    _set_creds(monkeypatch, tmp_path)
    stub = SlowChunkedApp(max_chunks=0, echo_client_frames=True)
    with StubServer(stub) as url:
        # Force the DELETE the smoke path issues to fail: the stub's own session store never
        # holds the id `smoke.run` will use once we delete it out from under it first.
        original_http = stub.http

        def failing_delete(method, path, headers, body):
            if method == "DELETE":
                return 500, [], {"error": {"code": "internal", "message": "stub close failure"}}
            return original_http(method, path, headers, body)

        stub.http = failing_delete
        assert smoke.run(url, TARGET_ID, None) == 1
        assert "close: FAILED" in capsys.readouterr().err


def test_smoke_refuses_a_cleartext_broker_url_off_loopback(monkeypatch, tmp_path, capsys) -> None:
    _set_creds(monkeypatch, tmp_path)
    assert smoke.run("http://example.invalid", TARGET_ID, None) == 1
    assert "FAIL broker URL" in capsys.readouterr().err


def test_smoke_missing_credentials_fails_loud(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TSB_SUPERADMIN_USER", raising=False)
    monkeypatch.delenv("TSB_SUPERADMIN_PASSWORD_FILE", raising=False)
    assert smoke.run("http://127.0.0.1:1", TARGET_ID, None) == 1
    assert "TSB_SUPERADMIN_USER" in capsys.readouterr().err


def test_smoke_reads_the_password_from_stdin_when_the_password_file_is_dash(monkeypatch) -> None:
    """`TSB_SUPERADMIN_PASSWORD_FILE=-` mirrors the core's own `tsb seed-superadmin
    --password-file -`: the deploy hook pipes the root-only password file into a smoke run as the
    unprivileged operator this way, so the operator's own installed code never runs as root."""
    monkeypatch.setenv("TSB_SUPERADMIN_USER", "fixture-user")
    monkeypatch.setenv("TSB_SUPERADMIN_PASSWORD_FILE", "-")
    monkeypatch.setattr(sys, "stdin", io.StringIO("fixture-password\n"))
    stub = SlowChunkedApp(max_chunks=0, echo_client_frames=True)
    with StubServer(stub) as url:
        assert smoke.run(url, TARGET_ID, None) == 0


def test_smoke_fails_when_the_password_from_stdin_is_empty(monkeypatch, capsys) -> None:
    # Mirrors the existing file-branch behavior (a missing or empty file also raises SystemExit
    # here, unchanged by this fix) rather than a plain return code.
    monkeypatch.setenv("TSB_SUPERADMIN_USER", "fixture-user")
    monkeypatch.setenv("TSB_SUPERADMIN_PASSWORD_FILE", "-")
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    with pytest.raises(SystemExit) as exc_info:
        smoke.run("http://127.0.0.1:1", TARGET_ID, None)
    assert exc_info.value.code == 1
    assert "empty password from stdin" in capsys.readouterr().err


def test_smoke_fails_when_the_target_never_echoes_the_probe(monkeypatch, tmp_path, capsys) -> None:
    """A target that emits output — even continuously — but never echoes back what the smoke
    sent must not pass: `output: ok` has to mean the round trip proved the target is alive and
    listening, not merely that *some* bytes arrived (a banner, a stray prompt)."""
    _set_creds(monkeypatch, tmp_path)
    stub = SlowChunkedApp(max_chunks=1, tail_chunks=0, echo_client_frames=False)
    with StubServer(stub) as url:
        assert smoke.run(url, TARGET_ID, None, recv_timeout=0.5) == 1
        assert "FAIL output" in capsys.readouterr().err


def test_smoke_fails_on_a_protocol_error_after_attach(monkeypatch, tmp_path, capsys) -> None:
    """A broken frame arriving once already attached (RFC 6455: the receiver must fail the
    connection) must end the smoke with its own `FAIL output` line, not an unhandled traceback —
    the same protocol failure the terminal screen's own receive loop must survive."""
    _set_creds(monkeypatch, tmp_path)
    stub = SlowChunkedApp(invalid_frame_after_attach=True)
    with StubServer(stub) as url:
        assert smoke.run(url, TARGET_ID, None, recv_timeout=1.0) == 1
        assert "FAIL output" in capsys.readouterr().err


def test_smoke_fails_on_a_refused_websocket_upgrade(monkeypatch, tmp_path, capsys) -> None:
    """The attach step must handle a `HandshakeError` (the WebSocket upgrade itself refused) the
    same way it handles every other attach failure: its own `FAILED` line, the session it opened
    closed anyway, exit 1 — never an unhandled traceback and never a leaked session."""
    _set_creds(monkeypatch, tmp_path)
    stub = SlowChunkedApp(refuse_upgrade=True)
    with StubServer(stub) as url:
        assert smoke.run(url, TARGET_ID, None) == 1
        assert "FAIL attach" in capsys.readouterr().err
        # The failure path still closes the session it opened: the stub's own store shows the
        # one session `smoke.run` created now `killed` by the DELETE. A run that reported the
        # attach failure and skipped cleanup leaves it `detached`, and fails here.
        assert [session["state"] for session in stub.sessions.values()] == ["killed"]


def test_smoke_fails_loud_on_a_transport_failure(monkeypatch, tmp_path, capsys) -> None:
    """A closed port makes the very first HTTP call (login) raise `HttpError`, not `OSError` —
    `_http.HttpClient.request` wraps every transport failure (a refused connection, a timeout, a
    TLS failure) into its own exception type. Proves the smoke path catches that too, not only the
    failures already covered by `except OSError`."""
    _set_creds(monkeypatch, tmp_path)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        closed_port = probe_socket.getsockname()[1]
    # The socket above is closed (the `with` block exited) so nothing listens on this port now.
    assert smoke.run(f"http://127.0.0.1:{closed_port}", TARGET_ID, None) == 1
    assert "FAIL login" in capsys.readouterr().err
