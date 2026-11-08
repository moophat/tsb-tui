"""Drive target writes submit the same target form against the contract fixture server."""
from __future__ import annotations

import asyncio

import pytest
from drive_support import Rig
from textual.widgets import Input

from tsb_tui.screens.target_form import TargetFormScreen


async def create(rig, **fields):
    # target_new's id is also its correlation id, so execute through the real runner with
    # a string id rather than Rig.cmd's generated numeric correlation id.
    return await rig.app_call(rig.driver.execute, {"op": "target_new", **fields})


def test_drive_target_new_edit_and_delete(serve, tmp_path):
    """Partial edits keep absent fields, null clears optionals, and a confirmed delete answers its id."""
    async def check():
        server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            made = await create(rig, id="drive-target", type="ssh", host="router.example", user="operator",
                                tags=["prod"], name="Before", group="core", port=2222)
            assert made["ok"], made
            assert made["id"] == "drive-target" and made["target"] == server.targets["drive-target"]
            changed = await rig.cmd("target_edit", target="drive-target", name="After")
            assert changed["ok"], changed
            assert changed["target"]["name"] == "After"
            assert server.target_definitions["drive-target"]["user"] == "operator"
            assert server.target_definitions["drive-target"]["port"] == 2222
            cleared = await rig.cmd("target_edit", target="drive-target", name=None, group=None, tags=None,
                                    port=None, user=None)
            assert cleared["ok"], cleared
            assert server.target_definitions["drive-target"] == {"id": "drive-target", "type": "ssh", "host": "router.example"}
            assert cleared["target"]["port"] == 22
            before = list(server.requests)
            refused = await rig.cmd("target_delete", target="drive-target")
            assert not refused["ok"] and refused["error"] == "bad_request"
            assert not any(m == "DELETE" for m, _, _ in server.requests[len(before):])
            deleted = await rig.cmd("target_delete", target="drive-target", confirm=True)
            assert deleted["ok"] and deleted["target"] == "drive-target"
            assert "drive-target" not in server.targets
    asyncio.run(check())


def test_drive_duplicate_and_forbidden_return_core_errors(serve, tmp_path):
    """Errors retain the core's code/message; a duplicate leaves its form available for correction."""
    async def check():
        _, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            duplicate = await create(rig, id="normal", type="ssh", host="example")
            assert not duplicate["ok"] and duplicate["error"] == "target_id_taken"
            assert duplicate["why"] == "a source already holds that target id"
            assert await rig.app_call(lambda: isinstance(rig.app.screen, TargetFormScreen))
            assert await rig.app_call(lambda: rig.app.screen.query_one("#target-host", Input).value) == "example"
            edit = await rig.cmd("target_edit", target="normal", name="No")
            assert not edit["ok"] and edit["error"] == "forbidden"
            assert edit["why"].startswith("This target comes from ")
            delete = await rig.cmd("target_delete", target="normal", confirm=True)
            assert delete["error"] == "forbidden" and delete["why"] == edit["why"]
    asyncio.run(check())


@pytest.mark.parametrize("field", ["host", "type"])
def test_drive_edit_null_required_field_sends_nothing(serve, tmp_path, field):
    """Null required fields fail before even reading a definition."""
    async def check():
        server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            before = len(server.requests)
            result = await rig.cmd("target_edit", target="normal", **{field: None})
            assert result["error"] == "bad_request"
            assert not any(p.startswith("/targets") for _, p, _ in server.requests[before:])
    asyncio.run(check())


def test_drive_switch_to_telnet_drops_account(serve, tmp_path):
    """Changing protocol through drive behaves like changing the form's radio button."""
    async def check():
        server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            assert (await create(rig, id="switch-type", type="ssh", host="example", user="account"))["ok"]
            changed = await rig.cmd("target_edit", target="switch-type", type="telnet")
            assert changed["ok"], changed
            assert changed["target"]["type"] == "telnet"
            assert "user" not in server.target_definitions["switch-type"]
    asyncio.run(check())


def test_drive_edit_keeps_empty_optional_values_until_null_clears_them(serve, tmp_path):
    """An absent edit field keeps even an explicitly empty account or tag list; null removes the key."""
    async def check():
        server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            assert (await create(rig, id="empty-optionals", type="ssh", host="example", user="", tags=[]))["ok"]
            edited = await rig.cmd("target_edit", target="empty-optionals", name="Changed")
            assert edited["ok"], edited
            assert server.target_definitions["empty-optionals"]["user"] == ""
            assert server.target_definitions["empty-optionals"]["tags"] == []
            cleared = await rig.cmd("target_edit", target="empty-optionals", user=None, tags=None)
            assert cleared["ok"], cleared
            assert "user" not in server.target_definitions["empty-optionals"]
            assert "tags" not in server.target_definitions["empty-optionals"]
    asyncio.run(check())


def test_drive_target_writes_while_locked_send_nothing_and_queue_no_form(serve, tmp_path):
    """Each target write refuses a locked screen before any read, mutation or queued form."""
    from tsb_tui.screens.lock import LockScreen

    async def check():
        server, url = serve()
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.app_call(rig.app.screen_lock.lock)
            await rig.pilot.pause()
            lock = await rig.app_call(lambda: rig.app.screen)
            assert isinstance(lock, LockScreen)
            before = list(server.requests)
            for command in (
                {"op": "target_new", "id": "locked-target", "type": "ssh", "host": "example"},
                {"op": "target_edit", "target": "normal", "name": "Must not read"},
                {"op": "target_delete", "target": "normal", "confirm": True},
            ):
                result = await rig.app_call(rig.driver.execute, command)
                assert result["ok"] is False and result["error"] == "not_available", result
                assert "screen is locked" in result["why"], result
                assert await rig.app_call(lambda: rig.app.screen) is lock
                assert await rig.app_call(lambda: len(rig.app.screen_lock._held)) == 0
                assert await rig.app_call(lambda: len(rig.app.screen_lock._after)) == 0
                assert server.requests == before, "a locked target command sent a request to TSB"
            await rig.app_call(lock._resumed, rig.app._login_gen)
            await rig.pilot.pause()
            assert not await rig.app_call(lambda: isinstance(rig.app.screen, TargetFormScreen))
            assert "locked-target" not in server.targets

    asyncio.run(check())
