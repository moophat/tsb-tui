# Fix Report: Three Load-Timed Tests (#119)

**AI-Model:** gemini-3.8-flash (agy)  
**Branch:** fix/119-load-timed-tests  
**Allowed paths:** `tests/test_redesign_lists_filter.py`, `tests/test_redesign_focus.py`, this report.  

---

## 1. Cause Analysis

### Test 1: `tests/test_redesign_lists_filter.py::test_a_stale_page_reply_never_overwrites_a_newer_one`
- **File & Lines:** `tests/test_redesign_lists_filter.py:226, 233, 236, 246-249`
- **Cause:**
  1. In `gated(query)`, the second page request (call index 1) blocked on `assert release.wait(10), "a held page was never released"` inside `served._list_targets`. Because `StubServer.http` executes all requests within `with self.lock:`, Call 1 held `served.lock` while blocked.
  2. When the test called `screen._fetch_targets()` (line 235) to trigger a refresh, the client dispatched a `GET /targets` request that blocked on `served.lock` in `StubServer.http`. Call 2 could never enter `served._list_targets` while Call 1 held `served.lock`.
  3. Line 236 awaited `len(calls) >= 3` with the default 10-second timeout. Because the release event (`gates[1][1].set()`) was only reached at line 245 after line 236 completed, line 236 was in an effective lock-contention race against Call 1's 10-second wait. Under CI load, `wait_until` timed out after 10 s waiting for "the refresh's request", after which Call 1 timed out with "a held page was never released".
  4. Lines 246-249 contained a busy-wait pause loop (`for _ in range(400): await pilot.pause(0.01)`) bounding the final pages completion to 4.0 seconds, which was also prone to timeout under load.
- **Product vs Test:** The cause was test synchronization. The product code in `TerminalScreen._targets_reply` and `TargetsModel.apply` correctly discards older page generations once superseded.

### Test 2: `tests/test_redesign_lists_filter.py::test_loading_title_at_8000_targets`
- **File & Lines:** `tests/test_redesign_lists_filter.py:347, 354, 363-368`
- **Cause:**
  1. `gate.wait(timeout=15)` at line 347 held `served.lock` with a wall-clock timeout of 15 seconds, risking early release under loaded runs before the initial page check completed.
  2. Line 363 waited only for the model state: `len(screen.targets.rows) == 8008 and not screen.targets.next_cursor`. When the 16th and final page arrived, `screen.targets` updated its row count and cleared `next_cursor` synchronously in `_targets_reply`.
  3. However, updating the sidebar widget's display model is scheduled via `self.call_after_refresh(self._flush_fleet_widgets)` on dirty marks. Line 363 immediately succeeded and line 368 read `screen.sidebar.render_line(0).text` before `_flush_fleet_widgets` executed and re-rendered the sidebar. Consequently, `title` still rendered the previous 15th-page state (`Targets · 1/7500…`), failing the assertion for `Targets · 1/8008`.
- **Product vs Test:** The cause was test synchronization. The product asynchronously flushes dirty fleet models after refresh; the test inspected `render_line(0)` before the widget flushed and settled.

### Test 3: `tests/test_redesign_focus.py::test_z_folds_the_other_list_and_z_again_restores[size1]`
- **File & Lines:** `tests/test_redesign_focus.py:44, 49, 75-82, 740, 749, 758`
- **Cause:**
  1. `snap` delegates to `settle(pilot, wait_until, screen, alerts_quiet=True)`. `settle` only verified that the alert row was quiet (`! Alerts 0`); it never checked whether background queries were in flight or whether `_fleet_dirty` flushes were pending before calling `snapshot(app, name)` (`assert path.read_text() == svg`).
  2. Line 740 checked only `len(screen.sidebar.model.targets) > 1`, allowing the folding keys and snapshot checks to begin while targets (`targets_loading`) or sessions (`sessions_loading`) were still loading.
  3. The assertions at lines 749 and 758 (`re.search(r"· \d+", title)` and `"· 1" in line`) accepted loading states (such as `1/8… loading`), allowing snapshots `fold-targets-160x45` and `fold-sessions-160x45` to be taken before the screen fully settled to `▦ Targets · 1/8` and `▸ Sessions · 1`.
- **Product vs Test:** The cause was test synchronization. The folding behavior functions correctly; the snapshot was taken before the screen and sidebar rendered their settled state.

---

## 2. Fix Description

### `tests/test_redesign_lists_filter.py`:
1. In `test_a_stale_page_reply_never_overwrites_a_newer_one`:
   - In `gated(query)`, released `served.lock` during `release.wait(40)` (re-acquiring in `finally`) so concurrent requests in `StubServer.http` proceed without deadlock.
   - Added explicit `timeout_s=40` on `wait_until` calls for the second page's request and the refresh's request.
   - Replaced the 400-iteration pause loop with `await wait_until(pilot, lambda: len(screen.targets.rows) == 608 and not screen.targets.next_cursor, "every page to land", 40)`.
2. In `test_loading_title_at_8000_targets`:
   - In `gated(query)`, released `served.lock` during `gate.wait(timeout=40)`.
   - Set `timeout_s=40` on the initial page wait and final pages wait.
   - Added `await wait_until(pilot, lambda: "Targets · 1/8008" in screen.sidebar.render_line(0).text, "the loaded title", 40)` so the test waits for the screen to settle and render the loaded count before asserting.

### `tests/test_redesign_focus.py`:
1. In `settle`:
   - Added a wait on `not screen._fleet_dirty` (`await wait_until(pilot, lambda: not screen._fleet_dirty, "fleet widgets to flush")`) and `await pilot.pause()` to ensure pending widget flushes and redraws complete before taking snapshots.
2. In `test_z_folds_the_other_list_and_z_again_restores`:
   - Changed the initial synchronization wait from `> 1` targets to `len(screen.sidebar.model.targets) == 8 and not screen.sidebar.model.targets_loading and not screen.sidebar.model.sessions_loading` with `timeout_s=40`.
   - Added deterministic waits for the settled folded line text before `snap`:
     `await wait_until(pilot, lambda: "▦ Targets · 1/8" in sidebar.render_line(0).text, "folded targets title", 40)` and
     `await wait_until(pilot, lambda: "▸ Sessions · 1" in sidebar.render_line(height - 1).text, "folded sessions line", 40)`.

---

## 3. Test Runs and Exit Codes

### Command Line
`env -u NO_COLOR TSB_TUI_TERM_LIBRARY=$PWD/vendor/libghostty-vt/libghostty-vt.so $(PYTHON) -m pytest -q -p no:cacheprovider <test_file>`

### Red Runs (Before Fix)
- `tests/test_redesign_lists_filter.py`: exit code `0` (30 passed in 121.68s; CI load flake did not reproduce locally).
- `tests/test_redesign_focus.py`: exit code `0` (41 passed in 188.08s; CI load flake did not reproduce locally).

### Green Runs (After Fix, 3 Full-File Runs Each)
- `tests/test_redesign_lists_filter.py`:
  - Run 1: exit code `0` (30 passed in 150.15s)
  - Run 2: exit code `0` (30 passed in 148.25s)
  - Run 3: exit code `0` (30 passed in 149.82s)
- `tests/test_redesign_focus.py`:
  - Run 1: exit code `0` (41 passed in 189.81s)
  - Run 2: exit code `0` (41 passed in 190.79s)
  - Run 3: exit code `0` (41 passed in 192.82s)

### Negative Mutation Checks

#### Mutation 1 (`test_a_stale_page_reply_never_overwrites_a_newer_one`):
- Mutation: In `_targets_reply` (`tsb_tui/screens/terminal.py`) and `apply` (`tsb_tui/model/targets.py`), bypassed generation checks to unconditionally append rows from the stale second-page reply.
- Mutated run: exit code `1` (failed at `wait_until("every page to land", 40)` because row count was 716 instead of 608 in 41.67s).
- Reverted run: exit code `0` (1 passed in 1.87s).

#### Mutation 2 (`test_loading_title_at_8000_targets`):
- Mutation: In `title_parts` (`tsb_tui/widgets/sidebar.py:318`), omitted the loading ellipsis (`parts = [core]`).
- Mutated run: exit code `1` (`AssertionError: ┏━ ▦ Targets · 1/500 ━━━━━━┓ assert 'Targets · 1/500…' in '┏━ ▦ Targets · 1/500 ━━━━━━┓'` in 41.23s).
- Reverted run: exit code `0` (1 passed in 2.85s).

#### Mutation 3 (`test_z_folds_the_other_list_and_z_again_restores`):
- Mutation: In `toggle_fold` (`tsb_tui/widgets/sidebar.py:1779`), omitted clearing `self.folded` on the second `z` press (fold not restoring).
- Mutated run: exit code `1` (`AssertionError: assert (1, 43) == (41, 3)` in 8.55s).
- Reverted run: exit code `0` (2 passed in 9.18s).

### Linter Check
- Command: `$(PYTHON) -m ruff check --no-cache tsb_tui tests`
- Output: `All checks passed!`
- Exit code: `0`

---

## 4. Anything Not Verified

- Only the target test files `tests/test_redesign_lists_filter.py` and `tests/test_redesign_focus.py`, along with `ruff check --no-cache tsb_tui tests`, were executed; the entire repository test suite was not run, per the lane brief.
