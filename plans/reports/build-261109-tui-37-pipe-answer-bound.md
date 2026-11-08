# Build report — pipe answer outer bound (#37)

**Branch:** fix/tui-37-pipe-answer-bound | **Model:** gemini-3.8-flash | **Date:** 2026-11-09 | **Timezone:** Asia/Saigon

## Summary

Fixed flaky test `test_missing_pipe_answer_lines_expire_without_sending_a_round` in `tests/test_login_completion.py` (issue #37).

Under CI load, the previous 0.6 s outer bound on `asyncio.wait_for(asyncio.shield(task), 0.6)` was insufficient wall-clock time for the test to complete even though `drive.answer_timeout_ms=60` had elapsed. Because the pipe's writer is kept open for the whole wait, a blocking drive never answers while the writer is open; therefore, the outer wait bound only needs to be finite to catch any failure to expire.

## Changes

- Defined named constant `PIPE_ANSWER_BOUND_S = 10.0` in `tests/test_login_completion.py` with the comment:
  `# A blocking drive never answers while the writer is open, so the bound only needs to be finite.`
- Replaced the hardcoded `0.6` bound in `test_missing_pipe_answer_lines_expire_without_sending_a_round` with `PIPE_ANSWER_BOUND_S`.
- Changed nothing else: all assertions and drive behavior remain intact.

## Verification

### 1. Test execution
Command:
```bash
python -m pytest tests/test_login_completion.py -k missing_pipe_answer -q
```
Output:
```text
.                                                                        [100%]
1 passed, 3 deselected in 1.87s
```
Exit code: `0`

Full file test run:
```bash
python -m pytest tests/test_login_completion.py -q
```
Output:
```text
....                                                                     [100%]
4 passed in 7.68s
```
Exit code: `0`

### 2. Lint check
Command:
```bash
make lint
```
Output:
```text
All checks passed!
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0
rootdir: the lane clone
configfile: pyproject.toml
plugins: anyio-4.12.1
collecting ... collected 66 items

.github/scripts/tests/test_docs_branch_guard.py .............            [ 19%]
.github/scripts/tests/test_leak_check_prefixes.py ..                     [ 22%]
.github/scripts/tests/test_release_install.py ...............            [ 45%]
tools/test_import_lint.py ....................................           [100%]

============================== 66 passed in 8.27s ==============================
import-lint: clean (./ 174 files)
docs_branch_guard: no event, not pull_request — no-op
clean: no node addresses, dev hostnames, secret paths, or workstation/mount paths
libghostty-vt.so: OK
include/ghostty/vt/allocator.h: OK
include/ghostty/vt/build_info.h: OK
include/ghostty/vt/color.h: OK
include/ghostty/vt/color_scheme.h: OK
include/ghostty/vt/device.h: OK
include/ghostty/vt/focus.h: OK
include/ghostty/vt/formatter.h: OK
include/ghostty/vt/grid_ref.h: OK
include/ghostty/vt/grid_ref_tracked.h: OK
include/ghostty/vt/io.h: OK
include/ghostty/vt/key/encoder.h: OK
include/ghostty/vt/key/event.h: OK
include/ghostty/vt/key.h: OK
include/ghostty/vt/kitty_graphics.h: OK
include/ghostty/vt/modes.h: OK
include/ghostty/vt/mouse/encoder.h: OK
include/ghostty/vt/mouse/event.h: OK
include/ghostty/vt/mouse.h: OK
include/ghostty/vt/osc.h: OK
include/ghostty/vt/paste.h: OK
include/ghostty/vt/point.h: OK
include/ghostty/vt/render.h: OK
include/ghostty/vt/screen.h: OK
include/ghostty/vt/search.h: OK
include/ghostty/vt/selection.h: OK
include/ghostty/vt/sgr.h: OK
include/ghostty/vt/size_report.h: OK
include/ghostty/vt/snapshot.h: OK
include/ghostty/vt/style.h: OK
include/ghostty/vt/sys.h: OK
include/ghostty/vt/terminal.h: OK
include/ghostty/vt/types.h: OK
include/ghostty/vt/unicode.h: OK
include/ghostty/vt/wasm.h: OK
include/ghostty/vt.h: OK
engine-check: shared object and all headers verified; object matches PIN
```
Exit code: `0`

Status: DONE
