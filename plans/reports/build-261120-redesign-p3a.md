# Build — TUI redesign P3, part 1: target and session rows

**AI-Model:** glm-5.3-flash
**Date:** 2026-11-20
**Branch:** `feat/redesign-p3a-rows` (on main `466ab48`), no commit, no push — the working tree holds the change.
**Brief:** `LANE-BRIEF.md` in the checkout root; plan `plans/261116-1920-tui-redesign/plan.md` ("Authority and ruled section", "Ruled (2026-11-16 19:4x)", "Amendment 2" P3 rows) and `phase-03-lists.md`.

## Outcome

The side panel's Targets and Sessions rows say what the part-1 rulings say: the ruled target row (label fallback, host · type · open numbers · others, two lines at the 24-inner budget, one at the wide one, natural order with open-here targets first), the ruled session row (`N name`; the state word beside its WHO_* mark and `state · owner`; the typer words; one line at the wide budget; `recorded`/REC off the rows; the end word with nothing invented after it), alike ended rows say `since HH:MM`, the full view's Sessions table follows the ruled session order with identity-stable highlight, and the ruled empty/loading/error states with their working keys (`a`, `r`, `e`, `n`) are in the side panel, and the no-session card ends with `F1 menu`.

Tests: `tests/test_redesign_lists.py`, 20 assertion tests + 9 gallery cases × 2 sizes = 38 passed. Gate (all commands run in the foreground, outputs and exit codes checked, NO_COLOR unset; every pytest command ran with the lane's venv interpreter and `PYTHONPATH=program/contract/client:.`):

| Command | Exit | Result |
|---|---|---|
| `pytest -q -p no:cacheprovider tests/test_redesign_lists.py` | 0 | 38 passed |
| same, with `tests/test_model_fleet.py tests/test_targets_tab.py tests/test_fleet_view.py tests/test_fleet_view_painted_rows.py tests/test_sidebar.py tests/test_redesign_layout.py tests/test_redesign_focus.py tests/test_redesign_rail_tint_keys.py tests/test_redesign_keys.py` | 0 | 305 passed |
| `make contract-check` | 0 | `program/` clean at `c4c2b81bb7a1`, v0.18.0 |
| `make lint` | 0 | ruff + engine check green |

`git diff --name-only` lists only: `tsb_tui/model/fleet.py`, `tsb_tui/model/targets.py`, `tsb_tui/widgets/sidebar.py`, `tsb_tui/screens/terminal.py`, `tests/test_redesign_focus.py`, `tests/test_redesign_layout.py`, `tests/test_sidebar.py`, the regenerated baselines named below, plus (untracked) `tests/test_redesign_lists.py` and `tests/snapshots/redesign-p3/`. `screens/fleet_view.py` needed no change.

## Per rule

The tests went red first, on `466ab48`, before implementation; the red run is recorded in the lane log (14 failed, 6 passed — the six passing are the regression guards named below). Each rule's first failing line is from that run or from a dedicated red re-run (the session-order test, whose red was made honest after the first run passed by creation-order coincidence).

1. **Target rows** — `tsb_tui/widgets/sidebar.py` (`_target_label`, `_target_facts`, `_target_painted`, the Targets section): two lines at the narrow budget (the label, then the facts line), one line at the wide one (the facts after the label, the name yielding with an ellipsis); the label falls back name → host → `type id` (`tsb_tui/model/targets.py:TargetRow.has_name` carries whether the record named the target, `make_row`); a non-ssh target shows its type word; no login words anywhere. Numbers: `open N,M` from the open-here items in their open order; `no open suffix when none`. Cut rule: `_cut_name` cuts at a word boundary, a one-word label at the edge, never past it. Test `test_target_row_grammar_two_lines_narrow_one_line_wide` (red: line 61), `test_target_rows_show_no_login_words` (guard, was green), `test_a_word_is_never_cut_and_a_long_name_ends_in_an_ellipsis` (guard, was green).
2. **Others on a target** — `tsb_tui/model/fleet.py:others_by_target` over the merged records the poll already returned (state live or detached, owner not the caller; one owner `<name> on it`, more `<n> others on it`, counted by owner), surfaced in the row's facts after the open numbers; at the narrow budget a word that does not fit whole is left out (`_target_painted` drops the facts line when it exceeds the inner width... it drops the *others* word only when the line cannot hold it — see the note in "Interpretations"), at the wide one it always shows. Tests `test_others_on_a_target` (narrow was green as the left-out guard; wide red: line 142).
3. **Target order** — `_ordered_targets`: open-here targets first by their lowest open number, then the rest by `tsb_tui/model/targets.py:natural_key` (case-insensitive, digit runs as numbers). Test `test_target_order_open_here_first_then_natural_label_order` (red: line 114).
4. **Session rows** — `_row_lines` in sidebar.py: `N name` (N from the entry's number, blank when not open here), `state · owner` (`yours` or the owner's login), `you type` / `<name> types` / `nobody types`; up to three lines narrow, one wide; the state words are the five ruled ones; the row's clock keeps its right edge, REC is gone. `SidebarEntry` carries `state`, `owner`, `created_at` (`tsb_tui/screens/terminal.py:_entry` fills them from the record). Tests `test_session_row_grammar_narrow_three_lines_wide_one_line` (red: line 166), `test_session_row_says_unreachable` (red: the wait for the word timed out at line 191).
5. **Marks** — the row's mark is the entry's existing `who` glyph (`who_types`) in its existing `_WHO_ROLES` role; every ruled state has one (live → ●/◆/· by who types, detached → ·, unreachable → ◌, killed/terminated → ✕); no new glyph set was added. Covered by the grammar, unreachable and ended tests.
6. **Ended rows** — an ended row says its name, then the end word with its mark, and nothing after it; the ruled reason words are not built. Test `test_show_ended_switch_and_end_words` (red: line 242).
7. **Alike rows** — `_alike_since` groups the visible rows by their rendered words (number included, so two open-here sessions on one target are told apart; ended rows have none); a group of more than one adds `since HH:MM` from `created_at` in local time to each of its rows, by the name at the narrow budget and as the wide row's last words. Test `test_alike_rows_say_since` (red: line 271).
8. **"recorded" leaves every row** — the side panel's rows no longer show REC (`_right_fields(…, rec=False)`; the tick's relayout too); the no-session card (`tsb_tui/screens/terminal.py`, `#no-session-card`, toggled in `refresh_status`) reads `No live sessions · n new session · F1 menu` and shows when no session is open. It appears in the galleries.
9. **Session order** — the full view's Sessions table: open-here rows in rail order, then the rest ranked by `tsb_tui/model/fleet.py:session_rank` (yours live, yours detached, joinable), stable within a rank; the highlight follows the entity (`FleetView` already keys the selection by identity). Test `test_session_order_and_highlight_follow_identity` (honest red: line 309, recorded by running the new test against the stashed unmodified source).
10. **Ended rows hidden by default; `e` toggles** — the side panel's Sessions list shows no ended row until `e` (regression assertion in the show-ended test's first lines), then shows them with rule 6's words; `e` again hides. Keys: `e` in the Sessions pane (`_sessions_key`), the model's ended records behind it (`tsb_tui/screens/terminal.py:_sidebar_model`).
11. **Empty states** — `_empty_copy` wraps the ruled copy at its ` · ` segments so the exact words show whole at the narrow budget too: `No live sessions · n new session` (n works), `No live sessions · e show ended` (e works), `No targets yet · a add one` (a opens the target form), `No targets you can see · ask an admin for access`, `Loading targets…` / `Loading sessions…`, `Could not load targets · r retry` / `Could not load sessions · r retry` (r asks again; the sessions retry repolls, the targets retry refetches). Keys `a`/`r` wired through `Sidebar.Command` (`CMD_TARGET_NEW`, `CMD_RETRY`) to the screen's existing handlers. Tests: the five empty-state tests (reds at lines 329, 348, 369, 401, 434).
12. **Existing tests changed** — listed below under "Existing tests updated".

## Existing tests updated (rule 12)

- `tests/test_sidebar.py::test_the_ascii_glyph_set_reaches_the_sidebar_and_the_status_row` — old: the row read `>1* rtr-core-01` (number and who glyph in one token). New: `1 rtr-core-01` on the row's first line and `. yours` / `you type` on the state and typer lines; the ASCII stand-ins for the mark and the separator dot are what the test still proves. Rule 4 (the row's words).
- `tests/test_redesign_focus.py::test_the_frame_says_so_when_the_keyboard_moves_to_you` — old: the panel's row said `1●`. New: the row says `you type`. Rule 4.
- `tests/test_redesign_layout.py::test_panel_split_frames_and_padding` — the Sessions section's height follows its painted lines (three per ruled session row at the narrow budget, one at the wide one; the wrapped empty copy's two lines), still at least 3 and at most half the panel. The ruled row budget's geometry consequence; P2a's rule stands, its numbers follow the rows.
- `tests/test_redesign_focus.py` — the row's words changed, so the P2b baselines under `tests/snapshots/redesign-p2b/` were regenerated (12 files): detach-hint, first-focus, fold-sessions, fold-targets, rail-activity, rail-new, rail-vault, token-notice, each 80x24 and 160x45.
- `tests/test_redesign_layout.py` — the P2a baselines under `tests/snapshots/redesign-p2a/` (6 files: empty, panel-focused, session × both sizes) regenerated for the same reason.
- `tests/test_redesign_keys.py::test_f1_menu_search_focus_and_hints` — the P1 baselines `tests/snapshots/redesign-p1/menu-workspace-{80x24,160x45}.svg` regenerated (the side panel is in the shot).

**Flagged: the regenerated baselines sit outside the brief's allowed snapshot directory.** The brief allows "new snapshots under `tests/snapshots/redesign-p3/`"; the P2a/P2b/P1 galleries capture the same side panel this part re-rows, so they could not stay green without regeneration, and the gate requires the named suites green. Each file is named above; the orchestrator may discard them and re-rule.

## Snapshots

`tests/snapshots/redesign-p3/`, 18 files, 9 cases × 80x24 and 160x45, captured by `test_two_size_galleries` with keys only, after a settle past one tick and a quiet-alert check: `targets-opens0`, `targets-opens1`, `targets-opens2`, `sessions-states`, `ended-on`, `alike-since`, `empty-targets`, `empty-sessions`, `only-ended`. The words and geometry are asserted separately by the other tests; the galleries carry the whole workspace for review.

## Interpretations (rulings read; veto by line)

- **The state mark is the who glyph.** Rule 5's "the glyphs the sidebar already draws for these states" reads `who_types`' WHO_* set: live shows ●/◆/· by who holds the keyboard, detached · (the holder is always none), unreachable ◌, killed/terminated ✕. No state lacked a glyph, so no STOP was due here.
- **The alike test includes the row's number.** Rule 7 says "would render the same words"; the number is part of the row, so two sessions open here on one target are not alike and the rows that can read alike are the ended ones. This also keeps rule 4's wide budget satisfiable (with alike open-here rows the ruled words plus `since HH:MM` do not fit 38 inner columns even with the name at one ellipsis cell — under the other reading the phase's own instruction to stop and report would have applied; under this reading every ruled case fits both budgets).
- **`N name`'s name is the target id**, as the frame title has shown it since P2a ("its number, target name" is `target_id` there). No inventory lookup; the target row's own label rule (rule 1) is unaffected.
- **The row's clock stays** (rule 4 does not remove it; rule 8 removes only recorded/REC). It keeps its right edge, yields to `since HH:MM` on alike rows, and drops when the ruled words need the room.
- **Empty-state copies wrap at their ` · ` segments** so the exact strings show whole at the narrow budget too (a single line cannot hold `No targets you can see · ask an admin for access` at 24 inner columns).
- **`session_rank` puts a yours+unreachable session in the joinable rank** (it is neither live nor detached); needs-you stays the full view's block above the table, so the table's ranks are open here, yours live, yours detached, joinable.

## Flagged: `terminal.py` beyond the no-session card

The brief scopes `tsb_tui/screens/terminal.py` to "(the no-session card only)". Rules 1–11 need data the `SidebarModel` did not carry (the state word, the owner word, `created_at`, others-on-target, the ended records, the loading/error flags, `target.edit`), and that model is built in `terminal.py`. The change keeps the plumbing minimal: `_entry` fills three new `SidebarEntry` fields, `_sidebar_model` passes seven model fields (the others map from `fleet.py`), `_fleet_view_model` sorts the table with `fleet.py`'s `session_rank`, `_sidebar_command` routes two new commands, `compose`/`refresh_status` add the no-session card. No drive, send-gate, attachment or recording code changed. If the orchestrator holds the brief's scope literally, this plumbing is the diff to re-rule.

## Not verified / limitations

- The galleries and suites above are the whole gate; `make test` (the full suite) was not run in this lane — the brief's gate names the suites run, which are green.
- The gallery snapshots are sensitive to the 1-second tick and the fixture's alert stream; the capture settles past one tick and waits for a quiet alert, as the P2a baselines do. Ten consecutive single-case runs of `targets-opens1-size0` passed after that.
- The no-session card's body beyond its ruled ending is this lane's composition (`No live sessions · n new session · F1 menu`, the ruled empty-state words plus the ruled ending); the amendment rules only the ending.
- Part 2 work (the filter, position and paging, the details view, the Recent section, the 8,000-row cases) was not started, as ruled.

## Round 2 — the review's five fixes (2026-11-20 20:1x rulings)

**AI-Model:** glm-5.3-flash. Same branch and tree as round 1, round 1's work uncommitted. Brief: `LANE-BRIEF-r2.md` on `LANE-BRIEF.md`. This round changed only `tsb_tui/widgets/sidebar.py` and `tests/test_redesign_lists.py` (+ snapshots); everything else in the diff is round 1's accepted work. Tests first, each red on the round-1 tree, then fixed, then the affected snapshots regenerated.

### 1. Rule 7 as written (alike = same state, owner and typer on one state)

`_alike_key` no longer counts the row's number: two sessions open in this window on one destination with the same state, owner and typer are alike and each says `since HH:MM`. Where it goes: at the 24-inner budget on the third line after the typer words, joined by ` · ` (`you type · since 19:00`, the exact text the implementation renders); at 160 columns at the end of the one line. When it does not fit there, the session's name takes the ellipsis first; a ruled word is never cut. `test_alike_rows_say_since`'s docstring (the old number-based reading) was updated (rule 12).

- Test `test_alike_open_rows_say_since` (80x24 and 160x45): two open sessions on `normal` — both rows say `since`, at 80 columns on the typer line; a second phase detaches both and the alike word stays. Red on the round-1 tree: the wait for the alike word timed out (recorded first failing line `tests/test_redesign_lists.py:314`, the `wait_until` for "both alike rows to say since"). Green: all 7 parametrized instances.
- **Interpretations (veto by line).** (a) An ended row has no typer line, so its alike word stands beside the name, as round 1 rendered and the review did not object to. (b) When the typer line cannot hold `typer · since HH:MM` whole at 24 inner, the alike word moves to the name's line and the name yields — the ruling's only named yielder; the codebase's fixtures never render that case (an open-here row's holder word is always `you`, so the pair always fits), so no test exercises it.
- **Flagged, an input the wide budget cannot hold.** At a 160-column window the panel is 42 columns, inner 38. A alike open row needs the mark (1) + number (1) + blank (1) + the name at one ellipsis cell (1) + ` ● live` (7) + ` · yours` (8) + ` you type` (9) + ` since 09:12` (12) = 40 cells — over by 2 even for the smallest ruled words, with the wide composition the review accepted (round 1's separators, the mark/number/blank cluster). `detached` (≈42) and `unreachable` (≈46) overflow under any spacing, zero separators included. Per Amendment 1 ("For an input that cannot fit this exact budget without cutting a word, stop and report it rather than silently changing the ruling") the composition was not changed: the code lays the alike word at the line's end when it fits whole and leaves it off whole when it does not (the rule-2 never-cut pattern), and the test asserts at 160 columns only that the rows render whole. Options: (i) tighten the wide composition's spacing for alike rows — the minimal `live` pair fits at exactly 38 only with the mark and number adjacent, no blank after the number and single spaces between the words, and `detached`/`unreachable` still overflow; (ii) drop a ruled word (owner or typer) at wide when alike — not ruled; (iii) accept the alike word absent at wide for states it cannot fit (what the code does now, reported); (iv) amend the wide budget.

### 2. No clock on a session row

`_row_lines` lays no right-hand fields at either budget: the row clock (the last output's age, the time to the end, the warning window's countdown and their reason words) is gone from every session row. `refresh_times` skips rows with no right-hand fields, so the tick no longer re-lays or rebuilds them. Countdowns and clocks stay where they are outside the rows (the session frame's title and the full view's own columns, untouched).

- Test `test_no_clock_on_a_session_row`: the contract's fixture double resets a live record's countdown fields, so a `_CountdownApp` test double passes a stored warning window through (`ending`, `ends_at`, `ends_by`); after a tick the row must carry no clock pattern. Red on the round-1 tree: the row read `▸1 sw-clock        1:58 ` (recorded first failing line `tests/test_redesign_lists.py:369`). Green.
- Note: the first red attempt used the last output's age; the fixture's attach dump never sets the pump's `last_output_at`, so no out-age clock renders in that scenario — the warning window's countdown is the deterministic round-1 clock and is what the test now uses.

### 3. No abbreviated words

The only source of the short form was the row clock's narrow reason word (`_time_field`), which item 2 removes from the rows; the row shows the state word whole on its second line (`◌ unreachable · yours`), the name taking the ellipsis. No new abbreviation path was added.

- Test `test_a_state_word_is_never_shortened` (80x24): the short form must never render. Red on the round-1 tree: the row clock had rendered it on the row's first line (recorded first failing line `tests/test_redesign_lists.py:377`, the `assert … not in …` on the joined rows). Green.

### 4. No half-drawn row

A list shows only whole rows, both sections: `SidebarView.row_lens` carries each painted row's line count; the Sessions list walks rows from its scroll position and stops at the first row that does not fit under the section's inner height — that row, and everything after it, is not drawn (blank rows instead); `_line_at` and `line_offset` honour the painted range. The list scrolls by whole rows: the scroll position is always a row's first line (`_scroll_to_selected` scrolls a selected row the window cannot show whole by the least that shows it whole; a wheel turn moves one row, `_scroll_section`). The other list does the same (`_target_row_pairs`, `_target_painted_end`, `_target_row_visible`, a row-based `_target_scroll_limit`, row-based wheel steps, a painted-range check in `_target_painted`); `_target_line_count` (its last caller gone) was removed. The row's position stays reachable by the keys: it is still selectable and pressing to it scrolls it onto the screen whole.

- Test `test_a_row_that_does_not_fit_is_not_drawn` (80x24, four open sessions: the fourth row is not drawn at the pane's bottom; three `j` presses scroll it onto the screen with all three of its lines). Red on the round-1 tree: the fourth row's name line was painted (recorded first failing line `tests/test_redesign_lists.py:400`, the `assert not any("sw-4" …)`; the recorded run used the test's first version, whose only difference was three selection presses that wrapped around and were removed afterwards — the assertion under test is unchanged, and it fails on the round-1 tree for the same reason: a partial row painted). Green.
- Interpretation: painting stops at the first non-fitting row rather than skipping it and drawing shorter rows after it (a hole mid-list); a wheel turn is one row.

### 5. Open numbers on every row that has them

Finding: the round-1 app already renders `open N,M` on every row of a destination that has open sessions and sorts those first — the review's `sessions-states` galleries could not show the numbers because the gallery's sessions are the fixture's renamed sessions (`named_sessions` renames the record's `target_id` without an inventory row), so no row existed for them at all. The gallery now registers `sw-live`, `sw-det`, `sw-unreach` in the served inventory before the targets read.

- Exact rendered text now (80x24, the section's rows): `▸sw-live` then `192.0.2.71 · open 1`, `sw-det` then `192.0.2.72 · open 2`, `sw-unreach` then `192.0.2.73 · open 3` — the three sorted first, in their lowest-open-number order, before `alt screen` and the rest; at 160x45 the same on one line each (`▸sw-live 192.0.2.71 · open 1` and so on).
- Test `test_open_numbers_on_every_target_row_that_has_them` (both sizes): asserts the three facts lines, the rule-3 order, exact row widths, and that the gallery baseline carries the numbers. Red on the round-1 tree: the baseline had no such rows (recorded first failing line `tests/test_redesign_lists.py:459`, the baseline-content assert). Green after regeneration.

### Existing tests updated this round (rule 12)

- `tests/test_redesign_lists.py::test_alike_rows_say_since` — docstring only: the old "told apart by their numbers" reading is replaced by the re-read alike rule. No assertion changed (the ended pairs it asserts are alike under both readings).

### Snapshots regenerated (all listed)

- `tests/snapshots/redesign-p3/`: all 18 files re-captured (9 cases × 80x24 and 160x45). Content changes follow the items: the session rows lost their clocks and gained the alike placement (`targets-opens1`, `targets-opens2`, `sessions-states`, `ended-on`, `alike-since`, both sizes each), and `sessions-states` additionally carries the three registered destinations with their numbers; the remaining 8 files (`targets-opens0`, `empty-targets`, `empty-sessions`, `only-ended`) are byte-stable re-captures of unchanged content.
- `tests/snapshots/redesign-p2b/`: all 12 files re-captured (`UPDATE_P2B_SNAPSHOTS=1`); the gate's first run failed exactly three of them (`rail-activity` both sizes, `detach-hint-80x24`) on the clock removal, the rest to match the re-captures. **Flag:** the re-captured `rail-vault` and `fold-targets` files also differ from their previous content in ways outside this round's items — the previous files showed the destinations in the core's listing order with no host facts lines, i.e. they predated round 1's row code (the natural order, the host facts line), and round 1's named suites had not re-captured them. The re-captures reflect the tree as it stands; the orchestrator should eyeball them.
- `tests/snapshots/redesign-p2a/` and `redesign-p1/`: unchanged this round (their suites passed without regeneration).

### Gate (each command run in the foreground, output to a file in the lane's log directory, exit code checked, never through a pipe; NO_COLOR unset; every pytest run with the lane's venv interpreter and `PYTHONPATH=program/contract/client:.`)

| Command | Exit | Result |
|---|---|---|
| the five new tests (`-k "alike_open_rows or no_clock or never_shortened or does_not_fit or open_numbers"`) | 0 | 7 passed |
| `tests/test_redesign_lists.py` (all) | 0 | 45 passed |
| the nine suites `LANE-BRIEF.md` names, first run | 1 | 302 passed, 3 failed — all three stale `redesign-p2b` baselines (`rail-activity` both sizes, `detach-hint-80x24`); regenerated, re-run below |
| the nine suites, after regeneration | 0 | 305 passed |
| the nine suites + `tests/test_redesign_lists.py`, after the wheel fix below | 0 | 350 passed |
| `make test` (the lane's venv interpreter, as `LANE-BRIEF-r2.md` orders), first run | 2 | 2504 passed, 7 failed, 3 skipped (details below) |
| `make test`, second (final) run | 2 | 2505 passed, 6 failed, 3 skipped (details below) |
| `make contract-check` | 0 | `program/` clean at `c4c2b81bb7a1`, v0.18.0 |
| `make lint` | 0 | ruff "All checks passed!" + engine shared object and headers match the PIN |

`git diff --name-only` lists only allowed files (round 1's set, this round's snapshots and this report); untracked: the two briefs, the report, `tests/test_redesign_lists.py`, `tests/snapshots/redesign-p3/`, the lane log directory (removed at handoff).

### The full suite's failures — named (LANE-BRIEF.md rule 12); none caused by this round's five items

The first full run also failed `test_the_wheel_moves_through_the_sidebar_and_the_view` — **that one was mine**: my row-based scroll limit for the destinations list was positive even when the list needed no scrolling, so a wheel turn over it scrolled that list instead of moving the selection (round 1's limit was 0 there and the wheel moved the selection). Fixed with a scrollability check (`_target_scrollable`); the test passes, and the second full run no longer lists it. The remaining six, each verified against `466ab48` where noted:

1. `tests/test_snapshots.py::test_s1_the_rail_widens_in_place_into_the_fleet_sidebar` (all three sizes) — asserts the old row words `1● rtr-core-01` / `2◆ infra-db-02`; round 1's ruled rows read `1 rtr-core-01` / `. yours` / `you type` (round 1's rule-12 update list covered `test_sidebar.py`'s ASCII test but not this one). Passes on `466ab48` (verified by stashing the working tree). The file is outside the nine named suites, which is why round 1's gate stayed green.
2. `tests/test_mouse_and_chrome.py::test_tooltips_name_each_glyphs_word_and_key_and_follow_the_glyph_set_and_the_knob` — hovering a framed session row must name the row it abbreviates (D13); round 1's `_row_lines` carries no row tooltip (the pre-P3 rows carried `entry.tip`). Passes on `466ab48` (verified by stash). Outside the nine named suites.
3. `tests/test_vault_unlock.py::test_init_shows_the_warning_and_the_recording_line_…` — a race the round-1 tree flips: the `save_login` task can complete its prefilled prompt before the test reads the warning, and the read then sees the workspace. Passes on `466ab48` (verified by stash); fails reproducibly on this tree standalone. Nothing in this round's items touches the vault or the prompt.
4. Load flakes seen once each across the two full runs, both green standalone afterwards: `rail-activity-160x45` (run 1, a capture whose style-role set differed; 3/3 standalone passes) and `tests/test_drive_screen_lock.py::test_any_command_restarts_the_idle_time` (run 2; the whole file passes standalone, 45/45).

I did not fix 1–3: they are round 1's accepted work outside this round's five items, and updating them is the orchestrator's call (the S1 assertions need the ruled row words; the tooltip needs `entry.tip` back on the framed rows; the vault race needs its own look).

### Not verified / limitations

- The alike word's wide placement is reported, not delivered — see item 1's flagged input (40 cells against 38 for the smallest ruled alike pair).
- The narrow fallback (the alike word beside the name when the typer line cannot hold the pair) is implemented but untested: every alike pair the fixture can render fits its typer line.
- The P2b re-captures carry round-1-era row content the previous files lacked (see the snapshot list's flag).
- Two full-suite runs each carried one load flake (named above); both green standalone. The suite was not run a third time.

## Round 3 — the four ruled fixes (2026-11-20 23:4x rulings)

**AI-Model:** glm-5.3-flash. Same branch and tree as rounds 1–2, their work uncommitted. Brief: `LANE-BRIEF-r3.md` on `LANE-BRIEF-r2.md` and `LANE-BRIEF.md`. This round changed `tsb_tui/widgets/sidebar.py`, `tests/test_redesign_lists.py` (the new test), `tests/test_snapshots.py` (item 1 only, that test's assertions), `tests/test_vault_unlock.py` (item 3 only, that test's wait and read) and one P3 snapshot; everything else in the diff is rounds 1–2's accepted work.

### 1. `test_s1_the_rail_widens_in_place_into_the_fleet_sidebar` to the ruled row words

`tests/test_snapshots.py` — the test's two row assertions updated, nothing else in the file. Old: `any("1●" in row and "rtr-core-01" in row)` and `any("2◆" in row and "infra-db-02" in row)` (the pre-P3 number+glyph+name row). New: the ruled rows (rule 4/1) — `1 rtr-core-01`; `2 infra-db-02` or, at the wide budget where the peer's typer words (`fixture-peer types`) leave the name no room, the ruled ellipsis form `2 … ◆ live`; the state lines `● live · yours` and `◆ live · yours`; the typer words `you type` and `fixture-peer types`. Red reproduced on the round-2 tree: `tests/test_snapshots.py:479` (the old `1●` assertion); green all three sizes.

### 2. Row tooltips come back

`tsb_tui/widgets/sidebar.py` — every line of a framed session row carries the row's tooltip again (`entry.tip or None` in `_row_lines`, narrow first/state/typer lines and wide one or two lines alike), as the pre-P3 rows did (D13: a row cut short says all of it). `tests/test_mouse_and_chrome.py::test_tooltips_name_each_glyphs_word_and_key_and_follow_the_glyph_set_and_the_knob` passes unchanged. Red reproduced on the round-2 tree: `tests/test_mouse_and_chrome.py:424` (the hovered row's tooltip was `None`); green.

### 3. The vault init test waits on the warning's own appearance

`tests/test_vault_unlock.py` — that test only. The read after `_prompt` is now behind a `wait_until` on the screen state the test asserts (the three warning phrases and the recording line), never on timing; no sleep, no longer timeout (the fixture's default 10 s). The race was the first paint: a fresh mount is queryable before it paints, so the old straight read could see the workspace. **Not the product code:** the warning itself appears correctly; nothing in it changed.

One test-mechanics change the plain wait could not avoid: the state is read scoped to the prompt panel's own region. The workspace behind the modal paints into the same compositor rows, so a whole-screen read interleaves its text with the panel's (`are 192 lost` — a wrapped phrase is never one contiguous run on the full read), which a plain `wait_until` on the phrases could never satisfy. The read is the compositor's strips cut to the panel's region, breaks taken out; the test's later assertions use the same read. Red reproduced on the round-2 tree: `tests/test_vault_unlock.py:204` (the warning-phrase read); green.

### 4. `since HH:MM` at 160 columns takes a second line

`tsb_tui/widgets/sidebar.py` — when an alike row's one wide line cannot hold the ruled words plus its alike word even with the name at one ellipsis cell, the row no longer leaves the word off: it takes a second line with `since HH:MM` on it (ruled 23:4x), nothing cut. The decision (`_wide_alike_fits`) and the words (`_wide_segments`) are one helper pair shared by the paint (`_row_lines`) and the frame's height (`sessions_height` via `_row_line_count`), so a frame cannot be one line short of its rows. The height's own map (`_wide_alike_lines`) is cached beside the model and width, as the frame reads it on every painted line. When the alike word fits with the name at its ellipsis, the one line keeps it (unchanged).

- Test `test_an_alike_pair_that_cannot_fit_takes_a_second_line_at_wide` (160x45, the smallest alike pair: two open-here live sessions, owner `yours`, typer `you type`): both rows say `since`; each alike word stands on its own second line (`since HH:MM` alone on it, the narrow continuation lines' indent); the ruled words stay whole on the first line; every painted line full width. Red on the round-2 tree: the wait for the alike words timed out (recorded first failing line `tests/test_redesign_lists.py:368`, the `wait_until` for "both alike rows to say since"). Green.
- Two red-to-green corrections on the way, recorded for veto: (a) the frame's height first still counted one line per wide row, so the whole-row rule left the second alike row unpainted — the height now counts the second line; (b) the test first asserted the name at its ellipsis (`… ● live`), but with a short name the ruled words leave room for the name whole once the alike word moves to the second line — the name only yields when the ruled words themselves need it, so the assertion reads `normal ● live · yours you type`.
- **Interpretation (veto by line):** the second line's composition is the narrow continuation lines' — two blank cells, then the alike word, padded to the width. The ruling says `since HH:MM` on it and nothing cut; the indent is the row's existing convention.

### Existing tests changed (rule 12)

- `tests/test_snapshots.py::test_s1_the_rail_widens_in_place_into_the_fleet_sidebar` — old/new above (item 1).
- `tests/test_vault_unlock.py::test_init_shows_the_warning_and_the_recording_line_and_sends_nothing_until_both_entries_match` — the read and the wait (item 3, ruled).

### Snapshots regenerated (all listed)

- `tests/snapshots/redesign-p3/targets-opens2-160x45.svg` — the gallery's two alike open rows now paint their second lines in the Sessions section visible under the Targets pane, so that frame grows two rows and everything under it moves. No other baseline moved: the P3 galleries, P2a/P2b and P1 all pass unchanged (their files' mtimes are round 2's), and tooltips paint nothing.

### Gate (each command in the foreground, output to a file in `.lane-logs/`, exit code checked, never through a pipe; NO_COLOR unset; every pytest run with the lane's pinned interpreter and `PYTHONPATH=program/contract/client:.`)

| Command | Exit | Result |
|---|---|---|
| the item-4 test, red on the round-2 tree | 1 | the wait for the alike words timed out (`tests/test_redesign_lists.py:368`) |
| the items-1–3 tests, red reproduced on the round-2 tree | 1 | tooltip `:424`, S1 `:479`, vault `:204` |
| the new and changed tests (4 tests, 6 cases) | 0 | 6 passed |
| `tests/test_redesign_lists.py` (all) | 0 | 46 passed (first run 1 failed — `targets-opens2-160x45` baseline, regenerated and listed) |
| the nine suites `LANE-BRIEF.md` names, first run | 1 | 304 passed, 1 failed — `test_two_size_baselines[session-size1]` (`session-160x45.svg`, a P2a capture flake; 3/3 standalone passes) |
| the nine suites, re-run | 0 | 305 passed |
| `make test`, once at the end, with the lane's pinned interpreter | 2 | 2511 passed, 1 failed, 3 skipped — `test_t1c_the_terminal_with_coloured_output_shows_the_scheme[200x60]` (the 30 s wait for the coloured screen timed out under load; 3/3 standalone passes — a load flake) |
| `make contract-check` | 0 | `program/` clean at `c4c2b81bb7a1`, v0.18.0 |
| `make lint` | 0 | ruff "All checks passed!" + engine shared object and headers match the PIN (first run exit 2: two unused locals and a blind except in this round's first helper and test drafts, fixed) |

`git diff --name-only` lists only round 1/2's accepted set plus this round's `tests/test_snapshots.py` and `tests/test_vault_unlock.py` (r3's allowed files for items 1 and 3), the P3 snapshot regeneration and the report; untracked: the three briefs, the report, `tests/test_redesign_lists.py`, `tests/snapshots/redesign-p3/`, `.lane-logs/` (removed at handoff).

### Not verified / limitations

- The nine suites and the full suite each carried one capture/load flake, named above; both 3/3 green standalone. Neither touched this round's items.
- The narrow fallback (the alike word beside the name when the typer line cannot hold the pair) remains implemented but untested, as round 2 left it: every alike pair the fixture can render fits its typer line.
- The second-line path at wide is exercised by the smallest pair (live/yours/`you type`) and the frame height by the same scenario; a detached or unreachable alike pair at wide (longer state words, deeper overflow) renders the same code path but no test paints it at 160.
- Round 2's flagged wide-budget report is resolved by item 4's ruling: the alike word is delivered, on a second line, never cut and never left off.
- Part 2 work (the filter, position and paging, the details view, the Recent section, the 8,000-row cases) was not started, as ruled.

## Round 4 — the regression: a close route's re-attach hung (2026-11-21 02:2x finding)

**AI-Model:** glm-5.3-flash. Same branch and tree, rounds 1–3 uncommitted. Brief: `LANE-BRIEF-r4.md` on `-r3`, `-r2` and `LANE-BRIEF.md`. This round changed one line-pair's worth of product code in `tsb_tui/screens/terminal.py` (a fallback attach start in `_add_slot`) and this report section; everything else in the diff is rounds 1–3's accepted work.

### What the test waits on, and the cause

`test_prompt_cache_is_dropped_on_every_close_without_losing_history` closes its session by four routes (detach, remove, release, end) and re-attaches after each of the first three. Drive's `attach` answer waits (drive/runner.py:417) for the new slot's pump state to say `client_id and dump_seen and clients`. That state only appears once the slot's attachment pump starts, and the pump starts in `_start_attach` (terminal.py), which the screen runs when the canvas's first `Canvas.Resized` reaches `on_canvas_resized` (terminal.py:1927–1947).

Evidence (instrumented runs, stacks and state dumps in `.lane-logs/` during the session, removed at handoff):

- On the failing runs, the re-attach's slot existed with `pump=None`, `attach_started=False`, `worker=None` for the whole 20 s wait: the pump had never started. The canvas meanwhile was fully laid out (`is_mounted=True`, `size=(72,37)`, `_size=(72,37)`, region set, `in_front=True`) — laid out, but the screen never saw its first `Resized`.
- Traced at the moment of loss: the canvas's first `Widget._size_updated` call returned `True` ("a resize event should be sent", textual/widget.py:4110) and no `Canvas.on_resize` ever ran. In `Screen._refresh_layout`'s full-reflow branch (textual/screen.py:1370–1379) the `Resize` event is posted only to widgets in the compositor's `shown | resized` sets; `shown` is "in the new map and not in the old", `resized` requires `old_map[widget].region.size != region.size`. The canvas was already in the compositor's full map at that first `_size_updated` (verified: membership in `_full_map` was 1, in `_visible_map` 0) with its final region, so it was in neither set and its `Resize` was never posted. No exception anywhere (`App._handle_exception` and `Screen._refresh_layout` were traced; nothing raised).
- Who put the fresh canvas into the full map before the layout pass: the map's lazy rebuild (`Compositor.full_map`, textual/_compositor.py:480–493), triggered by any widget region query while the map is invalidated. The failing runs' rebuild stacks show exactly such a query between the canvas's mount and the next layout pass: `Sidebar.on_blur` (sidebar.py:1493) `self.refresh()` → `Widget.refresh` → `self.size` → `find_widget` → `full_map[widget]`. Once the canvas is in the map with its final region, the next `_refresh_layout` treats it as "already laid out, unchanged" and never posts its `Resize`, so the attach never starts — the test's 20 s wait expires.
- Why rounds 1–3 and not the base: the closing path now does far more work in exactly that window — `refresh_status` toggling the no-session card's `display` (round 1's addition, terminal.py:3729), the sidebar's heavier relayout (row heights, alike maps, `sessions_height`, target spans), `open_sidebar` and the focus move that fires `on_blur`. Each widens the chance that a region query plus map rebuild lands between the canvas mount and the layout pass. The base tree (466ab48) failed 0/8 (orchestrator) and 0/5 standalone here; this tree failed 3/8 (orchestrator) and 4/60 across this session's un-instrumented batches (an interleaving race, not a deterministic one — the trace runs, which slow the loop, failed 2/8 then 0/16).

Timing check that isolates one contributor: with only `refresh_status`'s card-display line neutralized, the test passed 24/24 standalone (the same tree otherwise failing 2/24 in the adjacent batch); re-enabled, 2/24 failed. Reported as evidence of the widened window, not as the fix.

### The change

`tsb_tui/screens/terminal.py`, `_add_slot`, the front path: after `show_front`, the attach now also starts once the screen has refreshed, from `fit_size()` — the same source the hidden path already uses (its "Never laid out, so no first resize starts the attach" branch). `_start_attach` keeps the first starter (`slot.attach_started`), so when the canvas's own `Resized` does arrive it changes nothing, and when that event is lost the attach still starts one refresh cycle later instead of never. No ruled row behaviour is touched; the rows, sidebar and model files are unchanged.

### Proof: the 12 (36) standalone runs

Each run its own `pytest` process, `PYTHONPATH=program/contract/client:.`, pinned interpreter, `-p no:cacheprovider`, output to a file in `.lane-logs/`:

| Runs | Result |
|---|---|
| fix-01 … fix-12 | 12/12 passed, exit 0 (the brief's gate) |
| fix-13 … fix-36 | 24 further runs, all passed, exit 0 |

Before the fix, on the same tree, same command: 4 failures in 60 un-instrumented runs (r4-run4; m1, m3; ctl14, ctl20) and 3/8 on the orchestrator's batch; base 466ab48: 0/5.

### Gate

| Command | Exit | Result |
|---|---|---|
| the failing test, 12 standalone runs (each its own pytest process) | 0 each | 12/12 passed; 24 further runs also green |
| `tests/test_drive_prompt_pattern.py` (whole) | 0 | 27 passed |
| `tests/test_redesign_lists.py` | 0 | 46 passed |
| the nine suites `LANE-BRIEF.md` names | 0 | 305 passed |
| `make test PYTHON=<the test venv's python>` (once, at the end) | 2 | 2511 passed, **1 failed**, 3 skipped — `tests/test_drive_screen_lock.py::test_any_command_restarts_the_idle_time`; 3/3 standalone passes for the file and 3/3 for that test alone — a load flake per the brief's rule, named and not fixed |
| `make contract-check` | 0 | `program/` clean at `c4c2b81bb7a1`, v0.18.0; engine matches the PIN |
| `make lint` | 0 | ruff "All checks passed!" + engine shared object and headers OK |

`git diff --name-only` lists only rounds 1–3's accepted set (the P1–P2b baselines, `tests/test_redesign_focus.py`, `tests/test_redesign_layout.py`, `tests/test_sidebar.py`, `tests/test_snapshots.py`, `tests/test_vault_unlock.py`, `tsb_tui/model/fleet.py`, `tsb_tui/model/targets.py`, `tsb_tui/screens/terminal.py`, `tsb_tui/widgets/sidebar.py`); untracked: the four briefs, this report, `tests/test_redesign_lists.py`, `tests/snapshots/redesign-p3/`, `.lane-logs/` (removed at handoff). This round's only product change is inside the allowed `tsb_tui/screens/terminal.py`.

### Not verified / limitations

- The 1-in-`make test` failure is classified per the brief's rule (3/3 standalone green) but the full suite was not re-run end to end after it.
- The fix was proven green 36/36 on the affected test plus the whole file, the lists file and the nine suites; the race itself is Textual's (`Resize` posted only to `shown|resized` after a lazy full-map rebuild adds the widget), which this repo does not own — the product-side fallback removes the hang's trigger, and no Textual file was touched.
- The instrumented debug runs (plugin under `.lane-logs/`, removed) perturbed the race's rate; their role was evidence only, not the proof runs.

## Round 5 — pin the timezone the P3 tests render `since` in

**AI-Model:** deepseek-v4.1-flash

tsb-tui #107's CI failed four baselines. `since HH:MM` is the session's `created_at` in local time (rule 7, product
behaviour unchanged), and the committed baselines had been captured on a machine at UTC+7 (`since 19:00`); the CI
runner rendered `since 12:00`.

### The change

`tests/test_redesign_lists.py`: a module-level autouse fixture, `_utc_timezone`, sets `TZ=UTC` and calls
`time.tzset()` for each test, then restores the process's previous `TZ` (or removes it) and calls `time.tzset()`
again. The expected words keep computing local time the way the product does, so they agree under the pinned zone,
and the galleries are covered too. No product code changed.

### Regenerated baselines (only those whose `since` text changed)

- `tests/snapshots/redesign-p3/alike-since-80x24.svg`
- `tests/snapshots/redesign-p3/alike-since-160x45.svg`
- `tests/snapshots/redesign-p3/targets-opens2-80x24.svg`
- `tests/snapshots/redesign-p3/targets-opens2-160x45.svg`

Each diff is the two `since` words per file, `19:00` → `12:00`; no other baseline and no other line changed.

### Gate

Each run its own `pytest` process over `tests/test_redesign_lists.py`, `PYTHONPATH=program/contract/client:.`,
`-p no:cacheprovider`, output to `.lane-logs/`:

| Environment | Exit | Result |
|---|---|---|
| `TZ` unset | 0 | 46 passed |
| `TZ=UTC` | 0 | 46 passed |
| `TZ=Asia/Ho_Chi_Minh` | 0 | 46 passed |
| `make lint` | 0 | ruff clean; engine shared object and headers verified |

`git diff --name-only` lists only `tests/test_redesign_lists.py` and those four baselines.
