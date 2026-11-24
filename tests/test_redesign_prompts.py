"""Redesign P5 — prompt decoration from shell marks (OSC 133) and the target's own prompt pattern.

Ruled O4/O7: a prompt cell is bold + underline, never recoloured; OSC 133 wins where the shell prints
it, the target's `prompt_pattern` is the fallback and is matched on the cursor row only; no historical
row is marked by guess; an invalid pattern is ignored and named once in that session's status line; no
cursor-line tint. Amendment 2 (L1-prompts): a pattern row that matched while the cursor sat on it keeps
its mark as it scrolls up, and rows from before this attach stay unmarked.

The decoration lives in the client renderer only: received bytes, grid size, copy text, recording and the
target's ANSI colours are untouched. Red on `91b4ec6`: the Python `Cell` carried no semantic content, the
canvas painted no prompt emphasis, and the interactive pane never read a target's pattern.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest
from input_server import InputRecordingApp
from stub_server import emit_live_output
from test_redesign_keys import login as key_login
from textual.app import App
from workspace_support import named_sessions, open_here

from tsb_tui import config
from tsb_tui.app import TsbTuiApp
from tsb_tui.drive.prompt_pattern import compile_pattern
from tsb_tui.term import VTBackend, load
from tsb_tui.theme import TermPalette, contrast_ratio
from tsb_tui.widgets.canvas import Canvas

SIZES = [(80, 24), (160, 45)]

# A prompt (`A`), the typed command (`B`), its output (`C`), the end (`D`), terminated by BEL.
PROMPT_SEQ = (b"\x1b]133;A\a" + b"prompt$ " + b"\x1b]133;B\a" + b"ls -l"
              + b"\x1b]133;C\a" + b"\r\nsome output" + b"\x1b]133;D;0\a")


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


def _canvas(lib, cols: int, rows: int, *, typist: bool = False, scrollback: int | None = None) -> Canvas:
    cfg = _cfg()
    sb = scrollback if scrollback is not None else cfg["term.scrollback_bytes"]
    backend = VTBackend(lib, cols, rows, scrollback_bytes=sb)
    return Canvas(backend, TermPalette(cfg, cfg.scheme), cfg, typist=typist)


def _cells(strip) -> list[tuple[int, str, object]]:
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


def _emphasised(style) -> bool:
    return style.bold and style.underline


def _run_canvas(lib, cols: int, rows: int, body, *, typist: bool = False, scrollback: int | None = None) -> None:
    """Mount a canvas, take its first paint, then run `body(canvas)` with each feed followed by a repaint."""

    async def _check() -> None:
        canvas = _canvas(lib, cols, rows, typist=typist, scrollback=scrollback)
        async with _Host(canvas).run_test(size=(cols, rows)) as pilot:
            for _ in range(300):
                if canvas.paint_count >= 1:
                    break
                await pilot.pause(0.01)
            else:
                raise AssertionError("the canvas never painted")
            body(canvas)
            canvas.close()

    asyncio.run(_check())


def _paint(canvas: Canvas) -> None:
    canvas.repaint_now()


# -- L1-prompts-osc: the shell's marks reach the glyph cells, emphasis applied --


@pytest.mark.parametrize("size", SIZES)
def test_osc_prompt_cells_get_bold_underline_and_input_output_do_not(lib, size):
    def body(canvas):
        canvas.feed(PROMPT_SEQ)
        _paint(canvas)
        assert _emphasised(_style_at(canvas.render_line(0), 0)), "the prompt glyphs are not emphasised"
        # The typed command after `prompt$ ` is marked INPUT, not prompt: no emphasis.
        assert not _emphasised(_style_at(canvas.render_line(0), len("prompt$ "))), \
            "the typed command got prompt emphasis"
        assert not _emphasised(_style_at(canvas.render_line(1), 0)), "output got prompt emphasis"

    _run_canvas(lib, size[0], size[1], body, typist=True)


def test_no_shell_marks_paint_no_emphasis(lib):
    def body(canvas):
        canvas.feed(b"just output\r\nplain lines\r\n")
        _paint(canvas)
        assert not any(_emphasised(s) for _, _, s in _cells(canvas.render_line(0)))

    _run_canvas(lib, 20, 5, body, typist=True)


def test_wide_glyphs_and_a_multiline_prompt_get_the_emphasis(lib):
    def body(canvas):
        # A prompt with a wide glyph, then a continuation prompt on the next row.
        canvas.feed(b"\x1b]133;A\a\xe4\xb8\x96prompt$ \x1b]133;B\als\x1b]133;C\a\r\n"
                    b"\x1b]133;A\acont> \x1b]133;B\als\x1b]133;C\a\r\n")
        _paint(canvas)
        assert _style_at(canvas.render_line(0), 0).bold, "the wide prompt glyph is not emphasised"
        assert _style_at(canvas.render_line(1), 0).underline, "the continuation prompt row is not emphasised"

    _run_canvas(lib, 20, 6, body, typist=True)


def test_prompt_decoration_never_recolours_and_nonprompt_ansi_is_faithful(lib):
    """Ruled O7: the decoration is bold + underline only. A prompt cell keeps its own foreground and background; a
    nonprompt ANSI colour on another row is shown as received, faithful."""

    def body(canvas):
        canvas.feed(PROMPT_SEQ + b"\r\n\x1b[31mred output\x1b[0m")
        _paint(canvas)
        palette = canvas._palette
        prompt = _style_at(canvas.render_line(0), 0)
        assert prompt.color == palette.default.color, "the prompt decoration changed the foreground"
        assert prompt.bgcolor == palette.default.bgcolor, "the prompt decoration changed the background"
        red = _style_at(canvas.render_line(2), 0)
        assert red.color != palette.default.color, f"the target's red is not faithful: {red}"
        assert not _emphasised(red), "a nonprompt colour got prompt emphasis"

    _run_canvas(lib, 20, 4, body, typist=True)


# -- L1-prompts-pattern: the target's pattern, matched on the cursor row only --


def test_the_target_pattern_marks_the_cursor_row_and_a_nonmatch_does_not(lib):
    def body(canvas):
        canvas.set_prompt_pattern(compile_pattern("prompt$ .*"))
        canvas.feed(b"prompt$ ls")  # cursor sits at the end of this row
        _paint(canvas)
        assert _emphasised(_style_at(canvas.render_line(0), 0)), "the matching cursor row is not marked"
        canvas.feed(b"\r\nnot a prompt")  # cursor moves to a row that does not match
        _paint(canvas)
        assert not any(_emphasised(s) for _, _, s in _cells(canvas.render_line(1)))

    _run_canvas(lib, 20, 6, body, typist=True)


def test_the_pattern_applies_on_the_cursor_row_only(lib):
    """A matching row the cursor never sat on (it scrolled past before this attach's pattern matched it) stays
    unmarked: no historical row is marked by guess."""

    def body(canvas):
        canvas.set_prompt_pattern(compile_pattern("prompt$ .*"))
        canvas.feed(b"prompt$ old\r\nfiller\r\nfiller")  # cursor ends on the last row, not the prompt
        _paint(canvas)
        assert not any(_emphasised(s) for _, _, s in _cells(canvas.render_line(0))), \
            "a row from before this attach got a pattern mark"

    _run_canvas(lib, 20, 6, body, typist=True)


def test_a_pattern_row_keeps_its_mark_as_it_scrolls_up(lib):
    """Amendment 2 (L1-prompts): a prompt row that matched while the cursor sat on it keeps its mark as it scrolls
    up into scrollback."""

    def body(canvas):
        canvas.set_prompt_pattern(compile_pattern("prompt$ .*"))
        canvas.feed(b"prompt$ ls")  # cursor on the prompt row: it is tracked
        _paint(canvas)
        assert _emphasised(_style_at(canvas.render_line(0), 0)), "the cursor row is not marked at first"
        canvas.feed(b"\r\nout1\r\nout2\r\nout3\r\nout4\r\n")  # pushes the prompt row into scrollback
        _paint(canvas)
        from tsb_tui.term import ghostty as _G
        canvas._backend.scroll_viewport(_G.SCROLL_VIEWPORT_TOP)
        _paint(canvas)
        top = canvas.render_line(0)
        assert "prompt$ ls" in top.text, "the prompt row did not scroll into the window"
        assert any(s.bold and s.underline for _, text, s in _cells(top) if "prompt$ ls" in text), \
            "the matched prompt row lost its mark as it scrolled up"

    _run_canvas(lib, 20, 4, body, typist=True)


def test_osc_wins_where_the_shell_marks_and_the_pattern_is_the_fallback(lib):
    """OSC overlap: a cursor row the shell already marked gets the decoration once (no double), and a row the shell
    did not mark gets the pattern fallback. Both end up bold + underline."""

    def body(canvas):
        canvas.set_prompt_pattern(compile_pattern("prompt$ .*"))
        # Row 0 is marked by the shell (OSC). Row 1 is the cursor row, matched only by the pattern.
        canvas.feed(b"\x1b]133;A\aprompt$ \x1b]133;B\als\x1b]133;C\a\r\nprompt$ ls")
        _paint(canvas)
        assert _emphasised(_style_at(canvas.render_line(0), 0)), "the OSC-marked prompt row is not emphasised"
        assert _emphasised(_style_at(canvas.render_line(1), 0)), "the pattern fallback row is not emphasised"

    _run_canvas(lib, 20, 6, body, typist=True)


def test_no_cursor_line_tint(lib):
    """Ruled O4: no universal cursor-line tint. The cursor cell carries the cursor's own look; a far cell on the
    same row keeps what it had."""

    def body(canvas):
        canvas.feed(b"prompt$ ls")  # cursor lands after `ls`
        _paint(canvas)
        cursor_cell = _style_at(canvas.render_line(0), len("prompt$ ls"))
        assert cursor_cell.bgcolor.name == canvas._palette.cursor_typist.colour, \
            "the cursor cell is not the cursor look"
        far = _style_at(canvas.render_line(0), 0)
        assert far.bgcolor == canvas._palette.default.bgcolor, "a non-cursor cell on the row is tinted"

    _run_canvas(lib, 20, 4, body, typist=True)


# -- L1-prompts-pattern in the interactive pane: the target record drives it --


def _mark_rows(canvas: Canvas) -> set[int]:
    return {y for y in range(canvas._backend.rows)
            if any(_emphasised(s) for _, _, s in _cells(canvas.render_line(y)))}


@pytest.mark.parametrize("size", SIZES)
def test_a_target_pattern_marks_its_prompt_rows_in_the_pane(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        # The target's own pattern (the field the contract publishes); no built-in device pattern exists.
        serve_app.targets["normal"]["prompt_pattern"] = "prompt$ .*"
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            sid, = named_sessions(url, serve_app, "normal")
            await open_here(app, pilot, wait_until, sid)
            slot = screen.slot_for(sid)
            emit_live_output(serve_app, serve_app.sessions[sid], b"prompt$ ls")
            await wait_until(pilot, lambda: 0 in _mark_rows(slot.canvas), "the prompt row to be marked")
            assert 1 not in _mark_rows(slot.canvas), "the output row got a prompt mark"
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_an_invalid_pattern_is_ignored_and_named_once_in_the_status_line(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        serve_app.targets["normal"]["prompt_pattern"] = "("  # RFC 9485 parse error
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            sid, = named_sessions(url, serve_app, "normal")
            await open_here(app, pilot, wait_until, sid)
            slot = screen.slot_for(sid)
            emit_live_output(serve_app, serve_app.sessions[sid], b"prompt$ ls\r\nout\r\n")
            # The status line names the pattern once. It is truncated to the row's width at 80 columns, so read the
            # message that feeds the status line (`slot.login_status`), not the rendered row.
            await wait_until(pilot, lambda: "prompt pattern ignored" in slot.login_status,
                             "the status line to name the invalid pattern once")
            assert _mark_rows(slot.canvas) == set(), "an invalid pattern marked a row"
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_a_target_with_no_prompt_pattern_shows_no_prompt_marks(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)  # the fixture target has no prompt_pattern and prints no shell marks
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            sid, = named_sessions(url, serve_app, "normal")
            await open_here(app, pilot, wait_until, sid)
            slot = screen.slot_for(sid)
            emit_live_output(serve_app, serve_app.sessions[sid], b"prompt$ ls\r\nout\r\n")
            await pilot.pause()
            assert _mark_rows(slot.canvas) == set(), "a target with no pattern marked a row"
    asyncio.run(check())


def test_a_pattern_loaded_after_the_prompt_is_painted_decorates_the_idle_prompt(lib):
    """QODO-121-1 #1: installing a pattern rebuilds the affected cached row's strip even when the engine reports no
    dirty row. Red on the current head: an idle prompt painted before the pattern arrives stays plain."""

    def body(canvas):
        canvas.feed(b"prompt$ ")  # the prompt is painted, cursor rests on it
        _paint(canvas)
        assert not _emphasised(_style_at(canvas.render_line(0), 0)), "the prompt was decorated before the pattern"
        canvas.set_prompt_pattern(compile_pattern("prompt$"))  # the target read lands after the paint
        _paint(canvas)
        assert _emphasised(_style_at(canvas.render_line(0), 0)), \
            "an idle prompt stayed undecorated after its pattern loaded"

    _run_canvas(lib, 20, 4, body, typist=True)


def test_shell_prompt_marks_win_over_a_tracked_pattern_extent(lib):
    """QODO-121-1 #2 (Ruled O4): when a row carries shell prompt marks, its pattern extent is dropped and only the
    shell's prompt cells are decorated — for a row tracked before the marks arrived as well as a new one. Red on the
    current head: the tracked extent keeps decorating shell-marked input."""

    def body(canvas):
        canvas.set_prompt_pattern(compile_pattern("pr.*"))
        canvas.feed(b"prlong")  # cursor row matches pr.* -> the tracked extent is the whole row (6 columns)
        _paint(canvas)
        assert _emphasised(_style_at(canvas.render_line(0), 0))
        # The shell then marks a short prompt `pr` and the rest as input on that same row.
        canvas.feed(b"\x1b[1;1H\x1b]133;A\apr\x1b]133;B\along\x1b]133;C\a")
        _paint(canvas)
        assert _emphasised(_style_at(canvas.render_line(0), 0)), "the shell prompt lost its mark"
        assert not _emphasised(_style_at(canvas.render_line(0), 2)), \
            "a shell-marked input cell kept the pattern extent's decoration"

    _run_canvas(lib, 20, 4, body, typist=True)


def test_a_resize_keeps_earlier_prompt_marks(lib):
    """QODO-121-1 #3: a resize reflows the content and a tracked reference follows its cell, so earlier prompt marks
    survive; only a fresh terminal or an attach dump drops them. Red on the current head: resize_grid cleared every
    pattern handle."""

    def body(canvas):
        canvas.set_prompt_pattern(compile_pattern("prompt$"))
        canvas.feed(b"prompt$ ")  # matched and tracked on the cursor row
        _paint(canvas)
        assert _emphasised(_style_at(canvas.render_line(0), 0))
        canvas.resize_grid(30, 6)  # a wider grid: the engine reflows, the tracked reference follows its cell
        canvas.feed(b"x\r\n")  # scroll the prompt row up
        _paint(canvas)
        from tsb_tui.term import ghostty as _G
        canvas._backend.scroll_viewport(_G.SCROLL_VIEWPORT_TOP)
        _paint(canvas)
        top = canvas.render_line(0)
        assert "prompt$ " in top.text, "the prompt row left the window"
        assert any(s.bold and s.underline for _, text, s in _cells(top) if "prompt$" in text), \
            "the resize erased the earlier prompt mark"

    _run_canvas(lib, 20, 4, body, typist=True)


def test_prompt_handles_are_freed_when_their_content_is_gone_and_kept_when_off_viewport(lib):
    """QODO-121-1 #5: a reference that no longer resolves to retained content is freed and dropped with its extent; a
    valid reference merely off the viewport is kept so its mark reappears on scroll-back. Red on the current head: a
    reference pruned by a dump reset is kept and scanned every repaint. (Note in the report: Ghostty retains every fed
    row in this harness, so the unbounded-growth red is not reproducible by feeding alone; the fix's observable effect
    is freeing refs whose content is gone.)"""

    def body(canvas):
        canvas.set_prompt_pattern(compile_pattern("prompt$"))
        canvas.feed(b"prompt$ ")  # the cursor sits on the prompt row, so the pattern matches and tracks it
        _paint(canvas)
        assert len(canvas._pattern_marks) == 1
        # Scroll the prompt off the viewport: the reference stays valid, so it is kept.
        canvas.feed(b"a\r\nb\r\nc\r\nd\r\ne\r\nf\r\n")
        _paint(canvas)
        assert len(canvas._pattern_marks) == 1, "a valid off-viewport mark was dropped"
        # A dump reset invalidates every tracked reference: the handle is freed and its extent dropped.
        canvas.feed_dump(20, 4, b"fresh grid")
        _paint(canvas)
        assert len(canvas._pattern_marks) == 0, "a mark whose content is gone was kept"

    _run_canvas(lib, 20, 4, body, typist=True)


def test_a_primary_prompt_mark_is_not_drawn_on_the_alternate_screen(lib):
    """QODO-121-1 #6: a tracked reference resolves only on the screen it was made on. While the alternate screen is
    active, primary-screen marks are not drawn; they show again when the primary screen returns. Red on the current
    head: tracked_viewport_row subtracted the active screen's offset without checking the owning screen, so a primary
    prompt reference decorated the app's text."""

    def body(canvas):
        canvas.set_prompt_pattern(compile_pattern("prompt$"))
        canvas.feed(b"prompt$ ")  # a primary-screen prompt, matched and tracked
        _paint(canvas)
        assert _emphasised(_style_at(canvas.render_line(0), 0))
        canvas.feed(b"\x1b[?1049h\r\napp text")  # enter the alternate screen; its top row is 'app text'
        _paint(canvas)
        assert not any(_emphasised(s) for _, _, s in _cells(canvas.render_line(0))), \
            "a primary-screen mark decorated the alternate screen"
        canvas.feed(b"\x1b[?1049l")  # return to the primary screen; the prompt mark shows again
        _paint(canvas)
        assert _emphasised(_style_at(canvas.render_line(0), 0)), \
            "the prompt mark did not return with the primary screen"

    _run_canvas(lib, 20, 6, body, typist=True)


def test_a_pattern_decorates_only_the_prompt_cells_it_matched(lib):
    """Ruled O4 (round 4): the prompt cells of a pattern-matched row are the cells the row held when the pattern
    matched; cells written later on that row (the echoed input) are not. Red on the current grouping, which decorates
    every cell of the matched row."""

    def body(canvas):
        canvas.set_prompt_pattern(compile_pattern("prompt$"))
        canvas.feed(b"prompt$ ")  # cursor sits on the prompt; the pattern matches "prompt$" (trimmed)
        _paint(canvas)
        assert _emphasised(_style_at(canvas.render_line(0), 0)), "the matched prompt is not decorated"
        canvas.feed(b"ls\r\nout\r\n")  # the echoed input lands on the same row, then output below
        _paint(canvas)
        assert _emphasised(_style_at(canvas.render_line(0), 0)), "the prompt lost its mark when the input was typed"
        assert not _emphasised(_style_at(canvas.render_line(0), len("prompt$ "))), \
            "the echoed input got the pattern decoration"
        assert not any(_emphasised(s) for _, _, s in _cells(canvas.render_line(1))), "output got the decoration"

    _run_canvas(lib, 20, 4, body, typist=True)


def test_under_drive_an_attach_reads_no_target_for_decoration(serve, wait_until):
    """Option D: the attach-time pattern read is the interactive pane's alone. Under drive (headless), decoration is
    invisible on screen text, so an attach reads no target — drive's send-gate lifecycle stays exactly as before.
    Red on `91b4ec6`: the pane worker read the target on every attach, under drive included."""

    async def check():
        serve_app, url = serve(InputRecordingApp())
        serve_app.targets["normal"]["prompt_pattern"] = "prompt$ .*"
        app = TsbTuiApp(url)
        reads: list[str] = []
        real = app.client.get_target
        app.client.get_target = lambda tid: (reads.append(tid), real(tid))[1]
        app.under_drive = True
        async with app.run_test(size=(160, 45)) as pilot:
            await key_login(pilot, app, wait_until)
            sid, = named_sessions(url, serve_app, "normal")
            await open_here(app, pilot, wait_until, sid)
            assert reads == [], f"under drive an attach read the target for decoration: {reads}"
        app.client.get_target = real
    asyncio.run(check())


def test_a_stale_target_response_marks_nothing(serve, wait_until):
    """A target record whose id does not match the slot's target is stale: it marks nothing."""
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        real = app.client.get_target
        app.client.get_target = lambda tid: {"id": "some-other-target", "type": "ssh",
                                             "prompt_pattern": "prompt$ .*"}
        async with app.run_test(size=(160, 45)) as pilot:
            screen = await key_login(pilot, app, wait_until)
            sid, = named_sessions(url, serve_app, "normal")
            await open_here(app, pilot, wait_until, sid)
            slot = screen.slot_for(sid)
            emit_live_output(serve_app, serve_app.sessions[sid], b"prompt$ ls\r\nout\r\n")
            await pilot.pause()
            assert _mark_rows(slot.canvas) == set(), "a stale target response marked a row"
        app.client.get_target = real
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_the_decoration_is_attachment_local(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        serve_app.targets["normal"]["prompt_pattern"] = "prompt$ .*"
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            first, second = named_sessions(url, serve_app, "normal", "sw-2")
            await open_here(app, pilot, wait_until, first, second)
            for sid in (first, second):
                emit_live_output(serve_app, serve_app.sessions[sid], b"prompt$ ls\r\nout\r\n")
            await pilot.pause()
            assert 0 not in _mark_rows(screen.slot_for(second).canvas), \
                "the decoration leaked to the other attachment"
    asyncio.run(check())


# -- L1-contrast / L1-geometry / L1-focus: both sizes, marked shell and unmarked device --

SNAP_DIR = Path(__file__).parent / "snapshots" / "redesign-p5"


def _snapshot(app, name):
    svg = re.sub(r"terminal-\d+", "terminal-p5", app.export_screenshot(title="P5", simplify=True))
    path = SNAP_DIR / f"{name}.svg"
    if os.environ.get("UPDATE_P5_SNAPSHOTS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg)
    assert path.read_text() == svg
    return svg


def _svg_runs(svg: str) -> list[tuple[str, str]]:
    """(class, decoded text) of each `<text>` run in the export. The export writes spaces as `&#160;`."""
    out = []
    for m in re.finditer(r'<text class="([^"]+)"[^>]*>(.*?)</text>', svg):
        out.append((m.group(1), m.group(2).replace("&#160;", " ")))
    return out


def _css_rule(svg: str, cls: str) -> str:
    """The `<style>` rule body for a run's class; rich's export writes bold as `font-weight: bold` and underline as
    `text-decoration: underline;` (rich console.export_svg)."""
    m = re.search(r"\." + re.escape(cls) + r"\s*\{([^}]*)\}", svg)
    return m.group(1) if m else ""


def _decorated(svg: str, startswith: str) -> tuple[bool, bool]:
    """(bold, underline) of the run whose text starts with `startswith`."""
    cls = next(c for c, t in _svg_runs(svg) if t.startswith(startswith))
    rule = _css_rule(svg, cls)
    return ("font-weight: bold" in rule, "text-decoration: underline" in rule)


def _hexed(rgb):
    return "#" + "".join(f"{c:02x}" for c in rgb)


@pytest.mark.parametrize("size", SIZES)
def test_prompt_decoration_meets_contrast_and_the_snapshots_hold(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            sid, = named_sessions(url, serve_app, "normal")
            await open_here(app, pilot, wait_until, sid)
            slot = screen.slot_for(sid)
            # The marked fixture is an OSC 133 shell: the prompt (`prompt$ `) is marked PROMPT and the typed command
            # (`ls`) INPUT. The decoration is prompt-only. A target pattern is whole-row matched (Ruled O4), so it
            # cannot separate the prompt from its command; the marked picture comes from the shell's own marks.
            emit_live_output(serve_app, serve_app.sessions[sid],
                             b"\x1b]133;A\aprompt$ \x1b]133;B\als\x1b]133;C\a")
            await pilot.pause()
            # The decoration never recolours, so the chrome text rule applies to a prompt cell: 4.5:1 against its
            # own background.
            for _, text, style in _cells(slot.canvas.render_line(0)):
                if text.startswith("prompt$") and _emphasised(style):
                    fg = _hexed(style.color.get_truecolor())
                    bg = _hexed(style.bgcolor.get_truecolor())
                    assert contrast_ratio(fg, bg) >= 4.5, (fg, bg)
            region = slot.canvas.region
            assert region.right <= size[0] and region.bottom <= size[1], "the canvas geometry overflowed the size"
            marked_svg = _snapshot(app, f"prompt-marked-{size[0]}x{size[1]}")
            # The marked baseline shows the decoration, measured in the SVG: the prompt run carries bold + underline,
            # the input run carries neither.
            assert _decorated(marked_svg, "prompt$") == (True, True), \
                "the marked baseline's prompt run is not bold + underline"
            assert _decorated(marked_svg, "ls") == (False, False), \
                "the marked baseline's input run got the decoration"
            # The unmarked device (no pattern) shows no decoration.
            second, = named_sessions(url, serve_app, "sw-2")
            await open_here(app, pilot, wait_until, second)
            emit_live_output(serve_app, serve_app.sessions[second], b"prompt$ ls\r\nout\r\n")
            await pilot.pause()
            assert _mark_rows(screen.slot_for(second).canvas) == set(), "the unmarked device got a decoration"
            unmarked_svg = _snapshot(app, f"prompt-unmarked-{size[0]}x{size[1]}")
            assert _decorated(unmarked_svg, "prompt$") == (False, False), \
                "the unmarked baseline's prompt run carries a decoration"
    asyncio.run(check())


def test_scrolling_into_history_marks_no_unrelated_row(lib):
    """QODO-121-2 #1: `track_matching_prompt` read the active-area cursor Y as a viewport row, so scrolling into
    history could mark an unrelated row. Fix: read the render-state cursor under the same lock, require it is in the
    viewport (no match while the view is scrolled away from the live cursor), and use its viewport coordinates for both
    the row read and the tracked reference. Red on the current head: rows fed before the pattern loaded acquire a mark
    once the view scrolls away from the live cursor."""

    def body(canvas):
        # Feed prompt rows BEFORE any pattern is set: this history must stay unmarked.
        canvas.feed(b"prompt$ a\r\nprompt$ b\r\nprompt$ c\r\nprompt$ d\r\n")
        canvas.set_prompt_pattern(compile_pattern("prompt$ .*"))
        canvas.feed(b"prompt$ live")  # the live cursor sits on this prompt row in the active area
        _paint(canvas)
        from tsb_tui.term import ghostty as _G
        canvas._backend.scroll_viewport(_G.SCROLL_VIEWPORT_TOP)  # view scrolls into history, away from the live cursor
        _paint(canvas)
        # The rows on view are history fed before the pattern; none may acquire a mark.
        for y in range(canvas._backend.rows):
            assert not any(_emphasised(s) for _, _, s in _cells(canvas.render_line(y))), \
                "scrolling into history marked an unrelated row"

    _run_canvas(lib, 20, 4, body, typist=True)


def test_a_narrow_resize_decorates_every_wrapped_prompt_row(lib):
    """QODO-121-2 #2: a prompt mark was one tracked row plus a column extent, so a reflow that wrapped the prompt
    decorated only its first row. Fix: the mark covers the prompt's cells — track the prompt's first and last cells
    (two references, both made under the same lock as the match) and decorate the cells between them in reading order,
    on every row they span. Red on the current head: after narrowing until the prompt wraps, its continuation row stays
    plain."""

    def body(canvas):
        canvas.set_prompt_pattern(compile_pattern("prompt$ abcdef"))
        canvas.feed(b"prompt$ abcdef")  # cursor on the prompt row; matched and tracked
        _paint(canvas)
        assert _emphasised(_style_at(canvas.render_line(0), 0))
        canvas.resize_grid(10, 4)  # narrow enough that the 14-column prompt wraps onto two rows
        _paint(canvas)
        texts = [canvas.render_line(y).text for y in range(canvas._backend.rows)]
        first = next(y for y, text in enumerate(texts) if text.startswith("prompt$"))
        cont = first + 1  # the wrapped remainder ("cdef") lands on the next row
        assert texts[cont].startswith("cdef"), "the prompt did not wrap onto a continuation row"
        assert _emphasised(_style_at(canvas.render_line(first), 0)), "the first wrapped row lost its prompt mark"
        assert _emphasised(_style_at(canvas.render_line(cont), 0)), \
            "the wrapped prompt's continuation row is not decorated"

    _run_canvas(lib, 20, 4, body, typist=True)


def test_a_partly_visible_wrapped_prompt_keeps_decoration_on_its_visible_cells(lib):
    """QODO-121-3: `add()` made no spans when either tracked end of a wrapped prompt lay outside the viewport, so
    scrolling left the remaining visible prompt cells plain even though the mark was retained. Fix (the ruling): resolve
    both ends in whole-screen coordinates (on their own screen, as round 1 ruled) even when off the viewport, take the
    reading-order span between them, and decorate only the part inside the viewport. Red on the current head: a wrapped
    prompt whose first or last end is scrolled out loses all its decoration."""

    def body(canvas):
        canvas.set_prompt_pattern(compile_pattern("prompt$ abcdef"))
        canvas.feed(b"prompt$ abcdef")  # tracked on the wide grid: first cell column 0, last cell column 13
        _paint(canvas)
        canvas.resize_grid(7, 1)  # one row per line: the prompt wraps onto two screen rows
        _paint(canvas)
        canvas.feed(b"x1\r\nx2\r\nx3\r\n")  # push the prompt into scrollback so it can be scrolled to
        _paint(canvas)
        from tsb_tui.term import ghostty as _G
        # Only the first (prompt) row visible: its prompt cells are decorated, the last end is off the viewport.
        canvas._backend.scroll_viewport(_G.SCROLL_VIEWPORT_ROW, 0)
        _paint(canvas)
        row = canvas.render_line(0)
        assert "prompt$" in row.text, "the first wrapped row is not on view"
        assert _emphasised(_style_at(row, 0)), "the visible first row lost its prompt decoration"
        # Only the continuation row visible: its prompt cells are decorated, the first end is off the viewport.
        canvas._backend.scroll_viewport(_G.SCROLL_VIEWPORT_ROW, 1)
        _paint(canvas)
        row = canvas.render_line(0)
        assert "abcdef" in row.text, "the continuation row is not on view"
        assert _emphasised(_style_at(row, 0)), "the visible continuation row lost its prompt decoration"

    _run_canvas(lib, 20, 1, body, typist=True)
