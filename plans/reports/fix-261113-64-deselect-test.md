# Fix: test_pr_macos_build_deselection asserts stale whole-file deselect

**Status:** DONE
**AI-Model:** swift-1.5-iq2_xs

## Problem

`tests/test_installers.py:348` asserted `'--deselect packaging/tests/test_macos_build.py'` (whole-file deselect) in the PR CI packaging step. Commit `b36c9fd` replaced that with six per-case deselects (the three parametrized tests × two macOS targets), leaving launcher and interpreter tests to run.

## Fix

Replaced the single whole-file assertion with:
1. Six per-case assertions matching the workflow's exact `--deselect "…[target]"` strings.
2. A negative check that `test_posix_launcher*` and `test_macho_interpreter_architecture` never appear in any deselect line.
3. An assertion that the skip-fail `grep -E '^SKIPPED'` guard remains in the step.

Kept existing assertions for `test_built_zip` deselect, the pinned-inputs comment, and the publishing comment.

## Verification

- `pytest tests/test_installers.py` — 26 passed, 3 skipped (CI-image gates), exit 0.
- `make lint` — 110 passed, import-lint clean, engine-check OK, exit 0.
