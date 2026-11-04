"""What the person types, and when it leaves: keys and paste through the one input path (DT4 a).

Why these matter (Rule 9): the keyboard has one holder. A client that is only watching and sends its
keys anyway is refused by the core, and the person sees nothing happen; a client that holds back an
owner's keys cannot take the keyboard back from a contractor or an agent, which is the owner's brake
(terminal-size §5, §6; typing-ownership step 8). So the screen suppresses a non-owner viewer's typing
locally (Frontend §9 "View-only state"; typing-ownership CR3 allows it as a guardrail, never the
control) and never an owner's. Every other key reaches the session (design D17), encoded for the
session's own modes by the engine's encoder (D34), because a router's `?`, an editor's Esc and a
shell's Ctrl-C are the whole point of a terminal.

Servers: the contract's in-process fixture server (DT7), recording the bytes each client sends
(`input_server.py`); the TUI's own stub for the Textual-reserved keys.
"""

from __future__ import annotations

import asyncio

import pytest
from input_server import (
    InputRecordingApp,
    attached,
    flushed,
    make_session,
    other_client,
)
from textual import events
from tsbc import _serve

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.terminal import (
    KIND_INPUT_DROPPED,
    KIND_INPUT_SUPPRESSED,
    TerminalScreen,
)


def _log_kinds(screen: TerminalScreen, kind: str) -> list:
    return screen.pump.state.log.of_kind(kind)


def test_a_viewer_with_the_guard_on_sends_no_typing_and_no_paste_and_counts_them(
        serve, wait_until) -> None:
    """Frontend §9 "View-only state", typing-ownership CR3. A client whose principal may only watch
    (`session.join.ro`) must not send its keys: the core would refuse every one, and the person
    typing would be told nothing. They are held back here, counted and logged so the status row can
    say "your keys were not sent". Proved from the server's side: it received no input frame."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        session_id = make_session(url)
        with other_client(url, _serve.USER, session_id):
            app = TsbTuiApp(url)
            async with app.run_test(size=(80, 24)) as pilot:
                app.client.login(_serve.VIEWER, _serve.PASSWORD)
                screen = await attached(pilot, app, session_id, wait_until)
                assert not screen.pump.state.holds_keyboard
                assert not screen.pump.state.may_type

                await pilot.press("x", "enter")
                screen.post_message(events.Paste("hello"))
                await wait_until(pilot, lambda: screen.suppressed_inputs == 3, "the three inputs to be suppressed")
                await flushed(pilot, screen, serve_app, wait_until)

                assert serve_app.received(screen.pump.state.client_id) == []
                assert [e.detail["source"] for e in _log_kinds(screen, KIND_INPUT_SUPPRESSED)] == [
                    "key", "key", "paste"]
                assert screen.pump.state.last_input_refused is None, "the core was never asked"

    asyncio.run(_check())


def test_with_the_guard_off_a_viewers_input_is_sent_and_the_core_refuses_it(
        serve, monkeypatch, wait_until) -> None:
    """The guard is a knob (`input.guard`): off, the screen sends what is typed and the core is the
    only control (typing-ownership: a surface's suppression is a guardrail, never the control), so
    the refusal arrives as `input_refused` `not_typist` and the screen records it. This is what the
    harness needs to test the core's refusal, and it is why suppression cannot be hard-wired."""
    monkeypatch.setenv("TSB_TUI_INPUT_GUARD", "false")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        session_id = make_session(url)
        with other_client(url, _serve.USER, session_id):
            app = TsbTuiApp(url)
            async with app.run_test(size=(80, 24)) as pilot:
                app.client.login(_serve.VIEWER, _serve.PASSWORD)
                screen = await attached(pilot, app, session_id, wait_until)

                await pilot.press("x")
                screen.post_message(events.Paste("hi"))
                client_id = screen.pump.state.client_id
                await wait_until(pilot, lambda: serve_app.received(client_id) == [b"x", b"hi"],
                                 "both inputs to reach the server")
                await wait_until(pilot, lambda: screen.pump.state.last_input_refused == "not_typist",
                                 "the core's refusal")
                assert screen.suppressed_inputs == 0

    asyncio.run(_check())


def test_the_owner_typing_while_another_client_holds_the_keyboard_takes_it_and_ctrl_c_follows(
        serve, wait_until) -> None:
    """The owner's brake (5.53; terminal-size §5, §6; typing-ownership CR3). When a contractor or an
    agent holds the keyboard, the owner takes it back by typing, and a Ctrl-C right behind reaches
    the target in the same trip. A client that suppressed the owner's keys because it is not the
    holder would leave the owner unable to stop a runaway command. So nothing is suppressed, the
    first key moves the keyboard (`token` `takeover`) and the Ctrl-C goes out as the holder's."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        session_id = make_session(url)
        with other_client(url, _serve.PEER, session_id):
            app = TsbTuiApp(url)
            async with app.run_test(size=(80, 24)) as pilot:
                app.client.login(_serve.USER, _serve.PASSWORD)
                screen = await attached(pilot, app, session_id, wait_until)
                state = screen.pump.state
                assert not state.holds_keyboard and state.is_owner_client

                await pilot.press("x", "ctrl+c")
                await wait_until(pilot, lambda: serve_app.received(state.client_id) == [b"x", b"\x03"],
                                 "the owner's keys to reach the server")
                await wait_until(pilot, lambda: state.holds_keyboard, "the take-over token")
                assert state.token_history[-1]["reason"] == "takeover"
                assert screen.suppressed_inputs == 0

    asyncio.run(_check())


# What a terminal sends for each key in the session's default modes (xterm's own table, written out
# by hand: the test must fail if the encoder ever gives something else, not restate what it gives).
_DEFAULT_MODE_KEYS = [
    ("a", b"a"),
    ("A", b"A"),
    ("question_mark", b"?"),  # an IOS or Junos `?` must reach the device, never be taken
    ("space", b" "),
    ("enter", b"\r"),
    ("escape", b"\x1b"),
    ("tab", b"\t"),
    ("shift+tab", b"\x1b[Z"),
    ("up", b"\x1b[A"),
    ("left", b"\x1b[D"),
    ("home", b"\x1b[H"),
    ("pageup", b"\x1b[5~"),
    ("f1", b"\x1bOP"),
    ("f5", b"\x1b[15~"),
    ("f9", b"\x1b[20~"),  # no longer a key of ours: it detached the session before
    ("ctrl+a", b"\x01"),
    ("ctrl+c", b"\x03"),
    ("ctrl+p", b"\x10"),  # Textual's palette key
    ("ctrl+q", b"\x11"),  # Textual's quit key
    # Ctrl-\ is the one key left out: it is the leader (D15) and is sent only as `^\ ^\`, which
    # test_leader_and_menu proves.
    ("ctrl+circumflex_accent", b"\x1e"),  # IOS's abort for a hung ping or traceroute
    ("ctrl+right_square_bracket", b"\x1d"),  # telnet's escape character
    ("alt+a", b"\x1ba"),
    ("ctrl+left", b"\x1b[1;5D"),  # the engine's xterm form; see test_term_keys' recorded divergences
]


def test_every_key_reaches_the_session_with_the_bytes_a_terminal_sends(
        serve, wait_until) -> None:
    """D17, D34. Printable keys, Esc, Tab, arrows, F-keys, Ctrl and Alt combinations all reach the
    session; F9 is one of them and does not detach (it did, when the screen reserved it); Ctrl-\\ alone is
    the leader's. The holder
    sends them in order, as the bytes a real terminal sends in the session's default modes."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            app.client.login(_serve.USER, _serve.PASSWORD)
            session_id = app.client.create_session(
                _serve.TARGET, "new", "xterm-256color", 80, 24)["session"]["id"]
            screen = await attached(pilot, app, session_id, wait_until)
            assert screen.pump.state.holds_keyboard

            await pilot.press(*[key for key, _ in _DEFAULT_MODE_KEYS])
            client_id = screen.pump.state.client_id
            want = [data for _, data in _DEFAULT_MODE_KEYS]
            await wait_until(pilot, lambda: len(serve_app.received(client_id)) >= len(want),
                             "every key to reach the server")
            got = serve_app.received(client_id)
            assert got == want, [(key, w, g) for (key, w), g in zip(_DEFAULT_MODE_KEYS, got, strict=False)
                                 if w != g]
            assert isinstance(app.screen, TerminalScreen), "a key left the terminal screen"

    asyncio.run(_check())


@pytest.mark.parametrize(("session_mode", "key", "want"), [
    (b"\x1b[?1h", "up", b"\x1bOA"),  # DECCKM: the session asked for application arrows
    (b"\x1b[?1h", "shift+up", b"\x1b[1;2A"),  # a modified arrow keeps the CSI form
    (b"\x1b[>1u", "escape", b"\x1b[27u"),  # kitty keyboard protocol, flag 1: Esc is unambiguous
])
def test_a_key_is_encoded_for_the_modes_the_session_set(
        session_mode, key, want, serve, wait_until) -> None:
    """D34. The operator's outer terminal is not the session's terminal: an arrow means `ESC O A` to a
    program that turned on application cursor keys and `ESC [ A` to one that did not, and an Esc is
    only distinguishable from the start of a sequence once the kitty protocol is on. The mode comes
    from the session's own output, so the same keystroke leaves differently after it."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            app.client.login(_serve.USER, _serve.PASSWORD)
            session_id = app.client.create_session(
                _serve.TARGET, "new", "xterm-256color", 80, 24)["session"]["id"]
            screen = await attached(pilot, app, session_id, wait_until)

            screen.canvas.feed(session_mode)
            await pilot.press(key)
            client_id = screen.pump.state.client_id
            await wait_until(pilot, lambda: serve_app.received(client_id) == [want],
                             f"{key!r} to leave as {want!r}")

    asyncio.run(_check())


def test_paste_is_bracketed_only_when_the_session_asked_for_it(serve, wait_until) -> None:
    """A paste into a shell that did not ask for bracketing must arrive as typing (newlines as
    carriage returns); into one that did, it must be wrapped, so an editor can tell pasted text from
    typed text and a pasted newline does not run a command (mode 2004). The wrapping follows the
    session's mode at the moment of the paste."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            app.client.login(_serve.USER, _serve.PASSWORD)
            session_id = app.client.create_session(
                _serve.TARGET, "new", "xterm-256color", 80, 24)["session"]["id"]
            screen = await attached(pilot, app, session_id, wait_until)
            client_id = screen.pump.state.client_id

            screen.post_message(events.Paste("a\nb"))
            await wait_until(pilot, lambda: serve_app.received(client_id) == [b"a\rb"], "the plain paste")
            screen.canvas.feed(b"\x1b[?2004h")
            screen.post_message(events.Paste("c\nd"))
            await wait_until(
                pilot, lambda: serve_app.received(client_id) == [b"a\rb", b"\x1b[200~c\nd\x1b[201~"],
                "the bracketed paste")

    asyncio.run(_check())


def test_a_key_the_encoder_has_no_name_for_is_logged_and_the_next_key_still_goes(
        serve, wait_until) -> None:
    """Rule 12. A key the encoder cannot name must not crash the screen and must not vanish without a
    trace: it is dropped with a line in the event log naming it, and typing carries on."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            app.client.login(_serve.USER, _serve.PASSWORD)
            session_id = app.client.create_session(
                _serve.TARGET, "new", "xterm-256color", 80, 24)["session"]["id"]
            screen = await attached(pilot, app, session_id, wait_until)
            client_id = screen.pump.state.client_id

            screen.post_message(events.Key("f99", None))
            await pilot.press("z")
            await wait_until(pilot, lambda: serve_app.received(client_id) == [b"z"], "the next key")
            lines = _log_kinds(screen, KIND_INPUT_DROPPED)
            assert [(e.detail["reason"], e.detail["key"]) for e in lines] == [("unknown_key", "f99")]

    asyncio.run(_check())
