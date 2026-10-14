"""Key, paste, mouse and focus encoding through libghostty's own encoders (D34).

Why (design D34): a key must be re-encoded for the *session's* modes — DECCKM arrows, keypad
application, bracketed paste, the kitty keyboard flags — because the TUI's outer terminal is not
the session's terminal. libghostty's encoder already knows those modes (`key/encoder.h:186`
`setopt_from_terminal`), so the TUI asks it rather than carrying a second, drifting key table.

This module imports no Textual (the `term/` package imports only the stdlib and `tsb_tui`), so it
takes a *key name* — the same friendly name Textual and drive both use (`"up"`, `"ctrl+a"`,
`"alt+left"`, `"f5"`) — and returns bytes. A name it does not know raises :class:`UnknownKey` and
encodes nothing, so a caller can never silently send the wrong bytes.

The paste and focus encoders take the backend too, so the session's modes decide the framing: paste
is bracketed only under mode 2004, and a focus report is produced only under mode 1004. Off, they
return ``b""`` and the caller sends nothing.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import ghostty as G

if TYPE_CHECKING:
    from .backend import VTBackend


class UnknownKey(ValueError):
    """A drive key name this encoder does not know. Nothing is encoded."""


# Modifier names in a key string, mapped to the GhosttyMods bit (key/event.h:56).
_MOD_NAMES = {
    "shift": G.MODS_SHIFT,
    "ctrl": G.MODS_CTRL,
    "alt": G.MODS_ALT,
}

# Named keys, mapped to the physical GhosttyKey (key/event.h GhosttyKey enum): the non-letter keys a
# person types plus the ones drive names, and nothing speculative.
_NAMED_KEYS: dict[str, int] = {
    "enter": 58,  # GHOSTTY_KEY_ENTER
    "escape": 120,  # GHOSTTY_KEY_ESCAPE
    "esc": 120,
    "tab": 64,  # GHOSTTY_KEY_TAB
    "backspace": 53,  # GHOSTTY_KEY_BACKSPACE
    "delete": 68,  # GHOSTTY_KEY_DELETE
    "insert": 72,  # GHOSTTY_KEY_INSERT
    "home": 71,  # GHOSTTY_KEY_HOME
    "end": 69,  # GHOSTTY_KEY_END
    "pageup": 74,  # GHOSTTY_KEY_PAGE_UP
    "pagedown": 73,  # GHOSTTY_KEY_PAGE_DOWN
    "up": 78,  # GHOSTTY_KEY_ARROW_UP
    "down": 75,  # GHOSTTY_KEY_ARROW_DOWN
    "left": 76,  # GHOSTTY_KEY_ARROW_LEFT
    "right": 77,  # GHOSTTY_KEY_ARROW_RIGHT
    "space": 63,  # GHOSTTY_KEY_SPACE
}
for _n in range(1, 26):
    _NAMED_KEYS[f"f{_n}"] = 120 + _n  # F1..F25 are consecutive from GHOSTTY_KEY_F1 (121)

# Printable characters that appear in a key string after a modifier (the punctuation Textual
# names rather than gives a raw byte for). Maps the name to the character it stands for.
_PUNCT_NAMES = {
    "backslash": "\\",
    "underscore": "_",
    "circumflex_accent": "^",
    "right_square_bracket": "]",
    "left_square_bracket": "[",
    "at": "@",
    "plus": "+",
    "minus": "-",
    "slash": "/",
    "question_mark": "?",
    "comma": ",",
    "period": ".",
    "colon": ":",
    "semicolon": ";",
    "quote": "'",
    "quotation_mark": '"',
    "grave_accent": "`",
    "tilde": "~",
    "exclamation_mark": "!",
    "number_sign": "#",
    "dollar_sign": "$",
    "percent_sign": "%",
    "ampersand": "&",
    "asterisk": "*",
    "equals_sign": "=",
    "less_than_sign": "<",
    "greater_than_sign": ">",
    "vertical_line": "|",
    "left_curly_bracket": "{",
    "right_curly_bracket": "}",
    "left_parenthesis": "(",
    "right_parenthesis": ")",
}


@dataclass(frozen=True)
class _ParsedKey:
    """A key name decomposed into what the libghostty event needs."""

    key: int  # GhosttyKey, or GHOSTTY_KEY_UNIDENTIFIED (0)
    mods: int
    utf8: str | None
    unshifted: int | None


def _parse(name: str) -> _ParsedKey:
    """Parse a friendly key name into the encoder's inputs.

    Examples: ``"up"``, ``"ctrl+a"``, ``"alt+shift+a"``, ``"ctrl+left"``, ``"shift+tab"``,
    ``"f5"``. Raises :class:`UnknownKey` for anything else.
    """
    if not name:
        raise UnknownKey(name)
    if name.endswith("++"):
        # `+` is the separator, so a trailing `++` means the base is the printable plus itself
        # (`"ctrl++"` is Ctrl plus a literal `+`). Strip those two before splitting the modifiers.
        parts = name[:-2].split("+")
        *mod_names, base = [*parts, "+"]
    elif name == "+":
        # A lone `+` is the printable plus with no modifiers (`"+".split("+")` is two empty parts).
        mod_names, base = [], "+"
    else:
        *mod_names, base = name.split("+")
    mods = 0
    for m in mod_names:
        if m not in _MOD_NAMES:
            raise UnknownKey(name)
        mods |= _MOD_NAMES[m]

    # A single printable character is encoded as that character, with the modifiers applied.
    if len(base) == 1 and base.isprintable():
        return _ParsedKey(key=0, mods=mods, utf8=base, unshifted=ord(base))
    if base in _PUNCT_NAMES:
        ch = _PUNCT_NAMES[base]
        return _ParsedKey(key=0, mods=mods, utf8=ch, unshifted=ord(ch))
    if base == "space":
        # Space is a named key that also types a character: without its text the encoder returns nothing.
        return _ParsedKey(key=_NAMED_KEYS["space"], mods=mods, utf8=" ", unshifted=ord(" "))
    if base in _NAMED_KEYS:
        # A named key has no UTF-8 text; the encoder derives its sequence from the key and mods.
        return _ParsedKey(key=_NAMED_KEYS[base], mods=mods, utf8=None, unshifted=None)
    raise UnknownKey(name)


def _encode_event(
    backend: VTBackend, key: int, mods: int, utf8: str | None, unshifted: int | None
) -> bytes:
    """Encode one key event through a fresh libghostty key encoder synced from the session."""
    L = backend.lib.lib
    encoder = ctypes.c_void_p()
    if L.ghostty_key_encoder_new(None, ctypes.byref(encoder)) != G.GHOSTTY_SUCCESS:
        raise RuntimeError("ghostty_key_encoder_new failed")
    event = ctypes.c_void_p()
    if L.ghostty_key_event_new(None, ctypes.byref(event)) != G.GHOSTTY_SUCCESS:
        L.ghostty_key_encoder_free(encoder)
        raise RuntimeError("ghostty_key_event_new failed")
    try:
        # Pull DECCKM, keypad mode, alt-as-escape and the kitty flags from the session (D34). The
        # engine read holds the backend lock (DT20), so it cannot race a feed slice.
        with backend.lock():
            L.ghostty_key_encoder_setopt_from_terminal(encoder, backend.terminal_handle)
        L.ghostty_key_event_set_action(event, G.KEY_ACTION_PRESS)
        L.ghostty_key_event_set_key(event, key)
        L.ghostty_key_event_set_mods(event, mods)
        if utf8:
            encoded = utf8.encode("utf-8")
            L.ghostty_key_event_set_utf8(event, encoded, len(encoded))
        else:
            L.ghostty_key_event_set_utf8(event, b"", 0)
        if unshifted is not None:
            L.ghostty_key_event_set_unshifted_codepoint(event, unshifted)
        buf = ctypes.create_string_buffer(64)
        out_len = ctypes.c_size_t(0)
        rc = L.ghostty_key_encoder_encode(encoder, event, buf, len(buf), ctypes.byref(out_len))
        if rc == G.GHOSTTY_OUT_OF_SPACE:
            buf = ctypes.create_string_buffer(out_len.value)
            rc = L.ghostty_key_encoder_encode(
                encoder, event, buf, len(buf), ctypes.byref(out_len)
            )
        if rc != G.GHOSTTY_SUCCESS:
            raise RuntimeError(f"ghostty_key_encoder_encode failed with result {rc}")
        return buf.raw[: out_len.value]
    finally:
        L.ghostty_key_event_free(event)
        L.ghostty_key_encoder_free(encoder)


def encode_key(backend: VTBackend, name: str) -> bytes:
    """Encode a friendly key name for the backend's session modes (D34).

    Raises :class:`UnknownKey` and encodes nothing for a name this encoder does not know.
    """
    parsed = _parse(name)
    return _encode_event(backend, parsed.key, parsed.mods, parsed.utf8, parsed.unshifted)


def encode_paste(backend: VTBackend, text: str) -> bytes:
    """Encode pasted text for the pty, bracketed only when the session set mode 2004.

    Uses libghostty's own paste encoder (paste.h:241), which strips unsafe control bytes and either
    wraps in `ESC[200~ … ESC[201~` (bracketed) or turns newlines into carriage returns. Off, the
    text is written as plain text with newlines as carriage returns.
    """
    if not text:
        return b""
    bracketed = backend.modes().bracketed_paste
    L = backend.lib.lib
    data = text.encode("utf-8")
    src = ctypes.create_string_buffer(data, len(data))
    buf = ctypes.create_string_buffer(len(data) * 2 + 32)
    out_len = ctypes.c_size_t(0)
    rc = L.ghostty_paste_encode(
        ctypes.cast(src, ctypes.c_char_p), len(data), bracketed, buf, len(buf),
        ctypes.byref(out_len),
    )
    if rc == G.GHOSTTY_OUT_OF_SPACE:
        buf = ctypes.create_string_buffer(out_len.value)
        rc = L.ghostty_paste_encode(
            ctypes.cast(src, ctypes.c_char_p), len(data), bracketed, buf, len(buf),
            ctypes.byref(out_len),
        )
    if rc != G.GHOSTTY_SUCCESS:
        raise RuntimeError(f"ghostty_paste_encode failed with result {rc}")
    return buf.raw[: out_len.value]


def encode_focus(backend: VTBackend, gained: bool) -> bytes:
    """Encode a focus gained/lost report, or ``b""`` when the session has not set mode 1004.

    A focus report is input: it counts as E1 and can arm `quiet_prompt` (session model §9–§11), so
    the TUI never sends one a session did not ask for.
    """
    if not backend.modes().focus_events:
        return b""
    L = backend.lib.lib
    buf = ctypes.create_string_buffer(16)
    out_len = ctypes.c_size_t(0)
    event = G.FOCUS_GAINED if gained else G.FOCUS_LOST
    rc = L.ghostty_focus_encode(event, buf, len(buf), ctypes.byref(out_len))
    if rc != G.GHOSTTY_SUCCESS:
        raise RuntimeError(f"ghostty_focus_encode failed with result {rc}")
    return buf.raw[: out_len.value]


def encode_mouse(
    backend: VTBackend,
    *,
    action: int,
    button: int | None,
    x: int,
    y: int,
    mods: int = 0,
) -> bytes:
    """Encode a mouse event per the session's mouse modes (mouse/encoder.h:208).

    The session's tracking mode and output format are read from the terminal
    (`setopt_from_terminal`, mouse/encoder.h:175). Coordinates are grid cells; the encoder's cell
    size is set to 1×1 so cells pass through unchanged. An event the session's mode does not report
    encodes to ``b""`` (for example a press with no mouse mode enabled).
    """
    L = backend.lib.lib
    encoder = ctypes.c_void_p()
    if L.ghostty_mouse_encoder_new(None, ctypes.byref(encoder)) != G.GHOSTTY_SUCCESS:
        raise RuntimeError("ghostty_mouse_encoder_new failed")
    event = ctypes.c_void_p()
    if L.ghostty_mouse_event_new(None, ctypes.byref(event)) != G.GHOSTTY_SUCCESS:
        L.ghostty_mouse_encoder_free(encoder)
        raise RuntimeError("ghostty_mouse_event_new failed")
    try:
        with backend.lock():
            L.ghostty_mouse_encoder_setopt_from_terminal(encoder, backend.terminal_handle)
        size = G.GhosttyMouseEncoderSize()
        size.size = ctypes.sizeof(G.GhosttyMouseEncoderSize)
        size.screen_width = backend.cols
        size.screen_height = backend.rows
        size.cell_width = 1
        size.cell_height = 1
        L.ghostty_mouse_encoder_setopt(encoder, G.MOUSE_ENCODER_OPT_SIZE, ctypes.byref(size))
        L.ghostty_mouse_event_set_action(event, action)
        if button:
            L.ghostty_mouse_event_set_button(event, button)
        else:
            L.ghostty_mouse_event_clear_button(event)
        L.ghostty_mouse_event_set_mods(event, mods)
        position = G.GhosttyMousePosition(x=float(x), y=float(y))
        L.ghostty_mouse_event_set_position(event, position)
        buf = ctypes.create_string_buffer(64)
        out_len = ctypes.c_size_t(0)
        rc = L.ghostty_mouse_encoder_encode(encoder, event, buf, len(buf), ctypes.byref(out_len))
        if rc != G.GHOSTTY_SUCCESS:
            raise RuntimeError(f"ghostty_mouse_encoder_encode failed with result {rc}")
        return buf.raw[: out_len.value]
    finally:
        L.ghostty_mouse_event_free(event)
        L.ghostty_mouse_encoder_free(encoder)