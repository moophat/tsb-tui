"""P2b: focus chain, rail rows, one letter one meaning, Ctrl+] detach, folding. Keys only (Pilot keys), except the fold's
click. Every family runs at 80x24 and 160x45, the two sizes of the redesign."""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest
from input_server import InputRecordingApp, flushed, other_client
from stub_server import emit_live_output
from test_redesign_keys import login as key_login
from textual.command import CommandList
from tsbc import _serve
from workspace_support import named_sessions, open_here

from tsb_tui.app import MenuPalette, TsbTuiApp
from tsb_tui.model import actions as A
from tsb_tui.screens.credentials import CredentialsScreen
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.theme import contrast_ratio, role_colours
from tsb_tui.widgets.rail import (
    ACT_ALERTS,
    ACT_CHIP,
    ACT_FLEET,
    ACT_MENU,
    ACT_NEEDS,
    ACT_NEW,
    ACT_VAULT,
)

SIZES = [(80, 24), (160, 45)]
LEADER = "ctrl+backslash"
DETACH = "ctrl+right_square_bracket"


def snapshot(app, name):
    svg = re.sub(r"terminal-\d+", "terminal-p2b", app.export_screenshot(title="P2b", simplify=True))
    path = Path(__file__).parent / "snapshots" / "redesign-p2b" / f"{name}.svg"
    if os.environ.get("UPDATE_P2B_SNAPSHOTS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg)
    assert path.read_text() == svg


async def snap(pilot, wait_until, screen, app, name):
    """A baseline taken at a moment the fixture's alert stream has raised nothing (its `!n` would change the picture)."""
    await settle(pilot, wait_until, screen, alerts_quiet=True)
    snapshot(app, name)


def heavy_panes(app, screen, screen_text):
    """Which panes are drawn with the heavy border, read from the rendered cells: the rail's edge, each of the side
    panel's two sections, the session frame."""
    lines = screen_text(app).splitlines()

    def cell(y, x):
        return lines[y][x] if y < len(lines) and x < len(lines[y]) else " "

    panes = set()
    side, rail, frame = screen.sidebar.region, screen.rail.region, screen.query_one("#canvases").region
    if screen.sidebar.display:
        if cell(side.y, side.x) == "┏":
            panes.add("targets")
        if cell(side.y + screen.sidebar.targets_height, side.x) == "┏":
            panes.add("sessions")
    if any(cell(y, x) == "┃" for y in range(rail.y, rail.bottom) for x in range(rail.x, rail.right)):
        panes.add("rail")
    if cell(frame.y, frame.x) == "┏":
        panes.add("canvas")
    return panes


async def settle(pilot, wait_until, screen, *, alerts_quiet=False):
    await pilot.pause()
    if alerts_quiet:
        def quiet():
            row = next((r for r in screen.rail.rows if r.action == ACT_ALERTS), None)
            alerts_zero = row is None or row.text.strip() in ("! Alerts 0", "!0")
            alerts_empty = getattr(screen.app, "alerts", None) is None or getattr(screen.app.alerts, "count", 0) == 0
            return alerts_zero and alerts_empty
        await wait_until(pilot, quiet, "a moment with no alert raised")
    if hasattr(screen, "_fleet_dirty") and screen._fleet_dirty:
        await wait_until(pilot, lambda: not screen._fleet_dirty, "fleet widgets to flush")
    await pilot.pause(0.05)


async def workspace_with_sessions(app, pilot, wait_until, serve_app, url, *names, size_cols=80):
    """Log in with keys, then open one session per name (the first is in front)."""
    screen = await key_login(pilot, app, wait_until)
    sids = named_sessions(url, serve_app, *names, cols=size_cols)
    await open_here(app, pilot, wait_until, *sids)
    await pilot.pause()
    return screen, sids


async def to_sessions_list(pilot, screen):
    await pilot.press(LEADER, "s")
    await pilot.pause()


async def to_rail(pilot, screen):
    """The keys to the rail, by keys: Shift+Tab from either list, the leader's Switch from the canvas."""
    for _ in range(5):
        if screen.rail.has_focus:
            return
        await pilot.press("shift+tab" if screen.sidebar.has_focus else LEADER)
        if not screen.sidebar.has_focus and not screen.rail.has_focus:
            await pilot.press("s")
        await pilot.pause()
    raise AssertionError("the keys never reached the rail")


# -- L1-focus --


@pytest.mark.parametrize("size", SIZES)
def test_focus_chain_rail_targets_sessions_and_back(serve, wait_until, screen_text, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen, (_sid,) = await workspace_with_sessions(app, pilot, wait_until, serve_app, url, "rtr-core-01")
            client = screen.pump.state.client_id
            await to_sessions_list(pilot, screen)
            assert heavy_panes(app, screen, screen_text) == {"sessions"}
            forward = ["rail", "targets", "sessions", "rail"]
            for expected in forward:
                await pilot.press("tab")
                await pilot.pause()
                assert heavy_panes(app, screen, screen_text) == {expected}, expected
            # Up and Down move the rail's row and add no stop: the next Tab still leaves it.
            assert heavy_panes(app, screen, screen_text) == {"rail"}
            await pilot.press("down", "down", "up")
            assert heavy_panes(app, screen, screen_text) == {"rail"}
            await pilot.press("tab")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"targets"}
            for expected in ("rail", "sessions", "targets", "rail"):
                await pilot.press("shift+tab")
                await pilot.pause()
                assert heavy_panes(app, screen, screen_text) == {expected}, expected
            await flushed(pilot, screen, serve_app, wait_until)
            assert serve_app.received(client) == [], "a chrome navigation key reached the session"
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_an_unfocused_list_keeps_its_selection_as_a_dim_bar_with_a_marker(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen, (_sid,) = await workspace_with_sessions(app, pilot, wait_until, serve_app, url, "rtr-core-01")
            roles = role_colours(app.cfg)
            await to_sessions_list(pilot, screen)
            sidebar = screen.sidebar
            row = next(y for y in range(sidebar.size.height) if "rtr-core-01" in sidebar.render_line(y).text)

            def bars(y):
                strip = sidebar.render_line(y)
                return {seg.style.bgcolor.get_truecolor() for seg in strip if seg.style and seg.style.bgcolor}

            def hexed(rgb):
                return "#" + "".join(f"{c:02x}" for c in rgb)

            focused = {hexed(c) for c in bars(row)}
            assert roles["accent_soft"] in focused
            await pilot.press("tab")  # the rail has the keys now
            await pilot.pause()
            line = sidebar.render_line(row)
            assert "▸" in line.text and "rtr-core-01" in line.text, "the unfocused list keeps its marker"
            dim = {hexed(c) for c in bars(row)}
            assert roles["surface_3"] in dim and roles["accent_soft"] not in dim
            assert contrast_ratio(roles["text"], roles["surface_3"]) >= 4.5
    asyncio.run(check())


# -- L1-first-focus --


@pytest.mark.parametrize("size", SIZES)
def test_after_login_the_keys_land_in_the_targets_filter(serve, wait_until, screen_text, size):
    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"targets"}
            assert screen.sidebar.editing
            await pilot.press("z", "z")
            await pilot.pause()
            assert screen.sidebar.filter_text == "zz"
            assert "/ zz" in screen.sidebar.render_line(1).text
            await snap(pilot, wait_until, screen, app, f"first-focus-{size[0]}x{size[1]}")
    asyncio.run(check())


# -- L1-rail-new and L1-navigation --


@pytest.mark.parametrize("size", SIZES)
def test_new_is_the_last_selectable_rail_row_and_n_or_enter_opens_it(serve, wait_until, screen_text, size):
    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            await settle(pilot, wait_until, screen, alerts_quiet=True)
            await pilot.press("shift+tab")  # targets -> rail
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"rail"}
            await pilot.press("up")  # from the first row, up wraps to the last
            await pilot.pause()
            y = screen.rail.selected
            assert y is not None and screen.rail.rows[y].action == ACT_NEW
            assert screen.rail.rows[-1].action == ACT_NEW
            roles = role_colours(app.cfg)
            strip = screen.rail.render_line(y)
            assert strip.cell_length == screen.rail.size.width
            hexed = lambda c: "#" + "".join(f"{v:02x}" for v in c)
            assert {hexed(s.style.bgcolor.get_truecolor()) for s in strip if s.text.strip(" ┃│")} == {roles["accent_soft"]}
            for seg in strip:
                if seg.text.strip(" ┃│"):
                    assert contrast_ratio(hexed(seg.style.color.get_truecolor()),
                                          hexed(seg.style.bgcolor.get_truecolor())) >= 4.5, (seg.text, seg.style)
            await settle(pilot, wait_until, screen, alerts_quiet=True)
            await snap(pilot, wait_until, screen, app, f"rail-new-{size[0]}x{size[1]}")
            await pilot.press("enter")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "New session from Enter on + New")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "the dialog to close")
            assert screen.rail.has_focus
            await pilot.press("n")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "New session from n on the rail")
    asyncio.run(check())


@pytest.mark.parametrize("glyph_set", ["unicode", "ascii"])
def test_compact_rows_activate_by_up_down_enter_with_their_exact_marks(serve, wait_until, screen_text, monkeypatch,
                                                                       glyph_set):
    monkeypatch.setenv("TSB_TUI_GLYPHS_SET", glyph_set)
    marks = {"unicode": {ACT_NEEDS: "▸", ACT_FLEET: "▦", ACT_VAULT: "◆", ACT_ALERTS: "!", ACT_MENU: "≡", ACT_NEW: "+"},
             "ascii": {ACT_NEEDS: ">", ACT_FLEET: "#", ACT_VAULT: "*", ACT_ALERTS: "!", ACT_MENU: "=", ACT_NEW: "+"}}

    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await key_login(pilot, app, wait_until)
            await settle(pilot, wait_until, screen, alerts_quiet=True)
            rows = [r for r in screen.rail.rows if r.action in marks[glyph_set]]
            assert [r.action for r in rows] == [ACT_NEEDS, ACT_FLEET, ACT_VAULT, ACT_ALERTS, ACT_MENU, ACT_NEW]
            lines = screen.rail.text.splitlines()
            for index, row in enumerate(screen.rail.rows):
                if row.action in marks[glyph_set]:
                    assert lines[index][0] == marks[glyph_set][row.action], (row.action, lines[index])
                    painted = screen.rail.render_line(index).text
                    assert painted[0] == marks[glyph_set][row.action], painted
            await to_rail(pilot, screen)
            outcomes = []
            for step, row in enumerate(rows):
                if step:
                    await pilot.press("down")
                assert screen.rail.selected_row.action == row.action
                await pilot.press("enter")
                await pilot.pause(0.1)
                if app.screen is not screen:
                    outcomes.append(type(app.screen).__name__)
                    await pilot.press("escape")
                    await wait_until(pilot, lambda: app.screen is screen, "the screen to close")
                elif screen.view_open:
                    outcomes.append("full view:" + screen.fleet_view.tab)
                    await pilot.press("escape")
                    await wait_until(pilot, lambda: not screen.view_open, "the full view to close")
                else:
                    outcomes.append("list:" + ",".join(sorted(heavy_panes(app, screen, screen_text))))
                await to_rail(pilot, screen)
            assert outcomes == ["list:sessions", "list:targets", "CredentialsScreen", "full view:alerts",
                                "MenuPalette", "NewSessionScreen"], outcomes
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_vault_and_alerts_are_reachable_by_keys_alone(serve, wait_until, screen_text, size):
    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            await settle(pilot, wait_until, screen, alerts_quiet=True)
            await pilot.press("shift+tab")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"rail"}
            await pilot.press("down", "down")  # Sessions, Targets, Vault
            assert screen.rail.selected_row.action == ACT_VAULT
            await settle(pilot, wait_until, screen, alerts_quiet=True)
            await snap(pilot, wait_until, screen, app, f"rail-vault-{size[0]}x{size[1]}")
            await pilot.press("enter")
            await wait_until(pilot, lambda: isinstance(app.screen, CredentialsScreen), "Vault to open Credentials")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "Credentials to close")
            await pilot.press("down")  # Alerts
            assert screen.rail.selected_row.action == ACT_ALERTS
            await pilot.press("enter")
            await wait_until(pilot, lambda: screen.view_open and screen.fleet_view.tab == "alerts",
                             "Alerts to open the alerts listing")
            status = screen.status.region
            assert status.right <= size[0] and status.bottom <= size[1]
            assert "Esc Back" in screen.status.text
    asyncio.run(check())


# -- L1-escape --


@pytest.mark.parametrize("size", SIZES)
def test_escape_returns_to_the_open_session_and_the_canvas_gets_tab_and_escape(serve, wait_until, screen_text, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen, (_sid,) = await workspace_with_sessions(app, pilot, wait_until, serve_app, url, "rtr-core-01")
            client = screen.pump.state.client_id
            await to_sessions_list(pilot, screen)
            assert heavy_panes(app, screen, screen_text) == {"sessions"}
            await pilot.press("escape")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"canvas"}, "Esc in the list returns to the open session"
            assert screen.sidebar_open, "Esc leaves the panel open"
            # With a filter typed, the first Esc clears it and stays in the list; the second returns to the session.
            await to_sessions_list(pilot, screen)
            await pilot.press("shift+tab", "slash", "q", "q")
            await pilot.pause()
            assert screen.sidebar.filter_text == "qq" and heavy_panes(app, screen, screen_text) == {"targets"}
            await pilot.press("escape")
            await pilot.pause()
            assert screen.sidebar.filter_text == "" and heavy_panes(app, screen, screen_text) == {"targets"}
            await pilot.press("escape")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"canvas"}, "the second Esc returns to the open session"
            await to_sessions_list(pilot, screen)
            await pilot.press("enter")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"canvas"}, "Enter on a session row enters its canvas"
            await pilot.press("tab", "escape")
            await flushed(pilot, screen, serve_app, wait_until)
            assert serve_app.received(client) == [b"\t", b"\x1b"], serve_app.received(client)
            assert heavy_panes(app, screen, screen_text) == {"canvas"}, "Tab and Esc do not leave the canvas"
            await pilot.press("shift+tab")  # the rail's Esc also returns
            await to_sessions_list(pilot, screen)
            await pilot.press("tab", "escape")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"canvas"}
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_escape_clears_a_no_match_query_then_does_nothing_without_a_session(serve, wait_until, screen_text, size):
    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            await wait_until(pilot, lambda: len(screen.sidebar.model.targets) > 0, "the targets")
            total = len(screen.sidebar.visible_targets())
            await pilot.press("q", "q", "q", "q")
            await pilot.pause()
            assert screen.sidebar.visible_targets() == ()
            await pilot.press("escape")
            await pilot.pause()
            assert screen.sidebar.filter_text == "" and len(screen.sidebar.visible_targets()) == total
            assert heavy_panes(app, screen, screen_text) == {"targets"}
            await pilot.press("escape")
            await pilot.pause()
            assert app.is_running and app.screen is screen, "Esc never exits"
            assert heavy_panes(app, screen, screen_text) == {"targets"}, "with no session open Esc does nothing"
            await pilot.press("shift+tab", "escape")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"rail"}
            await pilot.press("n")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "a dialog")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "Esc to back out of the dialog one level")
            assert app.is_running
    asyncio.run(check())


# -- L1-rail-sessions, L1-rail-activity --


@pytest.mark.parametrize("size", SIZES)
def test_rail_lists_the_open_sessions_by_number_and_name(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen, (first, second, third) = await workspace_with_sessions(
                app, pilot, wait_until, serve_app, url, "core-rtr-01", "sw-2", "edge-firewall-east")
            await pilot.pause()
            chips = [(row.text, row.arg) for row in screen.rail.rows if row.action == ACT_CHIP]
            assert [arg for _, arg in chips] == [first, second, third]
            # The session opened last is in front, and its mark says so.
            if size[0] > 80:
                assert [t for t, _ in chips] == ["1 core-rt… ", "2 sw-2     ", "3 edge-fi…◂"], chips
            else:
                assert [t for t, _ in chips] == ["1  ", "2  ", "3◂ "], chips
            assert all(len(t) == screen.rail.rail_width - 1 for t, _ in chips)
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_a_background_session_that_printed_gets_a_mark_until_it_is_shown(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen, (first, second) = await workspace_with_sessions(
                app, pilot, wait_until, serve_app, url, "core-rtr-01", "sw-2")

            def row_of(sid):
                return next(row.text for row in screen.rail.rows if row.action == ACT_CHIP and row.arg == sid)

            assert screen.front.session_id == second and "•" not in row_of(first)
            emit_live_output(serve_app, serve_app.sessions[first], b"printed in the background\r\n")
            await wait_until(pilot, lambda: "•" in row_of(first), "the activity mark on the background session")
            # The row is the number and the mark; from 81 columns the name stands between them (cut at 12 columns).
            assert row_of(first) == ("1 core-rt…•" if size[0] > 80 else "1• "), row_of(first)
            assert "•" not in row_of(second)
            await settle(pilot, wait_until, screen, alerts_quiet=True)
            await snap(pilot, wait_until, screen, app, f"rail-activity-{size[0]}x{size[1]}")
            await pilot.press(LEADER, "1")
            await wait_until(pilot, lambda: screen.front.session_id == first, "session 1 in front")
            await wait_until(pilot, lambda: "•" not in row_of(first), "showing it clears the mark")
    asyncio.run(check())


# -- L1-rail-enter --


@pytest.mark.parametrize("size", SIZES)
def test_enter_on_sessions_or_targets_moves_the_keys_to_that_list_and_opens_a_closed_panel(
        serve, wait_until, screen_text, size):
    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            await pilot.press("shift+tab")  # the rail
            await pilot.press(LEADER, "f")  # closes the panel; the rail keeps the keys
            await pilot.pause()
            assert not screen.sidebar_open and screen.rail.has_focus
            await pilot.press("enter")  # Sessions, the first row
            await wait_until(pilot, lambda: screen.sidebar_open, "the panel to open")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"sessions"}
            await pilot.press("shift+tab", "shift+tab")  # sessions -> targets -> rail
            await pilot.press(LEADER, "f")
            await pilot.pause()
            assert not screen.sidebar_open and screen.rail.has_focus
            await pilot.press("down", "enter")  # Targets
            await wait_until(pilot, lambda: screen.sidebar_open, "the panel to open on Targets")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"targets"}
    asyncio.run(check())


# -- L1-words --


def _everything_readable(app, screen):
    texts = [screen.status.text, screen.rail.text, screen.sidebar.text]
    texts += [screen.rail.tip_for(y) or "" for y in range(len(screen.rail.rows))]
    return "\n".join(texts)


@pytest.mark.parametrize("size", SIZES)
def test_fleet_and_credentials_are_gone_from_every_label_and_the_old_names_still_find_their_items(
        serve, wait_until, size):
    from tsb_tui.screens.chrome_dialogs import keys_lines

    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            await pilot.pause()
            for open_state in (True, False):
                assert not re.search(r"fleet|credentials…", _everything_readable(app, screen), re.IGNORECASE)
                await pilot.press(LEADER, "f")
                await pilot.pause()
            await pilot.press(LEADER, "F")
            await pilot.pause()
            assert not re.search(r"fleet", screen.fleet_view.text, re.IGNORECASE), screen.fleet_view.text
            await pilot.press("escape")
            await pilot.press("f1")
            await wait_until(pilot, lambda: isinstance(app.screen, MenuPalette) and bool(app.screen.query(CommandList))
                             and app.screen.query_one(CommandList).option_count > 0, "the Menu")
            palette = app.screen
            names = [o.prompt.plain for o in palette.query_one(CommandList).options]
            assert not re.search(r"fleet|credentials…", "\n".join(names), re.IGNORECASE), names
            assert any("Vault…" in n for n in names)
            assert any("Targets and sessions" in n for n in names)
            for word, wanted in (("Credentials", "Vault…"), ("Fleet", "Targets and sessions")):
                await pilot.press(*word)
                await wait_until(pilot, lambda wanted=wanted: bool(palette.query_one(CommandList).options)
                                 and any(wanted in o.prompt.plain for o in palette.query_one(CommandList).options),
                                 f"{word} to find {wanted}")
                for _ in word:
                    await pilot.press("backspace")
            await pilot.press("escape")
            assert not re.search(r"fleet|credentials…", "\n".join(keys_lines(app.cfg)), re.IGNORECASE)
    asyncio.run(check())


def test_the_menu_puts_devices_and_lock_beside_the_vault():
    labels = [a.label for a in A.menu_actions()]
    vault = labels.index("Vault…")
    assert labels[vault:vault + 3] == ["Vault…", "Devices…", "Lock now"]


# -- L1-one-meaning --


# What one letter says in both places is one meaning; a letter that says two things is a conflict. The side panel and
# the full view write their letters as the hints they show; the leader's are the action table's.
SAME_MEANING = {"t": "take back", "w": "who's here", "x": "end", "F": "full view", "n": "new session"}
# No letter says two things: the Event log's letter after the leader moved off `e`, which the full view's
# Targets tab keeps for Edit target.
STILL_OPEN = set()


def _list_letters(screen):
    """Single-character keys of every list's hints with the action each names."""
    letters = {}
    sidebar = screen.sidebar
    for pane in ("targets", "sessions"):
        sidebar.pane = pane
        target = screen._sidebar_keys_target()
        for hint in target.hints:
            if len(hint.key) == 1 and hint.key.isalpha():
                letters.setdefault(hint.key, set()).add(hint.label.lower())
    return letters


def test_no_letter_means_two_things_between_the_lists_and_the_leader(serve, wait_until):
    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await key_login(pilot, app, wait_until)
            app.permissions = frozenset({"target.edit"})
            letters = _list_letters(screen)
            await pilot.press(LEADER, "F", "tab")
            await pilot.pause()
            for hint in screen._keys_target().hints:
                if len(hint.key) == 1 and hint.key.isalpha():
                    letters.setdefault(hint.key, set()).add(hint.label.lower())
            leader = {letter: A.BY_ID[action_id].label.lower().rstrip("…")
                      for action_id, letter in A.leader_keys().items() if len(letter) == 1}
            conflicts = {letter for letter, labels in letters.items() if letter in leader
                         and not any(SAME_MEANING.get(letter, "\0") in label for label in labels)
                         or (letter in leader and SAME_MEANING.get(letter) and SAME_MEANING[letter] not in leader[letter])}
            assert conflicts == STILL_OPEN, conflicts
            assert letters["n"] == {"new session"} and leader["n"] == "new session"
            assert "D" in letters and "d" not in letters
            assert A.leader_keys()["next"] == "→" and A.leader_keys()["prev"] == "←"
            assert "p" not in A.leader_keys().values()
            assert A.leader_keys()["answer"] == "y" and "a" in letters and "a" not in leader
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_leader_n_is_new_session_arrows_step_sessions_c_still_works(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen, (first, second, third) = await workspace_with_sessions(
                app, pilot, wait_until, serve_app, url, "rtr-1", "rtr-2", "rtr-3")
            assert screen.front.session_id == third
            await pilot.press(LEADER, "left")
            await wait_until(pilot, lambda: screen.front.session_id == second, "the previous session")
            await pilot.press(LEADER, "left")
            await wait_until(pilot, lambda: screen.front.session_id == first, "the previous session again")
            await pilot.press(LEADER, "right")
            await wait_until(pilot, lambda: screen.front.session_id == second, "the next session")
            await pilot.press(LEADER, "n")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "New session after the leader and n")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "the dialog to close")
            await pilot.press(LEADER, "c")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "c is still an alias")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "the dialog to close")
            await pilot.press(LEADER, "right")
            await wait_until(pilot, lambda: screen.front.session_id == third, "the next session once more")
            menu = {a.id: key for a, key, _ in screen.menu_entries()}
            assert menu["next"].endswith("→") and menu["prev"].endswith("←") and menu["new_session"].endswith(" n")
    asyncio.run(check())


def test_drive_documents_its_leader_sequences_unchanged():
    import tsb_tui

    guide = (Path(tsb_tui.__file__).parent / "drive" / "GUIDE.md").read_text()
    assert '["ctrl+backslash", "w"]' in guide and '["ctrl+backslash", "left_square_bracket"]' in guide
    assert A.leader_keys()["who"] == "w" and A.leader_keys()["scroll"] == "["


# -- L1-F1-passthrough --


@pytest.mark.parametrize("size", SIZES)
def test_leader_then_f1_sends_f1_to_the_session_and_plain_f1_opens_the_menu(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen, (_sid,) = await workspace_with_sessions(app, pilot, wait_until, serve_app, url, "rtr-core-01")
            client = screen.pump.state.client_id
            await pilot.press("f1")
            await wait_until(pilot, lambda: isinstance(app.screen, MenuPalette), "plain F1 opens the Menu")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "the Menu to close")
            await pilot.press(LEADER, "f1")
            await wait_until(pilot, lambda: b"\x1bOP" in serve_app.received(client), "F1 to reach the session")
            assert app.screen is screen, "the Menu is not left open"
            assert not screen.leader_pending
            assert serve_app.received(client) == [b"\x1bOP"]
    asyncio.run(check())


# -- L1-token-notice --


@pytest.mark.parametrize("size", SIZES)
def test_the_frame_says_so_when_the_keyboard_moves_to_you(serve, wait_until, screen_text, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            with other_client(url, _serve.PEER, sid):  # the peer attaches first and holds the keyboard
                screen = await key_login(pilot, app, wait_until)
                await open_here(app, pilot, wait_until, sid)
                assert not screen.pump.state.holds_keyboard
                frame = screen.query_one("#canvases")
                assert "you type now" not in str(frame.border_subtitle)
            await wait_until(pilot, lambda: screen.pump.state.holds_keyboard, "the keyboard to come to this client")
            await wait_until(pilot, lambda: "you type now" in screen_text(app), "the notice in the frame")
            await wait_until(pilot, lambda: any("you type" in screen.sidebar.render_line(y).text
                                                for y in range(screen.sidebar.size.height)),
                             "the panel's row to say you type")
            region = frame.region
            bottom = screen_text(app).splitlines()[region.bottom - 1][region.x:region.right]
            assert f"you type now: {_serve.PEER} left" in bottom, bottom
            assert bottom.startswith("┗") and bottom.endswith("┛")
            await snap(pilot, wait_until, screen, app, f"token-notice-{size[0]}x{size[1]}")
    asyncio.run(check())


# -- L1-detach --


@pytest.mark.parametrize("size", SIZES)
def test_ctrl_right_bracket_detaches_from_the_canvas_and_the_lists_and_the_hint_never_drops(
        serve, wait_until, screen_text, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen, _sids = await workspace_with_sessions(
                app, pilot, wait_until, serve_app, url, "rtr-core-01", "rtr-core-02")
            clients = [slot.pump.state.client_id for slot in screen.slots]
            await to_sessions_list(pilot, screen)
            for stop in ("sessions", "targets", "rail"):
                assert "Ctrl+] detach" in screen.status.text, (stop, screen.status.text)
                await pilot.press("tab")
                await pilot.pause()
            await pilot.press("escape")  # the rail: back to the canvas
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"canvas"}
            assert "Ctrl+] detach" in screen.status.text, screen.status.text
            region = screen.status.region
            assert region.right <= size[0] and region.bottom <= size[1]
            await snap(pilot, wait_until, screen, app, f"detach-hint-{size[0]}x{size[1]}")
            await pilot.press(DETACH)  # from the canvas
            await wait_until(pilot, lambda: len(screen.slots) == 1, "the front session to detach")
            assert screen.front.session_id == _sids[0], "the session after the detached one comes to the front"
            await to_sessions_list(pilot, screen)
            await pilot.press(DETACH)  # from a list
            await wait_until(pilot, lambda: len(screen.slots) == 0, "the last session to detach from the list")
            await pilot.pause()
            assert "Ctrl+] detach" not in screen.status.text, "no session is open: no hint"
            await flushed_clients(pilot, serve_app, clients, wait_until)
    asyncio.run(check())


async def flushed_clients(pilot, serve_app, clients, wait_until):
    await pilot.pause(0.1)
    for client in clients:
        assert b"\x1d" not in b"".join(serve_app.received(client)), "Ctrl+] reached a session"


@pytest.mark.parametrize("size", SIZES)
def test_the_menu_keeps_a_detach_item_and_the_leader_letter_still_detaches(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen, _sids = await workspace_with_sessions(
                app, pilot, wait_until, serve_app, url, "rtr-core-01", "rtr-core-02")
            keys = {a.id: key for a, key, _ in screen.menu_entries()}
            assert keys["detach"] == "Ctrl+]"
            await pilot.press(LEADER, "d")
            await wait_until(pilot, lambda: len(screen.slots) == 1, "the leader letter to still detach")
            await pilot.press("f1")
            await wait_until(pilot, lambda: isinstance(app.screen, MenuPalette) and bool(app.screen.query(CommandList))
                             and app.screen.query_one(CommandList).option_count > 0, "the Menu")
            palette = app.screen
            await pilot.press(*"detach")
            await wait_until(pilot, lambda: any("Detach" in o.prompt.plain for o in palette.query_one(CommandList).options),
                             "the Detach item")
            await pilot.press("enter")
            await wait_until(pilot, lambda: len(screen.slots) == 0, "the Menu item to detach")
    asyncio.run(check())


# -- L1-fold --


@pytest.mark.parametrize("size", SIZES)
def test_z_folds_the_other_list_and_z_again_restores(serve, wait_until, screen_text, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen, (_sid,) = await workspace_with_sessions(app, pilot, wait_until, serve_app, url, "rtr-core-01")
            await wait_until(pilot, lambda: len(screen.sidebar.model.targets) == 8 and not screen.sidebar.model.targets_loading
                             and not screen.sidebar.model.sessions_loading, "the targets and sessions", 40)
            sidebar = screen.sidebar
            height = sidebar.size.height
            before = (sidebar.targets_height, sidebar.sessions_height)
            await to_sessions_list(pilot, screen)
            await pilot.press("z")  # the Sessions list has the keys: Targets fold
            await pilot.pause()
            assert (sidebar.targets_height, sidebar.sessions_height) == (1, height - 1)
            await wait_until(pilot, lambda: "▦ Targets · 1/8" in sidebar.render_line(0).text,
                             "folded targets title", 40)
            title = sidebar.render_line(0).text
            assert "▦ Targets" in title and re.search(r"· \d+", title), title
            await snap(pilot, wait_until, screen, app, f"fold-targets-{size[0]}x{size[1]}")
            await pilot.press("z")
            await pilot.pause()
            assert (sidebar.targets_height, sidebar.sessions_height) == before
            await pilot.press("tab", "tab", "z")  # rail, then Targets, which has the keys: Sessions fold
            await pilot.pause()
            assert (sidebar.targets_height, sidebar.sessions_height) == (height - 1, 1)
            await wait_until(pilot, lambda: "▸ Sessions · 1" in sidebar.render_line(height - 1).text,
                             "folded sessions line", 40)
            line = sidebar.render_line(height - 1).text
            assert "▸ Sessions" in line and "· 1" in line, line
            await snap(pilot, wait_until, screen, app, f"fold-sessions-{size[0]}x{size[1]}")
            await pilot.press("z")
            await pilot.pause()
            assert (sidebar.targets_height, sidebar.sessions_height) == before
            assert sidebar.folded is None
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_a_click_on_a_list_title_folds_it_and_the_same_click_unfolds(serve, wait_until, size):
    from textual.widgets import Static  # noqa: F401

    from tsb_tui.widgets.sidebar import Sidebar

    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen, (_sid,) = await workspace_with_sessions(app, pilot, wait_until, serve_app, url, "rtr-core-01")
            await wait_until(pilot, lambda: len(screen.sidebar.model.targets) > 1, "the targets")
            sidebar = screen.sidebar
            height = sidebar.size.height
            before = (sidebar.targets_height, sidebar.sessions_height)
            await pilot.click(Sidebar, offset=(4, 0))
            await pilot.pause()
            assert (sidebar.targets_height, sidebar.sessions_height) == (1, height - 1)
            await pilot.click(Sidebar, offset=(4, 0))
            await pilot.pause()
            assert (sidebar.targets_height, sidebar.sessions_height) == before
            await pilot.click(Sidebar, offset=(4, sidebar.targets_height))
            await pilot.pause()
            assert (sidebar.targets_height, sidebar.sessions_height) == (height - 1, 1)
            await pilot.click(Sidebar, offset=(4, height - 1))
            await pilot.pause()
            assert (sidebar.targets_height, sidebar.sessions_height) == before
    asyncio.run(check())


# -- Phase 06 acceptance tests: L1-focus, L1-hints, L1-escape --


@pytest.mark.parametrize("size", SIZES)
def test_full_flow_focus_chain_forward_and_back(serve, wait_until, screen_text, size):
    """L1-focus: Full login -> targets -> open -> attach consent -> session -> leader -> sidebar/full view
    -> new -> cancel flow, plus Vault/All alerts and every other screen. Assert exact focus chain and reverse traversal."""
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            # 1. LoginScreen: check focus on inputs, Tab and Shift+Tab traversal
            assert app.screen.query_one("#username").has_focus, "Username input starts focused"
            await pilot.press("tab")
            await pilot.pause()
            assert app.screen.query_one("#password").has_focus, "Password input receives focus on Tab"
            await pilot.press("shift+tab")
            await pilot.pause()
            assert app.screen.query_one("#username").has_focus, "Username input recovers focus on Shift+Tab"

            # Log in
            screen = await key_login(pilot, app, wait_until)
            app.permissions = frozenset({"target.edit", "broker.health"})
            await pilot.pause()

            # 2. Cockpit first-focus: targets filter
            assert heavy_panes(app, screen, screen_text) == {"targets"}, "Targets list has first focus"

            # 3. Focus chain forward: targets -> sessions -> rail -> targets
            await pilot.press("tab")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"sessions"}, "Tab moves targets -> sessions"

            await pilot.press("tab")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"rail"}, "Tab moves sessions -> rail"

            await pilot.press("tab")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"targets"}, "Tab moves rail -> targets"

            # 4. Reverse traversal: targets -> rail -> sessions -> targets
            await pilot.press("shift+tab")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"rail"}, "Shift+Tab moves targets -> rail"

            await pilot.press("shift+tab")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"sessions"}, "Shift+Tab moves rail -> sessions"

            await pilot.press("shift+tab")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"targets"}, "Shift+Tab moves sessions -> targets"

            # 5. Open session and check canvas focus
            (sid,) = named_sessions(url, serve_app, "rtr-core-01")
            await open_here(app, pilot, wait_until, sid)
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"canvas"}, "Open session gives canvas focus"

            # 6. Full view forward and reverse
            await pilot.press(LEADER, "F")
            await wait_until(pilot, lambda: screen.view_open, "full view to open")
            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("shift+tab")
            await pilot.pause()
            await pilot.press("escape")
            await wait_until(pilot, lambda: not screen.view_open, "full view to close")
            assert heavy_panes(app, screen, screen_text) == {"canvas"}

            # 7. New session dialog forward and reverse traversal
            await pilot.press(LEADER, "n")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "NewSessionScreen to open")
            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("shift+tab")
            await pilot.pause()
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "NewSessionScreen to close")
            assert heavy_panes(app, screen, screen_text) == {"canvas"}

            # 8. Vault (CredentialsScreen) forward and reverse traversal
            await pilot.press(LEADER, "C")
            await wait_until(pilot, lambda: isinstance(app.screen, CredentialsScreen), "CredentialsScreen to open")
            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("shift+tab")
            await pilot.pause()
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "CredentialsScreen to close")

            # 9. All alerts via rail
            await to_rail(pilot, screen)
            alerts_index = next(i for i, r in enumerate(screen.rail.rows) if r.action == ACT_ALERTS)
            screen.rail.selected = alerts_index
            await pilot.press("enter")
            await wait_until(pilot, lambda: screen.view_open and screen.fleet_view.tab == "alerts", "Alerts view")
            await pilot.press("escape")
            await wait_until(pilot, lambda: not screen.view_open, "Alerts view to close")

    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_hint_row_inside_viewport(serve, wait_until, size):
    """L1-hints / V5: Status row and key hints are inside viewport at both sizes, styled apart, spelled Ctrl+\\ never ^\\."""
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen, (_sid,) = await workspace_with_sessions(app, pilot, wait_until, serve_app, url, "rtr-core-01")
            await pilot.pause()

            # Viewport containment check
            region = screen.status.region
            assert region.right <= size[0], f"Status row exceeded viewport width: {region.right} > {size[0]}"
            assert region.bottom <= size[1], f"Status row exceeded viewport height: {region.bottom} > {size[1]}"

            # Hint syntax and styling rules: Ctrl+\ never ^\
            status_text = screen.status.text
            assert "^\\" not in status_text, f"Legacy leader ^\\ found in status text: {status_text}"
            assert "Ctrl+\\" in status_text, f"Expected Ctrl+\\ in status text: {status_text}"

            # Spacing rule: at least one space between key and label
            pieces = screen.status._pieces
            hint_key_pieces = [p for p in pieces if p.role == "hint_key"]
            assert hint_key_pieces, "Status row must contain hint keys"
            for i, p in enumerate(pieces[:-1]):
                if p.role == "hint_key" and pieces[i + 1].role == "hint":
                    assert pieces[i + 1].text.startswith(" "), f"Hint {p.text}{pieces[i + 1].text} missing space"

            # Protected hints: F1 Menu key first
            assert "F1 Menu" in status_text, f"Protected hint 'F1 Menu' missing: {status_text}"

    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_escape_behaviour_per_screen(serve, wait_until, screen_text, size):
    """L1-escape / V10: Esc clears query in filter; returns to canvas with open session; does nothing without session;
    backs out one level in dialogs; reaches target on attached canvas."""
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            await pilot.pause()

            # 1. In list without session open: empty Esc does nothing (never exits)
            assert heavy_panes(app, screen, screen_text) == {"targets"}
            await pilot.press("escape")
            await pilot.pause()
            assert app.is_running and app.screen is screen
            assert heavy_panes(app, screen, screen_text) == {"targets"}, "Esc does nothing without session"

            # 2. In filter with query text: Esc clears query text and stays in list
            await pilot.press("slash", "q", "u", "e", "r", "y")
            await pilot.pause()
            assert screen.sidebar.filter_text == "query"
            await pilot.press("escape")
            await pilot.pause()
            assert screen.sidebar.filter_text == "", "Esc clears query"
            assert heavy_panes(app, screen, screen_text) == {"targets"}, "Focus remains in targets after clearing query"

            # 3. In dialogs: Esc backs out one level, never exits
            await pilot.press("n")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "NewSessionScreen to open")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "Esc backs out of NewSessionScreen")
            assert app.is_running

            # 4. Open a session
            (sid,) = named_sessions(url, serve_app, "rtr-core-01")
            await open_here(app, pilot, wait_until, sid)
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"canvas"}

            # 5. List with open session: Esc returns focus to canvas, leaving sidebar open
            await to_sessions_list(pilot, screen)
            assert heavy_panes(app, screen, screen_text) == {"sessions"}
            await pilot.press("escape")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"canvas"}, "Esc in list returns to canvas"
            assert screen.sidebar_open, "Sidebar remains open after Esc"

            # 6. Attached canvas: Esc reaches session as \x1b byte and does not leave canvas
            client = screen.pump.state.client_id
            await pilot.press("escape")
            await flushed(pilot, screen, serve_app, wait_until)
            assert b"\x1b" in serve_app.received(client), "Esc reaches the session"
            assert heavy_panes(app, screen, screen_text) == {"canvas"}, "Canvas retains focus"

    asyncio.run(check())
