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
