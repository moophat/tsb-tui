"""The stale notice while a choice is pending: a banner over the canvas, and a status row that stays one row
(stale-screen rule 18; design §2.2 principle 3, §2.6, F7, F12).

Why these matter (Rule 9): a surface that renders text MUST show the stale notice line, whole. A client that is
not the keyboard holder never sees the dialog, so for it the banner is the only place the notice appears, and a
notice cut or lost to the hints would leave that client typing into a screen that may be wrong without being
told. The notice must not cost the row its height either: the row is one row at every width, and `● REC` and
`≡ Menu` stay on it, where the wrapped notice used to push them off. Servers: the TUI's own stub (DT7), as a
client that does not hold the keyboard.
"""

from __future__ import annotations

import asyncio

from prompt_support import NOW, iso, open_stub_terminal, validated
from stub_server import SlowChunkedApp, StubServer
from tsb_tui.app import TsbTuiApp
from tsb_tui.widgets.stale_banner import STALE_NOTICE

PENDING = {"t": "stale", "v": 1, "session_id": "stub-session-1", "state": "pending", "cause": "grid_lost",
           "at": iso(NOW)}
CHOSEN = {"t": "stale", "v": 1, "session_id": "stub-session-1", "state": "chosen", "cause": "grid_lost",
          "at": iso(NOW), "choice": "continue", "by": {"client_id": "stub-other", "user": "fixture-other"}}


def _stub() -> SlowChunkedApp:
    validated(PENDING)
    validated(CHOSEN)
    return SlowChunkedApp(delay_s=0.05, after_attach_frames=[PENDING], holder_is_client=False,
                          respond_to={"resize": [CHOSEN]})


def _banner_text(app) -> str:
    """The banner's words as the screen shows them, rows joined (the rail's four columns are not the canvas)."""
    rows = [strip.text[4:] for strip in app.screen._compositor.render_strips()]
    return " ".join(" ".join(rows[:3]).split())


def test_the_banner_carries_the_contracts_notice_whole_at_every_width_and_the_row_stays_one_row(
        wait_until) -> None:
    """The notice is the contract's own sentence (rule 18), wrapped at spaces on a phone and never cut; the status
    row is one row at 65, 120 and 200 columns and still has the pill, who is choosing and the Menu, so it never
    adds chrome (§2.6)."""

    async def _check(width: int) -> None:
        with StubServer(_stub()) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(width, 30)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: screen.pump.state.stale_state == "pending", "the frame")
                await wait_until(pilot, lambda: f"▲ {STALE_NOTICE}" in _banner_text(app), "the banner")
                assert screen.status.size.height == 1
                row = screen.status.text
                assert "\n" not in row and len(row) == width, row
                assert "‹" in row and "▲ stale" in row and "≡ Menu" in row, row
                if width >= 120:
                    assert "fixture-other is choosing · input refused" in row, row

    for width in (65, 120, 200):
        asyncio.run(_check(width))


def test_the_banner_goes_when_the_choice_is_made(wait_until) -> None:
    """The notice is true only while a choice is outstanding: once someone chose, a banner left up would tell
    the operator the screen may be stale when the core has said it was dealt with."""

    async def _check() -> None:
        with StubServer(_stub()) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(70, 30)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: STALE_NOTICE in _banner_text(app), "the banner")
                await screen.pump.resize(60, 20, timeout=0)  # the stub answers with `chosen`
                await wait_until(pilot, lambda: STALE_NOTICE not in _banner_text(app), "the banner to go")
                assert "▲ stale" not in screen.status.text and screen.status.size.height == 1

    asyncio.run(_check())


def test_the_banner_never_causes_a_size_request_or_takes_canvas_rows(wait_until, monkeypatch) -> None:
    """Terminal-size §1: what the surface draws is its own business and never reaches the target. Auto size is on
    and this client holds the keyboard; the banner overlays the canvas, so the canvas keeps its rows, the size the
    TUI would ask for does not move, and no `resize` is sent."""
    monkeypatch.setenv("TSB_TUI_SIZE_AUTO_REQUEST", "true")

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.05, after_attach_frames=[PENDING])
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(65, 30)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                before, rows = screen.fit_size(), screen.canvas.size.height
                await wait_until(pilot, lambda: STALE_NOTICE in _banner_text(app), "the banner")
                await pilot.pause(0.6)  # longer than `size.debounce_ms`
                assert screen.canvas.size.height == rows == 30 - 1
                assert screen.fit_size() == before
                assert stub.resizes == [] and not any(f.get("t") == "resize" for f in stub.text_frames)

    asyncio.run(_check())
