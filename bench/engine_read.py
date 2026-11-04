#!/usr/bin/env python3
"""The engine-read go/no-go (phase 1's measurement, run by the orchestrator on the integration node).

Why this exists (phase 1 "Go or no-go", plan R4): a scrolling frame is `DIRTY_FULL`, so a firehose
re-reads every cell of a 196×59 grid on the loop that also answers pings. Before the canvas is built
on this read path, the orchestrator measures it. **Go:** a full-grid read at 196×59 has p95 at or
under 15 ms, leaving a ≤30 fps paint inside the design's 50 ms keystroke-to-screen p95 (§3.4 test 4).
**No-go:** the orchestrator picks among the plan's R4 fallbacks.

It is a script, not a rig: no target, no sshd, no network — a generated scrolling byte stream fed to
an in-process `VTBackend`. It imports only the stdlib and `tsb_tui` (DT8). Run it as::

    python tui/bench/engine_read.py

It prints a table (one row per grid size) and exits 0 when the 196×59 p95 is at or under 15 ms,
1 otherwise. It then prints the **feed rate**: megabytes a second through `ghostty_terminal_vt_write` for the
benchmark's own `yes` and `tech` streams (8 KiB chunks) at each grid size, with one thread and with three threads each
on a terminal of its own, and how far three threads scale. The rate is what decides whether any TUI design can keep up
with a firehose, and the scaling shows whether the engine serialises separate terminals; neither is gated here. The tiny smoke in `tests/test_bench_smoke.py` runs it with a tiny grid so it cannot rot.
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import threading
import time

# Make `tsb_tui` importable when run from the repo root without an install.
_HERE = os.path.dirname(os.path.abspath(__file__))
_TUI = os.path.dirname(_HERE)
if _TUI not in sys.path:
    sys.path.insert(0, _TUI)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from _common import CHUNK_BYTES, Stream

from tsb_tui.term import VTBackend, load

# The default `.so` path (the caller's knob in phase 3). The orchestrator passes `--library` when it
# stages the engine elsewhere; the repo-relative path is the fallback for a run in the worktree.
DEFAULT_LIBRARY = os.path.join(
    _TUI, "vendor", "libghostty-vt", "libghostty-vt.so"
)
DEFAULT_SCROLLBACK = 4 * 1024 * 1024  # DT18
GATE_MS = 15.0
GATE_COLS, GATE_ROWS = 196, 59


def _stream(frames: int, *, seed: int = 0) -> list[bytes]:
    """A generated scrolling stream: numbered lines with SGR colour, so each frame dirties rows.

    No server, no network (DT8): the bytes a busy session would send, produced here. Each frame is
    one line, wrapped at the grid width by the engine, with a colour run so styles are exercised.
    """
    colour = (seed % 7) + 31
    out: list[bytes] = []
    for i in range(frames):
        line = f"\x1b[{colour}m{i:06d}\x1b[0m the quick brown fox jumps over the lazy dog {i}"
        out.append(line.encode("ascii") + b"\r\n")
    return out


_GLYPHS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _-/.:"


def _varied_stream(cols: int, rows: int, frames: int) -> list[bytes]:
    """The worst case for a decoder that caches by cell value: every frame redraws the whole grid
    with random glyphs and a style change on about a third of the cells (hundreds of distinct
    glyph/style pairs per read), so almost nothing repeats. Seeded, so runs compare."""
    rnd = random.Random(0)
    sgr = ("0", "1", "4", "7", "31", "32", "33", "34", "1;35", "4;36", "38;5;208", "48;2;10;20;30")
    out: list[bytes] = []
    for _ in range(frames):
        parts = ["\x1b[H"]
        for _y in range(rows):
            for _x in range(cols):
                if rnd.random() < 0.3:
                    parts.append(f"\x1b[{rnd.choice(sgr)}m")
                parts.append(rnd.choice(_GLYPHS))
            parts.append("\x1b[0m\r\n")
        out.append("".join(parts).encode("ascii"))
    return out


def measure(
    cols: int, rows: int, frames: int, reps: int, library: str, scrollback: int, *, varied: bool = False
) -> dict:
    """Feed `frames` of output and measure a dirty read per repaint (a full grid while it scrolls)."""
    lib = load(library)
    backend = VTBackend(lib, cols, rows, scrollback_bytes=scrollback)
    stream = _varied_stream(cols, rows, frames) if varied else _stream(frames)
    samples: list[float] = []
    calls: list[int] = []
    dirty_counts: list[int] = []
    for frame in stream:
        backend.feed(frame)
        start = time.perf_counter()
        dirty = backend.dirty_rows()
        # Touch every cell, as a paint would, so the number includes decoding, not just the call.
        cells = sum(len(row.cells) for row in dirty)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        samples.append(elapsed_ms)
        calls.append(cells)
        dirty_counts.append(len(dirty))
        # The canvas clears dirty state after a frame; do the same so the next read is honest.
        backend.mark_clean()
    # Warm the read path with a few unmeasured repaints, then measure `reps` steady-state reads.
    steady: list[float] = []
    for _ in range(reps):
        backend.feed(stream[0])
        start = time.perf_counter()
        dirty = backend.dirty_rows()
        sum(len(row.cells) for row in dirty)
        steady.append((time.perf_counter() - start) * 1000.0)
        backend.mark_clean()
    backend.close()
    all_samples = samples + steady
    all_samples.sort()
    p50 = statistics.median(all_samples) if all_samples else 0.0
    p95 = all_samples[min(len(all_samples) - 1, round(0.95 * len(all_samples)) - 1)] if all_samples else 0.0
    return {
        "cols": cols,
        "rows": rows,
        "frames": frames,
        "p50_ms": p50,
        "p95_ms": p95,
        "max_cells": max(calls) if calls else 0,
        "max_rows": max(dirty_counts) if dirty_counts else 0,
        "reps": reps,
    }


def feed_rate(lib, cols: int, rows: int, kind: str, threads: int, total_bytes: int, scrollback: int) -> dict:
    """Feed `total_bytes` of the `kind` stream, in chunks, into each of `threads` terminals of `cols`x`rows` at once,
    one thread per terminal, and report the rate. ctypes releases the GIL for the length of the engine call, so the
    threads run in parallel unless the engine itself serialises separate terminals."""
    stream = Stream(kind)
    chunks = [stream.next_chunk() for _ in range(max(1, 64 * 1024 * 1024 // CHUNK_BYTES // 16))]  # a few MiB, reused
    per_chunk = len(chunks[0])
    lines_per_chunk = [chunk.count(b"\n") for chunk in chunks]
    count = max(1, total_bytes // per_chunk)
    backends = [VTBackend(lib, cols, rows, scrollback_bytes=scrollback) for _ in range(threads)]
    start = threading.Barrier(threads + 1)
    done = [0.0] * threads
    fed = [0] * threads
    lines = [0] * threads

    def work(index: int) -> None:
        backend = backends[index]
        feed = backend.feed
        n_chunks = len(chunks)
        start.wait()
        total = line_count = 0
        for i in range(count):
            chunk = chunks[i % n_chunks]
            feed(chunk)
            total += len(chunk)
            line_count += lines_per_chunk[i % n_chunks]
        done[index] = time.perf_counter()
        fed[index], lines[index] = total, line_count

    workers = [threading.Thread(target=work, args=(i,)) for i in range(threads)]
    for worker in workers:
        worker.start()
    start.wait()
    begun = time.perf_counter()
    for worker in workers:
        worker.join()
    wall = max(max(done) - begun, 1e-9)
    for backend in backends:
        backend.close()
    return {
        "kind": kind, "cols": cols, "rows": rows, "threads": threads, "wall_s": wall,
        "mb_s": sum(fed) / wall / 1e6, "mb_s_per_thread": sum(fed) / wall / 1e6 / threads,
        "lines_s": sum(lines) / wall,
    }


def print_feed(results: list[dict]) -> None:
    print("\nfeed rate through ghostty_terminal_vt_write (8 KiB chunks; threads each on a terminal of their own)")
    header = (f"{'stream':>6} {'grid':>10} {'threads':>8} {'MB/s':>8} {'MB/s each':>10} {'lines/s':>10} "
              f"{'x one thread':>13}")
    print(header)
    print("-" * len(header))
    one = {(r["kind"], r["cols"], r["rows"]): r["mb_s"] for r in results if r["threads"] == 1}
    for r in results:
        scale = r["mb_s"] / one[(r["kind"], r["cols"], r["rows"])]
        print(f"{r['kind']:>6} {str(r['cols']) + 'x' + str(r['rows']):>10} {r['threads']:>8} {r['mb_s']:>8.1f} "
              f"{r['mb_s_per_thread']:>10.1f} {r['lines_s']:>10.0f} {scale:>13.2f}")


def _print_table(rows: list[dict]) -> None:
    header = f"{'grid':>10} {'frames':>7} {'p50 ms':>8} {'p95 ms':>8} {'cells/read':>11} {'rows/read':>10}"
    print(header)
    print("-" * len(header))
    for r in rows:
        grid = f"{r['cols']}x{r['rows']}"
        print(
            f"{grid:>10} {r['frames']:>7} {r['p50_ms']:>8.3f} {r['p95_ms']:>8.3f} "
            f"{r['max_cells']:>11} {r['max_rows']:>10}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="engine-read go/no-go (phase 1)")
    parser.add_argument("--library", default=DEFAULT_LIBRARY, help="path to libghostty-vt.so")
    parser.add_argument("--scrollback", type=int, default=DEFAULT_SCROLLBACK)
    parser.add_argument("--frames", type=int, default=400, help="scrolling frames per grid")
    parser.add_argument("--reps", type=int, default=200, help="steady-state repaints per grid")
    parser.add_argument(
        "--sizes",
        default=f"{GATE_COLS}x{GATE_ROWS},116x39",
        help="comma-separated COLSxROWS list; the first is the gate",
    )
    parser.add_argument(
        "--varied",
        action="store_true",
        help="worst case: redraw the whole grid every frame with random glyphs and styles",
    )
    parser.add_argument("--feed-mb", type=int, default=64, help="megabytes fed per thread for each feed-rate row")
    parser.add_argument("--no-feed", action="store_true", help="skip the feed-rate rows")
    parser.add_argument(
        "--tiny",
        action="store_true",
        help="tiny inputs for the CI smoke: a few frames at a small grid",
    )
    args = parser.parse_args(argv)

    if args.tiny:
        sizes = [(20, 6)]
        frames, reps = 8, 3
    else:
        sizes = []
        for spec in args.sizes.split(","):
            cols, _, rows = spec.partition("x")
            sizes.append((int(cols), int(rows)))
        frames, reps = args.frames, args.reps

    results = [measure(c, r, frames, reps, args.library, args.scrollback, varied=args.varied) for c, r in sizes]
    _print_table(results)
    if not args.no_feed:
        lib = load(args.library)
        feed_bytes = 64 * 1024 if args.tiny else args.feed_mb * 1024 * 1024
        print_feed([feed_rate(lib, c, r, kind, threads, feed_bytes, args.scrollback)
                    for c, r in sizes for kind in ("yes", "tech") for threads in (1, 3)])

    gate = next((r for r in results if (r["cols"], r["rows"]) == (GATE_COLS, GATE_ROWS)), None)
    if args.tiny or gate is None:
        return 0
    verdict = "GO" if gate["p95_ms"] <= GATE_MS else "NO-GO"
    print(
        f"\n{verdict}: {GATE_COLS}x{GATE_ROWS} full-grid read p95 {gate['p95_ms']:.3f} ms "
        f"(gate {GATE_MS:.0f} ms)"
    )
    return 0 if verdict == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
