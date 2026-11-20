"""Helpers for the tests that go through the workspace: login lands on it (the rail, the sidebar open, no session),
sessions open from a sidebar row or a chip, and New session is a dialog over it (design §2.5, G1)."""

from __future__ import annotations

from collections.abc import Callable

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.fleet_view import FleetView
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.screens.terminal import TerminalScreen
from tsb_tui.widgets.rail import ACT_CHIP, ACT_NEW, Rail
from tsb_tui.widgets.sidebar import Sidebar


async def login(app: TsbTuiApp, pilot, user: str = "fixture-user", password: str = "fixture-password") -> TerminalScreen:
    """Log in through the form, as a person does; returns the workspace login lands on."""
    screen = app.screen
    screen.query_one("#username").value = user
    screen.query_one("#password").value = password
    await pilot.click("#login-button")
    for _ in range(300):
        if isinstance(app.screen, TerminalScreen) and app.screen.is_mounted and app.screen.sidebar is not None:
            await pilot.pause()
            return app.screen
        await pilot.pause(0.01)
    raise AssertionError(f"login did not reach the workspace: {app.screen!r}")


async def fixture_login(app: TsbTuiApp, pilot, user: str | None = None) -> TerminalScreen:
    """`login` against the contract's fixture server, whose logins (the owner, the peer, the viewer) share one password."""
    from tsbc import _serve

    return await login(app, pilot, user or _serve.USER, _serve.PASSWORD)


async def open_new_session(app: TsbTuiApp, pilot, wait_until: Callable | None = None, *,
                           auto_size: bool | None = True) -> NewSessionScreen:
    """Open the New session dialog the way the rail's `+` does (the same action). A session is created with the
    canvas's size only with auto size on (OPEN-1 was ruled on 2026-10-12: the default profile ships it on, and a
    create carries the creator's size); the test environment starts it off so a window change speaks for nothing,
    so the helper turns it on for this run unless asked not to."""
    screen = app.screen
    assert isinstance(screen, TerminalScreen), screen
    if auto_size is not None:
        app.cfg.set_runtime("size.auto_request", auto_size)
    await screen.run_action_id("new_session")
    for _ in range(300):
        if isinstance(app.screen, NewSessionScreen) and app.screen.is_mounted and app.screen.query("#create-button"):
            await pilot.pause()
            return app.screen
        await pilot.pause(0.01)
    raise AssertionError(f"New session did not open: {app.screen!r}")


def rail_row(screen: TerminalScreen, action: str, arg: str | None = None) -> int | None:
    """The row of the collapsed rail that a click runs `action` (and, for a chip, `arg`), or None."""
    for y, row in enumerate(screen.rail.rows):
        if row.action == action and (arg is None or row.arg == arg):
            return y
    return None


async def click_chip(pilot, screen: TerminalScreen, session_id: str) -> None:
    y = rail_row(screen, ACT_CHIP, session_id)
    assert y is not None, f"no chip for {session_id}: {screen.rail.text}"
    await pilot.click(Rail, offset=(1, y))


async def click_new_on_rail(pilot, screen: TerminalScreen) -> None:
    y = rail_row(screen, ACT_NEW)
    assert y is not None
    await pilot.click(Rail, offset=(1, y))


async def pick_in_sidebar(app: TsbTuiApp, pilot, wait_until: Callable, session_id: str) -> None:
    """Wait until the poll has listed `session_id` and click its row, as a person does: in the side panel when the
    session is open here, else in the full view's Sessions tab, which lists every other session (P2a)."""
    screen = app.screen
    assert isinstance(screen, TerminalScreen), screen
    if not screen.sidebar_open and not screen.view_open:
        screen.open_sidebar()
    screen._refresh_fleet()
    if screen.sidebar_open and any(row[1] == session_id for row in screen.sidebar.view.rows):
        y = next(i for i, line in enumerate(screen.sidebar.view.lines) if line.session_id == session_id)
        await click_sidebar_line(pilot, screen, y, 6)
        return
    if not screen.view_open:
        screen.open_fleet_view()
    view = screen.fleet_view
    await wait_until(pilot, lambda: any(row[1] == session_id for row in view.layout.rows),
                     f"{session_id} to be listed in the full view")
    await pilot.pause(0.3)  # the framed canvas settles its width before a click lands on the view
    y = next(i for i, line in enumerate(view.layout.lines) if line.session_id == session_id)
    await pilot.click(FleetView, offset=(8, y))


def sidebar_offset(screen, y: int, x: int) -> tuple[int, int]:
    """The widget offset of column `x` of the sidebar's logical line `y`. The lines sit in the framed Sessions
    section (frame and padding two columns each side) and scroll inside it, so the section is scrolled until the
    line is on screen."""
    sidebar = screen.sidebar
    line = sidebar.view.lines[y]
    if sidebar.line_offset(y) is None and line.session_id is not None and line.group is not None:
        # Selecting the row scrolls the section to it, and every later relayout keeps it there.
        sidebar.selected = (line.group, line.session_id)
        sidebar._relayout()
    elif sidebar.line_offset(y) is None:
        # A control line is no row: stop a relayout from scrolling the section back to the selected row.
        sidebar._scroll_to_selected = lambda: None
    while (row := sidebar.line_offset(y)) is None:
        sidebar._scroll_section(sidebar.targets_height + 1, -1 if y < sidebar._scroll_top else 1)
    return x + 2, row


async def click_sidebar_line(pilot, screen, y: int, x: int) -> None:
    """Click column `x` of the sidebar's logical line `y`."""
    await pilot.click(Sidebar, offset=sidebar_offset(screen, y, x))


def named_sessions(url: str, serve_app, *targets: str, user: str | None = None, cols: int = 80,
                   rows: int = 24) -> list[str]:
    """One session per name on the contract's fixture server, created by `user` (the owner by default), each
    listed under its own target name: the fixture only creates sessions on a few targets, so the record's
    `target_id` is renamed after creation, which is all `GET /sessions` and `attached` read it from."""
    from input_server import make_session
    from tsbc import _serve

    ids = []
    for name in targets:
        sid = make_session(url, user or _serve.USER, cols, rows, target="normal")
        serve_app.sessions[sid]["target_id"] = name
        ids.append(sid)
    return ids


async def open_here(app: TsbTuiApp, pilot, wait_until: Callable, *session_ids: str) -> None:
    """Open each session in the workspace, past the gate (a test about the gate says so), and wait until its dump is
    on its grid."""
    screen = app.screen
    assert isinstance(screen, TerminalScreen), screen
    for sid in session_ids:
        app.gated_sessions.add(sid)
        screen.open_session(app.client.session(sid))
        await wait_until(
            pilot, lambda sid=sid: (slot := screen.slot_for(sid)) is not None and slot.pump is not None
            and slot.pump.state.dump_seen, f"{sid} to attach")


async def select_in_sidebar(app: TsbTuiApp, pilot, wait_until: Callable, session_id: str) -> None:
    """Move the selection onto `session_id`'s row with `j` and `k` (the list then has the keys, for `t` or Enter): in the
    side panel when the session is open here, else in the full view's Sessions tab (P2a: the panel lists only the
    sessions open here)."""
    screen = app.screen
    assert isinstance(screen, TerminalScreen), screen
    screen.open_sidebar(pane="sessions")  # the Sessions list has the keys, not the Targets filter login lands in
    screen._refresh_fleet()
    await pilot.pause(0.3)
    if any(row[1] == session_id for row in screen.sidebar.view.rows):
        for _ in range(len(screen.sidebar.view.rows) + 1):
            if screen.sidebar.selected is not None and screen.sidebar.selected[1] == session_id:
                break
            await pilot.press("j")
        assert screen.sidebar.selected[1] == session_id, screen.sidebar.selected
        await wait_until(pilot, lambda: screen._sidebar_has_keys(), "the sidebar to hold the keys")
        return
    screen.open_fleet_view()
    view = screen.fleet_view
    await wait_until(pilot, lambda: any(row[1] == session_id for row in view.layout.rows),
                     f"{session_id} to be listed in the full view")
    for _ in range(len(view.layout.rows) + 1):
        if view.selected is not None and view.selected[1] == session_id:
            break
        await pilot.press("j")
    assert view.selected[1] == session_id, view.selected
    await wait_until(pilot, lambda: screen._view_has_keys(), "the full view to hold the keys")
