"""The one status row: who types, the session, what you can do now, and every hint a click (D11, D12, D43; F12).

Why these matter (Rule 9): the operator is never asked to remember a key (D40). The row is where a
person learns, at a glance, whether their next keystroke reaches the session or is refused, who has
the keyboard instead, and the one click that fixes it. A pill that says "you type" when the core
would refuse the keys, or hints that do not shrink in a fixed order and push `≡ Menu` off a phone's
65 columns, send the operator to guess. Layout tests read `build_pieces` at each width; click and
position tests drive the real widget under a Textual pilot against the contract's fixture server (DT7).
"""

from __future__ import annotations

import asyncio

import pytest
from input_server import InputRecordingApp, attached, make_session, other_client
from model_frames import state_of, token_frame
from model_frames import validated as validated_frame
from stub_server import create_owner_session, record_output
from textual import events
from textual.command import CommandPalette
from tsbc import _serve

from tsb_tui.app import TsbTuiApp
from tsb_tui.model import actions as A
from tsb_tui.screens.chrome_dialogs import ConfirmEndScreen
from tsb_tui.screens.size_dialog import SizeDialog
from tsb_tui.screens.whos_here import WhosHereScreen
from tsb_tui.widgets.status_row import (
    Hint,
    KeysTarget,
    StatusInputs,
    StatusRow,
    build_pieces,
)

ME, ME_USER = "client-me", "fixture-user"
PEER, PEER_USER = "client-peer", "fixture-peer"
VIEWER, VIEWER_USER = "client-viewer", "fixture-viewer"
THREE = ((ME, ME_USER, True), (PEER, PEER_USER, True), (VIEWER, VIEWER_USER, False))
# The packaged `status.hint_priority` (config_default.toml).
PRIORITY = ["menu", "answer", "take_back", "fleet", "who", "detach", "end", "size", "scroll", "inspector", "keys"]


def _inputs(state, **overrides) -> StatusInputs:
    values = {
        "state": state, "session": "rtr-core-01", "range_label": "", "keys_not_sent": False,
        "leader": "^\\", "leader_keys": A.leader_keys(), "hint_priority": PRIORITY,
        "keys_indicator": True, "target": None, "attaching": False,
    }
    values.update(overrides)
    return StatusInputs(**values)


def _text(state, width: int = 120, **overrides) -> str:
    return "".join(p.text for p in build_pieces(_inputs(state, **overrides), width))


def _hints(state, width: int, **overrides) -> list[str]:
    """The action ids whose hints are on the row at this width, left to right."""
    seen: list[str] = []
    for piece in build_pieces(_inputs(state, **overrides), width):
        if piece.action and not piece.role.startswith("pill") and piece.action not in seen:
            seen.append(piece.action)
    return seen


# -- F12: the pill and the words after it, by state --


def test_you_type_reads_as_a_filled_pill_the_session_its_state_and_rec() -> None:
    """D21, D43: the person who holds the keyboard is told so, with the session, that it is live and that
    it is being recorded (session record `recording`: the warning is never hidden once attached)."""
    state = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)
    text = _text(state)
    assert text.startswith("‹ you type ›  …  ● live  ● REC")
    assert build_pieces(_inputs(state), 120)[0].role == "pill_type"


def test_view_only_while_an_agent_types_in_your_session_says_typing_takes_it() -> None:
    """F12 row 2, Frontend §9: the owner whose keyboard an agent holds must be told that typing takes it
    back (terminal-size §5, 5.53) - the owner's brake is useless if nobody knows it is there."""
    state = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(PEER, PEER_USER), clients=THREE)
    text = _text(state)
    assert text.startswith("‹ view only ›  fixture-peer types · you own it: typing takes it · …")
    assert build_pieces(_inputs(state), 120)[0].role == "pill_view"


def test_view_only_for_a_client_who_may_type_and_for_one_whose_right_is_read_only() -> None:
    """F12 rows 4 and 5, Frontend §9 "view-only mark": the two are different: one waits for the keyboard,
    the other cannot ever have it (session-record `may_control`), and the row must not suggest otherwise."""
    waits = state_of(me=(PEER, PEER_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)
    text = _text(waits)
    assert "‹ view only ›  fixture-user types · …" in text
    assert "read-only" not in text

    never = state_of(me=(VIEWER, VIEWER_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)
    assert "fixture-user types · your right is read-only · …" in _text(never)


def test_nobody_types_names_who_put_the_keyboard_aside() -> None:
    """F12 row 6, typing-ownership step 6: after the owner's revoke the token sits in escrow (reason
    `owner`) until the owner hands it out, and the row says who did it; with no such move it says nobody
    has it (a session whose clients cannot type)."""
    state = state_of(me=(PEER, PEER_USER), owner=ME_USER, holder=(PEER, PEER_USER), clients=THREE)
    state.apply_frame(token_frame(None, "owner"))
    text = _text(state)
    assert text.startswith("‹ nobody types ›  fixture-user put the keyboard aside · …")
    assert build_pieces(_inputs(state), 120)[0].role == "pill_none"

    escrow = state_of(me=(PEER, PEER_USER), owner=ME_USER, holder=None, clients=THREE)
    assert "nobody has the keyboard" in _text(escrow)


def test_a_take_over_reads_both_ways() -> None:
    """F12 rows 3 and 7, Frontend §9 auto-transfer notice: the owner who takes the keyboard sees whom it
    was taken from; the client it was taken from sees that the owner did, so a keyboard that vanished
    mid-command is explained, not mysterious."""
    owner = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(PEER, PEER_USER), clients=THREE)
    owner.apply_frame(token_frame((ME, ME_USER), "takeover"))
    assert _text(owner).startswith("‹ you type ›  …  ● live  ● REC  you took the keyboard from fixture-peer")

    peer = state_of(me=(PEER, PEER_USER), owner=ME_USER, holder=(PEER, PEER_USER), clients=THREE)
    peer.apply_frame(token_frame((ME, ME_USER), "takeover"))
    assert _text(peer).startswith("‹ view only ›  fixture-user took the keyboard (owner) · …")


def test_the_pan_range_and_the_unsent_keys_note_follow_the_state() -> None:
    """D4, DT4 (a): a canvas that pans says which columns it shows, and a person whose keys were held
    back (the guard, because they are not the typist) is told "your keys were not sent" instead of
    typing into a screen that does not answer."""
    state = state_of(me=(VIEWER, VIEWER_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)
    text = _text(state, 200, range_label="cols 1-76 of 132 ▸", keys_not_sent=True)
    assert "cols 1-76 of 132 ▸" in text
    assert "your keys were not sent" in text
    assert "cols" not in _text(state) and "not sent" not in _text(state)
    # Squeezed, the range gives way before the note does: the note explains a keystroke that did nothing.
    squeezed = _text(state, 120, range_label="cols 1-76 of 132 ▸", keys_not_sent=True)
    assert "your keys were not sent" in squeezed and "cols 1-76" not in squeezed


def test_keys_arrow_replaces_the_pill_whenever_the_keys_go_elsewhere() -> None:
    """D14: while a dialog, the Menu or the leader has the keys, the row says where they go and shows that
    holder's own hints, not the session's. With `status.keys_indicator` off the pill stays."""
    state = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)
    target = KeysTarget("who's here", (Hint("↑↓", "choose"), Hint("t", "Take it"), Hint("Esc", "close")))
    text = _text(state, target=target)
    assert text.startswith("keys → who's here")
    assert "you type" not in text
    assert text.rstrip().endswith("↑↓ choose   t Take it   Esc close")
    assert "‹ you type ›" in _text(state, target=target, keys_indicator=False)


def test_a_session_that_is_not_attached_yet_has_no_pill() -> None:
    """Before `attached` nobody knows who types, and a pill would be a guess. The row says it is
    attaching and still offers the way out and the Menu."""
    text = _text(None, attaching=True)
    assert text.startswith("◐ attaching · rtr-core-01")
    assert "‹" not in text
    assert "^\\ d Detach" in text and "≡ Menu ^\\" in text


# -- the hints: key first, dropping by priority, the Menu last --


def test_hints_are_key_first_and_end_with_the_menu_and_the_fleet() -> None:
    """D11, D43: `^\\ d Detach` reads key then label, and the row ends `≡ Menu ^\\   ▦ Fleet ^\\ f`, the
    two universal entries, in that order."""
    state = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)
    text = _text(state, 200)
    assert text.rstrip().endswith(
        "^\\ d Detach   ^\\ w Who's here   ^\\ r Size   ^\\ [ Scroll   ^\\ x End…   ≡ Menu ^\\   ▦ Fleet ^\\ f")
    assert len(text) == 200


def test_a_hint_for_an_action_that_is_not_available_is_not_shown() -> None:
    """D11: the row lists the enabled actions. Who's here and Size have nothing to show before the attach."""
    assert _hints(None, 200) == ["detach", "menu", "fleet"]


def _drop_order(state, wide: bool, **overrides) -> list[str]:
    """The order in which hints leave the row as it narrows, within one chrome: from 200 down to 80
    columns (normal), or from 79 down (compact, whose pill is shorter, so the two sweeps are separate)."""
    widths = range(200, 79, -1) if wide else range(79, 30, -1)
    present = set(_hints(state, widths[0], **overrides))
    dropped: list[str] = []
    for width in widths:
        now = set(_hints(state, width, **overrides))
        assert now <= present, f"a hint came back at {width} columns"
        dropped += sorted(present - now)
        present = now
    return dropped


def test_hints_drop_from_the_right_by_priority_and_the_menu_is_the_last_to_go() -> None:
    """D43, `status.hint_priority`: as the window narrows the hints leave in the knob's reverse order
    (lowest priority first), one at a time and never back, and `≡ Menu ^\\` is still there when the rest
    are gone, so a phone's 65 columns keep the one click that reaches everything."""
    state = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)
    in_order = [name for name in reversed(PRIORITY) if name != "menu"]
    normal, compact = _drop_order(state, True), _drop_order(state, False)
    assert normal == ["scroll", "size", "end", "detach"], "all seven fit at 200; Fleet and the Menu outlast these"
    assert compact == ["detach", "who", "fleet"], "what is left at 79 columns keeps the Menu to the end"
    for dropped in (normal, compact):
        assert dropped == sorted(dropped, key=in_order.index), "a hint left before a lower-priority one"
    for width in (65, 120, 200):
        row = _text(state, width)
        assert len(row) == width
        assert "≡ Menu ^\\" in row, f"the Menu is gone at {width}"


def test_a_different_priority_drops_a_different_hint_first() -> None:
    """`status.hint_priority` is a knob the operator owns: reversing it must reverse what drops first, or
    the knob is read nowhere (DT19)."""
    state = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)
    reversed_priority = ["menu", "fleet", "scroll", "size", "end", "detach", "who"]
    assert _drop_order(state, True, hint_priority=reversed_priority) == ["who", "detach", "end", "size"]
    assert _drop_order(state, False, hint_priority=reversed_priority) == ["size", "scroll", "fleet"]


def test_the_phone_row_is_compact_and_carries_the_pan_range_and_the_menu() -> None:
    """Design T2: at 65 columns the pill loses its inner spaces and a live session says nothing about
    being live; what is left is the pill, the session, REC, the pan range and `≡ Menu ^\\`."""
    state = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)
    row = _text(state, 65, range_label="cols 1-61 of 116 ▸")
    assert row.startswith("‹you type›  …")
    assert "● live" not in row
    assert "● REC  cols 1-61 of 116 ▸" in row
    assert row.endswith("≡ Menu ^\\") and len(row) == 65


def test_the_pan_range_stays_on_a_phone_row_while_there_is_room_for_it() -> None:
    """Mockup T2/F12: the range (`cols a-b of N ▸`) is how an operator on 65 columns knows the screen is wider than their
    window. After the owner took the keyboard the row holds a note on whom it was taken from; that note leaves first,
    so the range is not lost to it while the row still has room. Where the row is wide enough, both are there."""
    state = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(PEER, PEER_USER), clients=THREE)
    state.apply_frame(token_frame((ME, ME_USER), "takeover"))
    row = _text(state, 65, range_label="cols 1-61 of 116 ▸")
    assert "cols 1-61 of 116 ▸" in row, row  # fails today: the range is dropped before the note
    assert "you took the keyboard" not in row
    assert row.endswith("≡ Menu ^\\") and len(row) == 65
    roomy = _text(state, 120, range_label="cols 1-61 of 116 ▸")
    assert "you took the keyboard from fixture-peer" in roomy and "cols 1-61 of 116 ▸" in roomy


def test_when_even_the_menu_leaves_no_room_for_the_words_the_name_goes_whole_not_the_menu() -> None:
    """D43: a very narrow row still ends in `≡ Menu ^\\`; it is the session's name that gives way, whole: a name
    cut to `a-very-long…` is a different name (principle 4), and a row of half words is worse than a short row."""
    state = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)
    row = _text(state, 36, session="a-very-long-session-name-indeed")
    assert row.endswith("≡ Menu ^\\") and len(row) == 36
    assert "a-very-long" not in row and "…" in row
    assert row.startswith("‹you type›")


# -- the real widget: clicks, position, the pan range --


def _text_of(screen) -> str:
    return screen.status.text


def test_a_click_on_the_pill_opens_whos_here_and_a_click_on_a_hint_runs_its_action(
        serve, wait_until) -> None:
    """D11, D12, plan R5: every hint and the pill take a single click on Textual 8.2.8, from a pilot, no
    key. The pill opens Who's here; Size opens its dialog; Detach leaves for the sessions list."""

    async def _check() -> None:
        _serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 30)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            await wait_until(pilot, lambda: "Detach" in _text_of(screen), "the status row to fill")
            row = screen.status
            assert set(row.actions()) == {"who", "detach", "size", "scroll", "end", "menu", "fleet"}

            assert row.text.startswith("‹ you type ›")
            await pilot.click(StatusRow, offset=(3, 0))  # on the pill itself, not on the `Who's here` hint
            await wait_until(pilot, lambda: isinstance(app.screen, WhosHereScreen),
                             "Who's here to open from the keyboard pill")
            app.screen.dismiss(None)
            await wait_until(pilot, lambda: app.screen is screen and "keys →" not in _text_of(screen),
                             "the dialog to close and the row to take its hints back")

            await pilot.click(StatusRow, offset=(row.hit_x("who", after=14) + 1, 0))
            await wait_until(pilot, lambda: isinstance(app.screen, WhosHereScreen),
                             "Who's here to open from its hint")
            app.screen.dismiss(None)
            await wait_until(pilot, lambda: app.screen is screen and "keys →" not in _text_of(screen),
                             "the dialog to close and the row to take its hints back")

            await pilot.click(StatusRow, offset=(row.hit_x("size") + 1, 0))
            await wait_until(pilot, lambda: isinstance(app.screen, SizeDialog), "Size to open")
            app.screen.dismiss(None)
            await wait_until(pilot, lambda: app.screen is screen and "keys →" not in _text_of(screen),
                             "the dialog to close and the row to take its hints back")

            await pilot.click(StatusRow, offset=(row.hit_x("end") + 1, 0))
            await wait_until(pilot, lambda: isinstance(app.screen, ConfirmEndScreen), "End… to ask first")
            app.screen.dismiss(None)
            await wait_until(pilot, lambda: app.screen is screen and "keys →" not in _text_of(screen),
                             "the dialog to close and the row to take its hints back")

            await pilot.click(StatusRow, offset=(row.hit_x("detach") + 1, 0))
            await wait_until(pilot, lambda: not screen.slots and screen.sidebar_open,
                             "Detach to leave the session and open the sidebar")

    asyncio.run(_check())


def test_the_status_row_sits_where_status_position_says(serve, monkeypatch, wait_until) -> None:
    """`status.position`: bottom is the default (design §2.6), top moves the row above the canvas. The
    canvas gives up exactly one row either way (D43: one row)."""
    for position in ("bottom", "top"):
        monkeypatch.setenv("TSB_TUI_STATUS_POSITION", position)

        async def _check(position=position) -> None:
            _serve_app, url = serve()
            app = TsbTuiApp(url)
            async with app.run_test(size=(100, 20)) as pilot:
                session_id = create_owner_session(app, _serve.TARGET, 80, 24)
                screen = await attached(pilot, app, session_id, wait_until)
                row, canvas = screen.status, screen.canvas
                assert row.region.height == 1 and canvas.region.height == 19
                if position == "top":
                    assert row.region.y == 0 and canvas.region.y == 1
                else:
                    assert row.region.y == 19 and canvas.region.y == 0

        asyncio.run(_check())


def test_status_show_off_removes_the_row_and_the_canvas_takes_its_line(
        serve, monkeypatch, wait_until) -> None:
    """`status.show`: off, there is no row, the canvas keeps the whole window, and the size a request
    asks for is the window minus the rail only. The leader still opens the Menu."""
    monkeypatch.setenv("TSB_TUI_STATUS_SHOW", "false")

    async def _check() -> None:
        _serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(100, 20)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            assert screen.status is None
            assert screen.canvas.region.height == 20
            assert screen.fit_size() == (100 - app.cfg["rail.width"], 20)

    asyncio.run(_check())


def test_the_pan_range_shows_in_the_row_and_follows_a_pan(serve, wait_until) -> None:
    """D4: an 80-column window over a 132-column session shows 76 columns, the rail having four, says which
    columns it shows, and the row updates when the window moves, so the operator always knows what part of
    the screen they see."""

    async def _check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 132, 40)
            record_output(serve_app, session_id, b"\x1b[2J\x1b[1;1Hhello")
            screen = await attached(pilot, app, session_id, wait_until)
            await wait_until(pilot, lambda: "cols 1-76 of 132 ▸" in _text_of(screen),
                             "the status row to show the range")
            screen.canvas.pan_to(40, 0)
            await wait_until(pilot, lambda: "cols 41-116 of 132 ▸" in _text_of(screen),
                             "the status row to follow the pan")

    asyncio.run(_check())


def test_the_row_says_your_keys_were_not_sent_after_the_guard_holds_a_key_back(
        serve, wait_until) -> None:
    """DT4 (a): the guard drops a viewer's typing and the viewer must be told, in the row, why nothing
    happened - and told no more once the keyboard moves (the note describes a state, not an event)."""

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        session_id = make_session(url)
        with other_client(url, _serve.USER, session_id):
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 24)) as pilot:
                app.client.login(_serve.VIEWER, _serve.PASSWORD)
                screen = await attached(pilot, app, session_id, wait_until)
                assert "not sent" not in _text_of(screen)

                await pilot.press("x")
                await wait_until(pilot, lambda: "your keys were not sent" in _text_of(screen),
                                 "the status row to say so")
                assert "‹ view only ›" in _text_of(screen)

                screen.post_message(events.Paste("p"))  # paste goes through the same guard
                await wait_until(pilot, lambda: screen.suppressed_inputs == 2, "paste to be held back")

    asyncio.run(_check())


@pytest.mark.parametrize("width", [65, 120, 200])
def test_the_menu_and_fleet_entries_are_on_the_row_and_clickable_at_three_widths(
        serve, wait_until, width) -> None:
    """Design §3.4 test 5: `≡ Menu` and `▦ Fleet` are in the status row and clickable at 65, 120 and 200
    columns. (The rail's `≡` and `▦▸` are phase 7's.) At 65 the Fleet may have dropped for room; the
    Menu may not."""

    async def _check() -> None:
        _serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(width, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            await wait_until(pilot, lambda: "Menu" in _text_of(screen), "the row to fill")
            actions = screen.status.actions()
            assert "menu" in actions
            if width >= 120:
                assert "fleet" in actions

            await pilot.click(StatusRow, offset=(screen.status.hit_x("menu") + 1, 0))
            await wait_until(pilot, lambda: isinstance(app.screen, CommandPalette),
                             "the Menu to open from a click")

    asyncio.run(_check())


def test_the_packaged_priority_is_the_one_these_tests_use() -> None:
    from pathlib import Path

    from tsb_tui import config

    cfg = config.load(config_file=Path("/nonexistent"), environ={}, overrides={"size.auto_request": "true"})
    assert list(cfg["status.hint_priority"]) == PRIORITY


def test_at_120_columns_the_answer_and_take_back_hints_come_ahead_of_detach_end_and_fleet_as_f12_draws_them() -> None:
    """Mockup F12, rows "view only, you own it" and "asks you". A state's own hint is the one click that fixes it, so
    at 120 columns it keeps its place and the general hints give way: with a question waiting the row offers Answer,
    Who's here and the Menu and not Detach, End or Fleet; while another client holds the keyboard of a session the
    person owns it offers Take back and the Menu. (The packaged `status.hint_priority` is the orchestrator's DEFAULT for
    the operator's veto; the mockup wins over the earlier default, which dropped these two first.)"""

    asking = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)
    asking.apply_frame(validated_frame({"t": "quiet_prompt", "v": 1, "prompt_id": "q" * 32,
                                        "code": "quiet_prompt.kill", "text": "Kill the session?"}))
    hints = _hints(asking, 120)
    assert hints[0] == "answer" and {"who", "menu"} <= set(hints), hints
    assert not {"detach", "end"} & set(hints), f"general hints kept ahead of Answer at 120: {hints}"
    narrower = _hints(asking, 100)  # where the row must choose: Answer outlives Detach and End
    assert narrower[0] == "answer" and "menu" in narrower and not {"detach", "end"} & set(narrower), narrower
    assert "^\\ a Answer: kill the session?" in _text(asking, 120)

    owned = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(PEER, PEER_USER), clients=THREE)
    hints = _hints(owned, 120)
    assert hints[0] == "take_back" and "menu" in hints, hints
    assert not {"detach", "end"} & set(hints), hints
    assert "^\\ t Take back" in _text(owned, 120)
