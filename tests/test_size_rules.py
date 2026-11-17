"""Who sizes the grid, and when anyone asks for a size.

Why these matter (Rule 9): the session's size belongs to the session (D3, Contract §11: clients own
all rendering and reflow). A TUI that sized the terminal to its own window would redraw a router's
`show` table or an editor at a width its other viewers do not have, and one that asked for a size on
every widget resize would resize a shared session under everyone each time a rail opened or a dialog
appeared (#176). The rules, from terminal-size §4 and session-attach/typing-ownership:

* the grid is the session's, from `attached`, `prefix` and `size`, and a smaller window pans (D3, D4);
* the `attach` frame says how big the canvas is, as information; no `resize` follows it (CR4, DT16);
* a request is made only on an outer window change, debounced, only while this client types, and
  asks for the window minus the rail and the status row; a clamped answer is final until the window
  changes again; getting the keyboard (CR2), opening a dialog or resizing a widget never asks (D44).

Servers: the contract's in-process fixture server (DT7). Frames come from what the TUI sends.
"""

from __future__ import annotations

import asyncio

from stub_server import create_owner_session, record_output, resize_requests
from textual.screen import ModalScreen
from textual.widgets import Label
from tsb_contract_client import Client
from tsbc import _serve

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.terminal import TerminalScreen
from tsb_tui.widgets.status_row import StatusRow

AUTO_ON = {"TSB_TUI_SIZE_AUTO_REQUEST": "true", "TSB_TUI_SIZE_DEBOUNCE_MS": "60"}
# The fixture server's target size bounds (C2 decision 8): a request below this clamps up to it.
FLOOR = (_serve.FLOOR_COLS, _serve.FLOOR_ROWS)
# How long "nothing was sent" is observed for: several debounce periods.
QUIET_S = 0.4


def _auto(monkeypatch, value: str = "true") -> None:
    for name, setting in AUTO_ON.items():
        monkeypatch.setenv(name, setting)
    monkeypatch.setenv("TSB_TUI_SIZE_AUTO_REQUEST", value)


async def _attached(pilot, app, session_id, wait_until) -> TerminalScreen:
    app.push_screen(TerminalScreen(session_id))
    screen = app.screen
    await wait_until(pilot, lambda: screen.pump is not None and screen.pump.state.dump_seen
                     and screen.canvas.painted, "the screen to attach and paint the dump")
    return screen


def test_a_window_smaller_than_the_session_shows_the_sessions_own_layout_and_sends_nothing(
        serve, monkeypatch, screen_text, wait_until) -> None:
    """#176 (D3). An 80x24 window attached to a 132x40 session must still draw the session at *its*
    size: a mark written at column 121 has to arrive at column 121 (so the canvas pans to show it),
    not be squeezed into the window's last column, where it wraps and no longer reads. And the window's
    smallness is not the session's business: nothing is sent. Only the screen's text and the
    server's record of frames are used, so this fails the same way against the earlier TUI."""
    _auto(monkeypatch)

    async def _check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 132, 40)
            # A line's head and a mark at column 121, leaving the cursor at column 130.
            record_output(serve_app, session_id, b"\x1b[2J\x1b[1;1HHEAD\x1b[1;121HTAIL-MARK")
            app.push_screen(TerminalScreen(session_id))
            await wait_until(pilot, lambda: "TAIL-MARK" in screen_text(app),
                             "the session's mark at column 121, whole", timeout_s=5.0)
            assert "HEAD" not in screen_text(app), "a window of 80 cannot also show columns 1-4"
            await pilot.pause(QUIET_S)
            assert resize_requests(serve_app) == []

    asyncio.run(_check())


def test_the_canvas_pans_over_a_grid_wider_than_the_window(
        serve, monkeypatch, screen_text, wait_until) -> None:
    """D3, D4. The grid is 132x40 whatever the window is, the status range says which columns show,
    and panning moves the window over the same grid."""
    _auto(monkeypatch)

    async def _check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 132, 40)
            record_output(serve_app, session_id, b"\x1b[2J\x1b[1;1HHEAD\x1b[1;121HTAIL-MARK")
            screen = await _attached(pilot, app, session_id, wait_until)

            assert (screen.backend.cols, screen.backend.rows) == (132, 40)
            canvas = screen.canvas
            assert canvas.size.width < 132, "the window must be smaller than the grid for this test"
            first, last, total = canvas.view_range[:3]
            assert (last - first + 1, total) == (canvas.size.width, 132)
            assert canvas.view_range.label == f"cols {first}-{last} of 132 ▸"
            assert "TAIL-MARK" in screen_text(app)

            canvas.pan_to(0, 0)
            await wait_until(pilot, lambda: "HEAD" in screen_text(app), "the start of the line after panning")
            assert canvas.view_range.label == f"cols 1-{canvas.size.width} of 132 ▸"

    asyncio.run(_check())


def test_a_size_frame_resizes_the_grid_and_a_clamp_is_the_size_in_effect(
        serve, monkeypatch, wait_until) -> None:
    """A `size` frame is the truth about the session's size, whoever asked and however the target
    bounded it (V5: the reason may be one this client does not know; cols and rows still are the size).
    Here this client asks for 100x30 and then for less than the target's floor."""
    _auto(monkeypatch, "false")

    async def _check() -> None:
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await _attached(pilot, app, session_id, wait_until)

            await screen.pump.resize(100, 30)
            await wait_until(pilot, lambda: (screen.backend.cols, screen.backend.rows) == (100, 30),
                             "the grid to take the applied size")
            await screen.pump.resize(10, 5)
            await wait_until(pilot, lambda: (screen.backend.cols, screen.backend.rows) == FLOOR,
                             "the grid to take the clamped size")
            assert screen.pump.state.size_reason == "clamped_floor"

    asyncio.run(_check())


def test_attaching_sends_no_resize_and_the_attach_frame_names_the_canvas(
        serve, monkeypatch, wait_until) -> None:
    """DT16, session-attach CR4/R5. The `attach` frame carries the canvas size as information, and
    attaching changes nobody's grid: a second client joining a session must never resize it just
    by arriving. Even with auto requests on, no `resize` follows the attach."""
    _auto(monkeypatch)

    async def _check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 100, 30)
            screen = await _attached(pilot, app, session_id, wait_until)
            await pilot.pause(QUIET_S)

            attach = next(f for f in serve_app.frames if f.get("t") == "attach")
            assert (attach["cols"], attach["rows"]) == (screen.canvas.size.width, screen.canvas.size.height)
            assert resize_requests(serve_app) == []

    asyncio.run(_check())


def test_a_burst_of_window_changes_sends_one_debounced_request(
        serve, monkeypatch, screen_text, wait_until) -> None:
    """#176. Dragging a window edge is dozens of resize events; each used to become a `resize` frame
    (and so a reflow of the shared session under every viewer). One request goes out, after the
    window settles, for the final window minus the rail (4 columns) and the status row (1 row).
    Only the screen's text and the server's record of frames are used, so this fails the same way
    against the earlier TUI."""
    _auto(monkeypatch)
    # Long enough that three separate resizes land inside one debounce window however slow the
    # machine is: the point is that they share a window, not what the window's length is.
    monkeypatch.setenv("TSB_TUI_SIZE_DEBOUNCE_MS", "1500")

    async def _check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            record_output(serve_app, session_id, b"READY-MARK")
            app.push_screen(TerminalScreen(session_id))
            await wait_until(pilot, lambda: "READY-MARK" in screen_text(app), "the attach and its dump")

            for size in ((70, 22), (66, 26), (64, 20)):
                await pilot.resize_terminal(*size)
            assert resize_requests(serve_app) == [], "a request went out before the window settled"
            await wait_until(pilot, lambda: resize_requests(serve_app), "the debounced request")
            await pilot.pause(QUIET_S)
            assert resize_requests(serve_app) == [(64 - 4, 20 - 1)]

    asyncio.run(_check())


def test_a_window_change_never_asks_a_session_that_came_to_the_front_later(
        serve, monkeypatch, wait_until) -> None:
    """A debounced auto request belongs to the session in front when the window changed.

    Why (Qodo #229, ruling 5; terminal-size §4, D44): bringing a session to the front asks for
    nothing. The debounce fires later, when another session may be in front; a request bound to
    "whatever is in front now" resizes the wrong session and reflows it under every other viewer,
    just by the operator switching sessions after a window resize. The request is dropped instead.
    """
    _auto(monkeypatch)
    # Long enough that the front is switched well before the debounce can fire.
    monkeypatch.setenv("TSB_TUI_SIZE_DEBOUNCE_MS", "1500")

    async def _check() -> None:
        from workspace_support import fixture_login, named_sessions, open_here

        serve_app, url = serve()
        first, second = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first, second)
            assert screen.front.session_id == second, "the second opened is in front"

            # The window changes while `second` is in front, then the operator switches to `first`
            # before the debounce fires.
            await pilot.resize_terminal(100, 30)
            screen.show_front(screen.slot_for(first))
            assert screen.front.session_id == first
            await pilot.pause(QUIET_S * 6)
            assert resize_requests(serve_app) == [], (
                "the window change's request must not be sent to the session now in front"
            )

    asyncio.run(_check())


def test_with_auto_requests_off_a_window_change_asks_for_nothing(
        serve, monkeypatch, wait_until) -> None:
    """terminal-size §1: a surface that must not ask (a phone, an agent's drive) sets the knob off,
    and then no window change may ask however the window moves."""
    _auto(monkeypatch, "false")

    async def _check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            await _attached(pilot, app, session_id, wait_until)
            await pilot.resize_terminal(66, 20)
            await pilot.pause(QUIET_S)
            assert resize_requests(serve_app) == []

    asyncio.run(_check())


def test_ask_size_on_a_closing_socket_returns_a_reason_and_does_not_escape(
        serve, monkeypatch, wait_until) -> None:
    """terminal-size §4, drive D48: an explicit `ask_size` on a closing socket is a reason, never an
    exception.

    Why (Qodo #229, ruling 6): the socket can close between the typist check and the send. The
    automatic path already treats `ConnectionClosed` as "not sent"; an explicit request (the Size
    dialog's Ask, drive's `size` op) must do the same and say why, or the exception escapes into a
    Textual worker / the drive command and the operator or agent sees a crash instead of a reason.
    """
    _auto(monkeypatch, "false")
    from tsb_tui._transport import ConnectionClosed

    async def _check() -> None:
        _serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await _attached(pilot, app, session_id, wait_until)
            assert screen.pump.state.holds_keyboard

            async def closing_resize(cols: int, rows: int, timeout: float = 0) -> None:
                raise ConnectionClosed("the socket closed mid-send")

            assert screen.pump._att is not None
            monkeypatch.setattr(screen.pump._att, "resize", closing_resize)
            why = await screen.ask_size()
            assert why == "the session's connection closed", why

    asyncio.run(_check())


def test_no_second_request_after_a_clamped_answer_until_the_window_changes(
        serve, monkeypatch, wait_until) -> None:
    """terminal-size §4: when the target clamps a request, that answer is final. A client that asked
    again to "get closer" would fight the target's bounds forever; only a new window change may ask."""
    _auto(monkeypatch)

    async def _check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await _attached(pilot, app, session_id, wait_until)

            await pilot.resize_terminal(42, 13)  # asks for 38x12: below the target's 40-column floor
            await wait_until(pilot, lambda: (screen.backend.cols, screen.backend.rows) == FLOOR,
                             "the clamped answer")
            await pilot.pause(QUIET_S)
            assert resize_requests(serve_app) == [(38, 12)]

            await pilot.resize_terminal(60, 20)
            await wait_until(pilot, lambda: len(resize_requests(serve_app)) == 2, "a request after the change")
            assert resize_requests(serve_app) == [(38, 12), (56, 19)]

    asyncio.run(_check())


def test_a_view_only_client_never_asks_for_a_size(serve, monkeypatch, wait_until) -> None:
    """typing-ownership: only the typist sizes the session. A viewer's request would only be refused
    (`not_typist`), and sending it would still be a client second-guessing the keyboard's holder."""
    _auto(monkeypatch)

    async def _check() -> None:
        serve_app, url = serve()
        owner = Client(url)
        owner.login(_serve.USER, _serve.PASSWORD)
        session_id = owner.create_session(_serve.TARGET, "new", "xterm-256color", 80, 24)["session"]["id"]
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            app.client.login(_serve.VIEWER, _serve.PASSWORD)
            screen = await _attached(pilot, app, session_id, wait_until)
            assert not screen.pump.state.holds_keyboard

            await pilot.resize_terminal(66, 20)
            await pilot.pause(QUIET_S)
            assert resize_requests(serve_app) == []

    asyncio.run(_check())


def test_getting_the_keyboard_sends_no_size_request(serve, monkeypatch, wait_until) -> None:
    """typing-ownership CR2: a client that is handed the keyboard must not announce itself with a
    size request; the typist's size is the session's until the typist's *window* changes. After that
    change, as the typist, it asks once."""
    _auto(monkeypatch)

    async def _check() -> None:
        serve_app, url = serve()
        owner = Client(url)
        owner.login(_serve.USER, _serve.PASSWORD)
        session_id = owner.create_session(_serve.TARGET, "new", "xterm-256color", 80, 24)["session"]["id"]
        owner_attachment = owner.attach(session_id)  # the owner types first
        try:
            app = TsbTuiApp(url)
            async with app.run_test(size=(80, 24)) as pilot:
                app.client.login(_serve.PEER, _serve.PASSWORD)
                screen = await _attached(pilot, app, session_id, wait_until)
                assert not screen.pump.state.holds_keyboard

                await pilot.resize_terminal(70, 22)  # not typing: no request
                await pilot.pause(QUIET_S)
                assert resize_requests(serve_app) == []

                owner_attachment.handoff(screen.pump.state.client_id)
                await wait_until(pilot, lambda: screen.pump.state.holds_keyboard, "the token to arrive")
                await pilot.pause(QUIET_S)
                assert resize_requests(serve_app) == [], "getting the keyboard sent a size request"

                await pilot.resize_terminal(66, 20)
                await wait_until(pilot, lambda: resize_requests(serve_app), "the typist's request")
                assert resize_requests(serve_app) == [(66 - 4, 20 - 1)]
        finally:
            owner_attachment.close()

    asyncio.run(_check())


class _Dialog(ModalScreen[None]):
    def compose(self):
        yield Label("a dialog")


def test_opening_a_dialog_or_resizing_a_widget_sends_no_request(
        serve, monkeypatch, wait_until) -> None:
    """D18, D44. Nothing the TUI opens or closes asks: a dialog opening, the status row hiding (which
    changes the canvas's own size, as a sidebar opening will), none of it is the *window* changing.
    A request here would resize the shared session every time the operator opened a menu."""
    _auto(monkeypatch)

    async def _check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await _attached(pilot, app, session_id, wait_until)
            before = screen.canvas.size

            app.push_screen(_Dialog())
            await pilot.pause(QUIET_S)
            app.pop_screen()
            screen.query_one(StatusRow).display = False
            await wait_until(pilot, lambda: screen.canvas.size.height == before.height + 1,
                             "the canvas to grow into the freed row")
            await pilot.pause(QUIET_S)
            assert resize_requests(serve_app) == []

    asyncio.run(_check())


def test_create_with_auto_size_off_carries_window_size(serve) -> None:
    from workspace_support import fixture_login, open_new_session

    from tsb_tui.screens.attach_confirm import AttachConfirmScreen

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url, target=_serve.TARGET)
        async with app.run_test(size=(80, 24)) as pilot:
            await fixture_login(app, pilot)
            await open_new_session(app, pilot, auto_size=False)
            await pilot.click("#create-button")
            await pilot.pause()
            assert isinstance(app.screen, AttachConfirmScreen)
            sid = next(iter(serve_app.sessions))
            assert (serve_app.sessions[sid]["cols"],
                    serve_app.sessions[sid]["rows"]) == (76, 23)

    asyncio.run(check())
