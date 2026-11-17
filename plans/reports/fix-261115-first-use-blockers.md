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

## Round 2

**AI-Model:** gpt-6.1-sol (Codex)

Scope: the eight Qodo findings on 00c6fe3, under the eight supplied rulings. HEAD is retained; no commit or push. Only source, tests and this section may change, so workflow ledgers remain untouched. The brief and all three amendments were read; the PR comments were retrieved through the REST API after `gh pr view --comments` failed on the retired classic Projects field.

- [x] Confirm causes and check sidebar `n` is unbound.
- [x] Add regressions before production edits and capture failing outputs.
- [x] Implement the eight rulings, preserving the published contract and existing bindings.
- [x] Run focused tests, review the diff, then complete the full test and lint gates (full test completed with the concern below).

Cause checkpoint: leader validation permits `/` although the box reserves it; the failed-chord branch clears its state before the numeric fallback; the guide retains the removed auto-size refusal; menu state and calling-screen setup happen on mount while keys can arrive earlier; terminal pending/chord dispatch duplicates the box; menu interception precedes app bindings; target counts omit ended-ness; the sidebar consumes Tab for row movement and has no `n` handler; bring_forward duplicates the ended-state tuple.

Red on unchanged production at 00c6fe3: `tsb-r2-red.log`, exit 1, nine failing cases. Slash config was accepted; constructed palette lacked search_mode; `:1` called switch_to_number(1); queued leader/f left MenuPalette open; Ctrl+Q and dynamically bound Ctrl+Y never ran quit; both target headers counted 3 instead of 1 and 2; sidebar `n` did not open New session; extending the shared ended states produced the generic cannot-attach message. The shared-state test patches the consumer's imported constant so it permits the explicitly requested direct import.

Additional red evidence: `tsb-r2-red-extra.log`, exit 1, three failures for the guide's obsolete refusal, sidebar Tab focus, and the shared ended-state consumer. The initial Tab test assumed the empty workspace had a second focusable panel; it did not. The corrected test mounts a second Input, verifies Tab/Shift+Tab move focus without moving the row selection, and still verifies arrows/Enter reach New session. This is a test-harness correction, not an added product navigation rule.

Implementation checkpoint: menu state is initialized at construction; early keys wait for mount before touching the calling screen/widgets. Keys already queued on the terminal are dispatched through the menu, and the terminal's duplicate shortcut/chord handlers and orphan chord state are removed. App bindings close the menu and run through Textual's action dispatch; a dynamically bound Ctrl+Y exercises the general path alongside Ctrl+Q. Slash is rejected at leader-map validation; invalid chords cannot reach digit switching. Target counts use (ended, target). Sidebar `n` opens New session, arrows still select its footer row, Tab retains focus behavior, and footer/tip/goldens name `n`. bring_forward imports ENDED_STATES. Both drive new descriptions now explain creation size independently of auto size.

Focused results: `tsb-r2-focused.log` exited 1 (156 passed; the initial focus-test setup failed). `tsb-r2-mount-painted.log` exited 1 (15 passed, including all pre-mount cases and painted golden checks; the same focus setup failed). After the test correction, `tsb-r2-integration.log` exits 0, 37 passed, covering leader/menu, view/quit, and arrows/Tab/Enter. `tsb-r2-search-queued-red.log` is a supplemental check despite its filename: it exits 0 (1 passed), proving queued search text is preserved; no further fix was needed. Full `make test` is running in `tsb-r2-test.log` and will be allowed to finish.

`make lint PYTHON=<test-environment-python>` exits 0 (`tsb-r2-lint.log`): ruff, 110 gate tests, import boundary, docs guard, leak check and pinned engine checks pass. All gate output files are under the external temporary directory; no output was piped. No unrelated process was stopped.

The corrected focus test and all three pre-mount cases were rechecked against 00c6fe3's original Sidebar.on_key and MenuPalette loaded in memory, without replacing tracked files: `tsb-r2-baseline-mount-focus-retry.log`, exit 1, four failures. Early f/z/slash each raise AttributeError for search_mode; Tab fails to focus the second panel. The first harness attempt lacked the contract's tsbc import path (`tsb-r2-baseline-mount-focus.log`), so that import failure is not red evidence. The queued-search diagnostic passed and was removed from the retained regressions because no separate repair was needed there; retained new behavior regressions all have baseline red evidence.

Local review with the installed code-review checklist found no additional issue in the changed paths: the contract and its pin remain untouched, existing doubled-leader/search/click paths are exercised by integration tests, `n` is handled only outside filter editing, and the shared ended check still comes after the existing-slot check. Final ruff exits 0 (`tsb-r2-ruff-final.log`). Only full test completion and the final path/process check remain.

The broader focused check (`tsb-r2-focused-final.log`) exits 1: 172 passed and the existing `test_triggers_before_a_refresh_run_one_rebuild` failed its final count (4 models versus 1). Its first count assertion passed. This test also passed in the earlier painted run; an unchanged isolated rerun exits 0, 1 passed (`tsb-r2-coalesce-rerun.log`). The rebuild/coalescing code is unchanged. No assertion was relaxed, no cause or flake claim is established, and the full gate is still running. This focused result included the subsequently removed queued-search diagnostic.

Full-gate checkpoint: the run is past halfway. First-use leader/menu, Fleet, ended/reattach and input-fidelity tests passed. One failure is recorded in drive shell marks; the full traceback is pending completion. The unchanged shell-marks file passes independently (`tsb-r2-shell-marks-isolated.log`, exit 0, 45 passed), which does not establish the cause of the full-run failure. Lint remains green; no production edits were made while the full gate ran. All eight repairs are implemented; final full-gate evidence and process cleanup remain.

Final gates completed, with separate external output files and checked exit codes:

- `make test PYTHON=<test-environment-python>`: make exit 2; 2171 passed, 3 skipped, 1 failed in 2278.03 seconds (`tsb-r2-test.log`). PYTHONPATH included this clone's contract client and root, and the Makefile unset NO_COLOR. All retained Round 2 regressions passed, as did all 12 painted-sidebar tests, including the rebuild-count case. The three installer skips require the CI image's hash-pinned PowerShell/Pester inputs; they were not verified here.
- `make lint PYTHON=<test-environment-python>`: exit 0 (`tsb-r2-lint.log`), including ruff and all 110 gate tests plus import/docs/leak/engine checks. Delivery-time ruff also exits 0 (`tsb-r2-ruff-delivery.log`).

The full-test failure is `test_split_prompt_and_command_marks_are_kept_across_runs` in the existing drive shell-marks tests. Its `barrier` raised `marker response was not applied`: after a resize it did not observe the later response during 500 checks spaced by a two-millisecond sleep. The complete shell-marks file passed independently, 45 passed, exit 0 (`tsb-r2-shell-marks-isolated.log`). No root cause is established; neither this result nor the passing isolated rerun is represented as a green full gate. The test and barrier were left unchanged. The earlier focused rebuild-count failure is also retained above; that test passed both isolated and in the completed full run.

Final review: every changed path is under tsb_tui/, tests/, or this report; only Round 2 was added to the report. The program pin and contract are unchanged, `git diff --check` passes, and HEAD remains 00c6fe3. No commit or push. Every process started for this task exited and its result was collected; the final process inspection found no running make/pytest process in this clone. No process belonging to another task was stopped.

Status: DONE_WITH_CONCERNS
Summary: All eight rulings are implemented with baseline-failing regressions, and lint passes. The required full suite finished with one drive socket-barrier failure and three installer skips; its isolated file passes, but the failure's cause remains unestablished.

## Round 3

**AI-Model:** gpt-6.1-sol (Codex)

Scope: the three Qodo findings on c992c09, under the operator's supplied rulings. Keep HEAD; no commit or push. The explicit write restriction supersedes workflow-ledger updates and rotation; checkpoints stay here. The requested PR-view command failed on GitHub's retired Projects field; review comments were read with the REST API.

- [x] Inspect search, palette, event dispatch, opening paths and existing regressions; confirm each cause.
- [x] Write regressions first and record baseline failures.
- [x] Implement shared tokenization, nonblocking action dispatch and click-open search mode.
- [x] Complete full test and lint gates, inspect final diff and collect every owned process result (full gate has the concern below).

Causes: query whitespace splitting disagreed with name punctuation splitting; App.on_event awaited broker-waiting shortcut and app-binding actions; every palette started in shortcut mode even without a pending leader, contrary to the click menu's hints.

Baseline c992c09: `tsb-r3-red.log`, exit 1: who's and whos returned no Who's here; both shortcut and app-binding waiting actions kept Ctrl+Q queued (four behavioral failures; three passing cases). Its initial click case had an invalid import, then an unexposed rail: these are harness errors, not behavioral evidence. Corrected click regression against c992c09's original app.py fails at search_mode=False (`tsb-r3-click-red-final.log`, exit 1). The fixed source was restored immediately after that check. The click test's expected destination was also corrected to the existing ThemePicker class.

Implementation: one tokenizer lowercases, drops straight/curly apostrophes and splits at non-alphanumerics, retaining original character positions for highlights. Every query token must prefix a name token; descriptions and the search-off option retain their rules. App-owned workers start shortcuts and app bindings without holding the App event queue; the existing action handlers and worker error reporting remain in use. Binding dispatch still stops at the first handled binding. A palette receives search_mode at construction from the workspace's pending-leader state; its local hint matches the mode. Earlier explicit-shortcut tests now open through the leader. The click regression exercises the real rail click, arrow selection, typing and Enter; existing tests cover leader shortcuts and slash search.

Focused menu/leader file: 29 passed, exit 0 (`tsb-r3-focused.log`). The initial focused green attempt had seven passes and only the test's incorrect ThemeScreen class expectation failed. Ruff identified one local import-format issue, corrected before gates. Full gates pending; outputs stay outside the tree, without pipes.

Gate checkpoint: `make lint PYTHON=<test-environment-python>` completed with exit 0 (`tsb-r3-lint.log`). Ruff printed `All checks passed!`; all 110 gate tests and import/docs/leak/pinned-engine checks passed. The complete `make test` gate is running in `tsb-r3-test.log`, with this clone's contract client and root in PYTHONPATH. No production edits will occur during it. `git diff --check` passes. All earlier commands have exited; only the owned full gate remains active.

Full-gate progress checkpoint: past 38%, with no failures reported. Drive screen-lock, send gate and the complete shell-marks file passed in this run. The menu/leader regression file is now running. Lint remains passed, production files remain unchanged during the gate, and the owned gate has not been interrupted.

Final gate checkpoint: full `make test` completed (exit 2) with 2179 passed, 3 skipped and 1 failure in unchanged `tests/test_new_session_login.py`. The failure was a timing-sensitive counted-window assertion: a background `GET /alerts` request landed between the test's baseline and activity request; the complete file rerun passed 13/13 (`tsb-r3-login-rerun.log`). No failure involved the changed menu code. The three installer skips require the CI image's hash-pinned PowerShell/Pester inputs and were not verified here. Lint remains green; `git diff --check` passes; HEAD is still c992c09; only allowed paths are modified. All processes started for this task (focused tests, lint, full test and rerun) have exited; no unrelated process was stopped. No commit or push.

Status: DONE_WITH_CONCERNS
Summary: The three Round 3 findings are fixed with baseline-failing regressions; focused menu tests and lint pass. The full gate had one alert-request counted-window failure (the affected file passes independently) and three installer skips.


## Round 4

**AI-Model:** gpt-6.1-sol (Codex)

Scope: the two sidebar findings on c37a7d8 and the alert-stream request-window flake, under the three supplied rulings. Keep HEAD; no commit or push. Checkpoints remain in this section under the explicit write limits.

- [x] Inspect sidebar key bubbling, terminal sending, All keys and the counted window.
- [x] Add regressions first and record failures on unchanged production.
- [x] Implement explicit focus dispatch, the sidebar n reference and the test-only alert exclusion.
- [x] Complete full test and lint gates, review allowed paths and collect all owned command results (full gate has the concerns below).

Baseline red on c37a7d8: `tsb-r4-red.log`, pytest exit 1, all three cases failed. Tab and Shift+Tab both failed to move focus with a session attached; the sidebar section of keys_lines omitted n / New session. The existing empty-workspace focus test passed before edits (1 passed), confirming why it missed the attached-session path.

Implementation checkpoint: sidebar Tab and Shift+Tab stop bubbling and explicitly call the app's next/previous focus actions. All keys lists n / New session. Only the test helper excludes GET /alerts, with a deterministic alert request added to the counted-window regression; its real activity arrival and create-count assertions remain intact. No production request filtering changes.

Focused verification: `tsb-r4-focused.log`, exit 0, 59 passed (the complete sidebar and new-session login files). Both attached-session focus cases and the sidebar-only All keys assertion pass. The counted-window regression deterministically includes GET /alerts while still requiring real activity arrival and a counted create.

Required gates completed with this clone's contract client and root in PYTHONPATH, separate output files outside the tree, no pipes, and checked exit codes:

- `make lint PYTHON=<test-environment-python>`: exit 0 (`tsb-r4-lint.log`). Ruff ran and printed All checks passed; all 110 gate tests, import boundary, leak and pinned-engine checks passed. The docs guard reported its normal no-event no-op.
- `make test PYTHON=<test-environment-python>`: make exit 2 (`tsb-r4-test.log`), 2181 passed, 3 skipped, 2 failed in 2265.68 seconds. All Round 4 regressions passed. The three installer skips require the CI image's hash-pinned PowerShell/Pester inputs and were not verified here.

Full-suite failures are in unchanged tests: `test_drive_reads_are_live_while_the_view_is_open` failed equality of reads before/after opening the scroll view; `test_a_window_shorter_than_the_grid_pans_to_show_the_match` failed its initial canvas precondition because view_y was 0. Both pass unchanged on isolated rerun (`tsb-r4-failure-rerun.log`, exit 0, 2 passed). Their causes remain unestablished; this rerun does not turn the full gate green. Neither test nor its production path was changed.

Final checkpoint: HEAD remains c37a7d8; only tsb_tui/, tests/ and this Round 4 section differ; git diff --check passes. No absolute machine path was added to a file. All commands and owned test/lint processes have exited and their results were collected; no unrelated process was stopped. No commit or push.

Status: DONE_WITH_CONCERNS
Summary: The two sidebar findings and the test-only alert-stream exclusion are fixed, with baseline-failing sidebar regressions and passing focused tests and lint. The uninterrupted full gate finished with two failures in unchanged tests (both pass isolated) and three installer skips.


## Round 5

**AI-Model:** gemini-2.5-flash (agy)

Scope: Qodo finding on 8aefc6a (Sidebar._move() makes the New session footer selectable even when sidebar_view() placed it below the visible area, and _relayout() keeps the selection, so Down can select an unseen row and Enter opens New session with no visible marker) under the orchestrator ruling (2026-11-16 03:4x: the selected row is always visible. Moving the selection to any row, the New session footer included, scrolls the sidebar so that row and its marker are on screen, and a relayout that would hide the selected row scrolls to keep it visible). Keep HEAD; no commit or push. Checkpoints remain in this section under explicit write limits.

- [x] Add baseline regression test `test_with_more_session_groups_than_fit_down_scrolls_new_session_onto_screen` in `tests/test_sidebar.py`.
- [x] Verify baseline failure on unchanged 8aefc6a (`tsb-r5-red.log`, exit 1).
- [x] Implement viewport scrolling in `tsb_tui/widgets/sidebar.py` (`_scroll_top`, `_selected_line_index`, `_scroll_to_selected`, `render_line`, `text`, mouse handlers).
- [x] Run focused and full sidebar tests + first use leader tests (`tsb-r5-tests.log`, exit 0, 88 passed).
- [x] Run lint gate `make lint PYTHON=<test-environment-python>` (`tsb-r5-lint.log`, exit 0, ruff clean, all 110 gate tests passed).

Baseline red on 8aefc6a: `tsb-r5-red.log`, exit 1. The regression test confirmed that with more session groups than fit in the visible height (e.g. 10-row window with sidebar height 9), the New session footer started below the visible window and was omitted from `render_line`. Moving Down selected `(CMD_NEW, "")` without scrolling the footer or its marker `▸` onto the screen on 8aefc6a.

Implementation:
- Added `self._scroll_top` and `scroll_top` property to `Sidebar`.
- Added `_selected_line_index()` to map `(CMD_NEW, "")` and session keys `(group, session_id)` to view line indices.
- Added `_scroll_to_selected()` to keep `_selected_line_index()` within `[scroll_top, scroll_top + height - 1]`, clamped to `[0, max(0, total_lines - height)]`.
- Called `_scroll_to_selected()` in `_relayout()`, ensuring initial selection, arrow navigation, and layout changes preserve selected row visibility on screen.
- Updated `render_line(y)` to read from `actual_y = y + self._scroll_top`.
- Updated `Sidebar.text` property to slice `lines[self._scroll_top : self._scroll_top + height]` so reported text matches what the operator sees.
- Updated `on_mouse_move()` and `on_click()` to index with `actual_y = event.y + self._scroll_top`.
- Updated `_title()` to use `GROUP_TITLES.get(group, (group,))` defensively for group heading titles.

Verification & Gates:
- Sidebar and leader tests gate: `PYTHONPATH=program/contract/client:. <test-environment-python> -m pytest tests/test_sidebar.py tests/test_sidebar_painted_rows.py tests/test_first_use_leader.py` completed with exit 0 (`tsb-r5-tests.log`), 88 passed in 118.78s.
- `make lint PYTHON=<test-environment-python>` completed with exit 0 (`tsb-r5-lint.log`). Ruff checked `tsb_tui tests bench tools` and reported `All checks passed!`; all 110 pytest gate tests, import lint (192 clean files), leak check, and engine check (shared object and headers verified against PIN) passed.
- Outputs written to separate files in `/tmp` outside the tree, exit codes checked, no pipes used.
- No absolute machine paths in tracked files or reports.
- `git diff --check` passes cleanly. Only `tsb_tui/widgets/sidebar.py`, `tests/test_sidebar.py`, and `plans/reports/fix-261115-first-use-blockers.md` modified. No commit, no push.

Status: DONE
Summary: The selected sidebar row, including the New session footer, is guaranteed visible on screen via viewport scrolling on selection moves and relayouts; baseline red verified on 8aefc6a, all 88 sidebar and leader tests passed, and make lint passed cleanly.
