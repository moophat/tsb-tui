# Drive screen-lock build report

**AI-Model:** gpt-6.1-sol
**Status:** DONE_WITH_CONCERNS

The corrected pending-wait rule and 429 behavior were implemented. A wait crossing the lock moment re-checks before returning session content; a 429 keeps it locked and retries only at the next refused command.

## D-items

- D-a: `tsb_tui/drive/screen_lock.py`, `DriveScreenLock.reset` and `DriveScreenLock.resume`, record the login password-file path only when it is a regular file and re-reads it with the existing `read_password_file` checks. Stdin, pipes and descriptors cannot unlock a later lock.
- D-b: `tsb_tui/drive/runner.py`, `Driver.execute_line` and `Driver.execute`, timestamp every nonblank, non-comment command line; `Commands.run` in `tsb_tui/drive/commands.py` checks the lock on content and typed commands. `Driver.run_wait` re-checks pending waits before answering.
- D-c: `DriveScreenLock.resume` in `tsb_tui/drive/screen_lock.py` handles 204, 403, unreadable files, 429 and 401. 429 names no retry time and says the next refused command retries. Password data is deleted after the request and is absent from answers and transcripts.
- D-d: `Commands.run` and `Commands.screen_answer` in `tsb_tui/drive/commands.py` withhold screens, looks and dumps while locked; non-content commands continue. `ScreenLock._tick` and `ScreenLock.lock` in `tsb_tui/screen_lock.py` delegate under drive without entering the Textual lock state; `ScreenLock.lock_now` still calls the published vault-lock API, and `DriveScreenLock.engage` voids drive looks. `GUIDE.md` sections 2, 5, 11 and the guarantee section document the regular-file requirement and source-versus-sender limitation.

## Tests

Initial red run on the unchanged pin-bump head (exit 1), before implementation:

```text
FF.FFFFF.FFFFF                                                           [100%]
12 failed, 2 passed in 79.61s (0:01:19)
```

Representative red assertions:

```text
content withholding: expected locked=True, got ok=True with lines and cursor
locked typed send: expected screen_locked, got no_look
pending wait: expected a re-check, got unlocks=0
settings refresh: expected the auth/me request, got an unset started event
headless UI: expected ScreenLock.locked=False, got True
```

The table lists tests in `tests/test_drive_screen_lock.py`. The pending-wait and settings tests were strengthened after the initial red, and startup-stdin coverage was added separately. The two initially green rows are compatibility assertions, not proof the old implementation supported locking.

| Test | Initial red | Final green |
|---|---|---|
| `test_idle_past_the_screen_lock_time_locks_drive_and_withholds_session_content` | FAIL | PASS |
| `test_while_locked_typed_sends_press_and_click_are_refused_and_send_nothing` | FAIL | PASS |
| `test_commands_with_no_session_content_go_on_while_locked` | PASS | PASS |
| `test_the_first_refused_command_re_reads_the_password_file_and_unlocks` | FAIL | PASS |
| `test_a_wait_held_across_the_lock_moment_re_checks_before_it_answers` | FAIL | PASS |
| `test_a_stdin_or_pipe_login_stays_locked_until_a_new_login` | FAIL | PASS |
| `test_a_refused_re_check_keeps_drive_locked` | FAIL | PASS |
| `test_the_password_never_appears_in_any_answer_log_event_or_capture` | FAIL | PASS |
| `test_any_command_restarts_the_idle_time` | PASS | PASS |
| `test_the_screen_lock_time_is_the_cores_and_absent_locks_mean_no_lock` | FAIL | PASS |
| `test_new_and_attach_reread_the_lock_settings_from_auth_me` | FAIL | PASS |
| `test_no_lock_screen_comes_up_under_drive` | FAIL | PASS |
| `test_lock_now_under_drive_locks_drive` | FAIL | PASS |
| `test_the_screen_lock_stays_on_with_the_gate_off` | FAIL | PASS |
| `test_a_startup_stdin_login_cannot_supply_a_recheck` | added later | PASS |

Latest focused green run (exit 0):

```text
...............                                                          [100%]
15 passed in 23.29s
```

`tests/test_screen_lock.py` and `tests/test_screen_lock_races.py` remain unchanged. An earlier combined focused run including them and target-write tests passed (exit 0):

```text
43 passed in 69.85s (0:01:09)
```

App login and sign-out checks passed (exit 0):

```text
30 passed in 48.36s
```

The full `make test` collected 2,042 tests but was interrupted after approximately ten minutes. It was not a passing full-suite result. It exposed obsolete drive overlay expectations in discoverability; those assertions were migrated to the headless lock. The fallback also exposed the missing GUIDE error-table entry, which was fixed and separately passed. An intermediate fallback completed with exit 1: `1 failed, 444 passed in 547.52s (0:09:07)`; the only failure was that GUIDE error-table entry. Final combined fallback (exit 0):

```text
475 passed in 584.89s (0:09:44)
```

Command scope: `python -m pytest -q tests/test_drive*.py tests/test_screen_lock.py tests/test_screen_lock_races.py tests/test_discoverability.py tests/test_login_completion.py tests/test_one_login_screen.py tests/test_reattach_login.py tests/test_sign_out.py --tb=short`. It includes every drive test and the unchanged attached lock tests, discovery, and app login/sign-out behavior. No tests were skipped. The separate final focused run confirms the last descriptor-source hardening.

Verification uses a temporary virtual environment with the project's pinned vault dependencies because system Python lacks them. No skip was added.

## Scope and constraints

No rig, real core or target was used. No commit, push or git configuration change was made. Only the allowed source, test, guide and report paths were changed. The full suite outside the final fallback remains unverified.

## Lint

Final `make lint` exit: 0.

```text
All checks passed!
102 passed
import-lint: clean
engine-check: shared object and all headers verified; object matches PIN
```

The docs-branch guard and leak check also completed successfully. Earlier lint attempts caught new-test file-writing style and import order; those were corrected.

## Final working tree

`git diff --check`: exit 0. `git status --short`:

```text
 M tests/test_discoverability.py
 M tests/test_drive_target_writes.py
 M tsb_tui/drive/GUIDE.md
 M tsb_tui/drive/commands.py
 M tsb_tui/drive/runner.py
 M tsb_tui/screen_lock.py
?? plans/reports/build-261112-drive-send-gate-p4.md
?? tests/test_drive_screen_lock.py
?? tsb_tui/drive/screen_lock.py
```

The two migrated existing drive tests now assert the headless lock rather than the obsolete Textual overlay. The attached TUI tests were not edited. Status is DONE_WITH_CONCERNS solely because the complete suite was interrupted and the authorized fallback was used.

## Review correction: transient password re-check failures

**AI-Model:** gpt-6.1-sol

`tsb_tui/drive/screen_lock.py`, `DriveScreenLock.resume`, now reads the password in a separate try block before making the existing client's `screen_unlock` request. A file read error sets permanent `failure` and requires a new login. Request errors are classified independently: 401 ends the login; 429, all 5xx responses, `HttpError`, and request-side `OSError` subclasses retain the lock without setting `failure`. Their answer says the re-check could not complete for now and drive will try again at the sender's next refused command. Other 4xx responses, including 403 and 422, remain permanent until a new login. The password local is deleted in the request's finally block.

### Red-first regression tests

Both new tests were run against the pre-correction implementation. Exit: 1.

```text
FF                                                                       [100%]
2 failed, 15 deselected in 3.34s
```

| Test in `tests/test_drive_screen_lock.py` | Red evidence | Green behavior |
|---|---|---|
| `test_a_server_failure_retries_the_recheck_on_the_next_refused_command` | 503 incorrectly set permanent `failure` | 503 withholds content, sets no permanent failure, then the next refused command re-checks and unlocks on the stub's 204 |
| `test_a_transport_failure_retries_the_recheck_on_the_next_refused_command` | `HttpError` incorrectly set permanent `failure` | `HttpError`, connection refusal, connection reset, timeout and TLS exceptions each withhold content and set no permanent failure; the next refused command reaches the stub and unlocks on 204 |

The existing `test_a_refused_re_check_keeps_drive_locked` was strengthened to assert that a 403 sets permanent failure, and that a disappearing password file sets permanent failure and makes no request even after the file is restored. A new login remains the remedy. The canary-password output/capture assertions still surround the new scenarios.

Final focused screen-lock command: `python -m pytest -q tests/test_drive_screen_lock.py tests/test_screen_lock.py tests/test_screen_lock_races.py --tb=short`. Exit: 0.

```text
39 passed in 62.98s (0:01:02)
```

`make lint`: exit 0, including 102 lint-gate tests, import lint, docs guard, leak check and engine verification. The first lint attempt identified an unbound test-loop closure; the loop variables were explicitly bound and the final lint passed. No tests were skipped.

The drive-test command completed with exit 0: `413 passed in 396.85s (0:06:36)`.

Only `tsb_tui/drive/screen_lock.py`, `tests/test_drive_screen_lock.py` and this report changed during this correction. No plan, ledger, ruling or pinned program file was edited. No commit, push, real core, target or rig run.

## Qodo round 1

**AI-Model:** gpt-6.1-sol

The eight review findings are implemented in the drive lock path:

- Typed sends and UI key/mouse delivery re-check the lock at delivery time (`tsb_tui/drive/commands.py`, `tsb_tui/drive/runner.py`, `tsb_tui/app.py`). Regression tests cover prompt preparation, gate-off sends, press, and click.
- Lock refresh callbacks are generation/client scoped, so answers from an ended login are discarded (`tsb_tui/drive/commands.py`).
- Replacement login passwords are read and validated before signing out the existing locked session (`tsb_tui/drive/commands.py`).
- Held waits re-check their predicate and matched values on the fresh screen after unlock, within the original deadline (`tsb_tui/drive/runner.py`).
- Completed `new` and `attach` retain their session identifier and success metadata while locked, without screen content (`tsb_tui/drive/commands.py`).
- Successful post-operation re-checks remove stale `locked` and `why` fields before adding fresh screen data (`tsb_tui/drive/commands.py`).
- Locked refusals use one `why` value and return `screen_locked` with `sent: false` and no session content (`tsb_tui/drive/commands.py`).
- Successful retry re-checks clear transient failure text while preserving the idle-lock reason used for invalidated looks (`tsb_tui/drive/screen_lock.py`, `tsb_tui/drive/commands.py`).

### Red-first tests

The twelve new regression tests in `tests/test_drive_screen_lock.py` were run against the rebased head before the fixes. Exit: 1.

```text
12 failed, 17 deselected in 18.33s
```

### Green verification

Focused screen-lock and drive-lock tests:

```text
51 passed in 84.87s (0:01:24)
```

Exit: 0. The full drive test command completed with exit 0: `425 passed in 427.58s (0:07:07)`. `make lint PYTHON=python` completed with exit 0, including the lint gates, import check, docs guard, leak check, and engine verification. No tests were skipped.

The complete `make test` target was not run in this round; the authorized focused screen-lock and drive test suites were run instead. No plans, ledgers, rulings, contract files, commits, or pushes were changed. The final working tree contains changes only under the allowed source/tests/report paths.


| Finding | Regression tests (all in `tests/test_drive_screen_lock.py`) |
|---|---|
| Input delivery | `test_a_send_rechecks_the_lock_after_preparing_the_prompt`, `test_a_gate_off_send_checks_the_lock_immediately_before_delivery`, `test_ui_delivery_stops_when_the_lock_engages_during_press`, `test_ui_delivery_stops_when_the_lock_engages_during_click` |
| Stale refresh | `test_a_lock_refresh_from_an_ended_login_is_discarded` |
| Replacement file | `test_an_unreadable_replacement_login_preserves_the_locked_session` |
| Fresh wait match | `test_a_wait_rechecks_its_condition_after_unlocking`, `test_a_wait_returns_values_matched_after_the_password_recheck` |
| Completed session | `test_a_completed_new_or_attach_keeps_its_identifier_when_locked` |
| Stale answer fields | `test_a_successful_post_send_recheck_removes_locked_fields` |
| Locked refusal | `test_a_refusal_while_locked_has_no_duplicate_why` |
| Retry message | `test_a_successful_retry_clears_the_transient_failure_reason` |

Final `git status --short`:

```text
 M plans/reports/build-261112-drive-send-gate-p4.md
 M tests/test_drive_screen_lock.py
 M tsb_tui/app.py
 M tsb_tui/drive/commands.py
 M tsb_tui/drive/runner.py
 M tsb_tui/drive/screen_lock.py
```

`git diff --check`: exit 0. The attached TUI screen-lock test files remain unchanged.

Status: DONE_WITH_CONCERNS
