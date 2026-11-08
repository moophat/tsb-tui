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
import os
import select
import stat
import sys
import time
from dataclasses import dataclass, replace
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
from ..screens.terminal import (
    _DRIVE_GATE,
    MARK_INPUT,
    SOURCE_KEY,
    SessionSlot,
    TerminalScreen,
    end_refused_text,
)
from ..term import UnknownKey, VTBackend, encode_key
from ..term.ghostty import MODS_SHIFT
from ..term.keys import _parse
from . import waits

if TYPE_CHECKING:
    from .runner import Driver

# Every op, in the order the guide lists them.
OPS = ("login", "sessions", "targets", "target_new", "target_edit", "target_delete", "new", "attach", "switch", "read", "type", "keys", "wait", "hand_back", "hand", "take", "ask_to_type",
       "answer", "size", "detach", "end", "logout", "snapshot", "click", "press", "resize", "quit",
       "login_hostkey", "login_answer", "login_cancel")
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


def answer_line(line: str) -> str:
    return line[:-1].removesuffix("\r") if line.endswith("\n") else line


def read_answer_pipe(path: str, count: int, timeout: float = 30.0) -> list[str]:
    """Read bounded answer lines from a nonblocking pipe, each against a deadline."""
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        if not stat.S_ISFIFO(os.fstat(fd).st_mode):
            raise DriveError("bad_request", "answers_file must name a pipe")
        answers = []
        for _ in range(count):
            deadline = time.monotonic() + timeout
            line = bytearray()
            while len(line) < 1027:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([fd], [], [], max(0, remaining))[0]:
                    raise DriveError("bad_request", "the answers did not arrive before the pipe deadline")
                try:
                    chunk = os.read(fd, 1)
                except BlockingIOError:
                    continue
                if not chunk:
                    if not line:
                        raise DriveError("bad_request", "the pipe ended before every answer line")
                    break
                line.extend(chunk)
                if chunk == b"\n":
                    break
            value = answer_line(line.decode("utf-8"))
            if len(value.encode()) > 1024:
                raise DriveError("bad_request", "an answer exceeds 1024 bytes")
            answers.append(value)
        return answers
    except (OSError, UnicodeError):
        raise DriveError("bad_request", "the answer pipe could not be read as UTF-8") from None
    finally:
        if fd is not None:
            os.close(fd)


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
        row = backend.live_cursor().y
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


@dataclass
class SendHistory:
    count: int = 0
    last_send_at: float | None = None
    output_since_send: bool = False
    last_output_at: float | None = None


@dataclass(frozen=True)
class Look:
    session: str
    state: AttachmentState
    attachment: int
    applied: int
    output: int
    keyboard: int
    keyboard_items: int
    stale_items: int
    size_items: int
    size: int
    stale: int
    sends: int
    kind: str
    at: float
    alt: bool
    cursor_hidden: bool


class Commands:
    """The ops, as methods named `op_<name>`. Each takes the command object and returns the answer's fields, or raises
    `DriveError`."""

    def __init__(self, driver: Driver) -> None:
        self.d = driver
        # A session whose create answered with a first-use host key: its gate stays up, and a later `attach` opens
        # it with this fingerprint rather than a gate that would let Attach through unticked.
        self._pending_hostkeys: dict[str, dict[str, Any]] = {}
        self._device_logins: dict[str, Any] = {}
        self.send_history: dict[str, SendHistory] = {}
        self._looks: dict[str, Look] = {}
        self._last_look: dict[str, str] = {}
        self._look_serial = 0

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

    async def _target_form(self, definition: dict, *, editing: bool) -> dict[str, Any]:
        from ..screens.target_form import TargetFormScreen

        screen = self._screen()
        form = TargetFormScreen(definition if editing else None)
        # A refused drive write leaves its form visible, just like a person's Save. Replace that
        # form on the next explicit write rather than stacking another over it.
        if isinstance(self.d.app.screen, TargetFormScreen):
            self.d.app.pop_screen()
        await self.d.app.push_screen(form, screen.target_saved)
        form.fill(definition)
        try:
            record = await form.save()
        except ApiError as exc:
            raise DriveError(exc.code, exc.message) from None
        except ValueError as exc:
            raise DriveError("bad_request", str(exc)) from None
        except (HttpError, OSError) as exc:
            raise DriveError("request_failed", str(exc)) from None
        return {"target": record}

    @staticmethod
    def _target_fields(cmd: dict, *, editing: bool) -> dict:
        from ..screens.target_form import FIELDS, OPTIONAL_FIELDS

        fields = {}
        for key in FIELDS:
            if editing and key == "id":
                continue  # drive's correlation id is never an edit of the target id
            if key not in cmd:
                if not editing and key in ("id", "type", "host"):
                    raise DriveError("bad_request", f"{key}: required")
                continue
            value = cmd[key]
            if editing and value is None and key in OPTIONAL_FIELDS:
                fields[key] = None
                continue
            kind = list if key == "tags" else int if key == "port" else str
            if isinstance(value, bool) or not isinstance(value, kind):
                raise DriveError("bad_request", f"{key}: expected {kind.__name__}")
            if key == "tags" and any(not isinstance(tag, str) or not tag.strip() or "," in tag for tag in value):
                raise DriveError("bad_request", "tags: expected nonempty strings without commas")
            if key == "type" and value not in ("ssh", "telnet"):
                raise DriveError("bad_request", "type: expected ssh or telnet")
            fields[key] = value
        return fields

    async def op_target_new(self, cmd: dict[str, Any]) -> dict[str, Any]:
        self._screen()
        return await self._target_form(self._target_fields(cmd, editing=False), editing=False)

    async def op_target_edit(self, cmd: dict[str, Any]) -> dict[str, Any]:
        self._screen()
        target = _field(cmd, "target", str)
        changes = self._target_fields(cmd, editing=True)
        try:
            definition = await asyncio.to_thread(self.d.app.client.get_target_definition, target)
        except ApiError as exc:
            raise DriveError(exc.code, exc.message) from None
        except (HttpError, OSError) as exc:
            raise DriveError("request_failed", str(exc)) from None
        for key, value in changes.items():
            if value is None:
                definition.pop(key, None)
            else:
                definition[key] = value
        if definition["type"] == "telnet":
            definition.pop("user", None)
        return await self._target_form(definition, editing=True)

    async def op_target_delete(self, cmd: dict[str, Any]) -> dict[str, Any]:
        screen = self._screen()
        if cmd.get("confirm") is not True:
            raise DriveError("bad_request", "deletion cannot be undone; send confirm: true; open sessions stay open")
        target = _field(cmd, "target", str)
        try:
            await screen.delete_target(target)
        except ApiError as exc:
            raise DriveError(exc.code, exc.message) from None
        except (HttpError, OSError) as exc:
            raise DriveError("request_failed", str(exc)) from None
        return {"target": target}

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

        if cmd.get("login_mode") == "prompt":
            return await self._new_device_login(cmd)
        if "login_mode" in cmd:
            raise DriveError("bad_request", 'login_mode: expected "prompt"')
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

    async def _new_device_login(self, cmd: dict[str, Any]) -> dict[str, Any]:
        if any(k in cmd for k in ("password", "password_file", "password_from", "remember")):
            raise DriveError("bad_request", "prompt mode takes no password source or Remember")
        target = _field(cmd, "target", str)
        user = _field(cmd, "login_user", str)
        if not 1 <= len(user) <= 256:
            raise DriveError("bad_request", "login_user: expected 1 to 256 characters")
        acknowledged = cmd.get("recording_acknowledged", False)
        if not isinstance(acknowledged, bool):
            raise DriveError("bad_request", "recording_acknowledged: expected bool")
        screen = self._screen()
        await self.d.workspace_ready(screen)
        size = create_size(self.d.app)
        if size is None:
            raise DriveError("not_available", CREATE_SIZE_OFF)
        try:
            record = await asyncio.to_thread(self.d.app.client.get_target, target)
            if record.get("type") != "ssh":
                raise DriveError("bad_request", "device login requires an SSH target")
            call = functools.partial(self.d.app.client.create_device_login, target, user, TERM, *size,
                                     recording_acknowledged=acknowledged)
            pending = await asyncio.to_thread(call)
        except ApiError as exc:
            if exc.status in (422, 503):
                self.d.app.device_login_unavailable = self.d.app.client
            if exc.status == 401:
                self.d.app.notify_api_error(exc)
            raise DriveError(exc.code, exc.message, target=target) from None
        except (HttpError, OSError):
            raise DriveError("request_failed", "The device login could not be started.") from None
        # Mounting starts the socket on the next UI turn, after this command has answered with its id.
        pane = screen.open_device_login(target, TERM, size, user=user, pending=pending,
                                       emit=self.d.emit_event,
                                       connected_answer=functools.partial(self._attached_answer, screen))
        self._device_logins[pending["device_login_id"]] = pane
        return {"device_login": pending["device_login_id"]}

    def _device_login(self, cmd: dict[str, Any]) -> Any:
        self._screen()
        ident = _field(cmd, "device_login", str)
        pane = self._device_logins.get(ident)
        if pane is None or not pane._live():
            raise DriveError("bad_request", "no such device login in this TSB login")
        return pane

    async def op_login_hostkey(self, cmd: dict[str, Any]) -> dict[str, Any]:
        pane = self._device_login(cmd)
        accept = cmd.get("accept")
        if not isinstance(accept, bool):
            raise DriveError("bad_request", "accept: expected bool")
        if not pane.hostkey_pending:
            raise DriveError("not_available", "No host-key question is pending.")
        await pane.answer_hostkey(accept)
        return {}

    async def op_login_cancel(self, cmd: dict[str, Any]) -> dict[str, Any]:
        pane = self._device_login(cmd)
        if pane.connected:
            raise DriveError("not_available", "the login already connected; its session lives on")
        await pane.cancel()
        return {}

    async def op_login_answer(self, cmd: dict[str, Any]) -> dict[str, Any]:
        pane = self._device_login(cmd)
        prompt = pane.prompt
        if prompt is None or "prompt_id" not in prompt or pane.connected or pane.ended:
            raise DriveError("not_available", "No device question is pending.")
        count = 1 if prompt["method"] == "password" else len(prompt["prompts"])
        source = cmd.get("answers_from")
        answers = None
        if source == "stdin" and not self.d.script:
            answers = []
            for _ in range(count):
                line = await self.d.take_stdin_line()
                if line is None:
                    raise DriveError("bad_request", "stdin ended before every answer line")
                answers.append(answer_line(line))
        if "answers" in cmd:
            raise DriveError("bad_request", "answers never go in a command: use stdin or a pipe")
        if source is not None and source != "stdin":
            raise DriveError("bad_request", 'answers_from: expected "stdin"')
        if source == "stdin" and self.d.script:
            raise DriveError("bad_request", "answers_from: stdin is refused in --script mode")
        path = _field(cmd, "answers_file", str, required=False)
        if (source is None) == (path is None):
            raise DriveError("bad_request", "give one answer source: answers_from or answers_file")
        prompt_id = _field(cmd, "prompt_id", int)
        if answers is None:
            answers = await asyncio.to_thread(read_answer_pipe, path, count,
                                              self.d.app.cfg["drive.answer_timeout_ms"] / 1000)
        if any(len(s.encode("utf-8")) > 1024 for s in answers):
            raise DriveError("bad_request", "an answer exceeds 1024 bytes")
        try:
            await pane.send_answers(answers, prompt_id)
        finally:
            answers.clear()
        return {}

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
        with backend.lock():
            return self.screen_answer(slot, join_wrapped=join, nested=False)

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

    def history(self, sid: str) -> SendHistory:
        return self.send_history.setdefault(sid, SendHistory())

    def note_output(self, sid: str) -> None:
        # Called from the processor's output event, never from the socket reader or an attach dump.
        history = self.history(sid)
        history.last_output_at = self.d.app.idle_clock()
        if history.last_send_at is not None:
            history.output_since_send = True

    def _record_send(self, slot: SessionSlot) -> None:
        history = self.history(slot.session_id)
        history.count += 1
        history.last_send_at = self.d.app.idle_clock()
        history.output_since_send = False

    def look_kind(self, slot: SessionSlot) -> tuple[str | None, str | None]:
        history = self.history(slot.session_id)
        now = self.d.app.idle_clock()
        if slot.canvas.backend.modes().synchronized_output:
            return "unsettled", "output still arriving"
        if history.last_send_at is None:
            return "ordinary", None
        if not history.output_since_send:
            if (now - history.last_send_at) * 1000 >= self.d.cfg["drive.wait_timeout_ms"]:
                return "after_silence", None
            return None, "no output since your send"
        if history.last_output_at is None or (now - history.last_output_at) * 1000 < self.d.cfg["drive.settled_ms"]:
            return "unsettled", "output still arriving"
        return "ordinary", None

    def _prune_looks(self, now: float) -> None:
        screen = self._screen()
        for ident, seen in list(self._looks.items()):
            slot = screen.slot_for(seen.session)
            state = slot.pump.state if slot is not None and slot.pump is not None else None
            ended = (state is None or slot.closed or state.closed or seen.state is not state
                     or seen.attachment != state.attachment_generation)
            if ended:
                del self._looks[ident]
                continue
            spent = seen.sends != self.history(seen.session).count
            moved = (seen.keyboard != state.keyboard_moves
                     or seen.keyboard_items != state.received_keyboard_items
                     or seen.stale != state.stale_changes
                     or seen.stale_items != state.received_stale_items)
            behind = (seen.applied != state.received_output_bytes or seen.output != state.output_bytes_applied
                      or seen.size != state.size_changes or seen.size_items != state.received_size_items)
            young = (now - seen.at) * 1000 < self.d.cfg["drive.brake_look_max_ms"]
            if spent or moved or (behind and not young):
                del self._looks[ident]
        # Dict order is mint order; a session's oldest surviving looks leave first.
        counts: dict[str, int] = {}
        for ident, seen in reversed(list(self._looks.items())):
            counts[seen.session] = counts.get(seen.session, 0) + 1
            if counts[seen.session] > 64:
                del self._looks[ident]

    def screen_answer(self, slot: SessionSlot, *, refusal: bool = False, join_wrapped: bool = False,
                      nested: bool = True) -> dict[str, Any]:
        """Caller holds backend.lock: applied counts, then grid, cursor, modes and the look together."""
        backend = slot.canvas.backend
        state = slot.pump.state
        applied = state.applied_output_bytes
        output = state.output_bytes_applied
        keyboard, size, stale = state.keyboard_moves, state.size_changes, state.stale_changes
        history = self.history(slot.session_id)
        backend.sync_render_state()
        lines = screen_lines(backend, join_wrapped=join_wrapped)
        cursor = backend.live_cursor()
        modes = backend.modes()
        now = self.d.app.idle_clock()
        last_output = history.last_output_at
        if last_output is None:
            last_output = state.last_output_at
        block = {"lines": lines, "cursor": [cursor.y, cursor.x], "cols": backend.cols, "rows": backend.rows,
                 "alt": modes.alternate_screen, "cursor_hidden": not cursor.visible,
                 "quiet_ms": max(0, int((now - last_output) * 1000)) if last_output is not None else 0,
                 "modes": {"cursor_keys_application": modes.cursor_keys_application,
                           "keypad_application": modes.keypad_application, "bracketed_paste": modes.bracketed_paste,
                           "mouse_tracking": modes.mouse_tracking, "focus_events": modes.focus_events,
                           "kitty_flags": modes.kitty_flags, "synchronized_output": modes.synchronized_output}}
        kind, why = self.look_kind(slot)
        out: dict[str, Any] = {"screen": block} if nested else block
        out["look"] = None
        if why is not None:
            out["no_look_yet"] = why
        if kind is not None:
            self._look_serial += 1
            ident = f"L{self._look_serial}"
            self._looks[ident] = Look(slot.session_id, state, state.attachment_generation, applied, output,
                                      keyboard, state.applied_keyboard_items, state.applied_stale_items,
                                      state.applied_size_items, size, stale, history.count, kind, now,
                                      modes.alternate_screen, not cursor.visible)
            self._prune_looks(now)
            if not refusal:
                self._last_look[slot.session_id] = ident
            out.update(look=ident, look_kind=kind)
        return out

    def _refuse(self, slot: SessionSlot, code: str, why: str, **extra: Any) -> None:
        raise DriveError(code, why + "; nothing was sent", sent=False,
                         **self.screen_answer(slot, refusal=True), **extra)

    def _check_gate(self, slot: SessionSlot, cmd: dict[str, Any], data: bytes, *,
                    text: str | None, names: list[str] | None, legacy: list[bytes]) -> None:
        state = slot.pump.state
        backend = slot.canvas.backend
        brake = data in (b"\x03", b"\x1e")
        bare_enter = text == "\r" if text is not None else len(legacy) == 1 and legacy[0] == b"\r"
        ends_line = text.endswith("\r") if text is not None else False
        has_text = text is not None
        if text is not None:
            if "\n" in text or "\r" in text[:-1] or ("\t" in text and "\r" in text):
                self._refuse(slot, "multi_line", "one final CR only, with no tab in the same send")
            if not brake and any(ord(ch) < 32 and ch not in "\t\r" for ch in text):
                self._refuse(slot, "control_bytes", "control bytes must be sent as keys alone")
        else:
            ends, tabs, controls = [], [], []
            for name, payload in zip(names, legacy, strict=True):
                parsed = _parse(name)
                base = name.split("+")[-1]
                line_end = (base == "enter" or name.lower() in ("ctrl+m", "ctrl+j")
                            or payload.endswith((b"\r", b"\n")))
                tab = base == "tab" or payload == b"\t"
                is_text = parsed.utf8 is not None and not (parsed.mods & ~MODS_SHIFT)
                has_text |= is_text
                ends.append(line_end)
                tabs.append(tab)
                controls.append(not (line_end or tab or is_text))
            ends_line = bool(ends and ends[-1])
            if sum(ends) > 1 or any(ends[:-1]) or (any(tabs) and any(ends)):
                self._refuse(slot, "multi_line", "one final line-end key only, with no tab in the same send")
            if len(names) > 1 and any(controls):
                self._refuse(slot, "control_bytes", "a control key must go alone")
        if state.stale_state == "pending" or state.stale_prompt is not None:
            self._refuse(slot, "stale", "a stale choice is pending")
        ident = cmd.get("look")
        if ident == "last":
            ident = self._last_look.get(slot.session_id)
        seen = self._looks.get(ident)
        if seen is None:
            try:
                serial = int(ident[1:]) if isinstance(ident, str) and ident.startswith("L") else 0
            except ValueError:
                serial = 0
            if 0 < serial <= self._look_serial and ident == f"L{serial}":
                self._refuse(slot, "no_look", "that look is too old")
        if seen is None or seen.session != slot.session_id:
            self._refuse(slot, "no_look", "name a look that showed this session's screen")
        if seen.state is not state or seen.attachment != state.attachment_generation:
            self._refuse(slot, "no_look", "the session was reattached since that look")
        if seen.sends != self.history(slot.session_id).count:
            self._refuse(slot, "no_look", "that look was used up by a send")
        # The screen-lock check belongs here when its contract release lands.
        changed = []
        if state.received_output_bytes != seen.applied or state.output_bytes_applied != seen.output:
            changed.append("output")
        if (state.keyboard_moves != seen.keyboard
                or state.received_keyboard_items != seen.keyboard_items):
            changed.append("keyboard")
        if state.size_changes != seen.size or state.received_size_items != seen.size_items:
            changed.append("size")
        if state.stale_changes != seen.stale or state.received_stale_items != seen.stale_items:
            changed.append("stale")
        young_brake = brake and (self.d.app.idle_clock() - seen.at) * 1000 < self.d.cfg["drive.brake_look_max_ms"]
        if young_brake:
            changed = [cause for cause in changed if cause not in ("output", "size")]
            if (seen.alt, seen.cursor_hidden) != (backend.modes().alternate_screen, not backend.live_cursor().visible):
                changed.append("output")
        if changed:
            self._refuse(slot, "screen_changed", "the screen changed after your look", changed=changed)
        if seen.kind == "unsettled" and not brake:
            self._refuse(slot, "unsettled", "output still arriving: only a stop key may go")
        if seen.kind == "after_silence" and not (brake or bare_enter):
            self._refuse(slot, "silent", "nothing came back after your send: only a stop key or bare Enter may go")
        if has_text and not brake:
            taken = backend.modes().alternate_screen or not backend.live_cursor().visible
            if taken and not cmd.get("full_screen", False):
                self._refuse(slot, "full_screen", "a program has taken the screen; say full_screen: true")
            if not taken and cmd.get("full_screen", False):
                self._refuse(slot, "not_full_screen", "no program has taken the screen")
        # The target prompt check belongs here when its contract release lands.
        if ends_line and not cmd.get("full_screen", False) and not state.shell_marks.at_prompt:
            self._refuse(slot, "not_at_prompt", "shell marks show the command has not returned to a prompt")

    async def _send(self, slot: SessionSlot, cmd: dict[str, Any], data: bytes, *, text: str | None = None,
                    names: list[str] | None = None, legacy: list[bytes] | None = None) -> dict[str, Any]:
        screen = self._screen()
        fronted = self._to_front(slot)
        state = slot.pump.state
        if state.closed or slot.closed:
            raise DriveError("not_attached", "the session is closed; nothing was sent", sent=False)
        if screen.scroll_view_open and slot is screen.front:
            raise DriveError("not_available", "the scroll view is open in this TUI, and nothing reaches the session "
                             "while it is: press escape to close it; nothing was sent", sent=False)
        if not state.may_type and self.d.cfg["input.guard"]:
            raise DriveError("not_typist", "you do not hold the keyboard; nothing was sent",
                             holder=self._holder_user(state), sent=False)
        with slot.canvas.backend.lock():
            if self.d.cfg["drive.send_gate"]:
                self._check_gate(slot, cmd, data, text=text, names=names, legacy=legacy or [])
            history = self.history(slot.session_id)
            before = replace(history)
            shell_marks = state.shell_marks
            exit_serial = shell_marks.exit_serial
            self._record_send(slot)
        if not await screen.send_input(SOURCE_KEY, data, slot, drive_gate=_DRIVE_GATE):
            with slot.canvas.backend.lock():
                history.count = before.count
                history.last_send_at = before.last_send_at
                history.output_since_send = before.output_since_send
                history.last_output_at = before.last_output_at
            raise DriveError("not_attached", "the session is not ready for input; nothing was sent", sent=False)
        out = {"front": screen.front.session_id if screen.front else None, "queued": len(data), "sent": True}
        out.update(await self.d.wait_after_send(slot, self._send_timeout(cmd)))
        if (text is not None and out.get("settled") and state.shell_marks is shell_marks
                and shell_marks.exit_serial > exit_serial and shell_marks.exit_code is not None):
            out["exit_code"] = shell_marks.exit_code
        if fronted.switched_from is not None:
            out["switched_from"] = fronted.switched_from
        return out

    def _send_timeout(self, cmd: dict[str, Any]) -> int:
        if "look" in cmd and not isinstance(cmd["look"], str):
            raise DriveError("bad_request", "look: expected a look id or last")
        if "full_screen" in cmd and not isinstance(cmd["full_screen"], bool):
            raise DriveError("bad_request", "full_screen: expected true or false")
        try:
            return waits.timeout_for(cmd.get("timeout_ms"), default_ms=self.d.cfg["drive.wait_timeout_ms"],
                                     max_ms=self.d.cfg["drive.wait_timeout_max_ms"])
        except waits.BadWait as exc:
            raise DriveError("bad_request", str(exc)) from None

    async def op_type(self, cmd: dict[str, Any]) -> dict[str, Any]:
        text = _field(cmd, "text", str)
        if not text:
            raise DriveError("bad_request", "text: expected at least one character")
        self._send_timeout(cmd)
        return await self._send(self._slot(cmd), cmd, text.encode("utf-8"), text=text)

    async def op_keys(self, cmd: dict[str, Any]) -> dict[str, Any]:
        names = cmd.get("keys")
        if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
            raise DriveError("bad_request", "keys: expected a list of key names such as [\"ctrl+c\"]")
        self._send_timeout(cmd)
        slot = self._slot(cmd)
        backend = slot.canvas.backend
        plain = VTBackend(backend.lib, 1, 1, scrollback_bytes=0)
        try:
            with backend.lock():
                data, legacy = b"", []
                for name in names:
                    try:
                        data += encode_key(backend, name)
                        legacy.append(encode_key(plain, name))
                    except UnknownKey:
                        raise DriveError("unknown_key", f"{name!r} is not a key name drive knows; nothing was sent",
                                         key=name, sent=False) from None
        finally:
            plain.close()
        return await self._send(slot, cmd, data, names=names, legacy=legacy)

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
            out.update(done.extra or {})
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

    async def op_take(self, cmd: dict[str, Any]) -> dict[str, Any]:
        slot = self._slot(cmd)
        self._to_front(slot)
        why = A.blocked("take_back", A.Facts(slot.pump.state, open_sessions=len(self._screen().slots)))
        if why is not None:
            raise DriveError("not_available", why)
        return await self.d.hand_off(slot, slot.pump.state.client_id)

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

    async def op_end(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """End a session for everyone, through the send half of `^\\ x` (`TerminalScreen.send_end`): the same request,
        the same toast or refusal. `confirm: true` is the agent saying it knows (ending is for everyone and cannot be undone), so no
        dialog opens. It answers once the core has; the end frame is for `wait {"ended": true}`. Whether
        this login may end it is the core's to say: nothing is pre-checked but the record's own `ended`."""
        if cmd.get("confirm") is not True:
            raise DriveError("bad_request", "ending is for everyone and cannot be undone; send confirm: true")
        slot = self._slot(cmd)
        screen = self._screen()
        sid = slot.session_id
        label = screen._session_label(sid)
        if screen.has_ended(sid):
            raise DriveError("ended", f"{label} has already ended", session=sid)
        self._to_front(slot)
        answered: asyncio.Future[Exception | None] = asyncio.get_running_loop().create_future()

        def settle(failure: Exception | None) -> None:
            if not answered.done():  # `wait_for` cancels it on a timeout, and the core's answer can come after
                answered.set_result(failure)

        screen.send_end(sid, settle)
        timeout_ms = self.d.cfg["drive.wait_timeout_ms"]
        try:
            failure = await asyncio.wait_for(answered, timeout_ms / 1000.0)
        except TimeoutError:
            raise DriveError("timeout", f"no answer to ending {label} within {timeout_ms} ms", session=sid) from None
        if failure is None:
            return {"session": sid}
        if isinstance(failure, ApiError) and failure.status == 403:
            raise DriveError("refused", end_refused_text(label), session=sid)
        if isinstance(failure, ApiError) and failure.status == 404:
            raise DriveError("no_such_session", "not a session you may see", session=sid)
        raise DriveError("request_failed", str(failure), session=sid)

    async def op_logout(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """The Menu's Sign out: every session open here is detached (each lives on), then the broker is told, and drive
        is back at Login, where a `login` is accepted again. `broker` is `unconfirmed` when the broker did not take
        the logout: this client holds no login either way."""
        self._screen()
        result = await self.d.app.sign_out()
        await self.d.wait_for_login_screen()
        return {"detached": result.detached, "broker": result.broker}

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
        screen = self.d.screen()
        before = screen.dropped_inputs if screen is not None else 0
        await self.d.pilot.press(*names)
        await self.d.pilot.pause()
        return {"dropped": (screen.dropped_inputs - before) if screen is not None else 0}

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

