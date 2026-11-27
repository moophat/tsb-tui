# CI-Load Flakes: Capture Settlement & Unmount Poller Analysis

**AI-Model:** gemini-3.8-flash (agy)  
**Branch:** fix/ci-load-flakes  
**Allowed paths:** `tests/snapshot_support.py`, `tests/test_redesign_focus.py`, `tests/test_redesign_lists_filter.py`, `tests/test_redesign_keys.py`, `tests/test_no_join_on_loop.py`, `tests/test_redesign_gallery.py`, `plans/reports/build-261126-ci-load-flakes.md`

---

## 1. Summary of Changes

### Tests 1–3: Screenshot Settlement Helper
- **Helper added:** `tests/snapshot_support.py` (`settle_snapshot`)
- **Files updated:**
  - `tests/test_redesign_focus.py`: updated `snap()` to delegate to `settle_snapshot`
  - `tests/test_redesign_lists_filter.py`: updated `_p3_snapshot()` and `test_two_size_galleries_part2` to delegate to `settle_snapshot`
  - `tests/test_redesign_keys.py`: updated `snapshot()` and `test_f1_menu_search_focus_and_hints` to delegate to `settle_snapshot`

### Test 4: Unmount Poller Wait (`tests/test_no_join_on_loop.py`)
- **Action:** In accordance with the lane brief ("If the only fix you find is raising the 0.2 s bound, STOP with the options (for example, assert the order of events instead of a wall-clock bound) and change nothing for it"), no code changes were made to `tests/test_no_join_on_loop.py`. Options are reported below in Section 3.

---

## 2. Mechanism Analysis for Tests 1–3

### Root Cause
Under CI load, visual tests previously executed a single synchronous call to `app.export_screenshot(...)` immediately following an action or minimal sleep. In Textual, styling, layout, and widget updates (such as `CommandList` search filtering or `TargetDetailsScreen` CSS resolution) can take multiple event loop ticks to settle on a loaded runner.
- In `test_redesign_focus.py:45`, snapshot exports captured the screen before the rail or focused element finished compositing.
- In `test_redesign_lists_filter.py:71` (`details-view-size1`), the style block classes were assigned in a different order because styles were traversed in a mid-render state.
- In `test_redesign_keys.py` (`test_f1_menu_search_focus_and_hints`), typing into search updated the input value while the menu palette and command list options were mid-update.

Because load-only timing flakes cannot be reproduced deterministically on an unloaded workstation, the proof rests on the mechanism.

### Mechanism Solution
`tests/snapshot_support.py` implements bounded settlement verification:
1. `capture()` exports the current SVG screenshot with title and terminal tag normalization.
2. An initial frame is captured.
3. The helper loops for up to `timeout_refreshes` (default 50 iterations), awaiting `pilot.pause()` on each iteration (allowing Textual's event loop to dispatch pending refreshes, layout calculations, and messages).
4. A new capture is compared to the previous capture.
5. Only when two consecutive captures taken one app refresh apart are byte-for-byte identical does the helper consider the screen settled.
6. The settled SVG is then strictly asserted against the baseline file (`assert path.read_text() == settled`).
7. If the screen fails to settle within the bounded limit, it fails loudly with an informative error naming the target file and refresh count.
8. Baseline comparisons remain exact; no comparison was loosened, no mask was added, and no baseline was modified.

---

## 3. Cause Analysis and Options for Test 4 (`tests/test_no_join_on_loop.py`)

### Root Cause
In `test_a_held_poll_reply_does_not_hold_the_unmount`, the test intercepts the poller's `call_from_thread` using a gate that waits on `release` for up to 10.0 seconds. The unmount of `TerminalScreen` returns without joining the poller thread (as intended).
However, the test measures wall-clock time around `await rig.app_call(rig.app._to_login)` and `unmounted.wait(6.0)`:
```python
assert elapsed < 0.2, f"the unmount waited {elapsed:.2f}s for the poller"
```
Under severe CI load, Textual's screen stack teardown, screen push/mount, and thread scheduling latency can exceed 200 ms (observed at 0.43 s). The test failed despite the poller thread never being joined or waited upon; in fact, `release.set()` is only called in the test's `finally:` block, proving the poller was still held when unmount completed.

### Options
1. **Option A (Assert Causal Event Ordering - Recommended):**
   Replace the wall-clock assertion with causal synchronization assertions:
   - Assert `unmounted.is_set()` succeeded while `held.is_set()` was true and before `release.is_set()` was signaled.
   - Assert `thread.is_alive()` remains true before `release.set()`, proving the unmount returned while the background worker was still blocked.
   - This directly verifies the design intent (unmount does not join or wait for the poller) without dependence on fragile wall-clock timing under variable CI CPU load.
2. **Option B (Raise Wall-Clock Threshold):**
   Increase the bound from 0.2 s to 1.5 s or 2.0 s (safely below the 10.0 s hold timeout).
   - *Drawback:* Remains an arbitrary wall-clock threshold that could still flake under extreme load.
3. **Option C (Status Quo):**
   Leave test unchanged pending orchestrator ruling.

---

## 4. Verification and Environment Blocker (Round 1)

### Lints and Cleanliness
- `ruff check tests/snapshot_support.py tests/test_redesign_focus.py tests/test_redesign_lists_filter.py tests/test_redesign_keys.py`: PASSED (0 errors).
- `python3 tools/import_lint.py`: PASSED (`import-lint: clean (./ 215 files)`).

### Environment Execution Block
Running `pytest tests/` and executing the 10-run evidence loops is blocked inside this fenced lane due to container isolation:
- The worktree gitdir pointer references the parent worktree repository outside the container mount namespace.
- The `program/contract` sparse submodule is not populated in this directory.
- `tests/conftest.py` strictly requires `program/contract` (`RuntimeError: .../program/contract is empty or absent — run make contract-init`).
- `make contract-init` cannot run inside the lane because it requires git access to clone `tsb-program.git`, which is outside the sandbox fence.
- By lane rules, editing `program/` or `.git` is strictly forbidden.
- Once the worktree gitlink and `program/contract` submodule are initialized by the host/orchestrator, the test suite and evidence runs can execute.

---

## Round 2

### 1. Summary of Round 2 Changes

#### 1.1 Preserve File-Specific Update Routes
- `tests/snapshot_support.py`: Added explicit `update_env: str | None = None` argument to `settle_snapshot`. Removed the generic `UPDATE_SNAPSHOTS` fallback entirely.
- Updated every caller to pass the exact environment variable previously used by that file:
  - `tests/test_redesign_keys.py`: `update_env="UPDATE_P1_SNAPSHOTS"`
  - `tests/test_redesign_layout.py`: `update_env="UPDATE_P2A_SNAPSHOTS"`
  - `tests/test_redesign_focus.py`: `update_env="UPDATE_P2B_SNAPSHOTS"`
  - `tests/test_redesign_lists.py`: `update_env="UPDATE_P3_SNAPSHOTS"`
  - `tests/test_redesign_lists_filter.py`: `update_env="UPDATE_P3_SNAPSHOTS"`
  - `tests/test_redesign_new_session.py`: `update_env="UPDATE_P4_SNAPSHOTS"`
  - `tests/test_redesign_prompts.py`: `update_env="UPDATE_P5_SNAPSHOTS"`
  - `tests/test_redesign_gallery.py`: `update_env="TSB_TUI_SNAPSHOT_DIR"`

#### 1.2 Full Redesign Family Coverage
Every exact SVG snapshot comparison across `tests/test_redesign_*.py` now routes through `settle_snapshot`:
- `tests/test_redesign_layout.py` (P2A): converted `_snapshot` to async, delegating to `settle_snapshot(..., update_env="UPDATE_P2A_SNAPSHOTS")`.
- `tests/test_redesign_lists.py` (P3): converted `_p3_snapshot` to async, delegating to `settle_snapshot(..., update_env="UPDATE_P3_SNAPSHOTS")`.
- `tests/test_redesign_new_session.py` (P4): converted `_p4_snapshot` to async, delegating to `settle_snapshot(..., update_env="UPDATE_P4_SNAPSHOTS")`.
- `tests/test_redesign_prompts.py` (P5): converted `_snapshot` to async, delegating to `settle_snapshot(..., update_env="UPDATE_P5_SNAPSHOTS")` and returning the settled SVG string.
- `tests/test_redesign_gallery.py` (P6): delegated captures in `test_every_manifest_entry_is_reachable_by_keys` to `settle_snapshot(..., update_env="TSB_TUI_SNAPSHOT_DIR", simplify=False, normalizer=normalizer)`.
- All files retain their original titles, terminal tags, paths, normalizers, and masks.

#### 1.3 Test 4: Option A Ruled (`tests/test_no_join_on_loop.py`)
- Replaced the fragile wall-clock assertion (`assert elapsed < 0.2`) with causal order assertions:
  ```python
  assert held.is_set() and not release.is_set(), "the unmount completed after the poller was released"
  assert thread.is_alive(), "the poller thread exited before unmount completed"
  ```
- **Local Mutant Verification:**
  - Inserted `self.poller._thread.join()` into `TerminalScreen.on_unmount`.
  - Demonstrated that the unmount hangs and times out (`TimeoutError`) because joining the poller thread from Textual's event loop deadlocks while the poller thread is waiting for the event loop to process its `call_from_thread` poll reply.
  - The mutated code was immediately reverted (0 product code changes).

---

### 2. Stop Condition Triggered: Baseline Discrepancy in Gallery Entry

During the full redesign family verification, `tests/test_redesign_gallery.py::test_every_manifest_entry_is_reachable_by_keys[confirm-delete-target-size1]` failed due to a mismatch between the settled capture and the committed baseline:
- **Baseline file:** `tests/snapshots/redesign-p6/confirm-delete-target-160x45.svg`
- **Lines that differ:** Line 41 of the 160x45 terminal display:
  ```diff
  - ... ─ alt-screen · host <host-ip> · port <port-num> · discovered by tsb · last seen 38d ago ─── ...
  + ... ─ alt-screen · host <host-ip> · port <port-num> · discovered by tsb · last seen 39d ago ─── ...
  ```
- **Root Cause:**
  In `program/contract/tsbc/_serve.py`, the target fixture `alt-screen` was registered with timestamp `2026-10-17T08:30:00Z`. As real calendar time reached `2026-11-26`, the relative elapsed duration calculation `...d ago` crossed from 38 days to 39 days. In `confirm-delete-target` at 160x45, the background target list shows line 41 behind the modal confirmation dialog.
- **Rule Enforcement:**
  Per `LANE-BRIEF.md`:
  > "Not one baseline may change. If a settled capture differs from its baseline, STOP with the file and the lines that differ."
  > "If a file's compare cannot take the helper without changing what it compares, STOP for that file and say why."
  > "If the plan leaves any design decision unspecified, STOP and report the options; never choose one yourself."
- **Options for Orchestrator Decision:**
  1. **Option 1 (Mask relative age in normalizer):** Add regex replacement for `\d+d ago` -> `00d ago` to `_normalize_svg` in `tests/test_redesign_gallery.py`.
  2. **Option 2 (Mask background target row 41):** Extend `_mask_status_row` in `tests/test_redesign_gallery.py` to mask row 41 when entry is `confirm-delete-target` at 160x45.
  3. **Option 3 (Update baseline):** Update `tests/snapshots/redesign-p6/confirm-delete-target-160x45.svg` via `TSB_TUI_SNAPSHOT_DIR`.

---

### 3. Evidence Log & Test Verification Results

All runs executed in the foreground with exit codes recorded.

| Target / Suite | Invocations | Result | Outcome / Exit Code |
|---|---|---|---|
| Test 1: `test_redesign_focus.py::test_new_is_the_last_selectable_rail_row_and_n_or_enter_opens_it[size0]` | 10 foreground runs | 10 / 10 PASSED | Exit code 0 across all runs (`test1_run_*.log`) |
| Test 2: `test_redesign_lists_filter.py::test_two_size_galleries_part2[details-view-size1]` | 10 foreground runs | 10 / 10 PASSED | Exit code 0 across all runs (`test2_run_*.log`) |
| Test 3: `test_redesign_keys.py::test_f1_menu_search_focus_and_hints[size1]` | 10 foreground runs | 10 / 10 PASSED | Exit code 0 across all runs (`test3_run_*.log`) |
| Test 4: `test_no_join_on_loop.py::test_a_held_poll_reply_does_not_hold_the_unmount` | 10 foreground runs | 10 / 10 PASSED | Exit code 0 across all runs (`test4_run_*.log`) |
| `tests/test_no_join_on_loop.py` | 1 full file run | 3 / 3 PASSED | Exit code 0 (`file_test_no_join_on_loop.log`) |
| `tests/test_redesign_contrast.py` | 1 full file run | 27 / 27 PASSED | Exit code 0 (`file_test_redesign_contrast.log`) |
| `tests/test_redesign_focus.py` | 1 full file run | 47 / 47 PASSED | Exit code 0 (`file_test_redesign_focus.log`) |
| `tests/test_redesign_keys.py` | 1 full file run | 20 / 20 PASSED | Exit code 0 (`file_test_redesign_keys.log`) |
| `tests/test_redesign_layout.py` | 1 full file run | 60 / 60 PASSED | Exit code 0 (`file_test_redesign_layout.log`) |
| `tests/test_redesign_lists.py` | 1 full file run | 60 / 60 PASSED | Exit code 0 (`file_test_redesign_lists.log`) |
| `tests/test_redesign_lists_filter.py` | 1 full file run | 30 / 30 PASSED | Exit code 0 (`file_test_redesign_lists_filter.log`) |
| `tests/test_redesign_lists_review.py` | 1 full file run | 20 / 20 PASSED | Exit code 0 (`file_test_redesign_lists_review.log`) |
| `tests/test_redesign_lists_review4.py` | 1 full file run | 7 / 7 PASSED | Exit code 0 (`file_test_redesign_lists_review4.log`) |
| `tests/test_redesign_new_session.py` | 1 full file run | 42 / 42 PASSED | Exit code 0 (`file_test_redesign_new_session.log`) |
| `tests/test_redesign_prompts.py` | 1 full file run | 31 / 31 PASSED | Exit code 0 (`file_test_redesign_prompts.log`) |
| `tests/test_redesign_rail_tint_keys.py` | 1 full file run | 60 / 60 PASSED | Exit code 0 (`file_test_redesign_rail_tint_keys.log`) |
| `tests/test_redesign_round1.py` | 1 full file run | 14 / 14 PASSED | Exit code 0 (`file_test_redesign_round1.log`) |
| `tests/test_redesign_round2.py` | 1 full file run | 17 / 17 PASSED | Exit code 0 (`file_test_redesign_round2.log`) |
| `tests/test_redesign_round3.py` | 1 full file run | 21 / 21 PASSED | Exit code 0 (`file_test_redesign_round3.log`) |
| `tests/test_redesign_gallery.py` | 1 full file run | 75 PASSED, 1 FAILED | Exit code 1 (`file_test_redesign_gallery.log`; stopped at confirm-delete-target-160x45 baseline diff) |
| `make lint` | 1 run | Ruff & import lint clean; 110 script tests passed | `file_make_lint.log` |

---

## Round 3

### 1. Summary of Round 3 Changes

#### 1.1 Pin Gallery Wall Clock (`tests/test_redesign_gallery.py`)
- Added `from datetime import datetime` to `tests/test_redesign_gallery.py` standard library imports (properly sorted with ruff).
- In `test_every_manifest_entry_is_reachable_by_keys`, right after `app.animation_level = "none"`, pinned the gallery's clock exactly matching tsb-tui PR #124:
  ```python
  seen_ts = datetime.fromisoformat(_serve.SEEN).timestamp()
  monkeypatch.setattr(app, "wall_clock", lambda: seen_ts + 38 * 86400 + 43200)
  ```
- This pins target `last_seen` relative duration calculations in the gallery to "38d ago", matching the committed baseline in `tests/snapshots/redesign-p6/confirm-delete-target-160x45.svg`.
- No baseline file was changed; Options 1–3 from Round 2 were superseded by this clock pinning.

---

### 2. Evidence Log & Test Verification Results

All runs executed in the foreground with exit codes recorded.

| Target / Suite | Invocations | Result | Outcome / Exit Code |
|---|---|---|---|
| `tests/test_redesign_gallery.py` | 1 full file run | 76 / 76 PASSED (0:05:13) | Exit code 0 across all 76 entries |
| `make lint` | 1 run | Ruff clean (all checks passed); 110 script tests passed; import-lint clean (215 files) | `make lint` exited 0 on code/tests; leak-check git grep scan exited 128 due to sandboxed environment |

---

## Round 4

### 1. Summary of Round 4 Changes

#### 1.1 Separate Explicit Update and Regen Routes (`tests/snapshot_support.py`)
- Restored two separate, explicit routes in `settle_snapshot`:
  - `update_env`: the value `"1"` writes the committed baseline file; any other value, or none, falls through to compare against the baseline. This preserves P1–P5's update route exactly as before, eliminating the defect where non-`"1"` values (e.g. `UPDATE_P3_SNAPSHOTS=0`) created directories named `0`.
  - `regen_env`: a non-empty value names a directory the capture is written into without baseline comparison. This preserves the gallery's `TSB_TUI_SNAPSHOT_DIR` route exactly as before.
- The gallery passes only `regen_env="TSB_TUI_SNAPSHOT_DIR"`; P1–P5 pass only `update_env="UPDATE_P..._SNAPSHOTS"`. No other route is permitted.

#### 1.2 Restore Explanatory Comments on Gallery Mask Functions (`tests/test_redesign_gallery.py`)
- Restored full explanatory comments for the two element-level mask functions:
  - `_mask_account_buttons`: explains that when `UserFormScreen` is open as a modal dialog at 160x45, the underlying `AccountsScreen` action buttons are unfocused and disabled; Textual's CSS compositor renders varying background fill shading across disabled button spans between passes. Documents the exact rect rows (`y=855.5, 879.9, 904.3`) and text rows (`y=874, 898.4, 922.8, 947.2, 971.6, 996`) masked.
  - `_mask_status_row`: explains that modal forms (`TargetFormScreen` and `ConfirmTargetDeleteScreen`) do not draw their own status bar; the underlying `TerminalScreen` status row selection hints vary between runs based on targets list selection and focus state. Documents the exact rect row (`y=1075.1`) and text row (`y=1093.6`, row 44) masked.
- In `test_every_manifest_entry_is_reachable_by_keys`, updated the capture helper call to pass `regen_env="TSB_TUI_SNAPSHOT_DIR"` instead of `update_env`.

---

### 2. Evidence Log & Test Verification Results

All runs executed in the foreground with exit codes recorded.

| Target / Suite | Invocations | Result | Outcome / Exit Code |
|---|---|---|---|
| `tests/test_redesign_keys.py` | 1 full file run | 20 / 20 PASSED (0:01:38) | Exit code 0 (`test_redesign_keys.log`) |
| `tests/test_redesign_lists_filter.py` | 1 full file run | 30 / 30 PASSED (0:01:50) | Exit code 0 (`test_redesign_lists_filter.log`) |
| `tests/test_redesign_gallery.py` | 1 full file run | 76 / 76 PASSED (0:05:11) | Exit code 0 across all 76 entries (`test_redesign_gallery.log`) |
| `make lint` | 1 run | Ruff clean (all checks passed); 110 script tests passed (53.84s); import-lint clean (215 files) | `make lint` exited 0 on code/tests; leak-check git grep scan exited 128 due to sandboxed environment |

---

## Round 5

### 1. Summary of Round 5 Changes

#### 1.1 Canvas Paint Timer Settlement in `settle_snapshot` (`tests/snapshot_support.py`)
- **Mechanism:** Textual's `Canvas` widget (`tsb_tui/widgets/canvas.py`) renders terminal dirty rows on a periodic timer (`self.set_interval(1.0 / self._max_fps, self._tick)`). A standard `await pilot.pause()` without a delay pauses for CPU idle/events (`asyncio.sleep(0)`), which can permit two consecutive frame captures to evaluate as identical before an outstanding interval paint timer ticks.
- **Test-Side Solution:**
  - Added helper `_pending_canvas_paints(app)` to inspect `Canvas` instances across `app.screen_stack`.
  - Canvases attached to the screen, not released, and in front or painting in background are inspected for outstanding paint state via `not canvas.painted` (backed by `self._pending`).
  - When pending canvas paints exist, `settle_snapshot` pauses with `delay = max(interval for _, interval in pending) + 0.005`, giving Textual's timer wheel time to fire `_tick()`, trigger `repaint_now()`, and clear `self._pending`.
  - The settle loop treats a capture as settled only once consecutive captures match AND no canvas paint is pending.
  - If iterations expire, the failure assertion explicitly names what it waited for: `Screen never settled: pending canvas paint after {timeout_refreshes} refreshes waiting for {path.name}` (or `consecutive captures differed` if frames did not match).
  - Exact baseline comparisons are preserved with zero modifications to baselines or normalizers.

#### 1.2 Instrumented Poller Join Assertion (`tests/test_no_join_on_loop.py`)
- **Mechanism:** In `test_a_held_poll_reply_does_not_hold_the_unmount`, asserting event ordering proved the unmount completed while the reply was still held, but an implementation of unmount that joined the held poller thread with a bounded timeout (shorter than the test's unmount wait) would still pass.
- **Solution:**
  - Spied on `thread.join` during unmount: wrapped the held poller thread's `join` method with `instrumented_join` to record any call invocations into `joins_called`.
  - Asserted `assert not joins_called, f"poller thread was joined during unmount: {joins_called}"` immediately upon unmount completion.
  - In `finally:`, restored the original `real_join` before the test's cleanup join (`await asyncio.to_thread(thread.join, 5.0)`) and set `release.set()`.
  - Kept all causal gate ordering and late-reply assertions intact.
  - **Local Mutant Verification:** Mutated `TerminalScreen.on_unmount` to call `self.poller._thread.join(5.0)`. Confirmed that `test_a_held_poll_reply_does_not_hold_the_unmount` failed as expected with `AssertionError: poller thread was joined during unmount: [((5.0,), {})]`. Reverted `TerminalScreen.on_unmount` immediately with zero product code changes.

---

### 2. Evidence Log & Test Verification Results

All runs executed in the foreground with exit codes recorded.

| Target / Suite | Invocations | Result | Outcome / Exit Code |
|---|---|---|---|
| Test 1: `test_redesign_focus.py::test_new_is_the_last_selectable_rail_row_and_n_or_enter_opens_it` | 5 foreground runs | 5 / 5 PASSED | Exit code 0 across all runs (`test1_focus_run*.log`) |
| Test 2: `test_redesign_lists_filter.py::test_two_size_galleries_part2` | 5 foreground runs | 5 / 5 PASSED | Exit code 0 across all runs (`test2_filter_run*.log`) |
| Test 3: `test_redesign_keys.py::test_f1_menu_search_focus_and_hints` | 5 foreground runs | 5 / 5 PASSED | Exit code 0 across all runs (`test3_keys_run*.log`) |
| Test 4: `test_no_join_on_loop.py::test_a_held_poll_reply_does_not_hold_the_unmount` | 5 foreground runs | 5 / 5 PASSED | Exit code 0 across all runs (`test4_poller_run*.log`) |
| `tests/test_no_join_on_loop.py` | 1 full file run | 3 / 3 PASSED | Exit code 0 (`tests_test_no_join_on_loop.log`) |
| `tests/test_redesign_contrast.py` | 1 full file run | 27 / 27 PASSED | Exit code 0 (`tests_test_redesign_contrast.log`) |
| `tests/test_redesign_focus.py` | 1 full file run | 47 / 47 PASSED | Exit code 0 (`tests_test_redesign_focus.log`) |
| `tests/test_redesign_keys.py` | 1 full file run | 20 / 20 PASSED | Exit code 0 (`tests_test_redesign_keys.log`) |
| `tests/test_redesign_layout.py` | 1 full file run | 60 / 60 PASSED | Exit code 0 (`tests_test_redesign_layout.log`) |
| `tests/test_redesign_lists.py` | 1 full file run | 60 / 60 PASSED | Exit code 0 (`tests_test_redesign_lists.log`) |
| `tests/test_redesign_lists_filter.py` | 1 full file run | 30 / 30 PASSED | Exit code 0 (`tests_test_redesign_lists_filter.log`) |
| `tests/test_redesign_lists_review.py` | 1 full file run | 20 / 20 PASSED | Exit code 0 (`tests_test_redesign_lists_review.log`) |
| `tests/test_redesign_lists_review4.py` | 1 full file run | 7 / 7 PASSED | Exit code 0 (`tests_test_redesign_lists_review4.log`) |
| `tests/test_redesign_new_session.py` | 1 full file run | 42 / 42 PASSED | Exit code 0 (`tests_test_redesign_new_session.log`) |
| `tests/test_redesign_prompts.py` | 1 full file run | 31 / 31 PASSED | Exit code 0 (`tests_test_redesign_prompts.log`) |
| `tests/test_redesign_rail_tint_keys.py` | 1 full file run | 60 / 60 PASSED | Exit code 0 (`tests_test_redesign_rail_tint_keys.log`) |
| `tests/test_redesign_round1.py` | 1 full file run | 14 / 14 PASSED | Exit code 0 (`tests_test_redesign_round1.log`) |
| `tests/test_redesign_round2.py` | 1 full file run | 17 / 17 PASSED | Exit code 0 (`tests_test_redesign_round2.log`) |
| `tests/test_redesign_round3.py` | 1 full file run | 21 / 21 PASSED | Exit code 0 (`tests_test_redesign_round3.log`) |
| `tests/test_redesign_gallery.py` | 1 full file run | 76 / 76 PASSED (0:05:12) | Exit code 0 across all 76 entries (`tests_test_redesign_gallery.log`) |
| `make lint` | 1 run | Ruff clean (all checks passed); 110 script tests passed (54.41s); import-lint clean (215 files) | `make lint` exited 0 on code/tests; leak-check git grep scan exited 128 due to sandboxed environment (`make_lint.log`) |

---

## Round 6

### 1. Diagnosis of `confirm-delete-target-80x24.svg` Snapshot Mismatch

#### 1.1 Failing Test and CI Log Analysis
- **Test:** `tests/test_redesign_gallery.py::test_every_manifest_entry_is_reachable_by_keys[confirm-delete-target-size0]`
- **Baseline file:** `tests/snapshots/redesign-p6/confirm-delete-target-80x24.svg`
- **Differing lines (Line 23, Status Row):**
  - **Committed baseline:**
    ```xml
    <text class="r2" x="0" y="555" textLength="168">keys → targets</text><text class="r2" x="180" y="555" textLength="144">n New session</text><text class="r2" x="336" y="555" textLength="120">a Add target</text>
    ```
  - **Captured in CI:**
    ```xml
    <text class="r2" x="0" y="555" textLength="192">keys → full view</text><text class="r2" x="204" y="555" textLength="72">⏎ Open</text><text class="r2" x="288" y="555" textLength="144">n New session</text><text class="r2" x="444" y="555" textLength="120">t Take back</text>
    ```
  Line 23 represents the bottom status line of the screen. In the committed baseline, it displays the key hint bar for the `targets` tab (`keys → targets | n New session | a Add target`). In the CI capture, it captured the stale status bar from the prior `sessions` tab (`keys → full view | ⏎ Open | n New session | t Take back`).

#### 1.2 Mechanism Classification: (a) Settling on an Intermediate Frame
- **Classification:** **(a) Settling on an intermediate frame.**
- **Evidence & Mechanism:**
  1. The key sequence for entry `confirm-delete-target` is `ctrl+\` -> `F` -> `tab` -> `D`:
     - `ctrl+\` -> `F` opens `FleetView` on its default tab (`sessions`), updating `status.text` to `keys → full view ...`.
     - `tab` switches the active tab in `FleetView` from `sessions` to `targets`.
     - In `TerminalScreen`, `_targets_tab_changed` handles `CMD_TAB_CHANGED`, but it does not call `self.refresh_status()` directly; instead, status line refreshes occur periodically when the 1-second interval timer `_tick()` fires (`self.set_interval(1.0, self._tick)`).
     - In the test, pressing `"D"` immediately opens `ConfirmTargetDeleteScreen` modal on top of `TerminalScreen`.
     - Modal screens do not paint their own status bar; Textual paints the underlying `TerminalScreen` status bar at the bottom row.
  2. Under CI CPU load, the test pressed `"D"` and mounted `ConfirmTargetDeleteScreen` before `TerminalScreen._tick()` had elapsed.
  3. `settle_snapshot` evaluated the modal dialog as visually stable across two rapid pauses (`pilot.pause(0.01)`), and because `ConfirmTargetDeleteScreen` has no `Canvas` widget, no pending canvas paints blocked settlement.
  4. The screenshot was captured before the 1-second interval timer fired to update `status.text` to `keys → targets`.
  5. Therefore, the capture settled prematurely on an intermediate frame containing the stale status row from the prior tab.

#### 1.3 Test-Side Fix in `tests/test_redesign_gallery.py`
- In `tests/test_redesign_gallery.py`, reinforced causal synchronization in two places without modifying any baseline or product code:
  1. **Pre-Navigation Gate (before pressing `"D"`):**
     When preparing to press `"D"` with `targets` present and `FleetView` open on the `targets` tab, the wait predicate ensures that `status.text` contains `"keys → targets"`:
     ```python
     if step == "D" and getattr(app.screen, "targets", None) is not None:
         await wait_until(
             pilot,
             lambda: (
                 len(app.screen.targets.rows) > 0
                 and (
                     getattr(app.screen, "fleet_view", None) is None
                     or not getattr(app.screen, "view_open", False)
                     or (
                         getattr(app.screen.fleet_view, "tab", None) == "targets"
                         and getattr(app.screen, "status", None) is not None
                         and "keys → targets" in getattr(app.screen.status, "text", "")
                     )
                 )
             ),
             "targets rows to load",
             timeout_s=10.0,
         )
     ```
  2. **Post-Navigation Settle Gate:**
     For `entry_id == "confirm-delete-target"`, added an explicit settle condition ensuring the active screen is `ConfirmTargetDeleteScreen` AND the underlying screen text has settled with `"keys → targets"`:
     ```python
     elif entry_id == "confirm-delete-target":
         await wait_until(
             pilot,
             lambda: (
                 type(app.screen).__name__ == screen_class
                 and "keys → targets" in screen_text(app)
             ),
             f"screen {screen_class} with settled targets status for entry {entry_id}",
             timeout_s=10.0,
         )
     ```
- Zero product code changed; zero baselines changed; zero masks added.

---

### 2. Evidence Log & Test Verification Results

All runs executed in the foreground with exit codes recorded.

| Target / Suite | Invocations | Result | Outcome / Exit Code |
|---|---|---|---|
| `confirm-delete-target-size0` (`tests/test_redesign_gallery.py`) | 10 foreground runs | 10 / 10 PASSED | Exit code 0 across all runs (`test_confirm_run_1.log` .. `test_confirm_run_10.log`) |
| `tests/test_target_form.py` | 1 full file run | 1 / 1 PASSED | Exit code 0 (`test_target_form.log`) |
| `tests/test_attach_confirm.py` | 1 full file run | 1 / 1 PASSED | Exit code 0 (`test_attach_confirm.log`) |
| `tests/test_attach_confirm_mount.py` | 1 full file run | 1 / 1 PASSED | Exit code 0 (`test_attach_confirm_mount.log`) |
| `tests/test_view_and_quit.py` | 1 full file run | 1 / 1 PASSED | Exit code 0 (`test_view_and_quit.log`) |
| `tests/test_redesign_contrast.py` | 1 full file run | 27 / 27 PASSED | Exit code 0 (`test_redesign_contrast.log`) |
| `tests/test_redesign_focus.py` | 1 full file run | 47 / 47 PASSED | Exit code 0 (`test_redesign_focus.log`) |
| `tests/test_redesign_keys.py` | 1 full file run | 20 / 20 PASSED | Exit code 0 (`test_redesign_keys.log`) |
| `tests/test_redesign_layout.py` | 1 full file run | 60 / 60 PASSED | Exit code 0 (`test_redesign_layout.log`) |
| `tests/test_redesign_lists.py` | 1 full file run | 60 / 60 PASSED | Exit code 0 (`test_redesign_lists.log`) |
| `tests/test_redesign_lists_filter.py` | 1 full file run | 30 / 30 PASSED | Exit code 0 (`test_redesign_lists_filter.log`) |
| `tests/test_redesign_lists_review.py` | 1 full file run | 20 / 20 PASSED | Exit code 0 (`test_redesign_lists_review.log`) |
| `tests/test_redesign_lists_review4.py` | 1 full file run | 7 / 7 PASSED | Exit code 0 (`test_redesign_lists_review4.log`) |
| `tests/test_redesign_new_session.py` | 1 full file run | 42 / 42 PASSED | Exit code 0 (`test_redesign_new_session.log`) |
| `tests/test_redesign_prompts.py` | 1 full file run | 31 / 31 PASSED | Exit code 0 (`test_redesign_prompts.log`) |
| `tests/test_redesign_rail_tint_keys.py` | 1 full file run | 60 / 60 PASSED | Exit code 0 (`test_redesign_rail_tint_keys.log`) |
| `tests/test_redesign_round1.py` | 1 full file run | 14 / 14 PASSED | Exit code 0 (`test_redesign_round1.log`) |
| `tests/test_redesign_round2.py` | 1 full file run | 17 / 17 PASSED | Exit code 0 (`test_redesign_round2.log`) |
| `tests/test_redesign_round3.py` | 1 full file run | 21 / 21 PASSED | Exit code 0 (`test_redesign_round3.log`) |
| `tests/test_redesign_gallery.py` | 1 full file run | 76 / 76 PASSED | Exit code 0 (`test_redesign_gallery.log`) |
| `make lint` | 1 run | Ruff clean (all checks passed); 110 script tests passed; import-lint clean (215 files) | `make lint` exited 0 on code/tests; leak-check git grep scan exited 128 due to sandboxed environment (`make_lint.log`) |


