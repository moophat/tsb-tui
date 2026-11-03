"""A remembered login belongs to its submitted request and its TSB login."""
import asyncio
import contextlib
import hashlib
import secrets
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from device_login_support import answer, opened, start
from test_remember import _save_spy
from textual.widgets import Input
from tsb_contract_client import AsyncDeviceLogin
from tsb_tui import vault_flow


@pytest.mark.parametrize("changed", ["client", "generation"])
@pytest.mark.parametrize("paused", ["salt", "store"])
def test_a_save_stops_before_the_next_vault_call_when_its_login_changes(changed, paused):
    async def check():
        client = SimpleNamespace(vault_salt=Mock(), store_credential=Mock(), map_profiles=Mock())
        replacement = SimpleNamespace(vault_salt=Mock(), store_credential=Mock(), map_profiles=Mock())
        app = SimpleNamespace(client=client, _login_gen=1)
        waiting = asyncio.Event()
        callbacks = []
        sent = []

        def dispatch(func, *args, on_done, **kwargs):
            sent.append(func)
            if func is (client.vault_salt if paused == "salt" else client.store_credential):
                callbacks.append(on_done)
                waiting.set()
            else:
                on_done({"initialized": True})

        app.call_api = dispatch
        task = asyncio.create_task(vault_flow.save_login(
            app, "normal", "fixture-account", password=secrets.token_urlsafe(), reference="remembered"))
        await asyncio.wait_for(waiting.wait(), 2)
        if changed == "client":
            app.client = replacement
        else:
            app._login_gen += 1
        callbacks[0]({"initialized": True})
        result = await asyncio.wait_for(task, 2)
        assert sent == ([client.vault_salt] if paused == "salt" else
                        [client.vault_salt, client.store_credential]), "a stale save sent another vault call"
        assert result.status == "cancelled" and not result.mapped
    asyncio.run(check())


@pytest.mark.parametrize("remember", [False, True])
def test_a_create_remembers_only_the_login_and_choice_it_submitted(wait_until, monkeypatch, remember):
    calls = _save_spy(monkeypatch)
    gate = threading.Event()

    async def check():
        async with opened(wait_until, create_gate=gate) as (_, pilot, _, dialog, stub):
            original = secrets.token_urlsafe()
            dialog.query_one("#login-user", Input).value = "submitted-account"
            dialog.query_one("#login-password", Input).value = original
            dialog.query_one("#login-remember").value = remember
            dialog._attempt_create()
            await wait_until(pilot, lambda: stub.create_started.is_set(), "create in flight")
            try:
                dialog.query_one("#login-user", Input).value = "edited-account"
                dialog.query_one("#login-password", Input).value = secrets.token_urlsafe()
                dialog.query_one("#login-remember").value = not remember
            finally:
                gate.set()
            await wait_until(pilot, lambda: dialog.app.screen is not dialog, "create completed")
            from test_new_session_login import _gate_up
            await _gate_up(dialog.app, pilot, wait_until)
            assert calls == ([("normal", "submitted-account", hashlib.sha256(original.encode()).hexdigest(),
                               "normal.submitted-account")] if remember else [])
            stub.creates.clear()
    asyncio.run(check())


def test_cancelling_while_the_connected_socket_waits_for_attachment_closes_it(wait_until, monkeypatch):
    async def check():
        waiting = asyncio.Event()
        closed = []
        original_close = AsyncDeviceLogin.close

        async def attachment(login):
            waiting.set()
            await asyncio.Future()

        async def close(login):
            closed.append(login)
            await original_close(login)

        monkeypatch.setattr(AsyncDeviceLogin, "attachment", attachment)
        monkeypatch.setattr(AsyncDeviceLogin, "close", close)
        async with opened(wait_until) as (_, pilot, _, dialog, _):
            pane = await start(dialog, pilot, wait_until)
            await answer(pane, pilot, wait_until)
            await asyncio.wait_for(waiting.wait(), 2)
            assert pane.connected
            await pane.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(pane._login_task, 2)
            assert pane.login in closed, "the connected socket was never adopted or closed"
    asyncio.run(check())
