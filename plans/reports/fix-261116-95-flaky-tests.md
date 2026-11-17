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

---

## 4. Round 2: Orchestrator Amendment 1 Analysis (STOP Condition Triggered)

### Triggered Instruction
> "If the run's result does not record whether the client reattached, STOP and report what it records."

Per the orchestrator's explicit conditional instruction in Amendment 1, `run_one()` and its returned `result` dictionary in `bench/paint.py` were inspected to determine whether reattachment and client ID state are recorded.

### What the Run's `result` Currently Records
In `bench/paint.py` (`run_one()`, lines 78–92), `result` is constructed as:
```python
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
    }
```
Specifically:
1. **No reattachment status is recorded:** `result` contains only a boolean `"dropped": dropped`.
2. **No client IDs are recorded:** `clients_before` and post-run client IDs are evaluated locally in `run_one()` at line 72 (`dropped = [screen.slot_for(sid).pump.state.client_id for sid in ids] != clients_before`), but are discarded and never placed into `result`.
3. **No run completion status is recorded:** There is no `"completed"` or `"reached_end"` field; `run_one()` either completes and returns the dict or raises an exception.
4. **Dropped-without-reattach ambiguity:** In `tsb_tui/model/attachment.py`, when a socket closes without reattaching (`AttachmentState.apply_close`), `state.client_id` is preserved (not reset to None), while `state.closed` becomes `True`. Thus:
   - If an attachment dropped and *did not* reattach, `[screen.slot_for(sid).pump.state.client_id for sid in ids] != clients_before` is `False`! Line 72 would record `dropped = False` unless `state.closed` is checked.
   - If an attachment dropped and *did* reattach, the server issued a fresh ticket/client_id, so `client_id` changed and line 72 recorded `dropped = True`.

### Why This Blocks Unit Testing of `judge()`
The requested unit test specification requires:
1. `judge()` under `--tiny` given a dropped-and-reattached result with one lost key -> passes with warning.
2. `judge()` under `--tiny` given a dropped-without-reattach result -> fails.
3. `judge()` under `--tiny` given no drop with one lost key -> fails.
4. `judge()` under gated mode given a dropped result -> fails.

Because `judge(result, gate=bool)` operates solely on `result`, it cannot distinguish Case 1 from Case 2 without a schema contract defining how reattachment and run completion are represented in `result`.

### Options for Orchestrator Ruling

- **Option 1 (Explicit boolean flags in `result`):**
  - In `bench/paint.py::run_one`:
    - `clients_after = [screen.slot_for(sid).pump.state.client_id for sid in ids]`
    - `any_closed = any(screen.slot_for(sid).pump.state.closed for sid in ids)`
    - `dropped = (clients_after != clients_before) or any_closed`
    - `reattached = (clients_after != clients_before) and not any_closed`
    - In `result`: add `"reattached": reattached`, `"completed": True`.
  - In `judge()`:
    - If `result["dropped"]`:
      - If not gate and result.get("reattached") and result.get("completed", True):
        - Log/print warning line: `f"WARN {result['grid']} {result['stream']}: an attachment was dropped and reattached ({result['lost']} keystrokes lost)"`
        - Do not fail on `result["dropped"]` or `result["lost"]`.
      - Else:
        - `bad.append("an attachment was dropped and reattached during the run")` (or unrecovered drop failure).

- **Option 2 (Record client ID lists in `result`):**
  - Add `"clients_before": clients_before` and `"clients_after": clients_after` to `result`.
  - `judge()` checks whether `clients_after != clients_before` and all slots have valid non-closed clients.

- **Option 3 (Warning delivery contract):**
  - Clarify whether `judge()` itself prints the warning line directly to stdout via `print()`, or if `judge()` returns a tuple `(bad: list[str], warnings: list[str])` for `main()` to print.

---

## 5. Round 2: Implementation, Red-First Verification, and Gating (Amendments 1 & 2)

### Orchestrator Ruling Applied (Amendment 2)
The orchestrator adopted Option 1:
- `bench/paint.py::run_one()` records three booleans in `result`:
  - `dropped`: any pump's client id changed, or any pump closed.
  - `reattached`: client ids changed and no pump closed at the end.
  - `completed`: the run reached its end and wrote its table row (`True`).
- `bench/paint.py::judge()`:
  - In `--tiny` mode (`gate=False`), passes a dropped run only if `reattached` and `completed` are both `True`.
  - Prints one warning line prefixed `WARN` naming the stream, grid, drop, and keystrokes lost (`print(f"WARN {result['grid']} {result['stream']}: an attachment was dropped and reattached ({result['lost']} keystrokes lost)")`).
  - Lost keystrokes are tolerated only when `dropped`, `reattached`, and `completed` are `True`. An unrecovered drop or a drop with incomplete run fails. A run with no drop must lose zero keystrokes.
  - Unanswered pings remain fatal in every run.
  - Gated mode (`gate=True`) remains 100% unchanged.

### Red-First Unit Test Verification (`tests/test_bench_judge.py`)
Unit tests covering all four ruled behaviors were created in `tests/test_bench_judge.py`:
1. `test_tiny_mode_judge_dropped_and_reattached_with_lost_key_passes_with_warning`: verifies tiny-mode judge given a dropped-and-reattached completed result with 1 lost keystroke passes with the expected `WARN` message.
   - **Red run failure on main:** Failed with `AssertionError: assert ['1 keystroke(s) never reached the screen', 'an attachment was dropped and reattached during the run'] == []` because main unconditionally failed on any drop and any lost key.
2. `test_tiny_mode_judge_dropped_without_reattach_fails`: verifies tiny-mode judge fails if dropped but not reattached, or dropped and incomplete.
3. `test_tiny_mode_judge_no_drop_with_one_lost_key_fails`: verifies zero tolerance for lost keystrokes when no drop occurred.
4. `test_gated_judge_given_dropped_result_fails`: verifies gated judge unconditionally fails on drop.

### Implementation
- `bench/paint.py`: Added `clients_after`, `any_closed`, `dropped`, `reattached`, and `completed` computation in `run_one()`, adding `reattached` and `completed` to `result`. Updated `judge()` to branch on `gate`, handling `--tiny` tolerance with `WARN` logging to stdout while leaving gated mode unchanged.

### Verification and Gates
All outputs directed to separate files in temporary storage outside tree, exit codes verified, no pipes:
1. **The New Tests:**
   - Command: `python3 -m pytest tests/test_bench_judge.py`
   - Outcome: 4 passed in 0.47s (exit code 0).
2. **Bench Smoke Suite (5 consecutive runs):**
   - Command: `python3 -m pytest tests/test_bench_smoke.py`
   - Run 1: 18 passed in 12.96s (exit code 0).
   - Run 2: 18 passed in 13.00s (exit code 0).
   - Run 3: 18 passed in 12.85s (exit code 0).
   - Run 4: 18 passed in 13.22s (exit code 0).
   - Run 5: 18 passed in 13.01s (exit code 0).
3. **Make Lint Gate:**
   - Command: `make lint PYTHON=python3`
   - Outcome: Ruff check clean ("All checks passed!"); 110 tooling tests passed in 72.30s; import lint clean (194 files); docs branch guard clean; leak check clean; engine check PIN verified (exit code 0).

---

## 6. Round 3: Qodo Review Findings on bebcfa5 (#97) (Amendment 3)

### Ruled Requirements
1. **Recovered Tiny Run Screen Check:**
   - In `--tiny` mode (`gate=False`), a dropped, reattached, and completed run passes only if at least one keystroke reached the screen (`lost < keys`).
   - If every key was lost (`lost >= keys`), the run fails with `"no keystroke came back after the drop"`, since the screen never resumed.
   - If at least one key came back (`lost < keys`), the run passes with the `WARN` line.
2. **Accurate Gated Drop Message:**
   - In gated mode (`gate=True`), on a dropped result, the judge reports:
     - `"an attachment was dropped and reattached"` when `reattached` is True.
     - `"an attachment was dropped and did not reattach"` otherwise.
   - It fails either way.

### Red-First Unit Test Verification (`tests/test_bench_judge.py`)
Tests specifying the requirements were added to `tests/test_bench_judge.py` and executed against `bebcfa5` prior to updating `bench/paint.py`:
- `test_tiny_mode_judge_dropped_and_reattached_with_every_key_lost_fails`:
  - Result: Failed with `AssertionError: assert [] == ['no keystroke came back after the drop']`. On `bebcfa5`, `judge` previously returned `[]` and emitted `WARN` even when all keystrokes were lost.
- `test_gated_judge_dropped_and_reattached_message`:
  - Result: Failed with `AssertionError: assert ['an attachment was dropped and reattached during the run'] == ['an attachment was dropped and reattached']`.
- `test_gated_judge_dropped_without_reattach_message`:
  - Result: Failed with `AssertionError: assert ['an attachment was dropped and reattached during the run'] == ['an attachment was dropped and did not reattach']`.
- `test_tiny_mode_judge_dropped_and_reattached_with_one_key_back_passes_with_warning`:
  - Result: Passed with `WARN` line and `3 keystrokes lost` (proving that one key back passes).

### Implementation
In `bench/paint.py::judge()`:
- **Gated mode:** Updated the `result["dropped"]` handler to branch on `result.get("reattached")`, appending `"an attachment was dropped and reattached"` when True, and `"an attachment was dropped and did not reattach"` otherwise.
- **Tiny mode:** Updated the recovered drop branch (`result.get("reattached") and result.get("completed")`) to check `result["lost"] >= result["keys"]`. If True, appends `"no keystroke came back after the drop"`; otherwise logs the `WARN` line.

### Verification and Gates
All outputs directed to separate files in temporary storage outside tree, exit codes verified, no pipes:
1. **Judge Unit Tests:**
   - Command: `python3 -m pytest tests/test_bench_judge.py`
   - Outcome: 7 passed in 0.47s (exit code 0).
2. **Bench Smoke Suite (Single Run):**
   - Command: `python3 -m pytest tests/test_bench_smoke.py`
   - Outcome: 18 passed in 15.28s (exit code 0).
3. **Make Lint Gate:**
   - Command: `make lint PYTHON=python3`
   - Outcome: Ruff check clean ("All checks passed!"); 110 tooling tests passed in 71.40s; import lint clean (194 files); docs branch guard clean; leak check clean; engine check PIN verified (exit code 0).

No commit, no push performed. No absolute machine paths in any file.
