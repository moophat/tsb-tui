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
from tsb_tui.widgets.sidebar import Sidebar

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


def _four_open_and_one_elsewhere(url, served) -> tuple[list[str], str]:
    """Four sessions of the login to open here, and one detached session of its own that stays out."""
    names = [f"sw-open-{n}" for n in range(1, 5)]
    opened = named_sessions(url, served, *names)
    elsewhere = named_sessions(url, served, "sw-away")[0]
    served.sessions[elsewhere]["state"] = "detached"
    served.sessions[elsewhere]["control_holder"] = None
    return opened, elsewhere


def _last_session_row(screen) -> str:
    """The Sessions section's last content row (the frame's own bottom edge is the row under it)."""
    sidebar = screen.sidebar
    return _painted(screen)[sidebar.size.height - 2][2:-2]


@pytest.mark.parametrize("last", [True, False])
def test_the_line_stays_painted_whatever_row_is_selected(serve, wait_until, last):
    """CodeRabbit on #138: four open sessions at 80x24 push the `N more` line below the scrolled window, and no
    key or wheel turn could bring it up. It is pinned: the Sessions section's last row, with the first or the last
    session selected; Down and End stop at the last session, and the line is no row."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await fixture_login(app, pilot)
            opened, _elsewhere = _four_open_and_one_elsewhere(url, served)
            await open_here(app, pilot, wait_until, *opened)
            screen._refresh_fleet()
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: screen.sidebar.model.mine_elsewhere == 1, "the fleet read")
            await pilot.pause(0.2)
            sidebar = screen.sidebar
            assert sidebar.size.height == 23, sidebar.size
            assert [row[1] for row in sidebar.view.rows] == opened, sidebar.view.rows
            sidebar.selected = sidebar.view.rows[-1 if last else 0]
            sidebar._relayout()
            await pilot.pause(0.1)
            text = "\n".join(_session_lines(screen))
            assert "1 more · F full view" in text, text
            assert _last_session_row(screen).startswith("1 more · F full view"), text
            assert any(f"sw-open-{4 if last else 1}" in line for line in _session_lines(screen)), text
            assert sidebar.sessions_height == max(sidebar.size.height // 2, 3), "the height cap is unchanged"
            # the line is no row: End and the wheel stop at the last session, the line stays painted
            await pilot.press("end")
            await pilot.pause(0.1)
            assert sidebar.selected == sidebar.view.rows[-1], sidebar.selected
            assert _last_session_row(screen).startswith("1 more · F full view")
            assert any("sw-open-4" in line for line in _session_lines(screen))
    asyncio.run(check())


def test_a_click_on_the_pinned_line_opens_the_full_view(serve, wait_until):
    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await fixture_login(app, pilot)
            opened, _elsewhere = _four_open_and_one_elsewhere(url, served)
            await open_here(app, pilot, wait_until, *opened)
            screen._refresh_fleet()
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: screen.sidebar.model.mine_elsewhere == 1, "the fleet read")
            await pilot.pause(0.2)
            sidebar = screen.sidebar
            assert not screen.view_open
            await pilot.click(Sidebar, offset=(6, sidebar.size.height - 2))
            await wait_until(pilot, lambda: screen.view_open, "the full view to open")
    asyncio.run(check())


@pytest.mark.parametrize(("rows", "content"), [(8, 1), (9, 2)])
def test_the_line_is_pinned_only_when_a_session_row_fits_above_it(serve, wait_until, rows, content):
    """At a sidebar whose Sessions section has one content row the session shows and the line does not; with two,
    both paint. Wide, so a session is one line."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)  # the login form needs room; the window shrinks after
            opened, _elsewhere = _four_open_and_one_elsewhere(url, served)
            await open_here(app, pilot, wait_until, opened[0])
            await pilot.resize_terminal(160, rows)
            screen._refresh_fleet()
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: screen.sidebar.model.mine_elsewhere > 0, "the fleet read")
            await pilot.pause(0.2)
            sidebar = screen.sidebar
            more = f"{sidebar.model.mine_elsewhere} more · F full view"
            assert sidebar.sessions_height - 2 == content, (sidebar.size, sidebar.sessions_height)
            lines = _session_lines(screen)
            text = "\n".join(lines)
            assert any("sw-open-1" in line for line in lines), text
            assert (more in text) == (content == 2), text
            if content == 2:
                assert _last_session_row(screen).startswith(more), text
    asyncio.run(check())


def test_the_selected_row_must_fit_above_the_line_to_pin_it(serve, wait_until):
    """Mixed row heights at the narrow width (CodeRabbit on #138): a sidebar of 10 rows, one open session (three
    lines) and one visible ended one (two), three content lines. The shortest row fits above the line, the selected
    one does not: the line is not painted and the selected session is."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await fixture_login(app, pilot)
            opened, _elsewhere = _four_open_and_one_elsewhere(url, served)
            served.sessions[opened[1]]["state"] = "killed"
            await open_here(app, pilot, wait_until, opened[0])
            screen._refresh_fleet()
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: screen.sidebar.model.mine_elsewhere > 0 and screen.sidebar.model.ended,
                             "the fleet read")
            screen.sidebar.toggle_show_ended()
            await pilot.resize_terminal(80, 11)
            await pilot.pause(0.2)
            sidebar = screen.sidebar
            assert sidebar.size.height == 10 and sidebar.sessions_height - 2 == 3, (sidebar.size, sidebar.sessions_height)
            assert sidebar.view.row_lens == (3, 2), sidebar.view.row_lens
            assert sidebar.selected == sidebar.view.rows[0], sidebar.selected
            lines = _session_lines(screen)
            text = "\n".join(lines)
            assert any("sw-open-1" in line for line in lines), text
            assert "more · F full view" not in text, text
    asyncio.run(check())
