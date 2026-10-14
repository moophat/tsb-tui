"""Two drivers on one session: the owner's brake, headless (design §2.9.6; typing-ownership steps 5-10, 14).

Why it matters (Rule 9): this is the scenario the whole agent model rests on. The owner lends a session and hands the
agent the keyboard; the owner can take it back at any moment, even mid-command, and from that instant the agent's
input must not reach the device. Two layers can stop it: drive's own guard (`input.guard`, Frontend §9) and the core's
refusal. D52 was ruled on 2026-10-12: the guard stays on by default in every profile, so a rig that wants to test the
core's refusal switches it off explicitly (`--set input.guard=false`), and both settings are tested here. In both, the
owner's watching client must send nothing of its own: a query reply or a focus report from a client that does not
hold the keyboard would take it, or arm a false quiet prompt (DT4 b). Servers: the contract's in-process fixture
server, whose two-client scenario moves the token on hand-off and on the owner's typing.
"""

from __future__ import annotations

import asyncio

import pytest
from drive_support import Rig
from input_server import InputRecordingApp, make_session
from tsbc import _serve

DSR = b"\x1b[6n"  # the session asks where the cursor is


def _emit_output(serve_app, sid: str, data: bytes) -> None:
    with serve_app.lock:
        serve_app._emit_output(serve_app.sessions[sid], data)


async def _until(predicate, what: str) -> None:
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.mark.parametrize("guard", ["false", "true"])
def test_the_owner_takes_the_keyboard_back_and_the_agents_next_input_is_stopped(serve, tmp_path, guard) -> None:
    """The §2.9.6 script, both guard settings. With the guard off the agent's `type` reaches the core and is refused
    there with `input_refused` `not_typist` (the core is the control, the guard only a guardrail); with it on, drive
    answers `not_typist`, `sent: false`, and the server records nothing from the agent. Either way the owner's Ctrl-C
    reached the device and the owner holds the keyboard."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as owner, Rig(
                url, tmp_path=tmp_path, profile="harness", overrides=(f"input.guard={guard}",)) as agent:
            await owner.login(_serve.USER)
            await owner.attach(sid)
            await agent.login(_serve.PEER)
            assert (await agent.attach(sid))["you"] == "view_only"
            owner_client = owner.app.workspace().slot_for(sid).pump.state.client_id
            agent_client = agent.app.workspace().slot_for(sid).pump.state.client_id

            handed = await owner.cmd("hand", to=_serve.PEER)
            assert handed["to"] == {"user": _serve.PEER, "client_id": agent_client}
            assert (await agent.cmd("wait", until={"holder": "me"}, timeout_ms=5000))["ok"]
            token = next(e for e in agent.events("token") if e["holder"] == "me")
            assert token["reason"] == "handoff" and token["from"] == _serve.USER

            assert (await agent.cmd("type", text="ping 198.51.100.77 repeat 100000\r"))["ok"]
            assert (await agent.cmd("wait", until={"reply": "ping 198.51.100.77"}, timeout_ms=5000))["ok"]
            assert (await owner.cmd("wait", until={"screen": "ping 198.51.100.77"}, timeout_ms=5000))["ok"]

            # The owner's client watches now: a query the session asks must be answered by the holder alone.
            _emit_output(serve_app, sid, DSR)
            await _until(lambda: any(chunk.endswith(b"R") for chunk in serve_app.received(agent_client)),
                         "the holder to answer the query")
            await owner.cmd("wait", until={"settled_ms": 150})
            assert serve_app.received(owner_client) == [], "the owner's watching client sent input"

            assert (await owner.cmd("keys", keys=["ctrl+c"]))["ok"]
            assert (await agent.cmd("wait", until={"holder": "not_me"}, timeout_ms=2000))["ok"]
            assert (await owner.cmd("wait", until={"holder": "me"}, timeout_ms=2000))["ok"]
            assert b"\x03" in b"".join(serve_app.received(owner_client)), "the Ctrl-C did not reach the core"

            before = list(serve_app.received(agent_client))
            typed = await agent.cmd("type", text="x")
            if guard == "false":
                assert typed["ok"] is True and typed["queued"] == 1
                refused = await agent.cmd(
                    "wait", until={"event": {"ev": "input_refused", "reason": "not_typist"}}, timeout_ms=2000)
                assert refused["ok"] is True, refused
                assert serve_app.received(agent_client) == [*before, b"x"], "the core never saw the agent's input"
            else:
                assert typed["ok"] is False and typed["error"] == "not_typist" and typed["sent"] is False
                assert typed["holder"] == _serve.USER
                await agent.cmd("wait", until={"settled_ms": 150})
                assert serve_app.received(agent_client) == before, "the guard let the agent's input through"
                assert not agent.events("input_refused"), "the core was asked, so the guard was not what stopped it"
            assert not any(chunk == b"x" for chunk in serve_app.received(owner_client))

    asyncio.run(check())


def test_hand_back_gives_the_keyboard_to_the_owners_client_or_says_why_it_cannot(serve, tmp_path) -> None:
    """D54: `hand_back` hands the keyboard to the owner's attached client. With none attached the answer says so and
    offers the two honest ways out (a holder that is not the owner may not put the keyboard aside); with two it asks
    which, by client id; a client that does not hold the keyboard cannot hand it on."""

    async def check() -> None:
        _app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as agent:
            await agent.login(_serve.PEER)
            await agent.attach(sid)
            lonely = await agent.cmd("hand_back")
            assert lonely["error"] == "owner_not_attached" and lonely["options"][0].startswith("detach:")
            assert _serve.USER in lonely["why"]
            async with Rig(url, tmp_path=tmp_path) as owner:
                await owner.login(_serve.USER)
                await owner.attach(sid)
                owner_client = owner.app.workspace().slot_for(sid).pump.state.client_id
                assert agent.app.workspace().slot_for(sid).pump.state.holds_keyboard, "the agent attached first"
                async with Rig(url, tmp_path=tmp_path) as second:
                    await second.login(_serve.USER)
                    await second.attach(sid)
                    second_client = second.app.workspace().slot_for(sid).pump.state.client_id
                    several = await agent.cmd("hand_back")
                    assert several["error"] == "choose_client"
                    assert sorted(several["clients"]) == sorted([owner_client, second_client])
                    answer = await agent.cmd("hand_back", to=second_client)
                    assert answer["ok"] is True
                    assert answer["to"] == {"user": _serve.USER, "client_id": second_client}
                    assert (await second.cmd("wait", until={"holder": "me"}))["ok"]
                    moved = next(e for e in agent.events("token") if e["holder"] != "me")
                    assert moved["holder"]["client_id"] == second_client
                    assert "your_input" not in moved, "the agent handed it over itself: no news"
                    refused = await agent.cmd("hand", to=owner_client)
                    assert refused["error"] == "not_typist", "a client that does not hold the keyboard cannot hand it"

    asyncio.run(check())


def test_hand_asks_the_action_table_and_the_core_has_the_last_word(serve, tmp_path) -> None:
    """The Who's here buttons are enabled by `model/actions.py`: handing to a client that is not there, or to oneself,
    is not sent. A hand-off the core refuses (`view_only`: the receiver's right is read-only) answers `refused` with the
    core's reason."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as owner, Rig(url, tmp_path=tmp_path) as viewer:
            await owner.login(_serve.USER)
            await owner.attach(sid)
            await viewer.login(_serve.VIEWER)
            await viewer.attach(sid)
            owner_client = owner.app.workspace().slot_for(sid).pump.state.client_id
            viewer_client = viewer.app.workspace().slot_for(sid).pump.state.client_id
            frames = len(serve_app.frames)
            assert (await owner.cmd("hand", to="nobody-here"))["error"] == "no_such_client"
            assert (await owner.cmd("hand", to=owner_client))["error"] == "no_such_client", "this client is no target"
            assert len(serve_app.frames) == frames, "something was sent for a hand-off the table does not allow"
            refused = await owner.cmd("hand", to=viewer_client)
            assert refused["error"] == "refused" and refused["reason"] == "view_only", refused
            assert (await viewer.cmd("hand", to=owner_client))["error"] == "not_typist"

    asyncio.run(check())
