"""drive's `targets` and `new` (E3 phase 11, section E): the commands U11's drive rig uses to open a telnet session from
the TUI.

Why it matters (Rule 9): an agent has no eyes, so what it learns about the inventory is exactly what `targets`
answers, and every string in it comes from a CMDB (R17): a control character in an answer is one an agent's own
terminal or log could take for a command. `new` is the only way it can open a session, and for a telnet target a reuse
would be a 502 it could not explain, so it must ask `connection: new` as the dialog does. Server: the contract's
in-process fixture server.
"""

from __future__ import annotations

import asyncio
import unicodedata

from drive_support import Rig
from tsbc import _serve
from tsbc._serve import ServeApp

from tsb_tui.screens.attach_confirm import AttachConfirmScreen

# A drive profile sends no size request on its own (D53), so a create, which carries the creator's size, needs auto size
# turned on explicitly, as the dialog does.
SIZED = ("size.auto_request=true",)
FINGERPRINT = "SHA256:FIXTUREFIRSTUSEHOSTKEY000000000000000000000"


class _FirstUseHostKey(ServeApp):
    """The fixture app, answering a create with a first-use host key as the core does for a target it has never seen."""

    def _create(self, user, body, cookie=""):
        status, headers, reply = super()._create(user, body, cookie)
        if status == 201:
            reply = {**reply, "hostkey": {"fingerprint": FINGERPRINT, "first_use": True}}
        return status, headers, reply


def test_drive_targets_lists_and_new_opens_a_telnet_session(serve, tmp_path) -> None:
    """`targets` answers the rows the tab would show for a query (ids, types, in id order, the broker filtering), every
    string through `as_text`; `new` on `telnet-scenario` creates with `connection: new` and no `connection_id`, then
    attaches as `attach` does, the `gate` event first because the session is recorded."""

    async def check() -> None:
        serve_app, url = serve()
        serve_app.targets["zz-escape"] = {**_serve.TARGETS["normal"], "id": "zz-escape",
                                          "name": "bad\x1b[2Jname", "tags": ["t\x07"]}
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED) as rig:
            await rig.login()
            listed = await rig.cmd("targets")
            assert listed["ok"]
            ids = [row["id"] for row in listed["targets"]]
            assert ids == sorted([*_serve.TARGETS, "zz-escape"])
            by_id = {row["id"]: row for row in listed["targets"]}
            assert by_id["telnet-scenario"]["type"] == "telnet"
            assert set(by_id["rich-name"]) == {"id", "type", "name", "group", "tags", "discovered_by"}
            assert by_id["rich-name"]["name"] == "[bold red]rich[/bold red]"
            assert by_id["zz-escape"]["name"] == "bad␛[2Jname" and by_id["zz-escape"]["tags"] == ["t␇"]
            assert not any(unicodedata.category(ch) == "Cc" for row in listed["targets"]
                           for value in row.values() for ch in str(value))

            telnet = await rig.cmd("targets", query="type:telnet")
            assert [row["id"] for row in telnet["targets"]] == ["telnet-scenario"]

            before = len(rig.lines)
            opened = await rig.cmd("new", target="telnet-scenario")
            assert opened["ok"], opened
            created = [body for method, path, body in serve_app.requests if (method, path) == ("POST", "/sessions")]
            assert len(created) == 1 and created[0]["target_id"] == "telnet-scenario"
            assert created[0]["connection"] == "new" and "connection_id" not in created[0]
            assert {"session", "chip", "front", "you", "holder", "you_may_type", "size"} <= set(opened)
            assert opened["chip"] == 1 and opened["front"] is True
            ev_names = [line["ev"] for line in rig.lines[before:] if "ev" in line]
            assert ev_names and ev_names[0] == "gate", ev_names

    asyncio.run(check())


def test_attach_after_new_cannot_pass_the_hostkey_gate_without_the_tick(serve, tmp_path) -> None:
    """`new` on a target whose key is seen for the first time leaves the session created and the gate up: `attach`
    must open that same gate with the fingerprint and Attach greyed, exactly as the TUI's own attach does, and an
    agent that never ticks it must not be attached — a gate an agent can walk through protects nobody."""

    async def check() -> None:
        _app, url = serve(_FirstUseHostKey())
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED) as rig:
            await rig.login()
            created = await rig.cmd("new", target="normal")
            assert created["ok"] is False and created["error"] == "not_available", created
            assert created["fingerprint"] == FINGERPRINT
            sid = created["session"]

            again = await rig.cmd("attach", session=sid)
            assert again["ok"] is False and again["error"] == "not_available", again
            assert again["fingerprint"] == FINGERPRINT
            gate = await rig.app_call(lambda: rig.app.screen)
            assert isinstance(gate, AttachConfirmScreen), gate
            assert gate.query_one("#attach-button").disabled, "Attach was open before the key was ticked"
            assert FINGERPRINT in str(gate.query_one("#attach-hostkey-notice").render())
            assert await rig.app_call(lambda: rig.app.workspace().slot_for(sid)) is None, "attached without the tick"

            await rig.cmd("press", keys=["space"])  # the tick (the checkbox has focus, so Enter would toggle it)
            await rig.cmd("press", keys=["tab"])  # the Attach button
            await rig.cmd("press", keys=["enter"])
            attached = await rig.attach(sid)
            assert attached["ok"]

    asyncio.run(check())


def test_an_accepted_hostkey_gate_is_not_asked_again_on_a_later_attach(serve, tmp_path) -> None:
    """The ruling of 2026-10-22: a first-use key is acknowledged once. Once the operator has ticked the gate and the
    session attached, a detach and a later attach of that same session by this driver asks no second
    acknowledgement — the key has been checked; a gate re-asked at every reattach trains the operator to tick
    without looking, which is the gate's whole purpose lost."""

    async def check() -> None:
        _app, url = serve(_FirstUseHostKey())
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED) as rig:
            await rig.login()
            created = await rig.cmd("new", target="normal")
            assert created["ok"] is False and created["error"] == "not_available", created
            sid = created["session"]
            assert created["fingerprint"] == FINGERPRINT

            await rig.cmd("press", keys=["space"])  # the tick (the checkbox has focus, so Enter would toggle it)
            await rig.cmd("press", keys=["tab"])     # the Attach button
            await rig.cmd("press", keys=["enter"])   # Attach: the gate is accepted, and this path attaches
            for _ in range(300):
                slot = await rig.app_call(lambda: rig.app.workspace().slot_for(sid))
                if slot is not None and slot.pump is not None and slot.pump.state.client_id is not None:
                    break
                await rig.pilot.pause(0.01)
            slot = await rig.app_call(lambda: rig.app.workspace().slot_for(sid))
            assert slot is not None and slot.pump is not None and slot.pump.state.client_id is not None, (
                "the accepted gate never attached the session")

            detached = await rig.cmd("detach", session=sid)
            assert detached["ok"], detached
            for _ in range(200):
                if await rig.app_call(lambda: rig.app.workspace().slot_for(sid)) is None:
                    break
                await rig.pilot.pause(0.01)
            assert await rig.app_call(lambda: rig.app.workspace().slot_for(sid)) is None, "the detach left it open"

            again = await rig.cmd("attach", session=sid)
            assert again["ok"], f"a reattach asked for the host key again: {again}"
            assert "fingerprint" not in again, f"the gate was asked a second time: {again}"
            gate = await rig.app_call(lambda: rig.app.screen)
            assert not isinstance(gate, AttachConfirmScreen), f"the host-key gate was shown again: {gate}"

    asyncio.run(check())


def test_drive_new_and_targets_refuse_what_the_dialog_would(serve, tmp_path) -> None:
    """A refused create answers with the core's own code and message, a query the broker cannot answer is `bad_request`
    naming the fields. Auto size being off still lets the creator send the window size."""

    async def check() -> None:
        serve_app, url = serve()
        async with Rig(url, tmp_path=tmp_path, overrides=SIZED) as rig:
            assert (await rig.cmd("targets"))["error"] == "not_logged_in"
            assert (await rig.cmd("new", target="normal"))["error"] == "not_logged_in"
            await rig.login()
            bad = await rig.cmd("targets", query="cisco")
            assert bad["error"] == "bad_request" and "type:" in bad["why"]
            refused = await rig.cmd("new", target="no-such-device")
            assert not refused["ok"] and refused["error"] != "internal" and refused["why"]
            assert (await rig.cmd("new"))["error"] == "bad_request"
        creates = [b for m, p, b in serve_app.requests if (m, p) == ("POST", "/sessions")]
        async with Rig(url, tmp_path=tmp_path) as plain:
            await plain.login()
            off = await plain.cmd("new", target="normal")
            assert off["ok"], off
            sent = [b for m, p, b in serve_app.requests if (m, p) == ("POST", "/sessions")]
            assert len(sent) == len(creates) + 1
            assert (sent[-1]["cols"], sent[-1]["rows"]) == (116, 39)

    asyncio.run(check())
