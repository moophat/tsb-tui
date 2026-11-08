# Build report — Windows engine build systemctl query fix (PR #33 review)

**Branch:** feat/packaging-engine-windows | **Model:** Gemini (agy minhtu.workmail) | **Date:** 2026-11-09 | **Timezone:** Asia/Saigon

## Finding & Solution

- **Finding (Qodo, PR #33):** `packaging/engine/build-engine.sh`'s `check_active_units` ran `systemctl list-units` with `--state=active` only, so an engine or post-merge loop unit that is still starting (`activating`) or `reloading` was missed.
- **Fix:**
  - Query `active`, `activating`, and `reloading` states together via `--state=active,activating,reloading`.
  - Format the refusal message to name all conflicting units and their states (e.g., `another node build or post-merge loop is running: <unit> (<state>)`).
  - Preserve the existing refusal when `systemctl` itself fails (`fail "failed to query systemctl for active units (exit status $status)${err:+: $err}"`).
  - Added unit test `test_guard_function_refuses_when_activating_unit_running` in `packaging/engine/tests/test_build_engine_guard.py` asserting that `systemctl` receives `--state=active,activating,reloading` and the refusal names the activating unit and its state.

## Files Changed

- `packaging/engine/build-engine.sh`:
  - Updated `check_active_units` to query `--state=active,activating,reloading`.
  - Filter units ignoring `TSB_ENGINE_BUILD_UNIT` and format conflicting units and their state (`$1 ($state)`).
  - Emit refusal message containing `another node build or post-merge loop is running: $conflict`.
- `packaging/engine/tests/test_build_engine_guard.py`:
  - Added `test_guard_function_refuses_when_activating_unit_running` asserting the guard refuses on activating units, names the unit and state, and passed `--state=active,activating,reloading` to `systemctl`.

## Verification Commands & Results

1. **Guard test suite (`packaging/engine/tests/test_build_engine_guard.py`):**
   - Command: `python3 -m pytest packaging/engine/tests/test_build_engine_guard.py -p no:cacheprovider`
   - Exit code: `0`
   - Test counts: 6 passed, 0 failed in 0.29s

2. **Repository lint & verification (`make lint`):**
   - Command: `make lint`
   - Exit code: `0`
   - Test counts: 66 passed, 0 failed in 9.05s across repo tests (`.github/scripts/tests/test_docs_branch_guard.py`, `.github/scripts/tests/test_leak_check_prefixes.py`, `.github/scripts/tests/test_release_install.py`, `tools/test_import_lint.py`)
   - Gates: `import-lint` clean (174 files), `docs_branch_guard` clean, `leak-check` clean, `check-engine` verified (shared object and headers match PIN).
