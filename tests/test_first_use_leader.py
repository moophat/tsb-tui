"""The leader owns keys until search is explicitly requested."""
from __future__ import annotations

import asyncio

import pytest
from textual.command import CommandList
from textual.widgets import Input
from workspace_support import fixture_login

from tsb_tui.app import MenuPalette, TsbTuiApp


def test_leader_waits_and_dispatches_before_search(serve, wait_until) -> None:
    async def check() -> None:
        _, url = serve()
        app = TsbTuiApp(url)
        app.cfg.set_runtime('menu.open_on_leader_after_ms', 10000)
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = await fixture_login(app, pilot)
            initial = workspace.sidebar_open
            await pilot.press('ctrl+backslash')
            await pilot.pause()
            assert isinstance(app.screen, MenuPalette), 'leader must open the menu immediately'
            palette = app.screen
            search = palette.query_one(Input)
            await pilot.pause(2)
            await pilot.press('f')
            await pilot.pause()
            assert app.screen is workspace
            assert workspace.sidebar_open is not initial
            assert search.value == ''
    asyncio.run(check())


@pytest.mark.parametrize("unknown_key", ["z", "enter", "down"])
def test_leader_unknown_key_and_explicit_search(serve, wait_until, unknown_key) -> None:
    async def check() -> None:
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = await fixture_login(app, pilot)
            await pilot.press('ctrl+backslash')
            await wait_until(pilot, lambda: isinstance(app.screen, MenuPalette), 'the menu')
            palette = app.screen
            await wait_until(pilot, lambda: bool(palette.query(Input)), 'the menu search to mount')
            search = palette.query_one(Input)
            before = workspace.sidebar_open
            await pilot.press(unknown_key)
            await pilot.pause()
            assert app.screen is palette
            assert search.value == ''
            assert workspace.sidebar_open == before
            assert 'no shortcut' in str(palette.query_one('#menu-key-message').render()).lower()
            await pilot.press('/', 't', 'h', 'e', 'm', 'e')
            await pilot.pause()
            assert search.value == 'theme'
            options = palette.query_one(CommandList).options
            assert options and options[0].hit.text == 'Theme…'
            assert len(options) < len(workspace.menu_entries())
    asyncio.run(check())


@pytest.mark.parametrize("width", [65, 120])
def test_menu_disabled_rows_and_columns(serve, wait_until, width) -> None:
    async def check() -> None:
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(width, 40)) as pilot:
            workspace = await fixture_login(app, pilot)
            app.open_menu()
            await pilot.pause()
            palette = app.screen
            options = palette.query_one(CommandList)
            entries = {action.label: why for action, _, why in workspace.menu_entries()}
            assert options.option_count
            for option in options.options:
                assert option.disabled == (entries[option.hit.text] is not None)
                assert '\n' not in option.prompt.plain
            assert options.highlighted is not None
            assert not options.get_option_at_index(options.highlighted).disabled
            assert palette.query_one('#--container').styles.border_top[0] != 'none'
            lines = [strip.text for strip in palette._compositor.render_strips()]
            assert any('Auto size on or off' in line for line in lines)
            unavailable = next(i for i, option in enumerate(options.options) if option.disabled)
            await pilot.click(options, offset=(2, unavailable))
            await pilot.pause()
            assert app.screen is palette
            await pilot.press('escape')
            assert app.screen is workspace
            assert not workspace.leader_pending
    asyncio.run(check())


def test_menu_search_matches_name_word_starts_and_search_off_preserves_all(serve, wait_until) -> None:
    async def check() -> None:
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = await fixture_login(app, pilot)
            app.open_menu()
            await pilot.pause()
            palette = app.screen
            await pilot.press('/')
            await pilot.press('n', 'e', 'w')
            await pilot.pause()
            options = palette.query_one(CommandList).options
            names = {o.hit.text for o in options}
            assert 'New session…' in names and 'New session on this connection' in names
            assert 'Event log' not in names
            assert all('\n' not in o.prompt.plain for o in options)
            app.cfg.set_runtime('menu.search', False)
            await pilot.press('escape')
            app.open_menu()
            await pilot.pause()
            palette = app.screen
            await pilot.press('/')
            await pilot.press('q', 'q', 'q')
            await pilot.pause()
            assert len(palette.query_one(CommandList).options) == len(workspace.menu_entries())
    asyncio.run(check())


def test_menu_match_highlight_preserves_spacing_and_footer_keys(serve, wait_until) -> None:
    async def check() -> None:
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = await fixture_login(app, pilot)
            workspace.open_sidebar()
            await pilot.pause()
            footer = workspace.status.render_line(0)
            text = footer.text
            assert '⏎ Open' in text and 't Take back' in text
            assert '⏎Open' not in text and 'tTake back' not in text and 'FFull view' not in text
            assert any(s.style != workspace.status._styles['hint'] for s in footer if '⏎' in s.text)
            app.open_menu()
            await pilot.pause()
            await pilot.press('/', 'n', 'e', 'w')
            await pilot.pause()
            palette = app.screen
            options = palette.query_one(CommandList).options
            option = next(o for o in options if o.hit.text == 'New session…')
            assert 'New session…' in option.prompt.plain
            assert any('underline' in str(span.style) for span in option.prompt.spans)
            lines = [strip.text for strip in palette._compositor.render_strips()]
            assert any('New session…' in line for line in lines)
    asyncio.run(check())


@pytest.mark.parametrize(('query', 'expected'), [
    ('NEW SES', {'New session…', 'New session on this connection'}),
    ('ses new', {'New session…', 'New session on this connection'}),
    ('ession', set()),
    ('newest', set()),
])
def test_menu_search_requires_each_name_word_prefix(serve, wait_until, query, expected) -> None:
    from tsb_tui.app import MenuProvider

    async def check() -> None:
        _, url = serve()
        app = TsbTuiApp(url)
        assert app.cfg['menu.search'] is True
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = await fixture_login(app, pilot)
            hits = [hit async for hit in MenuProvider(workspace).search(query)]
            assert {hit.text for hit in hits} == expected
    asyncio.run(check())
