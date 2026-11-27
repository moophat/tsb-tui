"""The unframed `sidebar_view` (the grouped list the side panel no longer draws): the clocks of the rows it paints
and of a selected session's details move with the tick, and what the tick paints is what a rebuild at the same
clock paints. The full view's own painted-window tests are in `test_fleet_view_painted_rows.py`.

Method: the real app, headless, on `drive_support.Rig`; the sidebar widget is run unframed over the function (see
the fixture), the way it painted before the side panel was reframed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from drive_support import Rig
from input_server import make_session
from tsb_contract_client import Client
from tsbc import _serve
from tsbc.schema import Registry

import tsb_tui.screens.terminal as terminal_screen
from tsb_tui import glyphs
from tsb_tui.model.fleet import GROUP_OPEN_HERE
from tsb_tui.widgets.sidebar import Sidebar, _prepare, sidebar_view

CONTRACT = Path(__file__).resolve().parents[1] / "contract"
REGISTRY = Registry.from_tree(CONTRACT)
BASE = json.loads((CONTRACT / "fixtures" / "C1" / "session-record-shape-v0.3.json")
                  .read_text(encoding="utf-8"))["case"]["instances"][0]["doc"]
ME = _serve.USER


@pytest.fixture(autouse=True)
def _the_unframed_sidebar_view(monkeypatch) -> None:
    """These tests are about the unframed `sidebar_view` (the grouped list and its painted window), which the side
    panel no longer draws (P2a: its Sessions section holds only what is open here). Until the full view builds rows
    for its painted window only, they keep testing the function: the widget runs it unframed, at the width and height
    it had, and `legacy_text` reads it the way the widget used to paint it."""
    original_refresh_times = Sidebar.refresh_times

    def build(self):
        needle = self.filter_text.strip()
        if self._prepared_for is None or self._prepared_for[1] is not self._model or self._prepared_for[0] != needle:
            self._prepared = _prepare(self._model, needle)
            self._prepared_for = (needle, self._model)
        return sidebar_view(
            self._model, width=max(self.sidebar_width - 1, 1), height=self.size.height or 40, selected=self.selected,
            filter_text=self.filter_text, editing=self.editing, wide=self.wide, show_details=self.details_shown,
            prepared=self._prepared)

    def refresh_times(self, times):
        self.sidebar_width += 3  # the framed widget re-lays rows 3 columns narrower than this function's width
        try:
            return original_refresh_times(self, times)
        finally:
            self.sidebar_width -= 3

    monkeypatch.setattr(Sidebar, "_build", build)
    monkeypatch.setattr(Sidebar, "refresh_times", refresh_times)
    monkeypatch.setattr(Sidebar, "sessions_height", property(lambda self: self.size.height or 40))


def legacy_text(sidebar) -> str:
    """The unframed view as the widget used to paint it: one line per row, the hairline at its right."""
    height = sidebar.size.height or len(sidebar.view.lines)
    lines = sidebar.view.lines[sidebar.scroll_top: sidebar.scroll_top + height]
    return "\n".join(glyphs.render(line.text, glyphs.UNICODE) + "│" for line in lines)


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
        if target in legacy_text(screen.sidebar):
            return screen
        await rig.pilot.pause(0.01)
    raise AssertionError(f"the sidebar never listed the fleet: {legacy_text(screen.sidebar)!r}")


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
            assert "output 9s ago" in legacy_text(screen.sidebar), legacy_text(screen.sidebar)
            assert "ends in 4:10 if nothing happens (idle)" in legacy_text(screen.sidebar), legacy_text(screen.sidebar)
            clock[0] = fixed + 60
            await rig.app_call(screen._tick)
            assert "output 1m ago" in legacy_text(screen.sidebar), (
                f"the details' output clock froze while the rows moved: {legacy_text(screen.sidebar)}")
            assert "ends in 3:10 if nothing happens (idle)" in legacy_text(screen.sidebar), legacy_text(screen.sidebar)
            after_tick = legacy_text(screen.sidebar)
            await rig.app_call(screen._sync_fleet_widgets)
            assert legacy_text(screen.sidebar) == after_tick, (
                "the tick painted details a rebuild at the same clock does not")

    asyncio.run(check())
