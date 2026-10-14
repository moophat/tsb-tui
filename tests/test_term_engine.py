"""The engine: golden replays, the resize/reset contract, effects and muting, and text reads.

Why these matter (phase 1 "Tests", plan DT3/DT6/DT12/R4): the TUI must render with the *same
engine the core's VT worker uses*, so the attach prefix it rebuilds is the one the core checked
(immortality §5.2: "each surface checks it with its own renderer, the TUI first"). These tests
prove the engine layer reproduces each `C4/golden-*` property the fixture's own description names
(DT6), that a dump split at any frame boundary lands on an equal grid (the plumbing half of DT12),
that replies are answered once and never while muted (DT3), and that the text reads drive needs
(lines that left the screen, joined wraps, text since a tracked mark) behave.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tsb_tui.term import VTBackend, load

GOLDENS = Path(__file__).resolve().parents[2] / "program" / "contract" / "fixtures" / "C4"

# Every golden is opened at 80×24 with scrollback 4194304 — the values every C4 sequence fixture's
# `open` carries (VT plan DV17, plan DT18).
OPEN_COLS, OPEN_ROWS, SCROLLBACK = 80, 24, 4 * 1024 * 1024


@pytest.fixture(scope="module")
def lib(term_so: Path):
    return load(term_so)


def _decode(name: str):
    """Decode a golden's `.bin` into its C4 messages with the contract's own codec (`tsbc._binfmt`).

    DT6 and Rule 8: the fixtures are decoded by the published codec, never re-parsed here, so the
    test reads the fixture exactly as the worker does. F14d: a kind-1 `resize` control message is
    applied as it comes.
    """
    from tsbc._binfmt import (
        VT_KIND_CONTROL,
        VT_KIND_FRAME,
        decode_control,
        decode_frames_vt,
        decode_output_frame,
    )

    raw = (GOLDENS / f"{name}.bin").read_bytes()
    messages, trailing = decode_frames_vt(raw)
    assert not trailing, f"{name}: trailing bytes after the last message"
    chunks = []
    for msg in messages:
        if msg.kind == VT_KIND_FRAME:
            chunks.append(("feed", decode_output_frame(msg).frame.payload))
        elif msg.kind == VT_KIND_CONTROL:
            control = decode_control(msg)
            assert control.get("cmd") == "resize", f"{name}: unhandled control {control.get('cmd')!r}"
            chunks.append(("resize", (control["cols"], control["rows"])))
        else:
            chunks.append(("blob", msg.body))
    return chunks


def _replay(lib, chunks, *, cols=OPEN_COLS, rows=OPEN_ROWS) -> VTBackend:
    backend = VTBackend(lib, cols, rows, scrollback_bytes=SCROLLBACK)
    try:
        for kind, payload in chunks:
            if kind == "feed":
                backend.feed(payload)
            elif kind == "resize":
                backend.resize(*payload)
        return backend
    except Exception:
        backend.close()
        raise


def _golden(lib, name):
    return _replay(lib, _decode(name))


# -- golden replays: each shows the property the fixture's description names (DT6) --


def test_golden_sgr_reports_bold_green_then_plain(lib) -> None:
    """golden-sgr: an SGR run bold+green at row 0 columns 0..9, plain after `CSI 0 m`.

    Why (DT6): the property the fixture names, so a round trip of two empty screens cannot pass.
    The style comes from the engine, resolved by style id (R4).
    """
    backend = _golden(lib, "golden-sgr")
    try:
        rows = {row.y: row for row in backend.dirty_rows()}
        cells = rows[0].cells
        run = cells[:10]
        assert "".join(c.text for c in run) == "green bold"
        style = rows[0].style_for(run[0].style_id)
        assert style is not None and style.bold, "the first ten cells are not bold"
        assert style.fg_color.tag == 1, "the run is not a palette colour"
        # After CSI 0m the style is default: style id 0, so no resolved style.
        after = cells[10:]
        assert "".join(c.text for c in after).startswith(" plain")
        assert all(c.style_id == 0 for c in after)
    finally:
        backend.close()


def test_golden_alt_screen_shows_alt_while_1049_then_primary_after(lib) -> None:
    """golden-alt-screen: the alternate screen while `CSI ?1049h` is in effect, the primary after
    `CSI ?1049l`, with both screens' lines in the dump's emission order.

    Why (DT6; design §3.2): the earlier renderer ignored mode 1049, so a dump made inside vim left
    vim's screen behind after `:q`. The TUI must follow the mode to the primary screen and recover
    `$ vim`; this is the property the fixture names.
    """
    chunks = _decode("golden-alt-screen")
    backend = VTBackend(lib, OPEN_COLS, OPEN_ROWS, scrollback_bytes=SCROLLBACK)
    try:
        backend.feed(chunks[0][1])
        assert backend.active_screen() == 0
        assert backend.grid_lines() == ["$ vim"]
        backend.feed(chunks[1][1])
        assert backend.active_screen() == 1, "CSI ?1049h must select the alternate screen"
        assert backend.grid_lines() == ["ALT SCREEN"]
        backend.feed(chunks[2][1])
        assert backend.active_screen() == 0, "CSI ?1049l must restore the primary screen"
        assert backend.grid_lines() == ["$ vim", "$"]
    finally:
        backend.close()


def test_golden_wide_chars_occupy_their_cell_once(lib) -> None:
    """golden-wide-chars: a wide glyph occupies its cell once — its continuation not repeated as a
    second glyph — at the cursor the output leaves.

    Why (DT6): the wide-cell tail must be reported as a spacer tail with no text, so the canvas
    paints the glyph once instead of twice (screen.h:108 GHOSTTY_CELL_WIDE_SPACER_TAIL).
    """
    backend = _golden(lib, "golden-wide-chars")
    try:
        rows = {row.y: row for row in backend.dirty_rows()}
        second = rows[1].cells
        # 世 wide, its tail empty, then 界 wide, its tail empty.
        assert second[0].text == "世" and second[0].wide == 1
        assert second[1].text == "" and second[1].wide_tail
        assert second[2].text == "界" and second[2].wide == 1
        assert second[3].text == "" and second[3].wide_tail
        painted = "".join(c.text for c in second)
        assert painted == "世界", f"wide glyphs painted once each, got {painted!r}"
    finally:
        backend.close()


def test_golden_reflow_reflows_at_the_new_size(lib) -> None:
    """golden-reflow: the same logical text reflows at 40×12 after the resize event (F14d).

    Why (DT6): the grid takes the size the session asked for and the wrapped line is rejoined by the
    engine's reflow; a TUI that keeps the window's size would reflow to the wrong width (D3).
    """
    backend = _golden(lib, "golden-reflow")
    try:
        assert (backend.cols, backend.rows) == (40, 12)
        lines = backend.grid_lines()
        assert "".join(l.replace(" ", "") for l in lines) == (
            "thequickbrownfoxjumpsoverthelazydogandkeepsonrunningpasttheedge")
    finally:
        backend.close()


def test_golden_resize_mid_stream_is_the_new_size_with_both_lines(lib) -> None:
    """golden-resize-mid-stream: the grid is 100×30 for the output after the resize event, and the
    earlier lines are still there (F14d).

    Why (DT6): the resize arrives mid-stream; the backend must take it and keep prior content.
    """
    chunks = _decode("golden-resize-mid-stream")
    backend = VTBackend(lib, OPEN_COLS, OPEN_ROWS, scrollback_bytes=SCROLLBACK)
    try:
        backend.feed(chunks[0][1])
        assert (backend.cols, backend.rows) == (OPEN_COLS, OPEN_ROWS)
        backend.resize(*chunks[1][1])
        backend.feed(chunks[2][1])
        assert (backend.cols, backend.rows) == (100, 30)
        assert backend.grid_lines() == ["one two three four", "five six seven eight"]
    finally:
        backend.close()


def test_golden_input_modes_reports_every_carried_mode(lib) -> None:
    """golden-input-modes: decckm, bracketed paste, mouse 1000 with sgr encoding, focus, keypad
    application, origin, autowrap and insert are all set.

    Why (DT6): nothing in TSB re-encodes keys; the prefix carries the input modes because the
    client's own encoder needs them (immortality §5.2). If the TUI's mode reads are wrong, arrows,
    paste, mouse and focus encode wrongly on a session that asked for them.
    """
    backend = _golden(lib, "golden-input-modes")
    try:
        assert backend.mode_is_set(1), "DECCKM"
        assert backend.mode_is_set(2004), "bracketed paste"
        assert backend.mode_is_set(1000), "normal mouse"
        assert backend.mode_is_set(1006), "SGR mouse format"
        assert backend.mode_is_set(1004), "focus events"
        assert backend.mode_is_set(66), "keypad application"
        assert backend.mode_is_set(6), "origin"
        assert backend.mode_is_set(7), "autowrap"
        assert backend.mode_is_set(4, ansi=True), "insert"
        modes = backend.modes()
        assert modes.cursor_keys_application and modes.bracketed_paste
        assert modes.mouse_tracking and modes.focus_events and modes.keypad_application
    finally:
        backend.close()


def test_golden_mode_reset_returns_every_mode(lib) -> None:
    """golden-mode-reset: every carried mode is back to its reset state after the second chunk.

    Why (DT6): a `prefix`/dump replay must leave the modes the reset names, so the surface after an
    attach behaves as the reset left it.
    """
    backend = _golden(lib, "golden-mode-reset")
    try:
        modes = backend.modes()
        assert not modes.cursor_keys_application
        assert not modes.bracketed_paste
        assert not modes.mouse_tracking
        assert not modes.focus_events
        assert not modes.keypad_application
        assert not backend.mode_is_set(4, ansi=True), "insert"
    finally:
        backend.close()


# -- split-at-every-frame-boundary: the plumbing half of DT12 --


@pytest.mark.parametrize(
    "name", ["golden-sgr", "golden-alt-screen", "golden-input-modes", "golden-mode-reset"]
)
def test_split_at_each_frame_boundary_ends_in_an_equal_grid(lib, name: str) -> None:
    """The same stream split at each frame boundary, fed as "raw prefix then the rest" into a
    second backend, ends in an equal grid (DT12's plumbing half; C4 goldens).

    Why (DT12): a dump replayed onto a grid already holding output must land exactly where a clean
    replay would. A backend that kept state across the split (or lost a mode) would show a
    different grid; this is the property that lets the core check the prefix by round trip.
    """
    chunks = _decode(name)
    whole = _replay(lib, chunks)
    try:
        expected = (whole.grid_lines(), whole.modes())
        for split in range(len(chunks) + 1):
            prefix, rest = chunks[:split], chunks[split:]
            part = _replay(lib, prefix + rest)
            try:
                assert (part.grid_lines(), part.modes()) == expected, (
                    f"{name}: split at {split} gives a different grid or modes")
            finally:
                part.close()
    finally:
        whole.close()


# -- resize / reset contract (phase 1 step 3; DT12) --


def test_resize_keeps_exactly_the_size_asked_and_never_reflows_unasked(lib) -> None:
    """`resize` keeps exactly the size asked; a resize the session did not ask for never reflows.

    Why (D3, DT16, phase 1 step 3): the grid always has the *session's* size. Resizing to the
    window's size would reflow a session's grid behind the operator's back (issue #176). Here a
    resize to the asked size changes geometry, and feeding output then reading keeps the size.
    """
    backend = VTBackend(lib, 80, 24, scrollback_bytes=SCROLLBACK)
    try:
        backend.resize(132, 40)
        assert (backend.cols, backend.rows) == (132, 40)
        backend.feed(b"hello\r\n")
        assert (backend.cols, backend.rows) == (132, 40), "feeding does not change the session's size"
    finally:
        backend.close()


def test_reset_empties_the_grid_and_the_modes(lib) -> None:
    """`reset` gives a fresh terminal: the grid is empty and the modes are back to their defaults.

    Why (DT12): a reattach's `prefix` resets the grid to `prefix.cols × prefix.rows` before the
    dump; if a mode survived (say DECCKM), arrows would encode wrongly on the reattached session.
    """
    backend = VTBackend(lib, 80, 24, scrollback_bytes=SCROLLBACK)
    try:
        backend.feed(b"\x1b[?1h\x1b[?2004hdirty line\r\n")
        assert backend.mode_is_set(1) and backend.grid_lines()
        backend.reset(100, 30)
        assert (backend.cols, backend.rows) == (100, 30)
        modes = backend.modes()
        assert not modes.cursor_keys_application and not modes.bracketed_paste
        assert all(line.strip() == "" for line in backend.grid_lines()), "the grid is not empty"
    finally:
        backend.close()


# -- effects: replies, muting, and what is never installed (DT3) --


def test_dsr_and_da1_each_produce_exactly_one_reply(lib) -> None:
    """A DSR (`ESC[6n`) and a DA1 (`ESC[c`) each produce exactly one reply to the callback.

    Why (DT3): the session queries the terminal and expects an answer on the pty; the input path
    (phase 4) sends it. Exactly one reply per query, or the session sees a duplicated answer.
    """
    replies: list[bytes] = []
    backend = VTBackend(lib, 80, 24, scrollback_bytes=SCROLLBACK)
    try:
        backend.set_reply_callback(replies.append)
        backend.feed(b"\x1b[6n")
        assert len(replies) == 1
        assert replies[0].startswith(b"\x1b[") and replies[0].endswith(b"R")
        replies.clear()
        backend.feed(b"\x1b[c")
        assert len(replies) == 1
        assert replies[0].endswith(b"c")
    finally:
        backend.set_reply_callback(None)
        backend.close()


def test_device_attributes_declare_da1_da2_and_da3(lib) -> None:
    """The fixed DA answer fills all three sub-structs and never advertises 132 columns.

    Why (Qodo #229, ruling 3; DT3): the engine calls this callback for DA1, DA2 and DA3 and uses
    whichever sub-struct matches. DA2/DA3 left zero made the TUI answer those queries with no data,
    so a session probing the terminal got an empty answer; and DA1's feature list holds parameter
    *numbers*, so parameter 1 ("132 columns") would invite the session to resize a screen the
    backend is not resizing (DECCOLM, the same reason the old bit-mask comment gave).
    """
    import ctypes

    from tsb_tui.term import ghostty as G

    backend = VTBackend(lib, 80, 24, scrollback_bytes=SCROLLBACK)
    try:
        attrs = G.GhosttyDeviceAttributes()
        assert backend._on_device_attributes(None, None, ctypes.byref(attrs))
        # DA1: the feature list is parameter numbers, and parameter 1 (132 columns) is absent.
        primary_params = list(attrs.primary.features[: attrs.primary.num_features])
        assert 1 not in primary_params, "DA1 must not advertise 132 columns (parameter 1)"
        assert 9 in primary_params, "DA1 keeps national replacement character sets (parameter 9)"
        # DA2: VT220 (device type 1), firmware version 10, no ROM cartridge.
        assert attrs.secondary.device_type == 1, "DA2 device type is VT220 (1)"
        assert attrs.secondary.firmware_version == 10, "DA2 firmware version is 10"
        assert attrs.secondary.rom_cartridge == 0, "DA2 ROM cartridge is 0 for an emulator"
        # DA3: unit id 0.
        assert attrs.tertiary.unit_id == 0
    finally:
        backend.close()


def test_da2_and_da3_reach_the_session(lib) -> None:
    """A DA2 (`ESC[>c`) and a DA3 (`ESC[=c`) each reply with the fixed identity; DA1 keeps no 132.

    Why (Qodo #229, ruling 3; DT3): with DA2/DA3 unset the engine copied whatever the callback's
    struct held, so a session that asked "what terminal are you?" (VT220) got garbage or none and
    could hang waiting. The reply is the fixed identity this TUI declares: VT220 (device type 1),
    firmware 10, no ROM cartridge; tertiary unit id 0. DA1 drops parameter 1 so a session is never
    invited to switch to 132 columns.
    """
    replies: list[bytes] = []
    backend = VTBackend(lib, 80, 24, scrollback_bytes=SCROLLBACK)
    try:
        backend.set_reply_callback(replies.append)
        backend.feed(b"\x1b[>c")
        assert replies == [b"\x1b[>1;10;0c"], "DA2 is the fixed VT220 identity"
        replies.clear()
        backend.feed(b"\x1b[=c")
        assert replies == [b"\x1bP!|00000000\x1b\\"], "DA3 reports unit id 0 as an 8-digit hex"
        replies.clear()
        backend.feed(b"\x1b[c")
        assert len(replies) == 1 and replies[0].endswith(b"c"), "DA1 is still answered exactly once"
        # `?` marks primary; conformance is 1, then the one parameter (9). Parameter 1 is absent.
        assert replies[0] == b"\x1b[?1;9c", "DA1 keeps only parameter 9 (national replacement char sets)"
    finally:
        backend.set_reply_callback(None)
        backend.close()


def test_no_reply_while_muted_and_reset_stays_muted(lib) -> None:
    """No reply while muted; a `reset` (which a reattach and its dump use) leaves replies muted
    for the block.

    Why (DT3): a query recorded in a dump must never be answered again, or the reattach would send
    a reply for a query from before the attach — input this client never produced.
    """
    replies: list[bytes] = []
    backend = VTBackend(lib, 80, 24, scrollback_bytes=SCROLLBACK)
    try:
        backend.set_reply_callback(replies.append)
        with backend.muted():
            backend.feed(b"\x1b[6n")
        assert replies == [], "a query inside a muted block must not be answered"
        backend.reset(80, 24)
        with backend.muted():
            backend.feed(b"\x1b[6n\x1b[c")
        assert replies == [], "a query inside a dump after a reset must not be answered"
    finally:
        backend.set_reply_callback(None)
        backend.close()


def test_osc_52_read_produces_no_reply(lib) -> None:
    """An OSC 52 read (`ESC]52;c;?`) produces no reply.

    Why (DT3): a target's OSC 52 read must never pull the operator's clipboard. The clipboard read
    effect is never installed, so the query is silently ignored and no reply reaches the callback.
    """
    replies: list[bytes] = []
    backend = VTBackend(lib, 80, 24, scrollback_bytes=SCROLLBACK)
    try:
        backend.set_reply_callback(replies.append)
        backend.feed(b"\x1b]52;c;?\x07")
        assert replies == [], "an OSC 52 read must not be answered (it would leak the clipboard)"
    finally:
        backend.set_reply_callback(None)
        backend.close()


def test_mode_2026_begin_and_end_toggle_the_hold_flag(lib) -> None:
    """A mode-2026 (synchronized output) begin sets the hold flag; its end clears it.

    Why (DT3, render.h "Render Holds"): a program drawing a frame asks the renderer to hold its
    paint; the canvas reads this flag and holds until it clears, so the operator never sees a
    half-drawn frame.
    """
    backend = VTBackend(lib, 80, 24, scrollback_bytes=SCROLLBACK)
    try:
        assert not backend.hold
        backend.feed(b"\x1b[?2026h")
        assert backend.modes().synchronized_output
        assert backend.hold, "a mode-2026 begin must raise the hold flag"
        backend.feed(b"\x1b[?2026l")
        assert not backend.hold, "a mode-2026 end must clear the hold flag"
    finally:
        backend.close()


# -- text reads for drive (D50) --


def test_lines_that_left_the_screen_are_returned_and_capped(lib) -> None:
    """`scrollback` returns the lines that left the screen, and every read is capped by the caller.

    Why (D50): drive's `read history` and `read reply` read within the scrollback; a cap keeps a
    long-running session's history from reading without bound (drive.reply_max_lines).
    """
    backend = VTBackend(lib, 10, 3, scrollback_bytes=SCROLLBACK)
    try:
        for i in range(12):
            backend.feed(f"L{i:02d}\r\n".encode())
        assert backend.scrollback_rows() > 0
        history = backend.scrollback()
        assert "L00" in history and "L02" in history, "lines that left the screen are returned"
        assert len(backend.scrollback(cap=2)) == 2, "the caller's cap bounds the read"
        visible = backend.visible_rows()
        assert len(visible) <= backend.rows
    finally:
        backend.close()


def test_wrapped_lines_join(lib) -> None:
    """Wrapped lines join when `join_wrapped` is asked for.

    Why (D50 "wrapped lines can be joined"): a command longer than the grid width is one logical
    line; an agent reading the screen wants it whole, not chopped at the wrap.
    """
    backend = VTBackend(lib, 8, 3, scrollback_bytes=SCROLLBACK)
    try:
        backend.feed(b"abcdefghijklmnop")
        assert backend.grid_lines() == ["abcdefgh", "ijklmnop"], "wrapped at the grid width"
        assert backend.grid_lines(join_wrapped=True) == ["abcdefghijklmnop"]
    finally:
        backend.close()


def test_text_since_a_tracked_mark_survives_scrolling(lib) -> None:
    """Text since a tracked mark survives the mark scrolling off the screen.

    Why (D50, design §3.4 test 7): `read reply` returns output that scrolled off the screen. The
    mark is a *tracked* grid reference (grid_ref_tracked.h), so it follows its cell as the page list
    changes; a mark that only knew its original row would return nothing after scrolling.
    """
    backend = VTBackend(lib, 10, 3, scrollback_bytes=SCROLLBACK)
    try:
        for i in range(4):
            backend.feed(f"old{i}\r\n".encode())
        backend.set_mark("reply")
        for i in range(8):
            backend.feed(f"new{i}\r\n".encode())
        backend.mark_clean()
        reply = backend.text_since("reply")
        assert reply, "text since the mark must survive scrolling"
        assert all(line.startswith("new") for line in reply), f"only new output, got {reply}"
        assert "new0" in reply
    finally:
        backend.close()

# -- the screen read, exactly (phase 9: drive's `read screen`, `read reply`, `prompt` and `screen` waits) --


def test_visible_rows_is_exactly_the_screen_whatever_the_formatter_trims(lib) -> None:
    """D50: `read screen` is the screen at the session's size, row for row. The formatter prints scrollback then screen
    and trims blank rows from the end, so "the last `rows` lines" is the screen only when the screen is full: a short
    screen was read as scrollback, and a cleared screen showed the lines that had scrolled off it. A row's index must be
    its row on screen (the cursor's row is read from it), blank rows included."""
    backend = VTBackend(lib, 20, 5, scrollback_bytes=SCROLLBACK)
    try:
        backend.feed(b"hello\r\nworld\r\n")
        assert backend.visible_rows() == ["hello", "world", "", "", ""], "a short screen keeps its blank rows"
        assert backend.scrollback() == []
        backend.feed(b"".join(f"line{i}\r\n".encode() for i in range(10)))
        assert backend.scrollback()[:2] == ["hello", "world"]
        backend.feed(b"\x1b[2J\x1b[H")
        assert backend.visible_rows() == [""] * 5, "a cleared screen is blank, not the scrollback's last lines"
        assert "line1" in backend.scrollback(), "what left the screen stays in the scrollback"
        backend.feed(b"\x1b[3;1Hmiddle")
        assert backend.visible_rows() == ["", "", "middle", "", ""]
    finally:
        backend.close()


def test_the_alternate_screen_is_read_as_the_screen_with_no_scrollback(lib) -> None:
    """`read reply` on a full-screen program says `alt` and returns the screen (guide §6): the alternate screen has no
    scrollback, so nothing of the shell's history may leak into its rows."""
    backend = VTBackend(lib, 20, 4, scrollback_bytes=SCROLLBACK)
    try:
        backend.feed(b"".join(f"sh{i}\r\n".encode() for i in range(8)))
        backend.feed(b"\x1b[?1049h\x1b[HVIM")
        assert backend.visible_rows() == ["VIM", "", "", ""] and backend.scrollback() == []
        backend.feed(b"\x1b[?1049l")
        assert backend.visible_rows()[-1] == "" and "sh0" in backend.scrollback()
    finally:
        backend.close()


def test_visible_rows_and_text_since_join_wrapped_lines_when_asked(lib) -> None:
    """D50 "wrapped lines can be joined": a command longer than the grid is one line to an agent, on the screen and in a
    reply. When the mark sits on a wrapped row, the cut is where its column is in the joined line."""
    backend = VTBackend(lib, 10, 12, scrollback_bytes=SCROLLBACK)
    try:
        backend.feed(b"$ ")
        backend.set_mark("m")
        backend.feed(b"echo a very long command line\r\nout1\r\nout2 wrapped around the grid\r\n$ ")
        assert backend.visible_rows()[:3] == ["$ echo a v", "ery long c", "ommand lin"]
        assert backend.visible_rows(join_wrapped=True) == [
            "$ echo a very long command line", "out1", "out2 wrapped around the grid", "$"]
        assert backend.text_since("m") == ["echo a v", "ery long c", "ommand lin", "e", "out1", "out2 wrapp",
                                           "ed around", "the grid", "$"]
        assert backend.text_since("m", join_wrapped=True) == [
            "echo a very long command line", "out1", "out2 wrapped around the grid", "$"]
        backend.feed(b"ls -la /var/log/some/dir")
        backend.set_mark("n")
        backend.feed(b"x")
        assert backend.text_since("n", join_wrapped=True) == ["x"]
    finally:
        backend.close()


def test_has_mark_tells_no_input_yet_from_a_reply_that_is_empty(lib) -> None:
    """`text_since` is `[]` both for a mark that was never set (or a reset freed) and for a reply with nothing in it;
    drive reads everything received in the first case and nothing in the second, so it must be able to tell them
    apart."""
    backend = VTBackend(lib, 10, 3, scrollback_bytes=SCROLLBACK)
    try:
        assert backend.has_mark("input") is False and backend.text_since("input") == []
        backend.set_mark("input")
        assert backend.has_mark("input") is True and backend.text_since("input") == []
        backend.reset(10, 3)
        assert backend.has_mark("input") is False, "a reset frees every mark"
    finally:
        backend.close()
