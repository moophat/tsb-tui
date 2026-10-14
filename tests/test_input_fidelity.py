"""Input fidelity: what a person types is what the session gets (design §3.4 test 1, the automatable half; plan R7).

Why it matters (Rule 9): TSB drives routers, switches and firewalls through a terminal, and the whole point of a
terminal is that every key reaches it: a router's `?`, an editor's Esc, a shell's Ctrl-C, readline's Alt-b. The TUI
takes exactly one key (Ctrl-\\, D17), re-encodes every other for the *session's* modes (D34), and so can fail in
three ways these tests close: an accelerator that swallows a key it should have passed on, a mode (the kitty keyboard
protocol) that changes the bytes when it should and not when it should not, and a key the outer terminal sent that
arrives at the session as a different key. The last is measured, not hoped for: a corpus of what a terminal sends is
parsed by Textual's own parser, named the way the screen names it, encoded the way the screen encodes it, and
compared with what was typed. The differences are recorded here and in the README, so nobody believes there are none.
The phone path (an SSH app, then zellij, then the TUI; taps and the wheel) cannot be automated and is the
orchestrator's manual check. Servers: the contract's in-process fixture server, recording what each client sends.
"""

from __future__ import annotations

import asyncio
import string
from pathlib import Path

import pytest
from drive_support import Rig
from input_server import InputRecordingApp, attached, make_session
from stub_server import create_owner_session
from textual import _parser
from textual._xterm_parser import XTermParser
from tsb_tui.app import TsbTuiApp
from tsb_tui.model import actions as A
from tsb_tui.screens.terminal import _key_name
from tsb_tui.term import VTBackend, load
from tsb_tui.term.keys import encode_key
from tsbc import _serve

LEADER = "ctrl+backslash"


@pytest.fixture(scope="module")
def lib(term_so: Path):
    return load(term_so)


async def _open(pilot, app, wait_until):
    session_id = create_owner_session(app, _serve.TARGET, 80, 24)
    screen = await attached(pilot, app, session_id, wait_until)
    return screen, screen.pump.state.client_id


def test_every_key_of_the_action_table_reaches_the_session_when_it_does_not_follow_the_leader(
        serve, monkeypatch, wait_until) -> None:
    """D16, D17, design §3.4 test 1 ("every accelerator checked to reach the session when it does not follow the
    leader"). The leader's letters, `?` and the `:q` of Quit are only the leader's after the leader; typed alone they
    are the session's, and a router's CLI uses every one of them. The keys come from the action table, so an action
    added later is held to this without being named here. The pinned environment holds the Menu's timer far away, so
    a letter after the leader never races it."""
    monkeypatch.setenv("TSB_TUI_MENU_OPEN_ON_LEADER_AFTER_MS", "10000")
    chars = sorted({c for action in A.ACTIONS if action.key for c in action.key})
    assert {"w", "r", "R", "d", "x", "f", "F", "t", "c", "s", "n", "p", "e", "v", "?", ":", "q", "a", "h"} <= set(chars)

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, client_id = await _open(pilot, app, wait_until)
            await pilot.press(*chars)
            await wait_until(pilot, lambda: len(serve_app.received(client_id)) >= len(chars), "every key to arrive")
            assert b"".join(serve_app.received(client_id)) == "".join(chars).encode()
            assert app.screen is screen and not screen.dialogs, "a key opened something it must not"

    asyncio.run(check())


def test_the_sidebars_keys_reach_the_session_while_the_sidebar_does_not_hold_the_keyboard(serve, wait_until) -> None:
    """D14, D16. The sidebar's k9s keys (`j k t w x m F /`, Enter, Esc, the arrows) are its own only while it has the
    keys; opened beside the session without them (a click on the session gave them back, or it was opened without
    focus) every one of them is the session's, so opening a list never costs the operator the keys of their editor."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, client_id = await _open(pilot, app, wait_until)
            screen.open_sidebar(focus=False)
            await wait_until(pilot, lambda: screen.sidebar_open, "the sidebar to open")
            assert not screen._sidebar_has_keys()
            keys = ["j", "k", "t", "w", "x", "m", "F", "slash", "enter", "escape", "down", "up"]
            want = b"jktwxmF/\r\x1b\x1b[B\x1b[A"
            await pilot.press(*keys)
            await wait_until(pilot, lambda: b"".join(serve_app.received(client_id)) == want,
                             f"the keys to reach the session, got {serve_app.received(client_id)!r}")
            assert screen.sidebar_open and not screen._sidebar_has_keys(), "a key acted on the sidebar"

    asyncio.run(check())


def test_the_leader_once_sends_nothing_and_twice_sends_one_0x1c_and_leaves_the_next_key_alone(
        serve, monkeypatch, wait_until) -> None:
    """D15, design §3.4 test 1 ("Ctrl-\\ once and twice"). One Ctrl-\\ starts the leader and sends nothing; a second
    sends exactly one 0x1c, the key itself, and ends the leader, so the letter after the pair is an ordinary letter and
    not an action: typing `w` after `^\\ ^\\` must send `w`, not open Who's here."""
    monkeypatch.setenv("TSB_TUI_MENU_OPEN_ON_LEADER_AFTER_MS", "10000")

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, client_id = await _open(pilot, app, wait_until)
            await pilot.press(LEADER)
            await wait_until(pilot, lambda: screen.leader_pending, "the leader to start")
            assert serve_app.received(client_id) == [], "the leader alone sent something"
            await pilot.press(LEADER, "w")
            await wait_until(pilot, lambda: len(serve_app.received(client_id)) >= 2, "the pair and the letter")
            assert serve_app.received(client_id) == [b"\x1c", b"w"]
            assert not screen.leader_pending and not screen.dialogs, "the letter after the pair acted as an action"

    asyncio.run(check())


# What each key sends with the session's keyboard flags off (legacy) and with the kitty protocol's "disambiguate" flag
# on: the forms a program that asked for the protocol expects (Esc is CSI 27 u, Ctrl-A is CSI 97;5 u, and so on); the
# keys that mean text or a legacy sequence either way do not change.
KITTY_CORPUS = [
    ("a", b"a", b"a"),
    ("A", b"A", b"A"),
    ("space", b" ", b" "),
    ("enter", b"\r", b"\r"),
    ("tab", b"\t", b"\t"),
    ("backspace", b"\x7f", b"\x7f"),
    ("up", b"\x1b[A", b"\x1b[A"),
    ("f5", b"\x1b[15~", b"\x1b[15~"),
    ("escape", b"\x1b", b"\x1b[27u"),
    ("shift+tab", b"\x1b[Z", b"\x1b[9;2u"),
    ("ctrl+a", b"\x01", b"\x1b[97;5u"),
    ("ctrl+c", b"\x03", b"\x1b[99;5u"),
    ("alt+a", b"\x1ba", b"\x1b[97;3u"),
    ("ctrl+right_square_bracket", b"\x1d", b"\x1b[93;5u"),
]


def test_the_kitty_keyboard_protocol_changes_the_bytes_when_the_session_turns_it_on_and_back_when_it_turns_it_off(
        serve, wait_until) -> None:
    """D34, R7, design §3.4 test 1 ("Kitty protocol on and off"). A session that enables the protocol expects CSI-u
    for the keys it disambiguates, and one that does not expects the legacy bytes: the same keystrokes, through the
    whole screen and the one input path, must produce each in turn as the session's own output turns the flag on and
    off. Ctrl-\\ pressed twice follows the same rule: 0x1c in legacy, CSI 92;5 u under the flag."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, client_id = await _open(pilot, app, wait_until)
            keys = [key for key, _, _ in KITTY_CORPUS]
            expected: list[bytes] = []

            async def type_corpus(which: int) -> None:
                before = len(serve_app.received(client_id))
                await pilot.press(*keys, LEADER, LEADER)
                want = [row[which] for row in KITTY_CORPUS] + [b"\x1c" if which == 1 else b"\x1b[92;5u"]
                await wait_until(pilot, lambda: len(serve_app.received(client_id)) >= before + len(want),
                                 "the corpus to arrive")
                expected.extend(want)

            await type_corpus(1)  # legacy
            screen.canvas.feed(b"\x1b[>1u")  # the session turns the kitty protocol on
            assert screen.backend.kitty_flags() == 1
            await type_corpus(2)
            screen.canvas.feed(b"\x1b[<1u")  # and off again
            assert screen.backend.kitty_flags() == 0
            await type_corpus(1)
            got = serve_app.received(client_id)
            assert got == expected, [(i, want, have) for i, (want, have) in enumerate(zip(expected, got, strict=False))
                                     if want != have]

    asyncio.run(check())


# -- the corpus: what a terminal sends, through Textual's parser and the screen's own naming and encoding --


class _Clock:
    """Textual's parser waits a moment after a lone Esc to see whether more of a sequence follows; the clock it asks is
    replaced so a test advances it instead of sleeping."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def through_textual(lib, monkeypatch):
    """`(bytes_from_the_outer_terminal, session_flags) -> (key names Textual made of them, bytes sent to the session)`."""
    clock = _Clock()
    monkeypatch.setattr(_parser, "get_time", clock)

    def run(data: bytes, flags: int = 0) -> tuple[list[str], bytes]:
        backend = VTBackend(lib, 80, 24, scrollback_bytes=1 << 20)
        try:
            if flags:
                backend.feed(f"\x1b[>{flags}u".encode())
            parser = XTermParser()
            events = list(parser.feed(data.decode("utf-8", "surrogateescape")))
            clock.now += 10.0
            events += list(parser.tick())
            names = [_key_name(event) for event in events]
            return names, b"".join(encode_key(backend, name) for name in names)
        finally:
            backend.close()

    return run


# What a typical xterm sends in its normal mode, key by key. Each must reach the session as the very same bytes, except
# the ones in RECORDED_DIVERGENCES.
LEGACY_CORPUS = [
    b"a", b"A", b"?", b"|", b"/", b":", b"~", b" ", b"\xc3\xa9", b"\r", b"\t", b"\x7f", b"\x1b", b"\x1b[Z",
    b"\x1b[A", b"\x1b[B", b"\x1b[C", b"\x1b[D", b"\x1b[H", b"\x1b[F", b"\x1b[2~", b"\x1b[3~", b"\x1b[5~", b"\x1b[6~",
    b"\x1bOP", b"\x1bOQ", b"\x1bOR", b"\x1bOS", b"\x1b[15~", b"\x1b[17~", b"\x1b[20~", b"\x1b[24~",
    b"\x1b[1;5D", b"\x1b[1;5C", b"\x1b[1;5A", b"\x1b[1;3D", b"\x1b[1;3A", b"\x1b[1;2A", b"\x1b[1;2P", b"\x1b[20;5~",
    *(bytes([n]) for n in range(1, 27) if n not in (8, 9, 13)),  # Ctrl-A to Ctrl-Z but Ctrl-H, Tab and Enter
    b"\x00", b"\x1c", b"\x1d", b"\x1e", b"\x1f", b"\x1ba", b"\x1bx", b"\x1bd",
]

# What Textual's parser makes of a key that the screen then sends as a different byte string. Each is Textual's
# reading of the outer terminal's bytes, not the TUI's: the event the screen gets cannot say more.
RECORDED_DIVERGENCES = {
    b"\x08": (["backspace"], b"\x7f", "Textual folds Ctrl-H into Backspace, so the session gets 0x7f"),
    b"\x1b\r": (["enter"], b"\r", "Textual drops the Alt of Alt-Enter"),
    b"\x1bb": (["ctrl+left"], b"\x1b[1;5D", "Textual reads Esc b (readline's backward-word, IOS's back one word) as Ctrl-Left"),
    b"\x1bf": (["ctrl+right"], b"\x1b[1;5C", "Textual reads Esc f (readline's forward-word, IOS's forward one word) as Ctrl-Right"),
    b"\x1b\x7f": (["ctrl+w"], b"\x17", "Textual reads Alt-Backspace as Ctrl-W (readline's word rubout, close but not the same)"),
    b"\x1b[1~": (["home"], b"\x1b[H", "the VT220 Home is sent as the xterm Home"),
    b"\x1b[4~": (["end"], b"\x1b[F", "the VT220 End is sent as the xterm End"),
    b"\x1bOH": (["home"], b"\x1b[H", "an SS3 Home is sent as the CSI Home"),
}


@pytest.mark.parametrize("data", LEGACY_CORPUS, ids=repr)
def test_a_key_the_outer_terminal_sends_reaches_the_session_as_the_same_bytes(data: bytes, through_textual) -> None:
    """D17, D34, design §3.4 test 1 ("the study's corpus"). For every key in the corpus, the bytes a terminal sends are
    parsed by Textual, named as the screen names a key, encoded for a session in its default modes, and the result is
    byte for byte what was typed: a router's CLI, readline and vi see the keys the operator pressed."""
    names, sent = through_textual(data)
    assert sent == data, f"{data!r} was read as {names} and sent as {sent!r}"


@pytest.mark.parametrize("data", sorted(RECORDED_DIVERGENCES), ids=repr)
def test_the_corpus_records_every_key_that_arrives_as_a_different_one(data: bytes, through_textual) -> None:
    """R7, Rule 12. Keys Textual cannot tell apart, or tells apart differently from the terminal, arrive at the session
    as another key. They are listed with their reasons here and in the README, and asserted as they are today: if
    Textual or the encoder starts handling one properly, this fails and the record is updated, so the README never
    claims a gap that closed or hides one that opened."""
    names, sent = through_textual(data)
    want_names, want_sent, _why = RECORDED_DIVERGENCES[data]
    assert (names, sent) == (want_names, want_sent)
    assert sent != data, "a recorded divergence that no longer differs: drop it from the record"


def test_what_the_kitty_protocol_asks_for_and_textual_cannot_express_is_recorded(through_textual) -> None:
    """R7, design §3.4 test 1 ("Kitty protocol on and off"). Textual's key event carries the key's name and its text,
    not the unshifted codepoint or the physical key. A session that asks for every key as an escape code (kitty
    flag 8, which editors rarely do) therefore gets a shifted letter reported as the capital's own codepoint
    (`CSI 65 u`) where the protocol says the unshifted key and the Shift modifier (`CSI 97;2 u`). In the usual
    "disambiguate" flag the text keys are plain text and the difference never shows. A terminal that speaks the
    protocol itself is read right: its Esc, Ctrl-A, Alt-A and Shift-Enter come through."""
    _, shifted = through_textual(b"A", flags=8)
    assert shifted == b"\x1b[65u", "a recorded gap: the protocol says CSI 97;2 u"
    _, lower = through_textual(b"a", flags=8)
    assert lower == b"\x1b[97u"
    for data in (b"\x1b[27u", b"\x1b[97;5u", b"\x1b[97;3u", b"\x1b[13;2u", b"\x1b[9;2u"):
        _, sent = through_textual(data, flags=1)
        assert sent == data, f"{data!r} was sent as {sent!r}"
    # Without the protocol on, the same terminal's Shift-Enter is a plain Enter to the session (nothing to tell it by).
    _, plain = through_textual(b"\x1b[13;2u", flags=0)
    assert plain == b"\x1b[27;2;13~", "the engine's own legacy form of Shift-Enter"


# -- the key names the drive guide promises --

GUIDE_KEY_NAMES = (["enter", "tab", "escape", "backspace", "delete", "insert", "space", "up", "down", "left", "right",
                    "home", "end", "pageup", "pagedown"] + [f"f{n}" for n in range(1, 26)]
                   + [f"ctrl+{c}" for c in string.ascii_lowercase] + [f"alt+{c}" for c in string.ascii_lowercase]
                   + [f"shift+{k}" for k in ("tab", "up", "down", "left", "right", "home", "end", "f1", "enter", "space")])


@pytest.mark.parametrize("flags", [0, 1, 8])
def test_every_key_name_the_drive_guide_promises_encodes_to_something(lib, flags: int) -> None:
    """Drive guide §"Key names". A name the guide promises that encodes to nothing sends nothing, and drive answers that
    the session is "not ready for input", which is false and sends the agent looking for a cause that is not there. This
    caught `space`: a named key that also types a character needs its text, and without it the encoder returned nothing
    in every mode, so an agent answering a `--More--` pager with `space` could not."""
    backend = VTBackend(lib, 80, 24, scrollback_bytes=1 << 20)
    try:
        if flags:
            backend.feed(f"\x1b[>{flags}u".encode())
        empty = [name for name in GUIDE_KEY_NAMES if encode_key(backend, name) == b""]
        assert not empty, f"these promised key names send nothing with the kitty flags at {flags}: {empty}"
    finally:
        backend.close()


def test_drive_presses_space_for_a_pager(serve, tmp_path) -> None:
    """Drive guide, pagers: "wait for `--More--` and press `space`". The key must arrive: one byte, a space, at the
    server, and the answer must say one byte was queued."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login(_serve.USER)
            await rig.attach(sid)
            client_id = rig.app.workspace().slot_for(sid).pump.state.client_id
            answer = await rig.cmd("keys", keys=["space"])
            assert answer["ok"] and answer["queued"] == 1, answer
            for _ in range(500):
                if serve_app.received(client_id):
                    break
                await asyncio.sleep(0.01)
            assert serve_app.received(client_id) == [b" "]

    asyncio.run(check())
