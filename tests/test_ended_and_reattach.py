"""How a session ends for this client, and the reattach that follows a lost connection (design F10, F12, D26, D36;
conventions V3; session-detach rules 10 and 12; session-attach CR2; client-remove rule 8; DT13's close table).

Why these matter (Rule 9): every close code means something different, and getting one wrong costs the
operator a session or leaves them looking at a frozen screen. 4010, 4012 and 4014 are final and must never
reattach (4014: "MUST NOT re-attach by itself", else a removed client would be put straight back); 4011 and a
dropped socket are the transport's, so the session lives on and the client tries again with a new ticket each
time; an ended session keeps its last screen, dimmed, so the operator can still read what it said. Servers: the
TUI's own stub (DT7), which can close with any code, drop the socket, and read the record as an end would
leave it.
"""

from __future__ import annotations

import asyncio

import pytest
from prompt_support import click_laid_out, open_stub_terminal
from stub_server import SlowChunkedApp, StubServer

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.screens.prompts import EndedScreen
from tsb_tui.screens.whos_here import WhosHereScreen

KILLED_TEXT = ("Ended by TSB or through it: one of its timers, its owner or an admin.\n"
               "Which one is not sent to clients yet (idle, unattended, orphan, owner, admin).")
TERMINATED_TEXT = "Ended from outside TSB: by the target, the network or its worker."


def _closing(code: int, **kwargs) -> SlowChunkedApp:
    return SlowChunkedApp(delay_s=0.02, stream_close=(code, 3), **kwargs)


async def _ended(pilot, app, wait_until):
    screen = await open_stub_terminal(app, pilot, wait_until)
    await wait_until(pilot, lambda: isinstance(app.screen, EndedScreen) and screen.dialogs
                     and app.screen.query("#ended-title"), "the end dialog")
    return screen, app.screen


@pytest.mark.parametrize(("code", "title", "body"), [
    (4010, "Session ended · killed (close 4010)", KILLED_TEXT),
    (4012, "Session ended · terminated (close 4012)", TERMINATED_TEXT),
    (4999, "Session ended · killed (close 4999)", KILLED_TEXT),  # an unknown 4xxx reads as 4010 (V3)
])
def test_an_ended_session_keeps_its_last_screen_dimmed_with_its_dialog_and_never_reattaches(
        code, title, body, wait_until) -> None:
    """D36, F10: the last screen stays (dimmed), the dialog says which end it was, the row says the recording
    is kept, and nothing is retried: a killed or terminated session is not coming back."""

    async def _check() -> None:
        stub = _closing(code)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen, dialog = await _ended(pilot, app, wait_until)
                assert str(dialog.query_one("#ended-title").render()) == title
                assert str(dialog.query_one("#ended-body").render()) == body
                assert screen.canvas.dimmed
                assert "✕ ended · stub-target · " in screen.status.text
                assert "● REC (the recording is kept)" in screen.status.text
                await pilot.pause(0.5)
                assert stub.attaches == 1, "an ended session must never reattach"

    asyncio.run(_check())


def test_4014_says_removed_and_never_reattaches(wait_until) -> None:
    """client-remove rule 8: a client the owner removed MUST NOT re-attach by itself."""

    async def _check() -> None:
        stub = _closing(4014)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen, dialog = await _ended(pilot, app, wait_until)
                assert str(dialog.query_one("#ended-title").render()) == "Removed by the owner"
                assert not screen.canvas.dimmed, "the session itself lives on"
                assert "✕ stub-target · removed by the owner" in screen.status.text
                await pilot.pause(0.5)
                assert stub.attaches == 1

    asyncio.run(_check())


def test_4003_says_you_may_no_longer_join(wait_until) -> None:
    async def _check() -> None:
        stub = _closing(4003)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                _, dialog = await _ended(pilot, app, wait_until)
                assert str(dialog.query_one("#ended-title").render()) == "You may no longer join this session"
                await pilot.pause(0.3)
                assert stub.attaches == 1

    asyncio.run(_check())


def test_4004_reads_the_record_for_the_state_the_session_is_in(wait_until) -> None:
    """The close alone does not say how a session ended; the record does (session model §12)."""

    async def _check() -> None:
        stub = _closing(4004, record_state_on_close="terminated")
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen, dialog = await _ended(pilot, app, wait_until)
                assert str(dialog.query_one("#ended-title").render()) == "Session ended · terminated"
                assert "✕ ended · stub-target · terminated" in screen.status.text

    asyncio.run(_check())


def test_a_dropped_socket_whose_record_says_the_session_ended_shows_that_end(wait_until) -> None:
    """A WebSocket dying ends nothing (session model §13): the record decides. Here it says killed."""

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.02, drop_after_chunks=3, record_state_on_close="killed")
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                _, dialog = await _ended(pilot, app, wait_until)
                assert str(dialog.query_one("#ended-title").render()) == "Session ended · killed"

    asyncio.run(_check())


def test_esc_closes_the_dialog_and_the_row_keeps_the_end_with_c_and_targets(wait_until) -> None:
    """G8: after Esc the row still says what happened, and `c` / `^\\ f` still lead somewhere."""

    async def _check() -> None:
        with StubServer(_closing(4010)) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen, _ = await _ended(pilot, app, wait_until)
                assert "Esc close" in screen.status.text
                await pilot.press("escape")
                await wait_until(pilot, lambda: app.screen is screen, "the dialog to close")
                row = screen.status.text
                assert "✕ ended · stub-target · killed" in row
                assert "c New session here" in row and "Ctrl+\\ f Targets" in row and "Esc close" not in row
                await pilot.press("c")
                await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "New session")
                assert app.screen.query_one("#target-id").value == "stub-target"

    asyncio.run(_check())


def test_the_dialog_buttons_lead_to_new_session_and_the_fleet(wait_until) -> None:
    async def _check(button: str, arrived) -> None:
        with StubServer(_closing(4010)) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen, _ = await _ended(pilot, app, wait_until)
                await click_laid_out(pilot, wait_until, button)
                await wait_until(pilot, lambda: arrived(app, screen), button)

    asyncio.run(_check("#ended-new", lambda app, screen: isinstance(app.screen, NewSessionScreen)))
    # The Fleet button opens the sidebar, beside the ended session's dimmed screen: it does not go anywhere.
    asyncio.run(_check("#ended-fleet", lambda app, screen: app.screen is screen and screen.sidebar_open))


def test_4011_and_a_dropped_socket_show_the_reattaching_row_and_esc_stops_it(
        wait_until, monkeypatch) -> None:
    """D26, F12: both are the transport's, so the session lives on and the row says so, with the attempt and
    the wait; Esc stops it and the row says nothing more is tried, offering the Fleet only."""
    monkeypatch.setenv("TSB_TUI_REATTACH_BACKOFF_MS", "60000,60000")

    async def _check(stub: SlowChunkedApp, why: str) -> None:
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(160, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: "◐ reattaching" in screen.status.text, "the reattach row")
                row = screen.status.text
                assert f"◐ reattaching · {why} · try 1 in 60 s" in row
                assert "Esc stops" in row and "F1 Menu" not in row and "? keys" not in row
                await pilot.press("escape")
                await wait_until(pilot, lambda: "not reattaching" in screen.status.text, "the stop")
                assert "◌ not reattaching · the session lives on" in screen.status.text
                assert "▦ Targets" in screen.status.text and "F1 Menu" not in screen.status.text
                await pilot.pause(0.3)
                assert stub.attaches == 1, "Esc must stop further attempts"

    asyncio.run(_check(SlowChunkedApp(delay_s=0.02, stream_close=(4011, 3)),
                       "the broker closed this view; the session lives on"))
    asyncio.run(_check(SlowChunkedApp(delay_s=0.02, drop_after_chunks=3),
                       "the connection to the broker was lost; the session lives on"))


def test_a_reattach_mints_a_new_ticket_and_the_grid_resets_at_the_next_prefix(wait_until) -> None:
    """session-attach CR2 and session-detach rule 12: a ticket is one-time, so every attach mints its own."""

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.02, drop_after_chunks=2)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: stub.attaches >= 3, "two reattaches")
                assert stub.ticket_mints >= stub.attaches - 1 and stub.ticket_mints >= 3

    asyncio.run(_check())


def test_with_reattach_auto_off_a_lost_connection_is_final_and_says_so(wait_until, monkeypatch) -> None:
    """`reattach.auto` off: a lost connection is not retried; the same row as after Esc."""
    monkeypatch.setenv("TSB_TUI_REATTACH_AUTO", "false")

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.02, drop_after_chunks=2)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(160, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: "not reattaching" in screen.status.text, "the row")
                await pilot.pause(0.3)
                assert stub.attaches == 1

    asyncio.run(_check())


def test_a_second_refused_ticket_while_reattaching_is_a_dialog_not_a_loop(wait_until) -> None:
    """DT13: 4001 retries once with a new ticket; the second is final, and the operator is told."""

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.02, drop_after_chunks=2, attach_close_code_after_first=4001)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                _, dialog = await _ended(pilot, app, wait_until)
                assert str(dialog.query_one("#ended-title").render()) == "Could not stay attached"
                assert "refused the attach ticket twice" in str(dialog.query_one("#ended-body").render())

    asyncio.run(_check())


def test_an_end_met_while_reattaching_is_shown_as_that_end(wait_until) -> None:
    """The session was killed while the client was away: the reattach is refused 4010, and that is the state."""

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.02, drop_after_chunks=2, attach_close_code_after_first=4010)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                _, dialog = await _ended(pilot, app, wait_until)
                assert str(dialog.query_one("#ended-title").render()) == "Session ended · killed (close 4010)"

    asyncio.run(_check())


def test_an_end_keeps_an_unrelated_dialog_and_shows_the_end_in_the_row(wait_until) -> None:
    """A session's end must not close a dialog the operator is using for something else: the New-session dialog
    and its picked target survive, the end is said in the status row at once, and the ended screen comes up only
    after the operator closes that dialog."""

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.02, stream_close=(4010, 20))  # the close lands well after attach
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                screen._new_session_dialog("picked-target")
                await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "the new-session dialog")
                dialog = app.screen
                assert dialog._initial_target == "picked-target", "the dialog opened on the picked target"
                await wait_until(pilot, lambda: screen.ended is not None, "the end behind the open dialog")
                assert app.screen is dialog, "the operator's unrelated dialog must stay open over the end"
                assert dialog._initial_target == "picked-target", "its picked target survives"
                assert "✕ ended · stub-target · killed" in screen.status.text, "the end said in the row at once"
                await pilot.press("escape")
                await wait_until(pilot, lambda: isinstance(app.screen, EndedScreen),
                                 "the ended screen after the dialog closes")

    asyncio.run(_check())


def test_an_end_pops_the_ended_session_s_own_dialog_and_shows_the_ended_screen_at_once(wait_until) -> None:
    """A dialog opened for the session that ended is popped, and the ended screen comes up at once."""

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.02, stream_close=(4010, 20))
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                screen.do_who()
                await wait_until(pilot, lambda: isinstance(app.screen, WhosHereScreen), "the who's here dialog")
                await wait_until(pilot, lambda: screen.ended is not None, "the end behind the dialog")
                await wait_until(pilot, lambda: isinstance(app.screen, EndedScreen), "the ended screen at once")
                assert not any(isinstance(s, WhosHereScreen) for s in app.screen_stack), \
                    "the ended session's own dialog is popped"

    asyncio.run(_check())


def test_an_end_with_no_dialog_open_shows_the_ended_screen_at_once(wait_until) -> None:
    """With no dialog open, behaviour is as today."""

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.02, stream_close=(4010, 3))
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: isinstance(app.screen, EndedScreen), "the ended screen at once")
                assert "✕ ended · stub-target · killed" in screen.status.text

    asyncio.run(_check())


@pytest.mark.parametrize("unrelated_above", [False, True])
def test_an_end_clears_nested_removal_dialogs_when_exposed(wait_until, unrelated_above) -> None:
    """Removal belongs to Who's here's session, including beneath an unrelated operator dialog."""
    from tsb_tui.screens.whos_here import ConfirmRemoveScreen

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.02, stream_close=(4010, 40))
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                screen.do_who()
                await wait_until(pilot, lambda: isinstance(app.screen, WhosHereScreen), "Who's here")
                who = app.screen
                who._confirm_remove("stub-other")
                await wait_until(pilot, lambda: isinstance(app.screen, ConfirmRemoveScreen), "remove confirmation")
                removal = app.screen
                assert screen._dialog_session[removal] == screen.session_id
                if unrelated_above:
                    screen._new_session_dialog("picked-target")
                    await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "unrelated dialog")
                    unrelated = app.screen
                await wait_until(pilot, lambda: screen.ended is not None, "session end")
                if unrelated_above:
                    assert app.screen is unrelated
                    assert who in app.screen_stack and removal in app.screen_stack
                    await pilot.press("escape")
                await wait_until(pilot, lambda: isinstance(app.screen, EndedScreen), "ended screen")
                assert who not in app.screen_stack and removal not in app.screen_stack
                assert sum(isinstance(s, EndedScreen) for s in app.screen_stack) == 1

    asyncio.run(_check())


def test_front_session_end_keeps_another_sessions_end_confirmation(wait_until) -> None:
    """An end confirmation belongs to its selected session, even while a different session is in front."""
    from tsb_tui.screens.chrome_dialogs import ConfirmEndScreen

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.02, stream_close=(4010, 40))
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                screen.end_session("other-session")
                await wait_until(pilot, lambda: isinstance(app.screen, ConfirmEndScreen), "other session confirmation")
                confirmation = app.screen
                assert screen._dialog_session[confirmation] == "other-session"
                await wait_until(pilot, lambda: screen.ended is not None, "front session end")
                assert app.screen is confirmation
                await pilot.press("escape")
                await wait_until(pilot, lambda: isinstance(app.screen, EndedScreen), "deferred ended screen")

    asyncio.run(_check())


def test_session_end_closes_its_scrollback_search_prompt(wait_until) -> None:
    """Search in a session's scrollback ends with that session rather than blocking its ended screen."""
    from tsb_tui.screens.chrome_dialogs import SearchPrompt

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.02, stream_close=(4010, 40))
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                screen.do_scroll()
                screen.do_search()
                await wait_until(pilot, lambda: isinstance(app.screen, SearchPrompt), "search prompt")
                prompt = app.screen
                await wait_until(pilot, lambda: isinstance(app.screen, EndedScreen), "ended screen")
                assert prompt not in app.screen_stack
                assert not screen._scroll_mode

    asyncio.run(_check())
