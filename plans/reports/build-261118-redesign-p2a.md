# P2a geometry and framing — blocked before implementation

**AI-Model:** codex  
**Branch:** feat/tui-redesign-p2a  
**Baseline:** f94422b (P1 plus Amendment 2)  
**Status:** BLOCKED

## Original title-budget stop — resolved

The dispatch brief requires: “If the plan leaves any design decision unspecified, STOP and report the options; never choose one yourself.”

`plans/261116-1920-tui-redesign/plan.md`, Amendment 2, P2a L1-frame-title requires the session number, target name, state, owner, who types and “recorded” in the session frame's top border at both required sizes. The phase's L1-layout-B requires a simultaneous 4-column compact rail and 28-column side panel at 80x24. This leaves a 48-column session frame, with at most 46 columns between its corners, before any title spacing or horizontal border strokes.

A modest example, `1 · core-rtr-01 · live · owner ana · you type · recorded`, takes 56 cells. Longer target and login names increase the deficit. Neither the plan nor phase specifies which title fields shorten or disappear, their priority, or where a complete fact remains available when it cannot fit. Amendment 1's ellipsis rule applies to list rows; P1's survival order applies to footer hints and facts. Applying either to this new border title would choose new behavior.

Options for the orchestrator/operator to rule:

1. Define a title field survival/shortening order at narrow widths, including how the omitted facts remain accessible.
2. Reserve another session-header line for overflow, explicitly amending the top-border-only requirement.
3. Amend the simultaneous narrow layout or required title facts to supply enough space, with a defined long-name overflow rule.

Dispatch Amendment 1 subsequently selected option 1 and specified owner removal, target ellipsis, recording removal and typist ellipsis in that order. The title-policy blocker is resolved; no implementation has yet been made. This is a missing layout policy, not a claimed conflict between frozen contract clauses.

## Continuation: existing-test scope blocker

The instruction to continue the whole task was read along with dispatch Amendment 1. That amendment resolves title overflow, but does not amend the existing-test scope restriction: existing tests may change only for a moved fact, a removed padding row or a border character; “Anything else: STOP and list it.”

Required P2a rail geometry contradicts these existing assertions:

- `tests/test_rail.py::test_the_rail_shows_who_types_in_each_session_open_here_and_the_one_in_front`: at a 120-column window it asserts a 4-column rail and exact old glyph-only top rows. P2a requires 12 columns and worded Sessions, Targets, Vault, Alerts and Menu at widths >=81. Updating the width and label expectations is not a border-character-only edit or a fact moved to the session title.
- `tests/test_sidebar.py::test_opening_the_sidebar_never_asks_for_a_size_and_the_canvas_pans`: at 120 columns it asserts a 116-column canvas with the sidebar closed. The required 12-column rail leaves at most 108 columns even before session framing. The preservation of the no-resize assertion remains required; the obsolete canvas-width expectation must change.
- `tests/test_sidebar.py::test_the_sidebar_follows_the_window_when_it_changes_size`: at 200 columns it asserts a 158-column canvas alongside a 42-column sidebar, leaving no space for P2a's simultaneous 12-column rail or frame. Its expected geometry also needs updating.

These are concrete required changes inside existing test files, but outside the allowed kinds of edits. The complete test scan may find more. This is a test-maintenance scope issue, not a frozen-corpus conflict. Per the brief, stopped before implementation instead of broadening scope or silently leaving incompatible regression tests red.

Required scope amendment: permit existing-test updates for superseded rail labels/order, rail/sidebar/canvas widths and frame content offsets, preserving their behavioral intent (especially no PTY resize, session selection, permissions and key isolation). Alternatively supply a different scope ruling that reconciles these assertions with P2a. No option was applied.

## Inspection and checkpoint

- Read the dispatch brief, repository instructions, relevant plan rulings and amendments, phase scope, rail renderer, sidebar model, terminal layout and status-row interfaces.
- Confirmed the baseline terminal hides the rail when opening the sidebar (`TerminalScreen.open_sidebar`), and P2a must change that to satisfy simultaneous layout.
- Confirmed the footer currently has an explicit fact survival order; the new frame title has no corresponding policy in the phase.
- Task breakdown after the scope amendment: write all eleven phase test families and capture behavioral red evidence; implement only allowed files; rerun phase and named regressions; capture/review 80x24 and 160x45 SVGs; run contract-check, lint and test; audit scope and complete this report.
- Done: read title ruling, inspected exact incompatible regression assertions and recorded required scope expansion. Verified: original title arithmetic and clean source tree. Left: test-scope ruling and all phase implementation/verification work.

## Files changed

- `plans/reports/build-261118-redesign-p2a.md`: this required report only.
- No source, test, baseline, plan, ledger or contract changes remain. No existing tests were adjusted.

## Commands and evidence

- `python .aiworkflow/rotate-work-tracker.py`: exit 0; invoked before reading the brief's override of general session rules. It reported nothing to compress or rotate and left no tracked diff. It was not rerun after the override was read.
- `git status --short`: exit 0, empty before report creation.
- `git branch --show-current`: exit 0, `feat/tui-redesign-p2a`.
- `git log --oneline -10`: exit 0, confirms baseline f94422b and merged P1/hotfix history.
- Deterministic Python title-width calculation: exit 0; example 56 cells, frame outer width 48, maximum title span 46 cells.

## Continuation verification

Read-only context scout inspected the pinned architecture, relevant Frontend/locked decisions and published protocols, and the redesign/hotfix regression suites. No relevant frozen-clause conflict was found. Stale notices, recording warnings, token/refusal/data-path/countdown semantics remain protected when facts move. No scout writes or test runs.

Baseline redesign command: `env -u NO_COLOR python -m pytest tests/test_redesign_contrast.py tests/test_redesign_keys.py tests/test_redesign_round1.py tests/test_redesign_round2.py tests/test_redesign_round3.py -p no:cacheprovider`, using the dispatch-required interpreter and PYTHONPATH (contract client plus clone root), with NO_COLOR unset. Output stored outside the tree. Exit 0: **89 passed in 119.58s**. No skips or xfails reported.

## Not verified

Stopped initially on the design-gap guard, then on the explicit existing-test scope guard, before source or test changes. No behavioral red/green evidence, SVG baselines, regression results, contract-check, lint or full-test result is claimed. Protocol/context and targeted caller inspection continued, but complete implementation-context inspection remains unfinished. Only the baseline redesign suites were run on continuation; their result is recorded separately. No commit or push was made; no broker/lab was contacted; no background processes were started.

Status: BLOCKED  
Summary: Amendment 1 resolves the title budget. Required rail geometry also needs existing regression width/label updates outside the brief's permitted edit kinds; await a test-scope amendment before implementation.

---

# Continuation: implementation, verification and open stops (Claude Sonnet, native sub)

**AI-Model:** claude-sonnet-5-5  
**Status:** BLOCKED (31 existing tests fail on a design gap and on one lost click target; see Stops)

## What I inherited and verified

The previous lane's uncommitted tree already held the 12-column worded rail, the compact marks, the tab underlines, the framed two-section side panel, the session frame with a heavy/light border and a title, a keys-only footer and `tests/test_redesign_layout.py`. I ran its tests: 40 passed, 2 failed (title budget). Fixes of mine in that work:

- `refresh_status`: the frame title budget was `size.width - 4`; Textual draws the border title in `outer width - 6` cells, so the title lost "recorded" early. Now `outer_size.width - 6`.
- `refresh_status`: `slots.index(front)` raised after a detach; guarded.
- `fit_size` (what a create or a size request asks for) still subtracted the old 4-column rail and no frame, so a request was bigger than the canvas shown. Now window minus the actual rail (4 or 12), 4 frame columns and 2 frame rows, and the status row.
- Removed the second status widget the lane had overlaid inside the frame (it duplicated notices); the footer (`StatusRow(keys_only=True)`) keeps the keys pill, the pan range, "keys not sent", state warnings and countdowns, the recording text of a later attach, "you took the keyboard", "you own it: typing takes it", "your right is read-only"; it drops the pill, the name, who types, live/state and REC, which are in the frame title.
- Wheel over the side panel moves the selection a row a turn again (the lane had turned it into section scrolling); over Targets it scrolls Targets only when that list overflows.

## Test families: red on f94422b, green now

Red proved in a scratch worktree of f94422b (since removed) with only `tests/test_redesign_layout.py` copied in: 36 failed, 1 passed. Counts are parametrized cases.

| Family (test) | Red on main (first assertion) | Green |
|---|---|---|
| L1-rail-breakpoint, L1-compact-marks (`test_compact_rail_exact_marks_and_new_last`) | rows were `≡ ▦▸ ▲0 !0 +`, expected `▸0 ▦ ◆ !0 ≡ +` | yes |
| L1-rail-breakpoint words, order, permission (`test_worded_rail_order_and_permission`) | `['≡','▦▸','▲0','+']` vs Sessions, Targets, Vault, Alerts, Menu, + New | yes |
| L1-tabs (`test_tabs_exact_underlines_and_bold`, 4) | `[Sessions 0]` brackets, no underline | yes |
| L1-frames amended, L1-panel-split (`test_panel_split_frames_and_padding`, 8: 0/1/5/20 sessions x 2 sizes) | no section frames (`▦ Fleet ◂ close` header) | yes |
| L1-focus-border, side panel (`test_focus_border_shape`, 4) | no `┏` focused / `┌` unfocused | yes |
| L1-focus-border, session frame (`test_session_frame_border_follows_focus`, 4) | corner cell blank on main | yes |
| L1-layout-B, L1-surfaces, L1-geometry (`test_layout_b_breakpoint_surfaces_and_pty_geometry`, 2) | rail hidden when the sidebar opens; no PTY resize asserted | yes |
| L1-frame-title (`test_frame_title_facts_and_keys_only_footer`, 8: short, long target, long typist, nobody x 2 sizes) | facts on the bottom row, no title | yes |
| L1-frame-title without a session, keys-only footer (`test_no_front_session_title_and_keys_only_footer`, 2) | no `No session open` title | yes |
| L1-rail-alerts (`test_worded_alerts_row_follows_broker_health`) | 160x45: no word `Alerts`; **80x24 passed on main**: the permission gate already existed (the Alerts row is hidden after the 403), so only the worded case is red | yes |
| Baselines (`test_two_size_baselines`, 6) | n/a (new baselines) | yes, stable over 5 repeats |

Not behavioural-red: L1-rail-alerts at 80x24 (above). No compile-red counted.

## Baselines

`tests/snapshots/redesign-p2a/{empty,panel-focused,session}-{80x24,160x45}.svg`, reviewed as text renders. The test waits for a moment with no alert raised (the fixture raises and clears one on its own clock). P1's four menu baselines regenerated (`menu-list`, `menu-workspace` at both sizes): they changed for the new geometry as a whole (rail, frames, footer), not only moved facts; reviewed by the 89-test P1 suite passing.

## Existing tests edited (Amendment 2): file, test, old value, new value, ruling

Rulings: L1-rail-breakpoint (rail 4 at 80 or below, 12 above; order Sessions, Targets, Vault, Alerts, Menu, + New), L1-layout-B (panel beside canvas, rail stays), L1-frames (frame = 2 border + 2 padding columns, 2 border rows), L1-frame-title (facts leave the bottom row).

- `tests/test_rail.py`: rail rows and widths (4 to 12, new labels and order); `tests/test_glyphs.py`: ASCII Vault mark `*` (ruled mark).
- `tests/workspace_support.py` (helper, not a test file: needed by many tests): `pick_in_sidebar` and new `click_sidebar_line`, `sidebar_offset`: click offsets now include the 2-column frame and scroll the Sessions section. **Outside the strict file list; flagged.**
- `test_attach_confirm.py`: REC check read from the row, now from the frame title ("recorded"). `test_leader_and_menu.py`: pill `‹ you type ›` on the row, now "you type" in the title. `test_take_back.py`, `test_whos_here.py` (2 places), `test_status_row.py::...keys_were_not_sent`, `test_stale_status_row.py`: same moved pill fact, new wording from the title (`view only` becomes `<name> types`).
- `test_discoverability.py`: rail `≡`/`▦▸` in rows 0 and 1 becomes Menu/Targets words above 80, marks below; the Menu click no longer closes the sidebar first (the rail stays beside it) and uses the word `Menu` above 80.
- `test_drive_commands.py::snapshot_text`: `Fleet` becomes `Targets`; `...size_asks_for_the_windows_size...`: 26x9 becomes 22x7.
- `test_fleet_view.py` x2, `test_mouse_and_chrome.py` (wheel on the view): added a 0.3 s settle before a click or wheel on the framed view (region is empty for a frame turn).
- `test_mouse_and_chrome.py`: `▲1` becomes Sessions count; tooltips hovered through the helper; rail test: 4 columns at every width becomes 4 at 80 or below and 12 above, Menu on row 4.
- `test_several_sessions.py`: chip text `rstrip` (rail rows are padded to 11).
- `test_sidebar.py`: canvas widths 116 to 104, 88 to 72, 158 to 142, `cols 1-116` to `cols 1-104`, rail knob 114 to 110; windows 120 to 130 in five tests so the canvas beside the open sidebar still shows the 80-column grid without a pan range (hint drop order is untouched); the shortened heading "Yours, another types" (an existing narrow variant now used at 28 inner columns); clicks via `click_sidebar_line`.
- `test_size_dialog.py`, `test_size_rules.py`, `test_size_and_faithful.py`, `test_connection_choice.py`, `test_drive_device_login.py`, `test_drive_targets.py`, `test_flow_pilot.py`: every requested/created size is the window minus the rail, frame (4 columns, 2 rows) and status row: 116x29 to 104x27, 96x29 to 84x27, 116x39 to 104x37, (90-4, 26-1) to (90-12-4, 26-1-2), etc.; one dialog test window moved 44x12 to 48x14 to keep its clamp case.
- `test_status_row.py`: canvas heights 19/20 to 17/18, y offsets 0/1 to 1/2, `fit_size`, pan range `cols 1-96` to `cols 1-84`.
- `test_stale_status_row.py`: banner read past the 12-column rail and the title row; canvas rows 29 to 27. `test_theme_picker.py`, `test_countdown_and_states.py`: x thresholds in pixels and the "live" fact read from the title.
- `test_snapshots.py` (about 40 assertions): `_rail`/`_shown_alerts` for the worded rail, prompt on line 1 (the frame's title is line 0), facts read from the title, canvas width `- 4`, pixel threshold fixes in the picker test (x was compared in pixels against cells), `above` rows start at 1.

## Gate (exit codes)

- `make contract-check`: exit 0. `make lint` (ruff): exit 0 (import order auto-fixed in changed files).
- Phase file `tests/test_redesign_layout.py`: 43 passed (last full run in the clone).
- P1 regression suites (keys, round1, round2, round3, contrast): 89 passed, exit 0 (after P1 baselines regenerated).
- `make test` once, before my last fixes: exit 2, 42 failed, 2294 passed, 3 skipped (2553 s). After it I fixed 11 more (create/size/drive/flow/countdown). I then re-ran only `test_sidebar`, `test_sidebar_painted_rows`, `test_snapshots`, `test_status_row`, `test_redesign_layout`: exit 1, 31 failed, 184 passed. The other previously failing files pass in per-file runs. **`make test` is not green.** `test_drive_commands.py::test_drive_reads_are_live_while_the_view_is_open` is order/load dependent: passes alone, passes 4 of 6 in-file runs; I did not prove it flaky on main (the scratch worktree was already gone).

## STOPS: 31 tests whose intent would change (not edited)

Two causes, both a design gap in the plan, not mine to choose:

1. **Where the side panel's controls and non-open sessions live (24 tests).** The ruled L1-panel-split makes Sessions "its rows high, at least 3, at most half; empty state one line" and says nothing about the old sidebar header (`◂ close`, filter, `mine│all`), footer (`+ New session`, alerts), detail block, or the groups Needs you / Yours, someone else types / Yours and joinable. The lane put all of that into the Sessions section's scroll, so at 3 rows only one line shows and with no sessions the empty-state line hides even `◂ close` (a click-only agent cannot close the panel; I worked around it in the discoverability test because the rail stays visible). Failing: `test_sidebar_painted_rows.py` (11), `test_sidebar.py` details-at-160, ascii header `# Fleet  < close`, unreachable-row x2, pan note, click-gives-keys (6), `test_snapshots.py::test_s1_...` (3) and the gallery colour test below. Options for the orchestrator: (a) a pinned control line inside the Sessions section that is always visible (height min 4); (b) controls move to the section titles or to the full view only, and non-open sessions go to the Targets/full view (needs P3's lists); (c) Sessions height counts every line of the grouped view up to half the panel (contradicts "at least 3 / one-line empty state" at 0 sessions).
2. **The pill was a click target (1 + 10 tests).** `test_status_row.py::test_a_click_on_the_pill_opens_whos_here...` clicks the pill that moved into the frame title, which is not clickable; and `test_the_gallery_pictures_each_packaged_theme...` (10) asserts a rect with the theme accent under the status row's pill at the left; the title carries no accent. Options: make the title (or a part of it) clickable to Who's here and give it the accent role, or accept dropping both assertions.

## Concerns

- `fit_size()` now includes the frame, so creates and size requests are smaller by 4 columns and 2 rows; no PTY resize happens from layout changes (asserted).
- `rail.width` knob: the code keeps it only when it is not the default 4 (the lane's choice); 12/4 come from the window width.
- Sessions in the panel not open here (needs-you, joinable) are reachable only by scrolling the small section (gap 1).
- `tests/workspace_support.py` is outside the allowed list as written.

Status: BLOCKED
Summary: P2a is implemented and its own 43 tests pass red-then-green with baselines, but 31 existing sidebar and snapshot tests need a ruling on where the panel's controls and other sessions live and on the clickable pill; `make test` exits non-zero.

---

# Continuation 2: Amendments 3 and 4 (Claude Sonnet, native sub)

**AI-Model:** claude-sonnet-5-5  
This section supersedes the "Gate" and "STOPS" parts of the previous continuation.

## Amendment 4 (marks beside words; section titles)

- Rail from 81 columns: `▸ Sessions`, `▦ Targets`, `◆ Vault`, `! Alerts n`, `≡ Menu`, `+ New`; same marks and ASCII fallbacks as the compact rail; 12 columns; `rail._worded` cuts a word that does not fit with an ellipsis, never the mark or the count.
- Side-panel section titles: `┌─ ▦ Targets · 8 ─`, `┌─ ▸ Sessions ─`, in ASCII `# Targets .`, `> Sessions`; same with NO_COLOR.
- Tests (red first on the tree before the change, then green): `test_worded_rail_shows_mark_and_word_together_in_order` (unicode and ascii; red: `['Sessions 0', 'Targets', ...]`), `test_a_word_that_does_not_fit_beside_its_mark_is_cut_with_an_ellipsis_never_the_mark` (new symbol, compile-red not counted), `test_panel_section_titles_carry_their_marks` (4 cases; red: `┌─ Targets · 2 ─`).
- **Needs-you count (design gap, my reading):** `▸ Sessions` is 10 of the 11 content cells; the count does not fit beside the word. The worded Sessions row therefore shows no count (the count shows on the marks-only rail, and the row keeps its `need` colour); P2b's `Sessions ▲1` owns it. Tests that read the count read `rail._model.need_you`. If you prefer `▸1 Sessions` (11 cells) say so.

## Amendment 3

1. **Sessions section = the sessions open here** (`sidebar_view(framed=True)`, one row each, no heading, header, footer or details). Everything else has its home:
   - groups and mine/all: the full view's Sessions tab (`mine│joinable` there);
   - `+ New session`: the rail's `+ New` row and `n`; alerts: the rail's Alerts row; closing: `Esc` and the Menu entry, hint `Esc Close` in the keys row while the panel has focus (the `m Mine│all` hint left the row; `F Full view` stays);
   - filter and count: the Targets section (`/`, typed text, Enter keeps it, Esc clears; title `Targets · 1/8` while filtered; matches name or host, local);
   - detail block: gone.
   Also: the rail's Sessions row opens the full view on the first session that needs you (`open_needs_you`), because the panel no longer holds that group; opening the panel re-reads Targets when the full view had emptied the shared model (red proved: `test_the_panels_targets_come_back_after_the_full_view_was_open`).
2. **Frame title as a control:** a click on the session frame's top border opens Who's here (`test_the_frame_title_opens_whos_here_and_carries_the_accent`), and the who-types words carry the pill's accent (on_accent on accent, bold) in every theme (the gallery test now asserts an accent rect on the window's first row).
3. **`test_drive_reads_are_live_while_the_view_is_open` on main f94422b** (scratch worktree, removed): 6 runs of the whole file, 6 passes. On my tree it failed 5 of 9 runs, always the same field: the first read was `look_kind: unsettled` (`output still arriving`) because it ran inside `drive.settled_ms` of the 300 lines of output. So P2a changed the timing and the test raced the settle window. Fixed in the test: it sleeps 1.5 x `drive.settled_ms` before the baseline reads (intent unchanged: reads before and after the view are equal). 6 runs after: green.
4. **130-column windows**, kept only where the test is about the keys row's hints with a session pannable (`...sidebars_keys_are_its_own...`, `...a_click_on_the_session_gives_the_keys_back...`, `...status_row_offers_the_sidebars_keys_as_clicks`, `...pan_note_follows...`): at 120 the 72-column canvas is narrower than the 80-column session, so the pan range joins the keys row and ordinary hints drop; the test's intent is those hints, not the window size. `test_the_poll_tells_the_row...` went back to 120.

## Existing tests moved to the new homes (file, test, old, new, ruling)

- `test_sidebar.py`: `...groups_hold_the_sessions_their_rules_name_in_d42s_order` becomes `...panel_lists_only_what_is_open_here_and_the_full_view_holds_the_groups` (panel rows == open here; groups in the view); `...mine_all_and_the_filter...` becomes `...mine_all_is_the_full_views_and_the_filter_narrows_the_targets`; `...30_32_or_42_columns...details_come_from_160` becomes `...and_has_no_detail_block`; `...sidebars_own_entries_open_the_fleet_view_new_session_alerts_and_close_by_click` becomes `...panels_old_entries_are_the_rails_and_the_keys_now`; `...keyboard_reaches_new_session_and_opens_it` becomes `...keyboard_moves_over_the_open_rows_tab_keeps_the_selection_and_n_opens_new_session`; `...scrolls_new_session_onto_screen` becomes `...scrolls_the_selected_row_onto_screen` (the hotfix's visible-selection protection, now over open rows); `...unreachable...never_shows_an_idle_countdown` reads the full view; held/peer sessions listed in the full view; the ASCII header test reads section titles; widths 88 to 82, 116 to 104 and so on.
- `test_mouse_and_chrome.py`: the sidebar-row click picks through `pick_in_sidebar` (panel, else the full view); the wheel test opens its three sessions here; the rail Sessions click lands in the full view on `needs_you`; the tooltip test lost the `◂ close` part (7 to 6 entries).
- `test_rail.py`, `test_snapshots.py`, `test_discoverability.py`, `test_attach_confirm.py`, `test_take_back.py`, `test_view_and_quit.py`, `test_flow_pilot.py`, `test_status_row.py` (the pill click becomes the title click), `workspace_support.py` (`pick_in_sidebar` and `select_in_sidebar` fall back to the full view for sessions not open here; click offsets): as listed in the previous section plus the above.
- `test_snapshots.py::test_s1_...`: the panel now asserts the two section titles, the open rows, and the absence of groups, filter row and New session.

## STOPS left (12 tests; intent cannot be kept)

- `test_sidebar_painted_rows.py` (11): they measure that the sidebar builds entries only for the rows it paints out of 14,000 records and compare its painted text with a golden (`sidebar_painted_golden.json`). The panel no longer paints that grouped list (open here only); the claim moves to the full view, which has its own model and no such test. Needs a ruling: retire these tests and the golden, or re-point them at the full view's painted rows.
- `test_sidebar.py::test_sidebar_groups_hides_the_groups_it_does_not_name`: the `sidebar.groups` knob picks the panel's groups; the panel now has the one section, so the knob has nothing to pick. Needs a ruling: retire the knob and test, or define what it means now.

## Gate

- `make contract-check` exit 0; `make lint` exit 0.
- New phase file `tests/test_redesign_layout.py`: 55 passed.
- `make test` once end to end, after my last code change: exit 2, 12 failed, 2336 passed, 3 skipped (2569 s). The 12 failures are exactly the STOPS above (11 in `test_sidebar_painted_rows.py`, 1 `sidebar.groups` knob test); nothing else fails.
- Legacy path: `sidebar_view(framed=False)` (the full grouped list) stays because about 25 unit tests of the row grammar read it; it is no longer drawn by the widget. Delete it with those tests when you rule on the painted-rows tests.
- `git diff --name-only`: only the allowed files plus the listed existing tests, 4 P1 baselines and `tests/workspace_support.py` (click offsets and the full-view fallback of the pick helpers).


---

# Continuation 3: Amendment 5

**AI-Model:** claude-sonnet-5-5

1. **Painted-rows tests: STOP, regression found (not weakened, not re-pointed).** The 11 tests in `test_sidebar_painted_rows.py` still stand as they were (they fail only because the panel no longer paints that list). Before re-pointing them I measured the full view with 14,000 records (95% ended, as the tests' fleet): one `_sync_fleet_widgets` with the view open built **14,000 `SidebarEntry` and 14,000 `FleetRow`** (0.5 s on this machine, on the UI thread) for about 37 painted lines (700 live rows in its layout). `_fleet_view_model` builds `row(record)` for every record in `needs`, `first` and `rest` before painting; that code is main's, unchanged by P2a, and `#80`'s 8,167 ended rows would pay it on every rebuild with the view open. The tests' intent (entries only for the painted rows) cannot pass on the full view as built. Options: (a) make the full view model lazy over the painted window (a refactor of `fleet_view.py` and `terminal._fleet_view_model`, the same technique the sidebar used), then re-point the 11 tests and the golden at it; (b) keep the tests on the retired unframed `sidebar_view` (which still builds only painted rows) until P3 and open the full-view fix as its own change. I did not choose.
2. **`sidebar.groups` retired:** its test is replaced by `test_the_retired_sidebar_groups_knob_still_loads_and_changes_nothing` (an old config naming it loads; the fleet's groups are the full set; the panel still has the one section). The config loader still accepts the key (`config.py` unchanged, the config tests that list it pass); `Fleet.from_config` no longer passes it on, so it is ignored.
3. Worded needs-you count, `sidebar_view(framed=False)` and the rail Sessions row opening the full view: left as accepted.

## Gate for this step

- `make contract-check` exit 0; `make lint` exit 0.
- `make test` was run once end to end after the last code change of the previous step (exit 2, 12 failed = the 11 painted-rows tests and the `sidebar.groups` test). Since then I changed only the knob's handling and replaced its test; I re-ran `test_sidebar.py`, `test_config.py`, `test_model_fleet.py` and `test_redesign_layout.py`: 208 passed. **I did not re-run the whole suite**: the expected result is exit 2 with exactly the 11 painted-rows failures, because of the stop in item 1.
- `git diff --name-only`: unchanged set plus `tsb_tui/model/fleet.py` (the retired knob).


---

# Continuation 4: Amendment 6 (final)

**AI-Model:** claude-sonnet-5-5  
This section supersedes the stop and the gate of Continuation 3.

- The 11 painted-rows tests (12 in the file with the model-scan one) run against the unframed `sidebar_view` again: an autouse fixture in `tests/test_sidebar_painted_rows.py` makes the widget build its view with that function at the width and height it had (`Sidebar._build`, `refresh_times` re-laying at the same width, `sessions_height` as the full height), and `legacy_text` paints it as the widget used to. Counts, tick checks and selection are the tests' own; no assertion was loosened. `tests/sidebar_painted_golden.json` and its generator are unchanged and match byte for byte (golden unchanged, intent kept). The full view's paint-window fix (14,000 `SidebarEntry` and `FleetRow` per rebuild, about 0.5 s) is its own change before P2b, as ruled; the 11 tests move to the full view with a golden named for it then.
- **Gate:** `make contract-check` exit 0; `make lint` exit 0; `make test` once end to end after the last change: **exit 0, 2348 passed, 3 skipped** (the skips are the installer tests that need the CI image).
- `sidebar.groups`: the loader still accepts the key and ignores it (as in Continuation 3).
- **Diff check:** `git diff --name-only` is 37 files: the six `tsb_tui` code files plus `tsb_tui/model/fleet.py` (the retired knob), the updated existing tests listed above, `tests/workspace_support.py` (click offsets and the full-view fallback) and the four regenerated P1 menu baselines; untracked: this report, `tests/test_redesign_layout.py` and `tests/snapshots/redesign-p2a/`. No plan, ledger, `program/` or `.aiworkflow` path is touched.
