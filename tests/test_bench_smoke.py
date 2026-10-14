"""The benchmark scripts run with tiny inputs, so they cannot rot (DT8), and their verdicts mean something.

Why this matters (plan DT8, phase 1 and phase 10 "Tests"): `tui/bench/` holds the three measurements the orchestrator
runs on the integration node (the engine read, keystroke-to-screen under a firehose, a 500-session fleet). They are
shipped code to the import-lint, not rigs. A benchmark nobody runs between node runs drifts and fails on the day it
matters, so each runs here with a tiny grid and a few keystrokes against its real server (a child process running the
contract's fixture server). And a gate that cannot fail is no gate: each script's `judge` is shown to fail for a
keystroke that never came back, a ping nobody answered, a dropped attachment and a number over its limit.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

BENCH_DIR = Path(__file__).resolve().parents[1] / "bench"
SCRIPTS = ("engine_read", "paint", "fleet_scale")
# Modules the benches may import beyond the stdlib (DT8): the TUI, the contract's runner, the framework the TUI is
# built on (which the import-lint allows `tui/` and which `tsb_tui` itself imports) and the benches' own helper.
ALLOWED = {"tsb_tui", "tsbc", "textual", "_common"}
# What opens a connection: the benches reach the network only through the TUI's own client and the fixture server.
NETWORK = {"requests", "httpx", "aiohttp", "socket", "ssl", "http", "urllib", "websockets", "paramiko", "asyncssh",
           "tsb_contract_client"}


def _load(name: str):
    if str(BENCH_DIR) not in sys.path:
        sys.path.insert(0, str(BENCH_DIR))
    spec = importlib.util.spec_from_file_location(f"tsb_tui_bench_{name}", BENCH_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _imported_modules(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize("path", sorted(BENCH_DIR.glob("*.py")), ids=lambda p: p.name)
def test_every_bench_imports_only_the_stdlib_the_tui_and_the_contract_runner(path: Path) -> None:
    """DT8. `tui/bench/` is shipped code the import-lint checks; a network import would fail that lint and let a
    benchmark reach the network by itself. Read from each file's syntax tree, so a new script in the directory is
    held to it without being named here."""
    imported = _imported_modules(path)
    outside = {name for name in imported if name not in sys.stdlib_module_names and name not in ALLOWED}
    network = imported & NETWORK
    assert not outside, f"{path.name} imports {sorted(outside)}, which is not stdlib, tsb_tui or tsbc"
    assert not network, f"{path.name} imports a network module: {sorted(network)}"


def test_every_bench_script_has_a_tiny_run_here() -> None:
    """A script added to `tui/bench/` without a case below would be the one that rots: the directory's scripts must
    be exactly the ones this file runs."""
    on_disk = {p.stem for p in BENCH_DIR.glob("*.py") if not p.stem.startswith("_")}
    assert on_disk == set(SCRIPTS), f"bench scripts and tiny runs differ: {on_disk ^ set(SCRIPTS)}"


def test_engine_read_runs_tiny_and_prints_its_table(term_so: Path, capsys: pytest.CaptureFixture) -> None:
    """`bench/engine_read.py` runs with a tiny grid and a few frames and prints its table (DT8).

    Why (DT8): the go/no-go must be runnable without the node's full grid; this tiny run is what CI executes so the
    script stays runnable between node runs. It uses the real `.so` at the given path, exactly as the node run does.
    """
    module = _load("engine_read")
    rc = module.main(["--tiny", "--library", str(term_so)])
    assert rc == 0, "the tiny run must exit cleanly"
    out = capsys.readouterr().out
    assert "grid" in out and "p95 ms" in out, f"the table header is missing: {out!r}"
    assert "20x6" in out, "the tiny grid row is missing"


def test_engine_read_prints_the_feed_rate_for_both_streams_with_one_thread_and_three(
        term_so: Path, capsys: pytest.CaptureFixture) -> None:
    """The feed-rate rows (design note, step 0): MB/s through `ghostty_terminal_vt_write` for the `yes` and `tech`
    streams, on one thread and on three threads each with a terminal of its own, and how far three scale. The tiny run
    prints all four rows with a rate above zero and a one-thread row that is exactly 1.00 times itself; the numbers
    themselves are the node's."""
    module = _load("engine_read")
    assert module.main(["--tiny", "--library", str(term_so)]) == 0
    out = capsys.readouterr().out
    rows = [line.split() for line in out.splitlines() if line.split()[:1] in (["yes"], ["tech"])]
    assert [(r[0], r[2]) for r in rows] == [("yes", "1"), ("yes", "3"), ("tech", "1"), ("tech", "3")], out
    assert all(float(r[3]) > 0 and float(r[5]) > 0 for r in rows), rows
    assert [r[6] for r in rows if r[2] == "1"] == ["1.00", "1.00"]


def test_the_feed_rate_counts_every_byte_it_feeds(term_so: Path) -> None:
    """The rate is bytes over wall time, so the bytes must be the ones fed: three threads of 128 KiB each report three
    times one thread's bytes in the same chunk size, and the lines are the stream's own line feeds."""
    from tsb_tui.term import load

    module = _load("engine_read")
    lib = load(term_so)
    one = module.feed_rate(lib, 20, 6, "yes", 1, 128 * 1024, 4096)
    three = module.feed_rate(lib, 20, 6, "yes", 3, 128 * 1024, 4096)
    assert one["threads"] == 1 and three["threads"] == 3
    assert abs(three["mb_s"] / three["mb_s_per_thread"] - 3) < 1e-9
    assert abs(one["lines_s"] / (one["mb_s"] * 1e6) - 1 / 3) < 0.01, "`yes` is one line feed in three bytes"


def test_paint_runs_tiny_against_a_server_of_its_own_and_every_keystroke_comes_back(
        term_so: Path, capsys: pytest.CaptureFixture) -> None:
    """`bench/paint.py` starts the real TUI against the fixture server in a child process, with three sessions
    attached, the sidebar open and a firehose on each, and times four keystrokes under each stream (design §3.4 test
    4). The run says PASS only if every keystroke reached the screen, every ping was answered and no attachment was
    dropped, and the table names both streams and the number of keystrokes timed."""
    module = _load("paint")
    rc = module.main(["--tiny", "--library", str(term_so)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "PASS" in out and "FAIL" not in out, out
    rows = [line.split() for line in out.splitlines() if line.strip().startswith("40x12")]
    assert [(r[0], r[1], r[2]) for r in rows[:2]] == [("40x12", "yes", "4"), ("40x12", "tech", "4")], out
    assert len(rows) == 4, "the stage table must follow the summary, one row per scenario"
    for stage in ("key sent", "server queue", "echo read", "echo fed", "engine write", "feed->paint",
                  "paint->draw", "echo->screen", "key->screen"):
        assert stage in out, f"stage {stage!r} is missing from the table"
    # The design note's §4 columns: the read-ahead's largest depth and the paint's longest wait for
    # the backend lock, both over the whole measured window rather than for one keystroke.
    assert "ahead KB" in out and "lock ms" in out, out


def test_fleet_scale_runs_tiny_and_filters_in_both_views(term_so: Path, capsys: pytest.CaptureFixture) -> None:
    """`bench/fleet_scale.py` lists a few sessions from the fixture server, filters them in the sidebar and in the
    full view a character at a time, and types into the attached session while polls arrive (design §3.4 test 6).
    The report has all six measurements, and each filter measurement has a sample per keystroke (three words typed
    and deleted again: 26 keystrokes)."""
    module = _load("fleet_scale")
    rc = module.main(["--tiny", "--library", str(term_so)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "13 sessions listed" in out, out
    for name in ("sidebar filter, list built", "sidebar filter, drawn", "full view filter, list built",
                 "full view filter, drawn"):
        line = next((ln for ln in out.splitlines() if ln.startswith(name)), None)
        assert line is not None, f"{name!r} is missing from the report:\n{out}"
        assert line[len(name):].split()[0] == "26", line
    assert "typing during polls, to screen" in out and "a poll applied" in out


def _paint_row(**changes) -> dict:
    row = {"grid": "116x39", "stream": "yes", "keys": 200, "lost": 0, "dropped": False,
           "latency": {"n": 200.0, "p50": 20.0, "p95": 40.0, "max": 60.0}, "server": {"pings_unanswered": 0}}
    row.update(changes)
    return row


def test_paint_judge_fails_what_the_design_says_must_fail() -> None:
    """Design §3.4 test 4 and session-detach 9. A keystroke that never reached the screen, a ping nobody answered, an
    attachment that was dropped and reattached, and a p95 at or over 50 ms each fail a row; the tiny run judges the
    first three and not the time, because a loaded CI machine says nothing about the node."""
    module = _load("paint")
    assert module.judge(_paint_row(), gate=True) == []
    assert any("never reached the screen" in why for why in module.judge(_paint_row(lost=1), gate=False))
    assert any("unanswered" in why for why in module.judge(_paint_row(server={"pings_unanswered": 2}), gate=False))
    assert any("dropped" in why for why in module.judge(_paint_row(dropped=True), gate=False))
    slow = _paint_row(latency={"n": 200.0, "p50": 30.0, "p95": 50.0, "max": 90.0})
    assert any("p95" in why for why in module.judge(slow, gate=True))
    assert module.judge(slow, gate=False) == [], "the tiny run must not judge time"


def _fleet_result(**changes) -> dict:
    stats = {"n": 60.0, "p50": 5.0, "p95": 20.0, "max": 30.0}
    row = {"sessions": 500, "listed": 501, "lost": 0, "polls": 3, "server": {"pings_unanswered": 0},
           "sidebar": {"built": stats, "drawn": stats}, "view": {"built": stats, "drawn": stats}, "typing": stats}
    row.update(changes)
    return row


def test_fleet_scale_judge_requires_the_attached_session_listed_too() -> None:
    """The completeness check counts the attached session, so `sessions` listed is one short and fails.

    Why (Qodo #229, ruling 4; design §3.4 test 6): the run opens the attached session plus
    `args.sessions` more, so a complete list holds `sessions + 1`. A check against `sessions` alone
    passed while the attached session was missing, which is exactly the session the operator is
    looking at; the benchmark would call a fleet complete that had dropped it.
    """
    module = _load("fleet_scale")
    ok = _fleet_result(sessions=500, listed=501)
    assert module.judge(ok, gate=False) == [], "500 asked for plus the attached one is 501 listed"
    short = _fleet_result(sessions=500, listed=500)
    failures = module.judge(short, gate=False)
    assert any("501" in why for why in failures), (
        f"the attached session must be counted (expected a 501-session complaint): {failures}"
    )


def test_fleet_scale_judge_fails_what_the_design_says_must_fail() -> None:
    """Design §3.4 test 6. A fleet that lists fewer sessions than the server holds, a filter whose draw is at or over
    100 ms in either view, typing at or over 50 ms while polls run, and a run in which no poll happened (so the poll
    was never shown not to block typing) each fail."""
    module = _load("fleet_scale")
    assert module.judge(_fleet_result(), gate=True) == []
    assert any("lists 400" in why for why in module.judge(_fleet_result(listed=400), gate=False))
    slow = {"n": 60.0, "p50": 50.0, "p95": 100.0, "max": 150.0}
    for view in ("sidebar", "view"):
        bad = _fleet_result(**{view: {"built": slow, "drawn": slow}})
        assert any(view in why and "filter" in why for why in module.judge(bad, gate=True)), view
    assert any("typing" in why for why in module.judge(_fleet_result(typing=slow), gate=True))
    assert any("no poll ran" in why for why in module.judge(_fleet_result(polls=0), gate=True))


class _SlowClient:
    """The write end of a client that does not read: `write` waits until it is told to go on."""

    def __init__(self) -> None:
        import threading

        self.go = threading.Event()
        self.written = bytearray()

    def write(self, data: bytes) -> int:
        self.go.wait(20)
        self.written += data
        return len(data)

    def flush(self) -> None:
        pass


def _outbox_for(client: _SlowClient):
    _load("paint")  # puts the bench directory on sys.path
    import _common as C
    from tsbc import _serve

    app = C.BenchApp()
    ws = _serve.ServerWS(None, client)
    return C, app, ws, C.Outbox(app, ws)


def test_a_client_that_does_not_read_never_blocks_the_server_or_another_client() -> None:
    """Design note § verification: one queue and one writer per client, as the core does. A client whose socket is full
    must not make `put` wait (so the echo and the pings of every other client never queue behind it), and the other
    client's writer must go on writing. The server used to hold one lock across a blocked write, which delayed every
    client's echo and pings and inflated the measured latency."""
    slow, quick = _SlowClient(), _SlowClient()
    quick.go.set()
    _c, _app, _ws, stuck = _outbox_for(slow)
    _C, _app2, _ws2, free = _outbox_for(quick)
    stuck.thread.start()
    free.thread.start()
    import time

    started = time.perf_counter()
    for _ in range(50):
        stuck.put(2, b"y" * 1024)
        free.put(2, b"Q")
    assert time.perf_counter() - started < 1.0, "put waited on a client that does not read"
    deadline = time.monotonic() + 10
    while len(quick.written) < 50 * 3 and time.monotonic() < deadline:  # a 1-byte binary frame is 3 bytes
        time.sleep(0.01)
    assert len(quick.written) == 50 * 3, "the other client's writer was held up"
    assert len(slow.written) == 0, "the slow client's writer should be parked in its write"
    slow.go.set()
    stuck.stop()
    free.stop()


def test_a_client_whose_queue_passes_the_limit_is_closed_with_4011_and_gets_no_more_output() -> None:
    """The core's rule (session-detach rule 3): a client that cannot keep up is closed with 4011 once its queue passes
    8 MiB, and the server never grows without bound for it."""
    client = _SlowClient()
    C, app, _ws, box = _outbox_for(client)
    box.thread.start()
    chunk = b"y" * (1024 * 1024)
    for _ in range(10):
        box.put(2, chunk)
    assert box.max_queued > C.QUEUE_LIMIT_BYTES
    client.go.set()
    box.stop()
    box.thread.join(20)
    from tsbc import _wsframe as wf

    close = wf.encode_frame(wf.OP_CLOSE, wf.encode_close(C.CLOSE_TOO_SLOW, "too slow"), mask=False)
    assert bytes(client.written).endswith(close), "no 4011 close at the end of what the client was sent"
    assert app.dropped_for_queue == 1
    assert len(client.written) < 10 * len(chunk), "output went on after the close"


def test_pings_sent_within_a_second_of_the_window_ending_are_not_judged_unanswered() -> None:
    """Design note § verification, ping accounting. The server stops pinging when the measured window ends, before
    the TUI shuts down, and judges only the pings sent more than a second before that: a ping still in flight, or one
    the closing TUI will never read, is not a miss, and a ping that was left unanswered long before is."""
    _load("paint")
    import _common as C

    app = C.BenchApp()
    app.quiesced_at = 100.0
    app.pinged = {b"old-unanswered": 90.0, b"old-answered": 91.0, b"late": 99.5}
    app.ponged = {b"old-answered"}
    assert app.stats()["pings_unanswered"] == 1


def test_the_stages_of_a_keystroke_are_the_differences_of_its_stamps() -> None:
    """The split of the journey: each stage is the later stamp minus the earlier, only over the keys that have both, and
    the server's two stamps are joined to the n-th typing of each letter. Wrong arithmetic here would put the time in
    the wrong stage and send the fix to the wrong place."""
    _load("paint")
    import _common as C

    probe = C.Probe()
    first, second = C.KeyLatency("Q", 10.000), C.KeyLatency("Q", 11.000)
    for key, off in ((first, 0.0), (second, 1.0)):
        key.sunk, key.fed, key.fed_end = 10.030 + off, 10.040 + off, 10.045 + off
        key.repainted, key.drawn = 10.060 + off, 10.062 + off
    probe.sent, probe.done = [first, second], [first, second]
    probe.join_server([("Q", 10.005), ("Q", 11.007)], [("Q", 10.010), ("Q", 11.012)])
    stages = probe.stage_stats()
    want = {"key sent": 6.0, "server queue": 5.0, "echo read": 19.0, "echo fed": 10.0, "engine write": 5.0,
            "feed->paint": 15.0, "paint->draw": 2.0, "echo->screen": 51.0, "key->screen": 62.0}  # medians, ms
    assert {name: stats["p50"] for name, stats in stages.items()} == pytest.approx(want, abs=1e-6)
    assert first.recv == 10.005 and second.written == 11.012


def test_the_bench_server_sets_tcp_nodelay_on_every_client_socket() -> None:
    """Without TCP_NODELAY a one-byte echo queued behind a larger write waits for the client's delayed ACK, about 40 ms:
    the server's doing, not the TUI's, and enough to hide or fake a 50 ms gate. The core's sockets are no-delay (Go's
    default), so the bench's must be. The tiny run cannot see it (its numbers are not judged), so the option is checked
    where it is set."""
    _load("paint")
    import _common as C
    from tsbc import _wsframe as wf

    class Sock:
        def __init__(self) -> None:
            self.options: list[tuple[int, int, int]] = []

        def setsockopt(self, level: int, name: int, value: int) -> None:
            self.options.append((level, name, value))

    class Ws:
        closed = True  # the handler sees a dropped client at once and returns

        def __init__(self, sock: Sock) -> None:
            self.sock = sock

        def _socket(self) -> Sock:
            return self.sock

        def recv(self):
            return wf.Message("close", b"", wf.CLOSE_ABNORMAL)

    sock = Sock()
    C.BenchApp().websocket("/ws/attach", Ws(sock))
    assert (6, 1, 1) in sock.options, sock.options  # IPPROTO_TCP, TCP_NODELAY, on
