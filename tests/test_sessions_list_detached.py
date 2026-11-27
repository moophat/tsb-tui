"""The Sessions list shows the sessions open in this window. A login whose sessions are all detached sees none of
them there, so the list says how many of its own sessions the full view holds; the full view still lists them.

The data is the shape a long-lived instance returns: a few hundred ended records, and three detached sessions, two
of them the login's own, with nothing open here.

AI-Model: claude-sonnet-5-5
"""
from __future__ import annotations

import asyncio
import copy

import pytest
from input_server import InputRecordingApp
from tsbc import _serve
from workspace_support import fixture_login, named_sessions, open_here

from tsb_tui.app import TsbTuiApp

ENDED_COUNT = 287


def _painted(screen) -> list[str]:
    return [screen.sidebar.render_line(y).text for y in range(screen.sidebar.size.height)]


def _session_lines(screen) -> list[str]:
    start = screen.sidebar.targets_height
    return [line[2:-2] for line in _painted(screen)[start:]]


def _staging_shaped(url, served) -> tuple[list[str], str]:
    """Two detached sessions of the login, one of the peer, and ENDED_COUNT killed ones listed before them."""
    mine = named_sessions(url, served, "sw-one", "sw-two")
    other = named_sessions(url, served, "sw-peer", user=_serve.PEER)[0]
    for sid in (*mine, other):
        served.sessions[sid]["state"] = "detached"
        served.sessions[sid]["control_holder"] = None
    template = served.sessions[mine[0]]
    ended = {}
    for index in range(ENDED_COUNT):
        record = copy.deepcopy(template)
        record["id"] = f"s-ended-{index:04d}"
        record["state"] = "killed"
        ended[record["id"]] = record
    served.sessions = {**ended, **served.sessions}  # the killed ones first, as the core lists them
    return mine, other


@pytest.mark.parametrize("size", [(80, 24), (160, 45)])
def test_own_detached_sessions_are_counted_in_the_empty_sessions_list(serve, wait_until, size):
    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            _staging_shaped(url, served)
            screen._refresh_fleet()
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: len(screen.fleet.records) > ENDED_COUNT, "the fleet read")
            await pilot.pause(0.2)
            text = "\n".join(_session_lines(screen))
            assert "2 more · F full view" in text, text
            assert "3 more" not in text, "the peer's detached session is not the login's own"
            assert screen.sidebar.sessions_height >= 3
            assert screen.sidebar.sessions_height <= max(screen.sidebar.size.height // 2, 3)
    asyncio.run(check())


@pytest.mark.parametrize("size", [(80, 24), (160, 45)])
def test_the_line_follows_the_rows_open_here(serve, wait_until, size):
    """With one of the two open here, the list shows that row and `1 more`; the open one is not counted."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            mine, _other = _staging_shaped(url, served)
            await open_here(app, pilot, wait_until, mine[0])
            screen._refresh_fleet()
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: len(screen.fleet.records) > ENDED_COUNT, "the fleet read")
            await pilot.pause(0.2)
            text = "\n".join(_session_lines(screen))
            assert "sw-one" in text, text
            assert "1 more · F full view" in text, text
            assert screen.sidebar.sessions_height <= max(screen.sidebar.size.height // 2, 3)
    asyncio.run(check())


@pytest.mark.parametrize("size", [(80, 24), (160, 45)])
def test_the_full_view_lists_the_detached_sessions(serve, wait_until, size):
    """A guard, not a red: the full view already lists the login's detached sessions among 287 ended ones."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            mine, other = _staging_shaped(url, served)
            screen._refresh_fleet()
            await wait_until(pilot, lambda: len(screen.fleet.records) > ENDED_COUNT, "the fleet read")
            screen.open_fleet_view()
            await pilot.pause(0.5)
            rows = [row[1] for row in screen.fleet_view.layout.rows if row[0] != "needs_you"]
            assert set(mine) <= set(rows), rows[:10]
            assert other not in rows, "mine: the peer's session is not in the login's own view"
            assert rows.index(mine[0]) < 5 and rows.index(mine[1]) < 5, rows[:10]
    asyncio.run(check())
