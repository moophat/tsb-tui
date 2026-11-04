"""Device-login pilot helpers. Answers exist only in memory."""
from contextlib import asynccontextmanager

from stub_server import DeviceLoginApp, StubServer
from textual.widgets import Input
from workspace_support import fixture_login, open_new_session

from tsb_tui.app import TsbTuiApp


def password(prompt_id=1, attempt=1):
    return {"t": "device_login_prompt", "v": 1, "method": "password",
            "prompt_id": prompt_id, "attempt": attempt}


def code(prompt_id=2, attempt=1, *, text="Code:", echo=False):
    return {"t": "device_login_prompt", "v": 1, "method": "keyboard-interactive",
            "prompt_id": prompt_id, "attempt": attempt, "name": "Device", "instruction": "",
            "prompts": [{"text": text, "echo": echo}]}


@asynccontextmanager
async def opened(wait_until, *, rounds=None, recording=False, login_input=False,
                 outcome="connected", hostkey=False, **kwargs):
    stub = DeviceLoginApp(rounds=rounds if rounds is not None else [password()],
                          outcome=outcome, hostkey=hostkey, login_input=login_input,
                          recording_on_create=recording, **kwargs)
    with StubServer(stub) as url:
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            dialog = await open_new_session(app, pilot)
            dialog._types["normal"] = "ssh"
            dialog.query_one("#target-id", Input).value = "normal"
            await pilot.pause()
            await wait_until(pilot, lambda: stub.me_reads > 0, "login recording settings")
            await pilot.pause()
            try:
                yield app, pilot, screen, dialog, stub
            finally:
                stub.finish_gate.set()


async def pane_for(dialog, pilot, wait_until, *, consent=True):
    dialog.query_one("#device-login-start").press()
    await wait_until(pilot, lambda: dialog.app.workspace().front is not None
                     and bool(dialog.app.workspace().front.device_login), "device login slot")
    pane = dialog.app.workspace().front.device_login
    await wait_until(pilot, lambda: pane.is_mounted, "device login pane")
    if consent and pane.query_one("#login-consent").display:
        pane.query_one("#login-consent").press()
        await pilot.pause()
    return pane


async def start(dialog, pilot, wait_until):
    pane = await pane_for(dialog, pilot, wait_until)
    pane.query_one("#device-user", Input).value = "fixture-account"
    await pane.submit_user()
    await wait_until(pilot, lambda: pane.prompt is not None or pane.hostkey_pending, "device question")
    return pane


async def answer(pane, pilot, wait_until, value="", *, remember=False):
    if remember:
        pane.query_one("#device-remember").value = True
        await pilot.pause()
    boxes = list(pane.query(".device-answer"))
    for box in boxes:
        box.value = value
    await pane.send_answers()
    assert all(box.value == "" for box in boxes), "a sent answer remains in a widget"
    await pilot.pause()
