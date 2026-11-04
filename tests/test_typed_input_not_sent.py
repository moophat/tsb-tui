"""Typed bytes lost during attachment closure are dropped visibly, never replayed."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from drive_support import Rig
from input_server import InputRecordingApp, attached, flushed, make_session
from textual import events
from tsbc import _serve

from tsb_tui._transport import ConnectionClosed
from tsb_tui.app import TsbTuiApp
from tsb_tui.model.attachment import AttachmentPump
from tsb_tui.screens.terminal import KIND_INPUT_DROPPED, SOURCE_FOCUS, SOURCE_REPLY

NOTE = "Not sent: reconnecting to TSB. Type it again once the session is back."


@pytest.mark.parametrize("source", ["key", "paste"])
@pytest.mark.parametrize("failure", ["closed", "unattached", "closing"])
def test_typed_key_is_counted_logged_and_shown_then_never_replayed(
        serve, wait_until, monkeypatch, failure, source) -> None:
    async def check() -> None:
        server, url = serve(InputRecordingApp())
        sid = make_session(url)
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 30)) as pilot:
            app.client.login(_serve.USER, _serve.PASSWORD)
            screen = await attached(pilot, app, sid, wait_until)
            pump = screen.pump
            att = pump._att
            client_id = pump.state.client_id
            original_send = pump.send
            attempted = AsyncMock(side_effect=ConnectionClosed())
            if failure == "closing":
                monkeypatch.setattr(pump, "send", attempted)
            elif failure == "closed":
                pump.state.apply_close(None)
            else:
                pump.state.client_id = None
            # The reconnect warning is visible only during an active retry.
            pump.state.reattach = {"attempt": 1, "due": pump.state.now() + 60, "cause": "detached"}
            before = screen.dropped_inputs
            if source == "key":
                await pilot.press("x")
            else:
                screen.post_message(events.Paste("x"))
                await wait_until(pilot, lambda: screen.dropped_inputs > before, "the paste to be dropped")
            assert NOTE in screen.status.text
            assert screen.dropped_inputs == before + 1
            event = pump.state.log.of_kind(KIND_INPUT_DROPPED)[-1]
            assert event.detail == {"source": source, "bytes": 1,
                                    "reason": "connection_closed" if failure == "closing" else "not_attached"}
            assert attempted.await_count == (1 if failure == "closing" else 0)
            # Use the pump's normal attached-frame application/hook to model recovery on the same socket.
            monkeypatch.setattr(pump, "send", original_send)
            pump._apply_attached(att)
            assert NOTE not in screen.status.text
            await pilot.press("y")
            await flushed(pilot, screen, server, wait_until)
            assert server.received(client_id) == [b"y"], "gap input must never be replayed"

    asyncio.run(check())


@pytest.mark.parametrize("source", [SOURCE_FOCUS, SOURCE_REPLY])
@pytest.mark.parametrize("failure", ["closed", "closing"])
def test_derived_input_remains_silent(serve, wait_until, monkeypatch, source, failure) -> None:
    async def check() -> None:
        server, url = serve(InputRecordingApp())
        sid = make_session(url)
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 30)) as pilot:
            app.client.login(_serve.USER, _serve.PASSWORD)
            screen = await attached(pilot, app, sid, wait_until)
            client_id = screen.pump.state.client_id
            if failure == "closed":
                screen.pump.state.apply_close(None)
            else:
                monkeypatch.setattr(screen.pump, "send", AsyncMock(side_effect=ConnectionClosed()))
            assert not await screen.send_input(source, b"derived")
            assert screen._scroll_notice is None
            assert screen.front.input_notice == ""
            assert NOTE not in screen.status.text
            await flushed(pilot, screen, server, wait_until)
            assert server.received(client_id) == []

    asyncio.run(check())


@pytest.mark.parametrize("missing", [False, True])
def test_pump_send_raises_when_attachment_cannot_send(missing) -> None:
    async def check() -> None:
        pump = AttachmentPump(None, "unused", sink=lambda *_: None)
        att = SimpleNamespace(closed=True, send=AsyncMock())
        pump._att = None if missing else att
        with pytest.raises(ConnectionClosed):
            await pump.send(b"x")
        att.send.assert_not_awaited()

    asyncio.run(check())


@pytest.mark.parametrize("failure", ["closed", "closing", "pump_closed"])
def test_drive_answers_nothing_was_sent(serve, tmp_path, monkeypatch, failure) -> None:
    async def check() -> None:
        server, url = serve(InputRecordingApp())
        sid = make_session(url)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            screen = rig.driver.screen()
            pump = screen.pump
            client_id = pump.state.client_id
            if failure == "closed":
                pump.state.apply_close(None)
            elif failure == "closing":
                monkeypatch.setattr(pump, "send", AsyncMock(side_effect=ConnectionClosed()))
            else:
                monkeypatch.setattr(pump, "_att", SimpleNamespace(closed=True))
            answer = await rig.cmd("type", session=sid, text="x")
            assert answer["ok"] is False, answer
            assert answer["error"] == "not_attached", answer
            assert answer["sent"] is False and "nothing was sent" in answer["why"], answer
            assert "queued" not in answer
            assert server.received(client_id) == []

    asyncio.run(check())


def test_a_real_reattach_clears_the_note_and_does_not_replay_gap_input(serve, wait_until, monkeypatch) -> None:
    async def check() -> None:
        server, url = serve(InputRecordingApp())
        sid = make_session(url)
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 30)) as pilot:
            app.client.login(_serve.USER, _serve.PASSWORD)
            screen = await attached(pilot, app, sid, wait_until)
            pump = screen.pump
            old_id = pump.state.client_id
            reconnecting, release = asyncio.Event(), asyncio.Event()
            original_attach = app.client.async_attach

            async def gated_attach(*args, **kwargs):
                reconnecting.set()
                await release.wait()
                return await original_attach(*args, **kwargs)

            monkeypatch.setattr(app.client, "async_attach", gated_attach)
            try:
                pump._att._ws._writer.transport.abort()  # drop the socket; the pump remains its only reader
                await wait_until(pilot, reconnecting.is_set, "the replacement attachment to wait")
                assert pump.state.closed
                await pilot.press("x")
                assert NOTE in screen.status.text
                release.set()
                await wait_until(pilot, lambda: not pump.state.closed and pump.state.client_id != old_id,
                                 "a new attachment to become live")
                assert NOTE not in screen.status.text
                await pilot.press("y")
                await flushed(pilot, screen, server, wait_until)
                assert server.received(old_id) == []
                assert server.received(pump.state.client_id) == [b"y"]
            finally:
                release.set()

    asyncio.run(check())


def _reconnecting(state) -> None:
    """Hold the model at the pump's backoff state without changing a live fixture socket."""
    state.apply_close(4011)
    state.reattach = {"attempt": 1, "due": state.now() + 60, "cause": "detached"}


@pytest.mark.parametrize("action", ["healthy", "return", "background_attach", "scroll"])
def test_reconnect_warning_belongs_to_its_slot(serve, wait_until, action) -> None:
    async def check() -> None:
        server, url = serve(InputRecordingApp())
        sid = make_session(url)
        healthy_sid = make_session(url)
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 30)) as pilot:
            app.client.login(_serve.USER, _serve.PASSWORD)
            screen = await attached(pilot, app, sid, wait_until)
            down = screen.front
            healthy = screen._add_slot(app.client.session(healthy_sid), front=False)
            await wait_until(pilot, lambda: healthy.pump is not None and healthy.pump.state.dump_seen,
                             "the background healthy session to attach")
            _reconnecting(down.pump.state)
            await pilot.press("x")
            assert NOTE in screen.status.text
            if action == "scroll":
                screen.do_scroll()
                assert NOTE in screen.status.text
                screen._drop_while_scrolled("key", 1)
                assert NOTE in screen.status.text, "a scroll notice must not overwrite the reconnect warning"
                screen.close_scroll_view()
                assert NOTE in screen.status.text
            else:
                if action == "return":
                    screen.do_scroll()  # switching closes this view, which must not clear a session warning
                screen.show_front(healthy)
                if action == "healthy":
                    assert NOTE not in screen.status.text, "a healthy session must not inherit the warning"
                elif action == "return":
                    screen.show_front(down)
                    assert down.pump.state.closed
                    assert NOTE in screen.status.text
                else:
                    down.pump._apply_attached(down.pump._att)
                    screen.show_front(down)
                    assert NOTE not in screen.status.text
                    assert down.input_notice == "", "background attach must clear the owning slot's warning"
            await flushed(pilot, screen, server, wait_until)
            assert server.received(down.pump.state.client_id) == []
            assert server.received(healthy.pump.state.client_id) == []

    asyncio.run(check())


@pytest.mark.parametrize("final_state", ["ended", "stopped"])
def test_input_after_reconnecting_is_over_preserves_status_and_counts_drop(serve, wait_until, final_state) -> None:
    from tsb_tui.screens.prompts import ended_view

    async def check() -> None:
        server, url = serve(InputRecordingApp())
        sid = make_session(url)
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 30)) as pilot:
            app.client.login(_serve.USER, _serve.PASSWORD)
            screen = await attached(pilot, app, sid, wait_until)
            slot = screen.front
            state = slot.pump.state
            client_id = state.client_id
            # Start with a warning already present: stopping/ending must hide it without another attach.
            _reconnecting(state)
            await pilot.press("x")
            assert NOTE in screen.status.text
            if final_state == "ended":
                state.apply_close(4010)
                slot.ended = ended_view(state, slot.target)
                slot.ended_dialog_shown = True  # exercise keys on the workspace after the dialog is dismissed
            else:
                state.apply_close(4011)
                state.reattach_stopped = True
            screen.refresh_status()
            before_text = screen.status.text
            assert ("ended" if final_state == "ended" else "not reattaching") in before_text
            before = screen.dropped_inputs
            await pilot.press("z")
            assert screen.status.text == before_text
            assert NOTE not in screen.status.text
            assert screen.dropped_inputs == before + 1
            event = state.log.of_kind(KIND_INPUT_DROPPED)[-1]
            assert event.detail == {"source": "key", "bytes": 1, "reason": "not_attached"}
            await flushed(pilot, screen, server, wait_until)
            assert server.received(client_id) == []

    asyncio.run(check())
