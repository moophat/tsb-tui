# Fix Report: Flaky Drive Wait and Bench Smoke Measurement (#95)

**AI-Model:** gemini-3.8-flash  
**Branch:** fix/flaky-drive-and-bench  
**Allowed paths:** `tests/test_drive_commands.py`, this report.  

---

## 1. Drive Test: `test_drive_reads_are_live_while_the_view_is_open`

### Cause and Flake Mechanism
`tests/test_drive_commands.py::test_drive_reads_are_live_while_the_view_is_open` previously used `_shown(rig, ...)`:
- `_shown(rig, "keys → scroll")` waited for the status row text by polling `snapshot` with `await asyncio.sleep(0.02)`.
- `_shown(rig, "L299", present=False)` waited for line `L299` to disappear from the window by polling `snapshot` with `await asyncio.sleep(0.02)`.

Under CI load, arbitrary wall-clock sleeps decoupled the test from the TUI's internal event loop and layout phases. Polling `snapshot` before the scroll view was mounted caused keystrokes to be processed out-of-phase, or snapshots to be taken before Textual repainted the canvas at the moved scroll position.

### Fix
Replaced the sleep-based text polling with bounded checks on the scroll view's own observable state:
1. **View Mounted:** Bounded poll waiting for `screen.scroll_view_open` to become `True` (`screen = rig.app.workspace()`), stepping Textual's event loop via `await rig.pilot.pause()` (which pauses for idle/screen update, never a sleep).
2. **Page Moved:** Recorded the pre-move viewport offset from the Ghostty backend (`pos_before = canvas.backend.scrollback_position()`). After sending `pageup`, bounded poll waiting for `pos.offset < pos_before.offset` and `canvas.painted == True`, stepping via `await rig.pilot.pause()`.

No product code was touched: the view does not miss keys.

### Negative Failure Proof
In a scratch copy outside the tree, the move was broken by substituting `pageup` with an inert key (`f`).
Result: `pos.offset < pos_before.offset` never satisfied; the bounded wait failed deterministically with:
```text
AssertionError: the scroll view viewport never moved
```
The test is proven to fail if the view does not move.

---

## 2. Bench Smoke: `tests/test_bench_smoke.py` (Measure Only)

`tests/test_bench_smoke.py` was left **unchanged**. Per instructions, 10 consecutive runs of `tests/test_bench_smoke.py` were executed under load (running alongside a concurrent instance of itself).

### Measured Results Across 10 Runs Under Load

| Run | Exit Code | Failing Test / Message | Stream `yes` (p50 / p95 / max ms \| stall ms \| lock ms \| ahead KB \| queue MB) | Stream `tech` (p50 / p95 / max ms \| stall ms \| lock ms \| ahead KB \| queue MB) |
|:---:|:---:|:---|:---|:---|
| 1 | 0 | None (All 18 passed) | 9.9 / 24.1 / 24.1 \| 2.6 \| 6.2 \| 8.0 \| 0.04 | 24.9 / 29.1 / 29.1 \| 7.2 \| 3.6 \| 8.1 \| 0.03 |
| 2 | 0 | None (All 18 passed) | 25.7 / 26.7 / 26.7 \| 8.1 \| 11.6 \| 8.0 \| 0.03 | 26.8 / 33.8 / 33.8 \| 7.1 \| 3.1 \| 8.1 \| 0.03 |
| 3 | 1 | `test_paint_runs_tiny_...` (line 121):<br>`FAIL 40x12 tech: an attachment was dropped and reattached during the run` | 25.7 / 27.0 / 27.0 \| 2.5 \| 7.6 \| 8.0 \| 0.03 | 80.4 / 161.7 / 161.7 \| 68.2 \| 2.4 \| 265.1 \| 0.48 |
| 4 | 0 | None (All 18 passed) | 25.7 / 28.4 / 28.4 \| 2.9 \| 7.9 \| 8.0 \| 0.05 | 25.7 / 34.1 / 34.1 \| 6.7 \| 4.3 \| 8.0 \| 0.03 |
| 5 | 1 | `test_paint_runs_tiny_...` (line 121):<br>`FAIL 40x12 yes: 1 keystroke(s) never reached the screen`<br>`FAIL 40x12 yes: an attachment was dropped and reattached during the run` | 28.1 / 28.6 / 28.6 \| 27.9 \| 6.9 \| 431.9 \| 0.70 | 22.5 / 26.0 / 26.0 \| 10.9 \| 4.0 \| 8.1 \| 0.02 |
| 6 | 0 | None (All 18 passed) | 26.3 / 34.9 / 34.9 \| 3.6 \| 10.0 \| 0.0 \| 0.03 | 21.7 / 26.6 / 26.6 \| 7.5 \| 4.3 \| 8.0 \| 0.05 |
| 7 | 0 | None (All 18 passed) | 9.2 / 12.7 / 12.7 \| 4.3 \| 14.3 \| 8.0 \| 0.03 | 30.9 / 46.8 / 46.8 \| 10.5 \| 4.5 \| 16.1 \| 0.03 |
| 8 | 0 | None (All 18 passed) | 9.3 / 19.7 / 19.7 \| 3.2 \| 4.6 \| 0.0 \| 0.03 | 15.2 / 25.9 / 25.9 \| 6.5 \| 3.9 \| 8.1 \| 0.04 |
| 9 | 0 | None (All 18 passed) | 9.3 / 9.7 / 9.7 \| 3.5 \| 6.7 \| 0.0 \| 0.03 | 28.6 / 39.5 / 39.5 \| 8.6 \| 6.6 \| 8.1 \| 0.02 |
| 10 | 0 | None (All 18 passed) | 24.0 / 26.0 / 26.0 \| 6.7 \| 4.2 \| 8.0 \| 0.22 | 17.3 / 30.7 / 30.7 \| 6.3 \| 5.4 \| 8.1 \| 0.03 |

### Threshold Failure Analysis
Across 10 runs under load:
1. **Failing Test:** Only `tests/test_bench_smoke.py::test_paint_runs_tiny_against_a_server_of_its_own_and_every_keystroke_comes_back` (line 121: `assert rc == 0, out`).
2. **Failure Frequency:** 2 out of 10 runs failed (20% failure rate under load).
3. **Thresholds Breached:**
   - `result["dropped"] == False`: Failed in 2/10 runs (Run 3 on `tech`, Run 5 on `yes`). Under socket queue congestion and CPU load, a WebSocket disconnect occurred and the pump reattached (`client_id` changed).
   - `result["lost"] == 0`: Failed in 1/10 runs (Run 5 on `yes`: 1 keystroke sent during the transport drop was lost, yielding 3 timed keys instead of 4).
4. **All Other Thresholds Held 100% (10/10 PASS):**
   - `pings_unanswered == 0`: 10/10 runs answered 100% of pings (e.g., 77/77 to 141/141).
   - Feed rate scaling `abs(three/one - 3) < 1e-9`: Passed in all 10 runs.
   - Feed line count ratio `abs(lines_s / (mb_s*1e6) - 1/3) < 0.01`: Passed in all 10 runs.
   - Fleet scale listed count (`13 sessions listed`) and 26 filter keystrokes: Passed in all 10 runs.
   - Slow client non-blocking write `time.perf_counter() - started < 1.0`: Passed in all 10 runs.
   - Queue limit 4011 closure: Passed in all 10 runs.

### Root Cause
`bench/paint.py` runs with three sessions attached and 1 MB/s firehose per session. In `--tiny` mode, timing gating is disabled (`gate=False`), but `judge()` unconditionally asserts plumbing purity: zero keystrokes lost (`lost == 0`) and zero transport drops (`dropped == False`). Under concurrent load, TCP buffer/event loop scheduling latency occasionally trips a WebSocket disconnect; while the TUI correctly reattaches (per DT13), `judge()` marks `dropped` as an immediate failure.

### Options for Orchestrator Ruling (STOP)
- **Option A (Relax tiny judge assertion):** In `bench/paint.py::judge`, under `args.tiny` (`gate=False`), treat `dropped` as informational rather than a fatal smoke failure, or assert that if dropped, reattachment succeeded cleanly.
- **Option B (Structural check in smoke test):** In `tests/test_bench_smoke.py::test_paint_runs_tiny...`, assert that the table was generated with the expected rows and stages, but log rather than fail on transient transport drops during `--tiny` CI runs.
- **Option C (Tolerance / retry threshold):** Allow up to 1 transient drop/reattach under tiny smoke load, or retry once on socket transport drop.
- **Option D (Harness adjustment):** Adjust tiny run parameters (e.g. increase key spacing or reduce firehose rate in tiny mode) to reduce socket buffer pressure under high load.

Awaiting orchestrator ruling before modifying any bench smoke files.

---

## 3. Gates and Verification

All gate outputs were written to individual files under temporary storage outside the tree with exit codes verified (never through a pipe):

1. **Drive Test 10x Consecutive Gate:**
   - Command: `python3 -m pytest tests/test_drive_commands.py -k test_drive_reads_are_live_while_the_view_is_open -p no:cacheprovider`
   - Outcome: 10/10 passed (all exit code 0).
2. **Whole `tests/test_drive_commands.py` Gate:**
   - Command: `python3 -m pytest tests/test_drive_commands.py -p no:cacheprovider`
   - Outcome: 47 passed in 64.37s (exit code 0).
3. **Make Lint Gate:**
   - Command: `make lint PYTHON=python3`
   - Outcome: Ruff check clean; 110 tooling tests passed in 70.51s; import lint clean; docs branch guard clean; leak check clean; engine check PIN verified (exit code 0).

*Note:* System python3 was used because the host environment has opt mounted read-only, preventing virtualenv creation there.
No commit, no push performed. No absolute machine paths in modified files or report.
