"""The fleet sidebar (design §2.3, D42, D44): the rail widened in place, beside the terminal, with every session the
operator owns or may join in groups, a filter and a `mine│all` toggle, and details beside it on a wide window.

Why it matters (Rule 9): the sidebar is where an operator with a dozen sessions finds the one that needs them. A group
that loses a session, a toggle that shows what it should hide, or a sidebar that asks a session to resize when it opens
(it takes width from the canvas, which pans: D44) each cost the operator a session or someone else's screen. Servers:
the contract's in-process fixture server (DT7); sessions on it are created through its own client and renamed after
creation, because it only creates a few target names.
"""

from __future__ import annotations

import asyncio

import pytest
from input_server import InputRecordingApp, other_client
from stub_server import resize_requests
from tsbc import _serve
from workspace_support import (
    fixture_login,
    named_sessions,
    open_here,
    pick_in_sidebar,
)

from tsb_tui.app import TsbTuiApp
from tsb_tui.model.fleet import (
    GROUP_MINE_AND_JOINABLE,
    GROUP_MINE_HELD_BY_OTHERS,
    GROUP_NEEDS_YOU,
    GROUP_OPEN_HERE,
    GROUP_ORDER,
)
from tsb_tui.screens.fleet_view import GROUP_TABLE, FleetView
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.widgets.sidebar import (
    SidebarEntry,
    SidebarModel,
    sidebar_view,
)

LEADER = "ctrl+backslash"


def _y(screen, fragment: str) -> int:
    """The row of the sidebar whose text holds `fragment`."""
    return next(i for i, line in enumerate(screen.sidebar.view.lines) if fragment in line.text)


def _groups(screen) -> list[str]:
    """The group headings in the order the sidebar shows them."""
    return [line.text.split(" · ")[0].strip() for line in screen.sidebar.view.lines if " · " in line.text
            and line.session_id is None and line.text.strip()[0].isupper()]


def _entry(sid: str, group: str, **fields) -> SidebarEntry:
    base = {"session_id": sid, "group": group, "number": None, "who": "·", "target": sid, "id_label": None,
            "login": "·", "rec": False, "front": False, "reason": None, "countdown": None, "remaining": None,
            "out_age": None, "search": sid}
    base.update(fields)
    return SidebarEntry(**base)


def _model(*groups: tuple[str, list[SidebarEntry]], alerts: int | None = 1) -> SidebarModel:
    return SidebarModel(tuple((name, tuple(entries)) for name, entries in groups), "mine", alerts, "^\\ f")


def _view(model: SidebarModel, *, width: int = 31, height: int = 39, **overrides):
    values = {"selected": None, "filter_text": "", "editing": False, "wide": False, "show_details": False}
    values.update(overrides)
    return sidebar_view(model, width=width, height=height, **values)


# -- the layout, from the models --


def test_a_row_reads_who_types_the_login_that_types_and_rec_in_columns() -> None:
    """D42: "every row carries who types, the login that types when it is not you, the countdown or the last output this
    TUI saw, and REC". The columns line up across the groups below Needs you, so a login is in one place wherever its
    session is listed (mockup S1: ` 1● rtr-core-01  you       REC`)."""
    open_here = [_entry("a", GROUP_OPEN_HERE, number=1, who="●", target="rtr-core-01", login="you", rec=True,
                        front=True),
                 _entry("b", GROUP_OPEN_HERE, number=2, who="◆", target="infra-db-02", login="ci-agent",
                        out_age="2s")]
    held = [_entry("c", GROUP_MINE_HELD_BY_OTHERS, number=3, who="◆", target="lab-linux-04", login="minh",
                   rec=True)]
    lines = [line.text for line in _view(_model((GROUP_OPEN_HERE, open_here),
                                                (GROUP_MINE_HELD_BY_OTHERS, held))).lines]
    assert " 1● rtr-core-01  you       REC " in lines
    assert " 2◆ infra-db-02  ci-agent  •2s " in lines
    assert " 3◆ lab-linux-04 minh      REC " in lines
    assert len({len(line) for line in lines}) == 1, "every line fills the sidebar's width exactly"


def test_a_shared_target_shows_the_id_only_in_yours_and_joinable() -> None:
    """Mockup S1: `rtr-core-01` is in Open here without an id (its number tells it from the other) and in Yours and
    joinable with `s.9e014b`, because there a row has nothing else to tell two sessions on one target apart."""
    open_here = [_entry("a", GROUP_OPEN_HERE, number=1, who="●", target="rtr-core-01", login="you",
                        id_label="s.7f3a2c")]
    joinable = [_entry("b", GROUP_MINE_AND_JOINABLE, target="rtr-core-01", id_label="s.9e014b"),
                _entry("c", GROUP_MINE_AND_JOINABLE, target="core-sw-11", login="linh")]
    lines = [line.text for line in _view(_model((GROUP_OPEN_HERE, open_here),
                                                (GROUP_MINE_AND_JOINABLE, joinable))).lines]
    assert not any("s.7f3a2c" in line for line in lines)
    assert any(line.startswith("    rtr-core-01 s.9e014b  ·") for line in lines), lines
    assert any(line.startswith("    core-sw-11") and "linh" in line for line in lines)
    long_id = [_entry("d", GROUP_MINE_AND_JOINABLE, target="lab-linux-04", id_label="s.9e014b12"),
               _entry("e", GROUP_MINE_AND_JOINABLE, target="rtr-core-01", id_label="s.9e014b34")]
    narrow = [line.text for line in _view(_model((GROUP_OPEN_HERE, open_here),
                                                 (GROUP_MINE_AND_JOINABLE, [*joinable, *long_id])), width=29).lines]
    assert len({len(line) for line in narrow}) == 1, "at a phone's width an id row still fills the line exactly"


def test_a_login_is_cut_only_when_the_row_is_full() -> None:
    """Principle 4: two logins that share a prefix must stay apart on the row. At 42 columns a 14-character login has the
    room (the target, the clock and the gaps leave 15 cells), so it is shown whole; where the row is full (a laptop's 31
    cells) it is cut with an ellipsis as before, and a login longer than any row is cut at the row's edge, never past it."""
    open_here = [_entry("a", GROUP_OPEN_HERE, number=2, who="◆", target="infra-db-02", login="fixture-user-1",
                        out_age="41s"),
                 _entry("b", GROUP_OPEN_HERE, number=3, who="◆", target="k8s-node-07", login="fixture-user-2",
                        out_age="2s")]
    model = _model((GROUP_OPEN_HERE, open_here))
    wide = [line.text for line in _view(model, width=41, wide=True).lines]
    assert any("fixture-user-1" in text and "out 41s" in text for text in wide), wide  # fails on `fixture…`
    assert any("fixture-user-2" in text and "out 2s" in text for text in wide), wide
    assert len({len(text) for text in wide}) == 1

    narrow = [line.text for line in _view(model, width=31).lines]
    assert any("fixture-…" in text and "•41s" in text for text in narrow), "a full row still cuts the login"
    assert len({len(text) for text in narrow}) == 1

    # Each row's login takes that row's own free width: a row with a countdown and REC (the layout of the real S1 shot)
    # shows a 12-character login whole, beside a row whose clock is wider and whose login is therefore cut.
    mixed = [_entry("d", GROUP_OPEN_HERE, number=2, who="◆", target="infra-db-02", login="fixture-peer",
                    remaining="4d 0h", rec=True),
             _entry("e", GROUP_OPEN_HERE, number=3, who="◆", target="k8s-node-07", login="fixture-peer",
                    out_age="41s", rec=True)]
    rows = [line.text for line in _view(_model((GROUP_OPEN_HERE, mixed)), width=41, wide=True).lines]
    assert any("infra-db-02 fixture-peer 4d 0h   REC" in text for text in rows), rows  # fails today: `fixture…`
    assert any("k8s-node-07 fixture-p… out 41s   REC" in text for text in rows), rows
    assert len({len(text) for text in rows}) == 1

    absurd = [_entry("c", GROUP_OPEN_HERE, number=1, who="◆", target="infra-db-02", login="x" * 30, out_age="41s")]
    cut = [line.text for line in _view(_model((GROUP_OPEN_HERE, absurd)), width=41, wide=True).lines]
    assert any("…" in text and "out 41s" in text for text in cut), "the clock keeps its place when the login is cut"
    assert len({len(text) for text in cut}) == 1


def test_the_long_tail_gives_way_to_full_view_when_the_window_is_not_tall_enough() -> None:
    """D42/S1: "the long tail ending in `full view ▸`": the joinable group shows as many rows as fit and then
    `┆ N more · full view ▸`, and N is exactly the rows left out."""
    joinable = [_entry(f"j{n}", GROUP_MINE_AND_JOINABLE, target=f"dist-sw-{n:02d}") for n in range(14)]
    view = _view(_model((GROUP_MINE_AND_JOINABLE, joinable)), height=14)
    texts = [line.text for line in view.lines]
    assert len(texts) == 14
    shown = [row for row in view.rows]
    assert len(shown) + view.hidden == 14 and view.hidden > 0
    assert any(f"┆ {view.hidden} more · full view ▸" in text for text in texts)
    roomy = _view(_model((GROUP_MINE_AND_JOINABLE, joinable)), height=39)
    assert roomy.hidden == 0 and not any("more" in line.text for line in roomy.lines)


def test_needs_you_names_the_clock_and_the_wide_sidebar_spells_it() -> None:
    """Mockups S1 and S3: a laptop row says `▲ 4:12`, a wide one `not answering 4:12` and `ends in 2d 23h`, and an
    unreachable one `unreach.` / `unreachable`; the same fact, in the room the sidebar has."""
    needs = [_entry("a", GROUP_NEEDS_YOU, who="▲", target="sw-agg-02", login="linh", reason="link_timeout",
                    countdown="4:12"),
             _entry("b", GROUP_NEEDS_YOU, who="▲", target="con-srv-07", reason="idle", countdown="2d 23h"),
             _entry("c", GROUP_NEEDS_YOU, who="◌", target="fw-edge-01", reason="unreachable")]
    narrow = [line.text for line in _view(_model((GROUP_NEEDS_YOU, needs))).lines]
    assert any("sw-agg-02" in t and "▲ 4:12" in t for t in narrow)
    assert any("fw-edge-01" in t and "unreach." in t for t in narrow)
    wide = [line.text for line in _view(_model((GROUP_NEEDS_YOU, needs)), width=41, wide=True).lines]
    assert any("sw-agg-02" in t and "not answering 4:12" in t for t in wide)
    assert any("con-srv-07" in t and "ends in 2d 23h" in t for t in wide)
    assert any("fw-edge-01" in t and "unreachable" in t for t in wide)


def test_a_warning_in_open_here_reads_the_short_clock_and_the_buttons_fill_their_line() -> None:
    """Mockups S1 and S3: the long phrase is for Needs you; in Open here a session in a warning window reads `4:12`
    on a laptop and `▲ 4:12` on a wide window, beside REC. Every line, the details' buttons included, fills the width so
    the hairline stays in one column."""
    entries = [_entry("a", GROUP_OPEN_HERE, number=1, who="▲", target="sw-agg-02", login="linh", rec=True,
                      reason="link_timeout", countdown="4:12")]
    model = SidebarModel(((GROUP_OPEN_HERE, tuple(entries)),), "mine", 0, "^\\ f",
                         detail_for=lambda sid: ("1 sw-agg-02",), buttons_for=lambda sid: ("t Take back", "⏎ Open"))
    narrow = [line.text for line in _view(model).lines]
    assert any(line.startswith(" 1▲ sw-agg-02 linh") and line.rstrip().endswith("4:12") for line in narrow), narrow
    wide_view = _view(model, width=41, wide=True, selected=(GROUP_OPEN_HERE, "a"), show_details=True)
    wide = [line.text for line in wide_view.lines]
    assert any("▲ 4:12   REC" in line for line in wide), wide
    assert len({len(line) for line in wide}) == 1


def test_details_show_only_when_asked_for_and_only_for_the_selected_session() -> None:
    """D42 / S3: "details of the selected session from `details_from` columns". The view builds them for the one
    selected row and only when told the window is wide enough."""
    entries = [_entry("a", GROUP_OPEN_HERE, number=1, who="●", target="rtr-core-01", login="you")]
    model = SidebarModel(((GROUP_OPEN_HERE, tuple(entries)),), "mine", 0, "^\\ f",
                         detail_for=lambda sid: (f"1 {sid} · s.7f3a2c · yours", "you type"),
                         buttons_for=lambda sid: ("t Take back", "⏎ Open"))
    key = (GROUP_OPEN_HERE, "a")
    without = _view(model, selected=key, show_details=False)
    with_details = _view(model, selected=key, show_details=True)
    assert not any("s.7f3a2c" in line.text for line in without.lines)
    assert any("1 a · s.7f3a2c · yours" in line.text for line in with_details.lines)
    assert any("[t Take back] [⏎ Open]" in line.text for line in with_details.lines)
    assert not any("s.7f3a2c" in line.text for line in _view(model, selected=None, show_details=True).lines), (
        "nothing selected, nothing to detail")


def test_the_filter_narrows_every_group_and_a_group_with_no_match_says_zero() -> None:
    """D42: "a filter (`/`)". It matches the target, the id, the login and the owner across every group."""
    open_here = [_entry("a", GROUP_OPEN_HERE, number=1, who="●", target="rtr-core-01", search="rtr-core-01 you"),
                 _entry("b", GROUP_OPEN_HERE, number=2, who="●", target="infra-db-02", search="infra-db-02 you")]
    view = _view(_model((GROUP_OPEN_HERE, open_here)), filter_text="infra")
    texts = "\n".join(line.text for line in view.lines)
    assert "infra-db-02" in texts and "rtr-core-01" not in texts
    assert "Open here · 1" in texts
    assert [row[1] for row in view.rows] == ["b"]


# -- the live sidebar --


def test_the_panel_lists_only_what_is_open_here_and_the_full_view_holds_the_groups(serve, wait_until) -> None:
    """D42 on the fixture server, after P2a: the side panel's Sessions section lists only the session open here; the
    groups (needs you, yours, someone else types, yours and joinable) are the full view's Sessions tab, where the one
    that needs the operator (its worker unreachable) leads. A session owned by someone else is not listed."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        mine, held, idle, broken = named_sessions(url, serve_app, "rtr-core-01", "k8s-node-07", "dist-sw-04",
                                                  "fw-edge-01")
        (theirs,) = named_sessions(url, serve_app, "lab-linux-04", user=_serve.PEER)
        serve_app.sessions[broken]["state"] = "unreachable"
        with other_client(url, _serve.PEER, held):
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await fixture_login(app, pilot)
                await open_here(app, pilot, wait_until, mine)
                screen._refresh_fleet()
                await wait_until(pilot, lambda: broken in screen.fleet.records, "the poll to list the fleet")
                screen._sync_fleet_widgets()
                assert screen.sidebar.view.rows == ((GROUP_OPEN_HERE, mine),)
                screen.open_fleet_view()
                view = screen.fleet_view
                await wait_until(pilot, lambda: any(r[1] == broken for r in view.layout.rows), "the view's rows")
                rows = view.layout.rows
                assert rows[0][0] == "needs_you" and rows[0][1] == broken, "Needs you leads"
                assert {sid for group, sid in rows if group == GROUP_TABLE} >= {mine, held, idle, broken}
                assert not any(sid == theirs for _, sid in rows), "another login's session is not mine"

    asyncio.run(_check())



def test_mine_all_is_the_full_views_and_the_filter_narrows_the_targets(serve, wait_until) -> None:
    """D42 / DT15 after P2a: `/` and typed text narrow the Targets section, its count in the title; Enter keeps the
    text, Esc clears it. `mine│joinable` is the full view's switch, a view over what the core returned: it asks for
    nothing."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        _mine, other = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02")
        (theirs,) = named_sessions(url, serve_app, "lab-linux-04", user=_serve.PEER)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(screen.sidebar.model.targets) > 1 and screen._sidebar_has_keys(),
                             "the targets")
            total = len(screen.sidebar.model.targets)
            await pilot.press("slash")
            await pilot.press(*"telnet")
            await wait_until(pilot, lambda: len(screen.sidebar.visible_targets()) == 1, "the filter to narrow")
            assert screen.sidebar.render_line(0).text.startswith(f"┌─ ▦ Targets · 1/{total}")
            assert "/ telnet▏" in screen.sidebar.render_line(1).text
            await pilot.press("enter")
            await wait_until(pilot, lambda: not screen.sidebar.editing, "Enter to leave the filter")
            assert len(screen.sidebar.visible_targets()) == 1, "Enter keeps the text"
            await pilot.press("slash", "escape")
            await wait_until(pilot, lambda: len(screen.sidebar.visible_targets()) == total, "Esc to clear the filter")

            screen.open_fleet_view()
            view = screen.fleet_view
            await wait_until(pilot, lambda: any(r[1] == other for r in view.layout.rows), "the poll to list my sessions")
            await pilot.pause(0.3)
            requests_before = len([r for r in serve_app.requests if r[:2] == ("GET", "/sessions")])
            lines = view.text.splitlines()
            joinable_line = next(i for i, line in enumerate(lines) if "joinable" in line)
            await pilot.click(FleetView, offset=(lines[joinable_line].index("joinable") + 1, joinable_line))
            await wait_until(pilot, lambda: any(r[1] == theirs for r in view.layout.rows), "joinable to list the peer's")
            await pilot.click(FleetView, offset=(lines[joinable_line].index("mine") + 1, joinable_line))
            await wait_until(pilot, lambda: not any(r[1] == theirs for r in view.layout.rows), "mine to hide it again")
            assert len([r for r in serve_app.requests if r[:2] == ("GET", "/sessions")]) == requests_before, (
                "the toggle is a view: it asked the core for nothing")

    asyncio.run(_check())


def test_opening_the_sidebar_never_asks_for_a_size_and_the_canvas_pans(serve, wait_until) -> None:
    """D44, terminal-size §4 ("nothing the TUI opens or closes asks"): at 120×40 a session of 116×39 opens with the
    rail collapsed and fits; opening the sidebar takes 28 more columns from the canvas, so it shows `cols 1-88 of
    116 ▸` and pans, the session keeps its size, and no `resize` frame is sent, even with auto size on and this client
    the typist. Closing it gives the width back."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01", cols=104, rows=37)  # the window less the rail, frame and row
        app = TsbTuiApp(url)
        app.cfg.set_runtime("size.auto_request", True)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.close_sidebar()
            await wait_until(pilot, lambda: screen.canvas.size.width == 104, "the canvas to take the whole width")
            # Keys-only shows the pan range in every state (HEAD hid it behind the recording bar).
            # After the canvas settles at the whole width, refresh so the row reflects it: a 104-wide canvas of a 104-column
            # session needs no pan, so the row says no range.
            screen.refresh_status()
            assert screen.status.text.count("cols 1-") == 0 and screen.pump.state.holds_keyboard
            assert (screen.canvas.backend.cols, screen.canvas.backend.rows) == (104, 37)

            await pilot.click(screen.query_one("Rail"), offset=(1, 1))
            await wait_until(pilot, lambda: screen.sidebar_open and screen.canvas.size.width == 72, "the sidebar to open")
            await wait_until(pilot, lambda: "cols 1-72 of 104 ▸" in screen.status.text, "the pan range")
            assert (screen.canvas.backend.cols, screen.canvas.backend.rows) == (104, 37)
            screen.close_sidebar()
            await wait_until(pilot, lambda: screen.canvas.size.width == 104, "the width to come back")
            await pilot.pause(0.5)  # longer than the debounce: a request would be on its way by now
            assert resize_requests(serve_app) == [], "opening or closing the sidebar sent a size request"

    asyncio.run(_check())


@pytest.mark.parametrize("width, expected", [(65, 30), (120, 32), (200, 42)])
def test_the_sidebar_is_30_32_or_42_columns_by_the_window_and_has_no_detail_block(
        serve, wait_until, width, expected) -> None:
    """D42, design §2.6: 30 on a phone (a compact window), 32 on a laptop, 42 from `sidebar.wide_from` columns. The
    old detail block left the panel (P2a): the session's facts are the frame title's and Who's here, at every size."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(width, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.open_sidebar()
            await wait_until(pilot, lambda: screen.sidebar.region.width == expected, f"a {expected}-column sidebar")
            text = screen.sidebar.text
            assert "attached:" not in text and "full view ▸" not in text
            assert screen.sidebar.view.detail_start is None

    asyncio.run(_check())



@pytest.mark.parametrize("mode, window, closes", [
    ("always", 120, False), ("never", 65, False), ("phone", 120, False), ("phone", 65, False)])
def test_picking_a_session_keeps_sidebar_open_regardless_of_old_close_setting(
        serve, monkeypatch, wait_until, mode, window, closes) -> None:
    """Picking a session preserves the sidebar and gives the terminal the keys."""
    monkeypatch.setenv("TSB_TUI_SIDEBAR_CLOSE_ON_PICK", mode)

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        first, second = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02")
        app = TsbTuiApp(url)
        async with app.run_test(size=(window, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first)
            app.gated_sessions.add(second)  # not about the gate: the pick goes straight through
            await pick_in_sidebar(app, pilot, wait_until, second)
            await wait_until(pilot, lambda: screen.front is not None and screen.front.session_id == second,
                             "the pick to bring the session forward")
            assert screen.sidebar_open is (not closes)
            assert not screen._sidebar_has_keys(), "the keys are the session's after a pick"

    asyncio.run(_check())


def test_the_sidebars_keys_are_its_own_until_it_closes_and_the_leader_still_works(serve, wait_until) -> None:
    """D14, D16: while the sidebar has the keys `j`/`k`/arrows move, Enter opens, Esc closes, and none of them reaches
    the session (a `j` typed into a router is a real keystroke); `^\\ f` still closes it, because the leader goes on to
    the screen; `^\\ s` gives the keys back to an open sidebar."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        first, second = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02")
        app = TsbTuiApp(url)
        async with app.run_test(size=(130, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first, second)  # the panel lists the sessions open here
            screen._refresh_fleet()
            screen.open_sidebar()
            await wait_until(pilot, lambda: len(screen.sidebar.view.rows) == 2 and screen._sidebar_has_keys(),
                             "the sidebar to hold the keys")
            client_id = screen.pump.state.client_id
            before = screen.sidebar.selected
            await pilot.press("j")
            await wait_until(pilot, lambda: screen.sidebar.selected != before, "j to move the selection")
            await pilot.press("k")
            await wait_until(pilot, lambda: screen.sidebar.selected == before, "k to move it back")
            await pilot.press("down")
            await wait_until(pilot, lambda: screen.sidebar.selected != before, "down to move it")
            await pilot.press("up")
            await wait_until(pilot, lambda: screen.sidebar.selected == before, "up to move it back")
            assert serve_app.received(client_id) == [], "a sidebar key reached the session"
            await pilot.press("escape")
            await wait_until(pilot, lambda: not screen.sidebar_open, "Esc to close the sidebar")
            await pilot.press(LEADER, "f")
            await wait_until(pilot, lambda: screen.sidebar_open, "^\\ f to open it again")
            await pilot.press(LEADER, "f")
            await wait_until(pilot, lambda: not screen.sidebar_open, "^\\ f to close it")
            screen.open_sidebar(focus=False)
            await pilot.press(LEADER, "s")
            await wait_until(pilot, lambda: screen._sidebar_has_keys(), "^\\ s to give the keys to the sidebar")
            assert serve_app.received(client_id) == []
            assert "keys → fleet" in screen.status.text and "Esc Close" in screen.status.text

    asyncio.run(_check())


def test_ctrl_q_quits_from_the_sidebar_that_has_the_keys_right_after_login(serve, wait_until) -> None:
    """D38: the app's own quit key still quits while the sidebar has the keys, which it does the moment login lands: an
    operator who has not opened anything must be able to leave, and sessions keep running."""

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            assert screen._sidebar_has_keys()
            await pilot.press("ctrl+q")
            await wait_until(pilot, lambda: app._exit, "the app to quit")

    asyncio.run(_check())


def test_a_click_on_the_session_gives_the_keys_back_to_it_from_an_open_sidebar(serve, wait_until) -> None:
    """D14: the sidebar has the keys while it is focused; an operator who clicks the session beside it is turning to it, so
    the next key is the session's (a typed key reaches the server) and the status row says so."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(130, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.open_sidebar()
            await wait_until(pilot, lambda: screen._sidebar_has_keys() and screen.canvas.size.width == 82, "the keys")
            await pilot.click(screen.canvas, offset=(10, 5))
            await wait_until(pilot, lambda: not screen._sidebar_has_keys(), "the keys to go back to the session")
            await pilot.press("x")
            await wait_until(pilot, lambda: serve_app.received(screen.pump.state.client_id) == [b"x"],
                             "the typed key to reach the session")
            assert screen.sidebar_open and "keys →" not in screen.status.text

    asyncio.run(_check())


def test_the_panels_old_entries_are_the_rails_and_the_keys_now(serve, wait_until) -> None:
    """D12/D42 after P2a: `full view ▸`, `+ New session`, `! n alerts` and `◂ close` left the panel. The full view is
    `F` (its hint in the keys row), New session the rail's `+ New` row, the alerts the rail's Alerts row, and closing
    the panel is Esc (its hint `Esc Close` in the keys row while it has focus)."""
    from workspace_support import rail_row

    from tsb_tui.widgets.rail import ACT_ALERTS, ACT_NEW, Rail

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(200, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.sidebar.region.width == 42 and screen._sidebar_has_keys(),
                             "the wide sidebar with the keys")
            text = screen.status.text
            assert "F Full view" in text and "Esc Close" in text, text
            assert not any(w in screen.sidebar.text for w in ("◂ close", "+ New session", "alert"))
            await pilot.press("F")
            await wait_until(pilot, lambda: screen.view_open, "the full view")
            await pilot.press("escape")
            await wait_until(pilot, lambda: not screen.view_open, "back to the workspace")

            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_NEW)))
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "New session")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "back to the workspace")

            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_ALERTS)))
            await wait_until(pilot, lambda: screen.view_open and screen.fleet_view.tab == "alerts", "the alerts")
            await pilot.press("escape")
            await wait_until(pilot, lambda: not screen.view_open, "back to the workspace")

            screen.open_sidebar()
            await wait_until(pilot, lambda: screen._sidebar_has_keys(), "the keys")
            await pilot.press("escape")
            await wait_until(pilot, lambda: not screen.sidebar_open, "Esc to close the panel")

    asyncio.run(_check())


def test_the_fleet_is_polled_every_refresh_interval_and_a_manual_interval_polls_once(
        serve, monkeypatch, wait_until) -> None:
    """D10 / `fleet_view.refresh_s`: the sidebar and the rail poll `GET /sessions` while on screen; 0 is "manual". The
    poll is a read (session-list step 3), so it sends nothing to any session."""

    def polls(serve_app) -> int:
        return len([r for r in serve_app.requests if r[:2] == ("GET", "/sessions")])

    async def _timed() -> None:
        monkeypatch.setenv("TSB_TUI_FLEET_VIEW_REFRESH_S", "1")
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            await wait_until(pilot, lambda: polls(serve_app) >= 3, "three polls", timeout_s=8)

    async def _manual() -> None:
        monkeypatch.setenv("TSB_TUI_FLEET_VIEW_REFRESH_S", "0")
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: polls(serve_app) == 1, "the one poll")
            await pilot.pause(1.5)
            assert polls(serve_app) == 1, "a manual interval polled again by itself"
            screen.close_sidebar()
            screen.open_sidebar()  # opening the sidebar asks for a fresh read
            await wait_until(pilot, lambda: polls(serve_app) == 2, "the read asked for")

    asyncio.run(_timed())
    asyncio.run(_manual())


def test_a_poll_never_attaches_a_session_another_login_holds(serve, wait_until) -> None:
    """`watch.held = "manual"` (D47): a session of yours that an agent types in is listed, with its chip, and the TUI
    does not attach to it by itself, because an attach costs a client and a redraw wiggle on the agent's screen."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (held,) = named_sessions(url, serve_app, "k8s-node-07")
        with other_client(url, _serve.PEER, held):
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await fixture_login(app, pilot)
                screen._refresh_fleet()
                screen.open_fleet_view()  # the panel lists only what is open here; the held session is the view's
                await wait_until(pilot, lambda: any(row[1] == held for row in screen.fleet_view.layout.rows),
                                 "the held session to be listed")
                screen.close_fleet_view()
                screen.close_sidebar()
                await wait_until(pilot, lambda: any(row.arg == held for row in screen.rail.rows), "its chip")
                assert screen.slots == [] and len(serve_app.clients[held]) == 1, "the TUI attached by itself"

    asyncio.run(_check())


# -- the chrome around it: login, the status row's hints, the chrome and glyph knobs --


@pytest.mark.parametrize("knob, opens", [("true", True), ("false", False)])
def test_login_opens_the_sidebar_when_no_session_is_open_per_the_knob(serve, monkeypatch, wait_until, knob, opens) -> None:
    """Design §2.5 and `sidebar.open_when_no_session`: after login with nothing open the next screen is the workspace with
    the sidebar open, with the keys (G1); an operator who turned the knob off gets the collapsed rail."""
    monkeypatch.setenv("TSB_TUI_SIDEBAR_OPEN_WHEN_NO_SESSION", knob)

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            assert screen.sidebar_open is opens and screen._sidebar_has_keys() is opens
            await pilot.pause(0.1)
            assert ("keys → fleet" in screen.status.text) is opens

    asyncio.run(_check())


def test_the_status_row_offers_the_sidebars_keys_as_clicks(serve, wait_until) -> None:
    """D14, design §2.3: while the sidebar has the keys the row says `keys → fleet` and offers `⏎ Open`, `t Take back`,
    `w Who's here`, `/ Filter`, `Esc Close`, each a click (D11). A click on `Esc Close` closes it, and the row goes back
    to the session's own."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(130, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.open_sidebar()
            await wait_until(pilot, lambda: screen._sidebar_has_keys(), "the keys")
            text = screen.status.text
            for hint in ("⏎ Open", "t Take back", "w Who's here", "/ Filter", "Esc Close"):
                assert hint in text, text
            await pilot.click("StatusRow", offset=(text.index("Esc Close") + 1, 0))
            await wait_until(pilot, lambda: not screen.sidebar_open, "Esc Close to close the sidebar")
            assert "keys →" not in screen.status.text

    asyncio.run(_check())


def test_the_pan_note_follows_the_sidebars_name_in_the_row(serve, wait_until) -> None:
    """Mockup S1: `keys → fleet · rtr-core-01 shows cols 1-88 of 116 ▸`: while the sidebar has the keys the row says
    which part of the session in front the narrowed canvas shows."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01", cols=116, rows=39)
        app = TsbTuiApp(url)
        async with app.run_test(size=(130, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.open_sidebar()
            await wait_until(pilot, lambda: "keys → fleet · rtr-core-01 shows cols 1-82 of 116 ▸" in screen.status.text,
                             "the pan note")

    asyncio.run(_check())


@pytest.mark.parametrize("chrome, window, sidebar_width, compact_pill", [
    ("compact", 120, 30, True), ("full", 65, 32, False), ("auto", 65, 30, True), ("auto", 120, 32, False)])
def test_view_chrome_decides_compact_for_the_sidebar_width_and_the_pill(
        serve, monkeypatch, wait_until, chrome, window, sidebar_width, compact_pill) -> None:
    """`view.chrome` (the knob file): "auto" is compact under 80 columns, "full" and "compact" force one or the other; the
    phone profile sets compact. Compact is what picks the sidebar's phone width (30) and the pill without its inner
    spaces (design §2.6, T2)."""
    monkeypatch.setenv("TSB_TUI_VIEW_CHROME", chrome)

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(window, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.open_sidebar()
            await wait_until(pilot, lambda: screen.sidebar.region.width == sidebar_width, f"{sidebar_width} columns")
            screen.close_sidebar()
            # Keys-only means keys only in every state, so the who-types pill is no longer on the
            # keys row (the recording warning is the frame title's "recorded"). The fact moved to the frame's title.
            title = str(screen.query_one("#canvases").border_title)
            assert "you type" in title, f"the who-types fact is the frame title, not the keys row: {title!r}"

    asyncio.run(_check())


def test_the_ascii_glyph_set_reaches_the_sidebar_and_the_status_row(serve, monkeypatch, wait_until) -> None:
    """`glyphs.set = ascii` (D37) is the whole chrome's, not the rail's alone: the sidebar's header and rows and the status
    row's pill and hints read in the knob file's ASCII stand-ins, with the cells they had."""
    monkeypatch.setenv("TSB_TUI_GLYPHS_SET", "ascii")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.open_sidebar()
            await wait_until(pilot, lambda: len(screen.sidebar.view.rows) == 1, "the row")
            lines = screen.sidebar.text.splitlines()
            assert lines[0].startswith("┌─ # Targets ."), lines[0]  # the section's mark and the dot in ASCII
            assert any(line.startswith(("┌─ > Sessions", "┏━ > Sessions")) for line in lines), lines
            assert any(">1* rtr-core-01" in line for line in lines), lines
            assert not any(glyph in screen.sidebar.text for glyph in "≡▦▸◂●◆▲◌✕◐•┄")
            screen.close_sidebar()
            await wait_until(pilot, lambda: "F1 Menu" in screen.status.text and "# Fleet" in screen.status.text,
                             "the row's own entries in ASCII")
            assert not any(glyph in screen.status.text for glyph in "≡▦▸●◐")

    asyncio.run(_check())


def test_the_retired_sidebar_groups_knob_still_loads_and_changes_nothing(serve, monkeypatch, wait_until) -> None:
    """`sidebar.groups` is retired (the panel has one section): an old config naming it loads, and the groups the
    full view and the rail compute are the same whatever it says."""
    monkeypatch.setenv("TSB_TUI_SIDEBAR_GROUPS", "open_here")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        mine, other = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, mine)
            screen._refresh_fleet()
            await wait_until(pilot, lambda: other in screen.fleet.records, "the poll")
            assert screen.fleet.group_names == GROUP_ORDER
            assert screen.sidebar.view.rows == ((GROUP_OPEN_HERE, mine),)

    asyncio.run(_check())

def test_the_poll_tells_the_row_that_the_worker_is_unreachable_and_that_it_is_back(serve, wait_until) -> None:
    """Phase 6 left it for the poll: no frame of an attachment says the core cannot reach the worker, only the record does.
    The session open here shows `◌ unreachable` in the status row and `◌` on its chip once a poll says so, and goes back to
    `live` when a later poll does."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.close_sidebar()
            serve_app.sessions[sid]["state"] = "unreachable"
            screen._refresh_fleet()
            await wait_until(pilot, lambda: "◌ unreachable" in screen.status.text, "the row to say unreachable")
            await wait_until(pilot, lambda: any(row.text.startswith("1◌") for row in screen.rail.rows), "the chip")
            serve_app.sessions[sid]["state"] = "live"
            screen._refresh_fleet()
            await wait_until(pilot, lambda: "◌ unreachable" not in screen.status.text and "live" in str(screen.query_one("#canvases").border_title),
                             "the title to read live again")

    asyncio.run(_check())


def test_a_selected_unreachable_session_says_so_and_never_shows_an_idle_countdown(serve, wait_until) -> None:
    """Mockup F1: a session whose worker the core cannot reach refuses input and is not ended. Its details read
    `unreachable · input refused · not ended`; a countdown (`ends in … if nothing happens`) or `nobody types` there
    would tell the operator it is a healthy session waiting out its idle time, and they would not go and look."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(200, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.open_fleet_view()  # the details left the panel: they are the full view's
            view = screen.fleet_view
            await wait_until(pilot, lambda: any(r[1] == sid for r in view.layout.rows), "the session to be listed")
            assert "unreachable · input refused · not ended" not in view.text
            serve_app.sessions[sid]["state"] = "unreachable"
            screen._refresh_fleet()
            await wait_until(pilot, lambda: "unreachable · input refused · not ended" in view.text,
                             "the view to say the session is unreachable")
            details = screen._details_for(sid)
            assert not any(line.startswith("ends in") for line in details), details

    asyncio.run(_check())


def test_the_sidebar_follows_the_window_when_it_changes_size(serve, wait_until) -> None:
    """D42: the sidebar is 32 columns on a laptop and 42 from 160: a window that grows while it is open gives it the wider
    width, and the canvas takes what is left. The window change is the operator's, so it is the one thing that may ask a
    session for a size, and here auto size is off, so nothing is asked."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.open_sidebar()
            await wait_until(pilot, lambda: screen.sidebar.region.width == 32, "the laptop sidebar")
            await pilot.resize_terminal(200, 40)
            await wait_until(pilot, lambda: screen.sidebar.region.width == 42, "the wide sidebar")
            await wait_until(pilot, lambda: screen.canvas.region.width == 142, "the canvas to take the rest")
            assert resize_requests(serve_app) == []

    asyncio.run(_check())


def test_rail_width_and_the_start_filter_are_the_knobs(serve, monkeypatch, wait_until) -> None:
    """`rail.width` (hairline included) and `sidebar.start_filter` (the toggle's start position, which the drive profile
    sets to `all`, DT15): the rail is as wide as the knob says and the toggle starts where it says."""
    monkeypatch.setenv("TSB_TUI_RAIL_WIDTH", "6")
    monkeypatch.setenv("TSB_TUI_SIDEBAR_START_FILTER", "all")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        named_sessions(url, serve_app, "lab-linux-04", user=_serve.PEER)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            assert screen.fleet.filter_mode == "all"
            await wait_until(pilot, lambda: len(screen.fleet.records) == 1, "the peer's session to be listed")
            screen.close_sidebar()
            await wait_until(pilot, lambda: screen.rail.region.width == 6, "a six-column rail")
            assert screen.fit_size()[0] == 110, "the window minus the rail the knob made and the frame"
            # A resize re-lays the chrome, building the rows for the responsive width, then the
            # `rail.width` knob set the width after them without rebuilding. The rows are built for the width the knob
            # uses: every row is `rail.width - 1` cells wide.
            await pilot.resize_terminal(130, 40)
            await pilot.pause()
            assert all(len(row.text) == 5 for row in screen.rail.rows), \
                [row.text for row in screen.rail.rows]

    asyncio.run(_check())


def test_the_status_rows_range_follows_the_canvas_when_it_widens_without_moving(serve, wait_until) -> None:
    """D4, design §2.3. The row says which columns the window shows (`cols 1-88 of 132 ▸`). Closing the sidebar widens the
    canvas from 35 to 61 columns and the window stays at column 1, so the view's x and y do not move: the range's end
    column is the only thing that changed, and the row must still say it (`cols 1-116`). A row that kept the old end column
    told the person the window showed less of the session than it did, for as long as nothing else redrew it."""
    import asyncio

    from input_server import InputRecordingApp
    from stub_server import create_owner_session

    async def check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            from input_server import attached

            session_id = create_owner_session(app, "fixture-target", 132, 40)
            screen = await attached(pilot, app, session_id, wait_until)
            screen.open_sidebar(focus=False)  # beside the terminal, the keys still the session's: the row keeps its range
            await wait_until(pilot, lambda: screen.sidebar_open and screen.canvas.size.width == 72
                             and "cols 1-72 of 132 ▸" in screen.status.text, "the narrow range in the row")
            screen.close_sidebar()
            await wait_until(pilot, lambda: not screen.sidebar_open and screen.canvas.size.width == 104,
                             "the canvas to widen")
            assert screen.canvas.view_x == 0, "the window moved, so this is not the case under test"
            label = screen.canvas.view_range.label
            assert label == "cols 1-104 of 132 ▸", label
            await wait_until(pilot, lambda: label in screen.status.text,
                             f"the row to say {label!r}, it says {screen.status.text!r}")

    asyncio.run(check())


def test_keyboard_moves_over_the_open_rows_tab_keeps_the_selection_and_n_opens_new_session(serve, wait_until) -> None:
    """The panel's rows are the sessions open here: the arrows move over them and stop at the ends (the New session
    row left the panel; its keys are `n` here and the rail's `+ New`); Tab moves focus on and keeps the selection."""
    async def check():
        serve_app, url = serve(InputRecordingApp())
        first, second = named_sessions(url, serve_app, "router", "switch")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first, second)
            screen.open_sidebar()
            await wait_until(pilot, lambda: len(screen.sidebar.view.rows) == 2 and screen.sidebar.has_focus, "rows")
            rows = screen.sidebar.view.rows
            screen.sidebar.selected = rows[0]
            await pilot.press("down")
            assert screen.sidebar.selected == rows[1]
            await pilot.press("down")
            assert screen.sidebar.selected == rows[0], "the arrows wrap over the open rows"
            await pilot.press("up")
            assert screen.sidebar.selected == rows[1]
            from textual.widgets import Input

            # The empty workspace has only one focusable panel. Add a second focus
            # target to verify Tab retains Textual's panel-focus behavior.
            panel = Input(id="other-panel")
            await screen.mount(panel)
            screen.sidebar.focus()
            selected = screen.sidebar.selected
            await pilot.press("tab")
            assert panel.has_focus
            assert screen.sidebar.selected == selected
            screen.sidebar.focus()
            assert "+ New session" not in screen.sidebar.text
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(app.screen, NewSessionScreen)
    asyncio.run(check())


def test_stale_ended_sidebar_row_explains_how_to_start_again(serve, monkeypatch) -> None:
    async def check():
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            notices = []
            monkeypatch.setattr(screen, "_record_for", lambda sid: {"id": sid, "state": "terminated"})
            monkeypatch.setattr(app, "notify", lambda text, **kwargs: notices.append(text))
            screen._sidebar_command("pick", "stale-ended")
            assert notices == ["This session has ended. Start a new one with + New session."]
    asyncio.run(check())


def test_sidebar_n_opens_new_session_directly(serve, wait_until) -> None:
    async def check():
        serve_app, url = serve(InputRecordingApp())
        named_sessions(url, serve_app, 'router')
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            assert screen.sidebar.has_focus
            await pilot.press('n')
            await pilot.pause()
            assert isinstance(app.screen, NewSessionScreen)
    asyncio.run(check())


def test_bring_forward_uses_shared_ended_states(serve, monkeypatch) -> None:
    from tsb_tui.model import fleet
    from tsb_tui.screens import terminal

    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            # Extend the authoritative set to prove the consumer follows it.
            monkeypatch.setattr(terminal, 'ENDED_STATES', fleet.ENDED_STATES | {'future-ended'}, raising=False)
            monkeypatch.setattr(screen, '_record_for', lambda sid: {'id': sid, 'state': 'future-ended'})
            notices = []
            monkeypatch.setattr(app, 'notify', lambda text, **kwargs: notices.append(text))
            screen.bring_forward('closed')
            assert notices == ['This session has ended. Start a new one with + New session.']
    asyncio.run(check())


@pytest.mark.parametrize("key", ["tab", "shift+tab"])
def test_sidebar_tab_moves_focus_without_sending_to_attached_session(serve, wait_until, key) -> None:
    async def check():
        from textual.widgets import Input

        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "router")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            panel = Input(id="other-panel")
            await screen.mount(panel)
            screen.open_sidebar()
            await wait_until(pilot, lambda: screen.sidebar.has_focus, "sidebar focus")
            selected = screen.sidebar.selected
            client_id = screen.pump.state.client_id
            await pilot.press(key)
            assert panel.has_focus
            assert screen.sidebar.selected == selected
            assert serve_app.received(client_id) == [], "a sidebar focus key reached the session"

    asyncio.run(check())


def test_all_keys_sidebar_names_new_session() -> None:
    from tsb_tui import config
    from tsb_tui.screens.chrome_dialogs import keys_lines

    lines = keys_lines(config.load())
    sidebar = lines[lines.index("In the fleet sidebar") + 1:lines.index("In the full fleet view")]
    assert any(line.split(maxsplit=1) == ["n", "New session"] for line in sidebar)


def test_with_more_open_sessions_than_fit_down_scrolls_the_selected_row_onto_screen(serve, wait_until) -> None:
    """Ruling (2026-11-16): the selected row is always visible. Moving the selection to any row scrolls the Sessions
    section so that row and its marker are on screen, and a relayout that would hide the selected row scrolls to keep
    it visible. (The New session row left the panel; its keys are `n` and the rail's `+ New`.)"""
    async def check():
        serve_app, url = serve(InputRecordingApp())
        sids = named_sessions(url, serve_app, "sw-1", "sw-2", "sw-3", "sw-4", "sw-5", "sw-6", "sw-7")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, *sids)
            screen.open_sidebar()
            await wait_until(pilot, lambda: len(screen.sidebar.view.rows) == 7, "sidebar rows")

            # A 12-row window leaves a 6-row Sessions section (at most half the panel): four rows show at a time.
            await pilot.resize_terminal(120, 12)
            await wait_until(pilot, lambda: screen.sidebar.size.height == 11, "resized sidebar")
            rows = screen.sidebar.view.rows
            assert screen.sidebar.sessions_height - 2 < len(rows), "not every row fits"

            def visible(name):
                lines = [screen.sidebar.render_line(y).text for y in range(screen.sidebar.size.height)]
                return any("▸" in line and name in line for line in lines)

            screen.sidebar.selected = rows[0]
            screen.sidebar._relayout()
            assert visible("sw-1")
            for _ in rows[1:]:
                await pilot.press("down")
            assert screen.sidebar.selected == rows[-1] and visible("sw-7"), "down scrolls the last row on screen"
            screen.sidebar._relayout()
            assert visible("sw-7"), "a relayout keeps the selected row visible"
            for _ in rows[1:]:
                await pilot.press("up")
            assert screen.sidebar.selected == rows[0] and visible("sw-1"), "up scrolls back to the first row"

    asyncio.run(check())
