"""The status row while a stale choice is pending (stale-screen rule 18; design F7, F12).

Why these matter (Rule 9): a surface that renders text MUST show the stale notice line. A client that is not the
keyboard holder never sees the dialog, so for it the status row is the only place the notice appears, and a
row that cut it with an ellipsis, or lost it to the hints, would leave that client typing into a screen that may
be wrong without being told. The row gives up its hints first, then the notice's tail; the pill and the notice
stay whole; when they cannot share one line the row grows a second line, and it goes back to one line once the
choice is made. Servers: the TUI's own stub (DT7), as a client that does not hold the keyboard.
"""

from __future__ import annotations

import asyncio

from prompt_support import NOW, iso, open_stub_terminal, validated
from stub_server import SlowChunkedApp, StubServer
from tsb_tui.app import TsbTuiApp
from tsb_tui.widgets.status_row import STALE_NOTICE

PENDING = {"t": "stale", "v": 1, "session_id": "stub-session-1", "state": "pending", "cause": "grid_lost",
           "at": iso(NOW)}
CHOSEN = {"t": "stale", "v": 1, "session_id": "stub-session-1", "state": "chosen", "cause": "grid_lost",
          "at": iso(NOW), "choice": "continue", "by": {"client_id": "stub-other", "user": "fixture-other"}}


def _stub() -> SlowChunkedApp:
    validated(PENDING)
    validated(CHOSEN)
    return SlowChunkedApp(delay_s=0.05, after_attach_frames=[PENDING], holder_is_client=False,
                          respond_to={"resize": [CHOSEN]})


def test_the_notice_line_is_whole_at_120_and_200_columns(wait_until) -> None:
    """At 200 the row is one line with the notice, its tail and the hints that fit; at 120 the pill, who
    types and the session leave no room for the notice on the same line, so the notice has its own. Either
    way it is whole."""

    async def _check(width: int, lines: int) -> None:
        with StubServer(_stub()) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(width, 30)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: screen.pump.state.stale_state == "pending", "the frame")
                await wait_until(pilot, lambda: screen.status.size.height == lines, f"{lines} line(s)")
                row = screen.status.text
                assert STALE_NOTICE in row, row
                if lines == 1:
                    assert "fixture-other is choosing · input refused" in row, row

    asyncio.run(_check(120, 2))
    asyncio.run(_check(200, 1))


def test_the_row_grows_a_second_line_for_the_notice_when_the_pill_and_it_do_not_share_one(
        wait_until) -> None:
    """The notice whole on its own line, and back to one line once the choice is made: the notice is gone then."""

    async def _check() -> None:
        with StubServer(_stub()) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(70, 30)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                row = screen.status
                await wait_until(pilot, lambda: row.size.height == 2, "the row to grow")
                first, second = row.text.split("\n")
                assert "‹view only›" in first
                assert second.strip() == "▲ " + STALE_NOTICE
                await screen.pump.resize(60, 20, timeout=0)  # the stub answers with `chosen`
                await wait_until(pilot, lambda: row.size.height == 1, "the row to shrink")
                assert "\n" not in row.text and STALE_NOTICE not in row.text

    asyncio.run(_check())


def test_at_65_columns_the_notice_wraps_with_every_word_in_order(wait_until) -> None:
    """A phone row cannot hold the 66-character notice on one line, so it wraps at spaces and the row grows to
    three lines: the `▲ ` and every word stay, in order, nothing elided (stale-screen rule 18)."""

    async def _check() -> None:
        with StubServer(_stub()) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(65, 30)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                row = screen.status
                await wait_until(pilot, lambda: row.size.height == 3, "the row to grow to three lines")
                lines = row.text.split("\n")
                assert all(len(line) == 65 for line in lines)
                assert "…" not in " ".join(lines[1:]), "the notice is never elided"
                assert " ".join(" ".join(lines[1:]).split()) == "▲ " + STALE_NOTICE

    asyncio.run(_check())


def test_a_taller_row_never_causes_a_size_request(wait_until, monkeypatch) -> None:
    """Terminal-size §1: the row's height is the surface's own rendering and never reaches the target. Auto size is
    on and this client holds the keyboard; the row grows and the canvas shows fewer rows, and no `resize` is sent,
    because the request still subtracts one status row and only an outer window change makes one."""
    monkeypatch.setenv("TSB_TUI_SIZE_AUTO_REQUEST", "true")

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.05, after_attach_frames=[PENDING])
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(65, 30)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                before = screen.fit_size()
                await wait_until(pilot, lambda: screen.status.size.height == 3, "the row to grow")
                rows = screen.canvas.size.height
                await pilot.pause(0.6)  # longer than `size.debounce_ms`
                assert rows == 30 - 3, "the canvas shows fewer rows while the choice is pending"
                assert screen.fit_size() == before
                assert stub.resizes == [] and not any(f.get("t") == "resize" for f in stub.text_frames)

    asyncio.run(_check())
