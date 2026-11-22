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
