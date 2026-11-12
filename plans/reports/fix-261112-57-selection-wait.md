# Saved-target selection diagnosis — #57

**AI-Model:** gpt-6.1-sol
**Status:** DONE

## Steps and outcome

- [x] Trace the save and selection path.
- [x] Add and run a deterministic lost-selection reproduction.
- [x] Implement the ruled ordering fix.
- [x] Validate the regression, target-form suite, related suites, and lint.

## Cause

`TargetFormScreen.save` (`tsb_tui/screens/target_form.py:145`) dismisses with the saved record. The dismissal callback, `TerminalScreen.target_saved` (`tsb_tui/screens/terminal.py:1291`), stores the id in `_target_select`, clears the query, and calls `_fetch_targets`. That calls `app.call_api`; `_run_blocking` returns its result through `call_from_thread` to `_targets_reply`.

The exact selection assignment is `tsb_tui/screens/terminal.py:1143`. It sets `fleet_view.selected`, then immediately clears `_target_select`. `selected_session` is a property returning that tuple's id (`tsb_tui/screens/fleet_view.py:638`). The fleet view still holds its previous model: `_mark_fleet_dirty` schedules an after-refresh rebuild (`tsb_tui/screens/terminal.py:785`). A relayout before that rebuild checks the selection against the old rows and replaces it with the first row (`tsb_tui/screens/fleet_view.py:625`). The later rebuild retains that valid first-row selection. The pending saved id has already been consumed, so subsequent polling does not restore it.

This is a demonstrated product ordering defect, compatible with the CI symptom. The experiment does not prove which event caused a relayout on the historical CI run. Slow execution alone is not sufficient evidence of that particular event.

The earlier worker-registration lead is not established as the cause. Inspection of Textual's installed `work` decorator shows that it calls `run_worker` synchronously. Regardless of worker completion timing, waiting longer for selection cannot recover the ordering reproduced here.

## Deterministic reproduction

`tests/test_target_form.py:223`, `test_saved_target_selection_survives_relayout_before_fleet_flush`, intercepts the real target reply. It temporarily holds the dirty scheduling step, verifies the reply has selected the saved id and cleared the pending selection, and invokes the same relayout used by resize against the old model. It then releases the rebuild and explicitly synchronizes the widgets. The reproduction uses no sleeps. The targets polling timer is stopped to isolate the ordering.

Command:

```sh
env -u NO_COLOR python3 -m pytest tests/test_target_form.py::test_saved_target_selection_survives_relayout_before_fleet_flush -p no:cacheprovider -q
```

Failure:

```text
>           assert view.selected_session == "delayed-target"
E           AssertionError: assert 'alt-screen' == 'delayed-target'
E             - delayed-target
E             + alt-screen
1 failed in 2.92s
```

## Options requiring a decision

1. Publish the updated fleet model before assigning and consuming the saved selection, so every subsequent relayout sees the saved row. Recommended: it addresses the stale-model gap directly.
2. Keep the saved id pending until the deferred widget synchronization can apply it against the updated model. This changes when the pending state is consumed and needs explicit handling for pagination and absent rows.

At the initial stop checkpoint, no product fix or wait-only workaround was made. The other target-form tests already wait for creation selection and edit/delete state; the final query-edit assertion still reads selection after the target model contains the edited row. Changing these waits cannot repair a lost selection, so they were left unchanged pending the product decision.

## Fix and verification

The ruled option 1 was implemented in `TerminalScreen._targets_reply` (`tsb_tui/screens/terminal.py:1142`). After applying a target page, when a saved selection is pending, `_refresh_view_clocks()` publishes the current fleet view model before the saved id is assigned and `_target_select` is cleared. This leaves the existing deferred dirty flush unchanged and does not alter the rail or sidebar publication path. No `fleet_view.py` change was needed.

The deterministic regression now passes: **1 passed in 2.41s**.

Target-form runs (three consecutive plain runs): **13 passed** in **22.97s**, **13 passed** in **22.12s**, and **13 passed** in **23.03s**.

Related fleet/Targets tests: `tests/test_fleet_view.py tests/test_targets_tab.py` — **32 passed in 46.04s**.

`make lint`: exit **0**; **102 passed in 71.11s**, import lint, leak check, and engine checks passed.

Final status: **DONE**.
