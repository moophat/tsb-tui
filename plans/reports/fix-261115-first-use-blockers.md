# First-use blockers — 2026-11-15

**AI-Model:** gpt-6.1-sol (Codex)

## Scope and checks

The brief and Amendment 1 cover session creation, leader dispatch and menu rendering, sidebar persistence and keyboard access, and ended-session grouping (#79, #80, #81). Themes, rail width, the target field and unrelated navigation remain excluded. No commit or push.

- [x] Inspect the contract, entry point, configuration, Windows launchers and installer, creation and leader handling; read issues #79 and #80.
- [x] Add regression tests and record their failures before production changes.
- [x] Implement creation independent of auto size, including shared callers.
- [x] Implement the amended leader behavior.
- [x] Record the Windows setting investigation without changing configuration, per Amendment 1.
- [x] Add failing tests and implement the amended leader/menu and sidebar/Fleet behavior.
- [ ] Check the final diff and run the required test and lint gates, each with separate external output and an inspected exit code.

The explicit task write limits supersede normal ledger/session updates; checkpoints are recorded here.

## Findings

Creation incorrectly treats auto size being off as an absence of a creation size. The contract independently requires session open to carry the creator's size; auto size controls subsequent window resize requests.

The workspace's leader timer clears its pending state before opening the palette. Subsequent keys then reach the palette input instead of the leader bindings.

Windows has no platform-specific profile or auto-size override in the launcher, assembler or installer. Both release 0.7.0 and this branch ship `profile = "default"` and `size.auto_request = true`. The installer writes only the broker address to a new config and preserves existing config. The cause on the operator's PC is unknown, likely a saved setting or the size toggle. Per Amendment 1, configuration remains unchanged; creation is made independent of auto size.

## Verification

The create regression fails on the unchanged branch because the screen remains the new-session dialog (exit 1). The leader regression fails for `f` because it remains in the menu palette and receives the letter; its non-binding `z` case passes (exit 1 overall). Outputs are stored outside the tree.

The first full baseline `make test` was stopped on encountering the original design gap (exit 143); it is not a passing gate. Final full gates follow implementation. `make lint` has not run yet.

Creation regression: `test_create_with_auto_size_off_carries_window_size` failed before the fix because the dialog stayed open (exit 1). After the fix, the size-rules file passes (13 passed, exit 0); connection-choice and drive-target callers pass (11 passed, exit 0). Their old refusal assertions now check creation size across profiles and drive. The fixture's actual recorded `cols` and `rows` fields are checked. Output files outside the tree: `tsb-create-red.log`, `tsb-create-green2.log`, `tsb-create-callers.log`.

Ended grouping regression: `test_ended_sessions_are_absent_from_every_sidebar_group` fails before implementation because killed and terminated records remain in sidebar groups (exit 1; external output `tsb-hotfix-sidebar-model-red.txt`).

Amended leader/menu tests fail before implementation: the menu does not open immediately, an unbound `z` enters search, and an unavailable action remains enabled (3 failed, exit 1; external output `tui-hotfix-leader-red-final.log`). An initial DOM-wait test failure was corrected before recording this behavioral evidence. The implementation now opens immediately, waits indefinitely, preserves bindings and doubled-leader input, and requires `/` to search. Disabled options cannot hold selection, columns are bounded to the box, and a frame separates the menu.

Sidebar regression output `tsb-hotfix-sidebar-red.txt` exits 1 for the old close-on-pick modes, keyboard access and missing Ended heading. `tsb-hotfix-ended-red.txt` exits 1 for the stale-ended-row refusal missing its next step. Focused amended sidebar/model/full-view regressions pass (8 passed, exit 0; `tsb-hotfix-sidebar-regression-green.txt`). Earlier broad integration failures during the concurrent dispatch changes were rerun independently: 2 passed, exit 0 (`tsb-hotfix-leader-integration.log`).

The obsolete device-login auto-size refusal test now verifies recording consent is still required with auto size off, then checks acknowledged creation carries 116 by 39. Focused file: 13 passed, exit 0 (`tsb-device-auto-green.log`). Configuration checks: 77 passed, exit 0 (`tsb-hotfix-config.log`). The accepted delay and close-on-pick keys are explicitly compatibility-only; their values and validation are unchanged.

## Resolved decision

Amendment 1 resolves the earlier stop: lowercase `f` keeps toggling the sidebar, uppercase `F` keeps opening the full view. Leader mode now opens the menu immediately and waits indefinitely; only `/` enters search. The old nonbinding-letter search expectation is superseded.

## Checkpoint

Done: source inspection, creation fix and focused checks; original and amended regression drafts. Creation changes are limited to the shared size helper and orphan refusal branches/imports in its callers. No configuration or packaging changes.

The operator resolved the remaining decisions: the Ended heading stays behind show-ended, off by default; the sidebar starts open and temporarily hides/restores for full Fleet as before. Every other automatic sidebar close is removed. Implementation resumed under those rulings.

Implementation is in final review and full test verification (`tsb-hotfix-make-test.log`). First lint gate exited 2 for four local style/import issues (`tsb-hotfix-make-lint.log`); the corrected full lint gate exits 0 (`tsb-hotfix-make-lint-final.log`), including 110 gate tests and the pinned-engine checks.

Sidebar painting checks pass (12 passed, exit 0; `tsb-hotfix-sidebar-painted.txt`). The long-list corpus remains mixed live/ended; expected sidebar counts and golden text now include only live records and the keyboard footer. The final multirow keyboard test was also run against the original widget and failed (exit 1; `tsb-hotfix-keyboard-main-red.txt`).

The final leader tests pass with color handling matching the Makefile (6 passed, exit 0; `tui-hotfix-leader-final.log`). Before the last fix, Enter in shortcut mode incorrectly closed the menu and ran the highlighted action (exit 1; `tui-hotfix-enter-red.log`); it now gives the same no-shortcut response as other unbound keys. Direct theme checks initially inherited `NO_COLOR`, producing false screenshot failures; clearing it resolves those failures without theme changes. Existing doubled-leader, click and search/Enter tests remain in the integration suite. Narrow-menu click coverage reaches Quit (1 passed, exit 0; `tui-hotfix-discovery65.log`).

The earlier full test run was stopped after production changed during review (exit 143); its partial output is not a passing gate. Final commands use separate output files, never pipes: `make test` with the test environment's Python and `PYTHONPATH` set to the clone root plus its contract client, and `make lint` with the same Python. Final lint exits 0 (110 passed; `tsb-hotfix-final-lint.log`). The final full test gate is running (`tsb-hotfix-final-test.log`). Left: its result and final path/process check. No commit or push.

Completed subprocesses have exited; the full test gate and focused menu checks are still running at this checkpoint. No unrelated process was stopped. Path review shows only allowed source, tests and this report; `git diff --check` passes.

Final-gate checkpoint: the full test run has passed the leader, Fleet view and creation regressions and is past halfway. One screen-lock test failed in that run; its complete file passes independently (45 passed, exit 0; `tsb-hotfix-screenlock-isolated.log`). The full traceback is pending; no cause or flake claim is made yet. Only the full gate remains running.

The first completed full gate exits 2: 4 failed, 2149 passed, 3 skipped (`tsb-hotfix-final-test.log`). Installer skips require the CI image's hash-pinned PowerShell/Pester inputs. Failures: the new ended refusal incorrectly intercepted already-open ended chips (fixed by checking existing slots first); an old chord test expected the superseded menu behavior (updated to require the open menu and explicit Esc); drive idle-lock assertion and Dracula colored-screen timeout (both pass on targeted rerun, no production change or established cause). The chip and sidebar checks pass together: 50 passed, exit 0 (`tsb-hotfix-ended-chip-fix.log`). All view/quit plus the two transient cases pass: 21 passed, exit 0 (`tsb-hotfix-gate-followup.log`).

The final full test and lint gates have been restarted after these changes, using separate external files `tsb-hotfix-final-test2.log` and `tsb-hotfix-final-lint2.log`. Only those owned gate processes remain running. No unrelated process was stopped.

## Amendment 2 inspection — stopped for a design ruling

The external brief already ends with Amendment 2 exactly as requested, so it was not duplicated. Read issue #92 through the GitHub REST API after the requested issue-view command failed on GitHub's deprecated classic-project field. No Amendment 2 implementation or tests were written. The previous lane was terminated during the second full gate at 15%; the operator confirms none of its processes remain. That interrupted gate is not a passing result. The repeated lint gate had exited 0 before termination.

Search has two unresolved behaviors:

1. Event log's description is “what this session's frames told this client, newest last”. Amendment 2 says names or descriptions match, while #92 names Event log among the items that must disappear for “new”. Substring matching would retain it; whole-word matching would remove it; the current fuzzy matcher can retain additional unrelated-looking results. Options: whole-word matching; contiguous substring matching with Event log retained; or retain fuzzy matching with a specified threshold. Recommendation: whole-word matching for complete query words, with the operator specifying whether an unfinished final word supports prefix matching.
2. The existing `menu.search=false` behavior deliberately returns every item and has an explicit regression test. Options: preserve this opt-out and require Amendment 2 filtering when search is enabled; or make search always filter and retain the old key only for config compatibility. Recommendation: preserve the existing opt-out unless the amendment intentionally retires it.

Stopped pending those rulings, per the brief's design-gap instruction. No new gate processes were started in this continuation, no unrelated process was stopped, and no commit or push was made. Existing code and earlier test evidence remain on disk. Full verification remains incomplete.

## Amendments 2 and 3

Amendment 3 resolves both search decisions: every typed word must prefix a word of the action name, case-insensitively; descriptions are excluded. The search-off option retains its previous behavior. The packaged default was already on and remains on.

Red before production changes: `new` retained Event log and unrelated actions (1 failed, `tsb-hotfix-amend3-red.log`); menu-column rendering discarded match-highlight spans (1 failed, `tsb-hotfix-amend2-render-red2.log`). The first rendering-test draft incorrectly required an overflow-hidden Full view hint at 120 columns; that draft failure is not behavioral red evidence. Rendered footer checks show spaces and separate key styles already work in this branch, so no footer production change is needed.

Against main's unchanged search method, injected in memory without changing tracked files, the word-prefix tests fail 3 cases and pass 1 (`tsb-hotfix-search-main-red2.log`, exit 1): fuzzy matching retains unwanted items for the new name-only rules. The first harness attempt omitted the contract's tsbc path and is not valid red evidence. Focused menu integration passes 25 tests (`tsb-hotfix-amend23-green.log`, exit 0); all 4 case/multiword/word-boundary checks pass (`tsb-hotfix-search-words.log`, exit 0).

The menu now preserves Content spans when composing columns, preserving literal spaces and highlighting matched letters. Names remain on a single aligned row with the existing fixed columns; no two-line items remain. Container/input/results/list use the surface background, including dark mode; the scrollbar uses muted foreground rather than the alert/accent color. Footer rendering uses its existing separate hint-key style and leading label spaces.

Final gates are running with separate external outputs `tsb-hotfix-final-test-amend3.log` and `tsb-hotfix-final-lint-amend3.log`, each through the required make target and test-environment Python. No commit or push.

The Amendment 3 full test gate reached 15% without failures, but I accidentally interrupted it during a process check; the interrupted output is `tsb-hotfix-final-test-amend3.log` and is not a passing gate. A retry was started in `tsb-hotfix-final-test-amend3-retry.log` and is likewise pending. Lint passed exit 0.

The retry was also stopped unnecessarily. Both interrupted runs are invalid gate results. A fresh final test gate is running in `tsb-hotfix-final-test-amend3-complete.log`; its result is pending.
