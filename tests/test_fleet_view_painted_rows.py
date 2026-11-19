"""The full Sessions view with a very long list: the model is built for the rows it paints, not for every record
the core returned; the counts, the grouping, the selection and the scroll are what they were when every row was
built; a tick moves the clocks of the rows it paints; and the rebuild triggers run at most once per refresh.

Why it matters (Rule 9): the view paints about 36 lines. If the model formats one row per record on the UI thread,
a 14,000-record list costs half a second of every rebuild, and a keystroke queues behind it. A test that only
checks the painted text would pass while the whole fleet is formatted; these tests count what is built, and
compare the painted text with a golden captured from the code that built every row.

Method: the real app, headless, on `drive_support.Rig` with the contract's fixture server for login, and
`GET /sessions` answered from a fixed list of the contract's own `C1/session-record-shape-v0.3` records, ~95% ended,
all owned by the login. No timing assertions: every claim is a count of constructions and calls, and the painted
text is compared with `fleet_view_painted_golden.json`.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from drive_support import Rig
from tsb_contract_client import Client
from tsbc import _serve
from tsbc.schema import Registry

import tsb_tui.screens.terminal as terminal_screen
from tsb_tui.model.fleet import Fleet

CONTRACT = Path(__file__).resolve().parents[1] / "program" / "contract"
REGISTRY = Registry.from_tree(CONTRACT)
BASE = json.loads((CONTRACT / "fixtures" / "C1" / "session-record-shape-v0.3.json")
                  .read_text(encoding="utf-8"))["case"]["instances"][0]["doc"]
ME = _serve.USER
GOLDEN = Path(__file__).resolve().parent / "fleet_view_painted_golden.json"
# The constant the rows built is allowed above the painted rows: the view builds one row per line it shows, and
# nothing else builds one on this path.
PAINTED_EXTRA = 2
TABLE = "Sessions you own or may join"

# The painted views the golden holds: the records, the knobs, and what the operator did to the selection. Each is
# a long list at its own place in it: the top, the bottom of the ended sessions, the middle of them grouped by
# target, and a filter.
CASES: dict[str, dict] = {
    "100": {"n": 100},
    "1000": {"n": 1000},
    "14000": {"n": 14000},
    "14000-ended-bottom": {"n": 14000, "ended": True, "jump": "end"},
    "14000-ended-grouped-middle": {"n": 14000, "ended": True, "group": "target", "move": 5000},
    "14000-filtered": {"n": 14000, "ended": True, "filter": "rtr-007"},
}


def make_records(n: int, **changes) -> list[dict]:
    """`n` session records as `GET /sessions` serves them: ~95% ended, all owned by the login, so the owner's
    view in `mine` mode lists the live ones and holds the ended ones behind `show ended`."""
    out = []
    for i in range(n):
        r = dict(BASE)
        r["id"] = f"s.{i:05x}"
        r["target_id"] = f"rtr-{i % 400:03d}"
        r["state"] = "killed" if i % 20 != 0 else "live"
        r["owner"] = ME
        r["control_holder"] = None
        r["clients"] = []
        r["recording"] = {"active": False}
        r["ending"] = False
        r.update(changes)
        out.append(r)
    errors = REGISTRY.validate(out[0], "schemas/session-record.schema.json")
    assert not errors, errors
    return out


def utc_stamp(when: float) -> str:
    return datetime.fromtimestamp(when, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeSessionsClient(Client):
    """The contract client with `GET /sessions` answered from a fixed list; everything else (login, attach, the
    alert stream) goes to the fixture server."""

    def __init__(self, url: str, records: list[dict]) -> None:
        super().__init__(url, timeout=70.0)
        self._records = records

    def sessions(self) -> list[dict]:
        return self._records


class Counter:
    """Counts what the screen module constructs for the full view, and the model builds, on one screen."""

    def __init__(self) -> None:
        self.entries = 0
        self.rows = 0
        self.models = 0

    def reset(self) -> None:
        self.entries = self.rows = self.models = 0


def instrument(screen, counter: Counter, monkeypatch) -> None:
    for name, field in (("SidebarEntry", "entries"), ("FleetRow", "rows")):
        original = getattr(terminal_screen, name)

        def counting(*args, _original=original, _field=field, **kw):
            setattr(counter, _field, getattr(counter, _field) + 1)
            return _original(*args, **kw)

        monkeypatch.setattr(terminal_screen, name, counting)
    build = screen._fleet_view_model

    def counted(*args, **kw):
        counter.models += 1
        return build(*args, **kw)

    screen._fleet_view_model = counted


async def counted_step(rig: Rig, counter: Counter, func, *args) -> Counter:
    """`func` run as one step on the UI thread with the counter reset before it, so a tick or a poll cannot land in
    the middle of what is being counted."""

    def step() -> Counter:
        counter.reset()
        func(*args)
        snapshot = Counter()
        snapshot.entries, snapshot.rows, snapshot.models = counter.entries, counter.rows, counter.models
        return snapshot

    return await rig.app_call(step)


def painted_lines(view) -> int:
    """The session lines the view paints: the window, not the list."""
    return sum(1 for line in view.layout.lines if line.session_id is not None)


async def open_view(rig: Rig, target: str):
    """The workspace screen with the full view open on the polled fleet: `target` is a text its Sessions title
    must show before the fleet counts as polled."""
    screen = rig.app.workspace()
    await rig.app_call(screen.open_fleet_view)
    for _ in range(600):
        if target in screen.fleet_view.text and screen.fleet_view.size.width >= 100:
            # the layout the painted text is read from is the one at the size the view was given
            await rig.app_call(screen.fleet_view._relayout)
            return screen
        await rig.pilot.pause(0.01)
    raise AssertionError(f"the view never listed the fleet: {screen.fleet_view.text!r}")


def live_count(records: list[dict]) -> int:
    return sum(r["state"] not in ("killed", "terminated") for r in records)


async def paint_case(url: str, tmp_path: Path, case: dict) -> str:
    """The text the full view paints for one golden case. The golden generator calls this too."""
    n = case["n"]
    overrides = ("fleet_view.refresh_s=0",) + ((f"fleet_view.group_by={case['group']}",) if "group" in case else ())
    async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=overrides, size=(120, 40)) as rig:
        rig.app.client = FakeSessionsClient(url, make_records(n))
        await rig.login(ME)
        screen = await open_view(rig, f"{TABLE} · {live_count(make_records(n))}")
        view = screen.fleet_view
        if case.get("ended"):
            await rig.app_call(view.toggle_show_ended)
        if "filter" in case:
            view.filter_text = case["filter"]
            await rig.app_call(view._relayout)
        if "move" in case:
            await rig.app_call(view._move, case["move"])
        if case.get("jump") == "end":
            await rig.app_call(view._jump, True)
        # The alerts count is the alert stream's, which lands at its own time: the golden is about the sessions.
        return re.sub(r"Alerts \d+", "Alerts N", view.text)


def test_one_rebuild_builds_rows_only_for_the_lines_the_view_paints(serve, tmp_path, monkeypatch) -> None:
    """The view paints about 36 lines of a 14,000-record list, so one rebuild must construct about 36 rows (and as
    many entries), not 14,000, with the ended sessions hidden and with them shown. The titles and the position
    count still say the whole fleet is in the model."""

    async def check() -> None:
        _app, url = serve()
        records = make_records(14000)
        counter = Counter()
        live = live_count(records)
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.client = FakeSessionsClient(url, records)
            await rig.login(ME)
            screen = await open_view(rig, f"{TABLE} · {live}")
            view = screen.fleet_view
            painted = painted_lines(view)
            assert 0 < painted < 60, painted
            instrument(screen, counter, monkeypatch)
            built = await counted_step(rig, counter, screen._sync_fleet_widgets)
            assert built.models == 1, "one rebuild ran"
            assert built.rows <= painted + PAINTED_EXTRA, (built.rows, painted)
            assert built.entries <= painted + PAINTED_EXTRA, (built.entries, painted)
            assert f"{TABLE} · {live}" in view.text
            assert len(view.layout.rows) == live, "every live session stays a position the selection can reach"
            await rig.app_call(view.toggle_show_ended)
            built = await counted_step(rig, counter, screen._sync_fleet_widgets)
            assert built.rows <= painted + PAINTED_EXTRA, (built.rows, painted)
            assert built.entries <= painted + PAINTED_EXTRA, (built.entries, painted)
            assert f"{TABLE} · 14000" in view.text
            assert len(view.layout.rows) == 14000

    asyncio.run(check())


def test_a_tick_moves_the_clocks_of_the_open_view_and_builds_only_the_painted_rows(serve, tmp_path,
                                                                                   monkeypatch) -> None:
    """The open view's clocks move with the tick: a countdown reads the new time with no poll. Its rebuild at the
    new clock builds the rows it paints, not the 14,000 the core listed."""
    fixed = 1_800_000_000.0
    records = make_records(14000)
    records[0] = {**records[0], "ending": True, "ends_by": "idle", "ends_at": utc_stamp(fixed + 250)}

    async def check() -> None:
        _app, url = serve()
        counter = Counter()
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.client = FakeSessionsClient(url, records)
            rig.app.wall_clock = lambda: fixed
            await rig.login(ME)
            screen = await open_view(rig, f"{TABLE} · {live_count(records)}")
            view = screen.fleet_view
            assert "▲ 4:10" in view.text, view.text
            painted = painted_lines(view)
            instrument(screen, counter, monkeypatch)
            rig.app.wall_clock = lambda: fixed + 60
            built = await counted_step(rig, counter, screen._tick)
            assert "▲ 3:10" in view.text, f"the open view's clock stopped between polls: {view.text}"
            assert built.rows <= painted + PAINTED_EXTRA, (built.rows, painted)
            assert built.entries <= painted + PAINTED_EXTRA, (built.entries, painted)

    asyncio.run(check())


def test_triggers_before_a_refresh_run_one_rebuild(serve, tmp_path, monkeypatch) -> None:
    """Poll, alerts and the pump each ask for the rail and the view to be rebuilt; three of them landing before a
    refresh of the screen must rebuild once, not three times."""

    async def check() -> None:
        _app, url = serve()
        records = make_records(100)
        counter = Counter()
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.client = FakeSessionsClient(url, records)
            await rig.login(ME)
            screen = await open_view(rig, f"{TABLE} · 5")
            screen._refresh_view_clocks = lambda: None  # the tick is not one of the triggers
            for _ in range(400):  # the alert stream that opened the view has answered and its rebuild has run
                if screen.app.alerts.count and not screen._fleet_dirty and not screen._fleet_flush:
                    break
                await rig.pilot.pause(0.01)
            await rig.pilot.pause(0.1)
            instrument(screen, counter, monkeypatch)
            counter.reset()

            def fire() -> None:  # all three land on the UI thread in one step, before any refresh
                screen._apply_poll(10, records)
                screen._apply_poll(11, records)
                screen._alerts_changed()

            await rig.app_call(fire)
            for _ in range(50):
                await rig.pilot.pause(0.01)
                if counter.models:
                    break
            assert counter.models == 1, f"the three triggers ran {counter.models} rebuilds, not one"
            for _ in range(20):
                await rig.pilot.pause(0.01)
            assert counter.models == 1, "the coalesced rebuild ran more than once"

    asyncio.run(check())


def test_the_painted_view_matches_the_golden_at_a_long_list(serve, tmp_path) -> None:
    """The text the view painted when it built every row, at 100, 1,000 and 14,000 records, the whole ended list
    shown and scrolled to its bottom, grouped by target with the selection deep inside, and filtered: the same
    headings, columns, counts, scroll and selection mark."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(golden) == set(CASES), "the golden and the cases name the same views"

    for name, case in CASES.items():
        _app, url = serve()
        text = asyncio.run(paint_case(url, tmp_path, case))
        assert text == golden[name], f"the painted view changed: {name}"


def test_the_selection_moves_over_the_listed_rows_as_before(serve, tmp_path) -> None:
    """The rows the view can select are the ones it lists, in the same order: `down` moves through them, the
    selection lands on the same row as before, and moving back returns to where it started."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    async def check() -> None:
        _app, url = serve()
        records = make_records(14000)
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.client = FakeSessionsClient(url, records)
            await rig.login(ME)
            screen = await open_view(rig, f"{TABLE} · {live_count(records)}")
            view = screen.fleet_view
            rows = list(view.layout.rows)
            assert len(rows) == live_count(records)
            start = view.selected
            assert start == rows[0]
            for _ in range(5):
                await rig.app_call(view._move, 1)
            assert view.selected == rows[5], (view.selected, rows[5])
            for _ in range(5):
                await rig.app_call(view._move, -1)
            assert view.selected == start
            assert re.sub(r"Alerts \d+", "Alerts N", view.text) == golden["14000"]
            await rig.app_call(view._jump, True)
            assert view.selected == rows[-1]
            assert f"{rows[-1][1]}" in view.text or any(
                line.session_id == rows[-1][1] for line in view.layout.lines), "the last row is scrolled into view"

    asyncio.run(check())


def test_the_selection_follows_its_session_when_the_list_changes(serve, tmp_path) -> None:
    """A poll that adds sessions above the selected one keeps the selection on the same session, and the scroll
    keeps it in view, deep in a 14,000-record list."""

    async def check() -> None:
        _app, url = serve()
        records = make_records(14000)
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.client = FakeSessionsClient(url, records)
            await rig.login(ME)
            screen = await open_view(rig, f"{TABLE} · {live_count(records)}")
            view = screen.fleet_view
            await rig.app_call(view._move, 400)
            chosen = view.selected
            assert chosen is not None and chosen[1] != view.layout.rows[0][1]
            added = make_records(1, state="live")[0]
            added["id"] = "s.added"
            added["target_id"] = "rtr-000"
            await rig.app_call(screen._apply_poll, 12, [added, *records])
            await rig.app_call(screen._sync_fleet_widgets)
            assert view.selected == chosen, (view.selected, chosen)
            assert any(line.session_id == chosen[1] for line in view.layout.lines), "the selection stays in view"
            assert f"Sessions {live_count(records) + 1} " in view.text

    asyncio.run(check())


def test_the_filter_and_show_ended_count_every_record_not_the_window(serve, tmp_path) -> None:
    """The filter matches target, id, login and owner over every record, and `show ended` lists the ended ones: the
    title's count and the positions are those of the whole list, whatever the window paints."""

    async def check() -> None:
        _app, url = serve()
        records = make_records(14000)
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.client = FakeSessionsClient(url, records)
            await rig.login(ME)
            screen = await open_view(rig, f"{TABLE} · {live_count(records)}")
            view = screen.fleet_view
            view.filter_text = "rtr-007"
            await rig.app_call(view._relayout)
            want = [r for r in records if r["state"] == "live" and r["target_id"] == "rtr-007"]
            assert f"{TABLE} · {len(want)}" in view.text, view.text
            assert [sid for _group, sid in view.layout.rows] == [r["id"] for r in want]
            await rig.app_call(view.toggle_show_ended)
            every = [r for r in records if r["target_id"] == "rtr-007"]
            assert f"{TABLE} · {len(every)}" in view.text, view.text
            assert len(view.layout.rows) == len(every)
            # the live sessions are listed before the ended ones, each in the order the core listed them
            ordered = [r for r in every if r["state"] == "live"] + [r for r in every if r["state"] != "live"]
            assert [sid for _group, sid in view.layout.rows] == [r["id"] for r in ordered]

    asyncio.run(check())


def test_the_columns_are_sized_by_every_listed_row_not_the_window(serve, tmp_path) -> None:
    """The table's column widths come from every row it lists, so a long value far below the window still widens
    its column and the table does not shift as it scrolls."""

    async def check() -> None:
        _app, url = serve()
        records = make_records(1000)
        records[-1] = {**records[-1], "state": "live", "target_id": "rtr-with-a-long-name-0001"}
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.client = FakeSessionsClient(url, records)
            await rig.login(ME)
            screen = await open_view(rig, f"{TABLE} · {live_count(records)}")
            view = screen.fleet_view
            def id_column() -> int:
                """Where the id of a listed session starts: the same at every scroll when the columns are sized by
                the whole list."""
                line = next(line.text for line in view.layout.lines if line.session_id is not None)
                return line.index("s.s.")

            at_top = id_column()
            await rig.app_call(view._jump, True)
            assert "rtr-with-a-long…" in view.text, "the long target is in view at the bottom"
            assert id_column() == at_top
            assert at_top >= 3 + 16 + 2, at_top

    asyncio.run(check())


def test_a_clock_that_changes_a_row_s_width_is_painted_as_a_rebuild_paints_it(serve, tmp_path) -> None:
    """The countdown's width changes (9:59 to 10:00, 1d 0h to 23h 59m): the view paints, after the tick, exactly
    the text a full rebuild at the same clock paints."""
    fixed = 1_800_000_000.0

    async def check(left: int, right: int) -> None:
        _app, url = serve()
        clock = [fixed]
        records = make_records(1, ending=True, ends_by="idle", ends_at=utc_stamp(fixed + left))
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.wall_clock = lambda: clock[0]
            rig.app.client = FakeSessionsClient(url, records)
            await rig.login(ME)
            screen = await open_view(rig, "Needs you")
            view = screen.fleet_view
            clock[0] = fixed + (left - right)
            await rig.app_call(screen._tick)
            after_tick = view.text
            await rig.app_call(screen._sync_fleet_widgets)
            assert view.text == after_tick, "a tick painted what a rebuild at the same clock does not"

    for left, right in ((60, 59), (600, 599), (86400, 86399)):
        asyncio.run(check(left, right))


def _small_fleet() -> Fleet:
    """A fleet with one record of each group, plus the states the needs-you reasons name: an ending session open
    here (in Needs you and Open here), one held by another login, one a peer owns, one stale with me typing, one
    asking me, one unreachable, and one ended."""
    fleet = Fleet(own_user=ME, filter_mode="mine")
    records = [
        {"id": "s-ending-open", "target_id": "rtr-a", "owner": ME, "control_holder": None, "state": "live",
         "ending": True, "ends_by": "idle", "ends_at": "2026-11-02T00:00:00Z", "recording": {"active": True}},
        {"id": "s-stale", "target_id": "rtr-b", "owner": ME, "control_holder": None, "state": "live",
         "ending": False, "recording": {"active": False}},
        {"id": "s-asks", "target_id": "rtr-b", "owner": ME, "control_holder": None, "state": "live",
         "ending": False, "recording": {"active": False}},
        {"id": "s-unreachable", "target_id": "rtr-c", "owner": ME, "control_holder": None,
         "state": "unreachable", "ending": False, "recording": {"active": False}},
        {"id": "s-held", "target_id": "rtr-c", "owner": ME, "state": "live", "ending": False,
         "control_holder": {"user": _serve.PEER, "client_id": "c-peer"}, "recording": {"active": True}},
        {"id": "s-joinable", "target_id": "rtr-d", "owner": ME, "control_holder": None, "state": "live",
         "ending": False, "recording": {"active": False}},
        {"id": "s-peer", "target_id": "rtr-e", "owner": _serve.PEER, "control_holder": None, "state": "live",
         "ending": False, "recording": {"active": True}},
        {"id": "s-ended", "target_id": "rtr-e", "owner": ME, "control_holder": None, "state": "killed",
         "ending": False, "recording": {"active": False}},
    ]
    fleet.apply(1, records)
    return fleet


def _same_as_the_three_scans(fleet: Fleet, open_here: list[str], stale: list[str], asks: list[str],
                             live: dict[str, dict]) -> None:
    """The one pass gives exactly what `needs_you`, `groups`, `chips` and `chip_count` give, and the column fields
    and target counts the model build reads are what reading each record gives."""
    scan = fleet.scan(open_here=open_here, stale_with_me=stale, question_for_me=asks, live=live)
    assert scan.needs_you == fleet.needs_you(open_here=open_here, stale_with_me=stale,
                                             question_for_me=asks, live=live)
    assert scan.groups == fleet.groups(open_here=open_here, stale_with_me=stale,
                                       question_for_me=asks, live=live)
    chips = {"open_here": open_here, "front": open_here[0] if open_here else None, "unseen": ["s-ending-open"],
            "question_for_me": asks, "stale_with_me": stale, "live": live}
    assert fleet.chips(scan=scan, **chips) == fleet.chips(**chips)
    assert len(scan.chip_ids) == fleet.chip_count(open_here=open_here, live=live)
    counts: dict[str, int] = {}
    for sid, (target, login) in scan.columns.items():
        record = scan.merged[sid]
        holder = record.get("control_holder")
        assert target == record.get("target_id", "")
        assert login == ("·" if holder is None else (
            "you" if holder.get("user") == fleet.own_user else str(holder.get("user"))))
        counts[target] = counts.get(target, 0) + 1
    assert scan.by_target == counts


def test_the_one_pass_over_the_records_gives_what_the_three_scans_give() -> None:
    """The rebuild used to scan the fleet for needs-you, again for the groups, again for the chips and again for the
    column fields. One pass must give the same values on a small fleet that covers every group, in `mine` and in
    `all`, with and without the live records of sessions open here, and on the 14,000-record fleet."""
    fleet = _small_fleet()
    groups = fleet.groups(open_here=["s-ending-open"], stale_with_me=["s-stale"], question_for_me=["s-asks"])
    assert [r["id"] for r in groups["needs_you"]] == ["s-ending-open", "s-stale", "s-asks", "s-unreachable"]
    assert [r["id"] for r in groups["open_here"]] == ["s-ending-open"]
    assert [r["id"] for r in groups["mine_held_by_others"]] == ["s-held"]
    assert [r["id"] for r in groups["mine_and_joinable"]] == [
        "s-stale", "s-asks", "s-unreachable", "s-joinable"]
    _same_as_the_three_scans(fleet, ["s-ending-open"], ["s-stale"], ["s-asks"], {})
    _same_as_the_three_scans(fleet, [], [], [], {})
    fleet.filter_mode = "all"
    _same_as_the_three_scans(fleet, ["s-held", "s-peer"], ["s-stale"], ["s-asks"], {})
    fleet.filter_mode = "mine"
    live = {"s-live": {"id": "s-live", "target_id": "rtr-f", "owner": ME, "control_holder": None,
                       "state": "live", "ending": True, "ends_by": "idle",
                       "ends_at": "2026-11-02T00:00:00Z", "recording": {"active": False}}}
    _same_as_the_three_scans(fleet, ["s-live", "s-ending-open"], [], [], live)

    big = Fleet(own_user=ME, filter_mode="mine")
    big.apply(1, make_records(14000))
    _same_as_the_three_scans(big, [], [], [], {})
    big.filter_mode = "all"
    _same_as_the_three_scans(big, ["s.00000", "s.0000a"], ["s.00001"], ["s.00002"], {})


def test_opening_the_view_shows_the_current_rows(serve, tmp_path) -> None:
    """A poll that landed while the view was closed added a session that needs the operator: opening the view must
    run the pending rebuild first, so it lists the new session and not the list from before the poll."""

    async def check() -> None:
        _app, url = serve()
        fixed = 1_800_000_000.0
        before = make_records(3)
        added = make_records(1, ending=True, ends_by="idle", ends_at=utc_stamp(fixed + 250))[0]
        added["id"] = "s.needs"
        added["target_id"] = "rtr-needs"
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.wall_clock = lambda: fixed
            rig.app.client = FakeSessionsClient(url, before)
            await rig.login(ME)
            screen = rig.app.workspace()
            for _ in range(600):
                if len(screen.fleet.records) == 3:
                    break
                await rig.pilot.pause(0.01)
            screen._apply_poll(10, [*before, added])  # the fleet changes while the view is closed
            await rig.app_call(screen.open_fleet_view)
            view = screen.fleet_view
            assert "rtr-needs" in view.text and "Needs you" in view.text, view.text
            assert view.selected == ("needs_you", "s.needs"), view.selected

    asyncio.run(check())


def test_a_relayout_after_a_tick_builds_its_rows_at_the_current_clock(serve, tmp_path) -> None:
    """The tick rebuilds the open view at the new clock. A relayout after it (the selection moves) builds the rows
    it paints again: their countdowns must read the current clock, never the clock of an earlier build, and they
    must be what a full rebuild at the same clock paints."""
    fixed = 1_800_000_000.0

    async def check() -> None:
        _app, url = serve()
        clock = [fixed]
        records = []
        for sid, left in (("s.a", 250), ("s.b", 300)):
            r = make_records(1, ending=True, ends_by="idle", ends_at=utc_stamp(fixed + left))[0]
            r["id"] = sid
            r["target_id"] = f"rtr-{sid[-1]}"
            records.append(r)
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.wall_clock = lambda: clock[0]
            rig.app.client = FakeSessionsClient(url, records)
            await rig.login(ME)
            screen = await open_view(rig, "Needs you")
            view = screen.fleet_view
            assert "▲ 4:10" in view.text and "▲ 5:00" in view.text, view.text
            clock[0] = fixed + 60
            await rig.app_call(screen._tick)
            assert "▲ 3:10" in view.text and "▲ 4:00" in view.text, view.text
            for _ in range(2):
                await rig.app_call(view._move, 1)  # the selection moves: the painted rows are built again
            after_relayout = view.text
            assert "▲ 3:10" in after_relayout and "▲ 4:00" in after_relayout, (
                f"a relayout after the tick built the rows at an old clock: {after_relayout}")
            await rig.app_call(screen._sync_fleet_widgets)
            assert view.text == after_relayout, "a relayout painted rows a rebuild at the same clock does not"

    asyncio.run(check())


def test_the_open_full_view_clocks_move_with_the_tick(serve, tmp_path) -> None:
    """While the view is open the tick rebuilds its model at the new clock: a countdown the view shows advances
    with no poll. A closed view is never rebuilt by a tick."""
    fixed = 1_800_000_000.0

    async def check() -> None:
        _app, url = serve()
        clock = [fixed]
        record = make_records(1, ending=True, ends_by="idle", ends_at=utc_stamp(fixed + 250))[0]
        builds = [0]
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.wall_clock = lambda: clock[0]
            rig.app.client = FakeSessionsClient(url, [record])
            await rig.login(ME)
            screen = rig.app.workspace()
            build = screen._fleet_view_model

            def counted(*args, **kw):
                builds[0] += 1
                return build(*args, **kw)

            screen._fleet_view_model = counted
            screen = await open_view(rig, "Needs you")
            assert "▲ 4:10" in screen.fleet_view.text, screen.fleet_view.text
            clock[0] = fixed + 60
            await rig.app_call(screen._tick)
            assert "▲ 3:10" in screen.fleet_view.text, (
                f"the open view's clock stopped between polls: {screen.fleet_view.text}")
            screen.close_fleet_view()
            builds[0] = 0
            clock[0] = fixed + 120
            await rig.app_call(screen._tick)
            assert builds[0] == 0, "a tick rebuilt the closed full view"

    asyncio.run(check())
