"""`tsb-tui drive`, the process: the real app run headless on Textual's own test runner, a JSON command per line in,
answers and events as JSON lines out, and an exit code (design §2.9.4).

`Driver` is what the commands stand on: it holds the app and the pilot, learns of every model change through the
app's tap (`TsbTuiApp.tapped`), turns the changes into events, and settles waits when one arrives. It is the one place
that touches the pilot; `commands.py` is the vocabulary and `waits.py` the judgement. Commands are taken one at a time,
in the order they come, so a `type` is always sent before the `wait` after it starts counting.

Exit codes: 0 when every step of a script passed or interactive input ended, 1 when a step failed (a failed wait, or
any command answering `ok: false`: the script stops there) or drive could not start, 2 on bad usage.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import sys
import threading
import time
import traceback
import unicodedata
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO, Any

from tsb_contract_client import ApiError

from .. import config
from .._transport import HttpError
from ..model.events import (
    KIND_CLOSE,
    KIND_OUTPUT,
    KIND_TOKEN,
    TAP_RECORD,
    TAP_SLOT_CLOSED,
    Event,
)
from ..screens.chrome_dialogs import tui_version
from ..screens.terminal import SessionSlot, TerminalScreen
from ..widgets.canvas import Canvas
from . import waits
from .commands import Commands, DriveError, SlotProbe, parse_size, read_password_file
from .events import OutputCounter, public, translate
from .screen_lock import DriveScreenLock

GUIDE = Path(__file__).with_name("GUIDE.md")
PROFILES = ("drive", "harness")
# The signals a wait may look back over: every event told to this driver, newest last.
SIGNAL_LIMIT = 10000
# How often the login and the gate are looked at while their screens mount: they are Textual screens coming up, which
# no model event announces.
SCREEN_POLL_S = 0.01


def cell_len(text: str) -> int:
    """How many terminal cells `text` takes: a wide character two, a combining mark none."""
    return sum(0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


@dataclass(eq=False)
class _Pending:
    """One thing a command is waiting on: `check` returns the outcome, or None while it is still open."""

    sid: str
    check: Callable[[], waits.Done | None]
    future: asyncio.Future[waits.Done]
    timeout: asyncio.TimerHandle | None = None
    settle: asyncio.TimerHandle | None = None
    settled_in: Callable[[], float] | None = None


class Driver:
    def __init__(self, app: Any, pilot: Any, *, emit: Callable[[dict[str, Any]], None]) -> None:
        self.app = app
        self.pilot = pilot
        self.cfg = app.cfg
        app.under_drive = True  # TT29: the one place the runner and the test rig both pass
        self.quitting = False
        # The lines the loop is taking commands from, and whether they are a script's: `new`'s `password_from: stdin`
        # takes the next one itself (set by `serve`).
        self._lines: AsyncIterator[str] | None = None
        self.script = False
        self._emit = emit
        self._enabled = frozenset(self.cfg["drive.events"])
        self._signals: list[tuple[int, str, dict[str, Any]]] = []
        self._seq = 0
        self._pending: list[_Pending] = []
        self._scheduled = False
        self._own_handoff: set[str] = set()
        # Why each session that left the screen was left (an attach that failed): what the operator was told.
        self._left: dict[str, str | None] = {}
        self._output = (OutputCounter(emit, time.monotonic) if self.cfg["drive.output_events"] == "counts"
                        else None)
        self.commands = Commands(self)
        self.screen_lock = DriveScreenLock(self)
        app.drive_screen_lock = self.screen_lock
        app.drive_commands = self.commands
        app.taps.append(self.tap)

    def ready(self, size: tuple[int, int]) -> dict[str, Any]:
        return {"ev": "ready", "version": tui_version(), "principal": self.app.username, "size": f"{size[0]}x{size[1]}",
                "profile": self.cfg.profile, "engine": self.app.engine.build_info.version_string,
                "send_gate": self.cfg["drive.send_gate"], "settled_ms": self.cfg["drive.settled_ms"],
                "wait_timeout_ms": self.cfg["drive.wait_timeout_ms"]}

    # -- the app, as the commands see it --

    def screen(self) -> TerminalScreen | None:
        """The workspace; None until the login has landed on it."""
        return self.app.workspace()

    def probe(self, slot: SessionSlot) -> SlotProbe:
        screen = self.screen()
        assert screen is not None
        return SlotProbe(screen, slot, reply_cap=self.cfg["drive.reply_max_lines"])

    def screen_text(self) -> str:
        """The whole UI as the operator would read it, row by row: what `snapshot` returns."""
        return "\n".join(strip.text.rstrip() for strip in self.app.screen._compositor.render_strips())

    # -- the line out --

    def emit_event(self, event: dict[str, Any]) -> None:
        """Tell the agent an event, if `drive.events` lets it out, and keep it for the waits that look for one."""
        self._seq += 1
        self._signals.append((self._seq, event.get("session", ""), event))
        if len(self._signals) > SIGNAL_LIMIT:
            del self._signals[: len(self._signals) - SIGNAL_LIMIT]
        if event.get("ev") == "login_ended":
            self.commands.note_login_ended()
        if event["ev"] in self._enabled or event["ev"] == "ready":
            self._emit(public(event))
        self._poke()

    def _events_since(self, sid: str, seq: int) -> list[dict[str, Any]]:
        return [event for n, session, event in self._signals if n > seq and session == sid]

    # -- the tap: a model changed --

    def tap(self, sid: str, kind: str, detail: Any) -> None:
        """Runs in the middle of whatever changed the model, so it only notes the change and pokes: nothing is judged
        until the change has finished (a byte's note is written before its grid is fed)."""
        if kind in (TAP_SLOT_CLOSED, KIND_CLOSE):
            self.commands.note_closed(sid)
        if kind == TAP_SLOT_CLOSED:
            self._left[sid] = getattr(detail, "left", None)
        elif kind != TAP_RECORD and isinstance(detail, Event):
            self._from_log(sid, detail)
        self._poke()  # a poll that changed a record or a session that left carries no event: the models are read now

    def _from_log(self, sid: str, event: Event) -> None:
        if event.kind == KIND_OUTPUT:
            if event.detail.get("bytes", 0) > 0:
                self.commands.note_output(sid)
            if self._output is not None:
                self._output.add(sid, int(event.detail.get("bytes", 0)))
            return
        screen = self.screen()
        slot = screen.slot_for(sid) if screen is not None else None
        if slot is None or slot.pump is None:
            return
        told = translate(sid, event, slot.pump.state, own_handoff=sid in self._own_handoff)
        if event.kind == KIND_TOKEN:
            self._own_handoff.discard(sid)
        for item in told:
            self.emit_event(item)

    # -- waiting on the models --

    def _poke(self) -> None:
        if self._pending and not self._scheduled:
            self._scheduled = True
            asyncio.get_running_loop().call_soon(self._evaluate)

    def _evaluate(self) -> None:
        self._scheduled = False
        for pending in list(self._pending):
            self._evaluate_one(pending)

    def _evaluate_one(self, pending: _Pending) -> None:
        if pending.future.done():
            return
        # Read the remaining quiet period before checking: if time crosses the deadline
        # during the check, a positive period still schedules a wake-up. Other unmet
        # predicates with an already-quiet screen must wait for model events, not spin.
        delay = pending.settled_in() if pending.settled_in is not None and pending.settle is None else 0
        try:
            done = pending.check()
        except DriveError as exc:
            pending.future.set_exception(exc)
            return
        if done is not None:
            pending.future.set_result(done)
            return
        if delay > 0:
            pending.settle = asyncio.get_running_loop().call_later(delay + 0.001, self._settle_due, pending)

    def _settle_due(self, pending: _Pending) -> None:
        pending.settle = None
        self._evaluate_one(pending)

    async def _await(self, pending: _Pending, timeout_ms: int, on_timeout: Callable[[], waits.Done]) -> waits.Done:
        loop = asyncio.get_running_loop()
        self._pending.append(pending)
        try:
            self._evaluate_one(pending)
            if not pending.future.done():
                pending.timeout = loop.call_later(
                    timeout_ms / 1000.0, lambda: pending.future.done() or pending.future.set_result(on_timeout()))
            return await pending.future
        finally:
            self._pending.remove(pending)
            for handle in (pending.timeout, pending.settle):
                if handle is not None:
                    handle.cancel()

    async def run_wait(self, slot: SessionSlot, until: waits.Until, timeout_ms: int) -> tuple[waits.Done, int]:
        """The agent's `wait`: its predicates over this session, until they hold, one of the failing events is told,
        or `timeout_ms` passes. Returns the outcome and the milliseconds it took."""
        probe = self.probe(slot)
        started = probe.now()
        since = self._seq
        wait = waits.Wait(until, probe, started=started)
        def check() -> waits.Done | None:
            with slot.canvas.backend.lock():
                done = wait.check(self._events_since(slot.session_id, since))
                if not self.screen_lock.check() and done is not None and done.ok and any(getattr(until, name) is not None
                                                       for name in ("prompt", "screen", "reply")):
                    done = replace(done, extra=self.commands.screen_answer(slot))
                return done

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_ms / 1000
        while True:
            pending = _Pending(slot.session_id, check, loop.create_future(),
                               settled_in=wait.settled_in if until.settled_ms is not None else None)
            remaining_ms = max(0, math.ceil((deadline - loop.time()) * 1000))
            done = await self._await(pending, remaining_ms, lambda: waits.Done(
                False, "timeout", f"nothing matched within {timeout_ms} ms"))
            if not self.screen_lock.check():
                break
            if not await self.screen_lock.resume():
                raise DriveError("screen_locked", **self.screen_lock.fields())
            if loop.time() >= deadline:
                done = waits.Done(False, "timeout", f"nothing matched within {timeout_ms} ms")
                break
            fresh = check()
            if fresh is not None:
                done = fresh
                break
        return done, int((probe.now() - started) * 1000)

    async def wait_after_send(self, slot: SessionSlot, timeout_ms: int) -> dict[str, Any]:
        def answer(settled: bool) -> waits.Done:
            with slot.canvas.backend.lock():
                return waits.Done(True, extra={"settled": settled, **self.commands.screen_answer(slot)})

        def check() -> waits.Done | None:
            with slot.canvas.backend.lock():
                return answer(True) if self.commands.look_kind(slot)[0] == "ordinary" else None

        def quiet_in() -> float:
            history = self.commands.history(slot.session_id)
            if not history.output_since_send or history.last_output_at is None:
                return 0
            return max(0, history.last_output_at + self.cfg["drive.settled_ms"] / 1000 - self.app.idle_clock())

        pending = _Pending(slot.session_id, check, asyncio.get_running_loop().create_future(), settled_in=quiet_in)
        done = await self._await(pending, timeout_ms, lambda: answer(False))
        return done.extra or {}

    async def condition(self, predicate: Callable[[], Any], sid: str, what: str) -> Any:
        """Wait, for at most `drive.wait_timeout_ms`, until `predicate` returns something true, and return it. The
        predicate may raise `DriveError` to end the wait with that answer."""
        timeout = self.cfg["drive.wait_timeout_ms"]
        pending = _Pending(sid, lambda: (waits.Done(True, matched={"value": v}) if (v := predicate()) else None),
                           asyncio.get_running_loop().create_future())
        done = await self._await(pending, timeout, lambda: waits.Done(False, "timeout"))
        if not done.ok:
            raise DriveError("timeout", f"no {what} within {timeout} ms")
        return done.matched["value"]  # type: ignore[index]

    # -- the screens that come up while a command runs --

    async def _until_screen(self, predicate: Callable[[], Any], what: str) -> Any:
        """Poll a Textual screen coming up (the login result, the gate): nothing in the models announces it."""
        deadline = time.monotonic() + self.cfg["drive.wait_timeout_ms"] / 1000.0
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            await self.pilot.pause(SCREEN_POLL_S)
        raise DriveError("timeout", f"{what} did not come within {self.cfg['drive.wait_timeout_ms']} ms")

    def _workspace_ready(self, screen: TerminalScreen | None) -> bool:
        """The workspace is ready for an op that reads the fleet: its `on_mount` has run, so the fleet and the
        poller it builds (terminal.py:410-411) are there. A `switch_screen` puts the screen on the stack before
        its mount is processed, so `workspace()` is not None while they are still None."""
        return screen is not None and screen.fleet is not None and screen.poller is not None

    async def workspace_ready(self, screen: TerminalScreen) -> None:
        """Wait until this workspace has mounted (the fleet and poller built), as the login's own wait does on a
        first start — the same readiness every op that reads `screen.fleet` assumes."""
        await self._until_screen(lambda: self._workspace_ready(screen), "the workspace to be ready")

    async def wait_for_login_screen(self) -> None:
        """Login is up with its form built: what `log_in` needs next (a form still being composed has no fields)."""
        from ..screens.login import LoginScreen

        def showing() -> bool:
            screen = self.app.screen
            return (self.screen() is None and isinstance(screen, LoginScreen) and screen.is_mounted
                    and bool(screen.query("#login-button")))

        await self._until_screen(showing, "the login screen")

    async def log_in(self, user: str, password: str, *, password_path: str | None = None) -> str:
        """The login screen, filled in and its button clicked, as a person does (honesty rule 1)."""
        from textual.widgets import Button, Input, Static

        from ..screens.login import LoginScreen

        login = self.app.screen
        if not isinstance(login, LoginScreen):
            raise DriveError("bad_request", "the login screen is not showing")
        # A screen that is pushed but not composed yet has no fields: wait for the form `log_in` needs.
        await self.wait_for_login_screen()
        login = self.app.screen
        self.screen_lock.reset()
        login.query_one("#username", Input).value = user
        login.query_one("#password", Input).value = password
        del password
        # The form keeps the last refusal until an attempt clears it: clear it first, so a refusal read below is this
        # attempt's.
        login.query_one("#login-error", Static).update("")
        # Enter in the password field submits the form as its button does, and needs no room on a window too small to
        # show the button.
        login.query_one("#password", Input).focus()
        await self.pilot.press("enter")

        def outcome() -> str | None:
            # "in" means the workspace is ready, not merely pushed: `switch_screen` puts the TerminalScreen on
            # the stack before its `on_mount` runs, so a login that answered as soon as `workspace()` was
            # non-None let drive's next op read a fleet that is not built yet (the re-login race).
            if self._workspace_ready(self.screen()):
                return "in"
            # Read the current screen each time, never `login`: a later 401 can replace the screen this call
            # filled in (the one `_to_login` used to push on every 401), and querying that unmounted screen is
            # the `NoMatches` this guards. If it was replaced, this attempt cannot answer.
            current = self.app.screen
            if isinstance(current, LoginScreen):
                if current is not login:
                    raise DriveError("login_failed", "the login screen was replaced before it answered")
                if (not current.query_one("#login-button", Button).disabled
                        and str(current.query_one("#login-error", Static).content)):
                    return "refused"
            return None

        if await self._until_screen(outcome, "the login result") == "refused":
            raise DriveError("login_failed", str(login.query_one("#login-error", Static).content))
        self.screen_lock.reset(password_path)
        self.commands.clear_ended_login()
        return str(self.app.username)

    async def refresh_fleet(self) -> None:
        """One `GET /sessions` now, applied as the fleet's poll would: what `sessions` and `attach` list from."""
        screen = self.screen()
        assert screen is not None and screen.poller is not None
        try:
            await asyncio.to_thread(screen.poller.poll_once)
        except (ApiError, HttpError, OSError) as exc:
            raise DriveError("request_failed", str(exc)) from None

    async def pass_gate(self, sid: str, gate_screen: type) -> None:
        """The gate before the first attach in this run (design G2): Attach, by its key (Enter, which the gate shows), as
        a click on the button would, and without needing room for the button. A later attach has no gate, and the
        session is open straight away. Returns once the session is open, or has already left the screen again because
        its attach failed."""
        screen = self.screen()
        assert screen is not None
        deadline = time.monotonic() + self.cfg["drive.wait_timeout_ms"] / 1000.0
        self._left.pop(sid, None)
        pressed = False
        while screen.slot_for(sid) is None and sid not in self._left:
            current = self.app.screen
            if (not pressed and isinstance(current, gate_screen) and current.is_mounted
                    and current.session_id == sid and current.query("#attach-button")):
                pressed = True
                await self.pilot.press("enter")
            if time.monotonic() > deadline:
                raise DriveError("timeout", f"the gate did not open or pass within {self.cfg['drive.wait_timeout_ms']} ms")
            await self.pilot.pause(SCREEN_POLL_S)

    async def attached(self, sid: str) -> SessionSlot:
        """Wait until the session is attached and its screen has arrived: the dump applied and the clients told. An
        attach that fails, or a session that ends first, answers with why."""
        screen = self.screen()
        assert screen is not None
        slot = screen.slot_for(sid)
        if slot is None:
            raise DriveError("attach_failed", self._left.get(sid) or "the session could not be attached", session=sid)

        def ready() -> SessionSlot | None:
            if slot.closed or slot not in screen.slots:
                raise DriveError("attach_failed", slot.left or "the session could not be attached", session=sid)
            state = slot.pump.state if slot.pump is not None else None
            if state is None:
                return None
            if state.closed and state.close is not None and state.close.show in ("ended_killed", "ended_terminated"):
                raise DriveError("ended", "the session ended", session=sid)
            if state.closed and state.close is not None and state.close.show == "removed":
                raise DriveError("removed", "the owner removed you from this session", session=sid)
            return slot if state.client_id and state.dump_seen and state.clients else None

        return await self.condition(ready, sid, "attach")

    async def hand_off(self, slot: SessionSlot, client_id: str) -> dict[str, Any]:
        """Send `handoff` and answer once the core has said: the `token` frame that moved it, or a refusal."""
        sid = slot.session_id
        state = slot.pump.state
        refusals = state.refusal_count
        before = len(state.token_history)
        target = next(c for c in state.clients if c.get("client_id") == client_id)
        self._own_handoff.add(sid)
        try:
            await slot.pump.handoff(client_id)
        except BaseException:
            self._own_handoff.discard(sid)
            raise

        def said() -> bool:
            if state.refusal_count != refusals:
                refused = state.last_request_refused or {}
                raise DriveError("refused", "your hand-off reached TSB and was refused",
                                 reason=refused.get("reason"))
            if state.closed:
                raise DriveError("not_attached", "the session closed before the keyboard moved")
            return len(state.token_history) > before

        try:
            await self.condition(said, sid, "answer to the hand-off")
        finally:
            self._own_handoff.discard(sid)
        return {"to": {"user": target.get("user"), "client_id": client_id}}

    async def click_label(self, label: str) -> dict[str, Any]:
        """Click the control whose label is on screen: the first match in reading order that is not the session's own
        grid (what a program prints there is not a control)."""
        screen = self.app.screen
        lines = [strip.text for strip in screen._compositor.render_strips()]
        for y, line in enumerate(lines):
            start = line.find(label)
            while start != -1:
                x = cell_len(line[:start])
                widget = screen.get_widget_at(x, y)[0]
                if not isinstance(widget, Canvas):
                    forward = screen._forward_event
                    def guarded(event: Any, forward=forward) -> None:
                        if self.screen_lock.check():
                            self.screen_lock.blocked_inputs += 1
                            return
                        forward(event)
                    screen._forward_event = guarded
                    try:
                        await self.pilot.click(offset=(x + cell_len(label) // 2, y))
                        await self.pilot.pause()
                    finally:
                        screen._forward_event = forward
                    return {}
                start = line.find(label, start + 1)
        raise DriveError("bad_request", f"nothing on screen is labelled {label!r}")

    # -- commands in, answers out --

    def _mark_slots(self) -> None:
        """Every session open here records where this client last typed, for `read reply`."""
        screen = self.screen()
        if screen is not None:
            for slot in screen.slots:
                slot.mark_input = True

    async def execute(self, cmd: Any) -> dict[str, Any]:
        """One command's answer. Nothing a command does escapes as an exception: a failure is an answer."""
        ident = cmd.get("id") if isinstance(cmd, dict) else None
        try:
            if not isinstance(cmd, dict):
                raise DriveError("bad_request", "a command is a JSON object with an op")
            self.screen_lock.command()
            self._mark_slots()
            answer: dict[str, Any] = {"ok": True, **await self.commands.run(cmd)}
        except DriveError as exc:
            answer = {"ok": False, "error": exc.code, "why": exc.why, **exc.extra}
        except Exception as exc:  # noqa: BLE001 — a bug in drive is an answer, with its trace on stderr
            traceback.print_exc(file=sys.stderr)
            answer = {"ok": False, "error": "internal", "why": f"{type(exc).__name__}: {exc}"}
        if isinstance(cmd, dict) and cmd.get("op") in ("type", "keys") and not self.cfg["drive.send_gate"]:
            answer["send_gate"] = False
        if isinstance(cmd, dict) and "id" in cmd:
            answer = {"id": ident, **answer}
        return answer

    async def execute_line(self, line: str) -> dict[str, Any]:
        self.screen_lock.command()
        try:
            cmd = json.loads(line)
        except ValueError as exc:
            return {"ok": False, "error": "bad_request", "why": f"not JSON: {exc}"}
        return await self.execute(cmd)

    async def take_stdin_line(self) -> str | None:
        """The next input line, as it came (its line end included) and not as a command: it is not shown to the
        transcript, which only `serve` writes to. None when the input has ended."""
        assert self._lines is not None
        return await anext(self._lines, None)

    async def serve(self, lines: AsyncIterator[str], out: Transcript, *, script: bool) -> int:
        """Take commands until the lines end or `quit`. In a script the first step that fails ends it, with 1: a script
        that went on typing after a wait failed would be typing into a screen it did not expect."""
        self._lines, self.script = lines, script
        async for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            out.inbound(line)
            answer = await self.execute_line(line)
            out.outbound(answer)
            if script and not answer.get("ok"):
                return 1
            if self.quitting:
                break
        return 0

    def close(self) -> None:
        with contextlib.suppress(ValueError):
            self.app.taps.remove(self.tap)


@dataclass
class Transcript:
    """The lines drive writes (stdout) and, with `--record`, the whole exchange as the design's transcripts show it:
    `> ` before a line the agent sent, `< ` before a line drive wrote."""

    out: IO[str]
    record: IO[str] | None = None
    closed: bool = field(default=False, init=False)

    def inbound(self, line: str) -> None:
        if self.record is not None:
            try:
                command = json.loads(line)
            except ValueError:
                command = None
            if isinstance(command, dict) and command.get("op") == "login_answer" and "answers" in command:
                command["answers"] = "<refused>"
                line = json.dumps(command, separators=(",", ":"))
            op = command.get("op") if isinstance(command, dict) else None
            if isinstance(op, str) and (op.startswith("account_") or op == "password_change"):
                for key in ("password", "current_password", "new_password"):
                    if key in command:
                        command[key] = "<refused>"
                line = json.dumps(command, separators=(",", ":"))
            self.record.write(f"> {line}\n")
            self.record.flush()

    def outbound(self, obj: dict[str, Any]) -> None:
        line = json.dumps(obj, separators=(",", ":"))
        if self.record is not None:
            self.record.write(f"< {line}\n")
            self.record.flush()
        if self.closed:
            return
        try:
            self.out.write(line + "\n")
            self.out.flush()
        except (BrokenPipeError, ValueError):
            self.closed = True  # the reader went away: keep the record, stop writing


async def stdin_lines(stream: IO[str]) -> AsyncIterator[str]:
    """Lines from a blocking stream, read on a daemon thread so that a `quit` is never held up by a read."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def pump() -> None:
        try:
            for line in iter(stream.readline, ""):
                loop.call_soon_threadsafe(queue.put_nowait, line)
        except (RuntimeError, ValueError, OSError):
            return  # the loop or the stream is gone
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=pump, daemon=True).start()
    while (line := await queue.get()) is not None:
        yield line


async def script_lines(lines: Iterable[str]) -> AsyncIterator[str]:
    for line in lines:
        yield line


def _config_help() -> str:
    """Short, platform-correct description of the default config file location."""
    if sys.platform == "win32":
        return (
            "a TOML configuration file overriding the file beside the program (TSB_TUI_HOME), "
            "instead of %%APPDATA%%\\tsb-tui\\config.toml "
            "(or $XDG_CONFIG_HOME/tsb-tui/config.toml when XDG_CONFIG_HOME is set)"
        )
    return (
        "a TOML configuration file overriding the file beside the program (TSB_TUI_HOME), "
        "instead of $XDG_CONFIG_HOME/tsb-tui/config.toml "
        "(defaults to ~/.config/tsb-tui/config.toml)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tsb-tui drive", allow_abbrev=False,
        description="Run the TSB terminal client without a screen: one JSON command per line on stdin (or --script), "
                    "answers and events as JSON lines on stdout. `tsb-tui drive --guide` prints the guide for agents.")
    parser.add_argument("--guide", action="store_true", help="print the guide for agents and exit; needs no broker")
    parser.add_argument("--broker", default=None,
                        help="the core's base URL; optional: when it is absent the broker.url knob in the config "
                             "file or TSB_TUI_BROKER_URL is used, and the flag wins")
    parser.add_argument("--profile", default="drive", choices=PROFILES,
                        help="drive (the default): acts for the agent on nothing; harness: for the rigs, with output "
                             "counts and the mouse; the guard stays on in both (D52), and --set input.guard=false "
                             "switches it off for a rig")
    parser.add_argument("--user", default=None, help="log in as this user at start (with --password-file)")
    parser.add_argument("--password-file", default=None, metavar="PATH",
                        help="the password's file, or - for the first line of stdin, after which stdin carries "
                             "commands; never an argument and never the environment")
    parser.add_argument("--size", default=None, metavar="COLSxROWS",
                        help="the headless window (default: the drive.default_cols and drive.default_rows knobs); "
                             "it changes what a snapshot shows, never what a read returns")
    parser.add_argument("--script", default=None, metavar="FILE", help="run the commands in FILE instead of stdin")
    parser.add_argument("--record", default=None, metavar="FILE",
                        help="keep the exchange as a transcript: > before a command, < before an answer or event")
    parser.add_argument("--ca-file", default=None, help="a PEM bundle to verify the core's certificate against")
    parser.add_argument("--config", default=None, metavar="FILE",
                        help=_config_help())
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                        help="set one knob, e.g. --set input.guard=false (repeatable; wins over everything)")
    return parser


def main(argv: list[str]) -> int:
    """`tsb-tui drive ...`: the exit code (see the module docstring)."""
    parser = build_parser()
    args = parser.parse_args(argv)  # a bad flag exits 2
    if args.guide:
        sys.stdout.write(GUIDE.read_text(encoding="utf-8"))
        return 0
    if (args.user is None) != (args.password_file is None):
        parser.error("--user and --password-file go together")
    try:
        size = parse_size(args.size) if args.size else None
    except ValueError as exc:
        parser.error(f"--size: {exc}")
    try:
        cfg = config.load(profile=args.profile, config_file=args.config, overrides=args.overrides)
    except config.ConfigError as exc:
        print(f"tsb-tui drive: {exc}", file=sys.stderr)
        return 1
    broker_source = None if args.broker else cfg.source("broker.url")
    args.broker = config.resolve_broker(args.broker, cfg)
    args.broker_source = broker_source
    if args.broker is None:
        from ..__main__ import _missing_broker

        return _missing_broker(args.config)
    script: list[str] | None = None
    if args.script is not None:
        try:
            script = Path(args.script).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            parser.error(f"--script: {exc}")
    password = None
    if args.password_file is not None:
        try:
            password = sys.stdin.readline().rstrip("\r\n") if args.password_file == "-" else read_password_file(
                args.password_file)
        except (DriveError, OSError) as exc:
            parser.error(f"--password-file: {getattr(exc, 'why', exc)}")
        if not password:
            parser.error("--password-file: the password is empty")
    record = open(args.record, "w", encoding="utf-8") if args.record else None  # noqa: SIM115 — closed in _run
    try:
        run = _run(args, cfg, size or (cfg["drive.default_cols"], cfg["drive.default_rows"]), password,
                   script, record)
        del password
        return asyncio.run(run)
    finally:
        if record is not None:
            record.close()


async def _run(args: argparse.Namespace, cfg: config.Config, size: tuple[int, int], password: str | None,
               script: list[str] | None, record: IO[str] | None) -> int:
    from ..app import EngineError, TsbTuiApp
    from ..theme import ThemeError

    try:
        app = TsbTuiApp(args.broker, ca_file=args.ca_file, cfg=cfg)
    except (EngineError, ThemeError, ValueError) as exc:
        source = getattr(args, "broker_source", None)
        prefix = f"broker.url ({source}): " if isinstance(exc, ValueError) and source else ""
        print(f"tsb-tui drive: {prefix}{exc}", file=sys.stderr)
        return 1
    out = Transcript(sys.stdout, record)
    async with app.run_test(size=size) as pilot:
        driver = Driver(app, pilot, emit=out.outbound)
        try:
            if password is not None:
                try:
                    await driver.log_in(args.user, password, password_path=args.password_file)
                    password = None
                except DriveError as exc:
                    print(f"tsb-tui drive: login failed: {exc.why}", file=sys.stderr)
                    return 1
            out.outbound(driver.ready(size))
            lines = script_lines(script) if script is not None else stdin_lines(sys.stdin)
            return await driver.serve(lines, out, script=script is not None)
        finally:
            driver.close()
