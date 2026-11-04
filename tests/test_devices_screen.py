"""Own devices: list and revoke (phase 12 § E; credential §7.1 "User revokes via Settings UI"; DEFAULT 40).

Why it matters (Rule 9): a lost laptop's browser is revoked from here. The list shows what a person needs to recognise a
device (its name as text, when it was registered, when it was last used, whether it is revoked) and never a key. Revoking
asks once and says what revocation does not stop: someone who knows the TSB password is dealt with by changing it, which
the claim "revoked" must not hide. A revoke the core refuses (404 `device_not_found`) is shown, not guessed at. The TUI
never registers a device or sends a challenge (DEFAULT 64). Server: `tsbc serve` (`VaultServe`).
"""

from __future__ import annotations

import asyncio

from textual.widgets import Button, DataTable
from tsbc import _serve
from vault_support import DEVICE_A, DEVICE_B, VaultServe, flat
from workspace_support import fixture_login

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.devices import DevicesScreen


async def _open(app, pilot, wait_until) -> DevicesScreen:
    workspace = await fixture_login(app, pilot)
    await workspace.run_action_id("devices")
    await wait_until(pilot, lambda: isinstance(app.screen, DevicesScreen) and app.screen.loaded, "the devices screen")
    return app.screen


def test_the_list_shows_name_registered_last_used_and_revoked(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe())
        serve_app.register_device(_serve.USER, DEVICE_A, "work laptop")
        serve_app.register_device(_serve.USER, DEVICE_B, "old phone")
        serve_app.devices[(_serve.USER, DEVICE_B)].update(revoked_at="2026-10-30T09:00:00Z", revoke_reason="user")
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            await _open(app, pilot, wait_until)
            text = screen_text(app)
            for words in ("work laptop", "old phone", "2026-10-29T08:00:00Z", "revoked 2026-10-30T09:00:00Z"):
                assert words in text, words
            assert DEVICE_A not in text, "the device id is a key, not a label"

    asyncio.run(check())


def test_revoke_asks_once_and_says_what_it_does_not_stop(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe())
        serve_app.register_device(_serve.USER, DEVICE_A, "work laptop")
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            screen = await _open(app, pilot, wait_until)
            screen.query_one("#devices-table", DataTable).move_cursor(row=0)
            screen.query_one("#dev-revoke", Button).press()
            await wait_until(pilot, lambda: "change that password" in flat(screen_text(app)), "the confirmation")
            text = flat(screen_text(app))
            assert "work laptop" in text
            assert "does not stop someone who knows your TSB password" in text and "change that password" in text
            app.screen.query_one("#revoke-keep", Button).press()
            await wait_until(pilot, lambda: app.screen is screen, "back on the list")
            assert not serve_app.calls("DELETE", f"/devices/{DEVICE_A}")
            screen.query_one("#dev-revoke", Button).press()
            await wait_until(pilot, lambda: bool(app.screen.query("#revoke-confirm")), "the confirmation")
            app.screen.query_one("#revoke-confirm", Button).press()
            await wait_until(pilot, lambda: "revoked_at" in serve_app.devices[(_serve.USER, DEVICE_A)], "the revocation")
            await wait_until(pilot, lambda: app.screen is screen and "revoked 2026-10-29T08:00:00Z" in screen_text(app),
                             "the list shows it revoked")

    asyncio.run(check())


def test_a_refused_revoke_is_shown(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe())
        serve_app.register_device(_serve.USER, DEVICE_A, "work laptop")
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            screen = await _open(app, pilot, wait_until)
            screen.query_one("#devices-table", DataTable).move_cursor(row=0)
            del serve_app.devices[(_serve.USER, DEVICE_A)]      # gone between the list and the revoke
            screen.query_one("#dev-revoke", Button).press()
            await wait_until(pilot, lambda: bool(app.screen.query("#revoke-confirm")), "the confirmation")
            app.screen.query_one("#revoke-confirm", Button).press()
            await wait_until(pilot, lambda: "no such device" in screen_text(app), "the core's message")

    asyncio.run(check())
