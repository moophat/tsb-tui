"""The rail (design §2.3, D41, D12): four columns beside the terminal that say what needs you and who types in each
session, and switch with one click.

Why it matters (Rule 9): the rail is where an operator with a dozen sessions sees, without opening anything, that one
of them is about to be killed, that an agent is typing in another, or that output arrived in a third. A chip that
shows the wrong glyph sends the operator to the wrong session; a count that is wrong hides a warning; a glyph that
does nothing when clicked breaks the rule that every action has a way in that needs no key. Chips are built from
contract-valid records (DT6) and the live pilot runs against the contract's own fixture server (DT7).
"""

from __future__ import annotations

import asyncio

from fleet_support import ME, PEER, derived, fixture_records, holder
from input_server import InputRecordingApp
from stub_server import SlowChunkedApp, StubServer
from textual.command import CommandPalette
from tsb_tui.app import TsbTuiApp
from tsb_tui.model.fleet import Fleet
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.widgets.rail import (
    ACT_ALERTS,
    ACT_FLEET,
    ACT_MENU,
    ACT_MORE,
    ACT_NEEDS,
    ACT_NEW,
    Rail,
    RailModel,
    rail_rows,
)
from tsbc import _serve
from workspace_support import (
    click_chip,
    fixture_login,
    login,
    named_sessions,
    open_here,
    rail_row,
)


def _record(sid: str, **changes: object) -> dict:
    return derived(fixture_records()[1], id=sid, **changes)


def _texts(model: RailModel) -> list[str]:
    return [row.text for row in rail_rows(model)]


# -- the rows, from the models --


def test_a_chip_reads_its_number_who_types_and_whether_it_is_in_front_or_unseen() -> None:
    """D41: cell 1 the number, cell 2 who types (`●` you, `◆` another login, `·` nobody, `▲` needs you, `?` asks you,
    `◌` unreachable, `✕` ended), cell 3 `◂` for the session in front or `•` for output not looked at. Mockup T1's
    own chips: `1●◂`, `2◆•`, `4▲`."""
    fleet = Fleet(own_user=ME)
    records = [
        _record("s-1", state="live", control_holder=holder(ME)),
        _record("s-2", state="live", control_holder=holder(PEER)),
        _record("s-3", state="live", control_holder=holder(PEER), ending=True,
                ends_at="2026-10-07T12:00:00Z", ends_by="idle"),
        _record("s-4"),
        _record("s-5", state="live", control_holder=holder(ME)),
        _record("s-6", state="unreachable", unreachable_reason="protocol"),
    ]
    fleet.apply(1, records)
    ids = [r["id"] for r in records]
    chips = fleet.chips(open_here=ids, front="s-1", unseen=["s-2"], question_for_me=["s-5"])
    model = RailModel(tuple(chips), more=False, need_you=3, alerts=None)
    assert _texts(model) == [" ≡ ", " ▦▸", "▲3 ", "┄┄┄", "1●◂", "2◆•", "3▲ ", "4· ", "5? ", "6◌ ", " + "]


def test_more_than_nine_chips_end_in_an_ellipsis_before_the_plus() -> None:
    """D41: "More than nine chips end in `…`, which opens the sidebar": nine chips, then `…`, then `+`. Ten sessions
    show nine, and the ellipsis row is a click on the sidebar."""
    fleet = Fleet(own_user=ME)
    records = [_record(f"s-{n:02d}") for n in range(10)]
    fleet.apply(1, records)
    ids = [r["id"] for r in records]
    model = RailModel(tuple(fleet.chips(open_here=ids)), more=fleet.chip_count(open_here=ids) > 9,
                      need_you=0, alerts=None)
    rows = rail_rows(model)
    assert [r.text for r in rows][-2:] == [" … ", " + "]
    assert sum(1 for r in rows if r.action == "chip") == 9
    assert [r.action for r in rows][-2:] == [ACT_MORE, ACT_NEW]


def test_the_alert_count_is_only_there_for_a_login_that_holds_broker_health() -> None:
    """DT14: `!n` is for a `broker.health` holder. A login that is not one has no such row at all, not `!0`: a count
    it can never act on would be noise on every rail."""
    absent = _texts(RailModel((), more=False, need_you=0, alerts=None))
    assert not any(text.startswith("!") for text in absent)
    assert "!0 " in _texts(RailModel((), more=False, need_you=0, alerts=0))
    assert "!3 " in _texts(RailModel((), more=False, need_you=0, alerts=3))
    assert "!99" in _texts(RailModel((), more=False, need_you=0, alerts=250)), "three digits do not fit a chip"


def test_the_edge_rail_is_one_tappable_column() -> None:
    """`rail.phone = "edge"`: one column, whose every tap opens the sidebar, for a phone that cannot spare four."""
    rows = rail_rows(RailModel((), more=False, need_you=0, alerts=None, edge=True))
    assert [r.text for r in rows] == ["▸  "]
    assert [r.action for r in rows] == [ACT_FLEET]


# -- the live rail --


def test_the_rail_shows_who_types_in_each_session_open_here_and_the_one_in_front(
        serve, wait_until, screen_text) -> None:
    """Mockup T1 on the fixture server: a session the operator types in (`●`), one a peer holds (`◆`), numbered in the
    order they were opened, the last opened in front (`◂`). The rail is four columns, hairline included."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        mine, theirs = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02")
        from input_server import other_client

        with other_client(url, _serve.PEER, theirs):  # the peer attaches first and so holds the keyboard
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await fixture_login(app, pilot)
                await open_here(app, pilot, wait_until, mine, theirs)
                screen.close_sidebar()
                await wait_until(pilot, lambda: not screen.sidebar_open, "the sidebar to close")
                await wait_until(pilot, lambda: screen.rail.rows and screen.rail.rows[-2].text == "2◆◂",
                                 "the second chip to show the peer typing and to be in front")
                lines = screen.rail.text.splitlines()
                assert lines[:5] == [" ≡ │", " ▦▸│", "▲0 │", "!0 │", "┄┄┄│"]
                assert lines[5:8] == ["1● │", "2◆◂│", " + │"]
                await wait_until(pilot, lambda: screen.rail.region.width == 4, "the rail to be laid out")

    asyncio.run(_check())


def test_the_alert_row_follows_the_login_through_the_alert_stream(serve, wait_until) -> None:
    """DT14 against both servers: the fixture's superadmin holds `broker.health` and its rail has `!n`; a login the
    core answers 403 has none, and the stream is not retried."""

    async def _holder() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.close_sidebar()
            await wait_until(pilot, lambda: rail_row(screen, ACT_ALERTS) is not None, "the alert row")

    async def _not_a_holder() -> None:
        stub = SlowChunkedApp(alerts_status=403)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 30)) as pilot:
                screen = await fixture_login(app, pilot)
                screen.close_sidebar()
                await wait_until(pilot, lambda: stub.alerts_requests >= 1 and not app.alerts.allowed,
                                 "the 403 to be read")
                await pilot.pause(0.1)
                assert rail_row(screen, ACT_ALERTS) is None, screen.rail.text
                assert stub.alerts_requests == 1

    asyncio.run(_holder())
    asyncio.run(_not_a_holder())


def test_each_top_glyph_and_each_chip_opens_what_d12_says_by_a_click(serve, wait_until) -> None:
    """D12, design §3.4 test 5: `≡` opens the Menu, `▦▸` the sidebar, `▲n` the sidebar at Needs you, `!n` the alerts, a
    chip brings its session to the front, `…` the sidebar and `+` New session, each by one click and no key."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        first, second = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02")
        # The second session is in a warning window, so it needs the operator.
        serve_app.sessions[second]["ending"] = True
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first, second)
            screen.close_sidebar()
            await wait_until(pilot, lambda: not screen.sidebar_open and rail_row(screen, ACT_MENU) is not None,
                             "the rail")

            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_MENU)))
            await wait_until(pilot, lambda: isinstance(app.screen, CommandPalette), "the Menu to open")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "the Menu to close")

            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_FLEET)))
            await wait_until(pilot, lambda: screen.sidebar_open, "the sidebar to open from ▦▸")
            screen.close_sidebar()

            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_ALERTS)))
            await wait_until(pilot, lambda: screen.view_open and screen.fleet_view.tab == "alerts",
                             "the Alerts tab to open from !n")
            await pilot.press("escape")
            await wait_until(pilot, lambda: not screen.view_open, "the full view to close")

            await click_chip(pilot, screen, first)
            await wait_until(pilot, lambda: screen.front.session_id == first, "the chip to bring its session forward")

            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_NEW)))
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "New session from +")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "New session to cancel")
            assert len(screen.slots) == 2, "opening and cancelling New session left the sessions open"

    asyncio.run(_check())


def test_the_needs_you_count_opens_the_sidebar_at_needs_you(wait_until) -> None:
    """D12: `▲3` "opens the sidebar at Needs you". A session in a warning window (a `notice` with `ending` true, DT7's
    one-frame case) is counted, and the click lands the selection on that group's first row."""

    async def _check() -> None:
        notice = {"t": "notice", "v": 1, "session_id": "stub-session-1", "ends_at": "2026-10-07T12:00:00Z",
                  "ends_by": "idle", "ending": True}
        stub = SlowChunkedApp(after_attach_frames=[notice], delay_s=0.5, max_chunks=2)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await login(app, pilot)
                app.client.create_session("stub-target", "new", "xterm-256color", 80, 24)
                app.gated_sessions.add("stub-session-1")
                screen.open_session(app.client.session("stub-session-1"))
                await wait_until(pilot, lambda: screen.slot_for("stub-session-1") is not None
                                 and screen.slot_for("stub-session-1").pump is not None
                                 and screen.slot_for("stub-session-1").pump.state.ending, "the warning window")
                screen.close_sidebar()
                await wait_until(pilot, lambda: screen.rail.rows[rail_row(screen, ACT_NEEDS)].text == "▲1 ",
                                 "the rail to count the session that needs the operator")
                await pilot.click(Rail, offset=(1, rail_row(screen, ACT_NEEDS)))
                await wait_until(pilot, lambda: screen.sidebar_open, "the sidebar")
                assert screen.sidebar.selected == ("needs_you", "stub-session-1")

    asyncio.run(_check())


def test_the_ascii_glyph_set_draws_the_rail_in_ascii_and_the_right_side_puts_the_hairline_left(
        serve, monkeypatch, wait_until, screen_text) -> None:
    """`glyphs.set = ascii` (D37) and `rail.side = right`: the knob file's own ASCII stand-ins on the rail, one cell
    each, and the hairline on the side that touches the canvas."""
    monkeypatch.setenv("TSB_TUI_GLYPHS_SET", "ascii")
    monkeypatch.setenv("TSB_TUI_RAIL_SIDE", "right")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.close_sidebar()
            await wait_until(pilot, lambda: len(screen.rail.rows) >= 6, "the rail")
            lines = screen.rail.text.splitlines()
            assert lines[:2] == ["│ = ", "│ #>"]
            assert lines[5] == "│1*<"
            await wait_until(pilot, lambda: screen.rail.region.width == 4, "the rail to be laid out")
            assert screen.rail.region.x == 96 and screen.canvas.region.x == 0
            painted = [line[96:100] for line in screen_text(app).splitlines()]
            assert painted[:2] == ["│ =", "│ #>"] and painted[5] == "│1*<", "what is painted, not only what is read"

    asyncio.run(_check())


def test_the_phone_profile_with_an_edge_rail_is_one_column_that_opens_the_sidebar(
        serve, monkeypatch, wait_until) -> None:
    """`rail.phone = "edge"` under the phone profile (design §2.6): the collapsed rail shrinks to one tappable column
    and a tap on it opens the sidebar, at the phone's width (30)."""
    monkeypatch.setenv("TSB_TUI_PROFILE", "phone")
    monkeypatch.setenv("TSB_TUI_RAIL_PHONE", "edge")
    monkeypatch.setenv("TSB_TUI_SIDEBAR_OPEN_WHEN_NO_SESSION", "false")

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(65, 30)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.rail.edge, "the rail to be the edge")
            assert not screen.sidebar_open and screen.rail.region.width == 1
            await pilot.click(Rail, offset=(0, 4))
            await wait_until(pilot, lambda: screen.sidebar_open, "the sidebar to open from a tap")
            assert screen.sidebar.region.width == 30

    asyncio.run(_check())

