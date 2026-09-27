"""Terminal — attach via `Client.async_attach` (the asyncio transport, run on Textual's own loop:
never the blocking `attach`), keys to binary frames, resize on terminal resize, the pyte screen
rendered into a plain-text Textual widget (Contract §11: the grid is internal broker state; the
client owns all rendering), and a WS close code turned into a banner (session model §12)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Static
from tsb_contract_client import ApiError, AttachRefused

from .._transport import ConnectionClosed, HandshakeError, HttpError, ProtocolError
from ..render import TerminalEmulator

# schemas/ws-frames.schema.json x-close-codes: these three are the end states a session can be in
# when its WebSocket closes with a 4xxx code; a code in that range this client does not know
# reads as 4010 (the contract's own rule for receivers — never mistaken, in the UI, for a session
# still attachable).
_CLOSE_BANNERS = {
    4010: "session ended: killed (by TSB, or through it)",
    4011: "detached by the server (missed heartbeats, or a send queue that would not drain) — "
          "the session lives on",
    4012: "session ended: terminated (from outside TSB)",
}

_CONNECTION_LOST = "connection to the broker lost"


def banner_for(code: int | None) -> str:
    """What the operator sees for a WS close code. Only 4000-4999 is close-code space the
    contract defines as an end state; anything else — a normal close, an abnormal one (1006), or
    no close frame at all — is never itself an end state (session model §13: a client's
    WebSocket dying ends nothing), so the caller must ask the broker what actually happened
    rather than show one of these words for it."""
    if code is not None and 4000 <= code <= 4999:
        return _CLOSE_BANNERS.get(code, _CLOSE_BANNERS[4010])
    return _CONNECTION_LOST


# The one key this screen reserves for itself rather than forwarding as PTY input. Shown in the
# footer; chosen because it is not a control character or arrow/editing key a shell or an
# interactive program is likely to bind (unlike, say, ctrl+d or escape).
DETACH_KEY = "f9"


class TerminalView(Static):
    """Renders a `TerminalEmulator` fed by the attached byte stream. Its own resize (not the
    screen's: a footer shares the screen) is the terminal's true viewport in character cells,
    because a Textual app already runs inside a real terminal — one layout cell is one column."""

    DEFAULT_CSS = """
    TerminalView {
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__("", markup=False)
        self.emulator: TerminalEmulator | None = None
        self.on_first_size: Callable[[int, int], None] | None = None
        self.on_user_resize: Callable[[int, int], None] | None = None

    def on_resize(self, event: events.Resize) -> None:
        cols, rows = max(event.size.width, 1), max(event.size.height, 1)
        if self.emulator is None:
            self.emulator = TerminalEmulator(cols, rows)
            if self.on_first_size is not None:
                self.on_first_size(cols, rows)
        else:
            self.emulator.resize(cols, rows)
            if self.on_user_resize is not None:
                self.on_user_resize(cols, rows)
        self._redraw()

    def feed(self, data: bytes) -> None:
        if self.emulator is not None:
            self.emulator.feed(data)
            self._redraw()

    def _redraw(self) -> None:
        if self.emulator is not None:
            self.update(self.emulator.render())


class TerminalScreen(Screen):
    # Shown in the footer as a hint; on_key (below) is what actually reserves the key and stops
    # it from being forwarded as PTY input — a Binding's action fires in parallel with on_key in
    # Textual, never instead of it, so the reservation has to be explicit either way.
    BINDINGS: ClassVar[list[Binding]] = [Binding(DETACH_KEY, "detach", "Detach", show=True)]

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self.session_id = session_id
        self.att = None
        self.view = TerminalView()
        self._attach_started = False
        self._leaving = False
        self._worker = None
        # The emulator's current size, kept up to date by every resize (including the very first
        # one) so the attach handshake always sends the size in effect *when it completes*, not
        # the one captured before the ticket mint and the WS upgrade started.
        self._cols = 80
        self._rows = 24

    def compose(self) -> ComposeResult:
        self.view.on_first_size = self._on_first_size
        self.view.on_user_resize = self._on_user_resize
        yield self.view
        yield Footer()

    def _on_first_size(self, cols: int, rows: int) -> None:
        self._cols, self._rows = cols, rows
        if self._attach_started:
            return
        self._attach_started = True
        self._worker = self.run_worker(self._attach_and_pump(), exclusive=True)

    def _on_user_resize(self, cols: int, rows: int) -> None:
        self._cols, self._rows = cols, rows
        if self.att is not None and not self.att.closed:
            self.run_worker(self._send_resize(cols, rows))

    async def _send_resize(self, cols: int, rows: int) -> None:
        try:
            await self.att.resize(cols, rows)
        except ConnectionClosed:
            pass  # the recv loop in _attach_and_pump will notice and show the close banner

    async def _attach_and_pump(self) -> None:
        try:
            att = await self.app.client.async_attach(self.session_id)
        except AttachRefused as exc:
            self._leave(f"attach refused: {exc.reason or 'close ' + str(exc.code)}")
            return
        except ApiError as exc:
            # Every ApiError here — the ticket mint's own call, since async_attach runs it off
            # the loop — gets the same toast every other screen's API errors get, including the
            # 401-routes-to-Login rule (`notify_api_error`): a 401 anywhere sends the operator
            # back to Login, not just the ones that happen to originate from a screen's own
            # `call_api`.
            if self._leaving:
                return
            self._leaving = True
            self.app.notify_api_error(exc)
            if exc.status != 401:
                self._go_to_sessions()
            return
        except (HttpError, OSError, HandshakeError, ProtocolError) as exc:
            self._leave(f"could not attach: {exc}")
            return
        self.att = att
        try:
            await att.resize(self._cols, self._rows)
            while True:
                chunk = await att.recv()
                if chunk is None:
                    break
                self.view.feed(chunk)
        except (ConnectionClosed, ProtocolError):
            # ConnectionClosed: a send or recv racing the peer closing the socket. ProtocolError:
            # the peer broke framing after attach — neither is a crash; both fall through to the
            # same close-code banner / connection-lost path below as an ordinary drop would.
            pass
        finally:
            # The one task that ever reads from `att` closes it — an explicit detach
            # (`_detach_session`, below) cancels this task rather than racing it for the same socket.
            await att.close()
        if not self._leaving:
            code = att.close_code
            if code is not None and 4000 <= code <= 4999:
                self._leave(banner_for(code))
            else:
                await self._leave_connection_lost(code)

    async def _leave_connection_lost(self, code: int | None) -> None:
        """Not a 4xxx close (session model §13: a client's WebSocket dying ends nothing) — ask
        the broker what state the session is actually in rather than guess from the close alone."""
        state = await self._fetch_session_state()
        if state is None:
            return  # a 401 already sent the operator back to Login
        self._leave(f"{banner_for(code)} — the session is {state}; re-attach from Sessions")

    async def _fetch_session_state(self) -> str | None:
        """None here means the caller should not show anything more — a 401 already routed to
        Login (`notify_api_error`, called for its side effect same as everywhere else)."""
        try:
            session = await asyncio.to_thread(self.app.client.session, self.session_id)
        except ApiError as exc:
            self.app.notify_api_error(exc)
            return None if exc.status == 401 else "unknown"
        except (HttpError, OSError):
            return "unknown"
        return session["state"]

    def _go_to_sessions(self) -> None:
        from .sessions import SessionsScreen

        self.app.switch_screen(SessionsScreen())

    def _leave(self, message: str) -> None:
        if self._leaving:
            return
        self._leaving = True
        self.app.notify(message, title="session", severity="warning", markup=False)
        self._go_to_sessions()

    async def on_key(self, event: events.Key) -> None:
        if event.key == DETACH_KEY:
            event.stop()
            self._detach_session()
            return
        if self.att is None or self.att.closed:
            return
        application_cursor_keys = (
            self.view.emulator.application_cursor_keys if self.view.emulator is not None else False
        )
        data = _encode_key(event, application_cursor_keys=application_cursor_keys)
        if data is not None:
            event.stop()
            try:
                await self.att.send(data)
            except ConnectionClosed:
                pass  # the recv loop in _attach_and_pump will notice and show the close banner

    async def on_paste(self, event: events.Paste) -> None:
        if self.att is not None and not self.att.closed and event.text:
            event.stop()
            data = event.text.encode("utf-8")
            if self.view.emulator is not None and self.view.emulator.bracketed_paste:
                data = b"\x1b[200~" + data + b"\x1b[201~"
            try:
                await self.att.send(data)
            except ConnectionClosed:
                pass

    def action_detach(self) -> None:
        """Bound to F9 (BINDINGS above, for the footer hint); on_key also calls this directly,
        since a Binding's action never suppresses delivery to on_key (see the BINDINGS comment)."""
        self._detach_session()

    def _detach_session(self) -> None:
        # _attach_and_pump is the one task that ever reads self.att; leave first (this sets
        # _leaving, so its own close banner is skipped), then cancel it so its `finally` closes
        # the socket without a second, concurrent reader racing it.
        self._leave("detached — the session lives on")
        if self._worker is not None:
            self._worker.cancel()


def _encode_key(event: events.Key, *, application_cursor_keys: bool = False) -> bytes | None:
    """Textual key -> the bytes a real terminal would have sent."""
    if event.key.startswith("alt+") and event.character is not None:
        # Textual folds Alt+<letter/digit> into "alt+<char>" but keeps the bare character in
        # `.character`; the Escape prefix a real terminal sends for Alt is not otherwise
        # recoverable from the key event (and is lost entirely for punctuation whose "friendly"
        # name is more than one character — Textual's own parser drops the alt marker there).
        return b"\x1b" + event.character.encode("utf-8")
    if event.is_printable and event.character is not None:
        return event.character.encode("utf-8")
    fixed = _KEY_BYTES.get(event.key)
    if fixed is not None:
        return fixed
    letter = _ARROW_LETTERS.get(event.key)
    if letter is not None:
        # DECCKM: a plain, unmodified arrow is the one case a real terminal encodes differently
        # for it; pyte tracks whether the target asked for that.
        return (b"\x1bO" if application_cursor_keys else b"\x1b[") + letter
    return _modified_arrow(event.key)


_ARROW_LETTERS: dict[str, bytes] = {"up": b"A", "down": b"B", "right": b"C", "left": b"D"}
_ARROW_MODIFIER_WEIGHTS = {"shift": 1, "alt": 2, "ctrl": 4}


def _modified_arrow(key: str) -> bytes | None:
    """A Shift/Alt/Ctrl arrow (any combination) as the xterm modifier-CSI form a real terminal
    sends for one — unlike a plain arrow, this ignores DECCKM: xterm's own `ESC O` application
    form has no room for a modifier parameter, so it always falls back to the `CSI` form."""
    *mods, base = key.split("+")
    letter = _ARROW_LETTERS.get(base)
    if letter is None or not mods or not set(mods) <= _ARROW_MODIFIER_WEIGHTS.keys():
        return None
    code = 1 + sum(_ARROW_MODIFIER_WEIGHTS[m] for m in mods)
    return b"\x1b[1;" + str(code).encode("ascii") + letter


_KEY_BYTES: dict[str, bytes] = {
    "enter": b"\r",
    "escape": b"\x1b",
    "tab": b"\t",
    "shift+tab": b"\x1b[Z",
    "backspace": b"\x7f",
    "delete": b"\x1b[3~",
    "insert": b"\x1b[2~",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "pageup": b"\x1b[5~",
    "pagedown": b"\x1b[6~",
    "f1": b"\x1bOP",
    "f2": b"\x1bOQ",
    "f3": b"\x1bOR",
    "f4": b"\x1bOS",
    "f5": b"\x1b[15~",
    "f6": b"\x1b[17~",
    "f7": b"\x1b[18~",
    "f8": b"\x1b[19~",
    "f10": b"\x1b[21~",
    "f11": b"\x1b[23~",
    "f12": b"\x1b[24~",
    # A real terminal driver sends these for Ctrl-Left/Right; xterm's own CSI-modifier form
    # (which _modified_arrow would otherwise compute) is not what readline/emacs-mode shells and
    # network-gear CLIs bind for word-back/word-forward.
    "ctrl+left": b"\x1bb",
    "ctrl+right": b"\x1bf",
}
# ctrl+a .. ctrl+z as the control bytes a real terminal driver would have sent (0x01-0x1a);
# f9 is deliberately excluded above (DETACH_KEY) and handled before this table is ever consulted.
for _i, _letter in enumerate("abcdefghijklmnopqrstuvwxyz", start=1):
    _KEY_BYTES[f"ctrl+{_letter}"] = bytes([_i])
_KEY_BYTES["ctrl+backslash"] = b"\x1c"
_KEY_BYTES["ctrl+underscore"] = b"\x1f"
# Ctrl-^ (IOS's Ctrl-Shift-6 abort for ping/traceroute/DNS lookups), Ctrl-] (the telnet escape
# character) and Ctrl-@ (NUL) — real, plain control bytes a shell or a target may bind, dropped
# until Textual's own key names for them were added here.
_KEY_BYTES["ctrl+circumflex_accent"] = b"\x1e"
_KEY_BYTES["ctrl+right_square_bracket"] = b"\x1d"
_KEY_BYTES["ctrl+@"] = b"\x00"
