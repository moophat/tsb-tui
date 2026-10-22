"""The full-screen Fleet view (design §2.3, D7, D8; mockup F1): tabs, Needs you first, the table of the sessions this
login owns or may join, `mine│joinable`, the filter, "show ended", `group_by`, `Esc` back to the session.

Why it matters (Rule 9): the view is where an operator with hundreds of sessions finds the one that needs them. A poll
reply that lands late and overwrites a newer list shows sessions that have gone; an Alerts tab for a login that holds
no `broker.health` is a door the core will refuse; an `Esc` that returns to the wrong session, or a view that asks a
session for a size while it opens (D44), each cost the operator a session or someone else's screen. Servers: the
contract's in-process fixture server (DT7) for the sessions, and the TUI's stub where a test needs a poll held on a gate.
"""

from __future__ import annotations

import asyncio
import threading

from input_server import InputRecordingApp
from stub_server import SlowChunkedApp, StubServer, resize_requests
from tsb_contract_client import Client
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens import fleet_view as fleet_view_module
from tsb_tui.screens.fleet_view import (
    GROUP_NEEDS,
    GROUP_TABLE,
    FleetRow,
    FleetView,
    FleetViewModel,
    fleet_view_lines,
    visible_rows,
)
from tsbc import _serve
from workspace_support import fixture_login, login, named_sessions, open_here

LEADER = "ctrl+backslash"


def _row(sid: str, **fields) -> FleetRow:
    base = {"session_id": sid, "who": "●", "target": sid, "id_label": f"s.{sid}", "type": "shell",
            "state": "● live", "state_role": "ok", "keys": "you", "open": "", "clients": "1", "owner": "you",
            "ends": "6d 23h · idle", "rec": True, "front": False, "note": "", "types": "you type", "search": sid}
    base.update(fields)
    return FleetRow(**base)


def _model(rows: list[FleetRow], needs: list[FleetRow] | None = None, **fields) -> FleetViewModel:
    values = {"needs": tuple(needs or ()), "rows": tuple(rows), "mode": "mine", "alert_lines": ("an alert",),
              "back_label": "1 rtr-core-01", "tabs": ("sessions", "targets", "alerts")}
    values.update(fields)
    return FleetViewModel(**values)


def _layout(model: FleetViewModel, *, width: int = 116, height: int = 36, **overrides):
    values = {"tab": "sessions", "selected": None, "filter_text": "", "editing": False, "show_ended": False,
              "group_by": "none", "details": False, "top": 0}
    values.update(overrides)
    return fleet_view_lines(model, width=width, height=height, **values)


def _text(layout) -> list[str]:
    return [line.text for line in layout.lines]


def _y(view: FleetView, sid: str) -> int:
    return next(i for i, line in enumerate(view.layout.lines) if line.session_id == sid)


# -- the layout, from the models --


def test_the_table_has_the_columns_of_f1_and_loses_them_in_a_fixed_order_as_it_narrows() -> None:
    """Mockup F1: TARGET, ID, TYPE, STATE, KEYS, OPEN, CLIENTS, OWNER, ENDS IN and REC. On a phone's 61 columns the
    target and the keys stay and the rest leave from the right, so a narrower view never shows a column a wider one
    hides (the same sessions read the same way at every size)."""
    rows = [_row("rtr-core-01", target="rtr-core-01", id_label="s.7f3a2c", open="1")]
    headers = ("TARGET", "ID", "TYPE", "STATE", "KEYS", "OPEN", "CLIENTS", "OWNER", "ENDS IN", "REC")
    wide = "\n".join(_text(_layout(_model(rows), width=116)))
    assert all(h in wide for h in headers), wide
    previous = set(headers)
    for width in (100, 80, 61, 40):
        text = "\n".join(_text(_layout(_model(rows), width=width)))
        shown = {h for h in headers if h in text}
        assert shown <= previous, f"a column came back at {width} columns"
        assert {"TARGET"} <= shown
        previous = shown
    phone = "\n".join(_text(_layout(_model(rows), width=61)))
    assert "KEYS" in phone and "REC" not in phone and "TYPE" not in phone


def test_needs_you_comes_first_and_says_why_then_the_table_lists_every_session() -> None:
    """D8, mockup F1: "Needs you first, then the sessions you own or may join". A session that needs the operator
    is in both: the block is a highlight over the table, as in the sidebar."""
    stuck = _row("sw-agg-02", who="▲", note="not answering · ends in 4:12", types="linh types", ends="▲ 4:12 · link")
    lines = _text(_layout(_model([stuck, _row("rtr-core-01")], needs=[stuck])))
    heading = [i for i, line in enumerate(lines) if "Needs you" in line]
    table = [i for i, line in enumerate(lines) if "Sessions you own or may join · 2" in line]
    assert heading and table and heading[0] < table[0]
    assert any("sw-agg-02" in line and "linh types" in line and "not answering · ends in 4:12" in line
               for line in lines[heading[0]:table[0]])
    assert sum("sw-agg-02" in line for line in lines) == 2, "in Needs you and in the table"


def test_the_filter_show_ended_and_group_by_shape_the_table_and_never_the_core() -> None:
    """D8, the knob file: the filter matches target, id, login and owner; an ended session is listed only when
    "show ended" is on; `group_by = target` puts a heading above each target's sessions. All three change what is
    shown, none changes what the core returned (DT15)."""
    rows = [_row("a", target="rtr-core-01", search="rtr-core-01 s.a you"),
            _row("b", target="rtr-core-01", search="rtr-core-01 s.b you"),
            _row("c", target="fw-edge-01", search="fw-edge-01 s.c linh", keys="linh"),
            _row("d", target="old-box", ended=True, state="✕ killed", state_role="danger",
                 search="old-box s.d you")]
    model = _model(rows)
    assert [r.session_id for r in visible_rows(model, "", False)] == ["a", "b", "c"]
    assert [r.session_id for r in visible_rows(model, "", True)] == ["a", "b", "c", "d"]
    assert [r.session_id for r in visible_rows(model, "linh", False)] == ["c"]
    assert [r.session_id for r in visible_rows(model, "OLD", True)] == ["d"]
    assert _layout(model, filter_text="linh").shown == 1
    flat = _text(_layout(model))
    assert not any("rtr-core-01 ·" in line for line in flat)
    grouped = _text(_layout(model, group_by="target"))
    assert any(line.strip() == "rtr-core-01 · 2" for line in grouped)
    assert any(line.strip() == "fw-edge-01 · 1" for line in grouped)


def test_the_details_sit_beside_the_table_only_from_details_from_columns() -> None:
    """D8: "details sit beside the table from 160 columns" (`fleet_view.details_from`): the selected session's own
    sentences, behind a hairline, never at a width where they would squeeze the table."""
    rows = [_row("a", target="rtr-core-01")]
    model = _model(rows, detail_for=lambda sid: (f"1 {sid} · s.a · yours", "you type since 14:31"),
                   buttons_for=lambda sid: ("t Take back", "⏎ Open"))
    selected = (GROUP_TABLE, "a")
    wide = _text(_layout(model, width=170, details=True, selected=selected))
    assert any("│" in line and "you type since 14:31" in line for line in wide)
    assert any("[t Take back] [⏎ Open]" in line for line in wide)
    narrow = _text(_layout(model, width=120, details=False, selected=selected))
    assert not any("you type since" in line for line in narrow)


def test_the_selection_stays_in_view_as_the_table_scrolls() -> None:
    """A table longer than the window scrolls so the selected row is always on screen, and the rows above it are not
    lost: the operator moves with the keys or the wheel through a list of hundreds."""
    rows = [_row(f"s{n:03d}", target=f"host-{n:03d}") for n in range(60)]
    model = _model(rows)
    first = _layout(model, height=20, selected=(GROUP_TABLE, "s000"))
    assert first.top == 0
    far = _layout(model, height=20, selected=(GROUP_TABLE, "s040"), top=first.top)
    assert far.top > 0
    assert any("host-040" in line for line in _text(far))
    assert not any("host-000" in line for line in _text(far))
    back = _layout(model, height=20, selected=(GROUP_TABLE, "s001"), top=far.top)
    assert back.top < far.top and any("host-001" in line for line in _text(back))


def test_a_keystroke_builds_only_the_lines_of_the_rows_in_the_window_however_long_the_table(monkeypatch) -> None:
    """The filter must stay fast at 500 sessions (plan DT8, a hundred milliseconds): every keystroke re-lays the view
    out, so the work per keystroke is bounded by what the window shows, not by the length of the table. Counted, not
    timed: a layout that built a line for each of the 500 rows and threw most away passed every output test."""
    built: list[str] = []
    real = fleet_view_module._table_row

    def counting(row, *args, **kwargs):
        built.append(row.session_id)
        return real(row, *args, **kwargs)

    monkeypatch.setattr(fleet_view_module, "_table_row", counting)
    rows = [_row(f"s{n:03d}", target=f"host-{n:03d}", search=f"host-{n:03d} s.{n:03d} you you") for n in range(500)]
    model = _model(rows)
    height = 30
    for text in ("", "h", "ho", "host-4", "host-49", "host-499"):
        built.clear()
        layout = _layout(model, height=height, filter_text=text, editing=True)
        assert len(built) <= height, f"{len(built)} row lines built for one layout at filter {text!r}"
        assert layout.shown == len([r for r in rows if text in r.search])
        assert len(layout.rows) == layout.shown  # the selection still knows every row, built or not
    built.clear()
    far = _layout(model, height=height, selected=(GROUP_TABLE, "s450"))
    assert len(built) <= height
    assert any("host-450" in line for line in _text(far))


def test_grouping_by_target_counts_each_group_once_however_long_the_table(monkeypatch) -> None:
    """`group_by = "target"` at 500 sessions: the group headers' counts come from one pass over the table, not one scan
    per group. Counted, not timed: every `target` read of a row is tallied, and a count per group would read each row
    once per group header."""
    reads = {"n": 0}

    class Counted(FleetRow):
        @property
        def target(self) -> str:
            reads["n"] += 1
            return self._target

    rows = []
    for n in range(500):
        row = _row(f"s{n:03d}", target=f"host-{n // 5:03d}")
        counted = Counted.__new__(Counted)
        for name in row.__dataclass_fields__:
            object.__setattr__(counted, name if name != "target" else "_target", getattr(row, name))
        rows.append(counted)
    layout = _layout(_model(rows), height=30, group_by="target")
    assert layout.shown == 500
    assert reads["n"] < 10 * 500, f"{reads['n']} reads of a row's target for 500 rows"
    assert any(line.strip() == "host-000 · 5" for line in _text(layout))


def test_the_alerts_tab_lists_the_active_alerts_and_is_not_drawn_without_them() -> None:
    """DT14: the Alerts tab holds the active set for a `broker.health` holder; a login that is not one (`alert_lines`
    None) has no tab at all. Targets is drawn between Sessions and Alerts, in the order the knob names them, and
    carries no count until the inventory has answered."""
    with_alerts = _text(_layout(_model([_row("a")], alert_lines=("▲ supervisor not answering · a",)), tab="alerts"))
    assert any("supervisor not answering" in line for line in with_alerts)
    head = "\n".join(_text(_layout(_model([_row("a")]))))
    assert "Alerts 1" in head and "Targets" in head
    assert head.index("Sessions") < head.index("Targets") < head.index("Alerts")
    # The widget drops the tab; the layout itself is told which tabs to draw, so it draws what it is given.
    without = _model([_row("a")], alert_lines=None, tabs=("sessions",))
    assert "Alerts" not in "\n".join(_text(_layout(without)))


# -- the view in the app --


def _two_sessions(serve, extra_peer: bool = False):
    serve_app, url = serve(InputRecordingApp())
    ids = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02")
    peer = named_sessions(url, serve_app, "fw-edge-01", user=_serve.PEER) if extra_peer else []
    return serve_app, url, ids, peer


def test_the_view_opens_by_the_leader_and_the_menu_with_the_rail_beside_it_and_esc_returns_to_the_same_session(
        serve, wait_until) -> None:
    """D7, §2.5: `^\\ F` and the Menu's Full fleet view open it; the rail stays on its left (mockup F1); `Esc`
    returns to the session it came from, not to another, and the keys go back to that session."""

    async def _check() -> None:
        _serve_app, url, (first, second), _ = _two_sessions(serve)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first, second)
            screen.close_sidebar()
            screen.show_front(screen.slot_for(first))
            await pilot.press(LEADER)
            await pilot.press("F")
            await wait_until(pilot, lambda: screen.view_open, "the full view to open from the leader")
            assert screen.rail.display, "the rail stays beside the view"
            await wait_until(pilot, lambda: screen.fleet_view.layout.rows, "the view to list the sessions")
            text = screen.fleet_view.text
            assert "Sessions you own or may join · 2" in text and "Esc back to 1 rtr-core-01" in text
            assert "keys → fleet view" in screen.status.text and "x End…" in screen.status.text
            await pilot.press("escape")
            await wait_until(pilot, lambda: not screen.view_open, "Esc to close the view")
            assert screen.front.session_id == first
            assert "keys →" not in screen.status.text, "the keys are the session's again"

            await screen.run_action_id("full_view")
            await wait_until(pilot, lambda: screen.view_open, "the Menu's action to open the view")
            await pilot.press("escape")
            await wait_until(pilot, lambda: not screen.view_open, "Esc to close the view")

    asyncio.run(_check())


def test_opening_the_view_from_the_sidebar_steps_the_sidebar_back_and_esc_brings_it_back(
        serve, wait_until) -> None:
    """§2.3: the sidebar's `full view ▸` opens the view in its place (the rail returns, as mockup F1 draws it), and
    `Esc` returns to the sidebar it came from with the keys where they were, not to a bare session."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(200, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            assert screen.sidebar_open
            screen._sidebar_command("full_view")
            await wait_until(pilot, lambda: screen.view_open, "the view to open")
            assert not screen.sidebar_open and screen.rail.display
            await pilot.press("escape")
            await wait_until(pilot, lambda: not screen.view_open, "the view to close")
            assert screen.sidebar_open and screen.sidebar.has_focus

    asyncio.run(_check())


def test_opening_the_view_asks_no_session_for_a_size_and_the_grid_keeps_the_sessions_own(serve, wait_until) -> None:
    """D44, terminal-size §4: nothing the TUI opens or closes asks. The view covers the canvas without touching it,
    so the session in front keeps its grid and no `resize` frame goes out while the view opens and closes."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01", cols=132, rows=40)
        app = TsbTuiApp(url)
        app.cfg.set_runtime("size.auto_request", True)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            before = list(resize_requests(serve_app))
            screen.open_fleet_view()
            await wait_until(pilot, lambda: screen.view_open, "the view")
            await pilot.pause(0.1)
            screen.close_fleet_view()
            await wait_until(pilot, lambda: not screen.view_open, "the view to close")
            await pilot.pause(0.1)
            assert (screen.backend.cols, screen.backend.rows) == (132, 40)
            assert list(resize_requests(serve_app)) == before

    asyncio.run(_check())


def test_the_view_lists_needs_you_first_and_mine_joinable_and_show_ended_change_only_what_is_shown(
        serve, wait_until) -> None:
    """D8 against the real poll: a session whose worker is unreachable is in Needs you; `mine│joinable` shows the peer's
    session only on `joinable`; an ended session appears only after "show ended"; the poll that feeds all of this is
    the sidebar's own (one filter state, DT15)."""

    async def _check() -> None:
        serve_app, url, (first, second), (theirs,) = _two_sessions(serve, extra_peer=True)
        serve_app.sessions[second]["state"] = "unreachable"  # the fixture's own way to a session that needs you
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first)
            screen.open_fleet_view()
            view = screen.fleet_view
            await wait_until(pilot, lambda: any(r[1] == second for r in view.layout.rows), "the poll's sessions")
            assert (GROUP_NEEDS, second) in view.layout.rows and (GROUP_TABLE, second) in view.layout.rows
            lines = view.text.splitlines()
            assert next(i for i, l in enumerate(lines) if "Needs you" in l) < next(
                i for i, l in enumerate(lines) if "Sessions you own" in l)
            assert any("infra-db-02" in l and "unreachable · input refused · not ended" in l for l in lines)
            assert not any(r[1] == theirs for r in view.layout.rows), "the peer's session is not mine"

            await pilot.click(FleetView, offset=(view.text.splitlines()[1].index("joinable") + 1, 1))
            await wait_until(pilot, lambda: any(r[1] == theirs for r in view.layout.rows), "joinable to list it")
            await pilot.click(FleetView, offset=(view.text.splitlines()[1].index("mine") + 1, 1))
            await wait_until(pilot, lambda: not any(r[1] == theirs for r in view.layout.rows), "mine to hide it")

            serve_app.sessions[first]["state"] = "killed"
            screen._refresh_fleet()
            await wait_until(pilot, lambda: not any(r[1] == first and r[0] == GROUP_TABLE for r in view.layout.rows),
                             "an ended session to leave the table")
            await pilot.press("e")
            await wait_until(pilot, lambda: (GROUP_TABLE, first) in view.layout.rows and "✕ killed" in view.text,
                             "show ended to bring it back")

    asyncio.run(_check())


def test_group_by_target_is_read_from_the_knob(serve, monkeypatch, wait_until) -> None:
    """`fleet_view.group_by`: a knob the operator owns must change the view, or nothing reads it (DT19)."""

    async def _check() -> None:
        monkeypatch.setenv("TSB_TUI_FLEET_VIEW_GROUP_BY", "target")
        _serve_app, url, _ids, _ = _two_sessions(serve)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_fleet_view()
            await wait_until(pilot, lambda: len(screen.fleet_view.layout.rows) == 2, "the sessions")
            assert any(line.strip() == "rtr-core-01 · 1" for line in screen.fleet_view.text.splitlines())

    asyncio.run(_check())


def test_a_stale_poll_reply_never_overwrites_a_newer_one(wait_until) -> None:
    """The case `test_sessions_refresh.py` guarded, moved to the view that lists the sessions now: two `GET /sessions`
    can be in flight, and the earlier one's reply can land after the later one's. Applying whichever lands last would
    show the operator a list that has lost a session that exists. The order is forced with the stub's gate (the first
    poll is held until the test releases it), never with a pause."""

    async def _check() -> None:
        release = threading.Event()
        stub = SlowChunkedApp(sessions_first_call_gate=release)
        with StubServer(stub) as url:
            admin = Client(url)
            admin.login("fixture-user", "fixture-password")
            admin.create_session("stub-target", "new", "xterm-256color", 80, 24)
            app = TsbTuiApp(url, target="stub-target")
            async with app.run_test(size=(120, 40)) as pilot:
                # The workspace mounts and starts its poll: the stub holds that first request, with one session.
                screen = await login(app, pilot)
                await wait_until(pilot, stub.sessions_first_call_started.is_set, "the first poll to be held")
                replies: list[tuple[int, int]] = []
                applied = screen._apply_poll

                def recording(generation: int, records: list[dict]) -> None:
                    replies.append((generation, len(records)))
                    applied(generation, records)

                screen._apply_poll = recording
                admin.create_session("stub-target", "new", "xterm-256color", 80, 24)
                screen.open_fleet_view()  # asks again: a newer poll, which answers at once with two sessions
                await wait_until(pilot, lambda: len(screen.fleet_view.layout.rows) == 2, "the newer reply")
                assert replies == [(2, 2)], "the newer poll is the first handled"

                release.set()
                await wait_until(pilot, lambda: len(replies) == 2, "the older reply to be handled")
                assert replies[1] == (1, 1), "the older reply carried the stale one-session list"
                assert len(screen.fleet_view.layout.rows) == 2, "a stale, slower poll overwrote the newer list"
                assert screen.fleet.applied_generation == 2

    asyncio.run(_check())


def test_the_alerts_tab_is_there_for_a_holder_and_absent_after_a_403(serve, wait_until) -> None:
    """DT14: a `broker.health` holder has the Alerts tab (Tab moves to it); a login the core answered 403 has no tab
    and no retry, so the view never offers a door the core has refused."""

    async def _holder() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_fleet_view()
            view = screen.fleet_view
            assert view.tabs == ("sessions", "targets", "alerts") and "Alerts" in view.text
            await pilot.press("tab")
            await wait_until(pilot, lambda: view.tab == "targets", "Tab to move to the Targets tab")
            await pilot.press("tab")
            await wait_until(pilot, lambda: view.tab == "alerts", "Tab to move on to the Alerts tab")
            assert "Active alerts" in view.text
            await pilot.press("tab")
            await wait_until(pilot, lambda: view.tab == "sessions", "Tab to come round")

    async def _not_a_holder() -> None:
        stub = SlowChunkedApp(alerts_status=403)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 30)) as pilot:
                screen = await fixture_login(app, pilot)
                await wait_until(pilot, lambda: stub.alerts_requests >= 1 and not app.alerts.allowed,
                                 "the 403 to be read")
                screen.open_fleet_view()
                view = screen.fleet_view
                await pilot.pause(0.1)
                assert view.tabs == ("sessions", "targets") and "Alerts" not in view.text
                await pilot.press("tab")
                await wait_until(pilot, lambda: view.tab == "targets", "Tab to move to the Targets tab")
                await pilot.press("tab")
                await wait_until(pilot, lambda: view.tab == "sessions", "Tab to come round without an Alerts tab")
                assert stub.alerts_requests == 1

    asyncio.run(_holder())
    asyncio.run(_not_a_holder())


def test_open_from_a_row_brings_that_session_forward_and_closes_the_view(serve, wait_until) -> None:
    """D8 row actions: `⏎ Open` (and a click on the row) attaches the session, past the gate, and the view steps
    aside so the operator sees it. The gate is not skipped for a session this run has not shown it for."""

    async def _check() -> None:
        _serve_app, url, (first, second), _ = _two_sessions(serve)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first)
            screen.open_fleet_view()
            view = screen.fleet_view
            await wait_until(pilot, lambda: any(r[1] == second for r in view.layout.rows), "the poll's sessions")
            await pilot.click(FleetView, offset=(8, _y(view, second)))
            await wait_until(pilot, lambda: not screen.view_open, "the view to close on a pick")
            await wait_until(pilot, lambda: app.screen is not screen, "the gate to ask first")
            assert screen.slot_for(second) is None, "nothing is attached before the gate is passed"

    asyncio.run(_check())
