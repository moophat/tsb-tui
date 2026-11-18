# Phase 02a — Geometry and framing

**AI-Model:** gpt-6.1-sol  
**Status:** Amended review draft — orchestrator dispatch after hotfix; rulings incorporated.  

## Scope and authority

One builder session, serial with all other phases touching these files. If the plan leaves any design decision unspecified, STOP and report the options; never choose one yourself. No code, contract or vendor change in this planning lane. Builders do not edit plans or ledgers.

**Depends on:** P1; ruled O2/O7. **Next:** P2b.

## Read before writing

Read tsb_tui/widgets/rail.py, widgets/sidebar.py, screens/terminal.py, screens/fleet_view.py, glyphs.py, app.py, config.py, config_default.toml, theme.py; their callers and existing layout/size/snapshot tests; hotfix sidebar persistence and selection scrolling tests. Read Amendment 1 in plan.md.

## Files allowed to write

tsb_tui/widgets/rail.py; tsb_tui/widgets/sidebar.py; tsb_tui/screens/terminal.py; tsb_tui/screens/fleet_view.py; tsb_tui/glyphs.py; tsb_tui/app.py; tsb_tui/config.py; tsb_tui/config_default.toml; tests/test_redesign_layout.py; this phase’s snapshot baselines. Amendment 2 adds `tsb_tui/widgets/status_row.py` (the bottom row becomes keys only) and any existing test under `tests/` whose only change is a moved fact (bottom row to the session frame title), a padding row removed or a border character, each listed in the phase report with the reason; any other file is a STOP.

## Tests first and measurable targets

- **L1-layout-B / L1-frames:** At 80x24 the side panel outer width is 28, inner width 24 after border/padding; it remains beside the canvas. At 160x45 the worded rail outer width is 12 and side panel remains beside canvas. Assert joined four-corner frames, continuous perimeters, one blank cell of padding, no clipped controls, and no orphan bars. Frame/padding and simultaneous rail/panel fail on hotfix.
- **L1-rail-breakpoint / L1-compact-marks:** At widths 80 and below assert compact rows; at 81 and above assert words Sessions, Targets, Vault, Alerts, Menu, with + New last. Test 80→81→80 plus the two required sizes. Exact Unicode/ASCII marks: Sessions ▸/>; Targets ▦/#; Vault ◆/*; Alerts !/!; Menu ≡/=; + New +/+. Assert rendered strings under both glyph settings, not only lookup constants. Red: old four-column glyph-only rail.
- **L1-tabs:** Active label is bold with ━ under every label column; inactive labels have ─ under them. Assert exact characters and bold flag, including monochrome render and both sizes. Red: colour-only bracketed tabs.
- **L1-surfaces / L1-geometry:** Assert P1’s computed surfaces >=1.2:1 against theme bg and geometry leaves canvas/panel visible without changing PTY dimensions. Snapshot empty/populated/focused states at 80x24 and 160x45. Contrast text >=4.5 under approved O7 scope.

- **Amendment 2 (2026-11-18), the rows for P2a in `plan.md`:**
  - **L1-frame-title:** the session frame's top border carries the front session's number, target name, state, owner, who types and "recorded"; the bottom row carries keys only. Assert both rows at 80x24 and 160x45, with and without a front session. Red: P1 puts the facts on the bottom row.
  - **L1-rail-alerts:** the Alerts row is shown only to a login holding `broker.health`. Red: P1 shows it to every login.
  - **L1-panel-split:** the side panel's Sessions section is its rows high, at least 3 and at most half the panel (its one-line empty state with none); Targets take the rest and scroll inside their section. Assert the heights with 0, 1, 5 and 20 open sessions at both sizes.
  - **L1-focus-border:** the focused pane, the session frame included, is drawn ┏━┓ and every other pane ┌─┐, with NO_COLOR set and unset. P2b owns where the keyboard goes; P2a owns the border it shows.
  - **L1-frames, amended:** one blank column inside the left and right borders; section titles in the top border line ("┌─ Targets · 14/230 ─"); no blank padding rows. This replaces the "one blank cell of padding" above for the top and bottom.

## Steps

1. Add the failing geometry, breakpoint, exact-mark and tab-character tests first; keep existing size/pan regressions.
2. Render the 12-column word rail from width 81, compact marks at <=80, and + New as its last row. Use glyph fallback strings above exactly.
3. Draw continuous full frames and one-cell padding; preserve hotfix sidebar persistence and visible selection. No duplicate rendering path after parity.
4. Implement ruled tab underlines and verify both-size snapshots plus boundary resize. P2b owns keyboard behavior and destinations.

## Gate and evidence

Before implementation, record the applicable ruling references and exact files/test function names in the orchestrator’s dispatch brief. Capture red assertion output on the hotfix baseline; implement; rerun the same tests green. The new test files above are planned names, not commands that can run before they exist. Use `env -u NO_COLOR python -m pytest <this phase’s test files> -p no:cacheprovider`, plus the named existing regression suites. Check actual test filenames before dispatch.

Gate: all phase assertions pass, both-size baselines reviewed, no new skips/xfails, no out-of-scope paths in `git diff --name-only`, and `make contract-check`, `make lint`, `make test` pass on the integrated branch with NO_COLOR unset. A dependency/setup failure is reported as unverified, never green. The phase report includes exact commands, output and red/green evidence; the orchestrator verifies it before the next merge. Stop every process started; no background command survives handoff.
