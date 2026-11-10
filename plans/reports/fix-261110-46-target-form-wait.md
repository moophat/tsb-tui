# Fix #46 — target form waits for the worker that pushes it

**Date:** 2026-11-10
**Branch:** test/target-form-waits-for-worker (from main 63b3ef4)
**Model:** opencode/deepseek-v4.1-flash-free
**Scope:** `tests/test_target_form.py` only; no `tsb_tui/` change.

## Cause

Pressing `e` calls `TerminalScreen._target_write_command` (`tsb_tui/screens/terminal.py`), which calls
`app.call_api(...)`. That helper lands in `App.call_api` → `App._run_blocking`, a
`@work(thread=True, exclusive=False)` method on the App. The thread reads
`GET /targets/<id>/definition` and, via `call_from_thread`, pushes `TargetFormScreen` from
`on_done` back on the UI thread. On a loaded runner the worker is starved, so the form appears only
after the 60 s `wait_until` bound — the test timed out waiting on the effect, never synchronising with
the cause.

`n` pushes the form directly (no worker). `d` pushes the confirmation dialog directly; its delete
worker starts only on confirm and is already covered by the `wait_until` on `server.targets` and the
row's removal. Only `e` starts the read worker.

## Fix

After each `e` that awaits the edit form, wait for the App's workers, then one `pilot.pause()` so the
queued `on_done` runs, then the existing `wait_until` (unchanged, still the 60 s bound):

```python
await pilot.press("e")
await app.workers.wait_for_complete()
await pilot.pause()
await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), ..., timeout_s=TARGET_FORM_WAIT_S)
```

Applied in `test_create_edit_and_delete_with_confirmation` (the flaking test) and
`test_saved_target_is_selected_after_it_leaves_the_query`, which presses the same key through the same
helper and carries the same flake class. `wait_for_complete` does cover this worker: `_run_blocking`
registers on `app.workers`, and the thread worker only completes after `call_from_thread` has run
`on_done`.

## Verification

- 10 consecutive runs of the file (no load generators or stress tools):
  `for i in $(seq 10); do ... pytest tests/test_target_form.py -p no:cacheprovider -q || break; done`
  — 12 passed each run, all 10 runs green, `ALL 10 RUNS PASSED`.
- `make test PYTHON=<the test venv's python>` — `1901 passed in 1869.63s`, exit 0
  (`contract-check: program/ is clean at ad31180f02b4, v0.14.1`).
- `make lint PYTHON=<the test venv's python>` — `All checks passed!`, 66 passed, exit 0.
- Scratch logs under `logs/` (`run-target-form-10x.sh`, `verify-bg.sh`, `verify.log`). Nothing
  committed or pushed.

## Notes

The 10 runs were on an unloaded machine, as the brief required no stress tools; they show the wait is
sufficient and introduces no new failure, while the causal synchronisation is what removes the
starvation window. The two `e` sites are fixed; no other key in this file starts the read worker.

Status: DONE
