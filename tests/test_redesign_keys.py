"""Keys-only P1 acceptance: Menu, list actions, visible hints and two-size gallery."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from snapshot_support import settle_snapshot
from textual.command import CommandList, SearchIcon
from textual.widgets import Input
from tsbc import _serve

from tsb_tui.app import MenuPalette, TsbTuiApp
from tsb_tui.screens.lock import LockScreen
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.screens.target_form import TargetFormScreen
from tsb_tui.screens.terminal import TerminalScreen

SIZES = [(80, 24), (160, 45)]


async def login(pilot, app, wait_until):
    await pilot.press(*_serve.USER, "tab", *_serve.PASSWORD, "enter")
    await wait_until(pilot, lambda: isinstance(app.screen, TerminalScreen)
                     and app.screen.sidebar is not None, "workspace")
    await pilot.pause()
    return app.screen


async def snapshot(app, pilot, name):
    path = Path(__file__).parent / "snapshots" / "redesign-p1" / f"{name}.svg"
    await settle_snapshot(
        app, pilot, path, title="P1", terminal_tag="terminal-p1", update_env="UPDATE_P1_SNAPSHOTS"
    )


@pytest.mark.parametrize("size", SIZES)
def test_f1_menu_search_focus_and_hints(serve, wait_until, size):
    async def check():
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            workspace = await login(pilot, app, wait_until)
            for keys in ((), ("ctrl+backslash", "F"), ("ctrl+backslash", "c")):
                if keys:
                    await pilot.press(*keys)
                    await pilot.pause()
                caller = app.screen
                focus = caller.focused
                await pilot.press("f1")
                await pilot.pause()
                assert isinstance(app.screen, MenuPalette), type(app.screen)
                palette = app.screen
                await wait_until(pilot, lambda palette=palette: bool(palette.query(CommandList))
                                 and palette.query_one(CommandList).option_count > 0, "menu results")
                await pilot.press("n", "e", "w")
                await wait_until(pilot, lambda palette=palette: palette.query_one(Input).value == "new", "search typing")
                options = palette.query_one(CommandList)
                assert options.options
                assert not options.get_component_rich_style("option-list--option").bold
                assert not options.get_component_rich_style("option-list--option-highlighted").bold
                assert all("New" in option.hit.text for option in options.options)
                assert options.highlighted is not None
                assert not options.get_option_at_index(options.highlighted).disabled
                starts = [option.prompt.plain.index("New") for option in options.options]
                assert len(set(starts)) == 1
                assert all("New session" in option.prompt.plain for option in options.options)
                box = palette.query_one("#--container").region
                assert box.width > 0 and box.height > 0
                assert 0 <= box.x and box.right <= size[0] and 0 <= box.y and box.bottom <= size[1]
                rows = [strip.text for strip in palette._compositor.render_strips()]
                assert rows[box.y][box.x] == "╭" and rows[box.y][box.right - 1] == "╮"
                assert rows[box.bottom - 1][box.x] == "╰" and rows[box.bottom - 1][box.right - 1] == "╯"
                icon = palette.query_one(SearchIcon).region
                assert rows[icon.y][icon.x] == "/"
                context = "workspace" if not keys else "list" if keys[-1] == "F" else "new-session"
                await snapshot(app, pilot, f"menu-{context}-{size[0]}x{size[1]}")
                await pilot.press("escape")
                await pilot.pause()
                assert app.screen is caller
                assert caller.focused is focus
                if caller is not workspace:
                    await pilot.press("escape")
                    await pilot.pause()
            # Close sidebar to expose the session's permanent Menu hint.
            await pilot.press("ctrl+backslash", "f")
            await pilot.pause()
            row = workspace.status
            assert "^\\" not in row.text
            assert row._inputs.leader == "Ctrl+\\"
            assert row._styles["hint_key"] != row._styles["hint"]
            assert row._styles["hint_key"].bold
            assert row.region.y < size[1] and row.region.right <= size[0]
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("permission", [False, True])
def test_inventory_n_and_permission_gated_a(serve, wait_until, size, permission):
    async def check():
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            workspace = await login(pilot, app, wait_until)
            app.permissions = frozenset({"target.edit"}) if permission else frozenset()
            await pilot.press("ctrl+backslash", "F", "tab")
            await pilot.pause()
            assert workspace.fleet_view.tab == "targets"
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(app.screen, NewSessionScreen), type(app.screen)
            await pilot.press("a", "n")
            assert app.screen.query_one(Input).value.endswith("an")
            # New session's first Escape leaves and clears the filter, a second goes back one level: two
            # Escapes close it to the inventory list, where `a` opens the target form with target.edit.
            await pilot.press("escape", "escape", "a")
            await pilot.pause()
            assert isinstance(app.screen, TargetFormScreen) is permission
            if permission:
                await pilot.press("escape")
            await pilot.press("/", "n", "a")
            await pilot.pause()
            assert workspace.fleet_view.editing
            # rule 1 (2026-11-21): the Targets filter is local free text, stored in `filter_text` (the structured
            # query and its `query_text` are retired).
            assert workspace.fleet_view.filter_text.endswith("na")
    asyncio.run(check())


def test_f1_is_inert_on_login_and_lock(serve, wait_until):
    async def check():
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test() as pilot:
            caller, focus = app.screen, app.screen.focused
            await pilot.press("f1")
            await pilot.pause()
            assert app.screen is caller and caller.focused is focus
            await login(pilot, app, wait_until)
            await pilot.press("ctrl+backslash", "L")
            await wait_until(pilot, lambda: isinstance(app.screen, LockScreen), "lock")
            focus = app.screen.focused
            await pilot.press("f1")
            await pilot.pause()
            assert isinstance(app.screen, LockScreen) and app.screen.focused is focus
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("action", ["Settings", "About", "Credentials", "Devices", "Change my password", "Theme"])
def test_f1_over_menu_opened_dialogs(serve, wait_until, size, action):
    async def check():
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            await login(pilot, app, wait_until)
            await pilot.press("f1")
            await wait_until(pilot, lambda: isinstance(app.screen, MenuPalette), "Menu")
            await pilot.press(*action)
            await wait_until(pilot, lambda: bool(app.screen.query(CommandList))
                             and app.screen.query_one(CommandList).option_count > 0, "matching action")
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, MenuPalette)
            caller, focus = app.screen, app.screen.focused
            await pilot.press("f1")
            await pilot.pause()
            assert isinstance(app.screen, MenuPalette)
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is caller and caller.focused is focus
    asyncio.run(check())


def test_status_shortens_session_before_dropping_the_lowest_hint():
    from tsb_tui.widgets.status_row import RANK_NAME, Item, Piece, _fit

    left = [Item("text", ("a-long-session-name",), RANK_NAME)]
    hints = [("low", [Piece("l Low", "hint")]), ("menu", [Piece("F1 Menu", "hint")])]
    text, kept = _fit(left, hints, ["low"], 22)
    assert [name for name, _ in kept] == ["low", "menu"]
    assert "".join(piece.text for piece in text).endswith("…")
    text, kept = _fit(left, hints, ["low"], 14)
    assert [name for name, _ in kept] == ["menu"]
    assert "".join(piece.text for piece in text).endswith("…")
