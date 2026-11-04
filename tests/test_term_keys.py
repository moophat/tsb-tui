"""The encoders: the legacy key table's bytes, DECCKM, the kitty flags, paste, mouse and focus (D34, R7).

Why these matter (phase 1 "Tests", plan D34/R7): a key encoded wrongly is invisible until the
operator's keystroke fails on real network gear — Ctrl-^ aborting a hung ping, Ctrl-] the telnet
escape character, an arrow that does not match what the target's mode asked for. The engine's
encoder reproduces, in legacy mode, the bytes of the hand-written table the screen used before it
(kept below as `_LEGACY_BYTES`, the expected values). It also records the four entries where it does
**not** reproduce that table, and why, so the divergence is decided rather than discovered on the
switch (Rule 12; R7).

The four divergences (see `test_recorded_divergences_from_the_old_table`):

- `ctrl+i` and `ctrl+m`: Textual folds Ctrl-I to `tab` and Ctrl-M to `enter` before `on_key` ever
  sees them (`textual.keys.KEY_ALIASES`), so these table entries are unreachable from a Textual
  event, and the folded names encode as Tab and Enter. The engine encodes the letter forms as
  kitty-style CSI-u.
- `ctrl+left` and `ctrl+right`: the old table deliberately sent the readline/emacs word-jump bytes
  `\\x1bb`/`\\x1bf`; the engine sends the xterm modifier-CSI `\\x1b[1;5D`/`\\x1b[1;5C`, and the screen
  now sends what the engine gives (D34). This is a real behaviour change for a session that binds
  Ctrl-arrow to something else; recorded here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tsb_tui.term import (
    UnknownKey,
    VTBackend,
    encode_focus,
    encode_key,
    encode_mouse,
    encode_paste,
    load,
)

MOD_CTRL = 1 << 1
MOUSE_PRESS = 0  # GhosttyMouseAction.GHOSTTY_MOUSE_ACTION_PRESS
MOUSE_BUTTON_LEFT = 1  # GhosttyMouseButton

# What the hand-written table the screen used before the engine's encoder sent, for each key a real
# Textual or drive key name reaches. The expected values of the parity test below.
_LEGACY_BYTES = {
    "enter": b"\r", "escape": b"\x1b", "tab": b"\t", "shift+tab": b"\x1b[Z", "backspace": b"\x7f",
    "delete": b"\x1b[3~", "insert": b"\x1b[2~", "home": b"\x1b[H", "end": b"\x1b[F",
    "pageup": b"\x1b[5~", "pagedown": b"\x1b[6~",
    "f1": b"\x1bOP", "f2": b"\x1bOQ", "f3": b"\x1bOR", "f4": b"\x1bOS", "f5": b"\x1b[15~",
    "f6": b"\x1b[17~", "f7": b"\x1b[18~", "f8": b"\x1b[19~", "f10": b"\x1b[21~",
    "f11": b"\x1b[23~", "f12": b"\x1b[24~",
    "ctrl+backslash": b"\x1c", "ctrl+underscore": b"\x1f", "ctrl+circumflex_accent": b"\x1e",
    "ctrl+right_square_bracket": b"\x1d", "ctrl+@": b"\x00",
    "ctrl+i": b"\t", "ctrl+m": b"\r", "ctrl+left": b"\x1bb", "ctrl+right": b"\x1bf",
    **{f"ctrl+{letter}": bytes([n]) for n, letter in enumerate("abcdefghijklmnopqrstuvwxyz", start=1)},
}

# The four table entries the engine does not reproduce, with the bytes each side gives and why.
_DIVERGENCES = {
    "ctrl+i": (b"\t", "Textual folds Ctrl-I to `tab`; the letter form is unreachable"),
    "ctrl+m": (b"\r", "Textual folds Ctrl-M to `enter`; the letter form is unreachable"),
    "ctrl+left": (b"\x1bb", "readline word-jump bytes vs the engine's CSI 1;5D"),
    "ctrl+right": (b"\x1bf", "readline word-jump bytes vs the engine's CSI 1;5C"),
}


@pytest.fixture(scope="module")
def lib(term_so: Path):
    return load(term_so)


@pytest.fixture
def backend(lib) -> VTBackend:
    b = VTBackend(lib, 80, 24, scrollback_bytes=4 * 1024 * 1024)
    yield b
    b.close()


# -- the old table's bytes, reproduced in legacy mode --

# Every entry of `_LEGACY_BYTES` that is reachable from a Textual or drive key name and is not one of
# the four recorded divergences. The arrows are absent from the table (the old code computed them from
# DECCKM); they are covered by the DECCKM test below.
_LEGACY_PARITY = [
    "enter", "escape", "tab", "shift+tab", "backspace", "delete", "insert", "home", "end",
    "pageup", "pagedown", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f10", "f11", "f12",
    "ctrl+a", "ctrl+b", "ctrl+c", "ctrl+d", "ctrl+e", "ctrl+f", "ctrl+g", "ctrl+h", "ctrl+j",
    "ctrl+k", "ctrl+l", "ctrl+n", "ctrl+o", "ctrl+p", "ctrl+q", "ctrl+r", "ctrl+s", "ctrl+t",
    "ctrl+u", "ctrl+v", "ctrl+w", "ctrl+x", "ctrl+y", "ctrl+z",
    "ctrl+backslash", "ctrl+underscore", "ctrl+circumflex_accent",
    "ctrl+right_square_bracket", "ctrl+@",
]


@pytest.mark.parametrize("key", _LEGACY_PARITY)
def test_every_reachable_key_encodes_the_same_bytes(backend: VTBackend, key: str) -> None:
    """Every reachable entry of the old hand-written table encodes to the same bytes in legacy mode.

    Why (D34): the screen sends what the engine's encoder gives. This proves the encoder is a
    drop-in replacement for the entries a real Textual or drive key reaches, so no key changes
    behaviour on the switch. The four entries it does not reproduce are recorded separately.
    """
    want = _LEGACY_BYTES[key]
    got = encode_key(backend, key)
    assert got == want, f"{key}: table {want!r} vs encoder {got!r}"


def test_recorded_divergences_from_the_old_table(backend: VTBackend) -> None:
    """The four table entries the engine does not reproduce, asserted as the engine's own bytes.

    Why (Rule 12, R7): the switch from the table to the engine is a real behaviour change for these
    four keys. Recording them here means nobody believes parity holds: the ctrl+i/ctrl+m entries are
    unreachable (Textual folds them to Tab and Enter), and the ctrl+arrow change is the engine's
    (D34). A test that hid them would make the report a lie.
    """
    assert encode_key(backend, "ctrl+i") == b"\x1b[105;5u", "the engine's Ctrl-I letter form"
    assert encode_key(backend, "ctrl+m") == b"\x1b[109;5u", "the engine's Ctrl-M letter form"
    assert encode_key(backend, "ctrl+left") == b"\x1b[1;5D", "the engine's Ctrl-Left CSI form"
    assert encode_key(backend, "ctrl+right") == b"\x1b[1;5C", "the engine's Ctrl-Right CSI form"
    # And the table values, so the divergence is a fact of this file, not prose.
    for key, (old, why) in _DIVERGENCES.items():
        assert _LEGACY_BYTES[key] == old, f"{key}: the recorded old bytes drifted"
        assert encode_key(backend, key) != old, f"{key} no longer diverges ({why}): drop it from the record"


def test_a_typed_plus_reaches_the_session(backend: VTBackend) -> None:
    """A `+` key name — bare or after modifiers — encodes the printable plus, not `UnknownKey`.

    Why (Qodo #229, ruling 1): `+` is the separator in a key name, so `"+"` split into two empty
    parts and never reached the session: an operator typing a plus into a shell that expects one
    (bc, a regex, a diff) silently sent nothing. A name ending in `++` is the modifiers plus a
    printable `+` (``ctrl++``), exactly as before for every other printable.
    """
    assert encode_key(backend, "+") == b"+"
    assert encode_key(backend, "ctrl++") == b"\x1b[43;5u", "Ctrl-+ carries the plus codepoint (43)"
    assert encode_key(backend, "alt+shift++") == b"\x1b+", "Alt-Shift-+ keeps the Escape prefix"


def test_printable_characters_encode_as_themselves(backend: VTBackend) -> None:
    """A printable key encodes as its UTF-8 bytes, as the table's `event.character` path did.

    Why (D34): the commonest key must be the commonest bytes, unchanged from the old table.
    """
    assert encode_key(backend, "x") == b"x"
    assert encode_key(backend, "A") == b"A"
    assert encode_key(backend, "1") == b"1"


def test_alt_prefixes_escape(backend: VTBackend) -> None:
    """Alt+letter encodes as the Escape prefix a real terminal sends, as the old table did.

    Why (D34): Textual folds Alt+letter into `alt+<char>` but keeps the bare character; the old
    table prepends ESC. The encoder's alt-as-escape option must reproduce that, or Alt-closing a
    program silently sends the bare character.
    """
    assert encode_key(backend, "alt+a") == b"\x1ba"
    assert encode_key(backend, "alt+shift+A") == b"\x1bA"
    assert encode_key(backend, "alt+1") == b"\x1b1"


# -- DECCKM changes plain arrows only --


def test_decckm_changes_plain_arrows_only(backend: VTBackend) -> None:
    """DECCKM switches plain arrows between `CSI` and `SS3` forms; a modified arrow ignores it.

    Why (D34, phase 1 "Tests"): xterm's `ESC O` form has no room for a modifier parameter, so a
    Shift/Arrow must stay in `CSI 1;<code>` form under DECCKM. Only the plain arrow changes, or a
    modified arrow stops reaching bindings that expect `CSI 1;5D`.
    """
    assert encode_key(backend, "up") == b"\x1b[A"
    assert encode_key(backend, "shift+up") == b"\x1b[1;2A"
    backend.feed(b"\x1b[?1h")  # DECSET 1: application cursor keys
    assert encode_key(backend, "up") == b"\x1bOA"
    assert encode_key(backend, "down") == b"\x1bOB"
    assert encode_key(backend, "left") == b"\x1bOD"
    assert encode_key(backend, "right") == b"\x1bOC"
    # The modified arrow ignores DECCKM.
    assert encode_key(backend, "shift+up") == b"\x1b[1;2A"
    backend.feed(b"\x1b[?1l")  # back to normal
    assert encode_key(backend, "up") == b"\x1b[A"


# -- kitty keyboard flags (R7) --


def test_kitty_flags_encode_escape_as_csi_u(backend: VTBackend) -> None:
    """With kitty disambiguate on, the encoder emits CSI-u for a key the flags cover.

    Why (R7, D34): a session that enables the kitty keyboard protocol expects CSI-u; without it the
    key reaches the target as the legacy form. This proves the encoder reads the session's flags.
    """
    # `ESC[>1u` is the disambiguate flag (protocol level 1).
    backend.feed(b"\x1b[>1u")
    assert backend.kitty_flags() != 0, "the session's kitty flags must be visible to the encoder"
    assert encode_key(backend, "escape") == b"\x1b[27u"
    assert encode_key(backend, "shift+tab") == b"\x1b[9;2u"
    backend.feed(b"\x1b[<1u")  # pop the kitty flags
    assert encode_key(backend, "escape") == b"\x1b"
    assert encode_key(backend, "shift+tab") == b"\x1b[Z", "back to the legacy form with flags off"


def test_kitty_ctrl_letter_reports_its_codepoint(backend: VTBackend) -> None:
    """With kitty disambiguate on, Ctrl/Shift combinations encode as CSI-u per the flags (R7).

    Why (R7): Textual's key event carries no unshifted codepoint or physical key, so the encoder may
    emit the wrong CSI-u once a session enables kitty flags. This records what the encoder does
    *with* a codepoint supplied; phase 4 (the Textual event → name mapping) records what is lost
    when it is not — the R7 known gap. The modifier code is 1 + the mods bitmask (5 = Ctrl, 6 =
    Ctrl+Shift).
    """
    backend.feed(b"\x1b[>1u")
    # Ctrl+A: codepoint 'a', Ctrl only.
    assert _encode_with_unshifted(backend, key=20, mods=MOD_CTRL, unshifted=ord("a")) == b"\x1b[97;5u"
    # Ctrl+Shift+A: the same codepoint, Shift added.
    assert (
        _encode_with_unshifted(backend, key=20, mods=MOD_CTRL | 1, unshifted=ord("a"))
        == b"\x1b[97;6u"
    )


def _encode_with_unshifted(
    backend: VTBackend, *, key: int, mods: int, utf8: str | None = None, unshifted: int | None = None
) -> bytes:
    """Encode with explicit key/mods/codepoint, for the kitty tests (R7)."""
    import ctypes

    from tsb_tui.term import ghostty as G

    L = backend.lib.lib
    encoder = ctypes.c_void_p()
    L.ghostty_key_encoder_new(None, ctypes.byref(encoder))
    L.ghostty_key_encoder_setopt_from_terminal(encoder, backend.terminal_handle)
    event = ctypes.c_void_p()
    L.ghostty_key_event_new(None, ctypes.byref(event))
    L.ghostty_key_event_set_action(event, G.KEY_ACTION_PRESS)
    L.ghostty_key_event_set_key(event, key)
    L.ghostty_key_event_set_mods(event, mods)
    if utf8:
        data = utf8.encode()
        L.ghostty_key_event_set_utf8(event, data, len(data))
    else:
        L.ghostty_key_event_set_utf8(event, b"", 0)
    if unshifted is not None:
        L.ghostty_key_event_set_unshifted_codepoint(event, unshifted)
    buf = ctypes.create_string_buffer(64)
    out_len = ctypes.c_size_t(0)
    L.ghostty_key_encoder_encode(encoder, event, buf, len(buf), ctypes.byref(out_len))
    out = buf.raw[: out_len.value]
    L.ghostty_key_event_free(event)
    L.ghostty_key_encoder_free(encoder)
    return out


# -- unknown key name --


def test_unknown_key_raises_and_encodes_nothing(backend: VTBackend) -> None:
    """An unknown drive key name raises `UnknownKey` and encodes nothing.

    Why (phase 1 "Tests", drive D48): a name the encoder does not know must not silently send the
    bytes of some other key or nothing at all. Raising lets drive answer `unknown_key` with
    `sent: false`, so an agent never believes a key it did not send went out.
    """
    with pytest.raises(UnknownKey):
        encode_key(backend, "not-a-key")
    with pytest.raises(UnknownKey):
        encode_key(backend, "f99")
    with pytest.raises(UnknownKey):
        encode_key(backend, "")


# -- paste: bracketed only under mode 2004 --


def test_paste_is_bracketed_only_under_mode_2004(backend: VTBackend) -> None:
    """Paste wraps in `ESC[200~ … ESC[201~` only when the session set mode 2004.

    Why (phase 1 "Tests", DT3): bracketed paste tells a program the text was pasted, not typed; a
    program that did not ask for it would show the framing as literal text. Unbracketed, a newline
    becomes a carriage return; bracketed, it is kept.
    """
    assert encode_paste(backend, "hello") == b"hello"
    assert encode_paste(backend, "a\nb") == b"a\rb", "unbracketed newlines become carriage returns"
    backend.feed(b"\x1b[?2004h")
    assert encode_paste(backend, "hello") == b"\x1b[200~hello\x1b[201~"
    assert encode_paste(backend, "a\nb") == b"\x1b[200~a\nb\x1b[201~", "bracketed newlines kept"
    backend.feed(b"\x1b[?2004l")
    assert encode_paste(backend, "hello") == b"hello"
    assert encode_paste(backend, "") == b""


# -- mouse: reports only under a mouse mode --


def test_mouse_reports_only_under_a_mouse_mode(backend: VTBackend) -> None:
    """A mouse press encodes only when the session set a mouse mode; off, it encodes nothing.

    Why (design D13; phase 1 "Tests"): a session that did not ask for mouse events must not receive
    them — the bytes would be interpreted as ordinary terminal input (a stray `ESC [ < 0 …`).
    """
    assert encode_mouse(
        backend, action=MOUSE_PRESS, button=MOUSE_BUTTON_LEFT, x=10, y=20
    ) == b"", "no mouse mode, no report"
    backend.feed(b"\x1b[?1000h\x1b[?1006h")  # normal tracking + SGR format
    assert encode_mouse(
        backend, action=MOUSE_PRESS, button=MOUSE_BUTTON_LEFT, x=10, y=20
    ) == b"\x1b[<0;11;21M", "a press under SGR tracking is 1-based"
    backend.feed(b"\x1b[?1000l\x1b[?1006l")
    assert encode_mouse(
        backend, action=MOUSE_PRESS, button=MOUSE_BUTTON_LEFT, x=10, y=20
    ) == b"", "back to no mouse mode"


# -- focus: reports only under mode 1004 --


def test_focus_reports_only_under_mode_1004(backend: VTBackend) -> None:
    """A focus report encodes only when the session set mode 1004; off, it encodes nothing.

    Why (DT4 b, session model §9–§11): a focus report is input that counts as E1 and can arm
    `quiet_prompt`. The TUI never sends one a session did not ask for, or a watching viewer could
    raise a quiet prompt for a focus change the session never tracked.
    """
    assert encode_focus(backend, True) == b"", "no mode 1004, no report"
    backend.feed(b"\x1b[?1004h")
    assert encode_focus(backend, True) == b"\x1b[I"
    assert encode_focus(backend, False) == b"\x1b[O"
    backend.feed(b"\x1b[?1004l")
    assert encode_focus(backend, True) == b""