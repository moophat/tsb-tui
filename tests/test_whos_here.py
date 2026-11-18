"""Who's here: who types, and the owner's brake (design F5; typing-ownership steps 5-9, 12-13; client-remove).

Why these matter (Rule 9): the keyboard has one holder and an owner who can always take it back. Who's
here is where a person hands a session to an agent, takes it back mid-command, puts it aside, or
removes a client that should not be there; a button that sends the wrong frame, a refusal that never
reaches the person who asked, or a removal that happens without asking the name of what it removes would
each cost the owner their session or their trust in the screen. Servers: the contract's in-process
fixture server (DT7) with its `two-client` scenario and its `session.join.ro` viewer, recording what each
client sends (`input_server.py`); a second TUI is a second app against the same server.
"""

from __future__ import annotations

import asyncio
import re

from input_server import InputRecordingApp, attached, make_session, other_client
from stub_server import record_output
from textual.widgets import Button, DataTable, Static
from tsbc import _serve

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.prompts import EndedScreen
from tsb_tui.screens.terminal import TerminalScreen
from tsb_tui.screens.whos_here import ConfirmRemoveScreen, WhosHereScreen

TWO = "two-client"
DSR = b"\x1b[6n"
CURSOR_REPORT = re.compile(rb"\x1b\[\d+;\d+R")


def _sent(serve_app, kind: str) -> list[dict]:
    """The text frames of one type any client sent, as the server saw them."""
    return [f for f in list(serve_app.frames) if f.get("t") == kind]


def _notifications(app) -> list[str]:
    return [n.message for n in app._notifications._notifications.values()]


async def _open_dialog(pilot, app, screen, wait_until) -> WhosHereScreen:
    """Open Who's here the way an operator does: the leader and `w`, through this app's own pilot (two
    apps share one test task, so only an app's pilot reaches that app)."""
    await pilot.press("ctrl+backslash", "w")
    await wait_until(pilot, lambda: isinstance(app.screen, WhosHereScreen), "Who's here to open")
    dialog = app.screen
    await wait_until(
        pilot, lambda: dialog.query("#whos-clients") and dialog.query_one(DataTable).row_count
        == len(screen.pump.state.clients), "Who's here to list the clients")
    return dialog


def _select(dialog: WhosHereScreen, client_id: str) -> None:
    table = dialog.query_one("#whos-clients", DataTable)
    table.move_cursor(row=[row.key.value for row in table.ordered_rows].index(client_id))


def _button(dialog, action_id: str) -> Button:
    return dialog.query_one(f"#{action_id}", Button)


def test_hand_to_selected_moves_the_keyboard_to_that_client(serve, wait_until) -> None:
    """Typing-ownership step 5 (C2/token-handoff): the holder hands the keyboard to a client, and the
    `token` frame (reason `handoff`) shows it moved: this client is now view-only, the dialog marks the
    peer as the one that types, and the operator is told. The frame sent is `handoff` with `to` the peer's
    client id, and the button's own click is all it took."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            session_id = make_session(url, target=TWO)
            app.client.login(_serve.USER, _serve.PASSWORD)
            screen = await attached(pilot, app, session_id, wait_until)
            state = screen.pump.state
            with other_client(url, _serve.PEER, session_id) as peer:
                await wait_until(pilot, lambda: len(state.clients) == 2, "the peer to be listed")
                dialog = await _open_dialog(pilot, app, screen, wait_until)
                assert state.holds_keyboard
                _select(dialog, peer.client_id)
                await wait_until(pilot, lambda: not _button(dialog, "hand").disabled, "Hand to selected to enable")

                await pilot.click("#hand")
                await wait_until(pilot, lambda: state.holder_client_id == peer.client_id,
                                 "the keyboard to move to the peer")
                assert _sent(serve_app, "handoff") == [{"t": "handoff", "v": 1, "to": peer.client_id}]
                assert state.token_history[-1]["reason"] == "handoff"
                assert not state.handoff_in_flight
                table = dialog.query_one("#whos-clients", DataTable)
                await wait_until(pilot, lambda: "● types" in " ".join(
                    str(c) for c in table.get_row(peer.client_id)), "the dialog to mark the peer as typing")
                assert "‹ view only ›" in screen.status.text or "keys → who's here" in screen.status.text
                assert any("now holds the keyboard" in m for m in _notifications(app))

    asyncio.run(_check())


def test_the_dialog_frames_its_buttons_marks_remove_as_destructive_selects_neutrally_and_fits_a_phone(
        serve, wait_until) -> None:
    """Mockup F5, design §2.8. The buttons carry `[ ]` frames like the stale and quiet-prompt dialogs, so they read as
    things to press; Remove disconnects another person, so it is drawn in the theme's danger style, apart from the
    three that only move the keyboard; the selected row is the theme's neutral surface, because the accent means "you
    type" and must not sit on another client's row; and at a phone's 65 columns (and below) the table fits its dialog
    without an inner scrollbar, the client id, which names what Hand to selected and Remove act on, kept whole."""

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(65, 30)) as pilot:
            session_id = make_session(url, target=TWO)
            app.client.login(_serve.USER, _serve.PASSWORD)
            screen = await attached(pilot, app, session_id, wait_until)
            with other_client(url, _serve.PEER, session_id) as peer:
                await wait_until(pilot, lambda: len(screen.pump.state.clients) == 2, "the peer to be listed")
                dialog = await _open_dialog(pilot, app, screen, wait_until)
                _select(dialog, peer.client_id)
                assert {a: _button(dialog, a).label.plain for a in ("take", "hand", "aside", "remove")} == {
                    "take": "[ Take it ]", "hand": "[ Hand to selected ]", "aside": "[ Put it aside ]",
                    "remove": "[ Remove selected… ]"}  # fails today: the labels have no frames
                assert _button(dialog, "remove").variant == "error"
                assert {_button(dialog, a).variant for a in ("take", "hand", "aside")} == {"default"}
                frame = dialog.query_one("#whos-panel").styles.border_top[1].hex.lower()
                assert frame == app.cfg["theme.frame"] and frame != app.cfg["theme.accent"]  # fails today: $primary is the accent

                table = dialog.query_one(DataTable)
                cursor = table.get_component_styles("datatable--cursor").background
                assert cursor.hex.lower() == app.cfg["theme.surface_3"], "the selection is the neutral surface"
                assert cursor.hex.lower() != app.cfg["theme.accent"]

                for width in (65, 50):
                    await pilot.resize_terminal(width, 30)
                    await wait_until(pilot, lambda: table.size.width > 0 and table.virtual_size.width <= table.size.width
                                     and not table.show_horizontal_scrollbar,
                                     f"the table to fit the dialog at {width} columns")  # fails today at 50
                    assert table.get_row(peer.client_id)[1] == peer.client_id, "the client column gives way last"

    asyncio.run(_check())


def test_take_it_as_the_owner_takes_the_keyboard_from_the_peer(serve, wait_until) -> None:
    """Typing-ownership step 9, terminal-size §5 (5.53): the owner's explicit take-over is `handoff` to its
    own client id, token reason `takeover`, and it is the owner's whether or not anyone agrees. The
    button is greyed while this client already holds the keyboard, and the dialog does not toast the
    operator about something they just did themselves."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        session_id = make_session(url, target=TWO)
        with other_client(url, _serve.PEER, session_id) as peer:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 30)) as pilot:
                app.client.login(_serve.USER, _serve.PASSWORD)
                screen = await attached(pilot, app, session_id, wait_until)
                state = screen.pump.state
                assert not state.holds_keyboard and state.is_owner_client
                dialog = await _open_dialog(pilot, app, screen, wait_until)
                assert not _button(dialog, "take").disabled

                await pilot.click("#take")
                await wait_until(pilot, lambda: state.holds_keyboard, "the keyboard to come to the owner")
                assert _sent(serve_app, "handoff") == [{"t": "handoff", "v": 1, "to": state.client_id}]
                assert state.token_history[-1]["reason"] == "takeover"
                assert state.holder_client_id != peer.client_id
                await wait_until(pilot, lambda: _button(dialog, "take").disabled, "Take it to grey out")
                assert not any("hold the keyboard" in m or "handed to you" in m for m in _notifications(app))

    asyncio.run(_check())


def test_put_it_aside_sends_the_keyboard_to_escrow(serve, wait_until) -> None:
    """Typing-ownership step 6 (C2/token-owner-revoke-reassign): the owner's `handoff` to null puts the
    keyboard in escrow, reason `owner`, and the row says who did it. Nobody types until the owner hands it
    out again, which is the point of the button."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            session_id = make_session(url, target=TWO)
            app.client.login(_serve.USER, _serve.PASSWORD)
            screen = await attached(pilot, app, session_id, wait_until)
            state = screen.pump.state
            with other_client(url, _serve.PEER, session_id):
                await wait_until(pilot, lambda: len(state.clients) == 2, "the peer to be listed")
                dialog = await _open_dialog(pilot, app, screen, wait_until)

                await pilot.click("#aside")
                await wait_until(pilot, lambda: state.holder is None and state.token_history, "escrow")
                assert _sent(serve_app, "handoff") == [{"t": "handoff", "v": 1, "to": None}]
                assert state.token_history[-1]["reason"] == "owner"
                await wait_until(pilot, lambda: _button(dialog, "aside").disabled, "Put it aside to grey out")
                dialog.dismiss(None)
                await wait_until(pilot, lambda: app.screen is screen and "keys →" not in screen.status.text,
                                 "the dialog to close")
                assert screen.status.text.startswith("‹ nobody types ›  fixture-user put it aside")

    asyncio.run(_check())


def test_handing_to_a_view_only_client_shows_the_refusal_inside_the_dialog_and_unblocks_input(
        serve, wait_until) -> None:
    """Typing-ownership rule 8 (C2/handoff-refused, reason `view_only`): a refusal is answered, never
    silent, and it must reach the person who asked, here in the dialog. The refusal also ends the "in
    flight" state, since no `token` frame follows it (steps 12-13): after it, the holder's session query
    is still answered (DT4 b), where a flag left set would have silenced every reply for good."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            session_id = make_session(url, target=TWO)
            app.client.login(_serve.USER, _serve.PASSWORD)
            screen = await attached(pilot, app, session_id, wait_until)
            state = screen.pump.state
            with other_client(url, _serve.VIEWER, session_id) as viewer:
                await wait_until(pilot, lambda: len(state.clients) == 2, "the viewer to be listed")
                dialog = await _open_dialog(pilot, app, screen, wait_until)
                _select(dialog, viewer.client_id)
                await wait_until(pilot, lambda: not _button(dialog, "hand").disabled, "Hand to selected")

                await pilot.click("#hand")
                note = dialog.query_one("#whos-note", Static)
                await wait_until(pilot, lambda: "read-only" in str(note.render()),
                                 "the refusal to show in the dialog")
                assert state.holds_keyboard, "a refused hand-off moves nothing"
                assert not state.handoff_in_flight
                assert state.last_request_refused == {"request": "handoff", "reason": "view_only"}

                dialog.dismiss(None)
                await wait_until(pilot, lambda: app.screen is screen, "the dialog to close")
                screen.canvas.feed(DSR)
                await wait_until(pilot, lambda: any(CURSOR_REPORT.fullmatch(b)
                                                    for b in serve_app.received(state.client_id)),
                                 "the holder's answer to a query after the refusal")

    asyncio.run(_check())


def test_remove_asks_first_naming_the_client_then_the_removed_client_is_closed_with_4014_and_stays_out(
        serve, wait_until) -> None:
    """Client-remove rule 9 (Frontend §9 "Requires confirmation"): the owner's kill button asks before it
    sends anything, and the question names the client. Cancel removes nothing. Confirm sends
    `DELETE …/clients/{id}`; the removed client is closed with 4014 and its TUI never re-attaches by itself
    (rule 8: 4014 is final), while the session and the owner live on."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        session_id = make_session(url, target=TWO)
        owner = TsbTuiApp(url)
        removed = TsbTuiApp(url)
        async with owner.run_test(size=(120, 30)) as pilot:
            owner.client.login(_serve.USER, _serve.PASSWORD)
            screen = await attached(pilot, owner, session_id, wait_until)
            state = screen.pump.state
            async with removed.run_test(size=(120, 30)) as other:
                removed.client.login(_serve.PEER, _serve.PASSWORD)
                peer_screen = await attached(other, removed, session_id, wait_until)
                await wait_until(pilot, lambda: len(state.clients) == 2, "both clients to be listed")
                peer_id = peer_screen.pump.state.client_id

                dialog = await _open_dialog(pilot, owner, screen, wait_until)
                _select(dialog, peer_id)
                await wait_until(pilot, lambda: not _button(dialog, "remove").disabled, "Remove to enable")

                await pilot.click("#remove")
                await wait_until(pilot, lambda: isinstance(owner.screen, ConfirmRemoveScreen),
                                 "the confirmation to open")
                question = str(owner.screen.query_one("#confirm-text", Static).render())
                assert "fixture-peer" in question and peer_id in question, question
                assert any(c["client_id"] == peer_id for c in state.clients), "nothing is removed before the answer"

                await pilot.click("#cancel")
                await wait_until(pilot, lambda: owner.screen is dialog, "the confirmation to close")
                assert peer_id in serve_app.clients[session_id], "Cancel removed the client"
                assert peer_screen.pump.state.close is None

                await pilot.click("#remove")
                await wait_until(pilot, lambda: isinstance(owner.screen, ConfirmRemoveScreen), "the question again")
                await pilot.click("#confirm")
                await wait_until(pilot, lambda: all(c["client_id"] != peer_id for c in state.clients),
                                 "the removed client to leave the set")
                await wait_until(other, lambda: isinstance(removed.screen, EndedScreen),
                                 "the removed client's TUI to say it was removed")
                assert str(removed.screen.query_one("#ended-title").render()) == "Removed by the owner"
                assert peer_screen.pump.state.close.show == "removed"
                assert peer_screen.pump.reattach_attempts == 0, "a removed client must not re-attach by itself"
                assert peer_id not in serve_app.clients[session_id], "the core kept the removed client"
                assert not state.closed and state.holds_keyboard, "the session and its owner live on"
                note = dialog.query_one("#whos-note", Static)
                await wait_until(pilot, lambda: "Removed fixture-peer" in str(note.render()),
                                 "the dialog to say it removed the client")


    asyncio.run(_check())


def test_a_session_that_ends_under_an_open_dialog_leaves_the_end_dialog_over_one_terminal_screen(
        serve, wait_until) -> None:
    """Session model §12, D14: the broker may end the session while Who's here is open (4010, killed). The
    operator must see the end dialog over the one terminal screen, with Who's here gone: a dialog left on
    top would hide the end, and a second terminal screen would hold a dead engine and socket for the rest
    of the run."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            session_id = make_session(url, target=TWO)
            app.client.login(_serve.USER, _serve.PASSWORD)
            screen = await attached(pilot, app, session_id, wait_until)
            await _open_dialog(pilot, app, screen, wait_until)

            sess = serve_app.sessions[session_id]
            with serve_app.lock:
                serve_app._drop_client(sess, screen.pump.state.client_id, code=4010, reason="")
            await wait_until(pilot, lambda: isinstance(app.screen, EndedScreen),
                             "the end dialog after the session ended")
            assert sum(isinstance(s, TerminalScreen) for s in app.screen_stack) == 1, app.screen_stack
            assert not any(isinstance(s, WhosHereScreen) for s in app.screen_stack)

    asyncio.run(_check())


def test_the_keyboard_coming_to_this_client_is_told_in_a_toast(serve, wait_until) -> None:
    """Frontend §9 (auto-transfer notice), D22: when another client hands the keyboard to this one, the
    operator is told, because a keyboard that arrives silently is a keyboard they type into by surprise.
    The holder (a peer, who attached first) hands it to this owner client."""

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        session_id = make_session(url, target=TWO)
        with other_client(url, _serve.PEER, session_id) as peer:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 30)) as pilot:
                app.client.login(_serve.USER, _serve.PASSWORD)
                screen = await attached(pilot, app, session_id, wait_until)
                state = screen.pump.state
                assert not state.holds_keyboard
                peer.handoff(state.client_id)
                await wait_until(pilot, lambda: state.holds_keyboard, "the keyboard to arrive")
                await wait_until(pilot, lambda: "The keyboard was handed to you." in _notifications(app),
                                 "the toast")

    asyncio.run(_check())


def test_two_tuis_on_one_session_show_who_types_and_who_only_watches(serve, wait_until, screen_text) -> None:
    """Phase-02's U7 item on the fixture server (the evidence itself is H3's, after PR B): two TUIs on one
    session, one `‹ you type ›` and one `‹ view only ›`, and Who's here, in each, names both. Each client's
    own screen text is exported as the operator sees it, and printed (run with -s) for the report."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        session_id = make_session(url, target=TWO, cols=100, rows=22)
        record_output(serve_app, session_id,
                      b"\x1b[2J\x1b[Hops@k8s-node-07:~$ uptime\r\n"
                      b" 14:30:58 up 63 days,  4:12,  1 user,  load average: 3.41, 3.12, 2.97\r\n"
                      b"ops@k8s-node-07:~$ ")
        typist, watcher = TsbTuiApp(url), TsbTuiApp(url)
        async with typist.run_test(size=(120, 24)) as a:
            typist.client.login(_serve.USER, _serve.PASSWORD)
            screen_a = await attached(a, typist, session_id, wait_until)
            async with watcher.run_test(size=(120, 24)) as b:
                watcher.client.login(_serve.VIEWER, _serve.PASSWORD)
                screen_b = await attached(b, watcher, session_id, wait_until)
                await wait_until(a, lambda: len(screen_a.pump.state.clients) == 2, "A to list both")
                await wait_until(b, lambda: len(screen_b.pump.state.clients) == 2, "B to list both")
                await _open_dialog(a, typist, screen_a, wait_until)
                await _open_dialog(b, watcher, screen_b, wait_until)
                await wait_until(a, lambda: "fixture-viewer" in screen_text(typist), "A's table")
                await wait_until(b, lambda: "● types" in screen_text(watcher), "B's table")

                text_a, text_b = screen_text(typist), screen_text(watcher)
                print("\n===== TUI A: fixture-user, the owner, holds the keyboard =====\n" + text_a)
                print("\n===== TUI B: fixture-viewer, read-only right =====\n" + text_b)
                assert "keys → who's here" in text_a and "keys → who's here" in text_b
                # Each client is "you" to itself and the other's login to the other.
                assert "fixture-viewer" in text_a and "fixture-user" not in text_a
                assert "fixture-user" in text_b and "fixture-viewer" not in text_b
                for text in (text_a, text_b):
                    assert "● types · owner" in text
                assert "can type" in text_a and "read-only" in text_a
                await a.press("escape")  # each app's own pilot: two apps share this task
                await b.press("escape")
                await wait_until(a, lambda: "‹ you type ›" in screen_text(typist), "A's pill")
                await wait_until(b, lambda: "‹ view only ›" in screen_text(watcher), "B's pill")
                print("\n===== TUI A after closing =====\n" + screen_text(typist))
                print("\n===== TUI B after closing =====\n" + screen_text(watcher))
                assert "your right is read-only" in screen_text(watcher)

    asyncio.run(_check())
