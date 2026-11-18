"""The scroll view: `^\\ [` opens a view over the scrollback this client's engine holds; PageUp, PageDown and the wheel
move it; Esc returns to live.

Why it matters (Rule 9): the view shares the engine with the live session, and a keyboard, mouse or focus report that leaves
for a session while the viewport is scrolled is aimed at a screen the person is not looking at: a stray letter reaching
a router, a click reported at the wrong cell, an input mark taken at a cursor the render state cannot see. So the tests
pin what leaves the screen while the view is open (nothing), what is lost while it is open (nothing), and where every
other way out of it lands (the viewport at the bottom, the live screen current). Servers: the contract's in-process
fixture server; a test reads the server's record of what the client sent, never the client's own say-so.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass
from typing import Any

from input_server import InputRecordingApp, flushed
from prompt_support import open_stub_terminal
from stub_server import SlowChunkedApp, StubServer
from textual import events
from workspace_support import fixture_login, named_sessions, open_here

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.terminal import (
    KIND_INPUT_DROPPED,
    MARK_INPUT,
    SOURCE_KEY,
    TerminalScreen,
)
from tsb_tui.widgets.status_row import StatusRow

LEADER = "ctrl+backslash"
MOUSE_ON = b"\x1b[?1000h\x1b[?1006h"  # normal tracking, SGR coordinates
FOCUS_ON = b"\x1b[?1004h"
DSR = b"\x1b[6n"  # "report the cursor position"
CURSOR_REPORT = re.compile(rb"\x1b\[\d+;\d+R")
DROPPED_NOTE = "Esc to return to live, then type"
ALT_REASON = "the session is on its alternate screen (a full-screen program); its own keys scroll it"
GRID_ROWS = 24


def _numbered(first: int, last: int) -> bytes:
    return b"".join(f"L{i:03d}\r\n".encode() for i in range(first, last + 1))


def _on_canvas(text: str) -> list[int]:
    """The numbered lines the screen shows, oldest first."""
    return sorted(int(n) for n in re.findall(r"\bL(\d{3})\b", text))


async def _wheel(pilot, widget, down: bool = True, offset=(5, 5)) -> None:
    event = events.MouseScrollDown if down else events.MouseScrollUp
    await pilot._post_mouse_events([event], widget=widget, offset=offset)


@dataclass
class Scene:
    app: TsbTuiApp
    pilot: Any
    screen: TerminalScreen
    serve_app: InputRecordingApp
    sessions: list[str]
    wait_until: Any
    screen_text: Any

    @property
    def canvas(self):
        return self.screen.canvas

    @property
    def client_id(self) -> str:
        return self.screen.pump.state.client_id

    def emit(self, data: bytes, session: str | None = None) -> None:
        with self.serve_app.lock:
            sid = session or self.sessions[-1]
            self.serve_app._emit_output(self.serve_app.sessions[sid], data)

    async def output(self, last: int, *, first: int = 0, session: str | None = None) -> None:
        """Numbered lines `first`..`last` from the target, waited for until they are on this client's grid."""
        self.emit(_numbered(first, last), session)
        slot = self.screen.slot_for(session or self.sessions[-1])
        await self.wait_until(self.pilot, lambda: f"L{last:03d}" in slot.canvas.backend.scrollback()
                              + slot.canvas.backend.visible_rows(), f"L{last:03d} on the grid")

    async def open_view(self) -> None:
        await self.pilot.press(LEADER, "[")
        await self.wait_until(self.pilot, lambda: "keys → scroll" in self.screen.status.text,
                              "the scroll view to open")

    async def numbers(self) -> list[int]:
        """The numbered lines on screen once the canvas has painted what the engine holds and the screen has composed it."""
        await self.wait_until(self.pilot, lambda: self.canvas.painted, "the canvas to paint")
        await self.pilot.pause()  # no delay: waits for the screen's queued messages and its update, not for time
        return _on_canvas(self.screen_text(self.app))

    def position(self, session: str | None = None):
        return self.screen.slot_for(session or self.sessions[-1]).canvas.backend.scrollback_position()

    def at_bottom(self, session: str | None = None) -> bool:
        position = self.position(session)
        return position.offset + position.length == position.total

    async def settled(self) -> None:
        """Everything this client sent before now has reached the server."""
        await flushed(self.pilot, self.screen, self.serve_app, self.wait_until)


@contextlib.asynccontextmanager
async def _scene(serve, wait_until, screen_text, *targets: str, cols: int = 80, rows: int = GRID_ROWS,
                 size=(120, 30)):
    serve_app, url = serve(InputRecordingApp())
    sessions = named_sessions(url, serve_app, *(targets or ("rtr-core-01",)), cols=cols, rows=rows)
    app = TsbTuiApp(url)
    async with app.run_test(size=size) as pilot:
        screen = await fixture_login(app, pilot)
        await open_here(app, pilot, wait_until, *sessions)
        screen.close_sidebar()
        await wait_until(pilot, lambda: screen.front.session_id == sessions[-1], "the last session in front")
        yield Scene(app, pilot, screen, serve_app, sessions, wait_until, screen_text)


def test_leader_bracket_opens_the_view_and_pageup_shows_older_output(serve, wait_until, screen_text) -> None:
    """`^\\ [` hands the keys to the view (the status row says `keys → scroll`) with the viewport where it was, live;
    PageUp shows the page of output just before the window, the canvas painting history; PageDown comes back."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(299)
            live = await scene.numbers()
            assert live[-1] == 299
            await scene.open_view()
            assert await scene.numbers() == live, "opening the view moves nothing"
            assert scene.screen.scroll_view_open

            await scene.pilot.press("pageup")
            await wait_until(scene.pilot, lambda: not scene.at_bottom(), "the viewport to move up")
            older = await scene.numbers()
            assert 299 not in older
            assert older[-1] == live[0] - 1, "the page before the window"
            assert scene.position().offset == scene.position().total - GRID_ROWS - GRID_ROWS, "one page up"

            await scene.pilot.press("pagedown")
            await wait_until(scene.pilot, scene.at_bottom, "the viewport to come back down")
            assert await scene.numbers() == live

    asyncio.run(_check())


def test_esc_returns_to_live_with_output_that_arrived_meanwhile(serve, wait_until, screen_text) -> None:
    """The session goes on running into the engine while the view is open: the view stays on the rows it shows, nothing
    is lost or held back, and Esc shows the live screen as it is now."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(299)
            await scene.open_view()
            await scene.pilot.press("pageup")
            await wait_until(scene.pilot, lambda: not scene.at_bottom(), "the viewport to move up")
            shown = await scene.numbers()

            await scene.output(339, first=300)
            assert await scene.numbers() == shown, "output while scrolled leaves the view on its rows"
            assert "keys → scroll" in scene.screen.status.text

            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: "keys → scroll" not in scene.screen.status.text, "Esc to close")
            assert not scene.screen.scroll_view_open
            await wait_until(scene.pilot, scene.at_bottom, "the viewport at the bottom")
            assert (await scene.numbers())[-1] == 339, "the live screen is current"
            lines = [line for line in scene.canvas.backend.grid_lines() if line.startswith("L")]
            assert lines == [f"L{i:03d}" for i in range(340)], "nothing was lost while the view was open"

    asyncio.run(_check())


def test_a_typed_key_while_scrolled_is_dropped_with_a_note_and_nothing_reaches_the_session(
        serve, wait_until, screen_text) -> None:
    """A key the view does not use, a paste, and input from drive's own path are dropped while the view is open, with the
    note "Esc to return to live, then type": a stray letter reaching network gear is the costlier mistake. Esc, and the
    session has its keys back."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(99)
            await scene.open_view()
            await scene.pilot.press("x", "left", "enter")
            scene.screen.post_message(events.Paste("pasted"))
            await wait_until(scene.pilot, lambda: DROPPED_NOTE in scene.screen.status.text, "the note")
            assert await scene.screen.send_input(SOURCE_KEY, b"z") is False, "drive's input path is held back too"
            assert "keys → scroll" in scene.screen.status.text and scene.screen.scroll_view_open

            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "Esc to close")
            await scene.settled()  # its marker is a resize, which would have closed the view: it comes after Esc
            assert scene.serve_app.received(scene.client_id) == [], "nothing reached the session while scrolled"
            await scene.pilot.press("y")
            await wait_until(scene.pilot, lambda: scene.serve_app.received(scene.client_id) == [b"y"],
                             "the key to reach the session after Esc")

    asyncio.run(_check())


def test_a_reply_goes_but_no_mouse_or_focus_report_leaves_while_scrolled(serve, wait_until, screen_text,
                                                                         monkeypatch) -> None:
    """A session that asked for mouse and focus reports gets none while the view is open: they are made from the pointer
    and the window, not asked for by the session. The engine's answer to the session's own cursor-position query still
    goes, as when live. The control is the same events before the view opens, which all reach the session."""

    async def _check() -> None:
        monkeypatch.setenv("TSB_TUI_INPUT_FOCUS_REPORTS", "true")  # off by default: each report counts as input
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(99)
            scene.emit(MOUSE_ON + FOCUS_ON)
            await wait_until(scene.pilot, lambda: scene.canvas.backend.modes().mouse_tracking
                             and scene.canvas.backend.modes().focus_events, "the session's reporting modes")
            await _wheel(scene.pilot, scene.canvas, True)
            scene.screen._on_outer_focus(True)
            scene.emit(DSR)
            await wait_until(scene.pilot, lambda: any(CURSOR_REPORT.fullmatch(d) for d in
                                                      scene.serve_app.received(scene.client_id)),
                             "the control reply to reach the server")
            control = list(scene.serve_app.received(scene.client_id))
            assert any(d.startswith(b"\x1b[<65;") for d in control), "control: the wheel was reported"
            assert b"\x1b[I" in control, "control: the focus report was sent"

            await scene.open_view()
            await _wheel(scene.pilot, scene.canvas, False)
            await wait_until(scene.pilot, lambda: not scene.at_bottom(), "the wheel to move the view, not the session")
            await scene.pilot._post_mouse_events([events.MouseDown, events.MouseUp], widget=scene.canvas,
                                                 offset=(5, 5))
            scene.screen._on_outer_focus(False)
            scene.emit(DSR)
            await wait_until(scene.pilot, lambda: len(scene.serve_app.received(scene.client_id)) > len(control),
                             "the reply to the query to reach the server")
            await scene.settled()
            sent = scene.serve_app.received(scene.client_id)[len(control):]
            assert len(sent) == 1 and CURSOR_REPORT.fullmatch(sent[0]), f"only the reply went out: {sent!r}"
            reasons = [e.detail["reason"] for e in scene.screen.pump.state.log.of_kind(KIND_INPUT_DROPPED)]
            assert reasons.count("scroll_view") == 1, reasons  # the focus report; the mouse is the view's, the reply went

    asyncio.run(_check())


def test_a_cursor_position_query_answered_while_scrolled_reaches_the_server_once(serve, wait_until,
                                                                                 screen_text) -> None:
    """The answer is computed from the active area, not the viewport: with the view scrolled a page up, the query gets
    the live cursor's position, once, and the view stays where it was."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(99)
            await scene.open_view()
            await scene.pilot.press("pageup")
            await wait_until(scene.pilot, lambda: not scene.at_bottom(), "the viewport to move up")
            offset = scene.position().offset
            cursor = scene.canvas.backend.live_cursor()
            scene.emit(DSR)
            await wait_until(scene.pilot, lambda: scene.serve_app.received(scene.client_id), "the answer to arrive")
            assert scene.screen.scroll_view_open and scene.position().offset == offset
            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "Esc to close")
            await scene.settled()  # its marker is a resize, which would have closed the view: it comes after Esc
            assert scene.serve_app.received(scene.client_id) == [f"\x1b[{cursor.y + 1};{cursor.x + 1}R".encode()]

    asyncio.run(_check())


def test_wheel_moves_the_view_by_scroll_wheel_lines(serve, wait_until, screen_text, monkeypatch) -> None:
    """The knob is read: each wheel turn moves `scroll.wheel_lines` rows, up with the wheel up and down with the wheel
    down, and a wheel down at the bottom does not close the view (Esc does)."""

    async def _check() -> None:
        monkeypatch.setenv("TSB_TUI_SCROLL_WHEEL_LINES", "5")
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(299)
            await scene.open_view()
            live = scene.position().offset
            await _wheel(scene.pilot, scene.canvas, False)
            await wait_until(scene.pilot, lambda: scene.position().offset == live - 5, "five rows up")
            await _wheel(scene.pilot, scene.canvas, False)
            await wait_until(scene.pilot, lambda: scene.position().offset == live - 10, "five rows more")
            await _wheel(scene.pilot, scene.canvas, True)
            await wait_until(scene.pilot, lambda: scene.position().offset == live - 5, "five rows down")
            for _ in range(3):
                await _wheel(scene.pilot, scene.canvas, True)
            await wait_until(scene.pilot, scene.at_bottom, "the bottom")
            await scene.pilot.pause()  # idle: every wheel turn posted above has been handled
            assert scene.screen.scroll_view_open, "a wheel down at the bottom leaves the view open"

    asyncio.run(_check())


def test_wheel_up_over_a_grid_with_nothing_to_pan_opens_the_view(serve, wait_until, screen_text, monkeypatch) -> None:
    """A wheel up the session did not take, over a window that already shows the grid from its top, opens the view and
    moves it `scroll.wheel_lines` (3) rows; where the grid is taller than the window the wheel still pans it first."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(299)
            live = scene.position().offset
            assert scene.canvas.view_y == 0
            await _wheel(scene.pilot, scene.canvas, False)
            await wait_until(scene.pilot, lambda: scene.screen.scroll_view_open, "the wheel to open the view")
            await wait_until(scene.pilot, lambda: scene.position().offset == live - 3, "three rows up")
            assert "keys → scroll" in scene.screen.status.text

        # The window must not follow the cursor, or a later paint or resize pans it under the test.
        monkeypatch.setenv("TSB_TUI_VIEW_FOLLOW_CURSOR", "off")
        async with _scene(serve, wait_until, screen_text, cols=80, rows=60, size=(120, 30)) as tall:
            await tall.output(299)
            await wait_until(tall.pilot, lambda: tall.canvas.backend.rows == 60 and tall.canvas.painted,
                             "the tall grid, painted")
            tall.canvas.pan_to(0, 10)
            assert tall.canvas.view_y == 10
            await _wheel(tall.pilot, tall.canvas, False)
            await wait_until(tall.pilot, lambda: tall.canvas.view_y == 2, "the wheel to pan up by the step")
            assert not tall.screen.scroll_view_open, "there was grid left to pan up: no view"

    asyncio.run(_check())


def test_switching_detaching_or_the_leader_closes_the_view_at_the_bottom(serve, wait_until, screen_text) -> None:
    """No session keeps a scrolled viewport off the front: the leader, a switch by number, a switch by the rail or a
    pick, and a detach each close the view with the viewport back at the bottom."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text, "rtr-core-01", "infra-db-02") as scene:
            first, second = scene.sessions
            await scene.output(299, session=first)
            await scene.output(299, session=second)

            await scene.open_view()
            await scene.pilot.press("pageup")
            await wait_until(scene.pilot, lambda: not scene.at_bottom(second), "second scrolled")
            await scene.pilot.press(LEADER)
            await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "the leader to close the view")
            assert scene.at_bottom(second), "the leader put the viewport back at the bottom"
            await scene.pilot.press("1")  # `^\ 1`: the first chip
            await wait_until(scene.pilot, lambda: scene.screen.front.session_id == first, "the first in front")

            await scene.open_view()
            await scene.pilot.press("pageup")
            await wait_until(scene.pilot, lambda: not scene.at_bottom(first), "first scrolled")
            scene.screen.show_front(scene.screen.slot_for(second))
            assert not scene.screen.scroll_view_open
            assert scene.at_bottom(first), "a session switched away from keeps no scrolled viewport"
            assert "keys → scroll" not in scene.screen.status.text

            await scene.open_view()
            await scene.pilot.press("pageup")
            await wait_until(scene.pilot, lambda: not scene.at_bottom(second), "second scrolled again")
            scene.screen.action_detach()
            await wait_until(scene.pilot, lambda: scene.screen.slot_for(second) is None, "second to detach")
            assert not scene.screen.scroll_view_open and scene.screen.front.session_id == first
            assert scene.at_bottom(first)
            assert "keys → scroll" not in scene.screen.status.text

    asyncio.run(_check())


def test_the_sidebar_or_the_fleet_view_taking_the_keys_closes_the_view(serve, wait_until, screen_text) -> None:
    """The view's keys are PageUp and Esc; once the sidebar or the full fleet view has the keys the view is not what
    they go to, so it closes, the viewport at the bottom."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(299)
            for open_other in (scene.screen.open_sidebar, scene.screen.open_fleet_view):
                await scene.open_view()
                await scene.pilot.press("pageup")
                await wait_until(scene.pilot, lambda: not scene.at_bottom(), "the viewport to move up")
                open_other()
                await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "the view to close")
                assert scene.at_bottom()
                if scene.screen.view_open:
                    scene.screen.close_fleet_view()
                scene.screen.close_sidebar()
                await wait_until(scene.pilot, lambda: not scene.screen.view_open
                                 and not scene.screen._sidebar_has_keys(), "the keys to be back on the terminal")

    asyncio.run(_check())


def test_scroll_view_on_the_alternate_screen_is_blocked_with_the_reason(serve, wait_until, screen_text) -> None:
    """The engine keeps no scrollback for the alternate screen, so the action is blocked there, in the Menu and at the
    key, with the reason; the wheel does nothing there either (a full-screen program scrolls itself)."""

    async def _check() -> None:
        from tsb_tui.model import actions as A

        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(99)
            scene.emit(b"\x1b[?1049h\x1b[2;3Hfull screen program")
            await wait_until(scene.pilot, lambda: scene.canvas.backend.modes().alternate_screen, "the alternate screen")
            assert A.blocked("scroll", scene.screen.facts()) == ALT_REASON
            entry = next(e for e in scene.screen.menu_entries() if e[0].id == "scroll")
            assert entry[2] == ALT_REASON, "the Menu says why"

            await scene.pilot.press(LEADER, "[")
            await wait_until(scene.pilot, lambda: any(ALT_REASON in n.message for n in
                                                      scene.app._notifications._notifications.values()),
                             "the reason to be shown")
            assert not scene.screen.scroll_view_open and "keys → scroll" not in scene.screen.status.text
            await _wheel(scene.pilot, scene.canvas, False)
            await scene.pilot.pause(0.1)  # invariant: a wheel up on the alternate screen opens no view; a negative check is a bounded wait
            assert not scene.screen.scroll_view_open, "a wheel up opens nothing on the alternate screen"

    asyncio.run(_check())


def test_reply_after_scrolling_and_typing_starts_at_the_live_cursor(serve, wait_until, screen_text) -> None:
    """The input mark is taken at the live cursor, never at a scrolled viewport (which has no cursor to read): after a
    visit to the view, the next typed line's reply starts at that line and holds nothing of the output before it."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            scene.screen.front.mark_input = True  # what drive's runner sets on every session it attaches
            assert await scene.screen.send_input(SOURCE_KEY, b"first\r\n")  # one input, as drive's `type` sends it
            await wait_until(scene.pilot, lambda: "first" in scene.canvas.backend.visible_rows(), "the echo")
            await scene.output(299)
            await scene.open_view()
            await scene.pilot.press("pageup", "pageup")
            await wait_until(scene.pilot, lambda: not scene.at_bottom(), "the viewport to move up")
            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "Esc to close")
            assert await scene.screen.send_input(SOURCE_KEY, b"second\r\n"), "the keys are the session's again"
            await wait_until(scene.pilot, lambda: any("second" in line for line in
                                                      scene.canvas.backend.text_since(MARK_INPUT)),
                             "the reply to hold the echo")
            reply = scene.canvas.backend.text_since(MARK_INPUT)
            assert reply[0] == "second", reply
            assert not any(line.startswith("L") for line in reply), "no output from before the input is in the reply"

    asyncio.run(_check())


def test_the_status_row_says_how_far_back_and_when_it_is_the_oldest_output(serve, wait_until, screen_text) -> None:
    """The note is the position the engine reports: rows back of the rows held; at the top it says that nothing older is
    held. The hints PgUp, PgDn and Esc are clicks, as Pan's are."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.pilot.resize_terminal(240, 40)
            await scene.output(99)
            await scene.open_view()
            status = scene.screen.status
            held = scene.position().total - GRID_ROWS
            assert f"⇡ 0 rows back of {held}" in status.text
            await scene.pilot.click(StatusRow, offset=(status.text.index("PgUp") + 1, 0))
            await wait_until(scene.pilot, lambda: f"⇡ {GRID_ROWS} rows back of {held}" in status.text,
                             "PgUp to move a page and the note to follow")
            assert "the oldest output this client holds" not in status.text
            for _ in range(5):
                await scene.pilot.press("pageup")
            await wait_until(scene.pilot, lambda: f"⇡ {held} rows back of {held}" in status.text
                             and "the oldest output this client holds" in status.text, "the top of what is held")
            await scene.pilot.click(StatusRow, offset=(status.text.index("PgDn") + 1, 0))
            await wait_until(scene.pilot, lambda: "the oldest output this client holds" not in status.text,
                             "PgDn to leave the top")
            await scene.pilot.click(StatusRow, offset=(status.text.index("Esc") + 1, 0))
            await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "Esc to close")
            assert scene.at_bottom()

    asyncio.run(_check())


def test_sign_out_closes_the_view_before_it_detaches(serve, wait_until, screen_text) -> None:
    """Sign out's first step is to close the view, so a session is never left detached with a scrolled viewport."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(99)
            await scene.open_view()
            seen: list[bool] = []
            detach_all = scene.screen.detach_all

            async def spy():
                seen.append(scene.screen.scroll_view_open)
                return await detach_all()

            scene.screen.detach_all = spy
            await scene.app.sign_out()
            assert seen == [False], "the view was still open when the detaches began"
            assert not scene.screen.scroll_view_open

    asyncio.run(_check())


def test_the_session_entering_the_alternate_screen_closes_the_view_and_its_keys_reach_it(
        serve, wait_until, screen_text) -> None:
    """A full-screen program has no scrollback to scroll, so a view left open would drop the keys it is waiting for: when
    the session in front switches to its alternate screen the view closes, and the next key reaches the session."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(99)
            await scene.open_view()
            await scene.pilot.press("pageup")
            await wait_until(scene.pilot, lambda: not scene.at_bottom(), "the viewport to move up")
            scene.emit(b"\x1b[?1049h\x1b[2;3Hfull screen program")
            await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "the view to close")
            assert "keys → scroll" not in scene.screen.status.text
            await scene.pilot.press("x")
            await wait_until(scene.pilot, lambda: scene.serve_app.received(scene.client_id) == [b"x"],
                             "the key to reach the session")

    asyncio.run(_check())


def test_a_resize_while_the_view_is_open_closes_it_at_the_bottom_and_says_why(serve, wait_until, screen_text) -> None:
    """The engine moves its viewport when the grid is resized, so a view left open would claim rows it is not on: the
    view closes, the viewport at the bottom, and the operator is told why."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(299)
            await scene.open_view()
            await scene.pilot.press("pageup")
            await wait_until(scene.pilot, lambda: not scene.at_bottom(), "the viewport to move up")
            await scene.settled()  # the marker resize it sends is the session's size change
            await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "the view to close")
            await wait_until(scene.pilot, lambda: any("size changed; back to live" in n.message for n in
                                                      scene.app._notifications._notifications.values()),
                             "the reason to be shown")
            assert scene.at_bottom() and "keys → scroll" not in scene.screen.status.text

    asyncio.run(_check())


def test_scroll_from_the_menu_closes_the_fleet_view_or_the_sidebar_and_takes_the_keys(
        serve, wait_until, screen_text) -> None:
    """Scroll back run from the Menu while the full fleet view or the sidebar holds the keys: the overlay gives the keys
    back to the terminal first, so PageUp is the view's and not the list's."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(299)
            screen = scene.screen
            screen.open_fleet_view()
            await wait_until(scene.pilot, screen._view_has_keys, "the full view to hold the keys")
            await screen.run_action_id("scroll")
            assert not screen.view_open and screen.scroll_view_open
            await scene.pilot.press("pageup")
            await wait_until(scene.pilot, lambda: not scene.at_bottom(), "PageUp to move the view")
            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: not screen.scroll_view_open, "Esc to close")

            screen.open_sidebar()
            await wait_until(scene.pilot, screen._sidebar_has_keys, "the sidebar to hold the keys")
            await screen.run_action_id("scroll")
            assert screen.scroll_view_open
            await wait_until(scene.pilot, lambda: not screen._sidebar_has_keys(), "the sidebar to give the keys back")
            await scene.pilot.press("pageup")
            await wait_until(scene.pilot, lambda: not scene.at_bottom(), "PageUp to move the view again")

    asyncio.run(_check())


def test_esc_while_reattaching_closes_the_view_first_and_a_second_esc_stops_the_reattach(
        wait_until, monkeypatch) -> None:
    """With the view open during a reattach, the first Esc closes the view and the second stops the reattaching, as Esc
    did before there was a view."""
    monkeypatch.setenv("TSB_TUI_REATTACH_BACKOFF_MS", "60000,60000")

    async def _check() -> None:
        # The stub closes the stream (4011) after eight chunks, half a second apart: the view is opened while the session
        # is attached, and the connection is lost while it is open.
        with StubServer(SlowChunkedApp(delay_s=0.5, stream_close=(4011, 8))) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(160, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await pilot.press(LEADER, "[")
                await wait_until(pilot, lambda: screen.scroll_view_open, "the view to open")
                await wait_until(pilot, lambda: "◐ reattaching" in screen.status.text or screen.pump.state.reattach,
                                 "the connection to be lost")
                assert screen.scroll_view_open, "the view is still open when the reattach starts"
                await pilot.press("escape")
                await wait_until(pilot, lambda: not screen.scroll_view_open, "the first Esc to close the view")
                assert screen.pump.state.reattach is not None and "◐ reattaching" in screen.status.text
                await pilot.press("escape")
                await wait_until(pilot, lambda: "not reattaching" in screen.status.text, "the second Esc to stop it")

    asyncio.run(_check())


def test_a_wheel_up_with_nothing_older_does_nothing_and_typing_reaches_the_session(
        serve, wait_until, screen_text) -> None:
    """The wheel opens the view only when the engine holds rows above the screen. On a fresh short session it does
    nothing, as it did before the view existed, and the next typed key is the session's."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(4)
            position = scene.position()
            assert position.total == position.length, "no scrollback yet"
            await _wheel(scene.pilot, scene.canvas, False)
            await scene.pilot.press("x")
            await wait_until(scene.pilot, lambda: scene.serve_app.received(scene.client_id) == [b"x"],
                             "the key to reach the session")
            assert not scene.screen.scroll_view_open

    asyncio.run(_check())


def test_up_and_down_move_the_view_one_line_and_other_arrows_are_dropped(serve, wait_until, screen_text) -> None:
    """The arrows are the view's: Up and Down move it one row each. Left and Right are keys it does not use, so they are
    dropped with the note, and nothing reaches the session."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await scene.output(299)
            await scene.open_view()
            live = scene.position().offset
            await scene.pilot.press("up", "up", "up")
            await wait_until(scene.pilot, lambda: scene.position().offset == live - 3, "three rows up")
            await scene.pilot.press("down")
            await wait_until(scene.pilot, lambda: scene.position().offset == live - 2, "one row down")
            assert DROPPED_NOTE not in scene.screen.status.text
            await scene.pilot.press("left", "right")
            await wait_until(scene.pilot, lambda: DROPPED_NOTE in scene.screen.status.text, "the note")
            assert scene.position().offset == live - 2
            await scene.settled()
            assert scene.serve_app.received(scene.client_id) == []

    asyncio.run(_check())
