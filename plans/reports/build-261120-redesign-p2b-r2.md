# Build report: TUI redesign P2b, fix round

**AI-Model:** claude-sonnet-5-5 (native Sonnet sub) | **Branch:** feat/tui-redesign-p2b on main 2bef566 (uncommitted build plus this round) | **Date:** 2026-11-20
**Status:** DONE_WITH_CONCERNS. Seven of the eight rulings are applied. Ruling 3 is applied for Answer (`a` to `y`) and NOT for Event log: its new letter `l` already means two things (a STOP item, below). No commit, no push.

## STOP item (ruling 3, Event log `e` to `l`)

`l` is already a list key: the full view's Targets tab has `l` "Save login…" (`fleet_view.py` key handler; hint in `terminal.py`; README and the guide document it). Moving Event log to `l` makes `l` mean two things between the list keys and the leader keys, so the "empty conflict set" the ruling asks for cannot hold. The plan and the ruling leave the choice open, so I did not pick one. Event log stays on `e` after the leader. The conflict set the test pins is now `{"e"}` (Edit target in the full view's Targets tab against Event log after the leader).

Options:
- (a) Event log takes a letter that is free in both sets. Free lowercase letters: `b g h i o q u` (mnemonic: `h` history, `i` info, `g` log). Empty set holds.
- (b) Save login moves off `l` to another free letter. This changes a list key, against "the lists' stay".
- (c) Keep `l` for both and relax the assertion to allow `{l}`.

My recommendation: (a). Docs and All keys currently say Event log is `^\ e`; when the letter is ruled, one table row, one test constant and the README/guide lines change.

## Rulings applied

1. **Needs-you count.** `rail.py` `rail_rows`: while `need_you > 0` the Sessions mark is `▲` and the count follows it (`▲1 Sessions` = 11 cells; `▲12 Sessio…`: the word is cut with an ellipsis, never the count); marks-only rail shows `▲N`. With none: `▸ Sessions` and `▸`. ASCII uses the existing `▲` role fallback `!`. Tooltip says `▲N need you` while there are some.
   - Concern, one line: the brief says "with none `▸`, as now", but the P2a build showed `▸0` on the marks-only rail (a P2a test pinned `▸0`). I followed the quoted outputs (`▸`), so that test changed (below). One-line revert in `rail_rows` (`_count(...)` unconditional on the compact branch) if `▸0` was meant.
2. **Per-section tint.** `theme.py`: `surface_colour(bg, variant, ratio=1.2)` and `sessions_surface(surface, variant)` (the same smallest-lightness-step walk, from the Targets surface, same direction, ratio 1.1). `sidebar.py`: two style tables (`_style_table`), Targets on the P1 surface, Sessions on the next step; frames and bars per section. No theme colour changed. All five shipped themes meet 4.5:1 on both tints for every role the panel draws (text, text_muted, text_faint, accent, steel, warn, danger, rec_list, border_on_surface); no STOP. NO_COLOR: sections stay apart by frames and marks (guard test).
3. **One letter, one meaning.** Answer `a` to `y` (`actions.py`; `prompts.py` docstrings); no alias for `a`. Event log: see STOP. `drive/GUIDE.md` documents only `w` and `[`; unchanged.
4. **Detach hint.** `status_row.py`: Detach is in the protected set at every width. Under 80 the protected hints leave Scroll, `? keys`, Detach, Take back, Menu last (the Menu stays down to 7 cells). The leave loop now runs for every row that has a protected set (the stale row too: with Detach protected at every width it overflowed 54 columns until the loop covered it; the red for it came from an existing test).
5. **Rail session rows.** Who glyph already gone; `✕` (ASCII `x`) now takes the mark cell of an ended session ahead of `◂` and `•`; `_sync_fleet_widgets` hands the rail only the sessions open in this window (a session of mine another login holds has no row; `more` counts open sessions only).
6. **Keyboard notice.** `_tell_keyboard_moved`: coming to you is the frame notice only (wording unchanged); no toast. Leaving you keeps its toast. Orphaned `KEYBOARD_GAINED` and `_toast_took` removed.
7. **Ctrl+] passthrough.** `app.py` `handle_menu_key`: after the leader, `Ctrl+]` is sent to the session like the leader twice (byte `0x1D`), no detach. All keys line says so.
8. **Docs.** `README.md`, `docs/using-tsb-tui.md` (a keys table added in section 3): Ctrl+] detach and its passthrough, `→`/`←`, `n`, `a` Add target / `D` Delete target, `y`, F1 and Ctrl+\ then F1, `z`, "Targets and sessions", "Vault…", "full view"; no "Fleet" or "Credentials…" as displayed words (file, knob and identifier names kept). Event log documented as `^\ e` (see STOP).

## Files changed this round (tsb_tui) and why

- `theme.py`: `surface_colour` ratio argument, `sessions_surface` (ruling 2).
- `widgets/sidebar.py`: per-section style tables, `_variant` (ruling 2).
- `widgets/rail.py`: count, ended cross, tooltip (rulings 1, 5).
- `widgets/status_row.py`: protected Detach and drop order (ruling 4).
- `model/actions.py`, `screens/prompts.py`: Answer on `y` (ruling 3).
- `screens/terminal.py`: rail chips open here only; keyboard notice without toast (rulings 5, 6).
- `app.py`: Ctrl+] after the leader (ruling 7). `screens/chrome_dialogs.py`: All keys line (ruling 7).
- Docs: `README.md`, `docs/using-tsb-tui.md` (ruling 8).

## New tests (`tests/test_redesign_rail_tint_keys.py`, 42 tests) and red/green

Red recorded on the build before any code change (all behavioural; none was a missing symbol). Green: the same file, 42 passed.

| Test (each at 80x24 and 160x45 where it renders) | Red line on the build |
|---|---|
| needs-you count, 0/1/12, unicode and ASCII | `((80, 24), 1, '▸1 ')`; `((160, 45), 12, '▸ Sessions ')`; 0 on the worded rail already passed |
| tint, five themes x two sizes | `Sessions is the next step` (Sessions painted on the Targets surface) |
| NO_COLOR frames and marks | passes on the build (guard, not a red) |
| Answer on `y`, `a` does nothing | `a means nothing after the leader now`; `assert 'a' == 'y'` |
| protected drop order under 80 (sweep of `build_pieces`) | `['fleet', 'who', 'detach', 'scroll', 'keys', 'take_back']` |
| rendered row at 79 and 70 columns | Detach absent: `... Ctrl+\ [ Scroll   Ctrl+\ ? keys   F1 Menu` |
| ended row (mark cell, ahead of `•`), ASCII `x` | timed out waiting for the cross in the mark cell |
| rail lists only sessions open here | `['sess-2', 'sess-4'] == ['sess-2']` |
| frame notice replaces the toast; take back says so in the frame only | notifications `['keyboard']` / `['keyboard · you took it']` |
| Ctrl+] after the leader reaches the session | timed out waiting for Ctrl+] to reach the session |
| All keys says it | `'Ctrl+\ Ctrl+] sends Ctrl+]'` missing |

Changed existing assertions went red first on the build too (listed next); then green.

## Existing tests changed (file, test, old to new, ruling)

- `test_quiet_prompt`: two tests renamed/edited, leader `a` to `y` (3).
- `test_input_fidelity`: the action-table letters set, `"a"` to `"y"` (3).
- `test_status_row` `_text(asking, 120)` assertion `^\ a Answer` to `^\ y Answer` (3); `test_ordinary_hints_drop_by_priority_while_scroll_stays` compact `["fleet","scroll"]` to `["scroll","keys","detach"]`, `test_a_different_priority_drops_a_different_hint_first` same (4); `test_the_phone_row_is_compact_and_carries_the_pan_range_and_scroll`: at 65 the row is pill plus Detach, Scroll, keys, Menu (the name and REC give way) (4).
- `test_status_row_narrow` `test_at_65_columns_hint_scope_and_backfilled_names_match_the_state` (10 states): Detach asserted, Scroll no longer required, the name only on the stale-holder row (`NAME_FITS_AT_65` to `NAME_AT_65`) (4).
- `test_redesign_round1` `test_keyboard_hints_outlast_name_and_ordinary_hints[65,120]` expected sets; `test_redesign_round2` `test_narrow_protected_hints_drop_in_ruled_order`: the sweep now has five stages with Detach third (4).
- `test_rail` `test_a_chip_reads_its_number_and_whether_it_is_in_front_or_unseen`: `"▸3 "` to `"▲3 "` (1). `test_redesign_layout` `test_compact_rail_exact_marks_and_new_last`: `▸0`/`>0` to `▸`/`>` (1, see concern).
- `test_redesign_focus` `test_no_letter_means_two_things...`: pinned set `{"a","e"}` to `{"e"}`, answer is `y` (3).
- `test_take_back` first test and `test_whos_here` (renamed, now `..._told_in_the_frame_not_a_toast`): the toast assertions became the frame notice, and no `keyboard` toast (6).
- `test_snapshots`: `_sessions_and_targets_on_rail(need)` and the T1 call (`▲1`) (1); S1 at 65x30 the note `tap a r` gives way to protected hints (4); quiet-prompt `a` to `y`, and "Detach not on the row at 120" to "Ctrl+] detach on the row at 120" (3, 4).
- `test_several_sessions`: the held-by-another-login test renamed; no rail row, brought in from the full view, gate then attach (5). `test_sidebar` `test_a_poll_never_attaches_a_session_another_login_holds`: no rail row asserted (5).

## Commands (NO_COLOR unset, PYTHONPATH = program/contract/client and the clone root, test venv python)

- New file: 42 passed (3 runs while building; final inside the groups).
- Whole suite, 11 consecutive file groups covering every test file once, all exit 0 on the final tree: 229, 221, 249, 191, 223 (+3 skipped, installer tests that need the CI image), 220, 232, 241, 196, 230, 211 = 2443 passed, 3 skipped, 0 failed (2446 collected). One group (the 8th) had one timing failure under load (`test_screen_lock` first-output wait, unrelated code); that file passed 3 of 3 alone and the whole group passed on the rerun.
- `make contract-check`: exit 0 (program clean at v0.18.0). `make lint`: exit 0.

## Baselines regenerated

Only the Sessions tint and the marks-only `▸0` to `▸` change them; text read back row by row, 160-column files change in colour only.
- redesign-p1: `menu-list-80x24`, `menu-workspace-80x24`, `menu-workspace-160x45`.
- redesign-p2a: `empty-80x24`, `panel-focused-80x24`, `panel-focused-160x45`, `session-80x24`, `session-160x45`.
- redesign-p2b (the build's own, new in this PR): all 16: `detach-hint`, `first-focus`, `fold-sessions`, `fold-targets`, `rail-activity`, `rail-new`, `rail-vault`, `token-notice`, each at 80x24 and 160x45.
- Not changed: p1 `menu-list-160x45`, `menu-new-session-*`; p2a `empty-160x45`.

## Concerns and not verified

- **Phone row at 65 columns.** With Detach protected at every width, the pill plus Detach, Scroll, keys and Menu fill the row; the state's key fact (countdown, "input refused"), `● REC` and the session name leave it (the frame title still says `recorded` and who types). The existing rule already lets protected hints outlast facts, so I kept it; the alternative (Scroll and keys leave before a fact does) is a design call for the orchestrator.
- **Dim selection bar on the Sessions tint.** The unfocused list's bar is `surface_3`; against the Sessions tint it reaches only 1.04 to 1.24:1 in the shipped themes (nord 1.05, terracotta 1.04, paper 1.07, dracula 1.10, contrast 1.24), so there the `▸` marker carries the selection, not colour. No theme colour touched.
- Leader digits (`^\ 1`-`^\ 9`) still address the chips the model numbers, including a session of mine another login holds, which no longer has a rail row. Unchanged, flagged.
- Not verified: no real terminal or PTY run; the new tests were not run with NO_COLOR set apart from the one guard test; Ctrl+] passthrough was proven through the in-process fixture server only.

## Unresolved questions

- Event log's letter (options above).
- Marks-only Sessions row with none: `▸` as ruled, or keep `▸0`?
- At 65 columns, should facts (countdown, input refused, REC) outlast Scroll and `? keys`?
