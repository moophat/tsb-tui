"""Headless device-login commands use the same flow without a Remember candidate."""
import asyncio
import io
import secrets

from device_login_support import code, password
from drive_support import Rig
from stub_server import DeviceLoginApp, StubServer
from test_drive_new_login import _pipe_with


async def _event(rig, name):
    for _ in range(500):
        if rig.events(name):
            return rig.events(name)[-1]
        await rig.pilot.pause(0.01)
    raise AssertionError(f"no {name} event")


async def _new(rig):
    result = await rig.cmd("new", target="normal", login_mode="prompt", login_user="fixture-account")
    assert result["ok"], result
    return result["device_login"]


def test_drive_relays_hostkey_prompts_and_result_as_events(tmp_path):
    async def check():
        stub = DeviceLoginApp(rounds=[code(1)], hostkey=True)
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
                await rig.login()
                dlid = await _new(rig)
                event = await _event(rig, "device_login_hostkey")
                assert event["kind"] == "first_use" and event["device_login"] == dlid
                assert (await rig.cmd("login_hostkey", device_login=dlid, accept=True))["ok"]
                prompt = await _event(rig, "device_login_prompt")
                assert prompt["attempt"] == 1 and prompt["prompt_id"] == 1 and not prompt["prompts"][0]["echo"]
                with _pipe_with("\n") as path:
                    assert (await rig.cmd("login_answer", device_login=dlid, prompt_id=1, answers_file=path))["ok"]
                result = await _event(rig, "device_login_result")
                assert result["outcome"] == "connected"
    asyncio.run(check())


def test_connected_carries_the_attach_fields(tmp_path):
    async def check():
        stub = DeviceLoginApp(rounds=[password()], recording_on_create=True)
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
                await rig.login()
                dlid = await _new(rig)
                await _event(rig, "device_login_prompt")
                with _pipe_with("\n") as path:
                    await rig.cmd("login_answer", device_login=dlid, prompt_id=1, answers_file=path)
                event = await _event(rig, "device_login_result")
                assert {"session", "chip", "front", "you", "holder", "you_may_type", "size"} <= event.keys()
                assert event["you"] == "type"
                assert rig.lines.index(rig.events("gate")[0]) < rig.lines.index(event)
    asyncio.run(check())


def test_prompt_mode_needs_size_auto_request_and_takes_recording_acknowledged(tmp_path):
    async def check():
        stub = DeviceLoginApp(rounds=[password()], login_input=True)
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=false",)) as rig:
                await rig.login()
                result = await rig.cmd("new", target="normal", login_mode="prompt", login_user="fixture-account")
                assert result["error"] == "not_available" and not stub.creates
                await rig.app_call(rig.app.cfg.set_runtime, "size.auto_request", True)
                result = await rig.cmd("new", target="normal", login_mode="prompt", login_user="fixture-account")
                assert result["error"] == "login_recording_not_acknowledged" and not stub.sessions
                result = await rig.cmd("new", target="normal", login_mode="prompt", login_user="fixture-account",
                                       recording_acknowledged=True)
                assert result["ok"] and stub.creates[-1]["login"]["recording_acknowledged"] is True
    asyncio.run(check())


def test_answers_from_stdin_are_never_in_the_transcript(tmp_path):
    async def check():
        stub = DeviceLoginApp(rounds=[password()])
        transcript = io.StringIO()
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",), record=transcript) as rig:
                await rig.login()
                dlid = await _new(rig)
                await _event(rig, "device_login_prompt")
                value = secrets.token_urlsafe()
                pending = rig.send("login_answer", device_login=dlid, prompt_id=1, answers_from="stdin")
                rig.raw(value)
                assert (await pending)["ok"]
                await _event(rig, "device_login_result")
                assert value not in transcript.getvalue()
                assert stub.login_frames[0]["answer_sizes"] == [len(value)]
    asyncio.run(check())


def test_answers_file_takes_a_pipe_and_refuses_a_regular_file(tmp_path):
    async def check():
        stub = DeviceLoginApp(rounds=[password()])
        empty = tmp_path / "empty"
        empty.touch()
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
                await rig.login()
                dlid = await _new(rig)
                await _event(rig, "device_login_prompt")
                for path in (str(empty), "/dev/null"):
                    result = await rig.cmd("login_answer", device_login=dlid, prompt_id=1, answers_file=path)
                    assert not result["ok"] and not stub.login_frames
                with _pipe_with("\n") as path:
                    assert (await rig.cmd("login_answer", device_login=dlid, prompt_id=1, answers_file=path))["ok"]
    asyncio.run(check())


def test_answers_in_the_command_and_stdin_in_script_mode_are_refused(tmp_path):
    async def check():
        stub = DeviceLoginApp(rounds=[password()])
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
                await rig.login()
                dlid = await _new(rig)
                await _event(rig, "device_login_prompt")
                result = await rig.cmd("login_answer", device_login=dlid, prompt_id=1, answers=[])
                assert result["error"] == "bad_request"
                rig.driver.script = True
                result = await rig.cmd("login_answer", device_login=dlid, prompt_id=1, answers_from="stdin")
                assert result["error"] == "bad_request" and not stub.login_frames
                rig.driver.script = False
    asyncio.run(check())


def test_the_look_rule_does_not_gate_login_answers(tmp_path):
    async def check():
        stub = DeviceLoginApp(rounds=[password()])
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
                await rig.login()
                dlid = await _new(rig)
                await _event(rig, "device_login_prompt")
                await rig.app_call(rig.app.screen_lock.apply, {"screen_lock_after": 1, "activity_interval": 1})
                rig.app.idle_clock = lambda: rig.app.screen_lock._last_input + 2
                await rig.app_call(rig.app.screen_lock._tick)
                with _pipe_with("\n") as path:
                    result = await rig.cmd("login_answer", device_login=dlid, prompt_id=1, answers_file=path)
                assert result["ok"]
                assert (await rig.cmd("login_cancel", device_login=dlid))["ok"]
    asyncio.run(check())


def test_drive_new_offers_no_remember(tmp_path):
    async def check():
        stub = DeviceLoginApp(rounds=[password()])
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
                await rig.login()
                await _new(rig)
                await _event(rig, "device_login_prompt")
                pane = rig.app.workspace().front.device_login
                assert not pane.query_one("#device-remember").display and pane.candidate is None
    asyncio.run(check())


def test_drive_cancels_an_unknown_kind_and_reports_it(tmp_path):
    async def check():
        stub = DeviceLoginApp(rounds=[], hostkey=True, hostkey_kind="future-kind")
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
                await rig.login()
                await _new(rig)
                event = await _event(rig, "device_login_result")
                assert "too old" in event["why"] and event["error"] == "not_available"
                assert stub.login_frames[0]["t"] == "device_login_cancel"
    asyncio.run(check())


def test_login_cancel_after_connected_says_the_session_lives_on(tmp_path):
    async def check():
        stub = DeviceLoginApp(rounds=[password()])
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
                await rig.login()
                dlid = await _new(rig)
                await _event(rig, "device_login_prompt")
                with _pipe_with("\n") as path:
                    await rig.cmd("login_answer", device_login=dlid, prompt_id=1, answers_file=path)
                await _event(rig, "device_login_result")
                result = await rig.cmd("login_cancel", device_login=dlid)
                assert result["error"] == "not_available" and "connected" in result["why"]
    asyncio.run(check())
