"""The quiet prompt (design F8, D23, D45; quiet-prompt steps 6-8 and rule 9; session model §11, Contract §10).

Why these matter (Rule 9): TSB never ends a session by itself. When input goes unanswered it asks the owner,
and a password typed at a no-echo prompt is the known false positive, so the question must never take the
keyboard: a box that captured keys would swallow the password it asks about. The answer carries the
`prompt_id` the core sent and nothing else (rule 9); Kill asks once more because ending a session cannot be
undone; and when an agent holds the keyboard the owner can take it back from the same box. Servers: the TUI's
own stub (DT7), because the fixture server never sends `quiet_prompt`.
"""

from __future__ import annotations

import asyncio

from prompt_support import click_laid_out, open_stub_terminal
from stub_server import SlowChunkedApp, StubServer
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.prompts import AnswerScreen, ConfirmKillScreen, QuietBox

PROMPT_ID = "q" * 32


def _prompt() -> dict:
    return {"t": "quiet_prompt", "v": 1, "prompt_id": PROMPT_ID, "code": "quiet_prompt.kill",
            "text": "Kill the session?"}


def _stub(validate, *, frames=True, **kwargs) -> SlowChunkedApp:
    frame = _prompt()
    validate(frame)
    return SlowChunkedApp(delay_s=0.05, after_attach_frames=[frame] if frames else [], **kwargs)


def _answers(stub) -> list[dict]:
    return [f for f in list(stub.text_frames) if f.get("t") == "quiet_prompt_answer"]


def test_the_box_appears_only_on_a_quiet_prompt(wait_until, server_frame_validator) -> None:
    """The TUI raises no question of its own (rule 9): without a `quiet_prompt` there is no box."""

    async def _check() -> None:
        with StubServer(_stub(server_frame_validator, frames=False)) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await pilot.pause(0.3)
                assert not screen.query_one(QuietBox).display
        with StubServer(_stub(server_frame_validator)) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                box = screen.query_one(QuietBox)
                await wait_until(pilot, lambda: box.display, "the box")
                assert str(box.query_one("#quiet-title").render()) == "stub-target asks you"
                assert str(box.query_one("#quiet-text").render()) == "Kill the session?  Something typed got no answer."
                assert not box.query_one("#quiet-holder").display  # this client holds the keyboard

    asyncio.run(_check())


def test_typing_still_reaches_the_session_while_the_box_is_shown(wait_until, server_frame_validator) -> None:
    """D23: the box never captures keys. A click on the box's own buttons must not leave a focused widget
    that would take Enter or Space from the session either."""

    async def _check() -> None:
        stub = _stub(server_frame_validator)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                box = screen.query_one(QuietBox)
                await wait_until(pilot, lambda: box.display, "the box")
                await pilot.press("p", "enter", "space")
                await wait_until(pilot, lambda: len(stub.client_frames) >= 3, "the keys to reach the session")
                assert stub.client_frames[:3] == [b"p", b"\r", b" "]
                await click_laid_out(pilot, wait_until, "#quiet-keep")
                assert app.focused is None, "a click on the box's own button must not take the focus"

    asyncio.run(_check())


def test_keep_it_answers_no_with_the_prompt_id_and_hides_the_box(wait_until, server_frame_validator) -> None:
    async def _check() -> None:
        stub = _stub(server_frame_validator)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                box = screen.query_one(QuietBox)
                await wait_until(pilot, lambda: box.display, "the box")
                await click_laid_out(pilot, wait_until, "#quiet-keep")
                await wait_until(pilot, lambda: _answers(stub), "the answer")
                assert _answers(stub) == [{"t": "quiet_prompt_answer", "v": 1, "prompt_id": PROMPT_ID,
                                           "kill": False}]
                await wait_until(pilot, lambda: not box.display, "the box to go")

    asyncio.run(_check())


def test_kill_it_asks_once_more_then_sends_kill_true_with_the_same_id(
        wait_until, server_frame_validator) -> None:
    """F8: `Kill it…` asks again, so one stray click cannot end a session; nothing is sent until the second yes."""

    async def _check() -> None:
        stub = _stub(server_frame_validator)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                box = screen.query_one(QuietBox)
                await wait_until(pilot, lambda: box.display, "the box")
                await click_laid_out(pilot, wait_until, "#quiet-kill")
                await wait_until(pilot, lambda: isinstance(app.screen, ConfirmKillScreen), "the second ask")
                assert str(app.screen.query_one("#kill-text").render()) == (
                    "Kill stub-target? This ends it for everyone.")
                await pilot.pause(0.2)
                assert _answers(stub) == [], "the first click must send nothing"
                await click_laid_out(pilot, wait_until, "#kill-confirm")
                await wait_until(pilot, lambda: _answers(stub), "the answer")
                assert _answers(stub)[0]["kill"] is True and _answers(stub)[0]["prompt_id"] == PROMPT_ID

    asyncio.run(_check())


def test_cancelling_the_second_ask_sends_nothing(wait_until, server_frame_validator) -> None:
    async def _check() -> None:
        stub = _stub(server_frame_validator)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                box = screen.query_one(QuietBox)
                await wait_until(pilot, lambda: box.display, "the box")
                await click_laid_out(pilot, wait_until, "#quiet-kill")
                await wait_until(pilot, lambda: isinstance(app.screen, ConfirmKillScreen), "the second ask")
                await pilot.press("escape")
                await wait_until(pilot, lambda: app.screen is screen, "the ask to close")
                await pilot.pause(0.2)
                assert _answers(stub) == [] and box.display

    asyncio.run(_check())


def test_take_back_shows_when_an_agent_holds_the_keyboard_and_hands_it_to_this_client(
        wait_until, server_frame_validator) -> None:
    """D45, F8: the owner can take the keyboard back from the box. The box names who holds it, and the
    answer is a `handoff` to this client's own id (typing-ownership: the owner takes it)."""

    async def _check() -> None:
        stub = _stub(server_frame_validator, holder_is_client=False)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                box = screen.query_one(QuietBox)
                await wait_until(pilot, lambda: box.display, "the box")
                assert str(box.query_one("#quiet-holder").render()) == "fixture-other holds the keyboard."
                await click_laid_out(pilot, wait_until, "#quiet-take-back")
                await wait_until(pilot, lambda: [f for f in stub.text_frames if f.get("t") == "handoff"],
                                 "the hand-off")
                handoff = next(f for f in stub.text_frames if f.get("t") == "handoff")
                assert handoff["to"] == screen.pump.state.client_id

    asyncio.run(_check())


def test_take_back_is_not_offered_to_the_keyboard_holder(wait_until, server_frame_validator) -> None:
    async def _check() -> None:
        with StubServer(_stub(server_frame_validator)) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                box = screen.query_one(QuietBox)
                await wait_until(pilot, lambda: box.display, "the box")
                assert not box.query_one("#quiet-take-back").display

    asyncio.run(_check())


def test_the_leader_and_a_opens_the_same_two_answers_for_the_keyboard(
        wait_until, server_frame_validator) -> None:
    """G3: `^\\ a` is the keyboard's way to answer, without the box ever capturing a key."""

    async def _check() -> None:
        stub = _stub(server_frame_validator)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(160, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                box = screen.query_one(QuietBox)
                await wait_until(pilot, lambda: box.display, "the box")
                assert "Answer: kill the session?" in screen.status.text
                await pilot.press("ctrl+backslash", "a")
                await wait_until(pilot, lambda: isinstance(app.screen, AnswerScreen), "the answer dialog")
                await pilot.press("k")
                await wait_until(pilot, lambda: _answers(stub), "the answer")
                assert _answers(stub)[0]["kill"] is False

    asyncio.run(_check())


def test_the_answer_action_is_not_offered_without_a_question(wait_until, server_frame_validator) -> None:
    async def _check() -> None:
        with StubServer(_stub(server_frame_validator, frames=False)) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(160, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                assert "Answer: kill the session?" not in screen.status.text
                await pilot.press("ctrl+backslash", "a")
                await pilot.pause(0.1)
                assert app.screen is screen

    asyncio.run(_check())


def test_the_leader_and_t_takes_the_keyboard_back_from_an_agent(wait_until, server_frame_validator) -> None:
    """D45: `^\\ t` is Take back for a session of yours that another login types in; nothing else is sent."""

    async def _check() -> None:
        stub = _stub(server_frame_validator, frames=False, holder_is_client=False)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(220, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                assert "^\\ t Take back" in screen.status.text
                await pilot.press("ctrl+backslash", "t")
                await wait_until(pilot, lambda: [f for f in stub.text_frames if f.get("t") == "handoff"],
                                 "the hand-off")
                assert [f["to"] for f in stub.text_frames if f.get("t") == "handoff"] == [
                    screen.pump.state.client_id]

    asyncio.run(_check())
