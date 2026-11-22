"""P2a ruled rail and tab geometry. AI-Model: gpt-6.1-sol."""
from __future__ import annotations

import asyncio
import re

import pytest
from textual.app import App, ComposeResult

from tsb_tui import glyphs
from tsb_tui.config import load
from tsb_tui.screens.fleet_view import (
    TAB_ALERTS,
    TAB_SESSIONS,
    TAB_TARGETS,
    FleetView,
    FleetViewModel,
)
from tsb_tui.widgets.rail import ACT_ALERTS, ACT_NEW, RailModel, rail_rows

from conftest import _screen_text


@pytest.mark.parametrize("glyph_set,marks", [("unicode", ["▸", "▦", "◆", "!0", "≡", "+"]),
                                             ("ascii", [">", "#", "*", "!0", "=", "+"])])
def test_compact_rail_exact_marks_and_new_last(glyph_set, marks):
    rows = rail_rows(RailModel((), False, 0, 0), 3)
    entries = [glyphs.render(row.text, glyph_set).strip() for row in rows if row.action]
    assert entries == marks
    assert rows[-1].action == ACT_NEW


@pytest.mark.parametrize("glyph_set,marks", [("unicode", ["▸", "▦", "◆", "!", "≡", "+"]),
                                             ("ascii", [">", "#", "*", "!", "=", "+"])])
def test_worded_rail_shows_mark_and_word_together_in_order(glyph_set, marks):
    """From 81 columns each row is its mark then its word; the rail stays 12 columns, so the word that does not fit
    beside the mark would be cut with an ellipsis, never its mark; the needs-you count of Sessions does not fit and shows as the row's colour."""
    for alerts in (None, 0, 3):
        rows = rail_rows(RailModel((), False, 0, alerts), 11)
        shown = [glyphs.render(row.text, glyph_set).strip() for row in rows if row.action]
        words = ["Sessions", "Targets", "Vault"] + ([f"Alerts {alerts}"] if alerts is not None else []) + ["Menu", "New"]
        wanted_marks = marks if alerts is not None else [m for i, m in enumerate(marks) if i != 3]
        assert shown == [f"{mark} {word}" for mark, word in zip(wanted_marks, words)], shown
        assert any(row.action == ACT_ALERTS for row in rows) == (alerts is not None)
        assert all(len(row.text) == 11 for row in rows)
        assert rows[-1].action == ACT_NEW


@pytest.mark.parametrize("size", [(80, 24), (160, 45)])
@pytest.mark.parametrize("monochrome", [False, True])
def test_tabs_exact_underlines_and_bold(tmp_path, monkeypatch, size, monochrome):
    if monochrome:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    config = load(config_file=tmp_path / "absent", environ={})
    view = FleetView(config, leader="Ctrl+\\")
    view.styles.display = "block"
    model = FleetViewModel((), (), "mine", (), "", tabs=(TAB_SESSIONS, TAB_TARGETS, TAB_ALERTS),
                           targets_count="0")

    class TabApp(App):
        def compose(self) -> ComposeResult:
            yield view

    async def check():
        async with TabApp().run_test(size=size) as pilot:
            view.set_model(model)
            for active in model.tabs:
                view.set_tab(active)
                await pilot.pause()
                lines = view.layout.lines
                y = next(i for i, line in enumerate(lines) if any(p.role == "tab_on" for p in line.parts))
                underline = lines[y + 1].text
                cursor = 0
                for part in lines[y].parts:
                    if part.role in ("tab_on", "tab"):
                        assert "[" not in part.text and "]" not in part.text
                        assert underline[cursor:cursor + len(part.text)] == (
                            "━" if part.role == "tab_on" else "─") * len(part.text)
                    cursor += len(part.text)
                segments = list(view.render_line(y))
                assert any(s.style.bold and "Sessions" in s.text for s in segments) == (active == TAB_SESSIONS)
                assert any(s.style.bold and "Targets" in s.text for s in segments) == (active == TAB_TARGETS)
                assert any(s.style.bold and "Alerts" in s.text for s in segments) == (active == TAB_ALERTS)
                # The underline occupies the row formerly wasted on blank padding.
                assert lines[y + 2].text.strip()
    asyncio.run(check())


@pytest.mark.parametrize('size', [(80, 24), (160, 45)])
@pytest.mark.parametrize('count', [0, 1, 5, 20])
def test_panel_split_frames_and_padding(tmp_path, size, count):
    from tsb_tui.model.fleet import GROUP_OPEN_HERE
    from tsb_tui.widgets.sidebar import Sidebar, SidebarEntry, SidebarModel

    cfg = load(config_file=tmp_path / 'absent', environ={})
    sidebar = Sidebar(cfg, leader='Ctrl+\\')
    sidebar.styles.display = 'block'
    entries = tuple(SidebarEntry(str(i), GROUP_OPEN_HERE, i + 1, '●', f'target-{i}', None,
                                 'you', False, False, None, None, None, None, f'target-{i}')
                    for i in range(count))

    class PanelApp(App):
        def compose(self):
            yield sidebar

    async def check():
        async with PanelApp().run_test(size=size) as pilot:
            sidebar.set_width(28)
            sidebar.set_window_width(size[0])
            sidebar.set_model(SidebarModel(((GROUP_OPEN_HERE, entries),), 'mine', None, 'Ctrl+\\ f'))
            await pilot.pause()
            sidebar.blur()
            await pilot.pause()
            lines = [sidebar.render_line(y).text for y in range(sidebar.size.height)]
            height = sidebar.size.height
            # The ruled session row takes the lines it paints: one at the wide budget, three at the narrow one
            # (P3 part 1), so the section's height follows its painted lines, still at least 3 and at most half.
            per_row = 1 if size[0] >= 160 else 3
            # The panel is 28 columns in this test at both sizes, so the empty copy wraps at its ` · ` (two
            # lines) and a ruled row takes three lines narrow, one wide.
            empty_lines = 2
            sessions_high = {0: 2 + empty_lines, 1: 2 + per_row, 5: 2 + 5 * per_row, 20: height // 2}[count]
            sessions_high = min(sessions_high, height // 2)
            assert 3 <= sessions_high <= height // 2
            section = height - sessions_high
            assert lines[0].startswith('┌─ ▦ Targets'), lines[0]
            assert lines[section].startswith('┌─ ▸ Sessions'), lines[section]
            for top, bottom in ((0, section - 1), (section, sidebar.size.height - 1)):
                assert lines[top].startswith('┌') and lines[top].endswith('┐')
                assert lines[bottom].startswith('└') and lines[bottom].endswith('┘')
                for line in lines[top + 1:bottom]:
                    assert line.startswith('│ ') and line.endswith(' │'), line
                    assert len(line) == 28
            assert 'Targets' not in lines[1] and 'Sessions' not in lines[section + 1]
    asyncio.run(check())


@pytest.mark.parametrize('size', [(80, 24), (160, 45)])
@pytest.mark.parametrize('monochrome', [False, True])
def test_focus_border_shape(tmp_path, monkeypatch, size, monochrome):
    from tsb_tui.widgets.sidebar import Sidebar

    if monochrome:
        monkeypatch.setenv('NO_COLOR', '1')
    cfg = load(config_file=tmp_path / 'absent', environ={})
    sidebar = Sidebar(cfg, leader='Ctrl+\\')
    sidebar.styles.display = 'block'

    class PanelApp(App):
        def compose(self):
            yield sidebar

    async def check():
        async with PanelApp().run_test(size=size) as pilot:
            sidebar.set_width(28)
            sidebar.focus()
            await pilot.pause()
            lines = [sidebar.render_line(y).text for y in range(sidebar.size.height)]
            assert any(line.startswith('┏━ ▸ Sessions') and line.endswith('┓') for line in lines)
            assert lines[0].startswith('┌─ ▦ Targets')
            sidebar.blur()
            await pilot.pause()
            assert not any(sidebar.render_line(y).text.startswith('┏') for y in range(sidebar.size.height))
    asyncio.run(check())


@pytest.mark.parametrize('size', [(80, 24), (160, 45)])
def test_layout_b_breakpoint_surfaces_and_pty_geometry(serve, wait_until, size):
    from input_server import InputRecordingApp, attached
    from stub_server import create_owner_session, resize_requests
    from tsbc import _serve

    from tsb_tui.app import TsbTuiApp
    from tsb_tui.theme import contrast_ratio, role_colours

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            sid = create_owner_session(app, _serve.TARGET, 132, 43)
            screen = await attached(pilot, app, sid, wait_until)
            screen.open_sidebar()
            await pilot.pause()
            assert screen.rail.display and screen.sidebar.display
            assert screen.rail.region.width == (4 if size[0] <= 80 else 12)
            assert screen.sidebar.region.width == (28 if size[0] == 80 else 42)
            assert screen.canvas.region.width > 0
            assert screen.canvas.region.x >= screen.sidebar.region.right
            assert (screen.backend.cols, screen.backend.rows) == (132, 43)
            roles = role_colours(app.cfg)
            assert contrast_ratio(roles['surface'], roles['bg']) >= 1.2
            assert contrast_ratio(roles['text'], roles['surface']) >= 4.5
            for width in (80, 81, 80):
                await pilot.resize_terminal(width, 24)
                await pilot.pause()
                assert screen.rail.region.width == (4 if width <= 80 else 12)
                assert ('Sessions' in screen.rail.text) == (width >= 81)
                assert screen.canvas.region.width > 0
            assert resize_requests(served) == []
    asyncio.run(check())


@pytest.mark.parametrize('size', [(80, 24), (160, 45)])
@pytest.mark.parametrize('names', ['short', 'long-target', 'long-typist', 'nobody'])
def test_frame_title_facts_and_keys_only_footer(serve, wait_until, screen_text, size, names):
    from input_server import InputRecordingApp, attached
    from stub_server import create_owner_session
    from tsbc import _serve

    from tsb_tui.app import TsbTuiApp

    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            sid = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, sid, wait_until)
            state = screen.pump.state
            target = 'core-rtr-01' if names in ('short', 'nobody') else 'core-router-with-a-long-target-name'
            state.session['target_id'] = target
            state.session['state'] = 'live'
            state.owner = 'ana'
            if names == 'long-typist':
                state.holder = {'user': 'a-very-long-typist', 'client_id': 'peer'}
            elif names == 'nobody':
                state.holder = None
            else:
                state.holder = {'user': 'ana', 'client_id': state.client_id}
            screen.open_sidebar(focus=False)
            screen.set_focus(None)
            screen.refresh_status()
            await pilot.pause()
            region = screen.query_one('#canvases').region
            title = screen_text(app).splitlines()[region.y][region.x:region.right]
            assert title.startswith('┏') and title.endswith('┓'), title
            assert '1' in title and 'live' in title
            assert 'you type' in title if names in ('short', 'long-target') else (
                'nobody types' in title if names == 'nobody' else ' types' in title)
            if size[0] == 160:
                assert target in title and 'owner ana' in title and 'recorded' in title
                if names == 'long-typist':
                    assert 'a-very-long-typist types' in title
            else:
                assert 'owner ana' not in title
                if names == 'long-target':
                    assert '…' in title and 'recorded' in title
                if names == 'long-typist':
                    assert 'recorded' not in title and '… types' in title
            footer = screen.status.text
            assert target not in footer and 'recorded' not in footer and 'you type' not in footer
            assert 'F1 Menu' in footer
    asyncio.run(check())


@pytest.mark.parametrize('size', [(80, 24), (160, 45)])
@pytest.mark.parametrize('monochrome', [False, True])
def test_session_frame_border_follows_focus(serve, wait_until, screen_text, monkeypatch, size, monochrome):
    from input_server import InputRecordingApp, attached
    from stub_server import create_owner_session
    from tsbc import _serve

    from tsb_tui.app import TsbTuiApp

    if monochrome:
        monkeypatch.setenv('NO_COLOR', '1')
    else:
        monkeypatch.delenv('NO_COLOR', raising=False)

    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            sid = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, sid, wait_until)
            screen.open_sidebar(focus=False)
            screen.set_focus(None)
            screen.refresh_status()
            await pilot.pause()
            frame = screen.query_one('#canvases').region
            side = screen.sidebar.region
            lines = screen_text(app).splitlines()

            def cell(y, x):
                return lines[y][x] if y < len(lines) and x < len(lines[y]) else ' '

            assert cell(frame.y, frame.x) == '┏' and cell(frame.bottom - 1, frame.x) == '┗'
            assert cell(side.y, side.x) == '┌', 'the unfocused side panel is light'
            screen.open_sidebar(focus=True)
            await pilot.pause()
            assert screen.sidebar.has_focus
            lines = screen_text(app).splitlines()
            assert cell(frame.y, frame.x) == '┌' and cell(frame.bottom - 1, frame.x) == '└'
            assert any(cell(y, side.x) == '┏' for y in range(side.y, side.bottom))
    asyncio.run(check())


@pytest.mark.parametrize('size', [(80, 24), (160, 45)])
def test_no_front_session_title_and_keys_only_footer(serve, wait_until, screen_text, size):
    from input_server import InputRecordingApp
    from workspace_support import fixture_login

    from tsb_tui.app import TsbTuiApp

    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_sidebar(focus=False)
            screen.set_focus(None)
            screen.refresh_status()
            await pilot.pause()
            region = screen.query_one('#canvases').region
            title = screen_text(app).splitlines()[region.y][region.x:region.right]
            assert title.startswith('┏') and 'No session open' in title, title
            assert 'recorded' not in screen.status.text and 'types' not in screen.status.text
            assert 'Ctrl+\\' in screen.status.text, 'the footer still carries its keys'
    asyncio.run(check())


@pytest.mark.parametrize('size', [(80, 24), (160, 45)])
def test_worded_alerts_row_follows_broker_health(serve, wait_until, size):
    from input_server import InputRecordingApp
    from stub_server import SlowChunkedApp, StubServer
    from workspace_support import fixture_login

    from tsb_tui.app import TsbTuiApp

    words = size[0] > 80

    async def holder():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: any(row.action == ACT_ALERTS for row in screen.rail.rows), 'the alerts row')
            assert ('Alerts' in screen.rail.text) == words

    async def not_holder():
        stub = SlowChunkedApp(alerts_status=403)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=size) as pilot:
                screen = await fixture_login(app, pilot)
                await wait_until(pilot, lambda: stub.alerts_requests >= 1 and not app.alerts.allowed, 'the 403')
                await pilot.pause(0.1)
                assert not any(row.action == ACT_ALERTS for row in screen.rail.rows)
                assert 'Alerts' not in screen.rail.text

    asyncio.run(holder())
    asyncio.run(not_holder())


def _snapshot(app, name):
    import os
    import re
    from pathlib import Path

    svg = re.sub(r'terminal-\d+', 'terminal-p2a', app.export_screenshot(title='P2a', simplify=True))
    path = Path(__file__).parent / 'snapshots' / 'redesign-p2a' / f'{name}.svg'
    if os.environ.get('UPDATE_P2A_SNAPSHOTS') == '1':
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg)
    assert path.read_text() == svg


@pytest.mark.parametrize('size', [(80, 24), (160, 45)])
@pytest.mark.parametrize('state', ['empty', 'panel-focused', 'session'])
def test_two_size_baselines(serve, wait_until, size, state):
    from input_server import InputRecordingApp, attached
    from stub_server import create_owner_session
    from tsbc import _serve
    from workspace_support import fixture_login

    from tsb_tui.app import TsbTuiApp

    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            if state == 'session':
                sid = create_owner_session(app, _serve.TARGET, 80, 24)
                screen = await attached(pilot, app, sid, wait_until)
                screen.open_sidebar(focus=False)
                screen.set_focus(None)
            else:
                screen = await fixture_login(app, pilot)
                if state == 'empty':
                    screen.close_sidebar()
                else:
                    screen.open_sidebar(focus=True)
            screen.refresh_status()
            await pilot.pause(0.3)

            def settle():
                # The session row is deterministic (rule 6): the highlight sits on the front session's row every run.
                # The paint of that mark lands a beat after set_focus; read the panel's painted lines and settle only
                # once the front session's row shows it, so the snapshot never captures an un-repainted frame. At the
                # narrow budget the mark is on the numbered name line, at the wide one on the single line.
                for y in range(screen.sidebar.size.height):
                    t = screen.sidebar.render_line(y).text
                    if '▸' in t and 'fixture-ta' in t:
                        return True
                return False
            if state == 'session':
                await wait_until(pilot, settle, 'the front session row to paint its highlight')

            def quiet():
                # The fixture's alert stream raises and clears an alert on its own clock; read at a moment with none.
                row = next(r for r in screen.rail.rows if r.action == ACT_ALERTS)
                return row.text.strip() in ('! Alerts 0', '!0')
            await wait_until(pilot, quiet, 'a moment with no alert raised')
            _snapshot(app, f'{state}-{size[0]}x{size[1]}')
    asyncio.run(check())


def _target_rows(*names):
    from tsb_tui.model.targets import make_row
    return tuple(make_row({'id': f't-{n}', 'name': n, 'type': 'ssh', 'host': f'{n}.lab'}) for n in names)


@pytest.mark.parametrize('size', [(80, 24), (160, 45)])
def test_sessions_section_holds_only_open_here_and_the_controls_are_gone(tmp_path, size):
    from tsb_tui.model.fleet import GROUP_NEEDS_YOU, GROUP_OPEN_HERE
    from tsb_tui.widgets.sidebar import Sidebar, SidebarEntry, SidebarModel

    cfg = load(config_file=tmp_path / 'absent', environ={})
    sidebar = Sidebar(cfg, leader='Ctrl+\\')
    sidebar.styles.display = 'block'

    def entry(sid, group, number):
        return SidebarEntry(sid, group, number, '●', f'host-{sid}', None, 'you', False, False, None, None, None,
                            None, f'host-{sid}')

    class PanelApp(App):
        def compose(self):
            yield sidebar

    async def check():
        async with PanelApp().run_test(size=size) as pilot:
            sidebar.set_width(28)
            sidebar.set_window_width(size[0])
            model = SidebarModel(((GROUP_NEEDS_YOU, (entry('n1', GROUP_NEEDS_YOU, None),)),
                                  (GROUP_OPEN_HERE, (entry('o1', GROUP_OPEN_HERE, 1), entry('o2', GROUP_OPEN_HERE, 2)))),
                                 'mine', 1, 'Ctrl+\\ f', targets=_target_rows('a', 'b'))
            sidebar.set_model(model)
            await pilot.pause()
            text = sidebar.text
            assert 'host-o1' in text and 'host-o2' in text
            assert 'host-n1' not in text, 'a session not open here is the full view\'s'
            for gone in ('Needs you', 'Open here', 'close', 'mine', 'New session', 'alert', 'Fleet'):
                assert gone not in text.replace('No live sessions · n new session', ''), gone
            assert [row[1] for row in sidebar.view.rows] == ['o1', 'o2']
    asyncio.run(check())


def test_the_filter_and_its_count_belong_to_the_targets_section(tmp_path):
    from tsb_tui.widgets.sidebar import Sidebar, SidebarModel

    cfg = load(config_file=tmp_path / 'absent', environ={})
    sidebar = Sidebar(cfg, leader='Ctrl+\\')
    sidebar.styles.display = 'block'

    class PanelApp(App):
        def compose(self):
            yield sidebar

    async def check():
        async with PanelApp().run_test(size=(160, 45)) as pilot:
            sidebar.set_width(28)
            sidebar.set_window_width(160)
            sidebar.set_model(SidebarModel((), 'mine', None, 'Ctrl+\\ f',
                                           targets=_target_rows('core-1', 'core-2', 'edge-1')))
            await pilot.pause()
            # rule 3 (2026-11-21): the title carries the position; P2b's selection rule puts the highlight on
            # the first row of a fresh list, so the index is 1.
            assert sidebar.render_line(0).text.startswith('┌─ ▦ Targets · 1/3')
            sidebar.focus()
            await pilot.press('slash', 'c', 'o', 'r', 'e')
            await pilot.pause()
            lines = [sidebar.render_line(y).text for y in range(sidebar.size.height)]
            # The Targets list has the keys while its filter is typed into: its frame is the heavy one.
            # rule 4 (2026-11-21): the filtered title carries the index within the matches, then `of` the total.
            assert lines[0].startswith('┏━ ▦ Targets · 1/2 of 3'), lines[0]
            assert lines[1].startswith('┃ / core▏'), lines[1]
            assert 'core-1' in lines[2] and 'core-2' in lines[3] and not any('edge-1' in l for l in lines)
    asyncio.run(check())


def test_a_filter_that_leaves_fewer_matches_is_not_left_scrolled_past_its_rows(tmp_path):
    """A filter that leaves fewer matches than the scroll top had scrolled to hides them. The
    scroll top is reset when the filter text changes, so every match is painted."""
    from tsb_tui.widgets.sidebar import Sidebar, SidebarModel

    cfg = load(config_file=tmp_path / 'absent', environ={})
    sidebar = Sidebar(cfg, leader='Ctrl+\\')
    sidebar.styles.display = 'block'
    names = [f'core-{n}' for n in range(20)] + ['zzz1', 'zzz2']

    class PanelApp(App):
        def compose(self):
            yield sidebar

    async def check():
        async with PanelApp().run_test(size=(80, 24)) as pilot:
            sidebar.set_width(28)
            sidebar.set_window_width(80)
            sidebar.set_model(SidebarModel((), 'mine', None, 'Ctrl+\\ f', targets=_target_rows(*names)))
            await pilot.pause()
            for _ in range(3):
                sidebar._scroll_section(0, 1)
            assert sidebar._target_scroll_top > 0, "the Targets section must be scrolled down first"
            sidebar.focus()
            await pilot.press('slash', 'z', 'z')
            await pilot.pause()
            body = '\n'.join(sidebar.render_line(y).text for y in range(sidebar.size.height))
            # rule 6 (2026-11-21): the filter leaves two rows; the highlight follows its entity (zzz2), the
            # scroll lands back at the head (the whole list fits), so both rows show.
            assert 'zzz1' in body and 'zzz2' in body, body
    asyncio.run(check())


def test_the_targets_title_shows_a_plus_when_more_pages_exist(tmp_path):
    """The Targets title counted the loaded rows as the total while a next page existed. When a
    next page exists the title shows the loaded count followed by `+` ("Targets · 5+"), never a false total."""
    from tsb_tui.widgets.sidebar import Sidebar, SidebarModel

    cfg = load(config_file=tmp_path / 'absent', environ={})
    sidebar = Sidebar(cfg, leader='Ctrl+\\')
    sidebar.styles.display = 'block'

    class PanelApp(App):
        def compose(self):
            yield sidebar

    async def check():
        async with PanelApp().run_test(size=(160, 45)) as pilot:
            sidebar.set_width(28)
            sidebar.set_window_width(160)
            sidebar.set_model(SidebarModel((), 'mine', None, 'Ctrl+\\ f',
                                           targets=_target_rows('t0', 't1', 't2', 't3', 't4'), target_has_next=True))
            await pilot.pause()
            title = sidebar.render_line(0).text
            # rule 3 (2026-11-21): `N+` is retired; while pages are out the count so far carries the ellipsis.
            assert title.startswith('┌─ ▦ Targets · 1/5…'), title
    asyncio.run(check())


def test_painting_the_target_rows_filters_once_not_once_per_row(tmp_path):
    """Every Targets `render_line` recomputed the filter over all targets. The filtered target
    lines are computed once per change (cached, invalidated when targets, filter or width change), as the panel did
    before P2a: painting N rows calls the filter once, not N times."""
    from tsb_tui.widgets.sidebar import Sidebar, SidebarModel

    cfg = load(config_file=tmp_path / 'absent', environ={})
    sidebar = Sidebar(cfg, leader='Ctrl+\\')
    sidebar.styles.display = 'block'
    names = tuple(f't{n}' for n in range(30))
    calls: list[int] = []

    def counting() -> tuple:
        calls.append(1)
        return _target_rows(*names)

    sidebar._filter_targets = counting

    class PanelApp(App):
        def compose(self):
            yield sidebar

    async def check():
        async with PanelApp().run_test(size=(80, 24)) as pilot:
            sidebar.set_width(28)
            sidebar.set_window_width(80)
            calls.clear()
            sidebar.set_model(SidebarModel((), 'mine', None, 'Ctrl+\\ f', targets=_target_rows(*names)))
            await pilot.pause()
            rows = sidebar.size.height
            target_rows = sum(1 for y in range(rows) if y < sidebar.targets_height)
            assert target_rows > 3, "the test needs several target rows to paint"
            for y in range(rows):
                sidebar.render_line(y)
            assert len(calls) == 1, f"the filter ran {len(calls)} times for {target_rows} target rows"
    asyncio.run(check())


@pytest.mark.parametrize('size', [(80, 24), (160, 45)])
def test_the_frame_title_opens_whos_here_and_carries_the_accent(serve, wait_until, size):
    from input_server import InputRecordingApp, attached
    from stub_server import create_owner_session
    from tsbc import _serve

    from tsb_tui.app import TsbTuiApp
    from tsb_tui.screens.whos_here import WhosHereScreen

    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            sid = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, sid, wait_until)
            screen.refresh_status()
            await pilot.pause()
            frame = screen.query_one('#canvases')
            import re

            from textual.color import Color
            title = str(frame.border_title)  # Textual hands a styled title back as markup
            red, green, blue = Color.parse(app.cfg['theme.accent']).rgb
            styled = re.search(rf'on rgb\({red},{green},{blue}\) bold\](.*?)\[/', title)
            assert styled and styled.group(1) == 'you type', title
            await pilot.click('#canvases', offset=(6, 0))
            await wait_until(pilot, lambda: isinstance(app.screen, WhosHereScreen), 'a click on the title to open Who\'s here')
    asyncio.run(check())


@pytest.mark.parametrize('size', [(80, 24), (160, 45)])
def test_only_a_click_on_the_frame_border_opens_whos_here(serve, wait_until, size):
    """`SessionFrame.on_click` opened Who's here on any click at row 0, and a click bubbles up
    from the canvas with child-relative coordinates, so a click on the session grid's top row opened it. Only a click
    on the frame's own top border opens Who's here."""
    from input_server import InputRecordingApp, attached
    from stub_server import create_owner_session
    from tsbc import _serve

    from tsb_tui.app import TsbTuiApp
    from tsb_tui.screens.whos_here import WhosHereScreen

    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            sid = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, sid, wait_until)
            await pilot.click(screen.canvas, offset=(6, 0))
            await pilot.pause()
            assert app.screen is screen, 'a click on the canvas top row opened Who\'s here'
            await pilot.click('#canvases', offset=(6, 0))
            await wait_until(pilot, lambda: isinstance(app.screen, WhosHereScreen),
                             'a click on the border to open Who\'s here')
    asyncio.run(check())


@pytest.mark.parametrize('size', [(80, 24), (160, 45)])
@pytest.mark.parametrize('monochrome', [False, True])
def test_panel_section_titles_carry_their_marks(tmp_path, monkeypatch, size, monochrome):
    from tsb_tui.widgets.sidebar import Sidebar, SidebarModel

    if monochrome:
        monkeypatch.setenv('NO_COLOR', '1')
    else:
        monkeypatch.delenv('NO_COLOR', raising=False)
    cfg = load(config_file=tmp_path / 'absent', environ={})
    sidebar = Sidebar(cfg, leader='Ctrl+\\')
    sidebar.styles.display = 'block'

    class PanelApp(App):
        def compose(self):
            yield sidebar

    async def check():
        async with PanelApp().run_test(size=size) as pilot:
            sidebar.set_width(28)
            sidebar.set_window_width(size[0])
            sidebar.set_model(SidebarModel((), 'mine', None, 'Ctrl+\\ f', targets=_target_rows('a', 'b')))
            sidebar.blur()
            await pilot.pause()
            lines = [sidebar.render_line(y).text for y in range(sidebar.size.height)]
            # rule 3 (2026-11-21): the title carries the position; P2b's selection rule puts the highlight on
            # the first row of a fresh list, so the index is 1.
            assert lines[0].startswith('┌─ ▦ Targets · 1/2 ─'), lines[0]
            # rule 3 (2026-11-21): the Sessions title carries the position; no rows means `–/0`.
            assert any(line.startswith('┌─ ▸ Sessions · –/0 ─') for line in lines), lines
    asyncio.run(check())


def test_a_word_that_does_not_fit_beside_its_mark_is_cut_with_an_ellipsis_never_the_mark():
    from tsb_tui.widgets.rail import _worded

    assert _worded('▦', 'Targets', '', 11) == '▦ Targets'
    assert _worded('▦', 'Targets', '', 6) == '▦ Tar…'
    assert _worded('!', 'Alerts', '12', 8) == '! Al… 12'


def test_the_panels_targets_come_back_after_the_full_view_was_open(serve, wait_until):
    from input_server import InputRecordingApp
    from workspace_support import fixture_login

    from tsb_tui.app import TsbTuiApp

    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.sidebar.model.targets) > 1, 'the targets')
            screen.open_fleet_view()
            await pilot.pause(0.3)
            screen.close_fleet_view()
            screen.open_sidebar()
            await wait_until(pilot, lambda: screen.targets.fetched and len(screen.sidebar.model.targets) > 1,
                             'the panel to read the targets again after the view emptied them')
    asyncio.run(check())
