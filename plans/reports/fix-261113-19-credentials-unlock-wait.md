# #19 — a stale credentials load overwrites a newer refresh

**AI-Model:** strata
**Branch:** fix/credentials-unlock-wait, from main (a78a3c8)
**Issue:** tsb-tui #19 — `test_the_list_shows_references_and_kinds_and_labels_only_while_unlocked` times out at "the label once unlocked" under CI load.

Status: DONE_WITH_CONCERNS

## Cause

`CredentialsScreen.action_refresh` (tsb_tui/screens/credentials.py, original line 204) fires a new list
load on every refresh press with no generation, and `_listed` (original line 208) applies any load's
answer. Two loads race: the last `_listed` to run rebuilds the table. When the mount's first load is
still running when a refresh press lands, the stale first load (locked, no labels) can finish after the
refresh (unlocked, with the label) and rebuild the table from the older data — erasing the label. Under
CI load the in-process fixture server serves the mount's first load slowly, so the press lands while it
is running, the stale load wins, and "core switches" never appears. The 30 s bound is a symptom; the
cause is the screen applying a stale load's answer.

## Reproduction (red on current code)

`tests/test_credentials_screen.py::test_a_refresh_press_while_a_load_is_running_is_not_overwritten_by_the_stale_load`.
The mount's first list load is held open (the stubbed `client.credentials` blocks on a threading event on
its first call), so the `#cred-refresh` press lands while it is running. The press starts a second load
that answers with the label; the test reads the label on screen, then releases the held first load. The
stale first load finishes after the refresh and rebuilds the table from the locked data. The assertion
"the newest refresh's label must survive a stale load that finishes after it" fails:

```text
E  AssertionError: the newest refresh's label must survive a stale load that finishes after it
E  assert 'core switches' in <screen text>
1 failed in 2.58s
```

This is deterministic (the order of the two loads is fixed by the event), not a timeout.

## Fix (ruled: loads are numbered, newest answer wins)

`CredentialsScreen` keeps a load counter `self._generation` (tsb_tui/screens/credentials.py:181).
`action_refresh` (:205) increments it and passes the number it started with to the callbacks (:207-209).
`_listed` (:212) and `_failed` (:226) apply their answer only when no newer load has started since
(`generation != self._generation` → the answer is stale and dropped, :213, :227). Nothing else changes.
A stale load that arrives after a newer refresh no longer overwrites the table, so the newest refresh's
label survives.

## Verification

Red run (current code, reproduction): `1 failed in 2.58s` (the assertion above).
After the fix the reproduction passes (`1 passed in 2.19s`).

Full file, five consecutive runs (run alone, NO_COLOR unset, pinned test venv):

```text
11 passed in 17.72s
11 passed in 17.49s
11 passed in 18.02s
11 passed in 18.43s
11 passed in 17.19s
```

`make lint` once: `MAKE_LINT_EXIT=0`. ruff is the CI gate; the Makefile SKIPs it when ruff is not on the
system python's PATH, so ruff was run from the pinned venv on `tsb_tui tests bench tools`: `All checks
passed!` `RUFF_EXIT=0`.

`git status --short` (within the allowed paths):
```text
M tests/test_credentials_screen.py
M tsb_tui/screens/credentials.py
?? plans/reports/fix-261113-19-credentials-unlock-wait.md
```

The original `test_the_list_shows_references_and_kinds_and_labels_only_while_unlocked` is unchanged.
Not committed, not pushed.

## Second race — a load answer on a torn-down table (ruled, fixed)

The orchestrator re-ran the file 3 times on the generation fix; one run failed in
`test_map_sends_one_batch_call_for_the_typed_targets` with `WorkerFailed: NoMatches("No nodes match
'#credentials-table' on CredentialsScreen()")`. The map form dismisses and `_after_form` refreshes the list
behind it; the load's answer arrives while the credentials screen is being torn down. The old `is_attached`
guard was True at that moment (the screen is still in the stack) while the table widget was already removed,
so `_listed`'s `query_one("#credentials-table")` raised `NoMatches` and failed the worker. This makes the
file flaky in CI, so it is in scope. Ruled (orchestrator DEFAULT): a load's answer that arrives after the
screen's table is gone is dropped — success or failure — with no exception.

**Test (red on the current file first).** `test_a_load_answer_after_the_table_is_gone_is_dropped_without_an_exception`:
a list load is held at the server (`_SlowFirstLoad`), the credentials screen's table is removed while that
load is running, then the load is released; the answer arrives on the torn-down table. The test asserts no
worker failure and `app._exception is None`. On the current file it fails (red):

```text
E  NoMatches: No nodes match '#credentials-table' on CredentialsScreen()
1 failed in 2.19s   # _listed raised on the torn-down table, the worker failed
```

**Guard.** In `tsb_tui/screens/credentials.py`, `_live_table()` (the new helper) returns the screen's table
or None when it is gone (`self.query("#credentials-table")` empty). The generation check stays first; then
`_listed` (:214) and `_failed` (:225) drop the answer when `_live_table()` is None, with no exception. A
detached screen has no table, so the old `is_attached` guard in `_listed` is subsumed by this check.

**Green.** After the guard the test passes (`1 passed in 1.79s`). The whole file, five consecutive runs (run
alone, NO_COLOR unset, pinned test venv):
```text
12 passed in 17.98s
12 passed in 17.79s
12 passed in 18.11s
12 passed in 18.17s
12 passed in 17.37s
```
`make lint` once: `MAKE_LINT_EXIT=0`; ruff from the pinned venv on `tsb_tui tests bench tools`:
`All checks passed!` `RUFF_EXIT=0`.

## Remaining concern — the 30 s bound (left as it is)

The original #19 test's single refresh request can exceed its 30 s bound when the in-process fixture server
is starved under heavy full-file CPU contention. The two ruled fixes (generation, table-gone guard) drop
stale and torn-down answers; they do not speed a slow server, so the original test still depends on the
30 s bound as a load-tolerant wait — the answer arrives when the server gets CPU. The bound is left as it
is, per instruction. Running the file alone it passes five times in a row; under contention the first run
once timed out at "the label once unlocked" after ~47 s.

