"""Mouse and touch on TSB's own chrome (design §2.4, D13): a single click or tap on every piece of chrome, the wheel on
lists and over the canvas, tooltips that name each glyph's word and key, and `mouse.enabled = false` turning it all off.

Why it matters (Rule 9): the phone path has no key to fall back on, so a piece of chrome that needs a second click, or a
hint that is only text, is a feature the operator on a phone does not have (plan R5: Textual 8.2.8's own `Footer` is not
single-click everywhere, so the chrome is our own widgets). A wheel that reports to a session that did not ask for it,
or from a client that holds no keyboard (DT4 (b)), types into someone else's screen. Servers: the contract's in-process
fixture server (DT7).
"""

from __future__ import annotations

import asyncio

import pytest
from input_server import InputRecordingApp, other_client
from textual import events
from textual.app import App
from textual.command import CommandPalette
from tsb_tui.app import TsbTuiApp
from tsb_tui.model.fleet import GROUP_NEEDS_YOU
from tsb_tui.screens.chrome_dialogs import ConfirmEndScreen
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.screens.size_dialog import SizeDialog
from tsb_tui.screens.whos_here import WhosHereScreen
from tsb_tui.widgets.rail import (
    ACT_ALERTS,
    ACT_FLEET,
    ACT_MENU,
    ACT_NEEDS,
    ACT_NEW,
    Rail,
)
from tsb_tui.widgets.sidebar import Sidebar
from tsb_tui.widgets.status_row import StatusRow
from tsbc import _serve
from workspace_support import (
    click_chip,
    fixture_login,
    named_sessions,
    open_here,
    rail_row,
)

SIZES = ((65, 30), (120, 40), (200, 60))


# -- one click on every piece of chrome, at three widths --


def test_each_rail_glyph_acts_on_one_click_at_65_120_and_200_columns(serve, wait_until) -> None:
    """D12, design §3.4 test 5: `≡` the Menu, `▦▸` the sidebar, `▲n` the sidebar at Needs you, `!n` the Alerts tab, a
    chip its session and `+` New session: each by one click, at a phone's, a laptop's and a wide window's width."""

    async def _at(width: int, height: int) -> None:
        serve_app, url = serve(InputRecordingApp())
        first, second, broken = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02", "fw-edge-01")
        serve_app.sessions[broken]["state"] = "unreachable"  # a session that needs the operator: `▲1`
        app = TsbTuiApp(url)
        async with app.run_test(size=(width, height)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first, second)
            screen.close_sidebar()
            await wait_until(pilot, lambda: rail_row(screen, ACT_NEEDS) is not None
                             and "▲1" in screen.rail.text, "the rail to count the session that needs you")

            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_MENU)))
            await wait_until(pilot, lambda: isinstance(app.screen, CommandPalette), f"≡ at {width}")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "the Menu to close")

            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_FLEET)))
            await wait_until(pilot, lambda: screen.sidebar_open, f"▦▸ at {width}")
            screen.close_sidebar()

            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_NEEDS)))
            await wait_until(pilot, lambda: screen.sidebar_open and screen.sidebar.selected is not None
                             and screen.sidebar.selected[0] == GROUP_NEEDS_YOU, f"▲n at {width}")
            screen.close_sidebar()

            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_ALERTS)))
            await wait_until(pilot, lambda: screen.view_open and screen.fleet_view.tab == "alerts", f"!n at {width}")
            screen.close_fleet_view()

            await click_chip(pilot, screen, first)
            await wait_until(pilot, lambda: screen.front.session_id == first, f"a chip at {width}")

            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_NEW)))
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), f"+ at {width}")

    for size in SIZES:
        asyncio.run(_at(*size))


def test_a_sidebar_row_and_every_status_hint_acts_on_one_click_at_three_widths(serve, wait_until) -> None:
    """D11, D42: a row of the sidebar opens its session and each hint on the status row runs its action, by a single
    click. The hints that fit differ by width (a phone keeps the Menu), and every one that is on the row is tried."""

    effects = {
        "menu": lambda app, screen: isinstance(app.screen, CommandPalette),
        "fleet": lambda app, screen: screen.sidebar_open,
        "who": lambda app, screen: isinstance(app.screen, WhosHereScreen),
        "size": lambda app, screen: isinstance(app.screen, SizeDialog),
        "end": lambda app, screen: isinstance(app.screen, ConfirmEndScreen),
        "scroll": lambda app, screen: screen.scroll_view_open,
    }

    async def _at(width: int, height: int) -> None:
        serve_app, url = serve(InputRecordingApp())
        first, second = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02")
        app = TsbTuiApp(url)
        async with app.run_test(size=(width, height)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first)
            screen.open_sidebar()
            await wait_until(pilot, lambda: any(row[1] == second for row in screen.sidebar.view.rows),
                             "the sidebar to list the second session")
            y = next(i for i, line in enumerate(screen.sidebar.view.lines) if line.session_id == second)
            await pilot.click(Sidebar, offset=(6, y))
            await wait_until(pilot, lambda: app.screen is not screen or screen.slot_for(second) is not None,
                             f"a sidebar row at {width}")
            if app.screen is not screen:  # the gate: the first attach to a session in this run
                await pilot.press("enter")
            await wait_until(pilot, lambda: app.screen is screen and screen.front.session_id == second,
                             "the picked session in front")
            screen.close_sidebar()
            await wait_until(pilot, lambda: "Menu" in screen.status.text, "the status row")

            hints = [a for a in screen.status.actions() if a != "detach"]
            assert "menu" in hints, f"the Menu is always on the row (at {width}: {screen.status.text!r})"
            for action in hints:
                assert action in effects, f"a hint no test knows how to observe: {action}"
                row = screen.status
                await pilot.click(StatusRow, offset=(row.hit_x(action) + 1, 0))
                await wait_until(pilot, lambda action=action: effects[action](app, screen), f"{action} at {width}")
                if app.screen is not screen:
                    await pilot.press("escape")
                    await wait_until(pilot, lambda: app.screen is screen, f"{action} to close")
                elif action == "fleet":
                    screen.close_sidebar()
                elif action == "scroll":
                    screen.close_scroll_view()
                    await wait_until(pilot, lambda: "Menu" in screen.status.text, "the hints to come back")

    for size in SIZES:
        asyncio.run(_at(*size))


def test_a_dialogs_buttons_act_on_one_click_at_three_widths(serve, wait_until) -> None:
    """Plan R5: a dialog's button takes a single click at any width, for Textual's own `Button` (Who's here) and for the
    plain-text choices of ours (End session…'s two answers). Put it aside sends `handoff` to nobody; End it sends
    `DELETE /sessions/{id}`; Keep it sends nothing."""

    async def _at(width: int, height: int) -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(width, height)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            await screen.run_action_id("who")
            await wait_until(pilot, lambda: isinstance(app.screen, WhosHereScreen)
                             and app.screen.query("#aside"), "Who's here")
            state = screen.pump.state
            await pilot.click("#aside")
            await wait_until(pilot, lambda: state.holder is None, f"Put it aside at {width}")
            app.screen.dismiss(None)
            await wait_until(pilot, lambda: app.screen is screen, "Who's here to close")

            await screen.run_action_id("end")
            await wait_until(pilot, lambda: isinstance(app.screen, ConfirmEndScreen)
                             and app.screen.query("#end-keep"), "End session…")
            await pilot.click("#end-keep")
            await wait_until(pilot, lambda: app.screen is screen, "Keep it to close the question")
            assert ("DELETE", f"/sessions/{sid}") not in [r[:2] for r in serve_app.requests]

            await screen.run_action_id("end")
            await wait_until(pilot, lambda: isinstance(app.screen, ConfirmEndScreen)
                             and app.screen.query("#end-confirm"), "End session… again")
            await pilot.click("#end-confirm")
            await wait_until(pilot, lambda: ("DELETE", f"/sessions/{sid}") in [r[:2] for r in serve_app.requests],
                             f"End it at {width}")

    for size in SIZES:
        asyncio.run(_at(*size))


def test_the_full_views_hints_and_pan_hints_are_clicks_too(serve, wait_until) -> None:
    """D11, D13: while the full view or Pan has the keys, what the status row lists is clickable (`x End…`,
    `Esc Back`, an arrow, `Esc Done`), so a phone reaches every row action and pans without a keyboard."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01", cols=150, rows=40)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.close_sidebar()
            screen.open_fleet_view()
            await wait_until(pilot, lambda: screen.fleet_view.layout.rows, "the view's rows")
            await wait_until(pilot, lambda: "keys → fleet view" in screen.status.text, "the view's hints")
            row = screen.status
            await pilot.click(StatusRow, offset=(row.text.index("x End…") + 1, 0))
            await wait_until(pilot, lambda: isinstance(app.screen, ConfirmEndScreen), "End… from the view's hint")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "the question to close")
            await pilot.click(StatusRow, offset=(row.text.index("Esc Back") + 1, 0))
            await wait_until(pilot, lambda: not screen.view_open, "Esc Back to close the view")

            screen.do_pan()
            await wait_until(pilot, lambda: "keys → pan" in screen.status.text, "Pan to take the keys")
            assert screen.canvas.view_x == 0
            await pilot.click(StatusRow, offset=(screen.status.text.index("→ Right") + 2, 0))
            await wait_until(pilot, lambda: screen.canvas.view_x == app.cfg["view.pan_step"], "→ to pan")
            await pilot.click(StatusRow, offset=(screen.status.text.index("Esc Done") + 1, 0))
            await wait_until(pilot, lambda: "keys → pan" not in screen.status.text, "Esc Done to end Pan")

    asyncio.run(_check())


# -- the wheel --


async def _wheel(pilot, widget, down: bool = True, offset=(5, 5)) -> None:
    event = events.MouseScrollDown if down else events.MouseScrollUp
    await pilot._post_mouse_events([event], widget=widget, offset=offset)


def test_the_wheel_moves_through_the_sidebar_and_the_view(serve, wait_until) -> None:
    """D13: the wheel scrolls lists: one row a turn, the selection with it, so a list of hundreds is not keys only."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        named_sessions(url, serve_app, "a-host", "b-host", "c-host")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.sidebar.view.rows) == 3, "three sessions")
            first = screen.sidebar.selected
            await _wheel(pilot, screen.sidebar, True)
            await wait_until(pilot, lambda: screen.sidebar.selected != first, "the wheel to move the selection")
            await _wheel(pilot, screen.sidebar, False)
            await wait_until(pilot, lambda: screen.sidebar.selected == first, "the wheel to move it back")

            screen.open_fleet_view()
            view = screen.fleet_view
            await wait_until(pilot, lambda: len(view.layout.rows) == 3, "the view's rows")
            start = view.selected
            await _wheel(pilot, view, True)
            await wait_until(pilot, lambda: view.selected != start, "the wheel to move the view's selection")

    asyncio.run(_check())


MOUSE_REPORTING_ON = b"\x1b[?1000h\x1b[?1006h"  # what vim or htop sends: report presses and the wheel, SGR coordinates


def test_the_wheel_over_the_canvas_reports_only_where_dt4_b_lets_it_and_otherwise_scrolls_locally(
        serve, wait_until) -> None:
    """D13, DT4 (b): over the canvas the wheel goes to the session only when the session asked for the mouse and this
    client holds the keyboard; a report from a client that does not (here the owner, with the peer typing) would be
    input from someone who is not typing, so nothing is sent, and the wheel scrolls this client's own window over the
    grid instead. The typist's wheel is the session's, and scrolls nothing locally."""

    async def _blocked() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01", cols=80, rows=60)
        with other_client(url, _serve.PEER, sid):  # the peer attaches first and so holds the keyboard
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 30)) as pilot:
                screen = await fixture_login(app, pilot)
                await open_here(app, pilot, wait_until, sid)
                screen.close_sidebar()
                state = screen.pump.state
                assert not state.holds_keyboard
                screen.canvas.feed(MOUSE_REPORTING_ON)
                await wait_until(pilot, lambda: screen.canvas.backend.rows == 60, "the grid at the session's size")
                assert screen.canvas.view_y == 0
                await _wheel(pilot, screen.canvas, True)
                await wait_until(pilot, lambda: screen.canvas.view_y == app.cfg["view.pan_step"],
                                 "the wheel to scroll this client's own window")
                await _wheel(pilot, screen.canvas, False)
                await wait_until(pilot, lambda: screen.canvas.view_y == 0, "the wheel to scroll back")
                assert serve_app.received(state.client_id) == [], "a blocked client reported the wheel"

    async def _typist() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01", cols=80, rows=60)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.close_sidebar()
            state = screen.pump.state
            assert state.holds_keyboard
            screen.canvas.feed(MOUSE_REPORTING_ON)
            await wait_until(pilot, lambda: screen.canvas.backend.rows == 60, "the grid at the session's size")
            panned: list[tuple[int, int]] = []
            pan_to = screen.canvas.pan_to
            screen.canvas.pan_to = lambda x, y: (panned.append((x, y)), pan_to(x, y))[1]
            await _wheel(pilot, screen.canvas, True)
            await wait_until(pilot, lambda: serve_app.received(state.client_id), "the wheel to reach the session")
            await pilot.pause()  # the handler has finished: whatever it would also have done is done
            assert serve_app.received(state.client_id)[0].startswith(b"\x1b[<65;"), "wheel down, SGR"
            assert panned == [], "a reported wheel does not also scroll the window"

    async def _not_asked() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01", cols=80, rows=60)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.close_sidebar()
            state = screen.pump.state
            await wait_until(pilot, lambda: screen.canvas.backend.rows == 60, "the grid at the session's size")
            await _wheel(pilot, screen.canvas, True)
            await wait_until(pilot, lambda: screen.canvas.view_y == app.cfg["view.pan_step"],
                             "a session that never asked for the mouse to leave the wheel to the window")
            assert serve_app.received(state.client_id) == []

    asyncio.run(_blocked())
    asyncio.run(_typist())
    asyncio.run(_not_asked())


# -- mouse.enabled = false --


def test_with_mouse_disabled_clicks_and_the_wheel_do_nothing_and_the_terminal_is_told_not_to_report_it(
        serve, monkeypatch, wait_until) -> None:
    """D13, `mouse.enabled`: "turns it all off". Clicks on the rail, the sidebar and the status row do nothing, the
    wheel neither moves a list nor scrolls the canvas, and `run` asks Textual for no mouse at all, so the operator's
    terminal keeps its own text selection."""

    async def _check() -> None:
        monkeypatch.setenv("TSB_TUI_MOUSE_ENABLED", "false")
        serve_app, url = serve(InputRecordingApp())
        first, second = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02", cols=80, rows=60)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first, second)
            screen.close_sidebar()
            await wait_until(pilot, lambda: screen.front.session_id == second, "the second session in front")

            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_MENU)))
            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_FLEET)))
            await click_chip(pilot, screen, first)
            await pilot.click(StatusRow, offset=(screen.status.hit_x("who") + 1, 0))
            await pilot.pause(0.1)
            assert app.screen is screen and not screen.sidebar_open and screen.front.session_id == second

            screen.open_sidebar()
            await wait_until(pilot, lambda: screen.sidebar_open, "the sidebar (by key)")
            before = screen.sidebar.selected
            await _wheel(pilot, screen.sidebar, True)
            row = next(i for i, line in enumerate(screen.sidebar.view.lines) if line.session_id == first)
            await pilot.click(Sidebar, offset=(6, row))
            await pilot.pause(0.1)
            assert screen.sidebar.selected == before and screen.front.session_id == second
            screen.close_sidebar()

            await _wheel(pilot, screen.canvas, True)
            await pilot.pause(0.1)
            assert screen.canvas.view_y == 0

    asyncio.run(_check())

    seen: dict = {}

    def fake_run(self, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(App, "run", fake_run)
    TsbTuiApp("http://127.0.0.1:1").run()
    assert seen.get("mouse") is False
    monkeypatch.delenv("TSB_TUI_MOUSE_ENABLED")
    seen.clear()
    TsbTuiApp("http://127.0.0.1:1").run()
    assert seen.get("mouse") is True


# -- tooltips --


def test_tooltips_name_each_glyphs_word_and_key_and_follow_the_glyph_set_and_the_knob(
        serve, monkeypatch, wait_until) -> None:
    """D13: hovering a glyph says its word and key (`≡ Menu ^\\`, `▦ Fleet ^\\ f`, a chip's number, session and who types),
    a hint says what the action is in full, and a sidebar row that was cut short says all of it. With `glyphs.set =
    ascii` the tooltip is in the same glyphs as the chrome; with `mouse.tooltips = false` there is none."""

    async def _check(env: dict[str, str]) -> list[str]:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        serve_app, url = serve(InputRecordingApp())
        first, second = named_sessions(url, serve_app, "rtr-core-01", "a-very-long-target-name")
        app = TsbTuiApp(url)
        seen: list[str] = []
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first, second)
            screen.open_sidebar()
            await wait_until(pilot, lambda: any(row[1] == second for row in screen.sidebar.view.rows), "the rows")
            y = next(i for i, line in enumerate(screen.sidebar.view.lines) if line.session_id == second)
            await pilot.hover(Sidebar, offset=(6, y))
            seen.append(str(screen.sidebar.tooltip))
            await pilot.hover(Sidebar, offset=(len(screen.sidebar.view.lines[0].text.split("◂")[0]) + 2, 0))
            seen.append(str(screen.sidebar.tooltip))
            screen.close_sidebar()
            await wait_until(pilot, lambda: not screen.sidebar_open, "the rail")
            for action, arg in ((ACT_MENU, None), (ACT_FLEET, None), ("chip", first), (ACT_NEW, None)):
                await pilot.hover(Rail, offset=(1, rail_row(screen, action, arg)))
                seen.append(str(screen.rail.tooltip))
            row = screen.status
            await pilot.hover(StatusRow, offset=(row.hit_x("who") + 2, 0))
            seen.append(str(screen.status.tooltip))
        return seen

    on = asyncio.run(_check({}))
    assert on[0].startswith("a-very-long-target-name · s.") and "you type" in on[0], on
    assert on[1].startswith("◂ close the sidebar"), on
    assert on[2] == "≡ Menu ^\\ · every action, searchable", on
    assert on[3].startswith("▦ Fleet ^\\ f"), on
    assert on[4].startswith("1 rtr-core-01 · you type") and on[4].endswith("^\\ 1"), on
    assert on[5] == "+ New session ^\\ c", on
    assert on[6].startswith("Who's here… · "), on

    ascii_set = asyncio.run(_check({"TSB_TUI_GLYPHS_SET": "ascii"}))
    assert ascii_set[2].startswith("= Menu ^\\") and ascii_set[3].startswith("# Fleet ^\\ f"), ascii_set

    off = asyncio.run(_check({"TSB_TUI_MOUSE_TOOLTIPS": "false"}))
    assert off == ["None"] * 7, off


@pytest.mark.parametrize("width", [65, 120, 200])
def test_the_rails_four_columns_are_the_same_at_every_width_so_a_tap_lands_in_the_same_place(
        serve, wait_until, width: int) -> None:
    """D41, T3: the rail does not grow with the window, so a thumb that learned where `≡` is finds it at any width."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(width, 30)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.close_sidebar()
            await wait_until(pilot, lambda: screen.rail.region.width == 4, "the rail's four columns")
            assert rail_row(screen, ACT_MENU) == 0 and rail_row(screen, ACT_FLEET) == 1

    asyncio.run(_check())
