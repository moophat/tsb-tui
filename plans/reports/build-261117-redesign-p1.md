# P1 build — theme, hints and Menu

**AI-Model:** gpt-6.1-sol
**Baseline:** `196e7bb`
**Status:** BLOCKED — implementation complete; required clean full-suite gate not achieved

**Current result (Amendment 6):** The one full gate completed without restart: **2 failed, 2231 passed, 3 allowed skips**, make exit **2**. Both failures passed unchanged in a subsequent focused run (**2 passed**, exit **0**), so they are intermittent; their cause is not established. Lint (including Ruff and 110 auxiliary tests) and contract-check both exit **0**. All P1 and Amendment 6 acceptance cases passed, including the two assertions required to remain unchanged. The clean full-suite requirement remains unmet. Historical checkpoints below are superseded by this result.

## Breakdown and checkpoint

- [x] Read the complete brief, plan and P1 phase; apply Amendments 1–3.
- [x] Add behavioral tests before implementation and capture failures on the integrated hotfix.
- [x] Implement Nord/frost, computed surfaces, contrast, F1, O3 keys and styled hints in authorized paths.
- [x] Capture two-size Menu SVGs and independently assert focus, style and geometry.
- [x] Finish six-baseline visual review and focused/regression/lint/contract runs.
- [x] Complete the one-shot Amendment 5 full-suite gate; enumerate every failure and await all processes.
- [x] Apply Amendment 6 hint/test-transition rulings.
- [ ] Obtain the required clean full-suite gate (two intermittent failures in the completed run).

The initial 18 tests produced 16 failures and two passes on unchanged baseline code. The expanded 35 tests produced 33 failures and two passes in an isolated archive of `196e7bb`, then all 35 passed on the working branch. Baseline failures were behavioral (F1 absent, incorrect list action/default/surface/contrast), never missing imports.

Visual review then found inherited hidden Menu container visibility. The original geometry check admitted a zero-size rectangle; strengthened tests failed at both sizes on that exact condition before its fix. The implementation now makes the actual frame visible and explicitly repaints the ASCII search icon. Final focused tests passed (35/35); all six SVGs were visually reviewed with librsvg/Cairo. Their frame, icon and columns are visible at both sizes.

## Scope

Amendment 1: FleetView key mapping only. Amendment 2: the F1 fidelity exception and the rebinding hint assertion only. Amendment 3: only `TerminalScreen._fleet_view_keys_target()` was edited in terminal.py; no sibling hint method was changed. No workflow, plan, program, vendor or frozen-input file was edited. No commit or push.

## Known follow-ups (left unchanged)

- `screens/chrome_dialogs.py`: All keys still advertises target-list `n` as create target and `c` for New session. The `c` alias remains valid, but the `n` target help is stale.
- `screens/new_session.py`: TARGET_REQUIRED still embeds the legacy leader spelling.
- Amendment 4 authorizes all replaced leader/colour assertions; those updates are enumerated below. The two regression suites subsequently passed 46/46.

## Computed surfaces

| Theme | Variant | Background | Minimum-step surface | Ratio |
|---|---|---|---|---|
| contrast | dark | #000000 | #1a1a1a | 1.206596461 |
| dracula | dark | #282a36 | #343746 | 1.207672644 |
| nord | dark | #2e3440 | #39404f | 1.201519352 |
| paper | light | #fcfbf9 | #ece6da | 1.201695130 |
| terracotta | dark | #0a0a0a | #1f1f1f | 1.201126783 |

`theme.surface_colour` bisects only HSL lightness in the ruled direction and returns the first passing quantized RGB. An independent fine scan plus bisection in `test_surface_is_the_minimum_lightness_step` checks minimality, direction and deterministic output. The theme layer computes it on load and selection; explicit higher-precedence role overrides retain their existing precedence. Palettes and packaged fallback roles are synchronized. Nord keeps exactly #88c0d0; supporting selected surfaces were darkened to keep that accent above 4.5:1. Text/frame/border roles meet 4.5:1 across chrome backgrounds; selected/disabled Menu rendering is checked separately. Terminal ANSI is untouched.

## Newly exposed driver follow-up

`tests/test_discoverability.py::test_an_agent_reaches_every_enabled_action_by_click_alone[200x60]` passes on an isolated `196e7bb` archive (24.14 s) and fails on the working branch (11.19 s). A temporary diagnostic plugin outside the tree identified the exact mismatch: the driver reads Menu at column 187 while attach layout transiently reports `cols 1-1 of 80`; by mouse dispatch the row has settled, Fleet fits again, and Menu is at column 168, while the click remains at 190. Input was not screen-lock blocked. This is a regression exposed by longer labels, not an obsolete assertion.

`drive/runner.py:Driver.click_label` reads rendered label coordinates before Pilot's asynchronous click can settle layout. Amendment 5 authorizes this method and requires the regression fixed. The final refresh/re-read/one-retry correction and its tests are recorded below; discoverability now passes all sizes. No hint ordering was changed.

## Verification history and current gate

Earlier focused tests passed 35/35 and regression suites passed 46/46 after Amendment 4. Earlier lint passed Ruff, 110 auxiliary tests, import/docs/leak checks and engine verification; contract-check passed. Those historical temporary logs were lost in the reboot. Interrupted full-suite runs are not completed gates.

The authoritative Amendment 5 full suite completed once without restart; its result and subsequent lint/contract/focused/regression exits are recorded below. New red/green driver evidence survives in this run's external logs. Fixtures/test doubles only; no real broker/sshd or release-terminal proof.

## Authorized regression assertion changes

Under Amendment 2, only `tests/test_leader_and_menu.py` changed; `tests/test_first_use_leader.py` stayed untouched. Additional Amendment 4/5 assertion changes are listed individually below.

- `test_every_key_but_the_leader_reaches_the_session`: the old `NEVER_TAKEN` entry required F1 bytes `ESC O P`. F1 was removed from that table, retaining every other key and byte expectation. Added assertions require Menu after F1 with no extra server bytes, the same Login screen after F1 before attachment, and the same Lock screen after F1 after locking, again without extra server bytes. The existing assertion that ordinary keys open no screen remains intact.
- `test_keys_leader_map_rebinds_a_letter_and_the_old_letter_runs_nothing`: the waited-for hint changed from `^\ q Detach` to `Ctrl+\ q Detach`. Amendment 4 subsequently updates the negative old-letter check to the same ruled spelling, as listed below.

## Reproduction commands and external output

`P1_PYTHON` denotes the interpreter specified by the dispatch brief; it is a notation here to keep machine-specific paths out of repository files. Every gate used NO_COLOR unset and PYTHONPATH set to the clone's contract client plus clone root. Output was redirected directly to a separate file in the external temporary directory, never piped. The names below refer to files in that directory.

| Command (interpreter normalized as above) | Output file | Result |
|---|---|---|
| `env -u NO_COLOR "$P1_PYTHON" -m pytest tests/test_redesign_keys.py tests/test_redesign_contrast.py -p no:cacheprovider` on baseline archive | `tui-p1-red-expanded.txt` | 33 failed, 2 passed; expected exit 1 |
| Same focused command, final behavior (explicit baseline capture flag only for reviewed capture) | `tui-p1-gate-focused.txt` | 35 passed; exit 0 |
| `env -u NO_COLOR "$P1_PYTHON" -m pytest tests/test_first_use_leader.py tests/test_leader_and_menu.py -p no:cacheprovider` | `tui-p1-gate-regressions.txt` | 45 passed, 1 failed; exit 1 |
| `env -u NO_COLOR make test PYTHON="$P1_PYTHON"` | `tui-p1-gate-test.txt` | interrupted; historical |
| `env -u NO_COLOR make lint PYTHON="$P1_PYTHON"` | `tui-p1-gate-lint.txt` | Ruff + 110 tests + repository checks; exit 0 |
| `env -u NO_COLOR ruff check tsb_tui tests bench tools` | `tui-p1-gate-ruff.txt` | All checks passed; exit 0 |
| `env -u NO_COLOR make contract-check` | `tui-p1-gate-contract.txt` | exit 0 |

The complete full-suite run has snapshot updates disabled, so it compares the committed SVG files. No new skip or xfail was introduced. No real broker/sshd, deployment or release-terminal proof was used or claimed.

## Amendment 4 checkpoint

The lane limit ended the prior full-suite run at 38%; it is not a completed gate. No process still holds its output file. Assertion-only updates follow Amendment 4; pure rendering tests with explicitly supplied legacy leader inputs retain their matching expectations.

- `tests/test_leader_and_menu.py:152`: `assert screen.pump is None and "^\\ d Detach" in screen.status.text` → `assert screen.pump is None and "Ctrl+\\ d Detach" in screen.status.text`
- `tests/test_leader_and_menu.py:373`: `assert "^\\" not in hit.help, f"{action.id} has no key but its help names one"  # fails today` → `assert "Ctrl+\\" not in hit.help, f"{action.id} has no key but its help names one"  # fails today`
- `tests/test_leader_and_menu.py:399`: `assert "^\\ d Detach" not in screen.status.text` → `assert "Ctrl+\\ d Detach" not in screen.status.text`
- `tests/test_ended_and_reattach.py:143`: `assert "c New session here" in row and "^\\ f Fleet" in row and "Esc close" not in row` → `assert "c New session here" in row and "Ctrl+\\ f Fleet" in row and "Esc close" not in row`
- `tests/test_quiet_prompt.py:226`: `assert "^\\ t Take back" in screen.status.text` → `assert "Ctrl+\\ t Take back" in screen.status.text`
- `tests/test_snapshots.py:739`: `assert "^\\ t Take back" in status, status` → `assert "Ctrl+\\ t Take back" in status, status`
- `tests/test_snapshots.py:741`: `assert "^\\ a Answer: kill the session?" in status, status` → `assert "Ctrl+\\ a Answer: kill the session?" in status, status`
- `tests/test_mouse_and_chrome.py:425`: `assert on[2] == "≡ Menu ^\\ · every action, searchable", on` → `assert on[2] == "≡ Menu Ctrl+\\ · every action, searchable", on`
- `tests/test_mouse_and_chrome.py:426`: `assert on[3].startswith("▦ Fleet ^\\ f"), on` → `assert on[3].startswith("▦ Fleet Ctrl+\\ f"), on`
- `tests/test_mouse_and_chrome.py:427`: `assert on[4].startswith("1 rtr-core-01 · you type") and on[4].endswith("^\\ 1"), on` → `assert on[4].startswith("1 rtr-core-01 · you type") and on[4].endswith("Ctrl+\\ 1"), on`
- `tests/test_mouse_and_chrome.py:428`: `assert on[5] == "+ New session ^\\ c", on` → `assert on[5] == "+ New session Ctrl+\\ c", on`
- `tests/test_mouse_and_chrome.py:432`: `assert ascii_set[2].startswith("= Menu ^\\") and ascii_set[3].startswith("# Fleet ^\\ f"), ascii_set` → `assert ascii_set[2].startswith("= Menu Ctrl+\\") and ascii_set[3].startswith("# Fleet Ctrl+\\ f"), ascii_set`
- `tests/test_view_and_quit.py:84`: `assert f"^\\ {action.key}" in text and action.label in text, f"{action.id} is missing from All keys"` → `assert f"Ctrl+\\ {action.key}" in text and action.label in text, f"{action.id} is missing from All keys"`
- `tests/test_view_and_quit.py:85`: `assert "^\\ :q" in text and "^\\ ?" in text and "^\\ F" in text and "^\\ x" in text` → `assert "Ctrl+\\ :q" in text and "Ctrl+\\ ?" in text and "Ctrl+\\ F" in text and "Ctrl+\\ x" in text`
- `tests/test_view_and_quit.py:90`: `assert detach and detach[0].strip().startswith("^\\ D"), detach` → `assert detach and detach[0].strip().startswith("Ctrl+\\ D"), detach`
- `tests/test_view_and_quit.py:127`: `assert "^\\ :q" in _body(app)` → `assert "Ctrl+\\ :q" in _body(app)`
- `tests/test_config.py:437`: faint text `#858585` → Nord `#b4bcca`.
- `tests/test_canvas.py:211,219`: cursor accent `ACCENT (#e56a4a)` → `#88c0d0`; viewer steel `STEEL (#7aa7d6)` → `#8fc4d3`.

Amendment 4 focused/regression/lint/contract checks completed; its full suite was interrupted, not green.

Amendment 4 gate **focused**: exit **0**; external output `tui-p1-a4-focused.txt`.

Amendment 4 gate **regressions**: exit **0**; external output `tui-p1-a4-regressions.txt`.

Amendment 4 gate **test**: interrupted (recorded exit **2**); external output `tui-p1-a4-test.txt` was lost in reboot.

Amendment 4 gate **lint**: exit **0**; external output `tui-p1-a4-lint.txt`.

Amendment 4 gate **contract**: exit **0**; external output `tui-p1-a4-contract.txt`.

## Amendment 5 checkpoint

The machine rebooted; temporary logs from prior runs are gone. Earlier results above are historical observations, not new completed gates. The Amendment 4 full suite was interrupted; no completed full-suite pass is claimed.

- New `tests/test_drive_click_label.py::test_click_label_follows_a_label_moved_by_pending_refresh` fails before the fix: click uses the pre-refresh position (`[False] != [True]`), exit 1; external output `tui-p1-a5-click-red.txt`. It models Pilot draining pending refresh work before mouse dispatch.
- `Driver.click_label` now awaits Pilot's pending-screen/idle refresh before reading the current screen and label coordinates. No fixed sleep, retry policy, action ordering or other driver behavior was added.
- `tests/test_input_path.py::test_every_key_reaches_the_session_with_the_bytes_a_terminal_sends`: old F1 entry expected `ESC O P` among session bytes → F1 removed from that table, every other key unchanged, then F1 explicitly requires Menu and unchanged received bytes.
- No existing assertion in the new driver unit test was replaced; it is new regression coverage.
- Final targeted tests, full suite, lint and contract: pending. The full suite will run once without restart.

Amendment 5 targeted result: **22 passed**, exit **0**, external output `tui-p1-a5-targeted-final.txt`. Discoverability passes all its parametrized sizes, including 200×60. A single refresh wait alone did not eliminate the integration race (21 passed, 1 failed); the final implementation re-reads after dispatch and retries once only if the label moved on the same screen. The stronger dispatch-movement unit test failed before that correction (`tui-p1-a5-click-dispatch-red.txt`, exit 1) and now passes. The original test expectations in discoverability were not modified.

Final one-shot full-suite run started; output `tui-p1-a5-test.txt`. If interrupted, it is not a completed gate; its last completed file/progress is in that external log.

Final full-suite checkpoint: 50%, model attachment running; discoverability, driver unit, input path, first-use and leader/menu passed. Installer cases reported three skips so far; no failures so far.

Full-suite checkpoint: 66%; three failures in scheme-mapping role-cursor assertions still naming the old terracotta defaults. Amendment 4 authorizes these assertion-only changes in `tests/test_scheme_mapping.py`:
- `test_block_cursor_on_a_reversed_cell_keeps_its_colours`: block background `ACCENT (#e56a4a)` → `#88c0d0`; glyph `ON_ACCENT (#0a0a0a)` → `#1f232b`.
- `test_cursor_role_keeps_tsbs_colours`: block background `ACCENT (#e56a4a)` → `#88c0d0`; glyph `ON_ACCENT (#0a0a0a)` → `#1f232b`; viewer colour `STEEL (#7aa7d6)` → `#8fc4d3`, underline assertion retained.
- `test_cursor_scheme_under_outer_takes_tsb_roles_open_t4`: the same three colour expectations change to Nord; all other assertions unchanged.
The full run is not restarted and retains its failures; a separate scheme-mapping verification will check the final assertions.

Full-suite checkpoint: 73%; two painted-sidebar golden assertions encode the old leader. Amendment 4 assertion-only changes in `tests/test_sidebar_painted_rows.py`:
- `test_the_painted_sidebar_text_matches_live_sessions_at_a_long_list`: expected `golden[str(n)]` → the same golden with header `^\ f    ` replaced by `Ctrl+\ f` (four padding cells consumed; row width unchanged).
- `test_the_selection_moves_over_the_painted_rows_as_before`: expected `golden["14000"]` → the identical header-only replacement. Selection and all other row assertions remain unchanged; the golden file is untouched.

Snapshot colour expectations under Amendment 4: `tests/test_snapshots.py` shared expected `TERRACOTTA_SURFACE` changes `#191919` → computed `#1f1f1f`. This changes only the replaced surface expectation in `test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome` (12 size/scheme cases), `test_the_picker_over_T1c_shows_the_panel_and_the_previewed_scheme` (panel and rail colour checks), and `test_the_picker_on_the_phone_over_T1c` (rail coverage); geometry and content checks stay unchanged.

## Unresolved acceptance and scope decisions

At 83% the full run is still progressing; no restart. The final snapshot rerun has **88 passed, 1 failed** (exit 1, `tui-p1-a5-snapshots.txt`). Scheme mapping has **9 passed** (exit 0, `tui-p1-a5-scheme.txt`); painted sidebar has **12 passed** (exit 0, `tui-p1-a5-sidebar.txt`).

- `test_f12_the_status_row_in_each_state[owner-view-only-120x40]` requires Take back at 120 columns. The longer ruled leader causes existing `_fit` policy (drop hints before shortening left-hand text) to drop it. No policy change was specified in P1. Options: shorten session text before dropping this high-priority hint, or explicitly revise its width-dependent acceptance. Implementation stops at this decision; no ordering, priority or assertion was changed to hide it.
- Existing `tests/test_target_form.py` cases still press `n` for target creation, whereas O3 now assigns New session to `n` and Add target to `a`. Amendments 2/4/5 authorize F1, leader and theme assertion replacements, not these key-sequence replacements. Tests remain unchanged; the ruled `a` path passes P1 coverage.
- Stale target-key help in `screens/chrome_dialogs.py` and legacy leader text in `screens/new_session.py` remain the Amendment 3 follow-ups, unchanged.

Amendment 5 gate **test**: exit **2**; external output `tui-p1-a5-test.txt`.

Amendment 5 gate **lint**: exit **0**; external output `tui-p1-a5-lint.txt`.

Amendment 5 gate **contract**: exit **0**; external output `tui-p1-a5-contract.txt`.

## Completed one-shot full-suite gate

`make test` completed: **41 failed, 2190 passed, 3 skipped in 2927.43 seconds**, make exit **2**. External output: `tui-p1-a5-test.txt`. No restart. The three skips require CI's pinned PowerShell/Pester inputs; no skip/xfail was added by P1.

Twenty recorded failures have subsequently passing coverage after authorized colour/header assertion corrections: three scheme-mapping cases (9/9 file pass), two painted-sidebar cases (12/12 pass), and fifteen surface/picker snapshot cases (88/89 snapshot cases pass). The full gate itself remains failed; no clean full-suite pass is claimed. Twenty-one recorded failures remain unresolved:
- One 120-column Take back snapshot and one 160-column clickable status-row action-set check (Scroll disappears): longer leader versus existing hint-dropping policy. Resolve the policy/width acceptance before changing it.
- Twelve target-form cases still use the old `n` target-create sequence; these are unchanged outside the amended assertion scope.
- Two saved-theme cases still expect terracotta for an unsaved/invalid-choice fallback; Nord is now the default.
- Five picker cases assume the original terracotta default or editable file-provided surface. Two test a move to Nord that is now a same-theme move; the edited-surface test now conflicts with derived surface semantics. Rewriting those fixtures/transitions would exceed the authorized assertion-only replacements. It must preserve the original test intent rather than merely make a vacuous assertion pass.

All 41 names recorded by the completed full gate:
- `tests/test_saved_theme.py::test_a_bad_entry_toasts_the_file_and_the_field_and_stays_in_the_file`
- `tests/test_saved_theme.py::test_two_logins_keep_their_own`
- `tests/test_scheme_mapping.py::test_block_cursor_on_a_reversed_cell_keeps_its_colours`
- `tests/test_scheme_mapping.py::test_cursor_role_keeps_tsbs_colours`
- `tests/test_scheme_mapping.py::test_cursor_scheme_under_outer_takes_tsb_roles_open_t4`
- `tests/test_sidebar_painted_rows.py::test_the_painted_sidebar_text_matches_live_sessions_at_a_long_list`
- `tests/test_sidebar_painted_rows.py::test_the_selection_moves_over_the_painted_rows_as_before`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[65x30-dracula]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[65x30-nord]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[65x30-solarized-dark]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[65x30-solarized-light]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[65x30-catppuccin-mocha]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[65x30-one-half-dark]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[120x40-dracula]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[120x40-nord]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[120x40-solarized-dark]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[120x40-solarized-light]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[120x40-catppuccin-mocha]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[120x40-one-half-dark]`
- `tests/test_snapshots.py::test_f12_the_status_row_in_each_state[owner-view-only-120x40]`
- `tests/test_snapshots.py::test_the_picker_over_T1c_shows_the_panel_and_the_previewed_scheme[120x40]`
- `tests/test_snapshots.py::test_the_picker_over_T1c_shows_the_panel_and_the_previewed_scheme[200x60]`
- `tests/test_snapshots.py::test_the_picker_on_the_phone_over_T1c`
- `tests/test_status_row.py::test_a_click_on_the_pill_opens_whos_here_and_a_click_on_a_hint_runs_its_action`
- `tests/test_target_form.py::test_create_edit_and_delete_with_confirmation`
- `tests/test_target_form.py::test_duplicate_keeps_every_field_and_core_message`
- `tests/test_target_form.py::test_keys_follow_auth_me_permission[True]`
- `tests/test_target_form.py::test_invalid_port_or_empty_tag_is_refused_without_write[port-0]`
- `tests/test_target_form.py::test_invalid_port_or_empty_tag_is_refused_without_write[port-65536]`
- `tests/test_target_form.py::test_invalid_port_or_empty_tag_is_refused_without_write[port-abc]`
- `tests/test_target_form.py::test_invalid_port_or_empty_tag_is_refused_without_write[tags-a,,b]`
- `tests/test_target_form.py::test_invalid_port_or_empty_tag_is_refused_without_write[tags- ]`
- `tests/test_target_form.py::test_hot_policy_refusal_keeps_the_form`
- `tests/test_target_form.py::test_target_edit_refusal_keeps_the_form`
- `tests/test_target_form.py::test_saved_target_is_selected_after_it_leaves_the_query`
- `tests/test_target_form.py::test_saved_target_selection_survives_relayout_before_fleet_flush`
- `tests/test_theme_picker.py::test_the_leader_and_T_open_the_picker_and_the_session_stays_visible_beside_it`
- `tests/test_theme_picker.py::test_moving_the_lists_previews_the_look_at_once`
- `tests/test_theme_picker.py::test_the_status_note_names_the_chrome_theme_being_previewed`
- `tests/test_theme_picker.py::test_using_an_edited_user_theme_again_reapplies_its_css_colours`
- `tests/test_theme_picker.py::test_a_real_chrome_switch_refreshes_only_through_textuals_watcher`

Final lint **exit 0** (Ruff passed, 110 auxiliary tests passed, import/docs/leak and engine checks passed). Final contract-check **exit 0**. Final focused and regression reruns passed 35/35 and 46/46, respectively.

Amendment 5 gate **focused**: exit **0**; external output `tui-p1-a5-focused.txt`.

Amendment 5 gate **regressions**: exit **0**; external output `tui-p1-a5-regressions.txt`.

## Final checkpoint

Done: P1 implementation, Amendment 5 click-label race with behavioral red/green unit evidence, F1 input-path expectation, reviewed six SVGs, all authorized assertion changes listed old → new above. The completed one-shot full gate is red; it is not a clean gate for the final tree after assertion corrections.

Verified: targeted driver/input/discoverability **22 passed**; final focused **35 passed**; final regressions **46 passed**; scheme **9 passed**; painted sidebar **12 passed**; snapshots **88 passed / 1 failed**; lint/Ruff and contract **exit 0**; tracked and untracked scope audit, new source/report machine-path check and `git diff --check` passed. No new skip/xfail, commit or push. No edits to workflow, plans, program, vendor or frozen inputs.

Left: operator/orchestrator ruling on preserving high-priority hints at fixed widths versus revising width-dependent acceptance, and authorization for remaining test fixture/key-transition changes that exceed the existing assertion-only amendments. No further implementation decision was chosen. All commands started in this continuation have exited; no task process remains running.


## Amendment 6 checkpoint

Amendment 6 supersedes the unresolved decisions above. Target creation uses `a`; unsaved/invalid saved choices expect Nord by name; picker moves to Nord start from terracotta; the edited-user-theme test now edits background and checks CSS repaint. A new independent-reference test reloads the same user theme after changing only surface white → black and requires the same background-derived surface both times.

Status layout shortens only session names with a trailing ellipsis before dropping hints in configured priority order; state phrases retain their existing whole-word survival rules. Reattachment reasons are not session names and retain their original whole-phrase removal. The required 120-column owner Take back case and untouched 160-column clickable-action check pass. The new shortening-before-lowest-hint test was red before the implementation and passes afterward. Existing full-name assertions now admit only session-name ellipses; state facts, REC, width and separators remain checked.

Focused evidence: `tui-p1-a6-status3.txt` had 78 passes and two setup/expectation failures; after correcting the attaching expectation (unchanged behavior) and the new test's user-directory setup, `tui-p1-a6-status4.txt` had 79 passes and one setup failure. `tui-p1-a6-surface.txt` then passed both the final user-surface test and invalid saved-theme test. Final focused run is in `tui-p1-a6-final-focused.txt`; full gate not started yet.

- [x] Apply four rulings and preserve both named acceptance assertions.
- [x] Verify changed status-row expectations and the new edited-surface behavior.
- [x] Finish focused verification on the final implementation.
- [x] Run make test once to its end, then lint and contract-check (full test exit 2).
- [x] Record final evidence and per-test old → new appendix.

## Complete changed-test old → new index

This index includes all changed lines in tracked test functions, including fixture/key changes as well as assertions. Minus is old; plus is new. Unchanged intent and assertions are omitted. New test files are listed separately below.

### tests/test_canvas.py

`test_the_typists_cursor_is_a_filled_accent_block_and_a_viewers_a_steel_underline` old → new:

```diff
-            assert typist.bgcolor.get_truecolor()[:3] == ACCENT
+            assert typist.bgcolor.get_truecolor()[:3] == (0x88, 0xC0, 0xD0)
-            assert viewer.color.get_truecolor()[:3] == STEEL
+            assert viewer.color.get_truecolor()[:3] == (0x8F, 0xC4, 0xD3)
```

### tests/test_config.py

`test_text_faint_passes_on_the_surface_the_chrome_draws_it_on` old → new:

```diff
-    assert cfg["theme.text_faint"] == "#858585"
+    assert cfg["theme.text_faint"] == "#b4bcca"
```

### tests/test_ended_and_reattach.py

`test_esc_closes_the_dialog_and_the_row_keeps_the_end_with_c_and_fleet` old → new:

```diff
-                assert "c New session here" in row and "^\\ f Fleet" in row and "Esc close" not in row
+                assert "c New session here" in row and "Ctrl+\\ f Fleet" in row and "Esc close" not in row
```

### tests/test_input_path.py

`module fixtures / helpers` old → new:

```diff
-    ("f1", b"\x1bOP"),
```

`test_every_key_reaches_the_session_with_the_bytes_a_terminal_sends` old → new:

```diff
+            await pilot.press("f1")
+            from tsb_tui.app import MenuPalette
+
+            await wait_until(pilot, lambda: isinstance(app.screen, MenuPalette), "F1 Menu")
+            assert serve_app.received(client_id) == want, "F1 sent bytes to the session"
```

### tests/test_leader_and_menu.py

`test_the_leader_works_while_the_attach_is_still_starting_and_other_keys_go_to_the_app` old → new:

```diff
-            assert screen.pump is None and "^\\ d Detach" in screen.status.text
+            assert screen.pump is None and "Ctrl+\\ d Detach" in screen.status.text
```

`module fixtures / helpers` old → new:

```diff
-    ("escape", b"\x1b"), ("tab", b"\t"), ("f1", b"\x1bOP"), ("f9", b"\x1b[20~"),
+    ("escape", b"\x1b"), ("tab", b"\t"), ("f9", b"\x1b[20~"),
```

`test_every_key_but_the_leader_reaches_the_session` old → new:

```diff
+            login = app.screen
+            await pilot.press("f1")
+            await pilot.pause()
+            assert app.screen is login
+            await pilot.press("f1")
+            await wait_until(pilot, lambda: isinstance(app.screen, CommandPalette), "F1 Menu")
+            assert serve_app.received(client_id) == wanted
+            await pilot.press("escape", LEADER, "L")
+            from tsb_tui.screens.lock import LockScreen
+
+            await wait_until(pilot, lambda: isinstance(app.screen, LockScreen), "lock")
+            locked = app.screen
+            await pilot.press("f1")
+            await pilot.pause()
+            assert app.screen is locked
+            assert serve_app.received(client_id) == wanted
```

`test_the_menu_names_a_key_only_where_there_is_one_mutes_what_is_unavailable_and_searches_with_a_slash` old → new:

```diff
-                    assert "^\\" not in hit.help, f"{action.id} has no key but its help names one"  # fails today
+                    assert "Ctrl+\\" not in hit.help, f"{action.id} has no key but its help names one"  # fails today
```

`test_keys_leader_map_rebinds_a_letter_and_the_old_letter_runs_nothing` old → new:

```diff
-            await wait_until(pilot, lambda: "^\\ q Detach" in screen.status.text, "the row to print the new letter")
-            assert "^\\ d Detach" not in screen.status.text
+            await wait_until(pilot, lambda: "Ctrl+\\ q Detach" in screen.status.text, "the row to print the new letter")
+            assert "Ctrl+\\ d Detach" not in screen.status.text
```

### tests/test_mouse_and_chrome.py

`test_tooltips_name_each_glyphs_word_and_key_and_follow_the_glyph_set_and_the_knob` old → new:

```diff
-    assert on[2] == "≡ Menu ^\\ · every action, searchable", on
-    assert on[3].startswith("▦ Fleet ^\\ f"), on
-    assert on[4].startswith("1 rtr-core-01 · you type") and on[4].endswith("^\\ 1"), on
-    assert on[5] == "+ New session ^\\ c", on
+    assert on[2] == "≡ Menu Ctrl+\\ · every action, searchable", on
+    assert on[3].startswith("▦ Fleet Ctrl+\\ f"), on
+    assert on[4].startswith("1 rtr-core-01 · you type") and on[4].endswith("Ctrl+\\ 1"), on
+    assert on[5] == "+ New session Ctrl+\\ c", on
-    assert ascii_set[2].startswith("= Menu ^\\") and ascii_set[3].startswith("# Fleet ^\\ f"), ascii_set
+    assert ascii_set[2].startswith("= Menu Ctrl+\\") and ascii_set[3].startswith("# Fleet Ctrl+\\ f"), ascii_set
```

### tests/test_quiet_prompt.py

`test_the_leader_and_t_takes_the_keyboard_back_from_an_agent` old → new:

```diff
-                assert "^\\ t Take back" in screen.status.text
+                assert "Ctrl+\\ t Take back" in screen.status.text
```

### tests/test_saved_theme.py

`module fixtures / helpers` old → new:

```diff
-    TERRACOTTA_SURFACE,
```

`test_a_bad_entry_toasts_the_file_and_the_field_and_stays_in_the_file` old → new:

```diff
-        assert app.cfg["theme.name"] == "terracotta", f"the look in force did not stay: {app.cfg['theme.name']!r}"
+        assert app.cfg["theme.name"] == "nord", f"the look in force did not stay: {app.cfg['theme.name']!r}"
-        assert any(f == TERRACOTTA_SURFACE and x < 4 for f, x in fills), "the rail is not terracotta's surface"
+        assert any(f == app.cfg["theme.surface"] and x < 4 for f, x in fills), "the rail does not use the active theme surface"
```

`test_two_logins_keep_their_own` old → new:

```diff
-            assert app.cfg["theme.name"] == "terracotta" and app.cfg["theme.term.scheme"] == "tsb-default", \
+            assert app.cfg["theme.name"] == "nord" and app.cfg["theme.term.scheme"] == "tsb-default", \
-            assert app.cfg.chrome.id == "terracotta", \
-                f"chrome.id is still {app.cfg.chrome.id!r}, not terracotta"
+            assert app.cfg.chrome.id == "nord", \
+                f"chrome.id is still {app.cfg.chrome.id!r}, not nord"
-            terracotta = parse_theme(packaged_dir("themes") / "terracotta.toml")
+            default_theme = parse_theme(packaged_dir("themes") / "nord.toml")
-            assert app.cfg["theme.surface"] == terracotta.roles["surface"], \
-                f"theme.surface is {app.cfg['theme.surface']!r}, not terracotta's"
+            assert app.cfg["theme.surface"] == default_theme.roles["surface"], \
+                f"theme.surface is {app.cfg['theme.surface']!r}, not nord's"
+            await attached(pilot, app, sid, wait_until)
```

### tests/test_scheme_mapping.py

`test_block_cursor_on_a_reversed_cell_keeps_its_colours` old → new:

```diff
-            assert block.bgcolor.get_truecolor()[:3] == ACCENT, "the block must show the accent it was given"
-            assert block.color.get_truecolor()[:3] == ON_ACCENT
+            assert block.bgcolor.get_truecolor()[:3] == (0x88, 0xC0, 0xD0), "the block must show the accent it was given"
+            assert block.color.get_truecolor()[:3] == (0x1F, 0x23, 0x2B)
```

`test_cursor_role_keeps_tsbs_colours` old → new:

```diff
-            assert block.bgcolor.get_truecolor()[:3] == ACCENT
-            assert block.color.get_truecolor()[:3] == ON_ACCENT
+            assert block.bgcolor.get_truecolor()[:3] == (0x88, 0xC0, 0xD0)
+            assert block.color.get_truecolor()[:3] == (0x1F, 0x23, 0x2B)
-            assert viewer.color.get_truecolor()[:3] == STEEL and viewer.underline
+            assert viewer.color.get_truecolor()[:3] == (0x8F, 0xC4, 0xD3) and viewer.underline
```

`test_cursor_scheme_under_outer_takes_tsb_roles_open_t4` old → new:

```diff
-            assert block.bgcolor.get_truecolor()[:3] == ACCENT, "outer must fall back to the role accent"
-            assert block.color.get_truecolor()[:3] == ON_ACCENT
+            assert block.bgcolor.get_truecolor()[:3] == (0x88, 0xC0, 0xD0), "outer must fall back to the role accent"
+            assert block.color.get_truecolor()[:3] == (0x1F, 0x23, 0x2B)
-            assert viewer.color.get_truecolor()[:3] == STEEL
-
-    asyncio.run(_check())
+            assert viewer.color.get_truecolor()[:3] == (0x8F, 0xC4, 0xD3)
+
+    asyncio.run(_check())
```

### tests/test_sidebar_painted_rows.py

`test_the_painted_sidebar_text_matches_live_sessions_at_a_long_list` old → new:

```diff
-            assert text == golden[str(n)], f"the painted sidebar changed at {n} records"
+            assert text == golden[str(n)].replace("^\\ f    ", "Ctrl+\\ f"), f"the painted sidebar changed at {n} records"
```

`test_the_selection_moves_over_the_painted_rows_as_before` old → new:

```diff
-            assert screen.sidebar.text == golden["14000"]
+            assert screen.sidebar.text == golden["14000"].replace("^\\ f    ", "Ctrl+\\ f")
```

### tests/test_snapshots.py

`test_t1_the_terminal_with_three_sessions_open` old → new:

```diff
-                    assert "rtr-core-01" in status and "▦ Fleet" in status, status
+                    assert ("rtr-core-01" in status or any(
+                        f"  {'rtr-core-01'[:n]}…  " in status for n in range(len("rtr-core-01")))) and "▦ Fleet" in status, status
```

`module fixtures / helpers` old → new:

```diff
-TERRACOTTA_SURFACE = "#191919"
+TERRACOTTA_SURFACE = "#1f1f1f"
-@_f12("you-type", "you type", "rtr-core-01  ● live  ● REC")
+@_f12("you-type", "you type", "● live  ● REC")
-@_f12("quiet-prompt", "you type", "rtr-core-01  ● live  ● REC")
+@_f12("quiet-prompt", "you type", "● live  ● REC")
```

`test_f12_the_status_row_in_each_state` old → new:

```diff
+            if size[0] >= 120 and state_id in ("you-type", "quiet-prompt"):
+                assert "rtr-core-01" in status or any(
+                    f" {"rtr-core-01"[:n]}… " in status for n in range(len("rtr-core-01"))), status
-                assert "^\\ t Take back" in status, status
+                assert "Ctrl+\\ t Take back" in status, status
-                assert "^\\ a Answer: kill the session?" in status, status
+                assert "Ctrl+\\ a Answer: kill the session?" in status, status
```

### tests/test_status_row.py

`test_you_type_reads_as_a_filled_pill_the_session_its_state_and_rec` old → new:

```diff
-    assert text.startswith("‹ you type ›  rtr-core-01  ● live  ● REC")
+    assert text.startswith("‹ you type ›  …  ● live  ● REC")
```

`test_view_only_while_an_agent_types_in_your_session_says_typing_takes_it` old → new:

```diff
-    assert text.startswith("‹ view only ›  fixture-peer types · you own it: typing takes it · rtr-core-01")
+    assert text.startswith("‹ view only ›  fixture-peer types · you own it: typing takes it · …")
```

`test_view_only_for_a_client_who_may_type_and_for_one_whose_right_is_read_only` old → new:

```diff
-    assert "‹ view only ›  fixture-user types · rtr-core-01" in text
+    assert "‹ view only ›  fixture-user types · …" in text
-    assert "fixture-user types · your right is read-only · rtr-core-01" in _text(never)
+    assert "fixture-user types · your right is read-only · …" in _text(never)
```

`test_nobody_types_names_who_put_the_keyboard_aside` old → new:

```diff
-    assert text.startswith("‹ nobody types ›  fixture-user put the keyboard aside · rtr-core-01")
+    assert text.startswith("‹ nobody types ›  fixture-user put the keyboard aside · …")
```

`test_a_take_over_reads_both_ways` old → new:

```diff
-    assert _text(owner).startswith("‹ you type ›  rtr-core-01  ● live  ● REC  you took the keyboard from fixture-peer")
+    assert _text(owner).startswith("‹ you type ›  …  ● live  ● REC  you took the keyboard from fixture-peer")
-    assert _text(peer).startswith("‹ view only ›  fixture-user took the keyboard (owner) · rtr-core-01")
+    assert _text(peer).startswith("‹ view only ›  fixture-user took the keyboard (owner) · …")
```

`test_hints_drop_from_the_right_by_priority_and_the_menu_is_the_last_to_go` old → new:

```diff
-    assert normal == ["scroll", "size", "end", "detach", "who"], "all seven fit at 200; Fleet and the Menu outlast these"
-    assert compact == ["who", "fleet"], "what is left at 79 columns keeps the Menu to the end"
+    assert normal == ["scroll", "size", "end", "detach"], "all seven fit at 200; Fleet and the Menu outlast these"
+    assert compact == ["detach", "who", "fleet"], "what is left at 79 columns keeps the Menu to the end"
```

`test_a_different_priority_drops_a_different_hint_first` old → new:

```diff
-    assert _drop_order(state, False, hint_priority=reversed_priority) == ["scroll", "fleet"]
+    assert _drop_order(state, False, hint_priority=reversed_priority) == ["size", "scroll", "fleet"]
```

`test_the_phone_row_is_compact_and_carries_the_pan_range_and_the_menu` old → new:

```diff
-    assert row.startswith("‹you type›  rtr-core-01")
+    assert row.startswith("‹you type›  …")
```

`test_when_even_the_menu_leaves_no_room_for_the_words_the_name_goes_whole_not_the_menu` old → new:

```diff
-    assert "a-very-long" not in row and "…" not in row
+    assert "a-very-long" not in row and "…" in row
```

### tests/test_status_row_narrow.py

`_row` old → new:

```diff
+    return row
```

`module fixtures / helpers` old → new:

```diff
+
+
```

`_expanded_session` old → new:

```diff
+def _expanded_session(row: str) -> str:
+    # Only an isolated session-name prefix may end in an ellipsis; state words stay whole.
+    for length in range(len(SESSION) - 1, -1, -1):
+        row = row.replace("  " + SESSION[:length] + "…  ", "  " + SESSION + "  ")
+        row = row.replace(" · " + SESSION[:length] + "…  ", " · " + SESSION + "  ")
```

`test_at_65_columns_the_key_fact_rec_and_menu_stay_and_the_session_name_goes` old → new:

```diff
-    assert "…" not in row, f"{state_id}: a phrase was cut with an ellipsis: {row!r}"
+    assert "…" not in _expanded_session(row), f"{state_id}: a phrase was cut with an ellipsis: {row!r}"
```

`test_wide_rows_are_the_full_words_in_the_order_they_always_were` old → new:

```diff
-        assert SESSION in row, row
-        assert "…" not in row.replace("End…", ""), row
+        assert SESSION in _expanded_session(row), row
+        assert "…" not in _expanded_session(row).replace("End…", ""), row
```

`test_the_row_at_120_and_200_columns_is_unchanged` old → new:

```diff
-        assert _row(make(), width).startswith(WIDE[state_id]), (state_id, width)
+        assert _expanded_session(_row(make(), width)).startswith(WIDE[state_id]), (state_id, width)
```

`test_the_narrowest_rows_keep_the_pill_and_the_menu_whole` old → new:

```diff
-        assert "…" not in row and not _dangling(row), row
+        assert "…" not in _expanded_session(row) and not _dangling(row), row
```

### tests/test_target_form.py

`test_create_edit_and_delete_with_confirmation` old → new:

```diff
-            await pilot.press("n")
+            await pilot.press("a")
```

`test_duplicate_keeps_every_field_and_core_message` old → new:

```diff
-            await pilot.press("n")
+            await pilot.press("a")
```

`test_keys_follow_auth_me_permission` old → new:

```diff
-            await pilot.press("n")
+            await pilot.press("a")
```

`test_invalid_port_or_empty_tag_is_refused_without_write` old → new:

```diff
-            await pilot.press("n")
+            await pilot.press("a")
```

`test_hot_policy_refusal_keeps_the_form` old → new:

```diff
-            await pilot.press("n")
+            await pilot.press("a")
```

`test_target_edit_refusal_keeps_the_form` old → new:

```diff
-            await pilot.press("n")
+            await pilot.press("a")
```

`test_saved_target_is_selected_after_it_leaves_the_query` old → new:

```diff
-            await pilot.press("n")
+            await pilot.press("a")
```

`test_saved_target_selection_survives_relayout_before_fleet_flush` old → new:

```diff
-            await pilot.press("n")
+            await pilot.press("a")
```

### tests/test_theme_picker.py

`test_the_leader_and_T_open_the_picker_and_the_session_stays_visible_beside_it` old → new:

```diff
-            assert any(f == TERRACOTTA_SURFACE and x < 4 for f, x in fills), (
+            assert any(f == NORD_SURFACE and x < 4 for f, x in fills), (
```

`test_moving_the_lists_previews_the_look_at_once` old → new:

```diff
+        app.apply_look("terracotta", "tsb-default")
```

`test_the_status_note_names_the_chrome_theme_being_previewed` old → new:

```diff
+        app.apply_look("terracotta", "tsb-default")
```

`test_using_an_edited_user_theme_again_reapplies_its_css_colours` old → new:

```diff
-        (themes_dir / "my-lab.toml").write_text('[theme]\nsurface = "#112233"\n', encoding="utf-8")
-        gen = _session(serve, wait_until)
-        app, screen, pilot = await gen.__anext__()
-        assert app.cfg["theme.surface"] == "#112233", "the active user theme's surface did not load"
+        (themes_dir / "my-lab.toml").write_text('[theme]\nbg = "#112233"\n', encoding="utf-8")
+        gen = _session(serve, wait_until)
+        app, screen, pilot = await gen.__anext__()
+        assert app.cfg["theme.bg"] == "#112233", "the active user theme's background did not load"
+        old_surface = app.cfg["theme.surface"]
-        assert any(f == "#112233" and x >= 66 for f, x in fills), "the panel is not the theme's surface"
-        (themes_dir / "my-lab.toml").write_text('[theme]\nsurface = "#445566"\n', encoding="utf-8")
+        assert any(f == app.cfg["theme.surface"] and x >= 66 for f, x in fills), "the panel is not the theme's surface"
+        (themes_dir / "my-lab.toml").write_text('[theme]\nbg = "#445566"\n', encoding="utf-8")
-        assert app.cfg["theme.surface"] == "#445566", "Use did not reload the edited role"
+        assert app.cfg["theme.bg"] == "#445566", "Use did not reload the edited role"
-        assert any(f == "#445566" and x >= 66 for f, x in fills), (
+        assert any(f == app.cfg["theme.surface"] and x >= 66 for f, x in fills), (
-        assert not any(f == "#112233" and x >= 66 for f, x in fills), "the panel still shows the old surface"
+        assert not any(f == old_surface and x >= 66 for f, x in fills), "the panel still shows the old surface"
```

`test_a_real_chrome_switch_refreshes_only_through_textuals_watcher` old → new:

```diff
+        app.apply_look("terracotta", "tsb-default")
```

### tests/test_view_and_quit.py

`test_all_keys_lists_every_actions_key_from_the_table_and_follows_a_rebinding` old → new:

```diff
-        assert f"^\\ {action.key}" in text and action.label in text, f"{action.id} is missing from All keys"
-    assert "^\\ :q" in text and "^\\ ?" in text and "^\\ F" in text and "^\\ x" in text
+        assert f"Ctrl+\\ {action.key}" in text and action.label in text, f"{action.id} is missing from All keys"
+    assert "Ctrl+\\ :q" in text and "Ctrl+\\ ?" in text and "Ctrl+\\ F" in text and "Ctrl+\\ x" in text
-    assert detach and detach[0].strip().startswith("^\\ D"), detach
+    assert detach and detach[0].strip().startswith("Ctrl+\\ D"), detach
```

`test_all_keys_about_and_settings_open_from_the_table_and_say_what_they_are` old → new:

```diff
-                    assert "^\\ :q" in _body(app)
+                    assert "Ctrl+\\ :q" in _body(app)
```

New coverage (no old assertion): `tests/test_drive_click_label.py` tests a moved-label click, `tests/test_redesign_keys.py` covers P1 reachability/style/geometry and shortening order, and `tests/test_redesign_contrast.py` covers defaults/surface minimality/effective contrast plus ignored user-surface edits. Six new SVGs are the reviewed P1 two-size baselines.


## Amendment 6 full-gate start

Final targeted regression set: **47 passed in 151.58 seconds**, exit 0 (`tui-p1-a6-regressions-final.txt`): all F12 states/sizes, saved-login fallback canvas, moved-label unit and all discoverability cases. Required 160-column action set and 120-column owner Take back assertions are unchanged and passed. Focused run had 201 passes and four obsolete-name assertion failures; those four then passed (`tui-p1-a6-t1-green.txt` plus final F12 regression set). Final Ruff preflight exit 0; whitespace check exit 0.

The saved-login canvas assertion now explicitly attaches its fixture session before reading rendered fills; previously it inspected an empty workspace whose old terracotta background happened to equal the terminal default. Its canvas-background assertion itself is unchanged, and is now testing a real canvas.

One full `env -u NO_COLOR make test PYTHON="$P1_PYTHON"` started, output `tui-p1-a6-test.txt`; no restart. Lint and contract-check follow it to completion. All source changes are complete before this run.

Full-gate checkpoint: 26%, no failures reported; discoverability and the moved-label unit passed in the full run. The single make test process is still running and has not been restarted.

Full-gate checkpoint: 57%, no failures reported; first-use leader, leader/Menu, F1 input-path and drive lock/send tests passed. Same uninterrupted make test process; no source edits during the gate.

Full-gate checkpoint: 74%, one failure reported in `test_a_create_that_completes_after_the_login_is_gone_is_discarded`; traceback is pending suite completion. All redesign keys/contrast, saved-theme, scheme mapping, painted-sidebar, first-use/Menu and discoverability cases passed. Same run continues; no restart or source changes.


## Final Amendment 6 result and handoff

| Check | External output | Result |
|---|---|---|
| One full `env -u NO_COLOR make test PYTHON="$P1_PYTHON"` | `tui-p1-a6-test.txt` | **2 failed, 2231 passed, 3 skipped** in 2331.77 s; make **exit 2** |
| The two failures, unchanged, focused `pytest -vv` | `tui-p1-a6-gate-failures.txt` | **2 passed** in 6.87 s; **exit 0** |
| `env -u NO_COLOR make lint PYTHON="$P1_PYTHON"` | `tui-p1-a6-lint.txt` | Ruff ran and passed; **110 passed** auxiliary tests; import/docs/leak/engine checks passed; **exit 0** |
| `env -u NO_COLOR make contract-check` | `tui-p1-a6-contract.txt` | **exit 0** |
| Final P1 regression subset | `tui-p1-a6-regressions-final.txt` | **47 passed**, **exit 0** |

The full run's two failures are:

- `tests/test_drive_commands.py::test_drive_reads_are_live_while_the_view_is_open`: the before/after read dictionaries differed. The default pytest output truncates the dictionary difference, so the exact differing metadata field is unproven. This test file was not changed. Its unchanged focused rerun passed.
- `tests/test_reattach_login.py::test_a_create_that_completes_after_the_login_is_gone_is_discarded`: timed out after 30 s waiting for the spied `_on_create_done` after Login. This test file was not changed. Its unchanged focused rerun passed. The test sets restart before clicking Create; whether the full run actually had a create in flight before logout is not established by this trace.

The three skips are the existing installer tests requiring the CI image's pinned PowerShell/Pester inputs (`test_installers.py` at the two skip sites, one and two cases respectively). No skip or xfail was added. The failed full run is retained as authoritative gate evidence; passing reruns do not replace it. No production or test source changed during or after the full gate.

Done: all six amendments implemented within scope; label-click race red/green unit and discoverability pass; F1 Menu/input fidelity preserved; target-create sequences changed per test; Nord default checks by name; real terracotta-to-Nord picker transitions; edited background reapplied; edited user surface ignored; status name ellipsis before lowest-priority hint; reviewed six SVG baselines; every tracked test change indexed old → new above.

Verified: required owner-view-only 120-column snapshot and 160-column clickable action-set assertions are unchanged and passed in the full run. All snapshot, status-row, target-form, saved-theme, picker, new redesign, first-use/Menu and discoverability cases passed there. Final path/whitespace audit passed. No workflow/plan/program/vendor/frozen-input edits, commit or push. All commands launched by this continuation have exited; none remains running.

Left: required full-suite cleanliness, plus the two previously authorized P1 help-text follow-ups listed above. Options for the gate owner: investigate/stabilize the two intermittent test scenarios and authorize another full gate, or authorize a fresh unchanged full gate to measure recurrence. Neither option was selected or executed: the instruction allowed one full run. Root cause is not claimed, and this phase is not reported as accepted.

## Round 1

**AI-Model:** gpt-6.1-sol
**Status:** IN PROGRESS — Amendments 1–3 resolve the earlier stops; all focused corrections pass, and the final unchanged-tree full gate is running (history and results below).

Read the complete round-1 brief and inspected the PR-head implementation, phase and prior report. The required existing test, `tests/test_drive_click_label.py::test_click_label_follows_a_label_moved_by_pending_refresh`, moves `label_x` from 2 to 20 inside its fake `click`, after the click offset was supplied. It then requires the last click to have used the new position. Two stable `locate()` results before that call necessarily both see the old position. Settling before one click cannot satisfy this fixture; the existing second click is what makes it pass.

The brief requires keeping this unit test while forbidding re-location or another click after dispatch. Stopped under its instruction to report options rather than choose an unspecified resolution:

1. Recommended: authorize moving this fixture's pending layout change into its refresh wait, retaining the test's intended pre-P1 regression coverage; add the separately required action-moves-label test to enforce exactly one click.
2. Keep the existing fixture unchanged and revise the one-click requirement. This would retain the risk finding 2 intends to remove.

Done: brief and targeted context inspection; identified the conflicting test expectations before editing code. Verified: the fixture changes its coordinate only inside `click`, and the current driver re-locates and clicks again afterward. Left: resolution, tests-first red/green work for all seven findings, touched-file tests and the full test/lint/contract gates. No tests or build gates were run; no claim of passing verification. No commit or push. All commands started in this round have exited.

### Amendment 1 continuation — tests first

Amendment 1 resolves the stop above: move the existing label test's layout change into the refresh wait; retain its intent and add the action-moves-label case. Breakdown: (1) add behavioral regressions and record red, (2) implement the seven ruled fixes, (3) run new and touched-file coverage, (4) complete make test and rerun each failure alone once, then lint and contract-check, (5) record evidence and audit paths. Builder scope excludes orchestrator ledgers and phase files.

Old → new: `test_click_label_follows_a_label_moved_by_pending_refresh` changes `label_x` during `finish_refresh` instead of `click`, and requires exactly one click instead of at most two. `test_click_label_action_that_moves_label_fires_once` independently covers movement caused by the click itself. Existing PR-head label test passed (baseline output `tui-r1-baseline.txt`, exit 0). New review coverage is in `tests/test_redesign_round1.py`; red run pending. All test runs use the brief's interpreter, explicit clone-root/contract-client PYTHONPATH and NO_COLOR unset, with direct external temporary output files.

### Implementation checkpoint

Done: workspace routing for Menu shortcuts/number/leader branches; bounded refresh/position settling before one label click; final-layer surface derivation and ignored surface overrides on load/switch; Alerts n hint; minimum four-character name prefix with retry from the original name after each hint drop; corrected help/string; opposite-direction surface bisection and actionable theme refusal. No priority knob is changed: the existing Take back ordering and each keyboard target's own hint order remain in force.

Verified: initial PR-head regressions **13 failed, 1 passed**, exit 1 (`tui-r1-red.txt`); initial fixes **14 passed**, exit 0 (`tui-r1-green.txt`). Added defensive refusal tests and delayed-refresh case fail against the PR-head function bodies (**3 failed**, exit 1, `tui-r1-additional-red.txt`). The retained settled-position test initially had a vacuous coordinate comparison: replacing its boolean with the actual recorded offset proves the pre-P1 function clicks the old coordinate (**1 failed**, exit 1, `tui-r1-pre-p1-red-final.txt`); an earlier probe passed and is retained as `tui-r1-pre-p1-red.txt`, not claimed as red evidence. Latest review/new tests **18 passed**, exit 0 (`tui-r1-new-final.txt`). Function-body baseline probes load source from git into memory and never check out or alter program or production files.

Left: finish assertion updates for the ruled name behavior, run touched-file coverage and complete all gates. Initial broader regression run exposed obsolete bare-ellipsis assertions and a full-name monotonicity assertion incompatible with restoring a longer name after dropping a hint (21 failed, 97 passed, exit 1, `tui-r1-regressions-initial.txt`). Follow-up reached 95 passed with three failures (exit 1, `tui-r1-updated-green.txt`); this overlapped edits, so it is diagnostic only and will be replaced by a completed run on an unchanged tree. Ruff preflight found three new-test import issues, all safely fixed.

Focused checkpoint: all new, redesign, contrast, status-row, configuration, theme-file and chrome-theme cases have passed so far; 429-case run continues without source edits. The binding-context read-only review found no relevant frozen-clause conflict. Status assertions now recognize only >=4-character name prefixes; name survival is checked within each full/compact layout, since crossing the existing 80-column boundary frees cells. The original full-name monotonicity check mistook a shortened name for removal and contradicted the explicitly requested longer-name retry after hint removal. No snapshots were rewritten.

### Focused gate and full-gate start

Final focused coverage: **429 passed in 499.84 s**, exit **0**, output `tui-r1-touched.txt`. Command: `env -u NO_COLOR "$P1_PYTHON" -m pytest tests/test_redesign_round1.py tests/test_drive_click_label.py tests/test_redesign_keys.py tests/test_redesign_contrast.py tests/test_status_row.py tests/test_status_row_narrow.py tests/test_drive_front_and_version.py tests/test_config.py tests/test_theme_files.py tests/test_chrome_themes.py tests/test_theme_picker.py tests/test_saved_theme.py tests/test_view_and_quit.py tests/test_first_use_leader.py tests/test_leader_and_menu.py tests/test_discoverability.py tests/test_fleet_view.py tests/test_new_session_telnet.py -p no:cacheprovider`. PYTHONPATH and output handling as above. All six existing P1 SVGs passed unchanged. No skips in this focused run.

Existing assertion updates, old → new: status-row bare `…` names now assert the ruled retained names/prefixes (`rtr-core…`, `rtr-core-01`, `rtr-…`, `rtr-c…`, `a-ver…`); phone name returns whole after lower-priority hints leave. Compact hint-drop expectation removes Detach because it has already dropped at that sweep's starting width. Narrow-row helper expands only prefixes of >=4 characters; survival counts shortened names and resets at the existing compact breakpoint. The pure name-before-hints example widths change 20/12 → 22/14 to make room for the new five-cell minimum. `test_all_keys_marks_target_edit_permission` changes n/create → a/Add target. New help coverage asserts n/New session, a/Add target and Ctrl+backslash in TARGET_REQUIRED. The Menu action test opens Settings then F1 in a pending-leader context to exercise shortcut mode; ordinary F1 search typing remains covered by unchanged P1 tests.

Done: all seven fixes and focused gate. Verified: new red/green tests, pre-P1 settled-coordinate regression, 429-case unchanged-tree focused run. Left: full make test to completion, isolated failure reruns if any, make lint, make contract-check, final path/whitespace audit. No source edits are planned during the full gate.

Full-gate checkpoint: 2,252 tests collected; through 19%, no failures reported. Discoverability, all three click-label cases, drive accounts and drive-command tests passed. The previous gate's intermittent live-read case passed in this run. No source edits during the full gate; same foreground command continues.

Full-gate checkpoint: through 45%, no failures reported; drive send/screen-lock/waits, first-use leader, Fleet and input-path tests passed. Three existing installer dependency skips appeared; no skip/xfail was added. Same uninterrupted foreground run; no production or test edits.

Full-gate checkpoint: through 62%, no failures reported. Both previously intermittent P1 failures passed in this run, as did all redesign contrast, key and Round 1 review regressions. Three existing installer dependency skips remain the only skips observed. Same uninterrupted command; no source edits.

Full-gate checkpoint: one failure appeared in `tests/test_snapshots.py` around 76%; its identity/traceback is pending suite completion. Run continues through 79% without interruption or source changes. The isolated rerun and diagnosis follow completion, not a restarted or hidden full gate.

### Final Round 1 gate and stop

| Check | External output basename | Result |
|---|---|---|
| New/review acceptance | `tui-r1-new-final.txt` | 18 passed, exit 0 |
| Touched-file and relevant regressions | `tui-r1-touched.txt` | 429 passed in 499.84 s, exit 0 |
| Full `env -u NO_COLOR make test PYTHON="$P1_PYTHON"` | `tui-r1-test.txt` | **1 failed, 2248 passed, 3 skipped** in 2273.88 s, make exit **2** |
| Exact failure rerun alone once | `tui-r1-snapshot-alone.txt` | **1 failed** in 2.04 s, exit **1** |
| `env -u NO_COLOR make lint PYTHON="$P1_PYTHON"` | `tui-r1-lint.txt` | Ruff passed, 110 auxiliary tests passed, import/docs/leak/engine checks passed, exit **0** |
| `env -u NO_COLOR make contract-check` | `tui-r1-contract.txt` | program clean at v0.18.0, exit **0** |

Every gate used the brief's interpreter and explicit clone-root/contract-client PYTHONPATH, with NO_COLOR unset and output redirected directly to its own external temporary file. Exact isolated command: `env -u NO_COLOR "$P1_PYTHON" -m pytest 'tests/test_snapshots.py::test_f12_the_status_row_in_each_state[owner-view-only-120x40]' -vv -p no:cacheprovider`. No test failed solely for a missing Python package. The three skips are existing `tests/test_installers.py` cases at its two PowerShell/Pester dependency skip sites (one and two cases); those checks remain unverified here.

The failure is deterministic in this run: `test_f12_the_status_row_in_each_state[owner-view-only-120x40]` requires `Ctrl+backslash t Take back`, but the row contains the full session name and Menu with no Take back. The minimum name prefix cannot fit alongside all the existing state words and the Take back/Menu hints at this width. Retrying after removing every droppable hint drops Take back itself and then restores the whole name. The original acceptance assertion was not modified. No source or tests changed during or after the full gate.

**Unspecified boundary, STOP:** finding 5 says Take back, Scroll and the keyboard's own actions stay high priority, while name removal waits until every droppable hint is gone. The current design has a priority order but no protected-hint category other than Menu (or a keyboard target's last hint). The brief does not settle whether the named high-priority hints become protected, or whether state text should yield while a minimum name and those hints remain. Options, not selected:

1. Recommended: define Take back, Scroll and the keyboard target's required actions as protected hints that survive the name. After ordinary droppable hints are gone, allow the name to leave whole using the existing body survival order. Confirm the protected set and its relationship to configurable hint priority.
2. Preserve the minimum name alongside high-priority hints by shortening/removing other status phrases first. This changes the existing body survival order and needs an explicit ordering ruling.

Done: six review findings pass their tests; finding 5 has the minimum-prefix and longer-name retry implemented, but is **not accepted** because it loses Take back at the required width. Verified: completed focused/full/lint/contract gates and one isolated failure rerun, no rewritten snapshots, no added skips/xfails. Left: operator/orchestrator ruling on the status priority boundary, its implementation, focused regression verification and a clean full gate. Both previously intermittent P1 failures passed in this full run; the new deterministic failure is not dismissed as timing.

Final path audit: all changed paths are within the round-1 allowlist, including the new review test file; terminal.py changes only the Alerts hint list, new_session.py only TARGET_REQUIRED. No workflow/plan/program/vendor/frozen-input edits, commit or push. Whitespace check passes. All commands started in this continuation have exited; the read-only context sub-agent completed and started no persistent process.

### Amendment 2 — protected-hint capacity stop

**AI-Model:** gpt-6.1-sol

Read Amendment 2 in full. It resolves the prior survival-order boundary: Take back, Scroll and the keyboard target's own actions are protected and never dropped; shorten the name to at least four characters plus ellipsis, drop ordinary hints, then remove the name whole. Before editing, checked the existing keyboard-target hints against the fixed one-row viewport budget. No new production or test edits were made in this continuation.

The Targets keyboard target already supplies eight actionable hints without target.edit: `n New session`, `⏎ New session here`, `/ Query`, `Space Mark`, `l Save login…`, `p Map…`, `Tab Next tab`, `Esc Back`. Their labels/keys total 87 cells, plus 21 existing gap cells: **108 cells**. With target.edit, Add/Edit/Delete add three actionable hints: **128 label/key cells plus 30 gap cells = 158 cells**. At an 80-column window the status row has **76 cells** after the existing four-column rail. At 160 it has **156 cells**, so the editable hint set still exceeds the row even without any left-side text. All measured hints have non-None actions; this is not a question about protecting explanatory text. Removing every inter-hint gap still cannot fit 128 cells into 76.

Verification: a read-only Python calculation called the existing `TerminalScreen._fleet_view_keys_target` with lightweight workspace doubles for both permission states, then counted each emitted actionable key/label plus the actual `GAP`. Output `tui-r1-a2-hint-budget.txt`, exit **0**, with NO_COLOR unset, the brief's interpreter and explicit clone-root/contract-client PYTHONPATH. The calculation confirms the capacity conflict before any implementation. It started no persistent process.

**STOP — unspecified layout:** Amendment 2's never-drop requirement cannot retain all these hints visibly in the existing single status row at the required sizes. The phase's viewport/reachability acceptance and `build_pieces`' exact-width invariant remain binding; silent clipping would make protected actions disappear. Options, not selected:

1. Define compact labels or a paging/overflow interaction for keyboard-target hints, including how the operator reaches every protected action at 80 columns. This needs exact layout/interaction rulings before a builder implements it.
2. Narrow the protected keyboard-target set to an explicitly named subset and permit the remaining hints to drop by a ruled order. This requires an amendment to the all-actions protection rule.

Done: read Amendment 2 and measured both real Targets hint sets before writing. Verified: deterministic cell budgets above; no implementation guess, no snapshot weakening. Left: capacity/layout ruling, Amendment 2 tests-first implementation (including the owner-view-only snapshot old → new), then the full requested gates. No new test/build gate was run because the amendment cannot yet be implemented without choosing a layout. The previous continuation's 429-test focused pass, full-suite deterministic snapshot failure, lint pass and contract-check pass remain recorded above and are not relabelled as Amendment 2 verification. No commit or push; all commands started here exited.


### Amendment 3 — implementation and verification in progress

**AI-Model:** gpt-6.1-sol

The protected hint set is exactly Take back and Scroll. Every ordinary hint, including Menu and focused-list hints, now drops in its existing priority order before the name leaves whole. The existing body survival order applies after ordinary hints are exhausted; no new state-text ranking or compact wording is introduced. Tests first: the new protection regression failed twice on the pre-amendment implementation (`tui-r1-a3-red.txt`, exit 1). Initial diagnostic coverage found obsolete Menu/Scroll and name assertions (44 failed, 71 passed, `tui-r1-a3-diagnostic.txt`, exit 1); one new 120-column assertion incorrectly demanded name removal even when dropping ordinary hints left room, and is corrected to require the surviving shortened name.

Snapshot diagnostic, old → new: `test_f12_the_status_row_in_each_state[owner-view-only-120x40]` previously kept the full name and Menu while dropping Take back; it now keeps Take back and Scroll, removes the name whole and drops Menu. The existing Take back assertion remains; explicit absence of name/Menu is added at 120. Output `tui-r1-a3-snapshot-diagnostic.txt`, exit 1 on the old unconditional Menu assertion. Focused verification including all snapshot cases is running. No commit or push.


Amendment 3 checkpoint: the diagnostic run including snapshots completed with 12 failed/191 passed (`tui-r1-a3-updated.txt`, exit 1). Its remaining assertions expected Menu instead of protected Scroll, expected a name where the minimum no longer fit, or waited for a droppable Menu hint before opening the Menu. Corrected those assertions without dropping the protected-hint, exact-width, state-word, countdown or separator checks. At 65 cells the existing body survival order cannot fit both protected hints plus the owner's full typing fact and REC; the explicit expected row now records that these body words yield after ordinary hints and the name. No new body-word policy was selected.

Broader touched-file run: 6 failed/513 passed in 584.47 s (`tui-r1-a3-touched.txt`, exit 1). All snapshot cases passed; remaining failures were in leader/consumer discoverability tests waiting for droppable Detach/Menu. Detach remapping is checked at 200 instead of 120 cells. Menu click-only reachability uses the existing rail entry when the status hint drops; the driver closes the existing covering sidebar through its existing close control first. It still clicks every enabled action and verifies each effect and no session input. The first reachability follow-up had 2 failed/43 passed (`tui-r1-a3-reachability.txt`, exit 1); isolated rail probes confirmed the sidebar coverage (`tui-r1-a3-rail-diagnostic.txt` and `tui-r1-a3-rail-screen.txt`, exit 1 each). Final reachability run is pending.

Lint preflights caught one missing import blank line and then an unused test import after moving click entry to the rail (`tui-r1-a3-lint.txt` and `tui-r1-a3-lint-final.txt`, make exit 2 each). Both fixed; completed lint passes (`tui-r1-a3-lint-verified.txt`, exit 0), including Ruff, auxiliary tests, import/docs/leak/engine checks. Contract-check passes (`tui-r1-a3-contract.txt`, exit 0), program clean at v0.18.0.


Final reachability coverage: **45 passed in 127.55 s**, exit **0** (`tui-r1-a3-reachability-final.txt`). This covers the new review regressions, leader mapping, click-only Menu actions at all three widths, and every enabled driver Menu action. Only docstrings/comments and an unused import were cleaned while that process was running; no behavior changed. Full `env -u NO_COLOR make test PYTHON="$P1_PYTHON"` started with separate output `tui-r1-a3-test.txt`. Source/tests are frozen for this full gate. Remaining: completed full result, one isolated rerun per failure, final lint/path audit and report status.


Full-gate checkpoint: 2,253 tests collected, through 65%; one failure observed in the mouse/status-hint test, pending completed traceback and required isolated rerun. Review regressions, redesign keys/contrast, click-only discoverability, click-label, final-layer themes and drive tests passed. Three existing PowerShell/Pester installer skips observed. No source/tests edited during the full run. Path audit confirms original report is retained byte-for-byte before the appended Round 1 section, all changed/untracked paths allowed, no machine paths in Round 1, program unchanged.


### Amendment 3 completed full gate — two remaining legacy assertions

Full make test completed: **2 failed, 2248 passed, 3 skipped in 2201.82 s**, make exit **2**, `tui-r1-a3-test.txt`. Exact isolated reruns, once each before changes: `test_a_sidebar_row_and_every_status_hint_acts_on_one_click_at_three_widths` failed again (1 failed in 11.95 s, exit 1, `tui-r1-a3-mouse-alone.txt`); `test_the_banner_carries_the_contracts_notice_whole_at_every_width_and_the_row_stays_one_row` failed again (1 failed in 1.27 s, exit 1, `tui-r1-a3-stale-alone.txt`). Both required the ordinary Menu hint to survive at 65 columns. No production defect or timing explanation was inferred.

Assertions old → new: the mouse test waits for protected Scroll and verifies its presence instead of requiring Menu, while still clicking every available hint and checking each action's effect. The stale-banner test retains the complete contract notice, exact one-row width, stale phrase and chooser/refusal assertions, and now requires Take back and Scroll instead of Menu. These are the only edits after the completed full gate; production remains unchanged. Final two-file coverage and a new complete full gate are pending.


Final two-file coverage: **14 passed in 44.56 s**, exit **0**, `tui-r1-a3-final-two.txt`. Final full make test now runs on the finished source/test tree with output `tui-r1-a3-test-final.txt`; lint and contract-check have separate final outputs. No further source/test edits are planned during this gate.


Final-gate checkpoint: through 55%, one failure in drive-command coverage pending full traceback and the mandated isolated rerun; no source/test edits since gate start. Final lint passes (`tui-r1-a3-lint-gate.txt`, exit 0), including Ruff and 110 auxiliary checks; final contract-check passes (`tui-r1-a3-contract-final.txt`, exit 0), clean v0.18.0. Three existing PowerShell/Pester skips remain unverified. The prior completed full gate and isolated stale-assertion failures remain recorded above, not relabelled as passing.
