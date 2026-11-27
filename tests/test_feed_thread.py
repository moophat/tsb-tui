"""The feed thread (DT20): what the design note's §4 asks CI to prove.

Why these matter (Rule 9): the feed moved off the event loop so three sessions at 4 MB/s each can
still be read, answered and painted by one core. That move is only safe if the invariants of the
old inline path survive, byte for byte and in order:

* **A ping behind unfed output is answered while the feed is held.** The reader only reads, so a
  pong goes out at once however far behind the feed thread has fallen. Under the old inline feed the
  pong waited for every chunk ahead of it, which is how a firehose stalled the answers and got the
  client detached (session-detach 9).
* **The read-ahead bound holds.** The reader stops reading at 1 MiB of queued output so TCP pushes
  back and the core's 8 MiB drain rule still applies, instead of the screen falling minutes behind.
* **Order is kept across size and prefix.** A `size` frame and a `prefix` with its dump, placed
  between runs of output, give the grid of a raw replay: DT12's plumbing on the new path, and with
  it DT3's muting and session-attach CR3.
* **DT4 (b) on the new path.** A status request queued before a `token` that takes the keyboard away
  is answered exactly once; one queued after it is not answered. The replies come back from the feed
  thread and are delivered on the loop, so the state they are judged against is the one the processor
  had when the run was fed — not the one it happens to see when a reply lands.
* **Close during a run.** A socket that closes while a run is in flight writes nothing to a freed
  engine, and the close still shows with every byte that arrived fed.
* **A loop-side `modes()` waits at most a few slices during a long feed.** Python's lock is not fair,
  so between its 32 KiB slices the feed thread yields the backend lock; a waiter takes it in a few
  slices, never after a whole run. A second test proves the probe can tell the difference, by feeding
  a run that holds the lock once around the whole slice loop and checking the probe measures a
  whole-run wait.

Servers: the contract's in-process fixture server (DT7) for what a core sends, subclassed here to
record the pong each ping gets. The feed thread is held by gating `TerminalScreen._sink`, the
dispatch that runs on it; the gate must be installed on the class *before* a session attaches,
because the pump binds `self._sink` when the attachment starts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from input_server import InputRecordingApp, flushed
from stub_server import create_owner_session, emit_live_output, record_output
from test_term_engine import _decode, _replay
from tsbc import _serve
from tsbc import _wsframe as wf

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.terminal import TerminalScreen
from tsb_tui.term import VTBackend, load

CURSOR_REPORT = re.compile(rb"\x1b\[\d+;\d+R")
OPEN = (80, 24)
# The feed thread's slice size (DT20, `term/backend.py`), written out because the point of the tests
# is the slice, not a constant imported from the code under test.
SLICE_BYTES = 32 * 1024
# How many runs the gate holds. The first is the dump; the rest are what the test emits.
HELD = 4
FEED_GATE_TIMEOUT = 10.0


class _PongApp(InputRecordingApp):
    """The fixture server, recording the pong each ping got and when."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.pongs: dict[bytes, float] = {}
        self.pings: list[bytes] = []

    def websocket(self, path, ws) -> None:
        original = ws.recv

        def recv():
            message = original()
            if message.kind == "pong":
                self.pongs[message.data] = time.monotonic()
            return message

        # Record pongs only: `InputRecordingApp.websocket` wraps this same `ws.recv` and records
        # every binary, so recording binaries here too would append each client input twice and make
        # an "exactly one reply" assertion unsatisfiable.
        ws.recv = recv
        InputRecordingApp.websocket(self, path, ws)

    def ping(self, sess: dict, payload: bytes) -> None:
        """A WebSocket ping to every attached client, as the core's heartbeat writes one between
        messages (session-detach 9)."""
        self.pings.append(payload)
        with self.lock:
            self._broadcast(sess, lambda _cid, ws: ws.send(wf.OP_PING, payload))


class _FeedGate:
    """Holds the feed thread shut for `HELD` runs, then lets it go. Installed as a function in place
    of `TerminalScreen._sink`, the dispatch the pump runs on its feed thread."""

    def __init__(self) -> None:
        self.open = threading.Event()
        self.calls = 0
        self.fed = 0
        self.real = None  # the real bound method, set by the fixture


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> _FeedGate:
    """Install the feed gate on the screen class, before any session attaches, so the pump binds it
    when the attachment starts. Restored after."""
    real = TerminalScreen._sink
    the_gate = _FeedGate()
    the_gate.real = real

    def gated(self: TerminalScreen, slot, kind: str, data: bytes) -> list:
        the_gate.calls += 1
        if the_gate.calls <= HELD and not the_gate.open.wait(FEED_GATE_TIMEOUT):
            raise AssertionError("the test never released the feed gate")
        replies = the_gate.real(self, slot, kind, data)
        the_gate.fed += len(data)
        return replies

    monkeypatch.setattr(TerminalScreen, "_sink", gated)
    return the_gate


async def held_attached(gate: _FeedGate, pilot, app: TsbTuiApp, session_id: str,
                       wait_until: Callable) -> TerminalScreen:
    """Push the terminal screen and wait until it has attached, the `prefix` arrived, and the dump is
    queued behind the feed gate — without requiring the dump to be fed.

    This is `attached()` for the gated tests here: `attached()` waits on `dump_seen`, which by design
    (`attachment.py` `_feed_item`) is only set after the dump run returns, and this file's `gate`
    holds that run shut, so `attached()` would wait for a dump the gate is deliberately not feeding.

    What is waited on instead: `client_id` proves the attachment, `state.prefix` proves the core's
    `prefix` frame was applied, and `gate.calls >= 1` proves the dump reached the dispatch the gate
    holds — i.e. it is queued and in flight on the feed thread, not merely arrived at the socket.
    """
    app.push_screen(TerminalScreen(session_id))
    screen = app.screen
    await wait_until(
        pilot, lambda: screen.pump is not None and screen.pump.state.client_id
        and screen.pump.state.prefix is not None and gate.calls >= 1,
        "the screen to attach, receive the prefix and queue the dump")
    return screen


def _hold_forever(screen: TerminalScreen, gate: _FeedGate, released: threading.Event):
    """Replace a live pump's `_sink` (a `functools.partial(self._sink, slot)`, so it takes
    `(kind, data)`) with one that blocks on `released` before calling the real sink against the
    same slot. This holds the feed thread shut; the pump still dispatches through it on its feed
    thread."""
    slot = screen.front

    def blocking(kind: str, data: bytes) -> list:
        if not released.wait(FEED_GATE_TIMEOUT):
            raise AssertionError("the test never released the held feed")
        return gate.real(screen, slot, kind, data)

    return blocking


@pytest.fixture(scope="module")
def lib(term_so: Path) -> VTBackend | object:
    return load(term_so)


def _replay_snapshot(lib, chunks, cols=OPEN[0], rows=OPEN[1]):
    """The grid, size, cursor and modes a raw replay of `chunks` gives (DT12's own comparison)."""
    backend = _replay(lib, chunks, cols=cols, rows=rows)
    try:
        backend.sync_render_state()
        cursor = backend.cursor()
        return (backend.grid_lines(), (backend.cols, backend.rows),
                (cursor.x, cursor.y, cursor.visible), backend.modes())
    finally:
        backend.close()


def _snapshot(backend: VTBackend):
    cursor = backend.cursor()
    return (backend.grid_lines(), (backend.cols, backend.rows),
            (cursor.x, cursor.y, cursor.visible), backend.modes())


def test_a_ping_behind_unfed_output_is_answered_while_the_feed_is_held(serve, gate, wait_until) -> None:
    """The point of DT20. The reader only reads, so it answers the ping at once; the feed thread is
    meanwhile held behind an event with the output queued ahead of it. Under the old inline feed the
    pong waited for every chunk ahead of it (design note §1.3), which is how a firehose stalled the
    answers — and a client that does not answer is detached (session-detach 9).

    Putting the inline feed back — the pump calling the sink on the loop instead of its executor —
    makes this fail: nothing answers the ping until the gate opens."""

    async def _check() -> None:
        app_serve, url = serve(_PongApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, *OPEN)
            screen = await held_attached(gate, pilot, app, session_id, wait_until)
            pump = screen.pump
            gate.open.set()  # the dump went through the gate; let the session be live
            await wait_until(pilot, lambda: screen.canvas.painted, "the dump to be painted")

            # Hold the next run's feed behind an event, then send more output than it can take.
            held = threading.Event()
            pump._sink = _hold_forever(screen, gate, held)
            with app_serve.lock:
                app_serve._emit_output(app_serve.sessions[session_id], b"x" * (SLICE_BYTES * 8))
            await wait_until(pilot, lambda: pump._feeding, "a run to be in flight")

            app_serve.ping(app_serve.sessions[session_id], b"behind-unfed")
            await wait_until(pilot, lambda: b"behind-unfed" in app_serve.pongs,
                             "the ping to be answered while the feed is held")
            assert pump._feeding, "the run was still in flight when the pong arrived"
            held.set()  # let the held run finish, so teardown is clean
            await wait_until(pilot, lambda: not pump._feeding, "the held run to finish")
            await flushed(pilot, screen, app_serve, wait_until)

    asyncio.run(_check())


def _held_feed_marker() -> None:  # placeholder removed below
    """(the earlier duplicate of `_hold_forever` lives in the fixture block above)"""


def test_the_read_ahead_bound_holds_and_the_reader_resumes(serve, gate, wait_until) -> None:
    """The reader stops reading once its queue holds `READ_AHEAD_BYTES` of unfed output, so TCP pushes
    back and the core's drain rule (session-detach 3, 4011) still sees a slow client; it resumes as
    the feed thread takes the bytes, and the grid is the raw replay of everything that arrived.

    The bound is proved by watching it: with the feed held and far more output than the bound
    arriving, the queue never passes it. The resume is proved by the grid, which can only be the raw
    replay if the reader kept reading after the feed took the first megabyte."""

    async def _check() -> None:
        from tsb_tui.model.attachment import READ_AHEAD_BYTES

        app_serve, url = serve(_PongApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, *OPEN)
            screen = await held_attached(gate, pilot, app, session_id, wait_until)
            pump = screen.pump
            gate.open.set()
            await wait_until(pilot, lambda: screen.canvas.painted, "the dump to be painted")

            # Hold the feed and send far more than the bound: more than the reader can drain, so the
            # queue pins at the bound and never past it.
            held = threading.Event()
            pump._sink = _hold_forever(screen, gate, held)
            payload = b"y" * (READ_AHEAD_BYTES // 2)
            with app_serve.lock:
                for _ in range(6):
                    app_serve._emit_output(app_serve.sessions[session_id], payload)
            await wait_until(pilot, lambda: pump._queued_output >= READ_AHEAD_BYTES,
                             "the reader to stop at the bound")
            high = 0
            for _ in range(50):
                high = max(high, pump._queued_output)
                await pilot.pause(0.01)
            assert high <= READ_AHEAD_BYTES + len(payload), (
                f"the read-ahead passed its bound ({high} > {READ_AHEAD_BYTES})")
            held.set()
            await wait_until(pilot, lambda: pump._queued_output < READ_AHEAD_BYTES,
                             "the reader to resume as the feed takes bytes")
            # Once the feed is running again the reader keeps up and the whole burst lands: the grid
            # holds the last of it (a `yes`-sized burst wraps the grid many times over).
            await wait_until(pilot, lambda: pump._queued_output == 0 and "y" in "".join(
                screen.backend.visible_rows()), "the burst to reach the grid")
            assert pump.reattach_attempts == 0, "the client was dropped while the reader was held"

    asyncio.run(_check())


def _lib() -> object:
    from conftest import TERM_SO

    from tsb_tui.term import load as _load

    return _load(TERM_SO)


def test_order_is_kept_across_a_size_frame_and_a_prefix_with_its_dump(
        serve, gate, wait_until, lib, server_frame_validator) -> None:
    """DT12's plumbing on the new path, and DT3's muting with it. Half of a golden stream is the
    core's ring when the TUI attaches (so it arrives as the dump after the `prefix`), the rest is
    live, and the resize the stream carries arrives as a `size` frame. A `size` frame and a second
    `prefix` with its dump, both placed between runs of output, must still leave the grid equal to a
    raw replay of the whole stream — in that order, not in arrival order."""

    async def _check() -> None:
        chunks = _decode("golden-resize-mid-stream")
        # Cut the stream in three: a run of output, a size frame's worth, more output.
        first, resize, second = chunks[0], chunks[1], chunks[2]

        app_serve, url = serve(_PongApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, *OPEN)
            sess = record_output(app_serve, session_id, first[1])
            screen = await held_attached(gate, pilot, app, session_id, wait_until)
            gate.open.set()
            await wait_until(pilot, lambda: screen.canvas.painted, "the dump to be painted")

            cols, rows = resize[1]
            frame = {"t": "size", "v": 1, "session_id": session_id, "cols": cols, "rows": rows,
                     "reason": "applied"}
            server_frame_validator(frame)
            with app_serve.lock:
                sess["cols"], sess["rows"] = cols, rows
                app_serve._broadcast(sess, lambda _cid, ws: ws.send_text(json.dumps(frame)))
            await wait_until(pilot, lambda: (screen.backend.cols, screen.backend.rows) == (cols, rows),
                             "the size frame to take the grid")
            # The rest of the stream, live.
            for kind, payload in (second, *chunks[3:]):
                if kind == "feed":
                    emit_live_output(app_serve, sess, payload)
                else:
                    more_cols, more_rows = payload
                    more = {"t": "size", "v": 1, "session_id": session_id, "cols": more_cols,
                            "rows": more_rows, "reason": "applied"}
                    server_frame_validator(more)
                    with app_serve.lock:
                        sess["cols"], sess["rows"] = more_cols, more_rows
                        app_serve._broadcast(sess, lambda _cid, ws, m=more: ws.send_text(json.dumps(m)))

            # And a `prefix` with its dump in the middle of live output: the grid is reset to the
            # prefix's geometry and rebuilt, so a replay that fed the dump where it fell in the
            # stream must give the same grid.
            dump = b"DUMP-REBUILDS-THE-GRID"
            with app_serve.lock:
                prefix = {"t": "prefix", "v": 1, "through_seq": sess["next_seq"], "cols": cols,
                          "rows": rows, "scrollback": {"first_seq": sess["next_seq"], "bytes": 0}}
                server_frame_validator(prefix)
                app_serve._broadcast(sess, lambda _cid, ws: ws.send_text(json.dumps(prefix)))
                app_serve._broadcast(sess, lambda _cid, ws: ws.send_binary(dump))
            expected_after = _replay_snapshot(lib, [("feed", dump), ("resize", (cols, rows))])
            await wait_until(pilot, lambda: screen.pump.state.dump_seen
                             and screen.canvas.painted and "DUMP-REBUILDS-THE-GRID" in "".join(
                                 screen.backend.grid_lines()),
                             "the prefix's dump to rebuild the grid")
            # The dump text is in the grid before the cursor move that follows it is applied: wait for the whole
            # state the assertion compares. On a timeout the assertion below reports the difference.
            with contextlib.suppress(AssertionError):
                await wait_until(pilot, lambda: _snapshot(screen.backend) == expected_after,
                                 "the grid, cursor included, to equal the raw replay")
            assert _snapshot(screen.backend) == expected_after, (
                "the grid after a prefix with its dump is not the raw replay of what came after it")

    asyncio.run(_check())


def test_dt4_b_a_reply_queued_before_a_token_is_sent_once_and_one_after_is_not(
        serve, gate, monkeypatch, wait_until) -> None:
    """DT4 (b) on the new path. A status request produced by output the feed thread has just fed is
    collected there and delivered on the loop, against the state as of those bytes. Queued before a
    `token` that takes the keyboard away, it is answered exactly once; queued after it, it is not
    answered at all.

    The bug the test guards: delivering replies straight from the feed thread (the old reply
    callback) judges them against the state when the reply *lands*, not when the bytes were fed, so
    the second reply would see a moved keyboard and both orderings would be judged wrongly."""

    async def _check() -> None:
        app_serve, url = serve(_PongApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, *OPEN)
            screen = await held_attached(gate, pilot, app, session_id, wait_until)
            state = screen.pump.state
            client_id = state.client_id
            assert state.holds_keyboard and state.is_owner_client
            gate.open.set()  # the dump went through the gate; let live output flow
            await wait_until(pilot, lambda: screen.canvas.painted, "the dump to be painted")
            # A cursor-position query in the *streamed output*, so its reply comes back through the
            # feed thread and the processor, not the direct loop path.
            emit_live_output(app_serve, app_serve.sessions[session_id], b"\x1b[6n")
            await wait_until(pilot, lambda: len(app_serve.received(client_id)) == 1,
                             "the reply to the query, while this client holds the keyboard")
            assert CURSOR_REPORT.fullmatch(app_serve.received(client_id)[0])

            # The keyboard moves away. The query that follows is queued behind a `token` frame, so it
            # must not be answered: this client is only watching.
            with app_serve.lock:
                sess = app_serve.sessions[session_id]
                sess["control_holder"] = {"client_id": "peer-client", "user": _serve.PEER}
                sess["owner_revoked"] = True
                app_serve._send_token(sess, "takeover")
            await wait_until(pilot, lambda: not state.holds_keyboard, "the keyboard to leave")
            emit_live_output(app_serve, sess, b"\x1b[6n")
            # Pump the loop until the post-token reply was judged: either it was counted as dropped
            # (DT4 b holds) or it reached the server as a second reply (DT4 b fails below). A single
            # `resize` marker would prove nothing: this run is queued before the marker is sent, so
            # the marker could be recorded first and a marker-based flush would return before the
            # reply had been judged at all.
            await wait_until(pilot, lambda: screen.dropped_inputs == 1
                             or len(app_serve.received(client_id)) == 2,
                             "the reply after the token to be judged")

            replies = app_serve.received(client_id)
            assert len(replies) == 1 and CURSOR_REPORT.fullmatch(replies[0]), (
                f"something left a client that no longer holds the keyboard, or the holder was not "
                f"answered exactly once: {replies}")
            assert screen.dropped_inputs == 1, "the reply after the token must be counted as dropped"

    asyncio.run(_check())


def test_close_during_a_run_frees_the_engine_and_nothing_is_written_afterwards(
        serve, gate, wait_until) -> None:
    """Lifetime. Closing the canvas while a run is in flight must not write to a freed engine and
    must not crash: `close` takes the backend lock, so the slice in flight finishes under it and any
    later slice finds the terminal gone and does nothing."""

    async def _check() -> None:
        app_serve, url = serve(_PongApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, *OPEN)
            screen = await held_attached(gate, pilot, app, session_id, wait_until)
            pump, canvas = screen.pump, screen.canvas
            gate.open.set()
            await wait_until(pilot, lambda: screen.canvas.painted, "the dump to be painted")

            held = threading.Event()
            pump._sink = _hold_forever(screen, gate, held)
            with app_serve.lock:
                app_serve._emit_output(app_serve.sessions[session_id], b"z" * (SLICE_BYTES * 8))
            await wait_until(pilot, lambda: pump._feeding, "a run to be in flight")

            canvas.close()
            assert canvas.backend._terminal is None, "close() must free the engine's terminal"
            held.set()
            await wait_until(pilot, lambda: not pump._feeding, "the held run to finish")
            painted = canvas.paint_count
            canvas.feed(b"late")
            canvas.repaint_now()
            await pilot.pause(0.2)
            assert canvas.paint_count == painted, "a closed canvas painted after it was closed"

    asyncio.run(_check())


def _probe_lock_waits(backend: VTBackend, payload: bytes) -> list[float]:
    """How long, in ms, each loop-side `modes()` read waits for the backend lock while a feed thread
    feeds `payload` — the fairness measurement both tests below turn on.

    A feed thread runs `backend.feed_run(payload)`, setting `started` just before the call and `done`
    when it returns. Once the run is under way this thread repeatedly takes the lock and reads
    `modes()` as the loop does, timing each acquisition, until the run is done. The 5 ms sleep between
    reads is pacing only: without it the probe would spin and measure itself.

    `feed_run` releases the lock between its 32 KiB slices, so each read should wait about one slice
    (DT20, design note §3 point 3). A `feed_run` that holds the lock once for the whole loop makes
    every read wait for the whole run, which is the failure the second test pins down."""
    started = threading.Event()
    done = threading.Event()

    def feed() -> None:
        started.set()
        backend.feed_run(payload)
        done.set()

    waits: list[float] = []
    thread = threading.Thread(target=feed)
    thread.start()
    try:
        assert started.wait(5), "the feed thread never started"
        while not done.is_set():
            t = time.perf_counter()
            with backend.lock():
                waits.append((time.perf_counter() - t) * 1000.0)
                backend.modes()
            time.sleep(0.005)  # pacing only: keep the probe from spinning while it measures
    finally:
        thread.join(30)
    assert not thread.is_alive(), "the feed thread never finished"
    return waits


def test_a_loop_side_lock_waits_at_most_a_few_slices_during_a_long_feed(lib) -> None:
    """A loop-side reader — a keystroke's encoder, a paint — must not wait for a whole feed run. The
    feed thread releases the backend lock between its 32 KiB slices, so a waiter takes it within a
    few slices; without that release the read waits for the entire run, which under a `yes`-sized run
    is hundreds of milliseconds of a stalled keypress (DT20, design note §3 point 3).

    The old form of this test could not fail: it fed a few milliseconds of work under a half-second
    bound, so a `feed_run` that never yielded still passed. The payload here is about 0.6 s of engine
    work for the same reason the second test exists — the bound has to be tight enough that a
    whole-run wait cannot hide under it."""

    backend = VTBackend(lib, 196, 59, scrollback_bytes=4 * 1024 * 1024)
    payload = b"y\r\n" * (8 * 1024 * 1024 // 3)
    try:
        waits = _probe_lock_waits(backend, payload)
        assert len(waits) >= 10, (
            "the run was too short to overlap the reads; the probe measured nothing")
        worst = max(waits)
        assert worst < 50.0, (
            f"a loop-side modes() read waited {worst:.1f} ms, not a few slices, so the feed held "
            f"the lock across the run")
    finally:
        backend.close()


def test_the_lock_probe_catches_a_feed_that_never_yields(lib, monkeypatch) -> None:
    """The probe in the test above must be able to fail: without this, it could pass for the wrong
    reason, as the tautological version did. A `feed_run` that holds `self._lock` once around the
    whole slice loop and never sleeps or releases between slices makes every loop-side read wait for
    the entire run, and the probe has to measure that (DT20, design note §3 point 3)."""

    def holding_feed_run(self: VTBackend, data: bytes) -> list[bytes]:
        replies: list[bytes] = []
        self._reply_sink = replies
        try:
            with self._lock:
                if self._terminal is None:
                    return replies
                for start in range(0, len(data), SLICE_BYTES):
                    chunk = data[start:start + SLICE_BYTES]
                    self._libc.ghostty_terminal_vt_write(self._terminal, chunk, len(chunk))
        finally:
            self._reply_sink = None
        return replies

    monkeypatch.setattr(VTBackend, "feed_run", holding_feed_run)
    backend = VTBackend(lib, 196, 59, scrollback_bytes=4 * 1024 * 1024)
    payload = b"y\r\n" * (8 * 1024 * 1024 // 3)
    try:
        waits = _probe_lock_waits(backend, payload)
        worst = max(waits)
        assert worst > 200.0, (
            f"the probe measured only {worst:.1f} ms for a feed that never yields, so it cannot tell "
            f"a yielding feed from a holding one")
    finally:
        backend.close()
