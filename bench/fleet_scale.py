"""A fleet of 500 sessions (design §3.4 test 6; plan DT8; run by the orchestrator on the integration node).

Why this exists: the sidebar and the full view are lists of whatever the core returns, and at the scale a fleet has
they must filter fast and must never hold up a person typing into the session in front. This starts the real TUI
headless against the contract's fixture server in a process of its own with N sessions listed (500 by default), one
session attached with the sidebar open and polling, and measures:

* the filter, in the sidebar and in the full view: from the keystroke to the list built and to its first line drawn
  (the gate is the draw, the stricter of the two: p95 under 100 ms);
* typing while the poll runs: keystroke-to-screen in the attached session with a `GET /sessions` every second that
  the server holds back for `--poll-delay-ms` (p95 under 50 ms, as test 4), the time each poll took to apply, and the
  longest stall of the event loop.

It is a script, not a rig: no target, no sshd, nothing outside the fixture server it starts itself. Run it as::

    python tui/bench/fleet_scale.py

It prints the three measurements and exits 0 when all gates hold and no keystroke was lost, 1 otherwise. The tiny
smoke in `tests/test_bench_smoke.py` runs it with `--tiny` so it cannot rot.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common as C
from _common import FILTER_GATE_MS, GATE_P95_MS


class FilterProbe:
    """The filter's latency on one widget (the sidebar or the full view): the keystroke is posted to the widget, and
    the time is taken when its filter text has changed and its list is built, and again when Textual first draws a
    line of the widget showing that text."""

    def __init__(self, widget) -> None:
        self.widget = widget
        self._expected: str | None = None
        self._built: float | None = None
        self._drawn: float | None = None
        draw, relayout = widget.render_line, widget._relayout

        def built(*args, **kwargs):
            outcome = relayout(*args, **kwargs)
            if self._built is None and self._expected is not None and widget.filter_text == self._expected:
                self._built = time.perf_counter()
            return outcome

        widget._relayout = built

        def probed(y: int):
            line = draw(y)
            if self._drawn is None and self._expected is not None and widget.filter_text == self._expected:
                self._drawn = time.perf_counter()
            return line

        widget.render_line = probed

    async def send(self, key: str, character: str | None, expected: str) -> None:
        """A key that is not measured (opening and closing the filter), waited for until it has taken effect."""
        from textual import events

        self.widget.post_message(events.Key(key, character))
        await C.wait_for(lambda: self.widget.filter_text == expected, f"the filter to read {expected!r}", 5.0)
        await asyncio.sleep(0.05)

    async def press(self, key: str, character: str | None, expected: str, timeout_s: float = 5.0) -> tuple[float, float]:
        """A key that changes the filter text to `expected`; returns (ms until the list is built, ms until drawn)."""
        from textual import events

        self._expected, self._built, self._drawn = expected, None, None
        started = time.perf_counter()
        self.widget.post_message(events.Key(key, character))
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._built is not None and self._drawn is not None:
                return (self._built - started) * 1000.0, (self._drawn - started) * 1000.0
            await asyncio.sleep(0.0005)
        raise RuntimeError(f"the filter never reached {expected!r} on screen")

    async def filter_through(self, words: tuple[str, ...]) -> tuple[list[float], list[float]]:
        """Open the filter, type each word a character at a time and delete it again; one sample per keystroke."""
        built, drawn = [], []
        await self.send("slash", "/", "")
        for word in words:
            text = ""
            for ch in word:
                text += ch
                b, d = await self.press(ch, ch, text)
                built.append(b)
                drawn.append(d)
                await asyncio.sleep(0.02)
            while text:
                text = text[:-1]
                b, d = await self.press("backspace", None, text)
                built.append(b)
                drawn.append(d)
                await asyncio.sleep(0.02)
        await self.send("escape", None, "")
        return built, drawn


async def run(args: argparse.Namespace) -> dict:
    cols, rows = args.grid
    cfg = C.make_config(args.library, **{"fleet_view.refresh_s": str(args.poll_s)})
    with C.ServerProcess(stream="none", ping_s=0.25, extra_sessions=args.sessions,
                         poll_delay_s=args.poll_delay_ms / 1000.0) as server:
        app = C.TsbTuiApp(server.url, cfg=cfg)
        async with app.run_test(size=args.window) as pilot:
            screen = await C.log_in(app, pilot)
            probe = C.Probe()
            probe.hook_sink(screen)
            (sid,) = await C.open_sessions(app, screen, 1, cols, rows)
            applies: list[float] = []
            apply_poll = screen._apply_poll

            def timed(generation, records):
                started = time.perf_counter()
                apply_poll(generation, records)
                applies.append((time.perf_counter() - started) * 1000.0)

            screen._apply_poll = timed
            screen.open_sidebar(focus=False)
            # `args.sessions` extra sessions plus the one attached: wait for all of them, not just the
            # extras, or the benchmark can start with the attached session still missing from the list.
            await C.wait_for(lambda: len(screen.listed_sessions()) >= args.sessions + 1,
                             f"the sidebar to list {args.sessions + 1} sessions")

            # The poll never blocks typing: keys into the attached session while the poll runs every second.
            front = screen.slot_for(sid)
            probe.install(front.canvas)
            monitor = C.LoopMonitor()
            monitor.start()
            polls_before = len(applies)
            lost = await probe.type_keys(screen, args.keys, args.spacing_ms / 1000.0)
            loop = await monitor.stop()
            polls = len(applies) - polls_before

            # The filter in the sidebar, then in the full view.
            words = ("core", "edge-0", "zzz")
            sidebar_probe = FilterProbe(screen.sidebar)
            screen.sidebar.focus()
            await pilot.pause(0.05)
            sidebar_built, sidebar_drawn = await sidebar_probe.filter_through(words)
            shown = len(screen.listed_sessions())

            screen.open_fleet_view()
            await pilot.pause(0.1)
            await C.wait_for(lambda: screen.view_open, "the full view to open")
            view_probe = FilterProbe(screen.fleet_view)
            view_built, view_drawn = await view_probe.filter_through(words)
            screen.close_fleet_view()
            server.quiesce()  # the window is over: no more pings, before the TUI starts to shut down
    return {
        "sessions": args.sessions,
        "listed": shown,
        "sidebar": {"built": C.summary(sidebar_built), "drawn": C.summary(sidebar_drawn)},
        "view": {"built": C.summary(view_built), "drawn": C.summary(view_drawn)},
        "typing": C.summary(probe.totals_ms()),
        "lost": lost,
        "polls": polls,
        "apply_ms": C.summary(applies),
        "loop_stall_ms": loop["max"],
        "server": server.stats,
    }


def judge(result: dict, *, gate: bool) -> list[str]:
    bad = []
    # The run opens the attached session plus `args.sessions` more, so completeness is `sessions + 1`.
    expected = result["sessions"] + 1
    if result["listed"] < expected:
        bad.append(f"the fleet lists {result['listed']} of {expected} sessions (the attached one included)")
    if result["lost"]:
        bad.append(f"{result['lost']} keystroke(s) never reached the screen")
    if result["server"].get("pings_unanswered"):
        bad.append(f"{result['server']['pings_unanswered']} ping(s) went unanswered")
    if gate:
        for name in ("sidebar", "view"):
            p95 = result[name]["drawn"]["p95"]
            if p95 >= FILTER_GATE_MS:
                bad.append(f"the {name}'s filter p95 {p95:.1f} ms is not under {FILTER_GATE_MS:.0f} ms")
        if result["typing"]["p95"] >= GATE_P95_MS:
            bad.append(f"typing during polls p95 {result['typing']['p95']:.1f} ms is not under {GATE_P95_MS:.0f} ms")
        if result["polls"] < 1:
            bad.append("no poll ran while typing was timed, so the poll was not shown not to block it")
    return bad


def print_report(r: dict) -> None:
    print(f"{r['listed']} sessions listed ({r['sessions']} asked for, and the one attached)\n")
    header = f"{'measurement':<34} {'n':>5} {'p50 ms':>8} {'p95 ms':>8} {'max ms':>8}"
    print(header)
    print("-" * len(header))
    rows = [
        ("sidebar filter, list built", r["sidebar"]["built"]),
        ("sidebar filter, drawn", r["sidebar"]["drawn"]),
        ("full view filter, list built", r["view"]["built"]),
        ("full view filter, drawn", r["view"]["drawn"]),
        ("typing during polls, to screen", r["typing"]),
        ("a poll applied", r["apply_ms"]),
    ]
    for name, s in rows:
        print(f"{name:<34} {int(s['n']):>5} {s['p50']:>8.1f} {s['p95']:>8.1f} {s['max']:>8.1f}")
    print(f"\nlongest event-loop stall while typing: {r['loop_stall_ms']:.1f} ms; {r['polls']} poll(s) applied meanwhile")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="500 sessions: filter and poll (design §3.4 test 6)")
    C.library_arg(parser)
    parser.add_argument("--sessions", type=int, default=500, help="sessions the core lists")
    parser.add_argument("--window", default="120x40", help="the window, COLSxROWS")
    parser.add_argument("--grid", default="80x24", help="the attached session's size, COLSxROWS")
    parser.add_argument("--keys", type=int, default=100, help="keystrokes timed while the poll runs")
    parser.add_argument("--spacing-ms", type=float, default=50.0)
    parser.add_argument("--poll-s", type=int, default=1, help="the poll interval (fleet_view.refresh_s)")
    parser.add_argument("--poll-delay-ms", type=float, default=250.0, help="how long the server holds each poll back")
    parser.add_argument("--tiny", action="store_true", help="tiny inputs for the CI smoke: no gate, a few sessions")
    args = parser.parse_args(argv)
    if args.tiny:
        args.sessions, args.keys, args.spacing_ms, args.poll_delay_ms = 12, 4, 5.0, 20.0
        args.window = "100x30"
    args.window = tuple(int(n) for n in args.window.split("x"))
    args.grid = tuple(int(n) for n in args.grid.split("x"))

    started = time.monotonic()
    result = asyncio.run(run(args))
    print_report(result)
    failures = judge(result, gate=not args.tiny)
    for why in failures:
        print(f"FAIL: {why}")
    print(f"\n{'FAIL' if failures else 'PASS'}: filter gate {FILTER_GATE_MS:.0f} ms, typing gate {GATE_P95_MS:.0f} ms"
          f"{' (not judged: --tiny)' if args.tiny else ''}, {time.monotonic() - started:.0f} s")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
