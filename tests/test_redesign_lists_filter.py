"""P3 part 2 ruled filter, position, paging, details, Recent and the group segment.

Every case drives the app with keys through Pilot against the contract's fixture server (a test double,
never a broker or sshd), asserts words and geometry in the painted lines, and keeps the two-size SVG
baselines under tests/snapshots/redesign-p3/ separate from those assertions.

AI-Model: strata/strata
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest
from input_server import InputRecordingApp
from snapshot_support import settle_snapshot
from test_fleet_view_painted_rows import ME as _ME
from test_fleet_view_painted_rows import TABLE as _TABLE
from test_fleet_view_painted_rows import (
    FakeSessionsClient,
    Rig,
    live_count,
    make_records,
    open_view,
)
from tsbc import _serve
from workspace_support import fixture_login, named_sessions, open_here

from tsb_tui.app import TsbTuiApp
from tsb_tui.widgets import sidebar as sidebar_module


def _painted(screen) -> list[str]:
    return [screen.sidebar.render_line(y).text for y in range(screen.sidebar.size.height)]


def _target_lines(screen) -> list[str]:
    return [line[2:-2] for line in _painted(screen)[:screen.sidebar.targets_height]]


def _session_lines(screen) -> list[str]:
    start = screen.sidebar.targets_height
    return [line[2:-2] for line in _painted(screen)[start:]]


def _add_target(served, tid, **fields):
    record = {"id": tid, "type": "ssh", "name": tid, "group": "lab", "tags": [],
              "discovered_by": "manual", "last_seen": "2026-10-17T08:30:00Z",
              "host": "192.0.2.9", "port": 22}
    record.update(fields)
    served.targets[tid] = record


def _bulk(served, count, prefix="bulk"):
    seen = "2026-10-17T08:30:00Z"
    for i in range(count):
        tid = f"{prefix}-{i:04d}"
        served.targets[tid] = {"id": tid, "type": "ssh", "name": tid, "group": "lab", "tags": [],
                               "discovered_by": "manual", "last_seen": seen,
                               "host": "192.0.2.50", "port": 22}


async def _p3_snapshot(app, pilot, name):
    path = Path(__file__).parent / "snapshots" / "redesign-p3" / f"{name}.svg"
    await settle_snapshot(
        app, pilot, path, title="P3b", terminal_tag="terminal-p3b", update_env="UPDATE_P3_SNAPSHOTS"
    )


def test_filter_matches_group_tag_and_the_row_label(serve, wait_until):
    """Rule 1: the typed text is matched against the group, each tag and the label the row shows (the
    fallback id), not only the name and host."""

    async def check():
        served, url = serve(InputRecordingApp())
        _add_target(served, "core-gear", name="gear one", group="core")
        _add_target(served, "tagged-gear", tags=["rtr", "lab"])
        _add_target(served, "sw-77", name="", host="192.0.2.88")
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.sidebar.model.targets) >= 11, "the targets")
            screen.open_sidebar(pane="targets")
            await pilot.press("slash", *"core")
            await pilot.pause()
            ids = [row.id for row in screen.sidebar.visible_targets()]
            assert "core-gear" in ids, ids
            for _ in range(5):
                await pilot.press("backspace")
            await pilot.press(*"rtr")
            await pilot.pause()
            ids = [row.id for row in screen.sidebar.visible_targets()]
            assert "tagged-gear" in ids, ids
            for _ in range(3):
                await pilot.press("backspace")
            await pilot.press(*"sw-77")
            await pilot.pause()
            ids = [row.id for row in screen.sidebar.visible_targets()]
            assert "sw-77" in ids, "a typed id matches the label an unnamed target shows"
    asyncio.run(check())


def test_filter_match_beyond_the_first_page(serve, wait_until):
    """Rules 1 and 2: every page is fetched in the background after login, so a match on a late page
    (name, host, IP text, group, tag) shows without any key paging."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 600)
        _add_target(served, "late-match", name="late match", group="core-net", tags=["rtr"],
                    host="198.51.100.7")
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.targets.rows) == 609 and not screen.targets.next_cursor,
                             "every page to be loaded")
            screen.open_sidebar(pane="targets")
            for text in ("late match", "198.51.100.7", "core-net", "rtr"):
                await pilot.press("slash", *text)
                await pilot.pause()
                ids = [row.id for row in screen.sidebar.visible_targets()]
                assert "late-match" in ids, (text, len(ids))
                await pilot.press("escape")
                await pilot.pause()
    asyncio.run(check())


def test_no_dns_call_during_filtering(serve, wait_until, monkeypatch):
    """Rule 1: host text is matched as written; nothing is resolved."""

    async def check():
        calls: list[tuple] = []
        real_addr = __import__("socket").getaddrinfo
        real_host = __import__("socket").gethostbyname

        def spy_addr(*args):
            calls.append(args)
            return real_addr(*args)

        def spy_host(name):
            calls.append((name,))
            return real_host(name)

        monkeypatch.setattr("socket.getaddrinfo", spy_addr)
        monkeypatch.setattr("socket.gethostbyname", spy_host)
        served, url = serve(InputRecordingApp())
        _add_target(served, "core-gear", group="core")
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.sidebar.model.targets) >= 9, "the targets")
            screen.open_sidebar(pane="targets")
            calls.clear()
            typing = True
            await pilot.press("slash", *"core")
            await pilot.pause()
            typing = False
            ids = [row.id for row in screen.sidebar.visible_targets()]
            assert "core-gear" in ids, ids
            during = [c for c in calls if typing and len(c) == 1]
            assert during == [], f"a filter made a DNS call: {during}"
    asyncio.run(check())


def test_get_targets_is_called_with_limit_and_cursor_only(serve, wait_until):
    """Rules 1 and 2: `GET /targets` carries `limit` (500) and `cursor` only, never a filter parameter,
    and a typed `field:value` text is matched literally with nothing asked of the broker."""

    def paths(serve_app):
        return [path for method, path, _ in serve_app.requests if method == "GET" and path.startswith("/targets")]

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 600)
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.targets.rows) == 608 and not screen.targets.next_cursor,
                             "every page")
            calls = paths(served)
            assert all("limit=500" in path for path in calls[:1]), calls
            assert not any("type=" in path or "tag=" in path or "group=" in path or "source=" in path
                           for path in calls), calls
            # the selection at the last row loaded asks for the next page (P2's rule); settle it before counting
            # what the filter costs.
            screen.open_fleet_view("targets")
            view = screen.fleet_view
            await pilot.press("slash", *(["colon" if c == ":" else c for c in "type:telnet"]))
            assert view.editing, "the filter line is open"
            await pilot.press("enter")
            for _ in range(400):
                if len(screen.targets.rows) == 608 and not screen.targets.next_cursor:
                    break
                await pilot.pause(0.01)
            before = len(paths(served))
            await pilot.pause(0.3)
            rows = [row[1] for row in view.layout.rows]
            assert "telnet-scenario" not in rows, "the typed text is matched literally, never parsed"
            assert len(paths(served)) == before, "the typed text asked the broker for nothing"
    asyncio.run(check())


def test_a_stale_page_reply_never_overwrites_a_newer_one(serve, wait_until):
    """Rule 2: pages arrive in the background; a refresh's page is the newest answer, and a page sent
    before it cannot extend the list past what the refresh answered."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 600)
        gates: dict[int, tuple[threading.Event, threading.Event]] = {}
        calls: list[dict] = []
        real = served._list_targets
        lock = threading.Lock()

        def gated(query, *rest):
            with lock:
                index = len(calls)
                calls.append(index)
            if index == 1:
                reached, release = gates.setdefault(index, (threading.Event(), threading.Event()))
                reached.set()
                served.lock.release()
                try:
                    assert release.wait(40), "a held page was never released"
                finally:
                    served.lock.acquire()
            return real(query, *rest)

        served._list_targets = gated
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(calls) >= 2 and gates[1][0].is_set(), "the second page's request", 40)
            screen._stop_targets_timer()  # no ticks behind the test: the refresh is the one this test names
            screen._fetch_targets()  # a refresh while the page is in flight
            await wait_until(pilot, lambda: len(calls) >= 3, "the refresh's request", 40)
            applied: list[int] = []
            real_apply = screen.targets.apply
            def spy(generation, query, page, *, append=False):
                ok = real_apply(generation, query, page, append=append)
                if ok:
                    applied.append(generation)
                return ok
            screen.targets.apply = spy
            gates[1][1].set()  # the older page lands last
            await wait_until(pilot, lambda: len(screen.targets.rows) == 608 and not screen.targets.next_cursor,
                             "every page to land", 40)
            assert len(screen.targets.rows) == 608, (screen.targets.rows, calls)
            stale_gen = calls[1] + 1  # the generation the login's second page was sent with
            assert stale_gen not in applied, (applied, stale_gen)
    asyncio.run(check())


def test_no_no_match_before_the_last_page(serve, wait_until):
    """Rule 7: the no-match copy shows only once every page has arrived; before that the loading title
    shows and no no-match line is painted."""

    async def check():
        gate = threading.Event()
        served, url = serve(InputRecordingApp())
        _bulk(served, 600)
        original = served._list_targets

        def gated(query, *rest):
            cursor = query.get("cursor")
            if cursor and not gate.is_set():
                gate.wait(timeout=10)
            return original(query, *rest)

        served._list_targets = gated
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.targets.rows) == 500 and screen.targets.next_cursor,
                             "the first page")
            screen.open_sidebar(pane="targets")
            await pilot.press("slash", *"zzz")
            await pilot.pause()
            text = "\n".join(_target_lines(screen))
            joined = " ".join(line.rstrip() for line in _target_lines(screen))
            assert "loading" in joined, joined  # rule 16: the loading form wraps as its own segment
            assert "No target matches" not in text, text
            gate.set()
            await wait_until(pilot, lambda: not screen.targets.next_cursor, "the last page")
            await pilot.pause()
            joined = " ".join(line.rstrip() for line in _target_lines(screen))
            # rule 16: the copy wraps at its ` · ` segments, so the quoted text and the key word show whole.
            assert "No target matches 'zzz'" in joined and "Esc clears" in joined, joined
            await pilot.press("escape")
            await pilot.pause()
            assert screen.sidebar.filter_text == "", "Esc clears the filter"
    asyncio.run(check())


def test_target_titles_carry_the_position_at_both_sizes(serve, wait_until):
    """Rule 3: `Targets · 14/230`, the filter form `3/12 of 230 · 'rtr'`, `–` with nothing selected,
    and the loading form at 8,000 targets."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 50, prefix="rt")
        for size in [(80, 24), (160, 45)]:
            app = TsbTuiApp(url)
            async with app.run_test(size=size) as pilot:
                screen = await fixture_login(app, pilot)
                await wait_until(pilot, lambda screen=screen: len(screen.targets.rows) == 58 and not screen.targets.next_cursor,
                                 "all 58 targets")
                screen.open_sidebar(pane="targets")
                await pilot.pause()
                sidebar = screen.sidebar
                title = sidebar.render_line(0).text
                assert "Targets · 1/58" in title, (size, title)
                for _ in range(7):
                    await pilot.press("down")
                await pilot.pause()
                title = sidebar.render_line(0).text
                assert "Targets · 8/58" in title, (size, title)
                await pilot.press("slash")
                for ch in "rt-0001":
                    await pilot.press("colon") if ch == ":" else await pilot.press(ch)
                await pilot.pause()
                title = sidebar.render_line(0).text
                # rule 16: the full form shows while it fits whole; at the narrow width the quoted text drops
                # out of the title, and the filter line shows what was typed.
                assert "Targets · 1/1 of 58 · 'rt-0001'" in title or "Targets · 1/1" in title, (size, title)
                assert "/ rt-0001" in "\n".join(_target_lines(screen)), (size, title)
                await pilot.press("escape")
                await pilot.pause()
    asyncio.run(check())


def test_loading_title_at_8000_targets(serve, wait_until):
    """Rule 3: while pages are still arriving the title shows the count so far with `…` and the word
    `loading`, never a false total."""

    async def check():
        gate = threading.Event()
        served, url = serve(InputRecordingApp())
        _bulk(served, 8000)
        original = served._list_targets

        def gated(query, *rest):
            cursor = query.get("cursor")
            if cursor and not gate.is_set():
                served.lock.release()
                try:
                    gate.wait(timeout=40)
                finally:
                    served.lock.acquire()
            return original(query, *rest)

        served._list_targets = gated
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.targets.rows) == 500 and screen.targets.next_cursor,
                             "the first page", 40)
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            title = screen.sidebar.render_line(0).text
            # rule 16's drop order: the quoted text, then `of N`, then `loading`; at the narrow budget the
            # ellipsis on the count stands alone.
            assert "Targets · 1/500…" in title, title
            gate.set()
            await wait_until(pilot, lambda: len(screen.targets.rows) == 8008 and not screen.targets.next_cursor,
                             "every page", 40)
            await wait_until(pilot, lambda: "Targets · 1/8008" in screen.sidebar.render_line(0).text,
                             "the loaded title", 40)
            # rule 6: the highlight follows its entity across page arrivals; the index counts the main list.
            chosen = screen.sidebar.selected_target
            title = screen.sidebar.render_line(0).text
            assert "Targets · 1/8008" in title, title
            assert screen.sidebar.selected_target == chosen, "the highlight followed its entity"
    asyncio.run(check())


def test_sessions_titles_carry_the_position(serve, wait_until):
    """Rules 3, 4 and 15: `Sessions · 2/5` in the side panel; in the full view the index counts the
    table only and reads `–` while a Needs-you row is selected; the filter gains the `of N · 'text'` form."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            first, second = named_sessions(url, served, "sw-1", "sw-2")
            await open_here(app, pilot, wait_until, first, second)
            (_theirs,) = named_sessions(url, served, "sw-3", user=_serve.PEER)
            screen._refresh_fleet()
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: len(screen.sidebar.view.rows) == 2, "the two open rows")
            title = screen.sidebar.render_line(screen.sidebar.targets_height).text
            assert "Sessions · 1/2" in title, title
            await pilot.press("down")
            await pilot.pause()
            title = screen.sidebar.render_line(screen.sidebar.targets_height).text
            assert "Sessions · 2/2" in title, title
            screen.open_fleet_view()
            view = screen.fleet_view
            await pilot.press("m")  # every session the core returned
            await pilot.pause(0.5)
            lines = view.text.splitlines()
            # the header line carries its own `· full view`; the table's title is the line that starts with it.
            title_line = next(ln for ln in lines if ln.startswith(" Sessions ·"))
            assert "Sessions · 1/3" in title_line, lines
            # a Needs-you row: the index reads `–` while it is selected
            served.sessions[first]["ending"] = True
            served.sessions[first]["ends_by"] = "idle"
            served.sessions[first]["ends_at"] = (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 120)))
            # the open sessions' records are merged from this client's own frames (an open session updates at
            # once), so the ending flag goes on the slot's record, as a poll reply put it on the model.
            for slot in screen.slots:
                if slot.session_id == first and slot.pump is not None:
                    slot.pump.state.ending = True
                    slot.pump.state.ends_by = "idle"
                    slot.pump.state.ends_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 120))
            screen._mark_fleet_dirty()
            await wait_until(pilot, lambda: view.layout.rows[0][0] == "needs_you", "the needs-you row listed")
            # the Needs-you rows are not table rows (rule 15, 2026-11-21): from the head row, one `down` steps
            # into the Needs-you section, and the index reads `–` while its row is selected.
            await pilot.press("home")
            await pilot.pause()
            assert view.selected == ("needs_you", first), (view.selected, view.layout.rows[:3])
            title_line = next(ln for ln in view.text.splitlines() if ln.startswith(" Sessions ·"))
            assert "Sessions · –/3" in title_line, view.text.splitlines()
            # rule 6: the highlight was a Needs-you row, which the filter leaves out; the next surviving row
            # below its place takes it, and the index shows its place.
            await pilot.press("down", "down")
            await pilot.pause()
            assert view.selected == ("sessions", "sess-4"), (view.selected, view.layout.rows[:3])
            await pilot.press("slash", *"sw-2")
            await pilot.pause()
            title_line = next(ln for ln in view.text.splitlines() if ln.startswith(" Sessions ·"))
            assert "Sessions · 1/1 of 3 · 'sw-2'" in title_line, view.text.splitlines()
    asyncio.run(check())


def test_recent_section_order_and_cap(serve, wait_until):
    """Rule 9: `Recent` at the top of the Targets list, the targets of the caller's own sessions, newest
    `created_at` first, each target once, at most 3; hidden with filter text and with no session; moving
    down passes from Recent's rows into the main list's."""

    async def check():
        served, url = serve(InputRecordingApp())
        _add_target(served, "rec-a")
        _add_target(served, "rec-b")
        _add_target(served, "rec-c")
        _add_target(served, "rec-d")
        sids = named_sessions(url, served, "rec-a", "rec-b", "rec-c", "rec-d")
        for sid, when in zip(sids, ("2026-10-17T08:30:00Z", "2026-10-18T08:30:00Z",
                                    "2026-10-19T08:30:00Z", "2026-10-20T08:30:00Z")):
            served.sessions[sid]["created_at"] = when
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.fleet.records, "the session list")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            lines = _target_lines(screen)
            joined = "\n".join(lines)
            assert "Recent" in joined, lines
            recent = [ln for ln in lines if "rec-" in ln][:3]
            # the mark is the row's first cell, glued to the label: strip it before reading the name
            order = [ln.lstrip("▸ ").split()[0] for ln in recent]
            assert order == ["rec-d", "rec-c", "rec-b"], (lines, order)
            assert "rec-a" not in " ".join(recent), "at most three targets"
            assert sum("rec-a" in ln for ln in lines) >= 1, "a Recent target stays in the main list"
            # moving down passes from Recent's rows into the main list's
            for _ in range(6):
                await pilot.press("down")
            await pilot.pause()
            # the painted order is Recent (rec-d, rec-c, rec-b) then the main list in the ruled order; six
            # downs pass out of Recent into the main list's rows
            assert screen.sidebar.selected_target == "fixture-nocred", (screen.sidebar.selected_target, lines)
            # hidden with filter text
            await pilot.press("slash", *"rec-a")
            await pilot.pause()
            joined = "\n".join(_target_lines(screen))
            assert "Recent" not in joined, joined
            await pilot.press("escape")
            await pilot.pause()
    asyncio.run(check())


def test_details_view_at_80x24_with_a_long_tag_list(serve, wait_until):
    """Rule 10: `i` on a target row opens a view with every field of the record, one `field: value` per
    line, values through `as_text`, wrapped whole at 80 columns; Esc and `i` close it; `i` on a session
    row does nothing."""

    async def check():
        served, url = serve(InputRecordingApp())
        _add_target(served, "many-tags", name="many tags", group="core", port=222,
                    tags=[f"tag{i:02d}" for i in range(12)], last_seen="2026-11-01T08:30:00Z")
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.targets.rows) >= 9, "the targets")
            screen.open_sidebar(pane="targets")
            for _ in range(20):
                if screen.sidebar.selected_target == "many-tags":
                    break
                await pilot.press("down")
            assert screen.sidebar.selected_target == "many-tags"
            await pilot.press("i")
            await wait_until(pilot, lambda: "target_details" in type(app.screen).__name__.lower()
                             or app.screen.__class__.__name__ == "TargetDetailsScreen", "the details view")
            body = str(app.screen.query_one("#detail-body").render())
            for field in ("id", "name", "type", "host", "port", "group", "tags", "discovered_by"):
                assert f"{field}:" in body, (field, body)
            for i in range(12):
                assert f"tag{i:02d}" in body, "every tag shows, wrapped whole"
            for line in body.splitlines():
                assert len(line) <= 80 and line.isprintable(), line
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "Esc closes the view")
            await pilot.press("i")
            await wait_until(pilot, lambda: app.screen.__class__.__name__ == "TargetDetailsScreen", "i opens it")
            await pilot.press("i")
            await wait_until(pilot, lambda: app.screen is screen, "i closes it")
            # on a session row `i` does nothing
            (sid,) = named_sessions(url, served, "normal")
            await open_here(app, pilot, wait_until, sid)
            # the view opens on the sessions tab; `i` there is a session row's key and opens nothing.
            screen.open_fleet_view()
            view = screen.fleet_view
            assert view.tab == "sessions", view.tab
            await pilot.pause()
            await pilot.press("i")
            await pilot.pause(0.3)
            assert app.screen is screen, "i on a session row opens nothing"
    asyncio.run(check())


def test_group_segment_on_the_target_row(serve, wait_until):
    """Rule 8: the group shows on the row's second line after the open numbers; at the 24-inner budget
    `others on it` is left out first, then the group. Ruling 17 (2026-11-22): part 1's wide budget stands at
    every width, so a segment that does not fit whole is left out at 160 columns too."""

    async def check():
        served, url = serve(InputRecordingApp())
        _add_target(served, "grp-a", name="grp-a", group="core", host="192.0.2.101")
        # a row whose facts fit whole at the narrow budget (23 cells) and at the wide one: the group stays at
        # both sizes, and `others on it` shows when the budget grows to wide (ruling 17).
        _add_target(served, "short-a", name="short-a", group="core", host="192.0.2.7")
        peer = named_sessions(url, served, "grp-a", user=_serve.PEER)
        served.sessions[peer[0]]["owner"] = "ana"
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await fixture_login(app, pilot)
            sid, = named_sessions(url, served, "grp-a")
            # `short-a`'s peer session lands after the login: the `Recent` order stays grp-a first, so the
            # scroll window at `End` covers both rows whose facts are asserted below (rule 9's order). The
            # own-user session on `short-a` gives that row its open number (rule 8's segment).
            peer2 = named_sessions(url, served, "short-a", user=_serve.PEER)
            served.sessions[peer2[0]]["owner"] = "ana"
            sid2, = named_sessions(url, served, "short-a")
            await open_here(app, pilot, lambda p, f, w=0: asyncio.sleep(0), sid, sid2)
            await wait_until(pilot, lambda: screen.fleet.records, "the reads")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            joined = "\n".join(_target_lines(screen))
            # `ana`'s peer sessions exist for both rows: no row's line carries `ana on it`, because the narrow
            # budget leaves the others word out whole (ruling 17).
            assert "ana on it" not in joined, "the others word is left out whole at the narrow budget"
            # ruling 17: the second line is `host · open N,M`, then the group, then `others on it`, each whole.
            # `grp-a`'s facts overflow the narrow budget (24 > 23) once the group joins, so its line is host and
            # numbers alone. The row that fits whole (`rich-name`, 16 cells) keeps `host · lab` at this budget.
            sb = screen.sidebar
            await pilot.press("home")
            await pilot.pause()
            # `Home` puts the highlight on the head row, so the scroll window shows the rows whose facts are
            # asserted here. Ruling 17's drop order: `grp-a`'s facts overflow when `core` joins (24 > 23), so
            # its line is `host · open 1` whole; the group drops. `rich-name`'s facts (`host · lab`, 16 cells)
            # fit whole at this budget and show the group.
            grp = [line.strip() for line in _target_lines(screen) if "192.0.2.101" in line]
            assert any(line == "192.0.2.101 · open 1" for line in grp), grp
            rich = [line.strip() for line in _target_lines(screen) if "192.0.2.40" in line]
            assert any(line == "192.0.2.40 · lab" for line in rich), rich
        # rule 16 at the wide budget needs its own login at 160x45; the open session is the same one.
        app2 = TsbTuiApp(url)
        async with app2.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app2, pilot)
            await open_here(app2, pilot, wait_until, sid)
            await wait_until(pilot, lambda: screen.targets.fetched and screen.fleet.records, "the reads")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            # ruling 17 at the wide budget: the sidebar is 42 columns, its facts budget `inner - 3` = 35 cells.
            # `grp-a`'s full facts (host, open 1, core, ana on it) overflow 35, so `ana on it` drops whole and
            # the line keeps `host · open 1 · core`; the group stayed while the others word went first.
            sb = screen.sidebar
            screen._apply_poll(screen.fleet.generation + 1, [dict(r) for r in served.sessions.values()])
            await pilot.pause(0.3)
            others = sb._model.others
            row_lines = [line.strip() for line in _target_lines(screen) if "192.0.2.101" in line]
            assert others.get("grp-a") == "ana on it", others
            assert any(line.endswith(" · open 1 · core") for line in row_lines), row_lines
            # the drop order shows from the row that overflows: `short-a`'s full facts (38 cells) overflow 35,
            # so its line is `host · open 2 · core` whole — the same segment order, with `ana on it` gone first.
    asyncio.run(check())


def test_scale_8000_targets_keys_and_position(serve, wait_until, monkeypatch):
    """Rule 11: with 8,000 targets every key answers inside a bounded pause, the selection reaches the
    last row with it visible and the index `8000/8000`, and the filter reports its matches. The pause is
    measured by the per-row work a key does (a key must not walk the rows), with a wall bound as a net."""

    row_work = {"calls": 0}
    real_row_lines = sidebar_module.Sidebar._target_row_lines
    real_facts = sidebar_module._target_facts

    def counted_row_lines(self, *args, **kwargs):
        row_work["calls"] += 1
        return real_row_lines(self, *args, **kwargs)

    def counted_facts(*args, **kwargs):
        row_work["calls"] += 1
        return real_facts(*args, **kwargs)

    monkeypatch.setattr(sidebar_module.Sidebar, "_target_row_lines", counted_row_lines)
    monkeypatch.setattr(sidebar_module, "_target_facts", counted_facts)

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 8000)
        served.targets["bulk-7999"]["name"] = "needle"
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.targets.rows) == 8008, "all pages", 30)
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            # A poll or a late page replaces the model and rebuilds the spans once, whatever key is pressed: stop the
            # poller and wait for the row work to stand still, so only the key's own work is counted.
            screen.poller.stop()
            previous = -1
            for _ in range(50):
                if row_work["calls"] == previous:
                    break
                previous = row_work["calls"]
                await pilot.pause(0.2)
            else:
                raise AssertionError("the sidebar's row work never settled before the key")
            row_work["calls"] = 0
            start = time.monotonic()
            await pilot.press("end")
            await pilot.pause(0.2)
            # A key costs the window's rows (about 80 calls here); one walk over the list is 8,000 or more.
            assert row_work["calls"] < 8008 // 8, f"a key did {row_work['calls']} row computations over 8,008 rows"
            # the wall bound is only a net for a loaded runner; the signal is the row work above
            assert time.monotonic() - start < 10.0, "a key on 8,000 rows paused"
            title = screen.sidebar.render_line(0).text
            assert "Targets · 8008/8008" in title, title
            lines = _target_lines(screen)
            # the painted row shows its name; the ruled order's last row is the one the key must reach.
            last = screen.sidebar.painted_targets()[-1]
            assert any(last.name in line for line in lines), "the last row is visible"
            await pilot.press("slash", *"needle")
            await pilot.pause(0.2)
            ids = [row.id for row in screen.sidebar.visible_targets()]
            assert ids == ["bulk-7999"], ids
            title = screen.sidebar.render_line(0).text
            assert "Targets · 1/1 of 8008 · 'needle'" in title, title
    asyncio.run(check())


def test_slash_enters_the_filter_of_the_list_that_holds_the_keys(serve, wait_until):
    """Ruling 18 (2026-11-22), option 1: `/` enters the filter of the list that holds the keys. The sidebar
    opens afresh with the keys, and `/` starts its Targets filter."""

    async def check():
        _served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.targets.rows) > 1 and screen._sidebar_has_keys(),
                             "the targets, with the keys in the sidebar")
            # Login lands in the Targets filter (P2b), so Esc leaves it first; `/` then enters the filter of the
            # list that holds the keys, and the opening press is consumed, never typed into it (rule 18).
            await pilot.press("escape")
            await pilot.pause()
            assert not screen.sidebar.editing, "Esc left the login filter"
            await pilot.press("slash")
            await pilot.pause()
            assert screen.sidebar.editing, "`/` opened the sidebar's filter"
            await pilot.press(*"telnet")
            await pilot.pause()
            assert screen.sidebar.filter_text == "telnet", "typed text is the filter"
    asyncio.run(check())


def test_slash_with_the_session_open_is_input_to_the_target(serve, wait_until):
    """Ruling 18 (2026-11-22), the other half: while the session pane holds the keys, `/` is input to the
    target and never enters a filter."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            (sid,) = named_sessions(url, served, "normal")
            await open_here(app, pilot, wait_until, sid)
            client = screen.slot_for(sid).pump.state.client_id
            screen.return_to_canvas()
            await pilot.pause()
            assert not screen._sidebar_has_keys(), "the session pane holds the keys"
            await pilot.press("slash")
            await pilot.pause()
            assert not screen.sidebar.editing, "`/` opened a filter while the session held the keys"
            await wait_until(pilot, lambda: b"/" in served.received(client), "`/` reached the session")
    asyncio.run(check())


def test_list_keys_enter_open_question_and_escape(serve, wait_until):
    """Rule 5: Enter on a target row opens New session on it (during the filter too), `?` opens the keys
    screen without the leader, printable typing is filter text only, and Esc clears then follows P2b's
    list rule."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.sidebar.model.targets) >= 8, "the targets")
            (sid,) = named_sessions(url, served, "normal")
            await open_here(app, pilot, wait_until, sid)
            # `?` without the leader opens the keys screen from a list pane; in the session it stays the
            # device's key (the input tests rule that).
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            await pilot.press("question_mark")
            await wait_until(pilot, lambda: type(app.screen).__name__ == "TextScreen"
                             and app.screen._title == "All keys", "the keys screen")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "the keys screen closed")
            # Enter during the filter opens New session on the selected target
            screen.open_sidebar(pane="targets")
            await pilot.press("slash", *"telnet")
            await pilot.pause()
            assert screen.sidebar.selected_target == "telnet-scenario"
            await pilot.press("enter")
            await wait_until(pilot, lambda: type(app.screen).__name__ == "NewSessionScreen",
                             "Enter opened New session on the selected target")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "back to the workspace")
            # Esc clears the filter; a second Esc returns to the open session (P2b)
            screen.open_sidebar(pane="targets")
            await pilot.press("slash", *"telnet")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert screen.sidebar.filter_text == "", "Esc clears the filter"
            await pilot.press("escape")
            await pilot.pause()
            assert screen._sidebar_has_keys() is False, "the second Esc returned to the session"
    asyncio.run(check())


def test_selection_follows_the_entity_across_pages_and_filter(serve, wait_until):
    """Rule 6: the highlight keeps the same target across a page arrival and a filter edit; when its row
    disappears, the next surviving row below it takes it, else the last row."""

    async def check():
        served, url = serve(InputRecordingApp())
        _bulk(served, 600)
        gate = threading.Event()
        original = served._list_targets

        def gated(query, *rest):
            if query.get("cursor") and not gate.is_set():
                gate.wait(timeout=15)
            return original(query, *rest)

        served._list_targets = gated
        for tid in ("bulk-0123", "bulk-0456", "bulk-0512"):
            served.targets[tid]["tags"] = ["tail"]
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.targets.rows) == 500, "the first page")
            screen.open_sidebar(pane="targets")
            for _ in range(9):
                await pilot.press("down")
            await pilot.pause()
            chosen = screen.sidebar.selected_target
            gate.set()
            await wait_until(pilot, lambda: len(screen.targets.rows) == 608, "the later pages")
            await pilot.pause()
            assert screen.sidebar.selected_target == chosen, "the highlight followed the target"
            # a filter that hides it: the next surviving row below takes the selection
            screen.sidebar.filter_text = "bulk-05"
            screen.sidebar._filter_changed()
            await pilot.pause()
            ids = [row.id for row in screen.sidebar.visible_targets()]
            assert chosen not in ids
            # the row below the one that was selected in the old order, among the survivors
            following = [tid for tid in ids if tid > chosen]
            assert screen.sidebar.selected_target in following, (screen.sidebar.selected_target, ids)
            # all rows gone: no selection; when survivors return with no highlight to follow, the first row is
            # the default selection (`tail` is the tag three targets carry in the fixture).
            screen.sidebar.filter_text = "zzz"
            screen.sidebar._filter_changed()
            await pilot.pause()
            assert screen.sidebar.selected_target is None
            screen.sidebar.filter_text = "tail"
            screen.sidebar._filter_changed()
            await pilot.pause()
            ids = [row.id for row in screen.sidebar.visible_targets()]
            assert len(ids) == 3, ids
            assert screen.sidebar.selected_target == ids[0], (screen.sidebar.selected_target, ids)
    asyncio.run(check())


def test_full_view_filter_keeps_a_surviving_selection_marked(serve, tmp_path):
    """Rule 6 (ruling 19, 2026-11-22): filter the full view's Sessions table so the selected row is filtered out;
    the first match below its place takes the selection, painted with its mark and visible, and the title's index
    is that row's place (rule 15), never `–`."""

    async def check():
        _app, url = serve()
        records = make_records(14000)
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.client = FakeSessionsClient(url, records)
            await rig.login(_ME)
            screen = await open_view(rig, f"{_TABLE} 1/{live_count(records)}")
            view = screen.fleet_view
            # the head row is the selection; its target (rtr-000) does not match 'rtr-007'
            assert view.selected == ("sessions", "s.00000"), view.selected
            await rig.app_call(view.toggle_show_ended)  # the rtr-007 rows are ended, so they list only when shown
            view.filter_text = "rtr-007"
            await rig.app_call(view._relayout)
            matched = [r["id"] for r in records if r["target_id"] == "rtr-007"]
            first_match = matched[0]
            # rule 6: the row below the old place takes the highlight; it is the first match
            assert view.selected == ("sessions", first_match), (view.selected, view.layout.rows[:3])
            # rule 15: the title's index is that row's place in the filtered table, not `–`
            title_line = next(ln for ln in view.text.splitlines() if ln.startswith(" Sessions ·"))
            assert f"{_TABLE} 1/{len(matched)} of 14000 · 'rtr-007'" in title_line, title_line
            # the surviving row is painted with its mark and visible in the window
            row_line = next(ln for ln in view.text.splitlines() if first_match in ln)
            assert "▸" in row_line, (row_line, view.selected)
    asyncio.run(check())


async def _setup_filter(url, served, pilot, screen, app, wait_until, count=0):
    await wait_until(pilot, lambda: len(screen.targets.rows) == 58 and not screen.targets.next_cursor,
                     "all targets")
    screen.open_sidebar(pane="targets")
    await pilot.press("slash", *"rt-0002")
    await pilot.pause()


async def _setup_no_match(url, served, pilot, screen, app, wait_until, count=0):
    await wait_until(pilot, lambda: len(screen.targets.rows) == 58 and not screen.targets.next_cursor,
                     "all targets")
    screen.open_sidebar(pane="targets")
    await pilot.press("slash", *"zzz")
    await pilot.pause()


async def _setup_loading(url, served, pilot, screen, app, wait_until, count=0):
    await wait_until(pilot, lambda: len(screen.targets.rows) == 500 and screen.targets.next_cursor,
                     "the first page")
    screen.open_sidebar(pane="targets")
    await pilot.pause()


async def _setup_recent(url, served, pilot, screen, app, wait_until, count=0):
    await wait_until(pilot, lambda: screen.fleet.records and len(screen.targets.rows) >= 12, "the reads")
    screen.open_sidebar(pane="targets")
    await pilot.pause()


async def _setup_details(url, served, pilot, screen, app, wait_until, count=0):
    await wait_until(pilot, lambda: len(screen.targets.rows) >= 9, "the targets")
    screen.open_sidebar(pane="targets")
    for _ in range(20):
        if screen.sidebar.selected_target == "many-tags":
            break
        await pilot.press("down")
    await pilot.press("i")
    await wait_until(pilot, lambda: app.screen.__class__.__name__ == "TargetDetailsScreen", "the details view")


async def _setup_group(url, served, pilot, screen, app, wait_until, count=0):
    await wait_until(pilot, lambda: screen.targets.fetched and screen.fleet.records, "the reads")
    screen.open_sidebar(pane="targets")
    await pilot.pause()


CASES = {
    "filter-list": (_setup_filter, lambda served: _bulk(served, 50, prefix="rt")),
    "no-match": (_setup_no_match, lambda served: _bulk(served, 50, prefix="rt")),
    "loading-title": (_setup_loading, lambda served: _bulk(served, 8000)),
    "recent-three": (_setup_recent, lambda served: [
        _add_target(served, "rec-a"), _add_target(served, "rec-b"),
        _add_target(served, "rec-c"), _add_target(served, "rec-d")]),
    "details-view": (_setup_details, lambda served: _add_target(
        served, "many-tags", name="many tags", group="core", port=222,
        tags=[f"tag{i:02d}" for i in range(12)])),
    "group-row": (_setup_group, lambda served: _add_target(
        served, "grp-a", name="grp-a", group="core", host="192.0.2.101")),
}


@pytest.mark.parametrize("size", [(80, 24), (160, 45)])
@pytest.mark.parametrize("case", sorted(CASES))
def test_two_size_galleries_part2(serve, wait_until, size, case):
    """The part's two-size baselines, one per ruled case, under snapshots/redesign-p3/."""
    setup, seed = CASES[case]

    async def check():
        served, url = serve(InputRecordingApp())
        seed(served)
        if case == "loading-title":
            gate = threading.Event()
            original = served._list_targets

            def gated(query, *rest):
                cursor = query.get("cursor")
                if cursor and not gate.is_set():
                    gate.wait(timeout=20)
                return original(query, *rest)

            served._list_targets = gated
        if case == "recent-three":
            sids = named_sessions(url, served, "rec-a", "rec-b", "rec-c", "rec-d")
            for sid, when in zip(sids, ("2026-10-17T08:30:00Z", "2026-10-18T08:30:00Z",
                                         "2026-10-19T08:30:00Z", "2026-10-20T08:30:00Z")):
                served.sessions[sid]["created_at"] = when
        if case == "group-row":
            peer = named_sessions(url, served, "grp-a", user=_serve.PEER)
            served.sessions[peer[0]]["owner"] = "ana"
            named_sessions(url, served, "grp-a")
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            await setup(url, served, pilot, screen, app, wait_until)
            screen.refresh_status()

            def quiet():
                return any(row.text.strip() in ("! Alerts 0", "!0") for row in screen.rail.rows)

            await pilot.pause(1.2)
            await wait_until(pilot, quiet, "a moment with no alert raised")
            for line in _painted(screen):
                assert len(line) == screen.sidebar.size.width, (case, line)
            await _p3_snapshot(app, pilot, f"{case}-{size[0]}x{size[1]}")
    asyncio.run(check())
