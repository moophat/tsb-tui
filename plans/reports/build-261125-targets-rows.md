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

