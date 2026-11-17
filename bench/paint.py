"""Keystroke-to-screen under a firehose (design §3.4 test 4; plan DT8; run by the orchestrator on the integration node).

Why this exists: a scrolling session redraws the whole grid, the pump that reads it also answers the server's pings,
and a person typing into the same session must see their keys within 50 ms (p95). This starts the real TUI headless
against the contract's fixture server in a process of its own, with three sessions attached (the one in front and two
behind, all receiving output), the sidebar open and polling, and measures the keystroke's journey to the screen at
116x39 and 196x59 under a `yes`-like firehose and a long `show tech-support`-like stream. It also reports the longest
stretch the event loop was kept from running anything else, and whether every WebSocket ping was answered
(session-detach 9: a client that does not answer is detached).

It is a script, not a rig: no target, no sshd, nothing outside the fixture server it starts itself. Run it as::

    python tui/bench/paint.py

It prints one row per (grid, stream) and exits 0 when every row's keystroke-to-screen p95 is under 50 ms, no keystroke
was lost, every ping was answered and the attachment never dropped; 1 otherwise. The tiny smoke in
`tests/test_bench_smoke.py` runs it with `--tiny` so it cannot rot.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common as C
from _common import GATE_P95_MS


async def run_one(cols: int, rows: int, stream: str, args: argparse.Namespace) -> dict:
    """One scenario: a fresh server and a fresh app, a warm-up, then `args.keys` keystrokes."""
    cfg = C.make_config(args.library, **{"fleet_view.refresh_s": str(args.poll_s)})
    with C.ServerProcess(stream=stream, mb_per_s=args.mb_per_s, ping_s=args.ping_s) as server:
        app = C.TsbTuiApp(server.url, cfg=cfg)
        async with app.run_test(size=(cols + 8, rows + 2)) as pilot:
            screen = await C.log_in(app, pilot)
            probe = C.Probe()
            probe.hook_sink(screen)  # before any session opens: the pump takes the screen's sink when it attaches
            ids = await C.open_sessions(app, screen, 3, cols, rows)
            screen.open_sidebar(focus=False)  # it polls `GET /sessions` while it is on screen
            await C.fit_canvas(pilot, screen, cols, rows)
            front = screen.slot_for(ids[0])
            assert front is not None and front.pump is not None
            clients_before = [screen.slot_for(sid).pump.state.client_id for sid in ids]
            probe.install(front.canvas)
            monitor = C.LoopMonitor()
            monitor.start()
            await asyncio.sleep(args.warmup_s)  # the stream reaches full speed before anything is timed

            # The read-ahead's largest depth is a high-water mark, so it is watched while the keys are
            # timed rather than read once: the reader fills and empties its queue many times a second
            # under a firehose, and one late read could see an empty one.
            max_depth = 0
            async def sample_window() -> None:
                nonlocal max_depth
                while True:
                    depth = getattr(front.pump, "_queued_output", 0)
                    max_depth = max(max_depth, depth)
                    await asyncio.sleep(0.01)

            sampler = asyncio.create_task(sample_window())
            lost = await probe.type_keys(screen, args.keys, args.spacing_ms / 1000.0)
            sampler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sampler
            loop = await monitor.stop()
            clients_after = [screen.slot_for(sid).pump.state.client_id for sid in ids]
            any_closed = any(screen.slot_for(sid).pump.state.closed for sid in ids)
            dropped = (clients_after != clients_before) or any_closed
            reattached = (clients_after != clients_before) and not any_closed
            completed = True
            paints = front.canvas.paint_count
            lock_wait = front.canvas.lock_wait_ms
            server.quiesce()  # the window is over: no more pings, before the TUI starts to shut down
    probe.join_server(server.stats["key_recv"], server.stats["echo_written"])
    totals = probe.totals_ms()
    result = {
        "grid": f"{cols}x{rows}",
        "stream": stream,
        "keys": args.keys,
        "lost": lost,
        "latency": C.summary(totals),
        "stages": probe.stage_stats(),
        "loop_stall_ms": loop["max"],
        "paints": paints,
        "readahead_max": float(max_depth),
        "lock_wait_ms": float(lock_wait),
        "server": server.stats,
        "dropped": dropped,
        "reattached": reattached,
        "completed": completed,
    }
    return result


def judge(result: dict, *, gate: bool) -> list[str]:
    """What this row fails on, in words; empty when it passes. Without `gate` (the tiny run) nothing is judged on
    time, only on the plumbing: a keystroke that never came back, a ping that was never answered, a drop."""
    bad = []
    if gate:
        if result["lost"]:
            bad.append(f"{result['lost']} keystroke(s) never reached the screen")
        if result["dropped"]:
            if result.get("reattached"):
                bad.append("an attachment was dropped and reattached")
            else:
                bad.append("an attachment was dropped and did not reattach")
        if result["server"].get("pings_unanswered"):
            bad.append(f"{result['server']['pings_unanswered']} ping(s) went unanswered")
        if result["latency"]["p95"] >= GATE_P95_MS:
            bad.append(f"keystroke-to-screen p95 {result['latency']['p95']:.1f} ms is not under {GATE_P95_MS:.0f} ms")
        return bad

    if result["dropped"]:
        if result.get("reattached") and result.get("completed"):
            if result["lost"] >= result["keys"]:
                bad.append("no keystroke came back after the drop")
            else:
                print(
                    f"WARN {result['grid']} {result['stream']}: an attachment was dropped and "
                    f"reattached ({result['lost']} keystrokes lost)"
                )
        else:
            bad.append("an attachment was dropped and did not reattach")
    elif result["lost"]:
        bad.append(f"{result['lost']} keystroke(s) never reached the screen")

    if result["server"].get("pings_unanswered"):
        bad.append(f"{result['server']['pings_unanswered']} ping(s) went unanswered")
    return bad


def print_table(results: list[dict]) -> None:
    """One row per scenario, then, for each, the stages of the keystroke's journey (see `STAGES`): p50 and p95 of each,
    so a miss says where the time went. `echo->screen` is what the TUI does with a frame once it has arrived, whole."""
    header = (f"{'grid':>8} {'stream':>6} {'keys':>5} {'p50 ms':>8} {'p95 ms':>8} {'max ms':>8} "
              f"{'stall ms':>9} {'lock ms':>8} {'ahead KB':>9} {'pongs':>9} {'pong p95':>9} {'queue MB':>9}")
    print(header)
    print("-" * len(header))
    for r in results:
        lat, srv = r["latency"], r["server"]
        pongs = f"{srv.get('pongs', 0)}/{srv.get('pings_sent', 0)}"
        print(f"{r['grid']:>8} {r['stream']:>6} {int(lat['n']):>5} {lat['p50']:>8.1f} {lat['p95']:>8.1f} {lat['max']:>8.1f} "
              f"{r['loop_stall_ms']:>9.1f} {r['lock_wait_ms']:>8.1f} {r['readahead_max'] / 1024:>9.1f} "
              f"{pongs:>9} {srv.get('pong_ms', {}).get('p95', 0.0):>9.1f} "
              f"{srv.get('max_queued_bytes', 0) / 1e6:>9.2f}")
    print("\nthe journey of a keystroke, p50 / p95 ms (key sent: the posting and its trip to the server;")
    print("echo read: the echo's wait in the socket and the reader; echo fed: its wait behind the read-ahead")
    print("and any run ahead of it, into the engine; engine write, feed->paint and paint->draw: the canvas")
    print("and Textual; echo->screen is what the TUI does with a frame once it has arrived, whole)")
    names = [name for name, _, _ in C.STAGES] + [name for name, _, _ in C.SPANS]
    print(f"{'grid':>8} {'stream':>6} " + " ".join(f"{n:>14}" for n in names))
    for r in results:
        cells = " ".join(f"{r['stages'][n]['p50']:>6.1f}/{r['stages'][n]['p95']:<7.1f}" for n in names)
        print(f"{r['grid']:>8} {r['stream']:>6} {cells}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="keystroke-to-screen under a firehose (design §3.4 test 4)")
    C.library_arg(parser)
    parser.add_argument("--sizes", default="116x39,196x59", help="comma-separated COLSxROWS grids")
    parser.add_argument("--streams", default="yes,tech", help="comma-separated: yes, tech")
    parser.add_argument("--keys", type=int, default=200, help="keystrokes timed per row")
    parser.add_argument("--spacing-ms", type=float, default=50.0, help="pause after each keystroke is drawn")
    parser.add_argument("--warmup-s", type=float, default=2.0, help="stream at full speed before timing starts")
    parser.add_argument("--mb-per-s", type=float, default=4.0, help="output per session, megabytes a second")
    parser.add_argument("--ping-s", type=float, default=0.25, help="seconds between the server's pings")
    parser.add_argument("--poll-s", type=int, default=1, help="the sidebar's poll interval (fleet_view.refresh_s)")
    parser.add_argument("--tiny", action="store_true", help="tiny inputs for the CI smoke: no gate, a few keys")
    args = parser.parse_args(argv)

    if args.tiny:
        sizes, streams = [(40, 12)], ["yes", "tech"]
        args.keys, args.warmup_s, args.spacing_ms, args.mb_per_s, args.ping_s = 4, 0.3, 5.0, 1.0, 0.1
    else:
        sizes = [tuple(int(n) for n in spec.split("x")) for spec in args.sizes.split(",")]
        streams = args.streams.split(",")
    unknown = [s for s in streams if s not in ("yes", "tech")]
    if unknown:
        parser.error(f"--streams: unknown {unknown}")

    started = time.monotonic()
    results = [asyncio.run(run_one(cols, rows, stream, args)) for cols, rows in sizes for stream in streams]
    print_table(results)

    failures = [(r, why) for r in results for why in judge(r, gate=not args.tiny)]
    for r, why in failures:
        print(f"FAIL {r['grid']} {r['stream']}: {why}")
    print(f"\n{'FAIL' if failures else 'PASS'}: keystroke-to-screen p95 gate {GATE_P95_MS:.0f} ms"
          f"{' (not judged: --tiny)' if args.tiny else ''}, {time.monotonic() - started:.0f} s")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
