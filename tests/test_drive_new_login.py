"""`drive`'s `new` with a login (login-at-connect phase A5; rulings 2026-10-29 14:05 A11, 15:36 items 5 and 6).

Why it matters (Rule 9): the point of the feature is an agent that opens a session as a given account without the
password ever resting where a person or a process listing could read it. So the password has exactly two doors: the
next line of stdin (read raw, never echoed, never part of the exchange transcript) and a pipe such as `/dev/fd/N`;
it is never in the command (a command is logged wherever the agent's tooling logs), never a file on disk, and never
in anything drive writes. A refusal must leave the stream in step, because a password line left unread would be
taken as the next command. Servers: the contract's in-process fixture server, which applies the contract's login
rules. Every secret here is synthetic.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import sys

from drive_support import Rig
from tsbc import _serve
from tsbc._serve import ServeApp

from tsb_tui.drive import commands, runner

SIZED = ("size.auto_request=true",)
PASSWORD = "FIXTURE-NOT-A-PASSWORD-agent"
USER = "fixture-account"


def _creates(serve_app) -> list[dict]:
    return [body for method, path, body in serve_app.requests if (method, path) == ("POST", "/sessions")]


@contextlib.contextmanager
def _pipe_with(text: str):
    """A path `/dev/fd/N` that reads `text`: a pipe, as `<(...)` hands one over, never a file."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, text.encode())
    os.close(write_fd)
    try:
        yield f"/dev/fd/{read_fd}"
    finally:
        os.close(read_fd)


def test_new_with_login_reads_the_password_from_stdin_never_the_command(serve, tmp_path) -> None:
    """`password_from: "stdin"` takes the next line of stdin, raw (a space and a trailing quote survive, only the line
    end goes), and the create carries it as the login, on a new connection. The command that asked is the only line
    the transcript holds: the password line is never an inbound line, and no answer or event repeats it."""

    async def check() -> None:
        serve_app, url = serve()
        record = io.StringIO()
        password = f"  {PASSWORD} with space\""
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED, record=record) as rig:
            await rig.login()
            pending = rig.send("new", target="normal", login_user=USER, password_from="stdin")
            rig.raw(password)
            opened = await asyncio.wait_for(pending, 20)
            assert opened["ok"], opened
            body = _creates(serve_app)[-1]
            assert body["connection"] == "new"
            assert body["login"] == {"user": USER, "auth": {"kind": "password", "password": password}}
            follow = await rig.cmd("sessions")
            assert follow["ok"], "the stream stayed in step: the password line was not taken as a command"
            everything = json.dumps(rig.lines) + record.getvalue()
        assert PASSWORD not in everything
        assert all(PASSWORD not in json.dumps(line) for line in rig.lines)

    asyncio.run(check())


def test_new_with_login_reads_the_password_from_a_dev_fd_pipe(serve, tmp_path) -> None:
    """`password_file` naming a pipe (`/dev/fd/N`, what a process substitution is) works: only its first line is the
    password, and nothing of it is written."""

    async def check() -> None:
        serve_app, url = serve()
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED) as rig:
            await rig.login()
            with _pipe_with(PASSWORD + "\nsecond line\n") as path:
                opened = await rig.cmd("new", target="normal", login_user=USER, password_file=path)
            assert opened["ok"], opened
            assert _creates(serve_app)[-1]["login"]["auth"] == {"kind": "password", "password": PASSWORD}
            assert PASSWORD not in json.dumps(rig.lines)

    asyncio.run(check())


def test_new_with_login_reads_the_password_from_a_file_as_login_does(serve, tmp_path) -> None:
    """A regular `password_file` is read as `login`'s is (its first line); the create carries it and no line drive
    wrote repeats it."""

    async def check() -> None:
        serve_app, url = serve()
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED) as rig:
            await rig.login()
            opened = await rig.cmd("new", target="normal", login_user=USER, password_file=rig.password_file(PASSWORD))
            assert opened["ok"], opened
            assert _creates(serve_app)[-1]["login"] == {"user": USER, "auth": {"kind": "password", "password": PASSWORD}}
            assert PASSWORD not in json.dumps(rig.lines)

    asyncio.run(check())


def test_password_in_the_command_is_refused(serve, tmp_path) -> None:
    """`password` in the command is refused as `login` refuses it: nothing is sent, and the refusal does not repeat the
    value."""

    async def check() -> None:
        serve_app, url = serve()
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED) as rig:
            await rig.login()
            refused = await rig.cmd("new", target="normal", login_user=USER, password=PASSWORD)
            assert refused["ok"] is False and refused["error"] == "bad_request"
            assert "password_from" in refused["why"] and "password_file" in refused["why"]
            assert PASSWORD not in json.dumps(refused)
            assert _creates(serve_app) == []

    asyncio.run(check())


def test_password_from_stdin_is_refused_in_script_mode(serve, tmp_path) -> None:
    """A script's file would then hold the password, and its stdin carries no commands to take it from: refused, with
    nothing read and nothing sent. The step failing ends the script, as any failed step does."""

    async def check() -> None:
        serve_app, url = serve()
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED, script=True) as rig:
            await rig.login()
            refused = await rig.cmd("new", target="normal", login_user=USER, password_from="stdin")
            assert refused["ok"] is False and refused["error"] == "bad_request" and "script" in refused["why"]
            assert _creates(serve_app) == []

            async def ended() -> None:
                while rig.rc is None:
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(ended(), 10)
            assert rig.rc == 1

    asyncio.run(check())


def test_half_a_login_and_two_sources_are_refused(serve, tmp_path) -> None:
    """A username without a password source, a source without a username, two sources, an unknown source and an
    acknowledgement without a login are each refused and nothing is sent. Where the command asked for the next stdin
    line, drive takes that line first whatever else is wrong, so the stream stays in step and a password is never
    read as a command (and never reaches the transcript)."""

    async def check() -> None:
        serve_app, url = serve()
        record = io.StringIO()
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED, record=record) as rig:
            await rig.login()
            shapes = (({"login_user": USER}, False),
                      ({"password_from": "stdin"}, True),
                      ({"login_user": USER, "password_from": "stdin", "password_file": "/dev/null"}, True),
                      ({"login_user": USER, "password_from": "keyboard"}, False),
                      ({"recording_acknowledged": True}, False),
                      ({"login_user": USER, "password_from": "stdin", "recording_acknowledged": "yes"}, True))
            for fields, takes_a_line in shapes:
                pending = rig.send("new", target="normal", **fields)
                if takes_a_line:
                    rig.raw(PASSWORD)
                refused = await asyncio.wait_for(pending, 20)
                assert refused["ok"] is False and refused["error"] == "bad_request", (fields, refused)
                assert PASSWORD not in json.dumps(refused)
            assert (await rig.cmd("sessions"))["ok"], "the stream stayed in step"
            assert _creates(serve_app) == []
            assert PASSWORD not in record.getvalue()

    asyncio.run(check())


def test_an_empty_stdin_password_is_refused_and_ends_nothing_else(serve, tmp_path) -> None:
    """An empty line is no password: refused, nothing sent."""

    async def check() -> None:
        serve_app, url = serve()
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED) as rig:
            await rig.login()
            pending = rig.send("new", target="normal", login_user=USER, password_from="stdin")
            rig.raw("")
            # drive reads the line itself, so an empty one is seen as empty and not skipped as the loop skips them.
            refused = await asyncio.wait_for(pending, 20)
            assert refused["ok"] is False and refused["error"] == "bad_request" and "empty" in refused["why"]
            assert _creates(serve_app) == []

    asyncio.run(check())


def test_a_recorded_login_is_refused_until_the_agent_acknowledges(serve, tmp_path) -> None:
    """The deployment records logins: the core refuses an unacknowledged one 409, and drive passes that on as the
    answer's error, sending nothing on and never acknowledging for the agent. The agent repeats with `recording_acknowledged: true` and the password again
    (drive keeps none), and that create carries the acknowledgement."""

    async def check() -> None:
        serve_app, url = serve(ServeApp(login_input=True))
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED) as rig:
            await rig.login()
            pending = rig.send("new", target="normal", login_user=USER, password_from="stdin")
            rig.raw(PASSWORD)
            refused = await asyncio.wait_for(pending, 20)
            assert refused["ok"] is False and refused["error"] == "login_recording_not_acknowledged", refused
            assert "recorded" in refused["why"], "the core's own words"
            assert "recording_acknowledged" not in _creates(serve_app)[0]["login"]
            assert not serve_app.connections and not serve_app.sessions, "nothing was opened"
            assert PASSWORD not in json.dumps(refused)

            pending = rig.send("new", target="normal", login_user=USER, password_from="stdin",
                               recording_acknowledged=True)
            rig.raw(PASSWORD)
            opened = await asyncio.wait_for(pending, 20)
            assert opened["ok"], opened
            assert _creates(serve_app)[1]["login"]["recording_acknowledged"] is True
            assert PASSWORD not in json.dumps(rig.lines)

    asyncio.run(check())


def test_the_core_s_refusals_are_passed_on_without_the_password(serve, tmp_path) -> None:
    """A login on a telnet target is the core's 422; drive answers with the core's own code and words, as it does
    for any refused create, and the password is in no word of it."""

    async def check() -> None:
        _serve_app, url = serve()
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED) as rig:
            await rig.login()
            pending = rig.send("new", target="telnet-scenario", login_user=USER, password_from="stdin")
            rig.raw(PASSWORD)
            refused = await asyncio.wait_for(pending, 20)
            assert refused["ok"] is False and refused["error"] == "invalid_request", refused
            assert PASSWORD not in json.dumps(rig.lines)

    asyncio.run(check())


def test_no_output_line_carries_the_password(serve, tmp_path, capfd) -> None:
    """Across a whole exchange: a login by stdin, one by pipe, a file on disk, the 409 and an acknowledged
    retry, every line drive wrote, the transcript and both of the process's output streams are free of the password
    ."""

    async def check() -> None:
        serve_app, url = serve(ServeApp(login_input=True))
        record = io.StringIO()
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED, record=record) as rig:
            await rig.login()
            pending = rig.send("new", target="normal", login_user=USER, password_from="stdin")
            rig.raw(PASSWORD)
            await asyncio.wait_for(pending, 20)  # the 409
            await rig.cmd("new", target="normal", login_user=USER, password_file=rig.password_file(PASSWORD))  # 409
            with _pipe_with(PASSWORD + "\n") as path:
                opened = await rig.cmd("new", target="normal", login_user=USER, password_file=path,
                                       recording_acknowledged=True)
            assert opened["ok"], opened
            assert len(_creates(serve_app)) == 3
            lines = list(rig.lines)
            transcript = record.getvalue()
        assert lines and any("ev" in line for line in lines), "events were written too"
        for line in lines:
            assert PASSWORD not in json.dumps(line), line
        assert PASSWORD not in transcript, "the transcript holds no password: it was never an inbound line"
        stdout_stderr = capfd.readouterr()
        assert PASSWORD not in stdout_stderr.out and PASSWORD not in stdout_stderr.err

    asyncio.run(check())


def test_through_the_real_process_the_stdin_line_is_not_a_command_nor_in_the_record(serve, tmp_path, monkeypatch,
                                                                                    capsys) -> None:
    """The process as an agent runs it: `new` with `password_from: stdin`, the password on the next line of the real
    stdin, `--record` on. The create carries the password; stdout, stderr and the record file hold none of it, and
    the command after it was taken as a command (the password line was not)."""
    serve_app, url = serve()
    login = tmp_path / "tsb-login"
    login.write_text(_serve.PASSWORD + "\n")
    login.chmod(0o600)
    record = tmp_path / "exchange.txt"
    stdin = "\n".join([json.dumps({"op": "new", "target": "normal", "login_user": USER, "password_from": "stdin"}),
                       PASSWORD, json.dumps({"op": "sessions"}), json.dumps({"op": "quit"})]) + "\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    rc = runner.main(["--broker", url, "--user", _serve.USER, "--password-file", str(login), "--record", str(record),
                      "--set", "size.auto_request=true"])
    captured = capsys.readouterr()
    answers = [json.loads(line) for line in captured.out.splitlines() if line.startswith("{")]
    assert rc == 0, captured.err
    assert _creates(serve_app)[-1]["login"]["auth"]["password"] == PASSWORD
    oks = [line for line in answers if "ok" in line]
    assert [line["ok"] for line in oks] == [True, True, True], oks  # new, sessions, quit
    for text in (captured.out, captured.err, record.read_text()):
        assert PASSWORD not in text


def test_a_refused_command_still_takes_its_stdin_line_so_it_is_never_a_command_nor_recorded(serve, tmp_path) -> None:
    """Qodo round 1, thread 1. A `new` that carries a `password` key (or any other refusal) and asks
    `password_from: "stdin"` is refused, but its password line is taken first all the same: otherwise `serve` would
    read the line as the next command, answer "not JSON" and write it, verbatim, to the `--record` file."""

    async def check() -> None:
        serve_app, url = serve()
        record = io.StringIO()
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED, record=record) as rig:
            await rig.login()
            refused = []
            for fields in ({"login_user": USER, "password": "inline", "password_from": "stdin"},
                           {"password": "inline", "password_from": "stdin", "password_file": "/dev/null"}):
                pending = rig.send("new", target="normal", **fields)
                rig.raw(PASSWORD)
                refused.append(await asyncio.wait_for(pending, 20))
            for answer in refused:
                assert answer["ok"] is False and answer["error"] == "bad_request", answer
            assert (await rig.cmd("sessions"))["ok"], "the stream stayed in step"
            assert not any("not JSON" in str(line.get("why", "")) for line in rig.lines), rig.lines
            assert PASSWORD not in record.getvalue(), "the password line reached the --record file"
            assert PASSWORD not in json.dumps(rig.lines)
            assert _creates(serve_app) == []

    asyncio.run(check())


def test_password_file_refuses_a_device_and_names_the_reason_never_the_content(serve, tmp_path) -> None:
    """Qodo round 1, thread 2. Only a regular file or a pipe is a password source: a device is refused before it is
    opened (`/dev/zero` has no first line to end, `/dev/null` is not a password), the refusal says why, and `login`,
    which shares the reader, refuses the same way."""

    async def check() -> None:
        _serve_app, url = serve()
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED) as rig:
            answer = await rig.cmd("login", user=_serve.USER, password_file="/dev/null")
            assert answer["ok"] is False and answer["error"] == "bad_request", answer
            assert "regular file or a pipe" in answer["why"], answer
            await rig.login()
            refused = await rig.cmd("new", target="normal", login_user=USER, password_file="/dev/null")
            assert refused["ok"] is False and refused["error"] == "bad_request", refused
            assert "regular file or a pipe" in refused["why"], refused

    asyncio.run(check())


def test_read_password_file_never_reads_a_device_endlessly(tmp_path) -> None:
    """`/dev/zero` is the case that froze the command: refused at once, with the reason, not read."""
    try:
        commands.read_password_file("/dev/zero")
    except commands.DriveError as exc:
        assert exc.code == "bad_request" and "regular file or a pipe" in exc.why
    else:
        raise AssertionError("a device was read as a password file")


def test_password_file_takes_at_most_1024_bytes_and_says_so_without_the_content(tmp_path) -> None:
    """Qodo round 1, thread 2. The read is bounded at 1025 bytes, so 1024 bytes is the longest password (from a file
    or a pipe, with or without its line end, CRLF too) and one more is refused, naming the limit and never the
    text."""
    secret = "S" * 1025
    longest = "L" * 1024
    for text, expected in ((longest, longest), (longest + "\n", longest), (longest + "\r\n", longest),
                           (longest + "\nrest\n", longest), ("x\n", "x")):
        path = tmp_path / "pw"
        path.write_text(text)
        path.chmod(0o600)
        assert commands.read_password_file(str(path)) == expected
    for text in (secret, secret + "\n", "S" * 4000 + "\nrest"):
        path = tmp_path / "pw-long"
        path.write_text(text)
        path.chmod(0o600)
        try:
            commands.read_password_file(str(path))
        except commands.DriveError as exc:
            assert exc.code == "bad_request" and "1024" in exc.why and "SSS" not in exc.why
        else:
            raise AssertionError("a password longer than 1024 bytes was taken")
    with _pipe_with(secret + "\n") as piped:
        try:
            commands.read_password_file(piped)
        except commands.DriveError as exc:
            assert "1024" in exc.why and "SSS" not in exc.why
        else:
            raise AssertionError("a pipe's password longer than 1024 bytes was taken")
