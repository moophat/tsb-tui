"""Take back (design §2.9.3, D45, mockup A1): the owner takes the keyboard back from an agent in one action.

Why it matters (Rule 9): "An agent works in your sessions without your credentials, and you take the keyboard back in one
action" is the point of the whole rail for the person who lent a session. `t` on a sidebar row must bring the session to
the front, attach first when this TUI is not attached (past the gate, session-attach CR1), then send `handoff` to this
client's own id (typing-ownership step 9: the take-over without bytes, token reason `takeover`), and say whose keyboard
it took and how to give it back. A take back that sent anything before the gate, that handed the keyboard to nobody, or
that offered itself to a login that does not own the session would each be a wrong action on a production session.
Servers: the contract's in-process fixture server (DT7); a peer login attached first holds the keyboard, as an agent does.
"""

from __future__ import annotations

import asyncio

from input_server import InputRecordingApp, other_client
from tsbc import _serve
from workspace_support import (
    fixture_login,
    named_sessions,
    open_here,
    select_in_sidebar,
)

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.attach_confirm import AttachConfirmScreen


def _handoffs(serve_app) -> list[dict]:
    return [f for f in list(serve_app.frames) if f.get("t") == "handoff"]


def _notes(app) -> list[tuple[str, str]]:
    return [(n.title, n.message) for n in app._notifications._notifications.values()]


def test_t_on_a_row_open_here_takes_the_keyboard_and_says_whose_it_was_and_how_to_give_it_back(
        serve, wait_until) -> None:
    """D45, step 9, mockup A1: the session comes to the front, `handoff` goes to this client's own id, the `token`
    frame says `takeover`, the status row reads "you took the keyboard from fixture-peer", and the session's frame says
    "you type now: you took it from fixture-peer" (the one notice; no toast repeats it)."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        other, agent_held = named_sessions(url, serve_app, "rtr-core-01", "k8s-node-07")
        with other_client(url, _serve.PEER, agent_held):
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await fixture_login(app, pilot)
                await open_here(app, pilot, wait_until, agent_held, other)  # the one held by the peer is behind
                state = screen.slot_for(agent_held).pump.state
                assert not state.holds_keyboard and screen.front.session_id == other

                await select_in_sidebar(app, pilot, wait_until, agent_held)
                await pilot.press("t")
                await wait_until(pilot, lambda: state.holds_keyboard, "the keyboard to come to this client")
                assert screen.front.session_id == agent_held, "Take back brings the session to the front"
                assert _handoffs(serve_app) == [{"t": "handoff", "v": 1, "to": state.client_id}]
                assert state.token_history[-1]["reason"] == "takeover"
                await wait_until(pilot, lambda: "you took the keyboard from fixture-peer" in screen.status.text,
                                 "the status row to say whose keyboard it took")
                assert "you type" in str(screen.query_one("#canvases").border_title)  # the pill's fact is the frame's title
                # One notice, not two: the frame says whose it was; no toast repeats it.
                notice = str(screen.query_one("#canvases").border_subtitle)
                assert "you type now: you took it from fixture-peer" in notice, notice
                assert not any(t.startswith("keyboard") for t, _ in _notes(app)), _notes(app)

    asyncio.run(_check())


def test_t_on_a_session_not_open_here_shows_the_gate_then_attaches_then_takes_it(serve, wait_until) -> None:
    """D45: "attaching first when needed". Nothing is attached and nothing is sent before the gate is passed; the
    attach follows, and only then the `handoff`, so the take-over is never sent to a session this client is not in."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (held,) = named_sessions(url, serve_app, "k8s-node-07")
        with other_client(url, _serve.PEER, held):
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await fixture_login(app, pilot)
                await select_in_sidebar(app, pilot, wait_until, held)
                await pilot.press("t")
                await wait_until(pilot, lambda: isinstance(app.screen, AttachConfirmScreen), "the gate to show first")
                assert len(serve_app.clients[held]) == 1 and _handoffs(serve_app) == [], (
                    "something was attached or sent before the gate")

                await pilot.click("#attach-button")
                await wait_until(pilot, lambda: screen.slot_for(held) is not None and screen.slot_for(held).pump
                                 is not None and screen.slot_for(held).pump.state.holds_keyboard,
                                 "the session to attach and the keyboard to come")
                state = screen.slot_for(held).pump.state
                assert _handoffs(serve_app) == [{"t": "handoff", "v": 1, "to": state.client_id}]
                assert state.token_history[-1]["reason"] == "takeover"
                assert screen.front.session_id == held

    asyncio.run(_check())


def test_with_take_back_brings_to_front_off_the_session_attaches_in_the_background(
        serve, monkeypatch, wait_until) -> None:
    """`watch.take_back_brings_to_front = false`: the keyboard comes back, and the operator's front session stays
    where it was, so a take back from the sidebar never pulls the screen out from under what they are typing."""
    monkeypatch.setenv("TSB_TUI_WATCH_TAKE_BACK_BRINGS_TO_FRONT", "false")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        mine, held = named_sessions(url, serve_app, "rtr-core-01", "k8s-node-07")
        with other_client(url, _serve.PEER, held):
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await fixture_login(app, pilot)
                await open_here(app, pilot, wait_until, mine)
                app.gated_sessions.add(held)
                await select_in_sidebar(app, pilot, wait_until, held)
                await pilot.press("t")
                await wait_until(pilot, lambda: screen.slot_for(held) is not None and screen.slot_for(held).pump
                                 is not None and screen.slot_for(held).pump.state.holds_keyboard,
                                 "the keyboard to come back in the background")
                assert screen.front.session_id == mine, "the front session did not change"

    asyncio.run(_check())


def test_a_login_that_does_not_own_the_session_cannot_take_back_and_nothing_is_attached(serve, wait_until) -> None:
    """Typing-ownership step 12: only an owner's client may take over (the core would answer `not_allowed`). The sidebar
    says so before attaching, so a non-owner does not join a session to be refused."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (theirs,) = named_sessions(url, serve_app, "k8s-node-07")  # owned by the fixture user
        with other_client(url, _serve.USER, theirs):
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await fixture_login(app, pilot, _serve.PEER)
                screen.fleet.filter_mode = "all"
                await select_in_sidebar(app, pilot, wait_until, theirs)
                await pilot.press("t")
                await wait_until(pilot, lambda: any("only the owner's clients can take the keyboard" in m
                                                    for _, m in _notes(app)), "the refusal to be said")
                assert screen.slots == [] and len(serve_app.clients[theirs]) == 1
                assert not isinstance(app.screen, AttachConfirmScreen) and _handoffs(serve_app) == []

    asyncio.run(_check())


def test_the_leader_t_takes_back_the_session_in_front(serve, wait_until) -> None:
    """D45: `^\\ t` with the session in front is the same action as the sidebar's `t`: one `handoff` to this client's
    own id, no bytes typed into the session."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (held,) = named_sessions(url, serve_app, "k8s-node-07")
        with other_client(url, _serve.PEER, held):
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await fixture_login(app, pilot)
                await open_here(app, pilot, wait_until, held)
                screen.close_sidebar()
                state = screen.pump.state
                await pilot.press("ctrl+backslash", "t")
                await wait_until(pilot, lambda: state.holds_keyboard, "the keyboard to come back")
                assert _handoffs(serve_app) == [{"t": "handoff", "v": 1, "to": state.client_id}]
                assert serve_app.received(state.client_id) == []

    asyncio.run(_check())
