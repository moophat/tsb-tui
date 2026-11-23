# Fix Report: Targets-Tab Flake (#116)

**AI-Model:** gemini-3.8-flash (agy)  
**Branch:** fix/116-targets-tab-flake  
**Allowed paths:** `tests/test_targets_tab.py`, this report.  

---

## 1. Cause Analysis

### The Path That Issues the Extra Unpaged Request
- **File & Lines:** `tsb_tui/screens/terminal.py:1434-1436` and `tsb_tui/screens/terminal.py:1438-1444`
  When the Targets tab is opened via `screen.open_fleet_view(TAB_TARGETS)`, `_targets_tab_changed()` schedules a recurring poll timer:
  ```python
  interval = self.app.cfg["fleet_view.refresh_s"]
  if interval > 0:
      self._targets_timer = self.set_interval(interval, self._targets_tick)
  ```
  Default `fleet_view.refresh_s` is 2 seconds.
- Every 2 seconds, `_targets_tick()` executes and calls:
  `self._fetch_targets(background=True)` (`tsb_tui/screens/terminal.py:1271-1329`).
- In `_fetch_targets(background=True)` (`tsb_tui/screens/terminal.py:1298-1302`), when no pass is currently staged and no foreground page is recorded as in flight, it creates a new `TargetsModel()` staging pass and sends an unpaged request: `GET /targets?limit=500` (`limit=PAGE_LIMIT, cursor=None`).
- When that reply lands, `_targets_reply` (`tsb_tui/screens/terminal.py:1344`) chains the next staging page:
  `self._fetch_targets(more=True, background=True)`, which sends `GET /targets?limit=500&cursor=bulk-498`.

### Why the Flake Occurred
In `tests/test_targets_tab.py:517` (`test_a_click_on_the_last_loaded_target_selects_it_while_pages_arrive`), unlike its sibling tests in the same file:
1. `screen._stop_targets_timer()` was never called after opening the Targets tab. The background poll timer continued ticking every 2 seconds.
2. The test did not wait deterministically for the foreground chain's cursor page to be in flight (`await wait_until(pilot, lambda: asked and asked[-1].get("cursor"), "the chained page's request", 40)` as sibling `test_a_poll_never_folds_the_loaded_pages_back:498` does).
3. In multi-test full-file runs or under load, the 2-second background timer fired and issued an unpaged request `{limit: 500}` and its cursor continuation `{limit: 500, cursor: 'bulk-498'}` inside the test window, resulting in 5 requests:
   `[{'limit': ['500']}, {'limit': ['500']}, {'limit': ['500'], 'cursor': ['bulk-498']}, {'limit': ['500']}, {'limit': ['500'], 'cursor': ['bulk-498']}]`.
4. As a result, `assert len(asked) == before` failed with `assert 5 == 4`.

### Verification of Product Behavior
The cause is **not** product behavior:
- On click, `FleetView.on_click` (`tsb_tui/screens/fleet_view.py:989`) calls `self._ask_more_targets()`.
- `_ask_more_targets()` (`tsb_tui/screens/fleet_view.py:867`) posts `CMD_MORE_TARGETS`.
- `TerminalScreen` handles `CMD_MORE_TARGETS` (`tsb_tui/screens/terminal.py:1537`) by calling `self._fetch_targets(more=True)`.
- In `_fetch_targets(more=True)` (`tsb_tui/screens/terminal.py:1305`):
  `if model.next_cursor is None or self._targets_page is not None: return`.
  Because the background chain already has a page in flight (`self._targets_page is not None`), `_fetch_targets(more=True)` immediately returns without issuing any request on the wire.
- Moreover, if a click had asked for a page, it would issue a paged cursor request (`more=True`), not the unpaged poll request `{limit: 500}` observed in the failure.

---

## 2. Fix Description

Modified `tests/test_targets_tab.py`:
1. In `test_a_click_on_the_last_loaded_target_selects_it_while_pages_arrive`:
   - Added `screen._stop_targets_timer()` immediately after `_open_targets` to disarm the unmanaged background poll timer.
   - Added `await wait_until(pilot, lambda: asked and asked[-1].get("cursor"), "the chained page's request", 40)` to establish a deterministic starting point with the chained cursor page confirmed in flight before measuring `before = len(asked)`.
2. Sibling test `test_a_poll_asks_limit_and_cursor_only_and_a_503_shows_the_state`:
   - Although it called `screen._stop_targets_timer()`, it lacked the deterministic synchronization on the chained page's request, causing it to fail in red runs 3 and 5 for the same reason (`assert 5 == 4`).
   - Added `await wait_until(pilot, lambda: asked and asked[-1].get("cursor"), "the chained page's request", 40)` matching `test_a_poll_never_folds_the_loaded_pages_back`.

---

## 3. Negative Mutation Check

To prove the test is not weakened and still fails if a click asks for a page:
- Mutated the click handler in `FleetView.on_click` (`tsb_tui/screens/fleet_view.py:989`) to call the pager (`self.screen.app.client.list_targets(limit=500)`).
- Ran `test_a_click_on_the_last_loaded_target_selects_it_while_pages_arrive`:
  - Failed deterministically:
    ```text
    AssertionError: a click asked for a page: the chain is the only pager
    assert 6 == 5
    where 6 = len([{'limit': ['500']}, {'limit': ['500']}, {'limit': ['500'], 'cursor': ['bulk-498']}, {'limit': ['500']}, {'limit': ['500'], 'cursor': ['bulk-498']}, {'limit': ['500']}])
    ```
  - Exit code: 1.
- Reverted the mutation (`git checkout tsb_tui/screens/fleet_view.py`).
- Ran `test_a_click_on_the_last_loaded_target_selects_it_while_pages_arrive` without the mutation:
  - Passed cleanly (1 passed in 11.85s).
  - Exit code: 0.

---

## 4. Test Runs and Exit Codes

### Command Line
`env -u NO_COLOR TSB_TUI_TERM_LIBRARY=$PWD/vendor/libghostty-vt/libghostty-vt.so $(PYTHON) -m pytest -q -p no:cacheprovider tests/test_targets_tab.py`

### Red Runs (Before Fix, 5 Full-File Runs)
- **red-1:** exit code `0` (15 passed in 75.28s)
- **red-2:** exit code `0` (15 passed in 75.26s)
- **red-3:** exit code `1` (1 failed, 14 passed in 85.09s — `test_a_poll_asks_limit_and_cursor_only_and_a_503_shows_the_state` failed at line 386 with `assert 5 == 4`)
- **red-4:** exit code `0` (15 passed in 75.46s)
- **red-5:** exit code `1` (2 failed, 13 passed in 95.08s — `test_a_poll_asks_limit_and_cursor_only_and_a_503_shows_the_state` failed at line 386 with `assert 5 == 4`, and `test_a_click_on_the_last_loaded_target_selects_it_while_pages_arrive` failed at line 534 with `assert 5 == 4`)

### Green Runs (After Fix, 3 Full-File Runs)
- **green-1:** exit code `0` (15 passed in 75.29s)
- **green-2:** exit code `0` (15 passed in 74.96s)
- **green-3:** exit code `0` (15 passed in 75.24s)

### Mutation Check
- **mutated:** exit code `1` (1 failed: `AssertionError: a click asked for a page: the chain is the only pager`, `assert 6 == 5` in 22.33s)
- **reverted:** exit code `0` (1 passed in 11.85s)

### Linter Check
- Command: `$(PYTHON) -m ruff check --no-cache tsb_tui tests`
- Output: `All checks passed!`
- Exit code: `0`

---

## 5. Anything Not Verified

- The full repository test suite was not run; only the target file `tests/test_targets_tab.py` and `ruff check --no-cache tsb_tui tests` were run, as specified by the brief.
