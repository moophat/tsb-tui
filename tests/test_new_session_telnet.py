"""New session on a telnet target (DEFAULT 22, R19; E3 phase 11 OPEN-11.4 and 11.5).

Why it matters (Rule 9): a telnet connection carries one session, so the worker refuses a reuse of it and the core
answers 502 "the connection to the target failed before a session opened", a sentence that blames the network for
what is the operator's own choice. The dialog must therefore never be able to send `reuse` for a telnet target, even
when this login holds a live connection to it that the dialog would otherwise offer; and a target id typed in, whose
type the TUI has not been told, is read first (`GET /targets/{id}`) so the same rule applies. Server: the contract's
in-process fixture server, which does not itself refuse a telnet reuse, so what the dialog sends is what the test reads.
"""

from __future__ import annotations

import asyncio
import threading

from stub_server import SlowChunkedApp, StubServer
from textual.widgets import Button, Input, RadioButton, Static
from tsb_contract_client import Client
from tsbc import _serve
from workspace_support import fixture_login, login, open_new_session

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.attach_confirm import AttachConfirmScreen
from tsb_tui.screens.new_session import NewSessionScreen


def _creates(serve_app) -> list[dict]:
    return [body for method, path, body in serve_app.requests if (method, path) == ("POST", "/sessions")]


async def _open_dialog(app, pilot, wait_until, *, target: str | None, target_type: str | None) -> NewSessionScreen:
    screen = await fixture_login(app, pilot)
    app.cfg.set_runtime("size.auto_request", True)
    screen._new_session_dialog(target, target_type)
    await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen) and app.screen.is_mounted
                     and bool(app.screen.query("#create-button")), "the New session dialog")
    return app.screen


def test_a_telnet_target_never_asks_reuse(serve, wait_until) -> None:
    """With a live connection to the telnet target in `GET /sessions`, which an ssh target would offer for reuse, the
    dialog still cannot send `reuse`: the choice is greyed with its reason, a click on it changes nothing, and Create
    sends `connection: new` with no `connection_id`."""

    async def check() -> None:
        serve_app, url = serve()
        client = Client(url)
        client.login(_serve.USER, _serve.PASSWORD)
        client.create_session("telnet-scenario", "new", "xterm-256color", 80, 24)
        assert any(s["target_id"] == "telnet-scenario" for s in client.sessions()), "a live connection to reuse"
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="telnet-scenario", target_type="telnet")
            reuse = dialog.query_one("#conn-reuse", RadioButton)
            assert reuse.disabled and dialog.query_one("#conn-new", RadioButton).value
            await pilot.click("#conn-reuse")
            await pilot.pause()
            assert not reuse.value and dialog.query_one("#conn-new", RadioButton).value
            assert dialog.query_one("#connection-id", Input).disabled
            before = len(_creates(serve_app))
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: len(_creates(serve_app)) > before, "the create")
            body = _creates(serve_app)[-1]
            assert body["target_id"] == "telnet-scenario" and body["connection"] == "new"
            assert "connection_id" not in body

    asyncio.run(check())


def test_an_ssh_target_keeps_the_reuse_choice(serve, wait_until) -> None:
    """The rule is for telnet only (DEFAULT 22): an ssh target keeps today's two choices, so the dialog does not
    take away a reuse that works."""

    async def check() -> None:
        _serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            assert not dialog.query_one("#conn-reuse", RadioButton).disabled
            assert str(dialog.query_one("#new-session-target", Static).render()) == "normal · ssh"
            assert str(dialog.query_one("#connection-note", Static).render()) == ""

    asyncio.run(check())


def test_a_typed_target_is_read_before_the_create_and_a_telnet_one_gets_new(serve, wait_until) -> None:
    """11.5: an id typed with no record behind it is read (`GET /targets/{id}`) before Create, so a telnet target typed by
    hand gets the same rule as one picked from the list: the operator chose `reuse`, the dialog sends `new`."""

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target=None, target_type=None)
            dialog.query_one("#target-id", Input).value = "telnet-scenario"
            await pilot.click("#conn-reuse")
            await pilot.pause()
            assert dialog.query_one("#conn-reuse", RadioButton).value, "nothing is known about the type yet"
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: isinstance(app.screen, AttachConfirmScreen), "the attach gate")
            reads = [path for method, path, _ in serve_app.requests if method == "GET" and path.startswith("/targets/")]
            assert reads == ["/targets/telnet-scenario"]
            body = _creates(serve_app)[-1]
            assert body["connection"] == "new" and "connection_id" not in body

    asyncio.run(check())


def test_a_typed_target_that_does_not_exist_says_so_and_creates_nothing(serve, wait_until) -> None:
    """11.5: a 404 from the read is shown as "no such target"; no create is made, because a create against an id the
    inventory does not list could only fail further in, with a sentence about a connection."""

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target=None, target_type=None)
            dialog.query_one("#target-id", Input).value = "no-such-device"
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: str(dialog.query_one("#new-session-error", Static).render())
                             == "no such target", "the refusal")
            assert not dialog.query_one("#create-button", Button).disabled
            assert _creates(serve_app) == [] and isinstance(app.screen, NewSessionScreen)

    asyncio.run(check())


def test_a_typed_id_creates_even_while_the_inventory_is_down() -> None:
    """11.5, inventory-list: a create still works while the index is down. When `GET /targets/{id}` fails with
    anything but 404 the type cannot be read, so the dialog creates anyway asking `connection: new` (safe for
    every protocol) and says so — greyed out would leave the operator unable to open a target the broker can."""
    async def check() -> None:
        gate = threading.Event()
        stub = SlowChunkedApp(target_read_error=(503, "inventory_unavailable", "the inventory is not answering"),
                              create_gate=gate)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 36)) as pilot:
                await login(app, pilot)
                dialog = await open_new_session(app, pilot)
                dialog.query_one("#target-id", Input).value = "mystery-box"
                await pilot.click("#conn-reuse")
                await pilot.pause()
                assert dialog.query_one("#conn-reuse", RadioButton).value, "the operator asked for a reuse"
                dialog.query_one("#create-button", Button).press()
                for _ in range(300):
                    if stub.create_started.is_set():
                        break
                    await pilot.pause(0.01)
                assert stub.create_started.is_set(), "no create was made while the inventory was down"
                assert "type could not be read" in str(dialog.query_one("#new-session-error", Static).render())
                body = stub.creates[-1]
                assert body["target_id"] == "mystery-box"
                assert body["connection"] == "new" and "connection_id" not in body
                gate.set()
                for _ in range(300):
                    if not isinstance(app.screen, NewSessionScreen):
                        break
                    await pilot.pause(0.01)
                assert not isinstance(app.screen, NewSessionScreen)

    asyncio.run(check())


def test_a_second_click_while_the_fallback_create_is_pending_starts_no_second_create() -> None:
    """The ruling of 2026-10-22: one read failure makes exactly one create. While the fallback create is in flight
    Create stays greyed until it ends, so a second click — an impatient operator, a double Enter — cannot open a
    second session on the same target behind the first, and the broker counts one `POST /sessions`."""

    async def check() -> None:
        gate = threading.Event()
        stub = SlowChunkedApp(target_read_error=(503, "inventory_unavailable", "the inventory is not answering"),
                              create_gate=gate)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 36)) as pilot:
                await login(app, pilot)
                dialog = await open_new_session(app, pilot)
                dialog.query_one("#target-id", Input).value = "mystery-box"
                dialog.query_one("#create-button", Button).press()
                for _ in range(300):
                    if stub.create_started.is_set():
                        break
                    await pilot.pause(0.01)
                assert stub.create_started.is_set(), "the fallback create never started"
                assert dialog.query_one("#create-button", Button).disabled, (
                    "Create stayed clickable while the fallback create was in flight, so a second click could start "
                    "a second session")
                dialog.query_one("#create-button", Button).press()
                await pilot.pause(0.1)
                assert len(stub.creates) == 1, f"a second create reached the broker: {stub.creates}"
                gate.set()
                for _ in range(300):
                    if not isinstance(app.screen, NewSessionScreen):
                        break
                    await pilot.pause(0.01)
                assert not isinstance(app.screen, NewSessionScreen), "the create never finished"
                assert len(stub.creates) == 1

    asyncio.run(check())


def test_an_empty_target_id_points_at_the_targets_tab(serve, wait_until) -> None:
    """The dialog no longer says there is no target list: with nothing typed it points the operator at the Targets tab
    of the full Fleet view, where a target is picked, and asks nothing of the broker."""

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target=None, target_type=None)
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: "Targets tab of the full view" in str(
                dialog.query_one("#new-session-error", Static).render()), "the hint")
            assert "no target list" not in str(dialog.query_one("#new-session-error", Static).render())
            assert _creates(serve_app) == []

    asyncio.run(check())
