"""The canvas: what it paints, how often, and what it must never hold up.

Why these matter (Rule 9): the canvas is the only thing between the engine's grid and the operator's
eyes, and it shares one event loop with the pump that answers the server's pings.

* Painting only what changed, and no faster than `render.max_fps`, is what keeps a firehose of
  output (a `show tech-support`, a log tail) from costing a repaint per byte (D33).
* A held frame (synchronized output) must stay held, or a full-screen program's redraw tears on
  screen; and it must give up after a bounded time, or a program that dies mid-redraw freezes the
  picture for good.
* The cursor is how the operator knows whether their keys go anywhere: a filled block for the
  typist, an underline for a viewer (D21).
* Under a steady stream of output the pump must keep reading, because an unanswered WebSocket ping
  gets the client detached by the server (session-detach 9).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from stub_server import SlowChunkedApp, StubServer
from textual.app import App

from tsb_tui import config
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.terminal import TerminalScreen
from tsb_tui.term import VTBackend, load
from tsb_tui.theme import TermPalette
from tsb_tui.widgets import canvas as canvas_mod
from tsb_tui.widgets.canvas import Canvas

STUB_TARGET = "stub-target"
ACCENT = (0xE5, 0x6A, 0x4A)
STEEL = (0x7A, 0xA7, 0xD6)


@pytest.fixture(scope="module")
def lib(term_so: Path):
    return load(term_so)


class _Host(App):
    def __init__(self, canvas: Canvas) -> None:
        super().__init__()
        self._canvas = canvas

    def compose(self):
        yield self._canvas


def _cfg(**overrides: str) -> config.Config:
    return config.load(config_file=Path("/nonexistent"), environ={},
                       overrides={"size.auto_request": "false", **overrides})


def _canvas(lib, cols: int, rows: int, *, typist: bool = False, **overrides: str) -> Canvas:
    cfg = _cfg(**overrides)
    backend = VTBackend(lib, cols, rows, scrollback_bytes=cfg["term.scrollback_bytes"])
    return Canvas(backend, TermPalette(cfg, cfg.scheme), cfg, typist=typist)


def _cells(strip) -> list[tuple[int, str, object]]:
    """(first column, text, style) of each run in a rendered line."""
    out, column = [], 0
    for segment in strip:
        out.append((column, segment.text, segment.style))
        column += segment.cell_length
    return out


def _style_at(strip, column: int):
    for start, text, style in _cells(strip):
        if start <= column < start + len(text):
            return style
    raise AssertionError(f"no run covers column {column}")


async def _first_paint(pilot, canvas: Canvas) -> None:
    for _ in range(300):
        if canvas.paint_count >= 1:
            return
        await pilot.pause(0.01)
    raise AssertionError("the canvas never painted")


def test_only_dirty_rows_are_rebuilt(lib) -> None:
    """D33. A prompt echoing one character changes one line; rebuilding all 59 rows of a 196x59 grid
    for it would make every keystroke cost as much as a full-screen redraw."""

    async def _check() -> None:
        canvas = _canvas(lib, 40, 10)
        async with _Host(canvas).run_test(size=(40, 10)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"".join(f"\x1b[{i + 1};1Hrow{i}".encode() for i in range(10)))
            canvas.repaint_now()
            assert canvas.rebuilt_rows, "a full redraw must rebuild rows"

            canvas.feed(b"\x1b[4;1HX")  # one cell on row 3; the cursor leaves row 9
            canvas.repaint_now()
            assert 3 in canvas.rebuilt_rows
            assert set(canvas.rebuilt_rows) <= {3, 9}, canvas.rebuilt_rows

    asyncio.run(_check())


def test_repaints_are_capped_at_max_fps(lib) -> None:
    """D33. Output arrives in many small frames; the picture is repainted on a timer, never per frame.
    A burst of feeds in half a second must cost about `max_fps * 0.5` paints, not one per feed."""

    async def _check() -> None:
        canvas = _canvas(lib, 40, 10, **{"render.max_fps": "10"})
        async with _Host(canvas).run_test(size=(40, 10)) as pilot:
            await _first_paint(pilot, canvas)
            baseline, feeds = canvas.paint_count, 0
            started = time.monotonic()
            while time.monotonic() - started < 0.5:
                canvas.feed(b"x")
                feeds += 1
                await pilot.pause(0.001)
            elapsed = time.monotonic() - started
            paints = canvas.paint_count - baseline
            assert feeds >= 50, "the test must actually produce a burst"
            assert 1 <= paints <= elapsed * 10 + 2, (paints, feeds, elapsed)

    asyncio.run(_check())


def test_no_repaint_while_the_session_holds_synchronized_output(lib) -> None:
    """DT3. Mode 2026 means "I am redrawing; show the last finished frame". Painting through it
    shows the operator a half-drawn screen, which is exactly what the program asked us not to do."""

    async def _check() -> None:
        canvas = _canvas(lib, 40, 10)
        async with _Host(canvas).run_test(size=(40, 10)) as pilot:
            await _first_paint(pilot, canvas)
            baseline = canvas.paint_count
            canvas.feed(b"\x1b[?2026hHALF-DRAWN")
            await pilot.pause(0.3)  # many 30 fps ticks
            assert canvas.paint_count == baseline
            assert "HALF-DRAWN" not in canvas.render_line(0).text

            canvas.feed(b"\x1b[?2026l")
            for _ in range(300):
                if "HALF-DRAWN" in canvas.render_line(0).text:
                    break
                await pilot.pause(0.01)
            assert "HALF-DRAWN" in canvas.render_line(0).text

    asyncio.run(_check())


def test_a_hold_that_never_ends_is_given_up_on(lib, monkeypatch) -> None:
    """The engine's header tells a renderer to stop waiting after a second. A program that begins a
    synchronized update and dies must not freeze this client's picture for the rest of the session."""
    monkeypatch.setattr(canvas_mod, "HOLD_LIMIT_S", 0.2)

    async def _check() -> None:
        canvas = _canvas(lib, 40, 10)
        async with _Host(canvas).run_test(size=(40, 10)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"\x1b[?2026hSTUCK")
            for _ in range(300):
                if "STUCK" in canvas.render_line(0).text:
                    break
                await pilot.pause(0.01)
            assert "STUCK" in canvas.render_line(0).text

    asyncio.run(_check())


def test_a_background_attachment_is_parsed_but_not_painted(lib) -> None:
    """D33. A session the operator is not looking at still has to track its screen (so it is right the
    moment they switch to it) but must cost no paint while it is out of sight."""

    async def _check() -> None:
        canvas = _canvas(lib, 40, 10)
        async with _Host(canvas).run_test(size=(40, 10)) as pilot:
            await _first_paint(pilot, canvas)
            baseline = canvas.paint_count
            canvas.in_front = False
            canvas.feed(b"OUT-OF-SIGHT")
            await pilot.pause(0.3)
            assert canvas.paint_count == baseline
            assert any("OUT-OF-SIGHT" in line for line in canvas.backend.visible_rows())

            canvas.in_front = True
            for _ in range(300):
                if "OUT-OF-SIGHT" in canvas.render_line(0).text:
                    break
                await pilot.pause(0.01)
            assert "OUT-OF-SIGHT" in canvas.render_line(0).text

    asyncio.run(_check())


def test_the_typists_cursor_is_a_filled_accent_block_and_a_viewers_a_steel_underline(lib) -> None:
    """D21. The cursor is the operator's evidence of who types. A viewer whose cursor looked like the
    typist's would believe their keystrokes were going somewhere."""

    async def _check() -> None:
        canvas = _canvas(lib, 20, 3)
        async with _Host(canvas).run_test(size=(20, 3)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"ab")
            canvas.typist = True
            canvas.repaint_now()
            typist = _style_at(canvas.render_line(0), 2)
            assert typist.bgcolor.get_truecolor()[:3] == ACCENT
            assert not typist.underline

            canvas.typist = False
            viewer = _style_at(canvas.render_line(0), 2)
            # Phase 1: viewer cursor now has term_bg as background
            assert viewer.bgcolor.get_truecolor()[:3] == (0x0a, 0x0a, 0x0a)
            assert viewer.underline
            assert viewer.color.get_truecolor()[:3] == STEEL

    asyncio.run(_check())


def test_sgr_colours_and_attributes_reach_the_strips(lib) -> None:
    """A router's `show` output uses colour to say which interfaces are down. With the theme palette a
    session's red is the theme's red (design D29); bold, a truecolor foreground, a background colour
    and underline each survive from the escape sequence to the painted line."""

    async def _check() -> None:
        canvas = _canvas(lib, 30, 3)
        async with _Host(canvas).run_test(size=(30, 3)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"\x1b[31mR\x1b[0m\x1b[1;38;2;10;20;30mG\x1b[0m\x1b[44mB\x1b[0m\x1b[4mU\x1b[0m")
            canvas.repaint_now()
            line = canvas.render_line(0)
            assert line.text.startswith("RGBU")
            red, green, blue, underlined = (_style_at(line, x) for x in range(4))
            assert red.color.get_truecolor()[:3] == (0xE5, 0x53, 0x4B)  # theme.term.colors[1]
            assert green.bold and green.color.get_truecolor()[:3] == (10, 20, 30)
            assert blue.bgcolor.get_truecolor()[:3] == (0x6F, 0x9F, 0xD8)  # theme.term.colors[4]
            assert underlined.underline

    asyncio.run(_check())


def test_outer_palette_passes_colour_indexes_through(lib) -> None:
    """`theme.term.palette = outer` (D29): the operator's own terminal theme decides what red is, so
    the canvas must hand it the index, not a colour of its own."""

    async def _check() -> None:
        canvas = _canvas(lib, 30, 3, **{"theme.term.palette": "outer"})
        async with _Host(canvas).run_test(size=(30, 3)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"\x1b[31mR\x1b[0m\x1b[38;5;200mP")
            canvas.repaint_now()
            line = canvas.render_line(0)
            assert (_style_at(line, 0).color.number, _style_at(line, 1).color.number) == (1, 200)

    asyncio.run(_check())


def test_canvas_paints_its_background_on_every_default_cell_and_padding(lib) -> None:
    """Phase 1: B1. The canvas is the darkest layer: blank rows, padding and cells with no SGR background
    carry term_bg. Under "outer" with bg="scheme" or bg="outer", cells stay see-through. With a hex,
    that hex. Dimmed rows carry it too. A reversed blank cell swaps fg and bg.

    fails today: Strip.blank(width) and adjust_cell_length(width) carry no background."""

    async def _check() -> None:
        theme_bg = (0x0a, 0x0a, 0x0a)  # theme.bg="#0a0a0a"
        canvas = _canvas(lib, 60, 10)
        async with _Host(canvas).run_test(size=(40, 8)) as pilot:
            await _first_paint(pilot, canvas)
            # Blank row: not yet painted
            blank = canvas.render_line(5)
            assert all(seg.style.bgcolor and seg.style.bgcolor.get_truecolor()[:3] == theme_bg
                      for seg in blank), "blank row carries no background"

            # Feed output: one short line
            canvas.feed(b"hello")
            canvas.repaint_now()
            line = canvas.render_line(0)
            cells = _cells(line)
            # The text itself
            text_run = next((col, text, style) for col, text, style in cells if "hello" in text)
            assert text_run[2].bgcolor and text_run[2].bgcolor.get_truecolor()[:3] == theme_bg, (
                "cells with no SGR background carry no term_bg")
            # Padding after the text
            padding = [seg for seg in line if seg.text and seg.text[0] == " " and len(seg.text) > 5]
            assert padding and all(seg.style.bgcolor and seg.style.bgcolor.get_truecolor()[:3] == theme_bg
                                   for seg in padding), "padding carries no background"

            # Dimmed: ended session
            canvas.dimmed = True
            dimmed = canvas.render_line(0)
            assert all(seg.style.bgcolor and seg.style.bgcolor.get_truecolor()[:3] == theme_bg
                      for seg in dimmed if seg.text.strip()), "dimmed rows carry no background"
            canvas.dimmed = False

            # Reversed blank cell
            canvas.feed(b"\x1b[7m \x1b[0m")
            canvas.repaint_now()
            line = canvas.render_line(0)
            # The reversed cell is the second character (index 5 is where 'hello' ends, next cell is reversed)
            reversed_seg = _style_at(line, 5)
            # Inverse swaps: bg becomes the default fg, fg becomes term_bg
            assert reversed_seg.bgcolor.get_truecolor()[:3] == (0xe6, 0xe6, 0xe6), (
                "reversed blank: bg should be default fg")

    asyncio.run(_check())


def test_canvas_background_is_see_through_under_outer(lib) -> None:
    """Phase 1: TT4. Under palette="outer" with bg="scheme" or bg="outer", term_bg is None and cells
    stay see-through as they are today.

    fails today: (passes, because today the canvas is always see-through)"""

    async def _check() -> None:
        canvas = _canvas(lib, 40, 5, **{"theme.term.palette": "outer"})
        async with _Host(canvas).run_test(size=(40, 5)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"text")
            canvas.repaint_now()
            line = canvas.render_line(0)
            # No segment should have a background
            assert all(seg.style.bgcolor is None for seg in line), (
                "outer palette with bg=scheme should stay see-through")

    asyncio.run(_check())


def test_canvas_background_paints_a_given_hex(lib) -> None:
    """Phase 1: step 2. theme.term.bg="#123456" paints that colour, regardless of palette mode.

    fails today: the canvas paints no background."""

    async def _check() -> None:
        custom_bg = (0x12, 0x34, 0x56)
        canvas = _canvas(lib, 40, 5, **{"theme.term.bg": "#123456"})
        async with _Host(canvas).run_test(size=(40, 5)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"test")
            canvas.repaint_now()
            line = canvas.render_line(0)
            assert all(seg.style.bgcolor and seg.style.bgcolor.get_truecolor()[:3] == custom_bg
                      for seg in line if seg.text.strip()), "hex bg not painted"

    asyncio.run(_check())


def test_a_wide_character_takes_two_columns_and_the_row_keeps_the_grids_width(lib) -> None:
    """A wide glyph's spacer cell adds no text; counting it would shift everything after it a column
    right, and the rows below would no longer line up under it."""

    async def _check() -> None:
        canvas = _canvas(lib, 20, 3)
        async with _Host(canvas).run_test(size=(20, 3)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed("a漢\x1b[31mb".encode())  # the colour starts a run of its own at the `b`
            canvas.repaint_now()
            line = canvas.render_line(0)
            assert line.text.startswith("a漢b")
            assert line.cell_length == 20
            assert next(start for start, text, _ in _cells(line) if text.startswith("b")) == 3

    asyncio.run(_check())


def test_a_closed_canvas_frees_the_engine_and_ignores_late_bytes_and_ticks(lib) -> None:
    """A screen that is left while the pump still has a frame in flight must not feed, resize or
    read a terminal the engine has freed (a use-after-free in C, not an exception). Closing frees the
    grid once, stops painting, and makes every later call a no-op."""

    async def _check() -> None:
        canvas = _canvas(lib, 20, 3)
        async with _Host(canvas).run_test(size=(20, 3)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.close()
            painted = canvas.paint_count
            assert canvas.backend._terminal is None, "close() must free the engine's terminal"

            canvas.feed(b"late")
            canvas.feed_dump(10, 2, b"late dump")
            canvas.resize_grid(30, 5)
            canvas.repaint_now()
            canvas._tick()
            canvas.close()  # idempotent
            await pilot.pause(0.1)
            assert canvas.paint_count == painted

    asyncio.run(_check())


def test_the_window_follows_the_cursor_when_the_grid_is_wider_and_stays_put_when_told_to(lib) -> None:
    """D4. A grid wider than the canvas is panned, never reflowed; following the cursor keeps what
    is being typed in view, and `view.follow_cursor = off` leaves the window where the operator put it."""

    async def _check(follow: str, expected_view_x: int) -> None:
        canvas = _canvas(lib, 60, 5, **{"view.follow_cursor": follow})
        async with _Host(canvas).run_test(size=(20, 5)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"x" * 50)  # the cursor is now at column 50 of 60
            canvas.repaint_now()
            assert canvas.view_x == expected_view_x
            assert canvas.view_range.label == (
                f"cols {expected_view_x + 1}-{expected_view_x + 20} of 60 ▸")

    asyncio.run(_check("auto", 50 - 20 + 1))
    asyncio.run(_check("off", 0))


def test_under_a_steady_stream_the_pump_keeps_reading_and_answers_the_servers_ping() -> None:
    """session-detach 9. The server pings and detaches a client that does not answer. The ping sits
    in the stream behind the output already queued, so it is answered only if reading and applying
    output (parse, paint) never makes the pump fall behind: the answer's latency is the measure."""

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.002, max_chunks=1500, tail_chunks=5, ping_after_chunks=50)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(100, 30)) as pilot:
                app.client.login("fixture-user", "fixture-password")
                session_id = app.client.create_session(
                    STUB_TARGET, "new", "xterm-256color", 100, 30)["session"]["id"]
                app.push_screen(TerminalScreen(session_id))
                for _ in range(1000):
                    if stub.pong_received_at is not None:
                        break
                    await pilot.pause(0.01)
                assert stub.pong_received_at is not None, "the ping was never answered"
                assert stub.chunks_sent < stub.max_chunks, "the stream ended before the ping was answered"
                assert stub.pong_received_at - stub.ping_sent_at < 1.0

    asyncio.run(_check())
