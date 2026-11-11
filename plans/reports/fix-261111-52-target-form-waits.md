# Fix #52 — the target-form tests wait for the worker that carries the refresh

**Date:** 2026-11-11
**Branch:** test/target-form-waits (agy Gemini's uncommitted diff, reviewed and kept as-is)
**Model:** strata
**Scope:** `tests/test_target_form.py` only; no `tsb_tui/`, `tests/conftest.py`, product change.

## Step 1 — what the selection follows after a save

The chain, cited:

1. Save button: `TargetFormScreen.on_button_pressed` (`tsb_tui/screens/target_form.py:156-159`) runs
   `self.run_worker(self._submit())`; `_submit` -> `save()` (target_form.py:126-148) does the blocking
   `client.create_target` / `replace_target` via `asyncio.to_thread`, then `self.dismiss(record)`.
2. The dismissal fires the `push_screen` callback `TerminalScreen.target_saved`
   (`tsb_tui/screens/terminal.py:1291-1300`), a message handled on the app's loop: it sets
   `self._target_select = record["id"]`, clears the query, and calls `self._fetch_targets(query=Query())`.
3. `_fetch_targets` (terminal.py:1107-1130) submits `client.list_targets` through `app.call_api` ->
   `App._run_blocking` (`tsb_tui/app.py:570-578`), a `@work(thread=True)` worker registered on
   `app.workers`; its body calls `self.call_from_thread(on_done, result)` with
   `on_done = _targets_reply`.
4. `_targets_reply` (terminal.py:1132-1147) is the step that moves the selection: when the fetched page
   contains the saved row it sets `fleet_view.selected = (GROUP_TARGETS, self._target_select)`;
   `FleetView.selected_session` (`tsb_tui/screens/fleet_view.py:638`) reads that tuple's id directly,
   no render needed. If the row is not on the first page and a `next_cursor` exists, it starts a
   follow-up `more=True` fetch (terminal.py:1145-1146) — another worker in the same chain.

So the selection is set inside `_targets_reply`, the `on_done` of the refresh worker started by
`target_saved`; no second worker and no later app-loop message sets it after that.

## Step 2 — the judged change (kept, unmodified)

`wait_for_target_workers` now drains the chain: pause, and while `app.workers` is non-empty wait for
completion (bounded by the remaining 60 s deadline), pause, repeat; break when no workers remain, then
each test asserts the selection once.

Why the exit guarantees the assertion is safe, in Textual 8.2.8:

- `app.workers` is a `WorkerManager` (`textual/worker_manager.py`); a finished worker is removed from
  its set via `_remove_worker` (worker_manager.py:79, 121-127), so `bool(app.workers)` is false only
  when every worker has finished.
- `App.call_from_thread` (`textual/app.py:1788-1836`) schedules the callback on the app's loop with
  `asyncio.run_coroutine_threadsafe` and blocks the worker thread on `future.result()`: `_run_blocking`
  returns, and is removed from `app.workers`, only after `_targets_reply` has run on the UI loop. So an
  empty `app.workers` means the selection-setting callback has completed — the single assert cannot see
  a pending posted message.
- The loop cannot spin to the deadline: each `wait_for_complete` is bounded by the remaining time and
  raises the named `AssertionError` on timeout; when no workers remain it breaks at once; `pilot.pause()`
  yields time slices (waits for idle) and processes queued messages, so between checks the save press
  starts `_submit`, the dismissal runs `target_saved`, and the refresh worker registers. The first
  `pause()` processes the pressed button, so the form worker is present at the first check.

What each test now awaits: the drain of every target-write worker — the form's `_submit`, then the
`_run_blocking` refresh whose `on_done` (`_targets_reply`) is exactly the step that moves the selection
after a save. The two changed assertions ("created row", "new row") run once, after that step; the
60 s bound is unchanged, no assertion is retried, nothing skipped. The remaining `wait_until` calls
(edit form, edited row, filtered rows, confirmations) are BRIEF.md's untouched sites and stay as they
were.

## Verification

- 5 consecutive runs, the Makefile way (`env -u NO_COLOR <the test venv's python> -m pytest
  tests/test_target_form.py -p no:cacheprovider`): 12 passed each run (19.94, 19.83, 20.22, 19.38,
  19.85 s), all green. No load generator on this machine.
- `make lint PYTHON=<the test venv's python>` — exit 0: 66 script tests passed, import-lint clean
  (180 files), leak check clean, engine check verified the vendored object and all headers. Ruff
  reported SKIP: not installed in the test venv, so the Python style check did not run — say so.
- `git diff` shows only `tests/test_target_form.py`; no commit, no push; nothing absolute-pathed.

Status: DONE
