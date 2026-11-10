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
from pathlib import Path

import pytest
from stub_server import SlowChunkedApp, StubServer

from tsb_tui import smoke
from tsb_tui.__main__ import main

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


def test_a_create_slower_than_ten_seconds_succeeds_with_the_default_timeout(monkeypatch, tmp_path, capsys) -> None:
    """A create can legitimately take far longer than the contract client's 10 s default
    (connect 30 s + a target's open bound + one heartbeat interval = 55 s with the deploy templates'
    defaults), so the TUI's own `broker.timeout_s` knob, 70 s by default, must bound its REST calls.
    Against a stub whose `POST /sessions` sleeps 10.5 s, `smoke.run` with the packaged defaults gets
    past "create session" and passes. With the client's 10 s default it answers
    `FAIL create session` with a timeout, so this is the one test that proves the 10 s default is
    gone. It sleeps; it never spins."""
    _set_creds(monkeypatch, tmp_path)
    stub = SlowChunkedApp(max_chunks=0, echo_client_frames=True, create_delay_s=10.5)
    with StubServer(stub) as url:
        assert smoke.run(url, TARGET_ID, None) == 0
    captured = capsys.readouterr()
    assert "create session: ok" in captured.out
    assert "FAIL create session" not in captured.err


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


# -- the engine step (DT2, DT11) --

def test_smoke_loads_the_engine_first_and_prints_its_build_info(monkeypatch, tmp_path, capsys) -> None:
    """DT11: from phase 3 the deploy hook's `--smoke` proves the node has a working terminal engine,
    and the rest of the run is exactly what it was. The engine line comes first: a node without its
    engine must fail before anything is created on the broker."""
    _set_creds(monkeypatch, tmp_path)
    stub = SlowChunkedApp(max_chunks=0, echo_client_frames=True)
    with StubServer(stub) as url:
        assert smoke.run(url, TARGET_ID, None) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("engine: ok libghostty-vt ")
    assert lines[1:] == [
        "login: ok", "create session: ok", "recording warning: acknowledged (smoke mode)",
        "attach: ok", "output: ok", "close: ok"]


@pytest.mark.parametrize("kind", ["missing", "not-a-library"])
def test_smoke_fails_naming_the_knob_and_the_path_when_the_engine_cannot_load(
        kind, monkeypatch, tmp_path, capsys) -> None:
    """DT2: a node whose engine is absent or unloadable fails the deploy check with a line that says
    which knob to set and which path was tried. Nothing is attempted against the broker (the URL
    below is a closed port: a login attempt would say `FAIL login`)."""
    _set_creds(monkeypatch, tmp_path)
    path = tmp_path / "libghostty-vt.so"
    if kind == "not-a-library":
        path.write_text("this is not a shared object")
    monkeypatch.setenv("TSB_TUI_TERM_LIBRARY", str(path))
    assert smoke.run("http://127.0.0.1:1", TARGET_ID, None) == 1
    captured = capsys.readouterr()
    assert "term.library" in captured.err and str(path) in captured.err
    assert "FAIL login" not in captured.err and "login: ok" not in captured.out


# -- the command line --

def test_main_smoke_runs_with_the_default_profiles_auto_size(monkeypatch, tmp_path) -> None:
    """The deploy hook runs `tsb-tui --smoke` with the same flags as before. It sends no size request,
    so the default profile's `size.auto_request = true` (OPEN-1 was ruled on 2026-10-12) is harmless
    to it."""
    _set_creds(monkeypatch, tmp_path)
    monkeypatch.delenv("TSB_TUI_SIZE_AUTO_REQUEST")
    stub = SlowChunkedApp(max_chunks=0, echo_client_frames=True)
    with StubServer(stub) as url:
        assert main(["--smoke", "--broker", url, "--target", TARGET_ID]) == 0


def test_main_interactive_config_loads_with_the_default_auto_size(monkeypatch) -> None:
    """OPEN-1 was ruled on 2026-10-12: auto size is on by default. An interactive start resolves its
    configuration with no override, no profile, no file and no environment value — the refusal that
    demanded one is gone, so the deploy hook and an operator's first run both get past it."""
    from tsb_tui.__main__ import _load_config, _parse_args

    monkeypatch.delenv("TSB_TUI_SIZE_AUTO_REQUEST", raising=False)
    cfg = _load_config(_parse_args(["--broker", "https://host"]), for_smoke=False)
    assert cfg["size.auto_request"] is True
    assert cfg["input.guard"] is True


def test_main_interactive_start_fails_naming_the_knob_when_the_engine_cannot_load(
        tmp_path, capsys) -> None:
    missing = tmp_path / "absent.so"
    assert main(["--broker", "http://127.0.0.1:1", "--set", f"term.library={missing}"]) == 1
    err = capsys.readouterr().err
    assert "term.library" in err and str(missing) in err


def test_main_takes_a_profile_a_config_file_and_set_in_that_order_of_precedence(tmp_path) -> None:
    """Design D30: defaults < profile < file < environment < `--set`. The command line is the last
    word, so `--set` beats the file, and a profile is what `--profile` names."""
    from tsb_tui.__main__ import _load_config, _parse_args

    config_file = tmp_path / "config.toml"
    config_file.write_text("[view]\nfollow_cursor = 'off'\n[render]\nmax_fps = 12\n", encoding="utf-8")
    args = _parse_args(["--broker", "x", "--profile", "phone", "--config", str(config_file),
                        "--set", "render.max_fps=20", "--set", "size.auto_request=true"])
    cfg = _load_config(args, for_smoke=False)
    assert cfg["view.chrome"] == "compact"        # from the phone profile
    assert cfg["view.follow_cursor"] == "off"     # the file beats the profile's "on"
    assert cfg["render.max_fps"] == 20            # --set beats the file
    assert cfg["size.auto_request"] is True       # --set beats the phone profile's false


def test_smoke_password_file_mode_check_skipped_on_windows(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """P4: on Windows, smoke password file mode check is skipped and prints notice."""
    monkeypatch.setattr(sys, "platform", "win32")
    pw = tmp_path / "superadmin_password"
    pw.write_text("admin-pass\n")
    pw.chmod(0o666)

    val = smoke._password_from_file(str(pw))
    assert val == "admin-pass"
    captured = capsys.readouterr()
    assert "mode check skipped on Windows" in captured.err
    assert "stdin is the safer door" in captured.err
    assert "is group- or other-readable" not in captured.err



def test_smoke_invalid_broker_names_source(monkeypatch, tmp_path, capsys):
    _set_creds(monkeypatch, tmp_path)
    assert smoke.run("http://example.org", TARGET_ID, None,
                     broker_source="config file portable/config.toml") == 1
    assert "broker.url (config file portable/config.toml):" in capsys.readouterr().err
