"""The fleet sidebar with a very long list (design §2.3, DT15): the model is built for the rows it paints, not for
every record the core returned; a tick moves only the clocks of the painted rows; and the rebuild triggers run at
most once per refresh of the screen.

Why it matters (Rule 9): the sidebar paints about 28 rows. If the model formats one row per record on the UI thread,
a 14,000-record list costs a quarter to a third of every second, and a keystroke queues behind it. A test that only
checks the painted text would pass while the whole fleet is formatted; these tests count what is built.

Method: the real app, headless, on `drive_support.Rig` with the contract's fixture server for login, and
`GET /sessions` answered from a fixed list of the contract's own `C1/session-record-shape-v0.3` records, ~95% ended,
all owned by the login — the same fleet the measurement report builds. No timing assertions: every claim is a count
of constructions and calls, and the painted text is compared with the golden captured from the code before the change
(`sidebar_painted_golden.json`).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from drive_support import Rig
from input_server import make_session
from tsb_contract_client import Client
from tsbc import _serve
from tsbc.schema import Registry

import tsb_tui.screens.terminal as terminal_screen
from tsb_tui.model.fleet import GROUP_NEEDS_YOU, GROUP_OPEN_HERE, Fleet

CONTRACT = Path(__file__).resolve().parents[1] / "program" / "contract"
REGISTRY = Registry.from_tree(CONTRACT)
BASE = json.loads((CONTRACT / "fixtures" / "C1" / "session-record-shape-v0.3.json")
                  .read_text(encoding="utf-8"))["case"]["instances"][0]["doc"]
ME = _serve.USER
GOLDEN = Path(__file__).resolve().parent / "sidebar_painted_golden.json"
# The constant the entry count is allowed above the painted rows: the sidebar paints one entry per row it shows,
# and nothing else builds one on this path.
PAINTED_EXTRA = 2


def make_records(n: int, **changes) -> list[dict]:
    """`n` session records as `GET /sessions` serves them: ~95% ended, all owned by the login, so the owner's
    sidebar in `mine` mode ends in `N more · full view` like the lab's."""
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
    """Counts the `SidebarEntry` objects the screen module constructs and the sidebar model builds on one screen."""

    def __init__(self) -> None:
        self.entries = 0
        self.models = 0

    def reset(self) -> None:
        self.entries = self.models = 0


def instrument(screen, counter: Counter, monkeypatch) -> None:
    original = terminal_screen.SidebarEntry

    def counting(*args, **kw):
        counter.entries += 1
        return original(*args, **kw)

    monkeypatch.setattr(terminal_screen, "SidebarEntry", counting)
    build = screen._sidebar_model

    def counted(*args, **kw):
        counter.models += 1
        return build(*args, **kw)

    screen._sidebar_model = counted


async def painted(rig: Rig, target: str):
    """The workspace screen once the sidebar lists the polled fleet."""
    screen = rig.app.workspace()
    for _ in range(600):
        if target in screen.sidebar.text:
            return screen
        await rig.pilot.pause(0.01)
    raise AssertionError(f"the sidebar never listed the fleet: {screen.sidebar.text!r}")


def test_one_rebuild_builds_entries_only_for_the_rows_the_sidebar_paints(serve, tmp_path, monkeypatch) -> None:
    """The sidebar paints about 28 rows of a 14,000-record list, so one rebuild must construct about 28 entries,
    not 14,000. The counts and the `N more · full view` line still say the whole fleet is in the model."""

    async def check() -> None:
        _app, url = serve()
        records = make_records(14000)
        counter = Counter()
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.client = FakeSessionsClient(url, records)
            await rig.login(ME)
            screen = await painted(rig, f"Yours and joinable · {sum(r['state'] not in ('killed', 'terminated') for r in records)}")
            rows = len(screen.sidebar.view.rows)
            assert 0 < rows < 60, rows
            instrument(screen, counter, monkeypatch)
            counter.reset()
            await rig.app_call(screen._sync_fleet_widgets)
            assert counter.models == 1, "one rebuild ran"
            assert counter.entries <= rows + PAINTED_EXTRA, (counter.entries, rows)
            hidden = sum(r["state"] not in ("killed", "terminated") for r in records) - rows
            assert f"┆ {hidden} more · full view ▸" in screen.sidebar.text, screen.sidebar.text

    asyncio.run(check())


def test_a_tick_with_unchanged_records_moves_only_the_clocks_of_the_painted_rows(serve, tmp_path,
                                                                                monkeypatch) -> None:
    """The tick's job is the clock: a row in its warning window reads the new countdown, and nothing else is
    rebuilt — no model build, no entry constructed."""
    fixed = 1_800_000_000.0
    record = make_records(1, ending=True, ends_by="idle", ends_at=utc_stamp(fixed + 250))[0]

    async def check() -> None:
        _app, url = serve()
        counter = Counter()
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.client = FakeSessionsClient(url, [record])
            rig.app.wall_clock = lambda: fixed
            await rig.login(ME)
            screen = await painted(rig, "Needs you · 1")
            assert "▲ 4:10" in screen.sidebar.text, screen.sidebar.text
            instrument(screen, counter, monkeypatch)
            counter.reset()
            rig.app.wall_clock = lambda: fixed + 60
            await rig.app_call(screen._tick)
            assert counter.models == 0, "the tick rebuilt the fleet model"
            assert counter.entries == 0, "the tick constructed entries"
            assert "▲ 3:10" in screen.sidebar.text, screen.sidebar.text
            assert "Needs you · 1" in screen.sidebar.text

    asyncio.run(check())


def test_triggers_before_a_refresh_run_one_rebuild(serve, tmp_path, monkeypatch) -> None:
    """Poll, alerts and the pump each ask for the rail and the sidebar to be rebuilt; three of them landing
    before a refresh of the screen must rebuild once, not three times."""

    async def check() -> None:
        _app, url = serve()
        records = make_records(100)
        counter = Counter()
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.client = FakeSessionsClient(url, records)
            await rig.login(ME)
            screen = await painted(rig, "Yours and joinable · 5")
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


def test_the_painted_sidebar_text_matches_live_sessions_at_a_long_list(serve, tmp_path) -> None:
    """The golden lists only live sessions, with matching headings, rows, overflow counts and footer."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    async def check(n: int) -> None:
        _app, url = serve()
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.client = FakeSessionsClient(url, make_records(n))
            await rig.login(ME)
            screen = await painted(rig, f"Yours and joinable · {(n + 19) // 20}")
            text = screen.sidebar.text
            assert text == golden[str(n)], f"the painted sidebar changed at {n} records"

    for n in (100, 1000, 14000):
        asyncio.run(check(n))


def test_the_selection_moves_over_the_painted_rows_as_before(serve, tmp_path) -> None:
    """The rows the sidebar can select are the ones it paints, in the same order: `down` moves through them and
    the selection lands on the same row as before the change."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    async def check() -> None:
        _app, url = serve()
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            rig.app.client = FakeSessionsClient(url, make_records(14000))
            await rig.login(ME)
            screen = await painted(rig, "Yours and joinable · 700")
            rows = list(screen.sidebar.view.rows)
            start = screen.sidebar.selected
            for _ in range(5):
                await rig.app_call(screen.sidebar._move, 1)
            assert screen.sidebar.selected == rows[5], (screen.sidebar.selected, rows[5])
            for _ in range(5):
                await rig.app_call(screen.sidebar._move, -1)
            assert screen.sidebar.selected == start
            assert screen.sidebar.text == golden["14000"]

    asyncio.run(check())


def _painted_rows(sidebar) -> list[str]:
    """The text of the session rows the sidebar paints: what the tick re-lays and a rebuild builds, without the
    header, the footer or the selected session's details, which the tick does not move."""
    return [line.text for line in sidebar.view.lines if line.session_id]


def _record_the_tick_flags(screen, counter: Counter, monkeypatch) -> list[bool]:
    """The tick's own judgement, recorded. `refresh_times` returns whether a painted row's right-hand fields
    changed width, which is what makes the tick mark the model dirty; wrapping it on the widget keeps the claim
    about the tick, not about a live trigger that landed in the same step."""
    flags: list[bool] = []
    original = screen.sidebar.refresh_times

    def counted(times):
        result = original(times)
        flags.append(result)
        return result

    screen.sidebar.refresh_times = counted
    instrument(screen, counter, monkeypatch)
    return flags


async def _tick_and_judge(rig: Rig, screen, flags: list[bool], counter: Counter, clock: list[float],
                          new_clock: float, needs_rebuild: bool) -> None:
    """The tick on a clean model, then the rebuild it asked for. The rows the sidebar paints after the tick are the
    rows a full rebuild at the same clock paints. The clock moves only after any queued rebuild has run, so the row
    the tick re-lays is the one painted at the old clock."""
    for _ in range(200):
        await rig.pilot.pause(0.01)
        if not screen._fleet_dirty:
            break
    assert not screen._fleet_dirty, "a live trigger left a rebuild queued before the tick"
    def tick() -> list[bool]:
        # The periodic tick can run while the setup waits for a clean model. Measure only
        # this explicit tick, with no event-loop turn between the clock change and its call.
        counter.reset()
        flags.clear()
        clock[0] = new_clock
        screen._tick()
        return list(flags)

    judged = await rig.app_call(tick)
    assert judged == [needs_rebuild], (
        f"a painted row's right-hand fields {'changed' if needs_rebuild else 'kept'} width: the tick said {judged}")
    # The tick marks the model dirty; the coalesced rebuild runs at the next refresh of the screen, which may
    # already have happened inside this step, so the mark is either still pending or spent on a rebuild.
    assert (screen._fleet_dirty or counter.models >= 1) == needs_rebuild, (
        f"the tick marked the model {screen._fleet_dirty} and ran {counter.models} rebuilds")
    for _ in range(50):
        await rig.pilot.pause(0.01)
        if counter.models:
            break
    # The rows the tick left are the ones it re-laid; a row it marked for a rebuild is painted by the coalesced
    # rebuild at the next refresh of the screen.
    after_tick = _painted_rows(screen.sidebar)
    await rig.app_call(screen._sync_fleet_widgets)
    rows_after_rebuild = _painted_rows(screen.sidebar)
    assert rows_after_rebuild == after_tick, (
        f"a tick painted a row a rebuild at the same clock does not: {after_tick!r} vs {rows_after_rebuild!r}")


def _tick_matches_a_rebuild(serve, tmp_path, monkeypatch, records: list[dict], target: str,
                            cases: list[tuple[float, float, bool]]) -> None:
    """One case: a fleet whose painted row shows one clock, the clock moved to the other side of a width change,
    then the tick."""

    async def check(before: float, after: float, needs_rebuild: bool) -> None:
        _app, url = serve()
        counter = Counter()
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(120, 40)) as rig:
            clock = [before]
            rig.app.wall_clock = lambda: clock[0]
            rig.app.client = FakeSessionsClient(url, records)
            await rig.login(ME)
            screen = await painted(rig, target)
            for slot in screen.slots:
                if slot.pump is not None:
                    slot.pump.state._clock = lambda: clock[0]
            flags = _record_the_tick_flags(screen, counter, monkeypatch)
            await _tick_and_judge(rig, screen, flags, counter, clock, after, needs_rebuild)

    for before, after, needs_rebuild in cases:
        asyncio.run(check(before, after, needs_rebuild))


def test_a_clock_that_changes_a_row_s_width_is_painted_by_a_rebuild(serve, tmp_path, monkeypatch) -> None:
    """The countdown's width changes (9:59 to 10:00, 1d 0h to 23h 59m): the tick cannot re-lay the row on its stored
    prefix, so it marks the model dirty and the coalesced rebuild paints it. A change that keeps the width (1:00 to
    0:59) re-lays the row alone, with no rebuild. In every case the painted rows are what a full rebuild at the same
    clock paints."""
    fixed = 1_800_000_000.0
    for left, right, needs_rebuild in ((60, 59, False), (600, 599, True), (86400, 86399, True)):
        records = make_records(1, ending=True, ends_by="idle", ends_at=utc_stamp(fixed + left))
        _tick_matches_a_rebuild(serve, tmp_path, monkeypatch, records, "Needs you · 1",
                                [(fixed, fixed + (left - right), needs_rebuild)])


def test_the_out_age_clock_that_changes_width_is_painted_by_a_rebuild(serve, tmp_path,
                                                                       monkeypatch) -> None:
    """The last-output clock of a session open here that is not in front: `out 9s` to `out 10s` changes width, so the
    tick marks the model dirty; `out 10s` to `out 11s` keeps it, so the row is re-laid alone. The painted rows are
    what a full rebuild at the same clock paints."""
    fixed = 1_800_000_000.0

    async def run(age: float, next_age: float, needs_rebuild: bool) -> None:
        _app, url = serve()
        first = make_session(url, ME)
        second = make_session(url, ME)
        records = []
        for sid in (first, second):
            r = dict(BASE)
            r["id"] = sid
            r["target_id"] = f"rtr-{sid[-3:]}"
            r["state"] = "live"
            r["owner"] = ME
            r["control_holder"] = None
            r["clients"] = []
            r["recording"] = {"active": False}
            r["ending"] = False
            records.append(r)
        counter = Counter()
        # The wide sidebar (from `sidebar.wide_from` columns): a narrow row has no room for this clock at all, so
        # neither the tick nor a rebuild paints it.
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(160, 40)) as rig:
            clock = [fixed]
            rig.app.wall_clock = lambda: clock[0]
            rig.app.client = FakeSessionsClient(url, records)
            await rig.login(ME)
            await rig.attach(first)
            await rig.attach(second)  # the second is in front: only the first shows its last-output clock
            screen = rig.app.workspace()
            back = next(s for s in screen.slots if s.session_id == first)
            assert back is not screen._front
            # The clock the age is measured against, and the last output this TUI saw, set before the paint so the
            # row starts at `age`.
            for slot in screen.slots:
                slot.pump.state._clock = lambda: clock[0]
            back.pump.state.last_output_at = fixed - age
            screen.open_sidebar()
            screen = await painted(rig, "Open here · 2")
            flags = _record_the_tick_flags(screen, counter, monkeypatch)
            await _tick_and_judge(rig, screen, flags, counter, clock, fixed + (next_age - age), needs_rebuild)

    asyncio.run(run(9.5, 10.5, True))
    asyncio.run(run(10.5, 11.5, False))


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


def test_opening_the_sidebar_on_a_group_selects_from_the_current_rows(serve, tmp_path) -> None:
    """The rail's `▲n` opens the sidebar at Needs you and selects that group's first row. A poll that landed while
    the sidebar was closed added a session that needs you: opening must run the pending rebuild before it selects,
    so the selection is the new session and not a row of the list from before the poll."""

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
            screen = await painted(rig, "Yours and joinable · 1")
            screen.close_sidebar()
            screen._apply_poll(10, [*before, added])  # the fleet changes while the sidebar is closed
            screen.open_sidebar(GROUP_NEEDS_YOU, refresh=False)
            assert screen.sidebar.selected == (GROUP_NEEDS_YOU, "s.needs"), (
                f"opening on the group selected {screen.sidebar.selected} of the rows before the poll: "
                f"{screen.sidebar.text}")

    asyncio.run(check())


def test_a_relayout_after_a_tick_builds_its_rows_at_the_current_clock(serve, tmp_path) -> None:
    """The tick moves the clocks of the rows the sidebar paints. A relayout (the selection moves) builds those rows
    again: their countdowns must read the current clock, never the clock the model was built at, and they must be
    the rows a full rebuild at the same clock paints."""
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
            screen = await painted(rig, "Needs you · 2")
            assert "▲ 4:10" in screen.sidebar.text and "▲ 5:00" in screen.sidebar.text, screen.sidebar.text
            clock[0] = fixed + 60
            await rig.app_call(screen._tick)
            assert "▲ 3:10" in screen.sidebar.text and "▲ 4:00" in screen.sidebar.text, screen.sidebar.text
            for _ in range(2):
                await rig.app_call(screen.sidebar._move, 1)  # the selection moves: the painted rows are rebuilt
            after_relayout = _painted_rows(screen.sidebar)
            assert "▲ 3:10" in screen.sidebar.text and "▲ 4:00" in screen.sidebar.text, (
                f"a relayout after the tick built the rows at the model's old clock: {screen.sidebar.text}")
            await rig.app_call(screen._sync_fleet_widgets)
            assert _painted_rows(screen.sidebar) == after_relayout, (
                f"a relayout painted rows a rebuild at the same clock does not: {after_relayout!r} vs "
                f"{_painted_rows(screen.sidebar)!r}")

    asyncio.run(check())


def test_the_open_full_view_clocks_move_with_the_tick(serve, tmp_path) -> None:
    """The full view formats every row of its model, so while it is open the tick rebuilds that model at the new
    clock: a countdown the view shows advances with no poll. A closed view is never rebuilt by a tick."""
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
            screen = await painted(rig, "Needs you · 1")
            build = screen._fleet_view_model

            def counted(*args, **kw):
                builds[0] += 1
                return build(*args, **kw)

            screen._fleet_view_model = counted
            screen.close_sidebar()
            screen.open_fleet_view()
            for _ in range(200):
                if screen.fleet_view.layout.rows:
                    break
                await rig.pilot.pause(0.01)
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


def test_the_details_clocks_move_with_the_rows(serve, tmp_path) -> None:
    """A wide sidebar shows the selected session's details under the groups. The tick moves the rows' clocks; the
    details' own clocks (`output Ns ago`, `ends in …`) move with them, and what the tick paints is what a rebuild
    at the same clock paints."""
    fixed = 1_800_000_000.0

    async def check() -> None:
        _app, url = serve()
        first = make_session(url, ME)
        second = make_session(url, ME)
        records = []
        for sid, ending in ((first, True), (second, False)):
            r = dict(BASE)
            r["id"] = sid
            r["target_id"] = f"rtr-{sid[-3:]}"
            r["state"] = "live"
            r["owner"] = ME
            r["control_holder"] = None
            r["clients"] = []
            r["recording"] = {"active": False}
            r["ending"] = ending
            if ending:
                r["ends_by"] = "idle"
                r["ends_at"] = utc_stamp(fixed + 250)
            records.append(r)
        clock = [fixed]
        # The wide sidebar (from `sidebar.wide_from` columns), where the details show.
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("fleet_view.refresh_s=0",),
                       size=(160, 40)) as rig:
            rig.app.wall_clock = lambda: clock[0]
            rig.app.client = FakeSessionsClient(url, records)
            await rig.login(ME)
            await rig.attach(first)
            await rig.attach(second)  # the second is in front: only the first shows its last-output clock
            screen = rig.app.workspace()
            for slot in screen.slots:
                slot.pump.state._clock = lambda: clock[0]
            back = next(s for s in screen.slots if s.session_id == first)
            assert back is not screen._front
            # The warning window and the last output this TUI saw, set before the paint so the row and its details
            # start at the clock's time.
            back.pump.state.ending = True
            back.pump.state.ends_at = utc_stamp(fixed + 250)
            back.pump.state.ends_by = "idle"
            back.pump.state.last_output_at = fixed - 9.5
            screen.open_sidebar()
            screen = await painted(rig, "Open here · 2")
            await rig.app_call(screen.sidebar.select_group, GROUP_OPEN_HERE)
            assert screen.sidebar.selected == (GROUP_OPEN_HERE, first), screen.sidebar.selected
            assert "output 9s ago" in screen.sidebar.text, screen.sidebar.text
            assert "ends in 4:10 if nothing happens (idle)" in screen.sidebar.text, screen.sidebar.text
            clock[0] = fixed + 60
            await rig.app_call(screen._tick)
            assert "output 1m ago" in screen.sidebar.text, (
                f"the details' output clock froze while the rows moved: {screen.sidebar.text}")
            assert "ends in 3:10 if nothing happens (idle)" in screen.sidebar.text, screen.sidebar.text
            after_tick = screen.sidebar.text
            await rig.app_call(screen._sync_fleet_widgets)
            assert screen.sidebar.text == after_tick, (
                "the tick painted details a rebuild at the same clock does not")

    asyncio.run(check())
