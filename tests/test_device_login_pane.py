"""The pane's SSH login, exercised over the pinned client's real socket."""
import asyncio
import secrets
import threading

import pytest
from device_login_support import answer, code, opened, pane_for, password, start
from textual.widgets import Input


def test_the_recorded_login_warning_comes_before_login_as(wait_until, screen_text):
    async def check():
        async with opened(wait_until, login_input=True) as (app, pilot, _, dialog, stub):
            pane = await pane_for(dialog, pilot, wait_until, consent=False)
            assert "every answer" in screen_text(app)
            assert not pane.query_one("#device-user").display
            assert not stub.creates
            pane.query_one("#login-consent").press()
            await wait_until(pilot, lambda: pane.query_one("#device-user").display, "consent")
            pane.query_one("#device-user", Input).value = "fixture-account"
            await pane.submit_user()
            await wait_until(pilot, lambda: bool(stub.creates), "create")
            assert stub.creates[0]["login"]["recording_acknowledged"] is True
    asyncio.run(check())


def test_login_as_is_asked_before_any_call(wait_until, screen_text):
    async def check():
        async with opened(wait_until) as (app, pilot, _, dialog, stub):
            await pane_for(dialog, pilot, wait_until)
            assert "login as:" in screen_text(app)
            assert not stub.creates and not stub.sessions
    asyncio.run(check())


def test_the_hostkey_is_accepted_in_the_pane_before_any_prompt(wait_until, screen_text):
    async def check():
        async with opened(wait_until, hostkey=True) as (app, pilot, _, dialog, stub):
            pane = await start(dialog, pilot, wait_until)
            # The host-key view is painted after the frame lands in the pane; read the screen once it is drawn, so the
            # fleet view behind the pane is not what the assertion reads.
            await wait_until(pilot, lambda: "Check it out of band" in screen_text(app),
                             "the host-key prompt on the screen", 30.0)
            assert "Check it out of band" in screen_text(app)
            assert pane.prompt is None
            await pane.answer_hostkey(True)
            await wait_until(pilot, lambda: pane.prompt is not None, "password")
            assert stub.login_frames[0]["accept"] is True
    asyncio.run(check())


def test_device_text_with_escapes_and_c1_is_plain_text_in_a_device_login_frame(wait_until):
    async def check():
        async with opened(wait_until, rounds=[code(text="[red]\x1b[2J\x9b\x7f\r\n\tX")]) as (_, pilot, _, dialog, _):
            pane = await start(dialog, pilot, wait_until)
            label = pane.query_one(".device-label")
            assert str(label.render()) == "[red]\\x1b[2J\\x9b\\x7f\n        X"
            assert pane.query_one("#device-text").border_title == "device login"
            assert label in pane.query_one("#device-text").query(".device-label")
    asyncio.run(check())


def test_a_password_round_shows_tsbs_own_label_outside_the_frame(wait_until):
    async def check():
        async with opened(wait_until) as (_, pilot, _, dialog, _):
            pane = await start(dialog, pilot, wait_until)
            assert str(pane.query_one("#own-password-label").render()) == "Password:"
            assert not pane.query_one("#device-text").query("#own-password-label")
    asyncio.run(check())


def test_echo_false_answers_are_masked_and_cleared_after_sending(wait_until):
    async def check():
        async with opened(wait_until, rounds=[code()]) as (_, pilot, _, dialog, stub):
            pane = await start(dialog, pilot, wait_until)
            box = pane.query_one(".device-answer", Input)
            assert box.password
            value = secrets.token_urlsafe(12)
            await answer(pane, pilot, wait_until, value)
            await wait_until(pilot, lambda: bool(stub.login_frames), "answer")
            assert stub.login_frames[0]["answer_sizes"] == [len(value)]
            assert not box.value and pane.candidate is None
    asyncio.run(check())


def test_an_empty_input_sends_an_empty_answer(wait_until):
    async def check():
        async with opened(wait_until) as (_, pilot, _, dialog, stub):
            pane = await start(dialog, pilot, wait_until)
            await answer(pane, pilot, wait_until)
            await wait_until(pilot, lambda: bool(stub.login_frames), "answer")
            assert stub.login_frames[0]["answer_sizes"] == [0]
    asyncio.run(check())


@pytest.mark.parametrize("attempt", [2])
def test_a_higher_attempt_says_the_device_refused_the_answer(wait_until, attempt):
    _attempt_check(wait_until, attempt, True)


def test_a_code_round_in_the_same_attempt_says_nothing(wait_until):
    _attempt_check(wait_until, 1, False)


def _attempt_check(wait_until, attempt, refused):
    async def check():
        async with opened(wait_until, rounds=[password(), code(attempt=attempt)]) as (_, pilot, _, dialog, _):
            pane = await start(dialog, pilot, wait_until)
            await answer(pane, pilot, wait_until)
            await wait_until(pilot, lambda: pane.prompt and pane.prompt.get("prompt_id") == 2, "code round")
            assert ("The device refused the answer." in str(pane.query_one("#device-status").render())) is refused
    asyncio.run(check())


def test_an_unknown_hostkey_kind_cancels_and_never_offers_accept(wait_until):
    _unknown(wait_until, hostkey=True, hostkey_kind="future-kind")


def test_an_unknown_prompt_method_cancels_and_says_too_old(wait_until):
    _unknown(wait_until, rounds=[{"t": "device_login_prompt", "v": 1, "method": "future-method"}])


def _unknown(wait_until, **kwargs):
    async def check():
        async with opened(wait_until, **kwargs) as (_, pilot, _, dialog, stub):
            pane = await pane_for(dialog, pilot, wait_until)
            pane.query_one("#device-user", Input).value = "fixture-account"
            await pane.submit_user()
            await wait_until(pilot, lambda: bool(stub.login_frames), "cancel")
            assert stub.login_frames[0]["t"] == "device_login_cancel"
            assert "too old" in str(pane.query_one("#device-status").render())
            assert not pane.query_one("#device-accept").display
    asyncio.run(check())


def test_connected_turns_the_overlay_into_the_attached_session_on_the_same_socket(wait_until):
    async def check():
        async with opened(wait_until) as (_, pilot, screen, dialog, stub):
            pane = await start(dialog, pilot, wait_until)
            slot = screen.front
            await answer(pane, pilot, wait_until)
            await wait_until(pilot, lambda: slot.pump and slot.pump.state.dump_seen, "attached dump")
            assert screen.front is slot and slot.session_id in stub.sessions
            assert stub.login_sockets == 1 and stub.ticket_mints == 0 and stub.attaches == 0
            assert not pane.display
    asyncio.run(check())


def test_a_display_only_round_between_password_and_acceptance_ends_connected(wait_until):
    display_only = {"t": "device_login_prompt", "v": 1, "method": "keyboard-interactive",
                    "name": "Notice", "instruction": "Welcome", "prompts": []}

    async def check():
        async with opened(wait_until, rounds=[password(), display_only]) as (_, pilot, screen, dialog, stub):
            pane = await start(dialog, pilot, wait_until)
            slot = screen.front
            await answer(pane, pilot, wait_until)
            await wait_until(pilot, lambda: slot.pump and slot.pump.state.dump_seen, "attached dump")
            assert screen.front is slot and slot.session_id in stub.sessions
            assert stub.login_sockets == 1 and stub.ticket_mints == 0 and stub.attaches == 0
            assert not pane.display
            assert pane.connected
    asyncio.run(check())


def test_an_injected_error_in_render_frame_produces_the_connect_failed_result(wait_until, monkeypatch):
    emitted = []

    async def check():
        from tsb_tui.screens import device_login

        async with opened(wait_until) as (_, pilot, _, dialog, stub):
            pane = await pane_for(dialog, pilot, wait_until)
            pane._emit = emitted.append

            async def broken(self, frame):
                raise RuntimeError("injected error")

            monkeypatch.setattr(device_login.DeviceLoginPane, "render_frame", broken)
            pane.query_one("#device-user", Input).value = "fixture-account"
            await pane.submit_user()
            await wait_until(pilot, lambda: pane.ended, "pane ended")
            assert pane.ended
            assert "The device connection failed." in str(pane.query_one("#device-status").render())
            assert pane.query_one("#device-open-again").display
            await wait_until(pilot, lambda: any(e.get("ev") == "device_login_result" for e in emitted), "emitted result")
            result = next(e for e in emitted if e.get("ev") == "device_login_result")
            assert result["outcome"] == "connect_failed"
            assert result["why"] == "The device connection failed."
            assert not any(f.get("t") == "device_login_cancel" for f in stub.login_frames)
    asyncio.run(check())


def test_input_waits_for_the_recording_gate_after_connected(wait_until):
    async def check():
        async with opened(wait_until, recording=True) as (app, pilot, screen, dialog, _):
            pane = await start(dialog, pilot, wait_until)
            await answer(pane, pilot, wait_until)
            await wait_until(pilot, lambda: bool(app.screen.query("#attach-button")), "recording gate")
            assert not await screen.send_input("key", b"x")
            app.screen.query_one("#attach-button").press()
            await wait_until(pilot, lambda: not screen.front.device_gate, "gate passed")
            assert await screen.send_input("key", b"x")
    asyncio.run(check())


@pytest.mark.parametrize("outcome", ["refused", "timeout", "password_change_requested", "hostkey_refused",
                                     "hostkey_changed", "cancelled", "connect_failed", "worker_unavailable",
                                     "too_long", "forbidden", "future-outcome"])
def test_every_other_outcome_offers_open_again(wait_until, outcome):
    async def check():
        async with opened(wait_until, outcome=outcome) as (_, pilot, _, dialog, _):
            pane = await start(dialog, pilot, wait_until)
            await answer(pane, pilot, wait_until)
            await wait_until(pilot, lambda: pane.query_one("#device-open-again").display, "open again")
            assert pane.ended and pane.candidate is None
    asyncio.run(check())


def test_a_close_with_no_result_says_lost_and_refreshes_the_sessions_list(wait_until):
    async def check():
        async with opened(wait_until, drop=True) as (_, pilot, _, dialog, stub):
            pane = await start(dialog, pilot, wait_until)
            before = stub._sessions_calls
            await answer(pane, pilot, wait_until)
            await wait_until(pilot, lambda: pane.ended, "lost link")
            assert "session may have opened" in str(pane.query_one("#device-status").render())
            await wait_until(pilot, lambda: stub._sessions_calls > before, "sessions refresh")
    asyncio.run(check())


def test_escape_sends_cancel(wait_until):
    async def check():
        async with opened(wait_until) as (_, pilot, _, dialog, stub):
            await start(dialog, pilot, wait_until)
            await pilot.press("escape")
            await wait_until(pilot, lambda: bool(stub.login_frames), "cancel")
            assert stub.login_frames[0]["t"] == "device_login_cancel"
    asyncio.run(check())


def test_a_screen_lock_hides_the_overlay_and_blocks_answers_and_the_result_waits(wait_until, screen_text):
    async def check():
        async with opened(wait_until) as (app, pilot, screen, dialog, stub):
            pane = await start(dialog, pilot, wait_until)
            app.screen_lock.lock()
            await pilot.pause()
            assert "Password:" not in screen_text(app)
            await pane.send_answers()
            assert not stub.login_frames
            app.screen_lock.unlock(app.screen)
            await pilot.pause()
            await answer(pane, pilot, wait_until)
            await wait_until(pilot, lambda: screen.front.pump is not None, "attached")
    asyncio.run(check())


@pytest.mark.parametrize("refusal", ["no_resolving_credential", "vault_locked", "org_credential_locked",
                                     "credential_choice_required", "mapped_credential_missing"])
def test_the_way_is_offered_after_each_refusal_and_never_chosen(wait_until, refusal):
    async def check():
        from tsb_contract_client import ApiError
        async with opened(wait_until) as (_, pilot, screen, dialog, stub):
            dialog._unlock_tried = True
            dialog._on_create_error(ApiError(409, refusal, "refused"))
            await pilot.pause()
            assert dialog.query_one("#device-login-start").display
            assert not screen.slots and not stub.creates
    asyncio.run(check())


@pytest.mark.parametrize("status", [422, 503])
def test_any_503_or_422_hides_the_way_for_the_login_without_reading_the_message(wait_until, status):
    async def check():
        from workspace_support import open_new_session
        async with opened(wait_until, create_error=(status, "unrelated_code", "arbitrary words")) as (_, pilot, screen, dialog, _):
            pane = await pane_for(dialog, pilot, wait_until)
            pane.query_one("#device-user", Input).value = "fixture-account"
            await pane.submit_user()
            await wait_until(pilot, lambda: pane.ended, "create refusal")
            screen._close_slot(screen.front)
            dialog = await open_new_session(screen.app, pilot)
            assert not dialog.query_one("#device-login-start").display
    asyncio.run(check())


def test_new_session_on_this_connection_sends_reuse_with_the_connection_id(wait_until):
    async def check():
        async with opened(wait_until) as (_, pilot, screen, dialog, stub):
            pane = await start(dialog, pilot, wait_until)
            await answer(pane, pilot, wait_until)
            await wait_until(pilot, lambda: screen.front.pump and screen.front.pump.state.dump_seen, "attached")
            connection = screen.front.pump.state.session["connection_id"]
            screen.app.cfg.set_runtime("size.auto_request", True)
            await screen.run_action_id("new_on_connection")
            await wait_until(pilot, lambda: len(stub.creates) == 2, "reuse")
            body = stub.creates[-1]
            assert body["connection"] == "reuse" and body["connection_id"] == connection
            assert "login" not in body
            from test_new_session_login import _gate_up
            await _gate_up(screen.app, pilot, wait_until)
    asyncio.run(check())


def test_a_cancel_while_the_create_is_in_flight_opens_no_socket(wait_until):
    gate = threading.Event()

    async def check():
        async with opened(wait_until, create_gate=gate) as (_, pilot, _, dialog, stub):
            pane = await pane_for(dialog, pilot, wait_until)
            pane.query_one("#device-user", Input).value = "fixture-account"
            await pane.submit_user()
            await wait_until(pilot, lambda: stub.create_started.is_set(), "create in flight")
            await pane.cancel()
            gate.set()
            await wait_until(pilot, lambda: pane.ended, "pane finished")
            assert pane.cancelled and stub.creates
            await pilot.pause(0.1)
            assert stub.login_sockets == 0, "a device-login socket opened after the cancel"
    asyncio.run(check())


def test_a_slot_whose_attach_failed_after_connected_shows_its_pane_again(wait_until):
    async def check():
        async with opened(wait_until, drop_after_connected=True) as (app, pilot, screen, dialog, _stub):
            pane = await start(dialog, pilot, wait_until)
            await answer(pane, pilot, wait_until)
            await wait_until(pilot, lambda: pane.ended, "the lost attach")
            assert pane.connected and screen.front.device_gate
            device_slot = screen.slots[0]
            second = screen._add_slot({"id": "other-slot", "target_id": "normal", "state": "live",
                                       "owner": app.username}, front=False, defer_attach=True)
            screen.show_front(second)
            await pilot.pause()
            assert not pane.display
            screen.show_front(device_slot)
            await pilot.pause()
            assert pane.display, "the unattached-login pane vanished when the slot came back to the front"
    asyncio.run(check())


async def _front_session(app, screen, dialog, pilot, wait_until):
    """A session opened with a login typed in the dialog and attached, in front: the device login behind it is the
    background one."""
    from test_new_session_login import _gate_up
    dialog.query_one("#login-user", Input).value = "fixture-account"
    dialog.query_one("#login-password", Input).value = secrets.token_urlsafe()
    dialog.query_one("#create-button").press()
    await _gate_up(app, pilot, wait_until)
    app.screen.query_one("#attach-button").press()
    await wait_until(pilot, lambda: bool(screen.slots), "the slot after the gate")
    slot = screen.slots[0]
    await wait_until(pilot, lambda: slot.pump is not None and slot.pump.state.client_id is not None
                     and slot.pump.state.may_type, "the session attached and typing")
    return slot


def test_a_background_login_prompt_never_takes_focus_from_the_front_session(wait_until):
    async def check():
        async with opened(wait_until, rounds=[password(), password(2, 2)]) as (app, pilot, screen, dialog, stub):
            session_slot = await _front_session(app, screen, dialog, pilot, wait_until)
            pane = screen.open_device_login("normal", "xterm-256color", screen.fit_size())
            await wait_until(pilot, lambda: pane.is_mounted, "the device login pane")
            pane.query_one("#device-user", Input).value = "fixture-account"
            await pane.submit_user()
            await wait_until(pilot, lambda: pane.prompt is not None, "the first prompt")
            await pane.send_answers()
            await pilot.pause()
            screen.show_front(session_slot)
            await pilot.pause()
            assert screen.focused is None, "moving to the session left the keys in the login's answer box"
            await wait_until(pilot, lambda: pane.prompt and pane.prompt["prompt_id"] == 2, "the background prompt")
            assert screen.focused is None, "a background login took the front session's keys"
            assert not any(box.has_focus for box in pane.query(".device-answer"))
            await pilot.press("enter")
            await wait_until(pilot, lambda: bool(stub.client_frames), "the key reaching the front session")
            assert len(stub.login_frames) == 1, "Enter sent a background login's answer to the device"
    asyncio.run(check())


def test_bringing_a_device_login_to_the_front_focuses_its_answer(wait_until):
    async def check():
        async with opened(wait_until, rounds=[password(), password(2, 2)]) as (app, pilot, screen, dialog, _stub):
            session_slot = await _front_session(app, screen, dialog, pilot, wait_until)
            pane = screen.open_device_login("normal", "xterm-256color", screen.fit_size())
            await wait_until(pilot, lambda: pane.is_mounted, "the device login pane")
            await pilot.pause()
            assert pane.query_one("#device-user").has_focus, "the login as: field did not take the keys"
            screen.show_front(session_slot)
            await pilot.pause()
            assert screen.focused is None, "moving to the session left the keys in the login pane"
            screen.show_front(pane.slot)
            await pilot.pause()
            assert pane.query_one("#device-user").has_focus, "the login as: field did not take the keys back"
            pane.query_one("#device-user", Input).value = "fixture-account"
            await pane.submit_user()
            await wait_until(pilot, lambda: pane.prompt is not None, "the first prompt")
            await pane.send_answers()
            await pilot.pause()
            screen.show_front(session_slot)
            await pilot.pause()
            assert screen.focused is None
            screen.show_front(pane.slot)
            await pilot.pause()
            assert pane.query(".device-answer").first().has_focus, "the answer box did not take the keys"
    asyncio.run(check())
