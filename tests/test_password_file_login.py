"""The attached TUI logs in from a password file: `tsb-tui --user U --password-file PATH`
(plans/reports/design-261128-tui-password-file.md, § 1 D1-D5, § 2 D6-D9, § 4 D10-D11, tests 1 to 13).

Why it matters (Rule 9): the file is the only door by which a client agent hands a password to the attached TUI
(session model §7: "by a file or a descriptor"), so what is graded is that (1) the flags and the file checks are
drive's, refuse what cannot work (`-`, `--smoke`) and fail before Textual owns the terminal; (2) the login is the
ordinary one (the Login screen's own `_on_login_done`, so `locks` arrive) with no key pressed; (3) the password is
never in a widget, on the screen or in a crash dump, and never leaves a trace in stdout, stderr or the logs;
(4) a refused login falls back to the ordinary form and spends one attempt only; (5) sign-out and a 401 never log in
from the file again. How a lock resumes under a file login is NOT built (design § 3, not ruled): the lock screen is
today's under every login, and no file is read after the login.

Servers: `tsbc serve` (`VaultServe` + the held-request and input-recording fixtures), never a real broker. Every
password here is a synthetic constant.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from input_server import InputRecordingApp
from signout_support import HeldRequestApp
from stub_server import record_output
from textual.widgets import Button, Input, Static
from textual.worker import WorkerFailed
from tsbc import _serve
from vault_support import VaultServe
from workspace_support import named_sessions, open_here

from tsb_tui import __main__ as entry
from tsb_tui import app as app_mod
from tsb_tui import config
from tsb_tui.app import TsbTuiApp
from tsb_tui.drive.commands import DriveError, read_password_file
from tsb_tui.file_login import FileLogin
from tsb_tui.screens.lock import LockScreen
from tsb_tui.screens.login import LoginScreen
from tsb_tui.screens.terminal import TerminalScreen

FILE_PASSWORD = "FIXTURE-NOT-A-PASSWORD-file"       # the password the served account holds in these tests
WRONG_PASSWORD = "FIXTURE-NOT-A-PASSWORD-wrong"
SESSION_BYTE = b"SESSION-BYTE-ONE-d41d8cd9\r\n"
BROKER = "http://127.0.0.1:9"                       # never connected to: main() stops before any request
WELCOME = "Logging in as "


class FileLoginServe(VaultServe, HeldRequestApp, InputRecordingApp):
    """The served account's password is `FILE_PASSWORD`. `login_bodies` keeps the raw bodies of `POST /auth/login`
    (the server redacts the password from `requests`)."""

    def __init__(self, *args, file_password: str = FILE_PASSWORD, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.login_bodies: list[dict] = []
        self.users[_serve.USER]["password_hash"] = self._account_hash(file_password)

    def http(self, method, path, headers, body):
        if (method, path) == ("POST", "/auth/login") and isinstance(body, dict):
            self.login_bodies.append(dict(body))
        return super().http(method, path, headers, body)


def _password_file(tmp_path: Path, content: str = FILE_PASSWORD, *, name: str = "pw") -> Path:
    path = tmp_path / name
    path.write_text(content + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _file_login(content: str = FILE_PASSWORD, user: str = _serve.USER) -> FileLogin:
    return FileLogin(user, content)


def _dump(app) -> str:
    return "\n".join(strip.text.rstrip() for strip in app.screen._compositor.render_strips())


def _no_trace(text: str) -> None:
    for secret in (FILE_PASSWORD, WRONG_PASSWORD, _serve.PASSWORD):
        assert secret not in text, "a password value leaked"


def _forbid_app(monkeypatch) -> list:
    built: list = []
    monkeypatch.setattr(app_mod, "TsbTuiApp", lambda *a, **kw: built.append((a, kw)) or SimpleNamespace(
        run=lambda: None, return_code=0))
    return built


def _main_exits(argv: list[str], capsys) -> tuple[int, str]:
    with pytest.raises(SystemExit) as exit_:
        entry.main(argv)
    captured = capsys.readouterr()
    _no_trace(captured.out + captured.err)
    return exit_.value.code, captured.err


# -- flags and start (design § 1) --


@pytest.mark.parametrize("flags", [["--user", "someone"], ["--password-file", "somewhere"]])
def test_user_and_password_file_go_together(flags, capsys, monkeypatch) -> None:
    built = _forbid_app(monkeypatch)
    code, err = _main_exits(["--broker", BROKER, *flags], capsys)
    assert code == 2
    assert "--user and --password-file go together" in err
    assert not built


def test_password_file_dash_is_refused_and_stdin_is_not_read(capsys, monkeypatch) -> None:
    built = _forbid_app(monkeypatch)
    stdin = io.StringIO(f"{WRONG_PASSWORD}\n")
    monkeypatch.setattr(sys, "stdin", stdin)
    code, err = _main_exits(["--broker", BROKER, "--user", "someone", "--password-file", "-"], capsys)
    assert code == 2
    assert "--password-file: - is stdin, which the attached TUI reads its keys from" in err
    assert "/dev/fd/N" in err
    assert stdin.tell() == 0 and stdin.read() == f"{WRONG_PASSWORD}\n", "stdin was read"
    assert not built


def _case_missing(tmp_path: Path) -> str:
    return str(tmp_path / "absent")


def _case_directory(tmp_path: Path) -> str:
    return str(tmp_path)


def _case_device(tmp_path: Path) -> str:
    return "/dev/null"


def _case_empty(tmp_path: Path) -> str:
    path = tmp_path / "empty"
    path.write_bytes(b"")
    path.chmod(0o600)
    return str(path)


def _case_too_long(tmp_path: Path) -> str:
    path = tmp_path / "long"
    path.write_bytes(b"L" * 1025)
    path.chmod(0o600)
    return str(path)


def _case_not_utf8(tmp_path: Path) -> str:
    path = tmp_path / "binary"
    path.write_bytes(b"\xff\xfe-not-text\n")
    path.chmod(0o600)
    return str(path)


@pytest.mark.parametrize("make,words", [
    (_case_missing, "does not name a file"),
    (_case_directory, "does not name a file"),
    (_case_device, "is not a regular file or a pipe"),
    (_case_empty, "is empty"),
    (_case_too_long, "holds a password longer than 1024 bytes"),
    (_case_not_utf8, "is not UTF-8 text"),
], ids=["missing", "directory", "character-device", "empty", "1025-bytes", "not-utf8"])
def test_password_file_errors_are_drives(make, words, tmp_path, capsys, monkeypatch) -> None:
    built = _forbid_app(monkeypatch)
    path = make(tmp_path)
    with pytest.raises(DriveError) as drive:
        read_password_file(path)
    code, err = _main_exits(["--broker", BROKER, "--user", "someone", "--password-file", path], capsys)
    assert code == 2
    assert f"--password-file: {drive.value.why}" in err, "drive's own words, from drive's own reader"
    assert words in err
    assert "L" * 20 not in err and "-not-text" not in err, "what was read is never in the message"
    assert not built, "the app is not built before the file is checked"


def test_a_group_readable_file_warns_as_drive_does(tmp_path, capsys, monkeypatch) -> None:
    built = _forbid_app(monkeypatch)
    path = _password_file(tmp_path)
    path.chmod(0o640)
    assert stat.S_IMODE(path.stat().st_mode) & stat.S_IRWXG
    assert entry.main(["--broker", BROKER, "--user", "someone", "--password-file", str(path)]) == 0
    err = capsys.readouterr().err
    if sys.platform == "win32":
        assert "NOTICE: password file mode check skipped on Windows; stdin is the safer door" in err
    else:
        assert f"WARNING: {path} is group- or other-readable; it should be owner-only" in err
    _no_trace(err)
    assert len(built) == 1, "the login goes on: the app is built with the file login"
    assert repr(built[0][1]["file_login"]) == "<password: withheld>"


def test_password_file_with_smoke_is_refused(tmp_path, capsys, monkeypatch) -> None:
    from tsb_tui import smoke

    monkeypatch.setattr(smoke, "run", lambda *a, **kw: pytest.fail("the smoke path ran"))
    path = _password_file(tmp_path)
    code, err = _main_exits(["--smoke", "--target", "t", "--broker", BROKER,
                             "--user", "someone", "--password-file", str(path)], capsys)
    assert code == 2
    assert ("--user and --password-file are for the interactive TUI; "
            "--smoke takes its password from TSB_SUPERADMIN_PASSWORD_FILE") in err


def test_no_flag_or_variable_takes_a_password() -> None:
    parser = entry.build_parser()
    options = {option for action in parser._actions for option in action.option_strings}
    assert {option for option in options if "pass" in option or "secret" in option} == {"--password-file"}
    taking = [action for action in parser._actions if "--password-file" in action.option_strings]
    assert taking and taking[0].metavar == "PATH", "the one flag names a path"
    for name in ("TSB_TUI_PASSWORD", "TSB_TUI_PASSWORD_FILE"):
        with pytest.raises(config.ConfigError, match="unknown configuration key"):
            config.load(config_file=Path("/nonexistent"), environ={name: "x"})
    knobs = {key for key in config.KNOB_TYPES if "password" in key.lower() or "secret" in key.lower()}
    assert not knobs, f"a knob takes a password: {sorted(knobs)}"


# -- the login (design § 2) --


def test_a_file_login_reaches_the_workspace_with_no_key(serve, wait_until, caplog) -> None:
    async def check() -> None:
        serve_app, url = serve(FileLoginServe(locks={"screen_lock_after": 777}))
        app = TsbTuiApp(url, file_login=_file_login())
        with caplog.at_level(logging.DEBUG):
            async with app.run_test(size=(120, 40)) as pilot:             # the pilot presses no key
                await wait_until(pilot, lambda: isinstance(app.screen, TerminalScreen) and app.screen.is_mounted
                                 and app.screen.sidebar is not None, "the workspace, with no key pressed")
                assert serve_app.login_bodies == [{"username": _serve.USER, "password": FILE_PASSWORD}]
                assert app.username == _serve.USER
                assert app.screen_lock.settings is not None
                assert app.screen_lock.settings["screen_lock_after"] == 777, "locks came from the login answer"
                assert app.screen_lock.settings == serve_app.vault_locks()
        _no_trace(caplog.text)

    asyncio.run(check())


def test_the_file_password_never_enters_a_widget_or_the_screen(serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(FileLoginServe())
        release = serve_app.holds[("POST", "/auth/login")] = threading.Event()
        app = TsbTuiApp(url, file_login=_file_login())
        async with app.run_test(size=(120, 40)) as pilot:
            try:
                await wait_until(pilot, serve_app.arrived.is_set, "the login to reach the server")
                login = app.screen
                assert isinstance(login, LoginScreen)
                username, password = login.query_one("#username", Input), login.query_one("#password", Input)
                assert username.value == _serve.USER and username.disabled
                assert password.value == "" and password.disabled
                assert login.query_one("#login-button", Button).disabled
                assert WELCOME + _serve.USER in str(login.query_one("#login-error", Static).render())
                assert "password from the file" in str(login.query_one("#login-error", Static).render())
                during = _dump(app)
                assert FILE_PASSWORD not in during and "•" not in during and "●" not in during, \
                    "no value and no bullets: a masked field would show the password's length"
                assert repr(app.file_login) == "<password: withheld>"
            finally:
                release.set()
            await wait_until(pilot, lambda: isinstance(app.screen, TerminalScreen) and app.screen.is_mounted,
                             "the workspace")
            assert FILE_PASSWORD not in _dump(app)
            assert not [w for w in app.screen.query(Input) if w.value == FILE_PASSWORD]

    asyncio.run(check())


def test_a_refused_file_login_shows_the_ordinary_form_and_does_not_retry(serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(FileLoginServe())
        app = TsbTuiApp(url, file_login=_file_login(WRONG_PASSWORD))
        async with app.run_test(size=(120, 40)) as pilot:
            login = app.screen
            assert isinstance(login, LoginScreen)
            button = login.query_one("#login-button", Button)
            await wait_until(pilot, lambda: len(serve_app.login_bodies) == 1, "the one login")
            await wait_until(pilot, lambda: not button.disabled, "the form to come back")
            await pilot.pause(0.3)                                 # a retry would have arrived by now
            assert app.screen is login and not isinstance(app.screen, TerminalScreen), "no exit, no workspace"
            error = str(login.query_one("#login-error", Static).render())
            assert error == "The login with the password from the file was refused: unknown user or wrong password"
            username, password = login.query_one("#username", Input), login.query_one("#password", Input)
            assert username.value == _serve.USER and not username.disabled
            assert password.value == "" and not password.disabled
            assert app.focused is password
            assert not button.disabled
            assert len(serve_app.login_bodies) == 1, "the file is not read again and no second login is sent"
            _no_trace(error + _dump(app))
            # A person may type from here: the ordinary form works.
            username.value, password.value = _serve.USER, FILE_PASSWORD
            await pilot.click("#login-button")
            await wait_until(pilot, lambda: isinstance(app.screen, TerminalScreen), "a typed login to work")
            assert len(serve_app.login_bodies) == 2

    asyncio.run(check())


def test_a_crash_during_the_file_login_dumps_no_password(serve, wait_until) -> None:
    async def check() -> None:
        _serve_app, url = serve(FileLoginServe())
        app = TsbTuiApp(url, file_login=_file_login())

        def boom(user, password):                   # a frame that holds the password as a named local
            raise RuntimeError("synthetic failure")

        app.client.login = boom
        reports: list[str] = []

        def reported() -> bool:
            # Textual keeps the report as rendered segments (`Segments(console.render(Traceback(show_locals=True)))`)
            # and clears them once it has printed them at exit, so it is read here, while the app still holds it.
            reports.extend("".join(segment.text for segment in renderable.segments)
                           for renderable in app._exit_renderables)
            return bool(reports)

        with pytest.raises(WorkerFailed) as failed:             # run_test re-raises the crash that ended the app
            async with app.run_test(size=(120, 40)) as pilot:
                await wait_until(pilot, reported, "the crash dump", timeout_s=15)
        _no_trace(str(failed.value) + repr(failed.value.__cause__))
        dump = "\n".join(reports)
        assert "FileLoginCrashed" in dump and "RuntimeError" in dump, "the crash is reported, by type"
        _no_trace(dump)
        _no_trace(repr(app.file_login))
        assert "<password: withheld>" in repr(app.file_login) or app.file_login is None

    asyncio.run(check())


# -- sign-out, a 401, Lock now (design § 4) --


async def _logged_in_from_a_file(app, pilot, wait_until, serve_app, path: Path) -> TerminalScreen:
    await wait_until(pilot, lambda: isinstance(app.screen, TerminalScreen) and app.screen.is_mounted
                     and app.screen.sidebar is not None, "the workspace")
    assert len(serve_app.login_bodies) == 1
    path.unlink()                                       # nothing may read the file again
    return app.screen


async def _assert_ordinary_empty_form(app, pilot, wait_until, serve_app) -> None:
    await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen) and app.screen.is_mounted, "the Login form")
    await pilot.pause(0.4)                              # a file login would have been sent by now
    login = app.screen
    username, password = login.query_one("#username", Input), login.query_one("#password", Input)
    assert username.value == _serve.USER, "the username is filled from --user"
    assert password.value == "" and not password.disabled and not username.disabled, "the ordinary form"
    assert not login.query_one("#login-button", Button).disabled
    assert str(login.query_one("#login-error", Static).render()).strip() == "", "no error: no file was read"
    assert len(serve_app.login_bodies) == 1, "no POST /auth/login after the first"
    _no_trace(_dump(app))


def test_sign_out_after_a_file_login_reads_no_file(serve, wait_until, tmp_path) -> None:
    async def check() -> None:
        serve_app, url = serve(FileLoginServe())
        path = _password_file(tmp_path)
        app = TsbTuiApp(url, file_login=_file_login(path.read_text().strip()))
        async with app.run_test(size=(120, 40)) as pilot:
            await _logged_in_from_a_file(app, pilot, wait_until, serve_app, path)
            await app.sign_out()
            await _assert_ordinary_empty_form(app, pilot, wait_until, serve_app)

    asyncio.run(check())


def test_a_401_after_a_file_login_returns_to_the_ordinary_form(serve, wait_until, tmp_path) -> None:
    async def check() -> None:
        serve_app, url = serve(FileLoginServe())
        path = _password_file(tmp_path)
        app = TsbTuiApp(url, file_login=_file_login(path.read_text().strip()))
        async with app.run_test(size=(120, 40)) as pilot:
            await _logged_in_from_a_file(app, pilot, wait_until, serve_app, path)
            serve_app.logins.clear()                    # the core ended the login (expiry, revocation, restart)
            app.call_api(app.client.me, on_done=lambda _me: None, on_error=app.notify_api_error)
            await _assert_ordinary_empty_form(app, pilot, wait_until, serve_app)

    asyncio.run(check())


def test_lock_now_under_a_file_login_locks_and_calls_the_operation(serve, wait_until) -> None:
    async def check() -> None:
        # `named_sessions` logs in with the fixture's own password, so this account holds it
        serve_app, url = serve(FileLoginServe(file_password=_serve.PASSWORD, locks={"screen_lock_after": 100000}))
        app = TsbTuiApp(url, file_login=_file_login(_serve.PASSWORD))
        async with app.run_test(size=(120, 40)) as pilot:
            await wait_until(pilot, lambda: isinstance(app.screen, TerminalScreen) and app.screen.is_mounted
                             and app.screen.sidebar is not None, "the workspace")
            workspace = app.screen
            sid, = named_sessions(url, serve_app, "rtr-7")
            record_output(serve_app, sid, SESSION_BYTE)
            await open_here(app, pilot, wait_until, sid)
            await wait_until(pilot, lambda: "SESSION-BYTE-ONE" in _dump(app), "the session's output")
            logins = len(serve_app.login_bodies)             # `named_sessions` made its own login on the server
            await workspace.run_action_id("lock_now")
            await wait_until(pilot, lambda: isinstance(app.screen, LockScreen) and app.screen.is_mounted,
                             "the lock screen")
            await wait_until(pilot, lambda: len(serve_app.calls("POST", "/auth/lock")) == 1, "the operation")
            dump = _dump(app)
            assert "SESSION-BYTE-ONE" not in dump and "rtr-7" not in dump, "no session content on the lock screen"
            assert len(serve_app.login_bodies) == logins, "the lock never logs in again"
            _no_trace(dump)

    asyncio.run(check())


@pytest.mark.skipif(sys.platform == "win32", reason="/dev/fd pipes are POSIX; Windows takes a file only")
def test_a_pipe_whose_writer_stays_open_returns_the_first_line() -> None:
    r, w = os.pipe()
    os.write(w, b"FIXTURE-NOT-A-PASSWORD-pipe\n")
    outcome: dict[str, object] = {}

    def read() -> None:
        try:
            outcome["value"] = read_password_file(f"/dev/fd/{r}")
        except BaseException as exc:  # noqa: BLE001
            outcome["error"] = exc

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    try:
        thread.join(5)
        alive = thread.is_alive()
    finally:
        os.close(w)
        try:
            os.close(r)
        except OSError:
            pass
    assert not alive, "the reader waited on the pipe's writer to close"
    assert outcome.get("value") == "FIXTURE-NOT-A-PASSWORD-pipe"
