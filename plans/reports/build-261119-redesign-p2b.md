# Build report: TUI redesign P2b, focus and navigation

**AI-Model:** claude-sonnet-5-5 (native Sonnet sub) | **Branch:** feat/tui-redesign-p2b on main 2bef566 | **Date:** 2026-11-19/20
**Status:** DONE_WITH_CONCERNS. Two of the briefed items are NOT built because the plan leaves a design decision open (per-section tint, the needs-you count on the worded rail). Several smaller gaps were filled by a minimal reading, listed under "Choices to confirm". No commit, no push.

## Rulings applied

- Focus chain (phase file): Rail (one stop) -> Targets list -> Sessions list -> Rail; Shift+Tab reverses; the canvas is no stop. Esc in a list or on the rail returns to the open session, does nothing with none open; Esc clears a typed filter first. Canvas Tab and Esc reach the session unchanged (bytes asserted).
- Amendment 2 P2b rows: rail session rows (number + name, "1•" at 80 and below, activity mark), first focus in the Targets filter after login, Enter on the rail's Sessions/Targets row moves the keys to that list (panel opens if closed), "Vault…" and "Fleet" gone from labels with Menu aliases, unfocused list keeps its selection as a dim bar with ▸, one letter one meaning (`n`; next/previous on Ctrl+\ then → and ←; Delete target `D`; `c` kept as unadvertised alias), Ctrl+\ then F1 sends F1, one-line keyboard notice in the session frame.
- Operator rulings 2026-11-19: Ctrl+] detaches from canvas and lists, Menu keeps "Detach", Ctrl+\ d still works unadvertised, hint "Ctrl+] detach" on the keys row; fold (click on a list title, `z`), running app only, a folded title keeps mark, word, count.
- NOT built: per-section tint; the needs-you count "Sessions ▲1" on the 12-column rail (see Gaps).

## Files changed (tsb_tui) and why

- `model/actions.py`: labels ("Vault…", "Targets and sessions", "Full view"), `aliases` (Menu search finds old names), `direct_key` (Detach: Ctrl+]), leader keys `n` new session, `→` next, `←` previous, `c` alias, Vault/Devices/Lock now adjacent, group "Targets and sessions".
- `app.py`: Menu search matches aliases; arrows after the leader; Ctrl+\ then F1 sends F1 to the session.
- `widgets/rail.py`: focusable (one stop), selected row bar, heavy hairline when focused, keys (Up/Down/j/k, Enter, `n`, Esc, Tab), session rows = number, name, mark; `Activated.by_key`; no focus on click.
- `widgets/sidebar.py`: two panes (Targets, Sessions) in one widget, selectable Targets rows (lazy lines), selection bar (bright focused, dim unfocused, ▸ both), pane-aware heavy border, `focus_pane`, fold (`z`, click on title), Esc -> back, filter typing ends when focus leaves, Tab/Shift+Tab hand over to the screen. The old text-changed `CMD_FILTER` post is gone (it re-opened the filter after Esc).
- `screens/terminal.py`: focus chain (`chain_step`, `focus_stop`, `focus_owner`), rail Enter routing, first focus after login, Esc/return to canvas, Ctrl+] handling, keyboard notice (frame bottom border), keys-row targets for rail/Targets/Sessions, Menu shows Detach as Ctrl+], panel keeps rail focus when closed.
- `screens/fleet_view.py`: `D` deletes a target, Ctrl+] passes through, header named by its tab.
- `widgets/status_row.py` (outside the phase file's list, needed for the hint): "Ctrl+] detach", protected from 80 columns up; rail keys target protected like the lists.
- `screens/chrome_dialogs.py`, `screens/new_session.py` (outside the list, needed so "Fleet" leaves every label): All keys text, New session hint text. `screens/prompts.py`: ended dialog button word.

## Tests

New: `tests/test_redesign_focus.py` (41 tests, each at 80x24 and 160x45 where it renders) and 16 SVG baselines in `tests/snapshots/redesign-p2b/` (first-focus, rail-new, rail-vault, rail-activity, token-notice, detach-hint, fold-targets, fold-sessions at both sizes). Keys only (Pilot keys); the fold's click test is the one click test. Not written: L1-tint, and the wide needs-you count.

Red on 2bef566 (stash of tsb_tui, new file kept), first failing assertion; the three baselines that did not fail at first were tightened until they did:

| Family | Red line on 2bef566 |
|---|---|
| L1-focus (chain, both sizes) | heavy pane is not the rail after Tab: `assert {'sessions'} == {'rail'}` style (rail has no focus) |
| L1-focus amended (dim bar) | `'#4f4549' in {'#39404f'}` (no selection bar) |
| L1-first-focus | `{'sessions'} == {'targets'}` |
| L1-rail-new | `{'sessions'} == {'rail'}`; `+ New` row never selectable |
| L1-compact-marks (unicode, ascii) | the keys never reached the rail |
| L1-navigation | `{'sessions'} == {'rail'}` before Vault/Alerts |
| L1-escape (list) | "Esc leaves the panel open": Esc closed the panel; no-match: filter never narrowed (not editing) |
| L1-rail-sessions | rows `1● `, `2● `, `3●◂` (who glyph, no name) |
| L1-rail-activity | row `1●•` instead of `1• ` / `1 core-rt…•` |
| L1-rail-enter | panel closed and the rail has no focus |
| L1-words | regexp matched "fleet"; Menu has no "Vault…" |
| L1-one-meaning | conflicts `{F,a,d,e,n,p}` instead of `{a,e}` |
| leader arrows / `n` | timed out waiting for the previous session |
| L1-F1-passthrough | timed out waiting for F1 to reach the session |
| L1-token-notice | timed out waiting for the notice in the frame |
| L1-detach (hint, Menu key) | `Ctrl+] detach` absent; Menu key `Ctrl+\ d` not `Ctrl+]` |
| L1-fold (`z`, click) | `(20, 3) == (1, 22)` (no fold) |
| Menu order Vault/Devices/Lock | label `Vault…` missing |
| drive sequences unchanged | passes on 2bef566 by design (regression guard) |

Green: the same file, 41 passed, run three times in a row with no flake after the baselines wait on a quiet alert stream and on the panel row.

### Existing tests changed (file, test, old -> new, rule)

- test_actions: `test_the_new_actions_have_their_own_letters_after_the_leader`, ("s","n","p") -> ("s","→","←"), one letter one meaning.
- test_drive_front_and_version `test_all_keys_marks_target_edit_permission`, test_target_form (two tests): delete key `d` -> `D`, one letter one meaning.
- test_leader_and_menu: attaching row `Ctrl+\ d Detach` -> `Ctrl+] detach`; Menu lists Detach under `Ctrl+]`; rebinding test: the row prints Ctrl+] detach, leader letter unadvertised; the "every key reaches the session" list drops ctrl+] (Ctrl+] is the TUI's now). Rule: Detach on Ctrl+].
- test_input_fidelity (two tests), test_input_path: ctrl+] leaves the corpora (telnet's escape character no longer reaches a session); table letters -> `n`, ASCII only (arrows are keys). Rule: Detach on Ctrl+].
- test_status_row (11 tests): expected rows now carry `Ctrl+] detach` (never drops at 80+) and `▦ Targets`; name/state words give way sooner; drop-order lists lose `detach`. Rules: Detach protected; "Fleet" leaves labels.
- test_redesign_round2 `test_protected_menu_and_keys_at_ruled_sizes`: asserted on the keys-only row (the product's row) and "full view" target name; five protected hints plus a pill cannot fit 80.
- test_rail (4): rows are number, name, mark; who glyph cell gone (old `1●◂`, `2◆•` -> `1◂`, `2•`; ASCII who-glyph test rewritten to assert the Vault `*` and no who glyph). Rule: rail session rows.
- test_several_sessions (2): leader `n`/`p` -> arrows; the ended session keeps its row but no `✕` on the rail. Rules: one meaning; rail rows.
- test_sidebar (15): Esc no longer closes the panel (`Esc Back`), login lands in the Targets filter (`keys → targets`, `slash` types), `keys → sessions`, Tab walks the chain (rail, Targets, Sessions) not to a stray widget, `open_sidebar(pane="sessions")` where a test works the Sessions list, All keys headings "In the side panel"/"In the full view", unframed header word. Rules: Esc, first focus, chain, words.
- test_snapshots: `_chip_rows` reads number and mark; T1/S1/F12 expectations (`▦ Targets`, `keys → sessions`, compact note `tap a r…`, three expected lists). Rules: rail rows, words.
- test_redesign_layout: Targets title `┌─` -> `┏━` with the filter focused (first focus); six P2a baselines regenerated (heavy Targets frame, filter line, selection marker, rows, Targets hint). test_redesign_keys: six P1 menu baselines regenerated (same causes).
- test_ended_and_reattach (2), test_mouse_and_chrome (2), test_new_session_telnet, test_fleet_view: `Fleet` -> `Targets` / `full view`; New session tooltip `Ctrl+\ c` -> `n`.
- test_first_use_leader: Tab to the Sessions list before reading its hints.
- tests/vault_support.py `flat()` also strips the heavy `┃` border; tests/workspace_support.py `select_in_sidebar` opens the Sessions list; tests/fleet_view_painted_golden.json: header ` ▦ Fleet · full view` -> ` ▦ Sessions · full view` (title named by its tab; line width kept).

## Commands (NO_COLOR unset, PYTHONPATH = program/contract/client and the clone root, test venv python)

- `make contract-check`: exit 0 (program clean at v0.18.0).
- `make lint` (ruff, import lint, docs guard, leak check, engine check): exit 0.
- `make test` could not run as one foreground command (about 40 minutes against a 10-minute call limit); the same pytest command (`pytest tests/ -p no:cacheprovider`) was run as ten consecutive file groups covering every test file once, each exit 0: 229, 150, 336, 175, 223 (+3 skipped, installer tests that need the CI image), 258, 268, 194, 343, 225 passed = 2401 passed, 3 skipped, 0 failed. Early runs of the same groups had 140 failures; each is listed above.
- Phase file only: `tests/test_redesign_focus.py` 41 passed; rail, sidebar, status row, discoverability, P1/P2a suites all inside the ten groups.

## Baselines produced

Sixteen new SVGs (list above, both sizes) read as text and compared with the assertions: rail-new bar on the last row, dim ▸ on the unfocused Targets row, `1•`/`1 core-rt…•`, `you type now: fixture-peer left` in the frame's bottom-left, `Ctrl+] detach` on the keys row at both sizes, folded one-line titles.

## Choices to confirm (the plan is silent; each is the minimal reading, none was blocked on)

1. Detach "never drops": impossible below 80 columns (pill and F1 Menu alone fill 30). Protected from 80 up (ruled sizes); an ordinary hint under 80.
2. Rail rows kept where the chips were (after the hairline). Who-types glyph and the ended `✕` left the rows (the literal "1•"). Rows still come from the chip list, so a session another login holds can have a row. Options: put `✕` in the mark cell; restrict the list to sessions open here.
3. Tab with the panel closed: the chain is the rail alone. Focus arriving at a folded list (Tab, Enter on the rail) unfolds it; a click that folds moves the keys to the list that stays.
4. Enter on the rail's Alerts row opens what a click opens (the full view's Alerts tab); the `All alerts` modal is still only reachable from the popup's click.
5. Hint words: `^\ f` hint "▦ Targets" (the Menu item is "Targets and sessions"; the long form pushed ordinary hints off at 120), "Esc Back", "z Fold". `open_sidebar()` without a pane keeps the last list used.
6. Keyboard notice wording by reason ("ana left", "handed it to you", "the owner gave it to you", "you took it from ana", "gave it back"), shown for `toasts.seconds`, bottom-left of the frame; the existing toast stays.
7. Ctrl+] no longer reaches a session; it is telnet's escape character, so telnet users lose that key.
8. Paths outside the phase file's list: `widgets/status_row.py`, `screens/chrome_dialogs.py`, `screens/new_session.py`; required by the hint and the word rule. Also the test support and golden files above.

## Gaps (STOP items, options)

- **Needs-you count "Sessions ▲1"**: the worded row is `▸ Sessions` (P2a: mark and word together) = 10 of 11 cells; the count needs 3 more. Options: (a) drop the mark when the count is above 0 ("Sessions ▲1" is exactly 11, two digits do not fit); (b) the count replaces the mark ("▲1 Sessions"); (c) cut the word ("▸ Sess… ▲1"). At 80 columns and below the count already shows (`▸3`). Today the wide row shows the need only as its colour.
- **Per-section tint**: "distinct between Targets and Sessions" has no rule. Options: (a) Targets = the surface step in the theme's direction, Sessions = the smallest step the other way (darker on dark themes; all text roles keep 4.5 against bg); (b) both in one direction at two different steps; (c) a hue-shifted lightness step from `steel`. No theme code touched.
- **One letter, one meaning, remaining pairs**: `a` (Add target in the lists, Answer after the leader) and `e` (Edit target / show ended, Event log) still say two things; the plan names no replacement. The test pins this set so a change shows.

## Not verified

No real terminal or PTY run (layer 2/3), Ctrl+\ on a UK keyboard, GNOME's F1; contrast was checked per theme only through the existing role-pair tests and the rendered selected rail row in Nord; new tests were not run with NO_COLOR set (the P2a focus-border test covers it for the border). README and `docs/using-tsb-tui.md` still describe `^\ d`, `^\ n`/`^\ p`, "fleet sidebar" and "Credentials…"; not edited (outside the allowed paths).

## Unresolved questions

- Which of the tint options, the count fit, and the `a`/`e` resolution does the orchestrator rule?
- Keep or restore the who glyph and the ended `✕` on the rail rows?
