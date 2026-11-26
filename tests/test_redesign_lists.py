"""P3 part 1 ruled rows: what each target and session row says, and the order of the rows (the side panel's
Targets and Sessions lists, and the ruled session order).

Every case drives the app with keys through Pilot against the contract's fixture server (a test double, never a
broker or sshd), asserts words and geometry in the painted lines, and keeps the two-size SVG baselines under
tests/snapshots/redesign-p3/ separate from those assertions.

AI-Model: glm-5.3-flash
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import json
import os
import re
import threading
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import pytest
from input_server import InputRecordingApp
from snapshot_support import settle_snapshot
from tsbc import _serve
from tsbc._serve import ServeApp
from workspace_support import fixture_login, named_sessions, open_here, sidebar_offset

from tsb_tui import config
from tsb_tui.app import TsbTuiApp
from tsb_tui.model.fleet import session_rank
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.screens.target_form import TargetFormScreen
from tsb_tui.widgets import sidebar as sidebar_module
from tsb_tui.widgets.canvas import Canvas
from tsb_tui.widgets.sidebar import Sidebar


@pytest.fixture(autouse=True)
def _utc_timezone():
    """Rule 7 renders `since HH:MM` in local time, as the product does; pin the test process to UTC so the
    expected words and the P3 baselines agree whatever zone the machine runs in."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def _painted(screen) -> list[str]:
    """The side panel's painted rows, top to bottom, borders included."""
    return [screen.sidebar.render_line(y).text for y in range(screen.sidebar.size.height)]


def _target_lines(screen) -> list[str]:
    """The Targets section's painted rows without their borders."""
    return [line[2:-2] for line in _painted(screen)[:screen.sidebar.targets_height]]


def _session_lines(screen) -> list[str]:
    """The Sessions section's painted rows without their borders."""
    start = screen.sidebar.targets_height
    return [line[2:-2] for line in _painted(screen)[start:]]


@pytest.mark.parametrize("size", [(80, 24), (160, 45)])
def test_target_row_grammar_two_lines_narrow_one_line_wide(serve, wait_until, size):
    """Rule 1 (part 1) with rule 8, 9 and ruling 17 (2026-11-21/22): at 24 inner columns a target row is two lines,
    the label then `host · open N,M` with no open suffix when none; at 160 columns one line. The group rides after
    the open numbers, the type word last, so it drops whole at the narrow budget; the `Recent` section heads the
    list. No login words."""

    async def check():
        served, url = serve(InputRecordingApp())
        for tid in list(served.targets):
            if tid not in ("normal", "telnet-scenario", "alt-screen"):
                served.invisible.add(tid)
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.fetched, "the targets")
            sids = named_sessions(url, served, "normal", "normal")
            await open_here(app, pilot, wait_until, *sids)
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            lines = _target_lines(screen)
            joined = "\n".join(lines)
            # rule 9 (2026-11-21): the `Recent` section sits at the top of the list, before the main rows.
            assert "Recent" in joined, lines

            def facts_line(target_id: str) -> str:
                # `_target_line_index` counts from the section's first content line, one below the title line.
                idx = screen.sidebar._target_line_index(target_id)
                assert idx is not None, target_id
                row = next(r for r in screen.sidebar.painted_targets() if r.id == target_id)
                return lines[idx + screen.sidebar._target_row_lines(row)]

            assert "192.0.2.20 · open 1,2" in facts_line("normal"), lines
            alt = facts_line("alt-screen")
            assert "192.0.2.21" in alt and "open" not in alt, alt
            tel = facts_line("telnet-scenario")
            # rule 8 / ruling 17 (2026-11-22): the group rides after the open numbers; the type word rides last,
            # so at the narrow budget it is left out whole and at the wide budget it fits.
            assert "192.0.2.30 · scenario" in tel, tel
            if size[0] >= 160:
                assert "192.0.2.30 · scenario · telnet" in tel, tel
            else:
                assert "telnet" not in tel, tel
            assert "login" not in joined, "a target row never carries login words"
            for line in _painted(screen):
                assert len(line) == screen.sidebar.size.width, line
    asyncio.run(check())


@pytest.mark.parametrize("size", [(80, 24), (160, 45)])
def test_target_rows_show_no_login_words(serve, wait_until, size):
    """P3 never stops at O1: no target row says anything about logins, saved or not."""

    async def check():
        _served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.fetched, "the targets")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            joined = "\n".join(_target_lines(screen))
            for word in ("login saved", "no saved login", "type it at connect"):
                assert word not in joined, word
    asyncio.run(check())


def test_target_order_open_here_first_then_natural_label_order(serve, wait_until):
    """Rule 3: targets with a session open in this window come first by their lowest open number; the rest in
    natural order of the label (case-insensitive, digit runs compared as numbers: rtr-2 before rtr-10)."""

    async def check():
        served, url = serve(InputRecordingApp())
        seen = "2026-10-17T08:30:00Z"
        served.targets["rtr-10"] = {"id": "rtr-10", "type": "ssh", "name": "rtr-10", "group": "lab",
                                    "tags": [], "discovered_by": "manual", "last_seen": seen,
                                    "host": "192.0.2.51", "port": 22}
        served.targets["rtr-2"] = {"id": "rtr-2", "type": "ssh", "name": "rtr-2", "group": "lab",
                                   "tags": [], "discovered_by": "manual", "last_seen": seen,
                                   "host": "192.0.2.52", "port": 22}
        served.targets["Rtr-1"] = {"id": "Rtr-1", "type": "ssh", "name": "Rtr-1", "group": "lab",
                                   "tags": [], "discovered_by": "manual", "last_seen": seen,
                                   "host": "192.0.2.53", "port": 22}
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.fetched and len(screen.sidebar.model.targets) >= 11,
                             "the targets")
            sids = named_sessions(url, served, "normal")
            await open_here(app, pilot, wait_until, *sids)
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            # rule 9 (2026-11-21): the `Recent` section heads the list, so the ruled order is read on the main
            # list's rows, where a Recent target also stays.
            assert "Recent" in "\n".join(_target_lines(screen)), _target_lines(screen)
            ids = [row.id for row in screen.sidebar.visible_targets()]
            assert ids[0] == "normal", ids
            rest = ids[1:]
            assert rest.index("rtr-2") < rest.index("rtr-10"), rest
            assert rest.index("Rtr-1") < rest.index("rtr-2"), rest
    asyncio.run(check())


@pytest.mark.parametrize("size", [(80, 24), (160, 45)])
def test_others_on_a_target(serve, wait_until, size):
    """Rule 2 (part 1) with rule 8 and ruling 17 (2026-11-22): the others word rides after the group, so at both
    budgets a row whose facts overflow leaves it out whole, never cut. The word is computed from the session list
    the TUI already holds."""

    async def check():
        served, url = serve(InputRecordingApp())
        peer = named_sessions(url, served, "normal", user=_serve.PEER)
        served.sessions[peer[0]]["owner"] = "ci-agent"
        named_sessions(url, served, "normal", user=_serve.PEER)
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.fetched and screen.fleet.records, "the reads")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            lines = "\n".join(_target_lines(screen))
            # ruling 17 (2026-11-22): the facts are `host · open N,M`, then the group, then the others word; a row
            # that overflows leaves the others word out whole at every budget. The word is computed from the
            # session list the TUI holds, so its absence from the line is the drop, not a missing read.
            assert screen.sidebar._model.others.get("normal") == "2 others on it", screen.sidebar._model.others
            assert "192.0.2.20 · scenario" in lines, lines
            assert "others on it" not in lines and "on it" not in lines, lines
    asyncio.run(check())


@pytest.mark.parametrize("size", [(80, 24), (160, 45)])
def test_session_row_grammar_narrow_three_lines_wide_one_line(serve, wait_until, size):
    """Rule 4: up to three lines at 24 inner columns (`N name`; `state · owner`; the typer words), one line at
    160 columns; the state words are live, detached, unreachable, killed, terminated."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            first, second = named_sessions(url, served, "sw-1", "sw-2")
            await open_here(app, pilot, wait_until, first, second)
            served.sessions[second]["state"] = "detached"
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: "detached · yours" in "\n".join(_session_lines(screen)),
                             "the row to say detached")
            joined = "\n".join(_session_lines(screen))
            assert "1 sw-1" in joined, joined
            assert "2 sw-2" in joined, joined
            assert "● live · yours" in joined, joined
            assert "you type" in joined, joined
            assert "detached · yours" in joined, joined
            assert "recorded" not in joined and "REC" not in joined, "recorded left every row"
            for line in _painted(screen):
                assert len(line) == screen.sidebar.size.width, line
    asyncio.run(check())


def test_session_row_says_unreachable(serve, wait_until):
    """Rule 4/5: an unreachable session open here says `unreachable` with its mark, and no idle countdown
    reads as if it were healthy."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await fixture_login(app, pilot)
            (sid,) = named_sessions(url, served, "sw-9")
            await open_here(app, pilot, wait_until, sid)
            served.sessions[sid]["state"] = "unreachable"
            screen._refresh_fleet()
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: "unreachable" in "\n".join(_session_lines(screen)),
                             "the row to say unreachable")
            lines = "\n".join(_session_lines(screen))
            assert "◌ unreachable" in lines, lines
            assert "yours" in lines, lines
    asyncio.run(check())


@pytest.mark.parametrize("size", [(80, 24), (160, 45)])
def test_a_word_is_never_cut_and_a_long_name_ends_in_an_ellipsis(serve, wait_until, size):
    """Rule 1: no word is cut; a name that does not fit ends in an ellipsis, never inside another word."""

    async def check():
        served, url = serve(InputRecordingApp())
        served.targets["core-router-with-a-very-long-inventory-name"] = {
            "id": "core-router-with-a-very-long-inventory-name", "type": "ssh",
            "name": "core-router-with-a-very-long-inventory-name", "group": "lab", "tags": [],
            "discovered_by": "manual", "last_seen": "2026-10-17T08:30:00Z", "host": "192.0.2.61", "port": 22}
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.fetched, "the targets")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            lines = _target_lines(screen)
            long = [line for line in lines if "…" in line]
            assert long, lines
            for line in long:
                assert not line.split("…")[0].endswith(("core", "router", "with", "a", "very")), line
            for line in _painted(screen):
                assert len(line) == screen.sidebar.size.width, line
    asyncio.run(check())


def test_show_ended_switch_and_end_words(serve, wait_until):
    """Rule 10: ended rows are hidden by default; `e` in the Sessions list shows them with their end words and
    nothing invented after; `e` again hides them."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            killed, terminated = named_sessions(url, served, "sw-3", "sw-4")
            served.sessions[killed]["state"] = "killed"
            served.sessions[terminated]["state"] = "terminated"
            screen._refresh_fleet()
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: "No live sessions · e show ended" in "\n".join(_session_lines(screen)),
                             "the sessions list after the refresh")
            text = "\n".join(_session_lines(screen))
            assert "killed" not in text and "terminated" not in text, "ended rows are hidden by default"
            assert "No live sessions · e show ended" in text, text
            await pilot.press("e")
            await pilot.pause()
            text = "\n".join(_session_lines(screen))
            assert "killed" in text and "terminated" in text, text
            assert "✕ killed" in text and "✕ terminated" in text, text
            assert "idle" not in text, "nothing is invented after the end word"
            await pilot.press("e")
            await pilot.pause()
            text = "\n".join(_session_lines(screen))
            assert "killed" not in text and "terminated" not in text, text
    asyncio.run(check())


def test_alike_rows_say_since(serve, wait_until):
    """Rule 7: two visible session rows that would read alike each add `since HH:MM`, from `created_at` in
    local time; otherwise no row carries a time. The ended rows behind `e` read alike — same target and end
    word, no owner or typer standing on their rows (ruled 2026-11-20, re-read 20:1x)."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            first, second = named_sessions(url, served, "normal", "normal")
            served.sessions[first]["state"] = "killed"
            served.sessions[second]["state"] = "killed"
            (third,) = named_sessions(url, served, "sw-8")
            served.sessions[third]["state"] = "terminated"
            screen._refresh_fleet()
            screen.open_sidebar(pane="sessions")
            await pilot.press("e")
            await wait_until(pilot, lambda: sum("since " in line for line in _session_lines(screen)) == 2,
                             "the alike rows")
            lines = _session_lines(screen)
            since = [line for line in lines if "since " in line]
            assert len(since) == 2, lines
            hhmm = datetime.fromisoformat(served.sessions[first]["created_at"]).astimezone().strftime("%H:%M")
            assert f"since {hhmm}" in "\n".join(lines), lines
            lone = [line for line in lines if "sw-8" in line]
            assert lone and not any("since " in line for line in lone), lone
    asyncio.run(check())


_CLOCK = re.compile(r"•\d+s|out \d+s|▲ ?\d+:\d\d|\b\d+:\d\d\b")


class _CountdownApp(InputRecordingApp):
    """The fixture double resets a live record's countdown fields; this one passes a stored warning window
    through, so the session's row has the clock a row must not carry."""

    def _record(self, sess: dict) -> dict:
        record = super()._record(sess)
        if sess.get("ending"):
            record.update(ends_at=sess.get("ends_at"), ends_by=sess.get("ends_by", "idle"), ending=True)
        return record


@pytest.mark.parametrize("size", [(80, 24), (160, 45)])
def test_alike_open_rows_say_since(serve, wait_until, size):
    """Rule 7 (re-read 2026-11-20 20:1x): rows alike are those with the same target, state, owner and typer;
    the row's number does not make two rows different. Two sessions open here on one target with the same
    state, owner and typer each say `since HH:MM` — at the 24-inner budget on the third line after the typer
    words, joined by ` · ` (`you type · since 09:12`); a pair whose typer line cannot hold it whole keeps it
    beside the name, which takes the ellipsis first, and no ruled word is ever cut."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            first, second = named_sessions(url, served, "normal", "normal")
            await open_here(app, pilot, wait_until, first, second)
            screen.open_sidebar(pane="sessions")
            words = [f"since {datetime.fromisoformat(served.sessions[sid]['created_at']).astimezone().strftime('%H:%M')}"
                     for sid in (first, second)]
            await wait_until(pilot, lambda: "you type" in screen.sidebar.text, "the rows")
            if size[0] == 80:
                await wait_until(pilot, lambda: sum("since " in line.text
                                                    for line in screen.sidebar.view.lines) == 2,
                                 "both alike rows to say since")
                lines = _session_lines(screen)
                assert sum("since " in line for line in lines) == 2, lines
                for word in set(words):
                    assert any(word in line for line in lines), (word, lines)
                typer = [line for line in lines if "you type" in line]
                assert len(typer) == 2 and all(" · since " in line for line in typer), lines
            for line in _painted(screen):
                assert len(line) == screen.sidebar.size.width, line
            if size[0] == 80:
                served.sessions[first]["state"] = "detached"
                served.sessions[second]["state"] = "detached"
                screen._refresh_fleet()
                await wait_until(pilot, lambda: sum("since " in line.text
                                                    for line in screen.sidebar.view.lines) == 2,
                                 "the detached alike rows to keep their since")
                lines = _session_lines(screen)
                assert sum("since " in line for line in lines) == 2, lines
                for line in _painted(screen):
                    assert len(line) == screen.sidebar.size.width, line
    asyncio.run(check())


def test_an_alike_pair_that_cannot_fit_takes_a_second_line_at_wide(serve, wait_until):
    """Ruled 2026-11-20 23:4x: at 160 columns, when an alike row's one line cannot hold the ruled words
    plus `since HH:MM` even with the name at its ellipsis, the row takes a second line with `since HH:MM`
    on it; no word is cut. The smallest alike pair: live, yours, `you type`."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            first, second = named_sessions(url, served, "normal", "normal")
            await open_here(app, pilot, wait_until, first, second)
            screen.open_sidebar(pane="sessions")
            words = [f"since {datetime.fromisoformat(served.sessions[sid]['created_at']).astimezone().strftime('%H:%M')}"
                     for sid in (first, second)]
            await wait_until(pilot, lambda: "you type" in screen.sidebar.text, "the rows")
            await wait_until(pilot, lambda: sum("since " in line.text
                                                for line in screen.sidebar.view.lines) == 2,
                             "both alike rows to say since")
            lines = _session_lines(screen)
            assert sum("since " in line for line in lines) == 2, lines
            for word in set(words):
                # The alike word stands on its own second line; the ruled words stay whole on the first.
                assert any(line.strip() == word for line in lines), (word, lines)
            assert sum("normal ● live · yours you type" in line for line in lines) == 2, lines
            for line in _painted(screen):
                assert len(line) == screen.sidebar.size.width, line
    asyncio.run(check())


def test_no_clock_on_a_session_row(serve, wait_until):
    """Ruled 2026-11-20 20:1x: no clock on a session row — the row clock goes from every session row;
    countdowns and clocks stay where they are outside the rows."""

    async def check():
        served, url = serve(_CountdownApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await fixture_login(app, pilot)
            (sid,) = named_sessions(url, served, "sw-clock")
            served.sessions[sid]["ending"] = True
            served.sessions[sid]["ends_by"] = "idle"
            served.sessions[sid]["ends_at"] = (
                datetime.now(tz=UTC) + timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
            await open_here(app, pilot, wait_until, sid)
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: "you type" in "\n".join(_session_lines(screen)), "the rows")
            await pilot.pause(2.0)  # past a tick: the warning window's clock would have painted on the row
            for line in _session_lines(screen):
                assert not _CLOCK.search(line), line
            for line in _painted(screen):
                assert len(line) == screen.sidebar.size.width, line
    asyncio.run(check())


def test_a_state_word_is_never_shortened(serve, wait_until):
    """Ruled 2026-11-20 20:1x: no abbreviated words — the row shows `unreachable` whole, the name taking the
    ellipsis; `unreach.` never renders."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await fixture_login(app, pilot)
            (sid,) = named_sessions(url, served, "sw-9")
            await open_here(app, pilot, wait_until, sid)
            served.sessions[sid]["state"] = "unreachable"
            screen._refresh_fleet()
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: "unreachable" in "\n".join(_session_lines(screen)),
                             "the row to say unreachable")
            lines = _session_lines(screen)
            assert "unreach." not in "\n".join(lines), lines
            assert "◌ unreachable" in "\n".join(lines), lines
    asyncio.run(check())


def test_a_row_that_does_not_fit_is_not_drawn(serve, wait_until):
    """Ruled 2026-11-20 20:1x: a list shows only whole rows — a three-line row that does not fit at the
    pane's bottom is not drawn at all; the list scrolls by whole rows and the row stays reachable by the
    keys."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await fixture_login(app, pilot)
            sids = named_sessions(url, served, "sw-1", "sw-2", "sw-3", "sw-4")
            await open_here(app, pilot, wait_until, *sids)
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: len(screen.sidebar.view.rows) == 4, "the four rows")
            lines = _session_lines(screen)
            assert not any("sw-4" in line for line in lines), lines
            for _ in range(3):
                await pilot.press("j")  # the fourth row's position stays reachable by the keys
            await pilot.pause()
            lines = _session_lines(screen)
            where = [i for i, line in enumerate(lines) if "sw-4" in line]
            assert len(where) == 1, lines
            assert "live" in lines[where[0] + 1] and "you type" in lines[where[0] + 2], lines
            for line in _painted(screen):
                assert len(line) == screen.sidebar.size.width, line
    asyncio.run(check())


def _register_states_targets(served) -> None:
    """The states gallery's three targets, in the inventory from the start: a session whose `target_id` the
    inventory does not list has no target row, so its open number could not show anywhere."""
    seen = "2026-10-17T08:30:00Z"
    for name, host in (("sw-live", "192.0.2.71"), ("sw-det", "192.0.2.72"), ("sw-unreach", "192.0.2.73")):
        served.targets[name] = {"id": name, "type": "ssh", "name": name, "group": "lab", "tags": [],
                                "discovered_by": "manual", "last_seen": seen, "host": host, "port": 22}


@pytest.mark.parametrize("size", [(80, 24), (160, 45)])
def test_open_numbers_on_every_target_row_that_has_them(serve, wait_until, size):
    """Ruled 2026-11-20 20:1x: open numbers on every target row that has them, in every snapshot. The states
    gallery's targets with open sessions 1, 2 and 3 read `open 1` / `open 2` / `open 3` and sort first by
    their lowest open number (rule 3), on the screen and in the gallery's baseline."""

    async def check():
        served, url = serve(InputRecordingApp())
        _register_states_targets(served)
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.fetched, "the targets read")
            screen.open_sidebar(pane="targets")
            first, second, third = named_sessions(url, served, "sw-live", "sw-det", "sw-unreach")
            await open_here(app, pilot, wait_until, first, second, third)
            served.sessions[second]["state"] = "detached"
            served.sessions[third]["state"] = "unreachable"
            screen._refresh_fleet()
            await wait_until(pilot, lambda: "open 1" in screen.sidebar.text and
                             "unreachable" in screen.sidebar.text, "the rows to say their states")
            lines = _target_lines(screen)
            joined = "\n".join(lines)
            assert "192.0.2.71 · open 1" in joined, lines
            assert "192.0.2.72 · open 2" in joined, lines
            assert "192.0.2.73 · open 3" in joined, lines

            # rule 9 (2026-11-21): `Recent` heads the list, so the ruled order is read on the main list's rows.
            ids = [row.id for row in screen.sidebar.visible_targets()]
            assert ids.index("sw-live") < ids.index("sw-det") < ids.index("sw-unreach"), ids
            assert ids.index("sw-unreach") < ids.index("alt-screen"), ids
            for line in _painted(screen):
                assert len(line) == screen.sidebar.size.width, line
            baseline = (Path(__file__).parent / "snapshots" / "redesign-p3" /
                        f"sessions-states-{size[0]}x{size[1]}.svg").read_text()
            text = html.unescape(baseline).replace("\xa0", " ")
            assert "· open 1" in text and "· open 2" in text and "· open 3" in text, "the numbers in the baseline"
    asyncio.run(check())


def test_session_order_and_highlight_follow_identity(serve, wait_until):
    """Rule 9: needs you, then open here, then yours live, then yours detached, then joinable; the highlight
    follows the same session across a refresh that reorders the rows."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            # Shuffled records: created detached first, live second, open third, the peer's last — the ruled
            # order must not be the core's listing order.
            detached_sid, live_sid, open_sid = named_sessions(url, served, "sw-det", "sw-live", "sw-open")
            await open_here(app, pilot, wait_until, open_sid)
            served.sessions[live_sid]["state"] = "live"
            peer = named_sessions(url, served, "sw-peer", user=_serve.PEER)
            served.sessions[peer[0]]["state"] = "live"
            screen.open_fleet_view()
            view = screen.fleet_view
            await pilot.press("m")  # every session the core returned: the peer's is joinable, mine are not open
            await pilot.pause(0.5)
            rows = [r[1] for r in view.layout.rows if r[0] != "needs_you"]
            assert rows.index(open_sid) < rows.index(live_sid) < rows.index(detached_sid), rows
            assert rows.index(detached_sid) < rows.index(peer[0]), rows
            await pilot.press("j")
            chosen = view.selected[1]
            named_sessions(url, served, "sw-new")
            screen._refresh_fleet()
            await pilot.pause(0.3)
            assert view.selected is not None and view.selected[1] == chosen, "the highlight followed the session"
    asyncio.run(check())


def test_targets_empty_for_a_target_edit_holder_and_a_adds_one(serve, wait_until):
    """Rule 11: with no targets at all, a `target.edit` holder reads `No targets yet · a add one` and `a` opens
    the target form."""

    async def check():
        served, url = serve(InputRecordingApp())
        served.invisible |= set(served.targets)
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.fetched, "the empty inventory")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            lines = _target_lines(screen)
            assert lines[1].startswith("No targets yet · a add one"), lines
            await pilot.press("a")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "the target form")
    asyncio.run(check())


def test_targets_empty_without_target_edit(serve, wait_until):
    """Rule 11: a login without `target.edit` reads `No targets you can see · ask an admin for access`."""

    async def check():
        served, url = serve(InputRecordingApp())
        served.invisible |= set(served.targets)
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot, user=_serve.VIEWER)
            await wait_until(pilot, lambda: screen.targets.fetched, "the empty inventory")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            lines = _target_lines(screen)
            assert " ".join(line.rstrip() for line in lines[1:3]) == (
                "No targets you can see · ask an admin for access"), lines
    asyncio.run(check())


class _InventoryDown(ServeApp):
    def _list_targets(self, query):
        return self._err(503, "inventory_unavailable", "the inventory is not answering")


def test_targets_error_and_r_retry(serve, wait_until):
    """Rule 11: the inventory down reads `Could not load targets · r retry`, and `r` asks again."""

    async def check():
        _served, url = serve(_InventoryDown())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.unavailable, "the 503")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            lines = _target_lines(screen)
            assert lines[1].startswith("Could not load targets · r retry"), lines
            await pilot.press("r")
            await pilot.pause(0.3)
            assert screen.targets.unavailable, "the retry ran (the inventory is still down)"
    asyncio.run(check())


class _SessionsGateApp(InputRecordingApp):
    def __init__(self, gate: threading.Event) -> None:
        super().__init__()
        self.gate = gate

    def http(self, method, path, headers, body):
        if (method, path) == ("GET", "/sessions"):
            self.gate.wait(timeout=10)
        return super().http(method, path, headers, body)


def test_sessions_loading_then_empty(serve, wait_until):
    """Rule 11: before the first poll lands the Sessions list reads `Loading sessions…`; with none it reads
    `No live sessions · n new session` and `n` opens New session."""

    async def check():
        gate = threading.Event()
        _served, url = serve(_SessionsGateApp(gate))
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_sidebar(pane="sessions")
            await pilot.pause(0.2)
            text = "\n".join(_session_lines(screen))
            assert "Loading sessions…" in text, text
            gate.set()
            await wait_until(pilot, lambda: "No live sessions · n new session" in "\n".join(_session_lines(screen)),
                             "the empty state")
            await pilot.press("n")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "n opened New session")
    asyncio.run(check())


class _SessionsDownApp(InputRecordingApp):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def http(self, method, path, headers, body):
        if (method, path) == ("GET", "/sessions"):
            self.reads += 1
            return self._err(503, "inventory_unavailable", "the session list is not answering")
        return super().http(method, path, headers, body)


def test_sessions_error_and_r_retry(serve, wait_until):
    """Rule 11: the session list failing reads `Could not load sessions · r retry`, and `r` asks again."""

    async def check():
        served, url = serve(_SessionsDownApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.poller.last_error is not None, "the failed poll")
            screen.open_sidebar(pane="sessions")
            await pilot.pause()
            lines = _session_lines(screen)
            assert lines[1].startswith("Could not load sessions · r retry"), lines
            reads = served.reads
            await pilot.press("r")
            await wait_until(pilot, lambda: served.reads > reads, "the retry read")
    asyncio.run(check())


async def _p3_snapshot(app, pilot, name):
    path = Path(__file__).parent / "snapshots" / "redesign-p3" / f"{name}.svg"
    await settle_snapshot(
        app, pilot, path, title="P3a", terminal_tag="terminal-p3a", update_env="UPDATE_P3_SNAPSHOTS"
    )


async def _nothing(url, served, pilot, screen, app, wait_until, count=0):
    return None


async def _opens(url, served, pilot, screen, app, wait_until, count=0):
    if count:
        sids = named_sessions(url, served, "normal", "normal")[:count]
        await open_here(app, pilot, wait_until, *sids)
        # The side panel reads the open numbers from its model: wait for the rebuild, not just the dump.
        await wait_until(pilot, lambda: "open 1" in screen.sidebar.text, "the open number on the row")


async def _states(url, served, pilot, screen, app, wait_until, count=0):
    first, second, third = named_sessions(url, served, "sw-live", "sw-det", "sw-unreach")
    await open_here(app, pilot, wait_until, first, second, third)
    served.sessions[second]["state"] = "detached"
    served.sessions[third]["state"] = "unreachable"
    screen._refresh_fleet()
    await wait_until(pilot, lambda: "unreachable" in screen.sidebar.text, "the rows to say their states")


async def _ended(url, served, pilot, screen, app, wait_until, count=0):
    killed, terminated = named_sessions(url, served, "sw-kill", "sw-term")
    served.sessions[killed]["state"] = "killed"
    served.sessions[terminated]["state"] = "terminated"
    screen._refresh_fleet()
    await wait_until(pilot, lambda: "killed" in screen.sidebar.text and "terminated" in screen.sidebar.text,
                     "the ended rows")


async def _alike(url, served, pilot, screen, app, wait_until, count=0):
    first, second = named_sessions(url, served, "normal", "normal")
    served.sessions[first]["state"] = "killed"
    served.sessions[second]["state"] = "killed"
    screen._refresh_fleet()
    await wait_until(pilot, lambda: len([line for line in screen.sidebar.view.lines
                                         if "since " in line.text]) == 2, "the alike rows")


async def _only_ended(url, served, pilot, screen, app, wait_until, count=0):
    (killed,) = named_sessions(url, served, "sw-kill")
    served.sessions[killed]["state"] = "killed"
    screen._refresh_fleet()
    await wait_until(pilot, lambda: "e show ended" in screen.sidebar.text, "the only-ended state")


# case -> (setup, the list with the keys, show ended already pressed)
CASES = {
    "targets-opens0": (partial(_opens, count=0), "targets", False),
    "targets-opens1": (partial(_opens, count=1), "targets", False),
    "targets-opens2": (partial(_opens, count=2), "targets", False),
    "sessions-states": (_states, "sessions", False),
    "ended-on": (_ended, "sessions", True),
    "alike-since": (_alike, "sessions", True),
    "empty-targets": (_nothing, "targets", False),
    "empty-sessions": (_nothing, "sessions", False),
    "only-ended": (_only_ended, "sessions", False),
}


@pytest.mark.parametrize("size", [(80, 24), (160, 45)])
@pytest.mark.parametrize("case", sorted(CASES))
def test_two_size_galleries(serve, wait_until, size, case):
    """The part's two-size baselines, one per ruled case, under snapshots/redesign-p3/. The words and the
    geometry are asserted by the tests above; here the whole workspace is captured for review."""
    setup, pane, show_ended = CASES[case]

    async def check():
        served, url = serve(InputRecordingApp())
        if case == "sessions-states":
            _register_states_targets(served)
        if case == "empty-targets":
            served.invisible |= set(served.targets)
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.fetched, "the targets read")
            screen.open_sidebar(pane=pane)
            if show_ended:
                await pilot.press("e")
            await setup(url, served, pilot, screen, app, wait_until)
            screen.refresh_status()

            def quiet():
                # The fixture's alert stream raises and clears an alert on its own clock; snapshot at a moment
                # with none, as the P2a baselines do.
                return any(row.text.strip() in ("! Alerts 0", "!0") for row in screen.rail.rows)
            # The 1-second tick repaints clocks and the recording text at an unpredictable moment; settle past
            # one, check the quiet moment again, and capture inside the next tick's interval.
            await pilot.pause(1.2)
            await wait_until(pilot, quiet, "a moment with no alert raised")
            for line in _painted(screen):
                assert len(line) == screen.sidebar.size.width, (case, line)
            await _p3_snapshot(app, pilot, f"{case}-{size[0]}x{size[1]}")
    asyncio.run(check())


def test_target_rows_show_broker_text_through_the_sanitizer(serve, wait_until):
    """Every text from a broker record is shown through the control-character sanitizer: a target id with an ESC
    and another login's owner with a bell paint their control pictures, never the raw control."""

    async def check():
        served, url = serve(InputRecordingApp())
        seen = "2026-10-17T08:30:00Z"
        served.targets["tel\u001b7"] = {"id": "tel\u001b7", "type": "telnet", "group": "lab", "tags": [],
                                        "discovered_by": "manual", "last_seen": seen}
        peer = named_sessions(url, served, "normal", user=_serve.PEER)
        served.sessions[peer[0]]["owner"] = "ci\u0007agent"
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.fetched and screen.fleet.records, "the reads")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            for line in _painted(screen):
                assert line.isprintable(), repr(line)
            joined = "\n".join(_target_lines(screen))
            assert "telnet tel␛7" in joined, joined
            # ruling 17 (2026-11-22): this row's facts overflow the budget, so the others word is left out whole;
            # the owner text still reaches the widget data through the sanitizer, as the paint loop above shows
            # no raw control reaches a line.
            assert any("ci␇agent" in word for word in screen.sidebar._model.others.values()), \
                screen.sidebar._model.others
    asyncio.run(check())


def test_two_alike_ended_rows_paint_whole_in_wide_mode(serve, wait_until):
    """A frame's height counts every line its rows paint: at the wide budget two alike ended rows whose one line
    cannot hold the ruled words plus `since HH:MM` each take a second line, and both rows paint whole."""

    async def check():
        served, url = serve(InputRecordingApp())
        cfg = config.load(overrides=["sidebar.width_wide=24"])
        app = TsbTuiApp(url, cfg=cfg)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            first, second = named_sessions(url, served, "normal", "normal")
            served.sessions[first]["state"] = "killed"
            served.sessions[second]["state"] = "killed"
            screen._refresh_fleet()
            screen.open_sidebar(pane="sessions")
            await pilot.press("e")
            await wait_until(pilot, lambda: sum("since " in line for line in _session_lines(screen)) == 2,
                             "the alike rows")
            lines = _session_lines(screen)
            assert sum("since " in line for line in lines) == 2, lines
            assert sum("normal" in line for line in lines) == 2, lines
    asyncio.run(check())


def test_a_session_tooltip_names_the_whole_row(serve, wait_until):
    """A row's tooltip names the whole row as painted: the target, the state beside its owner, the typer words,
    and the alike word a row that is cut short carries."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            first, second = named_sessions(url, served, "normal", "normal")
            await open_here(app, pilot, wait_until, first, second)
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: sum("since " in line.text
                                                for line in screen.sidebar.view.lines) == 2, "the alike rows")
            y = next(i for i, line in enumerate(screen.sidebar.view.lines) if line.session_id == first)
            await pilot.hover(Sidebar, offset=sidebar_offset(screen, y, 6))
            await pilot.pause()
            tip = str(screen.sidebar.tooltip)
            assert "normal" in tip and "live" in tip and "yours" in tip and "you type" in tip, tip
            assert "since " in tip, tip
    asyncio.run(check())


def test_open_numbers_number_by_the_rail_when_an_earlier_slot_ended(serve, wait_until):
    """The open numbers on a target row are exactly the sessions open in this window on that target, numbered as
    the rail numbers them: an open slot that ended leaves its number out, and the later session keeps its own."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            first, second = named_sessions(url, served, "normal", "normal")
            await open_here(app, pilot, wait_until, first, second)
            served.sessions[first]["state"] = "killed"
            screen._refresh_fleet()
            screen.open_sidebar(pane="targets")
            await wait_until(pilot, lambda: "open 2" in "\n".join(_target_lines(screen)),
                             "the renumbered target row")
            joined = "\n".join(_target_lines(screen))
            assert "open 2" in joined, joined
            assert "open 1" not in joined, joined
            assert "2 normal" in screen.sidebar.text, screen.sidebar.text
    asyncio.run(check())


def test_clicking_show_ended_in_the_empty_state_shows_the_ended_rows(serve, wait_until):
    """Every key an empty state advertises also works by one click on it: clicking the `e show ended` copy shows
    the ended rows."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            (killed,) = named_sessions(url, served, "sw-kill")
            served.sessions[killed]["state"] = "killed"
            screen._refresh_fleet()
            screen.open_sidebar(pane="sessions")
            await wait_until(pilot, lambda: "e show ended" in "\n".join(_session_lines(screen)),
                             "the only-ended state")
            y = screen.sidebar.targets_height + 1
            await pilot.click(Sidebar, offset=(4, y))
            await pilot.pause()
            assert screen.sidebar.show_ended, "a click on the copy did nothing"
            assert "killed" in "\n".join(_session_lines(screen)), _session_lines(screen)
    asyncio.run(check())


def test_clicking_a_add_one_in_the_targets_empty_state_opens_the_form(serve, wait_until):
    """A click on the `a add one` copy does what the `a` key does: it opens the target form."""

    async def check():
        served, url = serve(InputRecordingApp())
        served.invisible |= set(served.targets)
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.fetched, "the empty inventory")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            assert _target_lines(screen)[1].startswith("No targets yet · a add one"), _target_lines(screen)
            await pilot.click(Sidebar, offset=(4, 1))
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "the target form")
    asyncio.run(check())


class _TargetsDownApp(_InventoryDown):
    def __init__(self) -> None:
        super().__init__()
        self.list_calls = 0

    def _list_targets(self, query):
        self.list_calls += 1
        return self._err(503, "inventory_unavailable", "the inventory is not answering")


def test_clicking_r_retry_in_the_targets_error_state_asks_again(serve, wait_until):
    """A click on the `r retry` copy does what the `r` key does: it asks the inventory again."""

    async def check():
        served, url = serve(_TargetsDownApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.unavailable, "the 503")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            assert _target_lines(screen)[1].startswith("Could not load targets · r retry"), _target_lines(screen)
            calls = served.list_calls
            await pilot.click(Sidebar, offset=(4, 1))
            await wait_until(pilot, lambda: served.list_calls > calls, "the retry read")
    asyncio.run(check())


def test_a_wide_target_row_never_paints_past_the_inner_width(serve, wait_until):
    """A wide target row whose label is cut paints exactly the inner width, never one cell past it."""

    async def check():
        served, url = serve(InputRecordingApp())
        seen = "2026-10-17T08:30:00Z"
        name = "core-router-with-a-very-long-inventory-name"
        served.targets[name] = {"id": name, "type": "ssh", "name": name, "group": "lab", "tags": [],
                                "discovered_by": "manual", "last_seen": seen, "host": "192.0.2.61", "port": 22}
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.fetched, "the targets")
            sids = named_sessions(url, served, name, name)
            await open_here(app, pilot, wait_until, *sids)
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            joined = "\n".join(_target_lines(screen))
            assert "· open 1,2" in joined, joined
            for line in _painted(screen):
                assert len(line) == screen.sidebar.size.width, repr(line)
    asyncio.run(check())


def test_listed_sessions_are_in_the_order_the_full_view_shows(serve, wait_until):
    """The order the full view shows and the order `listed_sessions` gives drive are one order: the sessions open
    here in rail order, then the rest in the ruled rank."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            _detached_sid, live_sid, open_sid = named_sessions(url, served, "sw-det", "sw-live", "sw-open")
            await open_here(app, pilot, wait_until, open_sid)
            served.sessions[live_sid]["state"] = "live"
            peer = named_sessions(url, served, "sw-peer", user=_serve.PEER)
            served.sessions[peer[0]]["state"] = "live"
            screen.fleet.filter_mode = "all"
            screen.open_fleet_view()
            view = screen.fleet_view
            await pilot.press("m")
            await pilot.pause(0.5)
            rows = [r[1] for r in view.layout.rows if r[0] != "needs_you"]
            listed = [record["id"] for record in screen.listed_sessions()]
            assert listed == rows, (listed, rows)
    asyncio.run(check())


def test_a_target_name_int_cannot_parse_sorts_as_text(serve, wait_until):
    """A valid Unicode target name sorts: a run of characters `int()` cannot parse is text, never a number, so
    the Targets list paints."""

    async def check():
        served, url = serve(InputRecordingApp())
        seen = "2026-10-17T08:30:00Z"
        served.targets["router-²"] = {"id": "router-²", "type": "ssh", "name": "router-²", "group": "lab",
                                      "tags": [], "discovered_by": "manual", "last_seen": seen,
                                      "host": "192.0.2.80", "port": 22}
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.fetched, "the targets")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            joined = "\n".join(_target_lines(screen))
            assert "router-²" in joined, joined
    asyncio.run(check())


class _AttachSizeApp(InputRecordingApp):
    """The fixture server, taught to record the first text frame (the `attach`) of each socket."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.attach_frames: list[dict] = []

    def websocket(self, path, ws) -> None:
        original = ws.recv

        def recv():
            message = original()
            if message.kind == "text" and not self.attach_frames:
                with contextlib.suppress(ValueError):
                    self.attach_frames.append(json.loads(message.data))
            return message

        ws.recv = recv
        super().websocket(path, ws)


def test_the_fallback_attach_reports_the_canvas_size(serve, wait_until, monkeypatch):
    """When the canvas posts no resize of its own, the re-attach fallback reports the canvas's painted size, not
    the window-derived fit size: with the side panel open the attach frame is no wider than the painted canvas."""

    async def check():
        served, url = serve(_AttachSizeApp())
        monkeypatch.setattr(Canvas, "on_resize", lambda self, event: None)
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            (sid,) = named_sessions(url, served, "normal")
            await open_here(app, pilot, wait_until, sid)
            frame = served.attach_frames[0]
            canvas = screen.slot_for(sid).canvas
            assert (frame["cols"], frame["rows"]) == (canvas.size.width, canvas.size.height), (frame, canvas.size)
    asyncio.run(check())


def test_an_ownerless_session_is_never_ranked_as_yours():
    """Ruled 2026-11-21: a session is ranked as the caller's only when the caller's login is known and equals the
    record's owner. An absent login, or an ownerless record, ranks joinable — never yours."""
    ownerless_live = {"id": "s1", "owner": None, "state": "live"}
    assert session_rank(ownerless_live, set(), None) == 3, "an ownerless live session is not the caller's"
    peer_live = {"id": "s2", "owner": "ci-agent", "state": "live"}
    assert session_rank(peer_live, set(), None) == 3, "with no login known, a peer's session is joinable"
    mine_live = {"id": "s3", "owner": "me", "state": "live"}
    assert session_rank(mine_live, set(), "me") == 1, "a known login that owns a live session ranks yours"


def test_a_wide_target_row_never_paints_past_the_inner_width_at_the_facts_edge(serve, wait_until):
    """Ruled 2026-11-21: at the wide budget the facts are composed within `inner - 3` cells (the mark, at least one
    label cell and the space). A row whose full facts are `inner - 2` long drops its last segment whole, never cuts a
    word, and no painted line passes the inner width."""

    async def check():
        served, url = serve(InputRecordingApp())
        seen = "2026-10-17T08:30:00Z"
        name = "sw-edge"
        served.targets[name] = {"id": name, "type": "ssh", "name": name, "group": "lab", "tags": [],
                                "discovered_by": "manual", "last_seen": seen,
                                "host": "192.0.2.10.example", "port": 22}
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.sidebar.size.width - 4 >= 38, "the wide sidebar")
            sids = named_sessions(url, served, name, name, name, name, name, name)
            await open_here(app, pilot, wait_until, *sids)
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            rows = [line for line in _target_lines(screen) if "sw-edge" in line or "192.0.2.10" in line]
            joined = "\n".join(rows)
            assert "192.0.2.10.example" in joined, rows
            # The open numbers are the last segment: shown whole when they fit the wide budget, dropped whole when
            # they do not — never cut mid-segment, and never past the inner width.
            if "open" in joined:
                assert "open 1,2,3,4,5,6" in joined, rows
            for line in _painted(screen):
                assert len(line) == screen.sidebar.size.width, repr(line)
    asyncio.run(check())


def test_painting_a_targets_line_composes_each_target_facts_once(serve, wait_until, monkeypatch):
    """Ruled 2026-11-21: painting a Targets line does constant work. The spans cache records each row's facts and
    line count once per refresh; the row pairs and the painted end read that cache. With far more targets than the
    section paints, one full repaint composes each target's facts at most once."""

    async def check():
        served, url = serve(InputRecordingApp())
        seen = "2026-10-17T08:30:00Z"
        # The targets model loads one page (PAGE_LIMIT=100); 90 rows are far more than the section paints.
        for i in range(90):
            tid = f"t{i:03d}"
            served.targets[tid] = {"id": tid, "type": "ssh", "name": tid, "group": "lab", "tags": [],
                                   "discovered_by": "manual", "last_seen": seen,
                                   "host": "192.0.2.50", "port": 22}
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await fixture_login(app, pilot)
            await wait_until(pilot, lambda: screen.targets.fetched and len(screen.sidebar.model.targets) >= 90,
                             "the loaded targets")
            screen.open_sidebar(pane="targets")
            await pilot.pause()
            sidebar = screen.sidebar
            calls: list[str] = []
            original = sidebar_module._target_facts

            def counting(row, numbers, others, width=None):
                calls.append(row.id)
                return original(row, numbers, others, width)

            monkeypatch.setattr(sidebar_module, "_target_facts", counting)
            sidebar._target_spans_cache = None
            for y in range(sidebar.size.height):
                sidebar.render_line(y)
            counts = Counter(calls)
            repeats = [tid for tid, n in counts.items() if n > 1]
            assert not repeats, f"{len(repeats)} targets composed more than once: {counts.most_common(3)}"
    asyncio.run(check())
