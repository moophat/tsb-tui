"""Copy from the scroll view: the operator selects whole lines and the TUI writes them to the outer terminal as one OSC 52
write; a session's own OSC 52 never does.

Why it matters (Rule 9): the clipboard is the operator's, and the text on it must be what they chose and what the
screen held when they chose it: never more than the lines marked, never a different line because the history moved
under the mark, never anything a target asked for. So the tests read the bytes the outer terminal is sent (the driver's
write, which is where `copy_out` ends), the cells the canvas paints as selected, and the rows the engine holds, not the
screen's own say-so. The rows' text comes from the engine's formatter in every expectation, independently of the cell
reads the build uses.

Rulings the tests pin: whole lines; `v` starts at the top row on view (or at the current match) and toggles; the arrows
and PageUp / PageDown extend; `y` or Enter copies and the selection stays until Esc; a press and release on one row
does nothing; a drag replaces a keyboard selection and copies on release; with `copy.join_wrapped` a selected row the
engine marks as soft-wrapped into a selected next row is joined to it with no newline, and what is sent is only what
the selection covers; the rows are read again at copy and a difference sends nothing.
"""

from __future__ import annotations

import asyncio
import base64

import pytest
from rich.cells import cell_len
from test_scroll_search import _fill, _row_of, _search
from test_scroll_view import GRID_ROWS, _scene
from textual.events import MouseMove
from tsb_tui.term import VTBackend, load

BLANK = "nothing to copy: the selection is blank"


def _sent(n: int) -> str:
    return f"Sent {n} {'line' if n == 1 else 'lines'} to your terminal's clipboard."


NOTHING = "nothing selected: v to select"
MOVED = "the history moved under the selection; select again"
OFF = "copying is off (copy.osc52); use your terminal's own selection with the mouse off or Shift held"
SCROLLBACK = 1 << 20


@pytest.fixture(scope="module")
def lib(term_so):
    return load(term_so)


def _osc52(text: str) -> str:
    return f"\x1b]52;c;{base64.b64encode(text.encode('utf-8')).decode('ascii')}\a"


def _watch(scene, monkeypatch) -> list[str]:
    """Every OSC 52 write the outer terminal is sent from now on (the driver's write is where `copy_out` ends)."""
    sent: list[str] = []
    driver = scene.app._driver
    real = driver.write

    def write(data: str) -> None:
        if data.startswith("\x1b]52;"):
            sent.append(data)
        real(data)

    monkeypatch.setattr(driver, "write", write)
    return sent


def _lines(scene, first: int, last: int) -> list[str]:
    """The engine's own text of rows `first`..`last` (the formatter's, trailing blanks trimmed)."""
    return scene.canvas.backend.grid_lines()[first:last + 1]


async def _top_at(scene, row: int) -> None:
    """The viewport with engine row `row` at its top."""
    scene.canvas.scroll_to_row(row)
    await scene.wait_until(scene.pilot, lambda: scene.position().offset == row, f"row {row} at the top")
    await scene.numbers()


def _marked_now(scene) -> list[str]:
    """The text of the lines the canvas paints as selected: the row's first and last cells of the grid's width are both
    reversed (the selection's style, laid over the whole row; a match is reversed and bold inside a row, and the cursor
    is one cell)."""
    found = []
    canvas = scene.canvas
    cols = canvas.backend.cols
    for y in range(canvas.size.height):
        strip = canvas.render_line(y)
        shown, column = [], 0
        for text, style, _ in strip:
            if column < cols and text:
                shown.append(style)
            column += cell_len(text)
        if shown and shown[0] and shown[0].reverse and shown[-1] and shown[-1].reverse:
            found.append(strip.text.rstrip())
    return found


async def _marked(scene) -> list[str]:
    await scene.numbers()
    return _marked_now(scene)


async def _wait_marked(scene, expected: list[str]) -> None:
    await scene.wait_until(scene.pilot, lambda: _marked_now(scene) == expected, f"the marked lines {expected!r}")


async def _wait_note(scene, note: str) -> None:
    await scene.wait_until(scene.pilot, lambda: note in scene.screen.status.text, f"the note {note!r}")


def _toasts(scene) -> list[str]:
    return [n.message for n in scene.app._notifications._notifications.values()]


async def _wait_toast(scene, message: str) -> None:
    await scene.wait_until(scene.pilot, lambda: message in _toasts(scene), f"the toast {message!r}")


async def _move_held(scene, y: int) -> None:
    """Motion to canvas row `y` with the left button held, as a terminal reports a drag (`pilot.hover` is button-free)."""
    await scene.pilot.pause()
    await scene.pilot._post_mouse_events([MouseMove], widget=scene.canvas, offset=(5, y), button=1)


async def _drag(scene, y_from: int, y_to: int) -> None:
    """Press at canvas row `y_from`, move to `y_to` with the button held, release there."""
    canvas = scene.canvas
    await scene.pilot.mouse_down(canvas, offset=(5, y_from))
    if y_to != y_from:
        await _move_held(scene, y_to)
    await scene.pilot.mouse_up(canvas, offset=(5, y_to))
    await scene.pilot.pause()


async def _select(scene, first_row: int, count: int) -> None:
    """The view open with the rows `first_row`.. selected, by the keys: `v` at the top, then the down arrow."""
    await scene.open_view()
    await _top_at(scene, first_row)
    await scene.pilot.press("v", *(["down"] * (count - 1)))
    await _wait_marked(scene, _lines(scene, first_row, first_row + count - 1))


def test_v_then_the_arrows_then_y_sends_one_osc52_write_of_the_selected_lines_and_the_selection_stays(
        serve, wait_until, screen_text, monkeypatch) -> None:
    """Select three lines in the view and `y`: the outer terminal is sent exactly one `ESC]52;c;<base64>BEL` whose text
    is those three lines with `\\n` between them and none after; the toast counts the lines sent; the view stays open and
    the selection stays marked, so a second `y` sends the same write again. Esc clears the selection first, and a second
    Esc closes the view."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            row = _row_of(scene, "L100")
            await _select(scene, row, 3)
            text = "\n".join(_lines(scene, row, row + 2))
            assert text == "L100 filler\nL101 filler\nL102 filler", "the expectation is the three lines, not more"

            await scene.pilot.press("y")
            await _wait_toast(scene, _sent(3))
            assert sent == [_osc52(text)], "one write, the exact bytes, no trailing newline"
            assert scene.screen.scroll_view_open and await _marked(scene) == text.split("\n"), "the selection stays"

            await scene.pilot.press("y")
            await wait_until(scene.pilot, lambda: len(sent) == 2, "the second copy")
            assert sent[1] == sent[0], "a second y copies the same selection again"

            await scene.pilot.press("escape")
            await _wait_marked(scene, [])
            assert scene.screen.scroll_view_open, "the first Esc clears the selection and keeps the view"
            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "the second Esc to close the view")
            assert len(sent) == 2, "nothing more was sent"
            await scene.settled()
            assert scene.serve_app.received(scene.client_id) == [], "no key of the selection reached the session"

    asyncio.run(_check())


def test_enter_copies_too_and_the_hints_follow_whether_a_selection_exists(serve, wait_until, screen_text,
                                                                         monkeypatch) -> None:
    """Enter copies like `y`. The view's hints offer `v Select` until a selection exists, then `y Copy`, with `Esc Live`
    last in both."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            await scene.open_view()
            text = scene.screen.status.text
            assert "v Select" in text and "y Copy" not in text
            assert text.index("/ Search") < text.index("v Select") < text.index("Esc Live")
            row = _row_of(scene, "L100")
            await _top_at(scene, row)
            await scene.pilot.press("v")
            await wait_until(scene.pilot, lambda: "y Copy" in scene.screen.status.text, "the copy hint")
            text = scene.screen.status.text
            assert "v Select" not in text and text.index("y Copy") < text.index("Esc Live")

            await scene.pilot.press("enter")
            await _wait_toast(scene, _sent(1))
            assert sent == [_osc52("L100 filler")]
            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: "v Select" in scene.screen.status.text, "the select hint again")

    asyncio.run(_check())


def test_y_or_enter_with_no_selection_sends_nothing_and_v_toggles_the_selection(serve, wait_until, screen_text,
                                                                               monkeypatch) -> None:
    """With nothing selected `y` and Enter send nothing and say "nothing selected: v to select" (they are not the
    dropped-key note); `v` selects, and `v` again takes the selection off."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            await scene.open_view()
            await _top_at(scene, _row_of(scene, "L100"))
            for key in ("y", "enter"):
                await scene.pilot.press("x")  # a different note first, so each note below is this key's own
                await _wait_note(scene, "Esc to return to live, then type")
                await scene.pilot.press(key)
                await _wait_note(scene, NOTHING)
            assert sent == [] and await _marked(scene) == []

            await scene.pilot.press("v")
            await _wait_marked(scene, ["L100 filler"])
            await scene.pilot.press("v")
            await _wait_marked(scene, [])
            assert scene.screen.scroll_view_open
            await scene.settled()
            assert scene.serve_app.received(scene.client_id) == []

    asyncio.run(_check())


def test_v_starts_at_the_top_row_on_view_or_at_the_current_match_and_a_search_keeps_the_selection(
        serve, wait_until, screen_text, monkeypatch) -> None:
    """`v` starts on the top row on view; with a match shown it starts on the current match's row. `/`, `n` and `N`
    leave a selection as it is (it stays marked and copies what it covered)."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            tags = {40: "needle early", 270: "needle late"}
            await _fill(scene, 300, tags)
            await scene.open_view()
            await scene.pilot.press("pageup")
            await wait_until(scene.pilot, lambda: not scene.at_bottom(), "the view to move up")
            top = scene.position().offset
            await scene.pilot.press("v")
            await _wait_marked(scene, _lines(scene, top, top))
            await scene.pilot.press("v")
            await _wait_marked(scene, [])
            await scene.pilot.press("pagedown")
            await wait_until(scene.pilot, scene.at_bottom, "the view back at the bottom")

            await _search(scene, "needle")
            await wait_until(scene.pilot, lambda: scene.screen._search_at is not None, "a current match")
            current = scene.screen._search_at[0]
            await scene.pilot.press("v")
            await _wait_marked(scene, _lines(scene, current, current))
            line = _lines(scene, current, current)[0]
            assert line.startswith("L270"), "the nearest older match is the one shown"

            await scene.pilot.press("n")  # the next older match, which moves the view and not the selection
            await wait_until(scene.pilot, lambda: scene.screen._search_at[0] != current, "the next match")
            await scene.pilot.press("N")
            await wait_until(scene.pilot, lambda: scene.screen._search_at[0] == current, "back on the match")
            await _search(scene, "early")
            await scene.pilot.press("y")
            await _wait_toast(scene, _sent(1))
            assert sent == [_osc52(line)], "the selection was kept across the searches and is what was copied"

    asyncio.run(_check())


def test_v_in_a_window_shorter_than_the_grid_starts_at_the_first_row_actually_on_view(
        serve, wait_until, screen_text, monkeypatch) -> None:
    """The window is shorter than the grid and panned down to the live cursor: the first row on view is the viewport's
    offset plus the canvas's vertical pan, so `v` starts there (and not on a row above the window, where the mark would
    be out of sight and a copy would send lines the operator never saw)."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text, size=(100, 14)) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            await wait_until(scene.pilot, lambda: scene.canvas.painted, "the canvas to paint")
            await scene.open_view()
            canvas = scene.canvas
            assert canvas.size.height < GRID_ROWS and canvas.view_y > 0, "the window is panned below the grid's top"
            first = scene.position().offset + canvas.view_y
            on_view = _lines(scene, first, first)
            assert on_view != _lines(scene, first - 1, first - 1)
            await scene.pilot.press("v")
            await _wait_marked(scene, on_view)
            assert scene.screen._selection.lo == first
            await scene.pilot.press("y")
            await _wait_toast(scene, _sent(1))
            assert sent == [_osc52(on_view[0])]

    asyncio.run(_check())


def test_pageup_and_pagedown_move_the_end_of_the_selection_a_page_and_the_view_follows(
        serve, wait_until, screen_text, monkeypatch) -> None:
    """PageUp moves the selection's loose end a page toward older output (the anchor stays), scrolling so that end is
    on screen; PageDown moves it back. A copy after PageUp holds one more line than a page."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            row = _row_of(scene, "L200")
            await scene.open_view()
            await _top_at(scene, row)
            await scene.pilot.press("v", "pageup")
            await wait_until(scene.pilot, lambda: scene.position().offset == row - GRID_ROWS, "the view to follow")
            await scene.pilot.press("y")
            await _wait_toast(scene, _sent(GRID_ROWS + 1))
            assert sent == [_osc52("\n".join(_lines(scene, row - GRID_ROWS, row)))]

            await scene.pilot.press("pagedown")
            await _wait_marked(scene, _lines(scene, row, row))
            await scene.pilot.press("up")
            await _wait_marked(scene, _lines(scene, row - 1, row))
            assert scene.position().offset <= row - 1, "the end is on screen"

    asyncio.run(_check())


def test_a_selection_stays_while_output_arrives_and_copies_the_lines_it_covered(serve, wait_until, screen_text,
                                                                              monkeypatch) -> None:
    """Copying from a busy log is the common case: new lines arriving leave the selection where it is, marked, and the
    copy sends the lines that were selected."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            row = _row_of(scene, "L150")
            await _select(scene, row, 2)
            await _fill(scene, 340, {}, first=300)
            await _wait_marked(scene, _lines(scene, row, row + 1))
            assert _lines(scene, row, row + 1) == ["L150 filler", "L151 filler"]
            await scene.pilot.press("y")
            await _wait_toast(scene, _sent(2))
            assert sent == [_osc52("L150 filler\nL151 filler")]

    asyncio.run(_check())


def test_a_row_the_program_rewrote_under_the_selection_sends_nothing_clears_it_and_says_so(
        serve, wait_until, screen_text, monkeypatch) -> None:
    """The rows are read again at copy: if any differs from what they were when selected, nothing is sent, the selection
    is cleared and the note says the history moved. Here the rewritten row is the selection's middle one, which the check
    on each output (the first and last rows) cannot see."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            await scene.open_view()
            top = scene.position().offset  # the live screen's top row, with the viewport at the bottom
            await scene.pilot.press("v", "down", "down")
            await _wait_marked(scene, _lines(scene, top, top + 2))
            scene.emit(b"\x1b[2;1HREWRITTEN")
            await wait_until(scene.pilot, lambda: scene.canvas.backend.grid_lines()[top + 1].startswith("REWRITTEN"),
                             "the rewrite on the grid")
            assert await _marked(scene) != [], "the first row is as it was, so the selection is still there"
            await scene.pilot.press("y")
            await _wait_note(scene, MOVED)
            await _wait_marked(scene, [])
            assert sent == [] and scene.screen.scroll_view_open
            await scene.pilot.press("y")
            await _wait_note(scene, NOTHING)

    asyncio.run(_check())


def test_a_change_between_two_row_reads_of_a_copy_sends_nothing_and_clears_the_selection(
        serve, wait_until, screen_text, monkeypatch) -> None:
    """Each row of a copy is read on its own, so the feed can get in between two reads. A row already read and then
    rewritten would leave the text sent a screen that never existed: the backend says it changed across the read, and
    nothing is sent. Here the first selected row is rewritten after it was read, and the later rows read as they were."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            await scene.open_view()
            top = scene.position().offset
            await scene.pilot.press("v", "down", "down")
            await _wait_marked(scene, _lines(scene, top, top + 2))

            backend = scene.canvas.backend
            real = backend.row_texts
            reads: list[int] = []

            def rewriting(first: int, last: int):
                rows = real(first, last)
                reads.append(first)
                if len(reads) == 2:  # the second row has been read: the first one, read before it, changes now
                    backend.feed(b"\x1b[1;1HREWRITTEN")
                return rows

            monkeypatch.setattr(backend, "row_texts", rewriting)
            await scene.pilot.press("y")
            await _wait_note(scene, MOVED)
            await _wait_marked(scene, [])
            assert reads[:2] == [top, top + 1], "the copy read the rows one at a time, oldest first"
            assert sent == [] and scene.screen.scroll_view_open

    asyncio.run(_check())


def test_a_selections_first_row_is_checked_on_each_output_and_the_marks_never_sit_on_other_text(
        serve, wait_until, screen_text, monkeypatch) -> None:
    """When output changes the text of the selection's first row, the selection is cleared at once (not at the next
    copy), so the mark is never painted on other text; the note says why."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            await scene.open_view()
            top = scene.position().offset
            await scene.pilot.press("v", "down")
            await _wait_marked(scene, _lines(scene, top, top + 1))
            scene.emit(b"\x1b[1;1HCHANGED")
            await _wait_note(scene, MOVED)
            await _wait_marked(scene, [])  # no mark on the rewritten row
            await scene.pilot.press("y")
            await _wait_note(scene, NOTHING)
            assert sent == []

    asyncio.run(_check())


def test_the_check_on_each_output_compares_the_last_selected_row_as_well_as_the_first(
        serve, wait_until, screen_text, monkeypatch) -> None:
    """A trim can land a first row that is identical to the one selected while the last row is another's: both ends are
    compared with their stored text, and the selection is cleared as moved when either differs."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            await scene.open_view()
            top = scene.position().offset
            screen, backend = scene.screen, scene.canvas.backend

            await scene.pilot.press("v", "down", "down")
            await _wait_marked(scene, _lines(scene, top, top + 2))
            scene.emit(b"\x1b[3;1HREWRITTEN")  # the last selected row, by output
            await _wait_note(scene, MOVED)
            await _wait_marked(scene, [])

            await scene.pilot.press("v", "down", "down")
            await _wait_marked(scene, _lines(scene, top, top + 2))
            held = screen._selection.rows
            real = backend.row_texts
            # The rows after a trim: the first is the text it was selected with, the last is another row's.
            backend_rows = {top: held[top], top + 2: held[top + 1]}
            monkeypatch.setattr(backend, "row_texts", lambda first, last: [backend_rows.get(first)] if first == last
                                else real(first, last))
            screen._check_selection()
            assert screen._selection is None and MOVED in screen.status.text
            assert await _marked(scene) == []
            assert sent == []

    asyncio.run(_check())


def test_history_pruned_under_the_selection_clears_it(serve, wait_until, screen_text, monkeypatch) -> None:
    """With a small scrollback budget the oldest rows are dropped as lines arrive, which moves every row's number: a
    selection on the oldest rows is cleared instead of sitting on whatever line is now at that number."""

    async def _check() -> None:
        monkeypatch.setenv("TSB_TUI_TERM_SCROLLBACK_BYTES", "65536")
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            # Until the engine holds no more (the budget is full), so every further line drops one at the top.
            number = 0
            held = 0
            while True:
                await _fill(scene, number + 400, {}, first=number)
                number += 400
                total = scene.position().total
                if total <= held:
                    break
                held = total
            await scene.open_view()
            scene.canvas.scroll_to_row(0)
            await wait_until(scene.pilot, lambda: scene.position().offset == 0, "the oldest row on top")
            await scene.pilot.press("v", "down")
            await _wait_marked(scene, _lines(scene, 0, 1))
            await _fill(scene, number + 2000, {}, first=number)
            await _wait_note(scene, MOVED)
            await _wait_marked(scene, [])
            assert sent == []

    asyncio.run(_check())


def test_wrapped_rows_are_joined_when_copy_join_wrapped_and_only_what_the_selection_covers(
        serve, wait_until, screen_text, monkeypatch) -> None:
    """A row the engine marks as soft-wrapped into the next row, when that row is selected too, is joined to it with no
    newline, with the space at the wrap kept (read from the cells, not the trimmed text); everything else is
    separated by a newline. A selection that covers only some of a wrapped line sends only those rows."""

    long = "a" * 79 + " " + "b" * 79 + " " + "tail"

    async def _run(join: bool, picks: list[tuple[int, int, str, int]]) -> None:
        monkeypatch.setenv("TSB_TUI_COPY_JOIN_WRAPPED", "true" if join else "false")
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 100, {})
            scene.emit(long.encode() + b"\r\n")
            await _fill(scene, 200, {}, first=100)
            rows = [_row_of(scene, "a" * 79), _row_of(scene, "b" * 79), _row_of(scene, "tail")]
            assert rows == [rows[0], rows[0] + 1, rows[0] + 2], "the long line is three physical rows"
            for offset, count, expected, lines in picks:
                await scene.open_view()
                await _top_at(scene, rows[0] + offset)
                await scene.pilot.press("v", *(["down"] * (count - 1)))
                await _wait_marked(scene, _lines(scene, rows[0] + offset, rows[0] + offset + count - 1))
                before = len(sent)
                await scene.pilot.press("y")
                await wait_until(scene.pilot, lambda before=before: len(sent) > before, "the copy")
                assert sent[before:] == [_osc52(expected)], (join, offset, count)
                assert _sent(lines) in _toasts(scene)
                await scene.pilot.press("escape", "escape")
                await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "the view to close")

    async def _check() -> None:
        a, b = "a" * 79, "b" * 79
        await _run(True, [(0, 3, f"{a} {b} tail", 1), (0, 2, f"{a} {b}", 1), (1, 2, f"{b} tail", 1),
                          (2, 1, "tail", 1)])
        await _run(False, [(0, 3, f"{a}\n{b}\ntail", 3), (1, 1, b, 1)])

    asyncio.run(_check())


def test_over_copy_max_bytes_nothing_is_sent_and_the_note_says_why(serve, wait_until, screen_text, monkeypatch) -> None:
    """The text over `copy.max_bytes` bytes (of text, before base64) is never sent, and never cut to fit: the note says it
    is over the limit and names the knob (it gives no size, since reading stops once the text is over). A text exactly at
    the limit is sent."""

    async def _check() -> None:
        monkeypatch.setenv("TSB_TUI_COPY_MAX_BYTES", "59")
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            row = _row_of(scene, "L100")
            await _select(scene, row, 5)
            text = "\n".join(_lines(scene, row, row + 4))
            assert len(text.encode()) == 59
            await scene.pilot.press("y")
            await _wait_toast(scene, _sent(5))
            assert sent == [_osc52(text)], "at the limit it is sent"

            await scene.pilot.press("down")
            await _wait_marked(scene, _lines(scene, row, row + 5))
            await scene.pilot.press("y")
            await _wait_note(scene, "too large to copy (over 59 bytes; copy.max_bytes is 59)")
            assert len(sent) == 1, "nothing was sent, and no part of it"
            assert await _marked(scene) != [], "the selection stays, so a shorter one can be made from it"

    asyncio.run(_check())


def test_a_selection_far_over_the_limit_is_refused_without_reading_every_row(serve, wait_until, screen_text,
                                                                             monkeypatch) -> None:
    """A copy reads the selected rows only until the text so far is over `copy.max_bytes`, then stops and sends nothing:
    it never reads more than the limit's worth of rows. A row past the one it stopped at is not read, so a change there
    is not what the note says (the text was over the limit either way)."""

    async def _check() -> None:
        monkeypatch.setenv("TSB_TUI_COPY_MAX_BYTES", "100")
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            await scene.open_view()
            top = scene.position().offset  # the live screen's top row
            await scene.pilot.press("v", *(["down"] * 20))
            await _wait_marked(scene, _lines(scene, top, top + 20))
            assert len("\n".join(_lines(scene, top, top + 20)).encode()) > 200, "far over the limit"
            scene.emit(b"\x1b[16;1HREWRITTEN")  # a row past where the copy stops, and neither end of the selection
            await wait_until(scene.pilot, lambda: scene.canvas.backend.grid_lines()[top + 15].startswith("REWRITTEN"),
                             "the rewrite on the grid")

            backend = scene.canvas.backend
            reads: list[int] = []
            real = backend.row_texts

            def counted(first: int, last: int):
                reads.append(last - first + 1)
                return real(first, last)

            monkeypatch.setattr(backend, "row_texts", counted)
            await scene.pilot.press("y")
            await _wait_note(scene, "too large to copy (over 100 bytes; copy.max_bytes is 100)")
            assert sent == []
            assert 0 < sum(reads) <= 10, f"{sum(reads)} of 21 rows were read: only as many as the limit holds"
            assert await _marked(scene) != [], "the selection stays"

    asyncio.run(_check())


def test_copy_off_sends_nothing_and_names_the_terminals_own_selection(serve, wait_until, screen_text,
                                                                     monkeypatch) -> None:
    """With `copy.osc52 = false` selecting still works, and `y` sends nothing: it names the outer terminal's own
    selection (the mouse off, or Shift held)."""

    async def _check() -> None:
        monkeypatch.setenv("TSB_TUI_COPY_OSC52", "false")
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            row = _row_of(scene, "L100")
            await _select(scene, row, 2)
            await scene.pilot.press("y")
            await _wait_toast(scene, OFF)  # whole, whatever the width: the status row clips a note this long
            assert OFF[:60] in scene.screen.status.text, "and the note is in the status row too"
            assert sent == [] and await _marked(scene) == _lines(scene, row, row + 1)
            assert not any("Sent" in t for t in _toasts(scene))

    asyncio.run(_check())


def test_a_selection_of_blank_rows_sends_nothing_and_says_it_is_blank(serve, wait_until, screen_text,
                                                                     monkeypatch) -> None:
    """Only blank rows make a text with nothing in it: nothing is sent (an empty OSC 52 write can clear the clipboard),
    the note says the selection is blank, and the selection stays. A selection that includes a line of text is sent."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 100, {})
            scene.emit(b"BEFORE\r\n" + b"\r\n" * 5 + b"AFTER\r\n")
            await _fill(scene, 200, {}, first=100)
            row = _row_of(scene, "BEFORE")
            await _select(scene, row + 1, 3)
            await scene.pilot.press("y")
            await _wait_note(scene, BLANK)
            assert sent == [] and not any("Sent" in t for t in _toasts(scene))
            assert len(await _marked(scene)) == 3, "the selection stays"

            await scene.pilot.press("escape")
            await _wait_marked(scene, [])
            await _select(scene, row, 2)  # BEFORE and a blank row after it
            await scene.pilot.press("y")
            await wait_until(scene.pilot, lambda: len(sent) == 1, "the copy")
            assert sent == [_osc52("BEFORE\n")]
            assert _sent(2) in _toasts(scene)

    asyncio.run(_check())


def test_mouse_drag_in_the_view_selects_lines_and_release_copies(serve, wait_until, screen_text, monkeypatch) -> None:
    """With the mouse on, a drag over the canvas selects the lines it covers, whole, and releasing copies them. A press
    and release on one row with no movement does nothing; a drag replaces a keyboard selection; a drag with
    `copy.osc52 = false` selects and answers as `y` does. The pointer's events reach no session."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            row = _row_of(scene, "L100")
            await scene.open_view()
            await _top_at(scene, row)

            await _drag(scene, 4, 4)
            assert await _marked(scene) == [] and sent == [], "a press and release on one row does nothing"

            await _drag(scene, 3, 6)
            await _wait_toast(scene, _sent(4))
            expected = _lines(scene, row + 3, row + 6)
            assert sent == [_osc52("\n".join(expected))]
            await _wait_marked(scene, expected)

            await scene.pilot.press("escape")
            await _wait_marked(scene, [])
            await _top_at(scene, row)
            await scene.pilot.press("v", "down", "down")
            await _wait_marked(scene, _lines(scene, row, row + 2))
            await _drag(scene, 8, 9)
            await _wait_marked(scene, _lines(scene, row + 8, row + 9))
            assert sent[-1] == _osc52("\n".join(_lines(scene, row + 8, row + 9))), "the drag replaced the keys' selection"
            await scene.settled()
            assert scene.serve_app.received(scene.client_id) == [], "no pointer event reached the session"

    asyncio.run(_check())


def test_a_shifted_release_ends_the_drag_and_copies_nothing_and_later_motion_selects_nothing(
        serve, wait_until, screen_text, monkeypatch) -> None:
    """Every left-button release ends the drag, shifted or not, and only an unshifted release copies. So a press, then a
    Shift-held release (the operator's own terminal's selection), then the pointer moving with no button down, selects
    nothing and sends nothing: motion extends a drag only while the left button is held. A drag ended by a shifted
    release leaves its selection, copies nothing, and later motion does not extend it."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            row = _row_of(scene, "L100")
            await scene.open_view()
            await _top_at(scene, row)
            canvas, pilot, screen = scene.canvas, scene.pilot, scene.screen

            await pilot.mouse_down(canvas, offset=(5, 3))
            await pilot.mouse_up(canvas, offset=(5, 3), shift=True)
            assert screen._drag_from is None and not screen._dragged, "the shifted release ended the drag"
            await pilot.hover(canvas, offset=(5, 6))  # no button down
            await pilot.pause()
            assert await _marked(scene) == [] and sent == [], "button-free motion selects nothing"

            await pilot.mouse_down(canvas, offset=(5, 3))
            await _move_held(scene, 5)
            await _wait_marked(scene, _lines(scene, row + 3, row + 5))
            await pilot.mouse_up(canvas, offset=(5, 5), shift=True)
            await pilot.pause()
            assert sent == [], "only an unshifted release copies"
            assert screen._drag_from is None and not screen._dragged
            await pilot.hover(canvas, offset=(5, 8))
            await pilot.pause()
            assert await _marked(scene) == _lines(scene, row + 3, row + 5), "button-free motion does not extend it"
            assert sent == []

    asyncio.run(_check())


def test_a_drag_reads_only_the_rows_each_motion_newly_covers(serve, wait_until, screen_text, monkeypatch) -> None:
    """The first motion of a drag starts the selection; each later motion moves its loose end and reads only the rows now
    covered that were not before, so a drag over n rows reads n rows and not their running sum."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            row = _row_of(scene, "L100")
            await scene.open_view()
            await _top_at(scene, row)
            backend = scene.canvas.backend
            read: list[int] = []
            real = backend.row_texts
            monkeypatch.setattr(backend, "row_texts",
                                lambda first, last: read.extend(range(first, last + 1)) or real(first, last))

            await scene.pilot.mouse_down(scene.canvas, offset=(5, 2))
            for y in range(3, 9):
                await _move_held(scene, y)
            await _wait_marked(scene, _lines(scene, row + 2, row + 8))
            assert sorted(read) == list(range(row + 2, row + 9)), f"rows read: {read}"
            await _move_held(scene, 6)  # back over rows already held: nothing to read, the mark shrinks
            await _wait_marked(scene, _lines(scene, row + 2, row + 6))
            assert sorted(read) == list(range(row + 2, row + 9))
            read.clear()
            await scene.pilot.mouse_up(scene.canvas, offset=(5, 6))
            await _wait_toast(scene, _sent(5))
            assert sent == [_osc52("\n".join(_lines(scene, row + 2, row + 6)))]

    asyncio.run(_check())


def test_a_mouse_drag_with_copy_off_selects_and_names_the_terminals_own_selection(serve, wait_until, screen_text,
                                                                                 monkeypatch) -> None:
    async def _check() -> None:
        monkeypatch.setenv("TSB_TUI_COPY_OSC52", "false")
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 300, {})
            row = _row_of(scene, "L100")
            await scene.open_view()
            await _top_at(scene, row)
            await _drag(scene, 2, 3)
            await _wait_toast(scene, OFF)
            assert OFF[:60] in scene.screen.status.text
            await _wait_marked(scene, _lines(scene, row + 2, row + 3))
            assert sent == []

    asyncio.run(_check())


def test_a_sessions_own_osc52_write_never_reaches_the_clipboard(serve, wait_until, screen_text, monkeypatch) -> None:
    """Guard: a session that sends OSC 52 (a write of text, and a read) causes no write to the outer terminal, with the
    view closed and with it open and a selection made. Only the operator's `y` or drag writes there."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            sent = _watch(scene, monkeypatch)
            await _fill(scene, 100, {})
            scene.emit(b"\x1b]52;c;" + base64.b64encode(b"stolen") + b"\x07\x1b]52;c;?\x07")
            await _fill(scene, 140, {}, first=100)
            await scene.open_view()
            await scene.pilot.press("v")
            scene.emit(b"\x1b]52;c;" + base64.b64encode(b"again") + b"\x07")
            await _fill(scene, 180, {}, first=140)
            await scene.settled()
            assert sent == [], "nothing the session sent reached the operator's clipboard"
            assert scene.serve_app.received(scene.client_id) == [], "and the read was not answered"

    asyncio.run(_check())


def test_a_long_wrapped_run_stops_at_the_limit_with_each_row_encoded_once(lib, monkeypatch) -> None:
    """A wrapped line is gathered as fragments with a running byte count: the text is never rebuilt and re-encoded per
    row, so a run that is far over `copy.max_bytes` stops with work bounded by the rows it read. The seam is the one
    function that sizes text in bytes: the characters it is given across the whole copy are those of the rows read, once
    each (re-encoding the line so far at every row would give their square)."""
    from tsb_tui.term import selection as selection_module
    from tsb_tui.term.selection import Selection

    cols, rows_in_line, limit = 80, 400, 4000
    backend = VTBackend(lib, cols, 5, scrollback_bytes=SCROLLBACK)
    try:
        backend.feed(b"x" * (cols * rows_in_line) + b"\r\n")
        total = backend.scrollback_position().total
        selection = Selection(0, rows_in_line - 1)
        assert total > rows_in_line and selection.fit(backend)
        assert all(row.wrapped for row in selection.held()[:-1]), "one line over four hundred rows"

        sized: list[int] = []
        real = selection_module._utf8_size

        def counted(text: str) -> int:
            sized.append(len(text))
            return real(text)

        monkeypatch.setattr(selection_module, "_utf8_size", counted)
        reads: list[int] = []
        real_rows = backend.row_texts
        monkeypatch.setattr(backend, "row_texts", lambda first, last: reads.append(last - first + 1) or real_rows(first, last))
        assert selection.read_for_copy(backend, True, limit) == ("over", "")
        assert 0 < sum(reads) <= limit // cols + 2, "it stopped as soon as the limit was passed"
        assert sum(sized) <= cols * sum(reads), f"{sum(sized)} characters sized for {sum(reads)} rows of {cols}"
        assert len(sized) <= sum(reads), "one sizing per row at most"

        sized.clear()
        short = Selection(0, 9)
        assert short.fit(backend)
        assert short.read_for_copy(backend, True, 1 << 20) == ("ok", "x" * (cols * 10))
        assert sum(sized) == cols * 10, "a copy that fits sizes each row once too"
    finally:
        backend.close()


def test_row_texts_reads_a_scrolled_off_rows_cells_and_its_soft_wrap_fact(lib) -> None:
    """The backend reads a row by its place in the engine's row space, however far it has scrolled off: its text from
    the cells (the space at the wrap kept, blank cells as spaces, the full width), and whether the engine marked it
    soft-wrapped into the next row. Rows past what is held are None."""
    from tsb_tui.term.backend import RowText

    backend = VTBackend(lib, 20, 5, scrollback_bytes=SCROLLBACK)
    try:
        backend.feed(b"A" * 19 + b" " + b"B" * 19 + b" tail\r\n")
        for i in range(30):
            backend.feed(f"line {i}\r\n".encode())
        assert backend.scrollback_position().total > 30
        rows = backend.row_texts(0, 3)
        assert rows == [RowText("A" * 19 + " ", True), RowText("B" * 19 + " ", True),
                        RowText("tail" + " " * 16, False), RowText("line 0" + " " * 14, False)]
        assert backend.row_texts(5, 4) == []
        total = backend.scrollback_position().total
        assert backend.row_texts(total, total) == [None], "no such row"
        assert len(backend.row_texts(0, total - 1)) == total
    finally:
        backend.close()


def test_row_texts_gives_a_wide_character_once_and_not_its_spacer(lib) -> None:
    """A wide character is one character of the text although it fills two cells, and a wide character that did not fit
    at the end of a row leaves the row's last cell as a blank, not as text."""
    from tsb_tui.term.backend import RowText

    backend = VTBackend(lib, 10, 4, scrollback_bytes=SCROLLBACK)
    try:
        backend.feed("日本語 ok\r\n".encode())
        backend.feed(("x" * 9 + "日\r\n").encode())
        rows = backend.row_texts(0, 2)
        assert rows[0] == RowText("日本語 ok" + " ", False), "ten cells: three wide characters (six), a blank, 'ok', a blank"
        first_wrapped = rows[1]
        assert first_wrapped.wrapped and first_wrapped.text.strip() == "x" * 9
        assert "日" not in first_wrapped.text and rows[2].text.startswith("日")
    finally:
        backend.close()


def test_row_texts_keeps_a_combining_mark_with_its_base(lib) -> None:
    """A cell that holds a grapheme cluster gives the whole cluster, as the formatter prints it, not its base alone."""
    backend = VTBackend(lib, 10, 3, scrollback_bytes=SCROLLBACK)
    try:
        backend.feed("e\u0301x\r\n".encode())
        (row,) = backend.row_texts(0, 0)
        assert row is not None and row.text.rstrip() == "e\u0301x" == backend.grid_lines()[0]
    finally:
        backend.close()
