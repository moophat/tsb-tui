"""Late creates, independent Remember saves, and transient login notices."""
import asyncio
import os
import secrets

from device_login_support import answer, code, opened, password, start
from drive_support import Rig
from stub_server import DeviceLoginApp, StubServer
from test_drive_device_login import _event, _new
from test_remember import _save_spy
from textual.widgets import Input


def _remember(dialog):
    dialog.query_one("#login-user", Input).value = "fixture-account"
    dialog.query_one("#login-password", Input).value = secrets.token_urlsafe()
    dialog.query_one("#login-remember").value = True


def test_a_created_login_waits_for_unlock_before_remembering(wait_until, monkeypatch):
    calls = _save_spy(monkeypatch)

    async def check():
        for signout in (False, True):
            async with opened(wait_until) as (app, pilot, screen, dialog, _):
                _remember(dialog)
                login = dialog._login_to_send("normal")
                password_box = dialog.query_one("#login-password", Input)
                opened_sessions = []
                monkeypatch.setattr(screen, "open_session", lambda *a, opened_sessions=opened_sessions, **kw: opened_sessions.append(a))
                app.screen_lock.lock()
                await pilot.pause()
                before = len(calls)
                dialog._on_create_done({"session": {"id": "created", "target_id": "normal"}}, login)
                assert not opened_sessions and len(calls) == before
                assert dialog.query_one("#login-password", Input).value, "Remember password was discarded under lock"
                if signout:
                    await app.sign_out()
                    assert len(calls) == before and not opened_sessions
                    await wait_until(pilot, lambda password_box=password_box: not password_box.value, "password dropped on sign-out")
                else:
                    app.screen._resumed(app._login_gen)
                    await wait_until(pilot, lambda before=before: len(calls) == before + 1, "Remember after recheck")
                    assert calls[-1][0:2] == ("normal", "fixture-account")
                    assert len(opened_sessions) == 1
    asyncio.run(check())


def test_a_created_session_opens_before_its_remember_save_finishes(wait_until, monkeypatch):
    from tsb_tui import vault_flow

    async def check():
        release, saving = asyncio.Event(), asyncio.Event()
        captured = []

        async def save(app, target, user, *, password, reference):
            captured.append((target, user, bool(password)))
            saving.set()
            await release.wait()
            return vault_flow.SaveOutcome("saved", reference, stored=True, mapped=True)

        monkeypatch.setattr(vault_flow, "save_login", save)
        async with opened(wait_until) as (app, pilot, screen, dialog, _):
            _remember(dialog)
            login = dialog._login_to_send("normal")
            password_box = dialog.query_one("#login-password", Input)
            slots = []

            def open_session(session, hostkey, *, then=None):
                from types import SimpleNamespace
                slot = SimpleNamespace(login_status="", login_status_until=0)
                slots.append(slot)
                monkeypatch.setattr(screen, "slot_for", lambda sid: slot)
                if then:
                    then(slot)

            monkeypatch.setattr(screen, "open_session", open_session)
            dialog._on_create_done({"session": {"id": "created", "target_id": "normal"}}, login)
            try:
                await wait_until(pilot, saving.is_set, "save starts")
                assert len(slots) == 1, "Opening the session waited for the Remember save"
                assert captured == [("normal", "fixture-account", True)]
                assert not password_box.value
                app.screen_lock.lock()
            finally:
                release.set()
            await wait_until(pilot, lambda: bool(slots and slots[0].login_status), "save outcome on slot")
            assert "TSB now uses it for normal." in slots[0].login_status
    asyncio.run(check())


def test_login_notices_leave_the_status_row_after_the_overlay_and_notice_expiry(wait_until, monkeypatch):
    _save_spy(monkeypatch)

    async def check():
        async with opened(wait_until, rounds=[password(), password(2, 2)]) as (app, pilot, screen, dialog, _):
            pane = await start(dialog, pilot, wait_until)
            await answer(pane, pilot, wait_until, secrets.token_urlsafe())
            await wait_until(pilot, lambda: pane.prompt and pane.prompt["prompt_id"] == 2, "refused answer")
            assert "refused" in screen.status_inputs().login_status
            await answer(pane, pilot, wait_until, secrets.token_urlsafe())
            await wait_until(pilot, lambda: not pane.slot.device_gate, "overlay gone")
            assert screen.status_inputs().login_status == "", "The hidden overlay still replaces session status"
            screen.set_login_status(pane.slot, "Saved the login.")
            assert screen.status_inputs().login_status == "Saved the login."
            clock = app.wall_clock
            monkeypatch.setattr(app, "wall_clock", lambda: clock() + app.cfg["toasts.seconds"] + 1)
            assert screen.status_inputs().login_status == ""
    asyncio.run(check())


def test_missing_pipe_answer_lines_expire_without_sending_a_round(tmp_path):
    async def check():
        prompt = code(1)
        prompt["prompts"] *= 2
        stub = DeviceLoginApp(rounds=[prompt])
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path,
                           overrides=("size.auto_request=true", "drive.answer_timeout_ms=60")) as rig:
                await rig.login()
                dlid = await _new(rig)
                await _event(rig, "device_login_prompt")
                for prefix in (b"", b"incomplete", b"first\nincomplete"):
                    reader, writer = os.pipe()
                    os.write(writer, prefix)
                    task = asyncio.create_task(rig.cmd("login_answer", device_login=dlid, prompt_id=1,
                                                       answers_file=f"/dev/fd/{reader}"))
                    try:
                        try:
                            result = await asyncio.wait_for(asyncio.shield(task), 0.6)
                        except TimeoutError:
                            raise AssertionError("The missing answer line blocked past its configured deadline") from None
                        assert not result["ok"] and result["error"] == "bad_request"
                        assert "answers did not arrive" in result["why"]
                        assert not stub.login_frames
                    finally:
                        os.close(writer)
                        os.close(reader)
                        await task
                assert (await rig.cmd("login_cancel", device_login=dlid))["ok"]
    asyncio.run(check())
