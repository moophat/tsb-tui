# Builder report: timing flakes in tsb-tui tests

Model: claude-sonnet-5-5. Branch `fix/timing-flakes`. Test-only change; no product code touched.

## Per test

Four named flakes. All four failed because the wait read a weaker state than the assertion.

| Test | Waited on | Asserted | Change |
|---|---|---|---|
| `tests/test_target_form.py` `test_a_saved_target_that_leaves_the_filter_moves_the_selection_by_rule_6` | `layout.rows == ["two-client"]` | `view.selected_session == "two-client"` | the wait predicate also requires the selection |
| `tests/test_feed_thread.py` `test_order_is_kept_across_a_size_frame_and_a_prefix_with_its_dump` | dump text in the grid | the full snapshot, cursor included, equals the raw replay | `expected_after` computed before the wait; a second bounded `wait_until` on `_snapshot(backend) == expected_after`; its timeout is suppressed so the unchanged assertion reports the diff |
| `tests/test_redesign_new_session.py` `test_the_credential_choice_takes_focus_and_a_key_picks_a_candidate` | `#credential-choice` displayed | focus is `credential-choice` | a `wait_until` on the focused id before the assertion |
| `tests/test_drive_login_status.py` `..._mid_slice_at_close_4017` | the command answer | answered under a 0.4 s budget | slice held 8 s (was 1.5 s), budget 4.0 s (was 0.4 s), so a command loop that waits on the slice times out at the command's 5 s timeout; the assertions that the engine is not freed during the slice and is freed after are unchanged |

A fifth test of the same pattern failed in my first full-file run (found by this work, not in #130 or the profile):
`tests/test_redesign_new_session.py::test_r_after_a_connect_refusal_repeats_the_create` (line 767): waits for the refusal line, asserts focus on `create-button`. Fixed the same way (wait on the focused id). Not in the profile; flagged for the operator.

Budget tests (the profile's six). Where no plan, doc or contract text states the number, the budget is test slack:

| Test | Change |
|---|---|
| `tests/test_no_join_on_loop.py` stop under 0.2 s (alert watcher) | causal check added: at return the watcher thread is alive and the release not set; bound 5 s (a stop that joined waits out the gate's 10 s hold) |
| `tests/test_no_join_on_loop.py` answers under 1 s (three places) | command timeout 5 s and bound 5 s; the account-act test also asserts the answer came while the held poll was still held; docstrings updated |
| `tests/test_drive_waits.py` `elapsed_ms < 1000` (wait that starts after the end) | `< 10000`, half of the wait's own 20 s timeout; still tells "at once" from "timed out" |
| `tests/test_redesign_lists_filter.py` key on 8,000 rows under 1.0 s | 3.0 s; the bound includes the test's own `pilot.pause(0.2)` |
| `tests/test_drive_prompt_matcher.py` NFA on 1,000 chars under 1.0 s | 10.0 s; linear takes ms, exponential does not finish |

## Stopped

- `tests/test_update_check.py:189` (`test_slow_request_does_not_block_login_and_worker_is_bounded`, bound 2.5 s). `docs/using-tsb-tui.md:505` states the update check "waits at most 1.5 seconds", so the 2.5 s bound is that stated time plus 1 s of slack. Changed nothing. Options: (a) raise 2.5 to 4.5 s, still under the test server's 5 s delay, so it still proves the worker is bounded by its own timeout and not by the server; (b) leave as is. Recommend (a), the operator rules because the doc states the time.

## Before and after

Method: the 10 touched tests (8 test ids; the whole `test_no_join_on_loop.py` file counts 3 tests) in one pytest process, looped 10 times, with two more copies looping in parallel (3 copies x 10 loops = 30 runs), all under `capped --`, on an 8-core shared machine. The same script, stashed changes for "before".

- Before (HEAD): 30 of 30 runs passed, 0 failures.
- After: 30 of 30 runs passed, 0 failures.

The flakes did not reproduce locally under this load, before or after. They are fixed by the wait change (each wait now reads what the assertion reads), not shown fixed by a red-to-green run. The fifth flake (line 767) did reproduce once in the full-file run, in the first pass; after the fix the file passed 42 of 42.

## Commands (exit codes)

- before stress loop, 30 runs: all 0
- after stress loop, 30 runs: all 0
- touched files once (target_form, drive_login_status, feed_thread, redesign_new_session, no_join_on_loop, drive_waits, redesign_lists_filter, drive_prompt_matcher): exit 1, 1 failed, 236 passed: the failure was `test_r_after_a_connect_refusal_repeats_the_create` above, fixed afterwards
- `tests/test_redesign_new_session.py` again: exit 0, 42 passed
- `make lint` (with `PYTHON` set to the shared test venv): exit 0 after the final edit; also 0 on the first pass. The setuptools packaging failure did not occur here.

## Unverified

- No run of the flakes red on a loaded CI runner; the proof is the mechanism. Local load did not reproduce them.
- After the last edit, the seven other touched files were not re-run as whole files (only the 30-run stress of their target tests, taken before the line-767 edit, and the one whole-file run). The line-767 edit touched only `test_redesign_new_session.py`, which was re-run whole.
- The chosen bounds (4 s, 5 s, 10 s, 3 s) are judgement for slack; no document states them. The 8,000-row bound (3 s) trades some regression sensitivity; I did not measure how long the old per-key walk took.
- A similar pattern remains at `tests/test_redesign_new_session.py:878` (a `pilot.pause()` then a focus assertion). It did not fail; left alone as outside the list.
- Not run: the full suite.

Status: DONE_WITH_CONCERNS
Summary: Four named flakes plus a fifth of the same pattern are fixed by waiting on the asserted state, and five slack budgets loosened; update-check's 2.5 s test is left for a ruling because the guide states 1.5 s.

## Round 2: deterministic signals instead of loosened bounds

Each loosened budget now has a signal of the thing it guards; the wall bound stays only as a net. Product code was not
changed: the regressions below were reintroduced temporarily and reverted, and `git status` shows only test files.

1. `tests/test_no_join_on_loop.py` (alert stop, account-ended swap): `_record_joins` wraps the worker thread's `join` and records
   every call and whether it came from the event loop's thread. Both tests assert no join happened before the operation
   returned. The held-thread ordering check stays. The poll-unmount test already instrumented `join`.
   Red: `stop()` of the alert watcher and of the fleet poller each given a `join(0.5)`: all three tests fail
   ("the stop joined the watcher thread (1 time(s) on the loop)", "the login swap joined the poller thread", "poller thread was
   joined during unmount"). A 0.5 s join would have passed the earlier 5 s bound.
2. `tests/test_drive_waits.py` (wait on an ended session): records the `_Pending` objects `Driver._await` is given and the
   `Wait.check` calls. Asserts one pending whose `timeout` timer was never armed (the answer came on the first evaluation) and one
   check. The 10 s bound stays as a net. Red: `_await` evaluating on the next loop tick instead of at once (answer still
   `ended`, milliseconds later) fails with "the wait armed its timeout before the answer"; a wall bound of 1 s or 10 s passes it.
3. `tests/test_redesign_lists_filter.py` (8,000 rows, `End`): counts calls of `Sidebar._target_row_lines` and `_target_facts`
   during the key. Current code does 78; asserts under 1,001 (8,008 // 8). Wall bound 10 s as a net. Red: the spans cache disabled
   (`if False and cache ...` in `_target_spans`): 5,797,133 calls, the assertion fails.
   The first version counted 16,175 once under load: a poll or late page rebuilds the spans whatever key is pressed. The test now
   stops the poller and waits for the row work to stand still before it counts, so only the key's work is counted.

Runs: the three tests (the whole no-join file, the one wait test, the 8,000-row test) looped 10 times with three copies in parallel
under `capped --`: 30 of 30 passed (after the settle fix); the 8,000-row test alone, 12 loops x 3 copies: 36 of 36. Whole files
(no_join_on_loop, drive_waits, redesign_lists_filter): exit 0, 81 passed. `make lint`: exit 0.

Unverified in round 2: the bound of 1,001 row computations is a margin (78 measured, 8,000 or more for one walk). I saw the late-rebuild
case once and attributed it to the poller or a late page from its size (about two walks), not proven; the settle wait covers any source
that finishes before the key. No CI-runner run.

Status: DONE_WITH_CONCERNS
Summary: The three loosened budgets now assert a deterministic signal (no join, first-evaluation answer, row-work count), each shown red on a reintroduced regression; the update-check ruling is still open.

## Round 3: the row-work count attributable to the key alone

Finding: in `test_scale_8000_targets_keys_and_position`, stopping the poller and seeing the row work stand still does not prove no
rebuild is coming; a poll reply already in flight can land after the counter reset and its rebuild was counted as the key's.

Change (test only, no product change): the counters count only while the sidebar still holds the model object it held when the key was
pressed. A reply replaces the model (`set_model`), so its rebuild, which follows the model swap, is not counted; the key's own work runs on the
unchanged model and is. The poller stop and the settle loop are gone. Before the capture, the spans are built for the current model and one
paint is let through (no await between that and the capture), so a rebuild owed to an earlier reply cannot land inside the key's window on the
baseline model. The test also forces a late reply on every run: after the key it calls `set_model(copy.copy(model))` and pauses, so the
attribution is exercised each time, not only when a runner happens to be slow. The other options (a flag around the key handler, stubbing the
reply path) were not used: the spans are built lazily in the paint, after the handler returns, and the in-flight reply is a bound method already
held by the poller thread.

Red, then green:
- Before: the round 2 version with the late reply forced after the key: "a key did 16214 row computations over 8,008 rows" (fails).
- After: passes with the same forced reply.
- Mutation kept: the spans cache disabled in `_target_spans` (temporary, reverted): "a key did 1943096 row computations", still fails.

Runs: the scale test, 12 loops x 3 parallel copies under `capped --`: 36 of 36 passed. Whole `tests/test_redesign_lists_filter.py`: exit 0, 30 passed. `make lint`: exit 0.

Unverified in round 3: a regression that rebuilds only after a model swap (and never on an unchanged model) would not be counted; the test
guards the per-key rebuild on an unchanged model, which is the regression the 8,000-row rule describes. No CI-runner run.

Status: DONE_WITH_CONCERNS
Summary: The key's row work is now counted only on the model it was pressed against, so a late poll reply (forced in every run) cannot be mistaken for it; shown red before, green after, and still red on a disabled cache.

## Round 4: no poller in the 8,000-row test

Finding: the model-identity filter of round 3 excludes the key's work if an in-flight poll replaces the model before the key handler
runs, so a full-list walk could pass. Attributing around a live poller keeps leaking.

Change (test only): the poller is removed from the test. `FleetPoller.start` and `FleetPoller.refresh_async` are replaced from the start
(monkeypatch, before the app is created) by stubs that start no thread and record each ask, so no poll reply can ever be in flight. The targets load
through their own paged read (`_fetch_targets`), not the poller, so the test still builds its 8,008 rows itself. The model-identity filter and the forced
late reply are gone: every row computation counted while the key is handled is the key's. After the key the test asserts the signals that no poll ran:
`poller.calls == 0` and no ask was recorded during the key (opening the sidebar asks for a read once, before the key; the stub drops it, and the test
records it as the baseline).

The poller was not the only live source. Removing it alone still failed under load (the first loop: 7 of 12 in one copy, 16,214 and 48,486 row
computations). A stack taken at the 2,000th counted call showed `_flush_fleet_widgets` -> `_sync_fleet_widgets` -> `sidebar.set_model` -> spans rebuilt,
and a recorder on `_mark_fleet_dirty` showed who marked it during the key: the app's alert stream (`_apply_alerts` -> `alerts_changed` ->
`TerminalScreen._alerts_changed`), three events per failing run. So the test also replaces `TsbTuiApp.watch_alerts` with a stub that registers no
listener (asserted: `app._alert_listeners` is empty after the key), and before the key it waits until the screen has no flush pending (`_fleet_dirty` and
`_fleet_flush` both false), builds the spans once and lets the queued paint use them, so the model's first build is not counted either.

No product change was needed; the app starts without its live polling and alert listening through the stubs.

Red, then green:
- The spans cache disabled in `_target_spans` (temporary, reverted): "a key did 1,943,096 row computations", fails.
- Under five parallel copies (12 loops each) the poller-only version failed 6 of 60 (the alert events above); the final version passed 60 of 60.
- Without the baseline of the open-time ask, the first version of the poll assertion failed on `['start', 'refresh_async']`; it now compares against the asks recorded before the key.

Runs: the scale test, 12 loops x 5 parallel copies under `capped --`: 60 of 60 passed. Whole `tests/test_redesign_lists_filter.py`: exit 0, 30 passed. `make lint`: exit 0.

Unverified in round 4: other sources of a model swap during the key that this machine did not show (the stack and mark recorder found the alert stream and nothing else
in 60 runs); the flush wait covers any source that lands before the key. No CI-runner run.

Status: DONE_WITH_CONCERNS
Summary: The 8,000-row test now runs with the poller replaced by a recording stub that never starts, so no reply can rebuild the spans during the key, and counts all row work in it; a disabled cache still fails it.

## Round 5: two CodeRabbit threads

### Thread 1, `tests/test_drive_login_status.py` (Minor): valid, fixed

Check: the slice is held `hold` = 8 s from its own start, while `login_status` is timed from the moment `login_ended` arrives, which the test waits for
up to 5 s. The budget (4 s) and the command's 5 s timeout are therefore anchored to different clocks: a command that waited for the slice, sent
late enough that the slice's remaining time fits under 4 s, would pass `took < budget`. And `freed_during_slice` is read at an arbitrary point
after the answer with no ordering against the slice, so it does not catch that.
Change: `slow_sink` sets `slice_done` when its `release.wait(hold)` returns; the test captures `slice_done.is_set()` the moment the answer
arrives and asserts it is false before the budget assertion. The order is the signal; the budget stays as the net.
Red: a mutant `op_login_status` that waits (`await asyncio.sleep(3.0)`, temporary) with the test's `hold` shortened to 2.0 (temporary) so the slice ends before the
answer: "login_status was answered only after the feed slice ended" (fails). Both edits reverted (`git status` shows only the test file). With the real
hold (8 s) and the real code: 30 of 30 under load.
Not shown: the old assertions on the same mutant (they sit after the new one, so they never ran); the mutant waits a fixed time and not on the slice's
actual end, since the product keeps no handle to the slice after the swap.

### Thread 2, `tests/test_redesign_lists_filter.py` (Trivial): valid, STOPPED, product behaviour

Evidence: the scale test presses `End` on the Targets pane. That key goes to `Sidebar._jump_target(True)` (`tsb_tui/widgets/sidebar.py` lines 2267 to 2268), which builds
`ids = [row.id for row in painted]` (line 1790) over all 8,008 painted rows just to read the first or last id and the length. The counters
(`_target_row_lines`, `_target_facts`) count row heights and facts, not row visits, so this pass is not counted. The pass came in with #114
(86e11c2). So the reviewer is right: the measured key does reach a full walk of the painted rows.
It is a cheap attribute read per row (no height, fact or layout work), but it is a per-key walk proportional to the list, the thing the test's rule 11
guards against. Instrumenting row access during the key would fail on today's code, so it cannot be done without a product change. The brief says to stop
here: no product change in this PR. Nothing changed for this thread.

Options (not chosen by me):
1. Product PR: `_jump_target` reads `painted[-1].id` / `painted[0].id` and `len(painted)`, no list built; then the test counts iteration of `painted_targets()`
   (a tuple subclass whose `__iter__` counts) and a mutant that walks every row in that path fails it. Recommended if rule 11 means no per-key walk of the list.
2. Accept the pass: it is one attribute read per row; reply to the thread that the test measures the per-row layout and fact work, and that the id list is a
   known cheap pass, recorded as such. No change.
3. Test only, with a bound at or above the list size: it would pass on today's code and catch nothing the current counters do not; not recommended.

Reply text for the thread (option 2, if the coordinator rules it, or as the status until option 1 is ruled): "Valid: End goes through `_jump_target`, which builds a list of
all painted row ids (an attribute read per row, no layout or facts). The test counts the layout and facts work, which is what made keys pause (rule 11); the id list is not
counted. Changing `_jump_target` is a product change, so it is outside this test-only PR; follow-up: read `painted[-1].id` and `len(painted)` directly, then count row access in the test."

Reply text for thread 1: "Valid, thanks. The slice is held 8 s from its own start while the command is timed from `login_ended`, so the budget alone does not order the answer
against the slice. `slow_sink` now sets `slice_done` after its hold; the test asserts it is not set when the answer arrives, before the budget check. A mutant `login_status` that waits
fails it (shown with the hold shortened so the slice ends first)."

### Commands (exit codes)
- login_status test, 10 loops x 3 parallel copies under `capped --` on the real code: 30 of 30 exit 0.
- Mutant run: exit 1, the new assertion. Reverted.
- Whole `tests/test_drive_login_status.py`: exit 0, 13 passed.
- `make lint` with the test venv's `bin` first on PATH: exit 2, 1 failed, 121 passed, 1 skipped. The failure is the packaging test
  `.github/scripts/tests/test_contract_tarball.py:74` (`pip wheel --no-build-isolation`): that venv has no `setuptools`. CI installs it.
  Correction (round 6): I had written that no venv here had `setuptools`; I had only checked the two shared test venvs. A local venv with
  setuptools does exist, and `make lint` passes with it first on PATH (round 6 below).
- `make lint` with the system `pip` (as in rounds 1 to 4, `PYTHON` the test venv): exit 0.
- The scale test was not changed in this round, so its 60-run loop was not repeated.

Not verified: the mutant for thread 1 waits a fixed time; no CI-runner run; the full suite.

Status: DONE_WITH_CONCERNS
Summary: Thread 1 holds and is fixed with an ordering assertion (red on a waiting mutant); thread 2 holds but the walk is in product code (`_jump_target`), so I stopped and left the test as it is, with options and reply text.

## Round 6: the order is taken at receipt, not at resumption

Finding (`tests/test_drive_login_status.py`, Reliability): `slice_done.is_set()` was read after `await rig.cmd(...)` resumed. A slice that ends between the
answer's arrival and the coroutine's resumption was counted as the command having waited, so a loaded loop could fail a correct answer. Valid.

Fix (test only; the rig already supports it, no product change): `Rig.on_line` is called synchronously in the rig's `_line`, where the answer line is received and
before its future resolves. The test sends `login_status` with `rig.send`, hooks `on_line` for that request id, and records `slice_done.is_set()` at that point.
It then ends the slice itself (`release.set()`) and waits until it has ended (`slice_done.wait`) before the test goes on, so the test always resumes after the slice
ended; the assertion reads the value recorded at receipt. The budget and the 5 s wait stay as the net.

Red and green:
- Delayed resumption, today's read (`slice_done.is_set()` at resumption, with the slice ended before the test resumes): fails, "login_status was answered only
  after the feed slice ended" although the answer arrived first (temporary edit, reverted).
- The same case with the fix: passes.
- Waiting-command mutant (`op_login_status` waits 3 s, temporary, with the test's `hold` shortened to 2 s so the slice ends before the answer): still fails with the same message.
  Both edits reverted (product diff empty).
- Real code, real hold (8 s): 30 of 30 under load (10 loops x 3 parallel copies, `capped --`), all exit 0.

Commands (exit codes):
- Whole `tests/test_drive_login_status.py`: exit 0.
- `make lint` with a local venv with setuptools first on PATH (then the shared test venv): exit 0.
- `make lint` with the shared test venv first on PATH, the one without setuptools: exit 2, the same packaging test as in round 5 (`pip wheel --no-build-isolation`). Not touched by this change.

Not verified: no CI-runner run; the full suite.

Reply text for the thread: "Valid. The flag was read when the test coroutine resumed, so a slice that ended between the answer's arrival and the resumption counted as the command
having waited. The test now records `slice_done` in the rig's `on_line` hook, which runs where the answer line is received, and asserts on that value; it also ends the slice and waits
for it before going on, so the test always resumes after the slice ended and the old read fails it. The waiting-command mutant still fails it."

Status: DONE_WITH_CONCERNS
Summary: The login_status ordering is now taken at the rig's receipt of the answer line, so a late resumption cannot fail a correct answer (red on the old read, green on the fix, red on a waiting mutant); lint passes with a setuptools venv. Open from earlier rounds: the update-check ruling and the `_jump_target` product walk.
