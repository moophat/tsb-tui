"""drive's commands (design §2.9.4, D48): one JSON object in, one answer out.

Each command runs the same action a person's click or key would: `attach` is the sidebar row, the gate and its Attach
button; `type` and `keys` leave through `TerminalScreen.send_input`, the one way bytes leave the screen, so the guard
and DT4 apply to an agent exactly as they do to a hand; `hand`, `hand_back` and `size` ask the action table first
(`model/actions.py`), as the buttons of Who's here and Size do. Reads are made in this client's own grid, never the
window and never a core endpoint (honesty rule 2). drive acts for the agent on nothing by itself: it sends no size
request, answers no stale choice or quiet prompt and moves no keyboard unless a command says so (D53).

The codes and sentences of the answers are the guide's (`GUIDE.md` § Errors). Where the guide has none for a case, the
case is named in the build report and the code is the closest the guide has.
"""

from __future__ import annotations

import asyncio
import functools
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tsb_contract_client import ApiError

from .._transport import HttpError
from ..model import actions as A
from ..model.attachment import AttachmentState
from ..model.countdown import format_duration, parse_timestamp
from ..model.fleet import is_attachable
from ..model.targets import PAGE_LIMIT, QueryError, as_text, make_row, parse_query
from ..screens.new_session import CREATE_SIZE_OFF, TERM, create_size
from ..screens.terminal import MARK_INPUT, SOURCE_KEY, SessionSlot, TerminalScreen
from ..term import UnknownKey, VTBackend, encode_key
from . import waits

if TYPE_CHECKING:
    from .runner import Driver

# Every op, in the order the guide lists them.
OPS = ("login", "sessions", "targets", "new", "attach", "switch", "read", "type", "keys", "wait", "hand_back", "hand", "ask_to_type",
       "answer", "size", "detach", "snapshot", "click", "press", "resize", "quit")
READS = ("screen", "reply", "history", "who")

ASK_TO_TYPE_WHY = ("TSB has no way to ask for the keyboard yet; ask the owner another way, then wait for "
                   "holder me")
HOSTKEY_PENDING = ("the host key is seen for the first time: check it out of band, then tick it in the gate "
                   "(press space, then enter); the session is created")


class DriveError(Exception):
    """A command that did not do what it said: `code` is the answer's `error`, `why` its plain words, and `extra`
    the other fields the agent needs (`sent`, `options`, `tail` ...)."""

    def __init__(self, code: str, why: str, **extra: Any) -> None:
        super().__init__(f"{code}: {why}")
        self.code = code
        self.why = why
        self.extra = extra


PASSWORD_MAX = 1024  # bytes: the longest password a password_file may hold


def read_password_file(path: str) -> str:
    """The password in a file: its first line, without the line end, at most `PASSWORD_MAX` bytes. The path may name
    a regular file or a pipe (`/dev/fd/N`, what a process substitution is), so nothing need rest on disk; a device or
    anything else is refused before it is opened (`/dev/zero` has no first line to end). At most one byte more than
    the limit is read. A regular file that is group- or other-readable is warned about on stderr, as the smoke path
    does; an absent, empty or over-long file is an error whose words never carry what was read. Never argv, never the
    environment (honesty rule 5)."""
    file = Path(path)
    try:
        mode = file.stat().st_mode
    except OSError:
        mode = None
    if mode is None or stat.S_ISDIR(mode):
        raise DriveError("bad_request", f"password_file {path} does not name a file")
    if not (stat.S_ISREG(mode) or stat.S_ISFIFO(mode)):
        raise DriveError("bad_request", f"password_file {path} is not a regular file or a pipe")
    if stat.S_ISREG(mode) and mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(f"WARNING: {path} is group- or other-readable; it should be owner-only", file=sys.stderr)
    try:
        with file.open("rb") as stream:
            data = stream.read(PASSWORD_MAX + 1)
    except OSError as exc:
        raise DriveError("bad_request", f"password_file {path} could not be read: {exc.strerror}") from None
    line = data.split(b"\n", 1)[0].rstrip(b"\r")
    if len(line) > PASSWORD_MAX:
        raise DriveError("bad_request", f"password_file {path} holds a password longer than {PASSWORD_MAX} bytes")
    try:
        value = line.decode("utf-8")
    except UnicodeDecodeError:
        raise DriveError("bad_request", f"password_file {path} is not UTF-8 text") from None
    if not value:
        raise DriveError("bad_request", f"password_file {path} is empty")
    return value


def _field(cmd: dict[str, Any], name: str, kind: type, *, required: bool = True) -> Any:
    value = cmd.get(name)
    if value is None:
        if required:
            raise DriveError("bad_request", f"{name}: required")
        return None
    if isinstance(value, bool) or not isinstance(value, kind):
        raise DriveError("bad_request", f"{name}: expected {kind.__name__}")
    return value


def parse_size(text: str) -> tuple[int, int]:
    """`120x40` as (120, 40); ValueError when it is not two positive whole numbers."""
    cols, sep, rows = text.lower().partition("x")
    if not sep or not cols.isdigit() or not rows.isdigit() or int(cols) < 1 or int(rows) < 1:
        raise ValueError(f"expected COLSxROWS such as 120x40, got {text!r}")
    return int(cols), int(rows)


# -- what a read sees of one session --


def alt_screen(backend: VTBackend) -> bool:
    return backend.modes().alternate_screen


def screen_lines(backend: VTBackend, *, join_wrapped: bool = False) -> list[str]:
    """What is on the screen now, from the grid at the session's full size."""
    return backend.visible_rows(join_wrapped=join_wrapped)


def trimmed(lines: list[str]) -> list[str]:
    """Without the blank rows at the bottom."""
    out = list(lines)
    while out and not out[-1].strip():
        out.pop()
    return out


def reply_lines(backend: VTBackend, cap: int, mark: str, *, join_wrapped: bool = False) -> tuple[list[str], bool]:
    """The text since this client's last input here, scrollback included, and whether `cap` cut it. With no input yet
    it is everything this client has received. On the alternate screen there is no scrollback: the screen."""
    if alt_screen(backend):
        lines = trimmed(screen_lines(backend, join_wrapped=join_wrapped))
        return lines[:cap], len(lines) > cap
    if backend.has_mark(mark):
        lines = backend.text_since(mark, cap=cap + 1, join_wrapped=join_wrapped)
    else:
        lines = backend.grid_lines(join_wrapped=join_wrapped)[:cap + 1]
    return lines[:cap], len(lines) > cap


class SlotProbe:
    """What a wait reads of one session open here: its state, its grid, its record. Nothing is asked of the core."""

    def __init__(self, screen: TerminalScreen, slot: SessionSlot, *, reply_cap: int) -> None:
        self._screen = screen
        self._slot = slot
        self._cap = reply_cap

    @property
    def state(self) -> AttachmentState:
        assert self._slot.pump is not None
        return self._slot.pump.state

    def holder(self) -> str:
        state = self.state
        if state.holds_keyboard:
            return "me"
        return "nobody" if state.holder is None else "other"

    def record_state(self) -> str | None:
        return self._screen.live_records().get(self._slot.session_id, {}).get("state")

    def closed_as(self) -> str | None:
        close = self.state.close
        if not self.state.closed or close is None:
            return None
        if close.show in ("ended_killed", "ended_terminated"):
            return "ended"
        return "removed" if close.show == "removed" else None

    def cursor_row(self) -> str:
        backend = self._slot.canvas.backend
        backend.sync_render_state()
        row = backend.cursor().y
        rows = screen_lines(backend)
        return rows[row] if 0 <= row < len(rows) else ""

    def screen_text(self) -> str:
        return "\n".join(screen_lines(self._slot.canvas.backend))

    def reply_text(self) -> str:
        lines, _cut = reply_lines(self._slot.canvas.backend, self._cap, MARK_INPUT)
        return "\n".join(lines)

    def last_output_at(self) -> float | None:
        return self.state.last_output_at

    def now(self) -> float:
        return self.state.now()

    def tail(self) -> list[str]:
        return trimmed(screen_lines(self._slot.canvas.backend))[-waits.TAIL_LINES:]


# -- the commands --


@dataclass
class Fronted:
    """Which session was in front before a command brought another one there."""

    switched_from: str | None = None


class Commands:
    """The ops, as methods named `op_<name>`. Each takes the command object and returns the answer's fields, or raises
    `DriveError`."""

    def __init__(self, driver: Driver) -> None:
        self.d = driver
        # A session whose create answered with a first-use host key: its gate stays up, and a later `attach` opens
        # it with this fingerprint rather than a gate that would let Attach through unticked.
        self._pending_hostkeys: dict[str, dict[str, Any]] = {}

    async def run(self, cmd: dict[str, Any]) -> dict[str, Any]:
        op = cmd.get("op")
        if op not in OPS:
            raise DriveError("bad_request", f"op: expected one of {', '.join(OPS)}")
        return await getattr(self, f"op_{op}")(cmd)

    # -- helpers --

    def _screen(self) -> TerminalScreen:
        screen = self.d.screen()
        if screen is None:
            raise DriveError("not_logged_in", "log in first: the login command, or --user and --password-file")
        return screen

    def _slot(self, cmd: dict[str, Any], *, attached: bool = True) -> SessionSlot:
        """The session a command is addressed to: `session`, else the one in front."""
        screen = self._screen()
        sid = _field(cmd, "session", str, required=False)
        if sid is None:
            if screen.front is None:
                raise DriveError("not_attached", "no session is attached: attach first")
            slot = screen.front
        else:
            slot = screen.slot_for(sid)
            if slot is None:
                known = sid in screen.fleet.records if screen.fleet is not None else False
                raise DriveError("not_attached" if known else "no_such_session",
                                 "attach first" if known else "not a session you may see", session=sid)
        if attached and (slot.pump is None or slot.pump.state.client_id is None):
            raise DriveError("not_attached", "the session is still attaching", session=slot.session_id)
        return slot

    def _to_front(self, slot: SessionSlot) -> Fronted:
        """An action on another session brings it to the front first, as a click on its chip would (D49)."""
        screen = self._screen()
        before = screen.front
        if self.d.cfg["drive.switch_on_action"] and slot is not before:
            screen.show_front(slot)
            return Fronted(before.session_id if before is not None else None)
        return Fronted()

    @staticmethod
    def _holder_user(state: AttachmentState) -> str | None:
        return None if state.holder is None else state.holder.get("user")

    # -- login, sessions, attach, switch --

    async def op_login(self, cmd: dict[str, Any]) -> dict[str, Any]:
        if "password" in cmd:
            raise DriveError("bad_request", "a password is never given in a command: use password_file")
        user = _field(cmd, "user", str)
        path = _field(cmd, "password_file", str)
        if path == "-":
            raise DriveError("bad_request", "password_file: - is for the command line; stdin carries commands here")
        if self.d.screen() is not None:
            raise DriveError("bad_request", f"already logged in as {self.d.app.username}; one driver is one login")
        return {"principal": await self.d.log_in(user, read_password_file(path))}

    async def op_sessions(self, cmd: dict[str, Any]) -> dict[str, Any]:
        screen = self._screen()
        await self.d.refresh_fleet()
        now = self.d.app.wall_clock()
        rows = []
        for record in screen.listed_sessions():
            row: dict[str, Any] = {
                "session": record["id"], "target": record.get("target_id"), "state": record.get("state"),
                "owner": record.get("owner"), "holder": (record.get("control_holder") or {}).get("user"),
                "rec": bool((record.get("recording") or {}).get("active"))}
            ends = parse_timestamp(record.get("ends_at"))
            if ends is not None and record.get("state") not in ("killed", "terminated"):
                row["ends_in"] = format_duration(ends.timestamp() - now)
            rows.append(row)
        return {"sessions": rows}

    async def op_targets(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """The rows the Targets tab shows for a query: one page of what `GET /targets` answers, every string through
        `as_text`. `more` is true when the broker has a next page this command does not fetch."""
        self._screen()
        text = _field(cmd, "query", str, required=False) or ""
        try:
            query = parse_query(text)
        except QueryError as exc:
            raise DriveError("bad_request", f"query: {exc}") from None
        call = functools.partial(self.d.app.client.list_targets, **query.filters(), limit=PAGE_LIMIT)
        try:
            page = await asyncio.to_thread(call)
        except (ApiError, HttpError, OSError) as exc:
            raise DriveError("request_failed", str(exc)) from None
        rows = [make_row(record) for record in page.get("targets") or ()]
        answer: dict[str, Any] = {"targets": [
            {"id": row.label, "type": row.type, "name": row.name, "group": row.group, "tags": list(row.tags),
             "discovered_by": row.discovered_by} for row in rows]}
        if page.get("next_cursor"):
            answer["more"] = True
        return answer

    async def _supplied_login(self, cmd: dict[str, Any]) -> dict[str, Any] | None:
        """The `login` of a `new` that supplies one (login-at-connect, rulings A11 and 15:36 (6)), or None for a `new`
        that does not. The password has two doors, `password_from: "stdin"` (the next stdin line, raw) and
        `password_file` (a path, `/dev/fd/N` among them); `password` in the command is refused. Where the command asks
        for the next stdin line, that line is taken before anything else about the command is judged, so a refusal
        leaves the stream in step and the password is never read as a command (nor written to the transcript). It is
        never kept: the returned value is the call's argument and nothing here holds it."""
        source = cmd.get("password_from")
        from_stdin = None
        if source == "stdin" and not self.d.script:
            from_stdin = await self.d.take_stdin_line()
        if "password" in cmd:
            raise DriveError("bad_request",
                             "a password is never given in a command: use password_from or password_file")
        if source is not None and source != "stdin":
            raise DriveError("bad_request", 'password_from: expected "stdin"')
        if source == "stdin" and self.d.script:
            raise DriveError("bad_request", "password_from: stdin is refused in --script mode: the script would hold "
                                            "the password and stdin carries no commands to take it from")
        user = _field(cmd, "login_user", str, required=False)
        path = _field(cmd, "password_file", str, required=False)
        acknowledged = cmd.get("recording_acknowledged")
        if acknowledged is not None and not isinstance(acknowledged, bool):
            raise DriveError("bad_request", "recording_acknowledged: expected bool")
        if user is None and path is None and source is None:
            if acknowledged is not None:
                raise DriveError("bad_request", "recording_acknowledged: it goes with a login (login_user)")
            return None
        if source is not None and path is not None:
            raise DriveError("bad_request", "give one password source, password_from or password_file, not both")
        if not user:
            raise DriveError("bad_request", "login_user: required, and not empty, with a password source")
        if source is None and path is None:
            raise DriveError("bad_request", "login_user needs a password source: password_from or password_file")
        if source == "stdin":
            if from_stdin is None:
                raise DriveError("bad_request", "password_from: stdin ended before the password line")
            password = from_stdin.rstrip("\n").rstrip("\r")
            if not password:
                raise DriveError("bad_request", "password_from: the password line is empty")
        else:
            password = await asyncio.to_thread(read_password_file, path)  # type: ignore[arg-type]
        login: dict[str, Any] = {"user": user, "auth": {"kind": "password", "password": password}}
        if acknowledged:
            login["recording_acknowledged"] = True
        return login

    async def op_new(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """New session on a target, as the dialog's Create does (`connection: new`, which is all a telnet target
        allows), then attached as `attach` does: the gate's text first, the gate passed, the answer `attach`'s. A
        refused create answers with the core's own code and message. With `login_user` and a password source the create
        carries that login for this one connection (`_supplied_login`); the core's refusal of it, the consent it asks
        for among them (`login_recording_not_acknowledged`), is passed on as it is, never answered for the agent."""
        from ..screens.attach_confirm import AttachConfirmScreen

        login = await self._supplied_login(cmd)
        target = _field(cmd, "target", str)
        screen = self._screen()
        await self.d.workspace_ready(screen)
        size = create_size(self.d.app)
        if size is None:
            raise DriveError("not_available", CREATE_SIZE_OFF)
        call = functools.partial(self.d.app.client.create_session, target, "new", TERM, size[0], size[1],
                                 login=login)
        try:
            created = await asyncio.to_thread(call)
        except ApiError as exc:
            raise DriveError(exc.code, exc.message, target=target) from None
        except (HttpError, OSError) as exc:
            raise DriveError("request_failed", str(exc)) from None
        record = created["session"]
        sid = record["id"]
        recording = record.get("recording") or {}
        if recording.get("active"):
            self.d.emit_event({"ev": "gate", "session": sid, "recording": recording.get("warning_text")})
        hostkey = created.get("hostkey")
        screen.open_session(record, hostkey)
        if hostkey is not None:
            # A first-use host key is the operator's to check out of band (session-open step 22): the gate stays up
            # for a tick, and drive ticks nothing by itself.
            self._pending_hostkeys[sid] = hostkey
            raise DriveError("not_available", HOSTKEY_PENDING, session=sid,
                             fingerprint=as_text(hostkey.get("fingerprint")))
        await self.d.pass_gate(sid, AttachConfirmScreen)
        return await self._attached_answer(screen, sid)

    async def op_attach(self, cmd: dict[str, Any]) -> dict[str, Any]:
        from ..screens.attach_confirm import AttachConfirmScreen

        sid = _field(cmd, "session", str)
        screen = self._screen()
        slot = screen.slot_for(sid)
        if slot is None:
            # The workspace must be mounted before its fleet is read: after a re-login the login's `switch_screen`
            # puts the TerminalScreen on the stack before its `on_mount` builds the fleet, so drive's next attach
            # can land here with `screen.fleet` None. The same readiness the login's own wait gives on a first
            # start applies after a re-login (drive/runner.py:workspace_ready).
            await self.d.workspace_ready(screen)
            record = screen.fleet.records.get(sid)
            if record is None:
                await self.d.refresh_fleet()
                record = screen.fleet.records.get(sid)
            if record is None:
                raise DriveError("no_such_session", "not a session you may see", session=sid)
            state = record.get("state")
            if state in ("killed", "terminated"):
                raise DriveError("ended", f"the session ended ({state})", session=sid)
            if not is_attachable(state):
                raise DriveError("not_available", f"the session is {state}: it cannot be attached", session=sid)
            recording = record.get("recording") or {}
            if recording.get("active"):
                # CR1: the recording text comes before the attach, whether or not the gate is shown this time.
                self.d.emit_event({"ev": "gate", "session": sid, "recording": recording.get("warning_text")})
            pending = self._pending_hostkeys.get(sid)
            if pending is not None and sid in self.d.app.gated_sessions:
                # The operator accepted this session's first-use gate, so it is not asked again (the ruling of
                # 2026-10-22): the fingerprint is dropped as soon as the gate is accepted and the session attached,
                # whichever path attached it, and a later detach and attach of the same session shows no gate.
                self._pending_hostkeys.pop(sid)
                pending = None
            if pending is not None:
                # The create saw this session's host key first: its gate is the one to use, and Attach stays greyed
                # until the operator ticks it — exactly as the TUI's own attach does.
                self._open_pending_hostkey(screen, record, pending)
                raise DriveError("not_available", HOSTKEY_PENDING, session=sid,
                                 fingerprint=as_text(pending.get("fingerprint")))
            screen.open_session(record)
            await self.d.pass_gate(sid, AttachConfirmScreen)
            slot = screen.slot_for(sid)
        else:
            self._pending_hostkeys.pop(sid, None)
            screen.show_front(slot)
        return await self._attached_answer(screen, sid)

    def _open_pending_hostkey(self, screen: TerminalScreen, record: dict, hostkey: dict) -> None:
        """Open the host-key gate for a session whose create saw the key first, unless it is already up."""
        from ..screens.attach_confirm import AttachConfirmScreen

        current = self.d.app.screen
        if isinstance(current, AttachConfirmScreen) and current.is_mounted and current.session_id == record["id"]:
            return
        screen.open_session(record, hostkey)

    async def _attached_answer(self, screen: TerminalScreen, sid: str) -> dict[str, Any]:
        """Wait for the session to be attached, bring it forward and say how it stands: `attach`'s answer."""
        slot = await self.d.attached(sid)
        screen.show_front(slot)
        state = slot.pump.state
        you_may_type = state.may_control(state.client_id)
        return {"session": sid, "chip": screen.chip_number(sid), "front": screen.front is slot,
                "you": "type" if state.holds_keyboard else "view_only", "holder": self._holder_user(state),
                "you_may_type": you_may_type, "size": f"{state.size_cols}x{state.size_rows}"}

    async def op_switch(self, cmd: dict[str, Any]) -> dict[str, Any]:
        slot = self._slot(cmd, attached=False)
        self._screen().show_front(slot)
        return {"front": slot.session_id}

    # -- reads --

    async def op_read(self, cmd: dict[str, Any]) -> dict[str, Any]:
        what = cmd.get("what")
        if what not in READS:
            raise DriveError("bad_request", f"what: expected one of {', '.join(READS)}")
        join = cmd.get("join_wrapped", False)
        if not isinstance(join, bool):
            raise DriveError("bad_request", "join_wrapped: expected true or false")
        slot = self._slot(cmd)
        backend = slot.canvas.backend
        state = slot.pump.state
        cap = self.d.cfg["drive.reply_max_lines"]
        if what == "who":
            return self._who(state)
        if what == "reply":
            lines, cut = reply_lines(backend, cap, MARK_INPUT, join_wrapped=join)
            return {"lines": lines, "alt": alt_screen(backend), "truncated": cut}
        if what == "history":
            asked = cmd.get("lines")
            if asked is not None and (isinstance(asked, bool) or not isinstance(asked, int) or asked < 1):
                raise DriveError("bad_request", "lines: expected a positive number")
            lines = backend.grid_lines(join_wrapped=join)[-min(asked or cap, cap):]
            return {"lines": lines, "alt": alt_screen(backend)}
        backend.sync_render_state()
        cursor = backend.cursor()
        modes = backend.modes()
        return {"lines": screen_lines(backend, join_wrapped=join), "cursor": [cursor.y, cursor.x],
                "cols": backend.cols, "rows": backend.rows, "alt": modes.alternate_screen,
                "modes": {"cursor_keys_application": modes.cursor_keys_application,
                          "keypad_application": modes.keypad_application, "bracketed_paste": modes.bracketed_paste,
                          "mouse_tracking": modes.mouse_tracking, "focus_events": modes.focus_events,
                          "kitty_flags": modes.kitty_flags, "synchronized_output": modes.synchronized_output}}

    @staticmethod
    def _who(state: AttachmentState) -> dict[str, Any]:
        holder = state.holder
        return {"holder": None if holder is None else holder.get("user"),
                "holder_client": None if holder is None else holder.get("client_id"),
                "owner": state.owner, "you": state.client_id,
                "clients": [{"user": c.get("user"), "client_id": c.get("client_id"),
                             "may_control": bool(c.get("may_control"))} for c in state.clients],
                "you_may_type": state.may_control(state.client_id)}

    # -- input --

    async def _send(self, slot: SessionSlot, data: bytes) -> dict[str, Any]:
        """Bytes to the session through the one input path. The guard (DT4 a) answers `not_typist` and sends nothing;
        what the core refuses comes back as an `input_refused` event, since TSB sends no acknowledgement."""
        screen = self._screen()
        fronted = self._to_front(slot)
        state = slot.pump.state
        if await screen.send_input(SOURCE_KEY, data, slot):
            out: dict[str, Any] = {"front": screen.front.session_id if screen.front else None, "queued": len(data)}
            if fronted.switched_from is not None:
                out["switched_from"] = fronted.switched_from
            return out
        if state.closed:
            raise DriveError("not_attached", "the session is closed; nothing was sent", sent=False)
        if not state.may_type and self.d.cfg["input.guard"]:
            raise DriveError("not_typist", "you do not hold the keyboard; nothing was sent",
                             holder=self._holder_user(state), sent=False)
        raise DriveError("not_attached", "the session is not ready for input; nothing was sent", sent=False)

    async def op_type(self, cmd: dict[str, Any]) -> dict[str, Any]:
        text = _field(cmd, "text", str)
        if not text:
            raise DriveError("bad_request", "text: expected at least one character")
        return await self._send(self._slot(cmd), text.encode("utf-8"))

    async def op_keys(self, cmd: dict[str, Any]) -> dict[str, Any]:
        names = cmd.get("keys")
        if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
            raise DriveError("bad_request", "keys: expected a list of key names such as [\"ctrl+c\"]")
        slot = self._slot(cmd)
        data = b""
        for name in names:
            try:
                data += encode_key(slot.canvas.backend, name)
            except UnknownKey:
                raise DriveError("unknown_key", f"{name!r} is not a key name drive knows; nothing was sent",
                                 key=name, sent=False) from None
        return await self._send(slot, data)

    # -- waits --

    async def op_wait(self, cmd: dict[str, Any]) -> dict[str, Any]:
        cfg = self.d.cfg
        try:
            until = waits.parse_until(cmd.get("until"), default_settled_ms=cfg["drive.settled_ms"])
            timeout_ms = waits.timeout_for(cmd.get("timeout_ms"), default_ms=cfg["drive.wait_timeout_ms"],
                                           max_ms=cfg["drive.wait_timeout_max_ms"])
        except waits.BadWait as exc:
            raise DriveError("bad_request", str(exc)) from None
        slot = self._slot(cmd)
        done, elapsed_ms = await self.d.run_wait(slot, until, timeout_ms)
        if done.ok:
            out: dict[str, Any] = {"elapsed_ms": elapsed_ms}
            if done.matched:
                out["matched"] = done.matched
            return out
        raise DriveError(done.error or "timeout", done.why or "", elapsed_ms=elapsed_ms,
                         tail=self.d.probe(slot).tail(), **(done.extra or {}))

    # -- the keyboard --

    def _owner_clients(self, state: AttachmentState) -> list[dict[str, Any]]:
        return [c for c in state.clients if c.get("user") == state.owner and c.get("client_id") != state.client_id]

    async def _hand_to(self, slot: SessionSlot, client_id: str) -> dict[str, Any]:
        """`handoff` through the pump, after the action table has allowed it, and the answer once the core has said:
        the `token` frame that moved it, or `request_refused`."""
        screen = self._screen()
        state = slot.pump.state
        if not state.may_type:
            raise DriveError("not_typist", "only the keyboard holder or the owner can hand it over")
        why = A.blocked("hand", A.Facts(state, selected=client_id, open_sessions=len(screen.slots)))
        if why is not None:
            raise DriveError("not_available", why)
        return await self.d.hand_off(slot, client_id)

    async def op_hand_back(self, cmd: dict[str, Any]) -> dict[str, Any]:
        slot = self._slot(cmd)
        state = slot.pump.state
        self._to_front(slot)
        if not state.may_type:
            raise DriveError("not_typist", "only the keyboard holder or the owner can hand it over")
        owners = self._owner_clients(state)
        to = _field(cmd, "to", str, required=False)
        if to is not None:
            owners = [c for c in owners if to in (c.get("client_id"), c.get("user"))]
        if not owners:
            who = state.owner
            if to is not None:
                raise DriveError("bad_request", f"to: {to!r} is none of {who}'s clients attached here")
            raise DriveError(
                "owner_not_attached", f"{who} has no client here, and only an owner may put the keyboard aside",
                options=["detach: the keyboard passes to another client that may type, or to escrow",
                         f"keep: stay the holder; {who} takes it back by typing"])
        if len(owners) > 1:
            raise DriveError("choose_client", f"{state.owner} has several clients here; name one in to",
                             clients=[c.get("client_id") for c in owners])
        return await self._hand_to(slot, owners[0]["client_id"])

    async def op_hand(self, cmd: dict[str, Any]) -> dict[str, Any]:
        slot = self._slot(cmd)
        state = slot.pump.state
        to = _field(cmd, "to", str)
        self._to_front(slot)
        matches = [c for c in state.clients if c.get("client_id") != state.client_id
                   and to in (c.get("client_id"), c.get("user"))]
        if not matches:
            raise DriveError("no_such_client", f"{to!r} is no other client attached here")
        if len(matches) > 1:
            raise DriveError("choose_client", f"{to} has several clients here; name one by client id",
                             clients=[c.get("client_id") for c in matches])
        return await self._hand_to(slot, matches[0]["client_id"])

    async def op_ask_to_type(self, cmd: dict[str, Any]) -> dict[str, Any]:
        raise DriveError("not_available", ASK_TO_TYPE_WHY)

    async def op_answer(self, cmd: dict[str, Any]) -> dict[str, Any]:
        choice = cmd.get("choice")
        if isinstance(choice, bool) or not isinstance(choice, int) or not 1 <= choice <= 4:
            raise DriveError("bad_request", "choice: expected a number from 1 to 4")
        slot = self._slot(cmd)
        state = slot.pump.state
        screen = self._screen()
        self._to_front(slot)
        if not state.holds_keyboard:
            raise DriveError("not_typist", "only the keyboard holder is asked, and only it can answer")
        offered = [c["choice"] for c in (state.stale_prompt or {}).get("choices", [])][:4]
        if not screen.stale_active() or choice > len(offered):
            raise DriveError("not_available", "no stale choice is waiting for this client"
                             if not screen.stale_active() else f"only {len(offered)} choices are offered")
        await screen.answer_stale(offered[choice - 1])
        return {"choice": offered[choice - 1]}

    async def op_size(self, cmd: dict[str, Any]) -> dict[str, Any]:
        slot = self._slot(cmd)
        screen = self._screen()
        state = slot.pump.state
        self._to_front(slot)
        if not state.holds_keyboard:
            who = self._holder_user(state)
            raise DriveError("not_typist", f"only the typist can ask; {who or 'nobody'} types now")
        before = state.last_size_answer
        asked = screen.fit_size()
        why = await screen.ask_size()
        if why is not None:
            raise DriveError("not_available", why)
        answer = await self.d.condition(lambda: state.last_size_answer is not before and state.last_size_answer,
                                        slot.session_id, "the size answer")
        return {"asked": f"{asked[0]}x{asked[1]}", "size": f"{answer['cols']}x{answer['rows']}",
                "reason": answer["reason"]}

    async def op_detach(self, cmd: dict[str, Any]) -> dict[str, Any]:
        slot = self._slot(cmd, attached=False)
        self._to_front(slot)
        self._screen().action_detach()
        return {}

    # -- the UI itself --

    async def op_snapshot(self, cmd: dict[str, Any]) -> dict[str, Any]:
        kind = cmd.get("format", "text")
        if kind not in ("text", "svg"):
            raise DriveError("bad_request", "format: expected text or svg")
        if kind == "svg":
            return {"format": "svg", "svg": self.d.app.export_screenshot()}
        return {"format": "text", "text": self.d.screen_text()}

    async def op_click(self, cmd: dict[str, Any]) -> dict[str, Any]:
        label = _field(cmd, "label", str)
        if not label.strip():
            # An empty label would match the first control on screen and click it: a wrong action the agent cannot
            # see (it has no eyes). Refuse it as a usage error before any click.
            raise DriveError("bad_request", "label: expected a non-empty label")
        if not self.d.cfg["mouse.enabled"]:
            # The chrome ignores a click while `mouse.enabled` is off (D13): the drive profile turns it off, and a
            # click that did nothing and answered ok would be a lie.
            raise DriveError("not_available", "mouse.enabled is off in this profile, so clicks do nothing; "
                             "use press, or start with --set mouse.enabled=true")
        return await self.d.click_label(label)

    async def op_press(self, cmd: dict[str, Any]) -> dict[str, Any]:
        names = cmd.get("keys")
        if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
            raise DriveError("bad_request", "keys: expected a list of Textual key names such as [\"ctrl+backslash\"]")
        await self.d.pilot.press(*names)
        await self.d.pilot.pause()
        return {}

    async def op_resize(self, cmd: dict[str, Any]) -> dict[str, Any]:
        if "size" in cmd:
            try:
                cols, rows = parse_size(_field(cmd, "size", str))
            except ValueError as exc:
                raise DriveError("bad_request", f"size: {exc}") from None
        else:
            cols, rows = _field(cmd, "cols", int), _field(cmd, "rows", int)
            if cols < 1 or rows < 1:
                raise DriveError("bad_request", "cols and rows: expected positive numbers")
        await self.d.pilot.resize_terminal(cols, rows)
        await self.d.pilot.pause()
        return {"size": f"{cols}x{rows}"}

    async def op_quit(self, cmd: dict[str, Any]) -> dict[str, Any]:
        screen = self.d.screen()
        if screen is not None:
            await screen.run_action_id("quit")
        else:
            self.d.app.exit()
        self.d.quitting = True
        return {}

