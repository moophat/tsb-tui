"""The passphrase prompt while it works (E4 phase 12, Qodo's finding 6 on #494; the orchestrator's ruling of 2026-10-31 23:15).

Why it matters (Rule 9): while the prompt shows "Deriving…" or "Unlocking…" the flow holds the typed passphrase's halves.
A Cancel or an Escape that dismissed the prompt then would leave the unlock running, and sent, after the operator was told
it was cancelled; and a flow that called `busy()` or `refuse()` on a screen that had been removed (the login ended and
`_to_login` popped every screen) would raise inside a worker. So (1) while busy, Cancel and the Escape binding are disabled
with the inputs and nothing is cancelled, and (2) the flow never calls a removed screen: it ends as cancelled and sends
nothing. Server: `tsbc serve` (`VaultServe`: C7's salt and halves); argon2id is gated by a test's event so the busy window
is as long as the test needs.
"""

from __future__ import annotations

import asyncio
import threading

from test_vault_unlock import _answer, _prompt
from textual.widgets import Button
from tsb_tui import vault_crypto, vault_flow
from tsb_tui.app import TsbTuiApp
from vault_support import CHAIN, VaultServe
from workspace_support import fixture_login


def _gated_derivation(monkeypatch) -> tuple[threading.Event, threading.Event]:
    """argon2id waits for `gate`; `started` says it has begun (the prompt shows Deriving… from then on)."""
    gate, started = threading.Event(), threading.Event()
    real = vault_crypto.halves_b64

    def gated(passphrase, salt):
        started.set()
        gate.wait(10)
        return real(passphrase, salt)

    monkeypatch.setattr(vault_crypto, "halves_b64", gated)
    return gate, started


def test_while_the_prompt_is_busy_cancel_and_escape_do_nothing_and_the_unlock_is_the_one_that_is_sent(
        serve, wait_until, monkeypatch) -> None:
    async def check() -> None:
        gate, started = _gated_derivation(monkeypatch)
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire))
        app = TsbTuiApp(url)
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await fixture_login(app, pilot)
                flow = asyncio.ensure_future(vault_flow.unlock(app))
                prompt = await _prompt(app, pilot, wait_until)
                await _answer(app, pilot, CHAIN.passphrase)
                await wait_until(pilot, started.is_set, "the derivation to begin")
                cancel = prompt.query_one("#vault-cancel", Button)
                assert cancel.disabled, "Cancel is disabled with the inputs while the prompt works"
                cancel.press()
                await pilot.press("escape")
                await pilot.pause(0.1)
                assert app.screen is prompt and not flow.done(), "neither Cancel nor Escape took the prompt away"
                gate.set()
                result = await asyncio.wait_for(flow, 10)
                assert result.status == "unlocked", result
                assert len(serve_app.unlock_bodies) == 1, "the unlock the operator saw running is the one sent"
        finally:
            gate.set()

    asyncio.run(check())


def test_the_flow_never_calls_a_screen_that_was_removed_under_it_and_sends_nothing(
        serve, wait_until, monkeypatch) -> None:
    async def check() -> None:
        gate, started = _gated_derivation(monkeypatch)
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire))
        app = TsbTuiApp(url)
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await fixture_login(app, pilot)
                flow = asyncio.ensure_future(vault_flow.unlock(app))
                prompt = await _prompt(app, pilot, wait_until)
                await _answer(app, pilot, CHAIN.passphrase)
                await wait_until(pilot, started.is_set, "the derivation to begin")
                await app.pop_screen()      # what the end of a login does to every screen above the base
                await wait_until(pilot, lambda: not prompt.is_attached, "the prompt to be removed")
                gate.set()
                result = await asyncio.wait_for(flow, 10)
                assert result.status == "cancelled", result
                assert serve_app.unlock_bodies == [], "nothing is sent for a prompt that is gone"
        finally:
            gate.set()

    asyncio.run(check())
