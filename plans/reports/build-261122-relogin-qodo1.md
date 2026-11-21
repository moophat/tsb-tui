# Build Report: Qodo Round 1 Review Fixes (tsb-tui #108)

Date: 2026-11-22
Branch: `fix/relogin-after-large-poll` at 83ec4aa

## Finding 1 — The regression test must exercise the in-flight logout gate

### The Change
In `tests/test_relogin_after_large_poll.py` (lines 290–335):
- Recorded the baseline fleet generation before sign-out starts (`gen_baseline = await rig.app_call(lambda: old_screen.fleet.applied_generation)`).
- In the `during` branch, released the held poll reply only after the workspace marks that sign-out has begun via helper `_wait_logout_began(rig, old_screen)` polling `old_screen._logout_began` without fixed sleeps.
- Waited for the poller worker thread to complete via `_wait_poller_finished(old_screen)` before verifying that `old_screen.fleet.applied_generation` remains equal to `gen_baseline`.
- Kept the separate `before` timing case where the poll reply is released and verified to apply (`gen_baseline > 0`) before sign-out starts.

### Verification (Red / Green)
- **Red (on parent commit before 83ec4aa):**
  Running `test_relogin_after_a_poll_reply_during_logout` against the parent commit's code failed with:
  ```text
  tests/test_relogin_after_large_poll.py:353: AttributeError
  AttributeError: 'TerminalScreen' object has no attribute '_logout_began'
  ```
  Additionally, running `test_a_reply_after_logout_began_leaves_the_fleet_generation_unchanged` against a scratch copy with the gate reverted failed with:
  ```text
  tests/test_relogin_after_large_poll.py:467: AssertionError
  assert 2 == 1
  ```
- **Green (on 83ec4aa with strengthened test):**
  `test_relogin_after_a_poll_reply_during_logout` passed in 1.65s; full file (8 tests) passed in 47.29s.

---

## Finding 2 — A focus failure is not a refused login

### The Change
- In `tsb_tui/drive/runner.py`:
  When the password field never takes focus in `_press_when_password_focused`, raised `DriveError("login_not_submitted", "the login form could not take the focus; nothing was sent to TSB")`. Other `login_failed` paths were left untouched.
- In `tsb_tui/drive/GUIDE.md`:
  Added `login_not_submitted` to the Section 11 error table:
  `| login_not_submitted | the TUI could not submit the login form, so nothing reached TSB; why is the TUI's own sentence. Look at the screen, retry once, and report it if it repeats |`
- In `docs/using-tsb-tui.md`:
  Added `login_not_submitted` to the Section 9 error table:
  `| login_not_submitted | The TUI could not submit the login form, so nothing reached TSB; why is the TUI's own sentence | Look at the screen, retry once, and report it if it repeats |`
- In `tests/test_relogin_after_large_poll.py`:
  Updated `test_drive_answers_login_not_submitted_when_password_focus_cannot_land` to assert `answer.get("error") == "login_not_submitted"` and `answer.get("why") == "the login form could not take the focus; nothing was sent to TSB"`.

### Verification (Red / Green)
- **Red (on code before fix):**
  Before the fix, drive timed out after 30s instead of answering immediately:
  ```text
  tests/test_relogin_after_large_poll.py:431: AssertionError
  AssertionError: {'id': 3, 'ok': False, 'error': 'timeout', 'why': 'the login result did not come within 30000 ms'}
  assert 'timeout' == 'login_not_submitted'
  ```
- **Green (with fix):**
  `test_drive_answers_login_not_submitted_when_password_focus_cannot_land` passed cleanly, asserting `error == "login_not_submitted"` and `why == "the login form could not take the focus; nothing was sent to TSB"`.

---

## Gate Results

1. **Pytest suite:**
   - Command: `PYTHONPATH=program/contract/client:. <test-venv>/bin/python -m pytest -q -p no:cacheprovider tests/test_relogin_after_large_poll.py tests/test_drive*.py`
   - Exit code: `0`
   - Result: `496 passed in 548.50s`
2. **Contract check:**
   - Command: `make contract-check`
   - Exit code: `0`
   - Output: `contract-check: program/ is clean at c4c2b81bb7a1, v0.18.0`
3. **Lint & Boundary checks:**
   - Command: `make lint`
   - Exit code: `0`
   - Output: `110 passed`, `import-lint: clean (./ 207 files)`, `clean: no node addresses, dev hostnames, secret paths, or workstation/mount paths`, `engine-check: shared object and all headers verified; object matches PIN`
4. **Diff scope:**
   - `git diff --name-only` contains strictly allowed files.
