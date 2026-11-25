# Build Report: Targets Rows (tsb-tui #123)

**AI-Model:** Gemini 3.8 Flash (agy)  
**Branch:** `fix/signout-0160`  
**Allowed paths:** `tsb_tui/widgets/sidebar.py`, `tests/test_redesign_lists.py`, `plans/reports/build-261125-targets-rows.md`  

---

## 1. Item 1: The Host Twice (Fixed)

### Defect
When a target has no name or its name is its host address, `_target_label(row)` falls back to `row.host`. Previously, `_target_facts()` also unconditionally started the facts segment list with `row.host`. As a result:
- On wide one-line rows (160 columns), the row rendered with doubled host text: `<address> <address> · …`.
- On narrow two-line rows (80 columns), line 1 displayed `<address>` and line 2 began with `<address> · …`.

### Fix
In `tsb_tui/widgets/sidebar.py`:
- In `_target_facts()`: only prepend `row.host` to `segments` when `row.host and _target_label(row) != row.host`.
- In `_target_spans()`: wide-budget segment construction mirrors this condition (`segments = [row.host] if (row.host and _target_label(row) != row.host) else []`).
- In `_target_painted()`: wide-mode fallback checks `if self.wide and not facts and full:`, avoiding redundant re-computation when facts are legitimately empty.

### Test Evidence
- **Red run:** Added unit and screen-level assertions in `tests/test_redesign_lists.py` (`test_target_facts_omit_host_when_label_is_host` and `test_target_row_when_label_is_host_facts_do_not_repeat_it` at 80x24 and 160x45). The test failed on the previous code baseline with:
  ```
  AssertionError:  ▦ Targets · 1/3 ━━━━━━━━━━━━━━━━━━━━━
     192.0.2.55 192.0.2.55 · lab          
     192.0.2.66 192.0.2.66 · lab          
     core-rtr 192.0.2.77 · lab            
  ```
  (Exit code: 1)
- **Green run:** With the fix applied, the tests passed cleanly:
  `tests/test_redesign_lists.py: 3 passed, exit code 0`.
- **Regressions & Baselines:** Existing snapshot baselines in `tests/snapshots/redesign-p3/` do not feature unnamed targets; all snapshot tests in `tests/test_redesign_lists.py` and `tests/test_redesign_lists_filter.py` passed with zero diffs.

---

## 2. Item 2: Name Squeezed by Facts (Design Gap — Stopped)

### Spec Investigation
Reviewed the redesign specifications across all relevant phase and plan documents:
- `plans/261116-1920-tui-redesign/phase-03-lists.md` (lines 32–33):
  > "- **L1-row-budget:** At 28 outer/24 inner columns, target row is two lines: name (host if unnamed; name ends in ellipsis if longer), then host · open N,M, omitting open-number suffix when none. Session row is up to three lines: N name; state · owner; you type / <name> types / nobody typing. No word is cut. At 160 columns each target/session row is one line. Test zero/one/multiple open numbers. For an input that cannot fit this exact budget without cutting a word, stop and report it rather than silently changing the ruling."
- `plans/261116-1920-tui-redesign/phase-02a-geometry.md` (lines 22–23):
  > "- **L1-layout-B / L1-frames:** At 80x24 the side panel outer width is 28, inner width 24 after border/padding; it remains beside the canvas. At 160x45 the worded rail outer width is 12 and side panel remains beside canvas."
- `plans/261116-1920-tui-redesign/plan.md`:
  - Finding 2.3:
    > "28-column panel has 24 inner columns. Target: two lines, name (host if unnamed, terminal ellipsis when name longer), then host · open N,M, open numbers only when any. Session: up to three lines: N name; state · owner; typer words. Never cut a word. At 160 columns every row is one line."
  - Ruled O2:
    > "Amendment 1 fixes the 28-column/24-inner budget: targets two lines (name/host fallback then host · open N,M), sessions up to three lines (N name; state · owner; typer words); at 160x45 one line. Every open session number of a target is shown. P3 writes the exact column and line budget and the long-name rule (ellipsis at the end of the name, never inside a word of the state), and asserts it at 80x24 and 160x45."

### Finding
The redesign specification defines the two-line budget and long-name ellipsis rule for narrow columns, and states that at 160 columns each row is one line. However, the specification **does not state which part yields first** when the width is constrained on a single-line target row.

The current implementation in `tsb_tui/widgets/sidebar.py:1673-1675`:
```python
room = max(inner - 2 - len(facts), 0) if facts else inner - 1
return Line((Part(mark[0], mark[1]), Part(_cut_name(label, max(room, 1)), "text"),
             Part(f" {facts}" if facts else "", "text")), row.id, PANE_TARGETS, tip)
```
allocates width to `facts` first, squeezing the target name down to an extreme ellipsis (`AC…`) when facts are long.

Per instructions ("If the spec does not say which part yields first, STOP and report the options"), implementation of Item 2 is stopped pending ruling.

### Options for Ruling
- **Option 1 (Right-to-left facts segment dropping):** The target name is protected with a defined minimum cell budget (e.g., minimum 16 cells). Facts segments drop from right to left (type word drops first, then others, then group, then open numbers) to keep the facts string within the remainder. The name only yields with an ellipsis once facts cannot be reduced further.
- **Option 2 (Facts truncation with minimum name threshold):** The facts string is budgeted or truncated first down to a minimum cell count, after which the name yields.
- **Option 3 (Proportional share):** Name and facts split the available inner width according to a fixed ratio (e.g. 50/50 or 40/60).
- **Option 4 (Two-line fallback):** If a target row's combined name and required facts exceed the inner width of a single line, wrap to a two-line row rather than squeezing the name below readability.

---

## 3. Item 3: Side Panel Width at 160 Columns (Report Only)

### Investigation
Inspected git history for `sidebar.width_wide`:
`git log -S width_wide --oneline`:
```
e0c1031 feat(tui): redesign P3 part 1, the target and session rows and their order (#107)
a510af8 feat(tui): E2 TUI — libghostty-vt renders, the sidebar and rail, own and joinable sessions, the terminal view, the menu, tsb-tui drive, OPEN-1/D52 and the feed thread (phases 1–10) (#229)
```

- **Commit introducing the wide setting:**
  Commit `a510af8cfae98e2bb7f06ad803c687ddb1047d9e`
  Author: moophat <minhtu.workmail@gmail.com>, Date: Wed Oct 14 13:50:08 2026 +0700
  Message: `feat(tui): E2 TUI — libghostty-vt renders, the sidebar and rail, own and joinable sessions, the terminal view, the menu, tsb-tui drive, OPEN-1/D52 and the feed thread (phases 1–10) (#229)`
- **Plan / ruling cited:**
  TUI E2 phases 1–10 (`plans/261011-1000-e2-tui`), citing the operator's rulings of 2026-10-11.
  This introduced in `tsb_tui/config_default.toml`:
  ```toml
  [sidebar]
  width = 32
  width_phone = 30
  width_wide = 42
  wide_from = 160
  ```
- **Redesign P3 context:**
  In redesign P3 (`e0c1031`), `test_session_row_alike_words_take_a_second_line_when_one_line_cannot_fit_both` overrode this knob with `sidebar.width_wide=24` specifically to test narrow/forced budgets at 160 columns, while the shipped default remains 42 columns from `a510af8`.

---

## 4. Verification Evidence

Commands executed and logged:
- `tests/test_redesign_lists.py` (target row grammar and host deduplication):
  `env -u NO_COLOR python -m pytest tests/test_redesign_lists.py -p no:cacheprovider` -> 63 passed (Exit code: 0)
- `tests/test_sidebar.py`:
  `env -u NO_COLOR python -m pytest tests/test_sidebar.py -p no:cacheprovider` -> 49 passed (Exit code: 0)
- `tests/test_redesign_lists_filter.py`:
  `env -u NO_COLOR python -m pytest tests/test_redesign_lists_filter.py -p no:cacheprovider` -> 30 passed (Exit code: 0)
- `tests/test_targets_tab.py`:
  `env -u NO_COLOR python -m pytest tests/test_targets_tab.py -p no:cacheprovider` -> 15 passed (Exit code: 0)
- `tests/test_model_targets.py`:
  `env -u NO_COLOR python -m pytest tests/test_model_targets.py -p no:cacheprovider` -> 13 passed (Exit code: 0)
- Repository lint gate:
  `make lint` -> 110 passed, engine-check OK, import-lint clean, leak-check clean (Exit code: 0)

---

## 5. Round 2: Ruling on Yield Order (Item 2 Implemented)

### Ruling
Per the orchestrator's Round 2 ruling:
- The target name is the row's identity, so facts yield first.
- On a single-line Targets row (160 columns) that does not fit, whole fact segments drop from the right end, one at a time (last ` · …` segment first), until the name fits in full.
- Only when no fact segment is left does the name itself take an ellipsis at the end, as the spec's long-name rule does on two-line rows.
- Fact segments are never cut in the middle; the name is never dropped for facts.

### Implementation
In `tsb_tui/widgets/sidebar.py`:
- Updated `_target_facts()`:
  - Accepts `wide: bool = False, inner: int | None = None`.
  - When `wide=True` and `inner` is provided, segments are constructed in standard order (`host` if label != host, `open N,M`, `group`, `others`, `type`).
  - Segments are dropped from the right end (`pop()`) one at a time until `2 + len(label) + len(candidate) <= inner`. If none fit, returns `(full, "")`.
  - When `not wide`, preserves existing narrow-budget greedy construction within `budget = inner - 1`.
- In `Sidebar._target_spans()`:
  - Calls `_target_facts(..., wide=True, inner=inner)` to populate cache with correctly budgeted facts for wide rows.
- In `Sidebar._target_painted()`:
  - Uses cached wide facts, falling back to `_target_facts(..., wide=self.wide, inner=inner)` on cache miss.
  - Squeezing logic removed; `room = max(inner - 2 - len(facts), 0) if facts else inner - 1` guarantees that when facts are present, `room >= len(label)`, rendering the full name without ellipsis. When no facts remain (`facts == ""`), `_cut_name(label, max(inner - 1, 1))` applies terminal ellipsis only if the name alone exceeds the inner width.

### Test Evidence (Red / Green)
- **Red run (`.lane-logs/round2-red.log`):**
  Added tests in `tests/test_redesign_lists.py`:
  - `test_wide_target_row_name_protected_facts_drop_from_right_unit`: verified segment dropping logic.
  - `test_wide_target_row_yield_order_and_fitting`: screen-level test asserting:
    1. A long name with long facts at 160 columns keeps the full name and drops segments from the right;
    2. A name longer than the row alone gets the ellipsis with no facts;
    3. A row that fits is unchanged.
  On the pre-fix codebase, `test_wide_target_row_yield_order_and_fitting` failed with `AssertionError:  i… 192.0.2.88 · open 1,2 · production` (name squeezed to `i…`, exit code 1).
- **Green run (`.lane-logs/round2-green.log`):**
  With the fix applied:
  - `test_wide_target_row`: 2 passed (Exit code: 0).
  - `test_a_wide_target_row_never_paints_past_the_inner_width`: 2 passed (Exit code: 0).
  - `test_painting_a_targets_line_composes_each_target_facts_once`: 1 passed (Exit code: 0).
  - `test_target_row_when_label_is_host_facts_do_not_repeat_it`: 3 passed (Exit code: 0).

### Snapshot Baseline Updates
Regenerated only the affected 160x45 snapshot baselines under `tests/snapshots/redesign-p3/`:
- `alike-since-160x45.svg`
- `details-view-160x45.svg`
- `empty-sessions-160x45.svg`
- `ended-on-160x45.svg`
- `group-row-160x45.svg`
- `only-ended-160x45.svg`
- `recent-three-160x45.svg`
- `sessions-states-160x45.svg`
- `targets-opens0-160x45.svg`
- `targets-opens1-160x45.svg`
- `targets-opens2-160x45.svg`

**Rationale:** At 160 columns, default targets with longer names/facts (notably `rich-name` with name `[bold red]rich[/bold red]` and `telnet-scenario` with name `telnet scenario`) previously suffered name-squeezing (e.g. `[bold… 192.0.2.40 · lab` and `telne… 192.0.2.30 · scenario · telnet`). Under Round 2's ruling, full names are preserved and trailing fact segments yield, cleanly rendering `[bold red]rich[/bold red] 192.0.2.40` and `telnet scenario 192.0.2.30 · scenario`. Narrow (80x24) snapshots remain completely unchanged.

### Full Verification Evidence
- `tests/test_redesign_lists.py`: 65 passed (Exit code: 0) [logged to `.lane-logs/test-redesign-lists.log`]
- `tests/test_redesign_lists_filter.py`: 30 passed (Exit code: 0) [logged to `.lane-logs/test-redesign-lists-filter.log`]
- `tests/test_sidebar.py`, `tests/test_targets_tab.py`, `tests/test_model_targets.py`: 77 passed (Exit code: 0) [logged to `.lane-logs/sidebar-targets-models.log`]
- Repository lint gate (`make lint`): 110 passed, engine-check OK, import-lint clean, leak-check clean (Exit code: 0) [logged to `.lane-logs/make-lint.log`]

---

## 6. Round 3: Display Cell Width Measurement and Cell-Aware Ellipsis

### Finding
Measuring target names and fact segments with `len()` counted Unicode characters rather than terminal display cells. For names containing wide characters (e.g. CJK ideographs, emoji), character-based budgeting permitted fact segments that the row could not physically accommodate, or rendered wide names beyond available inner cells without terminal ellipsis.

### Implementation
In `tsb_tui/widgets/sidebar.py`:
- Imported `cell_len` from `rich.cells`.
- In `_target_facts()`:
  - For wide-row fitting: measured name and candidate facts using `cell_len(label)` and `cell_len(candidate)`, checking `2 + cell_len(label) + cell_len(candidate) <= inner`.
  - For narrow-row fitting: measured segments with `cell_len(candidate) <= budget`.
- In `Sidebar._target_painted()`:
  - Calculated wide name room using display cells: `room = max(inner - 2 - cell_len(facts), 0) if facts else inner - 1`.
- In `_cut_name()`:
  - Guarded by `cell_len(text) <= width`.
  - Determined target cell budget for the head prefix as `target = width - cell_len("…")`.
  - Accumulated full characters one by one without exceeding `target`, ensuring wide characters are never split across cells.
  - Cut at last space boundary when available (`space > 0`) or at character boundary, appending `"…"`. The result plus ellipsis fits within the available cell budget.

### Test Evidence (Red / Green)
- **Red run (`.lane-logs/round3-red.log`):**
  Added tests in `tests/test_redesign_lists.py`:
  - `test_wide_target_row_wide_characters_unit`: verified that a CJK name whose character count fits but cell count overflows drops trailing fact segments (`production`) until it fits within inner cell width, and verified `_cut_name` cell budgeting. Failed on unpatched code with `AssertionError: 192.0.2.88 · production == 192.0.2.88`.
  - `test_wide_target_row_wide_characters_screen`: screen-level test at 160x45 asserting that facts drop whole segments until the name fits by cells, a wide-character name longer than the row alone gets an ellipsis, and the row stays within its cell width. Failed on unpatched code with `AssertionError: assert '…' in ' 这是一段非常非常长的中文名称用于测试 '`.
- **Green run (`.lane-logs/round3-green.log`):**
  With the fix applied:
  - `test_wide_target_row_wide_characters_unit` and `test_wide_target_row_wide_characters_screen`: passed (Exit code: 0).
  - Round-2 tests (`test_wide_target_row_name_protected_facts_drop_from_right_unit`, `test_wide_target_row_yield_order_and_fitting`) remain unchanged and passing.

### Snapshot Baseline Updates
None affected. All existing snapshot baselines in `tests/snapshots/redesign-p3/` passed with zero diffs.

### Full Verification Evidence
- `tests/test_redesign_lists.py`: 67 passed (Exit code: 0) [logged to `.lane-logs/test-redesign-lists.log`]
- `tests/test_redesign_lists_filter.py`: 30 passed (Exit code: 0) [logged to `.lane-logs/test-redesign-lists-filter.log`]
- `tests/test_sidebar.py`, `tests/test_targets_tab.py`, `tests/test_model_targets.py`: 77 passed (Exit code: 0) [logged to `.lane-logs/sidebar-targets-models.log`]
- Repository lint gate (`make lint`): 110 passed, engine-check OK, import-lint clean, leak-check clean, ruff clean (Exit code: 0) [logged to `.lane-logs/make-lint.log`]

---

## 7. Round 4: Gallery Baselines and Stop Report

### Step 1: Initial Gallery Run and Mismatch List
Executed `tests/test_redesign_gallery.py` in the foreground. Exactly 20 entries mismatched at 160x45 (all 56 other tests, including all 80x24 baselines, passed cleanly).
Mismatching entries:
1. `terminal-empty` at 160x45 (`terminal-empty-160x45.svg`)
2. `terminal-session` at 160x45 (`terminal-session-160x45.svg`)
3. `attach-confirm` at 160x45 (`attach-confirm-160x45.svg`)
4. `menu-palette` at 160x45 (`menu-palette-160x45.svg`)
5. `target-form` at 160x45 (`target-form-160x45.svg`)
6. `confirm-delete-target` at 160x45 (`confirm-delete-target-160x45.svg`)
7. `target-details` at 160x45 (`target-details-160x45.svg`)
8. `whos-here` at 160x45 (`whos-here-160x45.svg`)
9. `confirm-remove-peer` at 160x45 (`confirm-remove-peer-160x45.svg`)
10. `size-dialog` at 160x45 (`size-dialog-160x45.svg`)
11. `theme-picker` at 160x45 (`theme-picker-160x45.svg`)
12. `confirm-end-session` at 160x45 (`confirm-end-session-160x45.svg`)
13. `confirm-kill-session` at 160x45 (`confirm-kill-session-160x45.svg`)
14. `stale-answer` at 160x45 (`stale-answer-160x45.svg`)
15. `session-ended` at 160x45 (`session-ended-160x45.svg`)
16. `event-log` at 160x45 (`event-log-160x45.svg`)
17. `scroll-search` at 160x45 (`scroll-search-160x45.svg`)
18. `text-screen-about` at 160x45 (`text-screen-about-160x45.svg`)
19. `text-screen-settings` at 160x45 (`text-screen-settings-160x45.svg`)
20. `text-screen-all-keys` at 160x45 (`text-screen-all-keys-160x45.svg`)

### Step 2: Target Row Diff Confirmation & Non-Target Stop
- **Confirmed (19 entries):** For 19 of the 20 entries, diff examination confirmed the change is **only in Targets rows** (the full name kept whole, trailing fact segments dropped from the right, host deduplicated when label is host; e.g. `[bold…` -> `[bold red]rich[/bold red]`, `telne…` -> `telnet scenario`).
- **Stopped (1 entry):** `confirm-delete-target` at 160x45 differs outside the Targets rows on line 41 (`clip-path="url(#terminal-p6-line-41)"`):
  ```diff
  - ─ alt-screen · host [redacted] · port [redacted] · discovered by tsb · last seen 38d ago ───────────────────────────────────────────────────────────
  + ─ alt-screen · host [redacted] · port [redacted] · discovered by tsb · last seen 39d ago ───────────────────────────────────────────────────────────
  ```
  This is the underlying `TerminalScreen` status footer bar displaying the fixture target's age (`last seen 38d ago` -> `last seen 39d ago`) due to the elapsed calendar day between baseline generation and current execution, rather than a Targets row.
  Per Step 2 instructions ("If any entry differs anywhere else, STOP and report it with the lines that differ; change nothing for it"), execution on this entry is stopped, and `confirm-delete-target-160x45.svg` was not modified.

### Step 3: Regenerated Gallery Baselines
Regenerated only the 19 confirmed entries' baselines using the gallery's own update route (`TSB_TUI_SNAPSHOT_DIR`):
- `tests/snapshots/redesign-p6/attach-confirm-160x45.svg`
- `tests/snapshots/redesign-p6/confirm-end-session-160x45.svg`
- `tests/snapshots/redesign-p6/confirm-kill-session-160x45.svg`
- `tests/snapshots/redesign-p6/confirm-remove-peer-160x45.svg`
- `tests/snapshots/redesign-p6/event-log-160x45.svg`
- `tests/snapshots/redesign-p6/menu-palette-160x45.svg`
- `tests/snapshots/redesign-p6/scroll-search-160x45.svg`
- `tests/snapshots/redesign-p6/session-ended-160x45.svg`
- `tests/snapshots/redesign-p6/size-dialog-160x45.svg`
- `tests/snapshots/redesign-p6/stale-answer-160x45.svg`
- `tests/snapshots/redesign-p6/target-details-160x45.svg`
- `tests/snapshots/redesign-p6/target-form-160x45.svg`
- `tests/snapshots/redesign-p6/terminal-empty-160x45.svg`
- `tests/snapshots/redesign-p6/terminal-session-160x45.svg`
- `tests/snapshots/redesign-p6/text-screen-about-160x45.svg`
- `tests/snapshots/redesign-p6/text-screen-all-keys-160x45.svg`
- `tests/snapshots/redesign-p6/text-screen-settings-160x45.svg`
- `tests/snapshots/redesign-p6/theme-picker-160x45.svg`
- `tests/snapshots/redesign-p6/whos-here-160x45.svg`

### Step 4: Verification Rerun Evidence
- `tests/test_redesign_gallery.py`: 75 passed, 1 failed (`confirm-delete-target-160x45.svg` mismatching on `last seen 38d ago` vs `39d ago`; all 19 regenerated entries and all other committed baselines passed with zero diffs). (Exit code: 1)
- `tests/test_redesign_lists.py`: 67 passed (Exit code: 0)
- `tests/test_redesign_lists_filter.py`: 30 passed (Exit code: 0)
- `tests/test_sidebar.py`, `tests/test_targets_tab.py`, `tests/test_model_targets.py`: 77 passed (Exit code: 0)
- Repository lint gate (`make lint`): 110 passed, engine-check OK, import-lint clean, leak-check clean (Exit code: 0)

---

## 8. Round 5: Open-Session Numbers Protected and Gallery Wall Clock Fixed

### Problem & Refined Rulings
1. **Ruling O2 Precedence (Open Session Numbers Never Dropped):**
   A conflict was identified between Round 2's ruling (which dropped whole fact segments right-to-left unconditionally) and earlier redesign ruling O2 ("Every open session number of a target is shown"). In Round 2, single-line Targets rows at 160 columns dropped `open 1` before other facts (e.g. producing `[bold red]rich[/bold red] 192.0.2.40` instead of showing its open session number).
   **Refined Rule:** On a one-line Targets row that does not fit, drop whole fact segments from the right, one at a time, **skipping the open-session segment** (`open N` / `open N,M`), which is never dropped. When only the open segment is left beside the name and they still do not fit, the name takes its ellipsis at the end (cell-aware, as in Round 3). Never cut a fact segment in the middle. Targets with no open sessions drop segments right-to-left as in Round 2.
2. **Gallery Wall Clock Drift:**
   `confirm-delete-target-160x45.svg` previously mismatched due to elapsed calendar time ("last seen 38d ago" vs "39d ago"). Investigation located the single time source in `TerminalScreen._target_summary`: `self.app.wall_clock() - seen.timestamp()`, where `app.wall_clock` defaults to `time.time`.
   In `tests/test_redesign_gallery.py`, `app.wall_clock` is now monkeypatched relative to the fixture's constant timestamp `_serve.SEEN` (`"2026-10-17T08:30:00Z"`) to 38.5 days (`seen_ts + 38 * 86400 + 43200`). This fixes the elapsed display to `"38d ago"` across all test runs permanently while keeping assertions and baselines exact without regex substitution.

### Test Evidence (Red / Green)
- **Red run (`.lane-logs/round5-red.log`):**
  Added tests to `tests/test_redesign_lists.py`:
  - `test_wide_target_row_name_protected_facts_drop_from_right_unit`: verified that a target with host, open numbers, and group drops group and host before open numbers; failed on unpatched code with `AssertionError: 192.0.2.88 == open 1,2`.
  - `test_wide_target_row_yield_order_and_fitting`: screen-level test at 160x45 asserting that name takes ellipsis when only open segment fits beside it, keeping `open 1`. Failed on unpatched code because `open 1` was dropped and long name was kept whole.
- **Green run (`.lane-logs/round5-green.log`):**
  Applied implementation in `tsb_tui/widgets/sidebar.py`:
  - `_target_facts`: identifies `open_seg` (e.g. `open 1` or `open 1,2`). Iteratively pops segments from the right while skipping `open_seg`. If only `open_seg` remains and cannot fit beside the full name, `open_seg` is preserved in `facts` so `_target_painted()` truncates the name with a cell-aware ellipsis.
  - Both unit and screen tests passed cleanly (Exit code: 0).
  - Existing Round 2 and Round 3 tests passed cleanly.

### Baseline Updates
- `tests/snapshots/redesign-p6/confirm-delete-target-160x45.svg`: With the wall clock fixed to 38.5 days, `confirm-delete-target` matches the committed baseline (with the underlying status row masked per test definition); the baseline is byte-identical to main ("or not at all").
- 9 gallery baselines under `tests/snapshots/redesign-p6/` where session 1 is open on `rich-name` were regenerated to reflect the open-session number (`[bold red]rich[/bold red] open 1` instead of `[bold red]rich[/bold red] 192.0.2.40`):
  - `attach-confirm-160x45.svg`
  - `confirm-end-session-160x45.svg`
  - `confirm-kill-session-160x45.svg`
  - `confirm-remove-peer-160x45.svg`
  - `event-log-160x45.svg`
  - `scroll-search-160x45.svg`
  - `size-dialog-160x45.svg`
  - `stale-answer-160x45.svg`
  - `whos-here-160x45.svg`
  All 19 modified baselines were verified to differ from main strictly in the sidebar Targets rows (lines 1, 2, 3, 7, 8, 9).
- All `tests/snapshots/redesign-p3/` baselines passed with zero diffs.

### Full Verification Evidence
- `tests/test_redesign_gallery.py`: 76 passed (Exit code: 0)
- `tests/test_redesign_lists.py`: 67 passed (Exit code: 0)
- `tests/test_redesign_lists_filter.py`: 30 passed (Exit code: 0)
- `tests/test_sidebar.py`, `tests/test_targets_tab.py`, `tests/test_model_targets.py`: 77 passed (Exit code: 0)
- Repository lint gate (`make lint`): 110 passed, engine-check OK, import-lint clean, leak-check clean, ruff clean (Exit code: 0)

---

## 9. Round 6: Qodo's Two Findings on PR #124

### 1. Finding 1: Open Segment Identified by Kind

#### Defect
In `tsb_tui/widgets/sidebar.py`, `_target_facts()` previously identified the open-session segment by string equality (`curr[idx] != open_seg`). If a target belonged to a group named `"open 1"`, the group segment was erroneously protected from being dropped when width was constrained, resulting in rows rendering with duplicated text: `open 1 · open 1`.

#### Fix
- In `_target_facts()`, fact segments are constructed as kind-tagged tuples `(kind, text)`, where `kind` is `"host"`, `"open"`, `"group"`, `"others"`, or `"type"`.
- When dropping fact segments from the right end under wide budget, the drop scan skips segments whose kind is `"open"` (`curr[idx][0] != "open"`).
- Non-open segments are dropped strictly by kind without string comparison.

#### Test Evidence
- **Red run (`.lane-logs/round6-red.log`):**
  - Unit test in `tests/test_redesign_lists.py` (`test_wide_target_row_name_protected_facts_drop_from_right_unit`, case 5) with target `service-alpha`, group `"open 1"`, and session 1 open at `inner=30`. On unpatched code, both open segments were preserved.
  - Screen-level test `check_group()` in `test_wide_target_row_eight_and_nine_open_sessions_and_open_group` asserting that `open 1 · open 1` does not appear.
- **Green run (`.lane-logs/round6-green.log`):**
  - Group segment `"open 1"` dropped from the right; open-session count rendered exactly once: `open 1`. Test passed cleanly (Exit code: 0).

---

### 2. Finding 2: Open Segment Outgrowing the Row (Two-Line Wide Rows)

#### Defect & Ruling
With 8 or 9 open sessions on a target and the wide panel at its narrowest width (`sidebar.width_wide` minimum, 24 outer / 20 inner), the open-session segment (`open 1,2,3,4,5,6,7,8` = 20 cells; `open 1,2,3,4,5,6,7,8,9` = 22 cells) plus the mark (1 cell), minimal 1-cell ellipsis name (1 cell), and space (1 cell) requires 23 or 25 cells, exceeding the inner width. On a single line, the right end of the open segment was clipped.
**Ruled (DEFAULT):** A Targets row whose name with an ellipsis and its open segment cannot share one line takes the two-line form the narrow panel already uses (the name, then the host and the open numbers per O2 two-line row). Every open session number remains visible and nothing is clipped. A row that fits on one line remains unchanged.

#### Fix
In `tsb_tui/widgets/sidebar.py`:
- `_target_facts()`: When `wide` and `inner` are set, if `open_seg` exists and `3 + cell_len(open_seg) > inner`, composes two-line facts for line 2 (`host · open_seg` if fitting in `inner - 1`, else `open_seg`, plus group/others/type as budget allows).
- `_target_row_lines()`: In wide mode, returns 2 if `numbers` and `3 + cell_len(open_seg) > inner`, else 1.
- `_build_pairs()`: In wide mode, row line count is determined via `self._target_row_lines(rows[position])`.
- `_target_spans()`: Increments span offset `at += self._target_row_lines(row)`.
- `_target_painted()`: When `row_lines == 2`, renders line 0 as mark + name (`_cut_name(label, max(inner - 1, 1))`) and line 1 as facts (`Part(facts, "text")` if `cell_len(facts) >= inner` else `Part(" ", "blank") + facts.ljust(max(inner - 1, 1))`).

#### Test Evidence
- **Red run (`.lane-logs/round6-red.log`):**
  - Unit tests for 8 sessions at `inner=20` and 9 sessions at `inner=24` in `test_wide_target_row_name_protected_facts_drop_from_right_unit` (cases 6 and 7).
  - Screen-level test `test_wide_target_row_eight_and_nine_open_sessions_and_open_group`:
    - `check_eight()`: target `router-eight` with 8 open sessions at `sidebar.width_wide=24` (inner 20) verified to take 2 lines (`_target_row_lines == 2`), line 0 showing `"router-eight"`, line 1 showing `"open 1,2,3,4,5,6,7,8"`, no line exceeding content area; fitting target `r1` takes 1 line.
    - `check_nine()`: target `router-nine` with 9 open sessions at `sidebar.width_wide=28` (inner 24) verified to take 2 lines (`_target_row_lines == 2`), line 0 showing `"router-nine"`, line 1 showing `"open 1,2,3,4,5,6,7,8,9"`, no line exceeding content area.
- **Green run (`.lane-logs/round6-green.log`):**
  - Both unit and screen tests passed cleanly (Exit code: 0).

---

### 3. Baseline Verification
- No snapshot baselines were affected by this change: `git diff tests/snapshots/` is empty.
- Existing baselines in `tests/snapshots/redesign-p3/` and `tests/snapshots/redesign-p6/` passed with zero diffs.

---

### 4. Full Verification Suite Evidence
- `tests/test_redesign_lists.py`: 68 passed (Exit code: 0)
- `tests/test_redesign_gallery.py`: 76 passed (Exit code: 0)
- `tests/test_sidebar.py`: 49 passed (Exit code: 0)
- `tests/test_redesign_lists_filter.py`: 30 passed (Exit code: 0)
- `tests/test_targets_tab.py`: 15 passed (Exit code: 0)
- `tests/test_model_targets.py`: 13 passed (Exit code: 0)
- Repository lint gate (`make lint`): 110 passed, engine-check OK, import-lint clean, leak-check clean, ruff clean (Exit code: 0)





