"""Snapshots of the screens the design draws, at the three sizes it draws them (design §3.4 test 7, last bullet).

Why it matters (Rule 9): the mockups are what the operator approved, and a reviewer compares pictures with pictures.
Each state the design shows (T1 the terminal, S1 the sidebar, F5 Who's here, F7 the stale dialog, F12 the status row's
states, A2 the agent's headless TUI) is built here in the running app at 65x30, 120x40 and 200x60, exported as text
and as SVG into the directory named by `TSB_TUI_SNAPSHOT_DIR` (a temporary one when it is not set), and its content is
asserted: the pill's words, the rail's glyphs at their cells, `cols a-b of N ▸` when the window pans, and each state's
status-row text. The files exist for a person and an Opus reviewer to open against the mockups (the orchestrator's
review, PR B gate 3); the assertions exist so that a screen that is wrong in a way a reviewer would miss, or drawn at
only two of the three sizes, still fails. Servers: the contract's in-process fixture server (DT7).
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest
from colour_canvas import COLOUR
from drive_support import Rig
from input_server import InputRecordingApp, attached, make_session, other_client
from model_frames import attached_frame, token_frame
from model_frames import clients_frame as _clients_frame
from prompt_support import NOW, iso, notice, validated
from stub_server import create_owner_session, record_output
from tsb_tui.app import TsbTuiApp
from tsb_tui.widgets.stale_banner import STALE_NOTICE
from tsbc import _serve
from workspace_support import fixture_login, named_sessions

SIZES = ((65, 30), (120, 40), (200, 60))
ROUTER = (b"rtr-core-01#show clock\r\n*14:02:07.311 ICT Sat Oct 10 2026\r\n"
          b"rtr-core-01#show version | include uptime\r\nrtr-core-01 uptime is 41 weeks, 3 days, 7 hours, 12 minutes\r\n"
          b"rtr-core-01#")


@pytest.fixture(scope="module")
def snapshot_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    chosen = os.environ.get("TSB_TUI_SNAPSHOT_DIR")
    directory = Path(chosen) if chosen else tmp_path_factory.mktemp("snapshots")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


QUIET_ALERT_STREAM = b": keepalive\n\n"
SHOWN_ALERTS = 0  # what the rail's `!n` says in every picture: the stream below raises nothing


@pytest.fixture
def serve(serve) -> Callable:
    """The contract's fixture server with an alert stream that raises nothing. The fixture's own stream raises an alert
    and clears it a moment later, on the streamer's thread: a picture taken in between shows `!1` or an alert popup over
    the output, one taken after shows `!0`, so the same state read differently at each size. These pictures are about
    the rail, the canvas and the row, and the popup has its own tests (test_alerts_popup.py), so the count is fixed:
    the stream is open, as it is for any broker.health holder, and says nothing."""

    def start(app=None):
        served, url = serve(app)
        served.alerts_body = QUIET_ALERT_STREAM
        return served, url

    return start


def _shown_alerts(lines: list[str]) -> list[int]:
    """The alert count the screen says: the rail's `!n` cell or the sidebar's `! n alerts` line."""
    return [int(m.group(1)) for line in lines if (m := re.match(r" ?!\s?(\d+)", line))]


@pytest.fixture
def shoot(snapshot_dir: Path, screen_text) -> Callable:
    """`shoot(app, id, size) -> text`: the screen as the operator reads it, written beside its SVG. The alert count it
    shows is the fixed one, at every size, so no picture depends on when the alert stream was read."""

    def take(app: TsbTuiApp, state: str, size: tuple[int, int], *, rail_covered: bool = False) -> str:
        text = screen_text(app)
        stem = f"{state}-{size[0]}x{size[1]}"
        (snapshot_dir / f"{stem}.txt").write_text(text + "\n", encoding="utf-8")
        svg = app.export_screenshot(title=stem)
        (snapshot_dir / f"{stem}.svg").write_text(svg, encoding="utf-8")
        assert svg.lstrip().startswith("<svg") and "</svg>" in svg, f"{stem}: not an SVG"
        lines = text.splitlines()
        assert len(lines) == size[1], f"{stem}: {len(lines)} lines tall"
        assert all(len(line) <= size[0] for line in lines), f"{stem}: a line is wider than the window"
        if rail_covered:
            # The Menu's palette is drawn over the rail, so the count is not on the screen: read the model the rail reads.
            assert app.alerts.allowed and app.alerts.count == SHOWN_ALERTS, (
                f"{stem}: the alert model holds {app.alerts.count}, expected {SHOWN_ALERTS}")
            return text
        shown = _shown_alerts(lines)
        assert shown and set(shown) == {SHOWN_ALERTS}, f"{stem}: the alert count reads {shown}, expected {SHOWN_ALERTS}"
        return text

    return take


async def _open_in_order(app, pilot, wait_until, screen, *session_ids: str) -> None:
    """Open the sessions here, past the gate, the first in front and the rest behind: chip 1 is the one in front."""
    for index, sid in enumerate(session_ids):
        app.gated_sessions.add(sid)
        screen.open_session(app.client.session(sid), front=index == 0)
        await wait_until(pilot, lambda sid=sid: (slot := screen.slot_for(sid)) is not None and slot.pump is not None
                         and slot.pump.state.dump_seen, f"{sid} to attach")


def _rail(lines: list[str]) -> list[str]:
    """The rail's four columns, row by row."""
    return [line[:4] for line in lines]


def _chip_rows(lines: list[str]) -> list[str]:
    return [cell[:3].rstrip() for cell in _rail(lines) if re.match(r"^\d[●◆·▲?◌✕]", cell)]


def _status(lines: list[str]) -> str:
    return lines[-1]


@pytest.fixture(autouse=True)
def _no_recording_bar(monkeypatch) -> None:
    """The recording text replaces the row's left part for a few seconds after the first attach (G9); the states the
    design draws are the steady ones."""
    monkeypatch.setenv("TSB_TUI_GATES_RECORDING_BAR_S", "0")


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_t1_the_terminal_with_three_sessions_open(size, serve, wait_until, shoot, snapshot_dir) -> None:
    """Mockups T1 and T2. You type in 1; another login types in 2 and 3; a fourth session cannot be reached. The rail
    shows the Menu and the fleet at its first two rows, `▲1` and the alerts, a chip for each session with who types
    (`●` you, `◆` another login) and `◂` for the one in front, and `+`; the status row shows the pill, the session and
    `≡ Menu`. The session is 132 columns wide: a 65 or 120 column window shows its range with `▸`, a 200 column one
    shows the whole grid and says nothing of panning. The canvas's background is painted: rects filled with theme.bg
    (#0a0a0a) sit under the canvas's columns, right of the rail (phase 1, B1)."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        first, second, third, broken = named_sessions(
            url, serve_app, "rtr-core-01", "infra-db-02", "k8s-node-07", "fw-edge-01", cols=132, rows=40)
        serve_app.sessions[broken]["state"] = "unreachable"
        record_output(serve_app, first, ROUTER)
        with other_client(url, _serve.PEER, second), other_client(url, _serve.PEER, third):
            app = TsbTuiApp(url)
            async with app.run_test(size=size) as pilot:
                screen = await fixture_login(app, pilot)
                await _open_in_order(app, pilot, wait_until, screen, first, second, third)
                screen.close_sidebar()
                await wait_until(pilot, lambda: not screen.sidebar_open and "▲1" in screen.rail.text
                                 and screen.canvas.painted, "the rail to count")
                await pilot.pause(0.2)
                await wait_until(pilot, lambda: "rtr-core-01#" in "".join(
                    s.text for s in app.screen._compositor.render_strips()), "the router's screen")
                if size[0] - 4 < 132:
                    # The row is drawn a repaint after the canvas widens; wait for the drawn row to say the range the
                    # canvas has now, rather than for a fixed time.
                    await wait_until(pilot, lambda: screen.canvas.view_range.label in
                                     list(app.screen._compositor.render_strips())[-1].text, "the row to draw the range")
                lines = shoot(app, "T1-terminal", size).splitlines()

                rail = _rail(lines)
                assert "≡" in rail[0] and "▦▸" in rail[1], rail[:2]
                assert any(cell.startswith("▲1") for cell in rail), "no ▲1 on the rail"
                assert any(cell.startswith("!") for cell in rail), "no alerts row on the rail"
                assert _chip_rows(lines) == ["1●◂", "2◆", "3◆"], _chip_rows(lines)
                assert any(cell.strip().startswith("+") for cell in rail), "no + on the rail"
                assert "rtr-core-01#" in lines[0][4:], "the session's own screen is not drawn beside the rail"

                status = _status(lines)
                assert "you type" in status and "≡ Menu" in status, status
                if size[0] >= 120:
                    assert "rtr-core-01" in status and "▦ Fleet" in status, status
                label = screen.canvas.view_range.label
                if size[0] - 4 < 132:
                    assert label.startswith("cols 1-") and label.endswith("of 132 ▸") and label in status, (label, status)
                else:
                    assert "cols" not in status, status
                svg = (snapshot_dir / f"T1-terminal-{size[0]}x{size[1]}.svg").read_text(encoding="utf-8")
                bg_rects = re.findall(r'<rect fill="#0a0a0a" x="([0-9.]+)"', svg)
                assert bg_rects, "no rect filled with theme.bg is under the canvas"
                assert min(float(x) for x in bg_rects) >= 40, (
                    "a theme.bg rect starts left of the rail, so it is a full-window overlay, not the canvas")

    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_t1c_the_terminal_with_coloured_output_shows_the_scheme(size, serve, wait_until, shoot,
                                                                 snapshot_dir) -> None:
    """Phase 1: T1c, state of T1 with a coloured Linux host in front. The canvas paints its background:
    every snapshot SVG has rects filled with theme.bg (#0a0a0a) inside the canvas's columns (right of
    the rail). The prompt text and a coloured cell's fill assert content.

    fails today: the canvas paints no background, so no rects sit under it."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        first, second, third, broken = named_sessions(
            url, serve_app, "infra-db-02", "rtr-core-01", "k8s-node-07", "fw-edge-01", cols=132, rows=40)
        serve_app.sessions[broken]["state"] = "unreachable"
        record_output(serve_app, first, COLOUR)
        with other_client(url, _serve.PEER, second), other_client(url, _serve.PEER, third):
            app = TsbTuiApp(url)
            async with app.run_test(size=size) as pilot:
                screen = await fixture_login(app, pilot)
                await _open_in_order(app, pilot, wait_until, screen, first, second, third)
                screen.close_sidebar()
                await wait_until(pilot, lambda: not screen.sidebar_open and "▲1" in screen.rail.text
                                 and screen.canvas.painted, "the rail to count")
                await wait_until(pilot, lambda: "normal 0-7" in "".join(
                    s.text for s in app.screen._compositor.render_strips()), "the coloured screen")
                await wait_until(pilot, lambda: screen.canvas.view_range.label in
                                 list(app.screen._compositor.render_strips())[-1].text if size[0] - 4 < 132 else True,
                                 "the row to draw the range")
                await pilot.pause(0.2)
                lines = shoot(app, "T1c-terminal-colour", size).splitlines()

                # Content assertions: the T1c state's prompt and the ANSI swatch are drawn.
                assert "ops@infra-db-02" in lines[0][4:], "the prompt is not drawn"
                assert "normal 0-7" in "\n".join(lines), "the swatch is not drawn"
                # The canvas paints its own background: rects filled with theme.bg (#0a0a0a) sit under
                # the canvas's columns (right of the rail), never as a full-window overlay.
                svg = (snapshot_dir / f"T1c-terminal-colour-{size[0]}x{size[1]}.svg").read_text(encoding="utf-8")
                bg_rects = re.findall(r'<rect fill="#0a0a0a" x="([0-9.]+)"', svg)
                assert bg_rects, "no rect filled with theme.bg is under the canvas"
                assert min(float(x) for x in bg_rects) >= 40, (
                    "a theme.bg rect starts left of the rail, so it is a full-window overlay, not the canvas")

    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_s1_the_rail_widens_in_place_into_the_fleet_sidebar(size, serve, wait_until, shoot) -> None:
    """Mockups S1 and S2. Opening the sidebar widens the rail beside the terminal, never over it: the groups (Needs you,
    Open here), a row for each session with its chip number and who types, the filter and the mine│all toggle, the
    alerts row and New session; the status row says the keys are the sidebar's (`keys → fleet`) and the terminal pans
    to make room (`cols a-b of 132 ▸`, unless the window is wide enough to show all 132 columns beside it) without the
    session being asked for a size."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        first, second, broken = named_sessions(
            url, serve_app, "rtr-core-01", "infra-db-02", "fw-edge-01", cols=132, rows=40)
        serve_app.sessions[broken]["state"] = "unreachable"
        record_output(serve_app, first, ROUTER)
        with other_client(url, _serve.PEER, second):
            app = TsbTuiApp(url)
            # Not NOW (2027-01-15): the fixture server gives every session `ends_at` 2026-09-30T12:00:00Z, so NOW is after
            # it and the sidebar would show an expired countdown. This clock stands four days before it, outside the
            # 72 h warning window, and is fixed so a countdown reads the same at every size.
            before_the_end = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc).timestamp()
            app.wall_clock = lambda: before_the_end
            async with app.run_test(size=size) as pilot:
                screen = await fixture_login(app, pilot)
                await _open_in_order(app, pilot, wait_until, screen, first, second)
                screen.open_sidebar()
                await wait_until(pilot, lambda: screen.sidebar_open and screen._sidebar_has_keys()
                                 and any(row[1] == broken for row in screen.sidebar.view.rows), "the sidebar to fill")
                await pilot.pause(0.2)
                text = shoot(app, "S1-sidebar", size)
                lines = text.splitlines()
                width = screen.sidebar.size.width
                side = [line[:width] for line in lines]

                assert width in (30, 32, 42), width
                assert "▦ Fleet" in side[0] and "close" in side[0], side[0]
                assert any("filter" in row and "mine│all" in row.replace(" ", "") or "filter" in row for row in side[:3])
                assert any(row.strip().startswith("Needs you") for row in side), side
                assert any(row.strip().startswith("Open here") for row in side), side
                assert any("fw-edge-01" in row and "◌" in row for row in side), "the unreachable session needs you"
                assert any("1●" in row and "rtr-core-01" in row for row in side), side
                assert any("2◆" in row and "infra-db-02" in row for row in side), side
                assert any("+" in row and "New session" in row for row in side), side
                assert lines[0][width:].strip(), "nothing is drawn beside the sidebar"
                assert not [row for row in side if "0:00" in row], (
                    f"a sidebar row reads an expired countdown: {[row for row in side if '0:00' in row]}")
                status = _status(lines)
                assert "keys → fleet" in status, status
                label = screen.canvas.view_range.label
                if screen.canvas.size.width < 132:
                    assert label.startswith("cols 1-") and label.endswith("of 132 ▸"), label
                    if size[0] >= 120:
                        assert label in status and "rtr-core-01 shows" in status, (label, status)  # mockup S1
                    else:
                        assert "tap a row" in status and "Esc Close" in status, status  # mockup S2: the phone's row
                else:
                    assert "cols" not in status, f"a window that shows the whole grid says it pans: {status}"
                assert not [f for f in serve_app.frames if f.get("t") == "resize"], "opening the sidebar asked for a size"

    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_f5_whos_here_lists_who_types_and_offers_the_four_moves(size, serve, wait_until, shoot) -> None:
    """Mockup F5. Three clients are on one session: you (the owner, typing), another login that may type, and one whose
    right is read-only. The dialog over the terminal names the session, lists each client with its right and says
    who has the keyboard, has the four moves as buttons (Take it, Hand to selected, Put it aside, Remove selected…) and
    a line saying what Hand to selected would do; the status row says the dialog has the keys (`keys → who's here`)."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER, 132, 40)
        record_output(serve_app, sid, ROUTER)
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            await _open_in_order(app, pilot, wait_until, screen, sid)
            with other_client(url, _serve.PEER, sid), other_client(url, _serve.VIEWER, sid):
                await wait_until(pilot, lambda: len(screen.pump.state.clients) == 3, "three clients")
                screen.close_sidebar()
                await screen.run_action_id("who")
                await wait_until(pilot, lambda: app.screen is not screen and "Who's here" in "".join(
                    s.text for s in app.screen._compositor.render_strips()), "the dialog")
                await pilot.pause(0.2)
                text = shoot(app, "F5-whos-here", size)
                lines = text.splitlines()
                joined = "\n".join(lines)

                assert re.search(r"Who's here · fixture-target · s\.[0-9a-z-]+", joined), "the title names the session (F5)"
                for word in ("who", "client", "right", "keyboard"):
                    assert re.search(rf"\b{word}\b", joined), f"no {word!r} column"
                for user in (_serve.USER, _serve.PEER, _serve.VIEWER):
                    assert user[:8] in joined, f"{user} is not listed"
                assert "can type" in joined and "read-only" in joined
                assert "● types · owner" in joined, "who has the keyboard is not said"
                if size[0] >= 120:
                    assert "this client" in joined, "the holder's own row does not say it is this client"
                for button in ("Take it", "Hand to selected", "Put it aside", "Remove selected"):
                    assert button in joined, f"no {button!r} button"
                status = _status(lines)
                assert "keys → who's here" in status and "Esc close" in status, status
                assert "≡" in _rail(lines)[0], "the rail is gone behind the dialog"

    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_f7_the_stale_dialog_offers_the_four_choices_to_the_one_who_types(size, serve, wait_until, shoot) -> None:
    """Mockup F7. The session's screen could not be rebuilt, and the one who types is asked what to do: the dialog
    carries the contract's own sentence and the four fixed choices in order (redraw, Ctrl-L, keep going, a fresh
    session), says they are clickable or 1-4, that keys are ignored for a second and that the choice is recorded; the
    status row carries the contract's notice line (the screen may be stale until a choice is made)."""

    async def check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            session_id = create_owner_session(app, "stale-pending", 132, 40)
            screen = await attached(pilot, app, session_id, wait_until)
            await wait_until(pilot, lambda: screen.stale_active() and "Choose" in "".join(
                s.text for s in app.screen._compositor.render_strips()), "the stale dialog")
            await pilot.pause(0.2)
            text = shoot(app, "F7-stale", size)
            lines = text.splitlines()
            joined = "\n".join(lines)

            assert "TSB: lost output - the screen may be wrong. Choose:" in re.sub(r"\s+", " ", joined) or (
                "the screen may be wrong" in joined and "Choose" in joined), "the contract's sentence is missing"
            order = [joined.index(f"[{n}]") for n in (1, 2, 3, 4)]
            assert order == sorted(order), "the four choices are not in order"
            squeezed = re.sub(r"[\s│]+", " ", joined)
            assert "(no guarantee the screen is right)" in squeezed, (
                "a choice's text must reach the person whole, wrapped when the window is narrow, never cut off")
            for words in ("Redraw the screen", "Send Ctrl-L", "Keep going without a redraw", "Start a fresh session"):
                assert words in squeezed, f"no {words!r}"
            assert "1-4" in squeezed and "recorded" in squeezed, "the footer is missing"
            status = _status(lines)
            assert "you type" in status and "▲ stale" in status, status
            assert "● REC" in status and "≡ Menu" in status, f"REC and the Menu stay on the one row: {status!r}"
            assert screen.status.size.height == 1, "the status row never grows a second line"
            # The row is one row; the contract's notice line (stale-screen rule 18) is the banner at the top of the
            # canvas, whole, wrapped at spaces on a phone, never cut.
            assert STALE_NOTICE in " ".join(line[4:].strip() for line in lines[:4]), lines[:4]
            assert "≡" in _rail(lines)[0]

    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_a2_an_agents_headless_tui_snapshot_is_the_same_ui(size, serve, snapshot_dir, tmp_path, screen_text) -> None:
    """Mockup A2. `tsb-tui drive`'s `snapshot` shows an agent the same screen a person would see: the rail with a chip for
    each session (`1●◂` the one in front, `2●•` output waiting in another, `3◆` held by another login), the terminal
    beside it and the status row with the pill; in text and as an SVG, equal to what the pilot of the same app shows.
    The agent reads the UI the way a person does (design honesty rule 1), so what it sees is what a reviewer is shown."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        first, second, third = (make_session(url, _serve.USER, 132, 40) for _ in range(3))
        record_output(serve_app, first, b"ops@infra-db-02:~$ df -h /var/lib/postgresql\r\nFilesystem  Size  Used Avail Use%\r\n"
                      b"/dev/mapper/vg0-pgdata  800G  731G   69G  92%\r\nops@infra-db-02:~$ ")
        with other_client(url, _serve.PEER, third):
            async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("input.guard=true",),
                           size=size) as rig:
                await rig.login(_serve.USER)
                for sid in (first, second, third):
                    await rig.attach(sid)
                assert (await rig.cmd("switch", session=first))["ok"]
                screen = rig.app.workspace()
                screen.close_sidebar()
                with serve_app.lock:
                    serve_app._emit_output(serve_app.sessions[second], b"active\r\n")
                for _ in range(300):
                    if "•" in screen.rail.text:
                        break
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.2)
                answer = await rig.cmd("snapshot")
                svg = (await rig.cmd("snapshot", format="svg"))["svg"]
                stem = f"A2-agent-{size[0]}x{size[1]}"
                (snapshot_dir / f"{stem}.txt").write_text(answer["text"] + "\n", encoding="utf-8")
                (snapshot_dir / f"{stem}.svg").write_text(svg, encoding="utf-8")
                assert svg.lstrip().startswith("<svg") and "</svg>" in svg
                lines = answer["text"].splitlines()
                assert answer["text"] == screen_text(rig.app), "the agent's snapshot differs from the screen a pilot reads"
                assert len(lines) == size[1]

                rail = _rail(lines)
                assert "≡" in rail[0] and "▦▸" in rail[1], rail[:2]
                assert _chip_rows(lines) == ["1●◂", "2●•", "3◆"], _chip_rows(lines)
                assert set(_shown_alerts(lines)) == {SHOWN_ALERTS}, _shown_alerts(lines)
                assert "ops@infra-db-02:~$" in "".join(line[4:] for line in lines[:8])
                status = _status(lines)
                assert "you type" in status and "≡ Menu" in status, status

    asyncio.run(check())


# -- F12: the status row in each state --

ME_ID = "client-me"


def _deliver(screen, slot, *frames: dict) -> None:
    """Each frame the way a pump hands it over: applied to the attachment's state, then to the screen's own frame
    handler, which is what moves the typist's cursor, the rail, the prompts and the row. Applying a frame to the state
    alone leaves all of those as they were, and a picture of that is a state no operator can reach."""
    state = slot.pump.state
    for frame in frames:
        state.apply_frame(frame)
        screen._on_event(slot, state, frame)


def _as_non_owner(state, *, holder: tuple[str, str] | None, may_control: bool = True) -> list[dict]:
    """This client, attached to a session somebody else owns: the `attached` and `clients` frames a core would send."""
    me = state.client_id
    peers = [("client-peer", _serve.PEER, True)]
    return [
        attached_frame(client_id=me, owner=_serve.PEER, holder=holder),
        _clients_frame((me, _serve.USER, may_control), *peers),
    ]


# (id, the frames that put the live state there, words on the row at every width, words from 120 columns, a hint that
# fits from 200 columns). The Answer and Take back hints keep their place at 120 columns, as mockup F12 draws them
# (`status.hint_priority`, the orchestrator's DEFAULT for the operator's veto).
F12_STATES: list[tuple[str, Callable, str, str, str]] = []


def _f12(state_id: str, always: str, wide: str, from_200: str = ""):
    def register(fn: Callable) -> Callable:
        F12_STATES.append((state_id, fn, always, wide, from_200))
        return fn
    return register


@_f12("you-type", "you type", "rtr-core-01  ● live  ● REC")
def _you_type(state) -> list[dict]:
    return []


@_f12("owner-view-only", "view only", "fixture-peer types · you own it: typing takes it")
def _owner_view_only(state) -> list[dict]:
    return [token_frame(("client-peer", _serve.PEER), "handoff")]


@_f12("took-the-keyboard", "you type", "you took the keyboard from fixture-peer")
def _took(state) -> list[dict]:
    return [token_frame(("client-peer", _serve.PEER), "handoff"), token_frame((state.client_id, _serve.USER), "takeover")]


@_f12("someone-else-types", "view only", "fixture-peer types · ")
def _someone(state) -> list[dict]:
    return _as_non_owner(state, holder=("client-peer", _serve.PEER))


@_f12("read-only-right", "view only", "your right is read-only")
def _read_only(state) -> list[dict]:
    return _as_non_owner(state, holder=("client-peer", _serve.PEER), may_control=False)


@_f12("nobody-types", "nobody types", "fixture-peer put the keyboard aside")
def _nobody(state) -> list[dict]:
    return [*_as_non_owner(state, holder=(state.client_id, _serve.USER)), token_frame(None, "owner")]


@_f12("owner-took-it", "view only", "fixture-peer took the keyboard (owner)")
def _owner_took(state) -> list[dict]:
    return [*_as_non_owner(state, holder=(state.client_id, _serve.USER)),
            token_frame(("client-peer", _serve.PEER), "takeover")]


@_f12("notice-link", "you type", "▲ not answering · ends in 4:12 unless it answers")
def _notice_link(state) -> list[dict]:
    return [validated(notice(252, "link_timeout", session_id=state.session_id))]


@_f12("notice-idle", "you type", "▲ no bytes either way · ends in 2d 23h · any byte keeps it")
def _notice_idle(state) -> list[dict]:
    return [validated(notice(2 * 86400 + 23 * 3600 + 60, "idle", session_id=state.session_id))]


@_f12("data-path-lost", "you type", "◐ restoring the data path · input refused for now")
def _data_path(state) -> list[dict]:
    return [validated({"t": "data_path", "v": 1, "session_id": state.session_id, "state": "lost", "at": iso(NOW)})]


@_f12("quiet-prompt", "you type", "rtr-core-01  ● live  ● REC")
def _quiet(state) -> list[dict]:
    return [validated({"t": "quiet_prompt", "v": 1, "prompt_id": "f" * 32, "code": "quiet_prompt.kill",
                       "text": "Kill the session?"})]


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@pytest.mark.parametrize(("state_id", "make", "always", "wide", "from_200"), F12_STATES, ids=[s[0] for s in F12_STATES])
def test_f12_the_status_row_in_each_state(state_id, make, always, wide, from_200, size, serve, wait_until,
                                          shoot) -> None:
    """Mockup F12. The one status row says who types and what the session is going through, in the same words at every
    width and with fewer hints as the window narrows, and `≡ Menu` is the last thing to go. Each state is the row the
    mockup draws: you type; view only (the owner whose keyboard another holds, a client who must wait, one whose right is
    read-only); nobody types; a take-over either way; a session inside its warning window (the link, an idle timeout);
    the data path lost; a question the session asks. The states arrive as the core's own frames (validated against the
    contract's schema) delivered through the screen's own frame handler, as a pump does, so the rail chip and the
    canvas cursor follow the pill; the screen is the running app's."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01", cols=116, rows=39)  # the 120-column window's own canvas
        record_output(serve_app, sid, ROUTER)
        app = TsbTuiApp(url)
        app.wall_clock = lambda: NOW
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            await _open_in_order(app, pilot, wait_until, screen, sid)
            screen.close_sidebar()
            slot = screen.slot_for(sid)
            state = slot.pump.state
            _deliver(screen, slot, *make(state))
            await pilot.pause(0.3)
            lines = shoot(app, f"F12-{state_id}", size).splitlines()
            status = re.sub(r"\s+", " ", " ".join(line.strip() for line in lines[-3:] if line.strip(" │")))
            assert always in status, f"{state_id} at {size[0]}: {always!r} not in {status!r}"
            # The chip's second cell is the session's attention first, who types only when nothing needs you (design
            # legend and §2.3 rail item 5): a warning window (the notice states) is `▲`, a question for you is `?`,
            # otherwise `●` you, `◆` another login, `·` nobody. The same at every size. The cursor follows who types alone
            # (D21: the typist's cursor is a block, a viewer's an underline).
            glyph = {"quiet-prompt": "?", "notice-link": "▲", "notice-idle": "▲"}.get(
                state_id) or {"you type": "●", "view only": "◆", "nobody types": "·"}[always]
            assert [chip[:2] for chip in _chip_rows(lines)] == [f"1{glyph}"], (
                f"{state_id} at {size[0]}: the rail chip disagrees with {always!r}: {_chip_rows(lines)}")
            typing = always == "you type"
            assert slot.canvas.typist is typing, f"{state_id} at {size[0]}: the cursor is not the {always!r} one"
            palette = slot.canvas._palette
            assert (palette.cursor_typist if slot.canvas.typist else palette.cursor_viewer).shape == (
                "block" if typing else "underline"), f"{state_id} at {size[0]}: wrong cursor shape for {always!r}"
            assert "≡ Menu" in status, f"{state_id} at {size[0]}: the Menu is not on the row: {status!r}"
            if size[0] >= 120:
                assert re.sub(r"\s+", " ", wide) in status, f"{state_id} at {size[0]}: {wide!r} not in {status!r}"
            if state_id == "owner-view-only" and size[0] >= 120:
                assert "^\\ t Take back" in status, status
            if state_id == "quiet-prompt" and size[0] >= 120:
                assert "^\\ a Answer: kill the session?" in status, status
            if state_id == "quiet-prompt" and size[0] == 120:
                assert "Detach" not in status, f"Detach kept ahead of Answer at 120: {status}"
            if size[0] >= 200 and from_200:
                assert from_200 in status, f"{state_id} at {size[0]}: the hint {from_200!r} is not on the row: {status!r}"
            if state_id == "quiet-prompt":
                assert "asks you" in "\n".join(lines), "the question is not on the screen"

    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_f12_the_row_for_an_unreachable_worker_comes_from_the_poll_and_says_the_session_is_not_ended(
        size, serve, wait_until, shoot) -> None:
    """Mockup F12, row "unreachable". The worker behind the session is down: the core says so on the session's record and
    the next poll tells the attachment. The row says the session is unreachable, that input is refused and that nothing
    has ended, at every width where the row has room for the sentence, with `≡ Menu` kept."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01", cols=116, rows=39)
        record_output(serve_app, sid, ROUTER)
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            await _open_in_order(app, pilot, wait_until, screen, sid)
            screen.close_sidebar()
            serve_app.sessions[sid]["state"] = "unreachable"
            screen._refresh_fleet()
            await wait_until(pilot, lambda: "unreachable" in screen.status.text, "the poll to tell the row")
            # The core then refuses what is typed, with its reason (byte-relay's refusal table).
            refusal = validated({"t": "input_refused", "v": 1, "reason": "unreachable"})
            _deliver(screen, screen.slot_for(sid), refusal)
            await pilot.pause(0.3)
            lines = shoot(app, "F12-unreachable", size).splitlines()
            status = re.sub(r"\s+", " ", " ".join(line.strip() for line in lines[-3:] if line.strip(" │")))
            assert "◌ unreachable" in status and "≡ Menu" in status, status
            if size[0] >= 120:
                assert "◌ unreachable · TSB cannot reach its worker · input refused · not ended" in status, status

    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_f12_the_row_while_reattaching_says_which_try_is_next_and_that_esc_stops_it(
        size, serve, monkeypatch, wait_until, shoot) -> None:
    """Mockup F12, row "reattaching". The core closed the view (4011) and the session lives on: the row says so, which
    try comes next and in how many seconds, and that Esc stops it, with `≡ Menu` kept; the status row does not claim
    the session ended."""
    monkeypatch.setenv("TSB_TUI_REATTACH_BACKOFF_MS", "60000,60000,60000")

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01", cols=116, rows=39)
        record_output(serve_app, sid, ROUTER)
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await fixture_login(app, pilot)
            await _open_in_order(app, pilot, wait_until, screen, sid)
            screen.close_sidebar()
            with serve_app.lock:
                serve_app._drop_client(serve_app.sessions[sid], next(iter(serve_app.clients[sid])), code=4011, reason="")
            await wait_until(pilot, lambda: "reattaching" in screen.status.text, "the reattaching row")
            await pilot.pause(0.3)
            lines = shoot(app, "F12-reattaching", size).splitlines()
            status = re.sub(r"\s+", " ", " ".join(line.strip() for line in lines[-3:] if line.strip(" │")))
            assert "◐ reattaching" in status and "≡ Menu" in status, status
            assert "ended" not in status.replace("not ended", ""), status
            if size[0] >= 120:
                assert re.search(r"the session lives on · try \d+ in \d+ s", status), status
                assert "Esc stops" in status, status

    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_f12_a_stale_screen_someone_else_is_choosing_shows_no_dialog_and_says_who(
        size, serve, wait_until, shoot) -> None:
    """Mockup F12, row "stale, someone else chooses". Only the one who types is asked: this client, which does not hold
    the keyboard, gets no dialog; the contract's notice line is the banner at the top of the canvas, and its one-row
    status row says the screen is stale, who is choosing and that its input is refused meanwhile."""

    async def check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER, 116, 39, target="stale-pending")
        with other_client(url, _serve.PEER, sid):
            app = TsbTuiApp(url)
            async with app.run_test(size=size) as pilot:
                screen = await fixture_login(app, pilot)
                await _open_in_order(app, pilot, wait_until, screen, sid)
                screen.close_sidebar()
                await wait_until(pilot, lambda: screen.pump.state.stale_state == "pending", "the stale state")
                await pilot.pause(0.3)
                lines = shoot(app, "F12-stale-someone-else", size).splitlines()
                joined = "\n".join(lines)
                status = _status(lines)
                assert "view only" in status and "▲ stale" in status, status
                assert "● REC" in status and "≡ Menu" in status, f"REC and the Menu stay on the one row: {status!r}"
                assert screen.status.size.height == 1, "the status row never grows a second line"
                assert STALE_NOTICE in " ".join(line[4:].strip() for line in lines[:4]), lines[:4]
                assert "Choose:" not in joined, "a client that does not type was shown the dialog"
                if size[0] >= 120:  # who is choosing fits where the row has room; a phone keeps `▲ stale · input refused`
                    assert "fixture-peer is choosing · input refused" in status, status

    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_f12_the_menu_takes_the_keys_and_says_how_to_use_it(size, serve, wait_until, shoot) -> None:
    """Mockup F12, row "menu". The Menu is a searchable list of every action; while it is open the keys are its own and
    the row says so and what they do: choose, run, type to find, close."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER, 116, 39)
        record_output(serve_app, sid, ROUTER)
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 116, 39)
            screen = await attached(pilot, app, session_id, wait_until)
            screen.close_sidebar()
            await wait_until(pilot, lambda: screen.status.hit_x("menu") is not None, "the row to fill")
            screen.app.open_menu()
            await wait_until(pilot, lambda: app.screen is not screen and "Find an action" in "".join(
                s.text for s in app.screen._compositor.render_strips()), "the Menu")
            await pilot.pause(0.3)
            lines = shoot(app, "F12-menu", size, rail_covered=True).splitlines()
            joined = "\n".join(lines)
            assert "Who's here…" in joined and "Size…" in joined, "the Menu lists no actions"
            status = lines[-1]
            assert "keys → menu" in status and "Esc close" in status, status
            if size[0] >= 120:
                assert "↑↓ choose" in status and "⏎ run" in status and "type to find" in status, status

    asyncio.run(check())
