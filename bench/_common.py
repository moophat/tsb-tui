"""What the two TUI benchmarks share: the import paths, a server that runs in a process of its own and generates
output, the keystroke probe, the loop monitor and the numbers' arithmetic (plan DT8).

Why the server is a separate process: it generates output, answers REST and pings, and in the same process it would
take the interpreter lock from the TUI it is meant to be measuring, so a miss would not say whose it was. It is the
contract's own in-process fixture server (`tsbc._serve`), subclassed, and run under `multiprocessing` in a child.

This module imports only the stdlib, `tsb_tui` and `tsbc`, so the import-lint passes it as shipped code. It opens no
socket itself: the server is `tsbc._serve.StubServer`, the client is the TUI's own.
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
import os
import queue
import random
import statistics
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

# Make `tsb_tui` and the pinned contract importable when run from the repository root without an install, the way
# `tests/conftest.py` does for the tests.
_HERE = Path(__file__).resolve().parent
TUI_DIR = _HERE.parent
REPO_ROOT = TUI_DIR
CONTRACT_DIR = REPO_ROOT / "program" / "contract"
for _entry in (TUI_DIR, CONTRACT_DIR, CONTRACT_DIR / "client"):
    if _entry.is_dir() and str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from tsbc import _serve
from tsbc import _wsframe as wf

from tsb_tui import config
from tsb_tui.app import TsbTuiApp

DEFAULT_LIBRARY = REPO_ROOT / "vendor" / "libghostty-vt" / "libghostty-vt.so"
CHUNK_BYTES = 8192
# Letters the generated streams never contain: a keystroke's echo is told from the stream by its letter.
MARKERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GATE_P95_MS = 50.0  # design §3.4 test 4: keystroke to screen
FILTER_GATE_MS = 100.0  # design §3.4 test 6: filtering 500 sessions


# -- the numbers --


def percentile(samples: list[float], fraction: float) -> float:
    """Nearest-rank percentile, the way `engine_read.py` takes its p95; 0.0 for no samples."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, max(0, round(fraction * len(ordered)) - 1))]


def summary(samples: list[float]) -> dict[str, float]:
    return {
        "n": float(len(samples)),
        "p50": statistics.median(samples) if samples else 0.0,
        "p95": percentile(samples, 0.95),
        "max": max(samples) if samples else 0.0,
    }


# -- the streams the server generates --


def yes_chunk() -> bytes:
    """What `yes` prints on a terminal: `y` and a line end, as many as fill a chunk."""
    line = b"y\r\n"
    return line * (CHUNK_BYTES // len(line))


def tech_lines(count: int = 4000) -> list[bytes]:
    """A long `show tech-support`: lines of mixed length, some with colour, from a seeded generator so runs compare.
    Lowercase, digits and punctuation only: a keystroke's marker letter is uppercase, so it is never in the stream."""
    rnd = random.Random(7)
    words = ("interface", "address", "counters", "input", "output", "errors", "drops", "queue", "buffer", "route",
             "table", "entry", "state", "up", "down", "bgp", "ospf", "neighbor", "area", "vlan", "trunk", "mtu")
    lines: list[bytes] = []
    for i in range(count):
        body = " ".join(rnd.choice(words) for _ in range(rnd.randint(3, 16)))
        text = f"{i:05d} {body} {rnd.randint(0, 99999)}"
        if i % 11 == 0:
            text = f"\x1b[1;3{rnd.randint(1, 6)}m{text}\x1b[0m"
        lines.append(text.encode("ascii") + b"\r\n")
    return lines


class Stream:
    """Chunks of one kind of output, one after another and for ever."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._yes = yes_chunk()
        self._lines = tech_lines() if kind == "tech" else []
        self._at = 0

    def next_chunk(self) -> bytes:
        if self.kind == "yes":
            return self._yes
        out, size = [], 0
        while size < CHUNK_BYTES:
            line = self._lines[self._at % len(self._lines)]
            self._at += 1
            out.append(line)
            size += len(line)
        return b"".join(out)


# -- the server --


# The core closes a client whose queue passes this (session-detach rule 3; `core/internal/router/router.go`).
QUEUE_LIMIT_BYTES = 8 * 1024 * 1024
CLOSE_TOO_SLOW = 4011


def _is_marker(data: bytes) -> bool:
    """A keystroke's letter, or its echo: one uppercase byte (the streams never contain one)."""
    return len(data) == 1 and data.isupper()


class Outbox:
    """One queue and one writer thread for one client, as the core does it. Everything the server sends a client goes
    through `put`, which never blocks: a client that reads slowly fills its own queue and nobody else's, so it can no
    longer hold a lock that delays another client's echo or pings. A queue past `QUEUE_LIMIT_BYTES` gets a close
    frame with 4011 and no more output, which is the core's rule for a client that cannot keep up."""

    def __init__(self, app: BenchApp, ws: Any) -> None:
        self.app, self.ws = app, ws
        self.items: queue.SimpleQueue = queue.SimpleQueue()
        self.queued = 0
        self.closed = False
        self.max_queued = 0
        self.thread = threading.Thread(target=self._write, daemon=True)

    def put(self, opcode: int, payload: bytes, *, fin: bool = True) -> None:
        if self.closed:
            return
        self.queued += len(payload)
        self.max_queued = max(self.max_queued, self.queued)
        self.items.put((opcode, payload, fin))

    def stop(self) -> None:
        self.items.put(None)

    def _write(self) -> None:
        send = _serve.ServerWS.send
        while (item := self.items.get()) is not None:
            opcode, payload, fin = item
            if self.closed:
                continue
            if self.queued > QUEUE_LIMIT_BYTES:
                self.closed = True
                with contextlib.suppress(OSError):
                    send(self.ws, wf.OP_CLOSE, wf.encode_close(CLOSE_TOO_SLOW, "too slow"))
                self.ws.closed = True
                self.app.dropped_for_queue += 1
                continue
            now = time.perf_counter()
            if opcode == wf.OP_PING:
                self.app.note_ping(payload, now)
            elif opcode == wf.OP_BINARY and _is_marker(payload):
                self.app.echo_written.append((payload.decode(), now))
            try:
                send(self.ws, opcode, payload, fin=fin)
            except OSError:
                self.closed = True
            self.queued -= len(payload)


class BenchApp(_serve.ServeApp):
    """The contract's fixture server, with a firehose, pings it counts the answers to, optional extra sessions
    for the fleet and an optional delay on `GET /sessions`. Every client has its own queue and writer (`Outbox`), so
    delivery never blocks the server's lock."""

    def __init__(self, *, stream: str = "none", mb_per_s: float = 4.0, ping_s: float = 0.25,
                 extra_sessions: int = 0, poll_delay_s: float = 0.0) -> None:
        super().__init__()
        self.stream = Stream(stream) if stream != "none" else None
        self.mb_per_s = mb_per_s
        self.ping_s = ping_s
        self.poll_delay_s = poll_delay_s
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.pinged: dict[bytes, float] = {}  # a ping's payload -> when it was sent
        self.ponged: set[bytes] = set()
        self.pong_ms: list[float] = []
        self.pings_sent = 0
        self.bytes_sent = 0
        self.quiesced_at: float | None = None
        self.dropped_for_queue = 0
        self.key_recv: list[tuple[str, float]] = []  # each keystroke's letter and when the server read it
        self.echo_written: list[tuple[str, float]] = []  # each echo's letter and when its writer sent it
        self._outboxes: list[Outbox] = []
        self._ping_lock = threading.Lock()
        for n in range(extra_sessions):
            self._add_listed_session(n)

    def _add_listed_session(self, n: int) -> None:
        """One more session in the fleet, as `GET /sessions` lists it: owned, detached, a target of its own."""
        status, _headers, reply = self._create(_serve.USER, {
            "target_id": _serve.TARGET, "connection": "new", "term": "xterm-256color", "cols": 80, "rows": 24})
        assert status == 201, reply
        sess = self.sessions[reply["session"]["id"]]
        sess["target_id"] = f"rtr-{('core', 'edge', 'dist', 'access')[n % 4]}-{n:03d}"
        if n % 25 == 0:
            sess["state"] = "unreachable"  # a few sessions that need the operator

    # -- the firehose and the pings --

    def start(self) -> None:
        if self.stream is not None:
            self._threads.append(threading.Thread(target=self._feed, daemon=True))
        if self.ping_s > 0:
            self._threads.append(threading.Thread(target=self._ping, daemon=True))
        for thread in self._threads:
            thread.start()

    def _live(self) -> list[dict]:
        return [s for s in list(self.sessions.values()) if s["state"] == "live" and self.clients.get(s["id"])]

    def _feed(self) -> None:
        """Output to every attached session, at `mb_per_s` each. The ring is kept short: a dump is only what a
        reattach would replay, and the bench must not grow without bound."""
        assert self.stream is not None
        started, sent = time.perf_counter(), 0
        while not self._stop.is_set():
            live = self._live()
            if not live:
                time.sleep(0.01)
                started, sent = time.perf_counter(), 0
                continue
            for sess in live:
                chunk = self.stream.next_chunk()
                with self.lock:
                    self._emit_output(sess, chunk)
                    del sess["output"][:-8]
                sent += len(chunk)
                self.bytes_sent += len(chunk)
            ahead = started + sent / (self.mb_per_s * 1e6 * len(live)) - time.perf_counter()
            if ahead > 0:
                time.sleep(ahead)

    def note_ping(self, payload: bytes, at: float) -> None:
        """A ping is timed from the moment its writer sends it, not from when it was queued: what is measured is the
        client's answer, and the queue is the server's own (the core writes pings between messages too)."""
        with self._ping_lock:
            self.pinged[payload] = at
            self.pings_sent += 1

    def _ping(self) -> None:
        """A ping to every attached client every `ping_s`: session-detach 9 says a client that does not answer is
        detached, and the answers' delay is the client's. Queued, never written here."""
        n = 0
        while not self._stop.wait(self.ping_s):
            if self.quiesced_at is not None:
                return
            n += 1
            for clients in list(self.clients.values()):
                for client_id, ws in list(clients.items()):
                    ws.send(wf.OP_PING, f"bench-{n}-{client_id}".encode())

    def quiesce(self) -> None:
        """Stop pinging. Called when the measured window ends and before the TUI shuts down, so that a ping the TUI
        never reads because it is closing is not counted as unanswered."""
        self.quiesced_at = time.perf_counter()

    def websocket(self, path: str, ws: Any) -> None:
        # The core's sockets are TCP_NODELAY (Go's default); without it a one-byte echo queued behind a larger write
        # waits for the client's delayed ACK, about 40 ms, which is the server's doing and not the TUI's. The
        # constants are written out because the benches import no socket module (DT8): IPPROTO_TCP, TCP_NODELAY.
        sock = ws._socket()
        if sock is not None:
            sock.setsockopt(6, 1, 1)
        outbox = Outbox(self, ws)
        self._outboxes.append(outbox)
        outbox.thread.start()
        ws.send = outbox.put
        original = ws.recv

        def recv():
            message = original()
            if message.kind == "pong" and message.data in self.pinged:
                self.pong_ms.append((time.perf_counter() - self.pinged[message.data]) * 1000.0)
                self.ponged.add(message.data)
            elif message.kind == "binary" and _is_marker(message.data):
                self.key_recv.append((message.data.decode(), time.perf_counter()))
            return message

        ws.recv = recv
        try:
            super().websocket(path, ws)
        finally:
            outbox.stop()

    def http(self, method, path, headers, body):
        if self.poll_delay_s and (method, path) == ("GET", "/sessions"):
            time.sleep(self.poll_delay_s)
        return super().http(method, path, headers, body)

    def stats(self) -> dict[str, Any]:
        """What the run needs from the server's side, once it is over. Only pings sent more than a second before the
        window ended (`quiesce`) are judged: a later one may be on its way, or unread because the TUI is closing."""
        horizon = (self.quiesced_at if self.quiesced_at is not None else time.perf_counter()) - 1.0
        unanswered = sum(1 for payload, sent in self.pinged.items() if sent < horizon and payload not in self.ponged)
        return {
            "pings_sent": self.pings_sent,
            "pongs": len(self.pong_ms),
            "pings_unanswered": unanswered,
            "pong_ms": summary(self.pong_ms),
            "bytes_sent": self.bytes_sent,
            "max_queued_bytes": max((o.max_queued for o in self._outboxes), default=0),
            "dropped_for_queue": self.dropped_for_queue,
            "key_recv": list(self.key_recv),
            "echo_written": list(self.echo_written),
        }

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(2.0)


def _serve_main(conn, options: dict[str, Any]) -> None:
    """The child's whole life: build the app, serve it, tell the parent the URL, wait to be told to stop, answer
    with the server's side of the numbers."""
    app = BenchApp(**options)
    with _serve.StubServer(app) as url:
        app.start()
        conn.send(url)
        while conn.recv() == "quiesce":
            app.quiesce()
            conn.send("quiesced")
        app.stop()
        conn.send(app.stats())


class ServerProcess:
    """`with ServerProcess(stream="yes") as server:` — `server.url` while it runs, `server.stats` after it."""

    def __init__(self, **options: Any) -> None:
        self._options = options
        self.url = ""
        self.stats: dict[str, Any] = {}
        self._process: multiprocessing.process.BaseProcess | None = None
        self._conn = None

    def __enter__(self) -> Self:
        context = multiprocessing.get_context("spawn")
        self._conn, child = context.Pipe()
        self._process = context.Process(target=_serve_main, args=(child, self._options), daemon=True)
        self._process.start()
        if not self._conn.poll(60):
            self._process.kill()
            raise RuntimeError("the bench server did not start within 60 s")
        self.url = self._conn.recv()
        return self

    def quiesce(self) -> None:
        """The measured window is over: the server stops pinging, before the TUI starts to shut down."""
        assert self._conn is not None
        self._conn.send("quiesce")
        if not self._conn.poll(30) or self._conn.recv() != "quiesced":
            raise RuntimeError("the bench server did not stop pinging")

    def __exit__(self, *exc: object) -> None:
        assert self._process is not None and self._conn is not None
        try:
            self._conn.send("stop")
            if self._conn.poll(30):
                self.stats = self._conn.recv()
        finally:
            self._process.join(10)
            if self._process.is_alive():
                self._process.kill()


# -- the app under measurement --


def make_config(library: str | os.PathLike, **overrides: Any) -> config.Config:
    """The default profile, `size.auto_request` off (the benchmark asks for no size) and the engine at `library`."""
    values = {"size.auto_request": "false", "term.library": str(library)}
    values.update({key: value for key, value in overrides.items()})
    return config.load(config_file=Path("/nonexistent"), environ={}, overrides=values)


async def wait_for(predicate: Callable[[], bool], what: str, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise RuntimeError(f"timed out after {timeout_s:g}s waiting for {what}")


async def log_in(app: TsbTuiApp, pilot) -> Any:
    """Log in through the form, as a person does, and wait for the workspace."""
    from tsb_tui.screens.terminal import TerminalScreen

    screen = app.screen
    screen.query_one("#username").value = _serve.USER
    screen.query_one("#password").value = _serve.PASSWORD
    await pilot.click("#login-button")
    await wait_for(lambda: isinstance(app.screen, TerminalScreen) and app.screen.is_mounted
                   and app.screen.sidebar is not None, "login to reach the workspace")
    return app.screen


async def open_sessions(app: TsbTuiApp, screen, count: int, cols: int, rows: int) -> list[str]:
    """Create `count` sessions of `cols`x`rows` and open them here, the first in front and the rest behind, each
    past the gate and on its grid."""
    ids = [app.client.create_session(_serve.TARGET, "new", "xterm-256color", cols, rows)["session"]["id"]
           for _ in range(count)]
    for index, sid in enumerate(ids):
        app.gated_sessions.add(sid)
        screen.open_session(app.client.session(sid), front=index == 0)
        await wait_for(lambda sid=sid: (slot := screen.slot_for(sid)) is not None and slot.pump is not None
                       and slot.pump.state.dump_seen, f"{sid} to attach")
    return ids


async def fit_canvas(pilot, screen, cols: int, rows: int) -> None:
    """Resize the window until the front canvas is exactly `cols`x`rows`, whatever the rail and the sidebar take."""
    width, height = cols + 8, rows + 2
    for _ in range(6):
        await pilot.resize_terminal(width, height)
        await pilot.pause(0.05)
        size = screen.canvas.size
        if (size.width, size.height) == (cols, rows):
            return
        width += cols - size.width
        height += rows - size.height
    raise RuntimeError(f"could not fit the canvas to {cols}x{rows}: it is {screen.canvas.size}")


# -- measuring --


class LoopMonitor:
    """How long the event loop was kept from running anything else: a task that asks to wake every `period_s` and
    records how late it was. The longest is the longest stall."""

    def __init__(self, period_s: float = 0.005) -> None:
        self.period_s = period_s
        self.lags_ms: list[float] = []
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        last = time.perf_counter()
        while True:
            await asyncio.sleep(self.period_s)
            now = time.perf_counter()
            self.lags_ms.append(max(0.0, now - last - self.period_s) * 1000.0)
            last = now

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> dict[str, float]:
        assert self._task is not None
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        return summary(self.lags_ms)


@dataclass
class KeyLatency:
    """One keystroke's journey, stamp by stamp. Client side: posted to the screen; its echo read by the pump (`sunk`:
    the frame has arrived and been taken off the socket); the engine write started and ended (`fed`, `fed_end`); the
    canvas rebuilt its rows (`repainted`); Textual first drew a line of the canvas (`drawn`). Server side, joined after
    the run: the server read the key (`recv`) and its writer sent the echo (`written`). Every stamp is
    `time.perf_counter()`, a monotonic clock the server's process shares."""

    marker: str
    posted: float
    sunk: float | None = None
    fed: float | None = None
    fed_end: float | None = None
    repainted: float | None = None
    drawn: float | None = None
    recv: float | None = None
    written: float | None = None

    @property
    def total_ms(self) -> float | None:
        return None if self.drawn is None else (self.drawn - self.posted) * 1000.0


# The stages of a keystroke's journey, in order: (name, later stamp, earlier stamp). A stage is only reported for the
# keys that have both stamps. `key sent` is the posting and its trip to the server; `echo read` is its wait in the
# socket and in the reader; `echo fed` is its wait behind the read-ahead and any run ahead of it, on the way into the
# engine. Those three are the split `->grid` is reported as (design note §4), and the rest say where the rest of the
# journey went.
STAGES = (
    ("key sent", "recv", "posted"),
    ("server queue", "written", "recv"),
    ("echo read", "sunk", "written"),
    ("echo fed", "fed", "sunk"),
    ("engine write", "fed_end", "fed"),
    ("feed->paint", "repainted", "fed_end"),
    ("paint->draw", "drawn", "repainted"),
)
# And the two spans the stages add up to: what the pump and the screen do with a frame once it arrives, and the whole.
SPANS = (("echo->screen", "drawn", "written"), ("key->screen", "drawn", "posted"))


@dataclass
class Probe:
    """Keystroke-to-screen on the canvas in front.

    A keystroke is an uppercase letter the stream never contains; the server echoes it as output, and the probe
    watches that letter arrive at the pump (`_sink`, hooked on the screen before any session opens), the engine take it
    (`feed_run`, the attachment's own path, not `feed`), the canvas rebuild its rows (`repaint_now`) and Textual draw a
    line of it (`render_line`). The last is
    "on screen" here: the headless driver never writes the final bytes to a real terminal, so the outer terminal's own
    draw is not in the number. Under a firehose the echo scrolls away within a frame or two, so the screen is not
    searched for the letter: the paint that followed its arrival is what carries it."""

    canvas: Any = None
    pending: dict[str, KeyLatency] = field(default_factory=dict)
    done: list[KeyLatency] = field(default_factory=list)
    sent: list[KeyLatency] = field(default_factory=list)
    _repainted: list[KeyLatency] = field(default_factory=list)

    def hook_sink(self, screen: Any) -> None:
        """Stamp the moment the pump hands an echo to the screen. The pump takes the screen's `_sink` when a session
        attaches, so this is done before the first session opens."""
        real = screen._sink

        def sunk(slot, kind: str, data: bytes) -> None:
            now = time.perf_counter()
            if kind == "output" and slot.canvas is self.canvas:
                for marker, key in self.pending.items():
                    if key.sunk is None and marker.encode() in data:
                        key.sunk = now
            real(slot, kind, data)

        screen._sink = sunk

    def install(self, canvas: Any) -> None:
        """Hook the path the attachment's output actually takes (DT20): `feed_run`, which runs on the
        attachment's feed thread, not `feed`, which nothing on that path calls any more."""
        self.canvas = canvas
        feed_run, repaint, render_line = canvas.feed_run, canvas.repaint_now, canvas.render_line

        def probed_feed_run(role: str, data: bytes, **kwargs) -> list:
            start = time.perf_counter()
            replies = feed_run(role, data, **kwargs)
            end = time.perf_counter()
            if role == "output":
                for marker, key in list(self.pending.items()):
                    if key.fed is None and marker.encode() in data:
                        key.fed, key.fed_end = start, end
            return replies

        def probed_repaint() -> None:
            repaint()
            now = time.perf_counter()
            for key in self.pending.values():
                if key.fed is not None and key.repainted is None:
                    key.repainted = now
                    self._repainted.append(key)

        def probed_render_line(y: int):
            line = render_line(y)
            if self._repainted:
                now = time.perf_counter()
                for key in self._repainted:
                    key.drawn = now
                    self.pending.pop(key.marker, None)
                    self.done.append(key)
                self._repainted.clear()
            return line

        canvas.feed_run, canvas.repaint_now, canvas.render_line = (
            probed_feed_run, probed_repaint, probed_render_line)

    async def type_keys(self, screen, count: int, spacing_s: float, *, timeout_s: float = 3.0) -> int:
        """Post `count` keystrokes to the screen, one at a time, waiting for each to be drawn. Returns how many never
        were (a keystroke the session did not echo, or a screen that never drew it)."""
        from textual import events

        lost = 0
        for n in range(count):
            marker = MARKERS[n % len(MARKERS)]
            key = KeyLatency(marker, time.perf_counter())
            self.pending[marker] = key
            self.sent.append(key)
            screen.post_message(events.Key(marker, marker))
            deadline = time.monotonic() + timeout_s
            while key.drawn is None and time.monotonic() < deadline:
                await asyncio.sleep(0.001)
            if key.drawn is None:
                lost += 1
                self.pending.pop(marker, None)
            await asyncio.sleep(spacing_s)
        return lost

    def totals_ms(self) -> list[float]:
        return [t for key in self.done if (t := key.total_ms) is not None]

    def join_server(self, key_recv: list[tuple[str, float]], echo_written: list[tuple[str, float]]) -> None:
        """Add the server's two stamps to each key: the n-th time a letter was typed is the n-th time the server read
        it and the n-th time its writer sent it back."""
        for stamps, attribute in ((key_recv, "recv"), (echo_written, "written")):
            by_marker: dict[str, list[float]] = {}
            for marker, at in stamps:
                by_marker.setdefault(marker, []).append(at)
            seen: dict[str, int] = {}
            for key in self.sent:
                n = seen.get(key.marker, 0)
                seen[key.marker] = n + 1
                if n < len(by_marker.get(key.marker, ())):
                    setattr(key, attribute, by_marker[key.marker][n])

    def stage_stats(self) -> dict[str, dict[str, float]]:
        """Each stage and span of the journey, p50, p95 and max in milliseconds, over the keys that have both of its
        stamps. A key whose echo was lost has no draw and no stage after its last stamp."""
        out: dict[str, dict[str, float]] = {}
        for name, later, earlier in (*STAGES, *SPANS):
            samples = [(getattr(k, later) - getattr(k, earlier)) * 1000.0 for k in self.done
                       if getattr(k, later) is not None and getattr(k, earlier) is not None]
            out[name] = summary(samples)
        return out


def library_arg(parser: Any) -> None:
    parser.add_argument("--library", default=str(DEFAULT_LIBRARY), help="path to libghostty-vt.so")
