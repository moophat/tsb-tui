"""The stale-screen choice (design F7, D23; stale-screen steps 3-8, 11, 22-23, rules 18-19; immortality §5.3).

Why these matter (Rule 9): when output was lost the screen may be wrong, and TSB refuses every client's input
until the keyboard holder chooses a remedy. If the dialog softened the contract's texts, answered with a stale
`prompt_id`, took a stray keystroke as the choice, showed itself to someone who cannot answer, or ended the
old session when the holder asked for a fresh one, the operator would lose output, a session, or both. Servers:
the contract's in-process fixture server with its `stale-pending` scenario (DT7), and the TUI's stub for the
re-sent prompt after a token move, which the fixture never sends.
"""

from __future__ import annotations

import asyncio

from input_server import (
    InputRecordingApp,
    attached,
    flushed,
    make_session,
    other_client,
)
from prompt_support import NOW, click_laid_out, iso, open_stub_terminal
from stub_server import SlowChunkedApp, StubServer
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens import prompts
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.screens.prompts import StalePanel
from tsb_tui.screens.terminal import TerminalScreen
from tsbc import _serve

STALE = "stale-pending"
CHOICES = ("wiggle", "ctrl_l", "continue", "fresh_session")


def _answers(serve_app) -> list[dict]:
    return [f for f in list(serve_app.frames) if f.get("t") == "stale_answer"]


async def _holder_with_dialog(pilot, app, wait_until, serve_app, url):
    session_id = make_session(url, target=STALE)
    app.client.login(_serve.USER, _serve.PASSWORD)
    app.push_screen(TerminalScreen(session_id))
    screen = app.screen
    await wait_until(pilot, lambda: screen.stale_active(), "the stale dialog")
    return screen, session_id


def _banner(app) -> str:
    """The notice line as the screen shows it (a banner over the canvas, stale-screen rule 18), rows joined."""
    rows = [strip.text for strip in app.screen._compositor.render_strips()]
    return " ".join(" ".join(rows[:3]).split())


def test_the_holder_gets_the_dialog_with_the_texts_as_sent(serve, wait_until, monkeypatch) -> None:
    # fails today: the row and the banner are read in the same loop turn the dialog opened in, so on a loaded
    # machine the status row has not repainted for the stale prompt yet and `row` says nothing about it.
    """Rule 18: the prompt's text and each choice's text are shown exactly as the core sent them, so no
    surface softens the warning; the header says what happened; the note says the choice is TSB's own."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        monkeypatch.setenv("TSB_TUI_THEME_SURFACE_3", "#123456")
        app = TsbTuiApp(url)
        async with app.run_test(size=(260, 40)) as pilot:
            screen, _ = await _holder_with_dialog(pilot, app, wait_until, serve_app, url)
            prompt = screen.pump.state.stale_prompt
            panel = screen.query_one(StalePanel)
            assert str(panel.query_one("#stale-title").render()) == prompt["text"]
            for index, choice in enumerate(prompt["choices"]):
                assert str(panel.query_one(f"#stale-choice-{index}").render()) == f"[{index + 1}] {choice['text']}"
            where = str(panel.query_one("#stale-where").render())
            assert "the screen could not be rebuilt; no output was lost" in where  # the fixture's cause: grid_lost
            assert "Click one, or press 1-4. Keys are ignored for 1 s after this opens." in str(
                panel.query_one("#stale-guard").render())
            assert "Your choice is recorded as TSB's own action." in "\n".join(
                str(w.render()) for w in panel.query(".stale-note"))
            row_ready = lambda: ("TSB: lost output - the screen may be stale until a choice is made." in _banner(app)
                                 and "nobody's input goes through until you choose" in screen.status.text
                                 and "1-4 choose" in screen.status.text)
            await wait_until(pilot, row_ready, "the status row and the banner to repaint for the stale prompt")
            row = screen.status.text
            assert "TSB: lost output - the screen may be stale until a choice is made." in _banner(app)
            assert "nobody's input goes through until you choose" in row
            assert "1-4 choose" in row
            await pilot.hover("#stale-choice-0")
            await pilot.pause()
            assert panel.query_one("#stale-choice-0").styles.background.hex.lower() == "#123456"  # fails today: $surface-lighten-1 is derived from surface and ignores the knob

    asyncio.run(_check())


def test_a_gap_says_where_output_was_lost(wait_until, server_frame_validator) -> None:
    """F7: for a declared gap the header names the seq the loss began at (`gap_seq`)."""

    async def _check() -> None:
        pending = {"t": "stale", "v": 1, "session_id": "stub-session-1", "state": "pending", "cause": "gap",
                   "gap_seq": 48211, "at": iso(NOW)}
        prompt = {"t": "stale_prompt", "v": 1, "prompt_id": "a" * 32, "code": "stale.choose",
                  "text": "TSB: lost output - the screen may be wrong. Choose:",
                  "choices": [{"choice": "continue",
                               "text": "Keep going without a redraw (no guarantee the screen is right)"}]}
        for frame in (pending, prompt):
            server_frame_validator(frame)
        with StubServer(SlowChunkedApp(delay_s=0.05, after_attach_frames=[pending, prompt])) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(260, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, screen.stale_active, "the stale dialog")
                where = str(screen.query_one(StalePanel).query_one("#stale-where").render())
                assert "output was lost after seq 48211 (a gap)" in where
                assert "Click one, or press 1. Keys" in str(screen.query_one("#stale-guard").render())

    asyncio.run(_check())


def test_keys_are_ignored_for_the_guard_then_a_digit_answers_with_the_outstanding_id(
        serve, wait_until, monkeypatch) -> None:
    """D23: a keystroke aimed at the session must not choose by accident, so the first second ignores keys; after
    it `2` answers `ctrl_l` and carries the `prompt_id` the core sent (stale-screen step 11)."""

    async def _check() -> None:
        clock = [1000.0]
        monkeypatch.setattr(prompts, "monotonic", lambda: clock[0])
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(260, 40)) as pilot:
            screen, _ = await _holder_with_dialog(pilot, app, wait_until, serve_app, url)
            prompt_id = screen.pump.state.stale_prompt["prompt_id"]
            await pilot.press("2")
            await flushed(pilot, screen, serve_app, wait_until)
            assert _answers(serve_app) == [], "a key inside the guard must not choose"
            clock[0] += 5
            await pilot.press("2")
            await wait_until(pilot, lambda: _answers(serve_app), "the answer")
            sent = _answers(serve_app)[0]
            assert sent["choice"] == "ctrl_l"
            assert sent["prompt_id"] == prompt_id
            await wait_until(pilot, lambda: not screen.stale_active(), "the dialog to close on `chosen`")
            assert "stale until a choice" not in screen.status.text

    asyncio.run(_check())


def test_each_choice_sends_its_own_answer_by_click(serve, wait_until) -> None:
    """The four buttons are the four contract choices, in the order the core sent them: a click on one
    sends exactly that choice, once."""

    async def _check(index: int) -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(260, 40)) as pilot:
            screen, _ = await _holder_with_dialog(pilot, app, wait_until, serve_app, url)
            prompt_id = screen.pump.state.stale_prompt["prompt_id"]
            await click_laid_out(pilot, wait_until, f"#stale-choice-{index}")
            await wait_until(pilot, lambda: _answers(serve_app), "the answer")
            answers = _answers(serve_app)
            assert [(a["choice"], a["prompt_id"]) for a in answers] == [(CHOICES[index], prompt_id)]

    for index in range(4):
        asyncio.run(_check(index))


def test_a_non_holder_sees_the_stale_line_and_no_dialog(serve, wait_until) -> None:
    # fails today: the row is read in the same loop turn the stale frame arrived in, before anything was waited for,
    # so on a loaded machine it has not repainted for the stale prompt and says nothing about who is choosing.
    """Stale-screen step 4: only the holder's client is asked. Another client sees the notice line in its
    row, who is choosing, and that input is refused: the dialog would be a button that can only be refused."""

    async def _check() -> None:
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(260, 40)) as pilot:
            session_id = make_session(url, target=STALE)
            with other_client(url, _serve.PEER, session_id):
                app.client.login(_serve.USER, _serve.PASSWORD)
                screen = await attached(pilot, app, session_id, wait_until)
                await wait_until(pilot, lambda: screen.pump.state.stale_state == "pending", "the stale frame")
                assert not screen.stale_active()
                await wait_until(pilot, lambda: "TSB: lost output - the screen may be stale until a choice is made."
                                 in _banner(app) and "fixture-peer is choosing · input refused" in screen.status.text,
                                 "the banner and the status row to repaint for the stale prompt")
                row = screen.status.text
                assert "fixture-peer is choosing · input refused" in row
                assert "1-4 choose" not in row

    asyncio.run(_check())


def test_a_re_sent_prompt_after_a_token_move_replaces_the_dialog(wait_until, server_frame_validator) -> None:
    """Stale-screen step 8: a token move voids the outstanding prompt and the client that receives the token
    gets a new `prompt_id`. The dialog must show the new one and answer with it: an answer carrying the old
    id is refused `not_allowed` or `not_pending`."""

    async def _check() -> None:
        pending = {"t": "stale", "v": 1, "session_id": "stub-session-1", "state": "pending",
                   "cause": "grid_lost", "at": iso(NOW)}

        def prompt(prompt_id: str) -> dict:
            return {"t": "stale_prompt", "v": 1, "prompt_id": prompt_id, "code": "stale.choose",
                    "text": "TSB: lost output - the screen may be wrong. Choose:",
                    "choices": [{"choice": "wiggle", "text": "Redraw the screen (no guarantee)"}]}

        mine = {"client_id": "stub-client-1", "user": "fixture-user"}
        token = {"t": "token", "v": 1, "kind": "control", "holder": mine, "reason": "release"}
        for frame in (pending, prompt("a" * 32), token, prompt("b" * 32)):
            server_frame_validator(frame)
        stub = SlowChunkedApp(delay_s=0.05, after_attach_frames=[pending, prompt("a" * 32)],
                              respond_to={"resize": [token, prompt("b" * 32)]})
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(260, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: screen.stale_active(), "the first dialog")
                panel = screen.query_one(StalePanel)
                assert panel.prompt_id == "a" * 32
                await screen.pump.resize(100, 30, timeout=0)
                await wait_until(pilot, lambda: panel.prompt_id == "b" * 32, "the dialog to show the new prompt")
                await click_laid_out(pilot, wait_until, "#stale-choice-0")
                await wait_until(
                    pilot, lambda: [f for f in stub.text_frames if f.get("t") == "stale_answer"], "the answer")
                answer = next(f for f in stub.text_frames if f.get("t") == "stale_answer")
                assert answer["prompt_id"] == "b" * 32

    asyncio.run(_check())


def test_the_dialog_goes_when_the_keyboard_leaves_this_client(wait_until, server_frame_validator) -> None:
    """A token move to someone else voids the prompt (step 8): the old holder's dialog must close, or its
    buttons would send an answer the core refuses `not_allowed`."""

    async def _check() -> None:
        pending = {"t": "stale", "v": 1, "session_id": "stub-session-1", "state": "pending",
                   "cause": "grid_lost", "at": iso(NOW)}
        prompt = {"t": "stale_prompt", "v": 1, "prompt_id": "a" * 32, "code": "stale.choose",
                  "text": "TSB: lost output - the screen may be wrong. Choose:",
                  "choices": [{"choice": "wiggle", "text": "Redraw the screen (no guarantee)"}]}
        other = {"client_id": "stub-other", "user": "fixture-other"}
        token = {"t": "token", "v": 1, "kind": "control", "holder": other, "reason": "handoff"}
        for frame in (pending, prompt, token):
            server_frame_validator(frame)
        stub = SlowChunkedApp(delay_s=0.05, after_attach_frames=[pending, prompt],
                              respond_to={"resize": [token]})
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(260, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, screen.stale_active, "the dialog")
                await screen.pump.resize(100, 30, timeout=0)
                await wait_until(pilot, lambda: not screen.stale_active(), "the dialog to close")
                assert "fixture-other is choosing · input refused" in screen.status.text

    asyncio.run(_check())


def test_fresh_session_opens_new_session_on_the_same_target_and_never_ends_the_old_one(
        serve, wait_until) -> None:
    """Stale-screen rule 19 and step 23: the surface calls `POST /sessions` itself after `fresh_session`, and the
    old session keeps running (rule 14): ending it would destroy output the operator may still need."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(260, 40)) as pilot:
            _, session_id = await _holder_with_dialog(pilot, app, wait_until, serve_app, url)
            await click_laid_out(pilot, wait_until, "#stale-choice-3")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "New session")
            assert app.screen.query_one("#target-id").value == STALE
            assert [a["choice"] for a in _answers(serve_app)] == ["fresh_session"]
            assert app.client.session(session_id)["state"] not in ("killed", "terminated")

    asyncio.run(_check())


def test_a_refused_answer_says_why_in_the_dialog_and_may_be_tried_again(wait_until, server_frame_validator) -> None:
    """Stale-screen "Refusals": `request_refused` for `stale_answer` (`not_allowed`, `not_pending`) comes back
    to the dialog that asked, so a choice never vanishes without a signal; the buttons work again, because
    the pending state did not end."""

    async def _check() -> None:
        pending = {"t": "stale", "v": 1, "session_id": "stub-session-1", "state": "pending",
                   "cause": "grid_lost", "at": iso(NOW)}
        prompt = {"t": "stale_prompt", "v": 1, "prompt_id": "a" * 32, "code": "stale.choose",
                  "text": "TSB: lost output - the screen may be wrong. Choose:",
                  "choices": [{"choice": "wiggle", "text": "Redraw the screen (no guarantee)"}]}
        refused = {"t": "request_refused", "v": 1, "request": "stale_answer", "reason": "not_allowed"}
        for frame in (pending, prompt, refused):
            server_frame_validator(frame)
        stub = SlowChunkedApp(delay_s=0.05, after_attach_frames=[pending, prompt],
                              respond_to={"stale_answer": [refused]})
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, screen.stale_active, "the dialog")
                await click_laid_out(pilot, wait_until, "#stale-choice-0")
                note = screen.query_one("#stale-refused")
                await wait_until(pilot, lambda: "only the keyboard holder can choose" in str(note.render()),
                                 "the refusal in the dialog")
                await click_laid_out(pilot, wait_until, "#stale-choice-0")
                await wait_until(
                    pilot, lambda: len([f for f in stub.text_frames if f.get("t") == "stale_answer"]) == 2,
                    "a second answer")

    asyncio.run(_check())


def test_a_prompt_sent_to_a_client_that_does_not_hold_the_keyboard_is_not_shown(
        wait_until, server_frame_validator) -> None:
    """Step 4 sends the prompt to the holder's client only. If a core ever sent one elsewhere, a button here
    could only be answered `not_allowed`: the dialog is for the holder and nobody else."""

    async def _check() -> None:
        pending = {"t": "stale", "v": 1, "session_id": "stub-session-1", "state": "pending",
                   "cause": "grid_lost", "at": iso(NOW)}
        prompt = {"t": "stale_prompt", "v": 1, "prompt_id": "a" * 32, "code": "stale.choose",
                  "text": "TSB: lost output - the screen may be wrong. Choose:",
                  "choices": [{"choice": "wiggle", "text": "Redraw the screen (no guarantee)"}]}
        for frame in (pending, prompt):
            server_frame_validator(frame)
        stub = SlowChunkedApp(delay_s=0.05, after_attach_frames=[pending, prompt], holder_is_client=False)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen = await open_stub_terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: screen.pump.state.stale_prompt is not None, "the prompt frame")
                await pilot.pause(0.2)
                assert not screen.stale_active()

    asyncio.run(_check())
