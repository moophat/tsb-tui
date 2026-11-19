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


def test_prompt_mode_with_auto_size_off_still_requires_recording_acknowledgement(tmp_path):
    async def check():
        stub = DeviceLoginApp(rounds=[password()], login_input=True)
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=false",)) as rig:
                await rig.login()
                result = await rig.cmd("new", target="normal", login_mode="prompt", login_user="fixture-account")
                assert result["error"] == "login_recording_not_acknowledged" and not stub.sessions
                result = await rig.cmd("new", target="normal", login_mode="prompt", login_user="fixture-account",
                                       recording_acknowledged=True)
                assert result["ok"] and stub.creates[-1]["login"]["recording_acknowledged"] is True
                assert (stub.creates[-1]["cols"], stub.creates[-1]["rows"]) == (104, 37)  # the window less the rail, the frame and the status row
                assert rig.app.cfg["size.auto_request"] is False
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


def test_an_error_after_adoption_leaves_the_attachment_and_sends_connected_once(tmp_path, monkeypatch, wait_until):
    """The device accepted the login and the slot took its attachment; an error finishing the login after adoption
    never says connect_failed: drive gets one `connected` result with the pane's plain words and no attach fields,
    the slot keeps its attachment, and the slot's status line carries those words."""

    async def check():
        from tsb_tui.screens import device_login, terminal

        def broken(self, slot):
            raise RuntimeError("the size request exploded")

        monkeypatch.setattr(terminal.TerminalScreen, "request_device_size", broken)
        stub = DeviceLoginApp(rounds=[password()])
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
                await rig.login()
                dlid = await _new(rig)
                await _event(rig, "device_login_prompt")
                pane = rig.app.workspace().front.device_login
                slot = pane.slot
                with _pipe_with("\n") as path:
                    await rig.cmd("login_answer", device_login=dlid, prompt_id=1, answers_file=path)
                result = await _event(rig, "device_login_result")
                results = rig.events("device_login_result")
                assert len(results) == 1, f"drive got more than one result: {results}"
                assert result["outcome"] == "connected" and result["why"] == device_login.POST_ADOPT
                assert not {"session", "chip", "front", "you", "holder", "you_may_type", "size"} & result.keys()
                assert slot.session_id is not None
                await wait_until(rig.pilot, lambda: slot.pump is not None and slot.pump.state.dump_seen,
                                 "the adopted slot's dump")
                assert slot.login_status == device_login.POST_ADOPT
    asyncio.run(check())


def test_an_error_after_the_connected_result_sends_drive_no_second_result(tmp_path, monkeypatch):
    """Drive has its `connected` result with the attach fields; an error after it (here, in the Remember save) is
    told on the slot's status line and sends drive nothing else: no second result, never connect_failed."""

    async def check():
        from tsb_tui.screens import device_login

        async def broken_save(app, target, username, **kw):
            raise RuntimeError("the save exploded")

        monkeypatch.setattr(device_login.vault_flow, "save_login", broken_save)
        stub = DeviceLoginApp(rounds=[password()])
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
                await rig.login()
                dlid = await _new(rig)
                await _event(rig, "device_login_prompt")
                pane = rig.app.workspace().front.device_login
                # Drive never offers Remember, so the test names the candidate the save step takes.
                pane.candidate = "x"
                with _pipe_with("\n") as path:
                    await rig.cmd("login_answer", device_login=dlid, prompt_id=1, answers_file=path)
                first = await _event(rig, "device_login_result")
                assert first["outcome"] == "connected" and "session" in first
                await rig.pilot.pause(0.3)
                results = rig.events("device_login_result")
                assert len(results) == 1, f"drive got a second result: {results}"
                assert not any(r.get("outcome") == "connect_failed" for r in results)
    asyncio.run(check())


def test_an_error_before_the_handoff_closes_the_slot_and_never_leaves_it_behind_the_gate(
        tmp_path, monkeypatch, wait_until):
    """Qodo's finding (round 3): an error after adoption but before `slot.device_gate` is cleared must never leave
    the adopted session behind the gate while drive heard `connected`. The pane closes the slot (the session lives
    on), toasts the unattached words, and drive gets one `connected` result with that why and no attach fields."""

    async def check():
        from tsb_tui.screens import device_login

        async def broken(self):
            raise RuntimeError("the unlock exploded")

        monkeypatch.setattr(device_login.DeviceLoginPane, "_unlocked", broken)
        stub = DeviceLoginApp(rounds=[password()])
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
                await rig.login()
                dlid = await _new(rig)
                await _event(rig, "device_login_prompt")
                pane = rig.app.workspace().front.device_login
                slot = pane.slot
                with _pipe_with("\n") as path:
                    await rig.cmd("login_answer", device_login=dlid, prompt_id=1, answers_file=path)
                result = await _event(rig, "device_login_result")
                results = rig.events("device_login_result")
                assert len(results) == 1, f"drive got more than one result: {results}"
                assert result["outcome"] == "connected" and result["why"] == device_login.UNATTACHED
                assert not {"session", "chip", "front", "you", "holder", "you_may_type", "size"} & result.keys()
                assert not any(r.get("outcome") == "connect_failed" for r in results)
                await wait_until(rig.pilot, lambda: slot not in rig.app.workspace().slots, "the closed slot")
                assert slot.leaving and slot.session_id is not None, "the adopted slot was never taken off the screen"
                assert not [s for s in rig.app.workspace().slots if s.device_gate and s.session_id is not None], \
                    "a session stayed attached behind the recording gate"
                assert any(device_login.UNATTACHED in n.message for n in rig.app._notifications), \
                    "the person was never told the session is in the sessions list"
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
