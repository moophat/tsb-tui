# Test flakes under load — 2026-11-05

**AI-Model:** strata
**Branch:** fix/tests-wait-on-their-state, from main (2a08ba4)
**Issues:** #3, #4, #6 of moophat/tsb-tui (#5 not mine)

Status: DONE — the four files pass and ruff is clean. No STOP: every failure was a test reading
before the state it reads, not a TUI defect, and no fix needed a change outside tests/.

The rule for all: a test waits on the state it reads with a bound sized for a loaded CI node, never a
fixed pause or a frame count. The suite's existing helper is `wait_until` (tests/conftest.py:112-124),
which already takes a `timeout_s` parameter; I used it and added no second helper. **conftest.py was not
changed** — its `wait_until` already accepts a bound, so no parameter was needed there. The only helper
I touched is `_at_login_with_no_workspace` in tests/test_reattach_login.py (an allowed path), which I gave
a `timeout_s` parameter that forwards to `wait_until`; the default keeps every other caller at 10 s.

Every assertion is left exactly as written; each test still fails when the behaviour it guards breaks
(a label shown while locked, a column dropped wrongly, a stale/early read, the map not built).

## #3 — tests/test_reattach_login.py

- `test_a_drive_attach_after_a_relogin_waits_for_the_workspace_to_be_ready`: the re-login's
  `_at_login_with_no_workspace` is now bounded 30 s (it reaches Login across a restart on a loaded node).
  drive's `op_attach` already waits for the workspace to mount through `workspace_ready`, bounded by the
  harness profile's `drive.wait_timeout_ms` = 30 000 ms; that internal readiness bound is unchanged and
  load-tolerant. The test still asserts the pushed-but-not-mounted window (`screen.fleet is None`) and
  that `op_attach` returns the session.
- `test_a_create_that_completes_after_the_login_is_gone_is_discarded`: the login wait is bounded 30 s; the
  create-completion spy is now waited on with `wait_until(pilot, done.is_set, ..., 30)` (the seen 10 s
  timeout); the stub's `create_gate.wait` is raised to 30 s so the create stays in flight until Login is
  reached and its completion lands after. The stack/`workspace()`/detached-session assertions are unchanged.

## #3 — tests/test_targets_tab.py

- `test_the_table_loses_columns_in_a_stated_order_and_keeps_name_and_type`: before each block reads the
  columns, it now waits until the table's header row is laid out — all seven headers present in the wide
  view, and NAME+TYPE present in the narrow view — bounded 30 s. The seen flake (TAGS read before the
  table laid out) is closed. The column-presence/absence assertions are unchanged; the test still fails if
  SOURCE/YOU CAN/TAGS survive in the narrow view.

## #4 — tests/test_credentials_screen.py

- `test_the_list_shows_references_and_kinds_and_labels_only_while_unlocked`: before the first assertions
  it waits until the locked list is rendered (references and kinds present, label absent), bounded 30 s;
  after unlock+refresh it waits for the label re-rendered, bounded 30 s. If the screen shows labels while
  locked, the first predicate never becomes true and the test times out — so it still fails on that defect.

## #6 — tests/test_model_fleet.py

- `test_a_poll_of_n_sessions_builds_the_display_ids_once_and_never_rescans_the_fleet_per_row` (the 100 and
  500-session cases): the fixed `await pilot.pause(0.1)` after `_apply_poll` is replaced by
  `wait_until(pilot, lambda: calls["map"] >= 1, "the display-id map to be built", 30)` — it waits on the
  map being built with a load-tolerant bound of 30 s. The three assertions (no per-row rescans, the map
  built once, exactly N+1 ids) are unchanged.

## Checks (clone root, PYTHONPATH = clone root, NO_COLOR unset)

```text
env -u NO_COLOR PYTHONPATH=<lanes>/tsb-tui-flakes <test venv>/bin/python \
    -m pytest tests/test_reattach_login.py tests/test_targets_tab.py \
              tests/test_credentials_screen.py tests/test_model_fleet.py -p no:cacheprovider
======================== 60 passed in 71.22s (0:01:11) =========================
PYTEST_EXIT=0

<test venv>/bin/ruff check tests/
All checks passed!
RUFF_EXIT=0
```

All four files ran; none skipped. The whole suite was not run (CI runs it). Not committed, not pushed.

**PR #7 review fixes (commit 67e4fd0, taken):** `LateCreateStub.create_gate.wait` raised to 60 s so the stub's own create timeout exceeds the 30 s login wait; the credentials test's first predicate drops `"core switches" not in screen_text(app)` (references and kinds only), so the label-shown-while-locked case is reported by its own assertion below.
