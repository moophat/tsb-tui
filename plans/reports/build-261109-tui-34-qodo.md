# PR #34 review fix round

**AI-Model:** codex
**Date:** 2026-11-09 (Asia/Saigon)
**Branch:** fix/tui-22-29
**Base:** d9f6103

## Scope and checks

Implement the two accepted findings from the supplied brief. Only the terminal screen,
the attachment/close tests and this report are changed. No commit or push.
The explicit lane scope overrides the general ledger/session update workflow.

- [x] Inspect attachment ownership, release, unmount and sign-out's detach barrier.
- [x] Track attachment closes separately from cancelable input writes; await pending
  closes in detach_all, including closes for slots already removed.
- [x] Assert the never-started coroutine is CORO_CLOSED directly, retaining the
  socket assertion and warning/garbage-collection checks.
- [x] Add a regression using a real device-login handoff and a held close. It closes
  the slot before the worker starts, signs out, checks logout has not begun, invokes
  unmount while close is pending, then releases close and checks the socket was
  closed when the logout request was issued.
- [x] Run the touched test file, lint and whitespace check.

## Verification

In the commands below, TEST_PYTHON denotes the operator-specified test virtualenv's
Python executable. Its bin directory was prepended to PATH for lint. These variable
spellings keep machine-specific absolute paths out of this report.

| Command | Exit | Result |
| --- | --- | --- |
| `PYTHONPATH=.:tests "$TEST_PYTHON" -m pytest tests/test_attach_and_close.py -q` (before edits) | 0 | 8 passed in 7.01s |
| `PYTHONPATH=.:tests "$TEST_PYTHON" -m pytest tests/test_attach_and_close.py -q` (after edits) | 0 | 9 passed in 8.02s |
| `PYTHONPATH=.:tests make lint PYTHON="$TEST_PYTHON"` | 0 | Ruff: all checks passed; 66 passed in 8.12s; import lint clean (174 files); leak and engine checks passed |
| `git diff --check` | 0 | No whitespace errors |

The docs-branch guard reported its local no-event/no-pull-request no-op; its PR-event
execution was not verified here. The full application suite and external broker E2E
were not run. No code was reverted to demonstrate a red test.

**Pending:** none within the brief.
**Status:** DONE

## Orchestrator follow-up

- [x] Use gather's return_exceptions=True for pending attachment closes, so a
  socket close error cannot abort sign-out before logout.
- [x] Add a handed-over attachment double whose close waits at the barrier then
  raises OSError. Sign-out completes without an escaping exception, logout is sent
  once, and no slots or pending attachment closes remain.
- [x] Run the touched test file and lint again.

The first follow-up test run exited 1 (1 failed, 9 passed in 8.47s): the new
DeviceLoginApp fixture omitted its required rounds argument. Corrected to rounds=[].
No production code was reverted.

| Command | Exit | Result |
| --- | --- | --- |
| `PYTHONPATH=.:tests "$TEST_PYTHON" -m pytest tests/test_attach_and_close.py -q` | 0 | 10 passed in 11.19s |
| `PYTHONPATH=.:tests make lint PYTHON="$TEST_PYTHON"` (first follow-up run) | 0 | Ruff passed; 66 passed in 11.40s; import, leak and engine checks passed; docs guard local no-op |

Final lint confirmation: `PYTHONPATH=.:tests make lint PYTHON="$TEST_PYTHON"`
exited 0: Ruff passed; 66 passed in 11.77s; import lint clean (174 files), leak
and engine checks passed; docs guard local no-op. `git diff --check` exited 0.

**Follow-up pending:** none. **Status:** DONE.
