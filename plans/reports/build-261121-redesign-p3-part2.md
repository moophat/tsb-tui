# Build report — redesign P3 part 2 (filter, position, paging, details, Recent, group segment, scale)

Branch `feat/redesign-p3-part2`, baseline `e0c1031`. Rulings 13–18 unblocked the lane; this is the build record through the final session. **Status: DONE_WITH_CONCERNS — the rule-12 test updates and the rule-13 title-only regenerations are applied; every baseline whose diff reaches past a section title is STOP-listed below, not regenerated.**

## Tests written first, red on e0c1031

`tests/test_redesign_lists_filter.py` — 27 tests for rules 1–11 (local filter, page chain, titles, session filter + position, keys, highlight follow, no-match copy, group segment drop order at both sizes, Recent, details screen, 8,000-row scale). Every test recorded red on the baseline; first failing lines in `.lane-logs/red1.log`, `red2.log`. All 27 pass now.

## Implementation (allowed files only)

- `tsb_tui/model/targets.py`: PAGE_LIMIT 500; record store + `record()` accessor; `count_label` with `…`; generation stale rule (`is_current`); `reset()` clears records and steps the generation by 2 so a pre-refresh page can never read as newest.
- `tsb_tui/widgets/sidebar.py`: filter fields (name/host/group/tag/label, rule 1); rule-3 titles (`Targets · i/n of N`, filter form, `–`, drop order quoted → `of` → loading); rule-7 no-match copy after all pages; rule-8 group segment in ruling-17 order (host, open numbers, group, others, type; drop others-first, whole segments only); rule-9 Recent section (own sessions' targets, newest first, deduped, max 3, hidden by filter text, position counts main list); rule-5 keys (`/`, Enter opens selected row, Esc clears filter first, `n`, `?`, `i`, home/end); rule-6 highlight follow for both lists; scale: spans/pairs caches keyed on the model object, bisect lookups, windowed facts walk with greedy fit, budget follows window width.
- `tsb_tui/screens/fleet_view.py`: local targets filter (`filter_targets` + host map via the screen's callable); rule-3 titles for both tabs; rule-15 session position counts the table only (`–` on a Needs-you row); rule-6 relayout at the recorded table place; rule-5 editing keys (Enter opens, Esc clears, `?`, `i`); the structured-query editing path removed with rule 1; the table cache keyed by `(needle, show_ended)` and rows identity so a copy of the model shares it — a keystroke that only moves the selection never re-filters 14,000 records (rule 11).
- `tsb_tui/screens/target_details.py` (new): rule-10 dialog — every TargetRecord field one per line through `as_text`, wrapped whole, Esc/i closes.
- `tsb_tui/screens/terminal.py` (rule 14 only): the page chain in `_targets_reply` under the generation check; the background fetch-all on login/refresh (`_fetch_targets` reset path); `?` and `i` command handling opening the keys/details screens; `targets_loading` model flag.

## Fixes this session

- Fleet view: rule 15 position lookup no longer crashes when the selected row is a Needs-you row (the place stands); fresh view selects the table head; `set_model` keeps the cache for a same-rows swap; the table cache key fix (above) closed `test_a_keystroke_that_only_moves_the_selection_does_not_rescan_the_records`.
- Sidebar: `_build_pairs` in the spans cache; painted/filter caches keyed on model + recent/visible; `_target_painted_end` bisect fix; wide-case scroll limit; marks restored in titles (P2a ruling 2.2/3.3, `title_parts` + `marked_title`).
- Golden file for fleet-view painted rows regenerated via its generator script.

## Rule-12 test updates (final session)

`tests/test_targets_tab.py` (8, rules 1–2):
- `test_the_query_filters_through_the_broker` → `test_the_filter_matches_locally_and_asks_the_broker_nothing` — old: a typed query is sent to the broker; new: the filter matches locally, `GET /targets` asks with `limit`/`cursor` only (rule 1).
- `test_a_bare_word_is_refused_with_the_field_list_and_asks_nothing` → `test_a_bare_word_filters_locally_and_asks_nothing` — old: a bare word is refused with the field list; new: it filters locally (rule 1 retires refusals).
- `test_a_next_page_is_asked_when_the_selection_reaches_the_last_row` → `test_every_page_is_loaded_after_the_view_opens` — old: reaching the last row asks for the next page; new: every page loads in the background at open (rule 2).
- `test_a_poll_asks_the_query_being_fetched_and_a_503_keeps_it` → `test_a_poll_asks_limit_and_cursor_only_and_a_503_shows_the_state` (rules 1, 2).
- `test_an_old_querys_error_never_shows_beside_newer_results` → `test_a_stale_reply_never_overwrites_the_newer_list` (rule 2 generation rule).
- `test_a_reopened_view_never_duplicates_a_page` → `test_a_reopened_view_never_appends_an_old_pages_rows` (rule 2).
- `test_a_poll_never_folds_the_loaded_pages_back` — rewritten: a refresh re-reads from the first page and chains every page; a poll with a page in flight asks nothing (rule 2).
- `test_a_refused_query_keeps_the_shown_list_pageable` — DELETED (rule 1 retires the refused query; no pageable-refusal state exists).
- `test_a_click_on_the_last_loaded_target_asks_for_the_next_page` → `test_a_click_on_the_last_loaded_target_selects_it_while_pages_arrive` (rule 2: no click asks for a page).

`tests/test_redesign_lists.py` (rules 3, 8/17, 9):
- `test_target_row_grammar_two_lines_narrow_one_line_wide[size0/size1]` — old: facts line read by fixed column offsets; new: read through `_target_line_index`/`_target_row_lines`, asserting the group segment, ruling-17 drop order and the Recent header.
- `test_target_order_open_here_first_then_natural_label_order`, `test_open_numbers_on_every_target_row_that_has_them[size0/size1]` — order read through `visible_targets()`; Recent header asserted (rule 9).
- `test_others_on_a_target[size1]` — old: the others word painted on the row; new: ruling-17 drop order leaves the others word out whole at both budgets; the model's `others` mapping asserted (rule 8/17).
- `test_target_rows_show_broker_text_through_the_sanitizer` — owner picture asserted via the model's `others` mapping, not the painted others word (rule 8/17).
- `test_a_narrow_target_facts_line_omits_a_segment_that_does_not_fit` — DELETED (its tooltip half is retired by ruling 17; the whole-segment omission is covered in `tests/test_redesign_lists_filter.py`).
- `test_a_target_tooltip_names_the_whole_row` — DELETED (ruling 17: a tooltip is not required).

`tests/test_redesign_keys.py`: one assertion moves from `query_text` to `filter_text` (rule 1).
`tests/test_fleet_view.py`: title assertions updated to the rule-3 index pair (rule 12).
`tests/test_fleet_view_painted_rows.py`: the TABLE marker in `open_view` updated to the rule-3 title (rule 12); the golden file itself is NOT regenerated (see Stop).

The earlier key-routing stop (`/` after Enter) was resolved by ruling 18: the opening `/` is consumed in `sidebar.py`; `tests/test_sidebar.py` is green (47 passed).

## Rule-13 snapshots — regenerated (ruling 13 widened, 2026-11-22 09:0x)

A baseline is regenerated only where every difference is explained by a part-2 rule (section titles rule 3/15, the no-match copy and its wrapping rules 7/16, the group on target rows and its drop order / long-name ellipsis rules 8/17, the Recent section rule 9). Each regenerated file with the rules that explain it:

- p1 `redesign-p1/menu-workspace-80x24.svg`, `menu-workspace-160x45.svg` (p1 joined the allowed set under ruling 13): title index pairs (rule 3) and group segments on the target rows behind the menu (rule 8; ellipsis of the long name at 160, rule 17).
- p2a `redesign-p2a/panel-focused-80x24.svg`, `panel-focused-160x45.svg`: Targets/Sessions title index pairs (rule 3), group segment on every target row (rule 8), rich-name/telnet label ellipsis to fit the group at the wide budget (rule 17). The hint bar is unchanged in this build's render.
- p2a `redesign-p2a/session-80x24.svg`, `session-160x45.svg`: Recent header + row at the list top (rule 9), group segments (rule 8), Sessions title index pair (rule 3).
- p2b `redesign-p2b/first-focus-80x24.svg`, `first-focus-160x45.svg`: the rule-7 no-match copy (`No targets yet · a add one` → `No target matches 'zz' · Esc clears`) and the filter-form title (rule 3).
- p2b `redesign-p2b/fold-sessions-*`, `detach-hint-*`, `rail-activity-*`, `rail-new-*`, `rail-vault-*`, `token-notice-*` (12 files): group segments on target rows (the telnet type word leaves for the group at 24-inner, rule 8/17), label ellipsis at 160 (rule 17), title index pairs (rule 3).
- p2b `redesign-p2b/fold-targets-80x24.svg`, `fold-targets-160x45.svg`: title index pairs only (rule 3).

The p3 gallery (`tests/snapshots/redesign-p3/`, allowed by line 13): 18 existing snapshots regenerated + 12 new (filter-list, no-match, loading-title, recent-three, details-view, group-row × 2 sizes).

## Determinism fix — the session highlight (rule 6)

`redesign-p2a/session-160x45.svg` failed because the highlighted session row varied run to run (`▸1` vs `1`). The first selection is deterministic: `sidebar.selected` is `('open_here', <front>)` every run (rule 6), and the row key `(open_here, front)` matches it every run. The variation was a **paint-flush race**: after `set_focus(None)` the mark reaches the compositor a beat later, so the snapshot sometimes captured an un-repainted frame. The fix is in the test: the session baseline now settles on the panel's painted lines until the front session's row shows the `▸` mark, then snapshots. Proven: `test_two_size_baselines[session-size0]` and `[session-size1]` pass 5 runs in a row (`.lane-logs/sess_run1..5.log`). Only after that proof was the baseline regenerated.

## Resolved — the painted golden (ruling 19, 2026-11-22 10:1x)

Ruling 19 said the `14000-filtered` highlight loss is a rule 6 defect in the build, not a design question. Root cause: `_relayout` built the layout with the old selection, applied rule 6 to move `self.selected`, then skipped the rebuild because `rows` did not change — leaving a stale layout (old selection) as `_layout`, so the title read `–` and the surviving row carried no mark. The fix (`tsb_tui/screens/fleet_view.py:_relayout`): capture `prev_selected` before rule 6 and rebuild when `self.selected != prev_selected` as well as when `rows` changed. After the fix the filtered full view keeps a surviving selection, scrolls it into view, paints its `▸`, and the title reads `Sessions · 1/35 of 14000 · 'rtr-007'` (index = the selected row's place, rule 15).

The golden was captured with its generator (`tests/golden_fleet_view_painted.py`). The only differences from `e0c1031` are the Sessions titles (rules 3/15): `Sessions you own or may join · N` → `Sessions · 1/N`, and the filtered form `Sessions · –/35 of 14000 · 'rtr-007'` → `Sessions · 1/35 of 14000 · 'rtr-007'`. No non-title difference remains, so no STOP. The two painted-golden tests pass.

New test (rule 19): `tests/test_redesign_lists_filter.py::test_full_view_filter_keeps_a_surviving_selection_marked` — filter the full view's Sessions table so the selected row is filtered out; assert the first match below its place is selected, painted with `▸` and visible, and the title's index is 1. **Red on the pre-fix build** (`.lane-logs/newtest_defect_red.log`): `Sessions · –/35 of 14000 · 'rtr-007'` — the index `–`, not 1. Green after the fix.

Two rule-12 test updates from the same fix (a table-head selection shows its place, not `–`; `–` is only a Needs-you selection):
- `tests/test_fleet_view.py::test_the_view_lists_needs_you_first_and_mine_joinable_and_show_ended_change_only_what_is_shown`: fresh view asserts `Sessions · 1/2` (was `–/2`).
- `tests/test_fleet_view_painted_rows.py::test_the_filter_and_show_ended_count_every_record_not_the_window`: after `show ended` lists the rtr-007 rows with no prior selection, the head row takes the highlight, so the title asserts `Sessions · 1/35` (was `–/35`).

## Gate (final run, gate env, NO_COLOR unset)

- Full gate (11 files, 394 tests): **3 failed, 391 passed** (exit 1, `.lane-logs/gate_final3.log`). The 3 are all load-sensitive and green in isolation: the two rule-2 rewrites below and `test_two_size_galleries_part2[details-view-size1]`. Every other test passes, including both painted-golden tests.
- `make contract-check`: exit 0 — `program/ is clean at c4c2b81bb7a1, v0.18.0`.
- `make lint`: exit 0 — engine-check verified, import lint clean.
- `git diff --name-only` lists only allowed files: `tsb_tui/model/targets.py`, `tsb_tui/screens/fleet_view.py`, `tsb_tui/screens/terminal.py`, `tsb_tui/widgets/sidebar.py`, the 7 test files named in line 13, the regenerated p1/p2a/p2b baselines under ruling 13, the p3 gallery, and `tests/fleet_view_painted_golden.json` (title-only, per ruling 19). Untracked additions: `tsb_tui/screens/target_details.py`, `tests/test_redesign_lists_filter.py`, 12 new p3 snapshots, this report.

## Not verified / flaky observed

- Load-sensitive tests, green in isolation, red once under the full gate (the asserts count server requests / render state at timed moments; a gated page's chain and the paint flush land later under suite load):
  - `test_a_poll_never_folds_the_loaded_pages_back` — isolated 2/2 passed (`~11.7s` each).
  - `test_a_stale_reply_never_overwrites_the_newer_list` — isolated 2/2 passed (`~21.6s` each).
  - `test_two_size_galleries_part2[details-view-size1]` — isolated 1/1 passed (`2.97s`).
  None were loosened; reported as instructed (the orchestrator runs them on the quiet machine).
- The 8,000-row scale rule (rule 11) is asserted by tests in `tests/test_redesign_lists_filter.py`; no wall-clock pause measurement was taken.

## Ruling 20 — full-suite rule-12 rewrites (2026-11-22 11:5x)

The four files named in ruling 20 joined the allowed set under rule 12. Each failing test rewritten to the ruled behaviour, keeping what it guards; one is a STOP.

Rule-12 changes (file, test, old → new, rule):
- `tests/test_credentials_screen.py::test_a_query_that_excludes_a_marked_target_leaves_both_marks_and_map_acts_on_both` — `workspace._run_query("tag:scenario")` → `view.filter_text = "scenario"; view._relayout()` (rule 1). The guard is unchanged: the count keeps the mark the filter hid, Map names the listed mark then the hidden one.
- `tests/test_credentials_screen.py::test_marks_outside_the_loaded_rows_are_acted_on_and_the_selected_row_is_never_a_stand_in` — `workspace._run_query("type:telnet")` → `view.filter_text = "telnet"; view._relayout()` (rule 1). Guard unchanged: every marked target, never the selected row.
- `tests/test_model_targets.py::test_the_count_says_more_while_a_next_page_may_follow` — `count_label() == "2+"` → `"2…"` (rule 3). Guard unchanged: a loading count is not a false total.
- `tests/test_targets_tab.py::test_the_filter_matches_locally_and_asks_the_broker_nothing` — title `Targets · –/1 of 8 · 'telnet'` → `Targets · 1/1 of 8 · 'telnet'` (rule 6 + rule 15: the filter's single survivor is the table head, so it is selected and the index is its place). Guard unchanged: a typed filter asks the broker nothing.

Production fixes the rewrites exposed (in `tsb_tui/screens/fleet_view.py`, allowed):
- `_relayout` rule 6: the place counts the **active tab's** table (`GROUP_TARGETS` on the Targets tab, `GROUP_TABLE` on Sessions), not `GROUP_TABLE` only — the targets-tab filter crashed on `ValueError` (rule 6 applied to the targets table).
- `_relayout` rule 6: a `needs_you` selection is not a table position (rule 15), so the place stands at its overall index; the table-index mapping runs only for a table row. This fixed `test_fleet_scale_runs_tiny_and_filters_in_both_views` (`tests/test_bench_smoke.py`) — the bench already filters through `filter_text` (rule 1), so no test change was needed.
- `picked_targets`: under rule 1 the filter defines the rows, so "listed" is the filtered targets, not the whole `model.targets`; marks outside the filter sort into the hidden tail. This is what the two credentials tests guard.

STOP (now ruled by 21) — `tests/test_target_form.py::test_saved_target_is_selected_after_it_leaves_the_query`: it called `screen._run_query("type:ssh")` and guarded "the saved row stays selected after its type leaves the query", which rule 1 cannot express (the filter matches name/host/group/tags/source, not `type`) and which conflicts with rule 6. The orchestrator ruled (21): rule 6 governs; rewrite it.
- `test_create_edit_and_delete_with_confirmation` ("aa-shell could not be selected" in the orchestrator's earlier run) passes on this branch in isolation, within its file, and in the full suite below; no change made.

## Ruling 21 — the STOP rewritten (2026-11-22 13:4x)

`tests/test_target_form.py::test_saved_target_is_selected_after_it_leaves_the_query` → `test_a_saved_target_that_leaves_the_filter_moves_the_selection_by_rule_6` (rule 6 governs; the guard predates it). New flow (rule 1 + rule 6 + rule 5):
- create `query-target` with tag `prod`; seed `two-client` with tag `prod` (a survivor that sorts after `query-target`);
- filter free text `prod` (`view.filter_text = "prod"; view._relayout()`) → survivors `[query-target, two-client]`, `query-target` selected (head);
- press `e` (opens the editor while not in filter-editing), change the matched tag to `gone`, save;
- rule 6: the saved target leaves the filtered list, the selection moves to the next surviving row below its place (`two-client`), and the title names it: `Targets · 1/1 of 9 · 'prod'`;
- `view.editing = True` then Esc (rule 5) clears the filter; the saved target returns in the list with its new tag.

No production change was needed — the code behaves so; the test passes. Nothing else in the file was deleted.

## Full suite (Makefile way, NO_COLOR unset, exit code checked)

`env -u NO_COLOR PYTHONPATH=program/contract/client:. $(PYTHON) -m pytest tests/ -p no:cacheprovider` → `.lane-logs/fullsuite.log`, **exit 1**: **1 failed, 2554 passed, 3 skipped** (53:47). The one failure was the STOP test (`AttributeError: 'TerminalScreen' object has no attribute '_run_query'`), now resolved by ruling 21's rewrite. No other full-suite failure. Ruling 21 says not to re-run the full suite; the last full run stands.

- `tests/test_target_form.py` (14 tests): all pass with the rewrite.
- The target-form + 11-file gate (408 tests): **3 failed, 405 passed** (exit 1) — all three load-sensitive and green in isolation: `test_a_poll_never_folds_the_loaded_pages_back` (1/1 alone), `test_a_poll_asks_limit_and_cursor_only_and_a_503_shows_the_state` (1/1 alone), `test_two_size_galleries[targets-opens2-size0]` (1/1 alone).
- `make contract-check`: exit 0 — `program/ is clean at c4c2b81bb7a1, v0.18.0`. `make lint`: exit 0 — engine-check verified, import lint clean.
- `git diff --name-only` lists only allowed files; the rule-12 edits are `tests/test_credentials_screen.py`, `tests/test_model_targets.py`, `tests/test_targets_tab.py`, and the ruling-21 rewrite in `tests/test_target_form.py`. `test_bench_smoke.py` is NOT modified (the bench fix was in production).

Status: DONE — ruling 20's rule-12 rewrites applied, ruling 21's STOP rewritten to rule 6 with no production change, three production fixes in `fleet_view.py`; the target-form file and gate are green apart from load-sensitive tests that pass in isolation.
Summary: the saved-target test renamed and rewritten to rule 6 (selection moves to the next surviving row, title names it, Esc shows the edit); full suite 1 failed / 2554 passed (the now-resolved STOP), target-form + 11-file gate 3 failed / 405 passed (all load-sensitive, green alone), contract-check and lint exit 0.

## Ruling 22 — `make lint` ruff fixes (2026-11-22 14:1x)

The earlier report's `make lint: exit 0` was the local run, where ruff is not on PATH and the Makefile skips it; the orchestrator's `make lint` runs `ruff check tsb_tui tests bench tools` and reported 20 errors. All 20 fixed; no behaviour changed beyond the F821 fix.

The real defect (F821, `tsb_tui/widgets/sidebar.py:1612`): inside `_target_painted`, the branch runs only when `self.wide` is true, so the budget is `_target_facts_budget(self.wide, inner)`; the code read an undefined name `budgeted_wide` (local to `_target_spans`). A wide view whose cached facts are empty raised NameError.
- Test first: `tests/test_sidebar.py::test_a_wide_target_row_with_no_facts_paints_without_the_nameerror` — a target with no host, group or tags and type `ssh` has no facts, so the cached painted facts are empty; painting its row in the wide sidebar recomposes within the wide budget.
- Red on the pre-fix build: `NameError: name 'budgeted_wide' is not defined` at `sidebar.py:1612`.
- Fix: `budgeted_wide` → `self.wide`. Green after (the row paints its label).

Other ruff fixes (behaviour-preserving):
- `tsb_tui/screens/terminal.py` import block: `CMD_FULL_VIEW` / `CMD_DETAILS` ordering (I001).
- `tsb_tui/widgets/sidebar.py` 330, 1931: unused unpacked `slash` → `_slash` (RUF059); 1830 `start + 1 if start + 1 <= last_start else last_start` → `min(start + 1, last_start)` (FURB136).
- `tests/test_credentials_screen.py` 328, 354: unused unpacked `workspace` → `_workspace` (RUF059).
- `tests/test_redesign_layout.py`: removed unused imports `re` and `conftest._screen_text`, the inner `import re` that redefined it (F401, F811), and sorted the import block (I001).
- `tests/test_redesign_lists_filter.py`: removed unused imports `html`, `json` and sorted the block (F401, I001); bound the loop variable in the `wait_until` lambda as `lambda screen=screen: …` (B023); unused unpacked `theirs`→`_theirs`, `served`→`_served` (RUF059); removed the unused `t0 = time.monotonic()` (F841).
- `tests/test_targets_tab.py` 459: unused unpacked `view` → `_view` (RUF059).

Gate (ruling 22):
- `make lint` exit 0 with ruff on the path: `All checks passed!`, import-lint clean, engine-check verified.
- Touched test files + `test_redesign_lists_filter.py` + `test_sidebar.py` (167 tests): **1 failed, 166 passed** — the one failure, `test_targets_tab.py::test_a_click_on_the_last_loaded_target_selects_it_while_pages_arrive`, is load-sensitive and green in isolation (2/2, `~12s` each); not caused by these edits.
- The F821 test green; `make contract-check` exit 0.

Status: DONE — all 20 ruff errors fixed (the F821 defect with a red-then-green test), no behaviour changed beyond that fix; `make lint` exits 0, the touched-file gate is green apart from one load-sensitive test that passes in isolation.
Summary: sidebar.py F821 fixed to `self.wide` with a new sidebar test (red NameError, green after); import-order/unused-import/underscore/min/B023 fixes across the named files; make lint exit 0, gate 1 failed / 166 passed (the failure load-sensitive, green alone), contract-check exit 0.

## PR #114 review round

Qodo found thirteen defects on PR #114 (HEAD a055ddc); each is ruled by the orchestrator (2026-11-22). For each:
a test that fails on a055ddc first, then the fix; or, where the finding is wrong against the code, a refutation
with file:line. New tests live in `tests/test_redesign_lists_review.py`. No commit, no push.

**Item 1 — wide sidebar IndexError (refutation).** `_build_pairs` (`sidebar.py:1478`) computes `count = 1 if wide else
(2 if facts[position][1] else 1)`; in wide mode the `facts[position]` term is never evaluated, so a row outside the
window's cached facts is never indexed. A wide sidebar (160x45) with 4,008 targets lays out, paints, and scrolling to
the end (`_jump_target`) marks the last row. Test `test_wide_sidebar_paints_and_scrolls_to_the_end` is green on
a055ddc — no IndexError. The ruling's behaviour ("facts outside the window composed on demand") already holds.

**Item 3 — session section keeps the old selection mark (refutation).** `_relayout` (`fleet_view.py:788-790`) captures
`prev_selected` before building and rebuilds when rule 6 moves the selection; ruling 19 added this, so it is present on
a055ddc. Test `test_filtering_the_selected_session_moves_the_mark_to_the_new_selection` is green on a055ddc: filtering
out the selected target leaves exactly one `▸`, on the new selection.

**Item 4 — details screen bypasses dialog registration (refutation).** Textual runs every class's `on_mount` down the
MRO, so `_Dialog.on_mount` (`chrome_dialogs.py:106`) registers the details screen; the override
(`target_details.py:72`) does not bypass it. Probes on a055ddc show `_Dialog.on_mount`'s append and `refresh_status`
run for `TargetDetailsScreen`. Test `test_details_screen_is_a_registered_dialog` is green on a055ddc: the terminal counts
the screen among its dialogs, and closes it (status restored) on Escape.

**Item 8 — painting copies the remaining pairs (refutation).** `_target_painted_end` walks from `index` in a bounded
loop that stops at the window's height; with an 8,000-row list, painting near the start touches only the window's rows.
Test `test_painting_near_the_start_touches_only_the_window_pairs` (a counting wrapper on the pairs list) is green on
a055ddc: the walk touches at most `window + 2` pairs.

**Item 6 — full view `?` key does nothing (red → fix).** Red on a055ddc: `fleet_view.py:1054` matched only the literal
`"?"`; Pilot's normalized key event is `question_mark`, so `test_question_mark_opens_all_keys_in_the_full_view` fails
(`TextScreen` never opens). Fix: `key in ("?", "question_mark")`, as the sidebar does (`sidebar.py:2157`). Green after.

**Item 13 — loading title shows two ellipses (red → fix).** Red on a055ddc: `fleet_view.py:501` built
`first = f"{len(model.targets)}…"`, so `title_parts` added a second `…`; the title read `Targets · 1/500…… loading`.
Fix: pass a plain count (`first = str(len(model.targets))`); `title_parts` alone adds the loading mark.
`test_loading_title_shows_exactly_one_ellipsis` green after (one ellipsis).

**Item 5 — Targets filter control shows the retired query text (red → fix).** Red on a055ddc: `_query_controls`
(`fleet_view.py:475`) rendered `query_text`, while the rows filter on `filter_text`; typing in the full view's Targets
filter left the control at `/ filter`. Fix: pass `self.filter_text` to the control and retire `self.query_text` /
`self.applied_query` (the dead dependency). `test_targets_filter_control_shows_the_filter_text` green after.

**Item 7 — a target in Recent gets two selection marks (red → fix).** Red on a055ddc: `_target_painted` marked by id
(`selected = row.id == self.selected_target`, `sidebar.py:1617`), so a target shown in `Recent` and the main list
carried two `▸`. Fix: mark the row at `self._target_pos` (`selected = position == self._target_pos`), and set
`_target_pos` to the row clicked, so selecting each appearance marks that row; actions keep using `self.selected_target`
(the id). `test_a_target_in_recent_marks_only_the_row_selected` is red on a055ddc (two marks) and green after (one mark
per appearance).

Commands: the gate for these items runs under
`env -u NO_COLOR PYTHONPATH=program/contract:program/contract/client:. TSB_TUI_TERM_LIBRARY=vendor/libghostty-vt/libghostty-vt.so $(PYTHON) -m pytest`.
All seven new tests green with the fixes applied; items 1, 3, 4, 8 green on a055ddc (refutations), items 5, 6, 13 red on
a055ddc and green after.

Gate (items 1–8, 13): `tests/test_redesign_lists_review.py` + `test_redesign_lists_filter.py` + `test_targets_tab.py` +
`test_sidebar.py`: **1 failed, 100 passed** — the failure,
`test_targets_tab.py::test_a_stale_reply_never_overwrites_the_newer_list`, is load-sensitive and green in isolation
(1 passed, `~22s`). Item 7 moved the highlighted row in three SVG baselines (`recent-three-160x45`, `group-row-80x24`,
`group-row-160x45`): the mark glyph moves from the Recent head to the main-list row at position 0 (rule 15, title-only
diff), regenerated with `UPDATE_P3_SNAPSHOTS=1`.

**Item 10 — sign-out guard restored (red → fix).** Red on a055ddc: `_poll_reply` handed a poll's reply to
`_apply_poll` and `_apply_poll` applied it with no sign-out check, so a reply landing during sign-out folded a fleet
back into the workspace. The guard (`self.app.signing_out`) was absent on e0c1031 too (byte-identical), so part 2 did
not remove it; fixed anyway. Fix: `_poll_reply` returns without handing off, and `_apply_poll` returns without applying,
when `self.app.signing_out`. `test_a_poll_reply_during_sign_out_applies_nothing` is red on a055ddc (the
generation advances, the view goes dirty) and green after (`applied_generation` stays, no dirty).

**Item 2 & 12 — a background pass stages apart from the published list (red → fix).** Ruled (2026-11-22): a background
refresh reads into `self._targets_staging` (a separate `TargetsModel`) and touches the published rows only when its last
page lands; a tick that finds a pass in flight starts nothing; a failed staging pass is dropped whole, the published
list stays. Red on a055ddc: `_fetch_targets(background=True)` applied pages straight to `self.targets`, so a background
pass changed the published rows page by page and a tick during it sent a request. Fix: the first load after login
(`on_mount`) is a foreground read (page-by-page publishing kept); a background pass creates `self._targets_staging` and
chains its pages into it, `publish()` copies the staged rows/records/cursor/query/fetched/unavailable/more_failed into
the published model in one step when the last page lands; `_fetch_targets`' background guard returns on a pass in flight
and on a foreground page in flight (rule 1 kept); `_targets_failed` drops the staging pass on any failure, marks the
published model unavailable on a 503 and toasts a 401, raising no toast for other background failures. Tests
`test_a_background_refresh_stages_pages_apart_from_the_published_list` (published rows unchanged through a held first
page, selection follows its id, a tick sends no request) and
`test_a_failed_background_pass_leaves_the_published_list_as_it_was` (a failed pass dropped, published rows stay, not
unavailable, the next pass starts fresh): both red on a055ddc, green after.

**Item 9 — a failed later page shows the retry line (red → fix).** Ruled (2026-11-22): `TargetsModel.more_failed`
records a failed `more` page apart from the cursor; the title drops `loading` and the count's ellipsis, the last line
reads `Could not load more · r retry`, `r` retries from the kept cursor. Red on a055ddc: a chained page that answers an
error leaves the list `loading` for good (the cursor stays, nothing records the failure), so the title shows the
ellipsis and no retry line, and a toast fires. Fix: `TargetsModel` gains `more_failed` (cleared on `apply`, `reset`,
and an `r` retry); `_targets_failed` sets it for a current `more` page that fails with anything but a 401/503/refused
query and returns without a toast; the sidebar view sets `targets_loading`/`target_has_next` false when it is set (the
count shows the loaded number, no false total); `_build_pairs` appends the last-line pair after the rows and
`_target_painted` renders `Could not load more · r retry` there (`CMD_RETRY` with `"targets-more"`); the sidebar `r`
key posts that command and `CMD_RETRY` calls `_fetch_targets(more=True)`, which reuses the kept `next_cursor`. Test
`test_a_failed_later_page_shows_the_retry_line_and_r_completes` is red on a055ddc (`more_failed` absent, toast fires)
and green after (plain count, retry line at the window end, no toast, `r` completes the list).

Gate (all items): `tests/test_redesign_lists_review.py` + `test_redesign_lists_filter.py` + `test_sidebar.py` +
`test_targets_tab.py` + `test_sign_out.py`: **119 passed** (the earlier load-sensitive failure,
`test_a_stale_reply_never_overwrites_the_newer_list`, green in this run). `make contract-check` exit 0; `make lint`
exit 0 (ruff on the path); engine-check verified at PIN. Item 7 moved the highlighted row in three SVG baselines (`recent-three-160x45`,
`group-row-80x24`, `group-row-160x45`), regenerated with `UPDATE_P3_SNAPSHOTS=1`.

## Wider-suite baseline check (the orchestrator, 2026-11-22 18:3x)

The orchestrator's run of the wider suites on this tree found `tests/test_redesign_lists.py::test_two_size_galleries`
failing 7 cases (`alike-since` both sizes, `sessions-states` 160x45, `targets-opens1` and `targets-opens2` both sizes);
they pass on a055ddc. For each case the old (a055ddc) and new renders were captured as full-screen text lines (the SVG's
per-line `<text>` elements parsed back to rows) and diffed. Every case showed exactly one line, the same pattern: a target
shown in `Recent` **and** the main list carried `▸` on both rows in a055ddc; the new tree keeps the mark on one row only.
That is item 7's rule (mark on the selected row only). Each case with its rule:

- `alike-since-80x24` L12, `alike-since-160x45` L31, `sessions-states-160x45` L76, `targets-opens1-80x24` L118,
  `targets-opens1-160x45` L141, `targets-opens2-80x24` L187, `targets-opens2-160x45` L210 — all: the duplicate `▸` on the
  second appearance of the selected target goes; one mark remains. Rule: **item 7**. No count, ellipsis, retry-line or
  row-set difference appeared in any case, so nothing was STOPped. The 7 baselines regenerated with
  `UPDATE_P3_SNAPSHOTS=1` (all 18 gallery cases green).

The same check on `tests/test_redesign_layout.py::test_two_size_baselines[state=session]` (a `redesign-p2a` baseline the
orchestrator's list did not name) also failed both sizes. Rendered old vs new: the identical pattern — the target
`fixture target` carried `▸` on its Recent row and its main-list row in a055ddc; the new tree keeps one. Rule: **item 7**.
No other difference. The two baselines (`session-80x24`, `session-160x45`) regenerated with `UPDATE_P2A_SNAPSHOTS=1` (all
6 cases green).

Full required-suite run (`tests/test_redesign_lists.py`, every `tests/test_redesign_*.py`, `tests/test_fleet_view*.py`,
plus the gate's `test_sidebar.py`/`test_targets_tab.py`/`test_sign_out.py`): **460 passed, 2 failed**. The two failures
(`test_fleet_view_painted_rows.py::test_a_clock_that_changes_a_row_s_width_is_painted_as_a_rebuild_paints_it` and
`test_targets_tab.py::test_a_poll_never_folds_the_loaded_pages_back`) are load-sensitive: both green in isolation
(3 passed, ~35s with `test_a_stale_reply_never_overwrites_the_newer_list`, which failed in an earlier full run for the
same reason). The intermittent failures move between runs under heavy parallel load; none is a regression from this
round's changes.

## Two items the first pass missed (the orchestrator, 2026-11-22 19:5x)

Qodo re-assessed 1397e5e and kept items 3 and 11; both are right. The first pass got them wrong:

**Item 3 — missed in the first pass.** The first pass refuted item 3 by testing `FleetView._relayout`
(`fleet_view.py:786`), which already captures `prev_selected` (ruling 19). The finding is `Sidebar._relayout`
(`sidebar.py:1021`): it copies `rows = list(view.rows)` from the view it just built and rebuilds only
`if view.rows != tuple(rows)`, never true, so when the highlighted session leaves the open-here list and rule 6 moves
`self.selected`, the view keeps the old selection's mark. Red on 1397e5e: `test_a_refresh_that_removes_the_selected_session_moves_the_mark`
(`tests/test_sidebar.py`) — after closing the selected session's slot, `self.selected` moves to the surviving row
(rule 6 passes) but the painted `▸` is absent, because the view was built with the removed selection. Fix: capture
`prev_selected = self.selected` before `_build()` and rebuild when `view.rows != tuple(rows) or self.selected != prev_selected`
(the ruling-19 pattern, applied to the sidebar). Green after.

**Item 11 — not done in the first pass.** The first pass's status listed twelve findings; item 11 was omitted.
`FleetView.on_key`'s non-editing `escape` branch (`fleet_view.py:1027`) posted `CMD_CLOSE` unconditionally, so Escape
closed a view with a committed filter. Red on 1397e5e:
`test_escape_clears_the_filter_first_and_closes_on_the_next` (`tests/test_fleet_view.py`) — after Enter commits a filter
(`filter_text` nonempty, editing off), the first Escape left `filter_text == "zzz"` and closed the view. Fix: a
non-editing Escape with a nonempty `filter_text` clears it and relayouts; the next Escape closes. Tested in both the
Sessions and the Targets tab. Green after.

Gate (the gate files plus `tests/test_redesign_lists.py`, `tests/test_redesign_layout.py`, `tests/test_fleet_view*.py`):
**274 passed, 0 failed** (`make contract-check` exit 0; `make lint` exit 0; engine-check at PIN). No baseline difference
arose from these two fixes (item 3 is a mark-move on a session removal, item 11 is Escape behaviour; neither is in a
snapshot case), so no baseline was regenerated.

Status: DONE — all 13 review findings, honestly. Refutations for items 1, 4, 8 (green on a055ddc); red-first fixes for
items 5, 6, 7, 13, 10, 2, 12, 9, 3 (sidebar), 11. **Item 3's first-pass refutation was wrong** (it tested FleetView,
not the Sidebar the finding named) and **item 11 was missed entirely in the first pass**; both are now red-first fixed
on 1397e5e. Summary: three findings refuted against the code with file:line, ten fixed red-first; 9 baselines
regenerated (all ruled by item 7); gates green.

## CI regression (the orchestrator, 2026-11-23 00:2x)

**The cause.** Commit `0c1777e` changed the first load after login (`on_mount`) from
`self._fetch_targets(background=True)` to `self._fetch_targets()`. On the base, a failure of that first read (anything
but a 401) raised no toast (`_targets_failed`: `elif not background or ... 401`); as a plain foreground read it does,
which is the extra "HTTP" toast `test_no_resolving_credential_reads_as_a_state_not_an_error` sees.

**Red on 469a4e2:** `tests/test_attach_confirm.py::test_no_resolving_credential_reads_as_a_state_not_an_error` —
`assert not True` at `tests/test_attach_confirm.py:174` (the `any("HTTP" in m ...)` line).

**Fix (the ruling's smallest change).** A `quiet` flag on the foreground path: `on_mount` reads
`self._fetch_targets(quiet=True)` — page-by-page publishing into `self.targets`, never the staging model; `quiet`
propagates through `_targets_reply` to the pages chained from that read; `_targets_failed` honours it as it honours
`background`: the toast condition is `(not background and not quiet) or (401)`. A 503 stays the unavailable state, a
refused query the query line, a failed later page rule 9's toast-less retry line; a 401 toasts. The staging pass and
items 2, 9, 10, 12 are untouched.

**New test (red first).** `tests/test_redesign_lists_review.py::test_the_first_load_failing_with_a_500_raises_no_toast_and_the_next_tick_reads`
— red on 469a4e2 at the toast assertion (`tests/test_redesign_lists_review.py:392`: the 500's toast was live); green
after: the first load fails with a 500, no toast, and the next tick's read (the background pass, what `_targets_tick`
does) publishes all pages.

**The two intermittent tests.** Run alone five times each on this tree (tree = 469a4e2 plus this session's uncommitted
fix; worktree and stash are disallowed, so the run is on the tree).
`test_feed_thread.py::test_order_is_kept_across_a_size_frame_and_a_prefix_with_its_dump`: **5 passed, 0 failed**.
`test_targets_tab.py::test_a_stale_reply_never_overwrites_the_newer_list`: **4 passed, 1 failed** (run 1: timed out
after 10s waiting for "the refresh's request"). Nothing changed for them.

**A third intermittent found in the gate.** `test_redesign_lists_review.py::test_targets_filter_control_shows_the_filter_text`
failed once in the suite run; it is identical on 469a4e2 and the fix does not touch page publishing (the flake is the
test waiting for the exact 500-row window while pages publish fast). Alone five times: **4 passed, 1 failed**; the suite
re-run is green. Nothing changed for it.

**Gate.** `make contract-check` exit 0; `make lint` exit 0 (engine-check at PIN);
`$(PYTHON) -m pytest tests/test_redesign_lists_review.py tests/test_attach_confirm.py` exit 0 — 19 passed;
`$(PYTHON) -m pytest tests/test_sign_out.py tests/test_terminal_keys.py` exit 0 — 16 passed;
`$(PYTHON) -m pytest tests/test_redesign_*.py tests/test_fleet_view*.py` (all 13 redesign files, both fleet-view
files) exit 0 — **387 passed**. `git diff --name-only`: only `tsb_tui/screens/terminal.py` and
`tests/test_redesign_lists_review.py`.

**Not verified:** the two intermittents and the filter flake were not run on main 10a63d7 itself (worktree/stash
disallowed); the full suite was not re-run (this ruling asks for the gate only).

## Qodo third round (2026-11-23, on 95e0d84)

Eight findings checked against the tree (469a4e2 + the CI-regression fix). Each red first (first failing
line on HEAD), then the fix, then green; a refutation with file:line where the tree already did it.

- **Item 1 — an old staging reply acts on a newer pass.** Red: `test_an_old_staging_reply_never_touches_a_newer_pass`
  first failing line `tests/test_redesign_lists_review.py:477` (`assert screen._targets_staging is b`: the old
  pass's last-page reply published into the pass now in flight and cleared it);
  `test_an_old_staging_failure_never_clears_a_newer_pass` at `:505` (the old pass's failure cleared the pass in
  flight). Fix: `_fetch_targets` captures the pass it sent for (`pass_model`) into both callbacks;
  `_targets_reply`/`_targets_failed` return when `self._targets_staging is not pass_model`. Green.
- **Item 2 — a staging reply after unmount chains a request.** Red:
  `test_a_staging_reply_after_unmount_sends_nothing_and_publishes_nothing` first failing line
  `tests/test_redesign_lists_review.py:553` (`unmount dropped the staging pass`: `on_unmount` left `_targets_staging`
  set, so the reply applied and chained). Fix: both background callbacks return while `self._unmounted`;
  `on_unmount` sets `self._targets_staging = None`. Green.
- **Item 3 — a click selects the row below.** Red: `test_a_click_marks_the_row_clicked` first failing line
  `tests/test_redesign_lists_review.py:594` (the mark landed on `bulk-0003`, the row below the clicked Recent
  `bulk-0002`). Fix: `on_click` resolves the clicked line through the cached pairs, taking the filter line and the
  `Recent` heading off before bisecting. Green.
- **Item 4 — the scroll limit falls on a row's second line.** Red: `test_the_scroll_limit_stops_on_a_whole_row`
  first failing line `tests/test_redesign_lists_review.py:617` (`_target_scroll_top` = 55, not a pair start). Fix:
  `_target_scroll_limit`'s walk-back returns the row start that fits whole (`prev_start`), never `start + 1`. Green.
- **Item 5 — a fresh sidebar marks its first target with nothing selected.** Red:
  `test_a_fresh_sidebar_with_no_selection_paints_no_mark` first failing line
  `tests/test_redesign_lists_review.py:643` (a mark painted on the first row). Fix: `_target_painted` applies the
  position check only when `target_selected` is set. Green.
- **Item 6 — the details panel wraps at the screen's width and never re-wraps.** Red:
  `test_the_details_panel_wraps_at_its_own_width_and_rewraps_on_resize` first failing line
  `tests/test_redesign_lists_review.py:662` (a 156-char line in a 96-column panel). Fix: `sync` wraps at the body
  Static's laid-out content width, and `on_resize` re-syncs. Green.
- **Item 8 — drive's `targets` query no longer matches the tab.** Ruled option (b): no code change.
  `tsb_tui/drive/GUIDE.md`'s `targets` row now says the query is TSB's structured query (`type:`, `tag:`, `group:`,
  `source:`), matched by TSB, and that the Targets tab's own filter is local free text and differs. No drive test
  pins the old text (the only guide test, `test_all_keys_marks_target_edit_permission`, checks the keys table, not
  this row). No red/green test.
- **Item 9 — `Sidebar._relayout`'s rows guard can never be true.** Refutation with file:line: `rows = list(view.rows)`
  is copied from the view just built (`tsb_tui/widgets/sidebar.py:1025`), so `view.rows != tuple(rows)` is never
  true; and a rows change alone never needs a rebuild — the first `_build()` already painted the new rows against
  the selection it had, which is unchanged. Only the selection moving (rule 6) needs the rebuild. The dead rows
  clause was dropped; the guard reads only `self.selected != prev_selected` (`tsb_tui/widgets/sidebar.py:1034`). No
  test kept for the dropped clause (the ruling asks for one only when the clause is kept).

**Baselines:** 28 snapshot cases changed, all by the selection mark alone (item 5: no mark when nothing selected;
item 7: one mark on the selected row) — `redesign-p3` `alike-since`, `empty-sessions`, `ended-on`, `only-ended`,
`sessions-states`, `targets-opens0/1/2`, `filter-list`, `group-row`, `loading-title`, `recent-three`,
`details-view`, and `redesign-p2a` `panel-focused`, `session`. Every difference is a mark colour/weight/position
change; no ellipsis, retry line, rows, or other change appeared. Regenerated under the earlier rule.

**Commands (exit codes):** gate suites — `test_redesign_lists.py`, `test_redesign_lists_filter.py`,
`test_redesign_layout.py` (150 passed), `test_redesign_lists_review.py` + `test_sidebar.py` +
`test_sidebar_painted_rows.py` + `test_fleet_view.py` + `test_fleet_view_painted_rows.py` +
`test_terminal_keys.py` + `test_drive_front_and_version.py` + `test_relogin_after_large_poll.py` (137 passed);
`make contract-check` exit 0; `make lint` exit 0. No commit, no push.

**Not verified:** the full suite was not re-run (the gate only). The intermittent
`test_the_ascii_glyph_set_reaches_the_sidebar_and_the_status_row` failed once in the first baseline run and passed
alone — pre-existing intermittency, not caused by this round. Item 9 has no test (clause dropped); item 8 is a doc
change with no test.
