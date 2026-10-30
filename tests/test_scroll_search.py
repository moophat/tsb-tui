"""Search in the scroll view: `/` asks for text, Enter finds it in the output this client holds, `n` and `N` step to the next
older and newer match, and the matches are marked on the canvas.

Why it matters (Rule 9): the view is how an operator finds the line that explains an outage in what scrolled past, so what
the tests pin is what the operator can see and be told: the row the match is on is in the window, the cells marked are
exactly the matched text (also after a wide character), the note says why a step did not move, and the text typed into the
prompt is the prompt's and never the session's. Servers: the contract's in-process fixture server; a test reads the
server's record of what the client sent and the strips the canvas renders, never the client's own say-so.

Semantics (a literal string; smart case; `/` searches from the top row on view toward older output, the top row
included; `n` and `N` step from the current match, older and newer, with no wrap-around; one match is one occurrence)
are the ruled ones.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import pytest
from rich.cells import cell_len
from test_scroll_view import GRID_ROWS, _scene
from tsb_tui.term import VTBackend, load
from tsb_tui.widgets.status_row import StatusRow

NEEDLE = "needle"
FILLER = "filler"
NOT_OLDER = "no older match"
NOT_NEWER = "no newer match"


async def _fill(scene, count: int, tags: dict[int, str], first: int = 0) -> None:
    """The lines on the target, waited for until the last of them is on this client's grid."""
    scene.emit(b"".join(f"L{i:03d} {tags.get(i, FILLER)}\r\n".encode() for i in range(first, count)))
    backend = scene.canvas.backend
    await scene.wait_until(scene.pilot, lambda: any(line.startswith(f"L{count - 1:03d} ")
                                                    for line in backend.grid_lines()), "the lines on the grid")


def _row_of(scene, prefix: str) -> int:
    """The engine row of the line that starts with `prefix`: the row space `/` searches and the viewport moves in."""
    lines = scene.canvas.backend.grid_lines()
    return next(i for i, line in enumerate(lines) if line.startswith(prefix))


async def _search(scene, query: str) -> None:
    """`/`, the query, Enter: the prompt opens over the view and closes on Enter."""
    await scene.pilot.press("/")
    await scene.wait_until(scene.pilot, lambda: bool(scene.screen.dialogs), "the search prompt")
    await scene.pilot.press(*query)
    await scene.pilot.press("enter")
    await scene.wait_until(scene.pilot, lambda: not scene.screen.dialogs, "the prompt to close")


def _runs(scene, current: bool) -> list[tuple[str, str, int]]:
    """What the canvas has marked, line by line: (the line's text, the marked text, the marked text's first column in
    cells). The current match is reverse and bold; any other match is underlined and not reversed."""
    found = []
    canvas = scene.canvas
    for y in range(canvas.size.height):
        strip = canvas.render_line(y)
        column, marked, first = 0, [], 0
        for text, style, _ in strip:
            is_current = bool(style and style.reverse and style.bold)
            is_other = bool(style and style.underline and not style.reverse)
            if (is_current if current else is_other) and text.strip():
                if not marked:
                    first = column
                marked.append(text)
            column += cell_len(text)
        if marked:
            found.append((strip.text.rstrip(), "".join(marked), first))
    return found


def _current(scene) -> list[tuple[str, str, int]]:
    return _runs(scene, True)


async def _wait_current(scene, line_prefix: str) -> None:
    """Until the current match is painted on the line that starts with `line_prefix`."""
    await scene.wait_until(scene.pilot, lambda: any(text.startswith(line_prefix) for text, _, _ in _current(scene)),
                           f"the current match on {line_prefix}")


async def _wait_note(scene, note: str) -> None:
    await scene.wait_until(scene.pilot, lambda: note in scene.screen.status.text, f"the note {note!r}")


async def _idle(scene) -> None:
    """Every key pressed so far has been handled (no delay: it waits for the queue, not for time)."""
    await scene.pilot.pause()


def test_slash_finds_the_nearest_older_match_and_brings_it_into_view(serve, wait_until, screen_text) -> None:
    """`/` searches from the top row on view toward older output: with the window on the live screen it finds the
    nearest match above it, the match's row is in the window, and the cells of the matched text (and only those) carry
    the current-match style, while another match on view carries the other style. The query is typed into the prompt and
    reaches no session; Esc leaves the view and takes the marks with it."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            tags = {50: f"far {NEEDLE}", 262: f"older {NEEDLE}", 270: f"{NEEDLE} nearest", 290: f"on view {NEEDLE}"}
            await _fill(scene, 300, tags)
            await scene.open_view()
            assert _runs(scene, True) == [] and _runs(scene, False) == [], "nothing is marked before a search"

            await scene.pilot.press("/")
            await wait_until(scene.pilot, lambda: bool(scene.screen.dialogs), "the search prompt")
            assert "keys → search" in scene.screen.status.text
            assert scene.screen.scroll_view_open, "the view stays open under the prompt"
            await scene.pilot.press(*NEEDLE)
            await scene.pilot.press("enter")
            await wait_until(scene.pilot, lambda: not scene.screen.dialogs, "the prompt to close")

            await _wait_current(scene, "L270")
            assert scene.screen.scroll_view_open and "keys → scroll" in scene.screen.status.text
            position = scene.position()
            row = _row_of(scene, "L270")
            assert position.offset <= row < position.offset + position.length, "the match's row is in the window"
            shown = await scene.numbers()
            assert 270 in shown and 262 not in shown, "the match is on screen, the older match is not"
            assert _current(scene) == [(f"L270 {NEEDLE} nearest", NEEDLE, len("L270 "))], \
                "exactly the matched text is the current match"
            assert _runs(scene, False) == [(f"L290 on view {NEEDLE}", NEEDLE, len("L290 on view "))], \
                "another match on view has the other style, and the matches off view are not on screen"

            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "Esc to close the view")
            await wait_until(scene.pilot, scene.at_bottom, "the viewport at the bottom")
            await scene.open_view()
            assert _current(scene) == [] and _runs(scene, False) == [], "the marks went with the view"
            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "Esc to close the view")
            await scene.settled()
            assert scene.serve_app.received(scene.client_id) == [], "the query reached no session"

    asyncio.run(_check())


def test_n_and_N_step_older_and_newer_and_say_so_at_each_end(serve, wait_until, screen_text) -> None:
    """`n` goes to the next older match and `N` to the next newer one, from the current match, with no wrap-around: at
    the oldest the note says "no older match" and at the newest "no newer match", and the view stays on the match it
    is on. Two matches on one line are two matches."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            tags = {50: NEEDLE, 120: f"{NEEDLE} and {NEEDLE}", 200: NEEDLE}
            await _fill(scene, 300, tags)
            await scene.open_view()
            await _search(scene, NEEDLE)
            await _wait_current(scene, "L200")

            await scene.pilot.press("n")
            await _wait_current(scene, "L120")
            assert [(marked, column) for _, marked, column in _current(scene)] == [
                (NEEDLE, len("L120 ") + len(NEEDLE) + len(" and "))], "the later of the two on the line comes first"

            await scene.pilot.press("n")
            await wait_until(scene.pilot, lambda: _current(scene) and _current(scene)[0][2] == len("L120 "),
                             "the earlier match on the same line")
            assert _current(scene)[0][0].startswith("L120")

            await scene.pilot.press("n")
            await _wait_current(scene, "L050")
            offset = scene.position().offset
            await scene.pilot.press("n")
            await _wait_note(scene, NOT_OLDER)
            assert scene.position().offset == offset and scene.screen.scroll_view_open, "the view stays"
            assert [text[:4] for text, _, _ in _current(scene)] == ["L050"], "the match it is on stays current"

            for expected in ("L120", "L120", "L200"):
                await scene.pilot.press("N")
                await _wait_current(scene, expected)
            offset = scene.position().offset
            await scene.pilot.press("N")
            await _wait_note(scene, NOT_NEWER)
            assert scene.position().offset == offset and [t[:4] for t, _, _ in _current(scene)] == ["L200"]

            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "Esc to close the view")

    asyncio.run(_check())


def test_case_follows_smart_case(serve, wait_until, screen_text) -> None:
    """A query with no capital letter matches whatever the case; a query with a capital matches that case only."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            tags = {60: "needle", 100: "Needle", 140: "NEEDLE", 180: "needle"}
            await _fill(scene, 300, tags)

            async def lines_found(query: str, expected_first: str) -> list[str]:
                """Open the view, search, and step older until the note says there is no more."""
                await scene.open_view()
                await _search(scene, query)
                await _wait_current(scene, expected_first)
                seen = [_current(scene)[0][0]]
                while True:
                    before = scene.position().offset
                    await scene.pilot.press("n")
                    await wait_until(scene.pilot, lambda: NOT_OLDER in scene.screen.status.text
                                     or _current(scene)[0][0] != seen[-1], "n to move or to say it cannot")
                    if NOT_OLDER in scene.screen.status.text:
                        assert scene.position().offset == before
                        break
                    seen.append(_current(scene)[0][0])
                await scene.pilot.press("escape")
                await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "Esc to close the view")
                return seen

            assert await lines_found("needle", "L180") == ["L180 needle", "L140 NEEDLE", "L100 Needle", "L060 needle"]
            assert await lines_found("Needle", "L100") == ["L100 Needle"]
            assert await lines_found("NEEDLE", "L140") == ["L140 NEEDLE"]

    asyncio.run(_check())


def test_not_found_says_so_and_keeps_the_view(serve, wait_until, screen_text) -> None:
    """A query nothing holds: the note says "not found: <query>", the view stays open on the rows it showed, and nothing
    is marked, including what an earlier query had marked. The query is a literal string, not a pattern."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await _fill(scene, 300, {100: f"{NEEDLE} a.c", 150: f"{NEEDLE} abc"})
            await scene.open_view()
            await scene.pilot.press("pageup")
            await wait_until(scene.pilot, lambda: not scene.at_bottom(), "the viewport to move up")
            offset = scene.position().offset
            shown = await scene.numbers()

            await _search(scene, "zzz")
            await _wait_note(scene, "not found: zzz")
            assert scene.screen.scroll_view_open and "keys → scroll" in scene.screen.status.text
            assert scene.position().offset == offset and await scene.numbers() == shown
            assert _current(scene) == [] and _runs(scene, False) == []

            await scene.pilot.press("n")
            await _idle(scene)
            assert scene.position().offset == offset, "n has nothing to step to after a query nothing holds"

            scene.canvas.scroll_to_bottom()
            await wait_until(scene.pilot, scene.at_bottom, "the bottom")
            await _search(scene, "a.c")
            await _wait_current(scene, "L100")
            assert _current(scene)[0][1] == "a.c", "the dot is a dot, not any character"

    asyncio.run(_check())


def test_esc_in_the_prompt_returns_to_the_view_unmoved(serve, wait_until, screen_text) -> None:
    """Esc in the prompt cancels it: the view is open where it was, nothing was searched or marked, and the first Esc
    did not close the view (the second does). What was typed in the prompt reached no session."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await _fill(scene, 300, {100: NEEDLE})
            await scene.open_view()
            await scene.pilot.press("pageup")
            await wait_until(scene.pilot, lambda: not scene.at_bottom(), "the viewport to move up")
            offset = scene.position().offset
            shown = await scene.numbers()

            await scene.pilot.press("/")
            await wait_until(scene.pilot, lambda: bool(scene.screen.dialogs), "the search prompt")
            await scene.pilot.press(*"nee")
            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: not scene.screen.dialogs, "the prompt to close")
            await _idle(scene)
            assert scene.screen.scroll_view_open and "keys → scroll" in scene.screen.status.text
            assert scene.position().offset == offset and await scene.numbers() == shown
            assert _current(scene) == [] and NOT_OLDER not in scene.screen.status.text

            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "the second Esc to close the view")
            await scene.settled()
            assert scene.serve_app.received(scene.client_id) == [], "the prompt's keys reached no session"

    asyncio.run(_check())


def test_an_empty_query_is_a_cancel(serve, wait_until, screen_text) -> None:
    """Enter on an empty prompt searches for nothing: the view is as it was and says nothing about a search."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await _fill(scene, 300, {100: NEEDLE})
            await scene.open_view()
            offset = scene.position().offset
            await scene.pilot.press("/")
            await wait_until(scene.pilot, lambda: bool(scene.screen.dialogs), "the search prompt")
            await scene.pilot.press("enter")
            await wait_until(scene.pilot, lambda: not scene.screen.dialogs, "the prompt to close")
            await _idle(scene)
            assert scene.position().offset == offset and scene.screen.scroll_view_open
            assert "not found" not in scene.screen.status.text and _current(scene) == []

    asyncio.run(_check())


def test_n_before_any_search_says_there_is_none(serve, wait_until, screen_text) -> None:
    """`n` and `N` with no query held are not a dropped key: the note says there is nothing to repeat."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await _fill(scene, 300, {100: NEEDLE})
            await scene.open_view()
            await scene.pilot.press("n")
            await _wait_note(scene, "no search yet")
            await scene.pilot.press("N")
            await _wait_note(scene, "no search yet")
            assert "Esc to return to live, then type" not in scene.screen.status.text
            assert scene.screen.scroll_view_open

    asyncio.run(_check())


def test_the_marks_follow_wide_characters(serve, wait_until, screen_text) -> None:
    """The match is found in the row's text and marked on the row's cells: after two wide characters (two cells each) the
    marked cells are the matched text's, not two columns early."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await _fill(scene, 300, {100: f"日本 {NEEDLE} 語"})
            await scene.open_view()
            await _search(scene, NEEDLE)
            await _wait_current(scene, "L100")
            ((text, marked, column),) = _current(scene)
            assert text == f"L100 日本 {NEEDLE} 語"
            assert marked == NEEDLE
            assert column == cell_len("L100 日本 "), "counted in cells: each wide character is two"

    asyncio.run(_check())


def test_n_after_new_output_searches_what_the_engine_holds_now(serve, wait_until, screen_text) -> None:
    """The rows a search read are not kept past new output: a match that arrived after the query was run is found by `N`
    (a stale list would say "no newer match"), and the marks the old rows made are not left on the screen."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await _fill(scene, 300, {50: NEEDLE, 200: NEEDLE})
            await scene.open_view()
            await _search(scene, NEEDLE)
            await _wait_current(scene, "L200")

            await _fill(scene, 340, {320: f"late {NEEDLE}"}, first=300)
            await scene.pilot.press("N")
            await _wait_current(scene, "L320")
            assert _current(scene)[0][1] == NEEDLE
            assert NOT_NEWER not in scene.screen.status.text

    asyncio.run(_check())


def test_a_window_shorter_than_the_grid_pans_to_show_the_match(serve, wait_until, screen_text) -> None:
    """The viewport puts the match on its top row, and a window shorter than the grid, panned down to follow the live
    cursor, would not show that row: the window moves to it, so the match is on screen and not only in the engine."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text, size=(100, 14)) as scene:
            await _fill(scene, 300, {262: NEEDLE, 100: NEEDLE})
            await wait_until(scene.pilot, lambda: scene.canvas.painted, "the canvas to paint")
            assert scene.canvas.size.height < GRID_ROWS and scene.canvas.view_y > 0, "the window is below the grid's top"
            await scene.open_view()
            await _search(scene, NEEDLE)
            await _wait_current(scene, "L262")
            await scene.pilot.press("n")
            await _wait_current(scene, "L100")

    asyncio.run(_check())


def test_the_window_stays_on_the_match_while_output_arrives(serve, wait_until, screen_text) -> None:
    """With the view open the window follows the view and never the live cursor: a window shorter than the grid, put on a
    match among the live screen's rows, stays there when output arrives (the next paint used to pan it back to the
    cursor, hiding the match). Closing the view gives the cursor its window back."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text, size=(100, 14)) as scene:
            await _fill(scene, 300, {280: NEEDLE})
            await wait_until(scene.pilot, lambda: scene.canvas.painted, "the canvas to paint")
            assert scene.canvas.size.height < GRID_ROWS and scene.canvas.view_y > 0
            await scene.open_view()
            await _search(scene, NEEDLE)
            await _wait_note(scene, NOT_OLDER)  # the match is on the live screen, below the top row on view
            await scene.pilot.press("N")
            await _wait_current(scene, "L280")
            top = scene.canvas.view_y

            await _fill(scene, 310, {}, first=300)
            await wait_until(scene.pilot, lambda: scene.canvas.painted, "the canvas to paint")
            await scene.pilot.pause()
            assert scene.canvas.view_y == top, "the window did not move to the live cursor"

            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: not scene.screen.scroll_view_open, "Esc to close the view")
            await wait_until(scene.pilot, lambda: scene.canvas.painted, "the canvas to paint")
            cursor = scene.canvas.backend.live_cursor()
            await wait_until(scene.pilot, lambda: scene.canvas.view_y <= cursor.y < scene.canvas.view_y
                             + scene.canvas.size.height, "the window to follow the cursor again")

    asyncio.run(_check())


HISTORY_MOVED = "the history moved; searching from the view"
TRIM_BYTES = "262144"  # a scrollback budget small enough to fill, and trim, in a few hundred lines


def _held_from(scene) -> int:
    """The number of the oldest numbered line the engine still holds (0 before any)."""
    first = next((line for line in scene.canvas.backend.grid_lines() if re.match(r"L\d{5} ", line)), "L00000")
    return int(first[1:6])


async def _stream(scene, first: int, last: int) -> None:
    """Lines `first`..`last` (every one a match), waited for on this client's grid."""
    scene.emit(b"".join(f"L{i:05d} {NEEDLE}\r\n".encode() for i in range(first, last + 1)))
    await scene.wait_until(scene.pilot, lambda: f"L{last:05d} {NEEDLE}" in scene.canvas.backend.grid_lines(),
                           f"L{last:05d} on the grid")


async def _fill_to_the_budget(scene) -> int:
    """Stream lines until the engine has dropped its oldest rows; returns the last line sent."""
    last = -1
    while _held_from(scene) == 0:
        assert last < 4000, "the scrollback never reached its byte budget"
        await _stream(scene, last + 1, last + 100)
        last += 100
    return last


async def _trim_more(scene, last: int) -> tuple[int, int]:
    """Stream until the oldest held line moves on; returns the last line sent and how far the oldest line moved."""
    before = _held_from(scene)
    while _held_from(scene) == before:
        assert last < 8000, "the engine trimmed nothing more"
        await _stream(scene, last + 1, last + 20)
        last += 20
    return last, _held_from(scene) - before


@pytest.mark.parametrize(("key", "step"), [("n", -1), ("N", 1)])
def test_n_and_N_after_the_history_is_trimmed_neither_revisit_nor_skip(serve, wait_until, screen_text, monkeypatch,
                                                                       key, step) -> None:
    """The engine drops its oldest rows once the byte budget is full, so a row's index moves down while its text stays:
    after that the current match is found again by its row text (at or below its old index) and `n` and `N` step from
    it, to the match just older and just newer. Every line here is a match with its own number, so a step that
    revisited the current match or skipped one shows in the number."""

    async def _check() -> None:
        monkeypatch.setenv("TSB_TUI_TERM_SCROLLBACK_BYTES", TRIM_BYTES)
        async with _scene(serve, wait_until, screen_text) as scene:
            last = await _fill_to_the_budget(scene)
            await scene.open_view()
            await _search(scene, NEEDLE)
            await wait_until(scene.pilot, lambda: bool(_current(scene)), "the current match")
            current = int(_current(scene)[0][0][1:6])

            await _trim_more(scene, last)
            assert _held_from(scene) < current - 3, "the history moved, and the current match is still held"
            await scene.pilot.press(key)
            await _wait_current(scene, f"L{current + step:05d}")
            assert HISTORY_MOVED not in scene.screen.status.text, "the current match was found again"
            await scene.pilot.press(key)
            await _wait_current(scene, f"L{current + 2 * step:05d}")

    asyncio.run(_check())


def test_n_after_the_current_match_was_trimmed_away_steps_from_the_view_and_says_so(serve, wait_until,
                                                                                   screen_text, monkeypatch) -> None:
    """When the current match's row is no longer held, its old index means nothing: the step starts from the top row on
    view, and the note says the history moved."""

    async def _check() -> None:
        monkeypatch.setenv("TSB_TUI_TERM_SCROLLBACK_BYTES", TRIM_BYTES)
        async with _scene(serve, wait_until, screen_text) as scene:
            last = await _fill_to_the_budget(scene)
            await scene.open_view()
            scene.canvas.scroll_by(-10 ** 6)  # the top of what is held
            await wait_until(scene.pilot, lambda: scene.position().offset == 0, "the top of the history")
            await _search(scene, NEEDLE)
            oldest = _held_from(scene)
            await _wait_current(scene, f"L{oldest:05d}")

            await _trim_more(scene, last)
            assert _held_from(scene) > oldest, "the current match's row is gone"
            await scene.pilot.press("n")
            await _wait_note(scene, HISTORY_MOVED)
            assert scene.position().offset == 0
            await _wait_current(scene, f"L{_held_from(scene):05d}")

    asyncio.run(_check())


def test_the_status_row_offers_search_and_a_click_opens_the_prompt(serve, wait_until, screen_text) -> None:
    """The view's hints include Search, and clicking it does what `/` does."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await _fill(scene, 100, {})
            await scene.open_view()
            row = scene.screen.status
            assert "scroll:search" in row.actions(), row.text
            await scene.pilot.click(StatusRow, offset=(row.hit_x("scroll:search") + 1, 0))
            await wait_until(scene.pilot, lambda: bool(scene.screen.dialogs), "the click to open the prompt")
            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: not scene.screen.dialogs, "the prompt to close")

    asyncio.run(_check())


def test_the_prompt_is_a_registered_dialog(serve, wait_until, screen_text) -> None:
    """The prompt is an ordinary dialog: it registers in `dialogs` while open and leaves when closed, so the models'
    redraws and the status row reach it as any other."""

    async def _check() -> None:
        async with _scene(serve, wait_until, screen_text) as scene:
            await _fill(scene, 100, {})
            await scene.open_view()
            assert scene.screen.dialogs == []
            await scene.pilot.press("/")
            await wait_until(scene.pilot, lambda: len(scene.screen.dialogs) == 1, "one dialog")
            await scene.pilot.press("escape")
            await wait_until(scene.pilot, lambda: scene.screen.dialogs == [], "no dialog")

    asyncio.run(_check())


@pytest.fixture(scope="module")
def lib(term_so: Path):
    return load(term_so)


def test_one_search_over_a_full_scrollback_is_timed_and_finds_every_match(lib, capsys) -> None:
    """The cost of one search with the engine at its byte budget: formatting every row it holds and finding the
    query in them. The figure is printed (the report keeps it); the matches found are every one the held text has."""
    from tsb_tui.term.search import search

    budget = 4 * 1024 * 1024  # `term.scrollback_bytes`' default
    backend = VTBackend(lib, 80, GRID_ROWS, scrollback_bytes=budget)
    try:
        held, line = 0, 0
        for _ in range(400):
            chunk = []
            for _ in range(1000):
                tag = f" {NEEDLE}" if line % 23 == 0 else ""
                chunk.append(f"{line:07d} " + "x" * 60 + tag)
                line += 1
            backend.feed(("\r\n".join(chunk) + "\r\n").encode())
            now = backend.scrollback_rows()
            if now <= held:
                break  # the budget dropped rows: the engine is full
            held = now
        else:
            raise AssertionError("the scrollback never reached its byte budget")

        rows = backend.grid_lines()
        expected = sum(row.lower().count(NEEDLE) for row in rows)
        assert expected > 100, "the fill holds enough matches to mean something"

        started = time.perf_counter()
        result = search(backend, NEEDLE)
        took_ms = (time.perf_counter() - started) * 1000.0
        with capsys.disabled():
            print(f"\nSEARCH over {len(rows)} rows ({budget} byte budget): {took_ms:.1f} ms, "
                  f"{len(result.matches)} matches; one frame at 60 fps is 16.7 ms")
        assert len(result.matches) == expected
        assert all(re.fullmatch(NEEDLE, rows[m.row][m.start:m.end]) for m in result.matches)
    finally:
        backend.close()
