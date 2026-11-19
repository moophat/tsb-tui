# Build report: drive answers login_status during the Login swap (tsb-core #733)

Model: claude-sonnet-5-5. Branch fix/drive-login-status-during-swap. Not committed.

## Cause (proven)
Not the attached session's own feed: a pump drains its own queue before it applies a 4017 (`_drain`), so its
own `shutdown_feed` finds an idle thread. The block comes from the OTHER slots. The first 4017 runs
`account_login_ended` -> `notify_api_error` -> `_to_login`, which pops the workspace; `on_unmount` runs
`_release_slot` for every slot, and `shutdown_feed` called `ThreadPoolExecutor.shutdown(wait=True)` on the event
loop. A second attached session whose feed thread is mid-slice holds the whole loop (and every drive command,
`login_status` included) until that slice ends. A drive with no attached session has no pump, so it answers.

Evidence: new test `test_login_status_answers_while_the_feed_thread_is_mid_slice_at_close_4017` (two attached
sessions, the second's sink held 1.5 s). On main: `login_status took 1.462s, budget 0.4s` (FAILED). A single-session
variant I tried first passed on main (0.04-0.10 s), which is how the other-slot path was isolated.

## Fix
- `tsb_tui/model/attachment.py`: `shutdown_feed()` no longer waits (flags, STOP, `executor.shutdown(wait=False)`).
  New `feed_running()`, `feed_stopped()` (join via `asyncio.to_thread`), `join_feed()` (blocking, for no loop),
  `_run_sink` (marks a slice in flight). `stop()` awaits `feed_stopped()` instead of blocking.
- `tsb_tui/screens/terminal.py`: `_release_slot` frees the canvas through new `_free_canvas`: at once when no slice
  runs, else after `feed_stopped()` in a task (`self._freeing`). DT20 order kept: feed thread ended, then engine freed.
  Slot bookkeeping (closed flag, reply callback cleared, tap) stays synchronous.
- `tests/test_drive_login_status.py`: the new test; it also asserts the engine is not freed during the slice and is
  freed after it.

## Numbers
Red on main 1.462 s (budget 0.4 s). Green after: 10 of 10 runs passed (each ~2.2 s total).

## Commands (exit codes)
- new test x10: all 0
- touched files (login_status, model_attachment, attach_and_close, drive_accounts, ended_and_reattach, reattach_login): 0, 194 passed
- make lint: 0
- make contract-check: 0
- make test (the repo's test venv, NO_COLOR unset): 0, 2356 passed, 3 skipped (installer tests, CI image only), 41 min

## Not verified
- Not run against the lab (real 4017 from an account act with real output load); the cause is proven in-process only.
- The deferred free relies on the loop still running at teardown; if the app exits first, the engine is not freed
  by that task (process exit frees it). A no-loop caller falls back to the blocking join.
- Whether the lab drive really had a second attached session is inferred from the evidence, not seen.

## Unresolved
- None blocking.
