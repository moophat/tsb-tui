"""Mount and initial focus on the attach confirm gate screen (issue #11, issue #12).

Tests:
1. Pushed with a first-use host key, the screen mounts with no exception and focus
   lands on the host-key checkbox (#hostkey-ack).
2. Pushed without a host key, focus lands on the Attach button (#attach-button).
3. Under drive, `new` on a target with a first-use host key answers not_available
   with the fingerprint, and drive keeps running (a subsequent `sessions` command answers).
"""

from __future__ import annotations

import asyncio

from drive_support import Rig
from stub_server import HOSTKEY, SlowChunkedApp, StubServer
from textual.widgets import Button, Checkbox

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.attach_confirm import AttachConfirmScreen

SIZED = ("size.auto_request=true",)
SESSION = {"id": "session-1", "target_id": "target-1", "state": "running"}


def test_gate_screen_pushed_with_first_use_hostkey_mounts_and_focuses_checkbox(serve) -> None:
    session = {"id": "session-1", "target_id": "target-1", "state": "running"}
    hostkey = {"fingerprint": HOSTKEY["fingerprint"], "first_use": True}
    screen = AttachConfirmScreen(session, hostkey)
    # on_mount must not call query_one synchronously before children are mounted
    screen.on_mount()

    async def _check() -> None:
        _serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test() as pilot:
            gate = AttachConfirmScreen(session, hostkey)
            app.push_screen(gate)
            await pilot.pause()
            assert app._exception is None
            assert isinstance(app.screen, AttachConfirmScreen)
            assert app.focused is not None
            assert app.focused.id == "hostkey-ack"
            assert isinstance(app.focused, Checkbox)

    asyncio.run(_check())


def test_gate_screen_pushed_without_hostkey_mounts_and_focuses_attach_button(serve) -> None:
    session = {"id": "session-1", "target_id": "target-1", "state": "running"}
    screen = AttachConfirmScreen(session, hostkey=None)
    # on_mount must not call query_one synchronously before children are mounted
    screen.on_mount()

    async def _check() -> None:
        _serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test() as pilot:
            gate = AttachConfirmScreen(session, hostkey=None)
            app.push_screen(gate)
            await pilot.pause()
            assert app._exception is None
            assert isinstance(app.screen, AttachConfirmScreen)
            assert app.focused is not None
            assert app.focused.id == "attach-button"
            assert isinstance(app.focused, Button)

    asyncio.run(_check())


def test_drive_new_on_first_use_hostkey_keeps_drive_running(tmp_path) -> None:
    async def _check() -> None:
        stub = SlowChunkedApp(hostkey_on_create=True, recording_on_create=True)
        with StubServer(stub) as url:
            async with Rig(url, tmp_path=tmp_path, overrides=SIZED) as rig:
                await rig.login()
                created = await rig.cmd("new", target="stub-target")
                assert created["ok"] is False and created["error"] == "not_available", created
                assert created["fingerprint"] == HOSTKEY["fingerprint"]
                sessions = await rig.cmd("sessions")
                assert sessions["ok"], sessions
                assert rig.app._exception is None

    asyncio.run(_check())
