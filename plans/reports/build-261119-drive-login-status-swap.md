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

## Round 1 (tsb-tui #103): a cancelled deferred free never frees the grid before its feed thread ended

Model: strata (swift-1.5). Not committed.

### Finding
`_free_canvas`'s deferred `free()` awaited `pump.feed_stopped()` inside a `try/finally` that ran `canvas.close()`.
When application shutdown cancels the pending task while it waits on the join, the `finally` closes the grid while
the feed thread is still inside a slice (closing can also block the loop on the backend lock). DT20: the feed thread
is stopped before the engine is freed, never after.

### Fix (ruled)
`tsb_tui/screens/terminal.py`: shield the join (`await asyncio.shield(pump.feed_stopped())`) and close the canvas
only after the join returned. A cancellation of the task raises `CancelledError` at the shielded await, so the grid
is never closed there (the process exit releases it) and the cancellation propagates.

### Test
`tests/test_drive_login_status.py`: new `test_cancelled_deferred_free_never_closes_the_canvas_before_the_feed_thread_ends`
— two attached sessions, the second's sink held 3.0 s; login_ended arrives through the free slot, sign-out starts the
deferred free for the held slot, the pending task is cancelled while its feed thread is mid-slice, and every canvas
close is recorded with `feed_running()` at that moment. No close may happen while a slice runs, and the cancelled task
leaves the canvas never closed here.

Red before the fix: `assert not any(close_log)` -> `AssertionError: the canvas was closed while the feed thread was
still running (True = any([True]))`. The first single-session draft could not reach this: a lone slot's own feed
thread is busy with the slice, so the 4017 close (and login_ended) is blocked until the slice ends; the two-session
shape (the free slot carries login_ended while the held slot stays mid-slice) is what reaches the cancelled join.

### Numbers (exit codes)
- `tests/test_drive_login_status.py` (python from the repo's test venv, NO_COLOR unset, PYTHONPATH = this clone's
  `program/contract/client` and root): 0, 13 passed (~17 s).
- `make lint`: 0.

## Round 2: why a later attach saw a stale first client

Cause (evidence): the failing run's second drive showed the status row "typing takes it ... Take back", i.e.
another client still held the typist role, so the "Who's here" control was not on screen and `click` answered
`bad_request: nothing on screen is labelled`. The first drive's attachment had never been detached. Round 1 changed
`AttachmentPump.stop()` to `await self.feed_stopped()` BEFORE closing the socket. When the first Rig exits the
app unmounts and cancels the worker running `stop()`; the cancel now landed on that await (a real suspension point,
where the old blocking join was not) and the `att.close()` after it never ran. The session kept counting the first
client as attached. The deferred free itself (`_free_canvas`) was not the cause; the Menu/picker timeout is the same
class (a stale attachment left by an earlier app), and that test is green after the fix.

Fix: `stop()` now calls `executor.shutdown(wait=False)`, closes the attachment, and only then awaits `feed_stopped()`.
A cancel during the join can no longer skip the close. Stop-before-free (DT20) is unchanged: the engine is freed
only by the slot's release, which waits for the feed thread itself (the shielded join in `_free_canvas`).

Numbers: click test failed 2 of 6 runs before the fix here (5 of 9 per the coordinator), 10 of 10 green after.
Picker test (`test_the_leader_and_T_open_the_picker_and_the_session_stays_visible_beside_it`) 10 of 10 green.
login_status file 3 of 3 green (13 tests each, including the mid-slice test). Touched files (login_status,
drive_commands, theme_picker, discoverability, model_attachment, attach_and_close, drive_accounts,
ended_and_reattach, reattach_login): exit 0, 296 passed. `make lint`: exit 0.

Not verified: full `make test` was not re-run in this round; CI's discoverability failure was not reproduced
locally, only the picker test was run 10 times.
