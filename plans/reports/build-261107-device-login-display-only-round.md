# Device Login Display-Only Round & Visible Failure Build Report

**AI-Model:** gemini-2.5-pro
**Date:** 2026-11-07
**Branch:** fix/device-login-display-only-round
**Base Commit:** 3f1eb0c

## Summary

This build resolves defect diagnosis during device login where:
1. A display-only keyboard-interactive round (`prompts: []`) carries no `attempt` or `prompt_id` field per contract (`program/contract/protocols/device-login.md`, `program/contract/schemas/ws-frames.schema.json`). In `tsb_tui/screens/device_login.py`, `render_frame` unconditionally read `frame["attempt"]` at line 305, raising `KeyError: 'attempt'`.
2. Unhandled exceptions in the pane's run loop (`_run()`) resulted in task termination and a silent cancellation (`await self.login.cancel()` in `finally:`) without updating the UI or notifying drive/listeners, leaving the operator and drive waiting until timeout (60 s).

## Implemented Changes

### 1. Refusal logic and display-only round handling (`tsb_tui/screens/device_login.py`)
- `render_frame` reads `frame["attempt"]` and executes the refusal check only when the round has prompts (`has_prompts = password or bool(frame.get("prompts"))`).
- For a display-only round (`prompts: []`), the refusal check is bypassed, name and instruction are rendered into `#device-name` and `#device-instruction`, no questions/inputs are mounted, `#device-send` remains hidden (`"prompt_id" in frame` is False), the frame is emitted via `self.emit(frame)`, and the pane waits for the next frame from the worker without sending any answer.
- In `send_answers()`, guarded against invocations when `"prompt_id"` is absent from `self.prompt` (and none explicitly supplied), and only records `self.last_answered_attempt` if `"attempt"` is present.

### 2. Visibly end device login on unexpected exceptions (`tsb_tui/screens/device_login.py`)
- Added an unexpected exception handler in `_run()`:
  ```python
  except Exception:  # noqa: BLE001 — an unexpected error ends the device login visibly
      if self._live():
          reason = OUTCOMES["connect_failed"]
          self.finish(reason)
          self.emit({"t": "device_login_result", "outcome": "connect_failed",
                     "error": "request_failed", "why": reason})
          self.terminal._refresh_fleet()
  ```
- Calling `self.finish(reason)` marks `self.ended = True`, displays the failure reason `"The device connection failed."`, exposes `#device-open-again`, and emits a `connect_failed` result with plain reason `"The device connection failed."`.
- Because `self.ended` is set to `True`, the `finally:` block does not invoke `await self.login.cancel()`, ensuring no silent cancel is issued while still closing the socket (`await self.login.close()`).

### 3. Drive module audit
- Audited `tsb_tui/drive/` (`commands.py`, `events.py`, `runner.py`). No unconditional `attempt` access was found. In `tsb_tui/drive/commands.py:565`, `op_login_answer` already validates `"prompt_id" not in prompt` before attempting to consume answers. No drive edits were necessary.

## Test Verification

### Red Run (Against unchanged code at 3f1eb0c)
Added test cases in `tests/test_device_login_pane.py` and executed against the base commit:
`python -m pytest tests/test_device_login_pane.py -k "test_a_display_only_round_between_password_and_acceptance_ends_connected or test_an_injected_error_in_render_frame_produces_the_connect_failed_result"`

```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0
rootdir: 
configfile: pyproject.toml
plugins: anyio-4.12.1
collecting ... collected 41 items / 39 deselected / 2 selected

tests/test_device_login_pane.py FF                                       [100%]

=================================== FAILURES ===================================
___ test_a_display_only_round_between_password_and_acceptance_ends_connected ___
...
E       AssertionError: timed out after 10s waiting for attached dump
tests/conftest.py:122: AssertionError

__ test_an_injected_error_in_render_frame_produces_the_connect_failed_result ___
...
E       AssertionError: timed out after 10s waiting for pane ended
tests/conftest.py:122: AssertionError
====================== 2 failed, 39 deselected in 23.75s =======================
```

### Green Run (Fixed implementation)
Rerun of targeted tests:
`python -m pytest tests/test_device_login_pane.py -k "test_a_display_only_round_between_password_and_acceptance_ends_connected or test_an_injected_error_in_render_frame_produces_the_connect_failed_result" -q`
```text
..                                                                       [100%]
2 passed, 39 deselected in 3.49s
```

Full device-login test suite execution:
`python -m pytest tests/test_device_login_pane.py tests/test_drive_device_login.py -q`
```text
...................................................                      [100%]
51 passed in 75.00s (0:01:15)
```

### Linter Verification
`make lint`
```text
All checks passed!
============================== 66 passed in 7.95s ==============================
import-lint: clean (./ 170 files)
docs_branch_guard: no event, not pull_request — no-op
clean: no node addresses, dev hostnames, secret paths, or workstation/mount paths
engine-check: shared object and all headers verified; object matches PIN
```

## Files Changed

- `tsb_tui/screens/device_login.py`: Read `attempt` and run refusal logic only when prompts exist; catch unexpected exceptions to visibly report `connect_failed` without silent cancel.
- `tests/test_device_login_pane.py`: Added tests for display-only round before acceptance and injected error in `render_frame`.
- `plans/reports/build-261107-device-login-display-only-round.md`: Build and verification report.
