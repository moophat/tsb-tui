"""Headless locks hide content until the core re-checks the login's password source."""
from __future__ import annotations

import asyncio
import functools
import inspect
import io
import json
import os
import ssl
import threading
import time
from argparse import Namespace
from pathlib import Path

from drive_support import Rig
from input_server import make_session
from stub_server import emit_live_output
from test_screen_lock import RecordingVaultServe
from tsbc import _serve

from tsb_tui import config
from tsb_tui._transport import HttpError
from tsb_tui.drive.runner import _run

CANARY = "password-canary-7c6a93ee9a7f4e98b46e6de51901e853"


class LockServe(RecordingVaultServe):
    def __init__(self):
        super().__init__(locks={"screen_lock_after": 10, "activity_interval": 1000})
        self.unlock_status = 204
        self.unlocks = 0
        self.me_gate = None
        self.me_started = threading.Event()
        self.me_status = 200

    def http(self, method, path, headers, body):
        if method == "GET" and path.split("?")[0] == "/auth/me":
            self.me_started.set()
            if self.me_gate is not None:
                assert self.me_gate.wait(10)
            if self.me_status != 200:
                return self._err(self.me_status, "unauthenticated" if self.me_status == 401 else "internal_error", "read failed")
        return super().http(method, path, headers, body)

    def vault_http(self, method, path, headers, body, query, user):
        if (method, path) == ("POST", "/auth/screen-unlock"):
            self.unlocks += 1
            if self.unlock_status != 204:
                code = {403: "screen_unlock_failed", 429: "rate_limited", 401: "unauthenticated"}.get(
                    self.unlock_status, "internal_error")
                return self._err(self.unlock_status, code, "re-check refused")
        return super().vault_http(method, path, headers, body, query, user)


def scenario(test):
    @functools.wraps(test)
    def run(serve, tmp_path, monkeypatch, capsys, caplog):
        async def check():
            monkeypatch.setattr(_serve, "PASSWORD", CANARY)
            server, url = serve(LockServe())
            sid = make_session(url)
            record = io.StringIO()
            async with Rig(url, tmp_path=tmp_path, record=record) as rig:
                path = rig.password_file(CANARY)
                assert (await rig.cmd("login", user=_serve.USER, password_file=path))["ok"]
                await rig.attach(sid)
                clock = [time.monotonic()]
                rig.app.idle_clock = lambda: clock[0]
                await test(rig, server, sid, clock, path, record, monkeypatch)
                captured = capsys.readouterr()
                assert CANARY not in json.dumps(rig.lines) + record.getvalue() + captured.out + captured.err + caplog.text
        asyncio.run(check())
    run.__signature__ = inspect.signature(lambda serve, tmp_path, monkeypatch, capsys, caplog: None)
    return run


def locked(answer):
    assert answer.get("locked") is True and answer.get("why"), answer
    assert not {"screen", "look", "lines", "text", "svg", "tail", "dump"}.intersection(answer), answer


async def expire(rig, clock):
    clock[0] += 11
    await rig.app_call(rig.app.screen_lock._tick)


@scenario
async def test_idle_past_the_screen_lock_time_locks_drive_and_withholds_session_content(rig, server, sid, clock, path, *rest):
    server.unlock_status = 403
    await expire(rig, clock)
    for op, fields in [("read", {"what": "screen"}), ("read", {"what": "history"}),
                       ("read", {"what": "reply"}), ("wait", {"until": {"screen": ".*"}}),
                       ("snapshot", {}), ("snapshot", {"format": "svg"}), ("type", {"text": "x"}),
                       ("keys", {"keys": ["ctrl+c"]}), ("new", {"target": _serve.TARGET}),
                       ("attach", {"session": sid}), ("switch", {"session": sid})]:
        locked(await rig.cmd(op, **fields))


@scenario
async def test_while_locked_typed_sends_press_and_click_are_refused_and_send_nothing(rig, server, sid, clock, *rest):
    server.unlock_status = 429
    client = rig.driver.screen().front.pump.state.client_id
    before = server.received(client)
    await expire(rig, clock)
    for op, fields in [("type", {"text": "x"}), ("type", {"text": "\x03"}),
                       ("keys", {"keys": ["ctrl+c"]}), ("press", {"keys": ["x"]}),
                       ("click", {"label": "Menu"})]:
        answer = await rig.cmd(op, **fields)
        locked(answer)
        assert answer["error"] == "screen_locked" and answer["sent"] is False
    assert server.received(client) == before


@scenario
async def test_commands_with_no_session_content_go_on_while_locked(rig, server, sid, clock, *rest):
    server.unlock_status = 403
    await expire(rig, clock)
    for op, fields in [("sessions", {}), ("targets", {}), ("hand", {"to": "missing"}),
                       ("hand_back", {}), ("take", {}), ("answer", {"choice": "keep"}),
                       ("size", {"cols": 80, "rows": 24}), ("resize", {"size": "120x40"})]:
        answer = await rig.cmd(op, **fields)
        assert answer.get("error") != "screen_locked" and not answer.get("locked"), answer
    assert server.unlocks == 0
    assert (await rig.cmd("detach"))["ok"]
    assert (await rig.cmd("end", session=sid))["error"] != "screen_locked"
    assert (await rig.cmd("logout"))["ok"]
    assert (await rig.cmd("login", user=_serve.USER, password_file=rig.password_file(CANARY)))["ok"]
    assert (await rig.cmd("quit"))["ok"]


@scenario
async def test_the_first_refused_command_re_reads_the_password_file_and_unlocks(rig, server, sid, clock, path, *rest):
    old = (await rig.cmd("read", what="screen"))["look"]
    server.screen_password = CANARY + "-changed"
    Path(path).write_text(server.screen_password)
    await expire(rig, clock)
    assert server.unlocks == 0
    answer = await rig.cmd("type", text="x", look=old)
    assert answer["error"] == "screen_locked" and answer["look"] != old and answer["screen"]
    assert server.unlocks == 1
    read = await rig.cmd("read", what="screen")
    assert read["ok"] and read["look"]
    assert server.unlocks == 1


@scenario
async def test_a_wait_held_across_the_lock_moment_re_checks_before_it_answers(rig, server, sid, clock, *rest):
    for status in (429, 204):
        server.unlock_status = status
        if rig.driver.screen_lock.locked:
            assert (await rig.cmd("read", what="screen"))["ok"]
        future = rig.send("wait", until={"screen": "LOCK-MATCH-" + str(status)}, timeout_ms=2000)
        await asyncio.sleep(.03)
        await expire(rig, clock)
        serial = rig.driver.commands._look_serial
        unlocks = server.unlocks
        emit_live_output(server, server.sessions[sid], ("LOCK-MATCH-" + str(status)).encode())
        answer = await future
        assert server.unlocks == unlocks + 1
        if status == 429:
            locked(answer)
            assert rig.driver.commands._look_serial == serial
        else:
            assert answer.get("ok") and answer.get("look") and answer.get("screen"), answer


@scenario
async def test_a_stdin_or_pipe_login_stays_locked_until_a_new_login(rig, server, sid, clock, path, *rest):
    await rig.cmd("logout")
    fifo = path + "-pipe"
    os.mkfifo(fifo)
    def write():
        with open(fifo, "w") as stream:
            stream.write(CANARY)
    writer = threading.Thread(target=write)
    writer.start()
    assert (await rig.cmd("login", user=_serve.USER, password_file=fifo))["ok"]
    writer.join(2)
    await rig.attach(sid)
    await expire(rig, clock)
    locked(await rig.cmd("read", what="screen"))
    assert server.unlocks == 0
    await rig.cmd("logout")
    assert (await rig.cmd("login", user=_serve.USER, password_file=path))["ok"]
    await rig.attach(sid)
    assert (await rig.cmd("read", what="screen"))["look"]


@scenario
async def test_a_refused_re_check_keeps_drive_locked(rig, server, sid, clock, path, *rest):
    server.unlock_status = 429
    await expire(rig, clock)
    answer = await rig.cmd("read", what="screen")
    locked(answer)
    assert "next" in answer["why"] and "retry_after" not in answer
    assert server.unlocks == 1
    server.unlock_status = 403
    locked(await rig.cmd("read", what="screen"))
    assert server.unlocks == 2
    assert rig.driver.screen_lock.failure
    server.unlock_status = 204
    locked(await rig.cmd("read", what="screen"))
    assert server.unlocks == 2
    await rig.cmd("logout")
    await rig.cmd("login", user=_serve.USER, password_file=path)
    await rig.attach(sid)
    await expire(rig, clock)
    os.unlink(path)
    before = server.unlocks
    locked(await rig.cmd("read", what="screen"))
    assert rig.driver.screen_lock.failure
    Path(path).write_text(CANARY)
    locked(await rig.cmd("read", what="screen"))
    assert server.unlocks == before
    await rig.cmd("logout")
    await rig.cmd("login", user=_serve.USER, password_file=path)
    await rig.attach(sid)
    server.unlock_status = 401
    await expire(rig, clock)
    await rig.cmd("read", what="screen")
    await rig.pilot.pause()
    assert rig.driver.screen() is None


@scenario
async def test_the_password_never_appears_in_any_answer_log_event_or_capture(rig, server, sid, clock, *rest):
    await expire(rig, clock)
    answer = await rig.cmd("read", what="screen")
    assert answer["ok"] and server.unlocks == 1


@scenario
async def test_any_command_restarts_the_idle_time(rig, server, sid, clock, *rest):
    for op, fields in [("sessions", {}), ("targets", {}), ("read", {"what": "screen"}),
                       ("unknown", {})]:
        clock[0] += 9
        await rig.cmd(op, **fields)
        await rig.app_call(rig.app.screen_lock._tick)
        assert not rig.app.screen_lock.locked
        assert not rig.driver.screen_lock.locked
    assert server.unlocks == 0
    assert (await rig.cmd("read", what="screen"))["ok"]
    assert server.unlocks == 0


@scenario
async def test_the_screen_lock_time_is_the_cores_and_absent_locks_mean_no_lock(rig, server, sid, clock, *rest):
    await rig.app_call(rig.app.screen_lock.clear)
    clock[0] += 10000
    assert (await rig.cmd("read", what="screen"))["ok"]
    assert server.unlocks == 0
    await rig.app_call(rig.app.screen_lock.apply, {"screen_lock_after": 2})
    clock[0] += 3
    server.unlock_status = 429
    locked(await rig.cmd("read", what="screen"))


@scenario
async def test_new_and_attach_reread_the_lock_settings_from_auth_me(rig, server, sid, clock, *rest):
    server.locks["screen_lock_after"] = 77
    server.me_started.clear()
    server.me_gate = threading.Event()
    try:
        answer = await rig.cmd("attach", session=sid, timeout=3)
        assert answer["ok"]
        for _ in range(100):
            if server.me_started.is_set():
                break
            await asyncio.sleep(.01)
        assert server.me_started.is_set()
        assert rig.app.screen_lock.settings["screen_lock_after"] == 10
    finally:
        server.me_gate.set()
    for _ in range(100):
        if rig.app.screen_lock.settings["screen_lock_after"] == 77:
            break
        await asyncio.sleep(.01)
    assert rig.app.screen_lock.settings["screen_lock_after"] == 77
    server.me_started.clear()
    server.locks["screen_lock_after"] = 88
    await rig.cmd("new", target=_serve.TARGET)
    for _ in range(100):
        if rig.app.screen_lock.settings["screen_lock_after"] == 88:
            break
        await asyncio.sleep(.01)
    assert rig.app.screen_lock.settings["screen_lock_after"] == 88
    server.me_status = 500
    await rig.cmd("attach", session=sid)
    await asyncio.sleep(.05)
    assert rig.app.screen_lock.settings["screen_lock_after"] == 88
    server.me_status = 401
    await rig.cmd("attach", session=sid)
    for _ in range(100):
        if rig.driver.screen() is None:
            break
        await asyncio.sleep(.01)
    assert rig.driver.screen() is None


@scenario
async def test_no_lock_screen_comes_up_under_drive(rig, server, sid, clock, *rest):
    await expire(rig, clock)
    assert not rig.app.screen_lock.locked
    assert rig.app.screen is rig.driver.screen()


@scenario
async def test_lock_now_under_drive_locks_drive(rig, server, sid, clock, *rest):
    old = (await rig.cmd("read", what="screen"))["look"]
    server.unlock_status = 429
    await rig.app_call(rig.app.screen_lock.lock_now)
    answer = await rig.cmd("type", text="x", look=old)
    locked(answer)
    assert not rig.app.screen_lock.locked
    assert old not in rig.driver.commands._looks


@scenario
async def test_the_screen_lock_stays_on_with_the_gate_off(rig, server, sid, clock, *rest):
    rig.driver.cfg._values["drive.send_gate"] = False
    server.unlock_status = 429
    await expire(rig, clock)
    locked(await rig.cmd("type", text="x"))
    server.unlock_status = 204
    answer = await rig.cmd("type", text="x")
    assert answer["error"] == "screen_locked" and answer["look"]
    assert (await rig.cmd("type", text="x", timeout_ms=1))["ok"]


def test_a_startup_stdin_login_cannot_supply_a_recheck(serve, monkeypatch):
    async def check():
        server, url = serve(LockServe())
        cfg = config.load(profile="drive")
        args = Namespace(broker=url, ca_file=None, user=_serve.USER, password_file="-")
        from tsb_tui.drive.runner import Driver
        login = Driver.log_in
        async def logged(driver, user, password, **kw):
            result = await login(driver, user, password, **kw)
            driver.screen_lock.engage()
            return result
        monkeypatch.setattr(Driver, "log_in", logged)
        record = io.StringIO()
        rc = await _run(args, cfg, (120, 40), _serve.PASSWORD,
                        ['{"id":1,"op":"snapshot"}'], record)
        assert rc == 1 and server.unlocks == 0
        assert '"locked":true' in record.getvalue().replace(" ", "")
    asyncio.run(check())


@scenario
async def test_a_server_failure_retries_the_recheck_on_the_next_refused_command(rig, server, sid, clock, *rest):
    server.unlock_status = 503
    await expire(rig, clock)
    answer = await rig.cmd("read", what="screen")
    locked(answer)
    assert not rig.driver.screen_lock.failure
    assert "for now" in answer["why"] and "next refused command" in answer["why"]
    assert server.unlocks == 1
    server.unlock_status = 204
    answer = await rig.cmd("read", what="screen")
    assert answer["ok"] and answer["look"]
    assert server.unlocks == 2 and not rig.driver.screen_lock.locked


@scenario
async def test_a_transport_failure_retries_the_recheck_on_the_next_refused_command(
        rig, server, sid, clock, path, record, monkeypatch):
    client = rig.app.client
    original = client.screen_unlock
    for error in (HttpError("connection reset"), ConnectionRefusedError("connection refused"),
                  ConnectionResetError("connection reset"), TimeoutError("request timed out"),
                  ssl.SSLError("TLS handshake failed")):
        attempts = []
        before = server.unlocks
        def request(password, attempts=attempts, error=error):
            attempts.append(True)
            if len(attempts) == 1:
                raise error
            return original(password)
        monkeypatch.setattr(client, "screen_unlock", request)
        await expire(rig, clock)
        answer = await rig.cmd("read", what="screen")
        locked(answer)
        assert not rig.driver.screen_lock.failure
        assert "for now" in answer["why"] and "next refused command" in answer["why"]
        answer = await rig.cmd("read", what="screen")
        assert answer["ok"] and answer["look"]
        assert len(attempts) == 2 and server.unlocks == before + 1
        assert not rig.driver.screen_lock.locked
