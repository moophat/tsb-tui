"""PR #114's fourth review round: a refresh that can replace a newer list, a failed refresh with rows on show,
a click that marks the row clicked, a relayout that keeps the mark on the appearance selected, and a removed
table row that must select the next table row, never a Needs-you row.

Every case drives the app through Pilot against the contract's fixture server (a test double, never a broker),
and asserts the painted mark, the selection and the request counts in the screen, not an implementation.

AI-Model: strata/strata
"""
from __future__ import annotations

import asyncio

from input_server import InputRecordingApp
from test_fleet_view import _model, _row
from test_targets_tab import _open_targets, _ScriptedTargets
from tsb_contract_client import ApiError
from workspace_support import fixture_login, named_sessions

from tsb_tui.app import TsbTuiApp
from tsb_tui.model.targets import Query, TargetsModel
from tsb_tui.screens.fleet_view import GROUP_TABLE, TAB_SESSIONS


def _bulk(served, count, prefix="bulk"):
    seen = "2026-10-17T08:30:00Z"
    for i in range(count):
        tid = f"{prefix}-{i:04d}"
        served.targets[tid] = {"id": tid, "type": "ssh", "name": tid, "group": "lab", "tags": [],
                               "discovered_by": "manual", "last_seen": seen,
                               "host": "192.0.2.50", "port": 22}


def _add_target(served, tid):
    served.targets[tid] = {"id": tid, "type": "ssh", "name": tid, "group": "lab", "tags": [],
                           "discovered_by": "manual", "last_seen": "2026-10-17T08:30:00Z",
                           "host": "192.0.2.9", "port": 22}


def _painted(screen) -> list[str]:
    return [screen.sidebar.render_line(y).text for y in range(screen.sidebar.size.height)]


def _target_lines(screen) -> list[str]:
    return [line[2:-2] for line in _painted(screen)[:screen.sidebar.targets_height]]


def _target_y(sidebar, target_id: str, last: bool = False) -> int:
    """The sidebar line that paints `target_id`; the last occurrence when `last`, the first otherwise."""
    found = None
    for y in range(1, sidebar.targets_height - 1):
        line = sidebar._target_line_at(y)
        if line is not None and line.session_id == target_id:
            found = y
            if not last:
                return y
    if found is None:
        raise AssertionError(f"no painted row for {target_id}")
    return found


def _record(tid: str) -> dict:
    return {"id": tid, "type": "ssh", "name": tid, "group": "lab", "tags": [],
            "discovered_by": "manual", "last_seen": "2026-10-17T08:30:00Z", "host": "192.0.2.50", "port": 22}


def test_a_tick_during_a_foreground_first_page_sends_no_request(serve, wait_until):
    """Rule 1 (2026-11-23): a foreground first page is in flight from its send to its reply, and a tick starts
    nothing while it is: no overlapping passes."""

    async def check():
        served, url = serve()
        _bulk(served, 600)
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            screen, _view = await _open_targets(app, pilot, wait_until, rows=500, timeout_s=40)
            screen._stop_targets_timer()
            scripted = _ScriptedTargets(app)
            _reached, release = scripted.hold(0)  # the refresh's first page is held in flight
            screen._fetch_targets()
            await pilot.pause(0.05)
            before = len(scripted.calls)
            screen._fetch_targets(background=True)  # a tick while the foreground first page is in flight
            await pilot.pause(0.05)
            assert len(scripted.calls) == before, "a tick started a pass during a foreground first page"
            release.set()
            await pilot.pause(0.1)

    asyncio.run(check())


def test_a_foreground_reply_after_a_staging_pass_published_applies_nothing():
    """Rule 1(b) (2026-11-23): publishing a staged pass moves the published generation past every request already
    sent, so a foreground reply sent before the pass applies nothing and chains nothing. The invariant lives in
    `TargetsModel.publish`, so the case is built on the model: a first page applied, a foreground first page sent at
    the newest generation, a staged pass published, then that older foreground reply arrives."""

    published = TargetsModel()
    # A first page lands, then a foreground first-page request is sent at the newest generation (in flight).
    published.apply(published.begin(Query()), Query(), {"targets": [_record("a")], "next_cursor": "c1"})
    fg_gen = published.begin()  # the foreground reply's generation, sent before the staging pass
    staged = TargetsModel()
    staged.apply(staged.begin(Query()), Query(), {"targets": [_record("b")], "next_cursor": None})
    published.publish(staged)  # the staged pass takes the list in one step
    assert [row.id for row in published.rows] == ["b"]
    # The older foreground reply arrives: it must apply nothing to the freshly published rows.
    assert not published.apply(fg_gen, Query(), {"targets": [_record("a")], "next_cursor": None}), \
        "an old foreground reply replaced the published rows"
    assert [row.id for row in published.rows] == ["b"], "an old foreground reply replaced the published rows"


def test_a_failed_refresh_with_rows_shows_the_retry_line_and_r_clears_it(serve, wait_until):
    """Rule 2 (2026-11-23): a background 503 keeps the loaded rows and shows `Could not load targets · r retry`
    as the section's last line, in the sidebar and in the full view; `r` reads, and on success the line goes."""

    async def check():
        served, url = serve()
        _bulk(served, 20)
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen, view = await _open_targets(app, pilot, wait_until, rows=28, timeout_s=30)
            screen._stop_targets_timer()
            scripted = _ScriptedTargets(app)
            scripted.fail[0] = ApiError(503, "inventory_unavailable", "the inventory is not answering")
            screen._fetch_targets(background=True)
            await wait_until(pilot, lambda: screen.targets.unavailable and screen.targets.rows, "the 503 with rows")
            assert any("Could not load targets · r retry" in ln for ln in view.text.splitlines()), view.text
            scripted.fail.clear()
            screen._fleet_view_command("retry", "targets")
            await wait_until(pilot, lambda: not screen.targets.unavailable, "the read after `r`", 30)
            await pilot.pause()
            assert not any("Could not load targets · r retry" in ln for ln in view.text.splitlines()), view.text

    asyncio.run(check())


def test_a_sidebar_with_rows_shows_the_unavailable_line_last(serve, wait_until):
    """Rule 2 (2026-11-23) in the sidebar: a background 503 keeps the loaded rows (they stay useful), and the
    section's last painted line reads `Could not load targets · r retry`. The copy is a pair at the end of
    `_build_pairs`, counted in `_target_painted_end()`, so scrolling to the end shows it as the section's last
    line while the targets are unavailable with rows on show."""

    async def check():
        served, url = serve()
        _bulk(served, 4)
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.rows, "the targets")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            sidebar = screen.sidebar
            screen._stop_targets_timer()
            scripted = _ScriptedTargets(app)
            scripted.fail[0] = ApiError(503, "inventory_unavailable", "the inventory is not answering")
            screen._fetch_targets(background=True)
            await wait_until(pilot, lambda: screen.targets.unavailable and screen.targets.rows, "the 503 with rows")
            sidebar._target_scroll_top = sidebar._target_scroll_limit()  # scroll to the end
            await pilot.pause()
            content = [sidebar.render_line(y).text[2:-2] for y in range(1, sidebar.targets_height - 1)]
            content = [ln for ln in content if ln.strip()]
            assert any("bulk-0000" in ln for ln in content), content  # the loaded rows stay
            assert "Could not load targets · r retry" in content[-1], content

    asyncio.run(check())


def test_a_click_marks_the_row_clicked(serve, wait_until):
    """Rule 3 (2026-11-23): with Recent populated, a click on a main-list target that is also in Recent, on one
    that is not, and on the same with a filter line on show, marks the row clicked."""

    async def check():
        served, url = serve(InputRecordingApp())
        _add_target(served, "rec-a")
        _add_target(served, "rec-b")
        named_sessions(url, served, "rec-a")
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.fleet.records, "the session list")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            sidebar = screen.sidebar

            def y_of(target_id, last=False):
                found = None
                for y in range(1, sidebar.targets_height - 1):
                    line = sidebar._target_line_at(y)
                    if line is not None and line.session_id == target_id:
                        found = y
                        if not last:
                            return y
                if found is None:
                    raise AssertionError(f"no painted row for {target_id}")
                return found

            rows = [row.id for row in sidebar.painted_targets()]
            # (a) a main-list target that is also in Recent: click its main-list line, not the Recent one.
            main_a = len(rows) - 1 - rows[::-1].index("rec-a")
            await pilot.click(sidebar, offset=(3, y_of("rec-a", last=True)))
            await pilot.pause()
            assert sidebar.selected_target == "rec-a" and sidebar._target_pos == main_a, \
                (sidebar.target_selected, sidebar._target_pos, main_a)
            marks = [ln for ln in _painted(screen) if "▸" in ln and "rec-a" in ln]
            assert len(marks) == 1, marks
            # (b) a main-list target that is not in Recent.
            b = rows.index("rec-b")
            await pilot.click(sidebar, offset=(3, y_of("rec-b")))
            await pilot.pause()
            assert sidebar.selected_target == "rec-b" and sidebar._target_pos == b, (sidebar.target_selected, b)
            # (c) the same with a filter line on show.
            sidebar.filter_text = "rec-a"
            sidebar._relayout()
            await pilot.pause()
            rows2 = [row.id for row in sidebar.painted_targets()]
            pos = rows2.index("rec-a")
            await pilot.click(sidebar, offset=(3, y_of("rec-a")))
            await pilot.pause()
            assert sidebar.selected_target == "rec-a" and sidebar._target_pos == pos, \
                (sidebar.target_selected, sidebar._target_pos, pos)

    asyncio.run(check())


def test_a_relayout_keeps_the_mark_on_the_appearance_selected(serve, wait_until):
    """Rule 4 (2026-11-23): the mark follows the row the selection was made on. A target shown in both Recent and
    the main list, selected on its main-list row, keeps the mark on that row through a relayout that keeps it shown;
    the mark never jumps to the Recent appearance."""

    async def check():
        served, url = serve(InputRecordingApp())
        _add_target(served, "rec-a")
        _bulk(served, 20)
        named_sessions(url, served, "rec-a")
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.fleet.records, "the session list")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            sidebar = screen.sidebar
            recent = [row.id for row in sidebar._recent_targets()]
            main = [row.id for row in sidebar.visible_targets()]
            assert "rec-a" in recent and "rec-a" in main, (recent, main)
            # Click the main-list appearance (the last occurrence): the row the operator selects.
            await pilot.click(sidebar, offset=(3, _target_y(sidebar, "rec-a", last=True)))
            await pilot.pause()
            main_pos = len(recent) + main.index("rec-a")
            assert sidebar.selected_target == "rec-a" and sidebar._target_pos == main_pos, \
                (sidebar.selected_target, sidebar._target_pos, main_pos)
            # A relayout that keeps rec-a shown in both sections: the mark stays on the main-list row.
            sidebar._relayout()
            await pilot.pause()
            recent2 = [row.id for row in sidebar._recent_targets()]
            main2 = [row.id for row in sidebar.visible_targets()]
            assert sidebar._target_pos == len(recent2) + main2.index("rec-a"), \
                "the mark moved to the Recent appearance"
            marks = [ln for ln in _painted(screen) if "▸" in ln and "rec-a" in ln]
            assert len(marks) == 1, marks

    asyncio.run(check())


def test_a_removed_table_row_selects_the_next_table_row(serve, wait_until):
    """Rule 5 (2026-11-23): with a Needs-you row on show, removing the selected table row selects the next table
    row, never the Needs-you row; removing the last table row selects the new last table row."""

    async def check():
        _served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            screen = await fixture_login(app, pilot)
            view = screen.fleet_view
            view.tab = TAB_SESSIONS
            needs = [_row("n1", note="needs you", types="you type")]
            rows = [_row("s1"), _row("s2"), _row("s3")]
            view.set_model(_model(rows, needs=needs))
            # Select the middle table row s2 and record the layout as it stood (Needs-you row first).
            view.selected = (GROUP_TABLE, "s2")
            view._pos = 1
            view._listed_rows = [("needs_you", "n1"), (GROUP_TABLE, "s1"), (GROUP_TABLE, "s2"),
                                 (GROUP_TABLE, "s3")]
            # Remove the selected table row s2: the next table row (s3) is selected, never Needs-you.
            view.set_model(_model([_row("s1"), _row("s3")], needs=needs))
            assert view.selected == (GROUP_TABLE, "s3"), (view.selected, "next table row after s2")
            # Remove the last table row s3: the new last table row (s2) is selected.
            view.selected = (GROUP_TABLE, "s3")
            view._pos = 2
            view._listed_rows = [("needs_you", "n1"), (GROUP_TABLE, "s1"), (GROUP_TABLE, "s2"),
                                 (GROUP_TABLE, "s3")]
            view.set_model(_model([_row("s1"), _row("s2")], needs=needs))
            assert view.selected == (GROUP_TABLE, "s2"), (view.selected, "new last table row")

    asyncio.run(check())
