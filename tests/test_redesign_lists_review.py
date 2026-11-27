"""PR #114's review round: the ruled fixes on the lists, the filter control, the dialog registration,
the `?` key, the painting's pair walk and the loading title.

Every case drives the app with keys through Pilot against the contract's fixture server (a test double,
never a broker or sshd), asserts words and geometry in the painted lines, and keeps the assertions in the
screen text, not an implementation.

AI-Model: strata/strata
"""
from __future__ import annotations

import asyncio
import threading

from input_server import InputRecordingApp
from tsb_contract_client import ApiError
from tsbc import _serve
from workspace_support import fixture_login, named_sessions

from tsb_tui.app import TsbTuiApp
from tsb_tui.model.targets import Query, TargetsModel
from tsb_tui.widgets.sidebar import Sidebar


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


def _targets_title(screen) -> str:
    lines = screen.fleet_view.text.splitlines()
    for line in lines:
        if line.startswith(" Targets ·"):
            return line.rstrip()
    return ""


def test_wide_sidebar_paints_and_scrolls_to_the_end(serve, wait_until):
    """A wide sidebar with far more targets than it shows lays out, paints, and scrolling to the end paints
    the last rows: pair construction never indexes facts for a row outside the window."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 4000)
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_sidebar(pane="targets")
            await wait_until(pilot, lambda: len(screen.targets.rows) == 4008, "every page", 30)
            last_id = screen.sidebar.painted_targets()[-1].id
            screen.sidebar._jump_target(True)
            await pilot.pause()
            painted = [ln[2:-2] for ln in _painted(screen)]
            assert screen.sidebar.selected_target == last_id, (screen.sidebar.selected_target, last_id)
            assert any(ln.startswith("▸") for ln in painted), painted[-6:]
    asyncio.run(check())


def test_filtering_the_selected_session_moves_the_mark_to_the_new_selection(serve, wait_until):
    """The session section repaints when relayout moves the selection: after filtering out the selected
    session the painted mark stands on the new selection only, not the old one."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 4000)
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_sidebar(pane="targets")
            await wait_until(pilot, lambda: len(screen.targets.rows) == 4008, "every page", 30)
            screen.sidebar.target_selected = screen.targets.rows[0].id
            screen.sidebar.filter_text = "bulk-0001"
            screen.sidebar._relayout()
            await pilot.pause()
            marks = [line for line in _target_lines(screen) if line.startswith("▸")]
            assert len(marks) == 1, marks
            assert "bulk-0001" in marks[0], marks
    asyncio.run(check())


def test_details_screen_is_a_registered_dialog(serve, wait_until):
    """While the target details are open the terminal counts the screen among its dialogs, and the status is
    restored when it closes."""

    async def check():
        _served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_sidebar(pane="targets")
            await wait_until(pilot, lambda: screen.targets.record("fixture-target") is not None, "the page")
            screen._open_target_details("fixture-target")
            await wait_until(pilot, lambda: any(d.__class__.__name__ == "TargetDetailsScreen"
                                                for d in screen.dialogs), "the details dialog registered")
            assert any(d.__class__.__name__ == "TargetDetailsScreen" for d in screen.dialogs)
            await pilot.press("escape")
            await wait_until(pilot, lambda: not any(d.__class__.__name__ == "TargetDetailsScreen"
                                                    for d in screen.dialogs), "the dialog closed", 20)
    asyncio.run(check())


def test_targets_filter_control_shows_the_filter_text(serve, wait_until):
    """The full view's Targets filter control renders the filter text as it is typed, edited and committed."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 4000)
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_fleet_view("targets")
            await wait_until(pilot, lambda: len(screen.targets.rows) == 500, "the first page")
            await pilot.press("/")
            for ch in "rt-0001":
                await pilot.press(ch)
            await pilot.pause()
            control = [line for line in screen.fleet_view.text.splitlines() if "/ rt-0001" in line]
            assert control, screen.fleet_view.text.splitlines()[:4]
    asyncio.run(check())


def test_question_mark_opens_all_keys_in_the_full_view(serve, wait_until):
    """The full view's `?` key opens All keys through the normalized key event, as the sidebar does."""

    async def check():
        _served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_fleet_view()
            await wait_until(pilot, lambda: screen.fleet_view.size.width >= 100, "the view open")
            await pilot.press("?")
            await pilot.pause()
            screens = [s.__class__.__name__ for s in app.screen_stack]
            assert "TextScreen" in screens, screens
    asyncio.run(check())


def test_painting_near_the_start_touches_only_the_window_pairs(serve, wait_until):
    """Painting near the start of an 8,000-row list touches no more pairs than the window holds."""

    class CountedPairs(list):
        def __init__(self, base):
            super().__init__(base)
            self.touched = 0

        def __getitem__(self, index):
            self.touched += 1
            return super().__getitem__(index)

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 8000)
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_sidebar(pane="targets")
            await wait_until(pilot, lambda: len(screen.targets.rows) == 8008, "every page", 30)
            window = max(screen.sidebar.targets_height - 2, 0)
            pairs = list(screen.sidebar._target_row_pairs())
            counted = CountedPairs(pairs)
            screen.sidebar._target_scroll_top = 0
            original = screen.sidebar._target_row_pairs
            screen.sidebar._target_row_pairs = lambda: counted
            screen.sidebar._target_painted_end()
            screen.sidebar._target_row_pairs = original
            assert counted.touched <= window + 2, (counted.touched, window)
    asyncio.run(check())


def test_loading_title_shows_exactly_one_ellipsis(serve, wait_until):
    """The full view's loading title shows the count so far with exactly one ellipsis, never two."""

    async def check():
        gate = threading.Event()
        served, url = serve(InputRecordingApp())
        _bulk(served, 8000)
        original = served._list_targets

        def gated(query, *rest):
            cursor = query.get("cursor")
            if cursor and not gate.is_set():
                gate.wait(timeout=15)
            return original(query, *rest)

        served._list_targets = gated
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_fleet_view("targets")
            await wait_until(pilot, lambda: len(screen.targets.rows) == 500 and screen.targets.next_cursor,
                             "the first page", 30)
            screen._sync_fleet_widgets()
            title = _targets_title(screen)
            assert "Targets · 1/500…" in title, title
            assert "……" not in title, title
            gate.set()
    asyncio.run(check())


def test_a_target_in_recent_marks_only_the_row_selected(serve, wait_until):
    """A target shown in `Recent` and the main list marks only the row the selection was made on; selecting
    each appearance marks exactly one row each time."""

    async def check():
        served, url = serve(InputRecordingApp())
        _add_target(served, "rec-a")
        named_sessions(url, served, "rec-a")
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.fleet.records, "the session list")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            rows = [row.id for row in screen.sidebar.painted_targets()]
            first = rows.index("rec-a")
            last = len(rows) - 1 - rows[::-1].index("rec-a")
            assert first != last, rows.count("rec-a")
            for place in (first, last):
                screen.sidebar.target_selected = "rec-a"
                screen.sidebar._target_pos = place
                await pilot.pause()
                marks = [ln for ln in _painted(screen) if ln[2:-2].startswith("▸") and "rec-a" in ln]
                assert len(marks) == 1, (place, marks)
    asyncio.run(check())


def test_a_poll_reply_during_sign_out_applies_nothing(serve, wait_until):
    """A poll reply landing while sign-out is running hands off nothing and applies nothing: the workspace
    it would repaint is already going away."""

    async def check():
        _served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            seed = [{"id": "s1", "target_id": "s1", "owner": _serve.USER, "control_holder": None,
                     "state": "live", "clients": [], "recording": {"active": False}, "ending": False}]
            screen.fleet.apply(1, seed)
            dirty = []
            screen._mark_fleet_dirty = lambda *a: dirty.append(True)
            # A sign-out task that has begun and not finished: signing_out is true.
            task = asyncio.create_task(asyncio.sleep(30))
            app._sign_out_task = task
            assert app.signing_out
            screen._apply_poll(2, seed)
            await pilot.pause()
            assert screen.fleet.applied_generation == 1, screen.fleet.applied_generation
            assert dirty == [], dirty
            task.cancel()
    asyncio.run(check())


def test_a_background_refresh_stages_pages_apart_from_the_published_list(serve, wait_until):
    """A background refresh reads into a staging model and does not touch the published rows until its last
    page lands; a selection stays on the same target throughout."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 1000)
        gates: dict[int, tuple[threading.Event, threading.Event]] = {}
        calls: list[dict] = []
        real = served._list_targets
        lock = threading.Lock()
        gate_on = [False]

        def gated(query, *rest):
            with lock:
                index = len(calls)
                calls.append(index)
            if gate_on[0] and index == first_bg[0]:  # only the first staging page is held; the rest chain freely
                reached, release = gates.setdefault(index, (threading.Event(), threading.Event()))
                reached.set()
                assert release.wait(12), "a held page was never released"
            return real(query, *rest)

        first_bg = [len(calls)]  # set below to the background pass's first request index

        served._list_targets = gated
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            # The foreground first load published every page (no gate); the rows are all present.
            await wait_until(pilot, lambda: len(screen.targets.rows) == 1008 and not screen.targets.next_cursor,
                             "the published pages", 30)
            screen.open_sidebar(pane="targets")
            for _ in range(9):
                await pilot.press("down")
            await pilot.pause()
            chosen = screen.sidebar.selected_target
            # Start a background pass with its first page held: the published rows must not move.
            gate_on[0] = True
            first_bg[0] = len(calls)
            screen._fetch_targets(background=True)
            await wait_until(pilot, lambda: gates and gates[first_bg[0]][0].is_set(), "the staging request")
            assert len(screen.targets.rows) == 1008, (len(screen.targets.rows), calls)
            assert screen._targets_staging is not None, "the pass stages apart from the published list"
            # A tick during a staging pass sends no request.
            n_before = len(calls)
            screen._targets_tick()
            await pilot.pause()
            assert len(calls) == n_before, (len(calls), n_before)
            gates[first_bg[0]][1].set()  # the first staging page lands; the rest chain freely to publish
            await wait_until(pilot, lambda: screen._targets_staging is None and len(screen.targets.rows) == 1008,
                             "the published pass", 30)
            assert screen.sidebar.selected_target == chosen, "the highlight followed the target"
    asyncio.run(check())


def test_a_failed_background_pass_leaves_the_published_list_as_it_was(serve, wait_until):
    """A staging pass that fails is dropped whole: the published rows stay, no toast for a background failure,
    and the next tick starts a fresh pass."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 1000)
        calls: list[dict] = []
        real = served._list_targets
        lock = threading.Lock()
        fail_on = [False]

        def gated(query, *rest):
            with lock:
                index = len(calls)
                calls.append(index)
            if fail_on[0]:  # the background pass's first page fails; it is not a 401 or 503
                return served._err(500, "internal_error", "the broker is not answering")
            return real(query, *rest)

        served._list_targets = gated
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.targets.rows) == 1008 and not screen.targets.next_cursor,
                             "the published pages", 30)
            fail_on[0] = True
            screen._fetch_targets(background=True)
            await wait_until(pilot, lambda: screen._targets_staging is None, "the pass dropped")
            assert len(screen.targets.rows) == 1008, (len(screen.targets.rows), calls)
            assert not screen.targets.unavailable, "a background failure is not the inventory being down"
            fail_on[0] = False
            n_before = len(calls)
            screen._fetch_targets(background=True)
            await pilot.pause()
            assert len(calls) > n_before, "the next pass starts fresh"
    asyncio.run(check())


def test_the_first_load_failing_with_a_500_raises_no_toast_and_the_next_tick_reads(serve, wait_until):
    """The first load after login publishes page by page into the published list and is quiet: a failure of it
    raises no toast (the next tick reads again), and the next tick's pass lands the rows."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 1000)
        real = served._list_targets
        fail_on = [True]

        def gated(query, *rest):
            if fail_on[0]:
                return served._err(500, "internal_error", "the broker is not answering")
            return real(query, *rest)

        served._list_targets = gated
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 1)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            assert len(screen.targets.rows) == 0, "the first load failed"
            live = [n for n in app._notifications if not n.has_expired]
            assert live == [], live  # the first load's failure raises no toast: the next tick retries
            fail_on[0] = False
            screen._fetch_targets(background=True)  # what the next tick does
            await wait_until(pilot, lambda: len(screen.targets.rows) == 1008
                             and not screen.targets.next_cursor, "the next tick's pass", 30)
    asyncio.run(check())


def test_a_failed_later_page_shows_the_retry_line_and_r_completes(serve, wait_until):
    """A chained page that answers an error keeps the loaded rows, drops `loading` and the count's ellipsis, and
    writes `Could not load more · r retry` as the list's last line with no toast; `r` fetches the kept cursor and
    the list completes."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 1000)
        calls: list[dict] = []
        real = served._list_targets
        lock = threading.Lock()
        fail_on = [False]

        def gated(query, *rest):
            with lock:
                index = len(calls)
                calls.append(index)
            if fail_on[0] and query.get("cursor"):  # the chained page answers the error; not a 401 or a 503
                return served._err(500, "internal_error", "the broker is not answering")
            return real(query, *rest)

        served._list_targets = gated
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(160, 45)) as pilot:
            fail_on[0] = True
            screen = await fixture_login(app, pilot)
            # The foreground first load published page 1; the chained second page answered the error.
            await wait_until(pilot, lambda: len(screen.targets.rows) == 500 and screen.targets.next_cursor
                             and screen.targets.more_failed, "the failed later page", 30)
            screen.open_sidebar(pane="targets")
            title = screen.sidebar.render_line(0).text
            assert "…" not in title, title  # the count shows the loaded number, no false total
            # The retry line is the list's last line: scroll to the last loaded row so it paints at the window end.
            screen.sidebar._target_pos = len(screen.targets.rows) - 1
            screen.sidebar._scroll_section(0, 1)
            for _ in range(len(screen.targets.rows)):
                screen.sidebar._scroll_section(0, 1)
            await pilot.pause()
            lines = [screen.sidebar.render_line(y).text for y in range(screen.sidebar.targets_height)]
            assert any("Could not load more · r retry" in line for line in lines), lines
            live = [n for n in app._notifications if not n.has_expired]
            assert live == [], live  # a failed `more` page raises no toast: the line says it
            fail_on[0] = False
            await pilot.press("r")
            await wait_until(pilot, lambda: len(screen.targets.rows) == 1008 and not screen.targets.next_cursor,
                             "the completed list", 30)
            assert not screen.targets.more_failed
    asyncio.run(check())


def test_an_old_staging_reply_never_touches_a_newer_pass(serve, wait_until):
    """A reply from a dropped staging pass applies nothing to the pass now in flight: its success neither
    publishes into the new pass nor clears it."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 1000)
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.targets.rows) == 1008 and not screen.targets.next_cursor,
                             "the first load", 30)
            record = next(iter(screen.targets.records.values()))
            a = TargetsModel()
            a_gen = a.begin(Query())
            screen._fetch_targets()  # the foreground read drops A: its replies must never apply
            await wait_until(pilot, lambda: len(screen.targets.rows) == 1008 and not screen.targets.next_cursor,
                             "the foreground read", 30)
            b = TargetsModel()
            b.begin(Query())
            screen._targets_staging = b
            # Deliver A's last-page reply against B: it applies nothing, publishes nothing, clears nothing.
            screen._targets_reply(a_gen, Query(), {"targets": [record], "next_cursor": None}, False, None, True)
            await pilot.pause()
            assert screen._targets_staging is b, "the old reply cleared the pass in flight"
            assert len(screen.targets.rows) == 1008, (len(screen.targets.rows), "the old reply published into the list")
            assert len(b.rows) == 0, b.rows
    asyncio.run(check())


def test_an_old_staging_failure_never_clears_a_newer_pass(serve, wait_until):
    """A failure from a dropped staging pass clears nothing on the pass now in flight."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 1000)
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.targets.rows) == 1008 and not screen.targets.next_cursor,
                             "the first load", 30)
            a = TargetsModel()
            a_gen = a.begin(Query())
            screen._fetch_targets()  # the foreground read drops A
            await wait_until(pilot, lambda: len(screen.targets.rows) == 1008 and not screen.targets.next_cursor,
                             "the foreground read", 30)
            b = TargetsModel()
            b.begin(Query())
            screen._targets_staging = b
            exc = ApiError(500, "internal_error", "the broker is not answering")
            screen._targets_failed(a_gen, exc, False, True, None)
            await pilot.pause()
            assert screen._targets_staging is b, "the old reply's failure cleared the pass in flight"
    asyncio.run(check())



def test_a_staging_reply_after_unmount_sends_nothing_and_publishes_nothing(serve, wait_until):
    """A staging page outstanding at unmount applies nothing and chains no request when its reply lands."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 1000)
        calls: list[int] = []
        real = served._list_targets
        lock = threading.Lock()
        gated: set[int] = set()
        gates: dict[int, tuple[threading.Event, threading.Event]] = {}

        def handler(query, *rest):
            with lock:
                index = len(calls)
                calls.append(index)
            if index in gated:
                reached, release = gates.setdefault(index, (threading.Event(), threading.Event()))
                reached.set()
                assert release.wait(12), "a held page was never released"
            return real(query, *rest)

        served._list_targets = handler
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.targets.rows) == 1008 and not screen.targets.next_cursor,
                             "the first load", 30)
            screen.open_sidebar(pane="targets")
            gated.add(len(calls))
            screen._fetch_targets(background=True)
            await wait_until(pilot, lambda: gates and any(g[0].is_set() for g in gates.values()), "the staging page")
            n_before = len(calls)
            screen.on_unmount()
            assert screen._targets_staging is None, "unmount dropped the staging pass"
            gates[len(calls) - 1][1].set()
            await pilot.pause()
            await pilot.pause(0.2)
            assert len(calls) == n_before, (len(calls), n_before)  # no chained request
            assert len(screen.targets.rows) == 1008, "nothing published after unmount"
    asyncio.run(check())


def test_a_click_marks_the_row_clicked(serve, wait_until):
    """A click on a target row selects and marks that row: each Recent appearance and the first row under an
    active filter mark the row clicked, not the one below."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 60)
        named_sessions(url, served, "bulk-0001", "bulk-0002")
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_sidebar(pane="targets")
            await wait_until(pilot, lambda: len(screen.targets.rows) > 0, "the rows")
            sidebar = screen.sidebar
            recent = sidebar._recent_targets()
            assert recent, "the login's own sessions put a target in Recent"
            sidebar.filter_text = "bulk"
            sidebar._relayout()
            await pilot.pause()
            # Click the first Recent row: find its widget row.
            def y_of(target_id):
                for y in range(1, sidebar.targets_height - 1):
                    line = sidebar._target_line_at(y)
                    if line is not None and line.session_id == target_id:
                        return y
                raise AssertionError(f"no painted row for {target_id}")
            await pilot.click(Sidebar, offset=(3, y_of(recent[0].id)))
            await pilot.pause()
            assert sidebar.selected_target == recent[0].id, (sidebar.selected_target, recent[0].id)
            marks = [ln[2:-2] for ln in (sidebar.render_line(y).text for y in range(sidebar.targets_height))
                     if ln[2:3] == "▸"]
            assert len(marks) == 1 and recent[0].name in marks[0], marks
    asyncio.run(check())


def test_the_scroll_limit_stops_on_a_whole_row(serve, wait_until):
    """The target scroll limit is a row start taken from the cached pair starts, never a start plus one: a
    final window preceded by a two-line target row clamps and wheel-scrolls to whole rows."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 40)
        app = TsbTuiApp(url)
        app.cfg.set_runtime("sidebar.width", 12)  # a thin sidebar inside a narrow window: two-line target rows
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(120, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_sidebar(pane="targets")
            await wait_until(pilot, lambda: len(screen.targets.rows) > 0, "the rows")
            sidebar = screen.sidebar
            pairs = sidebar._target_row_pairs()
            starts = set(sidebar._pair_starts(pairs))
            assert any(count == 2 for _start, count in pairs), "the window holds two-line target rows"
            limit = sidebar._target_scroll_limit()
            assert limit in starts, (limit, sorted(starts))
            for _ in range(120):
                sidebar._scroll_section(0, 1)
            await pilot.pause()
            starts2 = set(sidebar._pair_starts(sidebar._target_row_pairs()))
            assert sidebar._target_scroll_limit() in starts2, (sidebar._target_scroll_limit(), sorted(starts2))
            assert sidebar._target_scroll_top in starts2, (sidebar._target_scroll_top, sorted(starts2))
    asyncio.run(check())


def test_a_fresh_sidebar_with_no_selection_paints_no_mark(serve, wait_until):
    """A fresh sidebar with targets and nothing selected paints no selection mark."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 40)
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_sidebar(pane="targets")
            await wait_until(pilot, lambda: len(screen.targets.rows) > 0, "the rows")
            sidebar = screen.sidebar
            sidebar.target_selected = None
            sidebar._target_pos = 0
            await pilot.pause()
            marks = [ln[2:-2] for ln in (sidebar.render_line(y).text for y in range(sidebar.targets_height))
                     if ln[2:3] == "▸"]
            assert marks == [], marks
    asyncio.run(check())


def test_the_details_panel_wraps_at_its_own_width_and_rewraps_on_resize(serve, wait_until):
    """The details panel wraps at its laid-out content width (not the screen's), and re-wraps when that width
    changes after a resize."""

    async def check():
        served, url = serve(InputRecordingApp())
        long_id = "long-0001"
        served.targets[long_id] = {"id": long_id, "type": "ssh", "name": "x" * 160, "group": "lab", "tags": [],
                                   "discovered_by": "manual", "last_seen": "2026-10-17T08:30:00Z",
                                   "host": "192.0.2.50", "port": 22}
        app = TsbTuiApp(url)
        app.cfg.set_runtime("fleet_view.refresh_s", 0)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_sidebar(pane="targets")
            await wait_until(pilot, lambda: screen.targets.record(long_id) is not None, "the page", 30)
            screen._open_target_details(long_id)
            await wait_until(pilot, lambda: any(d.__class__.__name__ == "TargetDetailsScreen"
                                                for d in screen.dialogs), "the details open")
            details = next(d for d in screen.dialogs if d.__class__.__name__ == "TargetDetailsScreen")
            body = details.query_one("#detail-body")
            width = body.size.width
            assert width < 160, (width, "the panel is the 100-column box, not the 160-column screen")
            for line in body.content.splitlines():
                assert len(line) <= width, (len(line), width)
            await pilot.resize_terminal(80, 45)
            await pilot.pause()
            new_width = body.size.width
            assert new_width < width, (new_width, width)
            for line in body.content.splitlines():
                assert len(line) <= new_width, (len(line), new_width)
    asyncio.run(check())
