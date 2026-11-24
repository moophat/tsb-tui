# Build Report: TUI Redesign Phase 06 — Complete Gallery and CI UX Gate

**Date:** 2026-11-24  
**Branch:** `feat/redesign-p6-ux-gate` (base `49e372a`)  
**AI-Model:** gemini-3.8-flash  
**Python Interpreter:** `$(PYTHON)`  
**Plan Reference:** `plans/261116-1920-tui-redesign/phase-06-ux-gate.md`, `plan.md` Ruled section, Amendment 1, and Amendment 2 (L1-monochrome row)  
**Issue Resolution:** Closes #93 (Single view of all alerts via fleet view `TAB_ALERTS`; mouse-only `AlertsScreen` removed)

---

## 1. Overview & Screen Inventory

Phase 06 delivers the complete TUI screen gallery, the reachability and visual baseline suite, and the CI UX gate under the recorded rulings.

- **Issue #93 Resolution:**
  - `AlertsScreen` (`tsb_tui/screens/prompts.py`) was a legacy mouse-only modal reached solely by clicking `[ All alerts ]` on the alert popup.
  - Per the orchestrator's ruling, a single unified view of all alerts was established: the popup's `[ All alerts ]` button now routes to `self._terminal.open_fleet_view(TAB_ALERTS)`, exactly as the rail's Alerts action does.
  - The legacy `AlertsScreen` class and its helpers were removed completely from `tsb_tui/screens/prompts.py`.
  - The manifest entry `alerts-popup` was converted to `alerts-tab`, reaching the fleet view's Alerts tab via keys.
  - A red-first test `test_all_alerts_opens_the_fleet_view_alerts_tab` in `tests/test_alerts_popup.py` proves clicking `[ All alerts ]` lands on the fleet view's Alerts tab.
  - The PR closes #93 with no `xfail` workarounds.

- **Manifest:** `tests/redesign_screen_manifest.json`
  - Total entries: **36**
  - Concrete `Screen` subclasses covered: **30** (100% of concrete `Screen` subclasses in `tsb_tui`, including dialogs and palette; down from 31 following the removal of `AlertsScreen`)
  - Concrete classes: `LoginScreen`, `TerminalScreen`, `NewSessionScreen`, `AttachConfirmScreen`, `MenuPalette`, `CredentialsScreen`, `SaveLoginScreen`, `MapScreen`, `ConfirmDeleteScreen`, `AccountsScreen`, `AccountFormScreen`, `DevicesScreen`, `ConfirmRevokeScreen`, `TargetFormScreen`, `ConfirmTargetDeleteScreen`, `TargetDetailsScreen`, `WhosHereScreen`, `ConfirmRemoveScreen`, `SizeDialog`, `ThemePicker`, `LockScreen`, `PassphraseScreen`, `InitScreen`, `ConfirmEndScreen`, `ConfirmKillScreen`, `AnswerScreen`, `EndedScreen`, `EventLogScreen`, `SearchPrompt`, `TextScreen`
  - Multi-state screens covered: `TerminalScreen` (empty, populated, sidebar-closed, full-view, fleet-alerts), `TextScreen` (about, settings, all-keys)
  - Every entry defines `id`, `screen_class`, `title`, `description`, `modal`, `route_type`, `keys_route`, `expect`, and snapshot paths.
- **Baseline Snapshots:** `tests/snapshots/redesign-p6/`
  - Total committed SVGs: **72** (each of the 36 manifest entries at both `80x24` and `160x45`).
  - Captured directly from key-driven routes through `test_every_manifest_entry_is_reachable_by_keys`.

---

## 2. Acceptance Rows: Red and Green Evidence

Evidence taken against commit `196e7bb` (the pre-redesign baseline in an isolated scratch checkout outside the tree):

### L1-gallery
- **Requirement:** Inventory every concrete Screen subclass and material state in a manifest; every entry has 80x24 and 160x45 committed SVGs and a keys-only route. Assert no manifest omissions.
- **Red at `196e7bb`:**
  - First failing line:
    ```
    tests/test_redesign_gallery.py:24: ImportError: cannot import name 'TargetDetailsScreen' from 'tsb_tui.screens.target_details'
    ```
  - At `196e7bb`, `TargetDetailsScreen` did not exist. In addition, cockpit navigation, Targets filter pane, and prompt decorators did not exist at `196e7bb`, so keys routes and content assertions fail.
- **Green on branch:**
  - `test_manifest_lists_every_screen_class`: PASSED (verifies manifest file exists, lists 36 entries, and covers all 30 discovered concrete Screen classes).
  - `test_every_manifest_entry_has_both_baselines`: PASSED (verifies 72 committed SVG files exist and are non-empty).
  - `test_every_manifest_entry_is_reachable_by_keys`: PASSED (all 72 cases across 36 entries and 2 sizes pass with content assertions via keys-only routes).
  - `test_gallery_monochrome_marks`: PASSED at both sizes.

### L1-focus
- **Requirement:** Full login -> targets -> open -> attach consent -> session -> leader -> sidebar/full view -> new -> cancel flow, plus Vault/All alerts and every other screen. Assert exact focus chain and reverse traversal.
- **Red at `196e7bb`:**
  - First failing line:
    ```
    tests/test_redesign_focus.py:23: ImportError: cannot import name 'ACT_VAULT' from 'tsb_tui.widgets.rail'
    ```
  - At execution time against `196e7bb`:
    ```
    tests/test_redesign_focus.py:69: AttributeError: 'Sidebar' object has no attribute 'targets_height'
    ```
  - At `196e7bb`, `Sidebar` lacked `targets_height`, lacked the two-section targets/sessions split sidebar, and lacked redesign heavy border focus distinction.
- **Green on branch:**
  - `test_full_flow_focus_chain_forward_and_back`: PASSED at both 80x24 and 160x45. Proves exact forward chain (targets -> sessions -> rail -> targets) and reverse traversal (targets -> rail -> sessions -> targets), canvas focus on session open, full view, new session dialog, vault/credentials screen, and alerts view.

### L1-hints
- **Requirement:** Status row and key hints are inside viewport at both sizes, styled apart, spelled `Ctrl+\` never `^\`, with spacing rule and protected `F1 Menu` first.
- **Red at `196e7bb`:**
  - First failing line:
    ```
    tests/test_redesign_focus.py:926: AssertionError: Legacy leader ^\ found in status text: ‹ you type ›  This session is recorded byte-perfect. Anything typed …  ≡ Menu ^\
    ```
  - Status row contained legacy `^\` leader spelling instead of `Ctrl+\`, and lacked protected `F1 Menu` hint ordering.
- **Green on branch:**
  - `test_hint_row_inside_viewport`: PASSED at both 80x24 and 160x45. Confirms viewport containment, `Ctrl+\` leader spelling, at least one space between each key and label, and `F1 Menu` priority.

### L1-escape
- **Requirement:** Esc clears query in filter; returns to canvas with open session; does nothing without session; backs out one level in dialogs; reaches target on attached canvas as `\x1b`.
- **Red at `196e7bb`:**
  - First failing line:
    ```
    tests/test_redesign_focus.py:69: AttributeError: 'Sidebar' object has no attribute 'targets_height'
    ```
  - Failed due to absent split sidebar and absent heavy pane borders; also at `196e7bb`, list Esc did not return to canvas with an open session.
- **Green on branch:**
  - `test_escape_behaviour_per_screen`: PASSED at both 80x24 and 160x45. Verifies Esc clearing filter query, returning to canvas, preserving canvas focus and sending `\x1b` byte to session, and backing out of dialogs.

### L1-contrast
- **Requirement:** All shipped themes and both sizes meet text contrast >=4.5:1 on chrome roles, and target-supplied ANSI colors remain faithful.
- **Red at `196e7bb`:**
  - First failing line:
    ```
    tests/test_redesign_contrast.py:162: AssertionError: Theme contrast role border on bg ratio 4.12 < 4.5
    ```
  - Theme contrast and terracotta roles failed minimum 4.5:1 text contrast on chrome roles.
- **Green on branch:**
  - `test_every_theme_both_sizes_meets_contrast`: PASSED across all 5 themes (`contrast`, `nord`, `terracotta`, `solarized-light`, `solarized-dark`) at both sizes. Asserts both static role ratios and composed render strip cell contrast.

### L1-geometry
- **Requirement:** Worded rail from width 81 and compact Unicode/ASCII marks at <=80; + New last; 28-column side panel beside canvas; active `━` and inactive `─` tab underlines.
- **Red at `196e7bb`:**
  - First failing line:
    ```
    tests/test_redesign_layout.py:27: AssertionError: assert ['≡', '▦▸', '▲0', '!0', '+'] == ['▸', '▦', '◆', '!0', '≡', '+']
    ```
  - Rail at `196e7bb` was 4-column only with legacy actions, lacked the 12-column worded rail at width 81+, lacked `+ New` last, and lacked `_worded` support.
- **Green on branch:**
  - `test_redesign_layout.py`: All 60 tests PASSED cleanly.

### L1-monochrome (Amendment 2)
- **Requirement:** Monochrome and 16-colour gallery pass: the selected row, the focused pane, the active tab, and who types each stay identifiable by shape or words with `NO_COLOR` set.
- **Red at `196e7bb`:**
  - First failing line:
    ```
    tests/test_redesign_focus.py:69: AttributeError: 'Sidebar' object has no attribute 'targets_height'
    ```
  - Heavy borders `┏━┓` vs light borders `┌─┐` did not exist at `196e7bb` (all borders were light `┌─┐`), active tab heavy underlines `━` did not exist, and pane focus was not identifiable by shape.
- **Green on branch:**
  - `test_gallery_monochrome_marks`: PASSED at both 80x24 and 160x45 with `NO_COLOR=1` and `TERM=xterm`. Proves focused pane identifiable by heavy border `┏━┓`, selected row identifiable by marker (`▸` / `◂`), who types identifiable in words ("you type"), and active tab identifiable by heavy underline `━` vs inactive `─`.

---

## 3. Gate Commands and Real Exit Codes

All gate commands were executed directly, logging to `.lane-logs/` without pipes:

| Gate | Command | Result / Duration | Exit Code |
|---|---|---|:---:|
| **Gate 1: Phase test files** | `env -u NO_COLOR TSB_TUI_TERM_LIBRARY=$PWD/vendor/libghostty-vt/libghostty-vt.so $(PYTHON) -m pytest -q -p no:cacheprovider tests/test_redesign_gallery.py tests/test_redesign_focus.py tests/test_redesign_contrast.py` | 150 passed in 598.40s (0:09:58) | **0** |
| **Gate 2: All redesign tests** | `env -u NO_COLOR TSB_TUI_TERM_LIBRARY=$PWD/vendor/libghostty-vt/libghostty-vt.so $(PYTHON) -m pytest -q -p no:cacheprovider tests/test_redesign_*.py` | 530 passed, 2 failed in 1415.43s (0:23:35) | **1** |
| **Gate 3: Contract check** | `make contract-check` | `program/ is clean at c4c2b81bb7a1, v0.18.0` | **0** |
| **Gate 4: Lint** | `PATH="$(TEST_VENV_BIN):$PATH" make lint` | 110 auxiliary tests passed in 58.95s; import-lint: clean (214 files); docs guard clean; leak check clean; engine check OK against PIN | **0** |
| **Gate 5: Full suite test** | `PATH="$(TEST_VENV_BIN):$PATH" env -u NO_COLOR TSB_TUI_TERM_LIBRARY=$PWD/vendor/libghostty-vt/libghostty-vt.so make test` | 2762 passed, 3 skipped, 0 failed in 3961.41s (1:06:01) | **0** |

### Failures Recorded from Gate 2:
- `tests/test_redesign_focus.py::test_the_frame_says_so_when_the_keyboard_moves_to_you[size1]`
  - Cause: SVG baseline diff against `tests/snapshots/redesign-p2b/token-notice-160x45.svg` during heavy full-suite execution (alert badge timing jitter). Passes cleanly when isolated.
- `tests/test_redesign_new_session.py::test_r_after_a_not_found_refusal_refetches_the_list`
  - Cause: `assert app.screen.focused.id == "create-button"` returned `new-session-filter` on target refusal.

---

## 4. Boundary & File Scope Verification

`git diff --name-only` confirms only allowed files were modified or added:
- `tsb_tui/screens/prompts.py` (widened by ruling: `AlertStack.on_choice_pressed` routes `alert:all` to `open_fleet_view(TAB_ALERTS)`; `AlertsScreen` removed)
- `tests/test_alerts_popup.py` (widened by ruling: `test_all_alerts_opens_the_fleet_view_alerts_tab` tests `TAB_ALERTS` open)
- `tests/test_redesign_focus.py` (extended)
- `tests/test_redesign_contrast.py` (extended)
- `tests/redesign_screen_manifest.json` (new)
- `tests/test_redesign_gallery.py` (new)
- `tests/snapshots/redesign-p6/*.svg` (72 baseline files)
- `plans/reports/build-261124-redesign-p6-ux-gate.md` (this report)

No changes made to `.aiworkflow/`, any plan file, `program/`, `vendor/`, `.plan-inputs/`, `.git`, or `.claude`.

---

## 5. Unverified Scope

- **Real broker / SSH targets:** Tests ran against contract client fixtures, stub servers, and test doubles only; no real broker or sshd daemon was connected.
- **Login hints (P3b):** Deferred per plan; no login words tested.

---

## 6. Round 3: Pre-PR Baselines & Machine Path Elimination

In response to the orchestrator's review ("Before the PR: the gallery writes tracked files, and a baseline carries a machine path"):

1. **Ordinary Test Runs Never Write Tracked Baselines:**
   - In `tests/test_redesign_gallery.py`, `test_every_manifest_entry_is_reachable_by_keys` was modified to output screenshot captures to pytest's `tmp_path / baseline_name`. Captures are verified in the temporary directory (asserting SVG prefix, closing tag, and size >500 bytes).
   - Committed baselines under `tests/snapshots/redesign-p6/` are written only when the environment variable `TSB_TUI_SNAPSHOT_DIR` is explicitly set, documented in the test's docstring in one line.
   - `test_every_manifest_entry_has_both_baselines` continues reading `COMMITTED_SNAP_DIR` (`tests/snapshots/redesign-p6/`).
   - Removed orphaned legacy `alerts-popup-160x45.svg` and `alerts-popup-80x24.svg` files left over from the pre-ruling design. Exactly 72 committed SVG baselines remain, matching the 36 manifest entries at both sizes.

2. **Elimination of Machine Paths from About Baselines:**
   - Regenerated `text-screen-about-80x24.svg` and `text-screen-about-160x45.svg` via `TSB_TUI_SNAPSHOT_DIR=tests/snapshots/redesign-p6` with relative `TSB_TUI_TERM_LIBRARY=vendor/libghostty-vt/libghostty-vt.so` run from the repository root.
   - The line in both About baselines now reads `engine library vendor/libghostty-vt/libghostty-vt.so`.
   - Grep verification across all committed SVGs in `tests/snapshots/redesign-p6/` and changed files confirmed 0 occurrences of absolute machine paths. Product code unchanged.

3. **Round 3 Gate Commands and Real Exit Codes:**

| Gate / Command | Result / Duration | Exit Code |
|---|---|:---:|
| **Gallery file:** `env -u NO_COLOR TSB_TUI_TERM_LIBRARY=vendor/libghostty-vt/libghostty-vt.so $(PYTHON) -m pytest -q -p no:cacheprovider tests/test_redesign_gallery.py` | 76 passed in 312.85s (0:05:12) | **0** |
| **All redesign + alerts tests:** `env -u NO_COLOR TSB_TUI_TERM_LIBRARY=vendor/libghostty-vt/libghostty-vt.so $(PYTHON) -m pytest -q -p no:cacheprovider tests/test_redesign_*.py tests/test_alerts_popup.py` | 541 passed in 1441.80s (0:24:01) | **0** |
| **Snapshots stability check:** `git status --short tests/snapshots` | Filesystem timestamps confirm ordinary runs do not modify baseline files; only the About pair was regenerated | **0** |
| **Lint gate:** `PATH="$(TEST_VENV_BIN):$PATH" make lint` | ruff check clean; 110 auxiliary tests passed in 58.47s; import-lint clean (214 files); docs guard clean; leak check clean; check-engine PIN match clean | **0** |

