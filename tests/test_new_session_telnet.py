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

from stub_server import SlowChunkedApp, StubServer
from textual.widgets import Button, Input, RadioButton, Static
from tsb_contract_client import Client
from tsbc import _serve
from workspace_support import fixture_login, login, open_new_session

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.new_session import NewSessionScreen


def _creates(serve_app) -> list[dict]:
    return [body for method, path, body in serve_app.requests if (method, path) == ("POST", "/sessions")]


class _ListApp(SlowChunkedApp):
    """`SlowChunkedApp` with a `GET /targets` list route, so the New session screen's inventory loads. The route
    lives in this test file, not in stub_server.py; `GET /targets/{id}` keeps its configured error."""

    def http(self, method, path, headers, body):
        if method == "GET" and path.split("?", 1)[0] == "/targets":
            record = {"id": "stub-target", "type": "ssh", "name": "stub-target", "group": "stub", "tags": [],
                      "discovered_by": "manual", "last_seen": "2026-09-23T12:00:00Z", "host": "192.0.2.50", "port": 22}
            return 200, [], {"targets": [record]}
        return super().http(method, path, headers, body)


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
            await wait_until(pilot, lambda: any(row.id == "telnet-scenario" for row in app.workspace().targets.rows),
                             "the inventory")
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
            await wait_until(pilot, lambda: any(row.id == "normal" for row in app.workspace().targets.rows),
                             "the inventory")
            assert not dialog.query_one("#conn-reuse", RadioButton).disabled
            assert str(dialog.query_one("#new-session-target", Static).render()) == "normal · ssh"
            assert str(dialog.query_one("#connection-note", Static).render()) == ""

    asyncio.run(check())


def test_a_telnet_row_from_the_inventory_gets_new_and_asks_no_read(serve, wait_until) -> None:
    """DEFAULT 22: a telnet row picked from the loaded inventory gets the same rule as one prefilled — the reuse
    choice is greyed, Create sends `new`, and the screen asks the broker for no `GET /targets/{id}`."""

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target=None, target_type=None)
            await wait_until(pilot, lambda: any(row.id == "telnet-scenario" for row in app.workspace().targets.rows),
                             "the inventory")
            dialog.query_one("#new-session-filter", Input).value = "telnet scenario"
            await pilot.pause()
            assert dialog.selected_target_id == "telnet-scenario"
            reuse = dialog.query_one("#conn-reuse", RadioButton)
            assert reuse.disabled, "a telnet row must not offer reuse"
            before = len(_creates(serve_app))
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: len(_creates(serve_app)) > before, "the create")
            reads = [path for method, path, _ in serve_app.requests if method == "GET" and path.startswith("/targets/")]
            assert reads == []
            body = _creates(serve_app)[-1]
            assert body["connection"] == "new" and "connection_id" not in body

    asyncio.run(check())


def test_a_query_that_matches_no_row_shows_guidance_and_creates_nothing(serve, wait_until) -> None:
    """The retired `no such target` is gone: a text that matches no row is the Add-row guidance, no create is made,
    and the screen asks the broker for no `GET /targets/{id}`."""

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target=None, target_type=None)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            dialog.query_one("#new-session-filter", Input).value = "no-such-device"
            await pilot.pause()
            assert "+ Add 'no-such-device' as a target" in str(dialog.query_one("#target-rows", Static).render())
            dialog.query_one("#create-button", Button).press()
            await pilot.pause(0.3)
            reads = [path for method, path, _ in serve_app.requests if method == "GET" and path.startswith("/targets/")]
            assert reads == []
            assert _creates(serve_app) == [] and isinstance(app.screen, NewSessionScreen)

    asyncio.run(check())


def test_a_query_that_matches_no_row_creates_nothing_while_the_inventory_is_down(wait_until) -> None:
    """The retired fallback create is gone: a text that matches no row is the ruled guidance, and no create is made
    while the inventory is down."""

    async def check() -> None:
        stub = _ListApp(target_read_error=(503, "inventory_unavailable", "the inventory is not answering"))
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 36)) as pilot:
                await login(app, pilot)
                dialog = await open_new_session(app, pilot)
                await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 1, "the inventory")
                dialog.query_one("#new-session-filter", Input).value = "mystery-box"
                await pilot.pause()
                assert "No target matches 'mystery-box' · ask an admin to add it · Esc clears" \
                    in str(dialog.query_one("#no-match", Static).render())
                dialog.query_one("#create-button", Button).press()
                await pilot.pause(0.3)
                assert stub.creates == []

    asyncio.run(check())


def test_a_second_click_on_a_no_match_query_starts_no_create(wait_until) -> None:
    """The retired fallback create is gone: a text that matches no row creates nothing, so a second press of Create
    on the guidance reaches no broker."""

    async def check() -> None:
        stub = _ListApp(target_read_error=(503, "inventory_unavailable", "the inventory is not answering"))
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 36)) as pilot:
                await login(app, pilot)
                dialog = await open_new_session(app, pilot)
                await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 1, "the inventory")
                dialog.query_one("#new-session-filter", Input).value = "mystery-box"
                await pilot.pause()
                dialog.query_one("#create-button", Button).press()
                await pilot.pause(0.1)
                dialog.query_one("#create-button", Button).press()
                await pilot.pause(0.1)
                assert stub.creates == [], f"a create reached the broker: {stub.creates}"

    asyncio.run(check())


def test_an_empty_query_points_at_the_search(serve, wait_until) -> None:
    """With nothing typed the dialog points the operator at the searchable list and the full view, and asks nothing
    of the broker."""

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target=None, target_type=None)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            dialog.query_one("#new-session-filter", Input).value = "zzz"
            await pilot.pause()
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: "pick a target from the list" in str(
                dialog.query_one("#new-session-error", Static).render()), "the hint")
            assert "no target list" not in str(dialog.query_one("#new-session-error", Static).render())
            assert _creates(serve_app) == []

    asyncio.run(check())
