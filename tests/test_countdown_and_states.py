"""The countdown and the states a session's frames put in the status row (design D35, F12; session-countdown
rules 9-11; data-socket-redial `data_path`; byte-relay's refusal table; conventions V5).

Why these matter (Rule 9): a surface that drops `notice`, `data_path` or `input_refused` leaves an operator
typing into a session that is about to end, or whose input goes nowhere, with nothing on screen to say so.
That is issue #175: every C2 text frame was dropped. The countdown comes only from the core (rule 9): the
row computes words from `ends_at`, never the fact; an `ends_by` this build does not know shows `ends_at`
(rule 10); `ending` false withdraws it (rule 11). Servers: the TUI's own stub (DT7), which sends the frames
the fixture server never does. Frames are built here and checked against the contract's schema.
"""

from __future__ import annotations

import asyncio

from prompt_support import (
    NOW,
    create_stub_session,
    iso,
    notice,
    open_stub_terminal,
    validated,
)
from stub_server import SlowChunkedApp, StubServer
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.terminal import TerminalScreen


def _stub(*frames: dict, validate=None, **kwargs) -> SlowChunkedApp:
    for frame in frames:
        if validate is not None:
            validate(frame)
    return SlowChunkedApp(delay_s=0.05, after_attach_frames=list(frames), **kwargs)


def test_a_notice_with_ending_true_puts_its_countdown_on_screen(screen_text, wait_until) -> None:
    """#175: `notice` was dropped, so a session inside its warning window said nothing. A `link_timeout`
    window reads as F12's row does, with the time left from the core's `ends_at`."""

    async def _check() -> None:
        with StubServer(_stub(notice(252, "link_timeout"), validate=validated)) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 24)) as pilot:
                # Only what `main` has too: the app, the terminal screen and the screen's text (merge gate 4).
                app.wall_clock = lambda: NOW
                app.push_screen(TerminalScreen(create_stub_session(app)))
                await wait_until(pilot, lambda: "of slow output" in screen_text(app), "the session's output")
                await wait_until(pilot, lambda: "ends in 4:12" in screen_text(app), "the countdown")
                assert "▲ not answering · ends in 4:12 unless it answers" in screen_text(app)

    asyncio.run(_check())


def test_a_notice_with_ending_false_withdraws_the_countdown(
        screen_text, wait_until, server_frame_validator) -> None:
    """Rule 11: a flapping link that recovers withdraws its warning; the row goes back to `live`."""

    async def _check() -> None:
        withdrawn = notice(0, "link_timeout", ending=False)
        server_frame_validator(withdrawn)
        stub = _stub(notice(252, "link_timeout"), respond_to={"resize": [withdrawn]})
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 24)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: "ends in" in screen_text(app), "the countdown")
                await screen.pump.resize(100, 30, timeout=0)
                await wait_until(pilot, lambda: "ends in" not in screen_text(app), "the withdrawal")
                assert "● live" in screen_text(app)

    asyncio.run(_check())


def test_each_countdown_names_what_keeps_the_session_alive(
        screen_text, wait_until, server_frame_validator) -> None:
    """D35: the row says what resets the timer. idle: a byte either way; unattended: input from a client."""

    async def _check(ends_by: str, in_s: int, words: str) -> None:
        with StubServer(_stub(notice(in_s, ends_by), validate=server_frame_validator)) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(160, 24)) as pilot:
                await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: words in screen_text(app), words)

    asyncio.run(_check(
        "idle", 2 * 86400 + 23 * 3600 + 120, "no bytes either way · ends in 2d 23h · any byte keeps it"))
    asyncio.run(_check(
        "unattended", 3600 * 5 + 20 * 60,
        "no client input · ends in 5h 20m · any input from a client keeps it"))


def test_an_unknown_ends_by_shows_ends_at(screen_text, wait_until) -> None:
    """Rule 10: a timer this build does not know still shows when the session ends: `ends_at`, in local time.
    The frame is not valid at this tag (the schema's `ends_by` is closed), which is the point: a later
    contract adds a timer."""

    async def _check() -> None:
        from tsb_tui.model.countdown import countdown_text

        with StubServer(_stub(notice(600, "some_future_timer"))) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 24)) as pilot:
                await open_stub_terminal(app, pilot, wait_until)
                local = countdown_text("x", iso(NOW + 600), NOW)
                assert local is not None and local.startswith("▲ ends at ")
                await wait_until(pilot, lambda: local in screen_text(app), "the ends_at line")

    asyncio.run(_check())


def test_the_countdown_is_the_cores_ends_at_against_the_wall_clock(
        screen_text, wait_until, server_frame_validator) -> None:
    """Rule 9: the words are computed from `ends_at`; when the clock moves a minute the words move with it,
    and nothing else about the session changes."""

    async def _check() -> None:
        with StubServer(_stub(notice(252, "link_timeout"), validate=server_frame_validator)) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 24)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: "ends in 4:12" in screen_text(app), "the countdown")
                app.wall_clock = lambda: NOW + 60
                screen.refresh_status()
                await wait_until(pilot, lambda: "ends in 3:12" in screen_text(app), "the clock to move")

    asyncio.run(_check())


def test_format_duration_follows_f12s_two_shapes_and_the_rulings_between() -> None:
    """`4:12` and `2d 23h` are F12's; the rest follow the ruling: under a minute `0:SS`, an hour to a day `Hh Mm`."""
    from tsb_tui.model.countdown import format_duration

    assert format_duration(252) == "4:12"
    assert format_duration(9) == "0:09"
    assert format_duration(0) == "0:00"
    assert format_duration(-5) == "0:00"
    assert format_duration(3 * 3600 + 5 * 60 + 59) == "3h 5m"
    assert format_duration(2 * 86400 + 23 * 3600 + 3599) == "2d 23h"


def test_data_path_lost_and_restored_show_and_clear_the_restoring_row(
        screen_text, wait_until, server_frame_validator) -> None:
    """`data_path` lost: input may not reach the session (the core refuses it for now); restored ends it."""

    async def _check() -> None:
        lost = {"t": "data_path", "v": 1, "session_id": "stub-session-1", "state": "lost", "at": iso(NOW)}
        restored = dict(lost, state="restored")
        server_frame_validator(restored)
        stub = _stub(lost, validate=server_frame_validator, respond_to={"resize": [restored]})
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 24)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(
                    pilot, lambda: "◐ restoring the data path · input refused for now" in screen_text(app),
                    "the restoring row")
                await screen.pump.resize(100, 30, timeout=0)
                await wait_until(pilot, lambda: "restoring the data path" not in screen_text(app),
                                 "the row to clear")

    asyncio.run(_check())


def test_each_refusal_reason_has_its_line(screen_text, wait_until, server_frame_validator) -> None:
    """byte-relay's refusal table: `unreachable` and `replaying` have F12's rows; the two a client can
    cause itself say why."""

    async def _check(reason: str, words: str) -> None:
        frame = {"t": "input_refused", "v": 1, "reason": reason}
        with StubServer(_stub(frame, validate=server_frame_validator)) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(160, 24)) as pilot:
                await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: words in screen_text(app), words)

    asyncio.run(_check("unreachable", "◌ unreachable · TSB cannot reach its worker · input refused · not ended"))
    asyncio.run(_check("replaying", "◐ restoring the data path · input refused for now"))
    asyncio.run(_check("not_typist", "your input was refused: you do not hold the keyboard"))
    asyncio.run(_check("stale", "your input was refused: the screen may be wrong"))


def test_an_unknown_refusal_reason_reads_as_input_was_refused() -> None:
    """V5: a reason this build does not know is shown as the contract says. The frame is not valid at this
    tag, which is the case: a later contract adds a reason."""
    from tsb_tui.model.attachment import AttachmentState
    from tsb_tui.widgets.status_row import StatusInputs, build_pieces

    state = AttachmentState()
    state.apply_frame({"t": "input_refused", "v": 1, "reason": "from_the_future"})
    inputs = StatusInputs(state=state, session="s", range_label="", keys_not_sent=False, leader="^\\",
                          leader_keys={}, hint_priority=(), keys_indicator=True)
    assert "input was refused" in "".join(p.text for p in build_pieces(inputs, 120))


def test_a_refusal_stays_until_a_later_input_goes_unrefused(
        screen_text, wait_until, server_frame_validator, monkeypatch) -> None:
    """G11: the refusal line stays at least the toast time, then goes with the next input the core does not
    refuse; an input the core refuses again puts it straight back."""

    async def _check() -> None:
        monkeypatch.setenv("TSB_TUI_TOASTS_SECONDS", "0")
        frame = {"t": "input_refused", "v": 1, "reason": "not_typist"}
        with StubServer(_stub(frame, validate=server_frame_validator)) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(160, 24)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: "your input was refused" in screen_text(app), "the refusal")
                await pilot.press("x")  # the stub does not refuse it
                await wait_until(pilot, lambda: "your input was refused" not in screen_text(app),
                                 "the refusal to clear")
                assert screen.pump.state.refusal_active is False

    asyncio.run(_check())
