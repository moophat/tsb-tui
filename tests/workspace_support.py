"""Helpers for the tests that go through the workspace: login lands on it (the rail, the sidebar open, no session),
sessions open from a sidebar row or a chip, and New session is a dialog over it (design §2.5, G1)."""

from __future__ import annotations

from collections.abc import Callable

from tsb_tui.app import TsbTuiApp
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
    """Open the sidebar, wait until the poll has listed `session_id` and click its row, as a person does."""
    screen = app.screen
    assert isinstance(screen, TerminalScreen), screen
    if not screen.sidebar_open:
        screen.open_sidebar()
    screen._refresh_fleet()
    await wait_until(pilot, lambda: any(row[1] == session_id for row in screen.sidebar.view.rows),
                     f"{session_id} to be listed in the sidebar")
    y = next(i for i, line in enumerate(screen.sidebar.view.lines) if line.session_id == session_id)
    await pilot.click(Sidebar, offset=(6, y))


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
    """Open the sidebar with the keys, wait until the poll lists `session_id` and move the selection onto its row, as
    `j` and `k` would (the sidebar then has the keys, for `t` or Enter)."""
    screen = app.screen
    assert isinstance(screen, TerminalScreen), screen
    screen.open_sidebar()
    await wait_until(pilot, lambda: any(row[1] == session_id for row in screen.sidebar.view.rows),
                     f"{session_id} to be listed in the sidebar")
    for _ in range(len(screen.sidebar.view.rows) + 1):
        if screen.sidebar.selected is not None and screen.sidebar.selected[1] == session_id:
            break
        await pilot.press("j")
    assert screen.sidebar.selected[1] == session_id, screen.sidebar.selected
    await wait_until(pilot, lambda: screen._sidebar_has_keys(), "the sidebar to hold the keys")
