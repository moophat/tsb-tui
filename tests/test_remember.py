"""Remember saves only a password-only login, through the existing vault flow."""
import asyncio
import hashlib
import secrets

from device_login_support import answer, code, opened, password, start


def _save_spy(monkeypatch, *, collision=False):
    from tsb_contract_client import ApiError

    from tsb_tui import vault_flow
    calls = []

    async def save(app, target, user, *, password, reference):
        calls.append((target, user, hashlib.sha256(password.encode()).hexdigest(), reference))
        if collision:
            return vault_flow.SaveOutcome("error", reference, error=ApiError(409, "credential_exists", "exists"))
        return vault_flow.SaveOutcome("saved", reference, stored=True, mapped=True)
    monkeypatch.setattr(vault_flow, "save_login", save)
    return calls


def test_remember_saves_the_last_password_round_after_connected_through_save_login(wait_until, monkeypatch):
    calls = _save_spy(monkeypatch)
    async def check():
        async with opened(wait_until, rounds=[password(), password(2, 2)]) as (_, pilot, _, dialog, _):
            pane = await start(dialog, pilot, wait_until)
            await answer(pane, pilot, wait_until, secrets.token_urlsafe(), remember=True)
            await wait_until(pilot, lambda: pane.prompt and pane.prompt["prompt_id"] == 2, "second round")
            assert not calls
            last = secrets.token_urlsafe()
            await answer(pane, pilot, wait_until, last)
            await wait_until(pilot, lambda: len(calls) == 1, "saved")
            assert calls[0][1:] == ("fixture-account", hashlib.sha256(last.encode()).hexdigest(), "normal.fixture-account")
            assert pane.candidate is None
    asyncio.run(check())


def test_a_connected_login_whose_attach_is_lost_saves_remember_and_offers_no_open_again(wait_until, monkeypatch):
    calls = _save_spy(monkeypatch)
    async def check():
        async with opened(wait_until, drop_after_connected=True) as (_, pilot, _, dialog, stub):
            pane = await start(dialog, pilot, wait_until)
            before = stub._sessions_calls
            await answer(pane, pilot, wait_until, secrets.token_urlsafe(), remember=True)
            await wait_until(pilot, lambda: pane.ended, "the lost attach")
            assert pane.connected and pane.candidate is None
            status = str(pane.query_one("#device-status").render())
            assert "session opened" in status and "could not attach" in status
            assert not pane.query_one("#device-open-again").display
            assert len(calls) == 1 and calls[0][1] == "fixture-account"
            await wait_until(pilot, lambda: stub._sessions_calls > before, "sessions refresh")
    asyncio.run(check())


def test_remember_saves_after_a_201_for_phase_a_fields(wait_until, monkeypatch):
    from textual.widgets import Input
    calls = _save_spy(monkeypatch)
    async def check():
        async with opened(wait_until) as (_, pilot, _, dialog, stub):
            dialog.query_one("#target-id", Input).value = "normal"
            dialog._types["normal"] = "ssh"
            dialog.query_one("#login-user", Input).value = "fixture-account"
            dialog.query_one("#login-password", Input).value = secrets.token_urlsafe()
            dialog.query_one("#login-remember").value = True
            dialog.query_one("#create-button").press()
            await wait_until(pilot, lambda: len(calls) == 1, "save after create")
            assert stub.sessions and calls[0][0] == "normal"
            await wait_until(pilot, lambda: not dialog.query_one("#login-password", Input).value, "cleared fields")
            from test_new_session_login import _gate_up
            await _gate_up(dialog.app, pilot, wait_until)
            # The test stub's ordinary Phase A path is not a retained-secret request log.
            stub.creates.clear()
    asyncio.run(check())


def test_a_result_while_locked_saves_after_the_recheck_and_a_signout_drops_it(wait_until, monkeypatch):
    calls = _save_spy(monkeypatch)
    async def check():
        for signout in (False, True):
            async with opened(wait_until) as (app, pilot, _, dialog, stub):
                pane = await start(dialog, pilot, wait_until)
                stub.finish_gate.clear()
                await answer(pane, pilot, wait_until, secrets.token_urlsafe(), remember=True)
                app.screen_lock.lock()
                stub.finish_gate.set()
                await wait_until(pilot, lambda pane=pane: pane.connected, "result while locked")
                before = len(calls)
                assert pane.candidate is not None
                if signout:
                    await app.sign_out()
                    assert pane.candidate is None and len(calls) == before
                else:
                    app.screen._resumed(app._login_gen)
                    await wait_until(pilot, lambda before=before: len(calls) == before + 1, "save after recheck")
                    assert pane.candidate is None
    asyncio.run(check())


def test_a_declined_recording_gate_after_connected_still_saves_the_remembered_password(wait_until, monkeypatch):
    calls = _save_spy(monkeypatch)
    async def check():
        async with opened(wait_until, recording=True) as (app, pilot, screen, dialog, _stub):
            pane = await start(dialog, pilot, wait_until)
            value = secrets.token_urlsafe()
            await answer(pane, pilot, wait_until, value, remember=True)
            await wait_until(pilot, lambda: bool(app.screen.query("#attach-button")), "the recording gate")
            app.screen.query_one("#back-button").press()
            await wait_until(pilot, lambda: len(calls) == 1, "the save after the declined gate")
            assert calls[0][2] == hashlib.sha256(value.encode()).hexdigest()
            assert pane.candidate is None
            assert pane.slot.closed and not screen.slots, "declining the gate attached the pane to the session"
    asyncio.run(check())


def test_a_declined_gate_while_locked_saves_after_the_recheck(wait_until, monkeypatch):
    calls = _save_spy(monkeypatch)
    async def check():
        async with opened(wait_until, recording=True) as (app, pilot, _screen, dialog, stub):
            pane = await start(dialog, pilot, wait_until)
            stub.finish_gate.clear()
            value = secrets.token_urlsafe()
            await answer(pane, pilot, wait_until, value, remember=True)
            app.screen_lock.lock()
            stub.finish_gate.set()
            await wait_until(pilot, lambda: pane.connected, "connected while locked")
            assert not calls and pane.candidate is not None
            app.screen._resumed(app._login_gen)
            await wait_until(pilot, lambda: bool(app.screen.query("#back-button")), "the gate after the recheck")
            app.screen.query_one("#back-button").press()
            await wait_until(pilot, lambda: len(calls) == 1, "the save after the declined gate")
            assert calls[0][2] == hashlib.sha256(value.encode()).hexdigest()
    asyncio.run(check())


def test_the_declined_gate_shows_the_save_outcome_after_the_slot_closes(wait_until, monkeypatch):
    """A declined gate closes the login's slot, so the save's outcome is told where a closed slot cannot hide
    it: in the app's notice, the same way every other state change is told. Both the saved and the refused
    message reach the operator after the slot is gone."""

    def notices(app):
        return [n.message for n in app._notifications._notifications.values()]

    async def check(collision):
        calls = _save_spy(monkeypatch, collision=collision)
        expected = ("Not saved: you already have a login named normal.fixture-account" if collision
                    else "Saved normal.fixture-account; TSB now uses it for normal.")
        async with opened(wait_until, recording=True) as (app, pilot, screen, dialog, _stub):
            pane = await start(dialog, pilot, wait_until)
            value = secrets.token_urlsafe()
            await answer(pane, pilot, wait_until, value, remember=True)
            await wait_until(pilot, lambda: bool(app.screen.query("#attach-button")), "the recording gate")
            app.screen.query_one("#back-button").press()
            await wait_until(pilot, lambda: len(calls) == 1 and pane.slot.closed and not screen.slots,
                             "the save, then the closed slot")
            await wait_until(pilot, lambda: any(expected in message for message in notices(app)),
                             "the notice that outlives the slot")

    for collision in (False, True):
        asyncio.run(check(collision))


def test_no_box_is_offered_on_a_keyboard_interactive_round(wait_until):
    _no_box(wait_until, [code(1)])


def test_a_password_then_a_code_drops_the_candidate_and_says_why(wait_until):
    async def check():
        async with opened(wait_until, rounds=[password(), code()]) as (_, pilot, _, dialog, _):
            pane = await start(dialog, pilot, wait_until)
            await answer(pane, pilot, wait_until, secrets.token_urlsafe(), remember=True)
            await wait_until(pilot, lambda: pane.prompt and pane.prompt["method"] == "keyboard-interactive", "code")
            assert pane.candidate is None and not pane.query_one("#device-remember").display
            assert "only a password is saved" in str(pane.query_one("#device-status").render())
    asyncio.run(check())


def test_a_code_then_a_password_offers_no_box(wait_until):
    _no_box(wait_until, [code(1), password(2)])


def _no_box(wait_until, rounds):
    async def check():
        async with opened(wait_until, rounds=rounds) as (_, pilot, _, dialog, _):
            pane = await start(dialog, pilot, wait_until)
            assert not pane.query_one("#device-remember").display
            if len(rounds) > 1:
                await answer(pane, pilot, wait_until)
                await wait_until(pilot, lambda: pane.prompt and pane.prompt["prompt_id"] == 2, "password")
                assert not pane.query_one("#device-remember").display
    asyncio.run(check())


def test_remember_saves_nothing_when_unticked_or_not_connected(wait_until, monkeypatch):
    calls = _save_spy(monkeypatch)
    async def check():
        for outcome in ("connected", "refused"):
            async with opened(wait_until, outcome=outcome) as (_, pilot, _, dialog, _):
                pane = await start(dialog, pilot, wait_until)
                await answer(pane, pilot, wait_until, secrets.token_urlsafe(), remember=outcome != "connected")
                await wait_until(pilot, lambda pane=pane: pane.connected or pane.ended, "result")
                assert not calls and pane.candidate is None
    asyncio.run(check())


def test_the_proposed_reference_is_sanitised_cut_in_the_target_part_and_keeps_the_user():
    from tsb_tui.screens.device_login import proposed_reference
    assert proposed_reference("rtr/@", "alice+b") == "rtr__.alice_b"
    assert proposed_reference("t" * 100, "u" * 40) == "t" * 23 + "." + "u" * 40
    assert proposed_reference("target", "u" * 100) == "t." + "u" * 62


def test_a_used_reference_saves_nothing_and_says_so(wait_until, monkeypatch):
    _status(wait_until, monkeypatch, True)


def test_the_status_line_says_tsb_now_uses_the_saved_login(wait_until, monkeypatch):
    _status(wait_until, monkeypatch, False)


def _status(wait_until, monkeypatch, collision):
    calls = _save_spy(monkeypatch, collision=collision)
    async def check():
        async with opened(wait_until) as (_, pilot, screen, dialog, _):
            pane = await start(dialog, pilot, wait_until)
            await answer(pane, pilot, wait_until, secrets.token_urlsafe(), remember=True)
            await wait_until(pilot, lambda: bool(calls) and pane.candidate is None, "save completion")
            assert ("delete it in Credentials first" if collision else "TSB now uses it for normal.") in screen.front.login_status
    asyncio.run(check())


def test_the_candidate_is_dropped_on_untick_save_and_end(wait_until, monkeypatch):
    calls = _save_spy(monkeypatch)
    async def check():
        async with opened(wait_until, rounds=[password(), password(2, 2)]) as (_, pilot, _, dialog, _):
            pane = await start(dialog, pilot, wait_until)
            await answer(pane, pilot, wait_until, secrets.token_urlsafe(), remember=True)
            await wait_until(pilot, lambda: pane.prompt and pane.prompt["prompt_id"] == 2, "next prompt")
            assert pane.candidate is not None
            pane.query_one("#device-remember").value = False
            await pilot.pause()
            assert pane.candidate is None
            await pane.cancel()
            assert pane.candidate is None and not calls
    asyncio.run(check())
